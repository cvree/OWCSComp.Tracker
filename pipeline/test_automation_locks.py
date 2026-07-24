#!/usr/bin/env python3
"""
test_automation_locks.py — lease locks: exclusive acquire, heartbeat renewal,
crash recovery via expiry-steal, release. No network.
Run: python3 pipeline/test_automation_locks.py
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
from automation import locks as lk  # noqa: E402

T0 = dt.datetime(2026, 7, 24, 12, 0, 0, tzinfo=dt.timezone.utc)


class TestLocks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "automation.sqlite")
        self.store = js.JobStore(self.db)
        self.lm = lk.LockManager(self.store.con, lease_seconds=300)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_exclusive_acquire(self):
        self.assertTrue(self.lm.acquire("record:vid", "w1", now=T0))
        # A different worker cannot take a live lease.
        self.assertFalse(self.lm.acquire("record:vid", "w2", now=T0))
        # The holder can re-acquire (re-entrant refresh).
        self.assertTrue(self.lm.acquire("record:vid", "w1", now=T0))

    def test_heartbeat_only_by_holder(self):
        self.lm.acquire("record:vid", "w1", now=T0)
        self.assertTrue(self.lm.heartbeat("record:vid", "w1", now=T0 + dt.timedelta(seconds=60)))
        self.assertFalse(self.lm.heartbeat("record:vid", "w2", now=T0 + dt.timedelta(seconds=60)))

    def test_expired_lease_is_stealable(self):
        self.lm.acquire("record:vid", "w1", now=T0)
        # w1 goes dark; well past the 300s TTL w2 can steal it.
        later = T0 + dt.timedelta(seconds=400)
        self.assertIsNone(self.lm.holder("record:vid", now=later))
        self.assertTrue(self.lm.acquire("record:vid", "w2", now=later))
        holder = self.lm.holder("record:vid", now=later)
        self.assertEqual(holder.worker_id, "w2")

    def test_heartbeat_prevents_steal(self):
        self.lm.acquire("record:vid", "w1", now=T0)
        # Regular heartbeats keep pushing expiry out, so a steal at T0+400
        # would fail if w1 heartbeat at T0+350.
        self.lm.heartbeat("record:vid", "w1", now=T0 + dt.timedelta(seconds=350))
        self.assertFalse(self.lm.acquire("record:vid", "w2", now=T0 + dt.timedelta(seconds=400)))

    def test_release(self):
        self.lm.acquire("record:vid", "w1", now=T0)
        self.assertFalse(self.lm.release("record:vid", "w2"))  # not the holder
        self.assertTrue(self.lm.release("record:vid", "w1"))
        self.assertTrue(self.lm.acquire("record:vid", "w2", now=T0))  # free now

    def test_clear_expired(self):
        self.lm.acquire("a", "w1", now=T0)
        self.lm.acquire("b", "w1", now=T0)
        reaped = self.lm.clear_expired(now=T0 + dt.timedelta(seconds=400))
        self.assertEqual(reaped, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
