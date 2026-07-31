#!/usr/bin/env python3
"""
test_auto_run.py — the unattended pass driver (doctor -> scan -> advance).

This logic used to live only in PowerShell, where it could not be tested and
where one unrecognised cmdlet took the whole nightly pass down. Moving it to
Python is what makes the properties below assertable rather than hoped for:

  * a machine that CANNOT work stops the pass loudly (never a quiet no-op);
  * a pass already running is skipped, not raced;
  * a stale lock from a crashed pass is taken over, so one crash does not
    disable the schedule until somebody notices;
  * a job stopping at a held gate is a NORMAL outcome and never fails the
    pass — otherwise every night with a held gate looks like an outage;
  * logs are pruned, so the thing that runs unattended forever cannot fill
    the disk with its own output.

Every test injects the step runner, so nothing here downloads, decodes, or
touches a real stage.

Run: python3 pipeline/test_auto_run.py
"""
from __future__ import annotations
import datetime as dt
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from automation import auto_run as ar  # noqa: E402


def _job_rows(*states):
    return [{"job_key": f"record:vid{i}:source", "state": s}
            for i, s in enumerate(states)]


class StepRecorder:
    """A fake step runner: records every CLI invocation, returns scripted
    results keyed by the subcommand name."""

    def __init__(self, results=None, jobs=None):
        self.calls: list[list[str]] = []
        self.results = results or {}
        self.jobs = jobs if jobs is not None else _job_rows("DISCOVERED")

    def __call__(self, args):
        self.calls.append(list(args))
        name = args[0]
        if name in self.results:
            return self.results[name]
        if name == "list-jobs":
            return {"ok": True, "code": 0, "stdout": json.dumps(self.jobs),
                    "stderr": ""}
        return {"ok": True, "code": 0, "stdout": "", "stderr": ""}

    def named(self, name):
        return [c for c in self.calls if c and c[0] == name]


class AutoRunBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log_dir = os.path.join(self.tmp.name, "logs")

    def tearDown(self):
        self.tmp.cleanup()

    def run_pass(self, step, **kw):
        kw.setdefault("log_dir", self.log_dir)
        kw.setdefault("echo", False)
        kw.setdefault("power_fn", lambda: False)   # on AC
        kw.setdefault("operator", "TestOp")
        return ar.run_pass(step=step, **kw)


# ============================================================ the doctor gate
class TestDoctorGate(AutoRunBase):
    def test_a_failing_doctor_stops_the_pass_loudly(self):
        """The single worst outcome for an unattended system is a silent
        no-op, because it is indistinguishable from 'nothing to do'."""
        step = StepRecorder({"worker-doctor": {
            "ok": False, "code": 1, "stdout": "  ffmpeg : MISSING",
            "stderr": ""}})
        report = self.run_pass(step)
        self.assertEqual(report["outcome"], "doctor-failed")
        self.assertEqual(report["exitCode"], ar.EXIT_DOCTOR_FAILED)
        self.assertEqual(step.named("find-matches"), [],
                         "nothing may run after the doctor refuses")
        self.assertEqual(step.named("autopilot"), [])

    def test_the_doctor_failure_log_names_what_is_missing(self):
        step = StepRecorder({"worker-doctor": {
            "ok": False, "code": 1,
            "stdout": "  python : OK\n  ffmpeg : MISSING\n  yt-dlp : OK",
            "stderr": ""}})
        report = self.run_pass(step)
        text = "\n".join(e["message"] for e in report["events"])
        self.assertIn("ffmpeg : MISSING", text)
        self.assertNotIn("python : OK", text, "only the failures are echoed")

    def test_skip_doctor_runs_the_rest_anyway(self):
        step = StepRecorder({"worker-doctor": {"ok": False, "code": 1,
                                               "stdout": "", "stderr": ""}})
        report = self.run_pass(step, skip_doctor=True)
        self.assertEqual(report["outcome"], "ok")
        self.assertEqual(step.named("worker-doctor"), [],
                         "the doctor is not even run when skipped")
        self.assertTrue(step.named("autopilot"))

    def test_a_healthy_doctor_lets_the_pass_proceed(self):
        step = StepRecorder()
        report = self.run_pass(step)
        self.assertEqual(report["outcome"], "ok")
        self.assertEqual(report["exitCode"], ar.EXIT_OK)


# ================================================================== the lock
class TestPassLock(AutoRunBase):
    def _write_lock(self, since):
        os.makedirs(self.log_dir, exist_ok=True)
        with open(os.path.join(self.log_dir, ar.LOCK_NAME), "w",
                  encoding="utf-8") as f:
            json.dump({"since": since.isoformat(), "pid": 4242}, f)

    def test_a_live_lock_skips_rather_than_races(self):
        self._write_lock(ar._utcnow() - dt.timedelta(minutes=20))
        step = StepRecorder()
        report = self.run_pass(step)
        self.assertEqual(report["outcome"], "skipped-locked")
        self.assertEqual(report["exitCode"], ar.EXIT_SKIPPED)
        self.assertEqual(step.calls, [])

    def test_a_stale_lock_is_taken_over(self):
        """One crashed pass must not disable the schedule indefinitely."""
        self._write_lock(ar._utcnow()
                         - dt.timedelta(hours=ar.STALE_LOCK_HOURS + 1))
        step = StepRecorder()
        report = self.run_pass(step)
        self.assertEqual(report["outcome"], "ok")

    def test_an_unparseable_lock_is_treated_as_stale(self):
        os.makedirs(self.log_dir, exist_ok=True)
        with open(os.path.join(self.log_dir, ar.LOCK_NAME), "w",
                  encoding="utf-8") as f:
            f.write("not json at all")
        report = self.run_pass(StepRecorder())
        self.assertEqual(report["outcome"], "ok")

    def test_the_lock_is_released_even_when_a_step_explodes(self):
        def boom(args):
            raise RuntimeError("segfault in a native library")

        with self.assertRaises(RuntimeError):
            self.run_pass(boom)
        self.assertFalse(
            os.path.exists(os.path.join(self.log_dir, ar.LOCK_NAME)),
            "a crashed pass must not leave the schedule locked out")

    def test_the_lock_is_released_on_a_normal_pass(self):
        self.run_pass(StepRecorder())
        self.assertFalse(
            os.path.exists(os.path.join(self.log_dir, ar.LOCK_NAME)))


# ================================================================== battery
class TestPowerPolicy(AutoRunBase):
    def test_running_on_battery_skips(self):
        step = StepRecorder()
        report = self.run_pass(step, power_fn=lambda: True)
        self.assertEqual(report["outcome"], "skipped-battery")
        self.assertEqual(step.calls, [])

    def test_ignore_battery_overrides(self):
        report = self.run_pass(StepRecorder(), power_fn=lambda: True,
                               ignore_battery=True)
        self.assertEqual(report["outcome"], "ok")

    def test_an_unknown_power_state_proceeds(self):
        """A desktop with no battery must not skip every pass forever."""
        report = self.run_pass(StepRecorder(), power_fn=lambda: None)
        self.assertEqual(report["outcome"], "ok")

    def test_the_real_power_probe_never_raises(self):
        self.assertIn(ar.on_battery(), (True, False, None))


# =============================================================== advancing
class TestAdvancing(AutoRunBase):
    def test_terminal_jobs_are_not_advanced(self):
        step = StepRecorder(jobs=_job_rows("PUBLISHED", "IGNORED",
                                           "CANCELLED", "FAILED_PERMANENT",
                                           "DISCOVERED"))
        report = self.run_pass(step)
        self.assertEqual(len(step.named("autopilot")), 1)
        self.assertEqual(report["advanced"], 1)

    def test_max_jobs_caps_the_pass(self):
        step = StepRecorder(jobs=_job_rows(*(["DISCOVERED"] * 9)))
        report = self.run_pass(step, max_jobs=3)
        self.assertEqual(len(step.named("autopilot")), 3)
        self.assertEqual(report["advanced"], 3)

    def test_a_job_stopping_at_a_gate_does_not_fail_the_pass(self):
        """A held gate exits non-zero and is a NORMAL resting point. If that
        failed the pass, every night with a held gate would look like an
        outage and the real outages would be invisible."""
        step = StepRecorder({"autopilot": {"ok": False, "code": 1,
                                           "stdout": "", "stderr": ""}})
        report = self.run_pass(step)
        self.assertEqual(report["outcome"], "ok")
        self.assertEqual(report["exitCode"], ar.EXIT_OK)
        self.assertFalse(report["jobs"][0]["ok"])

    def test_the_gate_flags_reach_every_autopilot_call(self):
        step = StepRecorder(jobs=_job_rows("DISCOVERED", "DOWNLOADED"))
        self.run_pass(step, gate_flags=["--auto-source", "--auto-layout"])
        for call in step.named("autopilot"):
            self.assertIn("--auto-source", call)
            self.assertIn("--auto-layout", call)

    def test_the_operator_is_recorded_on_accepted_work(self):
        step = StepRecorder()
        self.run_pass(step, operator="Connor")
        call = step.named("autopilot")[0]
        self.assertIn("--accepted-by", call)
        self.assertEqual(call[call.index("--accepted-by") + 1], "Connor")

    def test_no_auto_accept_is_honoured(self):
        step = StepRecorder()
        self.run_pass(step, auto_accept=False)
        self.assertNotIn("--auto-accept", step.named("autopilot")[0])

    def test_a_failed_scan_still_advances_queued_jobs(self):
        """Discovery is a nice-to-have; jobs already queued still deserve a
        pass, so a scan failure is a warning, not an abort."""
        step = StepRecorder({"find-matches": {"ok": False, "code": 1,
                                              "stdout": "", "stderr": ""}})
        report = self.run_pass(step)
        self.assertEqual(report["outcome"], "ok")
        self.assertTrue(step.named("autopilot"))

    def test_unparseable_job_list_is_an_error_not_a_crash(self):
        step = StepRecorder({"list-jobs": {"ok": True, "code": 0,
                                           "stdout": "<html>nope</html>",
                                           "stderr": ""}})
        report = self.run_pass(step)
        self.assertEqual(report["outcome"], "list-failed")
        self.assertEqual(report["exitCode"], ar.EXIT_DOCTOR_FAILED)


# =================================================================== what-if
class TestWhatIf(AutoRunBase):
    def test_what_if_touches_nothing(self):
        step = StepRecorder()
        report = self.run_pass(step, what_if=True)
        self.assertEqual(report["outcome"], "what-if")
        self.assertEqual(step.calls, [])
        self.assertIsNone(report["log"], "what-if writes no log file")

    def test_what_if_leaves_no_lock_behind(self):
        self.run_pass(StepRecorder(), what_if=True)
        self.assertFalse(
            os.path.exists(os.path.join(self.log_dir, ar.LOCK_NAME)))


# =============================================================== log hygiene
class TestLogs(AutoRunBase):
    def test_a_pass_writes_a_log_and_a_json_summary(self):
        report = self.run_pass(StepRecorder())
        self.assertTrue(os.path.exists(report["log"]))
        self.assertTrue(os.path.exists(report["log"].replace(".log", ".json")))

    def test_the_summary_is_readable_back_as_the_last_pass(self):
        self.run_pass(StepRecorder())
        last = ar.last_pass(self.log_dir)
        self.assertIsNotNone(last)
        self.assertEqual(last["operator"], "TestOp")
        self.assertEqual(last["outcome"], "ok")

    def test_last_pass_on_an_empty_dir_is_none_not_an_error(self):
        self.assertIsNone(ar.last_pass(os.path.join(self.tmp.name, "nope")))

    def test_old_logs_are_pruned(self):
        """Something that runs unattended forever must not fill the disk
        with its own output."""
        os.makedirs(self.log_dir, exist_ok=True)
        for i in range(40):
            path = os.path.join(self.log_dir, f"auto-run_2020-01-{i:02d}.log")
            with open(path, "w", encoding="utf-8") as f:
                f.write("old\n")
            os.utime(path, (i, i))
        removed = ar.prune_logs(self.log_dir, keep=ar.KEEP_LOGS)
        remaining = [n for n in os.listdir(self.log_dir)
                     if n.endswith(".log")]
        self.assertEqual(len(remaining), ar.KEEP_LOGS)
        self.assertEqual(len(removed), 40 - ar.KEEP_LOGS)

    def test_pruning_keeps_the_newest(self):
        os.makedirs(self.log_dir, exist_ok=True)
        for i in range(5):
            path = os.path.join(self.log_dir, f"auto-run_{i}.log")
            with open(path, "w", encoding="utf-8") as f:
                f.write("x")
            os.utime(path, (i * 1000, i * 1000))
        ar.prune_logs(self.log_dir, keep=2)
        self.assertEqual(sorted(n for n in os.listdir(self.log_dir)
                                if n.endswith(".log")),
                         ["auto-run_3.log", "auto-run_4.log"])

    def test_format_report_survives_a_minimal_report(self):
        self.assertIsInstance(ar.format_report({}), str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
