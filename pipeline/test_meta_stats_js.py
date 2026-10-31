#!/usr/bin/env python3
"""
test_meta_stats_js.py — the browser's meta numbers, checked against the
pipeline's.

assets/js/app/metastats.js computes every filterable number on meta.html,
map.html and the team scouting report. It is a pure module that also loads
under Node, so this suite builds the real published dataset plus a fixture
spreadsheet import (offline), runs the module in Node, and asserts:

  * a spreadsheet map linked to a VOD map is ONE entry, never two;
  * time-weighted shares equal pipeline/time_weighting.py's (no second,
    drifting implementation of the denominators);
  * spreadsheet heroes are counted as appearances, never as seconds;
  * a disputed winner is excluded from win counts and counted as excluded;
  * coverage, team, source and date filters behave as labelled;
  * recency weighting is relative to the newest map, so it is deterministic;
  * the scouting report states its window and per-source coverage.

Skips (exit 0, saying so) when `node` is not installed.

Run: python3 pipeline/test_meta_stats_js.py
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import proc_text  # noqa: E402
from external_sheet import sync as S  # noqa: E402
from external_sheet.store import Paths, read_public  # noqa: E402

NODE = shutil.which("node")
MODULE = os.path.join(ROOT, "assets", "js", "app", "metastats.js")
FIXTURE = os.path.join(HERE, "fixtures", "gsheet", "owcs_sheet_fixture.xlsx")

DRIVER = r"""
const M = require(process.argv[2]);
const fs = require("fs");
const { pub, ext, calls } = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const entries = M.buildEntries(pub, ext);
const roles = {}; (pub.heroes || []).forEach((h) => { roles[h.id] = h.role; });
const out = { entries: entries.map((e) => ({ id: e.id, vodId: e.vodId, extId: e.extId, status: e.status,
  winner: e.winner, winnerConflict: e.winnerConflict, date: e.date, hasVod: !!e.vod, hasSheet: !!e.sheet })) };
for (const [name, fn, args] of calls) {
  const list = args.filter ? M.filterEntries(entries, args.filter) : entries;
  if (fn === "heroUsage") out[name] = M.heroUsage(list, args.filter || {}, roles, args.opt || {});
  else if (fn === "mapRecords") out[name] = M.mapRecords(list, args.filter || {});
  else if (fn === "scouting") out[name] = M.scouting(list, args.team, args.n);
  else if (fn === "sheetComps") out[name] = M.sheetComps(list, {});
  else if (fn === "count") out[name] = list.length;
}
process.stdout.write(JSON.stringify(out));
"""


def run_node(pub: dict, ext: dict, calls: list) -> dict:
    d = tempfile.mkdtemp(prefix="metajs-")
    try:
        data = os.path.join(d, "in.json")
        drv = os.path.join(d, "drv.js")
        with open(data, "w", encoding="utf-8") as f:
            json.dump({"pub": pub, "ext": ext, "calls": calls}, f)
        with open(drv, "w", encoding="utf-8") as f:
            f.write(DRIVER)
        res = subprocess.run([NODE, drv, MODULE, data], capture_output=True, text=True,
                             timeout=60, **proc_text.PIPE_TEXT)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        return json.loads(res.stdout)
    finally:
        shutil.rmtree(d, True)


@unittest.skipUnless(NODE, "node is not installed — metastats.js parity not checked")
class MetaStats(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="metajs-repo-")
        real = Paths()
        p = Paths(root=cls.tmp)
        for src, dst in ((real.source_cfg, p.source_cfg), (real.layout_cfg, p.layout_cfg),
                         (real.aliases_cfg, p.aliases_cfg), (real.decisions, p.decisions),
                         (real.public, p.public)):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
        with open(FIXTURE, "rb") as f:
            S.sync(p, data=f.read(), at="2026-10-31T00:00:00+00:00")
        cls.pub = read_public(p.public)
        cls.ext = read_public(p.export)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def test_linked_pair_is_one_entry(self):
        out = run_node(self.pub, self.ext, [])
        nepal = [e for e in out["entries"] if e["vodId"] == "m-qad-twis-s2po-m1"]
        self.assertEqual(len(nepal), 1)
        self.assertTrue(nepal[0]["hasVod"] and nepal[0]["hasSheet"])
        self.assertEqual(nepal[0]["status"], "partial")
        self.assertEqual(len(out["entries"]), len(self.ext["maps"]))   # 6 sheet-only + 1 linked
        self.assertEqual(sum(1 for e in out["entries"] if e["extId"] == nepal[0]["extId"]), 1)

    def test_time_shares_match_the_pipeline(self):
        out = run_node(self.pub, self.ext, [["u", "heroUsage", {"filter": {"source": "vod"}}]])
        py = {h["hero"]: h for h in self.pub["timeWeighting"]["heroes"]}
        for r in out["u"]["rows"]:
            if not r["vodSeconds"]:
                continue
            self.assertEqual(r["vodSeconds"], py[r["hero"]]["seconds"])
            self.assertAlmostEqual(r["vodShare"], py[r["hero"]]["shareOfAllKnownPlayerSeconds"], places=4)
            self.assertAlmostEqual(r["vodRoleShare"], py[r["hero"]]["shareOfRoleKnownSeconds"], places=4)
            self.assertAlmostEqual(r["vodPresence"], py[r["hero"]]["presenceRate"], places=4)

    def test_sheet_heroes_are_appearances_not_seconds(self):
        out = run_node(self.pub, self.ext, [["u", "heroUsage", {"filter": {"source": "sheet"}}]])
        rows = {r["hero"]: r for r in out["u"]["rows"]}
        self.assertEqual(out["u"]["vodTimelines"], 2)      # the linked map keeps its VOD lane (2 teams)
        self.assertGreater(rows["kiriko"]["sheetCount"], 0)
        sheet_only = run_node(self.pub, self.ext, [["u", "heroUsage", {"filter": {"source": "sheet",
                                                                                    "team": "x-fixture-falcons"}}]])
        for r in sheet_only["u"]["rows"]:
            self.assertEqual(r["vodSeconds"], 0)
            self.assertIsNone(r["vodShare"])

    def test_disputed_winner_is_excluded(self):
        ext = copy.deepcopy(self.ext)
        m = next(x for x in ext["maps"] if x["map"] == "nepal")
        m["winner"] = "qadsiah"
        out = run_node(self.pub, ext, [["r", "mapRecords", {"filter": {}}]])
        nepal = next(e for e in out["entries"] if e["vodId"] == "m-qad-twis-s2po-m1")
        self.assertTrue(nepal["winnerConflict"])
        self.assertIsNone(nepal["winner"])
        self.assertEqual(out["r"]["excludedConflicts"], 1)
        twis = next(t for t in out["r"]["teams"] if t["team"] == "twis")
        nep = next(x for x in twis["maps"] if x["map"] == "nepal")
        self.assertEqual((nep["wins"], nep["losses"], nep["conflicts"]), (0, 0, 1))

    def test_filters(self):
        out = run_node(self.pub, self.ext, [
            ["vod", "count", {"filter": {"source": "vod"}}],
            ["both", "count", {"filter": {"source": "both"}}],
            ["korea", "count", {"filter": {"region": "korea"}}],
            ["late", "count", {"filter": {"from": "2026-07-10"}}],
            ["vs", "count", {"filter": {"team": "qadsiah", "opponent": "twis"}}],
            ["cov", "heroUsage", {"filter": {"source": "all", "minCoverage": 1.0}}],
        ])
        self.assertEqual((out["vod"], out["both"], out["korea"], out["late"], out["vs"]), (1, 1, 2, 2, 3))
        self.assertEqual(out["cov"]["vodTimelines"], 0)      # 99.8% < 100%: excluded from time

    def test_recency_weighting_is_deterministic(self):
        a = run_node(self.pub, self.ext, [["u", "heroUsage", {"filter": {}, "opt": {"halfLife": 7}}]])
        b = run_node(self.pub, self.ext, [["u", "heroUsage", {"filter": {}, "opt": {"halfLife": 7}}]])
        self.assertEqual(a["u"], b["u"])
        flat = run_node(self.pub, self.ext, [["u", "heroUsage", {"filter": {}}]])
        ra = {r["hero"]: r for r in a["u"]["rows"]}
        rf = {r["hero"]: r for r in flat["u"]["rows"]}
        self.assertEqual(ra["kiriko"]["sheetCount"], rf["kiriko"]["sheetCount"])  # raw n unchanged

    def test_scouting_states_window_and_sources(self):
        out = run_node(self.pub, self.ext, [["s", "scouting", {"team": "twis", "n": 2}]])
        s = out["s"]
        self.assertEqual((s["available"], s["window"]), (3, 2))
        self.assertEqual(s["sources"]["sheet"], 2)
        self.assertTrue(s["from"] <= s["to"])

    def test_only_complete_listings_are_comps(self):
        out = run_node(self.pub, self.ext, [["c", "sheetComps", {}]])
        for r in out["c"]:
            self.assertEqual(len(r["heroes"]), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
