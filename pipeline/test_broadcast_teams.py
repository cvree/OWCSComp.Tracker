#!/usr/bin/env python3
"""broadcast_teams: teams the broadcast names, added with their HUD logo."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np  # noqa: E402

import broadcast_teams as bt  # noqa: E402


def hud_frame(mark_left: bool = True):
    """A 720p frame with the HUD's white logo plate and a black mark on it."""
    f = np.full((720, 1280), 40, np.uint8)
    for key, (x, y, w, h) in bt.LOGO_ZONES.items():
        f[y + 2:y + h - 2, x + 4:x + w - 4] = 235                 # the white plate
        cx, cy = x + w // 2, y + h // 2
        f[cy - 8:cy + 8, cx - 3:cx + 3] = 20                       # a mark
        f[cy - 3:cy + 3, cx - 9:cx + 9] = 20
    return f


class TestNames(unittest.TestCase):
    def test_display_names_and_codes(self):
        self.assertEqual(bt.display("TEAM LIQUID"), "Team Liquid")
        self.assertEqual(bt.display("VIRTUS PRO"), "Virtus Pro")
        self.assertEqual(bt.display("JD GAMING"), "JD Gaming")
        self.assertEqual(bt.display("AG.AL"), "AG.AL")
        self.assertEqual(bt.code("JD GAMING"), "JDG")
        self.assertEqual(bt.code("TEAM LIQUID"), "TL")
        self.assertEqual(bt.code("T1"), "T1")
        self.assertEqual(bt.slug("AG.AL"), "ag-al")


class TestLogo(unittest.TestCase):
    def test_the_mark_becomes_white_on_transparency(self):
        crops = [bt.crop_logo(hud_frame(), "team_a") for _ in range(4)]
        logo = bt.logo_from_crops(crops)
        self.assertEqual(logo.shape, (bt.LOGO_SIZE, bt.LOGO_SIZE, 4))
        self.assertTrue((logo[..., :3] == 255).all())
        a = logo[..., 3]
        self.assertGreater(int(a.max()), 200, "the mark is opaque")
        self.assertEqual(int(a[0, 0]), 0, "the plate and the border are transparent")

    def test_too_few_frames_make_no_logo(self):
        self.assertIsNone(bt.logo_from_crops([bt.crop_logo(hud_frame(), "team_b")]))


class TestTeams(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.sources = os.path.join(self.dir, "sources.json")
        with open(self.sources, "w", encoding="utf-8") as f:
            json.dump({"teams": {}}, f)
        self.con = sqlite3.connect(":memory:")
        self.con.execute("CREATE TABLE teams (id TEXT PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL "
                         "DEFAULT 'Unknown', code TEXT NOT NULL, logo_url TEXT, aliases TEXT, "
                         "source_authority TEXT, identity_verified_at TEXT, status TEXT NOT NULL DEFAULT "
                         "'active', needs_review INTEGER NOT NULL DEFAULT 0)")
        self.con.execute("INSERT INTO teams (id, name, code, logo_url) VALUES "
                         "('zeta', 'ZETA Division', 'ZETA', 'assets/img/teams/zeta/logo.png')")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_a_printed_name_becomes_a_team_with_its_logo_and_provenance(self):
        logo = bt.logo_from_crops([bt.crop_logo(hud_frame(), "team_a")] * 3)
        done = bt.ensure_teams(self.con, {"TEAM LIQUID"}, {"TEAM LIQUID": logo},
                               now="2026-10-30T00:00:00+00:00", teams_dir=self.dir, video="1",
                               url="https://www.twitch.tv/videos/1", sources_path=self.sources)
        self.assertEqual(done, [{"name": "TEAM LIQUID", "id": "team-liquid", "added": True, "logo": True}])
        row = self.con.execute("SELECT name, code, logo_url, source_authority, aliases FROM teams "
                               "WHERE id='team-liquid'").fetchone()
        self.assertEqual(row[:4], ("Team Liquid", "TL", "assets/img/teams/team-liquid/logo.png", "broadcast-hud"))
        self.assertEqual(json.loads(row[4]), ["TEAM LIQUID"])
        self.assertTrue(os.path.exists(os.path.join(self.dir, "team-liquid", "logo.png")))
        with open(os.path.join(self.dir, "team-liquid", "logo.source.json"), encoding="utf-8") as f:
            self.assertIn("broadcast", json.load(f)["source"].lower())
        with open(self.sources, encoding="utf-8") as f:
            reg = json.load(f)["teams"]["team-liquid"]
        self.assertEqual((reg["sourceUrl"], reg["sourceKind"]),
                         ("https://www.twitch.tv/videos/1", "official-owcs-broadcast"))

    def test_a_team_that_has_a_logo_keeps_it(self):
        logo = bt.logo_from_crops([bt.crop_logo(hud_frame(), "team_b")] * 3)
        done = bt.ensure_teams(self.con, {"ZETA DIVISION"}, {"ZETA DIVISION": logo},
                               now="x", teams_dir=self.dir, sources_path=self.sources)
        self.assertEqual((done[0]["id"], done[0]["added"], done[0]["logo"]), ("zeta", False, False))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "zeta")))

    def test_adding_twice_changes_nothing(self):
        bt.ensure_teams(self.con, {"T1"}, {}, now="x", teams_dir=self.dir, sources_path=self.sources)
        again = bt.ensure_teams(self.con, {"T1"}, {}, now="y", teams_dir=self.dir, sources_path=self.sources)
        self.assertFalse(again[0]["added"])
        self.assertEqual(self.con.execute("SELECT count(*) FROM teams").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
