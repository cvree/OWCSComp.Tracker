#!/usr/bin/env python3
"""
test_capture_trial.py — the capture trial, fully offline.

Verifies both branches without network or yt-dlp:
  - fixture fallback: when real capture yields no frames, the trial rebuilds
    the same flow on the bundled fixtures and labels the report clearly.
  - real success (simulated): with an injected probe + frame fetcher, the
    trial reports REAL mode and extracts the planned window.
Both branches must write index.html with resolvable image references and
must not touch the database.

Run:  python3 pipeline/test_capture_trial.py   (non-zero on failure)
"""
from __future__ import annotations
import os
import re
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

os.environ["OWCS_DB"] = os.path.join(ROOT, "work", "test_trial", "test.sqlite")
import video_ingest as vi  # noqa: E402
import run_capture_trial as rct  # noqa: E402

SOURCES = os.path.join(ROOT, "data", "sources", "video_sources.json")
META = os.path.join(HERE, "fixtures", "video", "vod_meta_sample.json")
FIXFRAME = os.path.join(HERE, "fixtures", "video", "demo_match", "frames",
                        "000000.png")


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        sys.exit(1)


def refs_resolve() -> int:
    h = open(os.path.join(rct.TRIAL_DIR, "index.html"), encoding="utf-8").read()
    imgs = re.findall(r'src="([^"]+)"', h)
    broken = [s for s in imgs if not os.path.exists(
        os.path.join(rct.TRIAL_DIR, s))]
    assert not broken, f"broken refs: {broken}"
    return len(imgs)


def main():
    meta = vi.probe_vod("x", dump_fn=lambda _u: vi.load_probe_file(META))

    # ---- fixture fallback (real capture returns 0 frames), per-timestamp -
    print("fixture fallback (per-timestamp)")

    def failing_fetch(url, offset, out_path, height, pad):
        raise FileNotFoundError("yt-dlp")   # caught inside extractor -> 0 frames

    res = rct.run_trial("owcs-afcxdimpsle", "1:30:00", "1:40:00", 30, 720,
                        SOURCES, clip_mode="per-timestamp",
                        real_probe=meta, real_frame_fn=failing_fetch)
    check("mode is fixture fallback", res["mode"] == "fixture fallback")
    check("reason recorded", bool(res["reason"])
          and ("zero frames" in res["reason"] or "yt-dlp" in res["reason"]))
    check("8 fixture frames copied", len(res["frames"]) == 8)
    check("a debug image per frame", len(res["debug_imgs"]) == 8)
    check("starter-placeholder illustration written",
          res["starter_demo"] and os.path.exists(res["starter_demo"]))
    check("80 hero crops (8 frames x 10 slots)", len(res["crop_recs"]) == 80)
    n = refs_resolve()
    check(f"index.html references resolve ({n} images)", n > 0)
    h = open(os.path.join(rct.TRIAL_DIR, "index.html"), encoding="utf-8").read()
    check("report labels fallback + shows exact command",
          "FIXTURE FALLBACK" in h and "--start 1:30:00 --end 1:40:00" in h)

    # ---- simulated real success, per-timestamp ---------------------------
    print("simulated real capture (per-timestamp)")

    def good_fetch(url, offset, out_path, height, pad):
        cv2.imwrite(out_path, cv2.imread(FIXFRAME))
        return True

    res2 = rct.run_trial("owcs-afcxdimpsle", "1:30:00", "1:31:00", 30, 720,
                         SOURCES, clip_mode="per-timestamp",
                         real_probe=meta, real_frame_fn=good_fetch)
    check("mode is real", res2["mode"] == "real")
    check("extracted the planned 2-frame window", len(res2["frames"]) == 2)
    check("real report has no fallback reason", res2["reason"] is None)
    check("report records the per-timestamp clip mode",
          res2["clip_mode"] == "per-timestamp")
    n2 = refs_resolve()
    check(f"real index.html references resolve ({n2} images)", n2 > 0)
    h2 = open(os.path.join(rct.TRIAL_DIR, "index.html"), encoding="utf-8").read()
    check("report labels REAL capture", "REAL YOUTUBE CAPTURE" in h2)
    check("report shows the clip mode used", "per-timestamp" in h2)

    # ---- simulated real success, DEFAULT local-window mode ---------------
    print("simulated real capture (DEFAULT local-window)")
    dl_calls = []

    def good_download(url, start, end, out_path, height):
        dl_calls.append((start, end))
        with open(out_path, "wb") as f:
            f.write(b"FAKECLIP")

    def good_local_fetch(clip_path, offset, clip_start, out_path):
        cv2.imwrite(out_path, cv2.imread(FIXFRAME))
        return True

    res3 = rct.run_trial("owcs-afcxdimpsle", "1:30:00", "1:31:00", 30, 720,
                         SOURCES, real_probe=meta,
                         real_download_fn=good_download,
                         real_frame_fn=good_local_fetch)
    check("mode is real (local-window, no clip_mode kwarg passed)",
          res3["mode"] == "real")
    check("extracted the planned 2-frame window", len(res3["frames"]) == 2)
    check("default clip_mode recorded is local-window",
          res3["clip_mode"] == "local-window")
    check("exactly one clip download for the whole window", len(dl_calls) == 1)
    n3 = refs_resolve()
    check(f"local-window index.html references resolve ({n3} images)", n3 > 0)
    h3 = open(os.path.join(rct.TRIAL_DIR, "index.html"), encoding="utf-8").read()
    check("report shows local-window clip mode + --clip-mode in exact command",
          "local-window" in h3 and "--clip-mode local-window" in h3)

    # ---- no DB writes ---------------------------------------------------
    print("no DB side effects")
    check("trial created no sqlite db",
          not os.path.exists(os.environ["OWCS_DB"]))

    print("\nALL CAPTURE TRIAL TESTS PASSED")


if __name__ == "__main__":
    main()
