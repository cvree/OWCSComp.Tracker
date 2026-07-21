#!/usr/bin/env python3
"""
detect_bans.py — generalized, OCR-based hero-ban detection.

Every hero ban in this project has always been TEXT: parsed out of a
FACEIT matchroom, or hand-picked by a human in fact-admin.html. No code
path has ever looked at a broadcast's pick/ban SCREEN. This module does,
and — deliberately — without hard-coding any one broadcast's ban-row
coordinates, so it works on "any overlay system":

  1. A frame counts as a pick/ban frame when its OCR text contains a
     'pickban' keyword (BAN, DRAFT, VETO, LOCKED IN, ... —
     data/heroes_aliases.json), the same whole-word matching
     ocr_hud.classify_frame already uses for replay/highlight detection.
  2. Inside those frames, every OCR hit that resolves to a hero name
     (ocr_hud.match_hero — exact/word/prefix/fuzzy, ambiguity-safe) and
     does NOT overlap one of the ten live pick/portrait slots (so a
     normal comp read is never mistaken for a ban) is a ban candidate.
  3. Side is assigned by which half of the frame the text sits in — the
     same left/right convention slots_a/slots_b already use, so it needs
     no per-broadcast calibration either.
  4. TEMPORAL CONSENSUS: a hero only becomes a confirmed ban for a side
     once the SAME hero has been read in >= MIN_AGREE_FRAMES separate
     pick/ban frames. A single OCR misread never becomes a ban.

Pure analysis, no DB writes (matches detect.py/gameplay_state.py/
team_identify.py) — ingest_map.py persists confirmed bans into hero_bans
(source='cv') and unresolved candidates into ingest_findings for review.
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture  # noqa: E402
import ocr_hud  # noqa: E402

MIN_AGREE_FRAMES = 2
MIN_OCR_CONF = 0.30
SLOT_OVERLAP_REJECT = 0.4   # OCR box overlapping a live slot this much = pick, not ban


def pickban_hits(ocr_items: list[dict], aliases: dict) -> list[dict]:
    """OCR items whose text contains a whole-word 'pickban' keyword."""
    kws = aliases.get("kw_cats", {}).get("pickban", [])
    hits = []
    for it in ocr_items:
        t = f" {ocr_hud._norm_text(it['text'])} "
        for kw in kws:
            if kw and f" {kw} " in t:
                hits.append({"keyword": kw, "text": it["text"],
                            "box": it["box"]})
                break
    return hits


def _overlaps_any(box: list[int], boxes: list, thresh: float) -> bool:
    return any(ocr_hud._overlap(box, b) > thresh for b in boxes)


def hero_candidates(ocr_items: list[dict], aliases: dict,
                    exclude_boxes: list | None = None,
                    min_conf: float = MIN_OCR_CONF) -> list[dict]:
    """OCR items -> resolved hero matches, skipping ones inside a live
    pick slot (those are comp picks, not bans) and low-confidence noise."""
    out = []
    for it in ocr_items:
        if it.get("conf", 0.0) < min_conf:
            continue
        if exclude_boxes and _overlaps_any(it["box"], exclude_boxes,
                                           SLOT_OVERLAP_REJECT):
            continue
        m = ocr_hud.match_hero(it["text"], aliases)
        if m["hero"]:
            out.append({"hero": m["hero"], "quality": m["quality"],
                        "conf": it["conf"], "box": it["box"],
                        "method": m["method"], "text": it["text"]})
    return out


def _side_of(box: list[int], fw: int) -> str:
    cx = box[0] + box[2] / 2.0
    return "a" if cx < fw / 2.0 else "b"


def _slot_boxes(layout: dict | None, fw: int, fh: int) -> list | None:
    if not layout or not fh:
        return None
    scaled, info = capture.scale_layout_to_frame(layout, fw, fh)
    if not info["ok"]:
        return None
    return list(scaled.get("slots_a") or []) + list(scaled.get("slots_b") or [])


def detect_bans_in_frames(ocr_per_frame: list[tuple], aliases: dict, fw: int,
                          layout: dict | None = None, fh: int | None = None,
                          min_agree: int = MIN_AGREE_FRAMES) -> dict:
    """Detect hero bans across a set of already-OCR'd frames.

    ocr_per_frame: [(t, ocr_items), ...] or [(t, ocr_items, evidence), ...]
    (evidence is an opaque tag — e.g. a crop/frame filename — carried
    through into the result for provenance; optional).

    Returns:
      {'a': [{'hero','confidence','n_frames','evidence_frames'}, ...],
       'b': [...],
       'unresolved': [{'hero','side','confidence','n_frames','reason'}, ...],
       'frames_scanned': int, 'pickban_frames': int}
    Confirmed entries (a/b) require consensus; everything short of that is
    unresolved, never silently promoted — same rule the swap/comp
    pipeline already follows.
    """
    exclude = _slot_boxes(layout, fw, fh) if fh else None
    votes: dict[str, dict[str, list[dict]]] = {"a": {}, "b": {}}
    pickban_frame_count = 0

    for entry in ocr_per_frame:
        t, items = entry[0], entry[1]
        evidence = entry[2] if len(entry) > 2 else None
        if not pickban_hits(items, aliases):
            continue
        pickban_frame_count += 1
        for cand in hero_candidates(items, aliases, exclude):
            side = _side_of(cand["box"], fw)
            votes[side].setdefault(cand["hero"], []).append({
                "t": t, "conf": cand["conf"], "quality": cand["quality"],
                "method": cand["method"], "text": cand["text"],
                "evidence": evidence,
            })

    result: dict = {"a": [], "b": [], "unresolved": [],
                    "frames_scanned": len(ocr_per_frame),
                    "pickban_frames": pickban_frame_count}
    for side in ("a", "b"):
        for hero, evs in votes[side].items():
            conf = sum(e["conf"] * e["quality"] for e in evs) / len(evs)
            item = {"hero": hero, "side": side, "confidence": round(conf, 3),
                    "n_frames": len(evs), "method": evs[0]["method"],
                    "evidence_frames": [e["t"] for e in evs][:5],
                    "evidence": next((e["evidence"] for e in evs
                                      if e["evidence"]), None)}
            if len(evs) >= min_agree:
                result[side].append(item)
            else:
                item["reason"] = (f"'{hero}' seen in only {len(evs)} "
                                  f"pick/ban frame(s) (< {min_agree} "
                                  "required for consensus)")
                result["unresolved"].append(item)
    for side in ("a", "b"):
        result[side].sort(key=lambda e: (-e["n_frames"], -e["confidence"]))
    return result
