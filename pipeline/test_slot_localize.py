#!/usr/bin/env python3
"""
test_slot_localize.py — offline, deterministic tests for slot_localize.py.

Synthetic 1280x720 HUD frames are drawn with cv2: 5 colorful "portrait"
squares per side at a known grid, name strips underneath, plus injected
"OCR" text boxes via a synthetic ocr_hud.json. No network, no OCR engine,
no real VOD artifacts. Covers the six required cases:
  1. proposed boxes stay inside frame bounds
  2. detects 5 slots per side on a synthetic HUD (incl. a missing portrait
     synthesized from the grid)
  3. rejects text-contaminated crops (OCR overlap => text-contaminated)
  4. writes proposed layout ONLY when enough slots are found; never touches
     the original layout
  5. no DB/template/comp writes (file snapshot before/after)
  6. plus: IoU comparison vs a deliberately shifted current layout.
"""
from __future__ import annotations
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np                  # noqa: E402
import cv2                          # noqa: E402
import slot_localize as sl          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

_fails = 0
GRID_A = [(40 + i * 80, 20) for i in range(5)]     # x,y — 64x64 portraits
GRID_B = [(800 + i * 80, 20) for i in range(5)]
PW = 64


def check(name, ok):
    global _fails
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        _fails += 1


def synth_frame(skip_slots=(), seed=3):
    """Dark frame + noisy portrait squares at the known grid + name strips."""
    rng = np.random.RandomState(seed)
    img = np.full((720, 1280, 3), 22, np.uint8)
    for si, grid in (("a", GRID_A), ("b", GRID_B)):
        for i, (x, y) in enumerate(grid):
            if f"{si}{i + 1}" in skip_slots:
                continue
            block = (rng.rand(PW, PW, 3) * 200 + 40).astype(np.uint8)
            cv2.rectangle(block, (0, 0), (PW - 1, PW - 1), (255, 255, 255), 2)
            img[y:y + PW, x:x + PW] = block
            cv2.putText(img, "PlayerTag", (x, y + PW + 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
    return img


def make_root(tmp, name):
    root = os.path.join(tmp, name)
    for d in ("data", "layouts", "reports", "templates", "work"):
        os.makedirs(os.path.join(root, d))
    shutil.copy(os.path.join(ROOT, "layouts", "owcs-demo.json"),
                os.path.join(root, "layouts", "owcs-demo.json"))
    return root


def setup_run(root, run, frames):
    fdir = os.path.join(root, "work", "auto", run, "frames_raw")
    os.makedirs(fdir)
    for fn, img in frames:
        cv2.imwrite(os.path.join(fdir, fn), img)
    return fdir


def fake_ocr_hud(root, run, frame_name, boxes):
    """Minimal ocr_hud.json that slot_localize reads contamination from."""
    rd = os.path.join(root, "reports", "auto", run)
    os.makedirs(rd, exist_ok=True)
    data = {"frames": [{"frame": frame_name, "analysis": {"ocr_purposes": [
        {"text": "SOMETEXT", "conf": 0.9, "box": b, "purpose": "other"}
        for b in boxes]}}]}
    with open(os.path.join(rd, "ocr_hud.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def snapshot(root, skip):
    out = {}
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            r = os.path.relpath(p, root)
            if any(s in r for s in skip):
                continue
            st = os.stat(p)
            out[r] = (st.st_size, st.st_mtime_ns)
    return out


SKIP = ("slot_localization", ".proposed.json")


def test_full_grid(tmp):
    print("\n[1+2+6] full synthetic HUD — 10 slots, bounds, IoU, safe write")
    root = make_root(tmp, "t1")
    run = "synth_000000_000030"
    setup_run(root, run, [("000000.png", synth_frame(seed=1)),
                          ("000010.png", synth_frame(seed=2))])

    # shift the current layout right by 12px so IoU < 1 but boxes overlap
    lp = os.path.join(root, "layouts", "owcs-demo.json")
    lay = json.load(open(lp, encoding="utf-8"))
    for k in ("slots_a", "slots_b"):
        lay[k] = [[x + 12, y, w, h] for x, y, w, h in lay[k]]
    json.dump(lay, open(lp, "w", encoding="utf-8"), indent=1)
    orig_bytes = open(lp, "rb").read()

    before = snapshot(root, SKIP)
    res = sl.run_localization(run, "layouts/owcs-demo.json", root=root)
    after = snapshot(root, SKIP)

    agg = res["agg"]
    check("10 slots localized", len(agg) == 10)
    ok_pos = all(abs(agg[f"a{i+1}"]["box"][0] - GRID_A[i][0]) <= 6
                 and abs(agg[f"a{i+1}"]["box"][1] - GRID_A[i][1]) <= 6
                 for i in range(5))
    check("A-side boxes within 6px of true grid", ok_pos)
    ok_pos_b = all(abs(agg[f"b{i+1}"]["box"][0] - GRID_B[i][0]) <= 6
                   for i in range(5))
    check("B-side boxes within 6px of true grid", ok_pos_b)
    check("all proposed boxes in bounds", all(
        a["box"][0] >= 0 and a["box"][1] >= 0
        and a["box"][0] + a["box"][2] <= 1280
        and a["box"][1] + a["box"][3] <= 720 for a in agg.values()))
    check("IoU vs shifted layout in (0.4, 0.95)", all(
        0.4 < v < 0.95 for v in res["ious"].values()) and len(res["ious"])
        == 10)
    check("verdict safe", res["safety"]["safe"] is True)
    check("proposed layout written", res["proposed"] is not None
          and os.path.isfile(os.path.join(root, res["proposed"])))
    check("proposed filename forced .proposed.json",
          res["proposed"].endswith(".proposed.json"))
    prop = json.load(open(os.path.join(root, res["proposed"]),
                          encoding="utf-8"))
    check("proposed carries review note + 10 boxes",
          "_proposed_by" in prop and len(prop["slots_a"]) == 5
          and len(prop["slots_b"]) == 5)
    check("original layout byte-identical",
          open(lp, "rb").read() == orig_bytes)
    check("no writes outside slot_localization/proposed", before == after)
    html = open(res["html"], encoding="utf-8").read()
    check("html: safe verdict + manual adopt command",
          "can be updated safely" in html and "copy " in html)
    check("html: crop previews rendered", "slot_localization/crops/" in html)


def test_missing_portrait_synthesized(tmp):
    print("\n[2b] one portrait missing — synthesized from grid, still 10")
    root = make_root(tmp, "t2")
    run = "gap_000000_000010"
    setup_run(root, run, [("000000.png", synth_frame(skip_slots=("a3",)))])
    res = sl.run_localization(run, "layouts/owcs-demo.json", root=root)
    data = json.load(open(res["json"], encoding="utf-8"))
    a3 = data["frames"][0]["slots"].get("a3")
    check("a3 present though portrait missing", a3 is not None)
    check("a3 marked synthesized (not detected)",
          a3 and a3["detected"] is False)
    check("a3 synthesized near true grid",
          a3 and abs(a3["box"][0] - GRID_A[2][0]) <= 8)
    check("still 10 aggregated slots", len(res["agg"]) == 10)


def test_contamination(tmp):
    print("\n[3] OCR text overlap -> text-contaminated, never hero identity")
    root = make_root(tmp, "t3")
    run = "contam_000000_000010"
    setup_run(root, run, [("000000.png", synth_frame())])
    # OCR says there is text right on top of portrait a2 (and b5 partially)
    a2 = [GRID_A[1][0], GRID_A[1][1], PW, PW]
    b5_half = [GRID_B[4][0], GRID_B[4][1] + PW // 2, PW, PW // 2]
    fake_ocr_hud(root, run, "000000.png", [a2, b5_half])
    res = sl.run_localization(run, "layouts/owcs-demo.json", root=root)
    data = json.load(open(res["json"], encoding="utf-8"))
    slots = data["frames"][0]["slots"]
    check("a2 marked text-contaminated",
          slots["a2"]["quality"] == "text-contaminated")
    check("b5 (half overlap) marked text-contaminated",
          slots["b5"]["quality"] == "text-contaminated")
    check("a1 stays usable", slots["a1"]["quality"] == "usable")
    check("ocr role stated in json",
          "never hero identity" in data["ocr_role"])
    check("json honesty markers", data["candidate"] is True
          and data["promoted"] is False)


def test_not_enough_slots(tmp):
    print("\n[4] sparse HUD — no proposed layout written")
    root = make_root(tmp, "t4")
    run = "sparse_000000_000010"
    # only 2 portraits per side -> grid support < MIN_SUPPORT
    img = synth_frame(skip_slots=("a3", "a4", "a5", "b1", "b2", "b3"))
    setup_run(root, run, [("000000.png", img)])
    res = sl.run_localization(run, "layouts/owcs-demo.json", root=root)
    check("verdict not safe", res["safety"]["safe"] is False)
    check("reason mentions support or slots",
          "support" in res["safety"]["reason"]
          or "slots" in res["safety"]["reason"])
    check("no proposed layout file", res["proposed"] is None and not any(
        f.endswith(".proposed.json")
        for f in os.listdir(os.path.join(root, "layouts"))))
    html = open(res["html"], encoding="utf-8").read()
    check("html: not-safe verdict", "Not safe to update layout yet" in html)


def test_empty_run(tmp):
    print("\n[5] no frames — renders, not safe, nothing written elsewhere")
    root = make_root(tmp, "t5")
    run = "ghost_000000_000010"
    before = snapshot(root, SKIP)
    res = sl.run_localization(run, "layouts/owcs-demo.json", root=root)
    after = snapshot(root, SKIP)
    check("renders with zero frames", res["frames"] == 0
          and os.path.isfile(res["html"]))
    check("not safe + capture hint",
          not res["safety"]["safe"]
          and "capture" in res["safety"]["reason"])
    check("no writes at all outside outputs", before == after)


def test_guards():
    print("\n[u] guards")
    check("proposed path never equals original",
          sl.proposed_layout_path("layouts/x.json", ROOT, "r")
          == "layouts/x.proposed.json")
    try:
        sl.write_proposed_layout("layouts/x.json", {}, {}, 1, 1, "r")
        check("write_proposed refuses non-proposed path", False)
    except RuntimeError:
        check("write_proposed refuses non-proposed path", True)


def main():
    print("slot_localize offline tests")
    with tempfile.TemporaryDirectory() as tmp:
        test_full_grid(tmp)
        test_missing_portrait_synthesized(tmp)
        test_contamination(tmp)
        test_not_enough_slots(tmp)
        test_empty_run(tmp)
        test_guards()
    print(f"\n{'ALL PASS' if _fails == 0 else f'{_fails} FAILURE(S)'}")
    return 1 if _fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
