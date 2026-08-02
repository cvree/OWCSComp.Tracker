#!/usr/bin/env python3
"""
portrait_roi.py — work out which part of a calibrated slot is actually the
hero portrait, from the footage itself.

A calibrated slot box is drawn by a human (or by `slot_localize`) around the
whole HUD element, and on several OWCS packages that element is not just a
portrait. Underneath it sits a flat separator bar and the **player's name**.
Nobody notices, because a template cut from the whole box matches beautifully
— against that player. Put the same hero in the other team's slot and the
name changes, correlation drops, and the detector either shrugs (UNKNOWN) or
picks the wrong hero.

That is not hypothetical. On the committed Nepal footage, a full-slot set
read enemy Lúcio as `juno` at 0.695 because the strip said `OX` rather than
`YASTRO`. See `detect.portrait_roi`.

### How the boundary is found without anyone measuring anything

Two per-row statistics over a pile of real slot crops separate the three
zones cleanly, and they do it without knowing anything about Overwatch:

  * **within-row variance** — how much a single row varies along its own
    width, averaged over crops. Portrait art is busy; a flat separator bar
    is not. It falls by more than an order of magnitude at the boundary.
  * **across-crop variance** — how much a given pixel differs between
    *different* crops. Name text differs wildly between players and is high
    here too, so it cannot separate name from portrait on its own — which is
    exactly why the flat bar between them is the reliable landmark.

So the rule is: find the longest run of near-flat rows in the bottom half of
the slot, and cut immediately above it. If there is no such run, the slot is
all portrait and no ROI is proposed. Refusing to guess is a real answer here:
a wrong ROI would silently throw away the top of a portrait.

Nothing here writes a layout. It proposes an ROI and shows the evidence; a
human (or the calibration wizard) puts it in the layout file.

CLI:
  python3 pipeline/portrait_roi.py --crops reports/ingest/qad-twis-nepal/evidence
  python3 pipeline/portrait_roi.py --crops <dir> --json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# A row counts as "flat" when its mean within-row variance is below this
# fraction of the crop's busiest row. Portrait rows sit at 0.3-1.0 of the
# maximum; the separator bar on real footage sits at ~0.02.
FLAT_RATIO = 0.10
# The flat band must be at least this many rows, as a fraction of the slot.
# One flat row is noise or a dark eyebrow; four in a row is furniture.
MIN_BAND_FRACTION = 0.08
# Only look for the band in the bottom part of the slot. A flat run across
# the top is a letterbox or a dark helmet, not a name strip, and cutting
# there would remove the portrait rather than the furniture.
SEARCH_FROM = 0.45
# Never propose an ROI that keeps less than this much of the slot — that
# would mean the "portrait" is a sliver and the geometry is wrong, which is
# a calibration problem, not an ROI problem.
MIN_KEEP = 0.40
# At least this many crops before the statistics mean anything.
MIN_SAMPLES = 20


def row_profile(images) -> "tuple[list[float], list[float]]":
    """(within-row variance, across-crop variance) per row."""
    import numpy as np
    stack = np.stack(images).astype(np.float32)
    within = stack.var(axis=2).mean(axis=0)     # along each row, avg over crops
    across = stack.var(axis=0).mean(axis=1)     # between crops, avg along row
    return [float(v) for v in within], [float(v) for v in across]


def discover(images, *, flat_ratio: float = FLAT_RATIO,
             min_band_fraction: float = MIN_BAND_FRACTION,
             search_from: float = SEARCH_FROM,
             min_keep: float = MIN_KEEP) -> dict:
    """Propose a `portrait_roi` for a pile of same-shaped slot crops."""
    if len(images) < MIN_SAMPLES:
        return {"roi": None, "confident": False,
                "reason": (f"only {len(images)} crop(s); {MIN_SAMPLES} are "
                           f"needed before a per-row statistic means anything")}
    shapes = {img.shape[:2] for img in images}
    if len(shapes) != 1:
        return {"roi": None, "confident": False,
                "reason": (f"crops are not all the same size ({len(shapes)} "
                           f"distinct shapes) — resample them first")}
    h, _w = images[0].shape[:2]
    within, across = row_profile(images)
    peak = max(within) or 1.0
    flat = [v / peak < flat_ratio for v in within]

    start = int(round(search_from * h))
    best = None                      # (length, first_row)
    run_start = None
    for r in range(start, h):
        if flat[r]:
            if run_start is None:
                run_start = r
        else:
            if run_start is not None:
                length = r - run_start
                if best is None or length > best[0]:
                    best = (length, run_start)
                run_start = None
    if run_start is not None:
        length = h - run_start
        if best is None or length > best[0]:
            best = (length, run_start)

    evidence = {
        "rows": h,
        "withinRowVariance": [round(v, 1) for v in within],
        "acrossCropVariance": [round(v, 1) for v in across],
        "flatRows": [i for i, f in enumerate(flat) if f],
        "samples": len(images),
    }

    if best is None or best[0] < max(2, round(min_band_fraction * h)):
        return {"roi": None, "confident": True, "evidence": evidence,
                "reason": ("no flat band in the lower part of the slot — the "
                           "whole box looks like portrait, so no ROI is "
                           "proposed and matching is unchanged")}
    length, first = best
    keep = first / float(h)
    if keep < min_keep:
        return {"roi": None, "confident": False, "evidence": evidence,
                "reason": (f"the flat band starts at row {first} of {h} "
                           f"({keep * 100:.0f}% in), which would leave a "
                           f"sliver of portrait — that is a slot-geometry "
                           f"problem, not something an ROI should paper over")}
    roi = [0.0, 0.0, 1.0, round(keep, 3)]
    return {
        "roi": roi, "confident": True, "evidence": evidence,
        "bandRows": [first, first + length - 1],
        "reason": (f"rows {first}-{first + length - 1} of {h} are flat "
                   f"(within-row variance under {flat_ratio:.0%} of the "
                   f"busiest row) — a separator bar, with the player-name "
                   f"strip below it. Keeping the top {keep * 100:.0f}% cuts "
                   f"both, so a template describes the hero rather than the "
                   f"player currently on that hero."),
    }


def load_crops(crops_dir: str, *, limit: int = 400):
    import cv2
    images = []
    for path in sorted(glob.glob(os.path.join(crops_dir, "*.png")))[:limit]:
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is not None:
            images.append(img)
    return images


def format_result(result: dict) -> str:
    lines = []
    if result.get("roi"):
        lines.append(f"  proposed portrait_roi : {result['roi']}")
    else:
        lines.append("  proposed portrait_roi : none")
    lines.append(f"  {result['reason']}")
    ev = result.get("evidence")
    if ev:
        peak = max(ev["withinRowVariance"]) or 1.0
        lines.append(f"  per-row profile ({ev['samples']} crops):")
        for r, v in enumerate(ev["withinRowVariance"]):
            bar = "#" * int(round(v / peak * 32))
            mark = "  <- flat" if r in ev["flatRows"] else ""
            lines.append(f"    row {r:2d} y={r / ev['rows']:.2f} "
                         f"within={v:8.1f} {bar}{mark}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="discover the portrait sub-rectangle of a calibrated slot")
    ap.add_argument("--crops", required=True,
                    help="directory of same-sized slot crop PNGs")
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    images = load_crops(args.crops, limit=args.limit)
    result = discover(images)
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print(format_result(result))
    return 0 if result.get("confident") else 1


if __name__ == "__main__":
    raise SystemExit(main())
