#!/usr/bin/env python3
"""
test_frame_filter_highlight.py — offline tests for HIGHLIGHT/HIGHLIGHTS
(reject-marker) frame rejection. No network, no DB, no real VOD.

Builds synthetic 1280x720 broadcast frames (textured background + a HUD
"anchor" bar + optional "replay" wipe + optional bright HIGHLIGHTS banner)
and writes matching grayscale templates to a temp dir, then exercises the
REAL code paths:

  1. capture._load_reject_markers  -> off when 'reject' absent, on when present
  2. capture.reject_reason         -> fires only when the banner is present
  3. capture.is_gameplay(..., rejects)
        * highlight banner        -> rejected, reason starts 'highlight'
        * clean frame             -> 'gameplay'
        * banner but NO anchor    -> still 'highlight' (checked first/anywhere)
        * replay marker present   -> still 'replay ...' (unchanged)
        * no anchor, no banner    -> 'no-hud ...' (unchanged)
  4. capture.scale_layout_to_frame -> anchor/replay/slots scale EXACTLY as
        before (reject block does not perturb them); reject rect scales too;
        aspect mismatch still returns ok=False with the layout unchanged
  5. frame_filter.filter_frames    -> keeps clean, rejects highlight with a
        'highlight' reason, and (default-off) passes the banner frame when the
        layout has no 'reject' key.

Run:  python3 pipeline/test_frame_filter_highlight.py
Exits non-zero on any failure.
"""
from __future__ import annotations
import os
import shutil
import sys
import tempfile

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import capture       # noqa: E402
import frame_filter  # noqa: E402

TMP = tempfile.mkdtemp(prefix="owcs_highlight_")
_fails = 0

W, H = 1280, 720
ANCHOR_RECT = [560, 8, 160, 40]
REPLAY_RECT = [20, 620, 120, 60]
HL_RECT = [520, 470, 240, 90]     # broad-ish banner band, lower-centre


def check(name: str, ok: bool) -> None:
    global _fails
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        _fails += 1


# ------------------------------------------------------------ frame builders
def _bg() -> np.ndarray:
    """Deterministic textured background so flat-region false matches can't
    inflate TM_CCOEFF_NORMED scores."""
    rng = np.random.default_rng(7)
    return rng.integers(30, 200, size=(H, W, 3), dtype=np.uint8)


def paint_anchor(frame: np.ndarray) -> None:
    x, y, w, h = ANCHOR_RECT
    frame[y:y + h, x:x + w] = (40, 40, 40)
    cv2.rectangle(frame, (x + 4, y + 4), (x + w - 4, y + h - 4), (0, 180, 255), 2)
    cv2.putText(frame, "OBJ", (x + 50, y + 28), cv2.FONT_HERSHEY_SIMPLEX,
                0.8, (255, 255, 255), 2)


def paint_replay(frame: np.ndarray) -> None:
    x, y, w, h = REPLAY_RECT
    frame[y:y + h, x:x + w] = (10, 10, 200)
    cv2.putText(frame, "RP", (x + 20, y + 40), cv2.FONT_HERSHEY_SIMPLEX,
                1.0, (255, 255, 255), 3)


def paint_highlight(frame: np.ndarray) -> None:
    x, y, w, h = HL_RECT
    frame[y:y + h, x:x + w] = (245, 245, 245)          # bright plate
    cv2.putText(frame, "HIGHLIGHTS", (x + 8, y + 58), cv2.FONT_HERSHEY_SIMPLEX,
                1.1, (0, 0, 0), 3)


def make_frame(anchor=True, replay=False, highlight=False) -> np.ndarray:
    f = _bg()
    if anchor:
        paint_anchor(f)
    if replay:
        paint_replay(f)
    if highlight:
        paint_highlight(f)
    return f


def write_template(frame: np.ndarray, rect: list, path: str) -> None:
    x, y, w, h = rect
    crop = cv2.cvtColor(frame[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)
    cv2.imwrite(path, crop)


# ----------------------------------------------------------------- fixtures
def build_layout() -> dict:
    """Layout with anchor + replay + an ACTIVE reject (highlight) marker,
    all templates cut from synthetic frames on disk."""
    anchor_png = os.path.join(TMP, "anchor.png")
    replay_png = os.path.join(TMP, "replay.png")
    hl_png = os.path.join(TMP, "highlight.png")
    write_template(make_frame(anchor=True), ANCHOR_RECT, anchor_png)
    write_template(make_frame(anchor=True, replay=True), REPLAY_RECT, replay_png)
    write_template(make_frame(anchor=True, highlight=True), HL_RECT, hl_png)
    return {
        "frame_width": W, "frame_height": H,
        "anchor": {"rect": ANCHOR_RECT, "template": anchor_png, "min_score": 0.7},
        "replay": {"rect": REPLAY_RECT, "template": replay_png, "min_score": 0.7},
        "reject": [{"label": "highlight", "kind": "template",
                    "rect": HL_RECT, "template": hl_png, "min_score": 0.8}],
        "slots_a": [[40 + i * 80, 20, 64, 64] for i in range(5)],
        "slots_b": [[800 + i * 80, 20, 64, 64] for i in range(5)],
        "match_threshold": 0.6,
    }


def main() -> int:
    layout = build_layout()
    anchor = capture._load_template(layout, "anchor")
    replay = capture._load_template(layout, "replay")

    print("_load_reject_markers on/off:")
    rejects = capture._load_reject_markers(layout)
    check("active 'reject' block loads exactly 1 marker", len(rejects) == 1)
    check("marker carries label + loaded template img",
          rejects[0]["label"] == "highlight" and "img" in rejects[0])
    no_reject_layout = {k: v for k, v in layout.items() if k != "reject"}
    check("absent 'reject' key -> feature OFF (0 markers)",
          capture._load_reject_markers(no_reject_layout) == [])
    check("inert '_reject_example' key is ignored (still 0 markers)",
          capture._load_reject_markers({"_reject_example": layout["reject"]}) == [])

    print("reject_reason fires only on the banner:")
    g_hl = cv2.cvtColor(make_frame(highlight=True), cv2.COLOR_BGR2GRAY)
    g_clean = cv2.cvtColor(make_frame(), cv2.COLOR_BGR2GRAY)
    r_hl = capture.reject_reason(g_hl, rejects)
    check("banner present -> reason returned", r_hl is not None)
    check("reason clearly starts with 'highlight'",
          bool(r_hl) and r_hl.startswith("highlight"))
    check("clean frame -> no reject reason",
          capture.reject_reason(g_clean, rejects) is None)

    print("is_gameplay with rejects:")
    ok, reason, _ = capture.is_gameplay(make_frame(highlight=True),
                                        anchor, replay, rejects)
    check("HIGHLIGHTS frame is skipped", ok is False)
    check("skip reason clearly 'highlight'", reason.startswith("highlight"))
    ok2, reason2, _ = capture.is_gameplay(make_frame(), anchor, replay, rejects)
    check("clean gameplay frame still passes", ok2 is True and reason2 == "gameplay")
    # banner overlaid but NO anchor -> highlight must win (checked first/anywhere)
    ok3, reason3, _ = capture.is_gameplay(make_frame(anchor=False, highlight=True),
                                          anchor, replay, rejects)
    check("banner without HUD anchor -> 'highlight', not 'no-hud'",
          ok3 is False and reason3.startswith("highlight"))

    print("existing anchor/replay behavior unchanged (reject block present):")
    okr, reasonr, _ = capture.is_gameplay(make_frame(replay=True),
                                          anchor, replay, rejects)
    check("replay frame still rejected as 'replay'",
          okr is False and reasonr.startswith("replay"))
    okn, reasonn, _ = capture.is_gameplay(make_frame(anchor=False),
                                          anchor, replay, rejects)
    check("no-anchor break frame still 'no-hud'",
          okn is False and reasonn.startswith("no-hud"))
    # backward-compat: old 3-arg call path is identical to rejects=None
    check("3-arg is_gameplay (no rejects) == rejects-off behavior",
          capture.is_gameplay(make_frame(highlight=True), anchor, replay)[0] is True)

    print("scale_layout_to_frame: anchor/replay/slots identical; reject scales:")
    base = {k: v for k, v in layout.items() if k not in ("reject",)}
    scaled_base, ib = capture.scale_layout_to_frame(base, 640, 360)
    scaled_full, if_ = capture.scale_layout_to_frame(layout, 640, 360)
    check("reject block does NOT change anchor scaling",
          scaled_full["anchor"]["rect"] == scaled_base["anchor"]["rect"])
    check("reject block does NOT change replay scaling",
          scaled_full["replay"]["rect"] == scaled_base["replay"]["rect"])
    check("reject block does NOT change slot scaling",
          scaled_full["slots_a"] == scaled_base["slots_a"]
          and scaled_full["slots_b"] == scaled_base["slots_b"])
    check("scale report note unchanged by reject block", ib["note"] == if_["note"])
    hx, hy, hw, hh = HL_RECT
    check("reject rect scales by the same 1/2 factor",
          scaled_full["reject"][0]["rect"]
          == [round(hx / 2), round(hy / 2), max(1, round(hw / 2)),
              max(1, round(hh / 2))])
    check("input layout not mutated by scaling",
          layout["reject"][0]["rect"] == HL_RECT)
    # aspect mismatch path unchanged: ok=False + layout returned unchanged
    _, bad = capture.scale_layout_to_frame(layout, 800, 800)
    check("aspect mismatch still ok=False with a reason",
          bad["ok"] is False and bad["reason"])

    print("frame_filter.filter_frames end to end:")
    in_dir = os.path.join(TMP, "frames_raw")
    out_dir = os.path.join(TMP, "frames")
    os.makedirs(in_dir, exist_ok=True)
    cv2.imwrite(os.path.join(in_dir, "000000.png"), make_frame())
    cv2.imwrite(os.path.join(in_dir, "000300.png"), make_frame(highlight=True))
    res = frame_filter.filter_frames(in_dir, out_dir, layout)
    check("only the clean frame is kept", res["kept"] == ["000000.png"])
    rej = dict(res["rejected"])
    check("highlight frame is in rejected", "000300.png" in rej)
    check("its filter reason clearly 'highlight'",
          rej.get("000300.png", "").startswith("highlight"))
    check("rejected frame copied aside for inspection",
          os.path.exists(os.path.join(res["rejected_dir"], "000300.png")))

    print("frame_filter default-OFF when layout has no 'reject':")
    out_dir2 = os.path.join(TMP, "frames_off")
    res_off = frame_filter.filter_frames(in_dir, out_dir2, no_reject_layout)
    check("without 'reject', the banner frame passes as gameplay",
          set(res_off["kept"]) == {"000000.png", "000300.png"})

    if _fails:
        print(f"\n{_fails} CHECK(S) FAILED")
        return 1
    print("\nALL HIGHLIGHT / REJECT-MARKER TESTS PASSED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
