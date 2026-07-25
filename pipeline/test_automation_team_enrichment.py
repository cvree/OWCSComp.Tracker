#!/usr/bin/env python3
"""
test_automation_team_enrichment.py — Phase D team profile enrichment.

Covers: FACEIT-only enrichment (no search, no guessed ids), fact upsert never
blanks a known value (COALESCE), candidate-source auto-population that never
writes a logo directly, idempotent reruns, one team's API failure never
blocking the rest, dry-run purity, team_id filtering, and that no hero/comp
field is ever produced. No network, no API key.
Run: python3 pipeline/test_automation_team_enrichment.py
"""
from __future__ import annotations
import datetime as dt
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db as content_db  # noqa: E402
from automation import faceit_api as fa  # noqa: E402
from automation import team_enrichment as tenrich  # noqa: E402

NOW = dt.datetime(2026, 7, 25, 12, 0, 0, tzinfo=dt.timezone.utc)


def raw_team(team_id="ft-1", *, nickname="Spacestation Gaming",
             description="Pro esports org", website="https://example.org",
             twitter="ssg", facebook=None, avatar="https://img.example/ssg.png",
             cover=None, members=None):
    return {
        "team_id": team_id, "nickname": nickname, "description": description,
        "website": website, "twitter": twitter, "facebook": facebook,
        "avatar": avatar, "cover_image": cover,
        "members": members if members is not None else [
            {"user_id": "p1", "nickname": "aaa", "country": "us"},
            {"user_id": "p2", "nickname": "bbb", "country": "ca"},
        ],
    }


def transport_for(mapping: dict) -> fa.Transport:
    """mapping: faceit_team_id -> raw team dict (or Exception to fail)."""
    import re as _re

    def _t(url, headers):
        m = _re.search(r"/teams/([^/?]+)", url)
        if not m:
            return 404, None, "unmapped"
        tid = m.group(1)
        val = mapping.get(tid)
        if val is None:
            return 404, None, "no such team"
        if isinstance(val, Exception):
            return 500, None, str(val)
        return 200, json.dumps(val), None
    return _t


class EnrichCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.content_path = os.path.join(self.tmp.name, "owcs.sqlite")
        self.assets_path = os.path.join(self.tmp.name, "team_asset_sources.json")
        self.con = content_db.connect(self.content_path)
        content_db.init_schema(self.con)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def seed_team(self, tid="ssg", faceit_team_id="ft-1", name="Spacestation Gaming"):
        self.con.execute(
            "INSERT INTO teams (id, name, region, code, faceit_team_id) "
            "VALUES (?,?,?,?,?)", (tid, name, "na", "SSG", faceit_team_id))
        self.con.commit()

    def client(self, mapping):
        return fa.FaceitClient(transport=transport_for(mapping))

    def run_enrich(self, mapping, **kw):
        return tenrich.enrich_teams(
            con=self.con, client=self.client(mapping),
            asset_sources_path=self.assets_path, now=NOW, **kw)


class TestNormalize(unittest.TestCase):
    def test_facts_only_no_composition_fields(self):
        n = fa.normalize_team(raw_team())
        for key in n:
            low = key.lower()
            self.assertNotIn("hero", low)
            self.assertNotIn("swap", low)
            self.assertNotIn("comp", low)
        self.assertEqual(n["faceitTeamId"], "ft-1")
        self.assertEqual(n["memberCount"], 2)
        self.assertEqual(n["members"][0]["nickname"], "aaa")

    def test_partial_response_no_crash(self):
        n = fa.normalize_team({"team_id": "ft-2"})
        self.assertIsNone(n["description"])
        self.assertIsNone(n["memberCount"])
        self.assertEqual(n["members"], [])


class TestEnrichHappyPath(EnrichCase):
    def test_enriches_known_team(self):
        self.seed_team()
        r = self.run_enrich({"ft-1": raw_team()})
        self.assertEqual(len(r["enriched"]), 1)
        row = self.con.execute("SELECT * FROM teams WHERE id='ssg'").fetchone()
        self.assertEqual(row["description"], "Pro esports org")
        self.assertEqual(row["website"], "https://example.org")
        self.assertEqual(row["member_count"], 2)
        self.assertIsNotNone(row["faceit_enriched_at"])
        # logo_url must NEVER be written directly from FACEIT's avatar.
        self.assertIsNone(row["logo_url"])

    def test_candidate_source_recorded_not_hotlinked(self):
        self.seed_team()
        r = self.run_enrich({"ft-1": raw_team(avatar="https://img.example/ssg.png")})
        self.assertEqual(len(r["newCandidateSources"]), 1)
        with open(self.assets_path) as f:
            registry = json.load(f)
        sources = registry["teams"]["ssg"]["candidateSources"]
        self.assertEqual(len(sources), 1)
        self.assertIn("https://img.example/ssg.png", sources[0])
        self.assertIn("unverified candidate", sources[0])
        # still never written as the logo.
        row = self.con.execute("SELECT logo_url FROM teams WHERE id='ssg'").fetchone()
        self.assertIsNone(row["logo_url"])

    def test_teams_without_faceit_id_never_touched(self):
        self.con.execute(
            "INSERT INTO teams (id, name, region, code) VALUES "
            "('mystery', 'Mystery Org', 'na', 'MYS')")
        self.con.commit()
        r = self.run_enrich({})
        self.assertEqual(r["teamsConsidered"], 0)
        self.assertEqual(r["enriched"], [])


class TestCoalesceNeverBlanks(EnrichCase):
    def test_thin_response_does_not_null_existing_facts(self):
        self.seed_team()
        self.run_enrich({"ft-1": raw_team(description="Full bio", website="https://a.example")})
        row1 = self.con.execute("SELECT description, website FROM teams WHERE id='ssg'").fetchone()
        self.assertEqual(row1["description"], "Full bio")
        # A second, thinner response (API returned less this time) must not
        # erase what was already known.
        self.run_enrich({"ft-1": {"team_id": "ft-1"}})
        row2 = self.con.execute("SELECT description, website FROM teams WHERE id='ssg'").fetchone()
        self.assertEqual(row2["description"], "Full bio")
        self.assertEqual(row2["website"], "https://a.example")


class TestIdempotency(EnrichCase):
    def test_rerun_no_duplicate_candidate_sources(self):
        self.seed_team()
        self.run_enrich({"ft-1": raw_team()})
        r2 = self.run_enrich({"ft-1": raw_team()})
        self.assertEqual(r2["newCandidateSources"], [])
        with open(self.assets_path) as f:
            registry = json.load(f)
        self.assertEqual(len(registry["teams"]["ssg"]["candidateSources"]), 1)

    def test_rerun_row_count_unchanged(self):
        self.seed_team()
        self.run_enrich({"ft-1": raw_team()})
        n1 = self.con.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
        self.run_enrich({"ft-1": raw_team()})
        n2 = self.con.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
        self.assertEqual(n1, n2)


class TestFailureIsolation(EnrichCase):
    def test_one_team_api_failure_does_not_block_others(self):
        self.seed_team("ssg", "ft-1")
        self.seed_team("nrg", "ft-2", name="NRG Shock")
        r = self.run_enrich({"ft-1": raw_team(team_id="ft-1"),
                             "ft-2": RuntimeError("boom 500")})
        self.assertEqual(len(r["enriched"]), 1)
        self.assertEqual(len(r["errors"]), 1)
        self.assertEqual(r["errors"][0]["teamId"], "nrg")
        row_ok = self.con.execute("SELECT description FROM teams WHERE id='ssg'").fetchone()
        self.assertIsNotNone(row_ok["description"])


class TestDryRunPurity(EnrichCase):
    def test_dry_run_writes_nothing(self):
        self.seed_team()
        r = self.run_enrich({"ft-1": raw_team()}, dry_run=True)
        self.assertEqual(len(r["enriched"]), 1)
        self.assertEqual(len(r["newCandidateSources"]), 1)  # reported, not written
        row = self.con.execute("SELECT description, faceit_enriched_at FROM teams WHERE id='ssg'").fetchone()
        self.assertIsNone(row["description"])
        self.assertIsNone(row["faceit_enriched_at"])
        self.assertFalse(os.path.exists(self.assets_path))


class TestTeamIdFilter(EnrichCase):
    def test_filters_to_requested_team_ids(self):
        self.seed_team("ssg", "ft-1")
        self.seed_team("nrg", "ft-2", name="NRG Shock")
        r = self.run_enrich(
            {"ft-1": raw_team(team_id="ft-1"), "ft-2": raw_team(team_id="ft-2", nickname="NRG")},
            team_ids=["ssg"])
        self.assertEqual(r["teamsConsidered"], 1)
        self.assertEqual(r["enriched"][0]["teamId"], "ssg")
        row = self.con.execute("SELECT description FROM teams WHERE id='nrg'").fetchone()
        self.assertIsNone(row["description"])


class TestFixtureTransport(unittest.TestCase):
    def test_team_endpoint_routes_to_team_file(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "team_ft-1.json"), "w") as f:
                json.dump(raw_team(), f)
            client = fa.FaceitClient(transport=fa.fixture_transport(d))
            raw = client.get_team("ft-1")
            self.assertEqual(raw["team_id"], "ft-1")

    def test_missing_team_fixture_raises(self):
        with tempfile.TemporaryDirectory() as d:
            client = fa.FaceitClient(transport=fa.fixture_transport(d))
            with self.assertRaises(fa.FaceitApiError):
                client.get_team("does-not-exist")


if __name__ == "__main__":
    unittest.main(verbosity=2)
