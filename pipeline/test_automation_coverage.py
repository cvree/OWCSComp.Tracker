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
from automation import models  # noqa: E402
from automation import state_machine as sm  # noqa: E402
from automation.config import AutomationConfig, DEFAULTS  # noqa: E402
from automation.job_store import JobStore  # noqa: E402

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


def _cfg(**over):
    v = dict(DEFAULTS)
    v.update(over)
    return AutomationConfig(values=v)


class TestBroadcastCoverage(unittest.TestCase):
    """Phase C6 — every configured match gets an explicit coverage label."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "automation.sqlite")
        self.store = JobStore(self.db, config=_cfg())

    def tearDown(self):
        self.store.close()
        try:
            self.tmp.cleanup()
        except PermissionError:
            pass

    def _match(self, mid, **over):
        row = {
            "id": mid, "faceit_match_id": mid.split(":")[-1], "competition_id": "c_na",
            "region": "na", "team_a": "Falcons", "team_b": "Zeta",
            "scheduled_at": "2026-07-20T20:00:00+00:00", "completed_at": "2026-07-20T22:00:00+00:00",
            "status": "finished", "tier": 2, "faceit_room_url": None,
            "state": sm.DISCOVERED, "capture_status": "pending", "data_status": "pending", "raw": "{}",
        }
        row.update(over)
        self.store.con.execute(
            """INSERT INTO scheduled_matches
                 (id, faceit_match_id, competition_id, region, team_a, team_b,
                  scheduled_at, completed_at, status, tier, faceit_room_url,
                  state, capture_status, data_status, raw)
               VALUES (:id,:faceit_match_id,:competition_id,:region,:team_a,:team_b,
                       :scheduled_at,:completed_at,:status,:tier,:faceit_room_url,
                       :state,:capture_status,:data_status,:raw)""",
            row)
        self.store.con.commit()

    def _candidate(self, match_id, video_id, confidence):
        self.store.con.execute(
            """INSERT INTO broadcast_candidates (match_id, channel_id, platform, video_id, score, confidence)
               VALUES (?, 'ch1', 'youtube', ?, 0, ?)""",
            (match_id, video_id, confidence))
        self.store.con.commit()

    def _video(self, video_id, live_status):
        self.store.con.execute(
            """INSERT INTO broadcast_videos (video_id, platform, live_broadcast_status, coverage_state)
               VALUES (?, 'youtube', ?, 'ARCHIVED')""",
            (video_id, live_status))
        self.store.con.commit()

    def test_high_confidence_archived_video_is_archive_available(self):
        self._match("match:m1")
        self._video("v1", "completed")
        self._candidate("match:m1", "v1", "high")
        r = cov.build_broadcast_coverage(self.db, window_days=14, now=NOW)
        row = next(m for m in r["matches"] if m["match_id"] == "match:m1")
        self.assertEqual(row["state"], "archive-available")
        self.assertEqual(r["counts"]["archive_available"], 1)

    def test_live_video_beats_archive(self):
        self._match("match:m1")
        self._video("v1", "live")
        self._candidate("match:m1", "v1", "high")
        r = cov.build_broadcast_coverage(self.db, window_days=14, now=NOW)
        row = next(m for m in r["matches"] if m["match_id"] == "match:m1")
        self.assertEqual(row["state"], "live")

    def test_high_no_video_row_is_broadcast_located(self):
        self._match("match:m1")
        self._candidate("match:m1", "v1", "high")
        r = cov.build_broadcast_coverage(self.db, window_days=14, now=NOW)
        row = next(m for m in r["matches"] if m["match_id"] == "match:m1")
        self.assertEqual(row["state"], "broadcast-located")

    def test_medium_only_is_needs_review(self):
        self._match("match:m1")
        self._candidate("match:m1", "v1", "medium")
        r = cov.build_broadcast_coverage(self.db, window_days=14, now=NOW)
        row = next(m for m in r["matches"] if m["match_id"] == "match:m1")
        self.assertEqual(row["state"], "needs-review")

    def test_low_only_is_broadcast_candidate_found_if_video_exists(self):
        self._match("match:m1")
        self._video("v1", "none")
        self._candidate("match:m1", "v1", "low")
        r = cov.build_broadcast_coverage(self.db, window_days=14, now=NOW)
        row = next(m for m in r["matches"] if m["match_id"] == "match:m1")
        self.assertEqual(row["state"], "broadcast-candidate-found")

    def test_no_candidates_upcoming_status_is_awaiting_broadcast(self):
        self._match("match:m1", status="upcoming", completed_at=None)
        r = cov.build_broadcast_coverage(self.db, window_days=14, now=NOW,
                                         supported_regions={"na"})
        row = next(m for m in r["matches"] if m["match_id"] == "match:m1")
        self.assertEqual(row["state"], "awaiting-broadcast")

    def test_unsupported_region_flagged_explicitly(self):
        self._match("match:m1", region="china", status="upcoming", completed_at=None)
        r = cov.build_broadcast_coverage(self.db, window_days=14, now=NOW,
                                         supported_regions={"na", "korea"})
        row = next(m for m in r["matches"] if m["match_id"] == "match:m1")
        self.assertEqual(row["state"], "unsupported-source")

    def test_finished_no_candidates_is_missing_broadcast(self):
        self._match("match:m1", state=sm.SCHEDULED)
        r = cov.build_broadcast_coverage(self.db, window_days=14, now=NOW,
                                         supported_regions={"na"})
        row = next(m for m in r["matches"] if m["match_id"] == "match:m1")
        self.assertEqual(row["state"], "missing-broadcast")

    def test_cancelled_match_never_disappears(self):
        self._match("match:m1", status="cancelled")
        r = cov.build_broadcast_coverage(self.db, window_days=14, now=NOW)
        row = next(m for m in r["matches"] if m["match_id"] == "match:m1")
        self.assertEqual(row["state"], "cancelled")
        self.assertEqual(r["counts"]["cancelled"], 1)

    def test_later_phase_state_wins_over_heuristic(self):
        self._match("match:m1", state=sm.PUBLISHED)
        r = cov.build_broadcast_coverage(self.db, window_days=14, now=NOW)
        row = next(m for m in r["matches"] if m["match_id"] == "match:m1")
        self.assertEqual(row["state"], "published")

    def test_quota_and_refresh_and_error_surface(self):
        self.store.con.execute(
            "INSERT INTO quota_usage (day, endpoint, units, calls) VALUES ('2026-07-24','videos.list',3,3)")
        self.store.con.execute(
            "INSERT INTO quota_usage (day, endpoint, units, calls) VALUES ('2026-07-24','search.list',100,1)")
        self.store.con.commit()
        self.store.enqueue(models.KIND_BROADCAST, "broadcast-discovery:ch1:a:b", payload={})
        self.store.record_attempt("broadcast-discovery:ch1:a:b", ok=False,
                                  error_code="YOUTUBE_API_ERROR", error_message="boom", now=NOW)
        r = cov.build_broadcast_coverage(self.db, window_days=14, now=NOW)
        self.assertEqual(r["quota_used"], 103)
        self.assertEqual(r["quota_by_endpoint"]["search.list"], 100)
        self.assertEqual(r["last_source_error"]["message"], "boom")

    def test_no_automation_db_returns_empty_shape(self):
        r = cov.build_broadcast_coverage(None, window_days=14, now=NOW)
        self.assertEqual(r["matches"], [])
        self.assertEqual(sum(r["counts"].values()), 0)

    def test_format_broadcast_coverage_renders(self):
        self._match("match:m1")
        self._video("v1", "completed")
        self._candidate("match:m1", "v1", "high")
        r = cov.build_broadcast_coverage(self.db, window_days=14, now=NOW)
        text = cov.format_broadcast_coverage(r)
        self.assertIn("archive-available: 1", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
