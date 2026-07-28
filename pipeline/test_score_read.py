#!/usr/bin/env python3
"""
test_score_read.py — Phase 6 score / winner / series extraction.

Offline: synthetic OCR item lists (the same {text, conf, box} contract
`ocr_hud.make_reader` produces). No frames, no network.

Covered behaviors:
  * a score needs BOTH numerals; half a score is never a score
  * temporal consensus on the FINAL pair
  * monotonicity discards impossible reads with a reason
  * implausibly large numbers in the score zone are rejected
  * a winner is derived from the score, never read independently
  * a level score yields no winner
  * a series score is cross-checked against established map winners
  * best-of is only inferred when the evidence forces it
  * map order + VOD timestamps come from segment windows
  * operator fallback is recorded as operator-sourced and attributed
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import score_read as sr  # noqa: E402

FW, FH = 1920, 1080
ZONES = sr.zones_for(None, FW, FH)


def box_in(zone):
    x, y, w, h = zone
    cx, cy = x + w // 2, y + h // 2
    return [cx - 8, cy - 8, 16, 16]


def item(text, zone, conf=0.9):
    return {"text": str(text), "conf": conf, "box": box_in(zone)}


def score_frame(a, b, *, conf=0.9):
    return [item(a, ZONES["score_left"], conf),
            item(b, ZONES["score_right"], conf)]


def frames(pairs, *, step=30.0):
    return [(float(i * step), score_frame(a, b))
            for i, (a, b) in enumerate(pairs)]


class TestReadScorePair(unittest.TestCase):
    def test_both_numerals_present(self):
        self.assertEqual(sr.read_score_pair(score_frame(2, 1), ZONES)[:2], (2, 1))

    def test_a_single_numeral_is_not_a_score(self):
        self.assertIsNone(sr.read_score_pair(
            [item(2, ZONES["score_left"])], ZONES))
        self.assertIsNone(sr.read_score_pair(
            [item(1, ZONES["score_right"])], ZONES))

    def test_zero_zero_is_a_valid_pair(self):
        self.assertEqual(sr.read_score_pair(score_frame(0, 0), ZONES)[:2], (0, 0))

    def test_an_implausibly_large_number_is_rejected(self):
        """A match timer or viewer count in the zone must not become a score."""
        self.assertIsNone(sr.read_score_pair(score_frame(2, 47), ZONES))

    def test_non_numeric_text_is_ignored(self):
        self.assertIsNone(sr.read_score_pair(
            [item("OVERTIME", ZONES["score_left"]),
             item("PUSH", ZONES["score_right"])], ZONES))

    def test_low_confidence_reads_are_ignored(self):
        self.assertIsNone(sr.read_score_pair(score_frame(2, 1, conf=0.1), ZONES))

    def test_text_outside_the_zones_is_ignored(self):
        far = [{"text": "2", "conf": 0.9, "box": [5, 1000, 12, 12]},
               {"text": "1", "conf": 0.9, "box": [1900, 1000, 12, 12]}]
        self.assertIsNone(sr.read_score_pair(far, ZONES))

    def test_the_highest_confidence_numeral_wins_within_a_zone(self):
        items = [item(9, ZONES["score_left"], conf=0.4),
                 item(2, ZONES["score_left"], conf=0.95),
                 item(1, ZONES["score_right"], conf=0.9)]
        self.assertEqual(sr.read_score_pair(items, ZONES)[:2], (2, 1))


class TestIdentifyMapScore(unittest.TestCase):
    def test_consensus_on_the_final_score(self):
        res = sr.identify_map_score(frames([(0, 0), (1, 0), (2, 0), (2, 1),
                                            (2, 1), (2, 1)]), fw=FW, fh=FH)
        self.assertEqual((res["scoreA"], res["scoreB"]), (2, 1))
        self.assertEqual(res["nFrames"], 3)
        self.assertEqual(res["source"], "cv-ocr")
        self.assertTrue(res["progression"])

    def test_a_final_score_seen_once_is_not_enough(self):
        res = sr.identify_map_score(frames([(0, 0), (1, 0), (1, 0), (2, 0)]),
                                    fw=FW, fh=FH)
        self.assertIsNone(res["scoreA"])
        self.assertEqual(res["weakCandidate"], [2, 0])
        self.assertIn("only read in 1 frame", res["reason"])

    def test_a_decreasing_read_is_discarded_with_a_reason(self):
        res = sr.identify_map_score(
            frames([(2, 1), (2, 1), (0, 1), (2, 1)]), fw=FW, fh=FH)
        self.assertEqual((res["scoreA"], res["scoreB"]), (2, 1))
        self.assertEqual(len(res["discarded"]), 1)
        self.assertIn("would decrease", res["discarded"][0]["reason"])

    def test_no_score_read_at_all_is_unknown_with_a_reason(self):
        res = sr.identify_map_score(
            [(0.0, [item("OVERTIME", ZONES["score_left"])])], fw=FW, fh=FH)
        self.assertIsNone(res["scoreA"])
        self.assertIn("no complete score pair", res["reason"])

    def test_empty_input_is_unknown_not_zero(self):
        res = sr.identify_map_score([], fw=FW, fh=FH)
        self.assertIsNone(res["scoreA"])
        self.assertIsNone(res["scoreB"])

    def test_a_score_that_only_ever_reads_zero_zero_is_reported_as_such(self):
        """0-0 is a real reading (a map that never started scoring), and it
        must be distinguishable from UNKNOWN."""
        res = sr.identify_map_score(frames([(0, 0), (0, 0), (0, 0)]),
                                    fw=FW, fh=FH)
        self.assertEqual((res["scoreA"], res["scoreB"]), (0, 0))
        self.assertIsNotNone(res["scoreA"])

    def test_read_time_is_recorded(self):
        res = sr.identify_map_score(frames([(2, 1), (2, 1)]), fw=FW, fh=FH)
        self.assertEqual(res["readAt"], 30.0)

    def test_layout_can_override_the_score_zones(self):
        layout = {"score_zones": {"score_left": [0.0, 0.9, 0.1, 0.1],
                                  "score_right": [0.9, 0.9, 0.1, 0.1]}}
        zones = sr.zones_for(layout, FW, FH)
        items = [item(3, zones["score_left"]), item(2, zones["score_right"])]
        res = sr.identify_map_score([(0.0, items), (30.0, items)],
                                    layout=layout, fw=FW, fh=FH)
        self.assertEqual((res["scoreA"], res["scoreB"]), (3, 2))

    def test_an_invalid_zone_override_is_ignored(self):
        layout = {"score_zones": {"score_left": "nonsense",
                                  "score_right": [2, 2, 2, 2]}}
        zones = sr.zones_for(layout, FW, FH)
        self.assertEqual(zones["score_left"],
                         sr.zones_for(None, FW, FH)["score_left"])


class TestMapWinner(unittest.TestCase):
    def test_winner_is_derived_from_the_score(self):
        r = sr.map_winner(2, 1, "qadsiah", "twis")
        self.assertEqual(r["winner"], "qadsiah")
        self.assertEqual(r["source"], "derived-from-score")
        r = sr.map_winner(1, 3, "qadsiah", "twis")
        self.assertEqual(r["winner"], "twis")

    def test_a_level_score_yields_no_winner(self):
        r = sr.map_winner(2, 2, "qadsiah", "twis")
        self.assertIsNone(r["winner"])
        self.assertIn("level", r["reason"])

    def test_an_unknown_score_yields_no_winner(self):
        self.assertIsNone(sr.map_winner(None, 1, "a", "b")["winner"])
        self.assertIsNone(sr.map_winner(2, None, "a", "b")["winner"])

    def test_unknown_teams_yield_no_winner_even_with_a_score(self):
        r = sr.map_winner(2, 1, "qadsiah", None)
        self.assertIsNone(r["winner"])
        self.assertIn("team identities", r["reason"])


class TestSeriesScore(unittest.TestCase):
    def _card(self, text, conf=0.9):
        return [item(text, ZONES["series_card"], conf)]

    def test_a_dash_separated_card_is_read(self):
        res = sr.identify_series_score(
            [(0.0, self._card("2 - 1")), (60.0, self._card("2 - 1"))],
            fw=FW, fh=FH)
        self.assertEqual((res["scoreA"], res["scoreB"]), (2, 1))

    def test_one_card_read_is_not_enough(self):
        res = sr.identify_series_score([(0.0, self._card("2 - 1"))],
                                       fw=FW, fh=FH)
        self.assertIsNone(res["scoreA"])
        self.assertEqual(res["weakCandidate"], [2, 1])

    def test_no_card_is_unknown(self):
        res = sr.identify_series_score([(0.0, self._card("MAP 3 NEXT"))],
                                       fw=FW, fh=FH)
        self.assertIsNone(res["scoreA"])
        self.assertIn("no series score card", res["reason"])

    def test_a_decreasing_series_read_is_discarded(self):
        res = sr.identify_series_score(
            [(0.0, self._card("2 - 1")), (30.0, self._card("2 - 1")),
             (60.0, self._card("0 - 1"))], fw=FW, fh=FH)
        self.assertEqual((res["scoreA"], res["scoreB"]), (2, 1))
        self.assertEqual(len(res["discarded"]), 1)


class TestCrossCheckSeries(unittest.TestCase):
    MAPS = [{"winner": "a", "teamA": "a", "teamB": "b"},
            {"winner": "b", "teamA": "a", "teamB": "b"},
            {"winner": "a", "teamA": "a", "teamB": "b"}]

    def test_agreement_is_reported(self):
        r = sr.cross_check_series({"scoreA": 2, "scoreB": 1}, self.MAPS)
        self.assertTrue(r["agrees"])

    def test_a_contradiction_is_reported_not_resolved(self):
        r = sr.cross_check_series({"scoreA": 3, "scoreB": 0}, self.MAPS)
        self.assertFalse(r["agrees"])
        self.assertIn("one of them is wrong", r["note"])

    def test_no_ocr_score_is_none_not_a_disagreement(self):
        r = sr.cross_check_series({"scoreA": None, "scoreB": None}, self.MAPS)
        self.assertIsNone(r["agrees"])
        self.assertIn("2-1", r["note"])


class TestSeriesResult(unittest.TestCase):
    def _maps(self, winners):
        return [{"winner": w, "teamA": "a", "teamB": "b"} for w in winners]

    def test_a_completed_bo3_names_the_winner(self):
        r = sr.series_result(self._maps(["a", "b", "a"]),
                             team_a="a", team_b="b")
        self.assertEqual(r["winner"], "a")
        self.assertEqual(r["bestOf"], 3)
        self.assertEqual((r["seriesScoreA"], r["seriesScoreB"]), (2, 1))

    def test_a_sweep_infers_the_smallest_forcing_format(self):
        r = sr.series_result(self._maps(["a", "a"]), team_a="a", team_b="b")
        self.assertEqual(r["bestOf"], 3)
        self.assertEqual(r["winner"], "a")

    def test_an_incomplete_series_claims_no_winner(self):
        r = sr.series_result(self._maps(["a", "b"]), team_a="a", team_b="b")
        self.assertIsNone(r["winner"])
        self.assertIn("no side has the", r["reason"])

    def test_a_map_without_a_winner_blocks_the_series_result(self):
        maps = self._maps(["a", "b"]) + [{"winner": None, "teamA": "a",
                                          "teamB": "b"}]
        r = sr.series_result(maps, team_a="a", team_b="b")
        self.assertIsNone(r["winner"])
        self.assertIn("no winner yet", r["reason"])

    def test_a_bo5_needs_three_wins(self):
        r = sr.series_result(self._maps(["a", "b", "a", "b", "a"]),
                             team_a="a", team_b="b")
        self.assertEqual(r["bestOf"], 5)
        self.assertEqual(r["winner"], "a")

    def test_no_maps_means_no_result(self):
        r = sr.series_result([], team_a="a", team_b="b")
        self.assertIsNone(r["winner"])
        self.assertIsNone(r["bestOf"])

    def test_a_single_map_series_is_a_bo1_win(self):
        r = sr.series_result(self._maps(["a"]), team_a="a", team_b="b")
        self.assertEqual(r["bestOf"], 1)
        self.assertEqual(r["winner"], "a")

    def test_a_shape_fitting_no_known_format_refuses_to_guess(self):
        # 6 wins for one side needs a Bo11 — not a format this project knows.
        r = sr.series_result(self._maps(["a"] * 6), team_a="a", team_b="b")
        self.assertIsNone(r["winner"])
        self.assertIn("refusing to guess", r["reason"])


class TestMapResultOrder(unittest.TestCase):
    def test_order_and_timestamps_come_from_segment_windows(self):
        segments = [
            {"id": 3, "start_time": 3000.0, "end_time": 3600.0,
             "map_name": "busan", "map_mode": "Control",
             "review_status": "approved", "team_a": "a", "team_b": "b"},
            {"id": 1, "start_time": 500.0, "end_time": 1100.0,
             "map_name": "nepal", "map_mode": "Control",
             "review_status": "approved", "team_a": "a", "team_b": "b"},
        ]
        out = sr.map_result_order(segments)
        self.assertEqual([m["mapOrder"] for m in out], [1, 2])
        self.assertEqual(out[0]["map"], "nepal")
        self.assertEqual(out[0]["vodStartSeconds"], 500.0)
        self.assertEqual(out[1]["map"], "busan")

    def test_rejected_and_merged_segments_do_not_consume_an_order(self):
        segments = [
            {"id": 1, "start_time": 100.0, "end_time": 200.0,
             "review_status": "rejected"},
            {"id": 2, "start_time": 300.0, "end_time": 900.0,
             "review_status": "approved", "map_name": "nepal"},
            {"id": 3, "start_time": 950.0, "end_time": 1000.0,
             "review_status": "merged"},
        ]
        out = sr.map_result_order(segments)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["mapOrder"], 1)
        self.assertEqual(out[0]["segmentId"], 2)

    def test_no_segments_yields_no_order(self):
        self.assertEqual(sr.map_result_order([]), [])


class TestOperatorFallback(unittest.TestCase):
    def test_an_operator_value_is_attributed_and_marked_operator_sourced(self):
        r = sr.operator_value("mapScore", [2, 1], operator="alice",
                              reason="score overlay was obscured by a sponsor bug")
        self.assertEqual(r["source"], "operator")
        self.assertEqual(r["operator"], "alice")
        self.assertEqual(r["value"], [2, 1])
        self.assertIn("sponsor bug", r["reason"])

    def test_an_unattributed_operator_value_is_refused(self):
        for name in ("", "   ", None):
            with self.assertRaises(ValueError):
                sr.operator_value("mapScore", [2, 1], operator=name)

    def test_a_default_reason_is_generated(self):
        r = sr.operator_value("mapWinner", "twis", operator="bob")
        self.assertIn("bob", r["reason"])
        self.assertIn("mapWinner", r["reason"])


class TestFormatting(unittest.TestCase):
    def test_unknown_score_formats_with_its_reason(self):
        text = sr.format_map_score(sr.identify_map_score([], fw=FW, fh=FH))
        self.assertIn("UNKNOWN", text)

    def test_a_known_score_formats_with_its_progression(self):
        res = sr.identify_map_score(frames([(1, 0), (2, 1), (2, 1)]),
                                    fw=FW, fh=FH)
        text = sr.format_map_score(res)
        self.assertIn("2-1", text)

    def test_series_formatting_names_the_winner_or_unknown(self):
        text = sr.format_series(sr.series_result(
            [{"winner": "a", "teamA": "a", "teamB": "b"}],
            team_a="a", team_b="b"))
        self.assertIn("winner   : a", text)
        text = sr.format_series(sr.series_result([], team_a="a", team_b="b"))
        self.assertIn("UNKNOWN", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
