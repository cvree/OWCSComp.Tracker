#!/usr/bin/env python3
"""
test_ext_stats_js.py — the browser's spreadsheet helpers, run under Node.

assets/js/app/extstats.js turns the small spreadsheet files
(external_summary.v1.js / external_series.v1.js) into what the public pages
draw; assets/js/app/metastats.js gained the rules for UNDATED maps. Both are
pure modules that also load under Node, so this suite runs them on a small
synthetic summary/series and on the committed files, and asserts:

  * series are ordered by stage of the season, region, week, day, game —
    the same order as pipeline/external_sheet/summary.py — never by a date;
  * series filters (region incl. "no region", stage, event, team, source,
    text) behave as labelled;
  * hero appearance rate = listed maps ÷ maps with listings, per slice, and
    ban rate uses banned maps ÷ maps with a ban recorded;
  * team records report win share over decided maps only, with disputed and
    undecided maps beside it, never folded in;
  * the series card keeps each player's heroes in the sheet's LIST order and
    never invents a time, date or pick;
  * metastats: recency weighting gives an undated map NO weight (it used to
    get 0.5, an invented age), a day window excludes undated maps even when
    nothing is dated, dateExclusions counts what date features leave out,
    and undated sheet maps follow dated maps in stage/week/day/game order;
  * the committed files load and their totals agree with each other.

Skips (exit 0, saying so) when `node` is not installed.

Run: python3 pipeline/test_ext_stats_js.py
"""
from __future__ import annotations

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
NODE = shutil.which("node")
EXT = os.path.join(ROOT, "assets", "js", "app", "extstats.js")
META = os.path.join(ROOT, "assets", "js", "app", "metastats.js")

DRIVER = r"""
const E = require(process.argv[2]);
const M = require(process.argv[3]);
const fs = require("fs");
const vm = require("vm");
const input = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const load = (file) => { const w = {}; vm.runInNewContext(fs.readFileSync(file, "utf8"), { window: w }); return w; };
const out = {};
if (input.committed) {
  const w1 = load(input.committed.summary), w2 = load(input.committed.series);
  const S = w1.OWCS_EXTERNAL_SUMMARY, R = w2.OWCS_EXTERNAL_SERIES;
  const ordered = R.series.slice().sort(E.seriesCompare).map((s) => s.id);
  out.committed = {
    sameOrder: JSON.stringify(ordered) === JSON.stringify(R.series.map((s) => s.id)),
    series: R.series.length, totalsMatches: S.totals.matches,
    mapsInSeries: R.series.reduce((n, s) => n + s.maps.length, 0), totalsMaps: S.totals.maps,
    coverageTotal: E.coverageMatrix(S).total,
    heroRows: E.heroTable(S, {}).rows.length,
    teamRows: E.teamList(S).length,
  };
} else {
  const S = input.summary, R = input.series;
  out.order = R.series.slice().sort(E.seriesCompare).map((s) => s.id);
  const f = (x) => E.filterSeries(R.series, x).map((s) => s.id);
  out.filters = {
    noRegion: f({ region: "none" }), na: f({ region: "na" }), stage2: f({ stage: "stage-2" }),
    team: f({ team: "x-beta" }), linked: f({ source: "linked" }), review: f({ source: "review" }),
    sheetOnly: f({ source: "sheet-only" }), q: f({ q: "alp", names: (id) => ({ "x-alpha": "Alpha" })[id] || id }),
  };
  out.options = E.seriesOptions(R.series);
  out.groups = E.groupSeries(E.filterSeries(R.series, {})).map((g) => [g.stage, g.region, g.rows.length]);
  out.heroAll = E.heroTable(S, {});
  out.heroNa = E.heroTable(S, { region: "na" });
  out.split = E.heroSplit(S, "kiriko");
  out.missing = E.heroSplit(S, "nobody");
  out.team = E.teamRecord(S.teamProfiles["x-alpha"]);
  out.cov = E.coverageMatrix(S);
  out.card = E.seriesCard(R.series.find((s) => s.id === "xm-2"), input.details, R.records);
  out.tally = E.seriesTally(R.series.find((s) => s.id === "xm-2"));
  // metastats: undated maps
  const entries = M.buildEntries(input.pub, input.ext);
  out.entryOrder = entries.map((e) => e.id);
  const w = M.weigher(entries, 30);
  out.weights = entries.map((e) => w(e));
  out.windowNoneDated = M.windowed(entries.filter((e) => !e.date), 30).length;
  out.windowed = M.windowed(entries, 30).map((e) => e.id);
  out.excl = M.dateExclusions(entries, { from: "2026-01-01" }, { days: 30, halfLife: 30 });
  const hu = M.heroUsage(entries, {}, {}, { halfLife: 30 });
  out.heroUsage = { undated: hu.undatedUnweighted, kiriko: hu.rows.find((r) => r.hero === "kiriko") };
  out.scout = M.scouting(entries, "x-beta", 2);
  out.scoutMixed = M.scouting(entries, "x-alpha", 5);
  out.stageOrder = [E.STAGE_ORDER, M.STAGE_ORDER];
}
process.stdout.write(JSON.stringify(out));
"""


def run(payload: dict) -> dict:
    d = tempfile.mkdtemp(prefix="extstats-js-")
    try:
        drv, inp = os.path.join(d, "driver.js"), os.path.join(d, "in.json")
        with open(drv, "w", encoding="utf-8") as f:
            f.write(DRIVER)
        with open(inp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        res = subprocess.run([NODE, drv, EXT, META, inp], capture_output=True, timeout=120,
                             **proc_text.PIPE_TEXT)
        if res.returncode:
            raise AssertionError(res.stderr)
        return json.loads(res.stdout)
    finally:
        shutil.rmtree(d, True)


def series_row(id_, stage, region, week, day, game, a, b, sa, sb, maps, **kw):
    r = {"id": id_, "stage": stage, "region": region, "year": 2026, "week": week, "day": day, "game": game,
         "teamA": a, "teamB": b, "scoreA": sa, "scoreB": sb, "complete": True, "event": kw.get("event", "OWCS"),
         "stageRaw": kw.get("stageRaw"), "label": f"Week {week} | Day {day} | Game {game}", "format": "Round Robin [Bo5]",
         "winner": a if sa > sb else b, "potm": kw.get("potm"), "flags": [], "maps": maps}
    return r


def synthetic() -> dict:
    series = {"meta": {}, "records": [
        {"externalMapId": "xmap-21", "vodMapId": "m-vod-1", "status": "conflict", "fields": {"winner": {"agree": False}}},
        {"externalMapId": "xmap-31", "vodMapId": None, "status": "needs_review", "candidates": [{"vodMapId": "m-vod-1"}]}],
        "series": [
            series_row("xm-3", "stage-2", "emea", 1, 1, 1, "x-alpha", "twis", 0, 1,
                       [{"id": "xmap-31", "n": 1, "map": "oasis", "w": "b", "rec": "needs_review"}], event="OWCS EMEA"),
            series_row("xm-2", "stage-1", "na", 2, 1, 1, "x-alpha", "x-beta", 2, 1,
                       [{"id": "xmap-23", "n": 3, "map": "busan", "w": "a"},
                        {"id": "xmap-21", "n": 1, "map": "nepal", "w": "a", "rec": "conflict"},
                        {"id": "xmap-22", "n": 2, "map": None, "raw": "Grimsvotn", "w": "draw"}], potm="Zorrow"),
            series_row("xm-1", "stage-1", "na", 1, 3, 2, "x-beta", "x-alpha", 3, 0, []),
            series_row("xm-0", "pre-season", None, 1, 1, 1, "x-beta", "twis", 3, 2, []),
            series_row("xm-4", "stage-1", "emea", 1, 1, 1, "twis", "x-beta", 3, 0, []),
        ]}
    summary = {
        "snapshot": {"sha256": "x"},
        "heroDenominators": {"all": {"maps": 10, "banMaps": 8},
                             "region": {"na": {"maps": 4, "banMaps": 4}, "emea": {"maps": 6, "banMaps": 4}},
                             "stage": {"stage-1": {"maps": 4, "banMaps": 4}, "stage-2": {"maps": 6, "banMaps": 4}}},
        "heroes": {"kiriko": {"listedMaps": 7, "listedSides": 9, "bans": 3, "banMaps": 2,
                              "region": {"na": [3, 2, 1], "emea": [4, 1, 1]},
                              "stage": {"stage-1": [3, 2, 1], "stage-2": [4, 1, 1]}},
                   "ana": {"listedMaps": 2, "listedSides": 2, "bans": 0, "banMaps": 0,
                           "region": {"emea": [2, 0, 0]}, "stage": {"stage-2": [2, 0, 0]}}},
        "teamProfiles": {"x-alpha": {"id": "x-alpha", "name": "Alpha", "mapped": False, "regions": ["na"],
                                     "stages": ["stage-1"], "series": {"played": 3, "won": 1, "lost": 2, "undecided": 0},
                                     "maps": {"played": 8, "won": 3, "lost": 2, "draw": 1, "undecided": 1, "disputed": 1,
                                              "listed": 6, "banned": 6},
                                     "byMap": {"busan": [4, 2, 1, 1], "nepal": [2, 1, 0, 0]},
                                     "byMode": {"Control": [2, 1, 0, 0]},
                                     "heroes": [["kiriko", 3]], "bansBy": [["ana", 2]], "bansAgainst": []}},
        "coverage": [{"stage": "stage-2", "region": "emea", "matches": 1, "maps": 1, "bans": 2, "listings": 10},
                     {"stage": "pre-season", "region": None, "matches": 1, "maps": 0, "bans": 0, "listings": 0},
                     {"stage": "stage-1", "region": "na", "matches": 2, "maps": 3, "bans": 4, "listings": 20}],
    }
    details = {"xmap-21": {"scoreA": 2, "scoreB": 1, "scoreRaw": "Map: 2-1", "mode": "Control", "flags": [],
                           "bans": [{"side": "a", "hero": "ana"}, {"side": "b", "raw": "D.Mon"}],
                           "teams": {"a": {"players": [
                               {"position": 2, "playerRaw": "Two", "role": "Damage",
                                "heroes": [{"hero": "tracer", "n": 2}, {"hero": "sojourn", "n": 1}]},
                               {"position": 1, "playerRaw": "One", "role": "Tank",
                                "heroes": [{"hero": None, "raw": "Doctrine", "n": 1}]}]},
                                     "b": {"players": []}}}}
    # a tiny public + external pair for metastats' undated rules
    pub = {"matches": [{"id": "m-1", "teamA": "twis", "teamB": "x-alpha", "scheduledAt": "2026-07-04T00:00:00Z",
                        "maps": [{"id": "m-1-1", "map": "nepal", "winner": "twis"}]}],
           "tournaments": [], "timeWeighting": {"maps": []}}
    ext = {"matches": [
        {"id": "xm-a", "stageKey": "stage-1", "year": 2026, "week": 1, "day": 1, "game": 1, "gameLabel": "Week 1 | Day 1 | Game 1"},
        {"id": "xm-b", "stageKey": "stage-2", "year": 2026, "week": 1, "day": 1, "game": 1, "gameLabel": "Week 1 | Day 1 | Game 1"},
        {"id": "xm-c", "stageKey": "stage-1", "year": 2026, "week": 3, "day": 2, "game": 1, "gameLabel": "Week 3 | Day 2 | Game 1"}],
        "maps": [
            {"id": "xmap-a1", "matchId": "xm-a", "order": 1, "map": "busan", "teamA": "x-alpha", "teamB": "x-beta",
             "winner": "x-alpha", "date": None, "bans": [], "teams": {"a": {"heroes": ["kiriko"]}, "b": {"heroes": []}}},
            {"id": "xmap-b1", "matchId": "xm-b", "order": 1, "map": "oasis", "teamA": "x-alpha", "teamB": "twis",
             "winner": "twis", "date": None, "bans": [], "teams": {"a": {"heroes": ["kiriko"]}, "b": {"heroes": []}}},
            {"id": "xmap-c2", "matchId": "xm-c", "order": 2, "map": "nepal", "teamA": "x-alpha", "teamB": "x-beta",
             "winner": None, "date": None, "bans": [], "teams": {"a": {"heroes": []}, "b": {"heroes": []}}},
            {"id": "xmap-c1", "matchId": "xm-c", "order": 1, "map": "ilios", "teamA": "x-alpha", "teamB": "x-beta",
             "winner": None, "date": None, "bans": [], "teams": {"a": {"heroes": []}, "b": {"heroes": []}}}],
        "reconciliation": {"records": []}}
    return {"summary": summary, "series": series, "details": details, "pub": pub, "ext": ext}


@unittest.skipUnless(NODE, "node is not installed")
class ExtStats(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.o = run(synthetic())

    def test_sheet_order(self):
        self.assertEqual(self.o["order"], ["xm-0", "xm-1", "xm-2", "xm-4", "xm-3"])
        self.assertEqual(self.o["groups"], [["pre-season", None, 1], ["stage-1", "na", 2],
                                            ["stage-1", "emea", 1], ["stage-2", "emea", 1]])
        self.assertEqual(self.o["stageOrder"][0], self.o["stageOrder"][1])

    def test_filters(self):
        f = self.o["filters"]
        self.assertEqual(f["noRegion"], ["xm-0"])
        self.assertEqual(f["na"], ["xm-1", "xm-2"])
        self.assertEqual(f["stage2"], ["xm-3"])
        self.assertEqual(f["team"], ["xm-0", "xm-1", "xm-2", "xm-4"])
        self.assertEqual(f["linked"], ["xm-2"])
        self.assertEqual(f["review"], ["xm-3"])
        self.assertEqual(f["sheetOnly"], ["xm-0", "xm-1", "xm-4"])
        self.assertEqual(f["q"], ["xm-1", "xm-2", "xm-3"])
        self.assertTrue(self.o["options"]["hasNoRegion"])
        self.assertEqual(self.o["options"]["stages"], ["pre-season", "stage-1", "stage-2"])

    def test_hero_rates(self):
        allrows = {r["hero"]: r for r in self.o["heroAll"]["rows"]}
        self.assertAlmostEqual(allrows["kiriko"]["rate"], 0.7)
        self.assertAlmostEqual(allrows["kiriko"]["banRate"], 2 / 8)
        self.assertEqual(self.o["heroAll"]["rows"][0]["hero"], "kiriko")
        na = {r["hero"]: r for r in self.o["heroNa"]["rows"]}
        self.assertNotIn("ana", na)                         # not listed in NA at all
        self.assertAlmostEqual(na["kiriko"]["rate"], 3 / 4)
        self.assertAlmostEqual(na["kiriko"]["banRate"], 1 / 4)   # banned maps, not ban count
        sp = self.o["split"]
        self.assertEqual([r["key"] for r in sp["region"]], ["na", "emea"])
        self.assertEqual([r["key"] for r in sp["stage"]], ["stage-1", "stage-2"])
        self.assertFalse(self.o["missing"]["known"])
        self.assertEqual(self.o["missing"]["all"]["listedMaps"], 0)

    def test_team_record_keeps_disputed_out(self):
        t = self.o["team"]
        self.assertEqual(t["decided"], 6)                   # 3 won + 2 lost + 1 draw; disputed/undecided apart
        self.assertAlmostEqual(t["winShare"], 0.5)
        self.assertEqual(t["byMap"][0]["key"], "busan")
        self.assertAlmostEqual(t["byMap"][0]["winShare"], 0.5)
        self.assertAlmostEqual(t["heroes"][0]["rate"], 0.5)  # 3 of 6 maps with listings

    def test_coverage(self):
        c = self.o["cov"]
        self.assertEqual(c["stages"], ["pre-season", "stage-1", "stage-2"])
        self.assertEqual(c["regions"], ["na", "emea", "none"])
        self.assertEqual(c["total"]["matches"], 4)
        self.assertEqual(c["byRegion"]["none"]["matches"], 1)

    def test_series_card(self):
        c = self.o["card"]
        self.assertEqual([m["order"] for m in c["maps"]], [1, 2, 3])
        m1 = c["maps"][0]
        self.assertEqual(m1["rec"]["status"], "conflict")
        self.assertEqual(c["linkedVodMaps"], ["m-vod-1"])
        self.assertEqual([p["position"] for p in m1["players"]["a"]], [1, 2])
        self.assertEqual([h["hero"] for h in m1["players"]["a"][1]["heroes"]], ["sojourn", "tracer"])  # list order
        self.assertEqual(m1["players"]["a"][0]["heroes"][0], {"hero": None, "raw": "Doctrine", "n": 1})
        self.assertEqual(m1["bans"]["b"], [{"hero": None, "raw": "D.Mon"}])
        self.assertEqual(c["maps"][1]["mapRaw"], "Grimsvotn")
        self.assertFalse(c["maps"][1]["loaded"])
        self.assertEqual(self.o["tally"], {"a": 2, "b": 0, "draw": 1, "undecided": 0, "maps": 3})
        self.assertFalse(c["scoreAgreesWithMaps"])           # card 2-1 vs maps 2-0 + a draw
        text = json.dumps(c)
        for forbidden in ('"date"', '"pickedBy"', '"seconds"', '"duration"'):
            self.assertNotIn(forbidden, text)


@unittest.skipUnless(NODE, "node is not installed")
class UndatedMaps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.o = run(synthetic())

    def test_order_dated_first_then_sheet_sequence(self):
        # VOD (dated) first; then undated sheet maps newest-first by stage/week/day/game/map number
        self.assertEqual(self.o["entryOrder"], ["m-1-1", "xmap-b1", "xmap-c2", "xmap-c1", "xmap-a1"])

    def test_weighting_never_invents_an_age(self):
        self.assertEqual(self.o["weights"][0], 1)
        self.assertEqual(self.o["weights"][1:], [None, None, None, None])
        self.assertEqual(self.o["heroUsage"]["undated"], 4)
        k = self.o["heroUsage"]["kiriko"]
        self.assertEqual(k["sheetCount"], 2)                # raw count still reported
        self.assertIsNone(k["sheetRate"])                   # no weighted rate from undated maps

    def test_windows_and_ranges_exclude_and_count(self):
        self.assertEqual(self.o["windowNoneDated"], 0)      # used to return everything unfiltered
        self.assertEqual(self.o["windowed"], ["m-1-1"])
        x = self.o["excl"]
        self.assertEqual((x["byRange"], x["byWindow"], x["byWeighting"]), (4, 4, 4))
        self.assertEqual((x["undated"], x["sheet"]), (4, 4))

    def test_scouting_says_how_recent_was_decided(self):
        s = self.o["scout"]
        self.assertEqual(s["ordering"], "sheet")
        self.assertEqual([m["id"] for m in s["maps"]], ["xmap-c2", "xmap-c1"])
        self.assertEqual(s["toLabel"], "Week 3 | Day 2 | Game 1")
        self.assertIsNone(s["to"])
        self.assertEqual(self.o["scoutMixed"]["ordering"], "mixed")


@unittest.skipUnless(NODE, "node is not installed")
class Committed(unittest.TestCase):
    def test_committed_files_load_and_agree(self):
        o = run({"committed": {"summary": os.path.join(ROOT, "assets", "data", "external_summary.v1.js"),
                               "series": os.path.join(ROOT, "assets", "data", "external_series.v1.js")}})["committed"]
        self.assertTrue(o["sameOrder"], "JS and Python order the series differently")
        self.assertEqual(o["series"], o["totalsMatches"])
        self.assertEqual(o["mapsInSeries"], o["totalsMaps"])
        self.assertEqual(o["coverageTotal"]["matches"], o["totalsMatches"])
        self.assertEqual(o["coverageTotal"]["maps"], o["totalsMaps"])
        self.assertGreater(o["heroRows"], 20)
        self.assertGreater(o["teamRows"], 10)


if __name__ == "__main__":
    if not NODE:
        print("node is not installed — skipping the extstats.js suite")
        sys.exit(0)
    unittest.main(verbosity=2)
