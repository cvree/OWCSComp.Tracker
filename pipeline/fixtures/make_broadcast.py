#!/usr/bin/env python3
"""
make_broadcast.py — build a broadcast-shaped test VOD from committed frames.

The sparse acquisition path can only be honestly tested against something
that behaves like a real broadcast over a real HTTP connection: hours long,
mostly NOT gameplay, with the live play in a few widely separated windows.
Downloading an actual OWCS VOD in a test suite is out of the question, so
this synthesises one from the repository's own committed evidence frames
(`reports/ingest/qad-twis-nepal/frames/`) — which are real 1280x720 OWCS
broadcast frames with a real HUD, so the chip detector, the gameplay filter
and the calibrator all see genuine material rather than drawn rectangles.

Structure, by default:

    0 ────────────── desk ──────────────┐
    │  (blurred + darkened gameplay: no chip saturation, no portrait
    │   texture — exactly what the filter is supposed to reject)
    ├── window 1: live gameplay ────────┤
    ├────────────── desk ───────────────┤
    ├── window 2: live gameplay ────────┤
    └────────────── desk ───────────────┘

Encoded at a low frame rate with a keyframe every second, because what is
being measured is HTTP range seeking, not codec efficiency, and a dense
keyframe grid makes a seek land quickly at any offset — the same property a
real YouTube VOD has.

Usage:
  python pipeline/fixtures/make_broadcast.py --out /tmp/vod.mp4 \\
      --duration 1800 --windows 300-600,1200-1500
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
FRAMES = os.path.join(REPO, "reports", "ingest", "qad-twis-nepal", "frames")

FPS = 2                 # low: this is a seek test, not a playback test
GOP_SECONDS = 1         # a keyframe every second, like a streamed VOD


def source_frames() -> list[str]:
    paths = sorted(glob.glob(os.path.join(FRAMES, "*.jpg")))
    if not paths:
        raise SystemExit(f"no committed broadcast frames in {FRAMES}")
    return paths


def parse_windows(spec: str) -> list[tuple[float, float]]:
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        out.append((float(a), float(b)))
    return out


def build(out_path: str, duration: float,
          windows: list[tuple[float, float]]) -> str:
    """Write the VOD. Returns the path."""
    import cv2

    gameplay = [cv2.imread(p) for p in source_frames()]
    gameplay = [f for f in gameplay if f is not None]
    if not gameplay:
        raise SystemExit("committed frames are unreadable")
    h, w = gameplay[0].shape[:2]

    # The desk frame: the same broadcast, blurred and darkened until the
    # chip row is gone. Derived from a real frame rather than drawn, so it
    # keeps a real broadcast's noise and colour distribution.
    desk = cv2.GaussianBlur(gameplay[0], (81, 81), 0)
    desk = (desk * 0.35).astype("uint8")

    tmp = tempfile.mkdtemp(prefix="mkvod_")
    try:
        n = int(duration * FPS)
        for i in range(n):
            t = i / FPS
            live = any(a <= t < b for a, b in windows)
            img = gameplay[i % len(gameplay)] if live else desk
            cv2.imwrite(os.path.join(tmp, f"f{i:07d}.png"), img)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".",
                    exist_ok=True)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-framerate", str(FPS), "-i", os.path.join(tmp, "f%07d.png"),
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
               "-pix_fmt", "yuv420p",
               "-g", str(FPS * GOP_SECONDS), "-keyint_min",
               str(FPS * GOP_SECONDS), "-sc_threshold", "0",
               "-movflags", "+faststart",
               out_path]
        subprocess.run(cmd, check=True, capture_output=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration", type=float, default=1800.0)
    ap.add_argument("--windows", default="300-600,1200-1500",
                    help="comma list of live-gameplay spans, seconds")
    args = ap.parse_args(argv)
    path = build(args.out, args.duration, parse_windows(args.windows))
    size = os.path.getsize(path)
    print(f"wrote {path} — {size / 1e6:.1f} MB, {args.duration:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
