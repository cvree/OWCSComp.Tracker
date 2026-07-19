#!/usr/bin/env python3
"""
ocr_hud.py — Phase 1 of the vision upgrade: OCR + dynamic HUD diagnostics.

    py pipeline/ocr_hud.py --run owcs-8c105lnzlam_000600_000630 \
        --layout layouts/owcs_8c105lnzlam.json --engine easyocr --open

DESIGN LINEAGE (patterns only — zero code copied)
  * OWTracker (krpouncy, MIT): OCR at known 1080p HUD regions + a browser
    review surface. We keep OCR region-scoped and reviewed-in-browser.
  * overtrack-cv (AGPL — ARCHITECTURE ONLY, no code/assets): a) classify the
    scene BEFORE extracting anything, b) one processor per concern, c) every
    processor runs standalone against sample frames with visual debug output.
  * OverFast API: hero display names — bundled as a static offline copy in
    data/heroes_aliases.json (no live API, $0).
  * FACEIT: NOT used here. Match/team metadata only, and never for comps.

WHAT THIS DOES (diagnostics + candidates ONLY)
  1. Per frame: full-frame OCR, then scene classification
     (gameplay / replay-highlight-potg / intermission / unknown) from
     ignore-keywords — these frames are flagged "ignore", never trusted.
  2. Team-name candidates from left/right top zones (fractional, resolution
     independent; overridable per layout via "ocr_zones").
  3. Hero-text candidates: OCR words inside/near each layout slot box,
     normalized through data/heroes_aliases.json (exact alias then fuzzy
     difflib match).
  4. Layout sanity: flags slot boxes that OCR says contain TEXT — a portrait
     box containing readable text is exactly the "crops hit UI/header"
     failure mode.
  5. Writes ONLY reports/auto/<run>/ocr_hud.json + ocr_hud.html +
     ocr_hud/ annotated PNGs. Candidate output; nothing is promoted,
     no DB/template/layout writes, no FACEIT.

OCR ENGINES (all optional, injectable for tests)
  --engine easyocr    (default; pip install easyocr — first run downloads
                       its model once, then fully offline)
  --engine tesseract  (pip install pytesseract + the tesseract binary)
  --engine paddle     (pip install paddleocr paddlepaddle)
  --engine none       (no OCR — page still renders with zones + instructions)
Tests inject a fake reader; no engine or model download is ever needed
offline. YOLO is deliberately NOT used yet — this phase exists to measure
whether OCR + layout methods suffice before any training is justified.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import html as _html
import json
import os
import re
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
ALIASES_PATH = os.path.join(DEFAULT_ROOT, "data", "heroes_aliases.json")
FRAME_EXTS = (".png", ".jpg", ".jpeg", ".webp")
MAX_FRAMES_DEFAULT = 12
FUZZY_CUTOFF = 0.78          # difflib ratio for hero-name fuzzy match
SLOT_PAD_FRAC = 0.35         # how far below/around a slot box hero text may sit
MIN_OCR_CONF = 0.30          # OCR results under this are shown but not matched

# Fractional zones (of frame w/h). A layout may override via "ocr_zones".
DEFAULT_ZONES = {
    "team_left":  [0.01, 0.00, 0.30, 0.09],   # x, y, w, h fractions
    "team_right": [0.69, 0.00, 0.30, 0.09],
    "center":     [0.20, 0.30, 0.60, 0.40],   # REPLAY/POTG/VICTORY splash area
    "top_strip":  [0.00, 0.00, 1.00, 0.16],   # whole HUD strip (slots live here)
}


# ------------------------------------------------------------------ aliases
def load_aliases(path: str = ALIASES_PATH) -> dict:
    """{'alias_map': {ALIAS->hero_id}, 'names': {id->display}, 'ignore':[...]}"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        data = {"heroes": {}, "ignore_keywords": []}
    alias_map: dict[str, str] = {}
    names: dict[str, str] = {}
    for hid, h in data.get("heroes", {}).items():
        names[hid] = h.get("name", hid)
        for a in h.get("aliases", []) + [hid]:
            alias_map[_norm_text(a)] = hid
    raw_kw = data.get("ignore_keywords", [])
    if isinstance(raw_kw, dict):          # categorized (current format)
        kw_cats = {c: [_norm_text(k) for k in v] for c, v in raw_kw.items()}
    else:                                 # legacy flat list -> best-effort
        kw_cats = {"intermission": [_norm_text(k) for k in raw_kw]}
    return {"alias_map": alias_map, "names": names, "kw_cats": kw_cats,
            "ignore": [k for v in kw_cats.values() for k in v]}


def _norm_text(s: str) -> str:
    """Uppercase, collapse to A-Z0-9+space. 'D.Va!' -> 'DVA'."""
    s = re.sub(r"[^A-Za-z0-9 ]+", "", s.upper())
    return re.sub(r"\s+", " ", s).strip()


FUZZY_MARGIN = 0.08          # best-vs-runner-up ratio gap to accept fuzzy


def match_hero(text: str, aliases: dict) -> dict:
    """Normalize OCR text to a hero id, with method + ambiguity handling.

    Returns {"hero": id|None, "quality": float, "method":
             exact|word|prefix|fuzzy|none, "reason": str}.
    Methods, in priority order:
      exact   whole normalized string is a known alias        (q = 1.0)
      word    one word of the string is an alias, len>=3      (q = 0.95)
      prefix  string (len>=3) is a prefix of exactly ONE
              hero's aliases — catches truncations WID/WIN    (q = 0.9)
      fuzzy   difflib >= FUZZY_CUTOFF AND beats the best
              OTHER-hero candidate by FUZZY_MARGIN            (q = ratio)
    Ambiguous prefix/fuzzy (two different heroes both fit) returns None
    with the reason naming both — never a silent coin-flip.
    """
    none = lambda r: {"hero": None, "quality": 0.0,      # noqa: E731
                      "method": "none", "reason": r}
    t = _norm_text(text)
    if not t or len(t) < 2:
        return none("too short")
    amap = aliases["alias_map"]
    if t in amap:
        return {"hero": amap[t], "quality": 1.0, "method": "exact",
                "reason": t}
    for w in t.split():
        if w in amap and len(w) >= 3:
            return {"hero": amap[w], "quality": 0.95, "method": "word",
                    "reason": w}
    if len(t) >= 3:
        pref = {amap[a] for a in amap if a.startswith(t)}
        if len(pref) == 1:
            return {"hero": pref.pop(), "quality": 0.9, "method": "prefix",
                    "reason": f"prefix '{t}'"}
        if len(pref) > 1:
            return none("ambiguous prefix: "
                        + "/".join(sorted(pref)))
    ranked = sorted(((difflib.SequenceMatcher(None, t, a).ratio(), a)
                     for a in amap), reverse=True)[:6]
    if not ranked or ranked[0][0] < FUZZY_CUTOFF:
        return none("no fuzzy match")
    best_r, best_a = ranked[0]
    best_h = amap[best_a]
    other = next(((r, a) for r, a in ranked[1:] if amap[a] != best_h), None)
    if other and best_r - other[0] < FUZZY_MARGIN:
        return none(f"ambiguous fuzzy: {best_h}/{amap[other[1]]} "
                    f"({best_r:.2f} vs {other[0]:.2f})")
    return {"hero": best_h, "quality": best_r, "method": "fuzzy",
            "reason": f"'{t}'~'{best_a}' {best_r:.2f}"}


def classify_frame(ocr_items: list[dict], aliases: dict):
    """Five-state scene class: gameplay / replay / highlight / intermission /
    unknown. Returns (state, hits, reason). Whole-word matching only —
    ROUND never fires inside ROUNDTWO. Non-gameplay states are all ignored
    for comps; 'unknown' means caution keywords (PAUSED/ROUND/...) were seen
    so the frame is uncertain rather than provably non-gameplay.
    """
    hits = []
    for it in ocr_items:
        t = f" {_norm_text(it['text'])} "
        for cat, kws in aliases["kw_cats"].items():
            for kw in kws:
                if kw and f" {kw} " in t:      # whole-word boundary match
                    hits.append({"keyword": kw, "category": cat,
                                 "text": it["text"], "box": it["box"]})
    if not hits:
        return "gameplay", hits, "no ignore keywords found"
    cats = {h["category"] for h in hits}
    for state in ("replay", "highlight", "intermission"):
        if state in cats:
            kws = sorted({h["keyword"] for h in hits
                          if h["category"] == state})
            return state, hits, f"{state} keyword(s): {', '.join(kws)}"
    kws = sorted({h["keyword"] for h in hits})
    return "unknown", hits, f"caution keyword(s): {', '.join(kws)}"


# -------------------------------------------------------------- OCR engines
def make_reader(engine: str):
    """Return read_fn(frame_bgr) -> [{'text','conf','box':[x,y,w,h]}].

    Raises RuntimeError with an install hint if the engine isn't available.
    """
    if engine == "none":
        return lambda frame: []
    if engine == "easyocr":
        try:
            import easyocr
        except ImportError:
            raise RuntimeError(
                "easyocr not installed — py -m pip install easyocr")
        reader = easyocr.Reader(["en"], gpu=False, verbose=False)

        def read(frame):
            out = []
            for box, text, conf in reader.readtext(frame):
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                out.append({"text": text, "conf": float(conf),
                            "box": [int(min(xs)), int(min(ys)),
                                    int(max(xs) - min(xs)),
                                    int(max(ys) - min(ys))]})
            return out
        return read
    if engine == "tesseract":
        try:
            import pytesseract
        except ImportError:
            raise RuntimeError("pytesseract not installed — py -m pip "
                               "install pytesseract (plus the tesseract "
                               "binary: https://github.com/UB-Mannheim/"
                               "tesseract/wiki)")

        def read(frame):
            d = pytesseract.image_to_data(
                frame, output_type=pytesseract.Output.DICT)
            out = []
            for i, txt in enumerate(d["text"]):
                if not txt.strip():
                    continue
                conf = float(d["conf"][i]) / 100.0
                if conf < 0:
                    continue
                out.append({"text": txt, "conf": conf,
                            "box": [d["left"][i], d["top"][i],
                                    d["width"][i], d["height"][i]]})
            return out
        return read
    if engine == "paddle":
        try:
            from paddleocr import PaddleOCR
        except ImportError:
            raise RuntimeError("paddleocr not installed — py -m pip install "
                               "paddleocr paddlepaddle")
        ocr = PaddleOCR(use_angle_cls=False, lang="en", show_log=False)

        def read(frame):
            out = []
            for line in (ocr.ocr(frame, cls=False) or []):
                for box, (text, conf) in (line or []):
                    xs = [p[0] for p in box]
                    ys = [p[1] for p in box]
                    out.append({"text": text, "conf": float(conf),
                                "box": [int(min(xs)), int(min(ys)),
                                        int(max(xs) - min(xs)),
                                        int(max(ys) - min(ys))]})
            return out
        return read
    raise RuntimeError(f"unknown engine '{engine}'")


# ----------------------------------------------------------- geometry utils
def zone_px(frac, fw: int, fh: int) -> list[int]:
    x, y, w, h = frac
    return [int(x * fw), int(y * fh), int(w * fw), int(h * fh)]


def _overlap(a, b) -> float:
    """Fraction of box a's area inside box b."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    return (ix * iy) / float(aw * ah) if aw > 0 and ah > 0 else 0.0


def _center_in(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    cx, cy = ax + aw / 2.0, ay + ah / 2.0
    return bx <= cx <= bx + bw and by <= cy <= by + bh


def slot_boxes(layout: dict, fw: int, fh: int) -> list[dict]:
    """Scaled slot boxes + a padded 'text zone' under/around each slot."""
    import capture
    scaled, _ = capture.scale_layout_to_frame(layout, fw, fh)
    out = []
    for side in ("a", "b"):
        for i, (x, y, w, h) in enumerate(scaled.get(f"slots_{side}", []), 1):
            x, y, w, h = int(x), int(y), int(w), int(h)
            px, py = int(w * SLOT_PAD_FRAC), int(h * SLOT_PAD_FRAC)
            out.append({"slot": f"{side}{i}", "box": [x, y, w, h],
                        "text_zone": [max(0, x - px), max(0, y - py),
                                      min(fw, x + w + px) - max(0, x - px),
                                      min(fh, y + h + int(h * 1.2))
                                      - max(0, y - py)]})
    return out


# ------------------------------------------------------------ frame analysis
def analyze_frame(frame_bgr, ocr_items: list[dict], layout: dict | None,
                  aliases: dict, known_teams: list[str]) -> dict:
    fh, fw = frame_bgr.shape[:2]
    zones_frac = dict(DEFAULT_ZONES)
    zones_source = "default"
    if layout and isinstance(layout.get("ocr_zones"), dict):
        for k, v in layout["ocr_zones"].items():
            ok = (isinstance(v, (list, tuple)) and len(v) == 4
                  and all(isinstance(n, (int, float)) for n in v)
                  and 0 <= v[0] < 1 and 0 <= v[1] < 1
                  and 0 < v[2] <= 1 and 0 < v[3] <= 1)
            if ok:                       # read-only: layout never edited
                zones_frac[k] = [float(n) for n in v]
                zones_source = "layout ocr_zones"
    zones = {k: zone_px(v, fw, fh) for k, v in zones_frac.items()}

    scene, ignore_hits, scene_reason = classify_frame(ocr_items, aliases)
    ignore_boxes = [tuple(h["box"]) for h in ignore_hits]
    purpose = ["ignore" if tuple(it["box"]) in ignore_boxes else "other"
               for it in ocr_items]

    def zone_texts(zname):
        return [it for it in ocr_items
                if it["conf"] >= MIN_OCR_CONF
                and _center_in(it["box"], zones[zname])]

    def team_candidate(zname):
        cands = sorted(zone_texts(zname), key=lambda i: -i["conf"])
        for it in cands:
            t = _norm_text(it["text"])
            if len(t) < 3 or t.isdigit():
                continue
            purpose[ocr_items.index(it)] = "team"
            matched = None
            if known_teams:
                close = difflib.get_close_matches(
                    t, [_norm_text(k) for k in known_teams], 1, 0.7)
                if close:
                    matched = close[0]
            return {"raw": it["text"], "normalized": t, "conf": it["conf"],
                    "box": it["box"], "known_team_match": matched}
        return None

    slots = slot_boxes(layout, fw, fh) if layout else []
    slot_results, contaminated = [], []
    for s in slots:
        in_zone = [it for it in ocr_items
                   if _overlap(it["box"], s["text_zone"]) > 0.5]
        in_box = [it for it in ocr_items
                  if _overlap(it["box"], s["box"]) > 0.5
                  and len(_norm_text(it["text"])) >= 3]
        if in_box:            # readable text INSIDE the portrait box = bad box
            contaminated.append(s["slot"])
        best_hero, best = None, None
        rejects = []
        for it in in_zone:
            m = match_hero(it["text"], aliases)
            if m["hero"]:
                purpose[ocr_items.index(it)] = "hero"
                if (best is None or m["quality"] * it["conf"]
                        > best["quality"] * best["conf"]):
                    best_hero = m["hero"]
                    best = {"text": it["text"], "conf": it["conf"],
                            "quality": m["quality"], "method": m["method"],
                            "box": it["box"]}
            elif m["reason"].startswith("ambiguous"):
                rejects.append({"text": it["text"], "reason": m["reason"]})
        slot_results.append({
            "slot": s["slot"], "box": s["box"], "text_zone": s["text_zone"],
            "texts": [{"text": i["text"], "conf": round(i["conf"], 3),
                       "box": i["box"]} for i in in_zone],
            "hero_candidate": best_hero,
            "hero_name": aliases["names"].get(best_hero),
            "evidence": best,
            "ambiguous": rejects,
            "box_contains_text": s["slot"] in contaminated,
        })

    comp = {"a": [], "b": []}
    for r in slot_results:
        if r["hero_candidate"]:
            comp[r["slot"][0]].append(r["hero_candidate"])

    # team candidates must be resolved BEFORE purposes are serialized,
    # because team_candidate() tags matched items with purpose='team'
    team_left = team_candidate("team_left")
    team_right = team_candidate("team_right")

    return {
        "size": [fw, fh], "scene": scene, "ignore": scene != "gameplay",
        "ignore_reason": scene_reason if scene != "gameplay" else None,
        "ignore_hits": ignore_hits, "zones": zones,
        "zones_source": zones_source,
        "ocr_purposes": [{"text": it["text"], "conf": round(it["conf"], 3),
                          "box": it["box"], "purpose": purpose[i]}
                         for i, it in enumerate(ocr_items)],
        "team_left": team_left,
        "team_right": team_right,
        "slots": slot_results,
        "comp_candidates": comp,
        "contaminated_slots": contaminated,
        "ocr_count": len(ocr_items),
    }


def stable_comps(frames: list[dict]) -> dict:
    """Per-side hero->seen-count over NON-ignored frames (stability signal)."""
    tally = {"a": {}, "b": {}}
    used = 0
    for f in frames:
        if f["analysis"]["ignore"]:
            continue
        used += 1
        for side in ("a", "b"):
            for h in f["analysis"]["comp_candidates"][side]:
                tally[side][h] = tally[side].get(h, 0) + 1
    return {"frames_used": used, "tally": tally}


# ------------------------------------------------------------------ verdict
V_MIN_OCR_ITEMS = 3.0        # avg OCR items/frame below this = weak signal
V_CONTAM_FRAC = 0.30         # >=30% slots with text-in-box = layout wrong
V_HERO_COVERAGE = 0.60       # >=60% slots w/ hero candidate = OCR viable


def build_verdict(frames: list[dict]) -> dict:
    """Data-driven 'what to do next' recommendation from this run's frames.

    One of (in priority order):
      no-signal      not enough OCR text found at all
      fix-layout     slot boxes contain readable text (boxes on UI, not
                     portraits) — fix coordinates before anything else
      ocr-heroes     hero text under slots is rich: OCR-assisted comps viable
      ocr-gating     OCR finds teams / classifies scenes but not hero names:
                     use OCR for frame gating + team identity, keep portrait
                     templates for comps
    Every verdict carries the measured numbers so it's checkable, not vibes.
    """
    n = len(frames)
    if n == 0:
        return {"verdict": "no-signal", "label": "not enough OCR signal",
                "detail": "no frames analyzed — run auto capture first",
                "metrics": {}}
    gameplay = [f for f in frames if not f["analysis"]["ignore"]]
    avg_items = sum(f["analysis"]["ocr_count"] for f in frames) / n
    slots_total = sum(len(f["analysis"]["slots"]) for f in gameplay)
    contam = sum(len(f["analysis"]["contaminated_slots"]) for f in gameplay)
    hero_hits = sum(1 for f in gameplay for s in f["analysis"]["slots"]
                    if s["hero_candidate"])
    teams = sum(1 for f in gameplay
                if f["analysis"]["team_left"] or f["analysis"]["team_right"])
    ignored = n - len(gameplay)
    metrics = {
        "frames": n, "frames_ignored": ignored,
        "avg_ocr_items_per_frame": round(avg_items, 2),
        "slot_checks": slots_total,
        "contaminated_slot_checks": contam,
        "contamination_frac": round(contam / slots_total, 3)
        if slots_total else None,
        "hero_candidate_slot_checks": hero_hits,
        "hero_coverage_frac": round(hero_hits / slots_total, 3)
        if slots_total else None,
        "frames_with_team_candidate": teams,
    }
    if avg_items < V_MIN_OCR_ITEMS:
        return {"verdict": "no-signal", "label": "not enough OCR signal",
                "detail": (f"avg {avg_items:.1f} OCR items/frame (< "
                           f"{V_MIN_OCR_ITEMS:.0f}) — check the engine, frame "
                           "resolution (720p+ recommended), or whether these "
                           "frames are gameplay at all"),
                "metrics": metrics}
    if slots_total and contam / slots_total >= V_CONTAM_FRAC:
        return {"verdict": "fix-layout", "label": "layout boxes likely wrong",
                "detail": (f"{contam}/{slots_total} slot checks found "
                           "readable TEXT inside the portrait box — the "
                           "layout is cropping UI/headers. Fix the slot "
                           "coordinates (layout.html / crops.html) before "
                           "trusting any crops or templates"),
                "metrics": metrics}
    if slots_total and hero_hits / slots_total >= V_HERO_COVERAGE:
        return {"verdict": "ocr-heroes",
                "label": "OCR useful for hero detection",
                "detail": (f"hero text matched in {hero_hits}/{slots_total} "
                           "slot checks — OCR-assisted comp candidates are "
                           "viable and can cross-check portrait matching"),
                "metrics": metrics}
    return {"verdict": "ocr-gating",
            "label": "OCR useful for team/frame gating only",
            "detail": (f"hero coverage {hero_hits}/{slots_total or '?'} is "
                       f"low, but team names hit {teams}/{len(gameplay)} "
                       f"gameplay frames and {ignored}/{n} frames were "
                       "correctly gated — use OCR for scene-ignoring + team "
                       "identity, keep portrait templates for comps"),
            "metrics": metrics}


# ---------------------------------------------------------------- rendering
_E = lambda v: _html.escape(str(v if v is not None else "—"))    # noqa: E731

_ZCOLORS = {"team_left": (60, 200, 255), "team_right": (60, 200, 255),
            "center": (200, 120, 255), "top_strip": (120, 120, 120)}


_PCOLORS = {"ignore": (60, 60, 230), "team": (60, 200, 255),
            "hero": (230, 200, 80), "other": (80, 220, 120)}


def annotate(frame_bgr, analysis: dict, ocr_items: list[dict]):
    img = frame_bgr.copy()
    for name, z in analysis["zones"].items():
        c = _ZCOLORS.get(name, (150, 150, 150))
        cv2.rectangle(img, (z[0], z[1]), (z[0] + z[2], z[1] + z[3]), c, 1)
        cv2.putText(img, name, (z[0] + 3, z[1] + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, c, 1, cv2.LINE_AA)
    for it in analysis["ocr_purposes"]:     # OCR boxes colored by purpose
        x, y, w, h = it["box"]
        cv2.rectangle(img, (x, y), (x + w, y + h),
                      _PCOLORS.get(it["purpose"], (80, 220, 120)),
                      2 if it["purpose"] != "other" else 1)
    for s in analysis["slots"]:                         # layout slots
        x, y, w, h = s["box"]
        col = (60, 60, 230) if s["box_contains_text"] else (43, 169, 255)
        cv2.rectangle(img, (x, y), (x + w, y + h), col, 2)
        tz = s["text_zone"]                              # hero-text zone: cyan
        cv2.rectangle(img, (tz[0], tz[1]),
                      (tz[0] + tz[2], tz[1] + tz[3]), (230, 200, 80), 1)
        if s["hero_candidate"]:
            cv2.putText(img, s["hero_candidate"], (x, y + h + 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (230, 200, 80), 1,
                        cv2.LINE_AA)
    if analysis["ignore"]:
        cv2.putText(img, f"IGNORE: {analysis['scene']}", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, (60, 60, 230), 2,
                    cv2.LINE_AA)
        cv2.putText(img, analysis.get("ignore_reason") or "", (20, 66),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (60, 60, 230), 1,
                    cv2.LINE_AA)
    return img


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
th,td{border:1px solid var(--line);padding:6px 10px;text-align:left;
vertical-align:top}th{color:var(--muted)}
.pill{display:inline-block;color:#fff;border-radius:999px;padding:1px 9px;
font-family:"Chakra Petch",sans-serif;font-weight:700;font-size:.62rem}
.sc-gameplay{background:#1f7a48}.sc-replay{background:#a03040}
.sc-highlight{background:#c04860}.sc-intermission{background:#5a3f8a}
.sc-unknown{background:#8a5a1f}
.p-ignore{color:#ff5c64}.p-team{color:#ffc83c}.p-hero{color:#50c8e6}
.p-other{color:#50dc78}
.verdict{border:1px solid rgba(46,189,107,.5);border-left:6px solid var(--ok);background:rgba(46,189,107,.08);padding:14px 18px;border-radius:10px;margin:16px 0}
.verdict.v-fix-layout,.verdict.v-no-signal{border-color:rgba(255,92,100,.5);border-left-color:var(--bad);background:rgba(255,92,100,.08)}
.ok{color:var(--ok);font-weight:700}.bad{color:var(--bad);font-weight:700}
.muted{color:var(--muted)}
.frame-block{border:1px solid var(--line);border-radius:12px;padding:12px;
margin:14px 0;background:var(--surface)}
.frame-block img{width:100%;max-width:1000px;border:1px solid var(--line);
border-radius:8px}
.note{border:1px solid var(--line);border-left:4px solid var(--muted);
background:rgba(255,255,255,.03);padding:8px 12px;border-radius:8px;
margin:10px 0;font-size:.8rem;color:var(--muted)}
.legend span{margin-right:16px}
"""


def render_html(run: str, meta: dict, frames: list[dict], stable: dict,
                aliases: dict, verdict: dict) -> str:
    rows = []
    for f in frames:
        a = f["analysis"]
        by_p = {}
        for it in a.get("ocr_purposes", []):
            by_p.setdefault(it["purpose"], []).append(it["text"])
        purpose_line = " · ".join(
            f"<span class='p-{p}'>{p}: {_E(', '.join(ts[:6]))}"
            f"{'…' if len(ts) > 6 else ''}</span>"
            for p, ts in (("ignore", by_p.get("ignore")),
                          ("team", by_p.get("team")),
                          ("hero", by_p.get("hero")),
                          ("other", by_p.get("other"))) if ts)
        slot_rows = "".join(
            f"<tr><td>{_E(s['slot'])}</td>"
            f"<td>{'<span class=bad>TEXT IN BOX</span>' if s['box_contains_text'] else '<span class=ok>clean</span>'}</td>"
            f"<td>{_E(s['hero_name'] or '')} "
            f"{('<code>' + _E(s['hero_candidate']) + '</code> <span class=muted>' + _E((s['evidence'] or {}).get('method', '')) + '</span>') if s['hero_candidate'] else '—'}</td>"
            f"<td class='muted'>{_E('; '.join(t['text'] for t in s['texts']) or '—')}"
            + ("".join(f"<div class='bad'>? {_E(r['text'])}: "
                       f"{_E(r['reason'])}</div>"
                       for r in s.get('ambiguous', [])))
            + "</td></tr>"
            for s in a["slots"])
        tl, tr = a["team_left"], a["team_right"]
        rows.append(
            f"<div class='frame-block'><b>{_E(f['frame'])}</b> "
            f"<span class='pill sc-{_E(a['scene'])}'>{_E(a['scene'])}</span>"
            + (" <span class='bad'>IGNORED for comps — "
               f"{_E(a.get('ignore_reason'))}</span>" if a["ignore"] else "")
            + f"<div class='muted'>OCR items: {a['ocr_count']} · team L: "
            f"<b>{_E(tl['raw'] if tl else None)}</b> · team R: "
            f"<b>{_E(tr['raw'] if tr else None)}</b> · zones: "
            f"{_E(a.get('zones_source', 'default'))}</div>"
            + (f"<div class='muted'>{purpose_line}</div>"
               if purpose_line else "")
            + (f"<img src='{_E(f['annotated'])}' loading='lazy'>"
               if f.get("annotated") else "")
            + (f"<table><tr><th>slot</th><th>layout box</th>"
               f"<th>hero candidate</th><th>OCR text near slot</th></tr>"
               f"{slot_rows}</table>" if a["slots"] else
               "<p class='muted'>no layout slots (pass --layout)</p>")
            + "</div>")

    tally_rows = "".join(
        f"<tr><td>{_E(side.upper())}</td><td>"
        + (", ".join(f"{_E(aliases['names'].get(h, h))} "
                     f"<code>{n}/{stable['frames_used']}</code>"
                     for h, n in sorted(t.items(), key=lambda kv: -kv[1]))
           or "<span class='muted'>none</span>") + "</td></tr>"
        for side, t in stable["tally"].items())

    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{_E(run)} — OCR HUD diagnostics</title>"
        f"<style>{_CSS}</style></head><body>"
        f"<h1>OCR HUD diagnostics — {_E(run)}</h1>"
        "<p class='note'>CANDIDATES ONLY — nothing here is promoted or "
        "written to the DB. This page measures whether OCR + layout methods "
        "are sufficient before any YOLO training is considered.</p>"
        f"<div class='verdict v-{_E(verdict['verdict'])}'>"
        f"<b>What to do next: {_E(verdict['label'])}</b><br>"
        f"{_E(verdict['detail'])}<br><small class='muted'>"
        + " · ".join(f"{_E(k)}={_E(v)}" for k, v in
                     verdict.get("metrics", {}).items())
        + "</small></div>"
        "<h2>Run</h2><table>"
        + "".join(f"<tr><th>{_E(k)}</th><td>{_E(v)}</td></tr>"
                  for k, v in meta.items())
        + "</table>"
        f"<h2>Stable comp signal (non-ignored frames: "
        f"{stable['frames_used']})</h2>"
        f"<table><tr><th>side</th><th>hero · frames seen</th></tr>"
        f"{tally_rows}</table>"
        "<p class='note'>A hero seen in most non-ignored frames is a stable "
        "candidate; one-frame wonders are OCR noise.</p>"
        "<h2>Legend</h2><div class='legend note'>"
        "<span style='color:#ffa92b'>■ layout slot box</span>"
        "<span style='color:#e63c3c'>■ slot box containing TEXT (bad box)"
        "</span><span style='color:#50c8e6'>■ hero-text zone</span>"
        "<span style='color:#50dc78'>■ OCR: other text</span>"
        "<span style='color:#e63c3c'>■ OCR: ignore keyword</span>"
        "<span style='color:#ffc83c'>■ OCR: team name / team zones</span>"
        "<span style='color:#50c8e6'>■ OCR: hero text</span>"
        "<span style='color:#c878ff'>■ center splash zone</span></div>"
        "<h2>Frames</h2>" + "".join(rows) +
        "<h2>Reading this page</h2><div class='note'>"
        "1) Frames marked IGNORED (replay/highlight/intermission) must never "
        "feed comps — this is the dynamic replacement for the anchor/replay "
        "template gate. 2) Red slot boxes contain readable text: the layout "
        "box is on UI, not a portrait — fix those coordinates first. "
        "3) If hero candidates under slots are rich, OCR-assisted comps are "
        "viable; if slots show player tags only, portraits stay the source "
        "and OCR's job is scene-gating + team identity.</div>"
        "<p><a href='slot_localization.html'>slot localization</a> · "
        "<a href='vision_dashboard.html'>vision dashboard</a> · "
        "<a href='index.html'>run report</a> · "
        "<a href='../../../runs.html'>all runs</a></p>"
        "</body></html>")


# ----------------------------------------------------------------- pipeline
def _load_layout(path: str | None) -> dict | None:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _known_teams(root: str) -> list[str]:
    """Read-only team names from the DB, if it exists. Never writes."""
    db_path = os.path.join(root, "data", "owcs.sqlite")
    if not os.path.isfile(db_path):
        return []
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            return [r[0] for r in
                    con.execute("SELECT name FROM teams").fetchall()]
        finally:
            con.close()
    except Exception:
        return []


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


def run_diagnostics(run: str, layout_path: str | None = None,
                    root: str = DEFAULT_ROOT, engine: str = "easyocr",
                    max_frames: int = MAX_FRAMES_DEFAULT,
                    read_fn=None, aliases_path: str | None = None) -> dict:
    """Generate ocr_hud.json + ocr_hud.html for one run. Read-only otherwise.

    read_fn is injectable for offline tests (takes a BGR frame, returns OCR
    items). When read_fn is given, `engine` is only recorded, not loaded.
    """
    root = os.path.abspath(root)
    if not HAS_CV:
        raise RuntimeError("cv2/numpy required — py -m pip install "
                           "opencv-python numpy")
    report_dir = os.path.join(root, "reports", "auto", run)
    out_dir = os.path.join(report_dir, "ocr_hud")
    os.makedirs(out_dir, exist_ok=True)

    aliases = load_aliases(aliases_path or
                           os.path.join(root, "data", "heroes_aliases.json"))
    if not aliases["alias_map"]:
        aliases = load_aliases(ALIASES_PATH)     # repo copy fallback

    engine_note = ""
    if read_fn is None:
        try:
            read_fn = make_reader(engine)
        except RuntimeError as e:                # missing engine: still render
            engine_note = str(e)
            read_fn = lambda f: []               # noqa: E731
            engine = f"{engine} (UNAVAILABLE)"

    layout = _load_layout(layout_path)
    teams = _known_teams(root)
    fdir = _frames_dir(root, run)
    frame_files = (sorted(f for f in os.listdir(fdir)
                          if f.lower().endswith(FRAME_EXTS))[:max_frames]
                   if fdir else [])

    frames_out: list[dict] = []
    for i, fn in enumerate(frame_files, 1):
        frame = cv2.imread(os.path.join(fdir, fn))
        if frame is None:
            continue
        items = read_fn(frame)
        analysis = analyze_frame(frame, items, layout, aliases, teams)
        ann_rel = None
        ann = annotate(frame, analysis, items)
        ann_abs = _safe_out(os.path.join(out_dir, f"{os.path.splitext(fn)[0]}"
                                         "_ocr.png"), out_dir)
        if cv2.imwrite(ann_abs, ann):
            ann_rel = os.path.relpath(ann_abs, report_dir).replace("\\", "/")
        print(f"[ocr-hud] [{i}/{len(frame_files)}] {fn}: "
              f"{analysis['scene']}, {analysis['ocr_count']} OCR items, "
              f"{len(analysis['contaminated_slots'])} contaminated slot(s)")
        frames_out.append({"frame": fn, "annotated": ann_rel,
                           "ocr": items, "analysis": analysis})

    stable = stable_comps(frames_out)
    verdict = build_verdict(frames_out)
    result = {
        "run": run, "engine": engine, "engineNote": engine_note,
        "verdict": verdict,
        "layout": layout_path, "framesDir": fdir,
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "candidate": True, "promoted": False,     # honesty markers
        "knownTeams": teams,
        "stable": stable,
        "frames": [{"frame": f["frame"], "annotated": f["annotated"],
                    "analysis": f["analysis"]} for f in frames_out],
    }
    json_path = os.path.join(report_dir, "ocr_hud.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)

    meta = {
        "engine": engine + (f" — {engine_note}" if engine_note else ""),
        "layout": layout_path or "(none — pass --layout for slot analysis)",
        "frames dir": fdir or "(no frames found — run auto capture first)",
        "frames analyzed": len(frames_out),
        "frames ignored": sum(1 for f in frames_out
                              if f["analysis"]["ignore"]),
        "known teams (read-only DB)": ", ".join(teams) or "none",
        "output": "candidates only — promoted: false",
        "verdict": f"{verdict['label']}",
    }
    html_path = os.path.join(report_dir, "ocr_hud.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(render_html(run, meta, frames_out, stable, aliases,
                             verdict))
    return {"html": html_path, "json": json_path, "frames": len(frames_out),
            "stable": stable, "verdict": verdict,
            "engineNote": engine_note}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="OCR + dynamic HUD diagnostics (candidates only)")
    ap.add_argument("--run", required=True)
    ap.add_argument("--layout", default=None)
    ap.add_argument("--engine", default="easyocr",
                    choices=["easyocr", "tesseract", "paddle", "none"])
    ap.add_argument("--max-frames", type=int, default=MAX_FRAMES_DEFAULT)
    ap.add_argument("--root", default=DEFAULT_ROOT, help=argparse.SUPPRESS)
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args(argv)

    res = run_diagnostics(args.run, args.layout, args.root, args.engine,
                          args.max_frames)
    rel = os.path.relpath(res["html"], args.root).replace(os.sep, "/")
    print(f"[ocr-hud] wrote {rel} (+ ocr_hud.json)")
    print(f"[ocr-hud] verdict: {res['verdict']['label']} — "
          f"{res['verdict']['detail']}")
    if res["engineNote"]:
        print(f"[ocr-hud] NOTE: {res['engineNote']}")
    print(f"[ocr-hud] open http://localhost:8000/{rel}")
    if args.open:
        import webbrowser
        webbrowser.open("file:///" + res["html"].replace(os.sep, "/"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
