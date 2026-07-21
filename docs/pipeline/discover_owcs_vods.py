#!/usr/bin/env python3
"""
discover_owcs_vods.py — find recent official OWCS YouTube VODs and save one
as a video source, without downloading any video.

Uses ONE `yt-dlp --flat-playlist -J` call (metadata only) against a channel's
/streams tab. No YouTube API key, no cookies, no browser automation, $0.

List (dry run — writes nothing):
  python pipeline/discover_owcs_vods.py --provider youtube \
      --channel-url "https://www.youtube.com/@ow_esports/streams" --limit 20

Save entry #1 into data/sources/video_sources.json:
  python pipeline/discover_owcs_vods.py --provider youtube --limit 20 \
      --select 1 --write

A saved source gets an id (slug) like `owcs-<videoid>` and works with the
existing tools, e.g.:
  python pipeline/run_capture_trial.py --source <slug> --start 1:30:00 \
      --end 1:32:00 --every 30 --clip-mode local-window
  python pipeline/extract_calibration_frames.py --source <slug> ...

Duplicates (same video already in video_sources.json) are never added twice.
This tool touches ONLY source metadata — it never infers or writes comps.
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import video_ingest as vi  # noqa: E402

DEFAULT_CHANNEL = "https://www.youtube.com/@ow_esports/streams"
DEFAULT_SOURCES = vi.DEFAULT_SOURCES
DEFAULT_LAYOUT = "layouts/owcs_youtube_2026.json"

YTDLP_INSTALL_HINT = (
    "yt-dlp not found on PATH.\n"
    "Windows install (simple method): download yt-dlp.exe from\n"
    "  https://github.com/yt-dlp/yt-dlp/releases/latest\n"
    "and put it in C:\\ffmpeg\\bin (if that folder is already on PATH).\n"
    "Verify with:  yt-dlp --version"
)


def log(msg: str) -> None:
    print(f"[discover] {msg}", flush=True)


# ------------------------------------------------------------- yt-dlp io
def fetch_channel_entries(channel_url: str, limit: int,
                          runner=subprocess) -> list[dict]:
    """One flat-playlist metadata dump; returns raw yt-dlp entry dicts."""
    cmd = ["yt-dlp", "--flat-playlist", "--playlist-end", str(limit),
           "-J", "--no-warnings", channel_url]
    try:
        res = runner.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise SystemExit(YTDLP_INSTALL_HINT)
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"yt-dlp failed ({e.returncode}) for {channel_url}\n"
                         f"{(e.stderr or '').strip()[-800:]}")
    try:
        payload = json.loads(res.stdout or "{}")
    except ValueError:
        raise SystemExit("could not parse yt-dlp JSON output")
    return payload.get("entries") or []


# ------------------------------------------------------- pure transforms
def normalize_entry(e: dict) -> dict | None:
    """Flat-playlist entry -> normalized vod dict (None if unusable)."""
    vid = e.get("id")
    if not vid:
        return None
    date = e.get("upload_date") or ""
    if len(date) == 8 and date.isdigit():                 # 20260315
        date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    return {
        "videoId": vid,
        "title": e.get("title") or "(untitled)",
        "url": f"https://www.youtube.com/watch?v={vid}",
        "date": date or "TBD",
        "durationSeconds": int(e.get("duration") or 0),
        "platform": "youtube",
    }


def relevance_score(vod: dict) -> int:
    """Heuristic: OWCS-ish title keywords + broadcast-length duration."""
    t = vod["title"].lower()
    score = 0
    for kw, pts in (("owcs", 50), ("champions", 10), ("stage", 10),
                    ("playoff", 10), ("final", 10), ("major", 10),
                    ("overwatch", 5)):
        if kw in t:
            score += pts
    d = vod["durationSeconds"]
    if d >= 7200:
        score += 15
    elif d >= 3600:
        score += 10
    elif d >= 1800:
        score += 5
    elif 0 < d < 600:                                     # shorts/clips
        score -= 20
    return score


def slug_for(vod: dict) -> str:
    return "owcs-" + vod["videoId"].lower()


def fmt_duration(seconds: int) -> str:
    if not seconds:
        return "?"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def discover(channel_url: str, limit: int, runner=subprocess) -> list[dict]:
    """Fetch + normalize + score + slug, sorted by score desc then recency."""
    vods = []
    for e in fetch_channel_entries(channel_url, limit, runner=runner):
        v = normalize_entry(e)
        if v:
            v["score"] = relevance_score(v)
            v["slug"] = slug_for(v)
            vods.append(v)
    # stable sort: score desc; ties keep the channel's newest-first order
    vods.sort(key=lambda v: -v["score"])
    return vods


# ----------------------------------------------------------- sources io
def load_sources_file(path: str) -> dict:
    if not os.path.exists(path):
        return {"sources": []}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("sources", [])
    return data


def save_sources_file(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def existing_video_ids(data: dict) -> set[str]:
    """Video ids already present (from source ids and from URLs)."""
    ids: set[str] = set()
    for s in data["sources"]:
        sid = (s.get("id") or "")
        if sid.startswith("owcs-"):
            ids.add(sid[len("owcs-"):].lower())
        url = s.get("url") or s.get("vodUrl") or ""
        if "watch?v=" in url:
            ids.add(url.split("watch?v=")[-1].split("&")[0].lower())
    return ids


def write_source(vod: dict, sources_path: str,
                 layout: str = DEFAULT_LAYOUT) -> tuple[bool, str]:
    """Append the vod as a source entry. Returns (added, slug)."""
    data = load_sources_file(sources_path)
    slug = vod["slug"]
    if vod["videoId"].lower() in existing_video_ids(data):
        return False, slug
    data["sources"].append({
        "id": slug,
        "title": vod["title"],
        "url": vod["url"],
        "platform": "youtube",
        "date": vod["date"],
        "region": "Unknown",
        "notes": ("Discovered via discover_owcs_vods.py. Ingestion/frame-"
                  "extraction only; comps and FACEIT pairing come later."),
        "enabled": True,
        "sampleIntervalSeconds": 300,
        "layout": layout,
    })
    save_sources_file(sources_path, data)
    return True, slug


# ------------------------------------------------------------------ cli
def print_list(vods: list[dict]) -> None:
    if not vods:
        log("no VODs found.")
        return
    for i, v in enumerate(vods, start=1):
        print(f"{i:2d}. {v['title']}")
        print(f"    url:      {v['url']}")
        print(f"    date:     {v['date']}")
        print(f"    duration: {fmt_duration(v['durationSeconds'])}")
        print(f"    platform: {v['platform']}")
        print(f"    score:    {v['score']}")
        print(f"    slug:     {v['slug']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Discover OWCS YouTube VODs")
    ap.add_argument("--provider", default="youtube", choices=["youtube"])
    ap.add_argument("--channel-url", default=DEFAULT_CHANNEL)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--select", type=int,
                    help="pick entry N from the list (1-based)")
    ap.add_argument("--write", action="store_true",
                    help="save the selected VOD into video_sources.json "
                         "(without this flag nothing is written)")
    ap.add_argument("--sources", default=DEFAULT_SOURCES)
    ap.add_argument("--layout", default=DEFAULT_LAYOUT)
    args = ap.parse_args(argv)

    vods = discover(args.channel_url, args.limit)
    print_list(vods)
    if not vods:
        return 1

    if args.select is None:
        if args.write:
            log("--write needs --select N; nothing written.")
        return 0
    if not (1 <= args.select <= len(vods)):
        log(f"--select {args.select} out of range 1..{len(vods)}")
        return 1

    vod = vods[args.select - 1]
    if not args.write:
        log(f"dry run — would save '{vod['title']}' as source "
            f"'{vod['slug']}'. Re-run with --write to save.")
        return 0

    added, slug = write_source(vod, args.sources, layout=args.layout)
    if not added:
        log(f"already in {args.sources} — not added again (slug {slug}).")
        return 0
    log(f"saved source '{slug}' -> {args.sources}")
    log("next steps:")
    log(f"  python pipeline/run_capture_trial.py --source {slug} "
        f"--start 1:30:00 --end 1:32:00 --every 30 --clip-mode local-window")
    log(f"  python pipeline/extract_calibration_frames.py --source {slug} "
        f"--start 1:30:00 --end 1:35:00 --every 30 --out work/calib_real")
    return 0


if __name__ == "__main__":
    sys.exit(main())
