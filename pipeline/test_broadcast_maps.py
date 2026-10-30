#!/usr/bin/env python3
"""broadcast_maps: the published index of where every map starts."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import broadcast_maps as bm  # noqa: E402


def team(v, known=False, votes=10, of=10):
    return {"value": v, "printed": v.upper() if known else v, "known": known,
            "votes": votes, "of": of}


def window(i, start, mp, game, a, b, **kw):
    return {"index": i, "start": start, "end": start + 600, "runs": [[start, start + 600]],
            "gameplaySamples": 10, "seconds": 600,
            "identity": {"map": {"value": mp, "votes": kw.get("map_votes", 10), "of": 10},
                         "game": {"value": game, "votes": 10, "of": 10},
                         "team_a": a, "team_b": b, "complete": True}}


TIMELINE = {"url": "https://www.twitch.tv/videos/2831815423", "layout": "owcs_8c105lnzlam.json",
            "durationSeconds": 41156, "step": 30, "maps": [
                window(1, 3840, "busan", 1, team("VARREL"), team("zeta", known=True), map_votes=9),
                window(2, 4680, "shambali", 2, team("VARREL"), team("zeta", known=True)),
                window(3, 22470, "nepal", 1, team("T1", votes=12), team("twis", known=True)),
                window(4, 24990, "aatlis", 2, team("N1", votes=8), team("twis", known=True)),
                window(5, 25950, "route66", 3, team("T1", votes=14), team("twis", known=True))]}


class TestRecord(unittest.TestCase):
    def setUp(self):
        self.rec = bm.slim(json.loads(json.dumps(TIMELINE)))

    def test_one_row_per_map_with_series(self):
        self.assertEqual(self.rec["videoId"], "2831815423")
        self.assertEqual([m["series"] for m in self.rec["maps"]], [1, 1, 2, 2, 2])
        self.assertEqual(self.rec["maps"][0]["start"], 3840)

    def test_known_teams_are_ids_unknown_keep_the_printed_name(self):
        m = self.rec["maps"][0]
        self.assertEqual(m["team_b"], {"id": "zeta", "name": "ZETA"})
        self.assertEqual(m["team_a"], {"id": None, "name": "VARREL"})

    def test_the_series_corrects_a_short_misread_name(self):
        self.assertEqual({m["team_a"]["name"] for m in self.rec["maps"][2:]}, {"T1"})

    def test_ninety_percent_or_less_is_marked_for_a_check(self):
        self.assertEqual(self.rec["maps"][0]["check"], ["map"])
        self.assertEqual(self.rec["maps"][1]["check"], [])

    def test_the_index_is_a_pure_function_of_the_records(self):
        d = tempfile.mkdtemp()
        try:
            bm.save(self.rec, d)
            a, b = bm.render(bm.build_index(d)), bm.render(bm.build_index(d))
            self.assertEqual(a, b)
            self.assertIn("2831815423", a)
            self.assertNotIn("generatedAt", a)
        finally:
            shutil.rmtree(d)


class TestPicking(unittest.TestCase):
    def rows(self):
        base = {"state": "found", "durationSeconds": 30000}
        return [
            dict(base, videoId="1", url="https://www.twitch.tv/videos/1", publishedAt="2026-08-01",
                 title="OWCS 2026 | Midseason Championship Day 4"),
            dict(base, videoId="2", url="https://www.twitch.tv/videos/2", publishedAt="2026-08-02",
                 title="[REBROADCAST] OWCS 2026 | Midseason Championship Day 5"),
            dict(base, videoId="3", url="https://www.twitch.tv/videos/3", publishedAt="2026-08-03",
                 title="OWWC 2026 | Group Stage Day 1"),
            dict(base, videoId="4", url="https://www.youtube.com/watch?v=abcdefghijk", publishedAt="2026-08-04",
                 title="OWCS 2026 | Midseason Championship Day 6"),
            dict(base, videoId="5", url="https://www.twitch.tv/videos/5", publishedAt="2026-08-05",
                 title="OWCS 2026 | Midseason Championship Day 7"),
        ]

    def test_newest_original_on_twitch_with_a_known_hud(self):
        d = tempfile.mkdtemp()
        try:
            self.assertEqual(bm.next_broadcast(self.rows(), d),
                             ("https://www.twitch.tv/videos/5", "owcs_8c105lnzlam"))
        finally:
            shutil.rmtree(d)

    def test_a_broadcast_is_never_probed_twice(self):
        d = tempfile.mkdtemp()
        try:
            for vid in ("5", "1"):
                open(os.path.join(d, f"{vid}.json"), "w", encoding="utf-8").close()
            self.assertIsNone(bm.next_broadcast(self.rows(), d))
        finally:
            shutil.rmtree(d)


class TestCommitted(unittest.TestCase):
    def test_the_committed_site_file_is_current(self):
        with open(bm.INDEX_PATH, encoding="utf-8") as f:
            self.assertEqual(f.read(), bm.render(bm.build_index()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
