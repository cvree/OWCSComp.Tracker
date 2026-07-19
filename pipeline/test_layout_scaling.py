#!/usr/bin/env python3
"""Offline tests for automatic layout scaling (native layout size -> actual
frame size) and its effect on cropping + detection preflight. No network."""
from __future__ import annotations
import contextlib
import io
import os
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture  # noqa: E402
import build_crop_report as bcr  # noqa: E402
import run_owcs_auto as roa  # noqa: E402

TMP = tempfile.mkdtemp(prefix="owcs_scaling_")
_fails = 0


def check(name: str, ok: bool) -> None:
    global _fails
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        _fails += 1


def layout_1080() -> dict:
    """A real-shaped 1920x1080 layout: 5+5 top-HUD portrait boxes."""
    return {"frame_width": 1920, "frame_height": 1080,
            "slots_a": [[252 + round(i * 82.5), 8, 76, 72] for i in range(5)],
            "slots_b": [[1262 + round(i * 82.5), 8, 76, 72] for i in range(5)],
            "match_threshold": 0.6}


def main() -> int:
    print("scale_layout_to_frame math:")
    lay = layout_1080()
    scaled, info = capture.scale_layout_to_frame(lay, 640, 360)
    check("640x360 from 1920x1080 reports scaled + correct factor",
          info["scaled"] and info["ok"] and abs(info["factor"] - 1 / 3) < 1e-3)
    check("report note states the exact from->to scaling",
          "1920x1080 to 640x360" in info["note"])
    check("first left box scales by 1/3",
          scaled["slots_a"][0] == [round(252 / 3), round(8 / 3),
                                   round(76 / 3), round(72 / 3)])
    check("input layout is not mutated",
          lay["slots_a"][0] == [252, 8, 76, 72])
    check("scaled frame size recorded",
          scaled["frame_width"] == 640 and scaled["frame_height"] == 360)

    print("all 10 scaled slots stay in bounds at 640x360:")
    oob = []
    for key in ("slots_a", "slots_b"):
        for i, (x, y, w, h) in enumerate(scaled[key], start=1):
            if x < 0 or y < 0 or x + w > 640 or y + h > 360 or w <= 0 or h <= 0:
                oob.append(f"{key}[{i}]")
    check("no scaled slot out of bounds", not oob)
    check("exactly 10 slots total",
          len(scaled["slots_a"]) + len(scaled["slots_b"]) == 10)

    print("identity + no-native-size + aspect-mismatch:")
    same, i2 = capture.scale_layout_to_frame(lay, 1920, 1080)
    check("same size -> identity, not scaled",
          not i2["scaled"] and same["slots_a"] == lay["slots_a"])
    no_wh = {"slots_a": [[1, 1, 2, 2]], "slots_b": []}
    _, i3 = capture.scale_layout_to_frame(no_wh, 640, 360)
    check("layout without native size -> used as-is", not i3["scaled"]
          and i3["ok"])
    _, i4 = capture.scale_layout_to_frame(lay, 1440, 1080)  # 4:3, mismatch
    check("aspect mismatch -> ok=False with reason",
          not i4["ok"] and "aspect" in i4["reason"])

    print("build_crop_report crops all 10 slots from a scaled 360p frame:")
    frames_dir = os.path.join(TMP, "frames_raw")
    os.makedirs(frames_dir, exist_ok=True)
    for off in (360, 370, 380):  # 3 real-sized fallback frames
        f = np.random.randint(0, 255, (360, 640, 3), dtype=np.uint8)
        cv2.imwrite(os.path.join(frames_dir, f"{off:06d}.png"), f)
    report_dir = os.path.join(TMP, "report")
    with contextlib.redirect_stdout(io.StringIO()):
        res = bcr.process(frames_dir, layout_1080(), report_dir,
                          templates_dir=os.path.join(TMP, "no_templates"))
    check("3 frames x 10 slots = 30 crops, none skipped",
          res["crops"] == 30 and res["skipped"] == [])
    html = open(os.path.join(report_dir, "crops.html"), encoding="utf-8").read()
    check("crops.html states actual size, layout size, scale factor",
          "640x360" in html and "1920x1080" in html and "x0.333" in html)
    check("crops.html shows raw + annotated frame per frame",
          "raw frame" in html and "annotated (scaled boxes)" in html)
    check("all 10 slot ids present", all(f">{s}<" in html
          for s in ["a1", "a5", "b1", "b5"]))

    print("skipped crops explain exactly which slot and why:")
    bad = layout_1080()
    bad["slots_b"][4] = [1900, 8, 76, 72]  # 1900+76 > 1920 native -> off-screen
    with contextlib.redirect_stdout(io.StringIO()):
        res2 = bcr.process(frames_dir, bad, report_dir,
                           templates_dir=os.path.join(TMP, "no_templates"))
    check("one slot skipped per frame -> 27 crops",
          res2["crops"] == 27 and len(res2["skipped"]) == 3)
    check("skip names the exact slot (b5) and reason",
          all(s["slot"] == "b5" and "outside" in s["note"]
              for s in res2["skipped"]))
    html2 = open(os.path.join(report_dir, "crops.html"),
                 encoding="utf-8").read()
    check("BAD BOX rendered for the off-screen slot", "BAD BOX" in html2)

    print("detect_preflight passes for a scaled 360p frame:")
    pf = os.path.join(TMP, "pf")
    os.makedirs(pf, exist_ok=True)
    cv2.imwrite(os.path.join(pf, "000360.png"),
                np.zeros((360, 640, 3), dtype=np.uint8))
    check("360p frame + 1080p layout -> no skip (auto-scaled)",
          roa.detect_preflight(pf, layout_1080()) is None)

    if _fails:
        print(f"\n{_fails} CHECK(S) FAILED")
        return 1
    print("\nALL LAYOUT SCALING TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
