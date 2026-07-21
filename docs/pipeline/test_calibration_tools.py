#!/usr/bin/env python3
"""
test_calibration_tools.py — calibration tooling, fully offline.

Exercises the real code paths with committed fixture frames and a tiny mp4
synthesized on the fly, so no network or yt-dlp is needed:

  extract_calibration_frames  youtube (fake fetcher) + local (real ffmpeg)
  build_layout_debug          draws all regions onto frames
  build_hero_templates        crops 10 slots/frame + writes the HTML sheet
  layouts/owcs_youtube_2026.json  starter layout is valid + has placeholders

Run:  python3 pipeline/test_calibration_tools.py   (non-zero on failure)
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

os.environ["OWCS_DB"] = os.path.join(ROOT, "work", "test_calib", "test.sqlite")
import capture  # noqa: E402
import extract_calibration_frames as ecf  # noqa: E402
import build_layout_debug as bld  # noqa: E402
import build_hero_templates as bht  # noqa: E402

FIX_FRAMES = os.path.join(HERE, "fixtures", "video", "demo_match", "frames")
DEMO_LAYOUT = os.path.join(HERE, "fixtures", "video", "demo-layout.json")
STARTER = os.path.join(ROOT, "layouts", "owcs_youtube_2026.json")
META = os.path.join(HERE, "fixtures", "video", "vod_meta_sample.json")
TW = os.path.join(ROOT, "work", "test_calib")


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        sys.exit(1)


def make_local_mp4(path: str) -> None:
    """Build a 3s test mp4 from the first three gameplay fixture frames."""
    seqdir = os.path.join(TW, "seq")
    os.makedirs(seqdir, exist_ok=True)
    srcs = ["000000.png", "000300.png", "000600.png"]
    for i, fn in enumerate(srcs, start=1):
        shutil.copy(os.path.join(FIX_FRAMES, fn),
                    os.path.join(seqdir, f"f{i:04d}.png"))
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-framerate", "1", "-i", os.path.join(seqdir, "f%04d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "1", path],
                   check=True)


def main():
    shutil.rmtree(TW, ignore_errors=True)
    os.makedirs(TW, exist_ok=True)
    layout = capture.load_layout(DEMO_LAYOUT)

    # ---- extract_calibration_frames: youtube, per-timestamp fallback -----
    print("extract_calibration_frames (youtube, per-timestamp, fake fetcher)")
    calls = []

    def fake_frame(url, offset, out_path, height, pad):
        calls.append((offset, os.path.splitext(out_path)[1]))
        cv2.imwrite(out_path, cv2.imread(os.path.join(FIX_FRAMES, "000000.png")))
        return True

    meta = ecf.vi.probe_vod("x", dump_fn=lambda _u: ecf.vi.load_probe_file(META))
    yt_out = os.path.join(TW, "yt")
    rep = ecf.run(url="https://youtu.be/x", start="0:10:00", end="0:12:00",
                  every=30, out=yt_out, fmt="jpg", probe_override=meta,
                  clip_mode="per-timestamp", frame_fn=fake_frame)
    check("youtube window 10:00–12:00 @30s -> 4 frames",
          rep["count"] == 4 and [c[0] for c in calls] == [600, 630, 660, 690])
    check("frames written as .jpg to chosen out dir",
          all(f.endswith(".jpg") for f in os.listdir(yt_out))
          and len(os.listdir(yt_out)) == 4)

    # ---- extract_calibration_frames: youtube, DEFAULT local-window -------
    print("extract_calibration_frames (youtube, DEFAULT local-window)")
    dl_calls = []
    local_calls = []

    def fake_download(url, start, end, out_path, height):
        dl_calls.append((start, end))
        with open(out_path, "wb") as f:
            f.write(b"FAKECLIP")

    def fake_local_frame(clip_path, offset, clip_start, out_path):
        local_calls.append(offset)
        cv2.imwrite(out_path, cv2.imread(os.path.join(FIX_FRAMES, "000000.png")))
        return True

    yt_out_lw = os.path.join(TW, "yt_lw")
    rep_lw = ecf.run(url="https://youtu.be/x", start="0:10:00", end="0:12:00",
                     every=30, out=yt_out_lw, fmt="jpg", probe_override=meta,
                     download_fn=fake_download, local_frame_fn=fake_local_frame)
    check("default clip_mode (no kwarg) is local-window -> 4 frames",
          rep_lw["count"] == 4 and local_calls == [600, 630, 660, 690])
    check("exactly one clip download for the whole window",
          len(dl_calls) == 1)
    check("frames written as .jpg to chosen out dir (local-window)",
          all(f.endswith(".jpg") for f in os.listdir(yt_out_lw))
          and len(os.listdir(yt_out_lw)) == 4)

    # ---- extract_calibration_frames: local file (real ffmpeg) ------------
    print("extract_calibration_frames (local mp4, real ffmpeg)")
    mp4 = os.path.join(TW, "clip.mp4")
    make_local_mp4(mp4)
    loc_out = os.path.join(TW, "local")
    rloc = ecf.run(local=mp4, start=0, end=2, every=1, out=loc_out, fmt="png")
    names = sorted(os.listdir(loc_out))
    check(f"local extract produced offset-named PNGs ({names})",
          rloc["count"] >= 2 and all(n.endswith(".png") for n in names)
          and "000000.png" in names)

    # ---- build_layout_debug on fixture frames ----------------------------
    print("build_layout_debug")
    dbg_out = os.path.join(TW, "layout_debug")
    made = bld.process_dir(FIX_FRAMES, layout, dbg_out)
    check("annotated a frame per input", len(made) == len(
        [f for f in os.listdir(FIX_FRAMES) if f.endswith(".png")]))
    img = cv2.imread(made[0])
    ref = cv2.imread(os.path.join(FIX_FRAMES,
                                  os.path.basename(made[0]).replace("_debug", "")))
    check("debug image readable + same size as source",
          img is not None and img.shape == ref.shape)
    check("works with no score_map in the demo layout",
          "score_map" not in layout)

    # ---- build_hero_templates + HTML -------------------------------------
    print("build_hero_templates")
    cand_out = os.path.join(TW, "candidates")
    html = os.path.join(TW, "template_candidates.html")
    two = os.path.join(TW, "two")
    os.makedirs(two)
    for fn in ("000000.png", "000600.png"):
        shutil.copy(os.path.join(FIX_FRAMES, fn), os.path.join(two, fn))
    recs = bht.crop_candidates(two, layout, cand_out)
    check("10 crops per frame (2 frames -> 20)", len(recs) == 20)
    crop = cv2.imread(recs[0]["path"])
    x, y, w, h = layout["slots_a"][0]
    check("crop size matches slot rect", crop.shape[0] == h and crop.shape[1] == w)
    bht.write_html(recs, html)
    body = open(html, encoding="utf-8").read()
    check("HTML sheet written + references crops + labels A/B slots",
          os.path.exists(html) and "candidates/" in body
          and "A1" in body and "B5" in body and "0:10:00" in body)

    # ---- starter layout sanity ------------------------------------------
    print("starter layout owcs_youtube_2026.json")
    sl = json.load(open(STARTER, encoding="utf-8"))
    check("has 5 A slots + 5 B slots",
          len(sl["slots_a"]) == 5 and len(sl["slots_b"]) == 5)
    check("has anchor, replay, score_map regions",
          all(k in sl for k in ("anchor", "replay", "score_map")))
    check("ships adjustment guidance (comments)",
          "_comments" in sl and any("PLACEHOLDER" in c for c in sl["_comments"]))
    # debug tool must accept the starter layout too (draws score_map)
    sl_layout = capture.load_layout(STARTER)
    ann = bld.draw_layout(cv2.imread(os.path.join(FIX_FRAMES, "000000.png")),
                          sl_layout)
    check("starter layout draws without error (incl. score_map)",
          ann is not None)

    print("\nALL CALIBRATION TOOL TESTS PASSED")


if __name__ == "__main__":
    main()
