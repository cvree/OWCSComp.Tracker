#!/usr/bin/env python3
"""
build_layout_debug.py — draw a layout's rectangles onto calibration frames.

Overlays every region a layout defines so you can see, at a glance, whether
your numbers line up with the broadcast: team A hero slots, team B hero
slots, the gameplay anchor, the replay marker, and the optional score/map
plate. Annotated copies go to reports/layout_debug/. Loop: look, nudge the
rectangles in the layout JSON, re-run, repeat.

This is a visual aid only — it reads no heroes and writes no data.

Colors (BGR): A slots green, B slots blue, anchor yellow, replay red,
score/map magenta.

Usage:
  python3 pipeline/build_layout_debug.py --layout layouts/owcs_youtube_2026.json \
        --frames-dir reports/calibration_frames/owcs-afcxdimpsle
  python3 pipeline/build_layout_debug.py --layout L.json --frame one.png --out reports/layout_debug
"""
from __future__ import annotations
import argparse
import json
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import capture  # noqa: E402

DEFAULT_OUT = os.path.join(db.REPO_ROOT, "reports", "layout_debug")

# (label prefix, BGR color)
COLORS = {
    "a": ((80, 220, 80), "A"),      # green
    "b": ((235, 160, 60), "B"),     # blue
    "anchor": ((60, 220, 235), "ANCHOR"),   # yellow
    "replay": ((70, 70, 235), "REPLAY"),    # red
    "score_map": ((220, 90, 220), "SCORE/MAP"),  # magenta
}


def _box(img, rect, color, label) -> None:
    x, y, w, h = [int(v) for v in rect]
    cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)
    ty = y - 6 if y - 6 > 8 else y + h + 16
    cv2.putText(img, label, (x, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1,
                cv2.LINE_AA)


def draw_layout(frame_bgr, layout: dict):
    """Return an annotated copy of frame_bgr with all layout regions drawn.

    The layout is auto-scaled to the frame's actual size first (same aspect
    ratio only) — a 1920x1080 layout draws correctly on 640x360 fallback
    frames. On aspect mismatch the rects are drawn unscaled (the layout.html
    scaling note + validation warnings surface the problem)."""
    fh, fw = frame_bgr.shape[:2]
    layout, _sinfo = capture.scale_layout_to_frame(layout, fw, fh)
    img = frame_bgr.copy()
    for side in ("a", "b"):
        color, tag = COLORS[side]
        for i, rect in enumerate(layout.get(f"slots_{side}", []), start=1):
            _box(img, rect, color, f"{tag}{i}")
    for key in ("anchor", "replay", "score_map"):
        cfg = layout.get(key)
        if not cfg:
            continue
        rect = cfg.get("rect") if isinstance(cfg, dict) else cfg
        if rect:
            color, tag = COLORS[key]
            _box(img, rect, color, tag)
    return img


def process_dir(frames_dir: str, layout: dict, out_dir: str) -> list[str]:
    if not os.path.isdir(frames_dir):
        raise FileNotFoundError(f"no frames dir: {frames_dir}")
    os.makedirs(out_dir, exist_ok=True)
    made = []
    for fn in sorted(f for f in os.listdir(frames_dir)
                     if f.lower().endswith((".png", ".jpg", ".jpeg"))):
        frame = cv2.imread(os.path.join(frames_dir, fn))
        if frame is None:
            continue
        out = os.path.join(out_dir, os.path.splitext(fn)[0] + "_debug.png")
        cv2.imwrite(out, draw_layout(frame, layout))
        made.append(out)
    return made


def first_frame_size(frames_dir: str) -> tuple | None:
    """(w, h) of the first readable frame in a directory, else None."""
    if not frames_dir or not os.path.isdir(frames_dir):
        return None
    for fn in sorted(os.listdir(frames_dir)):
        if fn.lower().endswith((".png", ".jpg", ".jpeg")):
            img = cv2.imread(os.path.join(frames_dir, fn))
            if img is not None:
                return img.shape[1], img.shape[0]
    return None


def validate_layout(layout: dict) -> list[str]:
    """Schema sanity: 5+5 slots, sane pixel bounds vs frame size.

    Returns a list of human-readable warnings (empty = looks valid).
    Purely advisory — calibration pages surface these; nothing is blocked.
    """
    warns: list[str] = []
    fw, fh = layout.get("frame_width"), layout.get("frame_height")
    if not (isinstance(fw, int) and fw > 0 and isinstance(fh, int) and fh > 0):
        warns.append("frame_width/frame_height missing or not positive ints")
        fw = fh = None
    for side in ("slots_a", "slots_b"):
        slots = layout.get(side)
        if not isinstance(slots, list) or len(slots) != 5:
            warns.append(f"{side}: expected exactly 5 boxes, got "
                         f"{len(slots) if isinstance(slots, list) else 'none'}")
            continue
        for i, rect in enumerate(slots, start=1):
            if (not isinstance(rect, (list, tuple)) or len(rect) != 4
                    or not all(isinstance(v, (int, float)) for v in rect)):
                warns.append(f"{side}[{i}]: box must be [x,y,w,h] numbers")
                continue
            x, y, w, h = rect
            if w <= 0 or h <= 0:
                warns.append(f"{side}[{i}]: zero/negative size {w}x{h}")
            if fw and (x < 0 or y < 0 or x + w > fw or y + h > fh):
                warns.append(f"{side}[{i}]: [{x},{y},{w},{h}] outside the "
                             f"{fw}x{fh} frame")
    for key in ("anchor", "replay", "score_map"):
        cfg = layout.get(key)
        rect = cfg.get("rect") if isinstance(cfg, dict) else cfg
        if rect and fw and len(rect) == 4:
            x, y, w, h = rect
            if x < 0 or y < 0 or x + w > fw or y + h > fh:
                warns.append(f"{key}: rect outside the {fw}x{fh} frame")
    return warns


_LAYOUT_HTML_CSS = """
:root{--bg:#060b15;--raise:#0c1524;--surface:#111c31;--line:#1f2e4d;
--text:#e9eef7;--muted:#8ea0bd;--amber:#ffa92b}
body{font-family:Inter,"Segoe UI",system-ui,sans-serif;max-width:1100px;
margin:0 auto;padding:28px 18px 48px;color:var(--text);background:
radial-gradient(1000px 420px at 85% -10%,rgba(79,169,255,.07),transparent 60%),
var(--bg);line-height:1.55}
h1{font-family:"Chakra Petch","Segoe UI",sans-serif;font-size:1.35rem}
h2,h3{font-family:"Chakra Petch",sans-serif}
h2{font-size:1rem;margin-top:28px;text-transform:uppercase;
letter-spacing:.1em;color:var(--muted)}
h3{font-size:.85rem;color:var(--muted);margin:18px 0 4px}
img{max-width:100%;border:1px solid var(--line);border-radius:8px;margin:6px 0;
transition:border-color .12s ease}
img:hover{border-color:var(--amber)}
code,pre{background:rgba(255,255,255,.07);padding:1px 6px;border-radius:4px;
font-family:ui-monospace,Consolas,monospace;font-size:.85em;color:#c7d3e6}
pre{padding:12px 14px;overflow-x:auto;background:#050a13;
border:1px solid var(--line);border-radius:10px;line-height:1.5}
.warn{border:1px solid rgba(232,161,60,.5);border-left:4px solid #e8a13c;
background:rgba(232,161,60,.12);padding:12px 16px;border-radius:10px;
margin:12px 0}
.okbox{border:1px solid rgba(46,189,107,.5);border-left:4px solid #2ebd6b;
background:rgba(46,189,107,.12);padding:12px 16px;border-radius:10px;
margin:12px 0}
.muted{color:var(--muted);font-size:.9rem}
a{color:var(--amber);text-decoration:none}a:hover{text-decoration:underline}
li{margin:2px 0}
"""


def write_layout_html(html_path: str, layout: dict, layout_path: str,
                      images: list[str], frames_dir: str | None = None,
                      frame_size: tuple | None = None) -> str:
    """Emit the per-run layout debug viewer (blueprint Phase 2).

    Shows every annotated frame, the layout's validation warnings, the slot
    coordinates, and the exact re-run commands for the calibrate loop
    (edit JSON -> re-run from the SAME frames, no re-download).
    """
    warns = validate_layout(layout)
    if frame_size is None:
        frame_size = first_frame_size(frames_dir)
    scale_box = ""
    if frame_size:
        _, sinfo = capture.scale_layout_to_frame(layout, *frame_size)
        cls = "okbox" if sinfo["ok"] else "warn"
        scale_box = (f"<div class='{cls}'><strong>Scaling:</strong> "
                     f"{sinfo['note']}. Boxes below are drawn at the "
                     f"frame's actual size.</div>" if sinfo["ok"] else
                     f"<div class='{cls}'><strong>Scaling:</strong> "
                     f"{sinfo['note']}</div>")
    esc = lambda v: (str(v).replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;"))
    rel_layout = os.path.relpath(layout_path, db.REPO_ROOT) \
        if os.path.isabs(layout_path) else layout_path
    html_dir = os.path.dirname(os.path.abspath(html_path))
    imgs = "".join(
        f"<h3>{esc(os.path.basename(p))}</h3>"
        f"<a href='{esc(os.path.relpath(p, html_dir).replace(os.sep, '/'))}'>"
        f"<img src='{esc(os.path.relpath(p, html_dir).replace(os.sep, '/'))}'>"
        "</a>"
        for p in images)
    if warns:
        vbox = ("<div class='warn'><strong>Layout validation — "
                f"{len(warns)} warning(s):</strong><ul>"
                + "".join(f"<li>{esc(w)}</li>" for w in warns)
                + "</ul></div>")
    else:
        vbox = ("<div class='okbox'>Layout validation: 5+5 slots present, "
                "all boxes inside the frame.</div>")
    slots_pre = json.dumps({k: layout.get(k) for k in
                            ("frame_width", "frame_height", "slots_a",
                             "slots_b")}, indent=1)
    rerun_frames = frames_dir or "<frames_dir>"
    if os.path.isabs(rerun_frames):
        rerun_frames = os.path.relpath(rerun_frames, db.REPO_ROOT)
    out_rel = os.path.relpath(html_dir, db.REPO_ROOT)
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>layout debug</title>"
        f"<style>{_LAYOUT_HTML_CSS}</style></head><body>"
        f"<h1>Layout debug — <code>{esc(rel_layout)}</code></h1>"
        f"{scale_box}{vbox}"
        "<p class='muted'>Goal: every A/B box sits on exactly one hero "
        "portrait. If boxes are off: edit the coordinates in the layout "
        "JSON, then re-run from the SAME frames (no re-download):</p>"
        f"<pre>python pipeline/build_layout_debug.py --layout {esc(rel_layout)} "
        f"--frames-dir {esc(rerun_frames)} --out {esc(out_rel)}/layout_debug\n"
        f"python pipeline/build_crop_report.py --layout {esc(rel_layout)} "
        f"--frames-dir {esc(rerun_frames)} --report-dir {esc(out_rel)}</pre>"
        "<p><a href='crops.html'>hero crop report</a> · "
        "<a href='index.html'>run report</a> · "
        "<a href='../../../runs.html'>all runs</a></p>"
        f"<h2>Slot coordinates</h2><pre>{esc(slots_pre)}</pre>"
        f"<h2>Annotated frames ({len(images)})</h2>"
        + (imgs or "<p class='muted'>No annotated frames.</p>")
        + "</body></html>")
    os.makedirs(html_dir, exist_ok=True)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    return html_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", required=True)
    ap.add_argument("--frames-dir", help="directory of calibration frames")
    ap.add_argument("--frame", help="a single frame instead of a directory")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    layout = capture.load_layout(args.layout)
    if args.frame:
        frame = cv2.imread(args.frame)
        if frame is None:
            raise SystemExit(f"cannot read frame: {args.frame}")
        os.makedirs(args.out, exist_ok=True)
        out = os.path.join(args.out,
                           os.path.splitext(os.path.basename(args.frame))[0]
                           + "_debug.png")
        cv2.imwrite(out, draw_layout(frame, layout))
        made = [out]
    elif args.frames_dir:
        made = process_dir(args.frames_dir, layout, args.out)
    else:
        raise SystemExit("provide --frames-dir or --frame")

    html_dir = (os.path.dirname(args.out.rstrip("/\\"))
                if os.path.basename(args.out.rstrip("/\\")) == "layout_debug"
                else args.out)
    fsz = (first_frame_size(args.frames_dir) if args.frames_dir else None)
    if fsz is None and args.frame:
        img0 = cv2.imread(args.frame)
        fsz = (img0.shape[1], img0.shape[0]) if img0 is not None else None
    html = write_layout_html(os.path.join(html_dir, "layout.html"),
                             layout, args.layout, made,
                             frames_dir=args.frames_dir, frame_size=fsz)
    print(f"[layout-debug] wrote {len(made)} annotated frame(s) → {args.out}")
    print(f"[layout-debug] viewer: {html}")
    print("[layout-debug] open it, nudge rectangles in the layout JSON, re-run.")


if __name__ == "__main__":
    main()
