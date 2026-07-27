#!/usr/bin/env python3
"""
map_identify.py — read the MAP and MODE off a broadcast, honestly (Phase 4).

OWCS overlays name the map twice: on the pre-map "next map" card and in the
in-game objective strip. Both are text, so both are OCR problems, and OCR on
a moving broadcast is noisy — which is exactly why this module is built the
same way `team_identify.py` is:

  * one candidate per frame, never a verdict from a single frame
  * a named winner only when >= MIN_AGREE_FRAMES frames agree
  * a tie between two plausible maps returns UNKNOWN with both names, never
    a coin flip
  * mode is derived from the resolved map's catalog row, NOT independently
    OCR'd — the catalog already knows Nepal is Control, and inventing a
    second, weaker signal for it could only ever disagree with itself

`UNKNOWN` is a first-class result. Every return value carries its evidence
(the raw OCR strings, the frames that voted, the match method) so an operator
reviewing a proposal can see why, and so `ingest_findings` can store it.

The OCR reader is always injected (`read_fn`: frame_bgr -> [{text, conf,
box}], the same contract `ocr_hud.make_reader` produces), so every behavior
here is testable offline with a fake reader.
"""
from __future__ import annotations

import difflib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ocr_hud  # noqa: E402

MIN_AGREE_FRAMES = 2      # frames that must agree before a map is named
FUZZY_CUTOFF = 0.74       # map names are long; a little looser than heroes
FUZZY_MARGIN = 0.08       # best must beat runner-up by this
MIN_OCR_CONF = 0.30

# Where map/mode text lives, as frame fractions. The objective strip sits
# top-center in every OWCS package; the map card is centered mid-screen.
DEFAULT_MAP_ZONES = {
    "objective_strip": [0.30, 0.00, 0.40, 0.12],
    "map_card": [0.15, 0.25, 0.70, 0.50],
}

# The mode words a broadcast prints. Used ONLY to corroborate a resolved
# map's catalog mode, and to report a disagreement — never to name a mode on
# its own (a bare "CONTROL" tells you nothing about WHICH control map).
MODE_WORDS = {
    "CONTROL": "Control", "ESCORT": "Escort", "PAYLOAD": "Escort",
    "HYBRID": "Hybrid", "PUSH": "Push", "FLASHPOINT": "Flashpoint",
    "CLASH": "Clash",
}


def _none(reason: str, **extra) -> dict:
    return {"map": None, "mode": None, "confidence": 0.0, "method": "none",
            "reason": reason, **extra}


def known_maps_from_db(con) -> list[dict]:
    """Read-only {'id','name','mode'} snapshot of the game_maps catalog."""
    return [{"id": r["id"], "name": r["name"], "mode": r["mode"]}
            for r in con.execute("SELECT id, name, mode FROM game_maps")]


def match_map(text: str, known_maps: list[dict]) -> dict:
    """Normalize one OCR string to a map id, honest about ambiguity.

    Returns {"map": id|None, "quality": float, "method":
             exact|contains|fuzzy|none, "reason": str}. Two maps tying never
    produces a silent guess — mirrors `ocr_hud.match_hero` and
    `team_identify.match_team` exactly.
    """
    t = ocr_hud._norm_text(text)
    if not t or len(t) < 3:
        return {"map": None, "quality": 0.0, "method": "none",
                "reason": "too short"}
    if not known_maps:
        return {"map": None, "quality": 0.0, "method": "none",
                "reason": "no known maps to match against"}

    by_name = {ocr_hud._norm_text(m["name"]): m for m in known_maps}
    if t in by_name:
        return {"map": by_name[t]["id"], "quality": 1.0, "method": "exact",
                "reason": t}

    # A broadcast strip often reads "NEPAL VILLAGE" or "CONTROL — NEPAL":
    # a full catalog name contained in the OCR string is a strong signal,
    # but only when exactly ONE catalog name is contained.
    contained = [m for name, m in by_name.items() if name and name in t]
    if len(contained) == 1:
        return {"map": contained[0]["id"], "quality": 0.93,
                "method": "contains",
                "reason": f"catalog name '{contained[0]['name']}' inside '{t}'"}
    if len(contained) > 1:
        return {"map": None, "quality": 0.0, "method": "none",
                "reason": (f"ambiguous: {len(contained)} catalog names inside "
                           f"'{t}' ({', '.join(m['id'] for m in contained)})")}

    ranked = sorted(((difflib.SequenceMatcher(None, t, name).ratio(), m)
                     for name, m in by_name.items()), key=lambda r: -r[0])
    if not ranked or ranked[0][0] < FUZZY_CUTOFF:
        return {"map": None, "quality": 0.0, "method": "none",
                "reason": (f"no fuzzy match for '{t}' "
                           f"(best {ranked[0][1]['id']} "
                           f"{ranked[0][0]:.2f} < {FUZZY_CUTOFF})"
                           if ranked else f"no fuzzy match for '{t}'")}
    best_r, best_m = ranked[0]
    other = next(((r, m) for r, m in ranked[1:] if m["id"] != best_m["id"]), None)
    if other and best_r - other[0] < FUZZY_MARGIN:
        return {"map": None, "quality": 0.0, "method": "none",
                "reason": (f"ambiguous fuzzy: {best_m['id']}/{other[1]['id']} "
                           f"({best_r:.2f} vs {other[0]:.2f})")}
    return {"map": best_m["id"], "quality": round(best_r, 3), "method": "fuzzy",
            "reason": f"'{t}'~'{best_m['name']}' {best_r:.2f}"}


def match_mode(text: str) -> str | None:
    """A printed mode word, or None. Corroboration only (see MODE_WORDS)."""
    t = ocr_hud._norm_text(text)
    for word, mode in MODE_WORDS.items():
        if word in t.split() or word == t:
            return mode
    return None


def _zone_candidates(items: list[dict], zone_px: list[int]) -> list[dict]:
    """Every OCR item centered in a zone, best confidence first. Unlike
    `team_identify._zone_text` this returns ALL of them: a map card prints
    the mode and the map name as separate items, and both matter."""
    cands = [it for it in items
             if it.get("conf", 0) >= MIN_OCR_CONF
             and ocr_hud._center_in(it["box"], zone_px)]
    cands.sort(key=lambda i: -i["conf"])
    return cands


def zones_for(layout: dict | None, fw: int, fh: int) -> dict:
    """Pixel zones, allowing a layout to override the defaults via
    `map_zones` (same validation shape `team_identify._zones_for` uses)."""
    fracs = dict(DEFAULT_MAP_ZONES)
    if layout and isinstance(layout.get("map_zones"), dict):
        for k, v in layout["map_zones"].items():
            ok = (isinstance(v, (list, tuple)) and len(v) == 4
                  and all(isinstance(n, (int, float)) for n in v)
                  and 0 <= v[0] < 1 and 0 <= v[1] < 1
                  and 0 < v[2] <= 1 and 0 < v[3] <= 1)
            if ok:
                fracs[k] = [float(n) for n in v]
    return {k: ocr_hud.zone_px(v, fw, fh) for k, v in fracs.items()}


def identify_map(ocr_per_frame: list[tuple[float, list[dict]]],
                 known_maps: list[dict], *, layout: dict | None = None,
                 fw: int = 1920, fh: int = 1080,
                 min_agree: int = MIN_AGREE_FRAMES) -> dict:
    """Resolve one segment's map + mode across frames.

    `ocr_per_frame`: [(offset_seconds, ocr_items), ...].

    Returns:
      {"map": id|None, "mode": str|None, "confidence": float,
       "method": str, "reason": str, "nFrames": int, "votes": {...},
       "modeWords": {...}, "evidence": [...], "unresolved": [...],
       "disagreement": bool}

    `map=None` means UNKNOWN, and `reason` always says why. `disagreement`
    is True when two different maps each reached consensus-worthy support —
    the case that must become a human review task, not a pick.
    """
    zones = zones_for(layout, fw, fh)
    votes: dict[str, list[dict]] = {}
    mode_words: dict[str, int] = {}
    unresolved: list[dict] = []
    for t, items in ocr_per_frame:
        seen_this_frame: set[str] = set()
        for zone_name in ("objective_strip", "map_card"):
            for it in _zone_candidates(items, zones[zone_name]):
                mode = match_mode(it["text"])
                if mode:
                    mode_words[mode] = mode_words.get(mode, 0) + 1
                m = match_map(it["text"], known_maps)
                if m["map"]:
                    if m["map"] in seen_this_frame:
                        continue          # one vote per map per frame
                    seen_this_frame.add(m["map"])
                    votes.setdefault(m["map"], []).append({
                        "t": t, "zone": zone_name, "raw": it["text"],
                        "conf": round(float(it["conf"]), 3),
                        "method": m["method"], "quality": m["quality"],
                        "score": m["quality"] * float(it["conf"]),
                    })
                elif m["reason"] not in ("too short",):
                    unresolved.append({"t": t, "zone": zone_name,
                                       "raw": it["text"], "reason": m["reason"]})

    catalog = {m["id"]: m for m in known_maps}
    if not votes:
        return _none(
            "no OCR map-name match in any frame" if not unresolved else
            f"{len(unresolved)} OCR hit(s), none matched a known map",
            nFrames=0, votes={}, modeWords=mode_words,
            evidence=[], unresolved=unresolved[:8], disagreement=False)

    ranked = sorted(votes.items(),
                    key=lambda kv: (len({e["t"] for e in kv[1]}),
                                    sum(e["score"] for e in kv[1])),
                    reverse=True)
    best_id, best_evs = ranked[0]
    best_frames = len({e["t"] for e in best_evs})

    # Two maps both reaching consensus is a genuine disagreement (a segment
    # spanning a map change, or a badly-cut window) — never resolved here.
    contenders = [(mid, len({e["t"] for e in evs})) for mid, evs in ranked
                  if len({e["t"] for e in evs}) >= min_agree]
    if len(contenders) > 1:
        return _none(
            f"conflicting map evidence: "
            + ", ".join(f"{mid} in {n} frame(s)" for mid, n in contenders)
            + " — the window probably spans more than one map",
            nFrames=best_frames,
            votes={k: len(v) for k, v in votes.items()},
            modeWords=mode_words, evidence=best_evs[:8],
            unresolved=unresolved[:8], disagreement=True)

    if best_frames < min_agree:
        return _none(
            f"'{best_id}' matched in only {best_frames} frame(s) "
            f"(< {min_agree} required for consensus)",
            nFrames=best_frames, weakCandidate=best_id,
            votes={k: len(v) for k, v in votes.items()},
            modeWords=mode_words, evidence=best_evs[:8],
            unresolved=unresolved[:8], disagreement=False)

    conf = sum(e["score"] for e in best_evs) / len(best_evs)
    row = catalog.get(best_id) or {}
    mode = row.get("mode")
    result = {
        "map": best_id, "mode": mode,
        "confidence": round(min(conf, 1.0), 3),
        "method": best_evs[0]["method"], "nFrames": best_frames,
        "reason": (f"{best_frames} frame(s) agree on '{best_id}' "
                   f"({row.get('name') or best_id}); mode '{mode}' from the "
                   f"map catalog, not OCR"),
        "votes": {k: len(v) for k, v in votes.items()},
        "modeWords": mode_words, "evidence": best_evs[:8],
        "unresolved": unresolved[:8], "disagreement": False,
    }
    # If the broadcast printed a mode word that contradicts the catalog, say
    # so loudly. The catalog still wins (it is authoritative), but a
    # contradiction means either OCR noise or the wrong map — worth a human.
    if mode and mode_words and mode not in mode_words:
        result["modeConflict"] = (
            f"broadcast printed mode word(s) {sorted(mode_words)} but the "
            f"catalog says {best_id} is {mode} — check the map identification")
        result["disagreement"] = True
    return result


def format_result(result: dict) -> str:
    if not result.get("map"):
        return (f"  map : UNKNOWN — {result.get('reason')}"
                + (f"\n  weak candidate: {result['weakCandidate']}"
                   if result.get("weakCandidate") else ""))
    lines = [f"  map : {result['map']} ({result['mode']}) "
             f"conf={result['confidence']:.2f} via {result['method']} "
             f"over {result['nFrames']} frame(s)"]
    for e in result.get("evidence", [])[:4]:
        lines.append(f"    t={e['t']:.0f}s [{e['zone']}] {e['raw']!r} "
                     f"conf={e['conf']}")
    if result.get("modeConflict"):
        lines.append(f"    CONFLICT: {result['modeConflict']}")
    return "\n".join(lines)
