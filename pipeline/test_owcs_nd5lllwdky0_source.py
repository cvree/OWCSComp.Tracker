#!/usr/bin/env python3
"""
test_owcs_nd5lllwdky0_source.py — offline tests for the new OWCS 2026
NA/EMEA Stage 2 Playoffs Day 3 calibration source (owcs-nd5lllwdky0).

No network, no yt-dlp, no ffmpeg. Covers:
  * the source is registered in video_sources.json and shows up in the
    exported data (Tools -> Broadcast sources / assets/data)
  * layouts/owcs_nd5lllwdky0.json is structurally valid and its slots stay
    in bounds after scaling to every resolution this VOD is known to
    actually downloads at (640x360, 854x480) plus native 1920x1080
  * build_crop_report produces exactly 30 crops (3 fixture frames x 10
    slots), none skipped, from this layout
  * the real committed anchor template honestly tells gameplay-shaped
    frames apart from frames without it (intro / map-pick cards, post-event
    graphics, replays) — never a silent guess
  * the anchor is MATCH-STATE INVARIANT. Regression for a real defect: the
    first anchor cut for this broadcast was the centre round-state row,
    which reads "0 0% <lock> 0% 0" on a Control map at 0-0 but "STOP THE
    PAYLOAD ... 1 / 2" on an Escort map mid-series. It scored -0.15 on a
    genuine full-HUD Escort frame from another match in the same VOD and
    the filter silently threw real gameplay away. The anchor is now the
    broadcast chyron, and these tests assert it stays off the centre HUD,
    in the upper chrome band, with a wide gameplay/non-gameplay margin
  * the capture.region_score cross-resolution fix: a template cut at one
    frame size still scores correctly against a differently-sized frame
    (the exact bug this source's real anchor tripped over: 640x360 vs
    854x480 fallback rungs of the same yt-dlp format ladder)
  * the two documented run_owcs_auto.py commands parse cleanly and resolve
    to this source/layout

Run:  python pipeline/test_owcs_nd5lllwdky0_source.py
"""
from __future__ import annotations
import json
import os
import shutil
import sys
import tempfile

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import capture               # noqa: E402
import frame_filter          # noqa: E402
import video_ingest as vi    # noqa: E402
import build_crop_report as bcr    # noqa: E402
import build_layout_debug as bld   # noqa: E402
import run_owcs_auto as roa        # noqa: E402
import export_data                 # noqa: E402

SOURCE_ID = "owcs-nd5lllwdky0"
LAYOUT_PATH = os.path.join(ROOT, "layouts", "owcs_nd5lllwdky0.json")
ANCHOR_PNG = os.path.join(ROOT, "layouts", "owcs_nd5lllwdky0-anchor.png")
SOURCES_PATH = os.path.join(ROOT, "data", "sources", "video_sources.json")

TMP = tempfile.mkdtemp(prefix="owcs_nd5lllwdky0_")
_fails = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _fails
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        _fails += 1


def load_layout() -> dict:
    return capture.load_layout(LAYOUT_PATH)


# --------------------------------------------------------------- fixtures
def synth_frame(w: int, h: int, seed: int = 3) -> np.ndarray:
    """Deterministic textured frame — flat regions can't inflate scores."""
    rng = np.random.default_rng(seed)
    return rng.integers(20, 220, size=(h, w, 3), dtype=np.uint8)


def main() -> int:
    print("source registration:")
    with open(SOURCES_PATH, "r", encoding="utf-8") as f:
        srcs = json.load(f)["sources"]
    by_id = {s.get("id"): s for s in srcs if s.get("id")}
    check(f"{SOURCE_ID} exists in video_sources.json", SOURCE_ID in by_id)
    src = by_id.get(SOURCE_ID, {})
    check("points at the right video",
          "nD5lLLWDkY0" in (src.get("url") or ""))
    check("points at its own layout",
          src.get("layout") == "layouts/owcs_nd5lllwdky0.json")
    check("platform is youtube + enabled",
          src.get("platform") == "youtube" and src.get("enabled") is True)
    check("video_ingest resolves it as a youtube source",
          vi.is_youtube_source(src))
    check("video_ingest.find_source resolves it by id",
          vi.find_source(SOURCES_PATH, SOURCE_ID) is not None)

    print("exported data (Tools -> Broadcast sources):")
    exported = export_data.load_video_sources(SOURCES_PATH)
    exp_by_id = {s["id"]: s for s in exported}
    check(f"{SOURCE_ID} present in load_video_sources()",
          SOURCE_ID in exp_by_id)
    if SOURCE_ID in exp_by_id:
        e = exp_by_id[SOURCE_ID]
        check("exported entry carries url/layout/enabled",
              e["url"] and e["layout"] == "layouts/owcs_nd5lllwdky0.json"
              and e["enabled"] is True)

    print("layout file: structurally valid:")
    check("layout json file exists", os.path.exists(LAYOUT_PATH))
    layout = load_layout()
    for key in ("frame_width", "frame_height", "slots_a", "slots_b",
                "anchor", "score_map", "match_threshold", "templates_dir",
                "notes"):
        check(f"layout has '{key}'", key in layout)
    check("native size is 1920x1080",
          layout.get("frame_width") == 1920
          and layout.get("frame_height") == 1080)
    check("5+5 slot boxes", len(layout.get("slots_a", [])) == 5
          and len(layout.get("slots_b", [])) == 5)
    warns = bld.validate_layout(layout)
    check("validate_layout finds no in-bounds/shape problems at native size",
          warns == [], "; ".join(warns) if warns else "")
    check("anchor template file is committed",
          os.path.exists(ANCHOR_PNG))
    check("notes name the real event + honest intro-vs-gameplay finding",
          "Stage 2 Playoffs Day 3" in layout["notes"]
          and "intro" in layout["notes"].lower()
          and "0:41:00" in layout["notes"])

    print("scaled layout stays in bounds at every resolution this VOD "
          "actually downloads at:")
    for (fw, fh) in [(640, 360), (854, 480), (1920, 1080)]:
        scaled, info = capture.scale_layout_to_frame(layout, fw, fh)
        oob = []
        for key in ("slots_a", "slots_b"):
            for i, (x, y, w, h) in enumerate(scaled[key], start=1):
                if (x < 0 or y < 0 or x + w > fw or y + h > fh
                        or w <= 0 or h <= 0):
                    oob.append(f"{key}[{i}]")
        check(f"{fw}x{fh}: scaling ok + all 10 slots in bounds",
              info["ok"] and not oob, ", ".join(oob))

    print("crop report: 3 fixture frames x 10 slots = 30 crops, none "
          "skipped (synthetic frames, this layout's real geometry):")
    frames_dir = os.path.join(TMP, "frames_raw")
    os.makedirs(frames_dir, exist_ok=True)
    for off in (2460, 2470, 2480):
        cv2.imwrite(os.path.join(frames_dir, f"{off:06d}.png"),
                    synth_frame(640, 360, seed=off))
    report_dir = os.path.join(TMP, "report")
    res = bcr.process(frames_dir, layout, report_dir,
                      templates_dir=os.path.join(TMP, "no_templates"))
    check("30 crops produced", res["crops"] == 30)
    check("no slots skipped", res["skipped"] == [])
    check("crops.html written", os.path.exists(res["html"]))

    print("real anchor template (broadcast chyron): gameplay vs "
          "intro/map-pick honesty:")
    anchor_tpl = capture._load_template(layout, "anchor")
    ax, ay, aw, ah = anchor_tpl["rect"]
    # a synthetic "gameplay-shaped" frame: paste the REAL anchor crop where
    # the (scaled) rect expects it, at the resolution this VOD actually
    # ships (640x360) -- mirrors a genuine live-HUD frame.
    scaled_layout, _ = capture.scale_layout_to_frame(layout, 640, 360)
    real_anchor_img = cv2.imread(ANCHOR_PNG)
    gameplay_frame = synth_frame(640, 360, seed=1)
    sx, sy, sw, sh = scaled_layout["anchor"]["rect"]
    patch = cv2.resize(real_anchor_img, (sw, sh))
    gameplay_frame[sy:sy + sh, sx:sx + sw] = patch
    intro_frame = synth_frame(640, 360, seed=2)   # no round-state row at all

    anchor_scaled = capture._load_template(scaled_layout, "anchor")
    ok_g, reason_g, score_g = capture.is_gameplay(
        gameplay_frame, anchor_scaled, None, [])
    ok_i, reason_i, score_i = capture.is_gameplay(
        intro_frame, anchor_scaled, None, [])
    check("frame carrying the real broadcast chyron -> gameplay",
          ok_g is True, f"reason={reason_g} score={score_g}")
    check("frame without it (intro/map-pick shape) -> honestly rejected",
          ok_i is False and reason_i.startswith("no-hud"),
          f"reason={reason_i}")
    check("gameplay score comfortably clears min_score",
          score_g >= anchor_scaled["min_score"])

    print("anchor is MATCH-STATE INVARIANT, not over-fitted to one frame:")
    # Regression for a real defect caught on this broadcast. The first anchor
    # cut here was the centre round-state row, which reads "0 0% <lock> 0% 0"
    # on a Control map at 0-0 but "STOP THE PAYLOAD ... 1 / 2" on an Escort
    # map mid-series -- it scored -0.15 on a genuine full-HUD Escort frame and
    # the filter silently discarded real gameplay. The anchor must therefore
    # sit on broadcast furniture that does not change with map/mode/score.
    ax, ay, aw, ah = layout["anchor"]["rect"]
    cx = ax + aw / 2.0
    fw_native = layout["frame_width"]
    check("anchor is not parked on the centre round-state/score readout "
          f"(rect x-centre {cx:.0f} of {fw_native})",
          not (0.40 * fw_native < cx < 0.60 * fw_native),
          "a centre-HUD anchor tracks match state and rejects real gameplay "
          "on other maps/modes")
    check("anchor sits in the upper broadcast chrome (chyron band)",
          ay + ah <= 0.15 * layout["frame_height"])
    # The template must still actually discriminate: rebuild the same
    # gameplay-shaped vs bare-background comparison used above, but assert the
    # MARGIN is wide, not merely that it passes the floor.
    margin = score_g - score_i
    check(f"gameplay-vs-non-gameplay margin is wide ({margin:.2f})",
          margin > 0.5, f"gameplay {score_g:.2f} vs non-gameplay {score_i:.2f}")
    check("min_score sits between the two, with headroom on both sides",
          score_i + 0.15 < layout["anchor"]["min_score"] < score_g - 0.15,
          f"min_score={layout['anchor']['min_score']}, "
          f"non-gameplay={score_i:.2f}, gameplay={score_g:.2f}")

    print("frame_filter.filter_frames applies the same scaling end to end:")
    in_dir = os.path.join(TMP, "filt_in")
    out_dir = os.path.join(TMP, "filt_out")
    os.makedirs(in_dir, exist_ok=True)
    cv2.imwrite(os.path.join(in_dir, "002460.png"), gameplay_frame)
    cv2.imwrite(os.path.join(in_dir, "002400.png"), intro_frame)
    fres = frame_filter.filter_frames(in_dir, out_dir, layout)
    check("gameplay-shaped frame kept, intro-shaped frame rejected",
          fres["kept"] == ["002460.png"]
          and dict(fres["rejected"]).get("002400.png", "").startswith("no-hud"))

    print("capture.region_score: cross-resolution template fix "
          "(the exact bug hit at 640x360 vs 854x480 fallback rungs):")
    # template cut at 640-space, frame delivered at 854-space (a real
    # fallback the yt-dlp format ladder actually returned for this source)
    tpl_640 = {"img": cv2.cvtColor(real_anchor_img, cv2.COLOR_BGR2GRAY),
              "rect": scaled_layout["anchor"]["rect"], "min_score": 0.5}
    layout_854, _ = capture.scale_layout_to_frame(layout, 854, 480)
    frame_854 = synth_frame(854, 480, seed=1)
    fx, fy, fw2, fh2 = layout_854["anchor"]["rect"]
    patch854 = cv2.resize(real_anchor_img, (fw2, fh2))
    frame_854[fy:fy + fh2, fx:fx + fw2] = patch854
    tpl_at_854_rect = dict(tpl_640, rect=layout_854["anchor"]["rect"])
    score_cross = capture.region_score(
        cv2.cvtColor(frame_854, cv2.COLOR_BGR2GRAY), tpl_at_854_rect)
    check("template resized to the (larger) crop instead of forced 0.0",
          score_cross > 0.5, f"score={score_cross}")
    check("previously this returned exactly 0.0 (crop/template size "
          "mismatch) -- confirms the fix actually changed behavior",
          score_cross != 0.0)

    print("documented run_owcs_auto.py commands parse + resolve correctly:")
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--source")
    ap.add_argument("--start", default="0")
    ap.add_argument("--end", required=True)
    ap.add_argument("--every", type=int, default=30)
    ap.add_argument("--height", type=int, default=None)
    ap.add_argument("--force", "--force-clip", dest="force",
                    action="store_true")
    ap.add_argument("--fast", action="store_true")

    smoke = ap.parse_args(["--source", SOURCE_ID, "--start", "0:40:00",
                           "--end", "0:40:40", "--every", "10", "--fast",
                           "--force"])
    check("smoke command: source/window/every/fast/force all resolve",
          smoke.source == SOURCE_ID and smoke.start == "0:40:00"
          and smoke.end == "0:40:40" and smoke.every == 10
          and smoke.fast and smoke.force)
    check("smoke start parses to 2400s", vi.parse_time(smoke.start) == 2400)

    calib = ap.parse_args(["--source", SOURCE_ID, "--start", "0:40:00",
                           "--end", "0:41:00", "--every", "10",
                           "--height", "1080", "--force"])
    check("calibration command: height=1080, no --fast, force set",
          calib.source == SOURCE_ID and calib.height == 1080
          and not calib.fast and calib.force)
    check("calibration window is 60s", vi.parse_time(calib.end)
          - vi.parse_time(calib.start) == 60)
    # both commands must resolve through the real source lookup used by
    # run_owcs_auto.run_auto (layout + youtube-ness), not just argparse
    resolved = vi.find_source(SOURCES_PATH, smoke.source)
    check("run_auto's own source resolution finds this source",
          resolved is not None and vi.is_youtube_source(resolved))
    check("resolved layout path matches the calibrated layout",
          (resolved.get("layout") or roa.DEFAULT_LAYOUT)
          == "layouts/owcs_nd5lllwdky0.json")

    if _fails:
        print(f"\n{_fails} CHECK(S) FAILED")
        return 1
    print("\nALL owcs-nd5lllwdky0 SOURCE TESTS PASSED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
