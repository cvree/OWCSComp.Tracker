#!/usr/bin/env python3
"""
test_discord_notify.py — the Discord messages (pipeline/discord_notify.py).

Offline: nothing is sent. Covers what counts as a new series, the pick-rate
and riser/faller arithmetic, the message built from the committed exports,
Discord's size limits, and that a run with no webhook sends nothing.

Run: python3 pipeline/test_discord_notify.py
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import discord_notify as DN   # noqa: E402


def tiers(rows):
    """rows: (date, heroes of side a, heroes of side b, bans of side a)."""
    matches, maps = [], []
    for i, (d, a, b, bn) in enumerate(rows):
        matches.append({"d": d})
        maps.append({"m": i, "a": {"h": [[h, 1.0] for h in a], "bn": bn}, "b": {"h": [[h, 1.0] for h in b], "bn": []}})
    return {"matches": matches, "maps": maps}


class TestResults(unittest.TestCase):
    def test_a_series_is_new_when_the_base_did_not_have_it(self):
        now = {"series": [{"id": "a", "winner": "x"}, {"id": "b", "winner": "y"}, {"id": "c", "winner": None}]}
        self.assertEqual([s["id"] for s in DN.new_series(now, {"series": [{"id": "a"}]})], ["b"])
        self.assertEqual(len(DN.new_series(now, None)), 2, "no base: every decided series")

    def test_the_winner_is_bold(self):
        s = {"teamA": "t1", "teamB": "zeta", "scoreA": 1, "scoreB": 3, "winner": "zeta"}
        self.assertEqual(DN.result_lines([s], {"t1": "T1", "zeta": "ZETA"}), ["T1 1–3 **ZETA**"])


class TestMeta(unittest.TestCase):
    def test_pick_rate_is_the_share_of_team_sides_and_ban_rate_of_maps(self):
        t = tiers([("2026-11-04", ["ana"], ["ana"], ["dva"]), ("2026-11-05", ["ana"], ["kiriko"], []),
                   ("2026-10-20", ["kiriko"], ["kiriko"], [])])
        u = DN.usage(t, "2026-11-03")
        self.assertEqual(u["maps"], 2)
        self.assertAlmostEqual(u["picks"]["ana"], 0.75)
        self.assertAlmostEqual(u["bans"]["dva"], 0.5)
        self.assertEqual(DN.usage(t, "2026-10-06", "2026-11-03")["picks"], {"kiriko": 1.0})

    def test_an_undated_map_is_in_no_window(self):
        t = tiers([(None, ["ana"], ["ana"], [])])
        self.assertEqual(DN.usage(t, "2026-01-01")["maps"], 0)


class TestMessage(unittest.TestCase):
    def test_the_real_exports_build_a_message_discord_accepts(self):
        root = DN.js_object(DN.read(DN.SERIES_JS))
        self.assertTrue(root and root.get("series"), "the committed series export reads")
        real = DN.read_at
        DN.read_at = lambda rev, path: '{"series": []}'      # as if every series were new
        try:
            msg = DN.results_message("HEAD")
        finally:
            DN.read_at = real
        e = msg["embeds"][0]
        self.assertLessEqual(len(e["description"]), 4096)
        self.assertLessEqual(len(e["fields"]), 25)
        self.assertTrue(all(0 < len(f["value"]) <= 1024 for f in e["fields"]))
        self.assertLessEqual(e["description"].count("–"), DN.MAX_RESULTS, "the list is capped")
        self.assertIn("more on the", e["description"])
        self.assertEqual(msg["allowed_mentions"], {"parse": []}, "a team name can never ping anyone")

    def test_nothing_new_posts_nothing(self):
        real = DN.read_at
        DN.read_at = lambda rev, path: DN.read(path)
        try:
            self.assertIsNone(DN.results_message("HEAD"))
        finally:
            DN.read_at = real

    def test_without_a_webhook_nothing_is_sent(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            DN.send(DN.alert_message("t", "b", None), None, False)
        self.assertIn("no webhook configured", out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=1)
