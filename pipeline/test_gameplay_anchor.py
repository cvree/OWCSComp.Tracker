#!/usr/bin/env python3
"""
test_gameplay_anchor.py — proves the REAL gameplay-HUD anchor works.

Standalone (no pytest):   python3 pipeline/test_gameplay_anchor.py

Everything here is measured against pixels ALREADY COMMITTED to this repo.
Nothing is downloaded, nothing is synthesized to stand in for a broadcast
frame, and no score is asserted that was not actually computed:

  POSITIVES (must match) — 11 real frames of the verified Nepal map from the
    owcs-jksix-qwc broadcast, every one of them classified 'gameplay' by the
    production run recorded in reports/ingest/qad-twis-nepal/observations.jsonl
      * reports/ingest/qad-twis-nepal/frames/*.jpg          (7 frames, 1280x720)
      * reports/calibration/owcs-jksix-qwc/sheet.png        (4 tiles, 1280x720)

  NEGATIVES (must NOT match) — real committed frames that carry a different
    overlay or none at all, i.e. exactly the "the broadcast HUD is not on
    screen" case the anchor exists to catch
      * reports/capture_trial/frames/*.png
      * pipeline/fixtures/video/demo_match/frames/*.png

  SPECIFICITY — the anchor is also slid across the WHOLE of each positive
    frame; its peak must be at the anchor's own rect and every other position
    must score far lower. A crop that matches half the screen is not an
    anchor.

  SCALE — every positive is resampled to the capture sizes yt-dlp's format
    ladder actually lands on (1920x1080 / 1280x720 / 854x480 / 640x360) and
    re-scored through capture.scale_layout_to_frame, because a template that
    only works at the resolution it was cut at is the classic silent failure.

WHAT THIS DELIBERATELY DOES NOT CLAIM
No replay / scoreboard / desk FRAME from this VOD is committed (only the
three hand-cut banner crops in layouts/), and the network is unavailable, so
"the anchor is absent on a replay" is NOT asserted for this source — a
replay here renders a complete HUD and the anchor is expected to be present
on it. Replays are rejected by the three real reject markers, which
gameplay_state checks BEFORE the anchor; the ordering itself is asserted
below with a real frame.
"""
from __future__ import annotations
import glob
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture  # noqa: E402
import gameplay_state as gs  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

LAYOUT_PATH = os.path.join(REPO, "layouts", "owcs_jksix_qwc.json")
FRAME_GLOB = os.path.join(REPO, "reports", "ingest", "qad-twis-nepal",
                          "frames", "*.jpg")
SHEET = os.path.join(REPO, "reports", "calibration", "owcs-jksix-qwc",
                     "sheet.png")
NEGATIVE_GLOBS = (
    os.path.join(REPO, "reports", "capture_trial", "frames", "*.png"),
    os.path.join(REPO, "pipeline", "fixtures", "video", "demo_match",
                 "frames", "*.png"),
)
RESOLUTIONS = ((1920, 1080), (1280, 720), (854, 480), (640, 360))

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    """`detail` is the FAILURE explanation and is only printed when it is
    needed — a passing run stays readable."""
    status = "PASS" if ok else "FAIL"
    print(f"  {status}  {name}" + (f" — {detail}" if detail and not ok
                                   else ""))
    if not ok:
        FAILURES.append(name)


# --------------------------------------------------------------- fixtures
def load_layout() -> dict:
    with open(LAYOUT_PATH, encoding="utf-8") as f:
        return json.load(f)


def positives() -> list[tuple[str, "np.ndarray"]]:
    """Real gameplay frames, grayscale, at their own committed resolution."""
    out = []
    for p in sorted(glob.glob(FRAME_GLOB)):
        img = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
        if img is not None:
            out.append((os.path.relpath(p, REPO), img))
    sheet = cv2.imread(SHEET, cv2.IMREAD_GRAYSCALE)
    if sheet is not None:
        # calibrate_source.py's contact sheet: a 2x2 grid of full frames.
        h, w = sheet.shape[:2]
        th, tw = h // 2, w // 2
        for r in (0, 1):
            for c in (0, 1):
                out.append((f"{os.path.relpath(SHEET, REPO)}[{r},{c}]",
                            sheet[r * th:(r + 1) * th, c * tw:(c + 1) * tw]))
    return out


def negatives() -> list[tuple[str, "np.ndarray"]]:
    out = []
    for pat in NEGATIVE_GLOBS:
        for p in sorted(glob.glob(pat)):
            img = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
            if img is not None:
                out.append((os.path.relpath(p, REPO), img))
    return out


def score_of(frame_gray, layout: dict) -> float:
    """Exactly what gameplay_state/capture do: scale the layout to this
    frame, load the anchor, matchTemplate inside its rect."""
    fh, fw = frame_gray.shape[:2]
    scaled, _info = capture.scale_layout_to_frame(layout, fw, fh)
    tpl = capture._load_template(scaled, "anchor")
    return capture.region_score(frame_gray, tpl)


# ------------------------------------------------------------------ tests
def test_the_layout_ships_a_real_anchor(layout: dict) -> None:
    print("anchor asset is real and correctly shaped:")
    cfg = layout.get("anchor")
    check("owcs_jksix_qwc declares an anchor", isinstance(cfg, dict))
    if not isinstance(cfg, dict):
        return
    path = os.path.join(REPO, cfg["template"])
    check("anchor template file exists (not a placeholder)",
          os.path.exists(path), cfg["template"])
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    check("anchor template is readable", img is not None)
    if img is None:
        return
    x, y, w, h = cfg["rect"]
    check("template is stored at the rect's native size",
          (img.shape[1], img.shape[0]) == (w, h),
          f"template {img.shape[1]}x{img.shape[0]} vs rect {w}x{h} — a "
          f"mismatch silently misaligns after region_score resizes it")
    check("anchor rect fits inside the layout's native frame",
          0 <= x and 0 <= y
          and x + w <= layout["frame_width"]
          and y + h <= layout["frame_height"])
    check("template carries real image detail, not a flat fill",
          float(img.std()) > 20.0, f"std {img.std():.1f}")


def test_matches_every_real_gameplay_frame(layout: dict, pos) -> float:
    print("\nmatches REAL gameplay frames:")
    thr = layout["anchor"]["min_score"]
    worst = 1.0
    for name, img in pos:
        s = score_of(img, layout)
        worst = min(worst, s)
        check(f"{s:+.3f}  {name}", s >= thr,
              f"score {s:.3f} < min_score {thr:.2f}")
    check("every positive clears min_score with room to spare",
          worst >= thr + 0.15,
          f"worst positive {worst:.3f}, min_score {thr:.2f}")
    return worst


def test_does_not_match_non_gameplay(layout: dict, neg) -> float:
    print("\nrejects frames whose HUD is absent or belongs to another "
          "broadcast:")
    thr = layout["anchor"]["min_score"]
    best = -1.0
    for name, img in neg:
        s = score_of(img, layout)
        best = max(best, s)
        check(f"{s:+.3f}  {name}", s < thr,
              f"score {s:.3f} >= min_score {thr:.2f}")
    check("no negative comes close to min_score", best < thr - 0.3,
          f"best negative {best:.3f}, min_score {thr:.2f}")
    return best


def test_specificity(layout: dict, pos) -> float:
    print("\nspecificity — best match ELSEWHERE in the same real frames:")
    cfg = layout["anchor"]
    tpl_native = cv2.imread(os.path.join(REPO, cfg["template"]),
                            cv2.IMREAD_GRAYSCALE)
    thr = cfg["min_score"]
    worst = -1.0
    for name, img in pos:
        fh, fw = img.shape[:2]
        scaled, _ = capture.scale_layout_to_frame(layout, fw, fh)
        rx, ry, rw, rh = scaled["anchor"]["rect"]
        tpl = tpl_native
        if (rh, rw) != tpl.shape[:2]:
            tpl = cv2.resize(tpl, (rw, rh))
        res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
        peak = np.unravel_index(int(res.argmax()), res.shape)
        masked = res.copy()
        masked[max(0, ry - 12):ry + 13, max(0, rx - 12):rx + 13] = -2.0
        elsewhere = float(masked.max())
        worst = max(worst, elsewhere)
        check(f"{elsewhere:+.3f}  {name}: peak is at the anchor's own rect "
              f"and nothing else impersonates it",
              abs(int(peak[1]) - rx) <= 2 and abs(int(peak[0]) - ry) <= 2
              and elsewhere < thr,
              f"peak at ({int(peak[1])},{int(peak[0])}) vs ({rx},{ry}); "
              f"best decoy {elsewhere:.3f} vs min_score {thr:.2f}")
    return worst


def test_survives_every_capture_resolution(layout: dict, pos) -> None:
    print("\nsurvives the capture resolutions the download ladder lands on:")
    thr = layout["anchor"]["min_score"]
    for (w, h) in RESOLUTIONS:
        scores = [score_of(cv2.resize(img, (w, h)), layout)
                  for _n, img in pos]
        lo = min(scores)
        check(f"min {lo:+.3f}  median {float(np.median(scores)):+.3f}  "
              f"{w}x{h}: every positive still matches", lo >= thr,
              f"worst {lo:.3f} < min_score {thr:.2f}")


def test_classify_frame_uses_the_anchor(layout: dict) -> None:
    print("\ngameplay_state.classify_frame consumes it:")
    # A frame the production run itself recorded as 'gameplay'
    # (reports/ingest/qad-twis-nepal/observations.jsonl, t=2175.0).
    path = os.path.join(REPO, "reports", "ingest", "qad-twis-nepal",
                        "frames", "frame_2175.jpg")
    bgr = cv2.imread(path, cv2.IMREAD_COLOR)
    if bgr is None:
        check("real gameplay frame is readable", False, path)
        return
    fh, fw = bgr.shape[:2]
    scaled, _ = capture.scale_layout_to_frame(layout, fw, fh)
    probe = scaled.get("hud_probe") or {}
    min_chips = probe.get("min_chips_per_side", gs.DEFAULT_MIN_CHIPS)

    state, reason = gs.classify_frame(bgr.copy(), dict(scaled),
                                      min_chips=min_chips)
    check("a real gameplay frame is still classified 'gameplay'",
          state == "gameplay", f"got {state!r} ({reason})")
    check("the reason records the measured anchor score",
          "anchor:" in reason, reason)

    # Blank out ONLY the anchor rect with real, in-frame pixels taken from a
    # HUD-free part of the same frame: the broadcast overlay is gone, the
    # chips are untouched. The structural probe alone would still say
    # 'gameplay'; the anchor gate is what catches it.
    rx, ry, rw, rh = scaled["anchor"]["rect"]
    doctored = bgr.copy()
    src_y = fh - rh - 1
    doctored[ry:ry + rh, rx:rx + rw] = bgr[src_y:src_y + rh, rx:rx + rw]
    probe_only = gs.probe_hud(doctored, dict(scaled))
    still_structural = (probe_only["chips"].get("a", 0) >= min_chips
                        and probe_only["chips"].get("b", 0) >= min_chips)
    check("covering the anchor leaves the chip probe satisfied "
          "(so this really tests the anchor)", still_structural,
          f"chips {probe_only['chips']}")
    state2, reason2 = gs.classify_frame(doctored, dict(scaled),
                                        min_chips=min_chips)
    check("with the anchor covered the frame is no longer 'gameplay'",
          state2 == "no-hud", f"got {state2!r} ({reason2})")
    check("and the reason names the anchor", "anchor" in reason2, reason2)


def test_layouts_without_an_anchor_are_unaffected() -> None:
    print("\nlayouts with no anchor (or an uncut one) behave exactly as "
          "before:")
    rng = np.random.default_rng(7)
    frame = rng.integers(0, 60, (480, 854, 3), dtype=np.uint8)
    no_anchor = {"frame_width": 854, "frame_height": 480,
                 "hud_probe": {"chips_a": [], "chips_b": []},
                 "slots_a": [], "slots_b": []}
    s, m = gs.anchor_score(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
                           no_anchor)
    check("no anchor block -> no opinion", s is None and m is None)

    placeholder = dict(no_anchor, anchor={
        "rect": [10, 10, 40, 20], "min_score": 0.7,
        "template": "layouts/this-was-never-cut.png"})
    s2, m2 = gs.anchor_score(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
                             placeholder)
    check("uncut placeholder template -> no opinion, no crash",
          s2 is None and m2 is None)
    state, _reason = gs.classify_frame(frame, dict(placeholder))
    check("and classification still runs", state in
          ("gameplay", "partial-hud", "no-hud", "replay"))


def main() -> int:
    layout = load_layout()
    pos = positives()
    neg = negatives()
    print(f"anchor: {layout.get('anchor', {}).get('template')}")
    print(f"{len(pos)} real gameplay frames, {len(neg)} negative frames\n")
    if len(pos) < 7:
        print("FAIL  the committed real gameplay frames are missing")
        return 1
    if not neg:
        print("FAIL  no negative frames available")
        return 1

    test_the_layout_ships_a_real_anchor(layout)
    worst_pos = test_matches_every_real_gameplay_frame(layout, pos)
    best_neg = test_does_not_match_non_gameplay(layout, neg)
    worst_spec = test_specificity(layout, pos)
    test_survives_every_capture_resolution(layout, pos)
    test_classify_frame_uses_the_anchor(layout)
    test_layouts_without_an_anchor_are_unaffected()

    thr = layout["anchor"]["min_score"]
    print(f"\nmeasured separation at native resolution:"
          f"\n  worst real-gameplay score  {worst_pos:+.3f}"
          f"\n  best decoy in a real frame {worst_spec:+.3f}"
          f"\n  best non-gameplay score    {best_neg:+.3f}"
          f"\n  min_score                  {thr:+.3f}")

    print(f"\n{'FAILED: ' + ', '.join(FAILURES) if FAILURES else 'ALL PASS'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
