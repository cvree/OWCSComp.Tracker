#!/usr/bin/env python3
"""
test_external_summary.py — the two small files derived from the public
spreadsheet export (pipeline/external_sheet/summary.py):

  assets/data/external_summary.v1.js   totals, coverage, hero and team aggregates
  assets/data/external_series.v1.js    one row per series, in the sheet's order

What is checked:

  * hero appearance rate = maps where the hero is listed / maps with any
    listing; ban counts only count resolved heroes; a hero listed by both
    teams on one map is one map, two sides;
  * team records: wins / losses / draws; a winner the VOD reading disputes
    is excluded and counted as disputed; no winner is "undecided";
  * bans are split into "listed under this team" and "under the opponent";
  * a linked map is counted once; ignored maps are already gone;
  * series are ordered by stage of the season, region, week, day, game —
    never by an invented date — and nothing carries a date, pick or time;
  * the files are produced by sync / rebuild and covered by `check`
    (deleting or editing one is drift);
  * the committed files agree with the committed export.

Offline. Run: python3 pipeline/test_external_summary.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from external_sheet import summary as SM  # noqa: E402
from external_sheet import sync as S  # noqa: E402
from external_sheet.store import Paths, read_public  # noqa: E402

CFG = Paths()
CARDS = os.path.join(HERE, "fixtures", "gsheet", "owcs_cards_fixture.xlsx")
JS_STAGE_FILES = [os.path.join(ROOT, "assets", "js", "app", f) for f in ("extstats.js", "metastats.js")]


def side(heroes, listed=None, unresolved=0):
    return {"heroes": heroes, "comp": None, "compKind": None,
            "listed": len(heroes) if listed is None else listed, "unresolved": unresolved}


def mp(i, match, a, b, winner_side, heroes_a, heroes_b, bans=(), mapid="busan", order=1):
    return {"id": f"xmap-{i:010x}", "matchId": match, "order": order, "map": mapid, "mode": "Control",
            "teamA": a, "teamB": b, "winnerSide": winner_side,
            "winner": {"a": a, "b": b}.get(winner_side), "date": None, "pickedBy": None,
            "bans": list(bans), "flags": [], "teams": {"a": side(heroes_a), "b": side(heroes_b)}}


def payload():
    matches = [
        {"id": "xm-2", "region": "emea", "stage": "stage-2", "stageKey": "stage-2", "stageRaw": "EMEA - Stage 2",
         "event": "OWCS EMEA", "year": 2026, "week": 1, "day": 1, "game": 1, "teamA": "x-alpha", "teamB": "twis",
         "seriesScoreA": 1, "seriesScoreB": 2, "seriesComplete": True, "winner": "twis",
         "mapIds": ["xmap-0000000001", "xmap-0000000002", "xmap-0000000003", "xmap-0000000004"]},
        {"id": "xm-1", "region": "na", "stage": "stage-1", "stageKey": "stage-1", "stageRaw": "North America - Stage 1",
         "event": "OWCS North America", "year": 2026, "week": 3, "day": 2, "game": 1, "teamA": "x-alpha",
         "teamB": "x-beta", "seriesScoreA": 0, "seriesScoreB": 0, "seriesComplete": False, "winner": None,
         "mapIds": ["xmap-0000000005"]},
        {"id": "xm-0", "region": "na", "stage": "stage-1", "stageKey": "stage-1", "stageRaw": "North America - Stage 1",
         "event": "OWCS North America", "year": 2026, "week": 1, "day": 2, "game": 3, "teamA": "x-beta",
         "teamB": "twis", "seriesScoreA": 3, "seriesScoreB": 0, "seriesComplete": True, "winner": "x-beta",
         "mapIds": []},
    ]
    maps = [
        mp(1, "xm-2", "x-alpha", "twis", "a", ["kiriko", "tracer"], ["kiriko", "ana"],
           bans=[{"side": "a", "teamId": "x-alpha", "hero": "sigma"}, {"side": "b", "teamId": "twis", "hero": "ana"}]),
        mp(2, "xm-2", "x-alpha", "twis", "b", ["tracer"], ["ana"], order=2,
           bans=[{"side": "a", "teamId": "x-alpha", "raw": "D.Mon"}]),
        mp(3, "xm-2", "x-alpha", "twis", "draw", [], [], order=3, mapid="nepal"),
        mp(4, "xm-2", "x-alpha", "twis", "b", ["kiriko"], [], order=4),           # VOD disputes this winner
        mp(5, "xm-1", "x-alpha", "x-beta", None, ["mercy"], ["mercy"]),
    ]
    recs = [{"externalMapId": "xmap-0000000004", "vodMapId": "m-vod-1", "status": "conflict",
             "candidates": [{"vodMapId": "m-vod-1"}], "fields": {"winner": {"agree": False}}},
            {"externalMapId": "xmap-0000000001", "vodMapId": None, "status": "needs_review",
             "candidates": [{"vodMapId": "m-vod-2"}], "fields": {}}]
    return {
        "meta": {"status": "ok", "fixture": True, "provenanceLabels": {}},
        "source": {"label": "sheet", "viewUrl": "https://example.invalid/sheet",
                   "creditsFound": [{"sheet": "Landing", "cell": "A1", "text": "Editors: x"},
                                    {"sheet": "Landing", "cell": "B1", "text": "Editors: x"}]},
        "snapshot": {"sha256": "ab" * 32, "importedAt": "2026-11-01T00:00:00+00:00"},
        "teams": {"x-alpha": {"id": "x-alpha", "name": "Alpha", "mapped": False},
                  "x-beta": {"id": "x-beta", "name": "Beta", "mapped": False},
                  "twis": {"id": "twis", "name": "Twisted Minds", "mapped": True}},
        "matches": matches, "maps": maps,
        "reconciliation": {"summary": {"conflict": 1, "needs_review": 1}, "records": recs},
        "health": {"unmapped": {"hero": [{"raw": "D.Mon", "count": 1, "refs": ["x"]}]}},
    }


class Summary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = SM.build_summary(payload())

    def test_hero_appearance_and_ban_counts(self):
        h = self.s["heroes"]
        # kiriko: listed on maps 1 (both sides) and 4 -> 2 maps, 3 sides
        self.assertEqual((h["kiriko"]["listedMaps"], h["kiriko"]["listedSides"]), (2, 3))
        # denominator: maps with any listing (map 3 lists nothing)
        self.assertEqual(self.s["heroDenominators"]["all"]["maps"], 4)
        self.assertEqual(h["ana"]["bans"], 1)
        self.assertEqual(h["ana"]["banMaps"], 1)
        self.assertNotIn("D.Mon", h)                         # unresolved ban never counted
        self.assertEqual(self.s["totals"]["bans"], 3)
        self.assertEqual(self.s["totals"]["resolvedBans"], 2)
        self.assertEqual(self.s["heroDenominators"]["all"]["banMaps"], 2)
        self.assertEqual(h["kiriko"]["region"]["emea"], [2, 0, 0])
        self.assertEqual(h["ana"]["region"]["emea"], [2, 1, 1])   # listed on maps 1-2, banned on 1
        self.assertEqual(h["mercy"]["stage"]["stage-1"], [1, 0, 0])

    def test_team_records_exclude_disputed_winners(self):
        a = self.s["teamProfiles"]["x-alpha"]
        self.assertEqual(a["maps"], {"played": 5, "won": 1, "lost": 1, "draw": 1, "undecided": 1,
                                     "disputed": 1, "listed": 4, "banned": 2})
        self.assertEqual(a["series"], {"played": 2, "won": 0, "lost": 1, "undecided": 1})
        self.assertEqual(a["byMap"]["busan"], [4, 1, 1, 0])
        self.assertEqual(a["byMap"]["nepal"], [1, 0, 0, 1])
        self.assertEqual(a["bansBy"], [["sigma", 1]])
        self.assertEqual(a["bansAgainst"], [["ana", 1]])
        self.assertFalse(a["mapped"])
        self.assertEqual(a["name"], "Alpha")
        self.assertTrue(self.s["teamProfiles"]["twis"]["mapped"])
        self.assertEqual(self.s["teamProfiles"]["x-beta"]["series"]["won"], 1)

    def test_totals_count_linked_maps_once(self):
        t = self.s["totals"]
        self.assertEqual((t["maps"], t["linkedMaps"], t["sheetOnlyMaps"]), (5, 1, 4))
        self.assertEqual((t["undatedMaps"], t["mapsWithPick"]), (5, 0))
        self.assertEqual((t["teams"], t["unmappedTeams"]), (3, 2))

    def test_coverage_in_season_order(self):
        cov = self.s["coverage"]
        self.assertEqual([(r["stage"], r["region"]) for r in cov], [("stage-1", "na"), ("stage-2", "emea")])
        self.assertEqual((cov[0]["matches"], cov[0]["maps"]), (2, 1))
        self.assertEqual((cov[1]["matches"], cov[1]["maps"], cov[1]["bans"]), (1, 4, 3))

    def test_credit_text_is_deduplicated_and_kept(self):
        self.assertEqual([c["text"] for c in self.s["source"]["credits"]], ["Editors: x"])

    def test_nothing_timed_or_dated(self):
        text = json.dumps(self.s)
        for forbidden in ('"seconds"', '"duration"', '"date"', '"pickedBy"', '"start"', '"end"'):
            self.assertNotIn(forbidden, text)


class Series(unittest.TestCase):
    def test_sheet_order_not_dates(self):
        s = SM.build_series(payload())
        self.assertEqual([r["id"] for r in s["series"]], ["xm-0", "xm-1", "xm-2"])
        self.assertEqual([m["n"] for m in s["series"][2]["maps"]], [1, 2, 3, 4])
        self.assertEqual(s["series"][2]["maps"][3]["rec"], "conflict")
        self.assertEqual(s["series"][2]["maps"][0]["rec"], "needs_review")
        self.assertEqual(len(s["records"]), 2)
        text = json.dumps(s["series"])
        for forbidden in ('"date"', '"pickedBy"', '"seconds"'):
            self.assertNotIn(forbidden, text)

    def test_stage_order_is_mirrored_in_js(self):
        for path in JS_STAGE_FILES:
            with open(path, encoding="utf-8") as f:
                src = f.read()
            self.assertIn("STAGE_ORDER = " + json.dumps(SM.STAGE_ORDER), src, path)

    def test_render_is_loadable_js(self):
        txt = SM.render_series(SM.build_series(payload()))
        self.assertTrue(txt.rstrip().endswith("};"))
        body = txt.split("window.OWCS_EXTERNAL_SERIES = ", 1)[1].rstrip().rstrip(";")
        self.assertEqual(len(json.loads(body)["series"]), 3)
        txt = SM.render_summary(SM.build_summary(payload()))
        body = txt.split("window.OWCS_EXTERNAL_SUMMARY = ", 1)[1].rstrip().rstrip(";")
        self.assertIn("kiriko", json.loads(body)["heroes"])


class Generated(unittest.TestCase):
    def temp_repo(self) -> Paths:
        d = tempfile.mkdtemp(prefix="gsheet-summary-")
        self.addCleanup(shutil.rmtree, d, True)
        p = Paths(root=d)
        for src, dst in ((CFG.source_cfg, p.source_cfg), (CFG.layout_cfg, p.layout_cfg),
                         (CFG.aliases_cfg, p.aliases_cfg), (CFG.decisions, p.decisions),
                         (CFG.public, p.public)):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
        return p

    def test_sync_writes_them_and_check_covers_them(self):
        p = self.temp_repo()
        with open(CARDS, "rb") as f:
            res = S.sync(p, data=f.read(), at="2026-11-01T00:00:00+00:00")
        self.assertEqual(res["status"], "imported", res)
        exp = read_public(p.export)
        summ = read_public(p.export_summary)
        series = read_public(p.export_series)
        self.assertEqual(summ["totals"]["maps"], len(exp["maps"]))
        self.assertEqual(summ["totals"]["matches"], len(exp["matches"]))
        self.assertEqual(len(series["series"]), len(exp["matches"]))
        self.assertEqual(summ["totals"]["linkedMaps"], 1)
        self.assertEqual(S.check(p), [])
        os.remove(p.export_summary)
        self.assertTrue(any("external_summary.v1.js" in x for x in S.check(p)))
        S.rebuild(p)
        with open(p.export_series, "a", encoding="utf-8") as f:
            f.write("\n")
        self.assertTrue(any("external_series.v1.js" in x for x in S.check(p)))
        S.rebuild(p)
        self.assertEqual(S.check(p), [])

    def test_committed_files_agree_with_committed_export(self):
        exp = read_public(CFG.export)
        summ = read_public(CFG.export_summary)
        series = read_public(CFG.export_series)
        self.assertEqual(summ["totals"]["maps"], len(exp["maps"]))
        self.assertEqual(summ["totals"]["matches"], len(exp["matches"]))
        self.assertEqual(sum(len(r["maps"]) for r in series["series"]), len(exp["maps"]))
        self.assertEqual(summ["snapshot"]["sha256"], (exp.get("snapshot") or {}).get("sha256"))
        # the small files really are small next to the full export
        self.assertLess(os.path.getsize(CFG.export_summary) * 10, os.path.getsize(CFG.export))
        self.assertLess(os.path.getsize(CFG.export_series) * 4, os.path.getsize(CFG.export))


if __name__ == "__main__":
    unittest.main(verbosity=2)
