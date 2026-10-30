#!/usr/bin/env python3
"""live_identity: naming each map window from the broadcast's own HUD text.

Offline: the OCR is a fake that answers by which zone it was shown (each
zone has its own width), so no tesseract binary is needed. The real reader
was measured on 80 frames from 20 maps of Twitch 2831815423 — the misreads
fed in here ("GAME |", "Tl", "GEEXAY", "SAMDA") are ones it actually made.
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np  # noqa: E402

import capture  # noqa: E402
import live_identity as lid  # noqa: E402

LAYOUT = capture.load_layout(os.path.join(HERE, "..", "layouts", "owcs_8c105lnzlam.json"))
MAPS = [{"id": "samoa", "name": "Samoa", "mode": "control"},
        {"id": "aatlis", "name": "Aatlis", "mode": "flashpoint"},
        {"id": "shambali", "name": "Shambali Monastery", "mode": "escort"},
        {"id": "njc", "name": "New Junk City", "mode": "flashpoint"}]
TEAMS = [{"id": "gen", "name": "Gen.G Esports", "code": "GEN"},
         {"id": "zeta", "name": "ZETA Division", "code": "ZETA"},
         {"id": "twis", "name": "Twisted Minds", "code": "TM"}]
FRAME = np.zeros((720, 1280, 3), np.uint8)


def zone_width(key: str) -> int:
    w = max(1, int(LAYOUT["identity_zones"][key][2] * 1280))
    return w * 4 + 40


def fake_ocr(script: dict[str, list[str]]):
    """script: zone -> one reading per frame (cycled per call within a frame)."""
    by_width = {zone_width(k): k for k in script}
    calls: dict[str, int] = {k: 0 for k in script}
    per_frame = len(lid.THRESHOLDS) * len(lid.MODES)

    def read(img, psm):
        key = by_width.get(img.shape[1])
        if key is None:
            return ""
        i = calls[key] // per_frame
        calls[key] += 1
        return script[key][i % len(script[key])]
    return read


class TestReadings(unittest.TestCase):
    def test_game_one_reads_as_a_bar(self):
        self.assertEqual(lid.resolve_game("GAME |"), 1)
        self.assertEqual(lid.resolve_game("CAME |"), 1)
        self.assertEqual(lid.resolve_game("GAME 3"), 3)
        self.assertIsNone(lid.resolve_game("a 2"))

    def test_team_bar_junk_is_dropped(self):
        self.assertEqual(lid.clean_team("y VARREL ye"), "VARREL")
        self.assertEqual(lid.clean_team("97 GLOBANT \\"), "GLOBANT")
        self.assertEqual(lid.clean_team("7 —_ SPACESTATION GAMING"), "SPACESTATION GAMING")
        self.assertEqual(lid.clean_team("y Tl"), "T1")

    def test_the_broadcasts_own_spelling_of_a_map(self):
        self.assertEqual(lid.resolve_map("ATLAS", MAPS, LAYOUT["map_aliases"]), "aatlis")
        self.assertEqual(lid.resolve_map("SAMDA", MAPS, {}), "samoa")
        self.assertIsNone(lid.resolve_map("MONASTERY", MAPS, {}))

    def test_misreads_of_one_name_vote_together(self):
        frames = [{"GEEKAY ESPORTS"}, {"GEEXAY ESPORTS"}, {"GEEKAY ESPORTS"}, {"GEEXAY ESPORTS"}]
        w = lid._winner(lid.cluster_names(frames), "team")
        self.assertEqual(w["value"], "GEEKAY ESPORTS")
        self.assertEqual(w["votes"], 4)

    def test_a_tie_names_nothing(self):
        w = lid._winner([{"T1"}, {"N1"}, {"T1"}, {"N1"}], "team")
        self.assertIsNone(w["value"])


class TestIdentify(unittest.TestCase):
    def test_a_window_is_named_from_its_frames(self):
        ocr = fake_ocr({"map": ["SAMOA", "SAMDA", "SAMOA", ""],
                        "game": ["GAME |"],
                        "team_a": ["y AG.AL", "y AGAL", "y AG.AL", "y AG.AL"],
                        "team_b": ["ZETA DIVISION \\"]})
        r = lid.identify([FRAME] * 4, LAYOUT, known_maps=MAPS, known_teams=TEAMS, ocr=ocr)
        self.assertEqual(r["map"]["value"], "samoa")
        self.assertEqual(r["game"]["value"], 1)
        self.assertEqual((r["team_a"]["value"], r["team_a"]["known"]), ("AG.AL", False))
        self.assertEqual((r["team_b"]["value"], r["team_b"]["known"]), ("zeta", True))
        self.assertTrue(r["complete"])

    def test_an_unknown_team_is_never_guessed_into_a_known_one(self):
        """team_identify's fuzzy cutoff read GEEKAY ESPORTS as Gen.G Esports."""
        ocr = fake_ocr({"map": ["AATLIS"], "game": ["GAME 2"],
                        "team_a": ["y AG.AL"], "team_b": ["GEEKAY ESPORTS \\"]})
        r = lid.identify([FRAME] * 3, LAYOUT, known_maps=MAPS, known_teams=TEAMS, ocr=ocr)
        self.assertEqual(r["team_b"]["value"], "GEEKAY ESPORTS")
        self.assertFalse(r["team_b"]["known"])

    def test_no_zones_no_identity(self):
        self.assertIsNone(lid.identify([FRAME], {}, known_maps=MAPS, known_teams=TEAMS,
                                       ocr=lambda i, p: "X"))


def window(start, end, mp, game, a, b, votes=10, known_b=False):
    def t(v, known=False):
        return {"value": v, "votes": votes, "known": known}
    return {"start": start, "end": end, "runs": [[start, end]], "gameplaySamples": 5,
            "identity": {"map": t(mp), "game": t(game), "team_a": t(a), "team_b": t(b, known_b),
                         "complete": True}}


class TestBroadcast(unittest.TestCase):
    def test_one_map_split_by_a_pause_is_joined(self):
        maps = lid.merge_split_maps([
            window(8760, 9420, "njc", 2, "TEAM SECRET", "cr"),
            window(9720, 9990, "njc", 2, "TEAM SECRET", "cr"),
            window(12480, 13110, "samoa", 1, "AG.AL", "GEEKAY ESPORTS")])
        self.assertEqual([(m["index"], m["start"], m["end"]) for m in maps],
                         [(1, 8760, 9990), (2, 12480, 13110)])
        self.assertTrue(maps[0]["mergedPause"])

    def test_the_next_game_of_a_series_is_not_joined(self):
        maps = lid.merge_split_maps([window(0, 600, "njc", 2, "A", "B"),
                                     window(800, 1400, "njc", 3, "A", "B")])
        self.assertEqual(len(maps), 2)

    def test_one_spelling_per_team_across_the_broadcast(self):
        maps = lid.harmonise_names([
            window(0, 600, "nepal", 1, "TEAM LIQUID", "WEHIBO GAMING", votes=3),
            window(900, 1500, "neonjunction", 2, "TEAM LIQUID", "WEIBO GAMING", votes=12),
            window(1800, 2400, "aatlis", 3, "TEAM LIQUID", "WEIBO GAMING", votes=9)])
        self.assertEqual({m["identity"]["team_b"]["value"] for m in maps}, {"WEIBO GAMING"})

    def test_a_short_name_misread_on_one_map_of_a_series_is_corrected(self):
        """The real run read T1 as N1 on game 2 of T1 vs Twisted Minds."""
        maps = lid.harmonise_series([
            window(22470, 23340, "nepal", 1, "T1", "twis", votes=12, known_b=True),
            window(24990, 25650, "aatlis", 2, "N1", "twis", votes=8, known_b=True),
            window(25950, 27060, "route66", 3, "T1", "twis", votes=14, known_b=True),
            window(29220, 29820, "nepal", 1, "TEAM LIQUID", "WEIBO GAMING")])
        self.assertEqual([m["identity"]["team_a"]["value"] for m in maps],
                         ["T1", "T1", "T1", "TEAM LIQUID"])
        self.assertEqual(maps[1]["identity"]["team_a"]["seriesCorrected"], "N1")

    def test_series_follow_the_game_numbers(self):
        runs = lid.series([window(0, 1, "a", 1, "A", "B"), window(2, 3, "b", 2, "B", "A"),
                           window(4, 5, "c", 1, "B", "C")])
        self.assertEqual([len(r) for r in runs], [2, 1])

    def test_a_known_team_is_never_respelled(self):
        maps = lid.harmonise_names([window(0, 600, "nepal", 1, "ZETAA", "zeta", known_b=True),
                                    window(900, 1500, "nepal", 2, "ZETAA", "zeta", known_b=True)])
        self.assertEqual(maps[0]["identity"]["team_b"]["value"], "zeta")


if __name__ == "__main__":
    unittest.main(verbosity=2)
