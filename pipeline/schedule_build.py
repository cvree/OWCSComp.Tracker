#!/usr/bin/env python3
"""
schedule_build.py — the season schedule behind the Matches page.

Writes assets/data/schedule.v1.js (window.OWCS_SCHEDULE): every event of the
season, every series in it (played or not), the bracket of every playoff and
the standings of every league phase. series.html renders it as Upcoming,
Schedule, Brackets and Results.

Where it comes from, joined here and nowhere else:

  * PLAYED series: assets/data/external_series.v1.js (the spreadsheet import),
    with their scores, winners and links to the full series page;
  * SCHEDULED series: the spreadsheet's own match cards that have no maps
    yet, read from the committed raw cell layer (data/external/gsheet/raw).
    The importer counts them and moves on; here they are the future: each
    has its round ('Semifinals [Bo7]'), its order (Week | Day | Game) and,
    once the editors fill it in, its two teams;
  * DATES: config/external/sheet_schedule.json (per week), refined by
    config/schedule.json (`phaseDates`, a published day for a phase);
  * LIQUIPEDIA: data/external/liquipedia/matches.json, written on a GitHub
    runner by pipeline/liquipedia_schedule.py — exact start times, the teams
    of TBD slots, streams, and events the sheet has no tab for yet;
  * OVERRIDES: config/schedule.json `matches`, typed in by a person.

Nothing is invented. A match with no known time has startsAt = null and the
page says 'time TBA'; a date that is an estimate says so in `dateFrom`; a
slot with no teams has teamA = teamB = null and reads TBD.

Usage:
  python3 pipeline/schedule_build.py           # write the export
  python3 pipeline/schedule_build.py --check   # exit 1 if it would change
  python3 pipeline/schedule_build.py --print   # summary to stdout, no write
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import gzip
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

OUT = os.path.join(ROOT, "assets", "data", "schedule.v1.js")
SERIES_JS = os.path.join(ROOT, "assets", "data", "external_series.v1.js")
RAW_SHEETS = os.path.join(ROOT, "data", "external", "gsheet", "raw", "sheets")
LAYOUT = os.path.join(ROOT, "config", "external", "gsheet_layout.json")
SHEET_SCHEDULE = os.path.join(ROOT, "config", "external", "sheet_schedule.json")
CONFIG = os.path.join(ROOT, "config", "schedule.json")
LIQUIPEDIA = os.path.join(ROOT, "data", "external", "liquipedia", "matches.json")

REGIONS = {"north america": "na", "na": "na", "emea": "emea", "korea": "korea", "japan": "japan",
           "china": "china", "pacific": "pacific", "asia": "asia"}
INTERNATIONAL = (("champions clash", "champions-clash", "Champions Clash"),
                 ("midseason", "midseason-championship", "Midseason Championship"),
                 ("world finals", "world-finals", "World Finals"),
                 ("pre-season", "pre-season", "Pre-Season Bootcamp"),
                 ("pre season", "pre-season", "Pre-Season Bootcamp"))
REGION_NAME = {"na": "North America", "emea": "EMEA", "korea": "Korea", "japan": "Japan", "china": "China",
               "pacific": "Pacific", "asia": "Asia", "international": "International"}
STAGE_ORDER = ["pre-season", "stage-1", "champions-clash", "stage-2", "midseason-championship",
               "stage-3", "world-finals"]
REGION_ORDER = ["na", "emea", "korea", "japan", "china", "pacific", "asia", "international"]


# ------------------------------------------------------------------ io
def read_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read_js_object(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            s = f.read()
    except OSError:
        return None
    return json.loads(s[s.index("{"):s.rindex("}") + 1])


def _h(*parts) -> str:
    return hashlib.sha1("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:10]


# ------------------------------------------------------- tab -> event key
def tab_event(tab: str) -> dict:
    """'Korea - Stage 3' -> {region: korea, stage: stage-3, key: korea/stage-3};
    'Midseason Championship Paris' -> international/midseason-championship."""
    t = " ".join(tab.lower().replace("–", "-").split())
    for needle, stage, name in INTERNATIONAL:
        if needle in t:
            region = None if stage == "pre-season" else "international"
            city = tab.split()[-1] if stage not in ("pre-season",) and len(tab.split()) > 2 else None
            return {"region": region, "stage": stage, "key": f"{region or stage}/{stage}",
                    "name": name, "city": city}
    m = re.match(r"^(.*?)\s*-\s*stage\s*(\d+)", t)
    if m and m.group(1).strip() in REGIONS:
        region = REGIONS[m.group(1).strip()]
        stage = f"stage-{m.group(2)}"
        return {"region": region, "stage": stage, "key": f"{region}/{stage}",
                "name": f"{REGION_NAME[region]} · Stage {m.group(2)}", "city": None}
    slug = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return {"region": None, "stage": slug, "key": f"{slug}/{slug}", "name": tab, "city": None}


# --------------------------------------------------------- round naming
BO_RE = re.compile(r"\[\s*bo\s*(\d+)\s*\]", re.I)


def classify(fmt: str | None) -> dict:
    """A card's format text -> phase, bracket side and round.

    'Round Robin [Bo5]'                 -> phase regular (a league)
    'Swiss Stage (Round 2) [Bo5]'       -> phase regular, round 'Swiss round 2'
    'Seeding Decider [Bo5]'             -> phase seeding (a list)
    'Last Chance Qualifier [Bo5]'       -> phase lcq (a list)
    'Group B Upper Quarterfinals [Bo3]' -> phase group-b, side upper, Quarterfinals
    'Lower Finals [Bo5]'                -> phase playoffs, side lower, Finals
    'Third Place Match [Bo7]'           -> phase playoffs, side third
    'Grand Finals [Bo7]'                -> phase playoffs, side final
    """
    raw = (fmt or "").strip()
    bo = BO_RE.search(raw)
    best_of = int(bo.group(1)) if bo else None
    name = " ".join(BO_RE.sub("", raw).split())
    low = name.lower()
    out = {"bestOf": best_of, "roundRaw": name or None, "phase": "regular", "kind": "league",
           "side": None, "round": name or "Match", "depth": 0}
    if not low:
        out["kind"] = "list"
        out["phase"] = "other"
        return out
    if "swiss" in low:
        sw = re.search(r"round\s*(\d+)", low)
        out.update(phase="swiss", round=("Swiss round " + sw.group(1)) if sw else "Swiss stage")
        return out
    if "group stage" in low:
        out.update(phase="groups", round="Group stage")
        return out
    if "round robin" in low or "regular" in low:
        out["round"] = "Round robin"
        return out
    if "seeding" in low:
        out.update(phase="seeding", kind="list", round="Seeding decider")
        return out
    if "last chance" in low or low == "lcq":
        out.update(phase="lcq", kind="list", round="Last Chance Qualifier")
        return out
    grp = re.match(r"^group\s+([a-z])\s+(.*)$", low)
    phase = "playoffs"
    rest = low
    if grp:
        phase = "group-" + grp.group(1)
        rest = grp.group(2)
    rest = re.sub(r"^playoffs?\s+", "", rest)
    side = "main"
    if rest.startswith("upper "):
        side, rest = "upper", rest[6:]
    elif rest.startswith("lower "):
        side, rest = "lower", rest[6:]
    if "third" in rest or "3rd" in rest:
        out.update(phase=phase, kind="bracket", side="third", round="Third place", depth=90)
        return out
    if "grand final" in rest:
        out.update(phase=phase, kind="bracket", side="final", round="Grand Final", depth=100)
        return out
    if "quarter" in rest:
        rnd, depth = "Quarterfinals", 2
    elif "semi" in rest:
        rnd, depth = "Semifinals", 3
    elif re.search(r"\bfinals?\b", rest):
        rnd, depth = "Final", 4
    else:
        r1 = re.search(r"round\s*(\d+)", rest)
        rnd, depth = (f"Round {r1.group(1)}", int(r1.group(1)) * 0.5) if r1 else (rest.title() or "Match", 1)
    if side == "upper":
        rnd = "Upper " + rnd
    elif side == "lower":
        rnd = "Lower " + rnd
    out.update(phase=phase, kind="bracket", side=side, round=rnd, depth=depth)
    return out


PHASE_NAME = {"regular": "Round robin", "swiss": "Swiss stage", "groups": "Group stage",
              "seeding": "Seeding deciders", "lcq": "Last Chance Qualifier",
              "playoffs": "Playoffs", "other": "Other series"}


def phase_name(pid: str) -> str:
    if pid.startswith("group-"):
        return "Group " + pid[6:].upper()
    return PHASE_NAME.get(pid, pid.title())


# --------------------------------------------------------------- dates
def estimate_date(region: str | None, stage: str, week, day, schedule: dict) -> tuple[str | None, str | None]:
    """Same rule as pipeline/external_sheet/tiers.py (one schedule file, one
    reading): Week N | Day D -> weeks[N] + (D-1) days; LAN days by `days`."""
    from external_sheet.tiers import estimate_date as tiers_estimate
    return tiers_estimate({"region": region, "stage": stage, "week": week, "day": day}, schedule)


# --------------------------------------------------- scheduled raw cards
def scheduled_cards(aliases) -> dict[str, list[dict]]:
    """{tab name: [card summary]} for every card in the committed raw layer,
    in sheet order. A summary carries the label, format, both teams as far
    as the card names them, and whether it has a map list."""
    from external_sheet import cards as C
    from external_sheet.workbook import grid
    layout = read_json(LAYOUT, {}) or {}
    L = C.CardLayout(layout.get("matchCards") or {})
    out: dict[str, list[dict]] = {}
    for f in sorted(glob.glob(os.path.join(RAW_SHEETS, "*.json.gz"))):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            sheet = json.load(fh)
        g, origin = grid(sheet)
        blocks = C.find_blocks(g, L)
        if not blocks:
            continue
        imgs = C.images_by_cell(sheet)
        rows = []
        for b, label in blocks:
            card = C.read_card(g, origin, imgs, b, label, L)
            named = [p for p in card["panels"] if p["teamsNamed"]]
            full_a = next((C._strip_team(L, p["teamA"][0]) for p in named if C._strip_team(L, p["teamA"][0])), None)
            full_b = next((C._strip_team(L, p["teamB"][0]) for p in named if C._strip_team(L, p["teamB"][0])), None)
            code_a = C._text(card.get("teamACode", (None,))[0])
            code_b = C._text(card.get("teamBCode", (None,))[0])
            real = [e for e in card["mapList"] if C.norm(e["map"][0]) not in L.map_forfeit]
            rows.append({
                "label": card["label"], "week": card["week"], "day": card["day"], "game": card["game"],
                "row": b + 1, "event": C._text(card.get("event", (None,))[0]),
                "format": C._text(card.get("format", (None,))[0]),
                "fullA": full_a, "fullB": full_b, "codeA": code_a, "codeB": code_b,
                "scoreA": C._int(card.get("seriesA", (None,))[0]), "scoreB": C._int(card.get("seriesB", (None,))[0]),
                "maps": len(real), "forfeit": len(card["mapList"]) > len(real),
                "played": any(p["played"] for p in card["panels"]),
            })
        out[sheet["name"]] = rows
    return out


# ------------------------------------------------------------ team ids
class Teams:
    """Resolve a name or code to a team id the site knows, else keep the
    name as printed. A code ('TGG') resolves through the codes the played
    cards pair with full names, so a scheduled card that only carries codes
    still shows the team's real name and logo."""

    def __init__(self, aliases, known: dict):
        self.aliases = aliases
        self.known = known           # id -> name, from the series export + registry
        self.codes: dict[str, str] = {}

    def learn_code(self, code: str | None, team_id: str | None) -> None:
        if code and team_id:
            self.codes.setdefault(code.strip().upper(), team_id)

    def resolve(self, name: str | None, code: str | None = None) -> tuple[str | None, str | None]:
        """(team id or None, the printed name or None)."""
        for raw in (name, code):
            if not raw:
                continue
            if raw.strip().upper() in self.codes and (raw == code or len(raw.strip()) <= 4):
                return self.codes[raw.strip().upper()], name or raw
            if self.aliases is not None:
                res = self.aliases.team(raw, None)
                if res.id and (res.method != "slug" or res.id in self.known):
                    return res.id, name or raw
        if code and code.strip().upper() in self.codes:
            return self.codes[code.strip().upper()], name or code
        printed = name or code
        return None, printed


def load_aliases():
    try:
        from external_sheet.store import Paths
        from external_sheet.sync import Bundle
        return Bundle.load(Paths()).aliases()
    except Exception as exc:  # the schedule must still build without the alias book
        print(f"[schedule] alias book unavailable ({type(exc).__name__}: {exc}); names stay as printed",
              file=sys.stderr)
        return None


# ---------------------------------------------------------------- build
def build(now: dt.datetime | None = None) -> dict:
    series_doc = read_js_object(SERIES_JS) or {"series": []}
    sched = read_json(SHEET_SCHEDULE, {}) or {}
    cfg = read_json(CONFIG, {}) or {}
    lp = read_json(LIQUIPEDIA, None)
    aliases = load_aliases()

    known = {}
    for s in series_doc.get("series") or []:
        for t in (s.get("teamA"), s.get("teamB")):
            if t:
                known[t] = t
    teams = Teams(aliases, known)

    cards = scheduled_cards(aliases) if os.path.isdir(RAW_SHEETS) else {}

    # Learn team codes from played cards (code -> resolved id).
    for tab, rows in cards.items():
        for c in rows:
            if c["fullA"] and c["codeA"]:
                tid, _ = teams.resolve(c["fullA"])
                teams.learn_code(c["codeA"], tid)
            if c["fullB"] and c["codeB"]:
                tid, _ = teams.resolve(c["fullB"])
                teams.learn_code(c["codeB"], tid)

    events: dict[str, dict] = {}
    matches: list[dict] = []

    def event_for(tab: str) -> dict:
        info = tab_event(tab)
        ev = events.get(info["key"])
        if ev is None:
            stage_cfg = (cfg.get("stages") or {}).get(info["key"]) or {}
            sch = (sched.get("stages") or {}).get(info["key"]) or {}
            ev = {"id": info["key"], "tab": tab, "name": info["name"], "region": info["region"],
                  "stage": info["stage"], "city": info["city"],
                  "location": stage_cfg.get("location"), "format": stage_cfg.get("format"),
                  "window": list(stage_cfg.get("window") or [None, None]),
                  "windowFrom": "config" if stage_cfg.get("window") else None,
                  "source": stage_cfg.get("source") or sch.get("source"),
                  "dateConfidence": sch.get("confidence")}
            events[info["key"]] = ev
        return ev

    # Played series, from the import.
    by_tab_label: dict[tuple, list[dict]] = {}
    for s in series_doc.get("series") or []:
        ev = event_for(s.get("stageRaw") or "")
        cl = classify(s.get("format"))
        m = {
            "id": s["id"], "event": ev["id"], "label": s.get("label"), "week": s.get("week"),
            "day": s.get("day"), "game": s.get("game"), "format": s.get("format"),
            "bestOf": s.get("bestOf") or cl["bestOf"], "phase": cl["phase"], "kind": cl["kind"],
            "side": cl["side"], "round": cl["round"], "depth": cl["depth"],
            "teamA": s.get("teamA"), "teamB": s.get("teamB"), "nameA": None, "nameB": None,
            "scoreA": s.get("scoreA"), "scoreB": s.get("scoreB"), "winner": s.get("winner"),
            "state": "done", "series": s["id"], "complete": s.get("complete", True),
            "row": (s.get("src") or {}).get("row"), "source": "sheet",
        }
        matches.append(m)
        by_tab_label.setdefault((s.get("stageRaw"), s.get("label")), []).append(m)

    # Cards the import did not turn into a series: the future (and a few
    # past cards it could not use, kept as results without map detail).
    for tab, rows in cards.items():
        ev = event_for(tab)
        nth: dict[str, int] = {}
        for c in rows:
            nth[c["label"]] = nth.get(c["label"], 0) + 1
            ta, na = teams.resolve(c["fullA"], c["codeA"])
            tb, nb = teams.resolve(c["fullB"], c["codeB"])
            done = by_tab_label.get((tab, c["label"])) or []
            hit = next((m for m in done if {m["teamA"], m["teamB"]} == {ta, tb} and ta and tb), None)
            if hit is None and done and not (ta and tb):
                hit = next((m for m in done if not m.get("_card")), None)
            if hit is not None:
                hit["_card"] = True
                hit["nth"] = nth[c["label"]]
                if not hit.get("row"):
                    hit["row"] = c["row"]
                continue
            cl = classify(c["format"])
            has_result = bool(ta and tb and (c["maps"] or c["forfeit"]) and c["scoreA"] is not None
                              and c["scoreB"] is not None and (c["scoreA"] or c["scoreB"]))
            winner = None
            if has_result and c["scoreA"] != c["scoreB"]:
                winner = ta if c["scoreA"] > c["scoreB"] else tb
            matches.append({
                "id": "xs-" + _h(tab, c["label"], nth[c["label"]]), "event": ev["id"], "label": c["label"],
                "nth": nth[c["label"]], "week": c["week"], "day": c["day"], "game": c["game"],
                "format": c["format"], "bestOf": cl["bestOf"], "phase": cl["phase"], "kind": cl["kind"],
                "side": cl["side"], "round": cl["round"], "depth": cl["depth"],
                "teamA": ta, "teamB": tb, "nameA": None if ta else na, "nameB": None if tb else nb,
                "codeA": c["codeA"], "codeB": c["codeB"],
                "scoreA": c["scoreA"] if has_result else None, "scoreB": c["scoreB"] if has_result else None,
                "winner": winner, "state": "done" if has_result else "scheduled", "series": None,
                "row": c["row"], "source": "sheet",
                "note": (("Decided by forfeit." if c["forfeit"] and not c["maps"] else
                          "Result from the card's series score; the editors did not fill in its maps.")
                         if has_result else None),
            })

    # Extra events the sheet has no tab for.
    for x in cfg.get("extraEvents") or []:
        if x["key"] in events:
            ev = events[x["key"]]
            for k in ("location", "format", "source"):
                ev[k] = ev.get(k) or x.get(k)
            if x.get("window"):
                ev["window"], ev["windowFrom"] = list(x["window"]), "config"
            continue
        events[x["key"]] = {"id": x["key"], "tab": None, "name": x.get("name") or x["key"],
                            "region": x.get("region"), "stage": x.get("stage"), "city": None,
                            "location": x.get("location"), "format": x.get("format"),
                            "window": list(x.get("window") or [None, None]), "windowFrom": "config",
                            "source": x.get("source"), "dateConfidence": "window"}

    # Dates, before Liquipedia is joined on: a slot's published day is how a
    # Liquipedia match finds the slot it belongs to, and how it is kept out
    # of a slot on another day.
    assign_dates(matches, events, cfg, sched)

    # Liquipedia: times, teams, streams; new matches for events with no cards.
    lp_meta = merge_liquipedia(lp, matches, events, teams) if lp else None

    # Hand overrides.
    apply_overrides(cfg.get("matches") or [], matches, events, teams)

    # Dates again, for what the join and the overrides gave a time.
    assign_dates(matches, events, cfg, sched)

    # Events: windows, phases, brackets, standings, champion.
    by_event: dict[str, list[dict]] = {}
    for m in matches:
        by_event.setdefault(m["event"], []).append(m)
    for ev in events.values():
        ms = sorted(by_event.get(ev["id"], []), key=order_key)
        dates = sorted(m["date"] for m in ms if m.get("date"))
        if ev["window"][0] is None and dates:
            ev["window"][0] = dates[0]
        if ev["window"][1] is None and dates:
            ev["window"][1] = dates[-1]
        if dates and ev["window"][1] and dates[-1] > ev["window"][1]:
            ev["window"][1] = dates[-1]
        ev["counts"] = {"matches": len(ms), "done": sum(m["state"] == "done" for m in ms),
                        "scheduled": sum(m["state"] != "done" for m in ms),
                        "tbd": sum(m["state"] != "done" and not (m["teamA"] or m["nameA"]) for m in ms)}
        ev["phases"] = build_phases(ms)
        gf = [m for m in ms if m["side"] == "final" and m["phase"] == "playoffs" and m["winner"]]
        if not gf:
            gf = [m for m in ms if m["side"] == "final" and m["winner"]]
        ev["champion"] = gf[-1]["winner"] if gf else None
        ev["runnerUp"] = None
        if gf:
            g = gf[-1]
            ev["runnerUp"] = g["teamB"] if g["winner"] == g["teamA"] else g["teamA"]

    for m in matches:
        m.pop("_card", None)
        m.pop("depth", None)

    ev_list = sorted(events.values(), key=lambda e: (
        e["window"][0] or "9999", STAGE_ORDER.index(e["stage"]) if e["stage"] in STAGE_ORDER else 99,
        REGION_ORDER.index(e["region"]) if e["region"] in REGION_ORDER else 99))
    matches.sort(key=lambda m: (m.get("date") or "9999", m["event"], order_key(m)))

    return {
        "meta": {"schema": "schedule.v1",
                 "generatedFrom": {"series": (series_doc.get("meta") or {}).get("sha256"),
                                   "liquipedia": lp_meta},
                 "note": "Dates marked estimate are placed from the stage's published weeks; "
                         "published-day means the day is published and the time is not; "
                         "liquipedia/official carry a start time."},
        "events": ev_list,
        "matches": matches,
        "sources": sources(lp_meta),
    }


def assign_dates(matches: list[dict], events: dict, cfg: dict, sched: dict) -> None:
    """Give every match its date: its start time's day, a day a source gave,
    the stage's published day for its slot, or an estimate that says so.
    Safe to run twice: a dated match keeps its date and where it came from."""
    for m in matches:
        ev = events[m["event"]]
        if m.get("startsAt"):
            m["date"] = local_date(m["startsAt"])
            m["dateFrom"] = m.get("dateFrom") or "liquipedia"
            continue
        if m.get("date"):
            m.setdefault("dateFrom", "liquipedia-day")
            continue
        pd = (((cfg.get("stages") or {}).get(m["event"]) or {}).get("phaseDates") or {}).get(m["phase"]) or {}
        if m.get("day") is not None and str(m["day"]) in pd:
            m["date"], m["dateFrom"] = pd[str(m["day"])], "published-day"
            continue
        if ev.get("windowFrom") == "config" and ev["window"][0] is None and m["state"] == "scheduled":
            m["date"], m["dateFrom"] = None, None    # the stage's days are not published
            continue
        d, conf = estimate_date(ev.get("region"), ev.get("stage"), m.get("week"), m.get("day"), sched)
        m["date"] = d
        m["dateFrom"] = None if d is None else ("published-day" if conf == "window" else "estimate")


def order_key(m: dict):
    return (m.get("week") or 0, m.get("day") or 0, m.get("game") or 0, m.get("nth") or 0, m["id"])


def local_date(iso: str) -> str:
    """The calendar date of a start time, in the time zone it was given in
    (a Seoul match at 01:00 KST is that day in Seoul)."""
    return iso[:10]


# ------------------------------------------------------------- phases
def build_phases(ms: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for m in ms:
        groups.setdefault(m["phase"], []).append(m)

    def rank(pid):
        # In the order the season plays them; the playoffs always last.
        first = min(order_key(m)[:4] for m in groups[pid])
        return (1 if pid == "playoffs" else 0, first, pid)
    out = []
    for pid in sorted(groups, key=rank):
        pm = groups[pid]
        kind = "bracket" if any(m["kind"] == "bracket" for m in pm) else pm[0]["kind"]
        ph = {"id": pid, "name": phase_name(pid), "kind": kind,
              "matches": [m["id"] for m in pm],
              "done": all(m["state"] == "done" for m in pm)}
        if kind == "league":
            ph["standings"] = standings(pm)
            ph["rounds"] = sorted({m["round"] for m in pm})
        if kind == "bracket":
            ph["bracket"] = bracket(pm)
        out.append(ph)
    return out


def standings(pm: list[dict]) -> list[dict]:
    rows: dict[str, dict] = {}
    for m in pm:
        for side in ("A", "B"):
            tid = m["team" + side]
            if tid:
                rows.setdefault(tid, {"team": tid, "w": 0, "l": 0, "mw": 0, "ml": 0, "left": 0})
        if m["state"] != "done" or not (m["teamA"] and m["teamB"]):
            for side in ("A", "B"):
                if m["team" + side]:
                    rows[m["team" + side]]["left"] += 1
            continue
        a, b = m["scoreA"] or 0, m["scoreB"] or 0
        ra, rb = rows[m["teamA"]], rows[m["teamB"]]
        ra["mw"] += a; ra["ml"] += b; rb["mw"] += b; rb["ml"] += a
        if m["winner"] == m["teamA"]:
            ra["w"] += 1; rb["l"] += 1
        elif m["winner"] == m["teamB"]:
            rb["w"] += 1; ra["l"] += 1
    table = sorted(rows.values(), key=lambda r: (-r["w"], r["l"], -(r["mw"] - r["ml"]), -r["mw"], r["team"]))
    for i, r in enumerate(table):
        r["rank"] = i + 1
        r["diff"] = r["mw"] - r["ml"]
    return table


def bracket(pm: list[dict]) -> dict:
    """Columns of rounds per side, each round's series ordered so a winner
    sits beside the series it advanced to (the order the bracket is drawn
    in). Upper/main first, lower beneath, then the final and third place."""
    sides: dict[str, dict[str, list[dict]]] = {}
    depth: dict[tuple, float] = {}
    for m in pm:
        if m["kind"] != "bracket":
            continue
        side = m["side"] or "main"
        if side in ("final", "third"):
            sides.setdefault(side, {}).setdefault(m["round"], []).append(m)
            continue
        sides.setdefault(side, {}).setdefault(m["round"], []).append(m)
        depth[(side, m["round"])] = m.get("depth") or 0

    def cols(side: str) -> list[dict]:
        rounds = sorted(sides.get(side, {}), key=lambda r: depth.get((side, r), 0))
        columns = [sorted(sides[side][r], key=order_key) for r in rounds]
        # Align from the last round back: a series sits in the order of the
        # next-round series its winner played in.
        for j in range(len(columns) - 2, -1, -1):
            nxt = columns[j + 1]
            pos = {}
            for i, n in enumerate(nxt):
                for t in (n["teamA"], n["teamB"]):
                    if t:
                        pos.setdefault(t, i)
            columns[j] = sorted(columns[j], key=lambda m: (pos.get(m["winner"], 99) if m["winner"] else 99,
                                                           order_key(m)))
        return [{"round": r, "matches": [m["id"] for m in c],
                 "bestOf": next((m["bestOf"] for m in c if m["bestOf"]), None)} for r, c in zip(rounds, columns)]
    out = {}
    for side in ("main", "upper", "lower"):
        if side in sides:
            out[side] = cols(side)
    for side in ("final", "third"):
        if side in sides:
            ms = [m for r in sides[side].values() for m in r]
            out[side] = [m["id"] for m in sorted(ms, key=order_key)]
    out["type"] = "double" if "lower" in out else "single"
    return out


# --------------------------------------------------------- liquipedia
def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


#: A Liquipedia section header that names a day, not a round: regular
#: seasons are listed under "October 3rd", "Week 2", "Day 1". Read as a
#: round, "October 3rd" is a third-place match.
DATEISH_RE = re.compile(r"(?i)^\s*(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}"
                        r"(?:st|nd|rd|th)?\b|(?:day|week)\s*\d+\b|\d{4}-\d{2}-\d{2})")


def lp_round(x: dict) -> str | None:
    r = (x.get("round") or "").strip()
    return None if not r or DATEISH_RE.match(r) else r


def lp_day(x: dict) -> str | None:
    return x.get("date") or (local_date(x["startsAt"]) if x.get("startsAt") else None)


def merge_liquipedia(lp: dict, matches: list[dict], events: dict, teams: Teams) -> dict:
    """Join Liquipedia's matches onto the sheet's.

    In an event the sheet has cards for, Liquipedia only fills them in — it
    never adds a match there, because the sheet is the record of the stage
    and a Liquipedia page also lists every series already played (often
    without a finished flag the parser can read). A match joins, in order:
    the sheet series of the same pair; else the TBD slot of the same round;
    else the first TBD slot on the same day. A slot with a day only takes a
    match on that day. In an event the sheet does not cover, Liquipedia's
    matches become the event's matches."""
    used = set()
    added = 0
    joined = 0
    dropped = 0
    by_event: dict[str, list[dict]] = {}
    for m in matches:
        by_event.setdefault(m["event"], []).append(m)
    for x in sorted(lp.get("matches") or [], key=lambda x: (x.get("startsAt") or "9999", x.get("order") or 0)):
        key = x.get("event")
        if not key:
            continue
        ta, na = teams.resolve(x.get("teamA"))
        tb, nb = teams.resolve(x.get("teamB"))
        rnd = lp_round(x)
        day = lp_day(x)
        cl = classify(rnd or x.get("section") or "")
        pool = by_event.get(key, [])
        covered = any(m.get("source") == "sheet" for m in pool)
        hit = None

        def same_day(m: dict) -> bool:
            return not m.get("date") or not day or m["date"] == day
        if (ta or na) and (tb or nb):
            # The same pair, in the same round when Liquipedia names one —
            # two teams can meet three times in a stage. A series Liquipedia
            # has not finished only joins one the sheet has not played.
            want = {ta or _norm(na), tb or _norm(nb)}
            same = [m for m in pool if m["id"] not in used and
                    {m["teamA"] or _norm(m.get("nameA")), m["teamB"] or _norm(m.get("nameB"))} == want
                    and (x.get("finished") or m["state"] != "done")]
            if rnd:
                same = [m for m in same if m["round"] == cl["round"]] or \
                       [m for m in same if m["phase"] == cl["phase"]]
            hit = same[0] if same else None
        if hit is None and rnd:
            hit = next((m for m in sorted(pool, key=order_key) if m["id"] not in used and m["state"] != "done"
                        and m["round"] == cl["round"] and m["phase"] == cl["phase"] and same_day(m)
                        and not (m["teamA"] or m.get("nameA"))), None)
        if hit is None and covered and day and not x.get("finished"):
            # Its round says nothing the sheet knows ("Regional Playoffs"):
            # the first empty slot on its day, in the sheet's order. Matches
            # are taken in start order, so a day's slots fill in order.
            hit = next((m for m in sorted(pool, key=order_key) if m["id"] not in used and m["state"] != "done"
                        and m.get("date") == day and m["dateFrom"] in ("published-day", "official")
                        and not (m["teamA"] or m.get("nameA") or m["teamB"] or m.get("nameB"))), None)
        if hit is not None:
            used.add(hit["id"])
            joined += 1
            if x.get("startsAt"):
                hit["startsAt"], hit["dateFrom"] = x["startsAt"], "liquipedia"
            elif x.get("date"):
                hit["date"], hit["dateFrom"] = x["date"], "liquipedia-day"
            for side, tid, nm in (("A", ta, na), ("B", tb, nb)):
                if not hit["team" + side] and not hit.get("name" + side) and (tid or nm):
                    hit["team" + side], hit["name" + side] = tid, None if tid else nm
            if x.get("streams"):
                hit["streams"] = x["streams"]
            if x.get("url"):
                hit["wiki"] = x["url"]
            if hit["state"] != "done" and x.get("finished") and x.get("scoreA") is not None:
                hit["scoreA"], hit["scoreB"] = x["scoreA"], x["scoreB"]
                hit["state"] = "done"
                hit["winner"] = (hit["teamA"] if x["scoreA"] > x["scoreB"] else hit["teamB"]) \
                    if x["scoreA"] != x["scoreB"] else None
                hit["source"] = "liquipedia"
            continue
        if key not in events:
            continue          # an event this site does not track
        if covered:
            dropped += 1      # the sheet is the record of this stage
            continue
        added += 1
        mid = "xl-" + _h(key, x.get("startsAt"), x.get("teamA"), x.get("teamB"), x.get("order"))
        done = bool(x.get("finished") and x.get("scoreA") is not None)
        winner = None
        if done and x["scoreA"] != x["scoreB"]:
            winner = (ta or None) if x["scoreA"] > x["scoreB"] else (tb or None)
        m = {"id": mid, "event": key, "label": x.get("section"), "nth": None, "week": None, "day": None,
             "game": x.get("order"), "format": x.get("round"), "bestOf": x.get("bestOf") or cl["bestOf"],
             "phase": cl["phase"], "kind": cl["kind"] if x.get("round") else "list", "side": cl["side"],
             "round": cl["round"] if x.get("round") else (x.get("section") or "Match"), "depth": cl["depth"],
             "teamA": ta, "teamB": tb, "nameA": None if ta else na, "nameB": None if tb else nb,
             "scoreA": x.get("scoreA") if done else None, "scoreB": x.get("scoreB") if done else None,
             "winner": winner, "state": "done" if done else "scheduled", "series": None, "row": None,
             "source": "liquipedia", "startsAt": x.get("startsAt"), "date": x.get("date"),
             "dateFrom": "liquipedia" if x.get("startsAt") else ("liquipedia-day" if x.get("date") else None),
             "streams": x.get("streams") or None, "wiki": x.get("url")}
        matches.append(m)
        by_event.setdefault(key, []).append(m)
    return {"fetchedAt": lp.get("fetchedAt"), "pages": len(lp.get("pages") or []),
            "matches": len(lp.get("matches") or []), "joined": joined, "added": added,
            "unjoined": dropped}


def apply_overrides(rows: list[dict], matches: list[dict], events: dict, teams: Teams) -> None:
    for o in rows:
        cands = [m for m in matches if m["event"] == o.get("stage")
                 and (not o.get("label") or m.get("label") == o["label"])
                 and (not o.get("round") or m["round"] == o["round"])]
        if o.get("nth"):
            cands = [m for m in cands if (m.get("nth") or 1) == o["nth"]] or cands[o["nth"] - 1:o["nth"]]
        if not cands:
            print(f"[schedule] override matched nothing: {o}", file=sys.stderr)
            continue
        m = cands[0]
        if o.get("startsAt"):
            m["startsAt"], m["dateFrom"] = o["startsAt"], "official"
        for side in ("A", "B"):
            v = o.get("team" + side)
            if v:
                tid, nm = teams.resolve(v)
                m["team" + side], m["name" + side] = tid, None if tid else nm
        for k in ("stream", "vod"):
            if o.get(k):
                m.setdefault("streams", [])
                m["streams"] = (m["streams"] or []) + [{"kind": k, "url": o[k]}]
        m["override"] = True


def sources(lp_meta: dict | None) -> list[dict]:
    out = [{"id": "sheet", "name": "The community OWCS spreadsheet",
            "what": "Every series, its round and format, and the result of every played map"},
           {"id": "schedule", "name": "Published stage dates",
            "what": "Blizzard's viewers' guides, Liquipedia, esports.gg, owtics.gg and Strafe, gathered by hand "
                    "into config/external/sheet_schedule.json and config/schedule.json"}]
    if lp_meta:
        out.append({"id": "liquipedia", "name": "Liquipedia", "url": "https://liquipedia.net/overwatch/",
                    "license": "CC BY-SA 3.0", "fetchedAt": lp_meta.get("fetchedAt"),
                    "what": "Start times, teams of undecided slots, and streams"})
    return out


# ----------------------------------------------------------------- write
def render(doc: dict) -> str:
    body = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return ("/* =====================================================================\n"
            "   OWCS Comp Tracker — schedule.v1.js  (GENERATED by pipeline/schedule_build.py)\n"
            "   The season: every event, every series played or still to play, every\n"
            "   bracket and standings table. Do not hand-edit: change config/schedule.json\n"
            "   or the imports it reads, then rebuild.\n"
            "   ===================================================================== */\n"
            "window.OWCS_SCHEDULE = " + body + ";\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--check", action="store_true", help="exit 1 if the export would change")
    ap.add_argument("--print", action="store_true", help="print a summary, write nothing")
    args = ap.parse_args(argv)
    doc = build()
    text = render(doc)
    if args.print:
        for ev in doc["events"]:
            c = ev["counts"]
            print(f"{ev['id']:<38} {str(ev['window']):<28} {c['done']:>3} played {c['scheduled']:>3} to play"
                  f"  phases: {', '.join(p['name'] for p in ev['phases'])}")
        return 0
    old = None
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            old = f.read()
    if args.check:
        if old != text:
            print("[schedule] assets/data/schedule.v1.js is not what the committed inputs produce; "
                  "run python3 pipeline/schedule_build.py", file=sys.stderr)
            return 1
        print("[schedule] export matches a fresh build")
        return 0
    if old != text:
        with open(OUT, "w", encoding="utf-8") as f:
            f.write(text)
    up = sum(1 for m in doc["matches"] if m["state"] != "done")
    print(f"[schedule] {len(doc['events'])} events, {len(doc['matches'])} series ({up} still to play)"
          + (" — unchanged" if old == text else " — written"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
