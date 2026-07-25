#!/usr/bin/env python3
"""
test_team_export.py — Phase D2 public team export.

Covers the guide's "public export independent of composition capture"
requirement directly against export_data.build_public_payload: every
registered team appears, whether or not it has a captured map, with an
explicit compositionTrackingPending flag, real roster/coverage facts, and
GitHub-Pages-safe relative asset paths (never a leading slash, never a
Windows-style backslash).
Run: python3 pipeline/test_team_export.py
"""
from __future__ import annotations
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db  # noqa: E402
import export_data as ed  # noqa: E402


class TeamExportTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.content_path = os.path.join(self.tmp.name, "owcs.sqlite")
        self.con = db.connect(self.content_path)
        db.init_schema(self.con)
        self.con.execute(
            "INSERT INTO teams (id, name, region, code) VALUES ('a','Team A','na','A')")
        self.con.execute(
            "INSERT INTO teams (id, name, region, code) VALUES ('b','Team B','na','B')")
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def _team(self, payload, tid):
        return next(t for t in payload["teams"] if t["id"] == tid)


class TestTeamsIndependentOfCapture(TeamExportTestCase):
    def test_teams_with_no_matches_or_captures_still_export(self):
        payload = ed.build_public_payload(self.con)
        ids = {t["id"] for t in payload["teams"]}
        self.assertEqual(ids, {"a", "b"})

    def test_uncaptured_team_flagged_pending(self):
        payload = ed.build_public_payload(self.con)
        self.assertTrue(self._team(payload, "a")["compositionTrackingPending"])

    def test_captured_team_not_flagged_pending(self):
        self.con.execute(
            "INSERT INTO matches (id, region, date, team_a, team_b) VALUES "
            "('m1','na','2026-07-01','a','b')")
        self.con.execute("INSERT INTO game_maps (id, name, mode) VALUES ('kingsrow','K','Hybrid')")
        self.con.execute(
            "INSERT INTO map_results (match_id, map_order, map_id) VALUES ('m1',1,'kingsrow')")
        self.con.execute("INSERT INTO heroes (id, name, role) VALUES ('kiriko','Kiriko','Support')")
        self.con.execute(
            """INSERT INTO hero_stints (match_id, map_result_id, team_id, slot, hero_id, start_offset)
               VALUES ('m1', 1, 'a', 1, 'kiriko', 0)""")
        self.con.commit()
        payload = ed.build_public_payload(self.con)
        self.assertFalse(self._team(payload, "a")["compositionTrackingPending"])
        self.assertTrue(self._team(payload, "b")["compositionTrackingPending"])

    def test_no_hero_pool_statistics_fabricated_for_pending_team(self):
        payload = ed.build_public_payload(self.con)
        snaps = [s for s in payload["compSnapshots"] if s["teamId"] == "a"]
        self.assertEqual(snaps, [])


class TestRosterAndIdentityFacts(TeamExportTestCase):
    def test_roster_reflects_most_recent_match(self):
        self.con.execute(
            "INSERT INTO players (id, nickname, team_id) VALUES ('p1','Old','a')")
        self.con.execute(
            "INSERT INTO players (id, nickname, team_id) VALUES ('p2','New','a')")
        self.con.execute(
            "INSERT INTO matches (id, region, date, team_a, team_b) VALUES "
            "('m1','na','2026-06-01','a','b')")
        self.con.execute(
            "INSERT INTO matches (id, region, date, team_a, team_b) VALUES "
            "('m2','na','2026-07-01','a','b')")
        self.con.execute(
            "INSERT INTO match_rosters (match_id, team_id, player_id) VALUES ('m1','a','p1')")
        self.con.execute(
            "INSERT INTO match_rosters (match_id, team_id, player_id) VALUES ('m2','a','p2')")
        self.con.commit()
        payload = ed.build_public_payload(self.con)
        roster = self._team(payload, "a")["roster"]
        self.assertEqual([p["handle"] for p in roster], ["New"])

    def test_needs_review_and_aliases_surface_honestly(self):
        self.con.execute(
            """UPDATE teams SET needs_review=1, review_reason='region conflict',
               previous_names='["Old Name"]', status='unsigned' WHERE id='a'""")
        self.con.commit()
        payload = ed.build_public_payload(self.con)
        row = self._team(payload, "a")
        self.assertTrue(row["needsReview"])
        self.assertEqual(row["reviewReason"], "region conflict")
        self.assertEqual(row["previousNames"], ["Old Name"])
        self.assertEqual(row["status"], "unsigned")

    def test_logo_url_null_until_a_real_file_is_set(self):
        payload = ed.build_public_payload(self.con)
        self.assertIsNone(self._team(payload, "a")["logoUrl"])


class TestGitHubPagesPathSafety(TeamExportTestCase):
    def test_logo_url_is_a_safe_relative_path_when_present(self):
        self.con.execute(
            "UPDATE teams SET logo_url='assets/img/teams/a/logo.png' WHERE id='a'")
        self.con.commit()
        payload = ed.build_public_payload(self.con)
        url = self._team(payload, "a")["logoUrl"]
        self.assertFalse(url.startswith("/"))
        self.assertNotIn("\\", url)
        self.assertTrue(url.startswith("assets/"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
