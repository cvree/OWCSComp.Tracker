#!/usr/bin/env python3
"""
frame_filter.py — Stage V2 of the video CV layer: keep live-gameplay frames.

Takes the raw frames from video_ingest (work/{match}/frames_raw) and keeps
only the ones that are live gameplay — HUD anchor present, replay marker
absent — using the deterministic template check in capture.is_gameplay.
Rejected frames (break screens, caster cams, replays) are moved aside to
work/{match}/frames_rejected/ with a one-line reason, never deleted here,
so a miscalibrated anchor rect is easy to inspect.

Kept frames land in work/{match}/frames/, which is exactly what the detect
stage already expects, so this slots cleanly in front of the existing
detector as well as the new hero_overlay_detect module.

Usage:
  python3 pipeline/frame_filter.py --layout layouts/owcs-demo.json --match m01
  python3 pipeline/frame_filter.py --layout L.json \
        --in work/m01/frames_raw --out work/m01/frames --report
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import capture  # noqa: E402

WORK_DIR = capture.WORK_DIR


def filter_frames(in_dir: str, out_dir: str, layout: dict,
                  report_only: bool = False) -> dict:
    """Copy live-gameplay frames from in_dir to out_dir.

    Returns {"kept": [...], "rejected": [(name, reason), ...]}.
    report_only=True classifies and reports but writes/moves nothing.
    """
    anchor = capture._load_template(layout, "anchor")
    replay = capture._load_template(layout, "replay")
    rejects = capture._load_reject_markers(layout)
    if anchor is None:
        raise ValueError("layout must define an 'anchor' region + template")

    if not os.path.isdir(in_dir):
        raise FileNotFoundError(f"no raw frames dir: {in_dir}")

    rej_dir = os.path.join(os.path.dirname(out_dir.rstrip("/")),
                           "frames_rejected")
    if not report_only:
        os.makedirs(out_dir, exist_ok=True)

    kept, rejected = [], []
    for fn in sorted(f for f in os.listdir(in_dir) if f.endswith(".png")):
        fp = os.path.join(in_dir, fn)
        frame = cv2.imread(fp)
        if frame is None:
            rejected.append((fn, "unreadable"))
            continue
        ok, reason, _score = capture.is_gameplay(
            frame, anchor, replay, rejects)
        if ok:
            kept.append(fn)
            if not report_only:
                shutil.copy(fp, os.path.join(out_dir, fn))
        else:
            rejected.append((fn, reason))
            if not report_only:
                os.makedirs(rej_dir, exist_ok=True)
                shutil.copy(fp, os.path.join(rej_dir, fn))

    return {"kept": kept, "rejected": rejected,
            "out_dir": out_dir, "rejected_dir": rej_dir}


def _default_dirs(match_id: str) -> tuple[str, str]:
    base = os.path.join(WORK_DIR, match_id)
    return os.path.join(base, "frames_raw"), os.path.join(base, "frames")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", required=True)
    ap.add_argument("--match", help="use work/{match}/frames_raw -> frames")
    ap.add_argument("--in", dest="in_dir", help="override input dir")
    ap.add_argument("--out", dest="out_dir", help="override output dir")
    ap.add_argument("--report", action="store_true",
                    help="classify and report only; move nothing")
    args = ap.parse_args()

    layout = capture.load_layout(args.layout)
    if args.match:
        d_in, d_out = _default_dirs(args.match)
    else:
        d_in, d_out = args.in_dir, args.out_dir
    if not d_in or not d_out:
        raise SystemExit("provide --match, or both --in and --out")
    d_in = args.in_dir or d_in
    d_out = args.out_dir or d_out

    res = filter_frames(d_in, d_out, layout, report_only=args.report)
    tag = "would keep" if args.report else "kept"
    print(f"[frame_filter] {tag} {len(res['kept'])} gameplay frames, "
          f"rejected {len(res['rejected'])}"
          + ("" if args.report else f" (rejects in {res['rejected_dir']})"))
    for fn, reason in res["rejected"]:
        print(f"  reject {fn}: {reason}")
    if args.report:
        print(json.dumps({"kept": res["kept"]}, indent=1))


if __name__ == "__main__":
    main()
