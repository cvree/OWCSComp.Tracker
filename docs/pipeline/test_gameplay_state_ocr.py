#!/usr/bin/env python3
"""Offline tests for gameplay_state.py's generalized OCR highlight guard.

Builds a synthetic "structurally gameplay" frame directly (same chip+
portrait geometry test_map_ingestion.py's synth_hud_frame draws) and a
fake OCR read_fn, so no OCR engine or calibration pass is needed.
"""
from __future__ import annotations
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gameplay_state as gs  # noqa: E402
import ocr_hud  # noqa: E402

FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  {status}  {name}" + (f" — {detail}" if detail and not ok
                                   else ""))
    if not ok:
        FAILURES.append(name)


W, H = 854, 480
CHIP, PITCH, CHIP_Y, A1 = 22, 48, 46, 18
B1 = W - A1 - 5 * PITCH + (PITCH - 2 * CHIP)


def synth_frame(seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    frame = rng.integers(0, 80, (H, W, 3), dtype=np.uint8)
    rng2 = np.random.default_rng(99)
    for x0, color in ((A1, (0, 140, 255)), (B1, (255, 120, 0))):
        for i in range(5):
            cx = x0 + i * PITCH
            frame[CHIP_Y:CHIP_Y + CHIP, cx:cx + CHIP] = color
            art = rng2.integers(0, 255, (CHIP, CHIP, 3), dtype=np.uint8)
            frame[CHIP_Y:CHIP_Y + CHIP,
                  cx + CHIP + 1:cx + CHIP + 1 + CHIP] = art
    return frame


def make_layout() -> dict:
    def chips(x0):
        return [[x0 + i * PITCH, CHIP_Y, CHIP, CHIP] for i in range(5)]

    def slots(x0):
        return [[x0 + CHIP + 1 + i * PITCH, CHIP_Y, CHIP, CHIP]
                for i in range(5)]
    return {
        "frame_width": W, "frame_height": H,
        "hud_probe": {"chips_a": chips(A1), "chips_b": chips(B1),
                      "sat_min": 110, "val_min": 90},
        "slots_a": slots(A1), "slots_b": slots(B1),
    }


ALIASES = ocr_hud.load_aliases()


def test_backward_compat_no_ocr_args() -> None:
    print("backward compatibility (no OCR args):")
    layout = make_layout()
    frame = synth_frame()
    state, reason = gs.classify_frame(frame, layout)
    check("structurally-gameplay frame stays 'gameplay' with no OCR wired",
          state == "gameplay", f"{state}: {reason}")


def test_ocr_guard_clean() -> None:
    print("ocr_guard: clean OCR text does not override gameplay:")
    layout = make_layout()
    frame = synth_frame()
    read_fn = lambda f: [{"text": "VESTOLA", "conf": 0.9,  # noqa: E731
                          "box": [10, 10, 60, 20]}]
    state, reason = gs.classify_frame(frame, layout, ocr_read_fn=read_fn,
                                      ocr_aliases=ALIASES)
    check("player-tag text does not trigger the guard", state == "gameplay",
          f"{state}: {reason}")


def test_ocr_guard_highlight() -> None:
    print("ocr_guard: HIGHLIGHT banner text overrides to replay:")
    layout = make_layout()
    frame = synth_frame()
    read_fn = lambda f: [{"text": "PLAY OF THE GAME", "conf": 0.9,  # noqa: E731
                          "box": [300, 200, 250, 40]}]
    state, reason = gs.classify_frame(frame, layout, ocr_read_fn=read_fn,
                                      ocr_aliases=ALIASES)
    check("HIGHLIGHT text overrides a structurally-clean frame to replay",
          state == "replay", f"{state}: {reason}")
    check("reason names the OCR guard", "OCR guard" in reason, reason)


def test_ocr_guard_replay_and_intermission() -> None:
    print("ocr_guard: replay/intermission keywords also override:")
    layout = make_layout()
    frame = synth_frame()
    for text in ("INSTANT REPLAY", "VICTORY"):
        read_fn = lambda f, t=text: [{"text": t, "conf": 0.9,
                                      "box": [300, 200, 200, 40]}]
        state, _ = gs.classify_frame(frame, layout, ocr_read_fn=read_fn,
                                     ocr_aliases=ALIASES)
        check(f"'{text}' overrides to replay", state == "replay",
              f"got {state}")


def test_ocr_guard_only_runs_on_gameplay_candidates() -> None:
    print("ocr_guard: never invoked on structurally non-gameplay frames:")
    layout = make_layout()
    blank = np.zeros((H, W, 3), np.uint8)   # no-hud: fails the structural gate
    calls = []

    def counting_read(f):
        calls.append(1)
        return [{"text": "HIGHLIGHT", "conf": 0.9, "box": [0, 0, 10, 10]}]
    state, reason = gs.classify_frame(blank, layout, ocr_read_fn=counting_read,
                                      ocr_aliases=ALIASES)
    check("blank frame is rejected structurally", state == "no-hud",
          f"{state}: {reason}")
    check("OCR guard never ran (cost stays bounded to gameplay candidates)",
          len(calls) == 0, f"{len(calls)} call(s)")


def test_ocr_guard_swallows_reader_errors() -> None:
    print("ocr_guard: a broken read_fn never crashes classify_frame:")
    layout = make_layout()
    frame = synth_frame()

    def broken(f):
        raise RuntimeError("engine exploded")
    state, reason = gs.classify_frame(frame, layout, ocr_read_fn=broken,
                                      ocr_aliases=ALIASES)
    check("reader exception is swallowed, structural verdict stands",
          state == "gameplay", f"{state}: {reason}")


def main() -> int:
    test_backward_compat_no_ocr_args()
    test_ocr_guard_clean()
    test_ocr_guard_highlight()
    test_ocr_guard_replay_and_intermission()
    test_ocr_guard_only_runs_on_gameplay_candidates()
    test_ocr_guard_swallows_reader_errors()
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURES: {FAILURES}")
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
