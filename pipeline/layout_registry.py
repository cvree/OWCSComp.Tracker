#!/usr/bin/env python3
"""
layout_registry.py — which layouts are real, and which is the default.

A layout file is a set of rectangles saying where the HUD is. There are two
kinds in this repository and conflating them produces silent garbage:

  **Calibrated.** Written by `calibrate_source.py` from real frames. Carries
  a `hud_probe` (the HSV chip geometry the calibrator measured) and a
  `calibration` block recording the frames used and the grid-fit residual.
  Detection against one of these is meaningful.

  **Starter.** A documented template of hand-guessed rectangles, kept so a
  new broadcast package has something to copy and nudge. `owcs_youtube_2026`
  is one, and says so in its own `_comments`. Detection against one of these
  reads whatever pixels happen to sit under a guessed box — usually nothing,
  occasionally something plausible and wrong.

The bug this module fixes: `run_owcs_auto.py` and `discover_owcs_vods.py`
both used the *starter* as `DEFAULT_LAYOUT`. Every automatic run that did not
name a layout explicitly therefore ran detection against guessed rectangles.
It did not crash — it produced UNKNOWNs and occasional false reads, which is
worse than crashing, because the run looked like it worked.

`default_layout()` now returns a calibrated layout or raises. `check_packaging`
asserts a calibrated default exists, so this cannot regress into a repository
whose automatic default is a guess.
"""
from __future__ import annotations

import json
import os
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
LAYOUT_DIR = os.path.join(REPO_ROOT, "layouts")

#: Preference order when nothing names a layout. First calibrated match wins.
#: owcs_jksix_qwc is the verified milestone package (real chip geometry, a
#: per-source template set, a calibration sheet and a full-map ingest behind
#: it), so it is the honest default for "process this with something real".
PREFERRED = ("owcs_jksix_qwc", "owcs_8c105lnzlam", "owcs_nd5lllwdky0")


class NoCalibratedLayout(RuntimeError):
    """No layout in the repository is calibrated — refuse rather than guess."""


def load(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def is_calibrated(layout: dict[str, Any] | str) -> bool:
    """True only for a layout the calibrator actually produced.

    The test is the presence of measured chip geometry. A starter layout can
    carry every other key — slots, anchor, thresholds — and still be guesses;
    `hud_probe.chips_a/chips_b` only exist when `calibrate_source.py` found
    real chip rows in real frames.
    """
    doc = load(layout) if isinstance(layout, str) else layout
    probe = doc.get("hud_probe")
    if not isinstance(probe, dict) or not probe:
        return False
    return bool(probe.get("chips_a")) and bool(probe.get("chips_b"))


def describe(path: str) -> dict[str, Any]:
    """One layout, summarised for a report or a UI."""
    name = os.path.splitext(os.path.basename(path))[0]
    try:
        doc = load(path)
    except (OSError, ValueError) as exc:
        return {"name": name, "path": path, "calibrated": False,
                "error": str(exc)}
    calib = doc.get("calibration") or {}
    residuals = [row.get("residual") for row in
                 (calib.get("chip_row_a"), calib.get("chip_row_b"))
                 if isinstance(row, dict) and row.get("residual") is not None]
    return {
        "name": name,
        "path": path,
        "calibrated": is_calibrated(doc),
        "sourceId": calib.get("source_id"),
        "framesUsed": calib.get("frames_used"),
        "residual": max(residuals) if residuals else None,
        "frameWidth": doc.get("frame_width"),
        "frameHeight": doc.get("frame_height"),
        "templatesDir": doc.get("templates_dir"),
        "manualEdits": len(doc.get("manual_edits") or []),
        "starter": not is_calibrated(doc),
    }


def all_layouts(directory: str | None = None) -> list[dict[str, Any]]:
    directory = directory or LAYOUT_DIR
    if not os.path.isdir(directory):
        return []
    return [describe(os.path.join(directory, name))
            for name in sorted(os.listdir(directory))
            if name.endswith(".json")]


def calibrated_layouts(directory: str | None = None) -> list[dict[str, Any]]:
    return [d for d in all_layouts(directory) if d["calibrated"]]


def default_layout(directory: str | None = None, *,
                   relative: bool = True) -> str:
    """The layout to use when the caller did not name one.

    Raises NoCalibratedLayout rather than falling back to a starter. A caller
    that cannot proceed without a layout should surface that message: it names
    the exact command that produces one.
    """
    calibrated = calibrated_layouts(directory)
    if not calibrated:
        raise NoCalibratedLayout(
            "no calibrated layout is installed, so there is nothing to read a "
            "broadcast with. Starter layouts are hand-guessed rectangles and "
            "are never used automatically. Calibrate one first:\n"
            "  python pipeline/calibrate_source.py --clip <clip.mp4> "
            "--times 100,150,250,350 --source-id <id> --out layouts/<id>.json\n"
            "or, in the desktop application, open Calibration and let it "
            "resolve a layout for the broadcast.")
    by_name = {d["name"]: d for d in calibrated}
    for name in PREFERRED:
        if name in by_name:
            chosen = by_name[name]
            break
    else:
        chosen = calibrated[0]
    if relative:
        return os.path.relpath(chosen["path"], REPO_ROOT).replace(os.sep, "/")
    return chosen["path"]


def resolve(preferred: str | None, *, directory: str | None = None) -> str:
    """`preferred` if given, else the calibrated default.

    An explicitly-named starter layout is still honoured — an operator
    deliberately pointing at one while calibrating it is legitimate. Only the
    *automatic* default refuses to be a guess.
    """
    if preferred:
        return preferred
    return default_layout(directory)


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="list the installed layouts and which is the default")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    layouts = all_layouts()
    try:
        default = default_layout()
    except NoCalibratedLayout as exc:
        default = None
        problem = str(exc)
    else:
        problem = None

    if args.json:
        print(json.dumps({"layouts": layouts, "default": default,
                          "problem": problem}, indent=2))
        return 0 if default else 1

    for entry in layouts:
        mark = "CALIBRATED" if entry["calibrated"] else "starter    "
        extra = ""
        if entry["calibrated"]:
            extra = (f"  frames={entry['framesUsed']}"
                     f"  residual={entry['residual']}")
        print(f"{mark}  {entry['name']:24}{extra}")
    print()
    if default:
        print(f"default: {default}")
        return 0
    print(problem)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
