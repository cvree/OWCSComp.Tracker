#!/usr/bin/env python3
"""
download_vod_clip.py — download ONE reusable local clip from a saved source.

The clip is kept on disk (nothing is auto-deleted), so you can extract frames
from it many times, feed it to run_owcs_auto.py --local, or eyeball it in a
video player — without re-downloading the VOD window.

Usage:
  python pipeline/download_vod_clip.py --source owcs-afcxdimpsle \
      --start 1:30:00 --end 1:35:00 --out work/clips/day1_0130_0135.mp4
  python pipeline/download_vod_clip.py --url "https://youtube.com/watch?v=..." \
      --start 10:00 --end 12:00

If --out already exists the download is SKIPPED and the existing clip is
reused (pass --force to re-download). Default --out is
work/clips/<id>_<start>_<end>.mp4. yt-dlp progress streams live.
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import video_ingest as vi  # noqa: E402

CLIPS_DIR = os.path.join(db.REPO_ROOT, "work", "clips")


def log(msg: str) -> None:
    print(f"[clip] {msg}", flush=True)


def default_out(name: str, start: int, end: int) -> str:
    def tag(sec: int) -> str:
        h, rem = divmod(int(sec), 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}{m:02d}{s:02d}"
    return os.path.join(CLIPS_DIR, f"{name}_{tag(start)}_{tag(end)}.mp4")


def download_clip(url: str, start: int, end: int, out: str,
                  height: int = 720, force: bool = False,
                  with_audio: bool = False,
                  stall_timeout: float | None = None,
                  prefer_muxed: bool = False,
                  download_fn=vi._download_youtube_clip,
                  validate_fn=vi.probe_clip_valid) -> dict:
    """Download [start,end] of url to out, reusing an existing file.

    Cache states are always announced: REUSING a complete clip, RESUMING a
    partial one, or DELETING + re-downloading with --force. Video-only by
    default (with_audio=True to include audio).

    CACHE SAFETY: a cached clip is validated (byte-size floor + ffprobe when
    available, via validate_fn) BEFORE reuse. An invalid/corrupt cache — e.g.
    an 8-byte stub left by a stalled download — is auto-deleted and
    re-downloaded instead of being fed to ffmpeg (which would fail later with
    a confusing "moov atom not found" / "Invalid data"). A freshly downloaded
    clip is validated too, and a corrupt result raises InvalidClip with a
    clear message rather than a misleading ffmpeg error downstream.

    stall_timeout (seconds) is forwarded to the downloader: if the clip makes
    no real progress in that window it is killed and a StallTimeout is raised
    (see video_ingest._download_youtube_clip). None disables the guard.
    Returns {"path", "reused", "sizeBytes", "attempts", "resolution"} —
    attempts is the list of capture strategies tried (empty when a cache was
    reused or the downloader didn't report them), resolution the actual
    downloaded WxH via ffprobe (None if unknown).
    """
    part = out + ".part"
    if os.path.exists(out) and not force:
        ok, reason = validate_fn(out)
        if ok:
            size = os.path.getsize(out)
            res_info = vi.probe_clip_resolution(out)
            log(f"cache: REUSING existing clip ({size} bytes"
                + (f", {res_info['width']}x{res_info['height']}"
                   if res_info else "") + f") — {out}")
            log("(pass --force to delete it and re-download)")
            return {"path": out, "reused": True, "sizeBytes": size,
                    "attempts": [], "resolution": res_info}
        # invalid cache: delete it and fall through to a fresh download
        log(f"cache: cached clip invalid/corrupt — {reason}. "
            f"DELETING and re-downloading {out}")
        for p in (out, part):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError as e:
                    log(f"cache: could not delete {p} ({e}) — continuing")
    if force:
        for p in (out, part):
            if os.path.exists(p):
                log(f"cache: --force — DELETING {p} and re-downloading")
                try:
                    os.remove(p)
                except OSError as e:
                    log(f"cache: could not delete {p} ({e}) — continuing")
    elif os.path.exists(part):
        log(f"cache: found partial download ({os.path.getsize(part)} bytes) "
            f"— yt-dlp will RESUME {part}")
    # Only the real downloader accepts stall_timeout; test fakes may not, so
    # pass it only when set (keeps injected fakes with the old signature OK).
    kw = {"with_audio": with_audio}
    if stall_timeout is not None:
        kw["stall_timeout"] = stall_timeout
    if prefer_muxed:                # only forwarded when set (fake-fn safe)
        kw["prefer_muxed"] = True
    dres = download_fn(url, start, end, out, height, **kw)
    attempts = (dres or {}).get("attempts", []) \
        if isinstance(dres, dict) else []
    # Validate the freshly-downloaded clip: a corrupt result must fail with a
    # clear message here, not as a confusing ffmpeg error two steps later.
    ok, reason = validate_fn(out)
    if not ok:
        # never leave a corrupt file behind to poison the next run's cache
        for p in (out, part):
            if os.path.exists(p):
                try:
                    os.remove(p)
                    log(f"deleted corrupt download: {p}")
                except OSError as e:
                    log(f"could not delete corrupt download {p} ({e})")
        raise vi.InvalidClip(
            f"downloaded clip is invalid/corrupt — {reason} ({out}). "
            f"It was deleted; re-run to retry, or try --with-audio / "
            f"another format.")
    size = os.path.getsize(out) if os.path.exists(out) else 0
    res_info = vi.probe_clip_resolution(out)
    if res_info:
        log(f"downloaded clip is {res_info['width']}x{res_info['height']} "
            f"({res_info.get('codec')}, ~{res_info.get('duration')}s)")
    return {"path": out, "reused": False, "sizeBytes": size,
            "attempts": attempts, "resolution": res_info}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Download a reusable VOD clip")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--source", help="source id from video_sources.json")
    g.add_argument("--url", help="a youtube URL directly")
    ap.add_argument("--start", required=True, help="seconds or H:MM:SS")
    ap.add_argument("--end", required=True, help="seconds or H:MM:SS")
    ap.add_argument("--out", help="output .mp4 path "
                    "(default work/clips/<id>_<start>_<end>.mp4)")
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--video-only", action="store_true", default=True,
                    help="download video stream only (this is the DEFAULT; "
                    "flag kept for explicitness)")
    ap.add_argument("--with-audio", action="store_true",
                    help="also download + merge audio (slower; only needed "
                    "if you want to watch the clip with sound)")
    ap.add_argument("--force", action="store_true",
                    help="delete any cached/partial clip and re-download")
    ap.add_argument("--sources", default=vi.DEFAULT_SOURCES)
    args = ap.parse_args(argv)

    start, end = vi.parse_time(args.start), vi.parse_time(args.end)
    if end <= start:
        raise SystemExit("--end must be after --start")

    if args.source:
        src = vi.find_source(args.sources, args.source)
        if not src:
            raise SystemExit(f"no source id '{args.source}' in {args.sources}")
        if not vi.is_youtube_source(src):
            raise SystemExit(f"source '{args.source}' is not a youtube source")
        url, name = src.get("url") or src.get("vodUrl"), args.source
    else:
        url, name = args.url, "clip"

    out = args.out or default_out(name, start, end)
    log(f"step: download clip · source: {name}")
    log(f"window: {vi.fmt_hms(start)}-{vi.fmt_hms(end)} · out: {out}")
    try:
        res = download_clip(url, start, end, out, height=args.height,
                            force=args.force, with_audio=args.with_audio)
    except FileNotFoundError as e:
        log(f"FAILED — {e} (is yt-dlp installed? see docs/windows-setup.md)")
        return 1
    except Exception as e:
        log(f"FAILED — {e!r}")
        return 1
    log(f"OK — {'reused existing' if res['reused'] else 'downloaded'} clip "
        f"({res['sizeBytes']} bytes) -> {res['path']}")
    log(f"next: python pipeline/run_owcs_auto.py --local {res['path']} "
        f"--start 0 --end {vi.fmt_hms(end - start)} --every 30")
    return 0


if __name__ == "__main__":
    sys.exit(main())
