#!/usr/bin/env python3
"""
test_automation_faceit_api.py — FACEIT Data API client + normalizer.
Proves: status/lifecycle mapping, epoch->ISO, forfeit detection, pagination,
fixture transport, partial responses, and that NO composition field is ever
produced. No network, no API key.
Run: python3 pipeline/test_automation_faceit_api.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from automation import faceit_api as fa  # noqa: E402


def raw_match(mid="1-aaa", status="FINISHED", **kw):
    return {
        "match_id": mid,
        "competition_id": kw.get("comp_id", "champ1"),
        "competition_name": kw.get("comp_name", "OWCS NA"),
        "region": kw.get("region", "EU"),
        "status": status,
        "scheduled_at": kw.get("scheduled"),
        "started_at": kw.get("started"),
        "finished_at": kw.get("finished"),
        "faceit_url": kw.get("url", "https://www.faceit.com/{lang}/ow2/room/" + mid),
        "teams": {
            "faction1": {"team_id": kw.get("tid_a", "fa"), "name": kw.get("name_a", "Alpha"),
                         "roster": kw.get("roster_a", [{"player_id": "p1", "nickname": "aaa"}])},
            "faction2": {"team_id": kw.get("tid_b", "fb"), "name": kw.get("name_b", "Bravo"),
                         "roster": kw.get("roster_b", [{"player_id": "p2", "nickname": "bbb"}])},
        },
        "results": kw.get("results", {"winner": "faction1", "score": {"faction1": 3, "faction2": 1}}),
    }


class TestNormalizer(unittest.TestCase):
    def test_status_mapping(self):
        self.assertEqual(fa.map_status("SCHEDULED"), ("scheduled", "upcoming"))
        self.assertEqual(fa.map_status("ONGOING"), ("live", "live"))
        self.assertEqual(fa.map_status("FINISHED"), ("finished", "final"))
        self.assertEqual(fa.map_status("CANCELLED"), ("cancelled", "unknown"))
        self.assertEqual(fa.map_status("WEIRD"), ("unknown", "unknown"))
        self.assertEqual(fa.map_status("FINISHED", forfeit=True), ("forfeit", "final"))

    def test_epoch_conversion(self):
        m = fa.normalize_match(raw_match(scheduled=1690000000))
        self.assertTrue(m["scheduledAt"].startswith("2023-07-22"))
        # milliseconds are handled too
        m2 = fa.normalize_match(raw_match(scheduled=1690000000000))
        self.assertEqual(m2["scheduledAt"][:10], m["scheduledAt"][:10])

    def test_faceit_url_lang_substituted(self):
        m = fa.normalize_match(raw_match())
        self.assertIn("/en/ow2/room/", m["faceitUrl"])
        self.assertNotIn("{lang}", m["faceitUrl"])

    def test_forfeit_detected_from_zero_score(self):
        m = fa.normalize_match(raw_match(
            status="FINISHED", results={"winner": "faction1", "score": {"faction1": 0, "faction2": 0}}))
        self.assertEqual(m["lifecycleStatus"], "forfeit")
        self.assertEqual(m["contentStatus"], "final")

    def test_region_override(self):
        m = fa.normalize_match(raw_match(region="EU"), region="emea")
        self.assertEqual(m["region"], "emea")

    def test_no_composition_field_ever(self):
        m = fa.normalize_match(raw_match())
        # No top-level key implies a hero composition (competitionId is fine).
        for key in m.keys():
            low = key.lower()
            self.assertNotIn("hero", low)
            self.assertNotIn("swap", low)
            self.assertNotIn("snapshot", low)
            self.assertNotEqual(low, "comp")
            self.assertNotEqual(low, "composition")
        # teams carry players but never heroes
        for t in m["teams"]:
            for p in t["players"]:
                self.assertNotIn("hero", p)

    def test_partial_missing_teams(self):
        raw = {"match_id": "1-x", "status": "SCHEDULED"}  # no teams/results
        m = fa.normalize_match(raw)
        self.assertEqual(m["faceitMatchId"], "1-x")
        self.assertIsNone(m["teams"][0]["name"])
        self.assertEqual(m["score"], {"a": None, "b": None})
        self.assertIsNone(m["winnerSide"])


class TestClient(unittest.TestCase):
    def test_pagination_and_fixture_transport(self):
        with tempfile.TemporaryDirectory() as d:
            # 120 matches -> pages of 50 -> 3 pages, but the fixture serves the
            # whole list in one file, so pagination stops when a short page
            # returns. Use a small list to keep it simple.
            items = [raw_match(mid=f"1-{i}") for i in range(3)]
            with open(os.path.join(d, "champ1.json"), "w") as f:
                json.dump({"items": items}, f)
            client = fa.FaceitClient(transport=fa.fixture_transport(d))
            got = client.list_championship_matches("champ1")
            self.assertEqual(len(got), 3)
            self.assertEqual(len(client.calls), 1)

    def test_missing_fixture_raises_api_error(self):
        with tempfile.TemporaryDirectory() as d:
            client = fa.FaceitClient(transport=fa.fixture_transport(d))
            with self.assertRaises(fa.FaceitApiError):
                client.list_championship_matches("does_not_exist")

    def test_no_key_no_transport_raises_auth(self):
        client = fa.FaceitClient(api_key=None, transport=None)
        with self.assertRaises(fa.FaceitAuthError):
            client.get_championship("champ1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
