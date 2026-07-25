#!/usr/bin/env python3
"""
test_automation_team_coverage.py — Phase D2 per-team coverage ledger.

Covers state derivation for every explicit state the guide names
(schedule-discovered/identity-verified/roster-verified/logo-candidate/
logo-verified/broadcast-located/composition-captured/needs-review/
unsupported), blocking-issue selection order, and that nothing silently
disappears from the report.
Run: python3 pipeline/test_automation_team_coverage.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db as content_db  # noqa: E402
from automation import team_coverage as tc  # noqa: E402


class TeamCoverageTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.content_path = os.path.join(self.tmp.name, "owcs.sqlite")
        self.sources_path = os.path.join(self.tmp.name, "team_asset_sources.json")
        self.con = content_db.connect(self.content_path)
        content_db.init_schema(self.con)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def _team(self, tid, **kw):
        defaults = dict(name=tid, region="na", code=tid[:3].upper(),
                        faceit_team_id=None, identity_verified_at=None,
                        roster_source=None, roster_verified_at=None,
                        needs_review=0, logo_url=None)
        defaults.update(kw)
        self.con.execute(
            """INSERT INTO teams (id, name, region, code, faceit_team_id,
                                   identity_verified_at, roster_source,
                                   roster_verified_at, needs_review, logo_url)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (tid, defaults["name"], defaults["region"], defaults["code"],
             defaults["faceit_team_id"], defaults["identity_verified_at"],
             defaults["roster_source"], defaults["roster_verified_at"],
             defaults["needs_review"], defaults["logo_url"]))
        self.con.commit()

    def _report(self, **kw):
        return tc.build_report(content_db=self.content_path,
                               asset_sources_path=self.sources_path, **kw)

    def _row(self, report, tid):
        return next(t for t in report["teams"] if t["id"] == tid)


class TestBareTeam(TeamCoverageTestCase):
    def test_freshly_discovered_team_has_only_schedule_discovered(self):
        self._team("t1")
        row = self._row(self._report(), "t1")
        self.assertEqual(row["states"], ["schedule-discovered"])
        self.assertIn("no faceit_team_id", row["blockingIssue"])


class TestIdentityAndRoster(TeamCoverageTestCase):
    def test_identity_verified_when_faceit_id_present(self):
        self._team("t1", faceit_team_id="F1", identity_verified_at="2026-07-01T00:00:00+00:00")
        row = self._row(self._report(), "t1")
        self.assertIn("identity-verified", row["states"])

    def test_roster_verified_requires_both_timestamp_and_actual_rows(self):
        self._team("t1", faceit_team_id="F1", identity_verified_at="2026-07-01T00:00:00+00:00",
                  roster_source="faceit", roster_verified_at="2026-07-01T00:00:00+00:00")
        # No match_rosters rows exist yet -> NOT roster-verified despite the timestamp.
        row = self._row(self._report(), "t1")
        self.assertNotIn("roster-verified", row["states"])

    def test_roster_verified_with_real_rows(self):
        self._team("t1", faceit_team_id="F1", identity_verified_at="2026-07-01T00:00:00+00:00",
                  roster_source="faceit", roster_verified_at="2026-07-01T00:00:00+00:00")
        self.con.execute("INSERT INTO teams (id, name, region, code) VALUES ('t2','T2','na','T2')")
        self.con.execute(
            "INSERT INTO players (id, nickname, team_id) VALUES ('p1','P1','t1')")
        self.con.execute(
            "INSERT INTO matches (id, region, date, team_a, team_b) VALUES "
            "('m1','na','2026-07-01','t1','t2')")
        self.con.execute(
            "INSERT INTO match_rosters (match_id, team_id, player_id) VALUES ('m1','t1','p1')")
        self.con.commit()
        row = self._row(self._report(), "t1")
        self.assertIn("roster-verified", row["states"])


class TestLogoStates(TeamCoverageTestCase):
    def test_logo_verified_from_logo_url(self):
        self._team("t1", logo_url="assets/img/teams/t1/logo.png")
        row = self._row(self._report(), "t1")
        self.assertIn("logo-verified", row["states"])

    def test_logo_candidate_from_asset_sources(self):
        self._team("t1")
        with open(self.sources_path, "w", encoding="utf-8") as f:
            json.dump({"teams": {"t1": {"candidateSources": ["some official source"]}}}, f)
        row = self._row(self._report(), "t1")
        self.assertIn("logo-candidate", row["states"])
        self.assertNotIn("logo-verified", row["states"])

    def test_no_logo_no_candidate_is_honestly_absent(self):
        self._team("t1")
        row = self._row(self._report(), "t1")
        self.assertNotIn("logo-candidate", row["states"])
        self.assertNotIn("logo-verified", row["states"])


class TestBroadcastAndCapture(TeamCoverageTestCase):
    def test_broadcast_located_from_vod_url(self):
        self._team("t1")
        self.con.execute("INSERT INTO teams (id, name, region, code) VALUES ('t2','T2','na','T2')")
        self.con.execute(
            "INSERT INTO matches (id, region, date, team_a, team_b, vod_url) VALUES "
            "('m1','na','2026-07-01','t1','t2','https://youtube.com/watch?v=x')")
        self.con.commit()
        row = self._row(self._report(), "t1")
        self.assertIn("broadcast-located", row["states"])

    def test_composition_captured_from_hero_stints(self):
        self._team("t1")
        self.con.execute("INSERT INTO teams (id, name, region, code) VALUES ('t2','T2','na','T2')")
        self.con.execute(
            "INSERT INTO matches (id, region, date, team_a, team_b) VALUES "
            "('m1','na','2026-07-01','t1','t2')")
        self.con.execute("INSERT INTO game_maps (id, name, mode) VALUES ('kingsrow','King''s Row','Hybrid')")
        self.con.execute(
            "INSERT INTO map_results (match_id, map_order, map_id) VALUES ('m1', 1, 'kingsrow')")
        self.con.execute("INSERT INTO heroes (id, name, role) VALUES ('kiriko','Kiriko','Support')")
        self.con.execute(
            """INSERT INTO hero_stints (match_id, map_result_id, team_id, slot, hero_id, start_offset)
               VALUES ('m1', 1, 't1', 1, 'kiriko', 0)""")
        self.con.commit()
        row = self._row(self._report(), "t1")
        self.assertIn("composition-captured", row["states"])

    def test_comp_snapshots_alone_does_not_count(self):
        """comp_snapshots is a legacy table the current export doesn't read
        from — a team with only stale comp_snapshots rows and no hero_stints
        must still show tracking as pending, not falsely 'captured'."""
        self._team("t1")
        self.con.execute("INSERT INTO teams (id, name, region, code) VALUES ('t2','T2','na','T2')")
        self.con.execute(
            "INSERT INTO matches (id, region, date, team_a, team_b) VALUES "
            "('m1','na','2026-07-01','t1','t2')")
        self.con.execute(
            "INSERT INTO comp_snapshots (match_id, team_id, stream_offset_seconds, frame_hash) "
            "VALUES ('m1', 't1', 0, 'h1')")
        self.con.commit()
        row = self._row(self._report(), "t1")
        self.assertNotIn("composition-captured", row["states"])


class TestNeedsReviewAndUnsupported(TeamCoverageTestCase):
    def test_needs_review_flows_through(self):
        self._team("t1", needs_review=1)
        row = self._row(self._report(), "t1")
        self.assertIn("needs-review", row["states"])
        self.assertIsNotNone(row["blockingIssue"])

    def test_unsupported_region_flagged(self):
        self._team("t1", region="mars")
        row = self._row(self._report(supported_regions={"na", "emea"}), "t1")
        self.assertIn("unsupported", row["states"])

    def test_supported_region_not_flagged(self):
        self._team("t1", region="na")
        row = self._row(self._report(supported_regions={"na", "emea"}), "t1")
        self.assertNotIn("unsupported", row["states"])


class TestBlockingIssuePriority(TeamCoverageTestCase):
    def test_needs_review_wins_over_everything_else(self):
        self._team("t1", faceit_team_id="F1", identity_verified_at="2026-07-01T00:00:00+00:00",
                  needs_review=1)
        row = self._row(self._report(), "t1")
        self.assertNotIn("no faceit_team_id", row["blockingIssue"])

    def test_fully_covered_team_has_no_blocking_issue(self):
        self._team("t1", faceit_team_id="F1", identity_verified_at="2026-07-01T00:00:00+00:00",
                  roster_source="faceit", roster_verified_at="2026-07-01T00:00:00+00:00",
                  logo_url="assets/img/teams/t1/logo.png")
        self.con.execute("INSERT INTO teams (id, name, region, code) VALUES ('t2','T2','na','T2')")
        self.con.execute("INSERT INTO players (id, nickname, team_id) VALUES ('p1','P1','t1')")
        self.con.execute(
            "INSERT INTO matches (id, region, date, team_a, team_b, vod_url) VALUES "
            "('m1','na','2026-07-01','t1','t2','https://youtube.com/watch?v=x')")
        self.con.execute(
            "INSERT INTO match_rosters (match_id, team_id, player_id) VALUES ('m1','t1','p1')")
        self.con.execute("INSERT INTO game_maps (id, name, mode) VALUES ('kingsrow','King''s Row','Hybrid')")
        self.con.execute(
            "INSERT INTO map_results (match_id, map_order, map_id) VALUES ('m1', 1, 'kingsrow')")
        self.con.execute("INSERT INTO heroes (id, name, role) VALUES ('kiriko','Kiriko','Support')")
        self.con.execute(
            """INSERT INTO hero_stints (match_id, map_result_id, team_id, slot, hero_id, start_offset)
               VALUES ('m1', 1, 't1', 1, 'kiriko', 0)""")
        self.con.commit()
        row = self._row(self._report(), "t1")
        self.assertIsNone(row["blockingIssue"])


class TestReportShape(TeamCoverageTestCase):
    def test_every_team_appears_exactly_once(self):
        for i in range(5):
            self._team(f"t{i}")
        report = self._report()
        self.assertEqual(len(report["teams"]), 5)
        self.assertEqual(len({t["id"] for t in report["teams"]}), 5)

    def test_empty_registry_returns_complete_shape(self):
        report = self._report()
        self.assertEqual(report["teams"], [])
        self.assertIn("counts", report)

    def test_format_report_is_deterministic_text(self):
        self._team("t1")
        report = self._report()
        text = tc.format_report(report)
        self.assertIn("Team coverage", text)
        self.assertIn("t1", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
