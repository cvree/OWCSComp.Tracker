#!/usr/bin/env python3
"""
ingest_faceit_batch.py — ingest many FACEIT rooms from a source list.

Reads data/sources/faceit_rooms.json and ingests each room using the same
logic as ingest_faceit (fetch/cache -> parse -> validate -> upsert facts).
Resilient: one bad room never stops the rest. Idempotent: duplicate URLs
and already-ingested match ids are skipped.

FACEIT facts only — never hero comps. See ingest_faceit.py for the split.

Usage:
  python3 pipeline/ingest_faceit_batch.py                 # ingest all rooms
  python3 pipeline/ingest_faceit_batch.py --dry-run       # parse+report, no writes
  python3 pipeline/ingest_faceit_batch.py --limit 5       # first 5 rooms
  python3 pipeline/ingest_faceit_batch.py --source path.json
  python3 pipeline/ingest_faceit_batch.py --cache-dir data/raw/faceit
  python3 pipeline/ingest_faceit_batch.py --offline       # only use cached bodies
"""
from __future__ import annotations
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import faceit_parser as fp  # noqa: E402
import ingest_faceit as ing  # noqa: E402

DEFAULT_SOURCE = os.path.join(db.REPO_ROOT, "data", "sources", "faceit_rooms.json")


def load_rooms(path: str) -> list[dict]:
    """Return the room entries. Missing/broken file -> empty list (no crash)."""
    if not os.path.exists(path):
        print(f"[batch] no source file at {path} — nothing to ingest.")
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (ValueError, OSError) as e:
        print(f"[batch] could not read {path}: {e}")
        return []
    rooms = payload.get("rooms", []) if isinstance(payload, dict) else []
    return [r for r in rooms if isinstance(r, dict) and r.get("url")]


def existing_match_ids(con) -> set[str]:
    return {r["faceit_match_id"] for r in
            con.execute("SELECT faceit_match_id FROM matches "
                        "WHERE faceit_match_id IS NOT NULL")}


def run_batch(con, rooms: list[dict], cache_dir: str, dry_run: bool = False,
              limit: int | None = None, offline: bool = False) -> dict:
    """Ingest each room. Returns a summary dict. Never raises for one bad room."""
    summary = {"rooms_total": len(rooms), "ingested": 0, "skipped": 0,
               "failed": 0, "warnings": 0, "maps": 0, "replay_codes": 0,
               "bans": 0, "players": 0}
    seen_urls: set[str] = set()
    already = existing_match_ids(con) if not dry_run else set()

    todo = rooms[:limit] if limit else rooms
    for i, room in enumerate(todo, start=1):
        url = (room.get("url") or "").strip()
        region = room.get("region") or "Unknown"
        label = room.get("stage") or room.get("notes") or ""

        # dedupe within this run by URL
        if url in seen_urls:
            print(f"[batch] {i}/{len(todo)} SKIP duplicate url in source list: {url}")
            summary["skipped"] += 1
            continue
        seen_urls.add(url)

        mid = fp.extract_match_id_from_url(url)
        if mid and mid in already:
            print(f"[batch] {i}/{len(todo)} SKIP already ingested: {mid}")
            summary["skipped"] += 1
            continue

        try:
            room_url = ing.canonical_room_url(mid or "unknown", url)
            if offline:
                # only use a previously cached body; skip if none exists
                key = ing.cache_key_for(room_url)
                body_path = os.path.join(cache_dir, f"{key}.body")
                if not os.path.exists(body_path):
                    print(f"[batch] {i}/{len(todo)} SKIP offline, no cache: {room_url}")
                    summary["skipped"] += 1
                    continue
                res = ing.load_local(body_path)
            else:
                res = ing.fetch_to_cache(room_url, cache_dir)

            parsed = fp.normalize_faceit_match(
                {"faceitMatchId": mid, "faceitRoomUrl": room_url})
            if res.get("text"):
                parsed = ing.parse_source(res["text"], room_url)
                if not parsed["faceitMatchId"] and mid:
                    parsed["faceitMatchId"] = mid
            elif not res.get("ok"):
                print(f"[batch] {i}/{len(todo)} FAIL fetch: {room_url} "
                      f"({res.get('error')})")
                summary["failed"] += 1
                continue

            warns = ing.validate_parsed(parsed)
            summary["warnings"] += len(warns)

            if dry_run:
                print(f"[batch] {i}/{len(todo)} DRY {parsed['faceitMatchId']}: "
                      f"{parsed['teams'][0]['name']} vs {parsed['teams'][1]['name']}"
                      f" · {len(parsed['maps'])} maps"
                      + (f" · {len(warns)} warn" if warns else ""))
                summary["ingested"] += 1
                summary["maps"] += len(parsed["maps"])
                summary["replay_codes"] += sum(1 for m in parsed["maps"] if m["replayCode"])
                summary["bans"] += sum(len(m["heroBans"]) for m in parsed["maps"])
                continue

            if not offline:
                ing.record_cache(con, room_url, res)
            counts = ing.upsert(con, parsed, room_url, region)
            if counts.get("_match_id"):
                already.add(counts["_match_id"])
            summary["ingested"] += 1
            summary["maps"] += counts["maps"]
            summary["replay_codes"] += counts["replay_codes"]
            summary["bans"] += counts["bans"]
            summary["players"] += counts["players"]
            print(f"[batch] {i}/{len(todo)} OK {counts['_match_id']} "
                  f"({counts['maps']} maps, {counts['replay_codes']} codes, "
                  f"{counts['bans']} bans, {counts['players']} players)"
                  + (f" · {len(warns)} warn" if warns else ""))
        except Exception as e:  # one bad room must never kill the batch
            print(f"[batch] {i}/{len(todo)} FAIL {url}: {e!r}")
            summary["failed"] += 1

    return summary


def print_summary(s: dict, dry_run: bool) -> None:
    print("\nBatch summary" + (" (dry run)" if dry_run else "") + ":")
    print(f"  rooms total:   {s['rooms_total']}")
    print(f"  ingested:      {s['ingested']}")
    print(f"  skipped:       {s['skipped']}")
    print(f"  failed:        {s['failed']}")
    print(f"  warnings:      {s['warnings']}")
    print(f"  maps found:    {s['maps']}")
    print(f"  replay codes:  {s['replay_codes']}")
    print(f"  bans found:    {s['bans']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=DEFAULT_SOURCE)
    ap.add_argument("--cache-dir", default=ing.DEFAULT_CACHE)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--offline", action="store_true",
                    help="only use already-cached room bodies; skip uncached")
    args = ap.parse_args()

    rooms = load_rooms(args.source)
    con = db.connect()
    if not args.dry_run:
        db.init_schema(con)
    summary = run_batch(con, rooms, args.cache_dir, dry_run=args.dry_run,
                        limit=args.limit, offline=args.offline)
    print_summary(summary, args.dry_run)


if __name__ == "__main__":
    main()
