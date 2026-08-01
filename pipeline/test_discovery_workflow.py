#!/usr/bin/env python3
"""
test_discovery_workflow.py — regression coverage for the discovery #28
false-success incident's OTHER half: even with cli.py fixed to never crash
on a missing cv2, `python3 ... | tee run-output.txt` without `pipefail` can
still mask a REAL failure (any future one) behind `tee`'s own zero exit
status. This suite proves:

  1. The workflow YAML itself is valid and every report-producing step uses
     the hardened `set -euo pipefail` + `test -s` pattern (never `tee`
     alone), `upload-artifact` requires a real file
     (`if-no-files-found: error`), and the pinned actions are current.
  2. At the shell level (not just by reading the YAML): a failing command
     piped through plain `tee` reports success; the same pipeline under
     `set -euo pipefail` reports the real (non-zero) exit code — this is
     the exact mechanism the fix relies on, demonstrated directly.
  3. `test -s` fails validation on an empty report and passes on a real one.

Run: python3 pipeline/test_discovery_workflow.py
"""
from __future__ import annotations
import os
import shlex
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
WORKFLOW_PATH = os.path.join(REPO_ROOT, ".github", "workflows", "discovery.yml")


def _sh(path: str) -> str:
    """Make a path safe to interpolate into a `bash -c` string.

    On Windows, os.path.join gives backslash separators; bash's word
    parser treats an unquoted backslash as an escape character and
    silently drops it, mangling the path (e.g. `C:\\Users\\x` becomes
    `C:Usersx`). Forward slashes are accepted by both native bash and
    Windows' own APIs, so they're safe on every platform this suite runs.
    shlex.quote then guards against spaces in the path (this repo's own
    checkout dir has one) breaking bash's word-splitting."""
    return shlex.quote(path.replace(os.sep, "/"))


REPORT_MODES = [
    "verify-channels", "calendar-dryrun", "broadcast-dryrun", "coverage",
    "teams-dryrun", "team-coverage", "team-assets-dryrun", "match-audit",
    "match-repair-dryrun", "export-dryrun",
]


def _load_yaml():
    import yaml  # pip-installed in this environment; not a stdlib dep of
    # the pipeline itself (config.py's own note about "no PyYAML" concerns
    # the automation config LOADER, not this test's validation tooling).
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestWorkflowYamlValid(unittest.TestCase):
    def test_yaml_parses(self):
        data = _load_yaml()
        self.assertIn("jobs", data)
        self.assertIn("discover", data["jobs"])

    def test_steps_are_a_nonempty_list(self):
        data = _load_yaml()
        steps = data["jobs"]["discover"]["steps"]
        self.assertGreater(len(steps), 10)

    def _step_by_id(self, step_id: str) -> dict:
        data = _load_yaml()
        for s in data["jobs"]["discover"]["steps"]:
            if s.get("id") == step_id:
                return s
        raise AssertionError(f"no step with id={step_id!r}")

    def test_every_report_step_has_an_id(self):
        # id: <name> is what lets the Summary step reference each one's
        # real conclusion — a step with a `tee`d report and no id can't be
        # reported on honestly.
        ids = ["verify_channels", "calendar_dryrun", "broadcast_dryrun",
              "coverage_report", "teams_dryrun", "team_coverage",
              "team_assets_dryrun", "match_audit", "match_repair_dryrun",
              "export_dryrun"]
        for step_id in ids:
            self._step_by_id(step_id)  # raises if missing

    def test_every_report_step_uses_pipefail_and_captures_stderr(self):
        ids = ["verify_channels", "calendar_dryrun", "broadcast_dryrun",
              "coverage_report", "teams_dryrun", "team_coverage",
              "team_assets_dryrun", "match_audit", "match_repair_dryrun",
              "export_dryrun"]
        for step_id in ids:
            step = self._step_by_id(step_id)
            run = step.get("run", "")
            with self.subTest(step=step_id):
                self.assertIn("set -euo pipefail", run)
                self.assertIn("2>&1", run, "must capture stderr, not just stdout")
                self.assertIn("test -s run-output.txt", run,
                             "must hard-fail on an empty report")

    def test_no_bare_tee_without_pipefail_guard(self):
        """Belt-and-suspenders: no `run:` block anywhere pipes into `tee`
        without `set -euo pipefail` appearing earlier in the SAME block —
        the exact incident pattern must never reappear undetected."""
        data = _load_yaml()
        for step in data["jobs"]["discover"]["steps"]:
            run = step.get("run", "")
            if "| tee" not in run:
                continue
            with self.subTest(step=step.get("name")):
                pipefail_pos = run.find("set -euo pipefail")
                tee_pos = run.find("| tee")
                self.assertNotEqual(pipefail_pos, -1,
                                   f"{step.get('name')!r} pipes into tee without pipefail")
                self.assertLess(pipefail_pos, tee_pos,
                               f"{step.get('name')!r}: pipefail must be set BEFORE the tee pipeline")

    def test_upload_artifact_requires_a_real_file(self):
        data = _load_yaml()
        steps = data["jobs"]["discover"]["steps"]
        upload = next(s for s in steps if s.get("uses", "").startswith("actions/upload-artifact"))
        self.assertEqual(upload["with"].get("if-no-files-found"), "error")

    def test_summary_step_reflects_report_step_conclusion(self):
        data = _load_yaml()
        steps = data["jobs"]["discover"]["steps"]
        summary = next(s for s in steps if s.get("name") == "Summary")
        env = summary.get("env", {})
        self.assertIn("REPORT_STEP_CONCLUSION", env)
        run = summary.get("run", "")
        self.assertIn("FAILED", run,
                     "the summary must be able to say a report step failed, "
                     "not just silently omit it")

    def test_actions_are_pinned_to_current_versions(self):
        data = _load_yaml()
        steps = data["jobs"]["discover"]["steps"]
        uses = {s["uses"].split("@")[0]: s["uses"] for s in steps if "uses" in s}
        self.assertEqual(uses["actions/checkout"], "actions/checkout@v6")
        self.assertEqual(uses["actions/setup-python"], "actions/setup-python@v6")
        self.assertEqual(uses["actions/upload-artifact"], "actions/upload-artifact@v6")
        # No actions/download-artifact is used in this workflow today; if one
        # is ever added it must be pinned the same way — not asserted here
        # since there is nothing to pin yet.


class TestPipefailMechanism(unittest.TestCase):
    """Prove the actual shell mechanism, independent of the YAML: this is
    what discovery #28 got wrong and what the fix relies on."""

    def test_failing_command_through_bare_tee_reports_success(self):
        """Reproduces the incident exactly: a failing command piped to
        `tee` (no pipefail) still exits 0 — tee's own success masks it."""
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "run-output.txt")
            res = subprocess.run(
                ["bash", "-c", f"python3 -c 'import sys; print(\"partial\"); "
                              f"sys.exit(1)' | tee {_sh(out)}"],
                capture_output=True, text=True)
            self.assertEqual(res.returncode, 0,
                            "this is the bug: tee's exit code hides the "
                            "failing command's real status")
            self.assertTrue(os.path.exists(out))

    def test_same_failure_under_pipefail_reports_failure(self):
        """The fix: with pipefail, the pipeline's exit status is the
        FIRST failing command's — python3's — not tee's."""
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "run-output.txt")
            res = subprocess.run(
                ["bash", "-c", f"set -euo pipefail; python3 -c 'import sys; "
                              f"print(\"partial\"); sys.exit(1)' 2>&1 | tee {_sh(out)}"],
                capture_output=True, text=True)
            self.assertNotEqual(res.returncode, 0,
                               "pipefail must surface the real python failure")

    def test_stderr_is_captured_in_the_report_with_2_and_1(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "run-output.txt")
            subprocess.run(
                ["bash", "-c",
                 f"set -euo pipefail; "
                 f"(python3 -c 'import sys; print(\"to stderr\", file=sys.stderr)'; true) "
                 f"2>&1 | tee {_sh(out)}"],
                capture_output=True, text=True)
            with open(out, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("to stderr", content)

    def test_empty_report_fails_validation(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "run-output.txt")
            open(out, "w", encoding="utf-8").close()  # empty file
            res = subprocess.run(["bash", "-c", f"test -s {_sh(out)}"])
            self.assertNotEqual(res.returncode, 0)

    def test_nonempty_report_passes_validation(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "run-output.txt")
            with open(out, "w", encoding="utf-8") as f:
                f.write("some real discovery output\n")
            res = subprocess.run(["bash", "-c", f"test -s {_sh(out)}"])
            self.assertEqual(res.returncode, 0)

    def test_a_real_successful_dry_run_produces_a_nonempty_report(self):
        """End-to-end version of the same guarantee, via the real CLI (the
        exact command discovery.yml's mode=coverage step runs)."""
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "automation.sqlite")
            out = os.path.join(d, "run-output.txt")
            cli_path = os.path.join(HERE, "automation", "cli.py")
            script = (f"set -euo pipefail; "
                     f"python3 {_sh(cli_path)} "
                     f"--db {_sh(db_path)} coverage --window 1 2>&1 | tee {_sh(out)}; "
                     f"test -s {_sh(out)}")
            res = subprocess.run(["bash", "-c", script], cwd=REPO_ROOT,
                                capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertTrue(os.path.getsize(out) > 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
