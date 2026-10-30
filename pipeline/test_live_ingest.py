#!/usr/bin/env python3
"""
test_live_ingest.py — finding the maps in a broadcast without downloading it.

Offline. Synthetic timelines for the shapes a real broadcast has (desk,
round breaks, replays, an ad break, a highlight package), and the real
Nepal map's committed observations as ground truth for one map.

    python3 pipeline/test_live_ingest.py
"""
from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import live_ingest as li  # noqa: E402

NEPAL = os.path.join(HERE, "..", "reports", "ingest", "qad-twis-nepal", "observations.jsonl")


def timeline(live, *, end=3600, step=30):
    """Samples every `step` s; `live(t)` says whether t is gameplay."""
    return [{"offset": float(t), "gameplay": live(t), "score": 1.0 if live(t) else 0.0,
             "reason": "synthetic", "sceneChange": False,
             "state": "gameplay" if live(t) else "no-hud"}
            for t in range(0, end, step)]


class TestFindMaps(unittest.TestCase):
    def test_round_breaks_and_replays_stay_inside_one_map(self):
        """A Control map's rounds are split by 30-90 s of replays and the
        scoreboard. That is one map, not three."""
        live = lambda t: 600 <= t < 1500 and not (900 <= t < 960 or 1200 <= t < 1260)
        maps = li.find_maps(timeline(live))
        self.assertEqual(len(maps), 1)
        self.assertEqual((maps[0]["start"], maps[0]["end"]), (600.0, 1500.0))
        self.assertEqual(len(maps[0]["runs"]), 3)

    def test_a_desk_segment_between_maps_splits_them(self):
        live = lambda t: 600 <= t < 1500 or 2100 <= t < 2700
        maps = li.find_maps(timeline(live))
        self.assertEqual([(m["start"], m["end"]) for m in maps],
                         [(600.0, 1500.0), (2100.0, 2700.0)])
        self.assertEqual([m["index"] for m in maps], [1, 2])

    def test_a_highlight_package_is_not_a_map(self):
        live = lambda t: 3000 <= t < 3090
        self.assertEqual(li.find_maps(timeline(live)), [])

    def test_nothing_live_means_no_maps(self):
        self.assertEqual(li.find_maps(timeline(lambda t: False)), [])

    def test_the_real_nepal_map_comes_out_as_one_window(self):
        """Ground truth: the verified Nepal map ran 30:05-46:18. Its committed
        5-second observations carry replays, partial HUD and round breaks."""
        with open(NEPAL, encoding="utf-8") as f:
            obs = [json.loads(line) for line in f]
        samples = [{"offset": o["t"], "gameplay": o["state"] == "gameplay",
                    "score": 1.0, "reason": o.get("reason", ""), "sceneChange": False,
                    "state": o["state"]} for o in obs]
        maps = li.find_maps(samples, step=5)
        self.assertEqual(len(maps), 1)
        self.assertLessEqual(abs(maps[0]["start"] - 1805), 30)
        self.assertLessEqual(abs(maps[0]["end"] - 2778), 30)

    def test_merging_is_order_independent(self):
        cands = [{"start_time": 2100.0, "end_time": 2700.0, "signals": {"gameplaySamples": 20}},
                 {"start_time": 600.0, "end_time": 900.0, "signals": {"gameplaySamples": 10}},
                 {"start_time": 960.0, "end_time": 1500.0, "signals": {"gameplaySamples": 18}}]
        maps = li.merge_into_maps(cands)
        self.assertEqual([(m["start"], m["end"]) for m in maps],
                         [(600.0, 1500.0), (2100.0, 2700.0)])
        self.assertEqual(maps[0]["gameplaySamples"], 28)


if __name__ == "__main__":
    unittest.main(verbosity=2)
