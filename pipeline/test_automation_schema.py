#!/usr/bin/env python3
"""
test_automation_schema.py — the automation schema applies cleanly, is
idempotent, and exposes the tables/views the roadmap names.
Run: python3 pipeline/test_automation_schema.py
"""
from __future__ import annotations
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from automation import job_store as js  # noqa: E402

EXPECTED_TABLES = {
    "source_channels", "source_events", "scheduled_matches",
    "broadcast_candidates", "map_segments", "review_tasks",
    "publication_runs", "coverage_snapshots", "jobs", "job_attempts", "locks",
}
EXPECTED_VIEWS = {"recording_jobs", "processing_jobs"}


class TestSchema(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "automation.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def _objects(self, con, kind):
        return {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = ?", (kind,))}

    def test_tables_and_views_created(self):
        store = js.JobStore(self.db)
        try:
            tables = self._objects(store.con, "table")
            views = self._objects(store.con, "view")
            self.assertTrue(EXPECTED_TABLES.issubset(tables),
                            f"missing: {EXPECTED_TABLES - tables}")
            self.assertTrue(EXPECTED_VIEWS.issubset(views),
                            f"missing views: {EXPECTED_VIEWS - views}")
        finally:
            store.close()

    def test_init_is_idempotent(self):
        store = js.JobStore(self.db)
        try:
            store.init_db()  # second application must not raise
            store.init_db()
        finally:
            store.close()

    def test_recording_view_reflects_jobs(self):
        store = js.JobStore(self.db)
        try:
            from automation import models
            store.enqueue(models.KIND_RECORD, models.record_key("vid1"))
            store.enqueue(models.KIND_PROCESS, models.process_key("vid1", "v1"))
            rec = store.con.execute("SELECT COUNT(*) n FROM recording_jobs").fetchone()["n"]
            proc = store.con.execute("SELECT COUNT(*) n FROM processing_jobs").fetchone()["n"]
            self.assertEqual(rec, 1)
            self.assertEqual(proc, 1)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
