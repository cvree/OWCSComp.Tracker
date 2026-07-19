#!/usr/bin/env python3
"""
slot_localize.py — dynamic HUD slot localization + portrait candidate
extraction (diagnostics only).

    py pipeline/slot_localize.py --run owcs-8c105lnzlam_000600_000630 \
        --layout layouts/owcs_8c105lnzlam.json --open

WHY (from the OCR HUD verdict on real frames)
  OCR cannot read hero identity on this broadcast (player tags, stylized
  fonts). Its jobs are now: scene gating, team support, and TEXT
  CONTAMINATION detection. Hero identity stays with portraits — but the
  fixed layout boxes sometimes sit on UI/text. This module finds the
  portrait rows DYNAMICALLY with image processing and proposes better boxes.

HOW (deterministic image processing, no ML, no OCR identity)
  1. Take the top HUD strip (default top 16%; a layout's ocr_zones
     "top_strip" overrides). Split left/right halves.
  2. Portrait candidates per side: Canny edges -> dilate -> contours ->
     keep near-square boxes of plausible portrait size.
  3. Grid fit: portraits are 5 equal, evenly spaced squares on one row.
     Vote over (anchor_x, spacing) pairs from candidate pairs; the best
     grid keeps supporting candidate boxes and SYNTHESIZES the missing
     slots at grid positions (median size). 5 boxes per side, A1-A5 left
     row left->right, B1-B5 right row left->right.
  4. OCR is a contamination detector ONLY: OCR boxes are read from the
     run's existing ocr_hud.json (no engine needed here); a proposed crop
     overlapped by text is marked text-contaminated. OCR hero guesses are
     NEVER used for identity.
  5. Per-frame proposals are aggregated by per-slot median; agreement with
     the current layout is reported as IoU per slot.

OUTPUTS (the only writes)
  reports/auto/<run>/slot_localization.html (+ slot_localization/ PNGs)
  reports/auto/<run>/slot_localization.json
  layouts/<layout-name>.proposed.json — ONLY if enough slots were found
     (>= MIN_SUPPORT real detections per side on >= 1 frame and all 10
     boxes in-bounds). NEVER overwrites the original layout; the file name
     is forced to end in .proposed.json.

NOT here: comp promotion, DB writes, template writes, FACEIT logic, YOLO.
(If image processing proves insufficient, the research plan is YOLO-based
HUD region detection — documented in the report, not implemented.)
"""
from __future__ import annotations

import argparse
import datetime as dt
import html as _html
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import cv2
    import numpy as np
    HAS_CV = True
except Exception:                      # pragma: no cover - env dependent
    cv2 = None
    np = None
    HAS_CV = False

DEFAULT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAME_EXTS = (".png", ".jpg", ".jpeg", ".webp")
MAX_FRAMES_DEFAULT = 12

TOP_STRIP_FRAC = [0.00, 0.00, 1.00, 0.16]   # x,y,w,h fractions (override:
                                            # layout ocr_zones["top_strip"])
ASPECT_MIN, ASPECT_MAX = 0.70, 1.60         # near-square portraits
H_FRAC_MIN, H_FRAC_MAX = 0.025, 0.12        # portrait height vs frame height
SPACING_MIN_W, SPACING_MAX_W = 1.00, 2.20   # slot pitch in portrait widths
GRID_TOL_FRAC = 0.34                        # candidate-center to grid-center
MIN_SUPPORT = 3          # real (non-synthesized) detections per side needed
                         # before a proposed layout may be written
TEXT_OVERLAP_BAD = 0.30  # OCR-box overlap fraction that marks a crop bad


# ------------------------------------------------------------- small helpers
def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _overlap_frac(a, b) -> float:
    """Fraction of box a inside box b."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    return (ix * iy) / float(aw * ah) if aw > 0 and ah > 0 else 0.0


def _clamp_box(box, fw, fh):
    x, y, w, h = [int(round(v)) for v in box]
    x = max(0, min(x, fw - 1))
    y = max(0, min(y, fh - 1))
    w = max(1, min(w, fw - x))
    h = max(1, min(h, fh - y))
    return [x, y, w, h]


def load_json(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


# ------------------------------------------------------ candidate detection
def portrait_candidates(strip_bgr, y_off: int, fh: int) -> list[list[int]]:
    """Near-square, portrait-sized contour boxes in a HUD strip.

    Coordinates are returned in FULL-FRAME space (y_off added back).
    """
    gray = cv2.cvtColor(strip_bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if h <= 0 or w <= 0:
            continue
        aspect = w / h
        if not (ASPECT_MIN <= aspect <= ASPECT_MAX):
            continue
        if not (H_FRAC_MIN * fh <= h <= H_FRAC_MAX * fh):
            continue
        out.append([x, y + y_off, w, h])
    return out


def fit_grid(cands: list[list[int]], region_x0: int, region_x1: int
             ) -> dict | None:
    """Fit a 5-slot equal-pitch grid to candidate boxes in one side region.

    Returns {"boxes": [5x [x,y,w,h]], "supported": [bool x5],
             "support": int, "pitch": float} or None if < 2 candidates.
    Deterministic: ties broken by (support desc, residual asc, x asc).
    """
    cands = [c for c in cands
             if region_x0 <= c[0] + c[2] / 2.0 <= region_x1]
    if len(cands) < 2:
        return None
    med_w = statistics.median(c[2] for c in cands)
    med_h = statistics.median(c[3] for c in cands)
    med_y = statistics.median(c[1] for c in cands)
    centers = sorted(c[0] + c[2] / 2.0 for c in cands)

    best = None
    for i in range(len(centers)):
        for j in range(i + 1, len(centers)):
            gap = centers[j] - centers[i]
            for k in (1, 2, 3, 4):
                pitch = gap / k
                if not (SPACING_MIN_W * med_w <= pitch
                        <= SPACING_MAX_W * med_w):
                    continue
                for m in range(5):          # slot index of centers[i]
                    x0 = centers[i] - m * pitch
                    grid = [x0 + t * pitch for t in range(5)]
                    if grid[0] - med_w / 2 < region_x0 - med_w * 0.6 or \
                       grid[4] + med_w / 2 > region_x1 + med_w * 0.6:
                        continue
                    support, residual, used = 0, 0.0, []
                    for g in grid:
                        near = min(centers, key=lambda cx: abs(cx - g))
                        if abs(near - g) <= pitch * GRID_TOL_FRAC:
                            support += 1
                            residual += abs(near - g)
                            used.append(True)
                        else:
                            used.append(False)
                    key = (-support, residual, x0)
                    if best is None or key < best["key"]:
                        best = {"key": key, "grid": grid, "pitch": pitch,
                                "supported": used, "support": support}
    if best is None or best["support"] < 2:
        return None

    by_center = {c[0] + c[2] / 2.0: c for c in cands}
    boxes = []
    for g, sup in zip(best["grid"], best["supported"]):
        if sup:                                  # snap to the real candidate
            near = min(by_center, key=lambda cx: abs(cx - g))
            boxes.append(list(by_center[near]))
        else:                                    # synthesize at grid position
            boxes.append([g - med_w / 2.0, med_y, med_w, med_h])
    return {"boxes": boxes, "supported": best["supported"],
            "support": best["support"], "pitch": best["pitch"]}


def localize_frame(frame_bgr, layout: dict | None) -> dict:
    """Find A1-A5 / B1-B5 portrait boxes in one frame."""
    fh, fw = frame_bgr.shape[:2]
    strip = TOP_STRIP_FRAC
    if layout and isinstance(layout.get("ocr_zones"), dict):
        z = layout["ocr_zones"].get("top_strip")
        if (isinstance(z, (list, tuple)) and len(z) == 4
                and 0 <= z[1] < 1 and 0 < z[3] <= 1):
            strip = [float(v) for v in z]
    sy0 = int(strip[1] * fh)
    sy1 = min(fh, int((strip[1] + strip[3]) * fh))
    cands = portrait_candidates(frame_bgr[sy0:sy1], sy0, fh)

    mid = fw // 2
    left = fit_grid([c for c in cands if c[0] + c[2] / 2.0 < mid], 0, mid)
    right = fit_grid([c for c in cands if c[0] + c[2] / 2.0 >= mid], mid, fw)

    slots = {}
    for side, fit in (("a", left), ("b", right)):
        if fit:
            for i, (box, sup) in enumerate(zip(fit["boxes"],
                                               fit["supported"]), 1):
                slots[f"{side}{i}"] = {"box": _clamp_box(box, fw, fh),
                                       "detected": bool(sup)}
    return {"size": [fw, fh], "candidates": cands, "slots": slots,
            "left_ok": left is not None, "right_ok": right is not None,
            "support": {"a": left["support"] if left else 0,
                        "b": right["support"] if right else 0}}


# --------------------------------------------------------- crop classifying
def classify_proposed(frame_bgr, box, ocr_boxes) -> str:
    """usable / blank / partial / text-contaminated / suspicious."""
    fh, fw = frame_bgr.shape[:2]
    x, y, w, h = box
    if x < 0 or y < 0 or x + w > fw or y + h > fh or w < 4 or h < 4:
        return "partial"
    for ob in ocr_boxes:            # OCR = contamination detector ONLY
        if _overlap_frac(ob, box) >= TEXT_OVERLAP_BAD or \
           _overlap_frac(box, ob) >= TEXT_OVERLAP_BAD:
            return "text-contaminated"
    crop = frame_bgr[y:y + h, x:x + w]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    std, mean = float(gray.std()), float(gray.mean())
    if std < 6.0 or mean < 14.0:
        return "blank"
    sat = float(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[..., 1].mean())
    edge_ratio = float((cv2.Canny(gray, 60, 160) > 0).mean())
    if sat < 22.0 and edge_ratio > 0.06:
        return "suspicious"          # looks like UI lines, not a portrait
    return "usable"


def ocr_boxes_for_frame(ocr_hud_data: dict | None, frame_name: str) -> list:
    """Text boxes from an existing ocr_hud.json (>=3 normalized chars)."""
    if not ocr_hud_data:
        return []
    for f in ocr_hud_data.get("frames", []):
        if f.get("frame") == frame_name:
            return [it["box"] for it in
                    f.get("analysis", {}).get("ocr_purposes", [])
                    if len([c for c in it.get("text", "")
                            if c.isalnum()]) >= 3]
    return []


# ----------------------------------------------------------- aggregation
def aggregate_slots(frames: list[dict]) -> dict:
    """Per-slot median box over frames that localized that slot."""
    agg = {}
    for sid in [f"a{i}" for i in range(1, 6)] + [f"b{i}" for i in range(1, 6)]:
        boxes = [f["local"]["slots"][sid]["box"] for f in frames
                 if sid in f["local"]["slots"]]
        det = sum(1 for f in frames
                  if f["local"]["slots"].get(sid, {}).get("detected"))
        if boxes:
            agg[sid] = {"box": [int(statistics.median(b[k] for b in boxes))
                                for k in range(4)],
                        "frames": len(boxes), "detected_in": det}
    return agg


def compare_layout(agg: dict, layout: dict | None, fw: int, fh: int) -> dict:
    if not layout:
        return {}
    import capture
    scaled, _ = capture.scale_layout_to_frame(layout, fw, fh)
    out = {}
    for side in ("a", "b"):
        for i, box in enumerate(scaled.get(f"slots_{side}", []), 1):
            sid = f"{side}{i}"
            if sid in agg:
                out[sid] = round(_iou([int(v) for v in box],
                                      agg[sid]["box"]), 3)
    return out


# ------------------------------------------------------ proposed layout out
def proposed_layout_path(layout_path: str | None, root: str, run: str) -> str:
    if layout_path:
        base = layout_path[:-5] if layout_path.endswith(".json") \
            else layout_path
        p = base + ".proposed.json"
    else:
        p = os.path.join(root, "layouts", f"{run}.proposed.json")
    if not p.endswith(".proposed.json"):        # hard guard: never original
        p += ".proposed.json"
    return p


def write_proposed_layout(path: str, layout: dict | None, agg: dict,
                          fw: int, fh: int, run: str) -> dict:
    """Write the proposal — caller has already checked eligibility."""
    if os.path.abspath(path).endswith(".json") and \
            not os.path.abspath(path).endswith(".proposed.json"):
        raise RuntimeError(f"refusing to write non-proposed layout: {path}")
    out = dict(layout) if layout else {}
    out.pop("_path", None)
    out["frame_width"], out["frame_height"] = fw, fh
    out["slots_a"] = [agg[f"a{i}"]["box"] for i in range(1, 6)]
    out["slots_b"] = [agg[f"b{i}"]["box"] for i in range(1, 6)]
    out["_proposed_by"] = {
        "tool": "slot_localize.py", "run": run,
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "note": ("REVIEW BEFORE USE — proposed boxes from dynamic HUD "
                 "localization; the original layout was not modified. "
                 "To adopt: manually copy this file over the original "
                 "after checking slot_localization.html.")}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh_:
        json.dump(out, fh_, indent=1)
    return out


# ----------------------------------------------------------------- report
_E = lambda v: _html.escape(str(v if v is not None else "—"))    # noqa: E731

_CSS = """
:root{--bg:#060b15;--surface:#111c31;--line:#1f2e4d;--text:#e9eef7;
--muted:#8ea0bd;--amber:#ffa92b;--ok:#2ebd6b;--bad:#ff5c64}
*{box-sizing:border-box}body{font-family:Inter,"Segoe UI",system-ui,
sans-serif;max-width:1280px;margin:0 auto;padding:26px 18px 60px;
color:var(--text);background:var(--bg);line-height:1.5}
h1{font-family:"Chakra Petch",sans-serif;font-size:1.3rem}
h2{font-family:"Chakra Petch",sans-serif;font-size:.95rem;margin-top:28px;
text-transform:uppercase;letter-spacing:.1em;color:var(--muted)}
a{color:var(--amber);text-decoration:none}a:hover{text-decoration:underline}
code,pre{background:rgba(255,255,255,.07);border-radius:4px;
font-family:ui-monospace,Consolas,monospace;font-size:.85em}
code{padding:1px 6px}pre{padding:10px 12px;white-space:pre-wrap}
table{border-collapse:collapse;width:100%;font-size:.85rem}
th,td{border:1px solid var(--line);padding:6px 10px;text-align:left}
th{color:var(--muted)}
.ok{color:var(--ok);font-weight:700}.bad{color:var(--bad);font-weight:700}
.muted{color:var(--muted)}
.pill{display:inline-block;color:#fff;border-radius:999px;padding:1px 9px;
font-family:"Chakra Petch",sans-serif;font-weight:700;font-size:.62rem}
.q-usable{background:#1f7a48}.q-blank{background:#5a6478}
.q-partial{background:#8a5a1f}.q-text-contaminated{background:#a03040}
.q-suspicious{background:#5a3f8a}
.verdict{border:1px solid rgba(46,189,107,.5);border-left:6px solid
 var(--ok);background:rgba(46,189,107,.08);padding:14px 18px;
border-radius:10px;margin:16px 0}
.verdict.no{border-color:rgba(255,92,100,.5);border-left-color:var(--bad);
background:rgba(255,92,100,.08)}
.frame-block{border:1px solid var(--line);border-radius:12px;padding:12px;
margin:14px 0;background:var(--surface)}
.frame-block img.big{width:100%;max-width:1000px;border:1px solid
 var(--line);border-radius:8px}
.strip{display:flex;flex-wrap:wrap;gap:10px;margin-top:10px}
.cell{border:1px solid var(--line);border-radius:10px;padding:8px;
background:#0a1322;text-align:center;font-size:.7rem;width:120px;
color:var(--muted)}
.cell img{width:96px;border:1px solid var(--line);display:block;
margin:3px auto;border-radius:4px}
.note{border:1px solid var(--line);border-left:4px solid var(--muted);
background:rgba(255,255,255,.03);padding:8px 12px;border-radius:8px;
margin:10px 0;font-size:.8rem;color:var(--muted)}
.legend span{margin-right:16px}
"""


def _annotate(frame_bgr, local: dict, layout: dict | None, ocr_boxes):
    img = frame_bgr.copy()
    fh, fw = img.shape[:2]
    if layout:
        import capture
        scaled, _ = capture.scale_layout_to_frame(layout, fw, fh)
        for side in ("a", "b"):
            for x, y, w, h in scaled.get(f"slots_{side}", []):
                cv2.rectangle(img, (int(x), int(y)),
                              (int(x + w), int(y + h)), (43, 169, 255), 1)
    for c in local["candidates"]:
        cv2.rectangle(img, (c[0], c[1]), (c[0] + c[2], c[1] + c[3]),
                      (200, 200, 200), 1)
    for ob in ocr_boxes:
        cv2.rectangle(img, (ob[0], ob[1]), (ob[0] + ob[2], ob[1] + ob[3]),
                      (60, 60, 230), 1)
    for sid, s in local["slots"].items():
        x, y, w, h = s["box"]
        col = (80, 220, 120) if s["detected"] else (230, 200, 80)
        cv2.rectangle(img, (x, y), (x + w, y + h), col, 2)
        cv2.putText(img, sid.upper(), (x + 2, y + h - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1, cv2.LINE_AA)
    return img


def render_html(run: str, meta: dict, frames: list[dict], agg: dict,
                ious: dict, safety: dict, proposed_rel: str | None) -> str:
    frame_blocks = []
    for f in frames:
        cells = ""
        for sid in sorted(f["crops"]):
            c = f["crops"][sid]
            cells += (
                f"<div class='cell'><b>{_E(sid.upper())}</b> "
                f"<span class='pill q-{_E(c['quality'])}'>"
                f"{_E(c['quality'])}</span>"
                + (f"<img src='{_E(c['img'])}' loading='lazy'>"
                   if c.get("img") else "")
                + f"<div>{'detected' if c['detected'] else 'synthesized'}"
                "</div></div>")
        frame_blocks.append(
            f"<div class='frame-block'><b>{_E(f['frame'])}</b> "
            f"<span class='muted'>candidates: "
            f"{len(f['local']['candidates'])} · support "
            f"A:{f['local']['support']['a']} B:{f['local']['support']['b']}"
            "</span>"
            + (f"<div><img class='big' src='{_E(f['annotated'])}' "
               "loading='lazy'></div>" if f.get("annotated") else "")
            + f"<div class='strip'>{cells}</div></div>")

    slot_rows = "".join(
        f"<tr><td>{_E(sid.upper())}</td>"
        f"<td><code>{_E(agg[sid]['box'])}</code></td>"
        f"<td>{agg[sid]['detected_in']}/{agg[sid]['frames']}</td>"
        f"<td>{_E(ious.get(sid))}</td></tr>"
        for sid in sorted(agg))

    v_cls = "" if safety["safe"] else "no"
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{_E(run)} — slot localization</title>"
        f"<style>{_CSS}</style></head><body>"
        f"<h1>Dynamic slot localization — {_E(run)}</h1>"
        "<p class='note'>Diagnostics only. Hero identity is NOT decided "
        "here (and never by OCR) — this page only proposes WHERE the "
        "portraits are. No DB/template/comp writes; the original layout "
        "file is never modified.</p>"
        f"<div class='verdict {v_cls}'><b>"
        f"{_E('Layout can be updated safely' if safety['safe'] else 'Not safe to update layout yet')}"
        f"</b><br>{_E(safety['reason'])}"
        + (f"<pre>proposed layout written: {_E(proposed_rel)}\n"
           "review it, then adopt MANUALLY (copy over the original):\n"
           f"copy {_E(proposed_rel)} {_E(safety.get('orig_rel', '<layout>'))}"
           "</pre>" if proposed_rel else "")
        + "</div>"
        "<h2>Run</h2><table>"
        + "".join(f"<tr><th>{_E(k)}</th><td>{_E(v)}</td></tr>"
                  for k, v in meta.items()) + "</table>"
        "<h2>Aggregated proposed boxes (median over frames)</h2>"
        "<table><tr><th>slot</th><th>proposed box [x,y,w,h]</th>"
        "<th>detected/frames</th><th>IoU vs current layout</th></tr>"
        f"{slot_rows}</table>"
        "<p class='note'>IoU near 1.0 = current layout already correct; "
        "low IoU on clean (usable) proposals = the layout box is the "
        "problem, exactly what the bad crops suggested.</p>"
        "<h2>Legend</h2><div class='legend note'>"
        "<span style='color:#ffa92b'>■ current layout box</span>"
        "<span style='color:#50dc78'>■ proposed (detected)</span>"
        "<span style='color:#ffc83c'>■ proposed (synthesized from grid)"
        "</span><span style='color:#c8c8c8'>■ raw contour candidate</span>"
        "<span style='color:#e63c3c'>■ OCR text box (contamination)</span>"
        "</div>"
        "<h2>Frames</h2>" + "".join(frame_blocks) +
        "<h2>Next steps</h2><div class='note'>"
        "1) If safe + boxes clean: adopt the proposed layout (manual copy "
        "above), re-run capture_hero_crops.py, label clean portrait crops, "
        "then export candidate templates. 2) If not safe: capture more/"
        "better frames (gameplay only — check ocr_hud gating) and re-run. "
        "3) Research plan only (NOT implemented): if dynamic localization "
        "keeps failing across broadcasts, a small YOLO/Roboflow "
        "overwatch_hero model for HUD region detection is the next thing "
        "to evaluate — after, and only after, this image-processing path "
        "is proven insufficient.</div>"
        "<p><a href='ocr_hud.html'>OCR HUD</a> · "
        "<a href='vision_dashboard.html'>vision dashboard</a> · "
        "<a href='index.html'>run report</a> · "
        "<a href='../../../runs.html'>all runs</a></p>"
        "</body></html>")


# ----------------------------------------------------------------- pipeline
def _frames_dir(root: str, run: str) -> str | None:
    for d in (os.path.join(root, "work", "auto", run, "frames_raw"),
              os.path.join(root, "work", "auto", run, "frames"),
              os.path.join(root, "reports", "auto", run, "frames_raw")):
        if os.path.isdir(d) and any(
                f.lower().endswith(FRAME_EXTS) for f in os.listdir(d)):
            return d
    return None


def _safe_out(path: str, out_dir: str) -> str:
    p, d = os.path.normpath(path), os.path.normpath(out_dir)
    if os.path.commonpath([p, d]) != d:
        raise RuntimeError(f"refusing to write outside {d}: {path}")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def run_localization(run: str, layout_path: str | None = None,
                     root: str = DEFAULT_ROOT,
                     max_frames: int = MAX_FRAMES_DEFAULT,
                     write_proposed: bool = True) -> dict:
    root = os.path.abspath(root)
    if not HAS_CV:
        raise RuntimeError("cv2/numpy required — py -m pip install "
                           "opencv-python numpy")
    report_dir = os.path.join(root, "reports", "auto", run)
    out_dir = os.path.join(report_dir, "slot_localization")
    os.makedirs(out_dir, exist_ok=True)

    layout = load_json(layout_path if not layout_path
                       or os.path.isabs(layout_path)
                       else os.path.join(root, layout_path))
    layout_abs = (layout_path if not layout_path or os.path.isabs(layout_path)
                  else os.path.join(root, layout_path))
    ocr_data = load_json(os.path.join(report_dir, "ocr_hud.json"))

    fdir = _frames_dir(root, run)
    frame_files = (sorted(f for f in os.listdir(fdir)
                          if f.lower().endswith(FRAME_EXTS))[:max_frames]
                   if fdir else [])

    frames_out, fw = [], 0
    fh = 0
    for i, fn in enumerate(frame_files, 1):
        frame = cv2.imread(os.path.join(fdir, fn))
        if frame is None:
            continue
        fh, fw = frame.shape[:2]
        local = localize_frame(frame, layout)
        ocr_boxes = ocr_boxes_for_frame(ocr_data, fn)
        crops = {}
        base = os.path.splitext(fn)[0]
        for sid, s in local["slots"].items():
            q = classify_proposed(frame, s["box"], ocr_boxes)
            img_rel = None
            x, y, w, h = s["box"]
            crop = frame[y:y + h, x:x + w]
            if crop.size:
                p = _safe_out(os.path.join(out_dir, "crops",
                                           f"{base}_{sid}.png"), out_dir)
                if cv2.imwrite(p, crop):
                    img_rel = os.path.relpath(p, report_dir)\
                        .replace("\\", "/")
            crops[sid] = {"box": s["box"], "detected": s["detected"],
                          "quality": q, "img": img_rel}
        ann = _annotate(frame, local, layout, ocr_boxes)
        ann_p = _safe_out(os.path.join(out_dir, f"{base}_slots.png"), out_dir)
        ann_rel = (os.path.relpath(ann_p, report_dir).replace("\\", "/")
                   if cv2.imwrite(ann_p, ann) else None)
        print(f"[slot-localize] [{i}/{len(frame_files)}] {fn}: "
              f"{len(local['candidates'])} candidates, support "
              f"A:{local['support']['a']} B:{local['support']['b']}, "
              f"{sum(1 for c in crops.values() if c['quality'] == 'usable')}"
              f"/{len(crops)} usable")
        frames_out.append({"frame": fn, "local": local, "crops": crops,
                           "annotated": ann_rel,
                           "ocr_text_boxes": len(ocr_boxes)})

    agg = aggregate_slots(frames_out)
    ious = compare_layout(agg, layout, fw, fh) if frames_out else {}

    # ---- safety gate for writing a proposed layout
    complete = len(agg) == 10
    max_support = {
        s: max([f["local"]["support"][s] for f in frames_out] or [0])
        for s in ("a", "b")}
    usable = sum(1 for f in frames_out for c in f["crops"].values()
                 if c["quality"] == "usable")
    total_crops = sum(len(f["crops"]) for f in frames_out)
    in_bounds = all(0 <= a["box"][0] and 0 <= a["box"][1]
                    and a["box"][0] + a["box"][2] <= fw
                    and a["box"][1] + a["box"][3] <= fh
                    for a in agg.values()) if agg and fw else False
    safe = (complete and in_bounds
            and max_support["a"] >= MIN_SUPPORT
            and max_support["b"] >= MIN_SUPPORT)
    reason = (f"all 10 slots localized, in-bounds, best real-detection "
              f"support A:{max_support['a']}/5 B:{max_support['b']}/5 "
              f"(need ≥{MIN_SUPPORT}), usable crops {usable}/{total_crops}"
              if safe else
              ("no frames found — run auto capture first" if not frames_out
               else f"only {len(agg)}/10 slots localized"
               if not complete else
               f"real-detection support too low (A:{max_support['a']} "
               f"B:{max_support['b']}, need ≥{MIN_SUPPORT} per side) — "
               "synthesized grids alone are not enough evidence"
               if (max_support['a'] < MIN_SUPPORT
                   or max_support['b'] < MIN_SUPPORT)
               else "aggregated boxes fall outside frame bounds"))
    safety = {"safe": safe, "reason": reason,
              "orig_rel": os.path.relpath(layout_abs, root)
              if layout_abs else None}

    proposed_path = proposed_layout_path(layout_abs, root, run)
    proposed_rel = None
    if safe and write_proposed:
        write_proposed_layout(proposed_path, layout, agg, fw, fh, run)
        proposed_rel = os.path.relpath(proposed_path, root)
        print(f"[slot-localize] proposed layout written: {proposed_rel} "
              "(original NOT modified)")

    result = {
        "run": run, "layout": layout_abs and os.path.relpath(layout_abs,
                                                             root),
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "candidate": True, "promoted": False,
        "ocr_role": "contamination detection only — never hero identity",
        "frames": [{"frame": f["frame"], "annotated": f["annotated"],
                    "slots": f["crops"],
                    "support": f["local"]["support"],
                    "candidates": len(f["local"]["candidates"]),
                    "ocr_text_boxes": f["ocr_text_boxes"]}
                   for f in frames_out],
        "aggregated": agg, "iou_vs_layout": ious,
        "safety": safety, "proposed_layout": proposed_rel,
    }
    with open(os.path.join(report_dir, "slot_localization.json"), "w",
              encoding="utf-8") as fh_:
        json.dump(result, fh_, indent=1)

    meta = {
        "layout": result["layout"] or "(none)",
        "frames dir": fdir or "(no frames — run auto capture first)",
        "frames analyzed": len(frames_out),
        "ocr_hud.json contamination source":
            "found" if ocr_data else
            "missing — run ocr_hud.py first for text-contamination checks",
        "usable proposed crops": f"{usable}/{total_crops}",
        "proposed layout": proposed_rel or "not written (see verdict)",
    }
    html_path = os.path.join(report_dir, "slot_localization.html")
    with open(html_path, "w", encoding="utf-8") as fh_:
        fh_.write(render_html(run, meta, frames_out, agg, ious, safety,
                              proposed_rel))
    return {"html": html_path,
            "json": os.path.join(report_dir, "slot_localization.json"),
            "safety": safety, "agg": agg, "ious": ious,
            "proposed": proposed_rel, "frames": len(frames_out)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Dynamic HUD slot localization (diagnostics only)")
    ap.add_argument("--run", required=True)
    ap.add_argument("--layout", default=None)
    ap.add_argument("--max-frames", type=int, default=MAX_FRAMES_DEFAULT)
    ap.add_argument("--no-proposed", action="store_true",
                    help="analyze only; never write the proposed layout")
    ap.add_argument("--root", default=DEFAULT_ROOT, help=argparse.SUPPRESS)
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args(argv)

    res = run_localization(args.run, args.layout, args.root, args.max_frames,
                           write_proposed=not args.no_proposed)
    rel = os.path.relpath(res["html"], args.root).replace(os.sep, "/")
    print(f"[slot-localize] wrote {rel} (+ slot_localization.json)")
    print(f"[slot-localize] verdict: "
          f"{'SAFE to update layout' if res['safety']['safe'] else 'NOT safe yet'} — "
          f"{res['safety']['reason']}")
    print(f"[slot-localize] open http://localhost:8000/{rel}")
    if args.open:
        import webbrowser
        webbrowser.open("file:///" + res["html"].replace(os.sep, "/"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
