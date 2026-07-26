#!/usr/bin/env python3
"""
test_automation_ops.py — Phase 1 operator surface (create/list/show/claim/
release/retry/cancel/reset-stale-lock/resume) + the automatic per-state
driver `run_one_job` + the Phase 7 job-coverage report. Heavy per-stage work
(actual download/detection) is exercised in test_automation_worker.py /
test_automation_detection_runner.py — here we monkeypatch those calls to
prove ops.py drives them correctly for each state, without redoing their
own coverage.
Run: python3 pipeline/test_automation_ops.py
"""
from __future__ import annotations
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from automation import job_store as js  # noqa: E402
from automation import locks as lk  # noqa: E402
from automation import models  # noqa: E402
from automation import ops  # noqa: E402
from automation import segmentation as seg  # noqa: E402
from automation import state_machine as sm  # noqa: E402


class OpsTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = js.JobStore(os.path.join(self.tmp.name, "a.sqlite"))
        self.locks = lk.LockManager(self.store.con)
        self.con = self.store.con

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def create(self, video_id="vid1"):
        return ops.create_job_from_broadcast(
            self.store, match_id="m-test", video_id=video_id,
            source_url=f"https://www.youtube.com/watch?v={video_id}",
            channel_id="UCofficial", team_a="qad", team_b="twis",
            tournament_id="owcs-2026", region="na", language="en",
            broadcast_authority="UCofficial", expected_layout_id="owcs_jksix_qwc")


class TestCreateJob(OpsTestBase):
    def test_idempotent_creation(self):
        a = self.create()
        b = self.create()
        self.assertEqual(a.job_key, b.job_key)
        self.assertEqual(len(self.store.list_jobs()), 1)
        self.assertEqual(a.state, sm.ARCHIVED)
        self.assertEqual(a.payload["teamA"], "qad")

    def test_different_videos_are_different_jobs(self):
        self.create("vid1")
        self.create("vid2")
        self.assertEqual(len(self.store.list_jobs()), 2)


class TestListShow(OpsTestBase):
    def test_list_and_show(self):
        self.create("vid1")
        self.create("vid2")
        self.assertEqual(len(ops.list_jobs(self.store)), 2)
        job = ops.show_job(self.store, models.record_key("vid1"))
        self.assertEqual(job.payload["videoId"], "vid1")
        self.assertIsNone(ops.show_job(self.store, "record:nope"))


class TestClaimReleaseRetryCancel(OpsTestBase):
    def test_claim_next_job_locks_resource(self):
        job = self.create()
        claimed = ops.claim_next_job(self.store, self.locks, "w1")
        self.assertEqual(claimed.job_key, job.job_key)
        from automation import worker
        self.assertIsNotNone(self.locks.holder(worker.resource_for(job)))

    def test_release_job_clears_worker_and_lock(self):
        job = self.create()
        ops.claim_next_job(self.store, self.locks, "w1")
        ops.release_job(self.store, self.locks, job.job_key, "w1")
        self.assertIsNone(self.store.get(job.job_key).worker_id)
        from automation import worker
        self.assertIsNone(self.locks.holder(worker.resource_for(job)))

    def test_retry_job_delegates_to_store(self):
        job = self.create()
        self.store.record_attempt(job.job_key, ok=False, error_code="E1")
        retried = ops.retry_job(self.store, job.job_key)
        self.assertEqual(retried.state, sm.RETRY_SCHEDULED)
        # expedited to "now" rather than the full backoff window — immediately claimable
        claimed = self.store.claim_next([models.KIND_RECORD], "w-retry")
        self.assertEqual(claimed.job_key, job.job_key)

    def test_cancel_job(self):
        job = self.create()
        cancelled = ops.cancel_job(self.store, self.locks, job.job_key,
                                   reason="operator stop")
        self.assertEqual(cancelled.state, sm.CANCELLED)

    def test_reset_stale_lock(self):
        job = self.create()
        from automation import worker
        import datetime as dt
        past = dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)
        self.locks.acquire(worker.resource_for(job), "dead", now=past)
        cleared = ops.reset_stale_lock(self.store, self.locks, job.job_key)
        self.assertTrue(cleared)
        self.assertIsNone(self.locks.holder(worker.resource_for(job)))


class TestRunOneJob(OpsTestBase):
    def test_archived_state_calls_worker_download(self):
        job = self.create()
        with mock.patch("automation.ops.worker.download_job") as m:
            m.return_value = {"ok": True, "path": "x.mp4"}
            result = ops.run_one_job(self.store, self.locks, self.con,
                                     job.job_key, worker_id="w1")
        self.assertTrue(result["ok"])
        m.assert_called_once()

    def test_downloading_state_reports_in_progress(self):
        job = self.create()
        self.store.transition(job.job_key, sm.DOWNLOADING)
        result = ops.run_one_job(self.store, self.locks, self.con,
                                 job.job_key, worker_id="w1")
        self.assertFalse(result["ok"])
        self.assertIn("in progress", result["reason"])

    def test_downloaded_state_generates_segments_and_moves_to_needs_review(self):
        job = self.create()
        self.store.transition(job.job_key, sm.DOWNLOADING)
        self.store.transition(job.job_key, sm.DOWNLOADED)
        self.store.update_payload(job.job_key, {
            "media": {"localPath": "data/worker/jobs/x/media/clip.mp4",
                      "videoId": "vid1"},
        })
        # ops.py imports `capture`/`segmentation` LAZILY inside run_one_job
        # (never at module level — see the cv2-import-isolation fix), so
        # there is no `automation.ops.capture`/`automation.ops.seg`
        # attribute to patch. Patch the real source modules instead; the
        # lazy `import capture` / `from . import segmentation as seg`
        # inside run_one_job resolve to these same sys.modules entries.
        with mock.patch("capture.load_layout") as load_layout, \
             mock.patch("automation.segmentation.generate_candidates") as gen:
            load_layout.return_value = {"frame_width": 1920, "frame_height": 1080}
            gen.return_value = [{"start_time": 0.0, "end_time": 60.0,
                                "confidence": 0.8, "signals": {}}]
            result = ops.run_one_job(self.store, self.locks, self.con,
                                     job.job_key, worker_id="w1")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["candidates"], 1)
        self.assertEqual(self.store.get(job.job_key).state, sm.NEEDS_REVIEW)
        self.assertEqual(len(seg.list_segments(self.con, video_id="vid1")), 1)

    def test_downloaded_state_without_media_refuses(self):
        job = self.create()
        self.store.transition(job.job_key, sm.DOWNLOADING)
        self.store.transition(job.job_key, sm.DOWNLOADED)
        result = ops.run_one_job(self.store, self.locks, self.con,
                                 job.job_key, worker_id="w1")
        self.assertFalse(result["ok"])

    def test_needs_review_reports_waiting_on_human(self):
        job = self.create()
        for s in (sm.DOWNLOADING, sm.DOWNLOADED, sm.SEGMENTING, sm.NEEDS_REVIEW):
            self.store.transition(job.job_key, s)
        result = ops.run_one_job(self.store, self.locks, self.con,
                                 job.job_key, worker_id="w1")
        self.assertFalse(result["ok"])
        self.assertIn("human", result["reason"])

    def test_ready_for_detection_calls_run_detection(self):
        job = self.create()
        for s in (sm.DOWNLOADING, sm.DOWNLOADED, sm.SEGMENTING, sm.NEEDS_REVIEW,
                  sm.READY_FOR_DETECTION):
            self.store.transition(job.job_key, s)
        [sid] = seg.store_candidates(self.con, "vid1", "m-test",
                                     [{"start_time": 0.0, "end_time": 60.0,
                                       "confidence": 0.9, "signals": {}}])
        seg.approve_segment(self.con, sid, map_order=1, map_name="Nepal",
                           map_mode="Control", team_a="qad", team_b="twis",
                           side_assignment="a_left", layout_id="l1")
        with mock.patch("automation.ops.dr.run_detection") as m:
            m.return_value = {"ok": True, "summary": {}}
            result = ops.run_one_job(self.store, self.locks, self.con,
                                     job.job_key, worker_id="w1")
        self.assertTrue(result["ok"])
        m.assert_called_once()
        self.assertEqual(self.store.get(job.job_key).state, sm.PROCESSING)

    def test_ready_for_detection_without_approved_segment_refuses(self):
        job = self.create()
        for s in (sm.DOWNLOADING, sm.DOWNLOADED, sm.SEGMENTING, sm.NEEDS_REVIEW,
                  sm.READY_FOR_DETECTION):
            self.store.transition(job.job_key, s)
        result = ops.run_one_job(self.store, self.locks, self.con,
                                 job.job_key, worker_id="w1")
        self.assertFalse(result["ok"])

    def test_approved_state_calls_commit_detection(self):
        job = self.create()
        for s in (sm.DOWNLOADING, sm.DOWNLOADED, sm.SEGMENTING, sm.NEEDS_REVIEW,
                  sm.READY_FOR_DETECTION, sm.PROCESSING, sm.NEEDS_REVIEW,
                  sm.APPROVED):
            self.store.transition(job.job_key, s)
        [sid] = seg.store_candidates(self.con, "vid1", "m-test",
                                     [{"start_time": 0.0, "end_time": 60.0,
                                       "confidence": 0.9, "signals": {}}])
        seg.approve_segment(self.con, sid, map_order=1, map_name="Nepal",
                           map_mode="Control", team_a="qad", team_b="twis",
                           side_assignment="a_left", layout_id="l1")
        with mock.patch("automation.ops.dr.commit_approved_detection") as m:
            m.return_value = {"ok": True}
            result = ops.run_one_job(self.store, self.locks, self.con,
                                     job.job_key, worker_id="w1")
        self.assertTrue(result["ok"])
        m.assert_called_once()

    def test_unknown_state_reports_no_automatic_action(self):
        job = self.create()
        for s in (sm.DOWNLOADING, sm.DOWNLOADED, sm.SEGMENTING, sm.NEEDS_REVIEW,
                  sm.READY_FOR_DETECTION, sm.PROCESSING, sm.NEEDS_REVIEW,
                  sm.APPROVED, sm.PUBLISHED):
            self.store.transition(job.job_key, s)
        result = ops.run_one_job(self.store, self.locks, self.con,
                                 job.job_key, worker_id="w1")
        self.assertFalse(result["ok"])
        self.assertIn("no automatic action", result["reason"])


class TestCoverageReport(OpsTestBase):
    def test_report_shape_and_blocked_listing(self):
        job = self.create()
        self.store.record_attempt(job.job_key, ok=False, error_code="E1")
        report = ops.build_job_coverage_report(self.store)
        self.assertIn("countsByState", report)
        self.assertEqual(report["totalJobs"], 1)
        self.assertEqual(len(report["blocked"]), 1)
        self.assertEqual(report["blocked"][0]["jobKey"], job.job_key)

    def test_no_blocked_jobs_when_healthy(self):
        self.create()
        report = ops.build_job_coverage_report(self.store)
        self.assertEqual(report["blocked"], [])

    def test_recommended_next_action_covers_every_state(self):
        job = self.create()
        for state in sm.ALL_STATES:
            job.state = state
            action = ops.recommended_next_action(job)
            self.assertIsInstance(action, str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
