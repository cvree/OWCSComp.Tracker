#!/usr/bin/env python3
"""
test_lab.py — the tools' maths (assets/js/app/lab.js) and the data they
stand on.

  * a best-of-n chance is the chance of reaching ceil(n/2) map wins first;
  * a hero's "with" and "against" tables count the right side of each map,
    pull small samples toward 50% and keep the raw W–L;
  * the Draft Lab never suggests a banned hero, only finished comps that
    contain every hero picked, only heroes of a role still open, and falls
    back to every map (saying so) when a map is thin;
  * Versus odds are the Elo expectation from team power, mirrored when the
    teams swap, and every past meeting is counted;
  * no hero the community spreadsheet lists often is missing from the
    catalogue. D.Mon and Doctrine once were: 1,518 listings dropped, and
    most recent line-ups counted as incomplete. A new hero now fails this
    loudly instead of quietly hollowing out the recent meta.

Runs the module in Node over the synthetic season of test_tier_stats.py
and over the committed export; skips the Node half when `node` is absent.
Run: python3 pipeline/test_lab.py
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

from test_tier_stats import ROLES, synthetic  # noqa: E402

NODE = shutil.which("node")
MODULE = os.path.join(ROOT, "assets", "js", "app", "lab.js")
EXPORT = os.path.join(ROOT, "assets", "data", "sheet_tiers.v1.js")
SUMMARY = os.path.join(ROOT, "assets", "data", "external_summary.v1.js")
PUBLIC = os.path.join(ROOT, "assets", "data", "public_data.v1.js")

DRIVER = r"""
const L = require(process.argv[1]);
let buf = ""; process.stdin.on("data", (d) => buf += d); process.stdin.on("end", () => {
  const { T, roles } = JSON.parse(buf);
  const out = {};
  out.series = [L.seriesWin(0.5, 3), L.seriesWin(0.6, 3), L.seriesWin(0.6, 5), L.seriesWin(1, 7), L.seriesWin(0, 5)];
  out.shrunk = [L.shrunk(0, 0), L.shrunk(2, 2), L.shrunk(30, 45)];
  const t = L.heroTables(T, "winston", { min: 1 });
  out.winston = { maps: t.maps, base: [t.base.w, t.base.l], with: Object.fromEntries(t.mostWith.map((r) => [r.id, [r.w, r.l]])),
    vs: Object.fromEntries(t.hardestAgainst.map((r) => [r.id, [r.w, r.l, r.win]])) };
  const d = L.draft(T, roles, { map: "busan", ours: ["winston"], bans: ["tracer"] });
  out.draftBan = { comps: d.completions.map((c) => c.heroes), slots: Object.fromEntries(Object.entries(d.slots).map(([k, v]) => [k, v.picks.map((p) => p.id)])),
    open: d.open, where: d.where };
  const d2 = L.draft(T, roles, { map: "busan", ours: ["winston"] });
  out.draft = { comps: d2.completions.map((c) => [c.heroes, c.w, c.l]), open: d2.open,
    slots: Object.fromEntries(Object.entries(d2.slots).map(([k, v]) => [k, v.picks.map((p) => p.id)])), where: d2.where };
  const d3 = L.draft(T, roles, { map: "busan", ours: ["winston", "dva"] });
  out.twoTanks = d3.open;
  const v = L.versus(T, "strong", "mid", {});
  const r = L.versus(T, "mid", "strong", {});
  out.versus = { p: v.pMap, rev: r.pMap, bo5: v.series.bo5, meetings: v.meetings.length, a: [v.a.maps.w, v.a.maps.l], mapRows: v.maps.length };
  console.log(JSON.stringify(out));
});
"""

REAL = r"""
const L = require(process.argv[1]);
let buf = ""; process.stdin.on("data", (d) => buf += d); process.stdin.on("end", () => {
  const { exportPath, roles } = JSON.parse(buf);
  global.window = {};
  require(exportPath);
  const T = window.OWCS_SHEET_TIERS;
  const max = T.dates.max;
  const from = new Date(Date.parse(max + "T00:00:00Z") - 13 * 86400000).toISOString().slice(0, 10);
  const d = L.draft(T, roles, { ours: [], bans: [], from });
  const top = d.completions[0];
  const ht = L.heroTables(T, top.heroes[0], { from });
  const teams = Object.keys(T.teams);
  const v = L.versus(T, teams[0], teams[1], {});
  console.log(JSON.stringify({ comps: d.completions.length, pool: d.pool, tops: top.heroes, slotRoles: d.open,
    htMaps: ht.maps, finite: [ht.bestWith, ht.hardestAgainst].every((rows) => rows.every((r) => isFinite(r.win))),
    vOk: v.pMap == null || (v.pMap > 0 && v.pMap < 1) }));
});
"""


def load_js_object(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        src = f.read()
    return json.loads(src[src.index("{"):src.rindex("}") + 1])


def run(script: str, payload: dict) -> dict:
    out = subprocess.run([NODE, "-e", script, MODULE], input=json.dumps(payload), capture_output=True,
                         text=True, encoding="utf-8", timeout=120)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


@unittest.skipUnless(NODE, "node is not installed")
class Synthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.o = run(DRIVER, {"T": synthetic(), "roles": ROLES})

    def test_series_odds(self):
        s = self.o["series"]
        self.assertAlmostEqual(s[0], 0.5, places=9)
        self.assertAlmostEqual(s[1], 0.648, places=9)          # 0.6^2 + 2*0.6^2*0.4
        self.assertAlmostEqual(s[2], 0.68256, places=9)
        self.assertEqual((s[3], s[4]), (1, 0))

    def test_shrinkage(self):
        sh = self.o["shrunk"]
        self.assertEqual(sh[0], 0.5)
        self.assertAlmostEqual(sh[1], 5 / 8)                    # 2-0 is 62.5%, not 100%
        self.assertGreater(sh[2], 0.6)

    def test_hero_tables_count_the_right_side(self):
        w = self.o["winston"]
        # winston (comp A) plays all seven synthetic maps: 5 wins, 2 losses
        self.assertEqual((w["maps"], w["base"]), (7, [5, 2]))
        self.assertEqual(w["with"]["tracer"], [5, 2])
        self.assertEqual(w["vs"]["dva"][:2], [5, 1])           # vs comp B: won five, lost the last
        self.assertEqual(w["vs"]["mauga"][:2], [0, 1])         # the Champions Clash upset

    def test_draft_respects_picks_bans_and_roles(self):
        d = self.o["draft"]
        self.assertTrue(all("winston" in c[0] for c in d["comps"]))
        self.assertEqual(d["open"], ["Damage", "Support"])
        self.assertNotIn("Tank", d["slots"])
        self.assertTrue(all(ROLES[h] == "Damage" for h in d["slots"]["Damage"]))
        b = self.o["draftBan"]
        self.assertEqual(b["comps"], [])                        # every winston comp ran tracer
        self.assertNotIn("tracer", b["slots"].get("Damage", []))
        self.assertEqual(self.o["twoTanks"], ["Damage", "Support"])

    def test_versus(self):
        v = self.o["versus"]
        self.assertGreater(v["p"], 0.5)
        self.assertAlmostEqual(v["p"] + v["rev"], 1, places=9)
        self.assertGreater(v["bo5"], v["p"])
        self.assertEqual(v["meetings"], 6)
        self.assertEqual(v["a"], [5, 2])                       # every map in range: 5 wins, the upset and the last map lost


@unittest.skipUnless(NODE, "node is not installed")
class RealExport(unittest.TestCase):
    def test_the_tools_answer_on_the_last_14_days(self):
        roles = {h["id"]: h["role"] for h in load_js_object(PUBLIC).get("heroes") or []}
        g = run(REAL, {"exportPath": EXPORT, "roles": roles})
        self.assertGreater(g["pool"], 100)
        self.assertGreaterEqual(g["comps"], 5)
        self.assertEqual(len(g["tops"]), 5)
        self.assertGreater(g["htMaps"], 10)
        self.assertTrue(g["finite"])
        self.assertTrue(g["vOk"])


class HeroCatalogue(unittest.TestCase):
    def test_no_often_listed_hero_is_missing(self):
        summary = load_js_object(SUMMARY)
        missing = [u for u in (summary.get("unmapped") or {}).get("hero") or []
                   if u.get("count", 0) >= 50 and not str(u.get("raw", "")).startswith("picture:")]
        self.assertEqual(missing, [], "add these heroes to pipeline/sample_data.json and the DB "
                                      "(docs/EXTERNAL-SHEET.md, 'A hero the catalog does not have yet')")

    def test_the_new_heroes_have_their_roles(self):
        roles = {h["id"]: h["role"] for h in load_js_object(PUBLIC).get("heroes") or []}
        self.assertEqual(roles.get("dmon"), "Tank")
        self.assertEqual(roles.get("doctrine"), "Support")


if __name__ == "__main__":
    unittest.main(verbosity=2)
