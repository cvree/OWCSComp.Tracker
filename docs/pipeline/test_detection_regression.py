#!/usr/bin/env python3
"""
test_detection_regression.py — lock current hero-overlay detection behavior.

This is intentionally tests-only. It reuses the existing synthetic fixture
helpers from test_pipeline_synthetic.py and drives the real detect.py and
hero_overlay_detect.py code paths without touching production templates/, work/,
DB, network, promotion, FACEIT, or team-side logic.

Run:  python3 pipeline/test_detection_regression.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

# Reuse the deterministic synthetic broadcast fixtures already trusted by the
# pipeline test. Importing this module does not run its main() function.
import test_pipeline_synthetic as syn  # noqa: E402
import detect  # noqa: E402
import hero_overlay_detect  # noqa: E402

TEST_DIR = os.path.join(ROOT, "work", "test_detection_regression")
TEMPLATES_DIR = os.path.join(TEST_DIR, "templates")
FRAMES_DIR = os.path.join(TEST_DIR, "frames")
QUARANTINE_DIR = os.path.join(TEST_DIR, "quarantine")
THRESHOLD = 0.6


_checks = 0


def check(name: str, cond: bool) -> None:
    global _checks
    _checks += 1
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        raise SystemExit(1)


def reset_workspace() -> None:
    shutil.rmtree(TEST_DIR, ignore_errors=True)
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    os.makedirs(FRAMES_DIR, exist_ok=True)


def write_templates(hero_ids: list[str] | None = None) -> dict:
    """Write deterministic grayscale templates to an isolated temp dir."""
    ids = hero_ids or syn.ALL_HEROES
    for hid in ids:
        icon = syn.hero_icon(hid)
        cv2.imwrite(os.path.join(TEMPLATES_DIR, f"{hid}.png"),
                    cv2.cvtColor(icon, cv2.COLOR_BGR2GRAY))
    return detect.load_templates(TEMPLATES_DIR)


def layout() -> dict:
    return {
        "frame_width": syn.W,
        "frame_height": syn.H,
        "slots_a": syn.SLOTS_A,
        "slots_b": syn.SLOTS_B,
        "match_threshold": THRESHOLD,
        "templates_dir": TEMPLATES_DIR,
    }


def heroes(slots: list[dict]) -> list[str]:
    return [s["hero"] for s in slots]


def all_slot_scores(slots: list[dict]) -> list[float]:
    return [float(s["score"]) for s in slots]


def assert_scores_clear_threshold(label: str, slots: list[dict], threshold: float = THRESHOLD) -> None:
    scores = all_slot_scores(slots)
    check(f"{label}: has 5 slot scores", len(scores) == 5)
    check(f"{label}: all scores are in [0, 1]",
          all(0.0 <= s <= 1.0 for s in scores))
    check(f"{label}: all scores clear threshold {threshold}",
          all(s >= threshold for s in scores))


def write_frame(name: str, frame: np.ndarray) -> str:
    path = os.path.join(FRAMES_DIR, name)
    ok = cv2.imwrite(path, frame)
    check(f"wrote fixture frame {name}", ok and os.path.exists(path))
    return path


def test_golden_detection_lock(lay: dict, lib: dict) -> None:
    print("golden detection lock:")
    frame = syn.make_frame(syn.COMP_A1, syn.COMP_B1, offset=101)

    comps = detect.read_frame_comps(frame, lay, lib)
    check("detect.read_frame_comps returns sides a+b", set(comps.keys()) == {"a", "b"})
    check("side a heroes exactly match known comp", heroes(comps["a"]) == syn.COMP_A1)
    check("side b heroes exactly match known comp", heroes(comps["b"]) == syn.COMP_B1)
    assert_scores_clear_threshold("side a", comps["a"])
    assert_scores_clear_threshold("side b", comps["b"])

    reading = hero_overlay_detect.read_frame(frame, lay, lib)
    check("hero_overlay accepts side a", reading["a"]["accepted"] is True)
    check("hero_overlay accepts side b", reading["b"]["accepted"] is True)
    check("hero_overlay side a confidence clears threshold", reading["a"]["confidence"] >= THRESHOLD)
    check("hero_overlay side b confidence clears threshold", reading["b"]["confidence"] >= THRESHOLD)
    check("hero_overlay side a exposes slots", len(reading["a"]["slots"]) == 5)
    check("hero_overlay side b exposes slots", len(reading["b"]["slots"]) == 5)

    shutil.rmtree(FRAMES_DIR, ignore_errors=True)
    os.makedirs(FRAMES_DIR, exist_ok=True)
    write_frame("000000.png", frame)
    res = hero_overlay_detect.detect_dir(FRAMES_DIR, lay, lib,
                                         quarantine_dir=QUARANTINE_DIR)
    check("detect_dir accepts exactly one clean frame", len(res["accepted"]) == 1)
    check("detect_dir quarantines zero clean frames", len(res["quarantined"]) == 0)
    got = res["accepted"][0]
    check("detect_dir side a heroes match known comp", got["a"]["heroes"] == syn.COMP_A1)
    check("detect_dir side b heroes match known comp", got["b"]["heroes"] == syn.COMP_B1)
    check("detect_dir side a confidence present", isinstance(got["a"]["confidence"], float))
    check("detect_dir side b confidence present", isinstance(got["b"]["confidence"], float))
    check("detect_dir side a confidence clears threshold", got["a"]["confidence"] >= THRESHOLD)
    check("detect_dir side b confidence clears threshold", got["b"]["confidence"] >= THRESHOLD)


def test_side_orientation(lay: dict, lib: dict) -> None:
    print("side orientation / left-right lock:")
    check("fixture comps are disjoint enough to catch a side swap",
          set(syn.COMP_A1).isdisjoint(set(syn.COMP_B1)))
    check("slots_a geometry is left of slots_b",
          max(x for x, _y, _w, _h in syn.SLOTS_A) < min(x for x, _y, _w, _h in syn.SLOTS_B))

    frame = syn.make_frame(syn.COMP_A1, syn.COMP_B1, offset=202)
    comps = detect.read_frame_comps(frame, lay, lib)
    check("slots_a content is reported as side a", heroes(comps["a"]) == syn.COMP_A1)
    check("slots_b content is reported as side b", heroes(comps["b"]) == syn.COMP_B1)

    swapped_content = syn.make_frame(syn.COMP_B1, syn.COMP_A1, offset=203)
    swapped = detect.read_frame_comps(swapped_content, lay, lib)
    check("when content is painted into left slots, it follows side a",
          heroes(swapped["a"]) == syn.COMP_B1)
    check("when content is painted into right slots, it follows side b",
          heroes(swapped["b"]) == syn.COMP_A1)


def test_low_confidence_quarantine(lay: dict, lib: dict) -> None:
    print("low-confidence quarantine:")
    shutil.rmtree(FRAMES_DIR, ignore_errors=True)
    shutil.rmtree(QUARANTINE_DIR, ignore_errors=True)
    os.makedirs(FRAMES_DIR, exist_ok=True)

    blank_slots = syn.make_frame([], [], gameplay=True, offset=303)
    write_frame("000303.png", blank_slots)
    res = hero_overlay_detect.detect_dir(FRAMES_DIR, lay, lib,
                                         quarantine_dir=QUARANTINE_DIR)
    check("blank-slot frame is not accepted", len(res["accepted"]) == 0)
    check("blank-slot frame is quarantined", len(res["quarantined"]) == 1)
    reasons = res["quarantined"][0]["reasons"]
    check("quarantine records reasons for both sides", set(reasons.keys()) == {"a", "b"})
    check("side a reason is low-confidence", str(reasons["a"]).startswith("low-confidence"))
    check("side b reason is low-confidence", str(reasons["b"]).startswith("low-confidence"))
    check("quarantined image copied for inspection",
          os.path.exists(os.path.join(QUARANTINE_DIR, "000303.png")))
    sidecar = os.path.join(QUARANTINE_DIR, "000303.png.json")
    check("quarantine sidecar JSON written", os.path.exists(sidecar))
    with open(sidecar, "r", encoding="utf-8") as f:
        payload = json.load(f)
    check("sidecar includes reasons", "reasons" in payload)
    check("sidecar includes raw read", "read" in payload)


def test_duplicate_hero_quarantine(lay: dict, lib: dict) -> None:
    print("duplicate-hero quarantine:")
    duplicate_a = ["winston", "winston", "genji", "kiriko", "juno"]
    frame = syn.make_frame(duplicate_a, syn.COMP_B1, offset=404)
    reading = hero_overlay_detect.read_frame(frame, lay, lib)
    check("duplicate side has strong slot reads", all(s["score"] >= THRESHOLD for s in reading["a"]["slots"]))
    check("duplicate side is rejected", reading["a"]["accepted"] is False)
    check("duplicate side reason is explicit", str(reading["a"]["reason"]).startswith("duplicate hero"))
    check("non-duplicate opposite side still reads cleanly", reading["b"]["accepted"] is True)

    shutil.rmtree(FRAMES_DIR, ignore_errors=True)
    shutil.rmtree(QUARANTINE_DIR, ignore_errors=True)
    os.makedirs(FRAMES_DIR, exist_ok=True)
    write_frame("000404.png", frame)
    res = hero_overlay_detect.detect_dir(FRAMES_DIR, lay, lib,
                                         quarantine_dir=QUARANTINE_DIR)
    check("frame with one duplicate side is not accepted", len(res["accepted"]) == 0)
    check("frame with one duplicate side is quarantined", len(res["quarantined"]) == 1)
    check("detect_dir preserves duplicate reason",
          str(res["quarantined"][0]["reasons"]["a"]).startswith("duplicate hero"))


def apply_slot_tint(frame: np.ndarray, slots: list[list[int]], bgr: tuple[int, int, int], alpha: float) -> np.ndarray:
    """Apply a team-color cast only over hero slots, not the whole frame."""
    out = frame.copy()
    color = np.full_like(frame, bgr, dtype=np.uint8)
    for x, y, w, h in slots:
        out[y:y+h, x:x+w] = cv2.addWeighted(frame[y:y+h, x:x+w], 1.0 - alpha,
                                            color[y:y+h, x:x+w], alpha, 0)
    return out


def test_tint_cast_lock(lay: dict, lib: dict) -> None:
    print("tint-cast robustness lock:")
    frame = syn.make_frame(syn.COMP_A1, syn.COMP_B1, offset=505)
    # BGR: red cast for left team, blue cast for right team. On these
    # synthetic fixtures the current grayscale + TM_CCOEFF_NORMED path is
    # effectively invariant, so lock that behavior. Real broadcast tint still
    # needs real cropped acceptance tests in the future masked-template pass.
    frame = apply_slot_tint(frame, syn.SLOTS_A, (35, 35, 210), 0.20)
    frame = apply_slot_tint(frame, syn.SLOTS_B, (210, 35, 35), 0.20)
    comps = detect.read_frame_comps(frame, lay, lib)
    check("red-tinted side a heroes unchanged", heroes(comps["a"]) == syn.COMP_A1)
    check("blue-tinted side b heroes unchanged", heroes(comps["b"]) == syn.COMP_B1)
    check("red-tinted side a scores stay high", min(all_slot_scores(comps["a"])) >= 0.9)
    check("blue-tinted side b scores stay high", min(all_slot_scores(comps["b"])) >= 0.9)



def main() -> int:
    reset_workspace()
    lib = write_templates()
    lay = layout()

    test_golden_detection_lock(lay, lib)
    test_side_orientation(lay, lib)
    test_low_confidence_quarantine(lay, lib)
    test_duplicate_hero_quarantine(lay, lib)
    test_tint_cast_lock(lay, lib)

    print(f"\nALL DETECTION REGRESSION TESTS PASSED ({_checks} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
