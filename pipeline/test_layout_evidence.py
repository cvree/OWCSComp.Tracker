#!/usr/bin/env python3
"""Offline tests for the Phase 2 calibration/evidence workflow:
validate_layout, layout.html, build_crop_report, and their wiring into
run_owcs_auto's layout-debug step. No network, no yt-dlp."""
from __future__ import annotations
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_layout_debug as bld  # noqa: E402
import build_crop_report as bcr  # noqa: E402
import run_owcs_auto as roa  # noqa: E402

TMP = tempfile.mkdtemp(prefix="owcs_layout_evidence_")
_fails = 0


def check(name: str, ok: bool) -> None:
    global _fails
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        _fails += 1


def good_layout() -> dict:
    return {"frame_width": 640, "frame_height": 360,
            "slots_a": [[10 + i * 60, 10, 40, 40] for i in range(5)],
            "slots_b": [[330 + i * 60, 10, 40, 40] for i in range(5)],
            "match_threshold": 0.6}


def synth_frame(layout: dict):
    """Frame with a distinct bright pattern inside every slot box.

    Circle radius varies by slot index (not just background color) so
    grayscale template matching can actually tell slots apart — solid-color
    blocks differing only by brightness tie at score 1.0 under normalized
    cross-correlation, which is realistic detector behavior but useless for
    exercising a clear top pick in tests."""
    img = np.zeros((layout["frame_height"], layout["frame_width"], 3),
                   dtype=np.uint8)
    val = 40
    for side in ("slots_a", "slots_b"):
        for idx, (x, y, w, h) in enumerate(layout[side]):
            img[y:y + h, x:x + w] = (val, 255 - val, (val * 3) % 255)
            r = max(6, w // 3 - idx * 4)
            cv2.circle(img, (x + w // 2, y + h // 2), r,
                       (255, 255, 255), -1)
            val += 20
    return img


def main() -> int:
    print("validate_layout:")
    check("valid layout -> no warnings", bld.validate_layout(good_layout()) == [])
    lay = good_layout(); lay["slots_a"] = lay["slots_a"][:4]
    check("4 slots on a side is flagged",
          any("expected exactly 5" in w for w in bld.validate_layout(lay)))
    lay = good_layout(); lay["slots_b"][2] = [630, 10, 40, 40]
    check("out-of-frame box is flagged",
          any("outside the 640x360" in w for w in bld.validate_layout(lay)))
    lay = good_layout(); lay["slots_a"][0] = [10, 10, 0, 40]
    check("zero-size box is flagged",
          any("zero/negative size" in w for w in bld.validate_layout(lay)))
    lay = good_layout(); del lay["frame_width"]
    check("missing frame size is flagged",
          any("frame_width/frame_height" in w for w in bld.validate_layout(lay)))
    lay = good_layout(); lay["anchor"] = {"rect": [600, 340, 100, 40]}
    check("out-of-frame anchor rect is flagged",
          any("anchor" in w for w in bld.validate_layout(lay)))

    print("layout.html viewer:")
    lay = good_layout()
    lay_path = os.path.join(TMP, "lay.json")
    with open(lay_path, "w", encoding="utf-8") as f:
        json.dump(lay, f)
    frames_dir = os.path.join(TMP, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    cv2.imwrite(os.path.join(frames_dir, "000000.png"), synth_frame(lay))
    cv2.imwrite(os.path.join(frames_dir, "000010.png"), synth_frame(lay))
    report_dir = os.path.join(TMP, "report")
    dbg_dir = os.path.join(report_dir, "layout_debug")
    made = bld.process_dir(frames_dir, dict(lay, _dir=TMP), dbg_dir)
    html_path = bld.write_layout_html(
        os.path.join(report_dir, "layout.html"), lay, lay_path, made,
        frames_dir=frames_dir)
    html = open(html_path, encoding="utf-8").read()
    check("embeds every annotated frame",
          html.count("_debug.png") >= 2 * len(made) // 2
          and "000000_debug.png" in html)
    check("valid layout shows the green validation box",
          "Layout validation: 5+5 slots present" in html)
    check("shows the exact no-re-download re-run commands",
          "build_layout_debug.py --layout" in html
          and "build_crop_report.py --layout" in html)
    check("links crops.html + run report + runs.html",
          "crops.html" in html and "index.html" in html
          and "runs.html" in html)
    bad = dict(lay); bad["slots_a"] = lay["slots_a"][:3]
    html2 = open(bld.write_layout_html(
        os.path.join(report_dir, "layout.html"), bad, lay_path, made,
        frames_dir=frames_dir), encoding="utf-8").read()
    check("warnings are rendered in the viewer",
          "warning(s):" in html2 and "expected exactly 5" in html2)

    print("build_crop_report (no templates yet):")
    empty_tdir = os.path.join(TMP, "empty_templates")
    os.makedirs(empty_tdir, exist_ok=True)
    with contextlib.redirect_stdout(io.StringIO()):
        res = bcr.process(frames_dir, lay, report_dir,
                          templates_dir=empty_tdir)
    crops_html = open(os.path.join(report_dir, "crops.html"),
                      encoding="utf-8").read()
    check("10 crops per frame written",
          res["crops"] == 20 and len(os.listdir(
              os.path.join(report_dir, "crops"))) == 20)
    check("no-templates state is loud, not silent",
          res["templates"] is False and "No hero templates yet" in crops_html)
    check("crops.html shows every slot id",
          all(f">{sid}<" in crops_html
              for sid in ["a1", "a5", "b1", "b5"]))

    print("build_crop_report (with templates -> scores + labels):")
    tdir = os.path.join(TMP, "templates")
    os.makedirs(tdir, exist_ok=True)
    frame = synth_frame(lay)
    for i, (x, y, w, h) in enumerate(lay["slots_a"], start=1):
        crop = cv2.cvtColor(frame[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)
        cv2.imwrite(os.path.join(tdir, f"hero{i}.png"), crop)
    with contextlib.redirect_stdout(io.StringIO()):
        res2 = bcr.process(frames_dir, lay, report_dir, templates_dir=tdir)
    crops_html2 = open(os.path.join(report_dir, "crops.html"),
                       encoding="utf-8").read()
    check("templates detected and scored",
          res2["templates"] is True and ">OK<" in crops_html2
          and "hero1" in crops_html2)
    check("matched template image shown next to the crop",
          "tpl_hero1.png" in crops_html2)
    check("runner-up and margin shown, not just a bare score",
          "margin" in crops_html2 and "2nd" in crops_html2)
    check("score labels legend present",
          "UNKNOWN" in crops_html2 or "LOW" in crops_html2
          or "OK &ge;" in crops_html2)

    def _read(hero, score, second="x", second_score=0.0, reject=None):
        return {"hero": hero, "score": score, "second": second,
                "second_score": second_score, "reject": reject}
    check("label thresholds",
          bcr._label(_read("hero1", 0.9), 0.6) == "OK"
          and bcr._label(_read("hero1", 0.5), 0.6) == "LOW"
          and bcr._label(_read("UNKNOWN", 0.1), 0.6) == "UNKNOWN")

    print("bad boxes degrade per-slot, not per-run:")
    lay_oob = good_layout(); lay_oob["slots_b"][4] = [635, 10, 40, 40]
    with contextlib.redirect_stdout(io.StringIO()):
        res3 = bcr.process(frames_dir, lay_oob, report_dir)
    html3 = open(os.path.join(report_dir, "crops.html"),
                 encoding="utf-8").read()
    check("out-of-bounds slot shows BAD BOX, others still crop",
          "BAD BOX" in html3 and res3["crops"] == 18)

    print("run_owcs_auto layout-debug step integration:")
    with contextlib.redirect_stdout(io.StringIO()):
        dbg = roa._step_debug(frames_dir, lay_path,
                              os.path.join(TMP, "run_report", "layout_debug"))
    rr = os.path.join(TMP, "run_report")
    check("real _step_debug writes images + layout.html + crops.html",
          dbg["images"] == 2 and dbg["layoutWarnings"] == 0
          and os.path.exists(os.path.join(rr, "layout.html"))
          and os.path.exists(os.path.join(rr, "crops.html"))
          and dbg.get("crops") == 20
          and dbg.get("cropTemplates") is True)  # 17 starter templates resolve
    check("report html links the evidence pages",
          "layout.html" in roa.build_report_html(
              {"run": "x", "steps": [], "ok": True}, [])
          and "crops.html" in roa.build_report_html(
              {"run": "x", "steps": [], "ok": True}, []))

    shutil.rmtree(TMP, ignore_errors=True)
    print("ALL PASS" if _fails == 0 else f"{_fails} FAILURES")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
