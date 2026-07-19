#!/usr/bin/env python3
"""Offline tests for ingest_map.observe()'s OCR wiring: OCR runs exactly
once per frame when enabled, feeds gameplay_state's generalized guard via
a reused-items closure (never re-invokes the engine), stashes '_ocr' for
team_identify/detect_bans, and stays fully backward compatible (identical
behavior, zero OCR calls) when ocr_read_fn/ocr_aliases are omitted.
"""
from __future__ import annotations
import os
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ingest_map as im  # noqa: E402
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
ALIASES = ocr_hud.load_aliases()
TMP = tempfile.mkdtemp(prefix="owcs_test_ingest_ocr_")


def synth_frame_path(seed=0) -> str:
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
    path = os.path.join(TMP, f"frame{seed}.png")
    cv2.imwrite(path, frame)
    return path


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


def test_ocr_disabled_backward_compat() -> None:
    print("observe(): OCR disabled (default) is unchanged:")
    layout = make_layout()
    fp = synth_frame_path(1)
    obs = im.observe(10.0, fp, layout, {}, TMP, save_crop=False)
    check("no '_ocr' key when OCR is not wired in", "_ocr" not in obs,
          str(obs.keys()))
    check("state still resolves normally", obs["state"] in
          ("gameplay", "partial-hud", "no-hud"), obs["state"])


def test_ocr_runs_exactly_once() -> None:
    print("observe(): OCR runs exactly once per frame:")
    layout = make_layout()
    fp = synth_frame_path(2)
    calls = []

    def counting_read(frame):
        calls.append(1)
        return [{"text": "VESTOLA", "conf": 0.9, "box": [10, 10, 60, 20]}]
    obs = im.observe(20.0, fp, layout, {}, TMP, save_crop=False,
                     ocr_read_fn=counting_read, ocr_aliases=ALIASES)
    check("read_fn invoked exactly once (reused for the guard, not "
          "re-run)", len(calls) == 1, f"{len(calls)} call(s)")
    check("'_ocr' items stashed on the observation", obs.get("_ocr")
          == [{"text": "VESTOLA", "conf": 0.9, "box": [10, 10, 60, 20]}],
          str(obs.get("_ocr")))


def test_ocr_guard_overrides_state() -> None:
    print("observe(): OCR guard correctly overrides classify_frame:")
    layout = make_layout()
    fp = synth_frame_path(3)
    read_fn = lambda f: [{"text": "PLAY OF THE GAME", "conf": 0.9,  # noqa: E731
                          "box": [300, 200, 250, 40]}]
    obs = im.observe(30.0, fp, layout, {}, TMP, save_crop=False,
                     ocr_read_fn=read_fn, ocr_aliases=ALIASES)
    check("HIGHLIGHT text via observe() flips a clean frame to 'replay'",
          obs["state"] == "replay", str(obs))
    check("slots are empty for a non-gameplay observation",
          obs["slots"] == {}, str(obs["slots"]))


def test_ocr_reader_error_is_swallowed() -> None:
    print("observe(): a broken OCR reader never crashes the run:")
    layout = make_layout()
    fp = synth_frame_path(4)

    def broken(frame):
        raise RuntimeError("engine exploded")
    obs = im.observe(40.0, fp, layout, {}, TMP, save_crop=False,
                     ocr_read_fn=broken, ocr_aliases=ALIASES)
    check("observation still produced despite reader failure",
          obs["state"] in ("gameplay", "partial-hud", "no-hud"),
          str(obs))
    check("'_ocr' present but empty on a failed read",
          obs.get("_ocr") == [], str(obs.get("_ocr")))


def main() -> int:
    test_ocr_disabled_backward_compat()
    test_ocr_runs_exactly_once()
    test_ocr_guard_overrides_state()
    test_ocr_reader_error_is_swallowed()
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURES: {FAILURES}")
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
