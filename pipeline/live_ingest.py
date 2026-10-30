#!/usr/bin/env python3
"""
live_ingest.py — find the maps in a broadcast without downloading it.

The job pipeline (`automation/worker.py`) downloads a whole broadcast at 720p
before it looks at anything. A Twitch broadcast day is 6–12 hours, ~2.25 GB
an hour at 720p, and a free GitHub runner has ~14 GB of disk: the unattended
chain could never get past its own disk check on the broadcasts it exists to
read. Everything downstream already works from remote frames —
`ingest_map.py --remote-url` reads one map's window by HTTP — so the only
missing piece is knowing WHERE the maps are.

This module answers that with the same machinery the rest of the pipeline
trusts:

  * `sample_timeline` — one remote frame every `step` seconds across the
    broadcast, each classified by `gameplay_state.classify_frame` against
    the broadcast's layout (gameplay / partial-hud / replay / no-hud);
  * `segmentation._group_candidates` — the job pipeline's own pure grouping
    of contiguous gameplay, unchanged;
  * `merge_into_maps` — the one new rule. A Control map's rounds, and every
    map's replays and kill-cams, break gameplay for 30–90 s; a real map
    change puts the desk, ads or a draft screen on air for minutes. Runs of
    gameplay closer than `map_gap` are one map.

What it never does: name a team, a map or a winner. A window is a span of
time with live play in it — identity is a separate, evidenced step.

CLI (on a runner; YouTube refuses GitHub's IP ranges, Twitch does not):
  python3 pipeline/live_ingest.py --url https://www.twitch.tv/videos/<id> \\
      --layout layouts/owcs_8c105lnzlam.json --out work/live/<id>
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Seconds between timeline samples. 30 s finds every map (the shortest real
# map is several minutes) at ~1,300 frames for an eleven-hour broadcast.
DEFAULT_STEP = 30.0
# Gameplay runs separated by less than this are the same map. Between rounds
# and through replays the HUD is off for 30–90 s; between maps the broadcast
# goes to the desk, a draft or an ad break for several minutes.
DEFAULT_MAP_GAP = 150.0
# A "map" shorter than this is a highlight package, a recap or a stray
# sample, not a map that was played.
DEFAULT_MIN_MAP = 240.0


def log(msg: str) -> None:
    print(f"[live-ingest] {msg}", flush=True)


def sample_timeline(source, duration: float, layout: dict, *,
                    step: float = DEFAULT_STEP, chunk: int = 24) -> list[dict]:
    """One classified sample every `step` seconds, in the shape
    `segmentation._group_candidates` reads."""
    import cv2
    import capture
    import gameplay_state as gs

    offsets = [round(t, 1) for t in _frange(0.0, duration, step)]
    out: list[dict] = []
    for i in range(0, len(offsets), chunk):
        got = source.grab(offsets[i:i + chunk])
        for t in offsets[i:i + chunk]:
            path = got.get(t)
            frame = cv2.imread(path) if path else None
            if frame is None:
                out.append({"offset": t, "gameplay": False, "score": 0.0,
                            "reason": "not acquired", "sceneChange": False,
                            "state": "missing"})
                continue
            lay, info = capture.scale_layout_to_frame(layout, frame.shape[1], frame.shape[0])
            state, reason = gs.classify_frame(frame, lay) if info.get("ok") \
                else ("no-hud", "frame aspect does not match the layout")
            out.append({"offset": t, "gameplay": state == "gameplay",
                        "score": 1.0 if state == "gameplay" else 0.0,
                        "reason": reason, "sceneChange": False, "state": state,
                        "frame": path})
        done = min(len(offsets), i + chunk)
        live = sum(1 for s in out if s["gameplay"])
        log(f"timeline {done}/{len(offsets)} samples · {live} live")
    return out


def _frange(a: float, b: float, step: float):
    t = a
    while t <= b:
        yield t
        t += step


def merge_into_maps(candidates: list[dict], *, map_gap: float = DEFAULT_MAP_GAP,
                    min_map: float = DEFAULT_MIN_MAP) -> list[dict]:
    """Join gameplay runs that are one map; drop ones too short to be a map.

    Pure, and testable with synthetic candidates. Each window keeps the runs
    it was built from, so a reviewer can see why two runs became one map."""
    runs = sorted(candidates, key=lambda c: c["start_time"])
    maps: list[dict] = []
    for r in runs:
        if maps and r["start_time"] - maps[-1]["end"] <= map_gap:
            m = maps[-1]
            m["end"] = max(m["end"], r["end_time"])
            m["runs"].append([r["start_time"], r["end_time"]])
            m["gameplaySamples"] += r["signals"]["gameplaySamples"]
        else:
            maps.append({"start": r["start_time"], "end": r["end_time"],
                         "runs": [[r["start_time"], r["end_time"]]],
                         "gameplaySamples": r["signals"]["gameplaySamples"]})
    out = []
    for m in maps:
        span = m["end"] - m["start"]
        if span < min_map:
            continue
        out.append(dict(m, index=len(out) + 1, seconds=round(span, 1)))
    return out


def find_maps(samples: list[dict], *, step: float = DEFAULT_STEP,
              map_gap: float = DEFAULT_MAP_GAP,
              min_map: float = DEFAULT_MIN_MAP) -> list[dict]:
    """Timeline samples -> map windows, via the job pipeline's own grouping."""
    from automation import segmentation as seg
    cands = seg._group_candidates(
        samples, interval=int(step), pre_roll=0, post_roll=0,
        gap_tolerance=1, min_candidate_seconds=0)
    return merge_into_maps(cands, map_gap=map_gap, min_map=min_map)


def identify_maps(maps: list[dict], samples: list[dict], layout: dict) -> list[dict]:
    """Name each window's map and teams from its own frames (live_identity),
    when the layout says where the broadcast prints them and OCR is here."""
    if not layout.get("identity_zones"):
        return maps
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
    except Exception as exc:   # noqa: BLE001 — any missing piece means skip
        log(f"identity skipped (needs pytesseract + the tesseract binary): {exc}")
        return maps
    import sqlite3
    import db
    import live_identity as lid
    import map_identify as mi
    import team_identify as ti
    con = sqlite3.connect(db.DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        known_maps, known_teams = mi.known_maps_from_db(con), ti.known_teams_from_db(con)
    finally:
        con.close()
    maps = lid.attach(maps, samples, layout, known_maps=known_maps,
                      known_teams=known_teams)
    for m in maps:
        i = m.get("identity") or {}
        log(f"#{m['index']} " + " · ".join(
            str((i.get(k) or {}).get("value") or "?") for k in ("map", "game", "team_a", "team_b")))
    return maps


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", required=True)
    ap.add_argument("--layout", required=True)
    ap.add_argument("--out", required=True, help="directory for timeline.json")
    ap.add_argument("--step", type=float, default=DEFAULT_STEP)
    ap.add_argument("--duration", type=float, default=None)
    args = ap.parse_args(argv)

    import calibrate_remote as cr
    import capture
    import remote_frames as rf

    layout = capture.load_layout(args.layout)
    src = rf.RemoteFrameSource(args.url, height=720)
    duration = args.duration or cr._probe_duration(args.url)
    if not duration:
        log("could not learn the broadcast's duration")
        return 1
    samples = sample_timeline(src, duration, layout, step=args.step)
    maps = find_maps(samples, step=args.step)
    maps = identify_maps(maps, samples, layout)
    os.makedirs(args.out, exist_ok=True)
    states: dict[str, int] = {}
    for s in samples:
        states[s["state"]] = states.get(s["state"], 0) + 1
    with open(os.path.join(args.out, "timeline.json"), "w", encoding="utf-8") as f:
        json.dump({"url": args.url, "layout": os.path.basename(args.layout),
                   "durationSeconds": duration, "step": args.step, "states": states,
                   "maps": maps,
                   "samples": [{k: s[k] for k in ("offset", "state", "reason")}
                               for s in samples]}, f, indent=1)
    log(f"{len(maps)} map window(s): " + ", ".join(
        f"#{m['index']} {m['start']:.0f}–{m['end']:.0f}s" for m in maps))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
