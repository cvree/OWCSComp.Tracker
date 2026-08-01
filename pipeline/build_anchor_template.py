#!/usr/bin/env python3
"""
build_anchor_template.py — cut a REAL gameplay-HUD anchor from real frames.

An "anchor" is the positive half of gameplay detection: a rectangle of the
broadcast overlay that is on screen while the game is being played, and off
screen on desk segments, transitions, player cams and full-screen graphics.
`capture.is_gameplay` and (since this tool exists) `gameplay_state`
matchTemplate the stored crop inside the anchor rect and compare the best
TM_CCOEFF_NORMED score against `min_score`.

WHY A TOOL AND NOT A ONE-LINER CROP
Two things silently break a hand-cut anchor, and both are measured here:

  1. SCALE. `capture.region_score` scales the anchor RECT to the frame it is
     scoring but leaves the template IMAGE at whatever size it was saved,
     resizing it DOWN only when it is larger than the crop. So a template cut
     at 720p and stored at 720p is compared against a 1080p crop at the wrong
     scale (measured on this repo's real frames: 0.30 instead of 0.99). The
     only shape that survives every capture resolution is
     template size == rect size in the layout's own native coordinates —
     then any later resize keeps template content and crop content aligned.
     This tool always writes the template at exactly that size.

  2. CONTENT DRIFT. An anchor cut from a region that encodes match state
     (score, round, map number, bracket label) matches the frame it was cut
     from and nothing else. `owcs_nd5lllwdky0.json` documents a real instance:
     a round-state-row anchor scored -0.15 on genuine Escort gameplay and the
     filter threw the frames away. So this tool scores the candidate crop
     against EVERY supplied frame, picks the reference frame with the best
     WORST-case score, and prints the whole distribution. A rect that is not
     invariant shows up immediately as a low minimum.

It refuses to invent pixels: every byte written comes from one real frame.

Usage (measure only — prints the score table, writes nothing):
  python3 pipeline/build_anchor_template.py \
      --layout layouts/owcs_jksix_qwc.json \
      --frames 'reports/ingest/qad-twis-nepal/frames/*.jpg' \
      --rect 51,12,276,24

Add --out layouts/owcs_jksix_qwc-anchor.png to write the crop, and
--negatives 'some/glob/*.png' to also measure frames that must NOT match.
"""
from __future__ import annotations
import argparse
import glob as globmod
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture  # noqa: E402
import db  # noqa: E402


def _load_gray(paths: list[str]) -> list[tuple[str, "np.ndarray"]]:
    out = []
    for p in paths:
        img = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise FileNotFoundError(f"unreadable frame: {p}")
        out.append((p, img))
    return out


def scale_rect(rect, native_w: int, native_h: int, fw: int, fh: int):
    """The SAME rounding capture.scale_layout_to_frame applies."""
    sx, sy = fw / native_w, fh / native_h
    x, y, w, h = rect
    return (int(round(x * sx)), int(round(y * sy)),
            max(1, int(round(w * sx))), max(1, int(round(h * sy))))


def cut(frame_gray, rect, native_w: int, native_h: int,
        interpolation=cv2.INTER_CUBIC):
    """Crop `rect` out of one frame and store it at the rect's NATIVE size.

    The frame may be any resolution; the rect is given in the layout's native
    space. Result is always exactly (rect_w, rect_h) so that
    capture.region_score's "resize the template down to the crop" path keeps
    template content and crop content pixel-aligned at every capture size.
    """
    fh, fw = frame_gray.shape[:2]
    rx, ry, rw, rh = scale_rect(rect, native_w, native_h, fw, fh)
    if rx < 0 or ry < 0 or rx + rw > fw or ry + rh > fh:
        raise ValueError(f"anchor rect {rect} falls outside a {fw}x{fh} frame")
    crop = frame_gray[ry:ry + rh, rx:rx + rw]
    if crop.shape[:2] == (rect[3], rect[2]):
        return crop.copy()
    return cv2.resize(crop, (rect[2], rect[3]), interpolation=interpolation)


def score(frame_gray, template, rect, native_w: int, native_h: int) -> float:
    """capture.region_score against a rect expressed in native coordinates."""
    fh, fw = frame_gray.shape[:2]
    tpl = {"img": template,
           "rect": scale_rect(rect, native_w, native_h, fw, fh),
           "min_score": 0.0}
    return capture.region_score(frame_gray, tpl)


def best_reference(frames, rect, native_w: int, native_h: int):
    """Pick the frame whose crop scores best in the WORST case elsewhere.

    Maximising the minimum (rather than the mean) is the point: an anchor is
    a gate, and a gate is only as good as the frame it nearly rejects.
    """
    rows = []
    templates = [cut(img, rect, native_w, native_h) for _p, img in frames]
    for i, tpl in enumerate(templates):
        scores = [score(img, tpl, rect, native_w, native_h)
                  for _p, img in frames]
        rows.append({"index": i, "path": frames[i][0], "template": tpl,
                     "scores": scores, "min": min(scores),
                     "median": float(np.median(scores))})
    rows.sort(key=lambda r: (r["min"], r["median"]), reverse=True)
    return rows


def elsewhere_peak(frame_gray, template, rect, native_w, native_h,
                   guard: int = 12) -> float:
    """Best score anywhere in the frame EXCLUDING the anchor's own position.

    A specificity check: if the crop matches half the screen equally well it
    is not an anchor, it is wallpaper. Only meaningful when the frame is at
    the template's own scale, so it is skipped otherwise.
    """
    fh, fw = frame_gray.shape[:2]
    rx, ry, rw, rh = scale_rect(rect, native_w, native_h, fw, fh)
    tpl = template
    if (rh, rw) != template.shape[:2]:
        tpl = cv2.resize(template, (rw, rh))
    if fh < tpl.shape[0] or fw < tpl.shape[1]:
        return float("nan")
    res = cv2.matchTemplate(frame_gray, tpl, cv2.TM_CCOEFF_NORMED)
    masked = res.copy()
    masked[max(0, ry - guard):ry + guard + 1,
           max(0, rx - guard):rx + guard + 1] = -2.0
    return float(masked.max())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--layout", required=True,
                    help="layout JSON (supplies the native frame size)")
    ap.add_argument("--frames", required=True, action="append",
                    help="glob of REAL gameplay frames (repeatable)")
    ap.add_argument("--negatives", action="append", default=[],
                    help="glob of frames that must NOT match (repeatable)")
    ap.add_argument("--rect", required=True,
                    help="x,y,w,h of the anchor in the layout's native pixels")
    ap.add_argument("--out", help="write the chosen crop here (PNG)")
    ap.add_argument("--reference",
                    help="force this frame as the source of the crop "
                         "instead of the measured best-worst-case one")
    ap.add_argument("--resolutions", default="1920x1080,1280x720,854x480,640x360",
                    help="capture sizes to re-measure the positives at")
    args = ap.parse_args(argv)

    root = db.REPO_ROOT
    lay_path = args.layout if os.path.isabs(args.layout) \
        else os.path.join(root, args.layout)
    with open(lay_path, encoding="utf-8") as f:
        layout = json.load(f)
    native_w = int(layout.get("frame_width", 1920))
    native_h = int(layout.get("frame_height", 1080))
    rect = [int(v) for v in args.rect.split(",")]
    if len(rect) != 4:
        ap.error("--rect must be x,y,w,h")

    def expand(patterns):
        paths = []
        for pat in patterns:
            p = pat if os.path.isabs(pat) else os.path.join(root, pat)
            paths.extend(sorted(globmod.glob(p)))
        return paths

    pos_paths = expand(args.frames)
    if not pos_paths:
        ap.error("--frames matched nothing")
    frames = _load_gray(pos_paths)
    print(f"layout {os.path.basename(lay_path)} native {native_w}x{native_h}")
    print(f"anchor rect (native) {rect}")
    print(f"{len(frames)} positive frame(s)")

    rows = best_reference(frames, rect, native_w, native_h)
    print("\nreference-frame ranking (min score across ALL positives):")
    for r in rows:
        print(f"  {os.path.relpath(r['path'], root):60s} "
              f"min={r['min']:.3f} median={r['median']:.3f}")
    chosen = rows[0]
    if args.reference:
        want = os.path.abspath(args.reference if os.path.isabs(args.reference)
                               else os.path.join(root, args.reference))
        match = [r for r in rows if os.path.abspath(r["path"]) == want]
        if not match:
            ap.error(f"--reference {args.reference} is not among --frames")
        chosen = match[0]
    tpl = chosen["template"]
    print(f"\nchosen reference: {os.path.relpath(chosen['path'], root)}")
    print(f"template size {tpl.shape[1]}x{tpl.shape[0]} "
          f"(== rect size, so it rescales without drifting)")
    print("per-frame scores:")
    for (p, _img), s in zip(frames, chosen["scores"]):
        print(f"  {s:+.3f}  {os.path.relpath(p, root)}")

    print("\nspecificity — best match ELSEWHERE in the same frame:")
    for p, img in frames:
        e = elsewhere_peak(img, tpl, rect, native_w, native_h)
        print(f"  {e:+.3f}  {os.path.relpath(p, root)}")

    print("\nre-measured after resampling every positive:")
    worst_res = 1.0
    for spec in args.resolutions.split(","):
        w, h = (int(v) for v in spec.lower().split("x"))
        ss = [score(cv2.resize(img, (w, h)), tpl, rect, native_w, native_h)
              for _p, img in frames]
        worst_res = min(worst_res, min(ss))
        print(f"  {spec:>10s}  min={min(ss):+.3f}  median={np.median(ss):+.3f}")

    neg_paths = expand(args.negatives)
    neg_max = None
    if neg_paths:
        print(f"\n{len(neg_paths)} negative frame(s) — these must NOT match:")
        negs = _load_gray(neg_paths)
        ns = []
        for p, img in negs:
            s = score(img, tpl, rect, native_w, native_h)
            ns.append(s)
            print(f"  {s:+.3f}  {os.path.relpath(p, root)}")
        neg_max = max(ns)

    print("\nsummary:")
    print(f"  worst positive (any resolution): {worst_res:+.3f}")
    if neg_max is not None:
        print(f"  best negative:                   {neg_max:+.3f}")
        mid = (worst_res + neg_max) / 2
        print(f"  midpoint of the gap:             {mid:+.3f}")
    print("  pick min_score inside the gap, nearer the negatives: throwing "
          "away real gameplay costs more than one extra reject-marker check.")

    if args.out:
        out = args.out if os.path.isabs(args.out) \
            else os.path.join(root, args.out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if not cv2.imwrite(out, tpl):
            print(f"FAILED to write {out}", file=sys.stderr)
            return 1
        print(f"\nwrote {os.path.relpath(out, root)} "
              f"({tpl.shape[1]}x{tpl.shape[0]}, grayscale, cut from "
              f"{os.path.relpath(chosen['path'], root)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
