#!/usr/bin/env python3
"""
gameplay_state.py — real gameplay-state classification for map ingestion.

Replaces the placeholder anchor-template check with a STRUCTURAL probe
derived by calibrate_source.py: live gameplay always renders the two
ult-chip rows (5 saturated solid chips per side at known positions) plus a
textured portrait next to each chip. Desk segments, replays wiped to
fullscreen, scoreboards, map transitions, player-cam walls and full-screen
graphics don't reproduce that structure at those exact positions.

States returned by classify_frame:
  gameplay       both chip rows present, portraits textured — hero-readable
  partial-hud    one side present / weak — HUD partly covered; not counted
  no-hud         chip structure absent (desk, transition, cams, graphics)
  replay         a per-source template OR the generalized OCR guard fired

Only 'gameplay' frames may feed the composition timeline; everything else
is recorded as a skipped observation with its reason.

The probe is COLOR-AGNOSTIC (any team color saturates) and works on dead
slots too: a death desaturates SOME chips, but 4-of-5 per side with the
portrait-texture backstop keeps classification stable through team fights.

GENERALIZED HIGHLIGHT/REPLAY GUARD (optional, second gate)
A highlight/POTG replay renders a complete, in-focus HUD — that is exactly
the failure mode the structural probe alone cannot catch, which is why the
project previously required a hand-cut template crop PER BROADCAST
PACKAGE (the layout's 'reject' markers). Passing ocr_read_fn + ocr_aliases
adds a second, template-free gate: any frame the structural probe already
calls 'gameplay' is re-OCR'd and checked for replay/highlight/intermission
banner text (data/heroes_aliases.json's ignore_keywords, the SAME
whole-word matching ocr_hud.classify_frame uses) — so a NEW broadcast's
highlight package is caught without anyone cutting a template for it
first. Omit both arguments (the default) and behavior is identical to
before this existed; the per-source templates keep working unchanged and
are still checked first (cheaper, no OCR engine required).
"""
from __future__ import annotations
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture  # noqa: E402
import ocr_hud  # noqa: E402 — only used when an OCR reader is injected;
                # importing it costs nothing (easyocr/tesseract/paddle are
                # all lazy-imported inside ocr_hud.make_reader, not here)

MIN_SAT_FRAC = 0.25       # fraction of chip box that must be saturated
# real portrait rows measure thousands of Laplacian variance; full-screen
# transition wipes that happen to saturate the chip boxes measure ~100
MIN_PORTRAIT_TEXTURE = 500.0
DEFAULT_MIN_CHIPS = 3     # per side (out of 5) — tolerant of deaths


def _sat_frac(hsv_box, sat_min: int, val_min: int) -> float:
    if hsv_box.size == 0:
        return 0.0
    return float(np.mean((hsv_box[:, :, 1] >= sat_min)
                         & (hsv_box[:, :, 2] >= val_min)))


def _texture(gray_box) -> float:
    if gray_box.size == 0:
        return 0.0
    return float(cv2.Laplacian(gray_box, cv2.CV_64F).var())


def probe_hud(frame_bgr, layout: dict) -> dict:
    """Count structurally-present chips per side + portrait texture.

    `layout` must already be scaled to the frame size (use
    capture.scale_layout_to_frame) and carry the 'hud_probe' block written
    by calibrate_source.py."""
    probe = layout.get("hud_probe") or {}
    sat_min = probe.get("sat_min", 110)
    val_min = probe.get("val_min", 90)
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    fh, fw = gray.shape[:2]
    out = {"chips": {}, "portrait_tex": {}}
    for side in ("a", "b"):
        boxes = probe.get(f"chips_{side}") or []
        n = 0
        for (x, y, w, h) in boxes:
            if x < 0 or y < 0 or x + w > fw or y + h > fh:
                continue
            if _sat_frac(hsv[y:y + h, x:x + w], sat_min, val_min) \
                    >= MIN_SAT_FRAC:
                n += 1
        out["chips"][side] = n
        texs = []
        for (x, y, w, h) in layout.get(f"slots_{side}") or []:
            if x < 0 or y < 0 or x + w > fw or y + h > fh:
                continue
            texs.append(_texture(gray[y:y + h, x:x + w]))
        out["portrait_tex"][side] = (float(np.median(texs)) if texs else 0.0)
    return out


def ocr_guard(frame_bgr, read_fn, aliases: dict) -> tuple[str | None, str]:
    """Re-check a frame the structural probe already called 'gameplay'.

    Returns (None, '') when clean (no override), or ('replay', reason)
    when replay/highlight/intermission banner text is found. Any read_fn
    failure is swallowed — the OCR guard can only ever ADD a rejection,
    never crash a run or block the structural verdict it's layered on."""
    try:
        items = read_fn(frame_bgr)
    except Exception:
        return None, ""
    scene, _hits, reason = ocr_hud.classify_frame(items, aliases)
    if scene in ("replay", "highlight", "intermission"):
        return "replay", f"OCR guard: {reason}"
    return None, ""


def classify_frame(frame_bgr, layout: dict,
                   min_chips: int = DEFAULT_MIN_CHIPS,
                   ocr_read_fn=None, ocr_aliases: dict | None = None
                   ) -> tuple[str, str]:
    """(state, reason) for one frame against a scaled layout.

    ocr_read_fn/ocr_aliases are optional — see the module docstring's
    "GENERALIZED HIGHLIGHT/REPLAY GUARD" section. Both must be given for
    the OCR guard to run; either omitted (the default) reproduces the
    exact prior behavior."""
    # reject markers (HIGHLIGHTS banner etc.) + optional replay marker are
    # checked FIRST — a highlight replay renders a complete HUD and would
    # otherwise pass the structural probe.
    gray = None
    cache = layout.setdefault("_gs_cache", {})
    if "rejects" not in cache:
        try:
            cache["rejects"] = capture._load_reject_markers(layout)
        except (FileNotFoundError, ValueError):
            cache["rejects"] = []
    rejects = cache["rejects"]
    if rejects:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        reason = capture.reject_reason(gray, rejects)
        if reason is not None:
            return "replay", reason
    if "replay" not in cache:
        try:
            cache["replay"] = capture._load_template(layout, "replay")
        except FileNotFoundError:
            cache["replay"] = None
    replay = cache["replay"]
    if replay is not None:
        if gray is None:
            gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        score = capture.region_score(gray, replay)
        if score >= replay["min_score"]:
            return "replay", f"replay marker {score:.2f}"

    p = probe_hud(frame_bgr, layout)
    ca, cb = p["chips"].get("a", 0), p["chips"].get("b", 0)
    ta = p["portrait_tex"].get("a", 0.0)
    tb = p["portrait_tex"].get("b", 0.0)
    detail = (f"chips a:{ca}/5 b:{cb}/5 "
              f"tex a:{ta:.0f} b:{tb:.0f}")
    if ca >= min_chips and cb >= min_chips \
            and ta >= MIN_PORTRAIT_TEXTURE and tb >= MIN_PORTRAIT_TEXTURE:
        if ocr_read_fn is not None and ocr_aliases is not None:
            guard_state, guard_reason = ocr_guard(
                frame_bgr, ocr_read_fn, ocr_aliases)
            if guard_state is not None:
                return guard_state, guard_reason
        return "gameplay", detail
    if (ca >= min_chips or cb >= min_chips) and max(ta, tb) >= \
            MIN_PORTRAIT_TEXTURE:
        return "partial-hud", detail
    return "no-hud", detail


def side_hue(frame_bgr, layout: dict, side: str) -> float | None:
    """Median hue of one side's chip row — the team-color tracking signal.

    Stable while the same team holds that screen side; a persistent jump
    across a round boundary (with a matching comp crossover) is the
    side-swap evidence used by ingest_map."""
    probe = layout.get("hud_probe") or {}
    boxes = probe.get(f"chips_{side}") or []
    if not boxes:
        return None
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    sat_min = probe.get("sat_min", 110)
    val_min = probe.get("val_min", 90)
    fh, fw = hsv.shape[:2]
    hues = []
    for (x, y, w, h) in boxes:
        if x < 0 or y < 0 or x + w > fw or y + h > fh:
            continue
        box = hsv[y:y + h, x:x + w]
        m = (box[:, :, 1] >= sat_min) & (box[:, :, 2] >= val_min)
        if m.sum() >= 10:
            hues.append(float(np.median(box[:, :, 0][m])))
    if not hues:
        return None
    return float(np.median(hues))
