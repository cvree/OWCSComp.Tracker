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


class TestOneOrgOneIdentity(unittest.TestCase):
    """A HUD name is OCR. A misreading of a team on record is that team;
    a name that cannot be tied to anything is flagged, not published as an org."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.maps = os.path.join(self.dir, "maps")
        os.makedirs(self.maps)
        self.sources = os.path.join(self.dir, "sources.json")
        with open(self.sources, "w", encoding="utf-8") as f:
            json.dump({"teams": {"whibo-gaming": {}, "who-gaming": {}, "weibo-gaming": {}}}, f)
        self.con = sqlite3.connect(":memory:")
        self.con.execute("CREATE TABLE teams (id TEXT PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL "
                         "DEFAULT 'Unknown', code TEXT NOT NULL, logo_url TEXT, aliases TEXT, "
                         "source_authority TEXT, identity_verified_at TEXT, status TEXT NOT NULL DEFAULT "
                         "'active', needs_review INTEGER NOT NULL DEFAULT 0, review_reason TEXT)")
        self.logo = bt.logo_from_crops([bt.crop_logo(hud_frame(), "team_a")] * 3)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def add(self, names, logos=None, now="2026-10-30T00:00:00+00:00", reference=None):
        return bt.ensure_teams(self.con, set(names), logos or {}, now=now, teams_dir=self.dir,
                               sources_path=None, reference=reference)

    def ids(self):
        return [r[0] for r in self.con.execute("SELECT id FROM teams ORDER BY id")]

    def test_a_near_spelling_is_the_known_team_not_a_second_one(self):
        self.add(["WEIBO GAMING"])
        done = self.add(["WHIBO GAMING"])
        self.assertEqual((done[0]["id"], done[0]["added"]), ("weibo-gaming", False))
        self.assertEqual(done[0]["mergedBy"], "spelling")
        self.assertEqual(self.ids(), ["weibo-gaming"])
        aliases = json.loads(self.con.execute("SELECT aliases FROM teams").fetchone()[0])
        self.assertIn("WHIBO GAMING", aliases, "the misreading is kept as an alias, so it resolves next time")

    def test_a_looser_spelling_needs_the_same_mark_beside_it(self):
        self.add(["WEIBO GAMING"], {"WEIBO GAMING": self.logo})
        self.assertGreaterEqual(bt.name_ratio("WHO GAMING", "WEIBO GAMING"), bt.MERGE_WITH_LOGO)
        self.assertLess(bt.name_ratio("WHO GAMING", "WEIBO GAMING"), bt.MERGE_NAME)
        # no logo: the spelling alone is not enough at this distance
        self.assertIsNone(bt.resolve_known(self.con, "WHO GAMING", None, teams_dir=self.dir))
        # the same mark: it is the same team
        hit = bt.resolve_known(self.con, "WHO GAMING", self.logo, teams_dir=self.dir)
        self.assertEqual((hit["id"], hit["by"]), ("weibo-gaming", "spelling + same HUD mark"))

    def test_different_teams_with_alike_names_stay_apart(self):
        self.add(["JD GAMING", "TEAM LIQUID", "TEAM SECRET", "T1"], {"JD GAMING": self.logo})
        for name in ("OU GAMING", "TEAM FALCONS", "TEAM PEPS", "TL"):
            self.assertIsNone(bt.resolve_known(self.con, name, self.logo, teams_dir=self.dir), name)
        self.add(["OU GAMING"], {"OU GAMING": self.logo})
        self.assertIn("ou-gaming", self.ids())

    def test_a_bare_generic_word_is_added_but_flagged_with_the_reason(self):
        self.add(["TEAM LIQUID"])
        self.add(["TEAM"])
        flagged, why = self.con.execute("SELECT needs_review, review_reason FROM teams WHERE id='team'").fetchone()
        self.assertEqual(flagged, 1)
        self.assertIn("TEAM", why)
        self.assertEqual(self.con.execute("SELECT needs_review FROM teams WHERE id='team-liquid'").fetchone()[0], 0)

    def test_a_name_that_resembles_another_sources_team_is_flagged_never_renamed(self):
        self.add(["FEAMRISK", "VARREL"], reference=["Team Risk", "Varrel", "The Kafe"])
        name, flagged, why = self.con.execute(
            "SELECT name, needs_review, review_reason FROM teams WHERE id='feamrisk'").fetchone()
        self.assertEqual((name, flagged), ("Feamrisk", 1), "flagged, and still spelled as the HUD printed it")
        self.assertIn("Team Risk", why)
        self.assertEqual(self.con.execute("SELECT needs_review FROM teams WHERE id='varrel'").fetchone()[0], 0,
                         "a name the other source spells the same way is not doubtful")

    def test_repair_merges_duplicates_already_on_record_and_repoints_the_map_index(self):
        import cv2
        for tid, name, when in (("weibo-gaming", "Weibo Gaming", "2026-10-30"),
                                ("who-gaming", "WHO Gaming", "2026-10-31"),
                                ("whibo-gaming", "Whibo Gaming", "2026-11-02"),
                                ("jd-gaming", "JD Gaming", "2026-10-30")):
            self.con.execute("INSERT INTO teams (id, name, code, aliases, source_authority, identity_verified_at) "
                             "VALUES (?,?,?,?,?,?)", (tid, name, "X", json.dumps([name.upper()]), bt.SOURCE, when))
            os.makedirs(os.path.join(self.dir, tid))
            cv2.imwrite(os.path.join(self.dir, tid, "logo.png"), self.logo)
        self.con.execute("CREATE TABLE matches (id TEXT, team_a TEXT)")
        with open(os.path.join(self.maps, "1.json"), "w", encoding="utf-8") as f:
            json.dump({"videoId": "1", "maps": [{"index": 1, "team_a": {"id": "who-gaming", "name": "WHO GAMING"},
                                               "team_b": {"id": "jd-gaming", "name": "JD GAMING"}}]}, f)
        dry = bt.repair(self.con, data_dir=self.maps, teams_dir=self.dir, sources_path=self.sources, dry_run=True)
        self.assertEqual(len(self.ids()), 4, "a dry run changes nothing")
        self.assertEqual({d["id"] for d in dry if d["action"] == "merged"}, {"who-gaming", "whibo-gaming"})

        out = bt.repair(self.con, data_dir=self.maps, teams_dir=self.dir, sources_path=self.sources)
        self.assertEqual({(d["id"], d["into"]) for d in out if d["action"] == "merged"},
                         {("who-gaming", "weibo-gaming"), ("whibo-gaming", "weibo-gaming")})
        self.assertEqual(self.ids(), ["jd-gaming", "weibo-gaming"], "JD Gaming has the same mark here and is NOT merged")
        with open(os.path.join(self.maps, "1.json"), encoding="utf-8") as f:
            rec = json.load(f)
        self.assertEqual(rec["maps"][0]["team_a"], {"id": "weibo-gaming", "name": "WHO GAMING"},
                         "the map points at the surviving team and keeps what the HUD printed")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "who-gaming")), "the duplicate logo is gone")
        with open(self.sources, encoding="utf-8") as f:
            self.assertEqual(sorted(json.load(f)["teams"]), ["weibo-gaming"])
        self.assertEqual(bt.repair(self.con, data_dir=self.maps, teams_dir=self.dir, sources_path=self.sources), [],
                         "running it again finds nothing to do")

    def test_repair_never_merges_a_team_other_records_refer_to(self):
        for tid, name, when in (("weibo-gaming", "Weibo Gaming", "2026-10-30"), ("whibo-gaming", "Whibo Gaming", "2026-11-02")):
            self.con.execute("INSERT INTO teams (id, name, code, aliases, source_authority, identity_verified_at) "
                             "VALUES (?,?,?,?,?,?)", (tid, name, "X", json.dumps([name.upper()]), bt.SOURCE, when))
        self.con.execute("CREATE TABLE matches (id TEXT, team_a TEXT)")
        self.con.execute("INSERT INTO matches VALUES ('m1', 'whibo-gaming')")
        out = bt.repair(self.con, data_dir=self.maps, teams_dir=self.dir, sources_path=self.sources)
        self.assertEqual([(d["id"], d["action"]) for d in out], [("whibo-gaming", "flagged")])
        self.assertIn("matches.team_a", out[0]["reason"])
        self.assertEqual(self.ids(), ["weibo-gaming", "whibo-gaming"])

    def test_the_committed_registry_has_no_misread_duplicates_left(self):
        import db
        con = db.connect()
        try:
            self.assertEqual([d for d in bt.repair(con, reference=bt.reference_names(), dry_run=True)
                              if d["action"] == "merged"], [],
                             "run `python3 pipeline/broadcast_teams.py --repair`")
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
