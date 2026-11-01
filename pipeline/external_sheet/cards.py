"""
cards.py — the real community sheet's MATCH CARDS -> External* records.

The sheet is not a table. Every series is a fixed-size card (26 rows): a
left panel with the event, format, game label, both teams, the series
score and the list of maps; then one panel per map with both line-ups,
where each player's heroes are PICTURES, the two hero bans, the winner and
the map score. docs/SHEET-LAYOUT-NOTES.md has the geometry; every offset
lives in config/external/gsheet_layout.json under "matchCards", never here.

How a card is read:

  1. find it by its game label ('Week 1 | Day 2 | Game 3') in the label
     column; the card's top row b is `labelRow` rows above;
  2. read every value at its configured offset from b;
  3. decode hero pictures through the sheet's own Hero Index tab (a
     picture, its name in the cell below) and the reviewed `pictures`
     overrides in aliases.json. A picture nobody names, or a name no hero
     alias knows (a hero newer than our catalog), stays an UNMAPPED hero
     beside its raw text — it is never guessed;
  4. emit the same records as the tabular importer (normalize.py): one
     match, one map per played panel, two bans per map, one appearance per
     listed hero with its list position, and the derived compositions.

What the sheet does not say is never produced: it has no calendar date
(the match keeps date = null and its week/day/game label), no map picks
(pickedBy stays null) and no time of any kind. A card whose teams are not
filled in yet is a scheduled match: it is counted, not imported.
"""
from __future__ import annotations

import re
from typing import Any

from .aliases import Resolution, norm, slugify, year_in
from .workbook import a1


# ------------------------------------------------------------ geometry
def col(letter: str) -> int:
    """'C' -> 2 (0-based)."""
    n = 0
    for ch in letter.strip().upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def parse_a1(ref: str) -> tuple[int, int]:
    """'N21' -> (20, 13), 0-based (row, col)."""
    m = re.match(r"^([A-Z]+)(\d+)$", ref)
    if not m:
        raise ValueError(ref)
    return int(m.group(2)) - 1, col(m.group(1))


class CardLayout:
    """The "matchCards" section of gsheet_layout.json, compiled."""

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg or {}
        c = self.cfg
        self.enabled = bool(c)
        self.label_re = re.compile(c.get("gameLabel") or r"^$", re.I)
        self.label_col = col(c.get("labelColumn") or "C")
        self.label_row = int(c.get("labelRow") or 2)
        self.left = {k: (int(v[0]), col(v[1])) for k, v in (c.get("left") or {}).items()}
        ml = c.get("mapList") or {}
        self.list_rows = (int(ml.get("firstRow", 9)), int(ml.get("lastRow", 20)))
        self.list_cols = {k: col(ml[k]) for k in ("wonA", "scoreA", "map", "scoreB", "wonB") if ml.get(k)}
        self.won_a, self.won_b = ml.get("wonAMark") or "►", ml.get("wonBMark") or "◄"
        p = c.get("panels") or {}
        self.panel0 = col(p.get("firstColumn") or "N")
        self.panel_step = int(p.get("step") or 17)
        self.panel_count = int(p.get("count") or 5)
        self.panel_width = int(p.get("width") or self.panel_step)
        self.starts = [int(x) for x in p.get("playerStarts") or [0, 3, 7, 10, 13]]
        self.name_offset = int(p.get("nameOffset", 1))
        self.side = {s: p.get(s) or {} for s in ("a", "b")}
        self.panel_cells = {k: (int(v[0]), int(v[1])) for k, v in p.items()
                            if isinstance(v, list) and len(v) == 2 and k not in ("playerStarts",)}
        self.winner_re = re.compile(c.get("winnerText") or r"^(?P<team>.+?)\s+wins!?$", re.I)
        self.map_score_re = re.compile(c.get("mapScoreText") or r"(?P<a>\d+)\s*-\s*(?P<b>\d+)", re.I)
        self.team_suffix = re.compile(c.get("teamSuffix") or r"\s*\[[^\]]*\]\s*$")
        hi = c.get("heroIndex") or {}
        self.hero_tab = re.compile(hi.get("tab") or r"^hero index$", re.I)
        self.name_below = int(hi.get("nameBelow", 1))
        self.role_right = int(hi.get("roleLabelRight", 1))
        self.role_words = {norm(k): v for k, v in (c.get("roleWords") or {}).items()}
        self.role_icons = dict(c.get("roleIcons") or {})
        self.ban_blank = {norm(x) for x in c.get("banBlank") or []}
        self.draw_words = {norm(x) for x in c.get("drawText") or ["draw"]}
        yf = c.get("yearFrom") or {}
        self.year_tab = re.compile(yf.get("tab") or r"^landing page$", re.I)
        self.year_rows = int(yf.get("rows") or 3)


def _val(g: list, r: int, c: int) -> Any:
    if 0 <= r < len(g) and 0 <= c < len(g[r]):
        v = g[r][c]
        return None if v in (None, "") else v
    return None


def _text(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, dict):
        v = v.get("date") or v.get("time")
    t = str(v).strip()
    return t or None


def images_by_cell(sheet: dict) -> dict[tuple[int, int], list[str]]:
    out: dict[tuple[int, int], list[str]] = {}
    for ref, h in sheet.get("images") or []:
        try:
            out.setdefault(parse_a1(ref), []).append(h)
        except ValueError:
            continue
    return out


def find_blocks(g: list, L: CardLayout) -> list[tuple[int, re.Match]]:
    """[(top row b, label match)] for every card in a grid."""
    out = []
    if not L.enabled:
        return out
    for r in range(len(g)):
        t = _text(_val(g, r, L.label_col))
        if not t:
            continue
        m = L.label_re.match(" ".join(t.split()))
        if m and r - L.label_row >= 0:
            out.append((r - L.label_row, m))
    return out


# --------------------------------------------------------- reference tabs
def hero_pictures(raw: dict, L: CardLayout) -> dict:
    """{'heroes': {hash: name}, 'roles': {hash: role}, 'tab': name,
    'conflicts': [...]} from the Hero Index tab: a picture, its name in the
    cell `nameBelow` rows down; a role icon has its label to the right."""
    from .workbook import grid
    out = {"heroes": {}, "roles": dict(L.role_icons), "tab": None, "conflicts": []}
    for sheet in raw.get("sheets") or []:
        if not L.hero_tab.search(sheet["name"].strip()):
            continue
        out["tab"] = sheet["name"]
        g, _ = grid(sheet)
        for (r, c), hashes in sorted(images_by_cell(sheet).items()):
            right = _text(_val(g, r, c + L.role_right))
            below = _text(_val(g, r + L.name_below, c))
            for h in hashes:
                if right and norm(right) in L.role_words:
                    out["roles"][h] = L.role_words[norm(right)]
                elif below:
                    name = " ".join(below.split())
                    if h in out["heroes"] and norm(out["heroes"][h]) != norm(name):
                        out["conflicts"].append({"picture": h, "names": [out["heroes"][h], name],
                                                 "cell": a1(r, c)})
                        continue
                    out["heroes"].setdefault(h, name)
        break
    return out


def workbook_year(raw: dict, L: CardLayout) -> tuple[int | None, str | None]:
    """(year, 'Tab!A1') stated on the landing tab, or (None, None)."""
    from .workbook import grid
    for sheet in raw.get("sheets") or []:
        if not L.year_tab.search(sheet["name"].strip()):
            continue
        g, _ = grid(sheet)
        for r in range(min(L.year_rows, len(g))):
            for c, v in enumerate(g[r]):
                if isinstance(v, str):
                    y = year_in(v)
                    if y:
                        return y, f"{sheet['name']}!{a1(r, c)}"
    return None, None


# --------------------------------------------------------------- reading
def read_card(g: list, origin: dict, imgs: dict, b: int, label: re.Match, L: CardLayout) -> dict:
    """Every value of one card, with the cell it came from. No aliases,
    no interpretation beyond the geometry."""
    def at(r_off: int, c: int) -> tuple[Any, str]:
        return _val(g, b + r_off, c), a1(b + r_off, c)

    card: dict = {"top": b, "label": " ".join(_text(label.group(0)).split()),
                  "week": int(label.group("week")) if label.groupdict().get("week") else None,
                  "day": int(label.group("day")) if label.groupdict().get("day") else None,
                  "game": int(label.group("game")) if label.groupdict().get("game") else None}
    for k, (ro, c) in L.left.items():
        card[k] = at(ro, c)
    maps = []
    lo, hi = L.list_rows
    mc = L.list_cols.get("map")
    for r in range(b + lo, b + hi + 1):
        if mc is None or (r, mc) in origin:
            continue
        name = _text(_val(g, r, mc))
        if not name:
            continue
        entry = {"row": r, "map": (name, a1(r, mc))}
        for k in ("wonA", "scoreA", "scoreB", "wonB"):
            if k in L.list_cols:
                entry[k] = (_val(g, r, L.list_cols[k]), a1(r, L.list_cols[k]))
        maps.append(entry)
    card["mapList"] = maps
    panels = []
    for k in range(L.panel_count):
        p = L.panel0 + k * L.panel_step
        cell = lambda name: (lambda ro_co: at(ro_co[0], p + ro_co[1]))(L.panel_cells[name]) \
            if name in L.panel_cells else (None, None)
        pan = {"index": k, "column": p}
        for name in ("map", "winner", "banA", "banB", "mapScore"):
            pan[name] = cell(name)
        for name in ("banPictureA", "banPictureB"):
            if name in L.panel_cells:
                ro, co = L.panel_cells[name]
                pan[name] = (imgs.get((b + ro, p + co)) or [None])[0], a1(b + ro, p + co)
        for side in ("a", "b"):
            sp = L.side[side]
            team = sp.get("team")
            pan["team" + side.upper()] = at(team[0], p + team[1]) if team else (None, None)
            players = []
            pr = int(sp.get("playerRow", 0))
            for i, s in enumerate(L.starts):
                players.append({
                    "position": i + 1,
                    "name": at(pr, p + s + L.name_offset),
                    "icon": (imgs.get((b + pr, p + s)) or [None])[0],
                    "heroes": []})
            for hr in sp.get("heroRows") or []:
                r = b + int(hr)
                for c in range(p, p + L.panel_width):
                    for h in imgs.get((r, c)) or []:
                        off = c - p
                        i = max(j for j, s in enumerate(L.starts) if s <= off) if off >= L.starts[0] else 0
                        players[i]["heroes"].append((h, a1(r, c)))
            pan["players" + side.upper()] = players
        pan["played"] = bool(_text(pan["map"][0]) or _text(pan["teamA"][0]) or _text(pan["teamB"][0]))
        panels.append(pan)
    card["panels"] = panels
    return card


# ------------------------------------------------------------- records
def _strip_team(L: CardLayout, v: Any) -> str | None:
    t = _text(v)
    if not t:
        return None
    return L.team_suffix.sub("", t).strip() or None


def _int(v: Any) -> int | None:
    """A whole number written in a cell, else None (70.36 is not one)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v.is_integer() else None
    m = re.fullmatch(r"\s*(\d+)\s*", str(v))
    return int(m.group(1)) if m else None


def _num(v: Any) -> int | float | None:
    """A number as written: Push maps list distances (70.36), not rounds."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v) if float(v).is_integer() else v
    try:
        f = float(str(v).strip())
    except ValueError:
        return None
    return int(f) if f.is_integer() else f


def parse_card_sheet(ctx, sheet: dict, g: list, origin: dict, inv: dict, ref: dict) -> None:
    """Parse every card of one tab into ctx (normalize.Ctx). `ref` carries
    the workbook-wide lookups: hero pictures, the year, the card layout."""
    from . import normalize as N
    L: CardLayout = ref["layout"]
    name = sheet["name"]
    imgs = images_by_cell(sheet)
    blocks = find_blocks(g, L)
    stats = {"blocks": len(blocks), "played": 0, "scheduled": 0, "failed": 0, "labels": []}
    inv["cards"] = stats
    ignored: list[dict] = []
    seen_labels: dict[str, int] = {}
    for b, label in blocks:
        card = read_card(g, origin, imgs, b, label, L)
        seen_labels[card["label"]] = seen_labels.get(card["label"], 0) + 1
        try:
            reason = _card(ctx, N, sheet, card, inv, ref)
        except Exception as exc:  # a parser bug must not lose the card silently
            reason = f"parser error: {type(exc).__name__}: {exc}"
            ctx.issues.append({"level": "error", "code": "CARD_PARSE_ERROR", "sheet": name,
                               "row": b + 1, "message": reason})
        if reason == "scheduled":
            stats["scheduled"] += 1
        elif reason:
            stats["failed"] += 1
            ignored.append({"row": b + 1, "reason": f"{card['label']}: {reason}"})
        else:
            stats["played"] += 1
    dup = sorted(k for k, n in seen_labels.items() if n > 1)
    if dup:
        ctx.issues.append({"level": "warning", "code": "CARD_LABEL_REPEATED", "sheet": name,
                           "message": f"tab '{name}' has more than one card labelled {dup[:5]}"})
    del stats["labels"]
    inv["dataRows"] = stats["played"] + stats["failed"]
    inv["ignoredCount"] = len(ignored)
    inv["ignoredRows"] = ignored[:50]


def _team(ctx, N, full: str | None, code: str | None, sheet: str, cell: str | None) -> Resolution:
    """A card names a team twice: in full on each map panel ('Telomere
    [0-0]') and as a short code in the left panel ('TLM'). The full name is
    the record's raw text; the code is tried only when the name is unknown."""
    res = ctx.aliases.team(full or code, None)
    if not res.resolved and full and code:
        alt = ctx.aliases.team(code, None)
        if alt.resolved:
            res = Resolution(alt.id, alt.method + "+code", full)
    ctx.note_resolution("team", res, sheet, cell)
    return res


def _hero(ctx, ref: dict, h: str | None, sheet: str, cell: str) -> Resolution | None:
    """A hero picture -> Resolution. Method says how: a reviewed picture
    override, or the Hero Index name then the hero aliases."""
    if not h:
        return None
    if h in ctx.aliases.pictures:
        return Resolution(ctx.aliases.pictures[h], "picture-override", f"picture:{h}")
    name = ref["pictures"]["heroes"].get(h)
    if not name:
        res = Resolution(None, "unresolved", f"picture:{h}")
        ctx.note_resolution("hero", res, sheet, cell)
        return res
    res = ctx.aliases.hero(name)
    res = Resolution(res.id, "hero-index+" + res.method, name)
    if res.id is None:
        ctx.miss("hero", name, sheet, cell)
    return res


def _side_of_name(ctx, N, text: str | None, names: dict, ta: Resolution, tb: Resolution) -> str | None:
    if not text:
        return None
    k = norm(text)
    for side in ("a", "b"):
        if any(k == norm(n) for n in names[side] if n):
            return side
    s, _ = N._side_of(ctx, text, ta, tb, None)
    return s if s in ("a", "b") else None


def _card(ctx, N, sheet: dict, card: dict, inv: dict, ref: dict) -> str | None:
    L: CardLayout = ref["layout"]
    name = sheet["name"]
    played = [p for p in card["panels"] if p["played"]]
    code_a, code_a_cell = card.get("teamACode", (None, None))
    code_b, code_b_cell = card.get("teamBCode", (None, None))
    full_a = next((_strip_team(L, p["teamA"][0]) for p in played if _strip_team(L, p["teamA"][0])), None)
    full_b = next((_strip_team(L, p["teamB"][0]) for p in played if _strip_team(L, p["teamB"][0])), None)
    code_a, code_b = _text(code_a), _text(code_b)
    if not (full_a or code_a) or not (full_b or code_b):
        return "scheduled"                      # teams not filled in yet
    if not played and not card["mapList"]:
        return "scheduled"                      # teams known, nothing played yet
    ta = _team(ctx, N, full_a, code_a, name, code_a_cell)
    tb = _team(ctx, N, full_b, code_b, name, code_b_cell)
    if N._team_key(ta) == N._team_key(tb):
        return "both teams are the same"

    tab = ref["tabs"][name]
    event_raw = _text(card.get("event", (None,))[0]) or name
    ev = {"eventRaw": event_raw, "stageRaw": name, "regionRaw": name,
          "region": tab["region"].id if tab["region"] and tab["region"].resolved else None,
          "regionMethod": "sheet-name" if tab["region"] and tab["region"].resolved else None,
          "stage": tab["stage"], "year": ref["year"]}
    keys = sorted([N._team_key(ta), N._team_key(tb)])
    mid = "xm-" + N._h("card", name, norm(card["label"]), keys[0], keys[1])
    a_slug = (ta.id or slugify(ta.raw)).replace("x-", "")
    b_slug = (tb.id or slugify(tb.raw)).replace("x-", "")
    label_slug = "-".join(f"{k[0]}{card[k]}" for k in ("week", "day", "game") if card.get(k) is not None)
    fmt = _text(card.get("format", (None,))[0])
    m = ctx.matches.get(mid)
    if m is None:
        m = {
            "id": mid, "slug": f"{slugify(name)}-{label_slug}-{a_slug}-vs-{b_slug}",
            "tournamentId": N._ensure_tournament(ctx, ev),
            "event": ev["eventRaw"], "stage": ev["stage"], "stageRaw": ev["stageRaw"],
            "stageKey": tab["stageKey"],
            "region": ev["region"], "regionRaw": ev["regionRaw"], "regionMethod": ev["regionMethod"],
            "year": ref["year"],
            # The sheet has no calendar dates. Never inferred.
            "date": None, "dateRaw": None, "dateMethod": "not-in-source", "dateAmbiguous": False,
            "patch": None,
            "teamA": ta.id, "teamARaw": ta.raw, "teamAMethod": ta.method,
            "teamB": tb.id, "teamBRaw": tb.raw, "teamBMethod": tb.method,
            "teamACode": code_a, "teamBCode": code_b,
            "seriesScoreA": None, "seriesScoreB": None, "seriesScoreRaw": None,
            "seriesScoreMethod": None, "winner": None, "winnerMethod": None,
            "format": fmt, "gameLabel": card["label"],
            "week": card["week"], "day": card["day"], "game": card["game"],
            "playerOfTheMatchRaw": _potm(card),
            "mapIds": [], "src": {"sheet": name, "row": card["top"] + 1, "kind": "match-card"},
        }
        ctx.matches[mid] = m
        for fld, res, cell in (("teamA", ta, code_a_cell), ("teamB", tb, code_b_cell)):
            ctx.prov(mid, fld, name, cell, res.method, res.raw, res.id)
        sa, sa_cell = card.get("seriesA", (None, None))
        sb, _ = card.get("seriesB", (None, None))
        if _int(sa) is not None and _int(sb) is not None:
            m["seriesScoreA"], m["seriesScoreB"] = _int(sa), _int(sb)
            m["seriesScoreRaw"], m["seriesScoreMethod"] = f"{sa}-{sb}", "sheet"
            ctx.prov(mid, "seriesScore", name, sa_cell, "direct", f"{sa}-{sb}", [_int(sa), _int(sb)])
        inv["parsed"]["matches"] += 1

    names = {"a": [full_a, code_a], "b": [full_b, code_b]}
    n_maps = max(len(card["mapList"]), max([p["index"] + 1 for p in played] + [0]))
    if len(card["mapList"]) < len(played):
        # More map panels than the map list names: the card is half-edited.
        ctx.issues.append({"level": "warning", "code": "CARD_SHAPE", "sheet": name, "row": card["top"] + 1,
                           "message": f"{card['label']}: the map list names {len(card['mapList'])} maps "
                                      f"but {len(played)} map panels are filled in"})
    # (A map list LONGER than the panels is normal: a tiebreak map after a
    # draw has no panel left. Those maps carry the flag card-no-map-panel.)
    for i in range(n_maps):
        entry = card["mapList"][i] if i < len(card["mapList"]) else None
        pan = card["panels"][i] if i < len(card["panels"]) else None
        _card_map(ctx, N, name, card, m, i + 1, entry, pan, ta, tb, names, inv, ref)
    return None


def _potm(card: dict) -> str | None:
    t = _text(card.get("playerOfTheMatch", (None,))[0])
    if not t:
        return None
    return t.split(":", 1)[1].strip() if ":" in t else t


def _card_map(ctx, N, name: str, card: dict, m: dict, order: int, entry: dict | None,
              pan: dict | None, ta: Resolution, tb: Resolution, names: dict, inv: dict, ref: dict) -> None:
    L: CardLayout = ref["layout"]
    flags: list[str] = []
    if not (pan and pan["played"]):
        flags.append("card-no-map-panel")
    list_map = entry["map"] if entry else (None, None)
    pan_map = pan["map"] if pan and pan["played"] else (None, None)
    map_text, map_cell = (list_map if _text(list_map[0]) else pan_map)
    map_text = _text(map_text)
    if _text(list_map[0]) and _text(pan_map[0]) and norm(list_map[0]) != norm(pan_map[0]):
        flags.append("card-map-list-disagrees-with-panel")
    mres = ctx.aliases.map(map_text) if map_text else Resolution(None, "empty", "")
    if map_text:
        ctx.note_resolution("map", mres, name, map_cell)
    if not mres.resolved:
        flags.append("map-unmapped")
    map_id = "xmap-" + N._h(m["id"], order)
    catalog_mode = ctx.aliases.map_modes.get(mres.id or "") or None
    if catalog_mode in ("", "Unknown"):
        catalog_mode = None

    # --- result: the panel's 'X Wins!' first, then the map list's arrow.
    w_side, w_method, w_raw, w_cell = None, None, None, None
    if pan and pan["played"]:
        t = _text(pan["winner"][0])
        if t:
            mm = L.winner_re.match(t)
            w_raw, w_cell = t, pan["winner"][1]
            if norm(t) in L.draw_words:
                w_side = "draw"
            else:
                w_side = _side_of_name(ctx, N, mm.group("team") if mm else t, names, ta, tb)
            w_method = "card-winner-text" if w_side else None
            if w_side is None:
                ctx.miss("winner", t, name, w_cell)
                flags.append("winner-unresolved")
    arrow = None
    if entry:
        a_mark = _text((entry.get("wonA") or (None,))[0])
        b_mark = _text((entry.get("wonB") or (None,))[0])
        if a_mark and L.won_a in a_mark and not (b_mark and L.won_b in b_mark):
            arrow = "a"
        elif b_mark and L.won_b in b_mark and not (a_mark and L.won_a in a_mark):
            arrow = "b"
    if w_side is None and arrow:
        w_side, w_method = arrow, "card-map-list-arrow"
        w_cell = (entry.get("wonA") if arrow == "a" else entry.get("wonB"))[1]
        w_raw = L.won_a if arrow == "a" else L.won_b
    elif w_side in ("a", "b") and arrow and w_side != arrow:
        flags.append("card-winner-disagrees-with-map-list")

    # --- map score: the panel's 'Map: a-b' first, then the map list.
    sa = sb = None
    score_raw, score_cell = None, None
    if pan and pan["played"] and _text(pan["mapScore"][0]):
        mm = L.map_score_re.search(_text(pan["mapScore"][0]))
        if mm:
            sa, sb = int(mm.group("a")), int(mm.group("b"))
            score_raw, score_cell = _text(pan["mapScore"][0]), pan["mapScore"][1]
    if entry:
        la, lb = _num((entry.get("scoreA") or (None,))[0]), _num((entry.get("scoreB") or (None,))[0])
        if la is not None and lb is not None:
            if sa is None:
                sa, sb = la, lb
                score_raw, score_cell = f"{la}-{lb}", f"{entry['scoreA'][1]},{entry['scoreB'][1]}"
            elif isinstance(la, int) and isinstance(lb, int) and (sa, sb) != (la, lb):
                # (Push maps list distances, not rounds: only whole
                # numbers are compared with the panel's map score.)
                flags.append("card-score-disagrees-with-map-list")
    if w_side is None and sa is not None and sb is not None and sa != sb:
        w_side, w_method = ("a" if sa > sb else "b"), "derived-from-map-score"
    elif w_side in ("a", "b") and sa is not None and sb is not None and sa != sb and (sa > sb) != (w_side == "a"):
        flags.append("winner-disagrees-with-map-score")
    winner = {"a": ta.id, "b": tb.id}.get(w_side or "")

    rec = {
        "id": map_id, "matchId": m["id"],
        "slug": f"{m['slug']}-m{order}" + (f"-{mres.id}" if mres.id else ""),
        "date": None, "order": order, "orderMethod": "card-position",
        "map": mres.id, "mapRaw": map_text, "mapMethod": mres.method,
        "mode": catalog_mode, "modeRaw": None, "modeMethod": "catalog-from-map" if catalog_mode else None,
        "teamA": ta.id, "teamB": tb.id,
        "winner": winner, "winnerSide": w_side, "winnerRaw": w_raw,
        "winnerMethod": w_method if w_side else None,
        # The sheet does not record who picked a map. Never inferred.
        "pickedBy": None, "pickedBySide": None, "pickedByRaw": None, "pickedByMethod": None,
        "pickNote": None,
        "scoreA": sa, "scoreB": sb, "scoreRaw": score_raw,
        "vodUrl": None, "notes": None, "flags": flags,
        "src": {"sheet": name, "row": card["top"] + 1,
                "cells": {k: v for k, v in (("map", map_cell), ("winner", w_cell),
                                            ("score", score_cell)) if v}},
        "card": {"label": card["label"], "week": card["week"], "day": card["day"],
                 "game": card["game"], "panel": order, "format": m.get("format")},
        "alsoListedAt": [],
    }
    for fld, cell, method, raw, val in (("map", map_cell, mres.method, map_text, mres.id),
                                        ("winner", w_cell, w_method, w_raw, winner),
                                        ("order", None, "card-position", None, order)):
        if raw is not None or val is not None:
            ctx.prov(map_id, fld, name, cell, method or "absent", raw, val)

    bans, apps = [], []
    team_of = {"a": ta.id, "b": tb.id}
    if pan and pan["played"]:
        for side, txt_key, pic_key in (("a", "banA", "banPictureA"), ("b", "banB", "banPictureB")):
            txt, cell = pan.get(txt_key) or (None, None)
            pic, pic_cell = pan.get(pic_key) or (None, None)
            res = None
            if _text(txt) and norm(txt) in L.ban_blank:
                rec["flags"].append(f"no-ban:{side}:{norm(txt)}")
                continue
            if _text(txt):
                res = ctx.aliases.hero(_text(txt))
                ctx.note_resolution("hero", res, name, cell)
                if pic:
                    pres = _hero(ctx, ref, pic, name, pic_cell)
                    if pres and pres.id and res.id and pres.id != res.id:
                        rec["flags"].append(f"ban-text-disagrees-with-picture:{side}")
            elif pic:
                res, cell = _hero(ctx, ref, pic, name, pic_cell), pic_cell
            if res is None or res.method == "empty":
                continue
            bans.append({"id": f"xban-{map_id[5:]}-{side}-1", "mapId": map_id, "matchId": m["id"],
                         "side": side, "teamId": team_of[side], "hero": res.id, "heroRaw": res.raw,
                         "heroMethod": res.method, "order": 1, "src": {"sheet": name, "cell": cell}})
        for side in ("a", "b"):
            for pl in pan["players" + side.upper()]:
                pname, pcell = pl["name"]
                pres = ctx.aliases.player(_text(pname)) if _text(pname) else None
                if pres:
                    ctx.note_resolution("player", pres, name, pcell)
                icon_role = ref["pictures"]["roles"].get(pl["icon"] or "")
                for n, (h, hcell) in enumerate(pl["heroes"], start=1):
                    res = _hero(ctx, ref, h, name, hcell)
                    apps.append({
                        "id": f"xpa-{map_id[5:]}-{side}-{pl['position']}-{n}",
                        "mapId": map_id, "matchId": m["id"], "side": side, "teamId": team_of[side],
                        "position": pl["position"], "player": pres.id if pres else None,
                        "playerRaw": pres.raw if pres else None,
                        "playerMethod": pres.method if pres else None,
                        "listOrder": n, "hero": res.id, "heroRaw": res.raw, "heroMethod": res.method,
                        "role": ctx.aliases.role_of(res.id) or icon_role,
                        "src": {"sheet": name, "cell": hcell, "picture": h}})
    N._store_map(ctx, rec, bans, apps, [], inv, m)


# ------------------------------------------------------------ self-check
def self_check(raw: dict, cfg: dict, tabs: list[dict], aliases) -> dict | None:
    """Compare the importer's own per-tab counts with the sheet's Self-Check
    tab. Returns {'tab', 'rows': [...], 'mismatches': n} or None when the
    workbook has no Self-Check tab."""
    from .workbook import grid
    if not cfg:
        return None
    tab_re = re.compile(cfg.get("tab") or r"^self-check$", re.I)
    sheet = next((s for s in raw.get("sheets") or [] if tab_re.search(s["name"].strip())), None)
    if sheet is None:
        return None
    g, _ = grid(sheet)
    cols = {k: col(v) for k, v in (cfg.get("columns") or {}).items()}
    header_row = None
    want = {k: {norm(x) for x in v} for k, v in (cfg.get("headers") or {}).items()}
    for r in range(min(int(cfg.get("headerScanRows") or 6), len(g))):
        found = {}
        for c, v in enumerate(g[r]):
            k = norm(v)
            for fld, spellings in want.items():
                if k in spellings and fld not in found:
                    found[fld] = c
        if "maps" in found and "matches" in found:
            cols.update(found)
            header_row = r
            break
    code_res = {k: re.compile(v, re.I) for k, v in (cfg.get("regionCodes") or {}).items()}
    card_tabs = [t for t in tabs if t.get("purpose") == "match_cards"]
    rows = []
    start = (header_row + 1) if header_row is not None else 0
    fill = set(cfg.get("fillDown") or [])
    last: dict[str, str | None] = {}
    for r in range(start, len(g)):
        get = lambda k: _val(g, r, cols[k]) if k in cols else None
        maps_v, matches_v = _int(get("maps")), _int(get("matches"))
        stage_v, region_v = _text(get("stage")), _text(get("region"))
        if "stage" in fill:
            stage_v = stage_v or last.get("stage")
            last["stage"] = stage_v
        if maps_v is None and matches_v is None:
            continue
        if not region_v:
            continue
        rx = code_res.get(region_v.strip().upper())
        cands = [t for t in card_tabs if rx and rx.search(t["name"].strip())]
        if len(cands) > 1 and stage_v:
            key = aliases.stage_key(stage_v)
            narrowed = [t for t in cands if key and aliases.stage_key(t["name"]) == key]
            cands = narrowed
        row = {"row": r + 1, "stage": stage_v, "region": region_v,
               "sheet": {"matches": matches_v, "maps": maps_v},
               "tabs": [t["name"] for t in cands], "parsed": None, "agree": None}
        if len(cands) == 1:
            t = cands[0]
            row["parsed"] = {"matches": t["parsed"]["matches"], "maps": t["parsed"]["maps"]}
            row["agree"] = ((matches_v is None or matches_v == row["parsed"]["matches"]) and
                            (maps_v is None or maps_v == row["parsed"]["maps"]))
        rows.append(row)
    return {"tab": sheet["name"], "headerRow": None if header_row is None else header_row + 1,
            "rows": rows,
            "compared": sum(1 for x in rows if x["agree"] is not None),
            "mismatches": sum(1 for x in rows if x["agree"] is False),
            "unpaired": sum(1 for x in rows if x["agree"] is None)}
