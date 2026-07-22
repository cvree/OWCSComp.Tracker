#!/usr/bin/env python3
"""
ingest_obssojourn.py — ONE command to ingest every map of a prepped POV
match. The map windows were already seeded into map_results by
prep_obssojourn_match.py (from the video's chapter timestamps), so this
orchestrator just walks them and runs pipeline/ingest_map.py per map with
all the right arguments — dry-run first by default, --write to persist.

  python3 pipeline/ingest_obssojourn.py --clip work/clips/krgf.mp4 \\
      --match m-cr-zeta-krgf [--maps 1,2] [--every 5] [--write]

For each map it derives: the window (vod_start_seconds -> next map's start,
last map -> clip duration via ffprobe or --end-last), the ingest id
(<match>-m<order>), the layout (the source's registered layout), and the
team ids from the match row. Stops at the first failing map with the exact
resume command. Safe to re-run; ingest_map reruns replace their own rows.
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

SOURCES_PATH = os.path.join(db.REPO_ROOT, "data", "sources",
                            "video_sources.json")


def clip_duration(clip: str) -> float | None:
    """Length of the local clip in seconds via ffprobe, or None."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", clip],
            capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip())
    except Exception:
        return None


def layout_for_source(source_id: str | None) -> str | None:
    try:
        with open(SOURCES_PATH, encoding="utf-8") as f:
            for s in json.load(f).get("sources", []):
                if s.get("id") == source_id:
                    return s.get("layout")
    except (OSError, ValueError):
        pass
    return None


def build_plan(con, match_id: str, clip: str, every: float,
               maps_filter: set[int] | None, end_last: float | None,
               write: bool, extra: list[str]) -> dict:
    """The full per-map command plan (pure — nothing executed here)."""
    m = con.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    if m is None:
        return {"ok": False, "error": f"unknown match '{match_id}' — run "
                "prep_obssojourn_match.py first"}
    source_id = m["source_ref"]
    layout = layout_for_source(source_id)
    if not layout:
        return {"ok": False, "error": (
            f"no layout registered for source '{source_id}' — calibrate "
            "first (see docs/INGEST-CR-ZETA-KRGF.md Part 3)")}
    rows = con.execute(
        """SELECT map_order, map_id, vod_start_seconds FROM map_results
           WHERE match_id=? AND vod_start_seconds IS NOT NULL
           ORDER BY map_order""", (match_id,)).fetchall()
    if not rows:
        return {"ok": False, "error": f"no map windows on '{match_id}' — "
                "run prep_obssojourn_match.py first"}

    dur = end_last if end_last is not None else clip_duration(clip)
    steps = []
    for i, r in enumerate(rows):
        order = r["map_order"]
        if maps_filter and order not in maps_filter:
            continue
        start = r["vod_start_seconds"]
        end = (rows[i + 1]["vod_start_seconds"] if i + 1 < len(rows)
               else dur)
        if end is None:
            return {"ok": False, "error": (
                f"can't determine the last map's end — install ffprobe or "
                f"pass --end-last <seconds> (map {order} starts at {start}s)")}
        cmd = [sys.executable, os.path.join("pipeline", "ingest_map.py"),
               "--clip", clip, "--clip-offset", "0",
               "--start", str(int(start)), "--end", str(int(end)),
               "--layout", layout, "--source-id", source_id,
               "--ingest-id", f"{match_id}-m{order}",
               "--match", match_id, "--map-order", str(order),
               "--map-id", r["map_id"],
               "--team-a", m["team_a"], "--team-b", m["team_b"],
               "--every", str(every)]
        if write:
            cmd.append("--write")
        cmd += extra
        steps.append({"order": order, "mapId": r["map_id"],
                      "window": [int(start), int(end)], "cmd": cmd})
    return {"ok": True, "matchId": match_id, "sourceId": source_id,
            "layout": layout, "teamA": m["team_a"], "teamB": m["team_b"],
            "clipDuration": dur, "steps": steps, "write": write}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clip", required=True, help="local video file")
    ap.add_argument("--match", default="m-cr-zeta-krgf")
    ap.add_argument("--maps", help="comma list of map orders (default: all)")
    ap.add_argument("--every", type=float, default=5.0)
    ap.add_argument("--end-last", type=float,
                    help="end second for the final map (default: clip length)")
    ap.add_argument("--write", action="store_true",
                    help="persist to the DB (default: dry-run per map)")
    ap.add_argument("--plan-only", action="store_true",
                    help="print the per-map commands and exit")
    ap.add_argument("extra", nargs="*",
                    help="extra args passed through to ingest_map.py "
                         "(e.g. --ocr-guard)")
    args = ap.parse_args()

    maps_filter = ({int(x) for x in args.maps.split(",")}
                   if args.maps else None)
    plan = build_plan(db.connect(), args.match, args.clip, args.every,
                      maps_filter, args.end_last, args.write, args.extra)
    if not plan.get("ok"):
        raise SystemExit(f"ERROR: {plan['error']}")

    print(f"[orchestrator] {plan['matchId']}: {plan['teamA']} vs "
          f"{plan['teamB']} — {len(plan['steps'])} map(s), layout "
          f"{plan['layout']}, {'WRITE' if plan['write'] else 'DRY-RUN'}")
    for s in plan["steps"]:
        print(f"  map {s['order']} ({s['mapId']}): "
              f"{s['window'][0]}-{s['window'][1]}s")
    if args.plan_only:
        for s in plan["steps"]:
            print("\n$ " + " ".join(s["cmd"]))
        return

    for s in plan["steps"]:
        print(f"\n[orchestrator] ==== map {s['order']} ({s['mapId']}) ====",
              flush=True)
        rc = subprocess.run(s["cmd"], cwd=db.REPO_ROOT).returncode
        if rc != 0:
            print(f"\n[orchestrator] map {s['order']} FAILED (exit {rc}). "
                  "Fix and resume with:\n$ " + " ".join(s["cmd"]))
            raise SystemExit(rc)
    done_note = ("written" if plan["write"]
                 else "dry-run — re-run with --write to persist")
    print(f"\n[orchestrator] all {len(plan['steps'])} map(s) done "
          f"({done_note}).")
    if plan["write"]:
        print("[orchestrator] next: python pipeline/export_data.py --public "
              "&& git add -A && git commit && git push  (auto-deploys)")


if __name__ == "__main__":
    main()
