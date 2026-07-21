#!/usr/bin/env python3
"""
prep_obssojourn_match.py — seed an honest match SKELETON from a POV-video
description, ready for the per-map ingest.

Given a pasted description (parsed by obssojourn_source), this:
  1. adds any brand-new map (e.g. Neon Junction) to game_maps, flagged with
     mode 'Unknown' for the operator to correct — never guessed,
  2. resolves the two team names to existing team ids,
  3. upserts the match row and one map_results row per map (map order, map
     id, and the VOD start second straight from the chapter timestamps),
  4. registers the video in data/sources/video_sources.json,
all IDEMPOTENTLY and with ZERO comps / winners invented — only the structure
the description actually gives. The comps come later, from the real per-map
ingest.

  python3 pipeline/prep_obssojourn_match.py --file desc.txt \\
      --match-id m-cr-zeta-krgf --vod https://youtu.be/is7eHd0nf84 \\
      --source-id owcs-is7ehd0nf84 [--write]
Dry-run by default; --write commits. Safe to re-run.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import obssojourn_source as O  # noqa: E402

SOURCES_PATH = os.path.join(db.REPO_ROOT, "data", "sources",
                            "video_sources.json")


def _team_id(con, name: str) -> str | None:
    key = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    for r in con.execute("SELECT id, name, code FROM teams"):
        if re.sub(r"[^a-z0-9]", "", r["name"].lower()) == key \
                or re.sub(r"[^a-z0-9]", "", (r["code"] or "").lower()) == key:
            return r["id"]
    return None


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def prep(parsed: dict, match_id: str, vod: str | None,
         source_id: str | None, write: bool) -> dict:
    con = db.connect()
    actions: list[str] = []

    ta = _team_id(con, parsed.get("teamA"))
    tb = _team_id(con, parsed.get("teamB"))
    if not ta or not tb:
        return {"ok": False,
                "error": (f"could not resolve teams "
                          f"(a={parsed.get('teamA')!r}->{ta}, "
                          f"b={parsed.get('teamB')!r}->{tb}); add them to the "
                          "teams table first")}

    # 1. ensure every parsed map exists in game_maps; a map we had to create
    #    gets mode 'Unknown' (flagged — never guessed) for the operator to fix
    new_maps = []
    for m in parsed["maps"]:
        mid = m["mapId"] or _slug(m["name"])
        m["mapId"] = mid
        exists = con.execute("SELECT 1 FROM game_maps WHERE id=?",
                             (mid,)).fetchone()
        if not exists:
            new_maps.append({"id": mid, "name": m["name"]})
            if write:
                con.execute("INSERT OR IGNORE INTO game_maps (id,name,mode) "
                            "VALUES (?,?,?)", (mid, m["name"], "Unknown"))
            actions.append(f"add map '{m['name']}' -> game_maps id '{mid}' "
                           "(mode Unknown — SET THE REAL MODE)")

    # 2. match row (skeleton — no scores/winner)
    if write:
        con.execute(
            """INSERT INTO matches (id, event_name, stage, region, date,
                   status, team_a, team_b, vod_url, source_ref)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   event_name=excluded.event_name, stage=excluded.stage,
                   vod_url=excluded.vod_url, source_ref=excluded.source_ref""",
            (match_id, parsed.get("event"), parsed.get("event"), "asia",
             parsed.get("date"), "final", ta, tb, vod, source_id))
    actions.append(f"upsert match '{match_id}': {ta} vs {tb}, "
                   f"{parsed.get('date')}, '{parsed.get('event')}'")

    # 3. one map_results row per map (order, map id, VOD start second)
    for m in parsed["maps"]:
        if write:
            con.execute(
                """INSERT INTO map_results
                       (match_id, map_order, map_id, vod_url,
                        vod_start_seconds, source)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT DO NOTHING""",
                (match_id, m["order"], m["mapId"], vod, m["start"],
                 "obssojourn-pov"))
        actions.append(f"  map {m['order']}: {m['name']} ({m['mapId']}) "
                       f"@ vod {m['start']}s"
                       + (f"-{m['end']}s" if m['end'] else "-end"))

    if write:
        con.commit()

    # 4. register the video source
    if source_id:
        _register_source(source_id, vod, parsed, write, actions)

    return {"ok": True, "matchId": match_id, "teamA": ta, "teamB": tb,
            "newMaps": new_maps, "maps": parsed["maps"],
            "heroesPlayed": parsed.get("heroesPlayed"),
            "signature": parsed.get("signature"),
            "actions": actions, "written": write}


def _register_source(source_id, vod, parsed, write, actions):
    try:
        with open(SOURCES_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {"sources": []}
    if any(s.get("id") == source_id for s in data.get("sources", [])):
        actions.append(f"source '{source_id}' already registered")
        return
    entry = {
        "id": source_id, "url": vod, "platform": "youtube",
        "title": f"{parsed.get('teamA')} vs {parsed.get('teamB')} — "
                 f"{parsed.get('event')} (POV)",
        "date": parsed.get("date"), "region": "asia",
        "kind": "pov", "channel": "ObsSojourn",
        "layout": "layouts/obssojourn_pov.json",
        "notes": ("Player-POV upload. Calibrate the ObsSojourn top-bar layout "
                  "ONCE (layouts/obssojourn_pov.json), then reuse for every "
                  "video from this channel. Map windows come from the "
                  "description; multiple POVs of one match share signature "
                  + str(parsed.get("signature")) + " and must merge, not "
                  "duplicate."),
        "enabled": True,
    }
    actions.append(f"register source '{source_id}' -> video_sources.json")
    if write:
        data.setdefault("sources", []).append(entry)
        with open(SOURCES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
            f.write("\n")


# the exact description the user pasted, so --demo seeds this match
CR_ZETA_KRGF_DESC = """Heroes played: Mizuki, Juno, Jetpack Cat, Lucio
Crazy Raccoon vs ZETA Division | OWCS Korea Stage 2 Grand Final
Match Date: July 12, 2026

Timestamps:
Antarctic Peninsula: 0:00
New Junk City: 13:20
King's Row: 29:00
Circuit Royal: 41:15
Colosseo: 58:50
Neon Junction: 1:09:20
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", help="description text file (else the embedded "
                    "CR-vs-ZETA Korea Grand Final)")
    ap.add_argument("--match-id", default="m-cr-zeta-krgf")
    ap.add_argument("--vod", default="https://www.youtube.com/watch?v=is7eHd0nf84")
    ap.add_argument("--source-id", default="owcs-is7ehd0nf84")
    ap.add_argument("--write", action="store_true", help="commit (else dry-run)")
    args = ap.parse_args()

    text = (open(args.file, encoding="utf-8").read() if args.file
            else CR_ZETA_KRGF_DESC)
    parsed = O.parse_description(text)
    res = prep(parsed, args.match_id, args.vod, args.source_id, args.write)
    print(json.dumps(res, indent=1, ensure_ascii=False))
    if res.get("ok") and not args.write:
        print("\nDRY RUN — nothing written. Re-run with --write to commit.")


if __name__ == "__main__":
    main()
