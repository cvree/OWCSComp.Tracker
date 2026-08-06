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

### Furniture is not always at the bottom

That rule was written from one broadcast, and it encoded that broadcast's
geometry as an assumption: the search started at 45% down, on the reasoning
that "a flat run across the top is a letterbox or a dark helmet, not a name
strip". The second broadcast processed by this pipeline
(`reports/ingest/cr-zeta-ccuf-m1-scan`, layout `owcs_8c105lnzlam`) has the
opposite shape — rows 0-12 of 54 are flat, the portrait runs to the bottom,
and this module confidently reported **"no ROI: the whole box looks like
portrait"** for a slot that is 24% furniture. A package can therefore be
left uncalibrated by a tool that believes it answered.

The band it missed is not a helmet. It is flat along each row and varies
hugely between crops, which is what a team-tinted bar does and what hero art
does not — and cutting it is measurable, by the exact test this module
exists for. A template must describe the HERO, not the side it came from, so
build a template from side-a crops and score it against side-b crops of the
same hero:

    hero      full slot   top 24% cut    delta
    cass         -0.023         0.258   +0.280
    jetcat        0.261         0.515   +0.254
    mizuki        0.407         0.424   +0.018
    tracer        0.321         0.614   +0.293

Four heroes of four improved; the median gain was +0.267 of correlation, and
cass went from *anti-correlated* to usable. So both ends are searched now,
and the ROI carries a `y0` as well as a `y1`.

The original warning still stands and is still enforced: a leading band is
only cut when it is flat by the same criterion, tall enough to be furniture
rather than a dark eyebrow, and confined to the top of the slot. Everything
else is refused, because a wrong leading cut removes the top of a face.

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
# fraction of the MEDIAN row's.
#
# It was originally a fraction of the busiest row, and that quietly encoded
# one broadcast's furniture. A solid separator bar sits at ~1% of the peak,
# so 10% of the peak caught it easily; a team-tinted header bar has an edge
# and a gradient and sits at 10-20% of the peak, so the same threshold
# declared it portrait. The peak is also a single row and moves with one
# bright ult flash, which is a poor thing to measure everything against.
#
# Against the median the two real packages separate with room on both sides:
#
#   package               furniture rows   dimmest portrait row
#   owcs_jksix_qwc         0.025 - 0.138                  0.637
#   owcs_8c105lnzlam       0.096 - 0.301                  0.743
#
# 0.35 sits in the gap for both, nearer the furniture than the portrait.
FLAT_RATIO = 0.35
# The flat band must be at least this many rows, as a fraction of the slot.
# One flat row is noise or a dark eyebrow; four in a row is furniture.
MIN_BAND_FRACTION = 0.08
# Only look for the TRAILING band in the bottom part of the slot.
SEARCH_FROM = 0.45
# ...and for the LEADING band in the top part. The two windows do not
# overlap, so one flat run can never be claimed as both, and a slot that is
# flat everywhere (a dead HUD, a fade-to-black) proposes nothing rather than
# proposing to keep the middle sliver of nothing.
SEARCH_LEAD_TO = 0.45
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
             search_lead_to: float = SEARCH_LEAD_TO,
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
    import statistics
    # The median row, not the busiest one: a single ult flash or a white
    # banner must not move the reference every row is judged against.
    reference = statistics.median(within) or 1.0
    flat = [v / reference < flat_ratio for v in within]

    def longest_flat_run(lo: int, hi: int):
        """(length, first_row) of the longest flat run in rows [lo, hi)."""
        best_run = None
        run_start = None
        for r in range(lo, hi):
            if flat[r]:
                if run_start is None:
                    run_start = r
            elif run_start is not None:
                length = r - run_start
                if best_run is None or length > best_run[0]:
                    best_run = (length, run_start)
                run_start = None
        if run_start is not None:
            length = hi - run_start
            if best_run is None or length > best_run[0]:
                best_run = (length, run_start)
        return best_run

    min_band = max(2, round(min_band_fraction * h))
    start = int(round(search_from * h))
    best = longest_flat_run(start, h)

    # The LEADING band: furniture above the portrait (a team-tinted bar, an
    # ult meter). Only counted when it actually starts at the top of the
    # slot — a flat run that begins in the middle is not a header, and
    # cutting from row 0 to reach it would delete real portrait above it.
    lead = longest_flat_run(0, int(round(search_lead_to * h)))
    lead_rows = 0
    if lead is not None and lead[1] == 0 and lead[0] >= min_band:
        lead_rows = lead[0]

    evidence = {
        "rows": h,
        "withinRowVariance": [round(v, 1) for v in within],
        "acrossCropVariance": [round(v, 1) for v in across],
        "flatRows": [i for i, f in enumerate(flat) if f],
        "leadingBandRows": ([0, lead_rows - 1] if lead_rows else None),
        "samples": len(images),
    }

    trailing = best if (best is not None and best[0] >= min_band) else None
    if trailing is None and not lead_rows:
        return {"roi": None, "confident": True, "evidence": evidence,
                "reason": ("no flat band at either end of the slot — the "
                           "whole box looks like portrait, so no ROI is "
                           "proposed and matching is unchanged")}

    y0 = lead_rows / float(h)
    y1 = (trailing[1] / float(h)) if trailing else 1.0
    keep = y1 - y0
    if keep < min_keep:
        where = []
        if lead_rows:
            where.append(f"a leading band of {lead_rows} row(s)")
        if trailing:
            where.append(f"a trailing band starting at row {trailing[1]}")
        return {"roi": None, "confident": False, "evidence": evidence,
                "reason": (f"{' and '.join(where)} would leave only "
                           f"{keep * 100:.0f}% of {h} rows as portrait — that "
                           f"is a slot-geometry problem, not something an ROI "
                           f"should paper over")}

    reasons = []
    if lead_rows:
        reasons.append(
            f"rows 0-{lead_rows - 1} of {h} are flat along their own width "
            f"yet differ sharply between crops — furniture above the "
            f"portrait (a team-tinted bar or meter), not hero art, and a "
            f"template that includes it describes the SIDE as much as the "
            f"hero")
    if trailing:
        length, first = trailing
        reasons.append(
            f"rows {first}-{first + length - 1} of {h} are flat "
            f"(within-row variance under {flat_ratio:.0%} of the median "
            f"row) — a separator bar, with the player-name strip below it, "
            f"so a template cut through it describes the PLAYER as much as "
            f"the hero")
    roi = [0.0, round(y0, 3), 1.0, round(y1, 3)]
    result = {
        "roi": roi, "confident": True, "evidence": evidence,
        "reason": ("; ".join(reasons)
                   + f". Keeping rows {int(round(y0 * h))}-"
                     f"{int(round(y1 * h)) - 1} ({keep * 100:.0f}% of the "
                     f"slot) cuts them off template and probe alike."),
    }
    if trailing:
        result["bandRows"] = [trailing[1], trailing[1] + trailing[0] - 1]
    return result


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
