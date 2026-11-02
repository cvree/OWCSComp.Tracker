#!/usr/bin/env python3
"""
test_comp_book.py — the Comps page's comp book (assets/js/app/compbook.js).

Run in Node over the same synthetic season test_tier_stats.py uses (a
strong Korean team beats a mid one five times with one comp; a weak team
upsets it at Champions Clash; the mid team wins the last map), plus one
mirror map, and over the real committed export:

  * a comp's maps, W–L, teams, maps-by-map and games add up;
  * head-to-head against other comps, and the record that leaves mirrors
    out, are counted from the opponent's own line-up;
  * the opponent's bans and the team's own bans are kept apart;
  * the costliest loss is the upset, by the tiers page's own weighting;
  * every filter narrows what it says (team, hero, region, as-of, minimum);
  * on the real export every line-up is counted once, nothing is NaN,
    and the Comps page loads the module after what it needs.

Skips the Node half (saying so) when `node` is not installed.
Run: python3 pipeline/test_comp_book.py
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from test_tier_stats import synthetic  # noqa: E402

NODE = shutil.which("node")
MODULE = os.path.join(ROOT, "assets", "js", "app", "compbook.js")
EXPORT = os.path.join(ROOT, "assets", "data", "sheet_tiers.v1.js")

A = "kiriko+lucio+sojourn+tracer+winston"
B = "ana+bap+cass+dva+sym"
C = "echo+juno+mauga+mercy+pharah"


def with_mirror() -> dict:
    """The synthetic season plus one mirror: both teams run comp A."""
    T = copy.deepcopy(synthetic())
    comp_a = ["winston", "tracer", "sojourn", "kiriko", "lucio"]
    side = {"h": [[h, 1] for h in comp_a], "c": comp_a, "bn": []}
    T["matches"].append({"id": "m7", "d": "2026-06-10", "r": "korea", "s": "stage-2", "ev": "stage-1",
                         "f": "Round Robin", "a": "strong", "b": "mid"})
    T["maps"].append({"m": 7, "id": "x7", "map": "busan", "mode": "Control", "o": 2, "w": "a",
                      "a": copy.deepcopy(side), "b": copy.deepcopy(side)})
    return T


DRIVER = r"""
const CB = require(process.argv[1]);
let buf = ""; process.stdin.on("data", (d) => buf += d); process.stdin.on("end", () => {
  const { T, M } = JSON.parse(buf);
  const pick = (r) => Object.fromEntries(r.comps.map((c) => [c.key, {
    n: c.n, wins: c.wins, losses: c.losses, und: c.und, mirrors: c.mirrors, off: c.offMirror,
    vs: Object.fromEntries(c.vs.map((v) => [v.id, [v.n, v.w, v.l]])),
    teams: Object.fromEntries(c.teams.map((t) => [t.id, [t.n, t.w, t.l]])),
    maps: Object.fromEntries(c.maps.map((m) => [m.id, [m.n, m.w, m.l]])),
    bansFaced: c.bansFaced, bansOwn: c.bansOwn, first: c.first, last: c.last,
    best: c.best && c.best.mapId, worst: c.worst && c.worst.mapId,
    games: c.games.map((g) => g.mapId), adj: c.adjWin, tier: c.tier }]));
  const out = {};
  const all = CB.build(T, {});
  out.all = pick(all);
  out.totals = { maps: all.maps, teamMaps: all.teamMaps, distinct: all.distinct, covered: all.coveredMaps };
  out.team = Object.keys(pick(CB.build(T, { team: "mid" })));
  out.hero = Object.keys(pick(CB.build(T, { hero: "mauga" })));
  out.korea = pick(CB.build(T, { region: "korea" }))["kiriko+lucio+sojourn+tracer+winston"].n;
  out.before = pick(CB.build(T, { asOf: "2026-05-23" }));
  out.min2 = Object.keys(pick(CB.build(T, { minMaps: 2 })));
  out.mirror = pick(CB.build(M, {}));
  out.sortWin = CB.build(T, { sort: "win" }).comps.map((c) => c.key);
  console.log(JSON.stringify(out));
});
"""

REAL = r"""
const CB = require(process.argv[1]);
let buf = ""; process.stdin.on("data", (d) => buf += d); process.stdin.on("end", () => {
  const { exportPath } = JSON.parse(buf);
  global.window = {};
  require(exportPath);
  const T = window.OWCS_SHEET_TIERS;
  const r = CB.build(T, { minMaps: 1 });
  const rows = r.comps;
  const sum = rows.reduce((s, c) => s + c.n, 0);
  const korea = CB.build(T, { region: "korea", minMaps: 1 });
  const f = CB.facets(T);
  console.log(JSON.stringify({
    maps: r.maps, teamMaps: r.teamMaps, distinct: r.distinct, shown: rows.length, sum: sum, covered: r.coveredMaps,
    five: rows.every((c) => c.heroes.length === 5),
    finite: rows.every((c) => isFinite(c.adjWin) && isFinite(c.share)),
    adds: rows.every((c) => c.wins + c.losses + c.und === c.n &&
      c.maps.reduce((s, m) => s + m.n, 0) === c.n && c.teams.reduce((s, t) => s + t.n, 0) === c.n &&
      c.games.length === c.n && c.offMirror.w + c.offMirror.l <= c.wins + c.losses),
    newestFirst: rows.every((c) => c.games.every((g, i) => i === 0 || g.d <= c.games[i - 1].d)),
    tiered: rows.filter((c) => c.tier).length,
    koreaFewer: korea.teamMaps < r.teamMaps && korea.teamMaps > 0,
    facets: { maps: f.maps.size, heroes: f.heroes.length, teams: f.teams.size, modes: f.modes.length },
  }));
});
"""


def run_node(script: str, payload: dict) -> dict:
    out = subprocess.run([NODE, "-e", script, MODULE], input=json.dumps(payload), capture_output=True,
                         text=True, encoding="utf-8", timeout=120)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


@unittest.skipUnless(NODE, "node is not installed")
class Synthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = run_node(DRIVER, {"T": synthetic(), "M": with_mirror()})
        cls.a = cls.out["all"][A]

    def test_every_line_up_is_counted_once(self):
        t = self.out["totals"]
        self.assertEqual(t["maps"], 7)
        self.assertEqual(t["teamMaps"], 14)
        self.assertEqual(t["covered"], 14)
        self.assertEqual(t["distinct"], 3)

    def test_record_adds_up(self):
        self.assertEqual((self.a["n"], self.a["wins"], self.a["losses"], self.a["und"]), (7, 5, 2, 0))
        self.assertEqual(self.a["teams"], {"strong": [7, 5, 2]})
        self.assertEqual(self.a["maps"], {"busan": [7, 5, 2]})
        b = self.out["all"][B]
        self.assertEqual((b["n"], b["wins"], b["losses"]), (6, 1, 5))

    def test_head_to_head_comes_from_the_opponents_line_up(self):
        self.assertEqual(self.a["vs"], {B: [6, 5, 1], C: [1, 0, 1]})
        self.assertEqual(self.a["mirrors"], 0)
        self.assertEqual((self.a["off"]["w"], self.a["off"]["l"]), (5, 2))

    def test_a_mirror_is_left_out_of_the_record_against_other_comps(self):
        a = self.out["mirror"][A]
        self.assertEqual(a["mirrors"], 1)
        self.assertEqual((a["n"], a["wins"], a["losses"]), (9, 6, 3))
        self.assertEqual((a["off"]["w"], a["off"]["l"]), (5, 2))
        self.assertNotIn(A, a["vs"])

    def test_bans_on_each_side_are_kept_apart(self):
        self.assertEqual(dict(self.a["bansFaced"]), {"winston": 5, "kiriko": 1})
        self.assertEqual(dict(self.a["bansOwn"]), {"ana": 5, "sym": 1})

    def test_the_costliest_loss_is_the_upset(self):
        self.assertEqual(self.a["worst"], "x5")
        self.assertEqual(self.out["all"][C]["best"], "x5")

    def test_games_are_newest_first_and_dated(self):
        self.assertEqual(self.a["games"][0], "x6")
        self.assertEqual(self.a["first"], "2026-03-20")
        self.assertEqual(self.a["last"], "2026-06-10")

    def test_filters_narrow(self):
        self.assertEqual(self.out["team"], [B])
        self.assertEqual(self.out["hero"], [C])
        self.assertEqual(self.out["korea"], 6)
        self.assertEqual(self.out["before"][A]["n"], 5)
        self.assertNotIn(C, self.out["before"])
        self.assertNotIn(C, self.out["min2"])

    def test_sort_by_adjusted_win(self):
        adj = [self.out["all"][k]["adj"] for k in self.out["sortWin"]]
        self.assertEqual(adj, sorted(adj, reverse=True))


@unittest.skipUnless(NODE, "node is not installed")
class RealExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.got = run_node(REAL, {"exportPath": EXPORT})

    def test_every_line_up_is_counted_once(self):
        g = self.got
        self.assertGreater(g["maps"], 1000)
        self.assertEqual(g["sum"], g["teamMaps"])
        self.assertEqual(g["covered"], g["teamMaps"])
        self.assertEqual(g["shown"], g["distinct"])

    def test_rows_are_whole(self):
        self.assertTrue(self.got["five"])
        self.assertTrue(self.got["finite"])
        self.assertTrue(self.got["adds"])
        self.assertTrue(self.got["newestFirst"])
        self.assertGreater(self.got["tiered"], 50)

    def test_region_filter_and_facets(self):
        self.assertTrue(self.got["koreaFewer"])
        f = self.got["facets"]
        self.assertGreater(f["maps"], 15)
        self.assertGreater(f["heroes"], 30)
        self.assertGreater(f["teams"], 50)
        self.assertGreaterEqual(f["modes"], 5)


class CompsPageWiring(unittest.TestCase):
    def test_stats_page_loads_the_comp_book_after_what_it_needs(self):
        with open(os.path.join(ROOT, "stats.html"), encoding="utf-8") as f:
            html = f.read()
        order = ["assets/data/sheet_tiers.v1.js", "assets/js/app/core.js", "assets/js/app/tierstats.js",
                 "assets/js/app/tierui.js", "assets/js/app/compbook.js", "assets/js/app/page-compbook.js",
                 "assets/js/app/page-stats.js"]
        at = [html.find('src="' + s + '"') for s in order]
        self.assertTrue(all(i >= 0 for i in at), dict(zip(order, at)))
        self.assertEqual(at, sorted(at))

    def test_the_two_sources_stay_labelled_and_apart(self):
        with open(os.path.join(ROOT, "assets", "js", "app", "page-stats.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn('id="comp-book"', js)
        self.assertIn("Read off the broadcast", js)
        with open(os.path.join(ROOT, "assets", "js", "app", "page-compbook.js"), encoding="utf-8") as f:
            page = f.read()
        self.assertIn("sourceBadge", page)
        self.assertIn("not seconds played", page)


if __name__ == "__main__":
    unittest.main(verbosity=2)
