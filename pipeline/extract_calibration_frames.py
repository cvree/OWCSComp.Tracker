#!/usr/bin/env python3
"""
extract_calibration_frames.py — pull a handful of frames to eyeball a layout.

Calibration is a human step: you grab 10–20 frames from a live-gameplay
window, then adjust rectangles until the boxes line up with the broadcast.
This tool just produces those frames (JPG or PNG). It reads NOTHING about
heroes and writes NOTHING to the database — it is pure frame extraction.

Inputs (choose one video):
  --source <id>   a youtube VOD from data/sources/video_sources.json
  --url <url>     a youtube URL directly
  --local <file>  a local .mp4 already on disk

Window / sampling:
  --start, --end  seconds or H:MM:SS (default 0 .. duration/end of file)
  --every         seconds between samples (default 30)
  --max-frames    safety cap (default 30)
  --out           output dir (default reports/calibration_frames/<id>)
  --format        jpg | png (default png)
  --height        max video height for youtube sections (default 720)
  --probe-file    saved `yt-dlp --dump-single-json` blob for offline dry runs
  --clip-mode     local-window (default) | per-timestamp — see video_ingest.py

youtube sampling never downloads a 6-hour VOD whole. By default it uses
LOCAL-WINDOW clip mode (one yt-dlp download covering the whole [start,end]
window, then local ffmpeg seeks per offset — reliable at any offset).
PER-TIMESTAMP clip mode (one remote yt-dlp seek per offset) is kept as an
explicit fallback; it can fail deep into long VODs ("could not seek to
position ..."). yt-dlp + ffmpeg must be on PATH for the youtube/url and
local paths respectively.

Usage:
  python3 pipeline/extract_calibration_frames.py --source owcs-afcxdimpsle \
        --start 1:30:00 --end 1:33:00 --every 20 --format jpg
  python3 pipeline/extract_calibration_frames.py --source owcs-afcxdimpsle \
        --start 1:30:00 --end 1:32:00 --every 30 --clip-mode local-window
  python3 pipeline/extract_calibration_frames.py --local match.mp4 \
        --start 300 --end 480 --every 20 --out reports/calibration_frames/m01
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import video_ingest as vi  # noqa: E402

REPORTS_DIR = os.path.join(db.REPO_ROOT, "reports")


def log(msg: str) -> None:
    print(f"[calib-frames] {msg}", flush=True)


def _default_out(name: str) -> str:
    return os.path.join(REPORTS_DIR, "calibration_frames", name)


# ------------------------------------------------------------------ local
def extract_local(path: str, out_dir: str, start: int, end: int | None,
                  every: int, ext: str) -> list[str]:
    """ffmpeg-sample a local file in [start, end] every `every` seconds.

    Frames are named by absolute stream offset so they sort chronologically
    and read like the rest of the pipeline (000600.png == 10 minutes in).
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"local file not found: {path}")
    os.makedirs(out_dir, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", path,
           "-ss", str(start)]
    if end is not None:
        cmd += ["-to", str(end)]
    cmd += ["-vf", f"fps=1/{every}", "-start_number", "0",
            os.path.join(out_dir, f"idx%06d.{ext}")]
    subprocess.run(cmd, check=True)
    made = []
    for fn in sorted(f for f in os.listdir(out_dir) if f.startswith("idx")):
        idx = int(fn[3:9])
        offset = start + idx * every
        new = os.path.join(out_dir, f"{offset:06d}.{ext}")
        os.replace(os.path.join(out_dir, fn), new)
        made.append(new)
    return made


# ---------------------------------------------------------------- youtube
def extract_youtube(url: str, out_dir: str, offsets: list[int], height: int,
                    ext: str, clip_mode: str = "local-window",
                    frame_fn=vi._download_section_frame,
                    download_fn=vi._download_youtube_clip,
                    local_frame_fn=vi._extract_frame_local) -> list[str]:
    """Dispatch to video_ingest's clip-mode extractors (see there for detail).

    clip_mode "local-window" (default) makes ONE yt-dlp download for the
    whole planned window, then seeks locally per offset with ffmpeg —
    reliable at any offset. "per-timestamp" is the older per-offset remote
    seek, kept as an explicit fallback.
    """
    os.makedirs(out_dir, exist_ok=True)
    if clip_mode == "local-window":
        return vi.extract_youtube_frames_local_window(
            url, out_dir, offsets, height=height, ext=ext,
            download_fn=download_fn, frame_fn=local_frame_fn)
    if clip_mode == "per-timestamp":
        made = []
        for off in offsets:
            out_path = os.path.join(out_dir, f"{off:06d}.{ext}")
            try:
                if frame_fn(url, off, out_path, height, 2):
                    made.append(out_path)
            except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
                log(f"  offset {vi.fmt_hms(off)}: skipped ({e})")
        return made
    raise ValueError(f"unknown clip_mode: {clip_mode!r} "
                     f"(expected 'local-window' or 'per-timestamp')")


# ------------------------------------------------------------------- plan
def run(source=None, url=None, local=None, start=0, end=None, every=30,
        max_frames=30, out=None, fmt="png", height=720, sources_path=None,
        probe_file=None, probe_override=None,
        clip_mode: str = "local-window",
        frame_fn=vi._download_section_frame,
        download_fn=vi._download_youtube_clip,
        local_frame_fn=vi._extract_frame_local) -> dict:
    ext = "jpg" if fmt.lower() in ("jpg", "jpeg") else "png"
    sources_path = sources_path or vi.DEFAULT_SOURCES

    # ---- local file --------------------------------------------------
    if local:
        name = os.path.splitext(os.path.basename(local))[0]
        out = out or _default_out(name)
        made = extract_local(local, out, vi.parse_time(start),
                             vi.parse_time(end) if end is not None else None,
                             every, ext)
        log(f"{name}: {len(made)} frames → {out}")
        return {"out": out, "frames": made, "count": len(made)}

    # ---- youtube (source id or url) ----------------------------------
    if source:
        src = vi.find_source(sources_path, source)
        if src is None:
            raise SystemExit(f"no source id '{source}' in {sources_path}")
        if not vi.is_youtube_source(src):
            raise SystemExit(f"source '{source}' is not a youtube source")
        url = src.get("url") or src.get("vodUrl")
        name = source
        default_interval = int(src.get("sampleIntervalSeconds") or every)
    elif url:
        name = "url"
        default_interval = every
    else:
        raise SystemExit("provide one of --source, --url, or --local")

    meta = probe_override
    if meta is None:
        if probe_file:
            meta = vi.probe_vod(url, dump_fn=lambda _u: vi.load_probe_file(probe_file))
        else:
            meta = vi.probe_vod(url)
    plan = vi.plan_frames(meta["duration"], start=start, end=end,
                          interval=every or default_interval,
                          max_frames=max_frames)
    log(f"{name}: \"{meta['title']}\" duration {vi.fmt_hms(meta['duration'])}")
    log(f"  window {vi.fmt_hms(plan['start'])}–{vi.fmt_hms(plan['end'])} "
        f"every {plan['interval']}s → {plan['count']} frames"
        + ("  [capped]" if plan["capped"] else ""))

    out = out or _default_out(name)
    made = extract_youtube(url, out, plan["offsets"], height, ext,
                           clip_mode=clip_mode, frame_fn=frame_fn,
                           download_fn=download_fn, local_frame_fn=local_frame_fn)
    log(f"  {len(made)}/{plan['count']} frames → {out}")
    return {"out": out, "frames": made, "count": len(made), "plan": plan}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--source", help="youtube VOD source id")
    g.add_argument("--url", help="youtube URL")
    g.add_argument("--local", help="local .mp4 file")
    ap.add_argument("--start", default=0, help="seconds or H:MM:SS")
    ap.add_argument("--end", default=None, help="seconds or H:MM:SS")
    ap.add_argument("--every", type=int, default=30, help="seconds between frames")
    ap.add_argument("--max-frames", type=int, default=30)
    ap.add_argument("--out", help="output dir")
    ap.add_argument("--format", default="png", choices=["png", "jpg", "jpeg"])
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--sources", default=vi.DEFAULT_SOURCES)
    ap.add_argument("--probe-file", help="offline metadata blob for youtube")
    ap.add_argument("--clip-mode", choices=["local-window", "per-timestamp"],
                    default="local-window",
                    help="local-window (default): one yt-dlp download for "
                         "the whole window + local ffmpeg seeks. "
                         "per-timestamp: one remote yt-dlp seek per offset "
                         "(fallback; unreliable deep into long VODs).")
    args = ap.parse_args()
    run(source=args.source, url=args.url, local=args.local, start=args.start,
        end=args.end, every=args.every, max_frames=args.max_frames,
        out=args.out, fmt=args.format, height=args.height,
        sources_path=args.sources, probe_file=args.probe_file,
        clip_mode=args.clip_mode)


if __name__ == "__main__":
    main()
