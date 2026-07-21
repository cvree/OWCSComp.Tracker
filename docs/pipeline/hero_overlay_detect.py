#!/usr/bin/env python3
"""
hero_overlay_detect.py — Stage V3: gameplay frame -> read hero overlay.

For each kept gameplay frame this crops the 5+5 hero-portrait slots from the
layout and template-matches each against the hero template set, producing a
structured, per-frame reading with confidence. It decides accept vs.
quarantine but does NOT write the database — persistence is video_to_snapshots'
job, so detection stays pure and testable.

A frame's team reading is ACCEPTED only when:
  - every slot beats layout['match_threshold'] (per-slot confidence), AND
  - no hero repeats within the team, AND
  - the team's overall confidence (mean slot score) is >=
    layout['min_overall_confidence'] if that key is set.
Otherwise the frame is copied to <quarantine>/ with a JSON sidecar listing
the per-slot scores and the rejection reason, and never becomes a snapshot.

Hero templates come from (highest priority first): an explicit templates_dir
argument, layout['templates_dir'], else the repo templates/ folder. This lets
the offline demo use its own fixture templates without touching production.

Usage (debugging a match's kept frames):
  python3 pipeline/hero_overlay_detect.py --layout L.json --match m01
  python3 pipeline/hero_overlay_detect.py --layout L.json \
        --frames-dir work/m01/frames --json
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import capture  # noqa: E402
import detect  # noqa: E402

WORK_DIR = capture.WORK_DIR


def resolve_templates_dir(layout: dict, templates_dir: str | None) -> str | None:
    d = templates_dir or layout.get("templates_dir")
    if d and not os.path.isabs(d):
        d = os.path.join(db.REPO_ROOT, d)
    return d


def load_lib(layout: dict, templates_dir: str | None = None) -> dict:
    return detect.load_templates(resolve_templates_dir(layout, templates_dir))


def _team_confidence(slots: list[dict]) -> float:
    return round(sum(s["score"] for s in slots) / len(slots), 3) if slots else 0.0


def read_frame(frame_bgr, layout: dict, lib: dict) -> dict:
    """Both teams' readings for one frame, each with an accept/quarantine
    verdict. Shape: {'a': {...}, 'b': {...}}."""
    threshold = layout.get("match_threshold", 0.6)
    min_overall = layout.get("min_overall_confidence")
    comps = detect.read_frame_comps(frame_bgr, layout, lib)
    out = {}
    for side in ("a", "b"):
        slots = comps[side]
        reason = detect.validate(slots, threshold)
        conf = _team_confidence(slots)
        if reason is None and min_overall is not None and conf < min_overall:
            reason = f"low overall confidence {conf} < {min_overall}"
        out[side] = {
            "heroes": [s["hero"] for s in slots],
            "slots": slots,
            "confidence": conf,
            "accepted": reason is None,
            "reason": reason,
        }
    return out


def frame_offset(fn: str) -> int:
    return int(os.path.splitext(os.path.basename(fn))[0])


def detect_dir(frames_dir: str, layout: dict, lib: dict,
               quarantine_dir: str | None = None) -> dict:
    """Read every PNG in frames_dir.

    Returns {"accepted": [ ... ], "quarantined": [ ... ]}. An accepted item is
    one frame with BOTH teams read cleanly:
      {offset, frame, frame_hash, a:{heroes,confidence}, b:{heroes,confidence}}
    Frames where either team fails are quarantined (copied + JSON sidecar).
    """
    if not os.path.isdir(frames_dir):
        raise FileNotFoundError(f"no frames dir: {frames_dir}")
    accepted, quarantined = [], []
    for fn in sorted(f for f in os.listdir(frames_dir) if f.endswith(".png")):
        fp = os.path.join(frames_dir, fn)
        frame = cv2.imread(fp)
        if frame is None:
            continue
        reading = read_frame(frame, layout, lib)
        if reading["a"]["accepted"] and reading["b"]["accepted"]:
            fhash = hashlib.sha1(open(fp, "rb").read()).hexdigest()[:16]
            accepted.append({
                "offset": frame_offset(fn),
                "frame": fp,
                "frame_hash": fhash,
                "a": {"heroes": reading["a"]["heroes"],
                      "confidence": reading["a"]["confidence"],
                      "slots": reading["a"]["slots"]},
                "b": {"heroes": reading["b"]["heroes"],
                      "confidence": reading["b"]["confidence"],
                      "slots": reading["b"]["slots"]},
            })
        else:
            reasons = {s: reading[s]["reason"] for s in ("a", "b")}
            quarantined.append({"offset": frame_offset(fn), "frame": fp,
                                "reasons": reasons})
            if quarantine_dir:
                os.makedirs(quarantine_dir, exist_ok=True)
                shutil.copy(fp, os.path.join(quarantine_dir, fn))
                with open(os.path.join(quarantine_dir, fn + ".json"), "w") as f:
                    json.dump({"reasons": reasons, "read": reading}, f, indent=1)
    return {"accepted": accepted, "quarantined": quarantined}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", required=True)
    ap.add_argument("--match", help="use work/{match}/frames")
    ap.add_argument("--frames-dir", help="override frames dir")
    ap.add_argument("--templates-dir", help="override hero templates dir")
    ap.add_argument("--json", action="store_true", help="print full readings")
    args = ap.parse_args()

    layout = capture.load_layout(args.layout)
    frames_dir = args.frames_dir or (
        os.path.join(WORK_DIR, args.match, "frames") if args.match else None)
    if not frames_dir:
        raise SystemExit("provide --match or --frames-dir")
    qdir = os.path.join(WORK_DIR, args.match, "quarantine") if args.match else None

    lib = load_lib(layout, args.templates_dir)
    res = detect_dir(frames_dir, layout, lib, quarantine_dir=qdir)
    print(f"[hero_overlay_detect] accepted {len(res['accepted'])} frames, "
          f"quarantined {len(res['quarantined'])}"
          + (f" (see {qdir})" if qdir and res['quarantined'] else ""))
    for q in res["quarantined"]:
        print(f"  quarantine t={q['offset']}: {q['reasons']}")
    if args.json:
        print(json.dumps(res["accepted"], indent=1))


if __name__ == "__main__":
    main()
