#!/usr/bin/env python3
"""
vision_dashboard.py — ONE read-only HTML dashboard for the whole OWCS vision
pipeline state of a single run.

    py pipeline/vision_dashboard.py --run owcs-8c105lnzlam_000600_000630 \
        --layout layouts/owcs_8c105lnzlam.json
    py pipeline/vision_dashboard.py --run <run> --layout <layout> --open

Writes ONLY:
    reports/auto/<run>/vision_dashboard.html
    reports/auto/<run>/vision_dashboard/   (context/annotated/fallback crop PNGs)

WHAT IT SHOWS
  1. Run status ladder (DB, layout, run folder, frames_raw, layout debug,
     hero_crops.html, crops.json, labels.json, candidate templates,
     candidate reports, candidate dry-run) + the ONE next recommended command.
  2. Visual diagnostics per frame: raw frame, annotated layout frame, all 10
     slot crops, a larger CONTEXT crop around each slot (slot box drawn),
     deterministic crop-quality class (usable/blank/partial/ui-only/
     suspicious), detector guess+score and manual label state if recorded.
  3. Links to every related report that exists; missing ones show the exact
     command that creates them.
  4. Per-state human instructions (the full ladder, current step highlighted).

WHAT IT NEVER DOES (hard rules)
  * Never writes to the DB, templates/, layouts/, work/, or any existing file.
  * Never promotes comps, never runs the detector, never OCRs,
    never touches FACEIT logic.
  * Missing files never crash it — they become MISS rows with a fix command.
  * Fully offline + deterministic. cv2/numpy are OPTIONAL: without them the
    dashboard still renders (existing crop images + statuses), it just can't
    generate context crops or quality classes ("unknown").
"""
from __future__ import annotations

import argparse
import html as _html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:                                   # OPTIONAL — degrade gracefully
    import cv2                         # noqa: F401
    import numpy as np                 # noqa: F401
    HAS_CV = True
except Exception:                      # pragma: no cover - env dependent
    cv2 = None
    np = None
    HAS_CV = False

DEFAULT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAME_EXTS = (".png", ".jpg", ".jpeg", ".webp")
MAX_FRAMES_DEFAULT = 12
MIN_LABELS = 20            # BLUEPRINT Phase 3: ~20-40 labelled crops
CONTEXT_PAD_FRAC = 0.75    # context crop grows each slot box by 75% per side
SUSPICIOUS_SCORE = 0.35    # detector score below this => "suspicious"


# --------------------------------------------------------------------- paths
def rpaths(root: str, run: str, layout: str | None) -> dict:
    """Every path the dashboard may look at. Lists = accepted alternates."""
    j = lambda *p: os.path.join(root, *p)                      # noqa: E731
    rd = j("reports", "auto", run)
    return {
        "root": root, "run": run, "run_dir": rd,
        "db": j("data", "owcs.sqlite"),
        "sources": j("data", "sources", "video_sources.json"),
        "auto_runs": j("data", "auto_runs.json"),
        "layout": (layout if layout and os.path.isabs(layout)
                   else (j(layout) if layout else None)),
        "frames_raw": [j("work", "auto", run, "frames_raw"),
                       j("work", "auto", run, "frames"),
                       os.path.join(rd, "frames_raw")],
        "frames_kept": j("work", "auto", run, "frames"),
        "run_index": os.path.join(rd, "index.html"),
        "layout_html": os.path.join(rd, "layout.html"),
        "crops_html": os.path.join(rd, "crops.html"),
        "hero_crops_html": os.path.join(rd, "hero_crops.html"),
        "crops_json": os.path.join(rd, "hero_crops", "crops.json"),
        "labels_json": [os.path.join(rd, "hero_crops", "labels.json"),
                        j("data", "eval", "labels.json")],
        "detections_json": os.path.join(rd, "detections.json"),
        "review_queue": os.path.join(rd, "review_queue.json"),
        "ocr_hud": os.path.join(rd, "ocr_hud.html"),
        "slot_loc": os.path.join(rd, "slot_localization.html"),
        "candidates_dir": j("templates", "candidates"),
        "cand_calib": [os.path.join(rd, "candidate_calib.html"),
                       j("reports", "candidates", run, "candidate_calib.html")],
        "cand_report": [os.path.join(rd, "candidate_report.html"),
                        j("reports", "candidates", run, "index.html"),
                        j("templates", "candidates", "report.html")],
        "cand_eval": [os.path.join(rd, "candidate_eval.html"),
                      j("reports", "candidates", run, "candidate_eval.html"),
                      j("templates", "candidates", "eval_report.json")],
        "cand_detect": [os.path.join(rd, "candidate_detections.html"),
                        j("reports", "candidates", run, "dry_run.json"),
                        j("templates", "candidates", "dry_run.json")],
        "out_html": os.path.join(rd, "vision_dashboard.html"),
        "out_dir": os.path.join(rd, "vision_dashboard"),
    }


def first_existing(p) -> str | None:
    for cand in (p if isinstance(p, list) else [p]):
        if cand and os.path.exists(cand):
            return cand
    return None


def dir_frames(d: str | None) -> list[str]:
    if not d or not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.lower().endswith(FRAME_EXTS))


def load_json(path: str | None):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def parse_run_id(run: str):
    """'owcs-8c105lnzlam_000600_000630' -> (source, '0:06:00', '0:06:30')."""
    import re
    m = re.match(r"^(.*?)_(\d{6})_(\d{6})$", run)
    if not m:
        return run, None, None
    hms = lambda s: f"{int(s[:2])}:{s[2:4]}:{s[4:6]}"          # noqa: E731
    return m.group(1), hms(m.group(2)), hms(m.group(3))


# ---------------------------------------------------- status ladder + advice
def label_stats(labels_path: str | None) -> tuple[int, int]:
    """(labeled_count, rejected_count) from a labels.json, tolerant of shape."""
    data = load_json(labels_path)
    if not isinstance(data, dict):
        return (len(data), 0) if isinstance(data, list) else (0, 0)
    labeled = rejected = 0
    for v in data.values():
        if isinstance(v, dict):
            st = v.get("status")
            if st == "labeled" or (st is None and v.get("hero")):
                labeled += 1
            elif st == "rejected":
                rejected += 1
        else:
            labeled += 1
    return labeled, rejected


def build_checks(P: dict) -> list[dict]:
    """Ordered ladder. Each: {id,label,ok,detail,path}. First MISS = next step."""
    root = P["root"]
    rel = lambda p: os.path.relpath(p, root) if p else "—"     # noqa: E731
    out: list[dict] = []

    def add(cid, label, ok, detail, path):
        out.append({"id": cid, "label": label, "ok": bool(ok),
                    "detail": detail, "path": rel(path)})

    add("db", "Database initialized", os.path.isfile(P["db"]),
        "found" if os.path.isfile(P["db"]) else "data/owcs.sqlite not found",
        P["db"])

    lay = P["layout"]
    add("layout", "Layout file exists", lay and os.path.isfile(lay),
        "found" if (lay and os.path.isfile(lay)) else
        ("not found" if lay else "no --layout given and none recorded"),
        lay or os.path.join(root, "layouts"))

    fdir = first_existing(P["frames_raw"])
    frames = dir_frames(fdir)
    ran = os.path.isfile(P["run_index"]) or bool(frames)
    add("run", "Auto capture ran", ran,
        ("run report present" if os.path.isfile(P["run_index"])
         else "frames captured (no run report yet)") if ran
        else "no run report and no frames — capture has not run",
        P["run_index"] if os.path.isfile(P["run_index"]) else P["run_dir"])

    add("frames_raw", "frames_raw has frames", bool(frames),
        f"{len(frames)} frame(s) in {rel(fdir)}" if frames
        else "no frame images found", fdir or P["frames_raw"][0])

    add("layout_debug", "Layout debug page exists",
        os.path.isfile(P["layout_html"]),
        "layout.html present" if os.path.isfile(P["layout_html"])
        else "not generated", P["layout_html"])

    add("hero_crops", "hero_crops.html exists",
        os.path.isfile(P["hero_crops_html"]),
        "present" if os.path.isfile(P["hero_crops_html"]) else "not generated",
        P["hero_crops_html"])

    add("crops_json", "Crop metadata (crops.json) exists",
        os.path.isfile(P["crops_json"]),
        "present" if os.path.isfile(P["crops_json"]) else "not generated",
        P["crops_json"])

    lpath = first_existing(P["labels_json"])
    n_lab, n_rej = label_stats(lpath)
    labels_ok = n_lab >= MIN_LABELS
    add("labels", "labels.json ready", labels_ok,
        (f"{n_lab} labeled / {n_rej} rejected (need ≥ {MIN_LABELS} labeled)"
         if lpath else "labels.json missing"),
        lpath or P["labels_json"][0])

    cand_ok = os.path.isdir(P["candidates_dir"]) and \
        any(os.scandir(P["candidates_dir"]))
    add("candidates", "templates/candidates populated", cand_ok,
        "has candidate templates" if cand_ok else "empty or missing",
        P["candidates_dir"])

    crep = first_existing(P["cand_calib"]) or first_existing(P["cand_report"]) \
        or first_existing(P["cand_eval"])
    add("cand_report", "Candidate reports exist", crep is not None,
        rel(crep) if crep else "not generated", crep or P["cand_report"][0])

    dry = first_existing(P["cand_detect"])
    add("cand_dryrun", "Candidate dry-run detection done", dry is not None,
        rel(dry) if dry else "not run", dry or P["cand_detect"][0])

    return out


def commands(P: dict) -> dict:
    """Exact next-step commands (printed only; never executed)."""
    run = P["run"]
    src, start, end = parse_run_id(run)
    lay = os.path.relpath(P["layout"], P["root"]) if P["layout"] \
        else "layouts\\<layout>.json"
    lay = lay.replace("/", "\\")
    return {
        "db": "py pipeline\\init_db.py",
        "layout": (f"create/fix {lay} (copy layouts\\owcs_8c105lnzlam.json as "
                   "a starting point, then verify boxes in layout.html)"),
        "run": (f"py pipeline\\run_owcs_auto.py --source {src or '<source>'} "
                f"--start {start or 'H:MM:SS'} --end {end or 'H:MM:SS'} "
                f"--every 30 --layout {lay}"),
        "frames_raw": (f"py pipeline\\run_owcs_auto.py --source "
                       f"{src or '<source>'} --start {start or 'H:MM:SS'} "
                       f"--end {end or 'H:MM:SS'} --every 30 --layout {lay}"),
        "layout_debug": (f"py pipeline\\build_layout_debug.py --layout {lay} "
                         f"--from-frames work\\auto\\{run}\\frames_raw "
                         f"--out reports\\auto\\{run}"),
        "hero_crops": f"py pipeline\\capture_hero_crops.py --run {run} "
                      f"--layout {lay}",
        "crops_json": f"py pipeline\\capture_hero_crops.py --run {run} "
                      f"--layout {lay}",
        "labels": ("py pipeline\\serve.py    then open  http://localhost:8000/"
                   f"reports/auto/{run}/hero_crops.html  and label/reject "
                   "crops (saves labels.json live)"),
        "candidates": (f"py pipeline\\capture_hero_crops.py --run {run} "
                       "--export-candidates --write   (candidates only — "
                       "never real templates/)"),
        "cand_report": ("(future step) candidate calibration/eval reports — "
                        "Phase 3 eval harness; nothing to run yet"),
        "cand_dryrun": ("(future step) candidate dry-run detection — do NOT "
                        "approve templates; this stays manual"),
        "ocr_hud": (f"py pipeline\\ocr_hud.py --run {run} --layout {lay} "
                    "--engine easyocr   (scene-gating + team/hero OCR "
                    "diagnostics; candidates only)"),
        "slot_loc": (f"py pipeline\\slot_localize.py --run {run} "
                     f"--layout {lay}   (dynamic portrait-box proposals; "
                     "run ocr_hud first for contamination checks)"),
        "ready": ("(nothing) — every calibration input is present; template "
                  "approval remains a future MANUAL step"),
    }


STEP_HUMAN = {
    "db": "initialize DB",
    "layout": "fix layout",
    "run": "run auto capture",
    "frames_raw": "run auto capture",
    "layout_debug": "run calibration/layout debug report",
    "hero_crops": "generate hero crops",
    "crops_json": "generate hero crops",
    "labels": "label/reject crops",
    "candidates": "export candidate templates",
    "cand_report": "run calibration report / evaluate candidates",
    "cand_dryrun": "run candidate dry-run detection",
    "ready": "ready for future template approval",
}


def recommend(checks: list[dict], P: dict) -> dict:
    cmds = commands(P)
    for c in checks:
        if not c["ok"]:
            human = STEP_HUMAN[c["id"]]
            if c["id"] == "labels":
                n_lab, _ = label_stats(first_existing(P["labels_json"]))
                if n_lab:
                    human = f"label more crops ({n_lab}/{MIN_LABELS})"
            return {"id": c["id"], "human": human,
                    "command": cmds[c["id"]], "reason": c["detail"]}
    return {"id": "ready", "human": STEP_HUMAN["ready"],
            "command": cmds["ready"],
            "reason": "every check on the ladder passed"}


# ------------------------------------------------- crop quality (cv2 opt-in)
def classify_crop(crop_bgr, score=None) -> str:
    """Deterministic quality class for one slot crop.

    partial     crop missing / box fell (partly) outside the frame
    blank       near-uniform or near-black (no portrait content)
    ui-only     low colour saturation + lots of thin edges (HUD text/lines)
    suspicious  looks like content but detector score is very low
    usable      everything else
    unknown     cv2/numpy not installed
    """
    if crop_bgr is None:
        return "partial"
    if not HAS_CV:
        return "unknown"
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    std, mean = float(gray.std()), float(gray.mean())
    if std < 6.0 or mean < 14.0:
        return "blank"
    sat = float(cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)[..., 1].mean())
    edge_ratio = float((cv2.Canny(gray, 60, 160) > 0).mean())
    if sat < 22.0 and edge_ratio > 0.06:
        return "ui-only"
    if score is not None and float(score) < SUSPICIOUS_SCORE:
        return "suspicious"
    return "usable"


def _safe_write_img(path: str, img, out_dir: str) -> bool:
    """Write ONLY inside out_dir (hard containment check)."""
    p, d = os.path.normpath(path), os.path.normpath(out_dir)
    if os.path.commonpath([p, d]) != d:
        raise RuntimeError(f"refusing to write outside {d}: {path}")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return bool(cv2.imwrite(p, img))


# ------------------------------------------------------- visual data builder
def build_visuals(P: dict, max_frames: int) -> dict:
    """Collect (and where needed generate) per-frame visuals.

    Returns {"notes":[...], "frames":[{frame, raw_rel, ann_rel, cells:[...]}]}.
    Generated files go ONLY under P["out_dir"].
    """
    run_dir, out_dir = P["run_dir"], P["out_dir"]
    notes: list[str] = []
    meta = load_json(P["crops_json"])
    fdir = first_existing(P["frames_raw"])
    frame_files = dir_frames(fdir)

    layout = None
    if P["layout"] and os.path.isfile(P["layout"]):
        layout = load_json(P["layout"])
        if layout is not None:
            layout["_path"] = P["layout"]

    # helpers only importable when cv2 exists (they import cv2 at top level)
    bcr = capture = bld = None
    if HAS_CV:
        try:
            import build_crop_report as bcr        # noqa: F811
            import capture                          # noqa: F811
            import build_layout_debug as bld        # noqa: F811
        except Exception as e:                      # pragma: no cover
            notes.append(f"pipeline helpers unavailable ({e}); "
                         "context/annotated generation skipped")
            bcr = capture = bld = None
    else:
        notes.append("cv2/numpy not installed — showing existing images only; "
                     "no context crops or quality classes (install with: "
                     "py -m pip install opencv-python numpy)")

    def rel_from_dash(abs_path: str) -> str:
        return os.path.relpath(abs_path, run_dir).replace("\\", "/")

    frames_out: list[dict] = []

    # Path A — crops.json exists: richest source (guesses, labels, crops).
    if meta and meta.get("frames"):
        by_id = {c["id"]: c for c in meta.get("crops", [])}
        for fm in meta["frames"][:max_frames]:
            raw_abs = os.path.normpath(os.path.join(run_dir, fm.get("raw", "")))
            ann_rel = fm.get("annotated") if os.path.isfile(
                os.path.join(run_dir, fm.get("annotated") or "")) else None
            cells = []
            frame_img = None
            if HAS_CV and bcr and layout and os.path.isfile(raw_abs):
                frame_img = cv2.imread(raw_abs)
            for cid in fm.get("cropIds", []):
                c = by_id.get(cid, {})
                crop_rel = c.get("crop") if os.path.isfile(
                    os.path.join(run_dir, c.get("crop") or "")) else None
                ctx_rel, quality = None, "unknown"
                if frame_img is not None:
                    ctx_rel, quality = _context_and_quality(
                        frame_img, layout, capture, c, cid, out_dir,
                        rel_from_dash,
                        crop_path=os.path.join(run_dir, c["crop"])
                        if c.get("crop") else None)
                elif c.get("bad"):
                    quality = "partial"
                cells.append(_cell(c, crop_rel, ctx_rel, quality))
            frames_out.append({"frame": fm.get("frame"),
                               "offset": fm.get("offset"),
                               "raw_rel": rel_from_dash(raw_abs)
                               if os.path.isfile(raw_abs) else None,
                               "ann_rel": ann_rel, "cells": cells,
                               "scaleNote": fm.get("scaleNote", "")})
        if len(meta["frames"]) > max_frames:
            notes.append(f"showing first {max_frames} of "
                         f"{len(meta['frames'])} frames (raise --max-frames)")
        return {"notes": notes, "frames": frames_out}

    # Path B — no crops.json: fall back to raw frames (+ generate crops
    # ourselves when cv2 + layout available, into vision_dashboard/ only).
    if not frame_files:
        notes.append("no frames and no crops.json yet — visual section will "
                     "populate after capture + hero crops")
        return {"notes": notes, "frames": frames_out}
    notes.append("crops.json not generated yet — showing raw frames"
                 + ("; slot/context crops cut on the fly (no guesses/labels "
                    "until you run capture_hero_crops.py)"
                    if (HAS_CV and bcr and layout) else ""))

    for fn in frame_files[:max_frames]:
        raw_abs = os.path.join(fdir, fn)
        cells, ann_rel = [], None
        if HAS_CV and bcr and layout:
            frame_img = cv2.imread(raw_abs)
            if frame_img is not None:
                base = os.path.splitext(fn)[0]
                ann_abs = os.path.join(out_dir, "annotated",
                                       f"{base}_annotated.png")
                if _safe_write_img(ann_abs, bld.draw_layout(frame_img, layout),
                                   out_dir):
                    ann_rel = rel_from_dash(ann_abs)
                for s in bcr.crop_slots(frame_img, layout):
                    slot_id = f"{s['side']}{s['i']}"
                    cid = f"{base}_{slot_id}"
                    crop_rel, ctx_rel = None, None
                    quality = "partial"
                    if s["crop"] is not None:
                        crop_abs = os.path.join(out_dir, "crops", f"{cid}.png")
                        if _safe_write_img(crop_abs, s["crop"], out_dir):
                            crop_rel = rel_from_dash(crop_abs)
                        quality = classify_crop(s["crop"])
                        ctx_rel, _ = _context_and_quality(
                            frame_img, layout, capture,
                            {"side": s["side"], "slot": slot_id},
                            cid, out_dir, rel_from_dash, quality_done=True)
                    cells.append(_cell({"slot": slot_id, "note": s["note"]},
                                       crop_rel, ctx_rel, quality))
        frames_out.append({"frame": fn, "offset": None,
                           "raw_rel": rel_from_dash(raw_abs),
                           "ann_rel": ann_rel, "cells": cells,
                           "scaleNote": ""})
    if len(frame_files) > max_frames:
        notes.append(f"showing first {max_frames} of {len(frame_files)} "
                     "frames (raise --max-frames)")
    return {"notes": notes, "frames": frames_out}


def _cell(c: dict, crop_rel, ctx_rel, quality) -> dict:
    return {"id": c.get("id") or "", "slot": c.get("slot") or "",
            "crop_rel": crop_rel, "ctx_rel": ctx_rel, "quality": quality,
            "guess": c.get("guess"), "score": c.get("score"),
            "second": c.get("second"), "second_score": c.get("secondScore"),
            "margin": c.get("margin"), "reject": c.get("reject"),
            "label": c.get("label"),
            "label_status": c.get("label_status") or "unlabeled",
            "note": c.get("note") or ""}


def _context_and_quality(frame_img, layout, capture_mod, c, cid,
                         out_dir, rel_fn, crop_path=None, quality_done=False):
    """Write a padded context crop (slot box drawn) for one slot; classify."""
    fh, fw = frame_img.shape[:2]
    scaled, _ = capture_mod.scale_layout_to_frame(layout, fw, fh)
    side = (c.get("side") or (c.get("slot") or "a?")[0])
    try:
        idx = int((c.get("slot") or "a1")[1:]) - 1
        x, y, w, h = [int(v) for v in scaled[f"slots_{side}"][idx]]
    except Exception:
        return None, ("partial" if not quality_done else "partial")
    px, py = max(24, int(w * CONTEXT_PAD_FRAC)), \
        max(24, int(h * CONTEXT_PAD_FRAC))
    x0, y0 = max(0, x - px), max(0, y - py)
    x1, y1 = min(fw, x + w + px), min(fh, y + h + py)
    ctx = frame_img[y0:y1, x0:x1].copy()
    if ctx.size == 0:
        return None, "partial"
    cv2.rectangle(ctx, (x - x0, y - y0), (x - x0 + w, y - y0 + h),
                  (43, 169, 255), 2)
    ctx_abs = os.path.join(out_dir, "context", f"{cid}_ctx.png")
    ok = _safe_write_img(ctx_abs, ctx, out_dir)
    quality = None
    if not quality_done:
        crop_img = cv2.imread(crop_path) if crop_path else \
            (frame_img[y:y + h, x:x + w]
             if (0 <= y < y + h <= fh and 0 <= x < x + w <= fw) else None)
        quality = classify_crop(crop_img, c.get("score"))
    return (rel_fn(ctx_abs) if ok else None), quality


# ----------------------------------------------------------------- rendering
_E = lambda v: _html.escape(str(v if v is not None else "—"))    # noqa: E731

_CSS = """
:root{--bg:#060b15;--surface:#111c31;--line:#1f2e4d;--text:#e9eef7;
--muted:#8ea0bd;--amber:#ffa92b;--ok:#2ebd6b;--bad:#ff5c64}
*{box-sizing:border-box}
body{font-family:Inter,"Segoe UI",system-ui,sans-serif;max-width:1280px;
margin:0 auto;padding:26px 18px 60px;color:var(--text);background:var(--bg);
line-height:1.5}
h1{font-family:"Chakra Petch","Segoe UI",sans-serif;font-size:1.35rem}
h2{font-family:"Chakra Petch",sans-serif;font-size:.95rem;margin-top:28px;
text-transform:uppercase;letter-spacing:.1em;color:var(--muted)}
a{color:var(--amber);text-decoration:none}a:hover{text-decoration:underline}
code,pre{background:rgba(255,255,255,.07);border-radius:4px;
font-family:ui-monospace,Consolas,monospace;font-size:.85em}
code{padding:1px 6px}pre{padding:10px 12px;white-space:pre-wrap}
table{border-collapse:collapse;width:100%;font-size:.85rem}
th,td{border:1px solid var(--line);padding:6px 10px;text-align:left}
th{color:var(--muted);font-weight:600}
.ok{color:var(--ok);font-weight:700}.miss{color:var(--bad);font-weight:700}
.next{border:1px solid rgba(255,169,43,.55);border-left:5px solid var(--amber);
background:rgba(255,169,43,.10);padding:12px 16px;border-radius:10px;
margin:16px 0}
.pill{display:inline-block;color:#fff;border-radius:999px;padding:1px 9px;
font-family:"Chakra Petch",sans-serif;font-weight:700;font-size:.62rem;
letter-spacing:.05em}
.q-usable{background:#1f7a48}.q-blank{background:#5a6478}
.q-partial{background:#8a5a1f}.q-ui-only{background:#5a3f8a}
.q-suspicious{background:#a03040}.q-unknown{background:#3a4a66}
.st-labeled{color:var(--ok)}.st-rejected{color:var(--bad)}
.st-unlabeled{color:var(--muted)}
.frame-block{border:1px solid var(--line);border-radius:12px;padding:12px;
margin:14px 0;background:var(--surface)}
.frame-imgs{display:flex;gap:12px;flex-wrap:wrap}
.frame-imgs figure{margin:0;flex:1 1 300px;max-width:480px}
.frame-imgs img{width:100%;border:1px solid var(--line);border-radius:8px}
figcaption{color:var(--muted);font-size:.72rem;text-align:center}
.strip{display:flex;flex-wrap:wrap;gap:10px;margin-top:10px}
.cell{border:1px solid var(--line);border-radius:10px;padding:8px;
background:#0a1322;text-align:center;font-size:.7rem;width:150px;
color:var(--muted)}
.cell img{border:1px solid var(--line);display:block;margin:3px auto;
border-radius:4px;background:#050a13}
.cell img.crop{width:96px}.cell img.ctx{width:134px}
.links a,.links span{margin-right:14px}
.missing{color:var(--muted)}
.note{border:1px solid var(--line);border-left:4px solid var(--muted);
background:rgba(255,255,255,.03);padding:8px 12px;border-radius:8px;
margin:10px 0;font-size:.8rem;color:var(--muted)}
.step-now{background:rgba(255,169,43,.12)}
"""


def _links_section(P: dict, cmds: dict) -> str:
    run_dir = P["run_dir"]

    def one(label, path_or_list, fix_key=None):
        found = first_existing(path_or_list)
        if found:
            href = os.path.relpath(found, run_dir).replace("\\", "/")
            return f'<a href="{_E(href)}">{_E(label)}</a>'
        tip = _E(cmds.get(fix_key, "")) if fix_key else ""
        return (f'<span class="missing" title="{tip}">{_E(label)} '
                f'(missing)</span>')

    rows = [
        one("run report", P["run_index"], "run"),
        one("layout debug page", P["layout_html"], "layout_debug"),
        one("crop report", P["crops_html"], "hero_crops"),
        one("hero_crops.html (label here)", P["hero_crops_html"],
            "hero_crops"),
        one("labels.json", P["labels_json"], "labels"),
        one("detections.json", P["detections_json"]),
        one("review_queue.json", P["review_queue"]),
        one("ocr_hud.html (OCR/scene diagnostics)", P["ocr_hud"],
            "ocr_hud"),
        one("slot_localization.html (proposed boxes)", P["slot_loc"],
            "slot_loc"),
        one("candidate_calib.html", P["cand_calib"], "cand_report"),
        one("candidate_report.html", P["cand_report"], "cand_report"),
        one("candidate_eval.html", P["cand_eval"], "cand_report"),
        one("candidate_detections.html", P["cand_detect"], "cand_dryrun"),
    ]
    return ('<div class="links">' + " ".join(rows) +
            ' <a href="../../../runs.html">all runs</a>'
            ' <a href="index.html">this run</a></div>'
            '<p class="note">A "(missing)" link shows the command that '
            'creates it when you hover it.</p>')


def render_html(P: dict, checks: list[dict], rec: dict, visuals: dict,
                extra: dict) -> str:
    run = P["run"]
    check_rows = "".join(
        f"<tr><td class='{'ok' if c['ok'] else 'miss'}'>"
        f"{'OK' if c['ok'] else 'MISS'}</td><td>{_E(c['label'])}</td>"
        f"<td>{_E(c['detail'])}</td><td><code>{_E(c['path'])}</code></td></tr>"
        for c in checks)

    cmds = commands(P)
    ladder_rows = "".join(
        f"<tr class='{'step-now' if rec['id'] == cid else ''}'>"
        f"<td>{i}</td><td>{_E(STEP_HUMAN[cid])}</td>"
        f"<td><pre style='margin:0'>{_E(cmds[cid])}</pre></td></tr>"
        for i, cid in enumerate(
            ["db", "layout", "run", "frames_raw", "layout_debug",
             "hero_crops", "labels", "candidates", "cand_report",
             "cand_dryrun", "ready"], 1) if cid in cmds)

    frame_blocks = []
    for fm in visuals["frames"]:
        imgs = ""
        if fm["raw_rel"]:
            imgs += (f"<figure><img src='{_E(fm['raw_rel'])}' loading='lazy'>"
                     f"<figcaption>raw · {_E(fm['frame'])}</figcaption>"
                     "</figure>")
        if fm["ann_rel"]:
            imgs += (f"<figure><img src='{_E(fm['ann_rel'])}' loading='lazy'>"
                     "<figcaption>annotated layout</figcaption></figure>")
        cells = ""
        for c in fm["cells"]:
            crop = (f"<img class='crop' src='{_E(c['crop_rel'])}' "
                    "loading='lazy'>" if c["crop_rel"] else
                    "<div class='missing'>no crop</div>")
            ctx = (f"<img class='ctx' src='{_E(c['ctx_rel'])}' "
                   "loading='lazy'>" if c["ctx_rel"] else "")
            if c["guess"] == "UNKNOWN":
                # read_slot refused to call it — show why, never a bare
                # score that could look like a confident pick.
                det = (f"guess: <b style='color:var(--bad)'>UNKNOWN</b>"
                       + (f"<div class='missing'>{_E(c['reject'])}</div>"
                          if c["reject"] else ""))
            elif c["guess"] is not None:
                det = f"guess: <b>{_E(c['guess'])}</b> ({_E(c['score'])})"
                if c["second"] is not None:
                    det += (f"<div class='missing'>2nd {_E(c['second'])} "
                            f"({_E(c['second_score'])}) &middot; margin "
                            f"{_E(c['margin'])}</div>")
            elif c["score"] is not None:
                det = "score: " + _E(c["score"])
            else:
                det = "no detector data"
            lab = (f"label: <b>{_E(c['label'])}</b>"
                   if c["label"] else c["label_status"])
            cells += (
                f"<div class='cell'><div><b>{_E(c['slot'])}</b> "
                f"<span class='pill q-{_E(c['quality'])}'>"
                f"{_E(c['quality'])}</span></div>{crop}{ctx}"
                f"<div>{det}</div>"
                f"<div class='st-{_E(c['label_status'])}'>{lab}</div>"
                + (f"<div class='missing'>{_E(c['note'])}</div>"
                   if c["note"] else "") + "</div>")
        note = (f"<div class='missing'>{_E(fm['scaleNote'])}</div>"
                if fm.get("scaleNote") else "")
        frame_blocks.append(
            f"<div class='frame-block'><b>{_E(fm['frame'])}</b>"
            + (f" <span class='missing'>offset {_E(fm['offset'])}s</span>"
               if fm["offset"] is not None else "")
            + f"{note}<div class='frame-imgs'>{imgs}</div>"
            f"<div class='strip'>{cells}</div></div>")

    notes = "".join(f"<div class='note'>{_E(n)}</div>"
                    for n in visuals["notes"])
    summary = "".join(
        f"<tr><th>{_E(k)}</th><td>{v}</td></tr>" for k, v in extra.items())

    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{_E(run)} — vision debug dashboard</title>"
        f"<style>{_CSS}</style></head><body>"
        f"<h1>Vision debug dashboard — {_E(run)}</h1>"
        "<p class='note'>Read-only view. This page (and its images folder) "
        "is the ONLY thing vision_dashboard.py writes — no DB, template, "
        "layout, comp, or detector changes.</p>"
        f"<h2>Run summary</h2><table>{summary}</table>"
        f"<div class='next'><b>Next recommended action:</b> "
        f"{_E(rec['human'])}<br><small>{_E(rec['reason'])}</small>"
        f"<pre>{_E(rec['command'])}</pre></div>"
        f"<h2>Status ladder</h2><table><tr><th></th><th>check</th>"
        f"<th>detail</th><th>path</th></tr>{check_rows}</table>"
        f"<h2>Reports &amp; files</h2>{_links_section(P, cmds)}"
        f"<h2>Visual diagnostics</h2>{notes}"
        + ("".join(frame_blocks) or
           "<p class='missing'>Nothing to show yet.</p>")
        + f"<h2>Full workflow (current step highlighted)</h2>"
        f"<table><tr><th>#</th><th>step</th><th>command</th></tr>"
        f"{ladder_rows}</table>"
        "<p class='note'>Hard rules: no comp promotion, no FACEIT-derived "
        "comps, no template overwrites, no automatic layout edits, no OCR. "
        "Manual labels always override CV guesses.</p>"
        "</body></html>")


# ----------------------------------------------------------------- top level
def generate(run: str, layout: str | None = None, root: str = DEFAULT_ROOT,
             max_frames: int = MAX_FRAMES_DEFAULT) -> dict:
    """Build the dashboard. Returns {'html': path, 'rec': recommendation}."""
    root = os.path.abspath(root)

    # layout fallback: the run's recorded layout in data/auto_runs.json
    if not layout:
        runs = load_json(os.path.join(root, "data", "auto_runs.json")) or []
        for r in (runs if isinstance(runs, list) else []):
            if r.get("run") == run and r.get("layout"):
                layout = r["layout"]
                break

    P = rpaths(root, run, layout)
    checks = build_checks(P)
    rec = recommend(checks, P)
    os.makedirs(P["out_dir"], exist_ok=True)
    visuals = build_visuals(P, max_frames)

    src, start, end = parse_run_id(run)
    fdir = first_existing(P["frames_raw"])
    n_frames = len(dir_frames(fdir))
    extra = {
        "run id": f"<code>{_E(run)}</code>",
        "source": _E(src),
        "time window": _E(f"{start} – {end}" if start else "unparsed"),
        "layout": f"<code>{_E(os.path.relpath(P['layout'], root) if P['layout'] else None)}</code>",
        "frames_raw": (f"{n_frames} frame(s) in "
                       f"<code>{_E(os.path.relpath(fdir, root))}</code>"
                       if fdir else "<span class='miss'>none</span>"),
        "DB initialized": ("<span class='ok'>yes</span>"
                           if os.path.isfile(P["db"])
                           else "<span class='miss'>no</span>"),
        "cv2 available": "yes" if HAS_CV else
                         "no — context crops/quality disabled",
    }
    ocr_data = load_json(os.path.join(P["run_dir"], "ocr_hud.json"))
    if isinstance(ocr_data, dict) and isinstance(
            ocr_data.get("verdict"), dict):
        v = ocr_data["verdict"]
        extra["OCR verdict"] = (f"<b>{_E(v.get('label'))}</b> — "
                                f"{_E(v.get('detail'))}")

    html = render_html(P, checks, rec, visuals, extra)
    with open(P["out_html"], "w", encoding="utf-8") as fh:
        fh.write(html)
    return {"html": P["out_html"], "rec": rec, "checks": checks}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="One read-only HTML dashboard for a run's vision state")
    ap.add_argument("--run", required=True, help="auto run id")
    ap.add_argument("--layout", default=None,
                    help="layout JSON (default: the run's recorded layout)")
    ap.add_argument("--root", default=DEFAULT_ROOT, help=argparse.SUPPRESS)
    ap.add_argument("--max-frames", type=int, default=MAX_FRAMES_DEFAULT)
    ap.add_argument("--open", action="store_true",
                    help="open the dashboard in the default browser")
    args = ap.parse_args(argv)

    res = generate(args.run, args.layout, args.root, args.max_frames)
    rel = os.path.relpath(res["html"], args.root)
    print(f"[vision-dashboard] wrote {rel}")
    print(f"[vision-dashboard] next: {res['rec']['human']}")
    print(f"[vision-dashboard]   $ {res['rec']['command']}")
    print(f"[vision-dashboard] open  file:///{res['html'].replace(os.sep, '/')}"
          f"  or  http://localhost:8000/{rel.replace(os.sep, '/')}")
    if args.open:
        import webbrowser
        webbrowser.open("file:///" + res["html"].replace(os.sep, "/"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
