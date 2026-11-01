#!/usr/bin/env python3
"""
test_external_sheet.py — the community-spreadsheet importer, offline.

The whole suite runs with outbound sockets DISABLED (see NoNetwork): the
importer is driven by the committed synthetic workbook
(pipeline/fixtures/gsheet/owcs_sheet_fixture.xlsx) and by small workbooks
built in memory, and the downloader by an injected opener.

What is covered:

  * parsing     every layout the importer claims (two-row headers, merged
                cells, repeated headers, junk rows, long per-player rows,
                per-ban rows, per-series rows, unrecognised tabs, credits)
  * IDs         stable, content-keyed ids and readable slugs that do not
                move when unrelated rows are inserted
  * aliases     heroes (renamed), maps, modes, regions, teams (history
                windows), player casing, dates (cell / text / ambiguous)
  * duplicates  identical listings merge; conflicting ones are flagged
  * hashes      SHA-256 in the manifest; unchanged bytes write nothing
  * failures    download / not-an-xlsx / layout change keep the last good
                import and write a failure report
  * reconcile   auto-link, review queue, conflicts, manual decisions, and
                the 'two sheet maps cannot both be one VOD map' rule
  * the CI gate rebuild + check, and drift detection
  * honesty     no timing field exists anywhere in the external layer

Run: python3 pipeline/test_external_sheet.py
"""
from __future__ import annotations

import copy
import datetime as dt
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import openpyxl  # noqa: E402

from external_sheet import reconcile as R  # noqa: E402
from external_sheet import sync as S  # noqa: E402
from external_sheet.aliases import AliasBook, norm  # noqa: E402
from external_sheet.export import render  # noqa: E402
from external_sheet.fetch import FetchFailed, download, sha256  # noqa: E402
from external_sheet.layout import Layout  # noqa: E402
from external_sheet.normalize import ImportFailed, import_workbook  # noqa: E402
from external_sheet import store as ST  # noqa: E402
from external_sheet.store import (Paths, read_json, read_normalized, read_public,  # noqa: E402
                                  read_raw)
from external_sheet.workbook import read_xlsx  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "gsheet", "owcs_sheet_fixture.xlsx")
CFG = Paths()


class NoNetwork:
    """Any attempt to open a socket fails the test that made it."""

    def __enter__(self):
        self._orig = socket.socket.connect

        def refuse(*_a, **_k):
            raise AssertionError("the external-sheet tests must never touch the network")
        socket.socket.connect = refuse
        return self

    def __exit__(self, *exc):
        socket.socket.connect = self._orig
        return False


GUARD = NoNetwork()


def setUpModule():
    GUARD.__enter__()


def tearDownModule():
    GUARD.__exit__(None, None, None)


def slurp(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def bundle() -> S.Bundle:
    return S.Bundle.load(CFG)


def fixture_bytes() -> bytes:
    with open(FIXTURE, "rb") as f:
        return f.read()


def parse(data: bytes, b: S.Bundle | None = None, **kw) -> dict:
    b = b or bundle()
    return import_workbook(read_xlsx(data), b.aliases(), b.layout(), **kw)


def xlsx(sheets: dict[str, list[list]]) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


SIMPLE_HEADER = ["Date", "Event", "Team 1", "Team 2", "Map #", "Map", "Winner",
                 "Team 1 Ban", "Team 2 Ban"]


def simple_rows(n: int = 6) -> list[list]:
    rows = [SIMPLE_HEADER]
    maps = ["Busan", "Kings Row", "Colosseo", "Rialto", "Aatlis", "Nepal"]
    for i in range(n):
        rows.append([dt.datetime(2026, 5, 1 + i // 3), "OWCS 2026 NA Stage 1", "Team Alpha",
                     "Team Beta", i % 3 + 1, maps[i % 6], "Team Alpha" if i % 2 else "Team Beta",
                     "Tracer", "Ana"])
    return rows


def temp_repo(test: unittest.TestCase) -> Paths:
    """A throwaway repo root holding copies of the configs and the
    published dataset, so sync writes never touch the real tree."""
    d = tempfile.mkdtemp(prefix="gsheet-test-")
    test.addCleanup(shutil.rmtree, d, True)
    p = Paths(root=d)
    for src, dst in ((CFG.source_cfg, p.source_cfg), (CFG.layout_cfg, p.layout_cfg),
                     (CFG.aliases_cfg, p.aliases_cfg), (CFG.decisions, p.decisions),
                     (CFG.public, p.public)):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    return p


# =================================================================== parse
class ParseFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.n = parse(fixture_bytes())
        cls.inv = cls.n["inventory"]
        cls.tabs = {t["name"]: t for t in cls.inv["tabs"]}

    def map_by_slug(self, part):
        hits = [m for m in self.n["maps"] if part in m["slug"]]
        self.assertEqual(len(hits), 1, part)
        return hits[0]

    def test_every_tab_is_inventoried_with_a_purpose(self):
        self.assertEqual([t["name"] for t in self.inv["tabs"]],
                         ["About", "EMEA 2026", "Korea Stage 2", "Bans", "Series", "All Maps", "Scratch"])
        self.assertEqual(self.tabs["EMEA 2026"]["purpose"], "map_rows")
        self.assertEqual(self.tabs["Korea Stage 2"]["purpose"], "player_rows")
        self.assertEqual(self.tabs["Bans"]["purpose"], "ban_rows")
        self.assertEqual(self.tabs["Series"]["purpose"], "match_rows")
        self.assertEqual(self.tabs["Scratch"]["purpose"], "unrecognised")
        self.assertEqual(self.tabs["About"]["purpose"], "credits")
        self.assertEqual(self.inv["sourceRowCount"], sum(t["rows"] for t in self.inv["tabs"]))

    def test_two_row_header_is_combined(self):
        cols = {c["header"]: c["as"] for c in self.tabs["EMEA 2026"]["mappedColumns"]}
        self.assertEqual(cols["Team 1 Ban"], "ban side a #1")
        self.assertEqual(cols["Team 2 Support 2"], "player_hero side b Support p5")
        self.assertEqual(self.tabs["EMEA 2026"]["headerRow"], 3)

    def test_counts(self):
        c = self.inv["counts"]
        self.assertEqual((c["matches"], c["maps"], c["bans"]), (4, 7, 13))
        self.assertEqual(c["duplicates"], 1)
        self.assertEqual(c["duplicateConflicts"], 1)

    def test_junk_and_repeated_header_rows(self):
        reasons = [r["reason"] for r in self.tabs["EMEA 2026"]["ignoredRows"]]
        self.assertEqual(len(reasons), 1, reasons)        # the 'Total' row
        self.assertNotIn("Date", [u["raw"] for u in self.inv["unmapped"].get("date", [])])
        k = [r["reason"] for r in self.tabs["Korea Stage 2"]["ignoredRows"]]
        self.assertEqual(k, ["row does not name both teams of its map"])

    def test_merged_date_applies_to_every_map_beneath_it(self):
        dates = {m["slug"]: m["date"] for m in self.n["maps"] if "qadsiah" in m["slug"]}
        self.assertEqual(set(dates.values()), {"2026-07-04"})
        self.assertEqual(len(dates), 3)

    def test_one_cell_two_heroes_keeps_list_order_and_no_time(self):
        nepal = self.map_by_slug("m1-nepal")
        apps = [a for a in self.n["appearances"] if a["mapId"] == nepal["id"]
                and a["side"] == "a" and a["position"] == 5]
        self.assertEqual([(a["hero"], a["listOrder"]) for a in apps], [("juno", 1), ("lucio", 2)])
        for a in self.n["appearances"]:
            for forbidden in ("seconds", "start", "end", "duration", "offset"):
                self.assertNotIn(forbidden, a)

    def test_result_pick_vod_and_mode(self):
        nepal = self.map_by_slug("m1-nepal")
        self.assertEqual((nepal["winner"], nepal["winnerSide"]), ("twis", "b"))
        self.assertEqual(nepal["pickedBy"], "qadsiah")
        self.assertEqual(nepal["mode"], "Control")
        self.assertIn("jkSiX___Qwc", nepal["vodUrl"])
        colosseo = self.map_by_slug("m3-colosseo")
        self.assertEqual(colosseo["winner"], "twis")           # written 'TM'
        self.assertEqual(colosseo["pickNote"], "decider")
        self.assertIsNone(colosseo["pickedBy"])

    def test_renamed_hero_and_typo(self):
        rialto = self.map_by_slug("m2-rialto")
        heroes = {a["hero"] for a in self.n["appearances"] if a["mapId"] == rialto["id"]}
        self.assertIn("cass", heroes)                            # McCree
        typo = [a for a in self.n["appearances"] if a["heroRaw"] == "Jnuo"]
        self.assertEqual(len(typo), 1)
        self.assertIsNone(typo[0]["hero"])                      # never guessed
        self.assertIn("Jnuo", [u["raw"] for u in self.inv["unmapped"]["hero"]])
        comp = next(c for c in self.n["compositions"] if c["mapId"] == rialto["id"]
                    and c["side"] == "b" and c["kind"] == "first-listed")
        self.assertFalse(comp["complete"])
        self.assertEqual(comp["unresolved"], ["Jnuo"])

    def test_long_format_player_rows(self):
        busan = self.map_by_slug("m1-busan")
        self.assertIn("created-from-long-format-rows", busan["flags"])
        apps = [a for a in self.n["appearances"] if a["mapId"] == busan["id"]]
        charlie = [a for a in apps if a["playerRaw"] == "Charlie"]
        self.assertEqual([a["hero"] for a in sorted(charlie, key=lambda a: a["listOrder"])],
                         ["genji", "sojourn", "echo"])
        zeta = {a["side"] for a in apps if a["playerRaw"] in ("ZOX", "Golf")}
        self.assertEqual(len(zeta), 1)
        self.assertNotEqual(zeta, {a["side"] for a in charlie})

    def test_player_casing_groups_one_player(self):
        zox = {a["player"] for a in self.n["appearances"] if norm(a["playerRaw"]) == "zox"}
        self.assertEqual(zox, {"zox"})

    def test_ban_rows_attach_to_their_map(self):
        busan = self.map_by_slug("m1-busan")
        bans = sorted(b["hero"] for b in self.n["bans"] if b["mapId"] == busan["id"])
        self.assertEqual(bans, ["juno", "tracer"])

    def test_series_rows_and_within_sheet_disagreement(self):
        m = next(x for x in self.n["matches"] if x["slug"].startswith("2026-07-05-fixture-falcons"))
        self.assertIn("series-score-disagrees-with-map-winners", m["flags"])
        self.assertEqual(m["seriesScoreMethod"], "sheet")
        qt = next(x for x in self.n["matches"] if "qadsiah" in x["slug"])
        self.assertEqual((qt["seriesScoreA"], qt["seriesScoreB"]), (1, 2))
        self.assertNotIn("series-score-disagrees-with-map-winners", qt["flags"])
        lions = next(x for x in self.n["matches"] if "lions" in x["slug"])
        self.assertIn("series-only", lions["flags"])

    def test_region_and_stage_from_tab_and_event(self):
        k = next(x for x in self.n["matches"] if "raccoon" in x["slug"])
        self.assertEqual(k["region"], "korea")
        self.assertEqual(k["stage"], "stage-2")
        e = next(x for x in self.n["matches"] if "qadsiah" in x["slug"])
        self.assertEqual(e["region"], "emea")

    def test_duplicates(self):
        kr = self.map_by_slug("m1-kingsrow")
        self.assertEqual(kr["duplicateConflicts"][0]["fields"], ["winner"])
        aat = self.map_by_slug("m2-aatlis")
        self.assertEqual(aat["alsoListedAt"][0]["sheet"], "All Maps")
        self.assertTrue(any(i["code"] == "DUPLICATE_CONFLICT" for i in self.inv["issues"]))

    def test_original_text_beside_every_normalised_value(self):
        for m in self.n["maps"]:
            self.assertIn("mapRaw", m)
            self.assertIn("src", m)
            self.assertTrue(m["src"]["sheet"])
        provs = {(p["record"], p["field"]) for p in self.n["fieldProvenance"]}
        nepal = self.map_by_slug("m1-nepal")
        for f in ("map", "winner", "pickedBy", "mode", "order"):
            self.assertIn((nepal["id"], f), provs)

    def test_credit_text_is_found(self):
        self.assertTrue(any("made by" in c["text"].lower() for c in self.inv["credits"]))

    def test_winner_score_disagreement_is_flagged_not_resolved(self):
        kr = self.map_by_slug("m1-kingsrow")
        self.assertIn("winner-disagrees-with-map-score", kr["flags"])
        self.assertEqual(kr["winnerMethod"], "side-token")


class Ids(unittest.TestCase):
    def test_ids_are_stable_and_content_keyed(self):
        a = parse(xlsx({"NA": simple_rows()}))
        rows = simple_rows()
        rows.insert(1, ["note row", "", "", "", "", "", "", "", ""])
        rows.insert(3, [])
        b = parse(xlsx({"NA": rows}))
        self.assertEqual([m["id"] for m in a["maps"]], [m["id"] for m in b["maps"]])
        self.assertEqual(a["maps"][0]["slug"], "2026-05-01-x-team-alpha-vs-x-team-beta-m1-busan"
                         .replace("x-team", "team"))

    def test_same_workbook_same_bytes(self):
        a = json.dumps(parse(fixture_bytes()), sort_keys=True)
        b = json.dumps(parse(fixture_bytes()), sort_keys=True)
        self.assertEqual(a, b)


class Aliases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = bundle()
        cls.a = cls.b.aliases()

    def test_heroes(self):
        for raw, want in (("Lúcio", "lucio"), ("LUCIO", "lucio"), ("McCree", "cass"),
                          ("Soldier: 76", "soldier"), ("D.Va", "dva"), ("Wrecking Ball", "ball"),
                          ("Torbjörn", "torb"), ("JQ", "jq")):
            self.assertEqual(self.a.hero(raw).id, want, raw)
        self.assertIsNone(self.a.hero("Jnuo").id)
        self.assertEqual(self.a.hero("-").method, "empty")

    def test_maps_modes_regions(self):
        self.assertEqual(self.a.map("King's Row").id, "kingsrow")
        self.assertEqual(self.a.map("Circuit Royale").id, "circuit")
        self.assertEqual(self.a.map("Watchpoint: Gibraltar").id, "gibraltar")
        self.assertEqual(self.a.mode("KOTH").id, "Control")
        self.assertEqual(self.a.region("North America").id, "na")
        self.assertEqual(self.a.stage("Stage 2 Week 3").id, "stage-2")

    def test_teams_and_history_window(self):
        self.assertEqual(self.a.team("TM").id, "twis")
        self.assertEqual(self.a.team("Twisted Minds").id, "twis")
        unknown = self.a.team("Team Nova")
        self.assertEqual((unknown.id, unknown.resolved), ("x-team-nova", False))
        cfg = copy.deepcopy(self.b.aliases_cfg)
        cfg["teamHistory"] = [{"name": "Old Org", "team": "twis", "from": "2024-01-01", "until": "2025-06-30"}]
        book = AliasBook.build(cfg, self.b.public)
        self.assertEqual(book.team("Old Org", "2025-01-01").id, "twis")
        self.assertFalse(book.team("Old Org", "2026-01-01").resolved)

    def test_approved_alias_wins(self):
        book = AliasBook.build(self.b.aliases_cfg, self.b.public,
                               [{"kind": "hero", "raw": "Jnuo", "to": "juno"}])
        r = book.hero("jnuo")
        self.assertEqual((r.id, r.method), ("juno", "approved"))

    def test_dates(self):
        self.assertEqual(self.a.date({"date": "2026-07-04"}), ("2026-07-04", "cell-date", False))
        self.assertEqual(self.a.date("Jul 12, 2026")[0], "2026-07-12")
        iso, _, amb = self.a.date("04/07/2026")
        self.assertTrue(amb)                       # 4 July or April 7 — flagged
        self.assertEqual(self.a.date("2026-07-04")[0], "2026-07-04")
        self.assertEqual(self.a.date(46207)[0], "2026-07-04")     # spreadsheet serial
        self.assertEqual(self.a.date("Jul 4", 2026)[0], "2026-07-04")
        self.assertIsNone(self.a.date("someday")[0])


# =================================================================== gates
class Gates(unittest.TestCase):
    def test_structure_change_fails_loudly(self):
        prev = {"NA": "map_rows"}
        renamed = xlsx({"North America": simple_rows()})
        with self.assertRaises(ImportFailed) as cm:
            parse(renamed, previous_tabs=prev)
        self.assertIn("TAB_REMOVED", [e["code"] for e in cm.exception.inventory["errors"]])
        ok = parse(renamed, previous_tabs=prev, accept_structure_change=True)
        self.assertGreater(len(ok["maps"]), 0)

    def test_header_change_fails_loudly(self):
        rows = simple_rows()
        rows[0] = ["When", "What", "Side One", "Side Two", "Number", "Arena", "Result", "x", "y"]
        with self.assertRaises(ImportFailed) as cm:
            parse(xlsx({"NA": rows}), previous_tabs={"NA": "map_rows"})
        codes = [e["code"] for e in cm.exception.inventory["errors"]]
        self.assertIn("NO_DATA_TABS", codes)
        self.assertIn("STRUCTURE_CHANGED", codes)

    def test_mostly_unparseable_tab_fails(self):
        rows = [SIMPLE_HEADER] + [[dt.datetime(2026, 5, 1), "e", "Team Alpha", "", 1, "Busan", "", "", ""]] * 8
        rows.append([dt.datetime(2026, 5, 2), "e", "Team Alpha", "Team Beta", 1, "Busan", "", "", ""])
        with self.assertRaises(ImportFailed) as cm:
            parse(xlsx({"NA": rows}))
        self.assertIn("MOSTLY_IGNORED", [e["code"] for e in cm.exception.inventory["errors"]])

    def test_unrecognised_workbook_fails(self):
        with self.assertRaises(ImportFailed):
            parse(xlsx({"Notes": [["hello"], ["world"]]}))


# =================================================================== sync
class Sync(unittest.TestCase):
    def test_import_hash_manifest_and_unchanged(self):
        p = temp_repo(self)
        data = fixture_bytes()
        res = S.sync(p, data=data, source_url="fixture", at="2026-10-31T00:00:00+00:00")
        self.assertEqual(res["status"], "imported")
        m = read_json(p.manifest)
        self.assertEqual(m["lastGood"], sha256(data))
        snap = m["snapshots"][-1]
        for k in ("sha256", "importedAt", "sourceUrl", "sheetNames", "parserVersion", "sourceRowCount"):
            self.assertIn(k, snap)
        self.assertEqual(snap["changes"]["new"], 7)
        raw = read_raw(p)
        self.assertEqual(raw["sha256"], sha256(data))
        self.assertIn("Scratch", [s["name"] for s in raw["workbook"]["sheets"]])  # preserved
        index = read_json(p.raw)
        self.assertEqual(index["format"], "gsheet-raw.v2")
        for entry in index["sheets"]:                       # one gzip per tab
            self.assertTrue(os.path.exists(os.path.join(p.raw_dir, entry["file"])))
        self.assertEqual(raw["workbook"]["sheets"], read_xlsx(data)["sheets"])  # lossless
        self.assertTrue(all("images" in s for s in raw["workbook"]["sheets"]))
        before = {f: os.path.getmtime(f) for f in (p.raw, p.normalized, p.manifest, p.export)}
        again = S.sync(p, data=data, at="2026-11-01T00:00:00+00:00")
        self.assertEqual(again["status"], "unchanged")
        self.assertEqual(before, {f: os.path.getmtime(f) for f in before})
        self.assertEqual(S.check(p), [])
        self.assertTrue(os.path.exists(os.path.join(p.reports, "inventory.md")))
        self.assertTrue(os.path.exists(os.path.join(p.reports, "reconciliation.json")))

    def test_raw_shards_are_deterministic_and_drop_removed_tabs(self):
        p = temp_repo(self)
        S.sync(p, data=fixture_bytes(), at="2026-10-31T00:00:00+00:00")
        idx = read_json(p.raw)
        blobs = {e["file"]: open(os.path.join(p.raw_dir, e["file"]), "rb").read() for e in idx["sheets"]}
        again, shards = ST.raw_shards(read_raw(p))
        self.assertEqual(shards, blobs)                     # same cells -> same bytes
        S.sync(p, data=xlsx({"NA": simple_rows()}), at="2026-11-01T00:00:00+00:00",
               accept_structure_change=True)
        left = os.listdir(os.path.join(p.raw_dir, "sheets"))
        self.assertEqual(len(left), 1)                      # old tabs' shards removed
        self.assertEqual(S.check(p), [])

    def test_package_profile_explains_size(self):
        n = parse(fixture_bytes())
        pkg = n["inventory"]["package"]
        self.assertEqual(pkg["bytes"], len(fixture_bytes()))
        self.assertEqual(pkg["mediaFiles"], 0)
        self.assertTrue(any(w["path"].startswith("xl/worksheets/") for w in pkg["worksheetXml"]))

    def test_a_file_github_would_reject_fails_the_sync_and_keeps_last_good(self):
        p = temp_repo(self)
        S.sync(p, data=fixture_bytes(), at="2026-10-31T00:00:00+00:00")
        before = read_normalized(p)
        old = ST.MAX_COMMITTED_FILE_BYTES
        ST.MAX_COMMITTED_FILE_BYTES = 200
        try:
            res = S.sync(p, data=xlsx({"NA": simple_rows()}), at="2026-11-01T00:00:00+00:00",
                         accept_structure_change=True)
        finally:
            ST.MAX_COMMITTED_FILE_BYTES = old
        self.assertEqual((res["status"], res["stage"]), ("failed", "store"))
        self.assertEqual(read_normalized(p), before)
        md = slurp(os.path.join(p.reports, "last_failure.md"))
        self.assertIn("Workbook package", md)

    def test_size_cap_message_names_the_setting(self):
        class Resp(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        with self.assertRaises(FetchFailed) as cm:
            download("https://example.invalid/x", max_bytes=10,
                     opener=lambda *a, **k: Resp(fixture_bytes()))
        self.assertIn("maxDownloadBytes", str(cm.exception))

    def test_download_failure_keeps_last_good(self):
        p = temp_repo(self)
        S.sync(p, data=fixture_bytes(), at="2026-10-31T00:00:00+00:00")
        normalized_before = read_normalized(p)

        def broken(*_a, **_k):
            raise OSError("connection refused (simulated)")
        res = S.sync(p, opener=broken, at="2026-11-01T00:00:00+00:00")
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["stage"], "download")
        self.assertEqual(read_normalized(p), normalized_before)
        fail = read_json(os.path.join(p.reports, "last_failure.json"))
        self.assertIn("simulated", fail["reason"])
        exp = read_public(p.export)
        self.assertEqual(exp["meta"]["status"], "failed-kept-last-good")
        self.assertEqual(len(exp["maps"]), 7)          # nothing erased
        self.assertEqual(S.check(p), [])

    def test_layout_change_keeps_last_good(self):
        p = temp_repo(self)
        S.sync(p, data=xlsx({"NA": simple_rows()}), at="2026-10-31T00:00:00+00:00")
        rows = simple_rows()
        rows[0] = ["When", "What", "Side One", "Side Two", "Number", "Arena", "Result", "x", "y"]
        res = S.sync(p, data=xlsx({"NA": rows}), at="2026-11-01T00:00:00+00:00")
        self.assertEqual((res["status"], res["stage"]), ("failed", "validate"))
        self.assertEqual(len(read_normalized(p)["maps"]), 6)
        md = slurp(os.path.join(p.reports, "last_failure.md"))
        self.assertIn("STRUCTURE_CHANGED", md)

    def test_downloader_rejects_html_and_reports_why(self):
        class Resp(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        with self.assertRaises(FetchFailed) as cm:
            download("https://example.invalid/x", opener=lambda *a, **k: Resp(b"<!DOCTYPE html><html>sign in"))
        self.assertIn("shared publicly", str(cm.exception))
        self.assertEqual(download("https://example.invalid/x",
                                  opener=lambda *a, **k: Resp(fixture_bytes())), fixture_bytes())

    def test_changes_between_snapshots(self):
        p = temp_repo(self)
        S.sync(p, data=xlsx({"NA": simple_rows(6)}), at="2026-10-31T00:00:00+00:00")
        rows = simple_rows(6)
        rows[1][6] = "Team Alpha" if rows[1][6] == "Team Beta" else "Team Beta"   # change a winner
        rows.append([dt.datetime(2026, 5, 9), "OWCS 2026 NA Stage 1", "Team Alpha", "Team Gamma", 1,
                     "Busan", "Team Gamma", "", ""])
        res = S.sync(p, data=xlsx({"NA": rows}), at="2026-11-01T00:00:00+00:00")
        self.assertEqual((res["changes"]["new"], res["changes"]["changed"]), (1, 1))

    def test_fixture_flag_and_never_synced_state(self):
        p = temp_repo(self)
        out = S.rebuild(p)
        self.assertEqual(out["payload"]["meta"]["status"], "never-synced")
        self.assertEqual(out["payload"]["maps"], [])
        self.assertFalse(out["payload"]["meta"]["fixture"])

    def test_refresh_after_a_new_vod_export_relinks_without_reparsing(self):
        p = temp_repo(self)
        S.sync(p, data=fixture_bytes(), at="2026-10-31T00:00:00+00:00")
        pub = read_public(p.public)
        pub["matches"][0]["maps"] = []           # the VOD map is withdrawn
        with open(p.public, "w", encoding="utf-8") as f:
            f.write("window.OWCS_PUBLIC = " + json.dumps(pub) + ";\n")
        self.assertTrue(S.check(p))              # export now stale vs. the VOD data
        self.assertEqual(S.refresh_export(p), p.export)
        self.assertEqual(read_public(p.export)["reconciliation"]["summary"]["linked"], 0)
        self.assertEqual(S.check(p), [])
        self.assertIsNone(S.refresh_export(p))   # unchanged -> no write

    def test_check_detects_drift(self):
        p = temp_repo(self)
        S.sync(p, data=fixture_bytes(), at="2026-10-31T00:00:00+00:00")
        cfg = read_json(p.aliases_cfg)
        cfg["heroes"]["juno"].append("jnuo")
        with open(p.aliases_cfg, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        problems = S.check(p)
        self.assertEqual(len(problems), 2)
        S.rebuild(p)
        self.assertEqual(S.check(p), [])
        n = read_normalized(p)
        self.assertNotIn("hero", n["inventory"]["unmapped"])     # typo now mapped


# =================================================================== reconcile
def public_with(maps: list[dict]) -> dict:
    pub = copy.deepcopy(read_public(CFG.public))
    match = pub["matches"][0]
    match["maps"] = maps
    pub["matches"] = [match]
    pub["timeWeighting"] = {"maps": []}
    pub["heroStints"] = [s for s in pub["heroStints"] if s["mapId"] in {m["id"] for m in maps}]
    pub["heroBans"] = []
    return pub


class Reconcile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.n = parse(fixture_bytes())
        cls.pub = read_public(CFG.public)

    def nepal(self, recon):
        return next(r for r in recon["records"] if r["vodMapId"] or r["candidates"])

    def test_real_vod_map_auto_links_and_compares_fields(self):
        recon = R.reconcile(self.n, self.pub, {})
        r = self.nepal(recon)
        self.assertEqual((r["vodMapId"], r["method"]), ("m-qad-twis-s2po-m1", "auto"))
        self.assertEqual(r["status"], "partial")          # bans/pick: sheet only
        self.assertTrue(r["fields"]["winner"]["agree"])
        self.assertTrue(r["fields"]["heroes:qadsiah"]["agree"])
        self.assertIsNone(r["fields"]["bans"]["agree"])
        self.assertEqual(recon["summary"]["linked"], 1)

    def test_sheet_hero_never_seen_on_vod_is_a_conflict(self):
        n = copy.deepcopy(self.n)
        nepal = next(m for m in n["maps"] if m["map"] == "nepal")
        a = next(x for x in n["appearances"] if x["mapId"] == nepal["id"] and x["teamId"] == "twis")
        a["hero"] = "widow"
        r = self.nepal(R.reconcile(n, self.pub, {}))
        self.assertEqual(r["status"], "conflict")
        self.assertEqual(r["fields"]["heroes:twis"]["sheetOnly"], ["widow"])
        self.assertFalse(r["fields"]["heroes:twis"]["agree"])

    def test_disagreeing_map_name_goes_to_review_not_link(self):
        n = copy.deepcopy(self.n)
        nepal = next(m for m in n["maps"] if m["map"] == "nepal")
        nepal["map"] = "lijiang"
        r = next(x for x in R.reconcile(n, self.pub, {})["records"] if x["externalMapId"] == nepal["id"])
        self.assertIsNone(r["vodMapId"])
        self.assertEqual(r["status"], "needs_review")
        self.assertTrue(r["candidates"][0]["mapDisagrees"])

    def test_two_close_candidates_go_to_review(self):
        base = self.pub["matches"][0]["maps"][0]
        twin = dict(base, id="m-qad-twis-s2po-m9", order=None)
        first = dict(base, order=None)
        pub = public_with([first, twin])
        n = copy.deepcopy(self.n)
        for m in n["maps"]:
            m["order"] = None if m["map"] == "nepal" else m["order"]
        r = next(x for x in R.reconcile(n, pub, {})["records"] if x["candidates"])
        self.assertEqual(r["status"], "needs_review")
        self.assertEqual(len(r["candidates"]), 2)

    def test_manual_decisions(self):
        nepal = next(m for m in self.n["maps"] if m["map"] == "nepal")
        rialto = next(m for m in self.n["maps"] if m["map"] == "rialto")
        dec = {"decisions": [
            {"externalMapId": nepal["id"], "action": "reject", "vodMapId": "m-qad-twis-s2po-m1",
             "by": "test", "at": "2026-10-31"},
            {"externalMapId": rialto["id"], "action": "link", "vodMapId": "m-qad-twis-s2po-m1",
             "by": "test", "at": "2026-10-31", "note": "operator says so"}]}
        recs = {r["externalMapId"]: r for r in R.reconcile(self.n, self.pub, dec)["records"]}
        self.assertEqual(recs[nepal["id"]]["status"], "unmatched")
        self.assertEqual(recs[rialto["id"]]["method"], "manual")
        self.assertEqual(recs[rialto["id"]]["status"], "conflict")   # map name differs: both kept
        self.assertEqual(recs[rialto["id"]]["decisions"][0]["note"], "operator says so")
        ign = {"decisions": [{"externalMapId": rialto["id"], "action": "ignore", "by": "t", "at": "x"}]}
        rec = next(r for r in R.reconcile(self.n, self.pub, ign)["records"]
                   if r["externalMapId"] == rialto["id"])
        self.assertTrue(rec["ignored"])

    def test_two_sheet_maps_cannot_both_be_one_vod_map(self):
        n = copy.deepcopy(self.n)
        nepal = next(m for m in n["maps"] if m["map"] == "nepal")
        clone = dict(nepal, id="xmap-clone", order=1)
        n["maps"].append(clone)
        recs = [r for r in R.reconcile(n, self.pub, {})["records"]
                if r["externalMapId"] in (nepal["id"], "xmap-clone")]
        self.assertEqual({r["status"] for r in recs}, {"needs_review"})
        self.assertTrue(all(r["vodMapId"] is None for r in recs))

    def test_status_vocabulary(self):
        recon = R.reconcile(self.n, self.pub, {})
        for r in recon["records"]:
            self.assertIn(r["status"], R.STATUSES)


class PublicExport(unittest.TestCase):
    def test_export_is_labelled_and_untimed(self):
        p = temp_repo(self)
        S.sync(p, data=fixture_bytes(), at="2026-10-31T00:00:00+00:00")
        exp = read_public(p.export)
        self.assertEqual(exp["meta"]["schema"], "external.v1")
        self.assertEqual(set(exp["meta"]["provenanceLabels"].values()),
                         {"VOD verified", "Spreadsheet imported", "Both sources agree",
                          "Conflicting sources"})
        self.assertEqual(exp["source"]["spreadsheetId"], "1OaU5O7lVDZbked5FZAvgfR9MnJIdGhTdnIdu4DsL9PQ")
        text = json.dumps(exp["maps"])
        for forbidden in ('"seconds"', '"start"', '"end"', '"duration"', '"offset"'):
            self.assertNotIn(forbidden, text)
        self.assertTrue(render(exp).startswith("/* ="))

    def test_detail_shards_hold_every_map_and_stay_out_of_the_summary(self):
        p = temp_repo(self)
        S.sync(p, data=fixture_bytes(), at="2026-10-31T00:00:00+00:00")
        exp = read_public(p.export)
        from external_sheet.export import detail_bucket
        for m in exp["maps"]:
            self.assertNotIn("src", m)                       # cell refs live in the detail
            self.assertIn("heroes", m["teams"]["a"])
            shard = os.path.join(p.export_detail_dir, f"detail-{detail_bucket(m['id'])}.js")
            body = slurp(shard)
            self.assertIn(json.dumps(m["id"]), body)
            self.assertIn(detail_bucket(m["id"]), exp["detailShards"])
        nepal = next(m for m in exp["maps"] if m["map"] == "nepal")
        self.assertEqual(nepal["teams"]["a"]["comp"], sorted(["mauga", "shion", "sym", "kiriko", "juno"]))
        # a stale shard is removed, and check() notices a missing one
        stray = os.path.join(p.export_detail_dir, "detail-zz.js")
        with open(stray, "w", encoding="utf-8") as f:
            f.write("x")
        self.assertTrue(S.check(p))
        S.rebuild(p)
        self.assertFalse(os.path.exists(stray))
        self.assertEqual(S.check(p), [])

    def test_committed_export_contacts_no_one(self):
        with open(CFG.export, encoding="utf-8") as f:
            body = f.read()
        self.assertNotIn("fetch(", body)
        self.assertNotIn("XMLHttpRequest", body)
        exp = read_public(CFG.export)
        self.assertFalse(exp["meta"]["fixture"], "the committed export must never be the fixture")


# ============================================================ match cards
CARDS = os.path.join(HERE, "fixtures", "gsheet", "owcs_cards_fixture.xlsx")


def cards_bytes() -> bytes:
    with open(CARDS, "rb") as f:
        return f.read()


class MatchCards(unittest.TestCase):
    """The real sheet's layout: fixed-size match cards, heroes as pictures
    (pipeline/fixtures/gsheet/build_cards_fixture.py)."""

    @classmethod
    def setUpClass(cls):
        cls.raw = read_xlsx(cards_bytes())
        b = bundle()
        cls.n = import_workbook(cls.raw, b.aliases(), b.layout())
        cls.inv = cls.n["inventory"]
        cls.tabs = {t["name"]: t for t in cls.inv["tabs"]}

    def match(self, part):
        hits = [m for m in self.n["matches"] if part in m["slug"]]
        self.assertEqual(len(hits), 1, part)
        return hits[0]

    def map(self, part):
        hits = [m for m in self.n["maps"] if part in m["slug"]]
        self.assertEqual(len(hits), 1, part)
        return hits[0]

    def apps(self, mp, side):
        return sorted((a for a in self.n["appearances"] if a["mapId"] == mp["id"] and a["side"] == side),
                      key=lambda a: (a["position"], a["listOrder"]))

    def test_tab_purposes(self):
        want = {"Landing Page": "credits", "EMEA - Stage 2": "match_cards", "Japan - Stage 1": "match_cards",
                "Stage 2 Hero Bans": "summary", "Map Index": "reference", "Hero Index": "reference",
                "Self-Check": "reference"}
        self.assertEqual({k: t["purpose"] for k, t in self.tabs.items()}, want)
        self.assertEqual(self.tabs["Stage 2 Hero Bans"]["parsed"]["maps"], 0)   # totals never imported

    def test_counts_and_scheduled_card(self):
        c = self.inv["counts"]
        self.assertEqual((c["matches"], c["maps"]), (3, 9))
        self.assertEqual(self.tabs["EMEA - Stage 2"]["cards"],
                         {"blocks": 3, "played": 2, "scheduled": 1, "incomplete": 0, "forfeited": 0,
                          "failed": 0})
        self.assertEqual(self.tabs["Japan - Stage 1"]["cards"],
                         {"blocks": 3, "played": 1, "scheduled": 0, "incomplete": 1, "forfeited": 1,
                          "failed": 0})
        self.assertNotIn("Map Forfeit", [m["mapRaw"] for m in self.n["maps"]])
        self.assertIn("CARD_INCOMPLETE", [i["code"] for i in self.inv["issues"]])
        mc = self.inv["matchCards"]
        self.assertEqual((mc["year"], mc["yearCell"], mc["heroIndexTab"]), (2026, "Landing Page!B1", "Hero Index"))

    def test_nothing_the_sheet_does_not_say(self):
        for m in self.n["matches"]:
            self.assertIsNone(m["date"])
            self.assertEqual(m["dateMethod"], "not-in-source")
        for mp in self.n["maps"]:
            self.assertIsNone(mp["date"])
            self.assertIsNone(mp["pickedBy"])
            self.assertIsNone(mp["pickedByRaw"])
        for a in self.n["appearances"]:
            for forbidden in ("seconds", "start", "end", "duration", "offset"):
                self.assertNotIn(forbidden, a)

    def test_card_match_fields(self):
        m = self.match("qadsiah-vs-twis")
        self.assertEqual((m["teamA"], m["teamARaw"], m["teamB"], m["teamBRaw"]),
                         ("qadsiah", "Al Qadsiah", "twis", "Twisted Minds"))     # '[0-0]' stripped
        self.assertEqual((m["week"], m["day"], m["game"], m["gameLabel"]), (3, 2, 1, "Week 3 | Day 2 | Game 1"))
        self.assertEqual(m["format"], "Playoffs Upper Final [Bo3]")
        self.assertEqual((m["region"], m["stage"], m["stageKey"], m["year"]), ("emea", "stage-2", "stage-2", 2026))
        self.assertEqual((m["seriesScoreA"], m["seriesScoreB"], m["seriesScoreMethod"]), (1, 2, "sheet"))
        self.assertEqual((m["bestOf"], m["seriesComplete"]), (3, True))
        self.assertEqual(m["winner"], "twis")
        self.assertEqual(m["playerOfTheMatchRaw"], "FixT-Sup2")
        self.assertEqual(len(m["mapIds"]), 3)
        self.assertNotIn("series-score-disagrees-with-map-winners", m["flags"])

    def test_heroes_from_pictures_keep_list_order(self):
        nepal = self.map("qadsiah-vs-twis-m1-nepal")
        a = self.apps(nepal, "a")
        self.assertEqual([(x["playerRaw"], x["hero"], x["listOrder"]) for x in a],
                         [("FixQ-Dps1", "shion", 1), ("FixQ-Dps2", "sym", 1), ("FixQ-Tank", "mauga", 1),
                          ("FixQ-Sup1", "kiriko", 1), ("FixQ-Sup2", "juno", 1), ("FixQ-Sup2", "lucio", 2)])
        self.assertTrue(all(x["heroMethod"].startswith("hero-index+") for x in a))
        self.assertTrue(all(x["src"]["picture"] for x in a))
        self.assertEqual([x["position"] for x in self.apps(nepal, "b")], [1, 2, 3, 4, 5])
        colosseo = self.map("qadsiah-vs-twis-m3-colosseo")
        b1 = [x["hero"] for x in self.apps(colosseo, "b") if x["position"] == 1]
        self.assertEqual(b1, ["tracer", "sojourn"])
        comp = next(c for c in self.n["compositions"] if c["mapId"] == nepal["id"]
                    and c["side"] == "a" and c["kind"] == "first-listed")
        self.assertEqual(sorted(comp["heroes"]), sorted(["shion", "sym", "mauga", "kiriko", "juno"]))
        self.assertTrue(comp["complete"])

    def test_unknown_hero_and_unnamed_picture_stay_unmapped(self):
        busan = self.map("fixture-gamma-vs-fixture-delta-m1-busan")
        a = {x["position"]: x for x in self.apps(busan, "a")}
        self.assertEqual((a[1]["hero"], a[1]["heroRaw"]), (None, "Fixturehero"))
        self.assertIsNone(a[2]["hero"])
        self.assertTrue(a[2]["heroRaw"].startswith("picture:"))
        raws = [u["raw"] for u in self.inv["unmapped"]["hero"]]
        self.assertIn("Fixturehero", raws)
        self.assertIn(a[2]["heroRaw"], raws)
        comp = next(c for c in self.n["compositions"] if c["mapId"] == busan["id"]
                    and c["side"] == "a" and c["kind"] == "first-listed")
        self.assertFalse(comp["complete"])

    def test_reviewed_picture_override(self):
        busan = self.map("fixture-gamma-vs-fixture-delta-m1-busan")
        pic = next(x for x in self.apps(busan, "a") if x["position"] == 2)["heroRaw"][len("picture:"):]
        b = bundle()
        cfg = copy.deepcopy(b.aliases_cfg)
        cfg["pictures"] = {pic: "sojourn"}
        n = import_workbook(self.raw, AliasBook.build(cfg, b.public), b.layout())
        x = next(a for a in n["appearances"] if a["mapId"] == busan["id"] and a["side"] == "a"
                 and a["position"] == 2)
        self.assertEqual((x["hero"], x["heroMethod"]), ("sojourn", "picture-override"))

    def test_bans_from_text_and_forfeit(self):
        nepal = self.map("qadsiah-vs-twis-m1-nepal")
        bans = {b["side"]: (b["teamId"], b["hero"]) for b in self.n["bans"] if b["mapId"] == nepal["id"]}
        self.assertEqual(bans, {"a": ("qadsiah", "tracer"), "b": ("twis", "ana")})
        busan = self.map("fixture-alpha-vs-fixture-beta-m1-busan")
        self.assertEqual([b for b in self.n["bans"] if b["mapId"] == busan["id"]], [])
        self.assertIn("no-ban:a:forfeit", busan["flags"])

    def test_results_draw_push_and_tiebreak(self):
        nepal = self.map("qadsiah-vs-twis-m1-nepal")
        self.assertEqual((nepal["winner"], nepal["winnerMethod"], nepal["scoreA"], nepal["scoreB"]),
                         ("twis", "card-winner-text", 1, 2))
        self.assertEqual((nepal["order"], nepal["mode"]), (1, "Control"))
        push = self.map("fixture-alpha-vs-fixture-beta-m2-runasapi")
        self.assertEqual((push["winnerSide"], push["scoreA"], push["scoreB"]), ("b", 0, 1))
        self.assertNotIn("card-score-disagrees-with-map-list", push["flags"])   # distances, not rounds
        draw = self.map("fixture-alpha-vs-fixture-beta-m3-midtown")
        self.assertEqual((draw["winner"], draw["winnerSide"]), (None, "draw"))
        tb = self.map("fixture-alpha-vs-fixture-beta-m4-gibraltar")
        self.assertIn("card-no-map-panel", tb["flags"])
        self.assertEqual((tb["winnerSide"], tb["winnerMethod"]), ("b", "card-map-list-arrow"))
        self.assertEqual([a for a in self.n["appearances"] if a["mapId"] == tb["id"]], [])
        m = self.match("fixture-alpha-vs-fixture-beta")
        self.assertEqual((m["seriesScoreA"], m["seriesScoreB"], m["winner"]), (1, 2, "x-fixture-beta"))

    def test_self_check(self):
        sc = self.inv["selfCheck"]
        rows = {(r["stage"], r["region"]): r for r in sc["rows"]}
        self.assertTrue(rows[("Stage 2", "EMEA")]["agree"])
        self.assertEqual(rows[("Stage 2", "EMEA")]["tabs"], ["EMEA - Stage 2"])
        self.assertFalse(rows[("Stage 1", "JP")]["agree"])
        self.assertIsNone(rows[("Stage 2", "NA")]["agree"])       # stage filled down; no tab
        codes = [i["code"] for i in self.inv["issues"]]
        self.assertEqual(codes.count("SELF_CHECK_MISMATCH"), 1)
        self.assertEqual(codes.count("SELF_CHECK_UNPAIRED"), 1)
        self.assertEqual(sc["mismatches"], 1)

    def test_ids_are_stable(self):
        b = bundle()
        again = import_workbook(read_xlsx(cards_bytes()), b.aliases(), b.layout())
        self.assertEqual([m["id"] for m in again["maps"]], [m["id"] for m in self.n["maps"]])
        self.assertEqual(len({m["slug"] for m in self.n["maps"]}), len(self.n["maps"]))

    def test_raw_layer_keeps_pictures(self):
        emea = next(s for s in self.raw["sheets"] if s["name"] == "EMEA - Stage 2")
        self.assertTrue(emea["images"])
        self.assertTrue(all(len(h) == 10 for _, h in emea["images"]))

    def test_mostly_unreadable_cards_fail_the_import(self):
        raw = copy.deepcopy(self.raw)
        jp = next(s for s in raw["sheets"] if s["name"] == "Japan - Stage 1")
        # Five cards whose two teams are the same team cannot be read.
        rows = {r: list(v) for r, v in jp["rows"]}
        for k in range(5):
            top = 3 + 26 * (k + 1)
            rows[top] = [None, None, "OWCS Japan"]
            rows[top + 2] = [None, None, f"Week 2 | Day 1 | Game {k + 1}"]
            rows[top + 4] = [None, None, "SAME", None, None, None, 1, 0, None, "SAME"]
            rows[top + 9] = [None, None, "►", 2, None, "Busan", None, None, None, 0]
        jp["rows"] = sorted([r, v] for r, v in rows.items())
        b = bundle()
        with self.assertRaises(ImportFailed) as cm:
            import_workbook(raw, b.aliases(), b.layout())
        self.assertIn("MOSTLY_IGNORED", [e["code"] for e in cm.exception.inventory["errors"]])


class MatchCardReconcile(unittest.TestCase):
    """No date in the sheet: the stage of the season stands in for it."""

    @classmethod
    def setUpClass(cls):
        b = bundle()
        cls.aliases = b.aliases()
        cls.n = import_workbook(read_xlsx(cards_bytes()), cls.aliases, b.layout())
        cls.pub = read_public(CFG.public)

    def nepal_record(self, n):
        nepal = next(m for m in n["maps"] if m["map"] == "nepal")
        recon = R.reconcile(n, self.pub, {}, self.aliases)
        return next(r for r in recon["records"] if r["externalMapId"] == nepal["id"])

    def test_same_stage_links_without_a_date(self):
        r = self.nepal_record(self.n)
        self.assertEqual((r["vodMapId"], r["method"]), ("m-qad-twis-s2po-m1", "auto"))
        self.assertIn("no date, same stage stage-2 (+2)", r["candidates"][0]["reasons"])
        self.assertEqual(r["status"], "partial")             # no date / pick / bans on one side
        self.assertIsNone(r["fields"]["date"]["agree"])
        self.assertIsNone(r["fields"]["pickedBy"]["agree"])
        self.assertTrue(r["fields"]["winner"]["agree"])
        self.assertTrue(r["fields"]["heroes:qadsiah"]["agree"])
        self.assertTrue(r["fields"]["heroes:twis"]["agree"])

    def test_different_stage_rejects(self):
        n = copy.deepcopy(self.n)
        for m in n["matches"]:
            m["stageKey"] = "stage-1"
        r = self.nepal_record(n)
        self.assertIsNone(r["vodMapId"])
        self.assertEqual((r["status"], r["candidates"]), ("unmatched", []))

    def test_different_season_rejects(self):
        n = copy.deepcopy(self.n)
        for m in n["matches"]:
            m["year"] = 2025
        self.assertEqual(self.nepal_record(n)["candidates"], [])

    def test_stage_key_ignores_phase_words(self):
        self.assertEqual(self.aliases.stage_key("Stage 2 Playoffs"), "stage-2")
        self.assertEqual(self.aliases.stage_key("EMEA - Stage 2"), "stage-2")
        self.assertEqual(self.aliases.stage_key("Midseason Championship Paris"), "midseason-championship")
        self.assertIsNone(self.aliases.stage_key("Playoffs"))
        self.assertEqual(self.aliases.region_in("North America - Stage 1").id, "na")

    def test_dated_records_are_unaffected(self):
        n = parse(fixture_bytes())
        recon = R.reconcile(n, self.pub, {}, self.aliases)
        r = next(x for x in recon["records"] if x["vodMapId"])
        self.assertNotIn("no date", " ".join(r["candidates"][0]["reasons"]))


class MatchCardSync(unittest.TestCase):
    def test_sync_export_and_check(self):
        p = temp_repo(self)
        res = S.sync(p, data=cards_bytes(), at="2026-11-01T00:00:00+00:00")
        self.assertEqual(res["status"], "imported", res)
        self.assertEqual(res["reconciliation"]["linked"], 1)
        exp = read_public(p.export)
        nepal = next(m for m in exp["maps"] if m["map"] == "nepal")
        self.assertIsNone(nepal["date"])
        self.assertIsNone(nepal["pickedBy"])
        self.assertEqual(exp["health"]["selfCheck"]["mismatches"], 1)
        self.assertEqual(exp["health"]["matchCards"]["played"], 3)
        text = json.dumps(exp["maps"])
        for forbidden in ('"seconds"', '"start"', '"end"', '"duration"', '"offset"'):
            self.assertNotIn(forbidden, text)
        md = slurp(os.path.join(p.reports, "inventory.md"))
        self.assertIn("## Self-Check", md)
        self.assertIn("**DISAGREE**", md)
        # the committed raw layer (with picture anchors) rebuilds identically
        self.assertEqual(S.check(p), [])

    def test_inventory_cli_writes_nothing(self):
        import contextlib
        import sheet_sync
        p = temp_repo(self)
        before = sorted(os.listdir(p.root))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = sheet_sync.main(["--root", p.root, "inventory", "--xlsx", CARDS, "--sample", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(os.listdir(p.root)), before)
        self.assertFalse(os.path.exists(p.manifest))
        text = out.getvalue()
        self.assertIn("## Sample of parsed maps", text)
        self.assertIn("Reconciliation against the published VOD maps (not written)", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
