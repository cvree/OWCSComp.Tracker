#!/usr/bin/env python3
"""
test_automation_coverage.py — the rolling completeness report counts capture
stages honestly and lists every missing broadcast. No network.
Run: python3 pipeline/test_automation_coverage.py
"""
from __future__ import annotations
import datetime as dt
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from automation import coverage as cov  # noqa: E402

NOW = dt.datetime(2026, 7, 24, tzinfo=dt.timezone.utc)


def _make_content_db(path: str) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE teams (id TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE matches (
          id TEXT PRIMARY KEY, event_name TEXT, region TEXT, date TEXT,
          finished_at TEXT, status TEXT, team_a TEXT, team_b TEXT, vod_url TEXT
        );
        CREATE TABLE map_results (
          id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT, map_order INTEGER
        );
        CREATE TABLE comp_snapshots (
          id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT, team_id TEXT
        );
        """
    )
    # m1: fully covered (broadcast + maps + processed) -> published
    # m2: broadcast + maps, not processed
    # m3: no broadcast at all -> missing
    # m4: OUTSIDE the 14-day window -> excluded entirely
    con.executemany(
        "INSERT INTO matches VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("m1", "OWCS NA", "na", "2026-07-20", "2026-07-20T22:00:00", "final", "a", "b", "http://vod/1"),
            ("m2", "OWCS EMEA", "emea", "2026-07-18", "2026-07-18T20:00:00", "final", "a", "b", "http://vod/2"),
            ("m3", "OWCS KR", "korea", "2026-07-22", "2026-07-22T10:00:00", "final", "a", "b", None),
            ("m4", "OWCS OLD", "na", "2026-06-01", "2026-06-01T10:00:00", "final", "a", "b", "http://vod/old"),
        ],
    )
    con.executemany("INSERT INTO map_results (match_id, map_order) VALUES (?,?)",
                    [("m1", 1), ("m1", 2), ("m2", 1)])
    con.executemany("INSERT INTO comp_snapshots (match_id, team_id) VALUES (?,?)",
                    [("m1", "a"), ("m1", "b")])
    con.commit()
    con.close()


class TestCoverage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.content = os.path.join(self.tmp.name, "owcs.sqlite")
        _make_content_db(self.content)

    def tearDown(self):
        self.tmp.cleanup()

    def test_counts(self):
        r = cov.build_report(content_db=self.content, window_days=14, now=NOW)
        c = r["counts"]
        self.assertEqual(c["discovered"], 3)          # m4 excluded (old)
        self.assertEqual(c["broadcast_located"], 2)   # m1, m2
        self.assertEqual(c["segmented"], 2)           # m1, m2 have maps
        self.assertEqual(c["processed"], 1)           # only m1
        self.assertEqual(c["published"], 1)           # m1 (maps + processed)
        self.assertEqual(c["missing_broadcast"], 1)   # m3

    def test_missing_list_names_the_match(self):
        r = cov.build_report(content_db=self.content, window_days=14, now=NOW)
        ids = {m["match_id"] for m in r["missing_broadcast"]}
        self.assertEqual(ids, {"m3"})
        text = cov.format_report(r)
        self.assertIn("m3", text)
        self.assertIn("Missing broadcast: 1", text)

    def test_missing_content_db_is_safe(self):
        r = cov.build_report(content_db="/no/such.sqlite", window_days=14, now=NOW)
        self.assertEqual(r["counts"]["discovered"], 0)

    def test_snapshot_persist(self):
        adb = os.path.join(self.tmp.name, "automation.sqlite")
        r = cov.build_report(content_db=self.content, window_days=14,
                             automation_db=adb, now=NOW)
        rid = cov.save_snapshot(adb, r)
        self.assertGreaterEqual(rid, 1)
        con = sqlite3.connect(adb)
        n = con.execute("SELECT COUNT(*) FROM coverage_snapshots").fetchone()[0]
        con.close()
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
