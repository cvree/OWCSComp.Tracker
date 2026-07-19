#!/usr/bin/env python3
"""
build_crop_report.py — cut every hero slot from calibration frames and emit
crops.html: 10 crop strips per frame, each side-by-side with the best-matching
hero template and its score (blueprint Phase 2 evidence page).

This is how you verify — by eye, in the browser — that every layout box
contains exactly one hero portrait, and later which templates are clean.
It reads no comps and writes nothing to the database.

Score labels (from the layout's match_threshold, default 0.6):
  OK        score >= match_threshold
  LOW       LOW_FLOOR <= score < match_threshold  (needs review)
  NO-MATCH  score < LOW_FLOOR
  no templates yet -> crops only, clearly labeled.

Usage:
  python pipeline/build_crop_report.py --layout layouts/owcs_youtube_2026.json \
      --frames-dir work/auto/<run>/frames_raw --report-dir reports/auto/<run>
"""
from __future__ import annotations
import argparse
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import capture  # noqa: E402
import detect  # noqa: E402
import hero_overlay_detect as hod  # noqa: E402
import build_layout_debug as bld  # noqa: E402

LOW_FLOOR = 0.35
MAX_FRAMES = 8          # crops.html stays light; earliest N frames
_LABEL_COLORS = {"OK": "#2ebd6b", "LOW": "#e8a13c", "NO-MATCH": "#ff5c64"}

_CSS = """
:root{--bg:#060b15;--raise:#0c1524;--surface:#111c31;--line:#1f2e4d;
--text:#e9eef7;--muted:#8ea0bd;--amber:#ffa92b}
body{font-family:Inter,"Segoe UI",system-ui,sans-serif;max-width:1200px;
margin:0 auto;padding:28px 18px 48px;color:var(--text);background:
radial-gradient(1000px 420px at 85% -10%,rgba(79,169,255,.07),transparent 60%),
var(--bg);line-height:1.55}
h1{font-family:"Chakra Petch","Segoe UI",sans-serif;font-size:1.35rem}
h2{font-family:"Chakra Petch",sans-serif;font-size:.95rem;margin-top:26px;
text-transform:uppercase;letter-spacing:.1em;color:var(--muted)}
.strip{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.cell{border:1px solid var(--line);border-radius:8px;padding:6px;
background:var(--surface);text-align:center;font-size:.72rem;width:96px;
color:var(--muted);transition:border-color .12s ease}
.cell:hover{border-color:var(--amber)}
.cell img{width:80px;image-rendering:auto;border:1px solid var(--line);
display:block;margin:2px auto;border-radius:4px;background:#050a13}
.pill{display:inline-block;color:#fff;border-radius:999px;padding:0 8px;
font-family:"Chakra Petch",sans-serif;font-weight:700;font-size:.62rem;
letter-spacing:.06em}
.muted{color:var(--muted);font-size:.9rem}
.frames{display:flex;gap:12px;flex-wrap:wrap;margin:6px 0}
.frames figure{margin:0;flex:1 1 320px;max-width:560px}
.frames img{width:100%;border:1px solid var(--line);border-radius:8px}
.frames figcaption{color:var(--muted);font-size:.75rem;text-align:center}
.warn{border:1px solid rgba(232,161,60,.5);border-left:4px solid #e8a13c;
background:rgba(232,161,60,.12);padding:12px 16px;border-radius:10px;
margin:12px 0}
a{color:var(--amber);text-decoration:none}a:hover{text-decoration:underline}
code{background:rgba(255,255,255,.07);padding:1px 6px;border-radius:4px;
font-family:ui-monospace,Consolas,monospace;font-size:.85em}
"""


def log(msg: str) -> None:
    print(f"[crop-report] {msg}", flush=True)


def _esc(v) -> str:
    return (str(v).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _label(score: float, threshold: float) -> str:
    if score >= threshold:
        return "OK"
    if score >= LOW_FLOOR:
        return "LOW"
    return "NO-MATCH"


def _try_load_lib(layout: dict, templates_dir: str | None):
    """Template lib or (None, reason) — missing templates is a normal state."""
    try:
        return hod.load_lib(layout, templates_dir), None
    except FileNotFoundError as e:
        return None, str(e)


def crop_slots(frame_bgr, layout: dict) -> list[dict]:
    """All 10 slot crops of one frame: [{side,i,crop|None,note}].

    The layout is auto-scaled to the frame's actual size first (same aspect
    ratio only), so a 1920x1080 layout crops correctly from 640x360 frames.
    A skipped slot's note names the slot's scaled box and exactly why."""
    fh, fw = frame_bgr.shape[:2]
    layout, sinfo = capture.scale_layout_to_frame(layout, fw, fh)
    out = []
    for side in ("a", "b"):
        for i, (x, y, w, h) in enumerate(layout.get(f"slots_{side}", []),
                                         start=1):
            x, y, w, h = int(x), int(y), int(w), int(h)
            why = None
            if w <= 0 or h <= 0:
                why = f"zero/negative size {w}x{h}"
            elif x < 0 or y < 0 or x + w > fw or y + h > fh:
                why = f"outside the {fw}x{fh} frame"
                if not sinfo["ok"]:
                    why += f" ({sinfo['reason']})"
            if why:
                out.append({"side": side, "i": i, "crop": None,
                            "note": f"box [{x},{y},{w},{h}] {why}"})
                continue
            out.append({"side": side, "i": i,
                        "crop": frame_bgr[y:y + h, x:x + w], "note": ""})
    return out


def process(frames_dir: str, layout: dict, report_dir: str,
            templates_dir: str | None = None,
            max_frames: int = MAX_FRAMES) -> dict:
    """Write crops + crops.html into report_dir. Returns a summary dict."""
    if not os.path.isdir(frames_dir):
        raise FileNotFoundError(f"no frames dir: {frames_dir}")
    frames = sorted(f for f in os.listdir(frames_dir)
                    if f.lower().endswith((".png", ".jpg", ".jpeg")))
    crops_dir = os.path.join(report_dir, "crops")
    os.makedirs(crops_dir, exist_ok=True)
    ann_dir = os.path.join(report_dir, "annotated")
    lib, lib_reason = _try_load_lib(layout, templates_dir)
    threshold = layout.get("match_threshold", 0.6)
    saved_tpl: dict[str, str] = {}
    sections: list[str] = []
    n_crops = 0
    skipped: list[dict] = []
    scale_note = ""

    for fn in frames[:max_frames]:
        frame = cv2.imread(os.path.join(frames_dir, fn))
        if frame is None:
            continue
        fh, fw = frame.shape[:2]
        _, sinfo = capture.scale_layout_to_frame(layout, fw, fh)
        scale_note = sinfo["note"]
        base = os.path.splitext(fn)[0]
        # raw frame (linked in place) + annotated copy with scaled boxes
        os.makedirs(ann_dir, exist_ok=True)
        ann_fn = f"{base}_annotated.png"
        cv2.imwrite(os.path.join(ann_dir, ann_fn),
                    bld.draw_layout(frame, layout))
        raw_rel = os.path.relpath(os.path.join(frames_dir, fn),
                                  report_dir).replace(os.sep, "/")
        lw, lh = sinfo["from"]
        meta = (f"frame <code>{fw}x{fh}</code> · layout native "
                f"<code>{lw}x{lh}</code> · scale factor "
                f"<code>x{sinfo['factor']}</code>")
        if not sinfo["ok"]:
            meta += (f" — <span style='color:#ff5c64'>"
                     f"{_esc(sinfo['reason'])}</span>")
        frame_head = (
            f"<p class='muted'>{meta}</p>"
            "<div class='frames'>"
            f"<figure><a href='{_esc(raw_rel)}'>"
            f"<img src='{_esc(raw_rel)}'></a>"
            "<figcaption>raw frame</figcaption></figure>"
            f"<figure><a href='annotated/{_esc(ann_fn)}'>"
            f"<img src='annotated/{_esc(ann_fn)}'></a>"
            "<figcaption>annotated (scaled boxes)</figcaption></figure>"
            "</div>")
        cells = []
        for s in crop_slots(frame, layout):
            slot_id = f"{s['side']}{s['i']}"
            if s["crop"] is None:
                skipped.append({"frame": fn, "slot": slot_id,
                                "note": s["note"]})
                cells.append(f"<div class='cell'>{slot_id}<br>"
                             f"<span class='pill' style='background:#c62828'>"
                             f"BAD BOX</span><br>"
                             f"<span class='muted'>{_esc(s['note'])}</span>"
                             "</div>")
                continue
            crop_fn = f"{base}_{slot_id}.png"
            cv2.imwrite(os.path.join(crops_dir, crop_fn), s["crop"])
            n_crops += 1
            body = f"{slot_id}<br><img src='crops/{_esc(crop_fn)}'>"
            if lib:
                gray = cv2.cvtColor(s["crop"], cv2.COLOR_BGR2GRAY)
                hero, score = detect.match_slot(gray, lib)
                lab = _label(score, threshold)
                if hero and hero not in saved_tpl:  # show what it matched
                    tfn = f"tpl_{hero}.png"
                    # lib entries are (gray_img, filename) pairs
                    cv2.imwrite(os.path.join(crops_dir, tfn),
                                lib[hero][0][0])
                    saved_tpl[hero] = tfn
                tpl_img = (f"<img src='crops/{_esc(saved_tpl[hero])}'>"
                           if hero in saved_tpl else "")
                body += (f"{tpl_img}"
                         f"<span class='pill' style='background:"
                         f"{_LABEL_COLORS[lab]}'>{lab}</span><br>"
                         f"{_esc(hero) or '—'} {score:.2f}")
            cells.append(f"<div class='cell'>{body}</div>")
        got = sum(1 for c in cells if "BAD BOX" not in c)
        count_note = (f"<span class='muted'> — {got}/10 slots cropped"
                      + ("" if got == 10 else ", see BAD BOX cells for why")
                      + "</span>")
        sections.append(f"<h2>{_esc(fn)}{count_note}</h2>"
                        f"{frame_head}"
                        f"<div class='strip'>{''.join(cells)}</div>")

    if lib:
        head_note = (f"<p class='muted'>Each cell: slot crop, the template "
                     f"it matched best, and the score. OK &ge; {threshold}, "
                     f"LOW &ge; {LOW_FLOOR}, else NO-MATCH. Low scores on "
                     "real portraits usually mean the box or the template "
                     "needs work — never a comp claim.</p>")
    else:
        head_note = (f"<div class='warn'>No hero templates yet "
                     f"({_esc(lib_reason or '')}) — crops only. Verify every "
                     "box holds exactly one portrait, then build templates "
                     "from the clean crops (Phase 3).</div>")
    html = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>hero crop report</title>"
            f"<style>{_CSS}</style></head><body>"
            f"<h1>Hero crop report — {min(len(frames), max_frames)} of "
            f"{len(frames)} frame(s)</h1>"
            f"{head_note}"
            "<p><a href='layout.html'>layout debug</a> · "
            "<a href='index.html'>run report</a> · "
            "<a href='../../../runs.html'>all runs</a></p>"
            + "".join(sections or
                      ["<p class='muted'>No readable frames.</p>"])
            + "</body></html>")
    html_path = os.path.join(report_dir, "crops.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    return {"frames": min(len(frames), max_frames), "crops": n_crops,
            "html": html_path, "templates": bool(lib),
            "templatesNote": lib_reason or "",
            "scale": scale_note, "skipped": skipped}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Hero crop evidence report")
    ap.add_argument("--layout", required=True)
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--report-dir", required=True,
                    help="run report folder; writes crops/ + crops.html")
    ap.add_argument("--templates-dir", default=None)
    ap.add_argument("--max-frames", type=int, default=MAX_FRAMES)
    args = ap.parse_args(argv)
    layout = capture.load_layout(args.layout)
    res = process(args.frames_dir, layout, args.report_dir,
                  templates_dir=args.templates_dir,
                  max_frames=args.max_frames)
    log(f"{res['crops']} crop(s) from {res['frames']} frame(s) -> "
        f"{res['html']}")
    if res["scale"]:
        log(res["scale"])
    for s in res["skipped"]:
        log(f"SKIPPED {s['frame']} slot {s['slot']}: {s['note']}")
    if not res["templates"]:
        log("no templates yet — crops only (expected before Phase 3)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
