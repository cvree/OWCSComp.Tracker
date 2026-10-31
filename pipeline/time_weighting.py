#!/usr/bin/env python3
"""
time_weighting.py — hero player-seconds from VOD-verified timelines.

The public export has always carried the hero-stint timeline itself
(`heroStints`: slot 5 of Al Qadsiah was Juno from 1815 to 2393, then Lúcio)
and the round windows the round-emblem detector found (`maps[].rounds`).
`mapstory.js` already DRAWS those as lanes and sums composition "eras" in
the browser, but every aggregate the exporter computed counted
*appearances* — one per (map, team) — so a hero swapped in for the last
thirty seconds of a map weighed exactly as much as one played for all of it.

This module turns the same published rows into SECONDS. It does not create a
second timeline model: its only inputs are the exported `heroStints`,
`heroSwaps` and `maps[].rounds`, i.e. the temporal-consensus output that the
rest of the site already shows.

THE RULES (stated in the export's `timeWeighting.method` too):

  * Exact intervals only. A second counts for a hero only when a published
    stint covers it. Nothing is interpolated across a gap.
  * Live play only. Seconds are counted inside the map's round windows (the
    round-emblem detector's output). Setup time, replays and the gaps
    between rounds are not "time on a hero". A map with no detected rounds
    falls back to the span of its stints and says so
    (`liveWindowSource: "stint-span"`), because that denominator includes
    non-live time.
  * Unknown is reported, never redistributed. A live second where a slot has
    no stint is `unknownPlayerSeconds`. Every time-weighted share divides by
    KNOWN seconds; coverage (`known / available`) is published beside it so a
    reader can see how much of the map the share actually describes.
  * Recorded offsets are whole broadcast seconds with an INCLUSIVE end (the
    sampler's ±1 sample, ~5 s). Both stints and rounds are converted to
    half-open [start, end + 1) once, here, so durations add up.

Run directly for a quick table:  python3 pipeline/time_weighting.py
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Iterable

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
ARCHETYPES_PATH = os.path.join(REPO_ROOT, "config", "comp_archetypes.json")

METHOD_VERSION = "tw-1"

#: Stint tiers a person or overwhelming temporal agreement stands behind.
AUDITED_TIERS = ("confirmed", "strong")

METHOD_NOTE = (
    "Seconds come only from published VOD hero stints, counted only inside "
    "the map's detected round windows (live play). A live second where a HUD "
    "slot has no stint is unknown, reported separately, and excluded from "
    "every time-weighted share: shares divide by KNOWN seconds, and coverage "
    "(known ÷ available player-seconds) is published beside each one. "
    "Recorded offsets are inclusive whole seconds (±1 sample, ~5 s).")


# --------------------------------------------------------------- helpers
def load_archetypes(path: str = ARCHETYPES_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"version": None, "rules": []}
    return {"version": data.get("version"), "rules": data.get("rules") or []}


def classify_archetype(heroes: Iterable[str], roles: dict[str, str],
                       archetypes: dict) -> str:
    """The editorial archetype of one complete line-up, or 'unclassified'."""
    heroes = list(heroes)
    tanks = [h for h in heroes if roles.get(h) == "Tank"]
    for rule in archetypes.get("rules") or []:
        if not any(t in (rule.get("tanks") or []) for t in tanks):
            continue
        need = rule.get("requiresAny") or []
        if need and not any(h in need for h in heroes):
            continue
        return rule.get("id") or "unclassified"
    return "unclassified"


def _half_open(start: Any, end: Any) -> tuple[int, int] | None:
    if start is None or end is None:
        return None
    s, e = int(start), int(end) + 1
    return (s, e) if e > s else None


def _merge(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for s, e in sorted(windows):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def live_windows(map_row: dict, stints: list[dict]) -> tuple[list[tuple[int, int]], str]:
    """(half-open live windows, source). Rounds when detected, otherwise the
    span of the published stints — labelled, because that span includes
    setup and between-round time."""
    wins = [w for w in (_half_open(r.get("start"), r.get("end"))
                        for r in (map_row.get("rounds") or [])) if w]
    if wins:
        return _merge(wins), "rounds"
    spans = [w for w in (_half_open(s.get("start"), s.get("end")) for s in stints) if w]
    if not spans:
        return [], "none"
    return [(min(s for s, _ in spans), max(e for _, e in spans))], "stint-span"


def _lane_reader(lane_stints: list[dict]):
    """hero_at(t) for one HUD slot. When two stints overlap (an edge read a
    sample early), the one that started most recently wins — the same rule
    export_data._comp_at uses for snapshots."""
    rows = sorted(((_half_open(s.get("start"), s.get("end")), s) for s in lane_stints),
                  key=lambda x: (x[0] or (0, 0))[0])
    rows = [(w, s) for w, s in rows if w]

    def hero_at(t: int):
        best = None
        for (s, e), row in rows:
            if s <= t < e:
                best = row
            elif s > t:
                break
        return best
    return hero_at


def _ratio(n: float, d: float) -> float | None:
    return round(n / d, 5) if d else None


# ----------------------------------------------------------- per map
def map_time(map_row: dict, match: dict, stints: list[dict], swaps: list[dict],
             roles: dict[str, str], archetypes: dict) -> dict | None:
    """Time-weighted facts for ONE published map, or None when it has no
    published stint timeline (nothing to weigh — never a zero)."""
    stints = [s for s in stints if s.get("start") is not None and s.get("end") is not None]
    if not stints:
        return None
    windows, source = live_windows(map_row, stints)
    live = sum(e - s for s, e in windows)
    if live <= 0:
        return None
    sides = {"a": match.get("teamA"), "b": match.get("teamB")}
    for s in stints:
        if s.get("side") in sides and s.get("teamId"):
            sides[s["side"]] = sides[s["side"]] or s["teamId"]
    team_size = max([int(s.get("slot") or 0) for s in stints] + [5])
    winner = map_row.get("winner")

    teams_out = []
    for side in ("a", "b"):
        team_id = sides.get(side)
        team_stints = [s for s in stints if s.get("side") == side]
        lanes = {slot: _lane_reader([s for s in team_stints if int(s.get("slot") or 0) == slot])
                 for slot in range(1, team_size + 1)}
        hero_sec: dict[str, int] = {}
        hero_audited: dict[str, int] = {}
        slot_sec: dict[int, dict[str, int]] = {slot: {} for slot in lanes}
        lineup_sec: dict[str, dict] = {}
        known = 0
        full = 0
        for ws, we in windows:
            for t in range(ws, we):
                heroes_now = []
                for slot, read in lanes.items():
                    row = read(t)
                    if row is None:
                        continue
                    h = row["hero"]
                    known += 1
                    heroes_now.append(h)
                    hero_sec[h] = hero_sec.get(h, 0) + 1
                    slot_sec[slot][h] = slot_sec[slot].get(h, 0) + 1
                    if row.get("auditTier") in AUDITED_TIERS:
                        hero_audited[h] = hero_audited.get(h, 0) + 1
                if len(heroes_now) == team_size and len(set(heroes_now)) == team_size:
                    full += 1
                    key = "+".join(sorted(heroes_now))
                    entry = lineup_sec.setdefault(key, {"key": key,
                                                        "heroes": sorted(heroes_now),
                                                        "seconds": 0, "firstAt": t})
                    entry["seconds"] += 1
        available = live * team_size
        role_known: dict[str, int] = {}
        for h, n in hero_sec.items():
            role = roles.get(h) or "Unknown"
            role_known[role] = role_known.get(role, 0) + n
        heroes = []
        for h, n in sorted(hero_sec.items(), key=lambda x: (-x[1], x[0])):
            role = roles.get(h) or "Unknown"
            heroes.append({
                "hero": h, "role": role, "seconds": n,
                "auditedSeconds": hero_audited.get(h, 0),
                "shareOfTeamKnownSeconds": _ratio(n, known),
                "shareOfRoleKnownSeconds": _ratio(n, role_known.get(role, 0)),
                "presenceShare": _ratio(n, live),
            })
        lineups = sorted(lineup_sec.values(), key=lambda x: (-x["seconds"], x["key"]))
        for lu in lineups:
            lu["share"] = _ratio(lu["seconds"], full)
            lu["archetype"] = classify_archetype(lu["heroes"], roles, archetypes)
        arche: dict[str, int] = {}
        for lu in lineups:
            arche[lu["archetype"]] = arche.get(lu["archetype"], 0) + lu["seconds"]
        team_swaps = [s for s in swaps if s.get("side") == side and s.get("status") == "confirmed"]
        teams_out.append({
            "teamId": team_id, "side": side,
            "result": (None if not winner else ("win" if winner == team_id else "loss")),
            "availablePlayerSeconds": available,
            "knownPlayerSeconds": known,
            "unknownPlayerSeconds": available - known,
            "coverage": _ratio(known, available),
            "roleKnownSeconds": dict(sorted(role_known.items())),
            "fullLineupSeconds": full,
            "heroes": heroes,
            "slots": [{"slot": slot,
                       "knownSeconds": sum(v.values()),
                       "heroes": [{"hero": h, "seconds": n} for h, n in
                                  sorted(v.items(), key=lambda x: (-x[1], x[0]))]}
                      for slot, v in sorted(slot_sec.items())],
            "lineups": lineups,
            "archetypes": [{"archetype": k, "seconds": v, "share": _ratio(v, full)}
                           for k, v in sorted(arche.items(), key=lambda x: (-x[1], x[0]))],
            "confirmedSwaps": len(team_swaps),
        })
    available = sum(t["availablePlayerSeconds"] for t in teams_out)
    known = sum(t["knownPlayerSeconds"] for t in teams_out)
    return {
        "mapId": map_row["id"], "matchId": match.get("id"),
        "map": map_row.get("map"), "mode": map_row.get("mode"),
        "winner": winner, "teamSize": team_size,
        "liveWindowSource": source,
        "liveWindows": [[s, e - 1] for s, e in windows],
        "liveSeconds": live,
        "availablePlayerSeconds": available,
        "knownPlayerSeconds": known,
        "unknownPlayerSeconds": available - known,
        "coverage": _ratio(known, available),
        "confirmedSwaps": sum(t["confirmedSwaps"] for t in teams_out),
        "teams": teams_out,
    }


# ----------------------------------------------------------- aggregates
def hero_time_stats(map_rows: list[dict]) -> list[dict]:
    """Across every VOD-timed map: seconds per hero and the shares of KNOWN
    seconds they represent. `presenceRate` is per (map, team) timeline —
    the same unit the appearance-based pick rate uses — so the two can be
    read side by side."""
    total_known = sum(t["knownPlayerSeconds"] for m in map_rows for t in m["teams"])
    role_known: dict[str, int] = {}
    timelines = 0
    win_known = loss_known = 0
    rows: dict[str, dict] = {}
    for m in map_rows:
        for t in m["teams"]:
            if not t["knownPlayerSeconds"]:
                continue
            timelines += 1
            if t["result"] == "win":
                win_known += t["knownPlayerSeconds"]
            elif t["result"] == "loss":
                loss_known += t["knownPlayerSeconds"]
            for role, n in t["roleKnownSeconds"].items():
                role_known[role] = role_known.get(role, 0) + n
            for h in t["heroes"]:
                r = rows.setdefault(h["hero"], {
                    "hero": h["hero"], "role": h["role"], "seconds": 0,
                    "auditedSeconds": 0, "timelinesPresent": 0,
                    "winnerSeconds": 0, "loserSeconds": 0, "undecidedSeconds": 0,
                    "mapIds": []})
                r["seconds"] += h["seconds"]
                r["auditedSeconds"] += h["auditedSeconds"]
                r["timelinesPresent"] += 1
                key = {"win": "winnerSeconds", "loss": "loserSeconds"}.get(
                    t["result"], "undecidedSeconds")
                r[key] += h["seconds"]
                if m["mapId"] not in r["mapIds"]:
                    r["mapIds"].append(m["mapId"])
    out = []
    for r in rows.values():
        out.append(dict(
            r,
            shareOfAllKnownPlayerSeconds=_ratio(r["seconds"], total_known),
            shareOfRoleKnownSeconds=_ratio(r["seconds"], role_known.get(r["role"], 0)),
            presenceRate=_ratio(r["timelinesPresent"], timelines),
            timelines=timelines,
            shareOfWinnerKnownSeconds=_ratio(r["winnerSeconds"], win_known),
            shareOfLoserKnownSeconds=_ratio(r["loserSeconds"], loss_known)))
    out.sort(key=lambda r: (-r["seconds"], r["hero"]))
    return out


def lineup_time_stats(map_rows: list[dict]) -> list[dict]:
    """Complete line-ups across every VOD-timed map, by verified seconds."""
    rows: dict[str, dict] = {}
    for m in map_rows:
        for t in m["teams"]:
            for lu in t["lineups"]:
                r = rows.setdefault(lu["key"], {
                    "key": lu["key"], "heroes": lu["heroes"],
                    "archetype": lu["archetype"], "seconds": 0, "timelines": 0,
                    "teamIds": [], "winnerSeconds": 0, "loserSeconds": 0,
                    "evidence": []})
                r["seconds"] += lu["seconds"]
                r["timelines"] += 1
                if t["teamId"] and t["teamId"] not in r["teamIds"]:
                    r["teamIds"].append(t["teamId"])
                if t["result"] == "win":
                    r["winnerSeconds"] += lu["seconds"]
                elif t["result"] == "loss":
                    r["loserSeconds"] += lu["seconds"]
                r["evidence"].append({"mapId": m["mapId"], "teamId": t["teamId"],
                                      "seconds": lu["seconds"], "firstAt": lu["firstAt"]})
    return sorted(rows.values(), key=lambda r: (-r["seconds"], r["key"]))


def build(payload: dict, archetypes: dict | None = None) -> dict:
    """Everything time-weighted for the public export. `payload` is the
    public.v1 dict AFTER withheld matches were removed, so nothing here can
    describe a withheld map."""
    archetypes = archetypes if archetypes is not None else load_archetypes()
    roles = {h["id"]: h.get("role") for h in payload.get("heroes") or []}
    stints = payload.get("heroStints") or []
    swaps = payload.get("heroSwaps") or []
    maps_out = []
    for match in payload.get("matches") or []:
        for mp in match.get("maps") or []:
            row = map_time(mp, match,
                           [s for s in stints if s.get("mapId") == mp["id"]],
                           [s for s in swaps if s.get("mapId") == mp["id"]],
                           roles, archetypes)
            if row:
                maps_out.append(row)
    return {
        "method": {
            "version": METHOD_VERSION,
            "note": METHOD_NOTE,
            "archetypesVersion": archetypes.get("version"),
            "archetypesSource": "config/comp_archetypes.json (editorial labels, not detections)",
            "archetypes": [{"id": r.get("id"), "label": r.get("label") or r.get("id"),
                            "tanks": list(r.get("tanks") or []),
                            "requiresAny": list(r.get("requiresAny") or [])}
                           for r in archetypes.get("rules") or []],
            "denominators": {
                "shareOfTeamKnownSeconds": "hero seconds ÷ the team's known player-seconds on that map",
                "shareOfRoleKnownSeconds": "hero seconds ÷ the team's known player-seconds on heroes of the same role",
                "presenceShare": "hero seconds ÷ the map's live seconds (a hero is fielded by at most one player per team)",
                "coverage": "known player-seconds ÷ (live seconds × HUD slots)",
                "presenceRate": "(map, team) timelines the hero appeared in ÷ all (map, team) timelines",
                "lineup share": "seconds a complete line-up was fielded ÷ seconds ANY complete line-up was known",
            },
        },
        "maps": maps_out,
        "heroes": hero_time_stats(maps_out),
        "lineups": lineup_time_stats(maps_out),
    }


def main() -> int:
    sys.path.insert(0, HERE)
    from automation.self_fill import load_public_data  # noqa: E402
    pub = load_public_data(os.path.join(REPO_ROOT, "assets", "data", "public_data.v1.js")) or {}
    tw = pub.get("timeWeighting") or build(pub)
    for m in tw["maps"]:
        print(f"{m['mapId']}: live {m['liveSeconds']}s ({m['liveWindowSource']}), "
              f"coverage {m['coverage']:.1%}, unknown {m['unknownPlayerSeconds']} player-s")
        for t in m["teams"]:
            print(f"  {t['teamId']}: " + ", ".join(
                f"{h['hero']} {h['seconds']}s" for h in t["heroes"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
