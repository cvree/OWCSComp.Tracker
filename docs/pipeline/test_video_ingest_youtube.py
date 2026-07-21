#!/usr/bin/env python3
"""
test_video_ingest_youtube.py — YouTube VOD ingestion, fully offline.

Exercises the timestamp-sampling logic with a fixture metadata blob and fake
frame extractors/downloaders, so no yt-dlp, ffmpeg, or network is needed.
Covers time parsing, the pure frame planner (window / clamp / cap), dry-run
(writes nothing), and both clip modes:
  local-window   (default) one fake clip "download" + fake local per-offset
                 extraction, verifying the window bounds and per-offset local
                 seek math.
  per-timestamp  the older one-fake-fetch-per-offset path, kept as an
                 explicit fallback.

Run:  python3 pipeline/test_video_ingest_youtube.py   (non-zero on failure)
"""
from __future__ import annotations
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

os.environ["OWCS_DB"] = os.path.join(ROOT, "work", "test_vod", "test.sqlite")
import video_ingest as vi  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "video")
META = os.path.join(FIX, "vod_meta_sample.json")
SOURCES = os.path.join(ROOT, "data", "sources", "video_sources.json")
TEST_WORK = os.path.join(ROOT, "work", "test_vod")

YT_SRC = {
    "id": "owcs-test", "title": "t", "platform": "youtube",
    "url": "https://www.youtube.com/live/AfCXDIMPsLE?si=x",
    "enabled": True, "sampleIntervalSeconds": 300,
    "layout": "layouts/owcs-demo.json",
}


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        sys.exit(1)


def main():
    shutil.rmtree(TEST_WORK, ignore_errors=True)
    os.makedirs(TEST_WORK, exist_ok=True)
    # Later sections extract fake frames into work/vods/owcs-test/frames_raw.
    # Remove any leftovers from a previous run so the dry-run "writes nothing"
    # assertion below starts from a clean slate (test isolation).
    shutil.rmtree(os.path.join(vi.VODS_DIR, "owcs-test"), ignore_errors=True)

    # ---- time parsing ----------------------------------------------------
    print("parse_time")
    check("seconds int", vi.parse_time(90) == 90)
    check("H:MM:SS", vi.parse_time("1:30:00") == 5400)
    check("MM:SS", vi.parse_time("02:30") == 150)
    check("empty -> 0", vi.parse_time(None) == 0 and vi.parse_time("") == 0)
    check("fmt round-trips", vi.fmt_hms(5400) == "1:30:00")

    # ---- pure planner ----------------------------------------------------
    print("plan_frames")
    dur = 21063  # ~5h51m
    full = vi.plan_frames(dur, start=0, end=None, interval=300)
    # range(0, 21063, 300) -> 0..21000 -> 71 offsets
    check(f"full-VOD 300s plan = 71 frames (got {full['count']})",
          full["count"] == 71 and full["offsets"][0] == 0
          and full["offsets"][-1] == 21000)
    win = vi.plan_frames(dur, start="0:10:00", end="0:30:00", interval=300)
    check("window 10–30m @300s = 4 frames [600,900,1200,1500]",
          win["offsets"] == [600, 900, 1200, 1500])
    clamp = vi.plan_frames(dur, start=0, end=999999, interval=600)
    check("end clamped to duration", clamp["end"] == dur)
    empty = vi.plan_frames(dur, start=1000, end=1000, interval=300)
    check("empty window -> 0 frames", empty["count"] == 0)
    cap = vi.plan_frames(dur, start=0, end=None, interval=60, max_frames=50)
    check("max-frames caps and flags", cap["count"] == 50 and cap["capped"])

    # ---- probe from fixture file (no network) ----------------------------
    print("probe_vod (fixture file)")
    meta = vi.probe_vod("ignored", dump_fn=lambda _u: vi.load_probe_file(META))
    check("probe reads title + duration",
          meta["duration"] == 21063 and "fixture" in meta["title"].lower())

    # ---- dry-run writes nothing ------------------------------------------
    print("ingest_vod --dry-run")
    rep = vi.ingest_vod(YT_SRC, dry_run=True, probe_override=meta)
    check("dry-run reports planned count without downloading",
          rep["dry_run"] and rep["plan"]["count"] == 71 and rep["frames"] == 0)
    check("dry-run created no frames_raw dir",
          not os.path.isdir(os.path.join(vi.VODS_DIR, "owcs-test", "frames_raw")))

    # ---- LOCAL-WINDOW is the default clip mode (fake download + fake local
    #      extraction, offline) --------------------------------------------
    print("ingest_vod extraction (local-window, DEFAULT clip mode)")
    dl_calls = []
    local_calls = []

    def fake_download(url, start, end, out_path, height):
        dl_calls.append((url, start, end, out_path, height))
        with open(out_path, "wb") as f:   # placeholder "clip"
            f.write(b"FAKECLIP")

    def fake_local_frame(clip_path, offset, clip_start, out_path):
        local_calls.append((clip_path, offset, clip_start))
        assert os.path.exists(clip_path), "local extractor ran before download"
        with open(out_path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        return True

    rep_lw = vi.ingest_vod(YT_SRC, start="0:10:00", end="0:30:00",
                           interval=300, dry_run=False, probe_override=meta,
                           download_fn=fake_download,
                           local_frame_fn=fake_local_frame)
    check("default clip_mode is local-window (no clip_mode kwarg passed)",
          rep_lw["frames"] == 4)
    check("exactly ONE clip download for the whole window",
          len(dl_calls) == 1)
    check("clip download spans [min offset, max offset + pad] = 600-1502",
          dl_calls[0][1] == 600 and dl_calls[0][2] == 1502)
    check("local extraction ran once per planned offset, all against the "
          "SAME downloaded clip",
          [c[1] for c in local_calls] == [600, 900, 1200, 1500]
          and len({c[0] for c in local_calls}) == 1
          and all(c[2] == 600 for c in local_calls))
    check("frames landed in work/vods/<id>/frames_raw (local-window)",
          rep_lw["raw_dir"].endswith(os.path.join("vods", "owcs-test", "frames_raw"))
          and len(os.listdir(rep_lw["raw_dir"])) == 4)
    check("downloaded clip is cleaned up (disk hygiene)",
          not any(fn.startswith("_window_clip") for fn in os.listdir(rep_lw["raw_dir"])))

    # ---- PER-TIMESTAMP is kept as an explicit fallback --------------------
    print("ingest_vod extraction (per-timestamp, explicit fallback)")
    calls = []

    def fake_frame(url, offset, out_path, height, pad):
        calls.append(offset)
        with open(out_path, "wb") as f:      # 1x1 png-ish placeholder
            f.write(b"\x89PNG\r\n\x1a\n")
        return True

    rep2 = vi.ingest_vod(YT_SRC, start="0:10:00", end="0:30:00",
                         interval=300, dry_run=False, probe_override=meta,
                         clip_mode="per-timestamp", frame_fn=fake_frame)
    check("extracted exactly the 4 planned offsets",
          rep2["frames"] == 4 and calls == [600, 900, 1200, 1500])
    check("frames landed in work/vods/<id>/frames_raw",
          rep2["raw_dir"].endswith(os.path.join("vods", "owcs-test", "frames_raw"))
          and len(os.listdir(rep2["raw_dir"])) == 4)

    # ---- extract_youtube_frames_local_window, direct unit coverage -------
    print("extract_youtube_frames_local_window (direct)")
    lw_out = os.path.join(TEST_WORK, "lw_direct")
    made = vi.extract_youtube_frames_local_window(
        "https://youtu.be/x", lw_out, [600, 900, 1200], height=480,
        clip_pad=3, download_fn=fake_download, frame_fn=fake_local_frame)
    check("returns one frame per offset", len(made) == 3)
    check("window end uses clip_pad (max offset + 3)",
          dl_calls[-1][2] == 1203)
    check("no offsets -> no download, empty result", (
        vi.extract_youtube_frames_local_window(
            "u", os.path.join(TEST_WORK, "lw_empty"), [],
            download_fn=fake_download, frame_fn=fake_local_frame) == []
    ) and len(dl_calls) == 2)  # unchanged from the previous check

    # ---- extract_youtube_frames dispatcher --------------------------------
    print("extract_youtube_frames clip_mode dispatch")
    try:
        vi.extract_youtube_frames("u", os.path.join(TEST_WORK, "bad"), [0],
                                  clip_mode="not-a-real-mode")
        check("unknown clip_mode raises ValueError", False)
    except ValueError as e:
        check("unknown clip_mode raises ValueError", "not-a-real-mode" in str(e))

    # ---- classification + lookup on the real committed sources -----------
    print("source classification + lookup")
    check("youtube source detected by platform + url",
          vi.is_youtube_source(YT_SRC))
    check("demo fixture source is NOT youtube",
          not vi.is_youtube_source({"match": "vdemo01",
                                    "fixtureFrames": "x"}))
    real = vi.find_source(SOURCES, "owcs-afcxdimpsle")
    check("committed VOD source found + is youtube",
          real is not None and vi.is_youtube_source(real)
          and real["url"] == "https://www.youtube.com/watch?v=AfCXDIMPsLE")

    # Leave no generated frames behind (keeps repeated runs and zips clean).
    shutil.rmtree(os.path.join(vi.VODS_DIR, "owcs-test"), ignore_errors=True)
    shutil.rmtree(TEST_WORK, ignore_errors=True)

    print("\nALL YOUTUBE VOD INGEST TESTS PASSED")


if __name__ == "__main__":
    main()
