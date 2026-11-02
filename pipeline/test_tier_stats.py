#!/usr/bin/env python3
"""
test_tier_stats.py — the Tiers page (tiers.html): its input export and its maths.

Python side (pipeline/external_sheet/tiers.py):
  * a series' estimated date comes from config/external/sheet_schedule.json:
    Week N | Day D -> weeks[N] + (D-1) days; LAN days by `days`; a missing
    week is extrapolated 7 days on and marked 'estimate';
  * a player's listed heroes are weighted by list order and sum to 1;
  * a disputed winner never reaches the export;
  * the committed sheet_tiers.v1.js is exactly what the committed inputs
    produce (same gate as `sheet_sync.py check`).

Browser side (assets/js/app/tierstats.js, run in Node):
  * bracket and event weights read the config as documented;
  * Elo: the stronger record ends higher; an upset win is worth more than
    a favourite's win; a map after the as-of date changes nothing;
  * ratings do not depend on the region filter;
  * small samples are pulled toward 50%; low-sample heroes get no tier;
  * the real dataset computes without error and tiers every role.

Skips the Node half (saying so) when `node` is not installed.
Run: python3 pipeline/test_tier_stats.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from external_sheet import tiers as TI  # noqa: E402

NODE = shutil.which("node")
MODULE = os.path.join(ROOT, "assets", "js", "app", "tierstats.js")
EXPORT = os.path.join(ROOT, "assets", "data", "sheet_tiers.v1.js")

SCHEDULE = {"dayStep": 1, "stages": {
    "korea/stage-1": {"weeks": {"1": "2026-03-20", "2": "2026-03-27"}, "confidence": "window"},
    "international/champions-clash": {"days": {"1": "2026-05-22", "2": "2026-05-23"}, "confidence": "window"},
    "pre-season/pre-season": {"weeks": {"1": "2026-02-13"}, "confidence": "estimate"},
}}


def run_node(script: str, payload: dict) -> dict:
    out = subprocess.run([NODE, "-e", script, MODULE], input=json.dumps(payload), capture_output=True,
                         text=True, encoding="utf-8", timeout=120)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


class ScheduleDates(unittest.TestCase):
    def test_week_and_day(self):
        m = {"region": "korea", "stageKey": "stage-1", "week": 2, "day": 3}
        self.assertEqual(TI.estimate_date(m, SCHEDULE), ("2026-03-29", "window"))

    def test_lan_days(self):
        m = {"region": "international", "stageKey": "champions-clash", "week": None, "day": 2}
        self.assertEqual(TI.estimate_date(m, SCHEDULE), ("2026-05-23", "window"))

    def test_missing_week_is_extrapolated_and_marked(self):
        m = {"region": "korea", "stageKey": "stage-1", "week": 4, "day": 1}
        self.assertEqual(TI.estimate_date(m, SCHEDULE), ("2026-04-10", "estimate"))

    def test_pre_season_has_no_region(self):
        m = {"region": None, "stageKey": "pre-season", "week": 1, "day": 2}
        self.assertEqual(TI.estimate_date(m, SCHEDULE), ("2026-02-14", "estimate"))

    def test_unknown_stage_has_no_date(self):
        m = {"region": "mars", "stageKey": "stage-9", "week": 1, "day": 1}
        self.assertEqual(TI.estimate_date(m, SCHEDULE), (None, None))


class ListOrder(unittest.TestCase):
    W = {"1": [1.0], "2": [0.7, 0.3], "3": [0.6, 0.25, 0.15]}

    def test_order_weights_sum_to_one_per_player(self):
        players = [{"heroes": [{"hero": "kiriko", "n": 1}, {"hero": "juno", "n": 2}]},
                   {"heroes": [{"hero": "tracer", "n": 1}]},
                   {"heroes": [{"hero": "ana", "n": 3}, {"hero": "bap", "n": 1}, {"hero": "lucio", "n": 2}]}]
        got = dict(TI.side_shares(players, self.W))
        self.assertAlmostEqual(sum(got.values()), 3.0, places=6)
        self.assertEqual(got["kiriko"], 0.7)
        self.assertEqual(got["juno"], 0.3)
        self.assertEqual(got["bap"], 0.6)
        self.assertEqual(got["ana"], 0.15)

    def test_unresolved_heroes_are_skipped(self):
        got = TI.side_shares([{"heroes": [{"hero": None, "n": 1}]}], self.W)
        self.assertEqual(got, [])


class Export(unittest.TestCase):
    def test_disputed_winner_is_dropped(self):
        payload = {"matches": [{"id": "m1", "region": "korea", "stageKey": "stage-1", "week": 1, "day": 1,
                                "teamA": "a", "teamB": "b", "format": "Round Robin [Bo5]"}],
                   "maps": [{"id": "x1", "matchId": "m1", "map": "busan", "winnerSide": "a", "order": 1},
                            {"id": "x2", "matchId": "m1", "map": "nepal", "winnerSide": "b", "order": 2}],
                   "reconciliation": {"records": [{"externalMapId": "x1",
                                                   "fields": {"winner": {"agree": False}}}]},
                   "teams": {"a": {"name": "A"}, "b": {"name": "B"}}}
        doc = TI.build_tiers(payload, {}, SCHEDULE, {})
        w = {m["id"]: m["w"] for m in doc["maps"]}
        self.assertIsNone(w["x1"])
        self.assertEqual(w["x2"], "b")
        self.assertEqual(doc["counts"]["disputedWinners"], 1)
        self.assertEqual(doc["matches"][0]["f"], "Round Robin")
        self.assertEqual(doc["teams"]["a"], ["A", "korea"])

    def test_committed_export_is_current(self):
        from external_sheet import sync as S
        from external_sheet.store import Paths
        paths = Paths()
        fresh = S.rebuild(paths, write=False)
        expected = S.export_files(paths, fresh["payload"], fresh["details"])[paths.export_tiers]
        with open(EXPORT, encoding="utf-8") as f:
            self.assertEqual(f.read(), expected,
                             "sheet_tiers.v1.js is stale — run: python3 pipeline/sheet_sync.py rebuild")


WEIGHTS = {"eventWeight": {"stage-1": 1.0, "champions-clash": 1.4}, "bracketWeight": [
    ["grand final", 1.5], ["semifinal", 1.25], ["round robin", 1.0]], "bracketDefault": 1.0,
    "groupPrefixFactor": 0.9, "regionTier": {"korea": 1600, "na": 1500, "default": 1450},
    "elo": {"k": 32, "scale": 400}, "standings": {"blend": 0.25, "points": 300, "priorMaps": 6},
    "shrink": 6, "pick": {"weight": 0.5}, "win": {"weight": 0.5, "span": 0.1}, "minPicks": 1.5,
    "minPickShare": 0, "compMinMaps": 2,
    "tiers": [{"id": "S", "min": 72}, {"id": "A", "min": 58}, {"id": "B", "min": 44}, {"id": "C", "min": 30},
              {"id": "D", "min": -1000}]}


def synthetic() -> dict:
    """Strong (korea) beats mid five times; weak (na) then upsets strong once
    at Champions Clash; one more map after the as-of date we test."""
    matches, maps = [], []
    side = lambda heroes: {"h": [[h, 1] for h in heroes], "c": heroes, "bn": []}  # noqa: E731
    comp_a = ["winston", "tracer", "sojourn", "kiriko", "lucio"]
    comp_b = ["dva", "cass", "sym", "ana", "bap"]
    for i in range(5):
        matches.append({"id": f"m{i}", "d": f"2026-03-{20 + i}", "r": "korea", "s": "stage-1", "ev": "stage-1",
                        "f": "Round Robin", "a": "strong", "b": "mid"})
        maps.append({"m": i, "id": f"x{i}", "map": "busan", "mode": "Control", "o": 1, "w": "a",
                     "a": side(comp_a), "b": side(comp_b)})
    matches.append({"id": "m5", "d": "2026-05-24", "r": "international", "s": "champions-clash",
                    "ev": "champions-clash", "f": "Grand Finals", "a": "weak", "b": "strong"})
    maps.append({"m": 5, "id": "x5", "map": "busan", "mode": "Control", "o": 1, "w": "a",
                 "a": side(["mauga", "pharah", "echo", "mercy", "juno"]), "b": side(comp_a)})
    matches.append({"id": "m6", "d": "2026-06-10", "r": "korea", "s": "stage-2", "ev": "stage-1",
                    "f": "Round Robin", "a": "mid", "b": "strong"})
    maps.append({"m": 6, "id": "x6", "map": "busan", "mode": "Control", "o": 1, "w": "a",
                 "a": side(comp_b), "b": side(comp_a)})
    return {"weights": WEIGHTS, "dates": {"min": "2026-03-20", "max": "2026-06-10"},
            "teams": {"strong": ["Strong", "korea"], "mid": ["Mid", "korea"], "weak": ["Weak", "na"]},
            "matches": matches, "maps": maps}


ROLES = {"winston": "Tank", "dva": "Tank", "mauga": "Tank", "tracer": "Damage", "sojourn": "Damage",
         "cass": "Damage", "sym": "Damage", "pharah": "Damage", "echo": "Damage", "kiriko": "Support",
         "lucio": "Support", "ana": "Support", "bap": "Support", "mercy": "Support", "juno": "Support"}

DRIVER = r"""
const S = require(process.argv[1]);
let buf = ""; process.stdin.on("data", (d) => buf += d); process.stdin.on("end", () => {
  const { T, roles } = JSON.parse(buf);
  const out = {};
  const W = T.weights;
  out.bw = ["Grand Finals", "Group A Upper Semifinals", "Round Robin", "Something else"].map((f) => S.bracketWeight(f, W));
  const atCC = S.compute(T, roles, { asOf: "2026-05-24" });
  const rk = (r) => Object.fromEntries(r.power.map((x) => [x.team, x.power]));
  out.powerCC = rk(atCC);
  out.per = Object.fromEntries(Array.from(atCC.per.entries()).map(([k, v]) => [k, v]));
  const beforeUpset = S.compute(T, roles, { asOf: "2026-05-23" });
  out.powerBefore = rk(beforeUpset);
  out.powerKoreaFilter = rk(S.compute(T, roles, { asOf: "2026-05-24", region: "korea" }));
  const heroes = Object.fromEntries(atCC.heroTiers("busan").map((h) => [h.id, h]));
  out.heroes = Object.fromEntries(Object.entries(heroes).map(([k, h]) => [k, { adj: h.adjWin, raw: h.rawWin,
    value: h.value, tier: h.tier, low: h.lowSample, picks: h.picks }]));
  out.mapsCC = atCC.bucket("*").maps;
  out.win60 = S.compute(T, roles, { asOf: "2026-06-10", days: 10 }).bucket("*").maps;
  console.log(JSON.stringify(out));
});
"""

REAL = r"""
const S = require(process.argv[1]);
let buf = ""; process.stdin.on("data", (d) => buf += d); process.stdin.on("end", () => {
  const { exportPath, roles } = JSON.parse(buf);
  global.window = {};
  require(exportPath);
  const T = window.OWCS_SHEET_TIERS;
  const r = S.compute(T, roles, { days: 60 });
  const tiers = r.heroTiers("*");
  const roleSet = [...new Set(tiers.filter((h) => h.tier).map((h) => h.role))].sort();
  console.log(JSON.stringify({ n: r.bucket("*").maps, roles: roleSet, teams: r.power.length,
    maps: r.maps.length, comps: r.compTiers("*").filter((c) => c.tier).length,
    finite: tiers.every((h) => isFinite(h.adjWin) && isFinite(h.score)) }));
});
"""


@unittest.skipUnless(NODE, "node is not installed")
class Maths(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = run_node(DRIVER, {"T": synthetic(), "roles": ROLES})

    def test_bracket_weights(self):
        self.assertEqual(self.out["bw"][0], 1.5)
        self.assertAlmostEqual(self.out["bw"][1], 1.25 * 0.9)
        self.assertEqual(self.out["bw"][2:], [1.0, 1.0])

    def test_winner_rises(self):
        p = self.out["powerBefore"]
        self.assertGreater(p["strong"], p["mid"])

    def test_upset_is_worth_more(self):
        favourite = self.out["per"]["x0"]["ea"]           # strong vs mid, first meeting: equal seeds
        upset = self.out["per"]["x5"]                       # weak (na) vs strong (korea) at a LAN final
        self.assertAlmostEqual(favourite, 0.5, places=6)
        self.assertLess(upset["ea"], 0.3)
        self.assertAlmostEqual(upset["imp"], 1.4 * 1.5)
        before, after = self.out["powerBefore"], self.out["powerCC"]
        self.assertGreater(after["weak"] - 1500, 30)
        self.assertLess(after["strong"], before["strong"])

    def test_as_of_excludes_later_maps(self):
        self.assertEqual(self.out["mapsCC"], 6)
        self.assertNotIn("x6", self.out["per"])
        self.assertEqual(self.out["win60"], 1)

    def test_ratings_ignore_region_filter(self):
        self.assertEqual(self.out["powerCC"], self.out["powerKoreaFilter"])

    def test_upset_heroes_carry_value_and_shrink(self):
        h = self.out["heroes"]
        self.assertEqual(h["pharah"]["raw"], 1.0)
        self.assertGreater(h["pharah"]["value"], 1.0)        # one map, worth > 1 win vs expected
        self.assertLess(h["pharah"]["adj"], 0.75)            # …but one map is pulled toward 50%
        self.assertGreater(h["pharah"]["adj"], 0.5)
        self.assertLess(h["winston"]["raw"], 1.0)            # lost the final
        self.assertTrue(h["pharah"]["low"])                  # one listing < minPicks 1.5
        self.assertIsNone(h["pharah"]["tier"])
        self.assertIsNotNone(h["winston"]["tier"])

    def test_real_dataset(self):
        roles = {}
        with open(os.path.join(ROOT, "assets", "data", "public_data.v1.js"), encoding="utf-8") as f:
            src = f.read()
        for hero in json.loads(src[src.index("{"):src.rindex("}") + 1]).get("heroes") or []:
            roles[hero["id"]] = hero["role"]
        got = run_node(REAL, {"exportPath": EXPORT, "roles": roles})
        self.assertGreater(got["n"], 100)
        self.assertEqual(got["roles"], ["Damage", "Support", "Tank"])
        self.assertGreater(got["teams"], 20)
        self.assertTrue(got["finite"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
