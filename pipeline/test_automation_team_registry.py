#!/usr/bin/env python3
"""
test_automation_team_registry.py — Phase D2 canonical team identity.

Covers the required scenarios: renamed teams (previous_names preserved),
duplicate names across different regions (never merged), temporary
unsigned/mix rosters, roster-change provenance, conflicting source facts
(needs_review instead of a silent overwrite), idempotent reruns, and no
hero-composition writes from this layer.
Run: python3 pipeline/test_automation_team_registry.py
"""
from __future__ import annotations
import datetime as dt
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db as content_db  # noqa: E402
from automation import discovery as disc  # noqa: E402
from automation import team_registry as tr  # noqa: E402

NOW = dt.datetime(2026, 7, 25, 12, 0, 0, tzinfo=dt.timezone.utc)


def _slug(name, fallback):
    import re
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return s or fallback


class TeamRegistryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.content_path = os.path.join(self.tmp.name, "owcs.sqlite")
        self.con = content_db.connect(self.content_path)
        content_db.init_schema(self.con)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()


class TestRename(TeamRegistryTestCase):
    def test_rename_preserves_previous_name(self):
        r1 = tr.upsert_identity(self.con, "t1", name="Falcons", region="na",
                                faceit_team_id="F1", source_authority="faceit", now=NOW)
        self.assertTrue(r1["created"])
        r2 = tr.upsert_identity(self.con, "t1", name="Team Falcons", region="na",
                                faceit_team_id="F1", source_authority="faceit",
                                now=NOW + dt.timedelta(days=1))
        self.assertTrue(r2["renamed"])
        row = self.con.execute("SELECT * FROM teams WHERE id='t1'").fetchone()
        self.assertEqual(row["name"], "Team Falcons")
        self.assertEqual(row["previous_names"], '["Falcons"]')

    def test_rename_twice_appends_not_replaces(self):
        tr.upsert_identity(self.con, "t1", name="A", region="na",
                           faceit_team_id="F1", source_authority="faceit", now=NOW)
        tr.upsert_identity(self.con, "t1", name="B", region="na",
                           faceit_team_id="F1", source_authority="faceit", now=NOW)
        tr.upsert_identity(self.con, "t1", name="C", region="na",
                           faceit_team_id="F1", source_authority="faceit", now=NOW)
        row = self.con.execute("SELECT previous_names FROM teams WHERE id='t1'").fetchone()
        self.assertEqual(row["previous_names"], '["A", "B"]')


class TestDuplicateNamesAcrossRegions(TeamRegistryTestCase):
    def test_same_name_different_region_no_faceit_id_stays_distinct(self):
        slug_a = tr.resolve_identity_slug(self.con, "Quick", None, "na", "A", "m1", _slug)
        tr.upsert_identity(self.con, slug_a, name="Quick", region="na",
                           faceit_team_id=None, source_authority="faceit", now=NOW)
        slug_b = tr.resolve_identity_slug(self.con, "Quick", None, "emea", "A", "m2", _slug)
        self.assertNotEqual(slug_a, slug_b)
        tr.upsert_identity(self.con, slug_b, name="Quick", region="emea",
                           faceit_team_id=None, source_authority="faceit", now=NOW)
        rows = self.con.execute("SELECT id, region FROM teams WHERE name='Quick'").fetchall()
        self.assertEqual({r["region"] for r in rows}, {"na", "emea"})
        self.assertEqual(len(rows), 2)

    def test_same_name_same_region_reuses_row(self):
        slug_a = tr.resolve_identity_slug(self.con, "Quick", None, "na", "A", "m1", _slug)
        tr.upsert_identity(self.con, slug_a, name="Quick", region="na",
                           faceit_team_id=None, source_authority="faceit", now=NOW)
        slug_b = tr.resolve_identity_slug(self.con, "Quick", None, "na", "A", "m2", _slug)
        self.assertEqual(slug_a, slug_b)

    def test_faceit_id_links_across_a_region_correction(self):
        # Same faceit_team_id seen with two different name-slug bases is
        # irrelevant — the id always wins, never merges into a name clash.
        slug_a = tr.resolve_identity_slug(self.con, "Quick", "Q1", "na", "A", "m1", _slug)
        tr.upsert_identity(self.con, slug_a, name="Quick", region="na",
                           faceit_team_id="Q1", source_authority="faceit", now=NOW)
        slug_b = tr.resolve_identity_slug(self.con, "Quick Esports", "Q1", "na", "A", "m2", _slug)
        self.assertEqual(slug_a, slug_b)


class TestUnsignedRoster(TeamRegistryTestCase):
    def test_mix_team_name_flagged_unsigned(self):
        tr.upsert_identity(self.con, "t1", name="NA Mix Team", region="na",
                           faceit_team_id="M1", source_authority="faceit", now=NOW)
        row = self.con.execute("SELECT status FROM teams WHERE id='t1'").fetchone()
        self.assertEqual(row["status"], "unsigned")

    def test_ordinary_org_name_stays_active(self):
        tr.upsert_identity(self.con, "t1", name="Team Falcons", region="na",
                           faceit_team_id="F1", source_authority="faceit", now=NOW)
        row = self.con.execute("SELECT status FROM teams WHERE id='t1'").fetchone()
        self.assertEqual(row["status"], "active")


class TestRosterProvenance(TeamRegistryTestCase):
    def test_record_roster_stamps_source_and_time(self):
        tr.upsert_identity(self.con, "t1", name="Falcons", region="na",
                           faceit_team_id="F1", source_authority="faceit", now=NOW)
        tr.record_roster(self.con, "t1", had_players=True, source="faceit", now=NOW)
        row = self.con.execute("SELECT roster_source, roster_verified_at FROM teams WHERE id='t1'").fetchone()
        self.assertEqual(row["roster_source"], "faceit")
        self.assertIsNotNone(row["roster_verified_at"])

    def test_empty_roster_never_marked_verified(self):
        tr.upsert_identity(self.con, "t1", name="Falcons", region="na",
                           faceit_team_id="F1", source_authority="faceit", now=NOW)
        tr.record_roster(self.con, "t1", had_players=False, source="faceit", now=NOW)
        row = self.con.execute("SELECT roster_verified_at FROM teams WHERE id='t1'").fetchone()
        self.assertIsNone(row["roster_verified_at"])

    def test_roster_change_updates_timestamp(self):
        tr.upsert_identity(self.con, "t1", name="Falcons", region="na",
                           faceit_team_id="F1", source_authority="faceit", now=NOW)
        tr.record_roster(self.con, "t1", had_players=True, source="faceit", now=NOW)
        later = NOW + dt.timedelta(days=10)
        tr.record_roster(self.con, "t1", had_players=True, source="faceit", now=later)
        row = self.con.execute("SELECT roster_verified_at FROM teams WHERE id='t1'").fetchone()
        self.assertEqual(row["roster_verified_at"], later.replace(microsecond=0).isoformat())


class TestConflictingFacts(TeamRegistryTestCase):
    def test_region_conflict_sets_needs_review_and_keeps_original(self):
        tr.upsert_identity(self.con, "t1", name="Falcons", region="na",
                           faceit_team_id="F1", source_authority="faceit", now=NOW)
        result = tr.upsert_identity(self.con, "t1", name="Falcons", region="emea",
                                    faceit_team_id="F1", source_authority="official-calendar", now=NOW)
        self.assertTrue(result["conflict"])
        row = self.con.execute("SELECT region, needs_review, review_reason FROM teams WHERE id='t1'").fetchone()
        self.assertEqual(row["region"], "na")  # never silently overwritten
        self.assertEqual(row["needs_review"], 1)
        self.assertIn("region conflict", row["review_reason"])

    def test_unknown_region_fills_in_without_conflict(self):
        tr.upsert_identity(self.con, "t1", name="Falcons", region="Unknown",
                           faceit_team_id="F1", source_authority="faceit", now=NOW)
        result = tr.upsert_identity(self.con, "t1", name="Falcons", region="na",
                                    faceit_team_id="F1", source_authority="faceit", now=NOW)
        self.assertFalse(result["conflict"])
        row = self.con.execute("SELECT region, needs_review FROM teams WHERE id='t1'").fetchone()
        self.assertEqual(row["region"], "na")
        self.assertEqual(row["needs_review"], 0)


class TestIdempotency(TeamRegistryTestCase):
    def test_identical_repeat_upsert_is_a_no_op(self):
        for _ in range(3):
            tr.upsert_identity(self.con, "t1", name="Falcons", region="na",
                               faceit_team_id="F1", source_authority="faceit", now=NOW)
        rows = self.con.execute("SELECT * FROM teams").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["previous_names"], "[]" if rows[0]["previous_names"] else None)
        self.assertFalse(rows[0]["needs_review"])


class TestNoCompositionWrites(TeamRegistryTestCase):
    def test_registry_functions_never_touch_comp_tables(self):
        before_snap = self.con.execute("SELECT COUNT(*) n FROM comp_snapshots").fetchone()["n"]
        before_stints = self.con.execute("SELECT COUNT(*) n FROM hero_stints").fetchone()["n"]
        tr.upsert_identity(self.con, "t1", name="Falcons", region="na",
                           faceit_team_id="F1", source_authority="faceit", now=NOW)
        tr.record_roster(self.con, "t1", had_players=True, source="faceit", now=NOW)
        after_snap = self.con.execute("SELECT COUNT(*) n FROM comp_snapshots").fetchone()["n"]
        after_stints = self.con.execute("SELECT COUNT(*) n FROM hero_stints").fetchone()["n"]
        self.assertEqual(before_snap, after_snap)
        self.assertEqual(before_stints, after_stints)


class TestDiscoveryIntegration(TeamRegistryTestCase):
    """Same scenario through the real discovery.upsert_match entry point."""

    def test_rename_through_upsert_match(self):
        comp = {"id": "c1", "region": "na", "season": "2026"}
        m1 = {
            "faceitMatchId": "m1", "lifecycleStatus": "finished", "contentStatus": "final",
            "finishedAt": "2026-07-01T00:00:00+00:00", "winnerSide": "A",
            "score": {"a": 3, "b": 1}, "faceitUrl": "u1",
            "teams": [
                {"name": "Falcons", "faceitTeamId": "F1", "players": []},
                {"name": "Bravo", "faceitTeamId": "B1", "players": []},
            ],
        }
        disc.upsert_match(self.con, m1, comp)
        m2 = dict(m1, faceitMatchId="m2", finishedAt="2026-07-02T00:00:00+00:00")
        m2["teams"] = [
            {"name": "Team Falcons", "faceitTeamId": "F1", "players": []},
            {"name": "Bravo", "faceitTeamId": "B1", "players": []},
        ]
        disc.upsert_match(self.con, m2, comp)
        rows = self.con.execute("SELECT * FROM teams WHERE faceit_team_id='F1'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Team Falcons")
        self.assertEqual(rows[0]["previous_names"], '["Falcons"]')


if __name__ == "__main__":
    unittest.main(verbosity=2)
