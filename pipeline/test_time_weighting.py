#!/usr/bin/env python3
"""
test_time_weighting.py — hero player-seconds from VOD-verified timelines.

Every rule pipeline/time_weighting.py states is pinned here with small,
hand-countable timelines:

  * only seconds inside detected round windows count (setup and the gaps
    between rounds are not time on a hero);
  * a live second with no stint is UNKNOWN — reported, never redistributed —
    and every share divides by KNOWN seconds;
  * inclusive end offsets become half-open intervals, so adjacent stints add
    up exactly;
  * overlapping stints resolve to the most recently started one;
  * line-ups are counted only while every slot is known;
  * no rounds -> the stint span, labelled as such;
  * no published stints -> no time row at all (never a zero);
  * the real Nepal milestone exported in public_data.v1.js agrees with a
    fresh computation and with its own stints.

Run: python3 pipeline/test_time_weighting.py
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import time_weighting as tw  # noqa: E402
from external_sheet.store import Paths, read_public  # noqa: E402

ROLES = {"rein": "Tank", "winston": "Tank", "tracer": "Damage", "genji": "Damage",
         "sojourn": "Damage", "ana": "Support", "kiriko": "Support", "lucio": "Support",
         "juno": "Support"}
ARCH = {"version": 1, "rules": [{"id": "dive", "tanks": ["winston"]},
                                {"id": "brawl", "tanks": ["rein"]}]}


def stint(side, slot, hero, start, end, tier="strong", team=None):
    return {"mapId": "m1", "side": side, "slot": slot, "hero": hero, "start": start,
            "end": end, "auditTier": tier, "teamId": team or ("ta" if side == "a" else "tb")}


def full_team(side, start, end, heroes=("rein", "tracer", "genji", "ana", "kiriko")):
    return [stint(side, i + 1, h, start, end) for i, h in enumerate(heroes)]


MATCH = {"id": "x", "teamA": "ta", "teamB": "tb"}


def run(stints, rounds=None, swaps=None, winner="ta"):
    mp = {"id": "m1", "map": "busan", "mode": "Control", "winner": winner,
          "rounds": rounds if rounds is not None else [{"start": 100, "end": 199}]}
    return tw.map_time(mp, MATCH, stints, swaps or [], ROLES, ARCH)


class Rules(unittest.TestCase):
    def test_only_live_round_seconds_count(self):
        # stints run 0..299 but the only round is 100..199 inclusive = 100 s
        row = run(full_team("a", 0, 299) + full_team("b", 0, 299))
        self.assertEqual(row["liveSeconds"], 100)
        self.assertEqual(row["liveWindowSource"], "rounds")
        a = row["teams"][0]
        self.assertEqual({h["hero"]: h["seconds"] for h in a["heroes"]},
                         {"rein": 100, "tracer": 100, "genji": 100, "ana": 100, "kiriko": 100})
        self.assertEqual(a["coverage"], 1.0)
        self.assertEqual(row["coverage"], 1.0)

    def test_unknown_seconds_are_reported_and_excluded_from_shares(self):
        st = full_team("a", 100, 199)
        st[4] = stint("a", 5, "kiriko", 100, 149)          # slot 5 unread for 50 s
        row = run(st + full_team("b", 100, 199))
        a = row["teams"][0]
        self.assertEqual(a["knownPlayerSeconds"], 450)
        self.assertEqual(a["unknownPlayerSeconds"], 50)
        self.assertEqual(a["coverage"], 0.9)
        kiri = next(h for h in a["heroes"] if h["hero"] == "kiriko")
        self.assertEqual(kiri["seconds"], 50)
        self.assertAlmostEqual(kiri["shareOfTeamKnownSeconds"], 50 / 450, places=5)
        self.assertAlmostEqual(kiri["shareOfRoleKnownSeconds"], 50 / 150, places=5)
        self.assertEqual(kiri["presenceShare"], 0.5)
        self.assertEqual(a["fullLineupSeconds"], 50)          # only while all 5 known

    def test_adjacent_inclusive_stints_add_up_exactly(self):
        st = full_team("a", 100, 199)
        st[4] = stint("a", 5, "kiriko", 100, 139)
        st.append(stint("a", 5, "lucio", 140, 199))
        a = run(st + full_team("b", 100, 199))["teams"][0]
        secs = {h["hero"]: h["seconds"] for h in a["heroes"]}
        self.assertEqual((secs["kiriko"], secs["lucio"]), (40, 60))
        self.assertEqual(a["unknownPlayerSeconds"], 0)
        self.assertEqual([lu["seconds"] for lu in a["lineups"]], [60, 40])
        self.assertEqual(sum(lu["share"] for lu in a["lineups"]), 1.0)

    def test_overlap_most_recent_start_wins(self):
        st = full_team("a", 100, 199)
        st.append(stint("a", 5, "lucio", 150, 199))
        a = run(st)["teams"][0]
        secs = {h["hero"]: h["seconds"] for h in a["heroes"]}
        self.assertEqual((secs["kiriko"], secs["lucio"]), (50, 50))

    def test_multiple_rounds_and_gaps(self):
        rounds = [{"start": 100, "end": 149}, {"start": 200, "end": 249}]
        row = run(full_team("a", 0, 400), rounds=rounds)
        self.assertEqual(row["liveSeconds"], 100)
        self.assertEqual(row["liveWindows"], [[100, 149], [200, 249]])

    def test_no_rounds_falls_back_to_stint_span_and_says_so(self):
        row = run(full_team("a", 10, 59), rounds=[])
        self.assertEqual(row["liveWindowSource"], "stint-span")
        self.assertEqual(row["liveSeconds"], 50)

    def test_no_stints_no_row(self):
        self.assertIsNone(run([]))

    def test_winner_loser_audit_tier_archetypes_and_swaps(self):
        st = full_team("a", 100, 199)
        st[0] = stint("a", 1, "rein", 100, 199, tier="provisional")
        b = full_team("b", 100, 199, heroes=("winston", "tracer", "sojourn", "ana", "lucio"))
        swaps = [{"side": "b", "status": "confirmed"}, {"side": "b", "status": "rejected"}]
        row = run(st + b, swaps=swaps)
        a, bb = row["teams"]
        self.assertEqual((a["result"], bb["result"]), ("win", "loss"))
        rein = next(h for h in a["heroes"] if h["hero"] == "rein")
        self.assertEqual((rein["seconds"], rein["auditedSeconds"]), (100, 0))
        self.assertEqual(a["archetypes"][0]["archetype"], "brawl")
        self.assertEqual(bb["archetypes"][0]["archetype"], "dive")
        self.assertEqual((a["confirmedSwaps"], bb["confirmedSwaps"]), (0, 1))
        agg = {h["hero"]: h for h in tw.hero_time_stats([row])}
        self.assertEqual(agg["tracer"]["winnerSeconds"], 100)
        self.assertEqual(agg["tracer"]["loserSeconds"], 100)
        self.assertEqual(agg["tracer"]["presenceRate"], 1.0)
        self.assertEqual(agg["rein"]["presenceRate"], 0.5)
        self.assertEqual(agg["rein"]["shareOfWinnerKnownSeconds"], 0.2)

    def test_unclassified_archetype_is_never_forced(self):
        self.assertEqual(tw.classify_archetype(["mauga", "ana"], {"mauga": "Tank"}, ARCH),
                         "unclassified")


class Milestone(unittest.TestCase):
    def test_exported_nepal_time_matches_a_fresh_build(self):
        pub = read_public(Paths().public)
        fresh = tw.build(pub)
        self.assertEqual(pub["timeWeighting"], fresh)
        nepal = next(m for m in fresh["maps"] if m["mapId"] == "m-qad-twis-s2po-m1")
        self.assertEqual(nepal["liveWindowSource"], "rounds")
        live = sum(e - s + 1 for s, e in nepal["liveWindows"])
        self.assertEqual(nepal["liveSeconds"], live)
        for t in nepal["teams"]:
            self.assertEqual(t["knownPlayerSeconds"] + t["unknownPlayerSeconds"],
                             t["availablePlayerSeconds"])
            self.assertEqual(sum(h["seconds"] for h in t["heroes"]), t["knownPlayerSeconds"])
            heroes = {s["hero"] for s in pub["heroStints"] if s["teamId"] == t["teamId"]}
            self.assertTrue({h["hero"] for h in t["heroes"]} <= heroes)
        qad = next(t for t in nepal["teams"] if t["teamId"] == "qadsiah")
        self.assertEqual(qad["confirmedSwaps"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
