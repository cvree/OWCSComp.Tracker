#!/usr/bin/env python3
"""
test_automation_candidates.py — read-only candidate discovery + verification.
Proves the FACEIT search/organizer/verify helpers normalize championships
correctly and stay fact-only. Offline (fixture transport), no key.
Run: python3 pipeline/test_automation_candidates.py
"""
from __future__ import annotations
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from automation import faceit_api as fa  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "automation")


def client():
    return fa.FaceitClient(transport=fa.fixture_transport(FIX))


class TestCandidateDiscovery(unittest.TestCase):
    def test_search_championships(self):
        rows = client().search_championships("OWCS")
        ids = [fa.normalize_championship(r)["championshipId"] for r in rows]
        self.assertIn("owcs-na-demo-0001", ids)

    def test_normalize_championship_fields(self):
        raw = client().search_championships("OWCS")[0]
        c = fa.normalize_championship(raw)
        self.assertEqual(c["championshipId"], "owcs-na-demo-0001")
        self.assertEqual(c["organizerId"], "org-owcs-demo")
        self.assertEqual(c["region"], "US")
        self.assertTrue(c["startDate"].startswith("20"))
        # facts only — no composition-shaped keys
        for k in c:
            self.assertNotIn("hero", k.lower())
            self.assertNotIn("comp", k.lower().replace("championship", "").replace("competition", ""))

    def test_search_organizers(self):
        orgs = client().search_organizers("Overwatch")
        o = fa.normalize_organizer(orgs[0])
        self.assertEqual(o["organizerId"], "org-owcs-demo")
        self.assertIn("Overwatch", o["name"])

    def test_list_organizer_championships_filters_game(self):
        rows = client().list_organizer_championships("org-owcs-demo", game="ow2")
        self.assertEqual(len(rows), 2)
        # a non-ow2 game filter yields nothing from these ow2 fixtures
        self.assertEqual(client().list_organizer_championships("org-owcs-demo", game="csgo"), [])

    def test_get_championship_detail(self):
        c = fa.normalize_championship(client().get_championship("owcs-na-demo-0001"))
        self.assertEqual(c["name"], "OWCS 2026 North America Stage 2")
        self.assertEqual(c["endDate"][:4], "2026")
        self.assertNotIn("{lang}", c["faceitUrl"])

    def test_verify_missing_raises(self):
        with self.assertRaises(fa.FaceitApiError):
            client().get_championship("does-not-exist")


if __name__ == "__main__":
    unittest.main(verbosity=2)
