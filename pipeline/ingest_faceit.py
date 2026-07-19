#!/usr/bin/env python3
"""
ingest_faceit.py — Milestone 3 FACEIT matchroom ingest.

Flow:  fetch/cache (or load fixture) -> parse (faceit_parser) -> validate
       facts -> upsert FACEIT-sourced tables -> print a clear summary.

FACEIT is the source ONLY for match facts. This module never writes hero
comps, opener/played heroes, swaps, timelines, or rates — those live in
tracker tables (comp_snapshots/snapshot_heroes) and come from manual
corrections / CV / replay review. Ingest never touches tracker tables, so
existing manual/CV comps are always preserved.

Upserts into: teams, players, match_rosters, matches, map_results,
hero_bans, map_veto_events (and replay codes carried on map_results).

Modes:
  --room-url URL         resolve match id from a room URL
  --match-id ID          use an explicit id (canonical room url derived)
  --from-cache / --fixture PATH
                         parse a local cached/fixture file instead of the
                         network (JSON or HTML, auto-detected)
  --cache-dir DIR        where fetched bodies are cached (default data/raw/faceit)
  --dry-run              parse + validate + print, write nothing
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import faceit_parser as fp  # noqa: E402

DEFAULT_CACHE = os.path.join(db.REPO_ROOT, "data", "raw", "faceit")
USER_AGENT = ("OWCS-Comp-Tracker/0.3 (+fan project; polite public matchroom "
              "ingest; caches responses)")


# ------------------------------------------------------------------ helpers
def canonical_room_url(match_id: str, room_url: str | None) -> str:
    if room_url:
        return room_url.strip()
    return f"https://www.faceit.com/en/ow2/room/{match_id}"


def slugify(text: str, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s or fallback


def team_slug(name, faceit_team_id, side, match_id):
    if name:
        return slugify(name, f"team_{side.lower()}")
    if faceit_team_id:
        return f"faceit_{slugify(faceit_team_id, 'team')}"
    short = re.sub(r"[^a-zA-Z0-9]", "", match_id)[-8:].lower() or "faceit"
    return f"faceit_{short}_{side.lower()}"


def map_slug(name):
    return slugify(name, "map") if name else None


# --------------------------------------------------------------- fetch/cache
def cache_key_for(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]


def fetch_to_cache(url: str, cache_dir: str) -> dict:
    """Polite single GET, cached to disk. Never raises on network failure."""
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    key = cache_key_for(url)
    body_path = os.path.join(cache_dir, f"{key}.body")
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT,
                      "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read()
            status = getattr(resp, "status", None) or 200
        Path(body_path).write_bytes(body)
        return {"ok": True, "status": status, "body_path": body_path,
                "sha256": hashlib.sha256(body).hexdigest(),
                "text": body.decode("utf-8", "ignore"), "error": None}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "status": None, "body_path": None,
                "sha256": None, "text": None, "error": str(exc)}


def load_local(path: str) -> dict:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    return {"ok": True, "status": 200, "body_path": path,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "text": text, "error": None}


def parse_source(text: str, room_url):
    stripped = text.lstrip()
    if stripped[:1] in "{[":
        try:
            return fp.parse_faceit_room_json(json.loads(text), room_url)
        except ValueError:
            pass
    return fp.parse_faceit_room_html(text, room_url)


# ------------------------------------------------------------------ validate
def validate_parsed(parsed: dict) -> list[str]:
    """Non-fatal warnings; never blocks writing whatever IS present."""
    warns = []
    if not parsed.get("faceitMatchId"):
        warns.append("no match id resolved")
    if not parsed.get("maps"):
        warns.append("no maps parsed (metadata-only import)")
    if not any(t.get("name") for t in parsed.get("teams", [])):
        warns.append("no team names parsed (placeholder slugs used)")
    codes = [m.get("replayCode") for m in parsed.get("maps", []) if m.get("replayCode")]
    if codes and len(codes) != len(set(codes)):
        warns.append("duplicate replay codes across maps")
    for m in parsed.get("maps", []):
        if m.get("replayCode") and m.get("scoreA") is None and m.get("scoreB") is None:
            warns.append(f"map {m.get('order')} has replay code but no score")
    return warns


# -------------------------------------------------------------------- upsert
def upsert_team(con, slug, name, faceit_team_id, region):
    con.execute(
        """INSERT INTO teams (id, name, region, code, faceit_team_id)
           VALUES (?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             name=COALESCE(excluded.name, teams.name),
             faceit_team_id=COALESCE(excluded.faceit_team_id, teams.faceit_team_id)""",
        (slug, name or slug, region,
         (name or slug)[:6].upper().replace(" ", ""), faceit_team_id))


def upsert_player(con, nickname, faceit_player_id, team_id, role):
    pid = slugify(nickname or faceit_player_id or "unknown", "unknown")
    con.execute(
        """INSERT INTO players (id, nickname, faceit_player_id, team_id, role, source)
           VALUES (?,?,?,?,?, 'faceit')
           ON CONFLICT(id) DO UPDATE SET
             nickname=excluded.nickname,
             faceit_player_id=COALESCE(excluded.faceit_player_id, players.faceit_player_id),
             team_id=COALESCE(excluded.team_id, players.team_id),
             role=COALESCE(excluded.role, players.role)""",
        (pid, nickname, faceit_player_id, team_id, role))
    return pid


def _pick_team_slug(side, slug_a, slug_b):
    if not side:
        return None
    s = side.upper()
    if s in ("A", "FACTION1", "TEAM_A"):
        return slug_a
    if s in ("B", "FACTION2", "TEAM_B"):
        return slug_b
    return None


def upsert(con, parsed: dict, room_url: str, region: str) -> dict:
    """Write all FACEIT-sourced facts. Idempotent per match. Never touches
    comp_snapshots/snapshot_heroes (tracker data preserved)."""
    mid = parsed["faceitMatchId"] or ("unknown-" + cache_key_for(room_url))
    internal_id = f"faceit-{mid}"
    counts = {"teams": 0, "players": 0, "rosters": 0, "maps": 0,
              "bans": 0, "veto": 0, "replay_codes": 0, "skipped": 0}

    tA, tB = parsed["teams"][0], parsed["teams"][1]
    slug_a = team_slug(tA["name"], tA["faceitTeamId"], "A", mid)
    slug_b = team_slug(tB["name"], tB["faceitTeamId"], "B", mid)
    upsert_team(con, slug_a, tA["name"], tA["faceitTeamId"], region)
    upsert_team(con, slug_b, tB["name"], tB["faceitTeamId"], region)
    counts["teams"] = 2

    sa, sb = parsed["score"]["teamA"], parsed["score"]["teamB"]
    winner = None
    if sa is not None and sb is not None:
        winner = slug_a if sa > sb else slug_b if sb > sa else None
    status = "final" if (sa is not None and sb is not None) else "upcoming"

    con.execute(
        """INSERT INTO matches
             (id, source_ref, faceit_match_id, faceit_room_url, event_name,
              region, date, status, team_a, team_b, score_a, score_b,
              winner_team, source_url, raw_source, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'faceit', CURRENT_TIMESTAMP)
           ON CONFLICT(id) DO UPDATE SET
             faceit_match_id=excluded.faceit_match_id,
             faceit_room_url=excluded.faceit_room_url,
             status=excluded.status,
             team_a=excluded.team_a, team_b=excluded.team_b,
             score_a=excluded.score_a, score_b=excluded.score_b,
             winner_team=excluded.winner_team,
             source_url=excluded.source_url,
             updated_at=CURRENT_TIMESTAMP""",
        (internal_id, f"faceit:{mid}", mid, room_url, "FACEIT matchroom",
         region, dt.datetime.now(dt.timezone.utc).date().isoformat(), status,
         slug_a, slug_b, sa, sb, winner, room_url))

    side_slug = {"A": slug_a, "B": slug_b}
    for roster in parsed.get("rosters", []):
        tid = side_slug.get(roster.get("side"))
        if not tid:
            counts["skipped"] += 1
            continue
        con.execute("DELETE FROM match_rosters WHERE match_id=? AND team_id=? "
                    "AND source='faceit'", (internal_id, tid))
        for pl in roster["players"]:
            pid = upsert_player(con, pl["nickname"], pl.get("faceitPlayerId"),
                                tid, pl.get("role"))
            con.execute(
                """INSERT OR REPLACE INTO match_rosters
                   (match_id, team_id, player_id, source)
                   VALUES (?,?,?, 'faceit')""", (internal_id, tid, pid))
            counts["players"] += 1
        counts["rosters"] += 1

    # Replace FACEIT-sourced map rows only. Tracker snapshots are keyed to
    # map_result_id; to preserve them across re-ingest we reuse existing
    # map_result ids by (match, map_order) instead of deleting blindly.
    con.execute("DELETE FROM hero_bans WHERE match_id=? AND source='faceit'", (internal_id,))
    con.execute("DELETE FROM map_veto_events WHERE match_id=? AND source='faceit'", (internal_id,))

    for mm in parsed["maps"]:
        mslug = map_slug(mm["name"])
        if mslug:
            con.execute(
                "INSERT OR IGNORE INTO game_maps (id, name, mode) VALUES (?,?,?)",
                (mslug, mm["name"], mm.get("mode") or "Unknown"))
        win = mm.get("winner")
        win_team = slug_a if win == "A" else slug_b if win == "B" else None
        code = mm.get("replayCode")
        if code:
            counts["replay_codes"] += 1

        existing = con.execute(
            "SELECT id FROM map_results WHERE match_id=? AND map_order=?",
            (internal_id, mm["order"])).fetchone()
        if existing:
            mr_id = existing["id"]
            con.execute(
                """UPDATE map_results SET map_id=?, score_a=?, score_b=?,
                     winner_team=?, picked_by_team=?, veto_action=?,
                     replay_code=?, source='faceit' WHERE id=?""",
                (mslug or "unknown", mm.get("scoreA"), mm.get("scoreB"),
                 win_team, _pick_team_slug(mm.get("pickedBy"), slug_a, slug_b),
                 mm.get("vetoAction"), code, mr_id))
        else:
            cur = con.execute(
                """INSERT INTO map_results
                     (match_id, map_order, map_id, score_a, score_b, winner_team,
                      picked_by_team, veto_action, replay_code, source)
                   VALUES (?,?,?,?,?,?,?,?,?, 'faceit')""",
                (internal_id, mm["order"], mslug or "unknown",
                 mm.get("scoreA"), mm.get("scoreB"), win_team,
                 _pick_team_slug(mm.get("pickedBy"), slug_a, slug_b),
                 mm.get("vetoAction"), code))
            mr_id = cur.lastrowid
        counts["maps"] += 1

        for ban in mm.get("heroBans", []):
            con.execute(
                """INSERT INTO hero_bans
                     (match_id, map_result_id, map_order, team_id, hero_id,
                      ban_order, source)
                   VALUES (?,?,?,?,?,?, 'faceit')""",
                (internal_id, mr_id, mm["order"],
                 _pick_team_slug(ban.get("team"), slug_a, slug_b),
                 slugify(ban["hero"], "hero"), ban.get("order")))
            counts["bans"] += 1

    con.commit()
    counts["_internal_id"] = internal_id
    counts["_match_id"] = mid
    counts["_status"] = status
    return counts


def record_cache(con, url, res):
    con.execute(
        """INSERT OR REPLACE INTO faceit_raw_cache
           (cache_key, url, fetched_at, status_code, body_path, sha256, error)
           VALUES (?,?,?,?,?,?,?)""",
        (cache_key_for(url), url, dt.datetime.now(dt.timezone.utc).isoformat(),
         res.get("status"), res.get("body_path"), res.get("sha256"), res.get("error")))
    con.commit()


def ingest(con, parsed, room_url, region, dry_run=False):
    """Programmatic entry used by tests. Returns (counts, warnings)."""
    warns = validate_parsed(parsed)
    if dry_run:
        return None, warns
    counts = upsert(con, parsed, room_url, region)
    return counts, warns


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest a FACEIT OW2 matchroom.")
    ap.add_argument("--room-url")
    ap.add_argument("--match-id")
    ap.add_argument("--fixture", "--from-cache", dest="fixture",
                    help="parse a local JSON/HTML file instead of the network")
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE)
    ap.add_argument("--region", default="Unknown")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    match_id = (fp.extract_match_id_from_url(args.room_url) or
                (args.match_id.strip() if args.match_id else None))
    if not match_id and not args.fixture:
        ap.error("provide --room-url, --match-id, or --fixture")
    room_url = canonical_room_url(match_id or "unknown", args.room_url)

    if args.fixture:
        print(f"Loading fixture: {args.fixture}")
        res = load_local(args.fixture)
    else:
        print(f"Fetching room (cached): {room_url}")
        res = fetch_to_cache(room_url, args.cache_dir)
        if not res["ok"]:
            print(f"  fetch unavailable: {res['error']} — nothing to parse.")

    parsed = fp.normalize_faceit_match({"faceitMatchId": match_id,
                                        "faceitRoomUrl": room_url})
    if res.get("text"):
        parsed = parse_source(res["text"], room_url)
        if not parsed["faceitMatchId"] and match_id:
            parsed["faceitMatchId"] = match_id

    warns = validate_parsed(parsed)

    print(f"  match id : {parsed['faceitMatchId']}")
    print(f"  teams    : {parsed['teams'][0]['name']} vs {parsed['teams'][1]['name']}")
    print(f"  score    : {parsed['score']['teamA']}-{parsed['score']['teamB']}")
    print(f"  maps     : {len(parsed['maps'])}  "
          f"(replay codes: {sum(1 for m in parsed['maps'] if m['replayCode'])}, "
          f"bans: {sum(len(m['heroBans']) for m in parsed['maps'])})")
    print(f"  rosters  : {sum(len(r['players']) for r in parsed['rosters'])} players")
    for w in warns:
        print(f"  WARN: {w}")

    if args.dry_run:
        print("Dry run: no database writes.")
        return

    con = db.connect()
    db.init_schema(con)
    if not args.fixture:
        record_cache(con, room_url, res)
    counts = upsert(con, parsed, room_url, args.region)
    print("Summary:")
    print(f"  matches inserted/updated: 1 ({counts['_status']})")
    print(f"  teams inserted/updated:   {counts['teams']}")
    print(f"  maps inserted/updated:    {counts['maps']}")
    print(f"  replay codes found:       {counts['replay_codes']}")
    print(f"  hero bans found:          {counts['bans']}")
    print(f"  rosters found:            {counts['rosters']} "
          f"({counts['players']} players)")
    print(f"  warnings:                 {len(warns)}")
    print(f"  skipped rows:             {counts['skipped']}")
    print(f"  match row id:             {counts['_internal_id']}")


if __name__ == "__main__":
    main()
