#!/usr/bin/env python3
"""
map_sync.py — Stage 3C: assign comp snapshots to the correct map.

The capture stage only kept live-gameplay frames, so the snapshot offsets
naturally form k blocks separated by inter-map breaks. Blocks are matched
in order to the match's known map_results (block 1 → map_order 1, ...).

Safety rails:
  - If detected blocks != maps played, the match is FLAGGED and nothing
    is assigned — no guessing. Fix by re-running capture with a denser
    interval or checking quarantined frames.
  - Snapshots that fall in no block stay unassigned (excluded from stats).

A gap of more than `gap_factor` × sample interval starts a new block
(breaks between maps are minutes long; in-map gaps are one interval).

Usage:
  python3 pipeline/map_sync.py --match m01
  python3 pipeline/map_sync.py --match m01 --interval 300 --gap-factor 2.5
  python3 pipeline/map_sync.py --match m01 --report   # verify, change nothing
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402


def blocks_from_offsets(offsets: list[int], max_gap: float) -> list[list[int]]:
    """Split sorted offsets into blocks wherever the gap exceeds max_gap."""
    blocks, cur = [], []
    prev = None
    for off in sorted(set(offsets)):
        if prev is not None and off - prev > max_gap:
            blocks.append(cur)
            cur = []
        cur.append(off)
        prev = off
    if cur:
        blocks.append(cur)
    return blocks


def sync_match(con, match_id: str, interval: int, gap_factor: float,
               report_only: bool) -> None:
    maps = con.execute(
        "SELECT * FROM map_results WHERE match_id=? ORDER BY map_order",
        (match_id,),
    ).fetchall()
    snaps = con.execute(
        "SELECT * FROM comp_snapshots WHERE match_id=? "
        "ORDER BY stream_offset_seconds",
        (match_id,),
    ).fetchall()
    if not maps:
        raise SystemExit(f"[{match_id}] no map_results — run results ingest first")
    if not snaps:
        raise SystemExit(f"[{match_id}] no snapshots — run detect.py first")

    offsets = [s["stream_offset_seconds"] for s in snaps]
    blocks = blocks_from_offsets(offsets, max_gap=gap_factor * interval)

    print(f"[{match_id}] maps played: {len(maps)}, "
          f"gameplay blocks detected: {len(blocks)}")
    for i, b in enumerate(blocks, start=1):
        mm = lambda s: f"{s // 60}m"
        print(f"  block {i}: {mm(b[0])} → {mm(b[-1])} ({len(b)} offsets)")

    if len(blocks) != len(maps):
        print(f"[{match_id}] FLAGGED: block count != map count — "
              f"nothing assigned. Re-capture with a shorter interval, or "
              f"check quarantined frames for a missed map.")
        return

    if report_only:
        print(f"[{match_id}] report only — assignment would be valid.")
        return

    # offset → map_result_id via its block index
    assign = {}
    for block, mr in zip(blocks, maps):
        for off in block:
            assign[off] = mr["id"]

    n = 0
    for s in snaps:
        mr_id = assign.get(s["stream_offset_seconds"])
        if mr_id is not None:
            con.execute("UPDATE comp_snapshots SET map_result_id=? WHERE id=?",
                        (mr_id, s["id"]))
            n += 1
    con.commit()
    print(f"[{match_id}] assigned {n}/{len(snaps)} snapshots to maps.")

    # verification report: heroes per team per map
    for mr in maps:
        row = con.execute(
            "SELECT map_id FROM map_results WHERE id=?", (mr["id"],)).fetchone()
        print(f"  map {mr['map_order']} ({row['map_id']}):")
        for team in con.execute(
            "SELECT DISTINCT team_id FROM comp_snapshots WHERE map_result_id=?",
            (mr["id"],),
        ):
            heroes = [r["hero_id"] for r in con.execute(
                """SELECT DISTINCT sh.hero_id FROM snapshot_heroes sh
                   JOIN comp_snapshots cs ON cs.id = sh.snapshot_id
                   WHERE cs.map_result_id=? AND cs.team_id=?""",
                (mr["id"], team["team_id"]),
            )]
            print(f"    {team['team_id']}: {', '.join(heroes)}")
    print(f"[{match_id}] next: python3 pipeline/export_data.py")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", required=True)
    ap.add_argument("--interval", type=int, default=300,
                    help="capture sample interval used (seconds)")
    ap.add_argument("--gap-factor", type=float, default=2.5,
                    help="gap > factor×interval starts a new map block")
    ap.add_argument("--report", action="store_true",
                    help="verify only; assign nothing")
    args = ap.parse_args()

    con = db.connect()
    sync_match(con, args.match, args.interval, args.gap_factor, args.report)


if __name__ == "__main__":
    main()
