#!/usr/bin/env python3
"""
obssojourn_source.py — ingest player-POV match videos (e.g. ObsSojourn's
"Support Player POVs" uploads) as a first-class source type.

Why POV videos are a great source
  * NO broadcast filler — no desk, no caster cuts, no highlight packages;
    almost every frame is live gameplay, so the gameplay-state filter has
    far less to reject.
  * The SAME in-client top scoreboard bar is on screen (both teams' five
    hero chips), so the existing slot layout + detector approach applies —
    it just needs its own one-time calibration for this recording format,
    which then serves EVERY video from that channel.
  * The description hands us the match identity AND the per-map timestamps,
    so map segmentation is given, not detected.
  * The recorded player's own hero is known (the "Heroes played" line), a
    free ground-truth slot and a POV-role signal.

The one thing to get right: a channel often posts MULTIPLE POVs of the SAME
match (a tank POV and a support POV, each covering all six maps). Those must
resolve to ONE match with several video sources whose comp reads
cross-confirm each other — never two matches, never double-counted comps.

This module is the pure-text/logic front end: parse a pasted description into
a match + maps + windows + heroes, map the map names to catalog ids (flagging
new maps like Neon Junction that aren't in the DB yet), compute a stable
match signature for de-duplication, and expose the cross-POV comp-merge key.
No network, no video — fully unit-testable. The calibrate/harvest/ingest of
the actual frames reuses the existing pipeline once per recording format.
"""
from __future__ import annotations
import argparse
import json
import re

# Canonical OW map name -> DB game_maps id. Extend as maps are added.
NAME_TO_ID = {
    "hanaoka": "hanaoka", "throne of anubis": "anubis",
    "antarctic peninsula": "antarctic", "busan": "busan", "ilios": "ilios",
    "lijiang tower": "lijiang", "nepal": "nepal", "oasis": "oasis",
    "samoa": "samoa", "circuit royal": "circuit", "dorado": "dorado",
    "havana": "havana", "junkertown": "junkertown", "route 66": "route66",
    "shambali monastery": "shambali", "aatlis": "aatlis",
    "new junk city": "njc", "suravasa": "suravasa",
    "blizzard world": "blizzworld", "eichenwalde": "eich",
    "king's row": "kingsrow", "kings row": "kingsrow", "midtown": "midtown",
    "paraiso": "paraiso", "paraíso": "paraiso", "colosseo": "colosseo",
    "esperanca": "esperanca", "esperança": "esperanca",
    "new queen street": "nqs", "runasapi": "runasapi",
    # 2026 season maps (added as they appear in schedules)
    "neon junction": "neonjunction",
}


def map_name_to_id(name: str, extra: dict | None = None) -> str | None:
    """Catalog id for a map name, or None when it's a map we don't know yet
    (e.g. a brand-new season map) — the caller then flags it for adding to
    game_maps rather than silently dropping the segment."""
    key = re.sub(r"\s+", " ", (name or "").strip().lower())
    if extra and key in extra:
        return extra[key]
    return NAME_TO_ID.get(key)


def _hms_to_seconds(ts: str) -> int:
    parts = [int(p) for p in ts.strip().split(":")]
    s = 0
    for p in parts:
        s = s * 60 + p
    return s


_TS_RE = re.compile(r"^\s*(.+?)\s*[:\-–]\s*((?:\d{1,2}:)?\d{1,2}:\d{2})\s*$")
_HEROES_RE = re.compile(r"heroes played\s*:\s*(.+)", re.I)
_DATE_RE = re.compile(r"match date\s*:\s*(.+)", re.I)
_TITLE_RE = re.compile(r"^(.+?)\s+vs\.?\s+(.+?)\s*\|\s*(.+)$", re.I | re.M)


def parse_description(text: str, extra_maps: dict | None = None) -> dict:
    """Turn a pasted POV-video description into a structured match.

    Returns {teamA, teamB, event, date, maps: [{order, name, mapId, start,
    end}], heroesPlayed, unknownMaps, signature}. `end` is the next map's
    start; the last map's end is None (runs to the video end).
    """
    out: dict = {"teamA": None, "teamB": None, "event": None, "date": None,
                 "maps": [], "heroesPlayed": [], "unknownMaps": []}

    m = _TITLE_RE.search(text)
    if m:
        out["teamA"] = m.group(1).strip()
        out["teamB"] = m.group(2).strip()
        out["event"] = m.group(3).strip()

    d = _DATE_RE.search(text)
    if d:
        out["date"] = _normalize_date(d.group(1).strip())

    h = _HEROES_RE.search(text)
    if h:
        out["heroesPlayed"] = [x.strip() for x in
                               re.split(r"[,/]", h.group(1)) if x.strip()]

    # timestamp lines: "<Map name>: M:SS" or "H:MM:SS". Only accept lines
    # whose label resolves to a real/known-shape map name (so social links
    # or "Timestamps:" headers don't get picked up).
    raw = []
    for line in text.splitlines():
        tm = _TS_RE.match(line)
        if not tm:
            continue
        label, ts = tm.group(1).strip(), tm.group(2)
        if label.lower() in ("timestamps", "chapters"):
            continue
        raw.append((label, _hms_to_seconds(ts)))
    # keep the first contiguous run that starts at 0 (the map chapter list)
    raw = _map_chapter_run(raw)

    for i, (label, start) in enumerate(raw):
        mid = map_name_to_id(label, extra_maps)
        if mid is None:
            out["unknownMaps"].append(label)
        end = raw[i + 1][1] if i + 1 < len(raw) else None
        out["maps"].append({"order": i + 1, "name": label, "mapId": mid,
                            "start": start, "end": end})

    out["signature"] = match_signature(out)
    return out


def _map_chapter_run(pairs: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """From all timestamp-shaped lines, keep the map chapter list: the run
    that begins at 0:00 and increases monotonically."""
    start_idx = next((i for i, (_l, s) in enumerate(pairs) if s == 0), None)
    if start_idx is None:
        return pairs
    run = [pairs[start_idx]]
    for label, s in pairs[start_idx + 1:]:
        if s > run[-1][1]:
            run.append((label, s))
        else:
            break
    return run


def _normalize_date(s: str) -> str | None:
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            import datetime as dt
            return dt.datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _team_key(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def match_signature(parsed: dict) -> str:
    """A stable key identifying the MATCH regardless of which POV recorded
    it — teams (order-independent) + date. Two POV videos of the same game
    produce the same signature and must collapse to one match."""
    teams = tuple(sorted((_team_key(parsed.get("teamA")),
                          _team_key(parsed.get("teamB")))))
    return f"{teams[0]}--{teams[1]}--{parsed.get('date') or 'nodate'}"


def is_same_match(a: dict, b: dict) -> bool:
    return match_signature(a) == match_signature(b)


def comp_merge_key(match_id: str, map_id: str, team_id: str,
                   round_index: int, heroes: list[str]) -> str:
    """De-dup / cross-confirm key for a comp read. Two POVs of the same match
    reading the same team's comp on the same map+round yield the SAME key, so
    the DB records one comp with provenance from both runs (and higher
    confidence), never two."""
    hero_set = ",".join(sorted(h for h in heroes if h and h != "UNKNOWN"))
    return f"{match_id}|{map_id}|{team_id}|r{round_index}|{hero_set}"


def pov_role(heroes_played: list[str], hero_roles: dict | None) -> str | None:
    """Guess which role POV this is from the recorded player's heroes (all
    supports -> a support POV). Only a hint, used to label the source."""
    if not heroes_played or not hero_roles:
        return None
    roles = {hero_roles.get(_team_key(h)) or hero_roles.get(h)
             for h in heroes_played}
    roles.discard(None)
    return next(iter(roles)) if len(roles) == 1 else None


DEMO_DESC = """Heroes played: Mizuki, Juno, Jetpack Cat, Lucio
Crazy Raccoon vs ZETA Division | OWCS Korea Stage 2 Grand Final
Match Date: July 12, 2026

*SPOILERS AHEAD*
Timestamps:
Antarctic Peninsula: 0:00
New Junk City: 13:20
King's Row: 29:00
Circuit Royal: 41:15
Colosseo: 58:50
Neon Junction: 1:09:20

Thank you to OWTV for providing the stats for this match!
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--file", help="parse a description from a text file")
    args = ap.parse_args()
    if args.demo:
        print(json.dumps(parse_description(DEMO_DESC), indent=1))
        return
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            print(json.dumps(parse_description(f.read()), indent=1))
        return
    print("obssojourn_source: parse_description(text). Use --demo or --file.")


if __name__ == "__main__":
    main()
