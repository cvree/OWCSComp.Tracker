#!/usr/bin/env python3
"""
test_automation_detection_runner.py — Phase G: wiring an approved+extracted
map segment into the EXISTING ingest_map detector. All tests inject a fake
`run_fn` in place of the real (heavy, DB/template-dependent) ingest_map.run —
the CV correctness itself is already covered by pipeline/test_map_ingestion.py
and friends; this suite only proves the job-state wiring and error taxonomy.
Run: python3 pipeline/test_automation_detection_runner.py
"""
from __future__ import annotations
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from automation import detection_runner as dr  # noqa: E402
from automation import job_store as js  # noqa: E402
from automation import models  # noqa: E402
from automation import segmentation as seg  # noqa: E402
from automation import state_machine as sm  # noqa: E402


def _approved_segment(con, *, layout_id="owcs_jksix_qwc"):
    [sid] = seg.store_candidates(con, "vid1", "m-test",
                                 [{"start_time": 100.0, "end_time": 400.0,
                                   "confidence": 0.9, "signals": {}}])
    seg.approve_segment(con, sid, map_order=1, map_name="Nepal",
                        map_mode="Control", team_a="qad", team_b="twis",
                        side_assignment="team_a_left", layout_id=layout_id)
    row = seg.get_segment(con, sid)
    # extraction normally sets this; fake it directly for these unit tests.
    con.execute("UPDATE map_segments SET extracted_path=? WHERE id=?",
               ("data/worker/jobs/x/media/clip.mp4", sid))
    con.commit()
    return seg.get_segment(con, sid)


class TestBuildArgs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = js.JobStore(os.path.join(self.tmp.name, "a.sqlite"))
        self.con = self.store.con
        k = models.process_key("vid1", "v1")
        self.store.enqueue(models.KIND_PROCESS, k,
                          payload={"videoId": "vid1", "sourceUrl": "https://www.youtube.com/watch?v=vid1"},
                          state=sm.PROCESSING)
        self.job = self.store.get(k)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_build_args_from_approved_segment(self):
        segment = _approved_segment(self.con)
        args = dr.build_ingest_args(self.job, segment, write=False)
        self.assertEqual(args.start, 100.0)
        self.assertEqual(args.end, 400.0)
        self.assertEqual(args.clip_offset, 100.0)
        self.assertEqual(args.team_a, "qad")
        self.assertEqual(args.team_b, "twis")
        self.assertEqual(args.match, "m-test")
        self.assertEqual(args.map_order, 1)
        self.assertTrue(args.layout.endswith("owcs_jksix_qwc.json"))
        self.assertFalse(args.write)

    def test_ingest_id_stable_across_calls(self):
        segment = _approved_segment(self.con)
        a1 = dr.build_ingest_args(self.job, segment, write=False)
        a2 = dr.build_ingest_args(self.job, segment, write=True)
        self.assertEqual(a1.ingest_id, a2.ingest_id)

    def test_refuses_unextracted_segment(self):
        [sid] = seg.store_candidates(self.con, "vid1", "m-test",
                                     [{"start_time": 0.0, "end_time": 60.0,
                                       "confidence": 0.5, "signals": {}}])
        seg.approve_segment(self.con, sid, map_order=1, map_name="X",
                           map_mode="Control", team_a="a", team_b="b",
                           side_assignment="a_left", layout_id="l1")
        row = seg.get_segment(self.con, sid)
        with self.assertRaises(ValueError):
            dr.build_ingest_args(self.job, row, write=False)

    def test_refuses_unapproved_segment(self):
        [sid] = seg.store_candidates(self.con, "vid1", "m-test",
                                     [{"start_time": 0.0, "end_time": 60.0,
                                       "confidence": 0.5, "signals": {}}])
        row = seg.get_segment(self.con, sid)
        with self.assertRaises(ValueError):
            dr.build_ingest_args(self.job, row, write=False)

    def test_absolute_layout_path_passed_through(self):
        segment = _approved_segment(self.con, layout_id="/abs/path/x.json")
        args = dr.build_ingest_args(self.job, segment, write=False)
        self.assertEqual(args.layout, "/abs/path/x.json")


class TestRunDetection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = js.JobStore(os.path.join(self.tmp.name, "a.sqlite"))
        self.con = self.store.con
        self.k = models.process_key("vid1", "v1")
        self.store.enqueue(models.KIND_PROCESS, self.k,
                          payload={"videoId": "vid1"}, state=sm.PROCESSING)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _fake_run_ok(self, **stats_over):
        stats = dict(frames_sampled=60, gameplay_frames=55, skipped_frames=5,
                    rounds=3, confirmed_swaps=1, rejected_swaps=8,
                    setup_changes=2, calibration_health="ok",
                    detector_version="v1")
        stats.update(stats_over)

        def _run(args):
            result = {"stats": stats, "out_root": "reports/ingest/x"}
            if args.write:
                result["db"] = {"map_result_id": 1, "stints": 10, "swaps": 1,
                                "observations": 60, "bans": 0, "findings": 0}
            return result
        return _run

    def test_dry_run_success_moves_to_needs_review(self):
        segment = _approved_segment(self.con)
        job = self.store.get(self.k)
        result = dr.run_detection(self.store, job, segment, write=False,
                                  run_fn=self._fake_run_ok())
        self.assertTrue(result["ok"])
        final = self.store.get(self.k)
        self.assertEqual(final.state, sm.NEEDS_REVIEW)
        self.assertEqual(final.payload["detection"]["stats"]["confirmed_swaps"], 1)
        self.assertNotIn("db", final.payload["detection"])

    def test_write_pass_does_not_change_state(self):
        segment = _approved_segment(self.con)
        self.store.transition(self.k, sm.NEEDS_REVIEW)
        self.store.transition(self.k, sm.APPROVED)
        job = self.store.get(self.k)
        result = dr.commit_approved_detection(self.store, job, segment,
                                              run_fn=self._fake_run_ok())
        self.assertTrue(result["ok"])
        final = self.store.get(self.k)
        self.assertEqual(final.state, sm.APPROVED)
        self.assertIn("db", final.payload["detection"])
        self.assertEqual(final.payload["detection"]["db"]["stints"], 10)

    def test_commit_refuses_when_not_approved(self):
        segment = _approved_segment(self.con)
        job = self.store.get(self.k)  # still PROCESSING
        with self.assertRaises(ValueError):
            dr.commit_approved_detection(self.store, job, segment,
                                         run_fn=self._fake_run_ok())

    def test_layout_mismatch_classified_and_retryable(self):
        segment = _approved_segment(self.con)
        job = self.store.get(self.k)

        def _run(args):
            raise SystemExit("layout cannot scale to 1280x720: aspect ratio mismatch")
        result = dr.run_detection(self.store, job, segment, write=False, run_fn=_run)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errorCode"], "layout_mismatch")
        self.assertIn(self.store.get(self.k).state,
                      (sm.RETRY_SCHEDULED, sm.FAILED_PERMANENT))

    def test_no_valid_gameplay_frames_classified(self):
        segment = _approved_segment(self.con)
        job = self.store.get(self.k)

        def _run(args):
            raise SystemExit("cannot read a frame at offset 100.0")
        result = dr.run_detection(self.store, job, segment, write=False, run_fn=_run)
        self.assertEqual(result["errorCode"], "no_valid_gameplay_frames")

    def test_missing_templates_classified(self):
        segment = _approved_segment(self.con)
        job = self.store.get(self.k)

        def _run(args):
            raise FileNotFoundError("no such templates dir: templates/missing")
        result = dr.run_detection(self.store, job, segment, write=False, run_fn=_run)
        self.assertEqual(result["errorCode"], "missing_templates")

    def test_never_writes_on_failure(self):
        segment = _approved_segment(self.con)
        job = self.store.get(self.k)

        def _run(args):
            raise RuntimeError("boom")
        dr.run_detection(self.store, job, segment, write=False, run_fn=_run)
        # state must NOT have advanced to NEEDS_REVIEW on failure
        self.assertNotEqual(self.store.get(self.k).state, sm.NEEDS_REVIEW)


if __name__ == "__main__":
    unittest.main(verbosity=2)
