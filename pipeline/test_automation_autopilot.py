#!/usr/bin/env python3
"""
test_automation_autopilot.py — the free-agent loop (autopilot.run_autopilot
+ the convert-link/autopilot CLI wiring).

What must hold, per the module's own contract:
  * the loop chains automatic stages and STOPS at every human gate — source
    authorization, layout approval, segment review (without --auto-accept),
    detection review (ALWAYS, --auto-accept or not), publication;
  * --auto-accept goes through the SAME accept-proposed gate a human uses,
    and a refusal there stops the loop instead of loosening anything;
  * approved segments get their detection clip extracted automatically, and
    an extraction failure is a visible blocker;
  * all-reviewed segments advance NEEDS_REVIEW -> READY_FOR_DETECTION;
  * the loop can never spin (no-progress guard, max-steps cap) and never
    steals a live lock.

Heavy per-stage work is faked via the injectable hooks — the per-stage
gates have their own suites (test_automation_worker/ops/segmentation/...).
Run: python3 pipeline/test_automation_autopilot.py
"""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import proc_text  # noqa: E402  (UTF-8 subprocess decoding)
from automation import autopilot as ap  # noqa: E402
from automation import job_store as js  # noqa: E402
from automation import locks as lk  # noqa: E402
from automation import ops  # noqa: E402
from automation import segmentation as seg  # noqa: E402
from automation import state_machine as sm  # noqa: E402
from automation import worker  # noqa: E402


APPROVED_SOURCE = {"state": "approved", "autoApproved": True,
                   "reasonCode": "registry_channel",
                   "reason": "test fixture: verified official channel"}


class AutopilotBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = js.JobStore(os.path.join(self.tmp.name, "a.sqlite"))
        self.locks = lk.LockManager(self.store.con)
        self.con = self.store.con

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def create(self, video_id="vid1", *, source=APPROVED_SOURCE):
        job = ops.create_job_from_broadcast(
            self.store, match_id="m-test", video_id=video_id,
            source_url=f"https://www.youtube.com/watch?v={video_id}",
            channel_id="UCofficial", team_a="qad", team_b="twis",
            expected_layout_id="owcs_jksix_qwc")
        if source is not None:
            self.store.update_payload(job.job_key, {"source": source})
        return self.store.get(job.job_key)

    def run_ap(self, job_key, **kw):
        kw.setdefault("worker_id", "ap-test")
        kw.setdefault("run_one", self._fail_run_one)
        return ap.run_autopilot(self.store, self.locks, job_key, **kw)

    @staticmethod
    def _fail_run_one(*a, **kw):  # a test must opt in to stage behavior
        raise AssertionError("run_one_job should not have been called")

    def make_pending_segment(self, video_id="vid1", **overrides):
        cand = dict(start_time=100.0, end_time=700.0, confidence=0.9,
                    signals={})
        cand.update(overrides)
        [sid] = seg.store_candidates(self.con, video_id, "m-test", [cand])
        return sid

    def approve_segment(self, sid):
        return seg.approve_segment(
            self.con, sid, map_order=1, map_name="Nepal", map_mode="Control",
            team_a="qad", team_b="twis", side_assignment="team_a_left",
            layout_id="owcs_jksix_qwc")

    def set_extracted(self, sid, path="work/x/segment.mp4"):
        self.con.execute(
            "UPDATE map_segments SET extracted_path=? WHERE id=?", (path, sid))
        self.con.commit()


class TestHumanGates(AutopilotBase):
    def test_unauthorized_source_stops_before_anything_runs(self):
        job = self.create(source=None)  # create-job payload has no source block
        res = self.run_ap(job.job_key)
        self.assertTrue(res["ok"])  # a human gate is a GOOD stop
        self.assertEqual(res["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("approve-source", res["stopDetail"])
        self.assertEqual(res["steps"], [])

    def test_rejected_source_stops(self):
        job = self.create(source={"state": "rejected",
                                  "reason": "not an official broadcast"})
        res = self.run_ap(job.job_key)
        self.assertEqual(res["stop"], ap.STOP_HUMAN_GATE)

    def test_needs_layout_is_a_human_gate(self):
        job = self.create()
        for s in (sm.DOWNLOADING, sm.DOWNLOADED, sm.NEEDS_LAYOUT):
            self.store.transition(job.job_key, s)
        res = self.run_ap(job.job_key)
        self.assertTrue(res["ok"])
        self.assertEqual(res["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("approve-layout", res["stopDetail"])

    def test_segment_review_without_auto_accept_is_a_human_gate(self):
        job = self.create()
        for s in (sm.DOWNLOADING, sm.DOWNLOADED, sm.SEGMENTING,
                  sm.NEEDS_REVIEW):
            self.store.transition(job.job_key, s)
        self.make_pending_segment()
        res = self.run_ap(job.job_key)
        self.assertTrue(res["ok"])
        self.assertEqual(res["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("--auto-accept", res["stopDetail"])

    def test_detection_no_longer_waits_for_a_person(self):
        """PUBLISH-THEN-AUDIT. This used to be the gate that stopped every
        unattended run forever: approving comps into production was always
        a human decision, and unattended there is no human. The detector's
        own bar is what still holds — an unreadable slot is UNKNOWN and
        never becomes a composition at all."""
        job = self.create()
        for s in (sm.DOWNLOADING, sm.DOWNLOADED, sm.SEGMENTING,
                  sm.NEEDS_REVIEW):
            self.store.transition(job.job_key, s)
        sid = self.make_pending_segment()
        self.approve_segment(sid)
        self.set_extracted(sid)
        self.store.update_payload(job.job_key,
                                  {"detection": {"ingestId": "x"}})

        def fake_commit(store, lock_mgr, con, job_key, **kw):
            return {"ok": True, "summary": {"written": True}}

        res = self.run_ap(job.job_key, auto_accept=True, run_one=fake_commit)
        # It advanced itself past the old gate rather than stopping there.
        self.assertEqual(self.store.get(job.job_key).state, sm.APPROVED)
        self.assertNotIn("never automatic", res.get("stopDetail") or "")
        published = [st for st in res["steps"] if st["action"] == "publish"]
        self.assertTrue(published, "the review stage recorded no publish step")

    def test_publication_is_opt_in_per_run_because_it_writes(self):
        """Not a review gate — a write gate. Publication regenerates the
        export, runs the packaging and reproducibility checks and commits,
        so a local dry run must not do it by surprise."""
        job = self.create()
        for s in (sm.DOWNLOADING, sm.DOWNLOADED, sm.SEGMENTING,
                  sm.NEEDS_REVIEW, sm.READY_FOR_DETECTION, sm.PROCESSING,
                  sm.NEEDS_REVIEW, sm.APPROVED):
            self.store.transition(job.job_key, s)
        sid = self.make_pending_segment()
        self.approve_segment(sid)
        self.set_extracted(sid)

        def fake_commit(store, lock_mgr, con, job_key, **kw):
            return {"ok": True, "summary": {"written": True}}

        res = self.run_ap(job.job_key, run_one=fake_commit)
        self.assertTrue(res["ok"])
        self.assertEqual(res["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("--publish", res["stopDetail"])
        # The reason given must not be the removed review gate.
        self.assertNotIn("supervised", res["stopDetail"])
        self.assertEqual(self.store.get(job.job_key).state, sm.APPROVED)

    def test_with_publish_it_runs_the_publication_step(self):
        job = self.create()
        for s in (sm.DOWNLOADING, sm.DOWNLOADED, sm.SEGMENTING,
                  sm.NEEDS_REVIEW, sm.READY_FOR_DETECTION, sm.PROCESSING,
                  sm.NEEDS_REVIEW, sm.APPROVED):
            self.store.transition(job.job_key, s)
        sid = self.make_pending_segment()
        self.approve_segment(sid)
        self.set_extracted(sid)
        calls = []

        def fake_commit(store, lock_mgr, con, job_key, **kw):
            return {"ok": True, "summary": {"written": True}}

        def fake_publish(store, j):
            calls.append(j.job_key)
            return {"ok": True}

        res = self.run_ap(job.job_key, run_one=fake_commit,
                          publish=True, publish_fn=fake_publish)
        self.assertEqual(calls, [job.job_key])
        self.assertTrue(res["ok"])
        self.assertEqual(res["stop"], ap.STOP_TERMINAL)
        self.assertIn("auditable", res["stopDetail"])

    def test_a_refused_publication_is_reported_not_swallowed(self):
        """publish_job enforces preconditions, export validation and the
        packaging check. Removing the human review removed the WAIT, not
        the checks — a refusal must still stop the loop and say why."""
        job = self.create()
        for s in (sm.DOWNLOADING, sm.DOWNLOADED, sm.SEGMENTING,
                  sm.NEEDS_REVIEW, sm.READY_FOR_DETECTION, sm.PROCESSING,
                  sm.NEEDS_REVIEW, sm.APPROVED):
            self.store.transition(job.job_key, s)
        sid = self.make_pending_segment()
        self.approve_segment(sid)
        self.set_extracted(sid)

        def fake_commit(store, lock_mgr, con, job_key, **kw):
            return {"ok": True, "summary": {"written": True}}

        for publish_fn, needle in (
            (lambda s_, j_: {"ok": False, "code": "export_drift",
                             "reason": "the committed export is not "
                                       "reproducible"}, "reproducible"),
            (lambda s_, j_: (_ for _ in ()).throw(RuntimeError("boom")),
             "RuntimeError"),
        ):
            with self.subTest(needle=needle):
                res = self.run_ap(job.job_key, run_one=fake_commit,
                                  publish=True, publish_fn=publish_fn)
                self.assertFalse(res["ok"])
                self.assertEqual(res["stop"], ap.STOP_BLOCKED)
                self.assertIn(needle, res["stopDetail"])


class TestLoopMechanics(AutopilotBase):
    def test_chains_stages_to_the_segment_review_gate(self):
        """ARCHIVED -> download -> segment -> NEEDS_REVIEW in ONE call, with
        every stage recorded and the lock released at the end."""
        job = self.create()

        def fake_run_one(store, lock_mgr, con, job_key, **kw):
            j = store.get(job_key)
            if j.state == sm.ARCHIVED:
                store.transition(job_key, sm.DOWNLOADING)
                store.transition(job_key, sm.DOWNLOADED)
                return {"ok": True, "path": "clip.mp4"}
            if j.state == sm.DOWNLOADED:
                store.transition(job_key, sm.SEGMENTING)
                store.transition(job_key, sm.NEEDS_REVIEW)
                seg.store_candidates(con, "vid1", "m-test",
                                     [{"start_time": 0.0, "end_time": 60.0,
                                       "confidence": 0.8, "signals": {}}])
                return {"ok": True, "candidates": 1}
            raise AssertionError(f"unexpected state {j.state}")

        res = self.run_ap(job.job_key, run_one=fake_run_one)
        self.assertTrue(res["ok"])
        self.assertEqual(res["stop"], ap.STOP_HUMAN_GATE)
        self.assertEqual(self.store.get(job.job_key).state, sm.NEEDS_REVIEW)
        self.assertEqual(
            [s["action"] for s in res["steps"]],
            ["run-one-job", "run-one-job"])
        self.assertIsNone(
            self.locks.holder(worker.resource_for(job)))

    def test_approved_source_in_discovered_advances_to_ready(self):
        job = self.create()
        # rewind to DISCOVERED (as if approved manually after intake)
        self.store.con.execute(
            "UPDATE jobs SET state=? WHERE job_key=?",
            (sm.DISCOVERED, job.job_key))
        self.store.con.commit()

        def fake_run_one(store, lock_mgr, con, job_key, **kw):
            self.assertEqual(store.get(job_key).state, sm.ARCHIVED)
            return {"ok": False, "reason": "stop here"}

        res = self.run_ap(job.job_key, run_one=fake_run_one)
        self.assertEqual(res["steps"][0]["action"], "advance-to-ready")
        self.assertEqual(res["stop"], ap.STOP_BLOCKED)

    def test_blocked_stage_stops_with_its_reason(self):
        job = self.create()

        def fake_run_one(store, lock_mgr, con, job_key, **kw):
            return {"ok": False, "errorCode": "network_stall",
                    "errorMessage": "yt-dlp stalled"}

        res = self.run_ap(job.job_key, run_one=fake_run_one)
        self.assertFalse(res["ok"])
        self.assertEqual(res["stop"], ap.STOP_BLOCKED)
        self.assertIn("yt-dlp stalled", res["stopDetail"])

    def test_no_progress_guard_refuses_to_spin(self):
        job = self.create()

        def fake_run_one(store, lock_mgr, con, job_key, **kw):
            return {"ok": True}  # claims success, changes nothing

        res = self.run_ap(job.job_key, run_one=fake_run_one)
        self.assertFalse(res["ok"])
        self.assertEqual(res["stop"], ap.STOP_NO_PROGRESS)
        self.assertEqual(len(res["steps"]), 1)

    def test_live_lock_is_never_stolen(self):
        job = self.create()
        self.locks.acquire(worker.resource_for(job), "someone-else")
        res = self.run_ap(job.job_key)
        self.assertFalse(res["ok"])
        self.assertEqual(res["stop"], ap.STOP_LOCKED)
        # and the live holder kept the lease
        holder = self.locks.holder(worker.resource_for(job))
        self.assertEqual(holder.worker_id, "someone-else")

    def test_terminal_state_is_a_clean_stop(self):
        job = self.create()
        self.store.cancel(job.job_key, reason="test")
        res = self.run_ap(job.job_key)
        self.assertTrue(res["ok"])
        self.assertEqual(res["stop"], ap.STOP_TERMINAL)

    def test_unknown_job_raises(self):
        with self.assertRaises(KeyError):
            self.run_ap("record:nope:source")

    def test_downloading_state_reports_blocked(self):
        job = self.create()
        self.store.transition(job.job_key, sm.DOWNLOADING)
        res = self.run_ap(job.job_key)
        self.assertEqual(res["stop"], ap.STOP_BLOCKED)
        self.assertIn("resume-job", res["stopDetail"])


class TestAutoAccept(AutopilotBase):
    def _review_job(self):
        job = self.create()
        for s in (sm.DOWNLOADING, sm.DOWNLOADED, sm.SEGMENTING,
                  sm.NEEDS_REVIEW):
            self.store.transition(job.job_key, s)
        return job

    def test_auto_accept_runs_propose_then_accept_then_advances(self):
        job = self._review_job()
        sid = self.make_pending_segment()
        calls = {"proposed": [], "accepted": []}

        def propose(store, j, segment):
            calls["proposed"].append(segment["id"])

        def accept(store, j, segment):
            calls["accepted"].append(segment["id"])
            return self.approve_segment(segment["id"])

        def fake_run_one(store, lock_mgr, con, job_key, **kw):
            j = store.get(job_key)
            self.assertEqual(j.state, sm.READY_FOR_DETECTION)
            return {"ok": False, "reason": "stop for the test"}

        res = self.run_ap(job.job_key, auto_accept=True,
                          propose_fn=propose, accept_fn=accept,
                          extract_fn=lambda s, j, g: self.set_extracted(g["id"]),
                          run_one=fake_run_one)
        self.assertEqual(calls["proposed"], [sid])
        self.assertEqual(calls["accepted"], [sid])
        # the review handler advanced the job before detection was attempted
        actions = [s["action"] for s in res["steps"]]
        self.assertIn("advance", actions)
        self.assertIn(f"extract-segment #{sid}", actions)

    def test_auto_accept_refusal_keeps_the_gate_and_stops(self):
        job = self._review_job()
        sid = self.make_pending_segment()

        def accept(store, j, segment):
            raise ValueError("mapOrder still UNKNOWN — edit by hand instead")

        res = self.run_ap(job.job_key, auto_accept=True,
                          propose_fn=lambda *a: None, accept_fn=accept)
        self.assertTrue(res["ok"])  # gate held = legitimate human stop
        self.assertEqual(res["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("UNKNOWN", res["stopDetail"])
        row = seg.get_segment(self.con, sid)
        self.assertEqual(row["review_status"], "pending")

    def test_all_segments_rejected_is_a_blocker_not_a_crash(self):
        job = self._review_job()
        sid = self.make_pending_segment()
        seg.reject_segment(self.con, sid, reason="desk content")
        res = self.run_ap(job.job_key, auto_accept=True)
        self.assertFalse(res["ok"])
        self.assertEqual(res["stop"], ap.STOP_BLOCKED)
        self.assertIn("no approved segment", res["stopDetail"])


class TestExtraction(AutopilotBase):
    def test_extraction_failure_is_a_visible_blocker(self):
        job = self.create()
        for s in (sm.DOWNLOADING, sm.DOWNLOADED, sm.SEGMENTING,
                  sm.NEEDS_REVIEW, sm.READY_FOR_DETECTION):
            self.store.transition(job.job_key, s)
        sid = self.make_pending_segment()
        self.approve_segment(sid)

        def extract(store, j, segment):
            raise ValueError("full-resolution source media is not on disk")

        res = self.run_ap(job.job_key, extract_fn=extract)
        self.assertFalse(res["ok"])
        self.assertEqual(res["stop"], ap.STOP_BLOCKED)
        self.assertIn("could not extract", res["stopDetail"])

    def test_already_extracted_segments_are_not_recut(self):
        job = self.create()
        for s in (sm.DOWNLOADING, sm.DOWNLOADED, sm.SEGMENTING,
                  sm.NEEDS_REVIEW, sm.READY_FOR_DETECTION):
            self.store.transition(job.job_key, s)
        sid = self.make_pending_segment()
        self.approve_segment(sid)
        self.set_extracted(sid)

        def fake_run_one(store, lock_mgr, con, job_key, **kw):
            return {"ok": False, "reason": "stop for the test"}

        res = self.run_ap(
            job.job_key, run_one=fake_run_one,
            extract_fn=lambda *a: (_ for _ in ()).throw(
                AssertionError("must not re-extract")))
        self.assertNotIn("extract-segment", str(res["steps"]))


class TestCliWiring(unittest.TestCase):
    """The two commands parse, run offline, and stop honestly. Uses a real
    subprocess (like the import-isolation suite) against a temp DB."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "a.sqlite")
        self.cli = os.path.join(HERE, "automation", "cli.py")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, self.cli, "--db", self.db, *args],
            capture_output=True, text=True, timeout=120,
            cwd=os.path.dirname(HERE), **proc_text.PIPE_TEXT)

    def test_convert_link_offline_stops_at_source_gate(self):
        res = self.run_cli("convert-link", "--url",
                           "https://youtu.be/AAAAAAAAAAA?t=90",
                           "--no-metadata", "--no-export")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("human-gate", res.stdout)
        self.assertIn("approve-source", res.stdout)

    def test_autopilot_by_url_resolves_the_same_job(self):
        self.run_cli("ingest-link", "--url",
                     "https://youtu.be/AAAAAAAAAAA", "--no-metadata")
        res = self.run_cli("autopilot", "--url",
                           "https://www.youtube.com/watch?v=AAAAAAAAAAA",
                           "--no-export")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("approve-source", res.stdout)

    def test_autopilot_unknown_job_refuses_with_guidance(self):
        res = self.run_cli("autopilot", "--job", "record:zzzzzzzzzzz:source",
                           "--no-export")
        self.assertEqual(res.returncode, 1)
        self.assertIn("paste the link first", res.stdout)

    def test_autopilot_requires_exactly_one_of_job_url(self):
        res = self.run_cli("autopilot", "--no-export")
        self.assertEqual(res.returncode, 1)
        self.assertIn("exactly one", res.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
