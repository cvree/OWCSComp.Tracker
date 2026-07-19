#!/usr/bin/env python3
"""
build_hero_templates.py — crop hero-slot candidates for template building.

Once the layout rectangles line up (verified with build_layout_debug), this
crops the ten hero portrait slots from each calibration frame and writes them
to templates/candidates/, plus a contact-sheet reports/template_candidates.html
that shows every crop with its timestamp, side (A/B), and slot (1–5).

You then pick the cleanest crop of each hero and rename it to
templates/<hero_id>.png (e.g. templates/tracer.png). Only files directly in
templates/ are used by the detector — the candidates/ subfolder is ignored,
so nothing here can pollute your real template set.

This tool reads no heroes automatically and writes no data; naming the heroes
is the human step.

Usage:
  python3 pipeline/build_hero_templates.py --layout layouts/owcs_youtube_2026.json \
        --frames-dir reports/calibration_frames/owcs-afcxdimpsle
  python3 pipeline/build_hero_templates.py --layout L.json --frames-dir F \
        --out templates/candidates --html reports/template_candidates.html
"""
from __future__ import annotations
import argparse
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import capture  # noqa: E402

DEFAULT_OUT = os.path.join(db.REPO_ROOT, "templates", "candidates")
DEFAULT_HTML = os.path.join(db.REPO_ROOT, "reports", "template_candidates.html")


def _offset_label(fn: str) -> tuple[int | None, str]:
    base = os.path.splitext(os.path.basename(fn))[0]
    digits = base.split("_")[0]
    if digits.isdigit():
        secs = int(digits)
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        return secs, f"{h:d}:{m:02d}:{s:02d}"
    return None, base


def crop_candidates(frames_dir: str, layout: dict, out_dir: str) -> list[dict]:
    """Crop all 10 slots from every frame. Returns crop metadata records."""
    if not os.path.isdir(frames_dir):
        raise FileNotFoundError(f"no frames dir: {frames_dir}")
    os.makedirs(out_dir, exist_ok=True)
    records = []
    for fn in sorted(f for f in os.listdir(frames_dir)
                     if f.lower().endswith((".png", ".jpg", ".jpeg"))):
        frame = cv2.imread(os.path.join(frames_dir, fn))
        if frame is None:
            continue
        secs, tlabel = _offset_label(fn)
        base = os.path.splitext(fn)[0]
        for side_key, tag in (("slots_a", "A"), ("slots_b", "B")):
            for i, (x, y, w, h) in enumerate(layout.get(side_key, []), start=1):
                crop = frame[y:y + h, x:x + w]
                if crop.size == 0:
                    continue
                name = f"{base}_{tag}{i}.png"
                path = os.path.join(out_dir, name)
                cv2.imwrite(path, crop)
                records.append({"path": path, "file": name, "seconds": secs,
                                "time": tlabel, "side": tag, "slot": i,
                                "frame": fn})
    return records


def write_html(records: list[dict], html_path: str) -> None:
    os.makedirs(os.path.dirname(html_path), exist_ok=True)
    html_dir = os.path.dirname(os.path.abspath(html_path))
    # group by frame (in time order)
    frames: dict[str, list[dict]] = {}
    for r in records:
        frames.setdefault(r["frame"], []).append(r)

    rows = []
    for frame in sorted(frames):
        recs = frames[frame]
        tlabel = recs[0]["time"]
        cells = []
        for side in ("A", "B"):
            for r in sorted((x for x in recs if x["side"] == side),
                            key=lambda x: x["slot"]):
                rel = os.path.relpath(r["path"], html_dir).replace(os.sep, "/")
                cells.append(
                    f'<figure><img src="{rel}" alt="{r["file"]}">'
                    f'<figcaption>{r["side"]}{r["slot"]}</figcaption></figure>')
        rows.append(
            f'<section><h2>{tlabel} <small>{frame}</small></h2>'
            f'<div class="grid">{"".join(cells)}</div></section>')

    doc = f"""<!doctype html><meta charset="utf-8">
<title>Hero template candidates</title>
<style>
 body{{font:14px system-ui,sans-serif;margin:24px;background:#111;color:#eee}}
 h1{{font-size:20px}} h2{{font-size:15px;margin:20px 0 8px}}
 small{{color:#888;font-weight:400}}
 .grid{{display:flex;flex-wrap:wrap;gap:10px}}
 figure{{margin:0;text-align:center}}
 img{{width:72px;height:72px;object-fit:contain;background:#000;
      border:1px solid #333;image-rendering:pixelated}}
 figcaption{{color:#aaa;font-size:12px;margin-top:2px}}
 .note{{color:#9cf}}
</style>
<h1>Hero template candidates — {len(records)} crops</h1>
<p class="note">Pick the cleanest crop of each hero and rename it to
templates/&lt;hero_id&gt;.png (e.g. templates/tracer.png). A/B = team, 1–5 = slot.</p>
{''.join(rows)}
"""
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(doc)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", required=True)
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--html", default=DEFAULT_HTML)
    args = ap.parse_args()

    layout = capture.load_layout(args.layout)
    records = crop_candidates(args.frames_dir, layout, args.out)
    write_html(records, args.html)
    print(f"[hero-templates] wrote {len(records)} crops → {args.out}")
    print(f"[hero-templates] contact sheet → {args.html}")
    print("[hero-templates] rename the clean ones to templates/<hero_id>.png")


if __name__ == "__main__":
    main()
