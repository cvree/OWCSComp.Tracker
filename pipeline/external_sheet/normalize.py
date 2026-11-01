"""
normalize.py — raw cell layer -> normalised External* records.

Record types (contract: docs/EXTERNAL-SHEET.md):

  ExternalTournament              an event as the sheet names it
  ExternalMatch                   a series: date, teams, series score
  ExternalMap                     one map: order, map, mode, result, pick
  ExternalHeroBan                 one hero ban on one map
  ExternalPlayerHeroAppearance    a hero the sheet LISTS for a player on a map
  ExternalCompositionObservation  a hero set the sheet lists for a team on a map
  SourceFieldProvenance           where each normalised value came from

What this module will NOT do, by design:

  * invent time. A player listed on 'Juno / Lúcio' is two appearances with
    list positions 1 and 2. Nothing here says when, in what order they were
    played, or for how long; nothing downstream may either.
  * guess a value. A hero, map or mode no alias recognises stays null beside
    its original text and is listed as unmapped. Teams and players that no
    roster knows get a deterministic slug so they can still be grouped.
  * hide a bad row. Every row in a recognised tab is either parsed or listed
    as ignored with its reason; a tab where most rows are ignored fails the
    import (the layout probably changed).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from . import PARSER_VERSION
from .aliases import AliasBook, Resolution, norm, slugify, year_in
from .layout import Header, Layout
from .workbook import a1, grid

DATA_PURPOSES = ("map_rows", "player_rows", "ban_rows", "match_rows", "match_cards")
#: Purposes are parsed in this order so player/ban/match rows can attach to
#: maps that map_rows already created. Match cards are self-contained.
PARSE_ORDER = {"match_cards": 0, "map_rows": 0, "player_rows": 1, "ban_rows": 2, "match_rows": 3}


class ImportFailed(RuntimeError):
    """The workbook's structure is not what the importer can trust."""

    def __init__(self, message: str, inventory: dict | None = None):
        super().__init__(message)
        self.inventory = inventory or {}


def _h(*parts: Any, n: int = 10) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:n]


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"\d+", str(value or ""))
    return int(m.group(0)) if m else None


SCORE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%?\s*(?:-|–|—|:|to|x)\s*(\d+(?:\.\d+)?)", re.I)
URL_RE = re.compile(r"https?://\S+")


def _score(value: Any) -> tuple[float | None, float | None]:
    m = SCORE_RE.search(str(value or ""))
    if not m:
        return None, None
    a, b = float(m.group(1)), float(m.group(2))
    return (int(a) if a.is_integer() else a), (int(b) if b.is_integer() else b)


@dataclass
class Ctx:
    """One importer run's working state."""
    aliases: AliasBook
    layout: Layout
    tournaments: dict = field(default_factory=dict)
    matches: dict = field(default_factory=dict)
    maps: dict = field(default_factory=dict)
    bans: dict = field(default_factory=dict)
    appearances: dict = field(default_factory=dict)
    compositions: dict = field(default_factory=dict)
    provenance: list = field(default_factory=list)
    unmapped: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)
    duplicates: list = field(default_factory=list)
    map_keys: dict = field(default_factory=dict)
    current_inv: dict | None = None
    # Per-map / per-match indexes so no row ever rescans the whole workbook
    # (a 100k-row sheet went from minutes to seconds).
    bans_by_map: dict = field(default_factory=dict)
    apps_by_map: dict = field(default_factory=dict)
    maps_by_match: dict = field(default_factory=dict)

    def add_ban(self, b: dict) -> None:
        if b["id"] not in self.bans:
            self.bans_by_map.setdefault(b["mapId"], []).append(b)
        self.bans[b["id"]] = b

    def add_app(self, a: dict) -> None:
        if a["id"] not in self.appearances:
            self.apps_by_map.setdefault(a["mapId"], []).append(a)
        self.appearances[a["id"]] = a

    def add_map(self, m: dict) -> None:
        if m["id"] not in self.maps:
            self.maps_by_match.setdefault(m["matchId"], []).append(m)
        self.maps[m["id"]] = m

    # ------------------------------------------------------------ helpers
    def prov(self, record: str, fld: str, sheet: str, cell: str | None,
             method: str, raw: Any, value: Any) -> None:
        self.provenance.append({"record": record, "field": fld, "sheet": sheet,
                                "cell": cell, "method": method,
                                "raw": None if raw is None else str(raw)[:200],
                                "value": value})

    def miss(self, kind: str, raw: Any, sheet: str, cell: str | None) -> None:
        text = str(raw).strip()
        if not text:
            return
        bucket = self.unmapped.setdefault(kind, {})
        k = norm(text)
        e = bucket.setdefault(k, {"raw": text, "count": 0, "refs": []})
        e["count"] += 1
        if len(e["refs"]) < 5:
            e["refs"].append(f"{sheet}!{cell}")

    def note_resolution(self, kind: str, res: Resolution, sheet: str, cell: str | None) -> None:
        if res.method in ("unresolved", "slug"):
            self.miss(kind, res.raw, sheet, cell)


# =================================================================== rows
class Row:
    """One worksheet row viewed through its header."""

    def __init__(self, sheet: str, r: int, values: list, header: Header,
                 origin: dict, links: dict):
        self.sheet, self.r, self.values = sheet, r, values
        self.header, self.origin, self.links = header, origin, links

    def get(self, fld: str) -> tuple[Any, str | None, bool]:
        """(value, A1 cell, came-from-merge) for a single field."""
        c = self.header.by_field(fld)
        if c is None or c.col >= len(self.values):
            return None, None, False
        v = self.values[c.col]
        return v, a1(self.r, c.col), (self.r, c.col) in self.origin

    def cell(self, col: int) -> tuple[Any, str]:
        v = self.values[col] if col < len(self.values) else None
        return v, a1(self.r, col)

    def link(self, col: int) -> str | None:
        return self.links.get(a1(self.r, col))

    def blank(self) -> bool:
        return all(v in (None, "") for v in self.values)


def _is_header_repeat(row: Row) -> bool:
    hits = total = 0
    for c in row.header.columns:
        total += 1
        v = row.values[c.col] if c.col < len(row.values) else None
        if norm(v) and norm(v) in (norm(c.header), c.key, norm(c.cell)):
            hits += 1
    return total > 0 and hits / total >= 0.6


# =================================================================== import
def import_workbook(raw: dict, aliases: AliasBook, layout: Layout,
                    previous_tabs: dict | None = None,
                    accept_structure_change: bool = False) -> dict:
    """Parse the whole workbook. Returns {records…, inventory}. Raises
    ImportFailed when the structure cannot be trusted."""
    from . import cards as C
    ctx = Ctx(aliases=aliases, layout=layout)
    sheets_inv: list[dict] = []
    credits: list[dict] = []
    plan = []
    # Workbook-wide lookups for match cards: the Hero Index pictures and the
    # season year the landing tab states.
    card_ref = {"layout": layout.cards, "pictures": C.hero_pictures(raw, layout.cards),
                "tabs": {}, "year": None, "yearCell": None}
    card_ref["year"], card_ref["yearCell"] = C.workbook_year(raw, layout.cards)
    for sheet in raw.get("sheets") or []:
        g, origin = grid(sheet)
        header = None
        named = layout.tab_purpose(sheet["name"]) if g else None
        if not g:
            purpose = "empty"
        elif named:
            purpose = named
        elif C.find_blocks(g, layout.cards):
            purpose = "match_cards"
            card_ref["tabs"][sheet["name"]] = {
                "region": aliases.region_in(sheet["name"]),
                "stage": aliases.stage(sheet["name"]).id,
                "stageKey": aliases.stage_key(sheet["name"])}
        else:
            header = layout.find_header(g)
            purpose = layout.purpose(header)
        sheet_credits = layout.credits(sheet["name"], g, a1) if g else []
        credits.extend(sheet_credits)
        if purpose == "unrecognised" and sheet_credits:
            purpose = "credits"
        inv = {
            "name": sheet["name"], "index": sheet["index"], "state": sheet.get("state"),
            "rows": len(sheet.get("rows") or []),
            "columns": max([len(v) for _, v in sheet.get("rows") or []] + [0]),
            "headerRow": (header.row + 1) if header else None,
            "purpose": purpose, "purposeLabel": layout.purpose_label(purpose),
            "mappedColumns": ([{"column": a1(header.row, c.col)[:-len(str(header.row + 1))],
                                "header": c.header, "as": c.label()}
                               for c in sorted(header.columns, key=lambda c: c.col)]
                              if header else []),
            "unmappedColumns": ([{"column": a1(header.row, c)[:-len(str(header.row + 1))],
                                  "header": t} for c, t in header.unmapped] if header else []),
            "parsed": {"maps": 0, "bans": 0, "appearances": 0, "compositions": 0, "matches": 0,
                       "duplicates": 0},
            "ignoredRows": [], "ignoredCount": 0, "dataRows": 0,
            "images": len(sheet.get("images") or []),
        }
        sheets_inv.append(inv)
        if purpose in DATA_PURPOSES:
            plan.append((PARSE_ORDER[purpose], sheet["index"], sheet, g, origin, header, inv))

    for _, _, sheet, g, origin, header, inv in sorted(plan, key=lambda p: (p[0], p[1])):
        if inv["purpose"] == "match_cards":
            ctx.current_inv = inv
            C.parse_card_sheet(ctx, sheet, g, origin, inv, card_ref)
        else:
            _parse_sheet(ctx, sheet, g, origin, header, inv)

    _finish_matches(ctx)
    inventory = _inventory(ctx, raw, sheets_inv, credits)
    if any(t["purpose"] == "match_cards" for t in sheets_inv):
        pics = card_ref["pictures"]
        inventory["matchCards"] = {
            "year": card_ref["year"], "yearCell": card_ref["yearCell"],
            "heroIndexTab": pics["tab"], "heroPictures": len(pics["heroes"]),
            "rolePictures": len(pics["roles"]), "heroIndexConflicts": pics["conflicts"],
            "blocks": sum((t.get("cards") or {}).get("blocks", 0) for t in sheets_inv),
            "played": sum((t.get("cards") or {}).get("played", 0) for t in sheets_inv),
            "scheduled": sum((t.get("cards") or {}).get("scheduled", 0) for t in sheets_inv),
            "failed": sum((t.get("cards") or {}).get("failed", 0) for t in sheets_inv),
        }
        if not card_ref["year"]:
            ctx.issues.append({"level": "warning", "code": "NO_SEASON_YEAR", "message": (
                "the landing tab states no season year; match-card records keep year = null")})
        for c in pics["conflicts"]:
            ctx.issues.append({"level": "warning", "code": "HERO_INDEX_CONFLICT", "message": (
                f"picture {c['picture']} is named both {c['names'][0]!r} and {c['names'][1]!r} "
                f"in the Hero Index ({c['cell']}); the first name is used")})
    sc = C.self_check(raw, layout.self_check, sheets_inv, aliases)
    if sc is not None:
        inventory["selfCheck"] = sc
        for row in sc["rows"]:
            if row["agree"] is False:
                ctx.issues.append({"level": "warning", "code": "SELF_CHECK_MISMATCH",
                                   "sheet": sc["tab"], "row": row["row"], "message": (
                    f"Self-Check {row['stage']} {row['region']}: the sheet counts "
                    f"{row['sheet']['matches']} matches / {row['sheet']['maps']} maps, the importer "
                    f"parsed {row['parsed']['matches']} / {row['parsed']['maps']} from "
                    f"'{row['tabs'][0]}'")})
            elif row["agree"] is None:
                ctx.issues.append({"level": "warning", "code": "SELF_CHECK_UNPAIRED",
                                   "sheet": sc["tab"], "row": row["row"], "message": (
                    f"Self-Check {row['stage']} {row['region']}: no single match-card tab to compare "
                    f"with (candidates: {row['tabs'] or 'none'}) — add the region code to "
                    "selfCheck.regionCodes in config/external/gsheet_layout.json")})
    _gate(inventory, previous_tabs, accept_structure_change, layout)
    return {
        "parserVersion": PARSER_VERSION,
        "tournaments": sorted(ctx.tournaments.values(), key=lambda x: x["id"]),
        "matches": sorted(ctx.matches.values(), key=lambda x: (x["date"] or "", x["id"])),
        "maps": sorted(ctx.maps.values(), key=lambda x: (x["date"] or "", x["matchId"], x["order"] or 0)),
        "bans": sorted(ctx.bans.values(), key=lambda x: x["id"]),
        "appearances": sorted(ctx.appearances.values(), key=lambda x: x["id"]),
        "compositions": sorted(ctx.compositions.values(), key=lambda x: x["id"]),
        "fieldProvenance": ctx.provenance,
        "inventory": inventory,
    }


# ------------------------------------------------------------- per sheet
def _sheet_context(ctx: Ctx, sheet: dict, header: Header) -> dict:
    """Facts the TAB NAME states, used only when no column states them."""
    name = sheet["name"]
    out = {}
    if not header.by_field("region"):
        reg = ctx.aliases.region(name)
        if not reg.resolved:
            for token in norm(name).split():
                r2 = ctx.aliases.region(token)
                if r2.resolved:
                    reg = Resolution(r2.id, "sheet-name", name)
                    break
        else:
            reg = Resolution(reg.id, "sheet-name", name)
        if reg.resolved:
            out["region"] = reg
    if not header.by_field("event"):
        out["event"] = name
    out["year"] = year_in(name)
    return out


def _parse_sheet(ctx: Ctx, sheet: dict, g: list, origin: dict, header: Header, inv: dict) -> None:
    purpose = inv["purpose"]
    ctx.current_inv = inv
    links = sheet.get("links") or {}
    tab = _sheet_context(ctx, sheet, header)
    ff: dict[str, tuple[Any, str]] = {}
    seq: dict[str, int] = {}
    ignored: list[dict] = []
    data_rows = 0
    for r in range(header.row + 1, len(g)):
        row = Row(sheet["name"], r, g[r], header, origin, links)
        if row.blank():
            continue
        if _is_header_repeat(row):
            continue
        # Forward-fill: a value written once for a block of rows (a date
        # over its maps) applies to the rows below it, with provenance
        # saying so.
        filled: dict[str, tuple[Any, str, str]] = {}
        for col in header.columns:
            if not col.field:
                continue
            v, cell, merged = row.get(col.field)
            if v not in (None, "") and not ctx.aliases.is_blank(v):
                filled[col.field] = (v, cell, "merged-cell" if merged else "direct")
                if col.field in ctx.layout.forward_fill:
                    ff[col.field] = (v, cell)
            elif col.field in ff:
                filled[col.field] = (ff[col.field][0], ff[col.field][1], "forward-fill")
        data_rows += 1
        try:
            if purpose == "map_rows":
                reason = _map_row(ctx, row, filled, tab, seq, inv)
            elif purpose == "player_rows":
                reason = _player_row(ctx, row, filled, tab, inv)
            elif purpose == "ban_rows":
                reason = _ban_row(ctx, row, filled, tab, inv)
            else:
                reason = _match_row(ctx, row, filled, tab, inv)
        except Exception as exc:  # a parser bug must not lose the row silently
            reason = f"parser error: {type(exc).__name__}: {exc}"
            ctx.issues.append({"level": "error", "code": "ROW_PARSE_ERROR",
                               "sheet": sheet["name"], "row": r + 1, "message": reason})
        if reason == "context":
            data_rows -= 1
        elif reason:
            ignored.append({"row": r + 1, "reason": reason})
    inv["dataRows"] = data_rows
    inv["ignoredCount"] = len(ignored)
    inv["ignoredRows"] = ignored[:50]


# ------------------------------------------------------------- shared
def _team_res(ctx: Ctx, raw: Any, date: str | None, sheet: str, cell: str | None) -> Resolution:
    res = ctx.aliases.team(raw, date)
    ctx.note_resolution("team", res, sheet, cell)
    return res


def _team_key(res: Resolution) -> str:
    return res.id or ("raw:" + norm(res.raw))


def _event_fields(ctx: Ctx, filled: dict, tab: dict) -> dict:
    ev_raw = filled.get("event", (None,))[0] or tab.get("event")
    st_raw = filled.get("stage", (None,))[0]
    rg_raw = filled.get("region", (None,))[0]
    region = ctx.aliases.region(rg_raw) if rg_raw else tab.get("region")
    stage = ctx.aliases.stage(st_raw) if st_raw else None
    if (stage is None or not stage.id) and ev_raw:
        stage = ctx.aliases.stage(ev_raw)        # 'Korea Stage 2' names its stage
    year = year_in(ev_raw, st_raw) or tab.get("year")
    return {"eventRaw": None if ev_raw is None else str(ev_raw),
            "stageRaw": None if st_raw is None else str(st_raw),
            "regionRaw": None if rg_raw is None else str(rg_raw),
            "region": region.id if region and region.resolved else None,
            "regionMethod": region.method if region else None,
            "stage": stage.id if stage and stage.id else None,
            "year": year}


def _date(ctx: Ctx, filled: dict, ev: dict, sheet: str) -> tuple[str | None, dict]:
    v, cell, how = filled.get("date", (None, None, None))
    iso, method, ambiguous = ctx.aliases.date(v, ev.get("year"))
    if v not in (None, "") and iso is None:
        ctx.miss("date", v if not isinstance(v, dict) else v.get("date"), sheet, cell)
    return iso, {"dateRaw": (v.get("date") if isinstance(v, dict) else
                             (None if v is None else str(v))),
                 "dateMethod": method if how != "forward-fill" else f"{method}+forward-fill",
                 "dateAmbiguous": ambiguous, "dateCell": cell}


def _ensure_tournament(ctx: Ctx, ev: dict) -> str | None:
    if not ev.get("eventRaw"):
        return None
    res = ctx.aliases.tournament(ev["eventRaw"])
    tid = res.id
    t = ctx.tournaments.setdefault(tid, {
        "id": tid, "slug": tid[3:] if tid.startswith("xt-") else tid,
        "name": ev["eventRaw"], "nameMethod": res.method,
        "regions": [], "stages": [], "year": ev.get("year")})
    if ev.get("region") and ev["region"] not in t["regions"]:
        t["regions"].append(ev["region"])
    if ev.get("stageRaw") and ev["stageRaw"] not in t["stages"]:
        t["stages"].append(ev["stageRaw"])
    return tid


def _ensure_match(ctx: Ctx, sheet: str, row: Row, date: str | None, dmeta: dict,
                  ta: Resolution, tb: Resolution, ev: dict, filled: dict) -> dict:
    keys = sorted([_team_key(ta), _team_key(tb)])
    explicit = filled.get("match_id", (None,))[0]
    mkey = (date or f"nodate:{sheet}:{explicit or ''}", keys[0], keys[1])
    mid = "xm-" + _h(*mkey)
    m = ctx.matches.get(mid)
    if m is None:
        a_slug = (ta.id or slugify(ta.raw)).replace("x-", "")
        b_slug = (tb.id or slugify(tb.raw)).replace("x-", "")
        m = {
            "id": mid, "slug": f"{date or 'undated'}-{a_slug}-vs-{b_slug}",
            "tournamentId": _ensure_tournament(ctx, ev),
            "event": ev.get("eventRaw"), "stage": ev.get("stage"), "stageRaw": ev.get("stageRaw"),
            "region": ev.get("region"), "regionRaw": ev.get("regionRaw"),
            "regionMethod": ev.get("regionMethod"),
            "date": date, **{k: v for k, v in dmeta.items() if k != "dateCell"},
            "patch": None if filled.get("patch", (None,))[0] is None else str(filled["patch"][0]),
            "teamA": ta.id, "teamARaw": ta.raw, "teamAMethod": ta.method,
            "teamB": tb.id, "teamBRaw": tb.raw, "teamBMethod": tb.method,
            "seriesScoreA": None, "seriesScoreB": None, "seriesScoreRaw": None,
            "seriesScoreMethod": None, "winner": None, "winnerMethod": None,
            "mapIds": [], "src": {"sheet": sheet, "row": row.r + 1},
        }
        ctx.matches[mid] = m
        for fld, res in (("teamA", ta), ("teamB", tb)):
            ctx.prov(mid, fld, sheet, None, res.method, res.raw, res.id)
        if dmeta.get("dateCell"):
            ctx.prov(mid, "date", sheet, dmeta["dateCell"], dmeta["dateMethod"], dmeta["dateRaw"], date)
    ss = filled.get("series_score", (None,))[0]
    if ss and m["seriesScoreRaw"] is None:
        a, b = _score(ss)
        if a is not None:
            # The row writes the score in ITS team order; store it in the
            # match's team order.
            if _team_key(ta) != (m["teamA"] or "raw:" + norm(m["teamARaw"])):
                a, b = b, a
            m["seriesScoreRaw"] = str(ss)
            m["seriesScoreA"], m["seriesScoreB"] = a, b
            m["seriesScoreMethod"] = "sheet"
            ctx.prov(mid, "seriesScore", sheet, filled["series_score"][1], "direct", ss, [a, b])
    return m


def _side_of(ctx: Ctx, raw: Any, ta: Resolution, tb: Resolution, date: str | None) -> tuple[str | None, str]:
    k = norm(raw)
    if not k or ctx.aliases.is_blank(raw):
        return None, "empty"
    if k in ("draw", "tie", "tied"):
        return "draw", "draw"
    if k in ("1", "team 1", "t1", "a", "team a", "left", "home", "blue"):
        return "a", "side-token"
    if k in ("2", "team 2", "t2", "b", "team b", "right", "away", "red"):
        return "b", "side-token"
    if ta.raw and k == norm(ta.raw):
        return "a", "same-text-as-team"
    if tb.raw and k == norm(tb.raw):
        return "b", "same-text-as-team"
    res = ctx.aliases.team(raw, date)
    if res.resolved:
        if res.id == ta.id:
            return "a", "alias"
        if res.id == tb.id:
            return "b", "alias"
    return None, "unresolved"


def _heroes_from(ctx: Ctx, value: Any, sheet: str, cell: str) -> list[Resolution]:
    out = []
    for token in ctx.layout.split(value):
        res = ctx.aliases.hero(token)
        if res.method == "empty":
            continue
        ctx.note_resolution("hero", res, sheet, cell)
        out.append(res)
    return out


# ------------------------------------------------------------- map rows
def _map_row(ctx: Ctx, row: Row, filled: dict, tab: dict, seq: dict, inv: dict) -> str | None:
    sheet = row.sheet
    map_v, map_cell, _ = row.get("map")
    ta_raw = filled.get("team_a", filled.get("team", (None, None, None)))
    tb_raw = filled.get("team_b", (None, None, None))
    date_only = filled.get("date") and not map_v and not ta_raw[0]
    if date_only or (not map_v and not ta_raw[0] and not tb_raw[0]):
        return "context"            # a section label / date row, not a map
    if not map_v or ctx.aliases.is_blank(map_v):
        return "no map named on this row"
    if not ta_raw[0] or not tb_raw[0]:
        return "map row without both teams"
    ev = _event_fields(ctx, filled, tab)
    date, dmeta = _date(ctx, filled, ev, sheet)
    ta = _team_res(ctx, ta_raw[0], date, sheet, ta_raw[1])
    tb = _team_res(ctx, tb_raw[0], date, sheet, tb_raw[1])
    if _team_key(ta) == _team_key(tb):
        return "both teams are the same"
    mres = ctx.aliases.map(map_v)
    ctx.note_resolution("map", mres, sheet, map_cell)
    if not mres.resolved and not (ta.resolved or tb.resolved):
        return f"unrecognised map '{map_v}' and neither team recognised — not treated as a map"

    m = _ensure_match(ctx, sheet, row, date, dmeta, ta, tb, ev, filled)
    order_v, order_cell, _ = row.get("map_order")
    order = _int(order_v)
    order_method = "sheet"
    if order is None:
        seq[m["id"]] = seq.get(m["id"], 0) + 1
        order, order_method = seq[m["id"]], "row-sequence"
    else:
        seq[m["id"]] = max(seq.get(m["id"], 0), order)
    map_id = "xmap-" + _h(m["id"], order)
    src_cells = {"map": map_cell}
    mode_v, mode_cell, _ = row.get("mode")
    mode_res = ctx.aliases.mode(mode_v) if mode_v else None
    if mode_res:
        ctx.note_resolution("mode", mode_res, sheet, mode_cell)
    catalog_mode = ctx.aliases.map_modes.get(mres.id or "") or None
    if catalog_mode in ("", "Unknown"):
        catalog_mode = None
    mode = (mode_res.id if mode_res and mode_res.resolved else None) or catalog_mode
    mode_method = ("sheet" if mode_res and mode_res.resolved else
                   "catalog-from-map" if catalog_mode else None)
    flags = []
    if mode_res and mode_res.resolved and catalog_mode and mode_res.id != catalog_mode:
        flags.append("mode-disagrees-with-map-catalog")
    if not mres.resolved:
        flags.append("map-unmapped")

    # result
    sa, sb = (None, None)
    score_raw = None
    for f in ("map_score",):
        v, cell, _ = row.get(f)
        if v not in (None, ""):
            sa, sb = _score(v)
            score_raw = str(v)
            src_cells["score"] = cell
    if sa is None:
        va, ca, _ = row.get("score_a")
        vb, cb, _ = row.get("score_b")
        if va not in (None, "") and vb not in (None, ""):
            sa, sb = _score(f"{va}-{vb}")
            score_raw = f"{va}-{vb}"
            src_cells["score"] = f"{ca},{cb}"
    win_v, win_cell, _ = row.get("winner")
    w_side, w_method = _side_of(ctx, win_v, ta, tb, date)
    if win_v not in (None, "") and w_side is None:
        ctx.miss("winner", win_v, sheet, win_cell)
        flags.append("winner-unresolved")
    if w_side is None:
        lose_v, lose_cell, _ = row.get("loser")
        l_side, l_method = _side_of(ctx, lose_v, ta, tb, date)
        if l_side in ("a", "b"):
            w_side, w_method, win_v, win_cell = ("b" if l_side == "a" else "a"), f"from-loser:{l_method}", lose_v, lose_cell
    if w_side is None and sa is not None and sb is not None and sa != sb:
        w_side, w_method = ("a" if sa > sb else "b"), "derived-from-map-score"
    elif w_side in ("a", "b") and sa is not None and sb is not None and sa != sb:
        if (sa > sb) != (w_side == "a"):
            flags.append("winner-disagrees-with-map-score")
    winner = {"a": ta.id, "b": tb.id}.get(w_side or "")
    pick_v, pick_cell, _ = row.get("picked_by")
    p_side, p_method = _side_of(ctx, pick_v, ta, tb, date)
    pick_note = None
    if pick_v not in (None, "") and p_side is None:
        pick_note = str(pick_v)              # 'decider', 'loser pick', …
    vod = None
    vcol = header_col(row, "vod")
    if vcol is not None:
        vod = row.link(vcol) or (URL_RE.search(str(row.values[vcol] or "")) or [None])[0] \
            if vcol < len(row.values) else None
    if vod is None:
        mc = header_col(row, "map")
        vod = row.link(mc) if mc is not None else None
    notes_v, _, _ = row.get("notes")

    rec = {
        "id": map_id, "matchId": m["id"],
        "slug": f"{m['slug']}-m{order}" + (f"-{mres.id}" if mres.id else ""),
        "date": date, "order": order, "orderMethod": order_method,
        "map": mres.id, "mapRaw": mres.raw, "mapMethod": mres.method,
        "mode": mode, "modeRaw": None if mode_v is None else str(mode_v), "modeMethod": mode_method,
        "teamA": ta.id, "teamB": tb.id,
        "winner": winner, "winnerSide": w_side, "winnerRaw": None if win_v is None else str(win_v),
        "winnerMethod": w_method if w_side else None,
        "pickedBy": {"a": ta.id, "b": tb.id}.get(p_side or ""), "pickedBySide": p_side if p_side in ("a", "b") else None,
        "pickedByRaw": None if pick_v is None else str(pick_v),
        "pickedByMethod": p_method if p_side in ("a", "b") else None, "pickNote": pick_note,
        "scoreA": sa, "scoreB": sb, "scoreRaw": score_raw,
        "vodUrl": vod, "notes": None if notes_v is None else str(notes_v),
        "flags": flags,
        "src": {"sheet": sheet, "row": row.r + 1, "cells": src_cells},
        "alsoListedAt": [],
    }
    for fld, cell, method, raw, val in (
            ("map", map_cell, mres.method, mres.raw, mres.id),
            ("winner", win_cell, w_method, win_v, winner),
            ("pickedBy", pick_cell, p_method, pick_v, rec["pickedBy"]),
            ("mode", mode_cell, mode_method, mode_v, mode),
            ("order", order_cell, order_method, order_v, order)):
        if raw is not None or val is not None:
            ctx.prov(map_id, fld, sheet, cell, method or "absent", raw, val)

    bans, apps, comps = _row_heroes(ctx, row, map_id, m["id"], ta, tb, date)
    _store_map(ctx, rec, bans, apps, comps, inv, m)
    return None


def header_col(row: Row, fld: str) -> int | None:
    c = row.header.by_field(fld)
    return c.col if c else None


def _row_heroes(ctx: Ctx, row: Row, map_id: str, match_id: str,
                ta: Resolution, tb: Resolution, date: str | None):
    sheet = row.sheet
    team_of = {"a": ta.id, "b": tb.id, None: None}
    bans, apps, comps = [], [], []
    # bans
    ban_cols = row.header.group("ban") + row.header.group("ban_noside")
    bc = row.header.by_field("banned_hero")
    if bc:
        ban_cols = ban_cols + [bc]
    counter: dict = {}
    for c in sorted(ban_cols, key=lambda c: c.col):
        v, cell = row.cell(c.col)
        for res in _heroes_from(ctx, v, sheet, cell):
            side = c.side
            counter[side] = counter.get(side, 0) + 1
            order = counter[side]
            bans.append({
                "id": f"xban-{map_id[5:]}-{side or 'x'}-{order}",
                "mapId": map_id, "matchId": match_id, "side": side,
                "teamId": team_of[side], "hero": res.id, "heroRaw": res.raw,
                "heroMethod": res.method, "order": order,
                "src": {"sheet": sheet, "cell": cell}})
    # players / heroes
    positions: dict[tuple[str, int], dict] = {}
    for c in row.header.group("player_name"):
        v, cell = row.cell(c.col)
        if v in (None, "") or ctx.aliases.is_blank(v):
            continue
        positions.setdefault((c.side or "a", c.p or 1), {})["name"] = (v, cell)
    for c in sorted(row.header.group("player_hero"), key=lambda c: (c.side or "", c.p or 0, c.n or 0, c.col)):
        v, cell = row.cell(c.col)
        heroes = _heroes_from(ctx, v, sheet, cell)
        pos = positions.setdefault((c.side or "a", c.p or 1), {})
        pos.setdefault("role", c.role)
        lst = pos.setdefault("heroes", [])
        for res in heroes:
            lst.append((res, cell))
    for (side, p), pos in sorted(positions.items()):
        name = pos.get("name")
        pres = ctx.aliases.player(name[0]) if name else None
        if pres:
            ctx.note_resolution("player", pres, sheet, name[1])
        for n, (res, cell) in enumerate(pos.get("heroes") or [], start=1):
            apps.append({
                "id": f"xpa-{map_id[5:]}-{side}-{p}-{n}",
                "mapId": map_id, "matchId": match_id, "side": side, "teamId": team_of[side],
                "position": p, "player": pres.id if pres else None,
                "playerRaw": pres.raw if pres else None,
                "playerMethod": pres.method if pres else None,
                "listOrder": n, "hero": res.id, "heroRaw": res.raw, "heroMethod": res.method,
                "role": ctx.aliases.role_of(res.id) or pos.get("role"),
                "src": {"sheet": sheet, "cell": cell}})
    for c in row.header.group("comp"):
        v, cell = row.cell(c.col)
        heroes = _heroes_from(ctx, v, sheet, cell)
        if heroes:
            comps.append(_comp(map_id, match_id, c.side or "a", team_of[c.side or "a"],
                               "listed-comp", heroes, {"sheet": sheet, "cell": cell}))
    return bans, apps, comps


def _comp(map_id: str, match_id: str, side: str, team: str | None, kind: str,
          heroes: list[Resolution], src: dict) -> dict:
    ids, raws, unresolved = [], [], []
    for r in heroes:
        raws.append(r.raw)
        if r.id:
            if r.id not in ids:
                ids.append(r.id)
        else:
            unresolved.append(r.raw)
    return {"id": f"xco-{map_id[5:]}-{side}-{kind}", "mapId": map_id, "matchId": match_id,
            "side": side, "teamId": team, "kind": kind, "heroes": ids,
            "heroesRaw": raws, "unresolved": unresolved,
            "complete": len(ids) == 5 and not unresolved, "src": src}


def _derived_comps(map_id: str, match_id: str, apps: list[dict], team_of: dict) -> list[dict]:
    """first-listed: each position's first listed hero (NOT 'the starting
    comp' — the sheet does not say that); hero-pool: everything listed."""
    out = []
    for side in ("a", "b"):
        rows = [a for a in apps if a["side"] == side]
        if not rows:
            continue
        firsts = [a for a in rows if a["listOrder"] == 1]
        firsts.sort(key=lambda a: a["position"])
        first_res = [Resolution(a["hero"], a["heroMethod"], a["heroRaw"]) for a in firsts]
        pool_res = [Resolution(a["hero"], a["heroMethod"], a["heroRaw"]) for a in
                    sorted(rows, key=lambda a: (a["listOrder"], a["position"]))]
        src = {"derivedFrom": "appearances"}
        if first_res:
            out.append(_comp(map_id, match_id, side, team_of[side], "first-listed", first_res, src))
        if any(a["listOrder"] > 1 for a in rows):
            out.append(_comp(map_id, match_id, side, team_of[side], "hero-pool", pool_res, src))
    return out


def _store_map(ctx: Ctx, rec: dict, bans: list, apps: list, comps: list, inv: dict, match: dict) -> None:
    team_of = {"a": rec["teamA"], "b": rec["teamB"]}
    comps = comps + _derived_comps(rec["id"], rec["matchId"], apps, team_of)
    existing = ctx.maps.get(rec["id"])
    if existing is not None:
        diffs = [k for k in ("map", "winner", "pickedBy", "mode", "scoreA", "scoreB")
                 if rec.get(k) is not None and existing.get(k) is not None
                 and rec.get(k) != existing.get(k)]
        old_bans = sorted(b["hero"] or b["heroRaw"] for b in ctx.bans_by_map.get(rec["id"], []))
        new_bans = sorted(b["hero"] or b["heroRaw"] for b in bans)
        if old_bans and new_bans and old_bans != new_bans:
            diffs.append("bans")
        old_h = sorted({a["hero"] or a["heroRaw"] for a in ctx.apps_by_map.get(rec["id"], [])})
        new_h = sorted({a["hero"] or a["heroRaw"] for a in apps})
        if old_h and new_h and old_h != new_h:
            diffs.append("heroes")
        inv["parsed"]["duplicates"] = inv["parsed"].get("duplicates", 0) + 1
        if not diffs:
            existing["alsoListedAt"].append(rec["src"])
            ctx.duplicates.append({"mapId": rec["id"], "kind": "identical",
                                   "first": existing["src"], "again": rec["src"]})
        else:
            existing.setdefault("duplicateConflicts", []).append(
                {"src": rec["src"], "fields": diffs or ["heroes/bans"],
                 "values": {k: rec.get(k) for k in diffs}})
            ctx.duplicates.append({"mapId": rec["id"], "kind": "conflicting",
                                   "first": existing["src"], "again": rec["src"],
                                   "fields": diffs or ["heroes/bans"]})
            ctx.issues.append({"level": "warning", "code": "DUPLICATE_CONFLICT",
                               "sheet": rec["src"]["sheet"], "row": rec["src"]["row"],
                               "message": (f"map {rec['slug']} is listed twice with different "
                                           f"{', '.join(diffs) or 'heroes/bans'}; the first listing "
                                           f"({existing['src']['sheet']} row {existing['src']['row']}) "
                                           "is kept and the second is shown as a within-sheet conflict")})
        return
    ctx.add_map(rec)
    if rec["id"] not in match["mapIds"]:
        match["mapIds"].append(rec["id"])
    for b in bans:
        ctx.add_ban(b)
    for a in apps:
        ctx.add_app(a)
    for c in comps:
        ctx.compositions[c["id"]] = c
    inv["parsed"]["maps"] += 1
    inv["parsed"]["bans"] += len(bans)
    inv["parsed"]["appearances"] += len(apps)
    inv["parsed"]["compositions"] += len(comps)


# ------------------------------------------------------- long formats
def _find_map(ctx: Ctx, row: Row, filled: dict, tab: dict, *, create: bool):
    """Locate (or create, for player rows that carry a full map key) the map
    a long-format row belongs to. Returns (map, side, team Resolution) or
    a reason string."""
    sheet = row.sheet
    ev = _event_fields(ctx, filled, tab)
    date, dmeta = _date(ctx, filled, ev, sheet)
    team = filled.get("team", (None, None, None))
    ta_raw = filled.get("team_a", (None, None, None))
    tb_raw = filled.get("team_b", (None, None, None))
    if team[0] and tb_raw[0] and not ta_raw[0]:
        ta_raw = team                       # team + opponent
    if not ta_raw[0] or not tb_raw[0]:
        return "row does not name both teams of its map"
    ta = _team_res(ctx, ta_raw[0], date, sheet, ta_raw[1])
    tb = _team_res(ctx, tb_raw[0], date, sheet, tb_raw[1])
    side = "a"
    if team[0] and team is not ta_raw:
        s, _ = _side_of(ctx, team[0], ta, tb, date)
        if s not in ("a", "b"):
            return f"player's team '{team[0]}' is neither team of the map"
        side = s
    keys = sorted([_team_key(ta), _team_key(tb)])
    mid = "xm-" + _h(date or f"nodate:{sheet}:{filled.get('match_id', (None,))[0] or ''}", keys[0], keys[1])
    order_v, _, _ = row.get("map_order")
    order = _int(order_v)
    map_v, map_cell, _ = row.get("map")
    mres = ctx.aliases.map(map_v) if map_v else None
    cand = [m for m in ctx.maps_by_match.get(mid, []) if
            ((order is not None and m["order"] == order) or
             (order is None and mres is not None and mres.id and m["map"] == mres.id))]
    if len(cand) == 1:
        m = cand[0]
        # The row names its teams in its own order; orient the player's
        # side to the map's team order (teamA is side 'a' on the map).
        my_team = ta if side == "a" else tb
        side = "a" if _team_key(my_team) == _map_team_key(ctx, m, "a") else "b"
        return m, side, my_team
    if len(cand) > 1:
        return "row matches more than one map of its series (add a map number)"
    if not create or not map_v or order is None and not mres:
        return "no map of this series matches the row (needs date, both teams and map or map number)"
    if not mres or not mres.resolved:
        ctx.note_resolution("map", mres or Resolution(None, "unresolved", str(map_v)), sheet, map_cell)
    m = _ensure_match(ctx, sheet, row, date, dmeta, ta, tb, ev, filled)
    if order is None:
        order = len(m["mapIds"]) + 1
    rec = {
        "id": "xmap-" + _h(m["id"], order), "matchId": m["id"],
        "slug": f"{m['slug']}-m{order}" + (f"-{mres.id}" if mres and mres.id else ""),
        "date": date, "order": order, "orderMethod": "sheet" if _int(order_v) is not None else "row-sequence",
        "map": mres.id if mres else None, "mapRaw": None if map_v is None else str(map_v),
        "mapMethod": mres.method if mres else "absent",
        "mode": ctx.aliases.map_modes.get(mres.id or "") or None if mres else None,
        "modeRaw": None, "modeMethod": "catalog-from-map" if mres and mres.id else None,
        "teamA": ta.id, "teamB": tb.id, "winner": None, "winnerSide": None, "winnerRaw": None,
        "winnerMethod": None, "pickedBy": None, "pickedBySide": None, "pickedByRaw": None,
        "pickedByMethod": None, "pickNote": None, "scoreA": None, "scoreB": None, "scoreRaw": None,
        "vodUrl": None, "notes": None, "flags": ["created-from-long-format-rows"] +
        ([] if mres and mres.resolved else ["map-unmapped"]),
        "src": {"sheet": sheet, "row": row.r + 1, "cells": {"map": map_cell}}, "alsoListedAt": [],
    }
    ctx.add_map(rec)
    m["mapIds"].append(rec["id"])
    inv_counter = ctx.current_inv
    if inv_counter is not None:
        inv_counter["parsed"]["maps"] += 1
    my_team = ta if side == "a" else tb
    return rec, side, my_team


def _map_team_key(ctx: Ctx, m: dict, side: str) -> str:
    match = ctx.matches.get(m["matchId"]) or {}
    tid = m["team" + side.upper()]
    if tid:
        return tid
    raw = match.get("teamARaw" if side == "a" else "teamBRaw")
    return "raw:" + norm(raw)


def _player_row(ctx: Ctx, row: Row, filled: dict, tab: dict, inv: dict) -> str | None:
    sheet = row.sheet
    pv, pcell, _ = row.get("player")
    if not pv or ctx.aliases.is_blank(pv):
        return "no player named"
    found = _find_map(ctx, row, filled, tab, create=True)
    if isinstance(found, str):
        return found
    m, side, team = found
    pres = ctx.aliases.player(pv)
    ctx.note_resolution("player", pres, sheet, pcell)
    cells = []
    hc = row.header.by_field("hero")
    if hc:
        cells.append(row.cell(hc.col))
    for c in sorted(row.header.group("long_hero"), key=lambda c: (c.n or 0, c.col)):
        cells.append(row.cell(c.col))
    heroes = []
    for v, cell in cells:
        heroes.extend((res, cell) for res in _heroes_from(ctx, v, sheet, cell))
    if not heroes:
        return "no hero listed for the player"
    existing = [a for a in ctx.apps_by_map.get(m["id"], []) if a["side"] == side]
    by_player = {a["player"]: a["position"] for a in existing}
    position = by_player.get(pres.id) or (max([a["position"] for a in existing] + [0]) + 1)
    role_v, _, _ = row.get("role")
    for n, (res, cell) in enumerate(heroes, start=1):
        aid = f"xpa-{m['id'][5:]}-{side}-{position}-{n}"
        if aid in ctx.appearances:
            continue
        ctx.add_app({
            "id": aid, "mapId": m["id"], "matchId": m["matchId"], "side": side,
            "teamId": team.id, "position": position, "player": pres.id,
            "playerRaw": pres.raw, "playerMethod": pres.method, "listOrder": n,
            "hero": res.id, "heroRaw": res.raw, "heroMethod": res.method,
            "role": ctx.aliases.role_of(res.id) or (str(role_v) if role_v else None),
            "src": {"sheet": sheet, "cell": cell}})
        inv["parsed"]["appearances"] += 1
    # rebuild derived comps for this map side
    apps = ctx.apps_by_map.get(m["id"], [])
    for c in _derived_comps(m["id"], m["matchId"], apps, {"a": m["teamA"], "b": m["teamB"]}):
        ctx.compositions[c["id"]] = c
    return None


def _ban_row(ctx: Ctx, row: Row, filled: dict, tab: dict, inv: dict) -> str | None:
    sheet = row.sheet
    found = _find_map(ctx, row, filled, tab, create=False)
    if isinstance(found, str):
        return found
    m, side, team = found
    v, cell, _ = row.get("banned_hero")
    heroes = _heroes_from(ctx, v, sheet, cell)
    if not heroes:
        return "no banned hero on the row"
    for res in heroes:
        n = 1 + sum(1 for b in ctx.bans_by_map.get(m["id"], []) if b["side"] == side)
        bid = f"xban-{m['id'][5:]}-{side}-{n}"
        ctx.add_ban({"id": bid, "mapId": m["id"], "matchId": m["matchId"], "side": side,
                     "teamId": team.id, "hero": res.id, "heroRaw": res.raw,
                     "heroMethod": res.method, "order": n,
                     "src": {"sheet": sheet, "cell": cell}})
        inv["parsed"]["bans"] += 1
    return None


def _match_row(ctx: Ctx, row: Row, filled: dict, tab: dict, inv: dict) -> str | None:
    sheet = row.sheet
    ta_raw, tb_raw = filled.get("team_a"), filled.get("team_b")
    if not ta_raw or not tb_raw:
        return "context" if filled.get("date") else "series row without both teams"
    ev = _event_fields(ctx, filled, tab)
    date, dmeta = _date(ctx, filled, ev, sheet)
    ta = _team_res(ctx, ta_raw[0], date, sheet, ta_raw[1])
    tb = _team_res(ctx, tb_raw[0], date, sheet, tb_raw[1])
    created = "xm-" + _h(date or f"nodate:{sheet}:", *sorted([_team_key(ta), _team_key(tb)])) not in ctx.matches
    m = _ensure_match(ctx, sheet, row, date, dmeta, ta, tb, ev, filled)
    if m["seriesScoreA"] is None:
        va, _, _ = row.get("score_a")
        vb, _, _ = row.get("score_b")
        if va not in (None, "") and vb not in (None, ""):
            a, b = _score(f"{va}-{vb}")
            if _team_key(ta) != (m["teamA"] or "raw:" + norm(m["teamARaw"])):
                a, b = b, a
            m["seriesScoreA"], m["seriesScoreB"] = a, b
            m["seriesScoreRaw"], m["seriesScoreMethod"] = f"{va}-{vb}", "sheet"
    wv, wcell, _ = row.get("winner")
    s, method = _side_of(ctx, wv, ta, tb, date)
    if s in ("a", "b") and not m["winner"]:
        m["winner"] = (ta if s == "a" else tb).id
        m["winnerMethod"] = f"sheet:{method}"
    if created:
        m.setdefault("flags", []).append("series-only")
        inv["parsed"]["matches"] += 1
    return None


def _finish_matches(ctx: Ctx) -> None:
    """Series scores the sheet did not write are DERIVED from its own map
    winners and labelled so; a written score that disagrees is flagged."""
    for m in ctx.matches.values():
        maps = [ctx.maps[i] for i in m["mapIds"] if i in ctx.maps]
        maps.sort(key=lambda x: x["order"] or 0)
        m["mapIds"] = [x["id"] for x in maps]
        # By team id, not side: a map may list its teams in the other order.
        wa = sum(1 for x in maps if x["winner"] and x["winner"] == m["teamA"])
        wb = sum(1 for x in maps if x["winner"] and x["winner"] == m["teamB"])
        if m["seriesScoreA"] is None and (wa or wb):
            m["seriesScoreA"], m["seriesScoreB"] = wa, wb
            m["seriesScoreMethod"] = "derived-from-map-winners"
        elif m["seriesScoreA"] is not None and (wa or wb) and (m["seriesScoreA"], m["seriesScoreB"]) != (wa, wb):
            m.setdefault("flags", []).append("series-score-disagrees-with-map-winners")
            m["mapWinnerCount"] = [wa, wb]
        if not m["winner"] and m["seriesScoreA"] is not None and m["seriesScoreA"] != m["seriesScoreB"]:
            m["winner"] = m["teamA"] if m["seriesScoreA"] > m["seriesScoreB"] else m["teamB"]
            m["winnerMethod"] = "derived-from-series-score"
        m.setdefault("flags", [])


# ------------------------------------------------------------ inventory
def _inventory(ctx: Ctx, raw: dict, sheets: list, credits: list) -> dict:
    unmapped = {}
    for kind, bucket in sorted(ctx.unmapped.items()):
        unmapped[kind] = sorted(bucket.values(), key=lambda e: (-e["count"], e["raw"].lower()))
    return {
        "parserVersion": PARSER_VERSION,
        "layoutVersion": ctx.layout.version,
        "aliasesVersion": ctx.aliases.version,
        "tabs": sheets,
        "tabCount": len(sheets),
        "dataTabs": sum(1 for s in sheets if s["purpose"] in DATA_PURPOSES),
        "sourceRowCount": sum(s["rows"] for s in sheets),
        "counts": {
            "tournaments": len(ctx.tournaments), "matches": len(ctx.matches),
            "maps": len(ctx.maps), "bans": len(ctx.bans),
            "appearances": len(ctx.appearances), "compositions": len(ctx.compositions),
            "ignoredRows": sum(s["ignoredCount"] for s in sheets),
            "duplicates": sum(1 for d in ctx.duplicates if d["kind"] == "identical"),
            "duplicateConflicts": sum(1 for d in ctx.duplicates if d["kind"] == "conflicting"),
            "unmappedValues": sum(len(v) for v in unmapped.values()),
        },
        "unmapped": unmapped,
        "duplicates": ctx.duplicates[:200],
        "credits": credits,
        "package": raw.get("package"),
        "issues": ctx.issues,
        "errors": [],
    }


def _gate(inv: dict, previous_tabs: dict | None, accept: bool, layout: Layout) -> None:
    """Fail loudly when the workbook's structure cannot be trusted."""
    errors = inv["errors"]
    if not inv["tabs"]:
        errors.append({"code": "EMPTY_WORKBOOK", "message": "the workbook has no worksheets"})
    if inv["dataTabs"] == 0:
        errors.append({"code": "NO_DATA_TABS", "message": (
            "no worksheet was recognised as match/map data — every tab's header is listed "
            "in the inventory; add the new header spellings to config/external/gsheet_layout.json")})
    elif inv["counts"]["maps"] == 0:
        errors.append({"code": "NO_MAPS", "message": "data tabs were found but no map could be parsed"})
    for t in inv["tabs"]:
        if t["purpose"] in DATA_PURPOSES and t["dataRows"] >= 5:
            share = t["ignoredCount"] / max(1, t["dataRows"])
            if share > layout.max_ignored:
                errors.append({"code": "MOSTLY_IGNORED", "sheet": t["name"], "message": (
                    f"tab '{t['name']}': {t['ignoredCount']} of {t['dataRows']} rows could not be "
                    f"parsed (>{layout.max_ignored:.0%}) — its layout probably changed. First "
                    f"reasons: {[r['reason'] for r in t['ignoredRows'][:3]]}")})
    for name, purpose in sorted((previous_tabs or {}).items()):
        if purpose not in DATA_PURPOSES:
            continue
        now = next((t for t in inv["tabs"] if t["name"] == name), None)
        if now is None:
            errors.append({"code": "TAB_REMOVED", "sheet": name, "message": (
                f"tab '{name}' held {purpose} in the last good snapshot and is gone now")})
        elif now["purpose"] != purpose:
            errors.append({"code": "STRUCTURE_CHANGED", "sheet": name, "message": (
                f"tab '{name}' was read as {purpose} in the last good snapshot and as "
                f"{now['purpose']} now; header row {now['headerRow']}: "
                f"{[c['header'] for c in now['mappedColumns']][:12]} unmapped: "
                f"{[c['header'] for c in now['unmappedColumns']][:12]}")})
    if accept:
        for e in errors:
            if e["code"] in ("TAB_REMOVED", "STRUCTURE_CHANGED"):
                e["accepted"] = True
    fatal = [e for e in errors if not e.get("accepted")]
    if fatal:
        raise ImportFailed("; ".join(e["message"] for e in fatal), inv)
