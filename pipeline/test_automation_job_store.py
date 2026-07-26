#!/usr/bin/env python3
"""
test_automation_job_store.py — idempotent enqueue, validated transitions,
retry/backoff, dead-letter, and worker claiming. No network.
Run: python3 pipeline/test_automation_job_store.py
"""
from __future__ import annotations
import datetime as dt
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from automation import job_store as js  # noqa: E402
from automation import models  # noqa: E402
from automation import state_machine as sm  # noqa: E402
from automation.config import AutomationConfig, DEFAULTS  # noqa: E402


def _cfg(**over):
    vals = dict(DEFAULTS)
    vals.update(over)
    return AutomationConfig(values=vals)


class TestJobStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "automation.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def store(self, config=None):
        return js.JobStore(self.db, config=config)

    def test_enqueue_is_idempotent(self):
        s = self.store()
        try:
            k = models.match_key("1-abc")
            a = s.enqueue(models.KIND_DISCOVERY, k, payload={"x": 1})
            b = s.enqueue(models.KIND_DISCOVERY, k, payload={"x": 2})
            self.assertEqual(a.job_key, b.job_key)
            # Second enqueue does NOT overwrite payload or duplicate the row.
            self.assertEqual(b.payload, {"x": 1})
            self.assertEqual(len(s.list_jobs()), 1)
        finally:
            s.close()

    def test_unknown_kind_rejected(self):
        s = self.store()
        try:
            with self.assertRaises(ValueError):
                s.enqueue("bogus", "match:z")
        finally:
            s.close()

    def test_transition_validates(self):
        s = self.store()
        try:
            k = models.record_key("vid")
            s.enqueue(models.KIND_RECORD, k)
            s.transition(k, sm.SCHEDULED)
            with self.assertRaises(ValueError):
                s.transition(k, sm.PUBLISHED)  # illegal jump
            self.assertEqual(s.get(k).state, sm.SCHEDULED)
        finally:
            s.close()

    def test_retry_then_dead_letter(self):
        # Ceiling of 2 attempts, deterministic backoff.
        s = self.store(config=_cfg(max_discovery_retries=2, retry_backoff_minutes=[10]))
        try:
            k = models.match_key("flaky")
            s.enqueue(models.KIND_DISCOVERY, k)
            now = dt.datetime(2026, 7, 24, tzinfo=dt.timezone.utc)
            j1 = s.record_attempt(k, ok=False, error_code="E1",
                                  error_message="boom", now=now)
            self.assertEqual(j1.attempts, 1)
            self.assertEqual(j1.state, sm.RETRY_SCHEDULED)
            self.assertIsNotNone(j1.next_retry_at)
            self.assertEqual(j1.last_error_code, "E1")
            # Second failure hits the ceiling -> dead-letter, still present.
            j2 = s.record_attempt(k, ok=False, error_code="E2", now=now)
            self.assertEqual(j2.attempts, 2)
            self.assertEqual(j2.state, sm.FAILED_PERMANENT)
            self.assertIsNone(j2.next_retry_at)
            self.assertEqual(len(s.attempts_for(k)), 2)
        finally:
            s.close()

    def test_success_clears_error(self):
        s = self.store(config=_cfg(retry_backoff_minutes=[10]))
        try:
            k = models.match_key("ok")
            s.enqueue(models.KIND_DISCOVERY, k)
            s.record_attempt(k, ok=False, error_code="E1")
            j = s.record_attempt(k, ok=True)
            self.assertIsNone(j.next_retry_at)
            self.assertIsNone(j.last_error_code)
            self.assertEqual(j.attempts, 2)
        finally:
            s.close()

    def test_claim_prefers_priority(self):
        s = self.store()
        try:
            low = models.match_key("low")
            high = models.match_key("high")
            s.enqueue(models.KIND_DISCOVERY, low, priority=1)
            s.enqueue(models.KIND_DISCOVERY, high, priority=9)
            claimed = s.claim_next([models.KIND_DISCOVERY], "worker-1")
            self.assertEqual(claimed.job_key, high)
            self.assertEqual(claimed.worker_id, "worker-1")
        finally:
            s.close()

    def test_claim_gated_by_retry_time(self):
        # A retry-scheduled job is not claimable until its backoff elapses.
        s = self.store(config=_cfg(retry_backoff_minutes=[10]))
        try:
            future = models.match_key("later")
            s.enqueue(models.KIND_DISCOVERY, future)
            now = dt.datetime(2026, 7, 24, tzinfo=dt.timezone.utc)
            s.record_attempt(future, ok=False, error_code="E", now=now)
            early = s.claim_next([models.KIND_DISCOVERY], "worker-2",
                                 now=now + dt.timedelta(minutes=1))
            self.assertIsNone(early)  # still inside the 10-minute backoff
            late = s.claim_next([models.KIND_DISCOVERY], "worker-3",
                                now=now + dt.timedelta(minutes=20))
            self.assertEqual(late.job_key, future)
        finally:
            s.close()

    def test_claim_out_of_pool_after_forward_transition(self):
        # Per the claim contract, a worker transitions a claimed job forward;
        # once it leaves the claimable set it is no longer handed out.
        s = self.store()
        try:
            k = models.process_key("vid", "v1")
            s.enqueue(models.KIND_PROCESS, k, state=sm.SEGMENTING)
            first = s.claim_next([models.KIND_PROCESS], "worker-1")
            self.assertEqual(first.job_key, k)
            s.transition(k, sm.NEEDS_REVIEW)  # legal, and not a claimable state
            self.assertIsNone(s.claim_next([models.KIND_PROCESS], "worker-2"))
        finally:
            s.close()

    def test_counts_by_state(self):
        s = self.store()
        try:
            s.enqueue(models.KIND_DISCOVERY, models.match_key("a"))
            s.enqueue(models.KIND_DISCOVERY, models.match_key("b"))
            counts = s.counts_by_state()
            self.assertEqual(counts.get(sm.DISCOVERED), 2)
        finally:
            s.close()

    def test_update_payload_merges_not_replaces(self):
        s = self.store()
        try:
            k = models.record_key("vid")
            s.enqueue(models.KIND_RECORD, k, payload={"videoId": "vid"})
            j = s.update_payload(k, {"sha256": "abc", "durationSeconds": 90})
            self.assertEqual(j.payload["videoId"], "vid")
            self.assertEqual(j.payload["sha256"], "abc")
            j2 = s.update_payload(k, {"sha256": "def"})
            self.assertEqual(j2.payload["sha256"], "def")
            self.assertEqual(j2.payload["durationSeconds"], 90)
        finally:
            s.close()

    def test_clear_worker_releases_to_pool(self):
        s = self.store()
        try:
            k = models.record_key("vid")
            s.enqueue(models.KIND_RECORD, k, state=sm.ARCHIVED)
            s.claim_next([models.KIND_RECORD], "worker-1")
            self.assertEqual(s.get(k).worker_id, "worker-1")
            s.clear_worker(k)
            self.assertIsNone(s.get(k).worker_id)
        finally:
            s.close()

    def test_cancel_from_active_state_is_terminal(self):
        s = self.store()
        try:
            k = models.record_key("vid")
            s.enqueue(models.KIND_RECORD, k, state=sm.DOWNLOADING)
            j = s.cancel(k, reason="operator stop")
            self.assertEqual(j.state, sm.CANCELLED)
            self.assertEqual(j.last_error_message, "operator stop")
            # idempotent: cancelling an already-cancelled job is a no-op
            j2 = s.cancel(k)
            self.assertEqual(j2.state, sm.CANCELLED)
            # a cancelled job is never claimable again
            self.assertIsNone(s.claim_next([models.KIND_RECORD], "worker-2"))
        finally:
            s.close()

    def test_retry_job_expedites_scheduled_retry(self):
        s = self.store(config=_cfg(retry_backoff_minutes=[720]))
        try:
            k = models.match_key("flaky")
            s.enqueue(models.KIND_DISCOVERY, k)
            now = dt.datetime(2026, 7, 24, tzinfo=dt.timezone.utc)
            s.record_attempt(k, ok=False, error_code="E1", now=now)
            self.assertIsNone(s.claim_next([models.KIND_DISCOVERY], "w",
                                           now=now + dt.timedelta(minutes=5)))
            s.retry_job(k, now=now + dt.timedelta(minutes=5))
            claimed = s.claim_next([models.KIND_DISCOVERY], "w",
                                   now=now + dt.timedelta(minutes=5))
            self.assertEqual(claimed.job_key, k)
        finally:
            s.close()

    def test_retry_job_refuses_dead_letter_without_force(self):
        s = self.store(config=_cfg(max_discovery_retries=1))
        try:
            k = models.match_key("dead")
            s.enqueue(models.KIND_DISCOVERY, k)
            s.record_attempt(k, ok=False, error_code="E1")
            self.assertEqual(s.get(k).state, sm.FAILED_PERMANENT)
            with self.assertRaises(ValueError):
                s.retry_job(k)
            j = s.retry_job(k, force=True)
            self.assertEqual(j.state, sm.RETRY_SCHEDULED)
        finally:
            s.close()

    def test_record_error_preserves_state(self):
        s = self.store()
        try:
            k = models.record_key("vid")
            s.enqueue(models.KIND_RECORD, k, state=sm.APPROVED)
            j = s.record_error(k, error_code="evidence_missing",
                              error_message="no committed detection")
            self.assertEqual(j.state, sm.APPROVED)
            self.assertEqual(j.last_error_code, "evidence_missing")
            self.assertEqual(j.attempts, 0)
        finally:
            s.close()

    def test_retry_job_wrong_state_raises(self):
        s = self.store()
        try:
            k = models.record_key("vid")
            s.enqueue(models.KIND_RECORD, k, state=sm.DOWNLOADED)
            with self.assertRaises(ValueError):
                s.retry_job(k)
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
