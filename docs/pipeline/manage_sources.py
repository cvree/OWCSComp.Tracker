#!/usr/bin/env python3
"""
manage_sources.py — safe CRUD for data/sources/faceit_rooms.json.

Keeps the FACEIT room source list tidy without hand-editing JSON. Preserves
the "_readme" key and pretty formatting, validates URL shape, and dedupes by
resolved match id (falling back to URL).

Commands:
  python3 pipeline/manage_sources.py list
  python3 pipeline/manage_sources.py add --url "<room url>" \
      [--region NA] [--stage "Stage 2"] [--notes "..."]
  python3 pipeline/manage_sources.py remove --url  "<room url>"
  python3 pipeline/manage_sources.py remove --match-id "1-...."
  python3 pipeline/manage_sources.py dedupe
  python3 pipeline/manage_sources.py validate

All commands accept --source PATH (default data/sources/faceit_rooms.json).
"""
from __future__ import annotations
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import faceit_parser as fp  # noqa: E402

DEFAULT_SOURCE = os.path.join(db.REPO_ROOT, "data", "sources", "faceit_rooms.json")


def load(path: str) -> dict:
    if not os.path.exists(path):
        return {"rooms": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError):
        return {"rooms": []}
    if not isinstance(data, dict):
        data = {"rooms": []}
    data.setdefault("rooms", [])
    return data


def save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Write atomically and preserve pretty 2-space formatting + key order.
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def room_key(room: dict) -> str:
    """Identity for dedupe: resolved match id, else the raw url."""
    mid = fp.extract_match_id_from_url(room.get("url"))
    return mid or (room.get("url") or "").strip()


def valid_url(url: str) -> bool:
    return bool(fp.extract_match_id_from_url(url))


# --------------------------------------------------------------- commands
def cmd_list(data: dict, args) -> int:
    rooms = data["rooms"]
    if not rooms:
        print("No rooms configured.")
        return 0
    print(f"{len(rooms)} room(s):")
    for i, r in enumerate(rooms, start=1):
        mid = fp.extract_match_id_from_url(r.get("url")) or "?"
        tags = " · ".join(x for x in (r.get("region"), r.get("stage")) if x)
        print(f"  {i}. {mid}  [{tags or 'no tags'}]")
        print(f"     {r.get('url')}")
        if r.get("notes"):
            print(f"     note: {r['notes']}")
    return 0


def cmd_add(data: dict, args) -> int:
    if not valid_url(args.url):
        print(f"Refusing to add: not a valid FACEIT room URL: {args.url}")
        return 1
    key = fp.extract_match_id_from_url(args.url)
    for r in data["rooms"]:
        if room_key(r) == key:
            print(f"Already present ({key}); updating tags instead of duplicating.")
            if args.region: r["region"] = args.region
            if args.stage:  r["stage"] = args.stage
            if args.notes:  r["notes"] = args.notes
            save(args.source, data)
            return 0
    entry = {"url": args.url.strip()}
    if args.region: entry["region"] = args.region
    if args.stage:  entry["stage"] = args.stage
    if args.notes:  entry["notes"] = args.notes
    data["rooms"].append(entry)
    save(args.source, data)
    print(f"Added {key} ({len(data['rooms'])} total).")
    return 0


def cmd_remove(data: dict, args) -> int:
    if not args.url and not args.match_id:
        print("Provide --url or --match-id.")
        return 1
    target = (args.match_id.strip() if args.match_id
              else fp.extract_match_id_from_url(args.url) or (args.url or "").strip())
    before = len(data["rooms"])
    data["rooms"] = [r for r in data["rooms"] if room_key(r) != target]
    removed = before - len(data["rooms"])
    save(args.source, data)
    print(f"Removed {removed} room(s) matching {target}.")
    return 0 if removed else 1


def cmd_dedupe(data: dict, args) -> int:
    seen, out, dropped = set(), [], 0
    for r in data["rooms"]:
        k = room_key(r)
        if k in seen:
            dropped += 1
            continue
        seen.add(k)
        out.append(r)
    data["rooms"] = out
    save(args.source, data)
    print(f"Deduped: dropped {dropped}, {len(out)} remain.")
    return 0


def cmd_validate(data: dict, args) -> int:
    bad = [r for r in data["rooms"] if not valid_url(r.get("url", ""))]
    keys = [room_key(r) for r in data["rooms"]]
    dupes = len(keys) - len(set(keys))
    print(f"{len(data['rooms'])} rooms · {len(bad)} invalid URL(s) · {dupes} duplicate(s).")
    for r in bad:
        print(f"  invalid: {r.get('url')}")
    return 1 if (bad or dupes) else 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Manage FACEIT room source list.")
    ap.add_argument("--source", default=DEFAULT_SOURCE)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    a = sub.add_parser("add")
    a.add_argument("--url", required=True)
    a.add_argument("--region"); a.add_argument("--stage"); a.add_argument("--notes")
    rm = sub.add_parser("remove")
    rm.add_argument("--url"); rm.add_argument("--match-id")
    sub.add_parser("dedupe")
    sub.add_parser("validate")
    args = ap.parse_args()

    data = load(args.source)
    cmds = {"list": cmd_list, "add": cmd_add, "remove": cmd_remove,
            "dedupe": cmd_dedupe, "validate": cmd_validate}
    sys.exit(cmds[args.cmd](data, args))


if __name__ == "__main__":
    main()
