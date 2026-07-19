#!/usr/bin/env python3
"""
faceit_parser.py — defensive parser for FACEIT OW2 matchroom data.

Turns a cached FACEIT room (HTML with an embedded state blob, or a JSON
payload from the FACEIT Data API) into a normalized dict of *facts only*.

Hard rule: this module NEVER infers or fabricates hero picks, team comps,
swaps, timelines, or rates. It only extracts match metadata that FACEIT is
authoritative for. Missing fields become null or empty lists; malformed
input returns a valid empty-ish object instead of raising.

Public API:
    extract_match_id_from_url(url) -> str | None
    parse_faceit_room_html(html, room_url) -> dict          (normalized)
    parse_faceit_room_json(payload, room_url) -> dict        (normalized)
    normalize_faceit_match(parsed) -> dict                   (idempotent)

Normalized shape (see project spec):
{
  "faceitMatchId": str|None,
  "faceitRoomUrl": str|None,
  "teams": [{"side":"A","name":str|None,"faceitTeamId":str|None}, {...B}],
  "score": {"teamA": int|None, "teamB": int|None},
  "maps": [{"order":int,"name":str|None,"mode":str|None,
            "scoreA":int|None,"scoreB":int|None,"winner":str|None,
            "replayCode":str|None,"heroBans":[...],"pickedBy":str|None,
            "vetoAction":str|None}],
  "rosters": [{"side":"A","teamName":str|None,"players":[
                 {"nickname":str,"faceitPlayerId":str|None,"role":str|None}]}]
}
"""
from __future__ import annotations

import json
import re
from typing import Any

ROOM_RE = re.compile(r"/room/([^/?#]+)")
# Replay codes are the 6-char alphanumeric codes OW shows; keep the token
# check strict so we don't grab random uppercase words.
REPLAY_TOKEN_RE = re.compile(r"^[A-Z0-9]{6}$")

# Canonical OW2 map -> mode. Only used to *label* a FACEIT-provided map name;
# never to invent a map that FACEIT didn't report.
MAP_MODES = {
    "busan": "Control", "ilios": "Control", "lijiang tower": "Control",
    "nepal": "Control", "oasis": "Control", "antarctic peninsula": "Control",
    "samoa": "Control",
    "circuit royal": "Escort", "dorado": "Escort", "havana": "Escort",
    "junkertown": "Escort", "route 66": "Escort", "shambali monastery": "Escort",
    "rialto": "Escort", "watchpoint: gibraltar": "Escort",
    "blizzard world": "Hybrid", "eichenwalde": "Hybrid", "king's row": "Hybrid",
    "midtown": "Hybrid", "paraíso": "Hybrid", "paraiso": "Hybrid",
    "hollywood": "Hybrid", "numbani": "Hybrid",
    "colosseo": "Push", "esperança": "Push", "esperanca": "Push",
    "new queen street": "Push", "runasapi": "Push",
    "new junk city": "Flashpoint", "suravasa": "Flashpoint", "aatlis": "Flashpoint",
    "hanaoka": "Clash", "throne of anubis": "Clash",
}


# --------------------------------------------------------------------- utils
def _s(v: Any) -> str | None:
    """Coerce to a clean non-empty string, else None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return str(v)
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v or None


def _int(v: Any) -> int | None:
    """Coerce to int, else None (never raises)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+", v)
        if m:
            try:
                return int(m.group(0))
            except ValueError:
                return None
    return None


def _mode_for(name: str | None) -> str | None:
    if not name:
        return None
    return MAP_MODES.get(name.strip().lower())


def _looks_like_replay(v: Any) -> str | None:
    s = _s(v)
    if s and REPLAY_TOKEN_RE.match(s.upper()):
        return s.upper()
    return None


def extract_match_id_from_url(url: str | None) -> str | None:
    """Pull the room/match id out of a FACEIT room URL. Defensive: None-safe."""
    s = _s(url)
    if not s:
        return None
    m = ROOM_RE.search(s)
    if m:
        return _s(m.group(1))
    # Bare id passed in as "url"
    if re.fullmatch(r"[0-9a-fA-F-]{6,}", s) or s.startswith("1-"):
        return s
    return None


# ------------------------------------------------------------------ JSON path
def _json_players(team_obj: dict) -> list[dict]:
    players = []
    raw = team_obj.get("roster") or team_obj.get("players") or []
    if not isinstance(raw, list):
        return players
    for p in raw:
        if not isinstance(p, dict):
            continue
        nick = _s(p.get("nickname") or p.get("name") or p.get("game_player_name"))
        if not nick:
            continue
        players.append({
            "nickname": nick,
            "faceitPlayerId": _s(p.get("player_id") or p.get("user_id")
                                 or p.get("id")),
            "role": _s(p.get("role") or p.get("game_role")),
        })
    return players


def _json_team(team_obj: Any) -> dict:
    if not isinstance(team_obj, dict):
        return {"name": None, "faceitTeamId": None, "players": []}
    name = _s(team_obj.get("name") or team_obj.get("nickname")
              or team_obj.get("team_name"))
    tid = _s(team_obj.get("faction_id") or team_obj.get("team_id")
             or team_obj.get("id"))
    return {"name": name, "faceitTeamId": tid, "players": _json_players(team_obj)}


def _json_maps(payload: dict, team_a_name: str | None,
               team_b_name: str | None) -> list[dict]:
    """Extract per-map facts from whatever map-ish list FACEIT provides.
    Recognizes common shapes: voting/tickets 'entities', detailed_results,
    or an explicit 'maps' array. Anything unknown yields no maps (not junk).
    """
    maps: list[dict] = []

    # Shape A: explicit maps array (our own API-shaped payloads / fixtures)
    arr = payload.get("maps")
    if isinstance(arr, list) and arr:
        for i, mm in enumerate(arr, start=1):
            if not isinstance(mm, dict):
                continue
            name = _s(mm.get("name") or mm.get("map") or mm.get("label"))
            maps.append({
                "order": _int(mm.get("order")) or i,
                "name": name,
                "mode": _s(mm.get("mode")) or _mode_for(name),
                "scoreA": _int(mm.get("scoreA") if "scoreA" in mm
                               else mm.get("score_a")),
                "scoreB": _int(mm.get("scoreB") if "scoreB" in mm
                               else mm.get("score_b")),
                "winner": _s(mm.get("winner")),
                "replayCode": _looks_like_replay(
                    mm.get("replayCode") or mm.get("replay_code")),
                "heroBans": _json_hero_bans(mm.get("heroBans")
                                            or mm.get("hero_bans")),
                "pickedBy": _s(mm.get("pickedBy") or mm.get("picked_by")),
                "vetoAction": _s(mm.get("vetoAction") or mm.get("veto_action")),
            })
        return maps

    # Shape B: FACEIT detailed_results (per-map winners/scores). Map NAMES are
    # not in detailed_results — they live in voting.map (entities keyed by guid,
    # pick[] giving the played order). Merge them by position when present.
    dr = payload.get("detailed_results")
    if isinstance(dr, list) and dr:
        picked_names = _voting_pick_names(payload)
        for i, rnd in enumerate(dr, start=1):
            if not isinstance(rnd, dict):
                continue
            factions = rnd.get("factions") or {}
            a = (factions.get("faction1") or {})
            b = (factions.get("faction2") or {})
            # name precedence: explicit label on the round, else the voting pick
            # order (index i-1), else None. Never invented.
            name = _s(rnd.get("label") or rnd.get("map"))
            if not name and i - 1 < len(picked_names):
                name = picked_names[i - 1]
            maps.append({
                "order": i,
                "name": name,
                "mode": _mode_for(name),
                "scoreA": _int(a.get("score")),
                "scoreB": _int(b.get("score")),
                "winner": _winner_side(_s(rnd.get("winner")), a, b),
                "replayCode": None,
                "heroBans": [],
                "pickedBy": None,
                "vetoAction": None,
            })
        return maps

    return maps


def _voting_pick_names(payload: dict) -> list[str]:
    """Ordered list of picked map display names from voting.map, or []."""
    voting = payload.get("voting")
    if not isinstance(voting, dict):
        return []
    mp = voting.get("map")
    if not isinstance(mp, dict):
        return []
    entities = mp.get("entities")
    guid_to_name = {}
    if isinstance(entities, list):
        for e in entities:
            if isinstance(e, dict) and _s(e.get("guid")):
                guid_to_name[e["guid"]] = _s(e.get("name"))
    pick = mp.get("pick")
    names = []
    if isinstance(pick, list):
        for g in pick:
            nm = guid_to_name.get(g) if isinstance(g, str) else None
            # pick entries can be names directly on some payloads
            names.append(nm or (_s(g) if isinstance(g, str) and g not in guid_to_name else None))
    return [n for n in names if n]


def _winner_side(winner_raw: str | None, a: dict, b: dict) -> str | None:
    if not winner_raw:
        return None
    w = winner_raw.lower()
    if w in ("faction1", "team_a", "a"):
        return "A"
    if w in ("faction2", "team_b", "b"):
        return "B"
    return None


def _json_hero_bans(raw: Any) -> list[dict]:
    out = []
    if not isinstance(raw, list):
        return out
    for i, ban in enumerate(raw, start=1):
        if isinstance(ban, str):
            hero = _s(ban)
            out.append({"hero": hero, "team": None, "order": i})
        elif isinstance(ban, dict):
            out.append({
                "hero": _s(ban.get("hero") or ban.get("name")),
                "team": _s(ban.get("team") or ban.get("side")),
                "order": _int(ban.get("order")) or i,
            })
    return [b for b in out if b["hero"]]


def parse_faceit_room_json(payload: Any, room_url: str | None) -> dict:
    """Parse a FACEIT Data-API-style JSON payload into the normalized shape."""
    if not isinstance(payload, dict):
        return normalize_faceit_match({"faceitRoomUrl": room_url})

    # match id
    mid = (_s(payload.get("match_id")) or _s(payload.get("matchId"))
           or _s(payload.get("id")) or extract_match_id_from_url(room_url))

    # teams: FACEIT nests under teams.faction1/faction2 (Data API) or a list
    teams_obj = payload.get("teams")
    a_obj = b_obj = {}
    if isinstance(teams_obj, dict):
        a_obj = teams_obj.get("faction1") or teams_obj.get("team_a") or {}
        b_obj = teams_obj.get("faction2") or teams_obj.get("team_b") or {}
    elif isinstance(teams_obj, list) and len(teams_obj) >= 2:
        a_obj, b_obj = teams_obj[0], teams_obj[1]

    ta, tb = _json_team(a_obj), _json_team(b_obj)

    # overall score: results.score.faction1/2, or top-level score object
    score_a = score_b = None
    results = payload.get("results") or {}
    if isinstance(results, dict):
        sc = results.get("score") or {}
        if isinstance(sc, dict):
            score_a = _int(sc.get("faction1") if "faction1" in sc else sc.get("teamA"))
            score_b = _int(sc.get("faction2") if "faction2" in sc else sc.get("teamB"))
    if score_a is None and isinstance(payload.get("score"), dict):
        score_a = _int(payload["score"].get("teamA"))
        score_b = _int(payload["score"].get("teamB"))

    maps = _json_maps(payload, ta["name"], tb["name"])

    rosters = []
    for side, t in (("A", ta), ("B", tb)):
        if t["players"]:
            rosters.append({"side": side, "teamName": t["name"],
                            "players": t["players"]})

    parsed = {
        "faceitMatchId": mid,
        "faceitRoomUrl": _s(room_url),
        "teams": [
            {"side": "A", "name": ta["name"], "faceitTeamId": ta["faceitTeamId"]},
            {"side": "B", "name": tb["name"], "faceitTeamId": tb["faceitTeamId"]},
        ],
        "score": {"teamA": score_a, "teamB": score_b},
        "maps": maps,
        "rosters": rosters,
    }
    return normalize_faceit_match(parsed)


# ------------------------------------------------------------------ HTML path
_STATE_PATTERNS = [
    # Common embedded-state hooks. We try each; first that yields valid JSON wins.
    re.compile(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S),
    re.compile(r'window\.__INITIAL_STATE__\s*=\s*({.*?})\s*;?\s*</script>', re.S),
    re.compile(r'window\.__PRELOADED_STATE__\s*=\s*({.*?})\s*;?\s*</script>', re.S),
    re.compile(r'<script[^>]+type="application/json"[^>]*>(\{.*?\})</script>', re.S),
]


def _find_embedded_json(html_text: str) -> dict | None:
    for pat in _STATE_PATTERNS:
        for m in pat.finditer(html_text):
            blob = m.group(1).strip()
            try:
                data = json.loads(blob)
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict):
                match = _dig_for_match(data)
                if match is not None:
                    return match
    return None


def _dig_for_match(data: dict, depth: int = 0) -> dict | None:
    """Walk a nested state blob looking for an object that smells like a
    FACEIT match (has match_id/teams). Bounded depth; defensive."""
    if depth > 6 or not isinstance(data, dict):
        return None
    keys = set(data.keys())
    if ({"match_id", "teams"} & keys) or ({"matchId", "teams"} & keys):
        return data
    for v in data.values():
        if isinstance(v, dict):
            found = _dig_for_match(v, depth + 1)
            if found is not None:
                return found
        elif isinstance(v, list):
            for item in v[:20]:
                if isinstance(item, dict):
                    found = _dig_for_match(item, depth + 1)
                    if found is not None:
                        return found
    return None


def parse_faceit_room_html(html_text: Any, room_url: str | None) -> dict:
    """Parse a cached FACEIT room HTML page. Prefers an embedded JSON state
    blob (client-rendered apps ship one); otherwise returns a minimal object
    with just the match id from the URL. Never raises on bad HTML."""
    text = html_text if isinstance(html_text, str) else ""
    embedded = _find_embedded_json(text) if text else None
    if embedded is not None:
        return parse_faceit_room_json(embedded, room_url)

    # No usable structured data in the static HTML (expected for JS apps).
    return normalize_faceit_match({
        "faceitMatchId": extract_match_id_from_url(room_url),
        "faceitRoomUrl": _s(room_url),
    })


# ------------------------------------------------------------ normalization
def normalize_faceit_match(parsed: Any) -> dict:
    """Coerce any partial parsed object into the full normalized shape with
    safe defaults. Idempotent: normalizing a normalized object is a no-op."""
    p = parsed if isinstance(parsed, dict) else {}

    teams_in = p.get("teams")
    if not isinstance(teams_in, list):
        teams_in = []

    def team_at(side: str) -> dict:
        for t in teams_in:
            if isinstance(t, dict) and _s(t.get("side")) == side:
                return t
        # positional fallback
        idx = 0 if side == "A" else 1
        if len(teams_in) > idx and isinstance(teams_in[idx], dict):
            return teams_in[idx]
        return {}

    ta, tb = team_at("A"), team_at("B")
    teams = [
        {"side": "A", "name": _s(ta.get("name")),
         "faceitTeamId": _s(ta.get("faceitTeamId"))},
        {"side": "B", "name": _s(tb.get("name")),
         "faceitTeamId": _s(tb.get("faceitTeamId"))},
    ]

    score_in = p.get("score") if isinstance(p.get("score"), dict) else {}
    score = {"teamA": _int(score_in.get("teamA")),
             "teamB": _int(score_in.get("teamB"))}

    maps_out = []
    maps_in = p.get("maps") if isinstance(p.get("maps"), list) else []
    for i, mm in enumerate(maps_in, start=1):
        if not isinstance(mm, dict):
            continue
        name = _s(mm.get("name"))
        bans = mm.get("heroBans")
        if not isinstance(bans, list):
            bans = []
        maps_out.append({
            "order": _int(mm.get("order")) or i,
            "name": name,
            "mode": _s(mm.get("mode")) or _mode_for(name),
            "scoreA": _int(mm.get("scoreA")),
            "scoreB": _int(mm.get("scoreB")),
            "winner": _s(mm.get("winner")),
            "replayCode": _looks_like_replay(mm.get("replayCode")),
            "heroBans": [
                {"hero": _s(b.get("hero")) if isinstance(b, dict) else _s(b),
                 "team": _s(b.get("team")) if isinstance(b, dict) else None,
                 "order": (_int(b.get("order")) if isinstance(b, dict) else None) or j}
                for j, b in enumerate(bans, start=1)
                if (_s(b.get("hero")) if isinstance(b, dict) else _s(b))
            ],
            "pickedBy": _s(mm.get("pickedBy")),
            "vetoAction": _s(mm.get("vetoAction")),
        })

    rosters_out = []
    rosters_in = p.get("rosters") if isinstance(p.get("rosters"), list) else []
    for r in rosters_in:
        if not isinstance(r, dict):
            continue
        players_in = r.get("players") if isinstance(r.get("players"), list) else []
        players = []
        for pl in players_in:
            if not isinstance(pl, dict):
                continue
            nick = _s(pl.get("nickname"))
            if not nick:
                continue
            players.append({"nickname": nick,
                            "faceitPlayerId": _s(pl.get("faceitPlayerId")),
                            "role": _s(pl.get("role"))})
        if players:
            rosters_out.append({"side": _s(r.get("side")),
                                "teamName": _s(r.get("teamName")),
                                "players": players})

    return {
        "faceitMatchId": _s(p.get("faceitMatchId")),
        "faceitRoomUrl": _s(p.get("faceitRoomUrl")),
        "teams": teams,
        "score": score,
        "maps": maps_out,
        "rosters": rosters_out,
    }


if __name__ == "__main__":  # tiny manual smoke test
    import sys
    if len(sys.argv) > 1:
        raw = open(sys.argv[1], encoding="utf-8", errors="ignore").read()
        try:
            obj = parse_faceit_room_json(json.loads(raw), None)
        except ValueError:
            obj = parse_faceit_room_html(raw, None)
        print(json.dumps(obj, indent=2))
