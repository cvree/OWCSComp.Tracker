#!/usr/bin/env python3
"""
apply_corrections.py — the manual/admin path for comp data.

Reads corrections/corrections.json (authored by hand or with admin.html)
and writes them into the DB as comp snapshots with source='manual', which
OVERRIDE any CV/replay-derived snapshots for that map+team at export time.
The CV data is never deleted — corrections are additive and reversible by
removing the entry and re-running (which deletes only manual rows).

The corrections file is committed to the repo, so git history is the audit
trail for every manual change.

File format (corrections/corrections.json):
{
  "corrections": [
    {
      "match": "m12",                 // internal match id (see matches page)
      "mapOrder": 1,                  // FACEIT map order, 1-based
      "team": "falcons",              // team id
      "openerComp": ["winston","tracer","genji","kiriko","juno"],
      "swaps": ["sojourn"],           // optional: heroes swapped in mid-map
      "note": "read from replay code M8W3D6",
      "author": "connor"              // optional
    }
  ]
}

Rules enforced:
  - match, mapOrder, team must exist; every hero id must exist;
  - openerComp must be exactly 5 unique heroes;
  - swaps must not repeat opener heroes.
Invalid entries are reported and skipped; valid ones still apply.
Re-running is idempotent: each entry replaces the previous manual rows
for its (map, team).

Usage:
  python3 pipeline/apply_corrections.py                 # default file
  python3 pipeline/apply_corrections.py --file path.json
  python3 pipeline/apply_corrections.py --dry-run       # validate only
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

DEFAULT_FILE = os.path.join(db.REPO_ROOT, "corrections", "corrections.json")


def validate(con, c: dict, idx: int) -> tuple[dict | None, str | None]:
    where = f"corrections[{idx}]"
    for key in ("match", "mapOrder", "team", "openerComp"):
        if key not in c:
            return None, f"{where}: missing '{key}'"
    mr = con.execute(
        "SELECT id FROM map_results WHERE match_id=? AND map_order=?",
        (c["match"], c["mapOrder"])).fetchone()
    if not mr:
        return None, f"{where}: no map {c['mapOrder']} in match '{c['match']}'"
    m = con.execute("SELECT team_a, team_b FROM matches WHERE id=?",
                    (c["match"],)).fetchone()
    if c["team"] not in (m["team_a"], m["team_b"]):
        return None, f"{where}: team '{c['team']}' did not play match '{c['match']}'"
    opener = c["openerComp"]
    swaps = c.get("swaps", []) or []
    if len(opener) != 5 or len(set(opener)) != 5:
        return None, f"{where}: openerComp must be exactly 5 unique heroes"
    if set(swaps) & set(opener):
        return None, f"{where}: swaps repeat opener heroes: {set(swaps) & set(opener)}"
    known = {r["id"] for r in con.execute("SELECT id FROM heroes")}
    bad = [h for h in opener + swaps if h not in known]
    if bad:
        return None, f"{where}: unknown hero ids {bad}"
    return {"map_result_id": mr["id"], **c, "swaps": swaps}, None


def apply_one(con, c: dict) -> None:
    """Replace this (map, team)'s manual snapshots with the correction."""
    con.execute(
        "DELETE FROM comp_snapshots WHERE map_result_id=? AND team_id=? "
        "AND source='manual'", (c["map_result_id"], c["team"]))

    def insert(offset: int, heroes: list[str]) -> None:
        fh = "man-" + hashlib.sha1(
            f"{c['map_result_id']}:{c['team']}:{offset}:{','.join(heroes)}"
            .encode()).hexdigest()[:12]
        cur = con.execute(
            """INSERT INTO comp_snapshots
               (match_id, map_result_id, team_id, stream_offset_seconds,
                overall_confidence, frame_hash, source)
               VALUES (?,?,?,?,1.0,?, 'manual')""",
            (c["match"], c["map_result_id"], c["team"], offset, fh))
        con.executemany(
            "INSERT INTO snapshot_heroes (snapshot_id, slot, hero_id, confidence)"
            " VALUES (?,?,?,1.0)",
            [(cur.lastrowid, i, h) for i, h in enumerate(heroes, start=1)])

    insert(0, c["openerComp"])
    if c["swaps"]:
        # Second snapshot represents the post-swap state; export derives
        # playedHeroes as the union and swaps as (played − opener).
        insert(1, c["openerComp"] + c["swaps"])


def apply_file(con, path: str, dry_run: bool = False) -> tuple[int, int]:
    """Apply a corrections file to an open connection. Returns (ok, skipped).
    Validates every entry; invalid ones are skipped with a message."""
    if not con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name='map_results'").fetchone():
        print("[corrections] DB not initialized yet — run init_db.py first. "
              "Nothing applied.")
        return (0, 0)
    if not os.path.exists(path):
        print(f"[corrections] no file at {path} — nothing to apply.")
        return (0, 0)
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    entries = payload.get("corrections", [])
    if not entries:
        print("[corrections] file present but empty — nothing to apply.")
        return (0, 0)

    ok = bad = 0
    for i, c in enumerate(entries):
        valid, err = validate(con, c, i)
        if err:
            print(f"[corrections] SKIP {err}")
            bad += 1
            continue
        if not dry_run:
            apply_one(con, valid)
        ok += 1
        note = f" — {c['note']}" if c.get("note") else ""
        print(f"[corrections] {'valid' if dry_run else 'applied'}: "
              f"{c['match']} map {c['mapOrder']} {c['team']}{note}")
    if not dry_run:
        con.commit()
    print(f"[corrections] {ok} applied, {bad} skipped."
          + (" (dry run)" if dry_run else ""))
    return (ok, bad)


def main_from_file(path: str, dry_run: bool = False) -> tuple[int, int]:
    """Programmatic entry with an explicit file path."""
    return apply_file(db.connect(), path, dry_run=dry_run)


def main_from(args=None) -> None:
    """Programmatic entry (used by run_batch): default file, non-dry."""
    apply_file(db.connect(), DEFAULT_FILE, dry_run=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=DEFAULT_FILE)
    ap.add_argument("--dry-run", action="store_true",
                    help="validate only, change nothing")
    args = ap.parse_args()
    apply_file(db.connect(), args.file, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
