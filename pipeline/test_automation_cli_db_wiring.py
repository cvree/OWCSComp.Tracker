#!/usr/bin/env python3
"""
test_automation_cli_db_wiring.py — regression test for a real bug found
while adding the cv2-import-isolation fix: `cli.py`'s segment/detect/
publish/run-job commands opened the CONTENT database (data/owcs.sqlite —
matches/teams/heroes) for `map_segments`/`publication_runs` operations, but
those tables live only in the AUTOMATION database (pipeline/automation/
schema.sql, alongside jobs/locks). Every prior test exercised this in
process with ONE blended sqlite connection standing in for both databases,
which never caught the mismatch — `cli.py segment-list` against two real,
separate database files raised `sqlite3.OperationalError: no such table:
map_segments`.

This suite drives the REAL cli.py as a subprocess against a genuinely
separate `--db` (automation) and the repo's real content db, so the fix is
proven against the actual two-database architecture, not a test fixture
that happens to merge them.

Run: python3 pipeline/test_automation_cli_db_wiring.py
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import proc_text  # noqa: E402  (UTF-8 subprocess decoding)
REPO_ROOT = os.path.dirname(HERE)
CLI = os.path.join(HERE, "automation", "cli.py")


def _cli(db_path: str, *args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    cmd = [sys.executable, CLI, "--db", db_path, *args]
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True,
                         timeout=timeout, **proc_text.PIPE_TEXT)


class TestSegmentCommandsUseTheAutomationDb(unittest.TestCase):
    """`map_segments` lives in the automation db; these commands must never
    touch the content db for it, and must work against a real, separate
    automation sqlite file (not the same connection as content data)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "automation.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def test_segment_list_on_fresh_separate_db_does_not_crash(self):
        res = _cli(self.db_path, "segment-list", "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout), [])

    def test_create_job_then_segment_workflow_round_trips_on_separate_dbs(self):
        # 1. create a job (writes to the automation db's `jobs` table).
        res = _cli(self.db_path, "create-job",
                  "--match", "m-cr-zeta-ccuf", "--video-id", "wiringtestvid",
                  "--source-url", "https://www.youtube.com/watch?v=wiringtestvid",
                  "--channel-id", "UCiAInBL9kUzz1XRxk66v-gw",
                  "--team-a", "cr", "--team-b", "zeta")
        self.assertEqual(res.returncode, 0, res.stderr)

        # 2. insert a candidate segment directly (simulating what
        #    ops.run_one_job's DOWNLOADED branch would do) against the SAME
        #    automation db file, then approve it via the real CLI.
        script = f"""
import sys
sys.path.insert(0, {HERE!r})
from automation import job_store as js, segmentation as seg
store = js.JobStore({self.db_path!r})
seg.store_candidates(store.con, "wiringtestvid", "m-cr-zeta-ccuf",
                    [{{"start_time": 0.0, "end_time": 60.0, "confidence": 0.9,
                       "signals": {{}}}}])
store.close()
"""
        res = subprocess.run([sys.executable, "-c", script], cwd=REPO_ROOT,
                            capture_output=True, text=True, timeout=15,
                                **proc_text.PIPE_TEXT)
        self.assertEqual(res.returncode, 0, res.stderr)

        # 3. `segment-list` (real CLI, real separate db file) must find it —
        #    this is exactly the call that raised "no such table:
        #    map_segments" before the fix.
        res = _cli(self.db_path, "segment-list", "--video-id", "wiringtestvid", "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = json.loads(res.stdout)
        self.assertEqual(len(rows), 1)
        segment_id = rows[0]["id"]

        # 4. approve it via the real CLI.
        res = _cli(self.db_path, "segment-approve", str(segment_id),
                  "--map-order", "1", "--map-name", "TestMap", "--map-mode", "Control",
                  "--team-a", "cr", "--team-b", "zeta", "--side", "team_a_left",
                  "--layout-id", "owcs_8c105lnzlam")
        self.assertEqual(res.returncode, 0, res.stderr)

        res = _cli(self.db_path, "segment-list", "--status", "approved", "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        approved = json.loads(res.stdout)
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0]["map_name"], "TestMap")

    def test_segment_reject_on_separate_db(self):
        script = f"""
import sys
sys.path.insert(0, {HERE!r})
from automation import job_store as js, segmentation as seg
store = js.JobStore({self.db_path!r})
seg.store_candidates(store.con, "vidx", "m-cr-zeta-ccuf",
                    [{{"start_time": 0.0, "end_time": 30.0, "confidence": 0.5,
                       "signals": {{}}}}])
store.close()
"""
        res = subprocess.run([sys.executable, "-c", script], cwd=REPO_ROOT,
                            capture_output=True, text=True, timeout=15,
                                **proc_text.PIPE_TEXT)
        self.assertEqual(res.returncode, 0, res.stderr)
        rows = json.loads(_cli(self.db_path, "segment-list", "--json").stdout)
        segment_id = rows[0]["id"]
        res = _cli(self.db_path, "segment-reject", str(segment_id), "--reason", "not a match")
        self.assertEqual(res.returncode, 0, res.stderr)


class TestDetectJobUsesAutomationDbForSegments(unittest.TestCase):
    def test_detect_job_looks_up_segments_in_automation_db(self):
        """A job with no approved segment must report that honestly (not
        crash on a missing content-db table) when the automation db is a
        real separate file."""
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "automation.sqlite")
            res = _cli(db_path, "create-job",
                      "--match", "m-cr-zeta-ccuf", "--video-id", "detectwiringtest",
                      "--source-url", "https://www.youtube.com/watch?v=detectwiringtest",
                      "--channel-id", "UCiAInBL9kUzz1XRxk66v-gw",
                      "--team-a", "cr", "--team-b", "zeta")
            self.assertEqual(res.returncode, 0, res.stderr)
            res = _cli(db_path, "detect-job", "record:detectwiringtest:source")
        self.assertEqual(res.returncode, 1)
        self.assertIn("no approved segment", (res.stdout + res.stderr).lower())
        self.assertNotIn("no such table", (res.stdout + res.stderr).lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
