#!/usr/bin/env python3
"""
video_to_snapshots.py — Stage V4: accepted CV readings -> comp_snapshots.

Runs hero_overlay_detect over a match's kept gameplay frames and writes each
accepted reading into comp_snapshots (source='cv') plus its five
snapshot_heroes rows, one snapshot per team per frame. Confidence is stored
per snapshot (team mean) and per slot.

Provenance & override rules honored here:
  - Every row written by this module is source='cv'. It never writes,
    edits, or deletes source='manual' rows. Manual corrections still win at
    export time (export_data prefers manual over cv), and deleting a manual
    correction lets the cv rows surface again — this module keeps them intact.
  - Dedup is by (frame_hash, team_id): re-running over the same frames adds
    nothing, so the stage is idempotent and safe to resume.

Map assignment (map_result_id) is left to map_sync; pass --sync to run it.

Usage:
  python3 pipeline/video_to_snapshots.py --layout L.json --match m01
  python3 pipeline/video_to_snapshots.py --layout L.json --match m01 --sync
"""
from __future__ import annotations
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import capture  # noqa: E402
import hero_overlay_detect as hod  # noqa: E402

WORK_DIR = capture.WORK_DIR


def _write_one(con, match_id: str, team_id: str, offset: int,
               heroes_slots: list[dict], confidence: float,
               frame_hash: str) -> bool:
    """Insert one team's snapshot. Returns False if it already exists."""
    try:
        cur = con.execute(
            """INSERT INTO comp_snapshots
               (match_id, map_result_id, team_id, stream_offset_seconds,
                overall_confidence, frame_hash, source)
               VALUES (?,?,?,?,?,?, 'cv')""",
            (match_id, None, team_id, offset, confidence, frame_hash),
        )
    except sqlite3.IntegrityError:
        return False  # (frame_hash, team_id) already recorded
    con.executemany(
        "INSERT INTO snapshot_heroes (snapshot_id, slot, hero_id, confidence)"
        " VALUES (?,?,?,?)",
        [(cur.lastrowid, i, s["hero"], s["score"])
         for i, s in enumerate(heroes_slots, start=1)],
    )
    return True


def snapshots_for_match(con, match_id: str, frames_dir: str, layout: dict,
                        lib: dict, quarantine_dir: str | None = None) -> dict:
    """Detect + persist CV snapshots for one match. Returns a report dict."""
    m = con.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    if m is None:
        raise SystemExit(f"unknown match id: {match_id}")
    res = hod.detect_dir(frames_dir, layout, lib, quarantine_dir=quarantine_dir)

    written = 0
    for item in res["accepted"]:
        if _write_one(con, match_id, m["team_a"], item["offset"],
                      item["a"]["slots"], item["a"]["confidence"],
                      item["frame_hash"]):
            written += 1
        if _write_one(con, match_id, m["team_b"], item["offset"],
                      item["b"]["slots"], item["b"]["confidence"],
                      item["frame_hash"]):
            written += 1
    con.commit()
    return {"accepted_frames": len(res["accepted"]),
            "quarantined_frames": len(res["quarantined"]),
            "snapshots_written": written}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", required=True)
    ap.add_argument("--match", required=True)
    ap.add_argument("--frames-dir", help="override work/{match}/frames")
    ap.add_argument("--templates-dir", help="override hero templates dir")
    ap.add_argument("--sync", action="store_true",
                    help="run map_sync after writing snapshots")
    ap.add_argument("--interval", type=int, default=None,
                    help="map_sync sample interval (default: from layout)")
    ap.add_argument("--gap-factor", type=float, default=2.5)
    args = ap.parse_args()

    layout = capture.load_layout(args.layout)
    frames_dir = args.frames_dir or os.path.join(WORK_DIR, args.match, "frames")
    qdir = os.path.join(WORK_DIR, args.match, "quarantine")

    con = db.connect()
    lib = hod.load_lib(layout, args.templates_dir)
    rep = snapshots_for_match(con, args.match, frames_dir, layout, lib,
                              quarantine_dir=qdir)
    print(f"[video_to_snapshots] {args.match}: "
          f"accepted {rep['accepted_frames']} frames, "
          f"wrote {rep['snapshots_written']} cv snapshots, "
          f"quarantined {rep['quarantined_frames']}.")

    if args.sync:
        import map_sync  # noqa: E402
        interval = args.interval or layout.get("sample_interval_seconds", 300)
        map_sync.sync_match(con, args.match, interval=interval,
                            gap_factor=args.gap_factor, report_only=False)
    else:
        print(f"[video_to_snapshots] next: python3 pipeline/map_sync.py "
              f"--match {args.match}")


if __name__ == "__main__":
    main()
