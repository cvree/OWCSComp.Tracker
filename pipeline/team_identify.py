#!/usr/bin/env python3
"""
team_identify.py — OCR-based team identity, generalized across broadcasts.

Comp/round tracking (ingest_map.py) already knows whether the two screen
sides SWAPPED relative to round 1 (gameplay_state.side_hue + chip-hue
continuity). It has never known WHICH team is which — that has always come
from an operator-typed --team-a/--team-b flag, trusted blindly.

This module closes that gap the same way the rest of the CV pipeline
closes gaps: read the evidence, match it against the known teams table,
and say so ONLY when the evidence is unambiguous — otherwise return no
opinion rather than a guess. It never touches the DB itself (pure
analysis, like detect.py/gameplay_state.py); ingest_map.py persists the
result into ingest_findings and cross-checks it against the operator's
claim.

Pipeline:
  1. OCR the team-name zone on each screen side, across several frames
     (reuses ocr_hud's DEFAULT_ZONES / zone geometry — resolution
     independent, no per-broadcast calibration needed).
  2. Fuzzy-match each OCR hit against the teams table's name AND short
     code (e.g. both "TWISTED MINDS" and "TM" resolve the same team).
  3. TEMPORAL CONSENSUS: a side is only assigned a team once the SAME
     team_id has matched in >= MIN_AGREE_FRAMES frames — a single OCR
     misread can never flip a team's identity.
  4. Cross-check against the operator-supplied team, if any: agreement,
     disagreement (needs review), or "no signal" (OCR unavailable/unclear
     — operator's claim stands, unchallenged).

No OCR engine is required to install/import this module: every function
here takes pre-computed OCR items (or none) and is exercised in tests via
a fake reader, exactly like ocr_hud.py.
"""
from __future__ import annotations
import difflib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ocr_hud  # noqa: E402 — reuse zone geometry, text normalization

MIN_AGREE_FRAMES = 2       # frames that must agree on the same team_id
FUZZY_CUTOFF = 0.72        # team names are longer/noisier than hero aliases
FUZZY_MARGIN = 0.08
MIN_OCR_CONF = 0.30


def _none(reason: str) -> dict:
    return {"team": None, "quality": 0.0, "method": "none", "reason": reason}


def match_team(text: str, known_teams: list[dict]) -> dict:
    """Normalize OCR text to a team id, honest about ambiguity.

    known_teams: [{'id','name','code'}, ...] (read-only DB snapshot).
    Returns {"team": id|None, "quality": float, "method":
             exact|code|fuzzy|none, "reason": str} — same shape/philosophy
    as ocr_hud.match_hero: two teams tying never produces a silent guess.
    """
    t = ocr_hud._norm_text(text)
    if not t or len(t) < 2:
        return _none("too short")
    if not known_teams:
        return _none("no known teams to match against")

    by_name = {ocr_hud._norm_text(tm["name"]): tm for tm in known_teams}
    by_code = {ocr_hud._norm_text(tm["code"]): tm for tm in known_teams
              if tm.get("code")}

    if t in by_name:
        return {"team": by_name[t]["id"], "quality": 1.0, "method": "exact",
                "reason": t}
    if t in by_code:
        return {"team": by_code[t]["id"], "quality": 0.97, "method": "code",
                "reason": t}
    for w in t.split():
        if len(w) >= 2 and w in by_code:
            return {"team": by_code[w]["id"], "quality": 0.9,
                    "method": "code", "reason": f"word '{w}'"}

    ranked = sorted(
        ((difflib.SequenceMatcher(None, t, name).ratio(), tm)
         for name, tm in by_name.items()), key=lambda r: -r[0])
    if not ranked or ranked[0][0] < FUZZY_CUTOFF:
        return _none("no fuzzy match")
    best_r, best_tm = ranked[0]
    other = next(((r, tm) for r, tm in ranked[1:] if tm["id"] != best_tm["id"]),
                 None)
    if other and best_r - other[0] < FUZZY_MARGIN:
        return _none(f"ambiguous fuzzy: {best_tm['id']}/{other[1]['id']} "
                     f"({best_r:.2f} vs {other[0]:.2f})")
    return {"team": best_tm["id"], "quality": round(best_r, 3),
            "method": "fuzzy",
            "reason": f"'{t}'~'{best_tm['name']}' {best_r:.2f}"}


# ---------------------------------------------------------------- zones
def _zone_text(items: list[dict], zone_px: list[int]) -> dict | None:
    """Best-confidence OCR item centered in zone_px, skipping digits/short."""
    cands = [it for it in items
             if it.get("conf", 0) >= MIN_OCR_CONF
             and ocr_hud._center_in(it["box"], zone_px)]
    cands.sort(key=lambda i: -i["conf"])
    for it in cands:
        t = ocr_hud._norm_text(it["text"])
        if len(t) >= 2 and not t.isdigit():
            return it
    return None


def _zones_for(layout: dict | None, fw: int, fh: int) -> dict:
    zones_frac = dict(ocr_hud.DEFAULT_ZONES)
    if layout and isinstance(layout.get("ocr_zones"), dict):
        for k, v in layout["ocr_zones"].items():
            ok = (isinstance(v, (list, tuple)) and len(v) == 4
                  and all(isinstance(n, (int, float)) for n in v)
                  and 0 <= v[0] < 1 and 0 <= v[1] < 1
                  and 0 < v[2] <= 1 and 0 < v[3] <= 1)
            if ok:
                zones_frac[k] = [float(n) for n in v]
    return {k: ocr_hud.zone_px(v, fw, fh) for k, v in zones_frac.items()}


# ------------------------------------------------------------- consensus
def identify_side(ocr_per_frame: list[tuple[float, list[dict]]],
                  zone_px: list[int], known_teams: list[dict],
                  min_agree: int = MIN_AGREE_FRAMES) -> dict:
    """Resolve one screen side's team identity across frames.

    ocr_per_frame: [(t, ocr_items), ...] — ocr_items already computed for
    that frame (list of {'text','conf','box'}). Requires the SAME team_id
    to win in >= min_agree frames before returning a team; otherwise
    returns team=None with the evidence so far (never a one-frame guess).
    """
    votes: dict[str, list[dict]] = {}
    unresolved = []
    for t, items in ocr_per_frame:
        it = _zone_text(items, zone_px)
        if it is None:
            continue
        m = match_team(it["text"], known_teams)
        if m["team"]:
            votes.setdefault(m["team"], []).append({
                "score": m["quality"] * it["conf"], "raw": it["text"],
                "conf": it["conf"], "method": m["method"], "t": t,
            })
        else:
            unresolved.append({"raw": it["text"], "reason": m["reason"],
                               "t": t})
    if not votes:
        return {"team": None, "confidence": 0.0, "n_frames": 0,
                "reason": ("no OCR team-name match in any frame"
                          if not unresolved else
                          f"{len(unresolved)} OCR hit(s), none matched a "
                          "known team"),
                "unresolved": unresolved[:5]}
    best_team, evs = max(votes.items(),
                         key=lambda kv: (len(kv[1]),
                                        sum(e["score"] for e in kv[1])))
    if len(evs) < min_agree:
        return {"team": None, "confidence": 0.0, "n_frames": len(evs),
                "weak_candidate": best_team,
                "reason": (f"'{best_team}' matched in only {len(evs)} "
                          f"frame(s) (< {min_agree} required for "
                          "consensus)"),
                "unresolved": unresolved[:5]}
    conf = sum(e["score"] for e in evs) / len(evs)
    return {"team": best_team, "confidence": round(conf, 3),
            "n_frames": len(evs), "method": evs[0]["method"],
            "raw_text": evs[0]["raw"],
            "reason": f"{len(evs)} frame(s) agree on '{best_team}'"}


def identify_teams(ocr_per_frame_a: list[tuple[float, list[dict]]],
                   ocr_per_frame_b: list[tuple[float, list[dict]]],
                   layout: dict | None, known_teams: list[dict],
                   fw: int, fh: int) -> dict:
    """{'a': candidate, 'b': candidate} — see identify_side.

    Callers pass the SAME ocr_items list twice (once per call site) when
    they already ran OCR once per frame; the two _zone_text lookups use
    different zone rects (team_left/team_right) on the same items.
    """
    zones = _zones_for(layout, fw, fh)
    a = identify_side(ocr_per_frame_a, zones["team_left"], known_teams)
    b = identify_side(ocr_per_frame_b, zones["team_right"], known_teams)
    return {"a": a, "b": b}


# ------------------------------------------------------------ cross-check
def cross_check(detected: dict | None, operator_team_id: str | None) -> dict:
    """Compare an identify_side() result against the operator's claim.

    Returns {'agrees': True|False|None, 'note': str}. None means "no CV
    signal available" — the operator's claim stands unchallenged, exactly
    as it always has; this never blocks a run, only informs it."""
    if not detected or not detected.get("team"):
        return {"agrees": None,
                "note": (detected or {}).get("reason")
                or "no CV team-identity signal available"}
    if detected["team"] == operator_team_id:
        return {"agrees": True,
                "note": (f"OCR confirms '{operator_team_id}' "
                         f"({detected['confidence']:.0%} confidence, "
                         f"{detected['n_frames']} frame(s))")}
    return {"agrees": False,
            "note": (f"OCR detected '{detected['team']}' but the run was "
                     f"started with '{operator_team_id}' — "
                     f"{detected['confidence']:.0%} confidence over "
                     f"{detected['n_frames']} frame(s); review before "
                     "trusting either side's labeling")}


# ---------------------------------------------------------- DB read (RO)
def known_teams_from_db(con) -> list[dict]:
    """Read-only {'id','name','code'} snapshot of the teams table."""
    return [{"id": r["id"], "name": r["name"], "code": r["code"]}
            for r in con.execute("SELECT id, name, code FROM teams")]


# ------------------------------------------------------- frame -> OCR I/O
def ocr_zone_frames(frame_paths: list[tuple[float, str]], read_fn,
                    ) -> list[tuple[float, list[dict]]]:
    """Run OCR once per frame path, return [(t, ocr_items), ...].

    Thin convenience wrapper so callers with real frames (not pre-computed
    OCR) don't have to re-derive this loop; read_fn is the same
    frame_bgr -> [{'text','conf','box'}] contract as ocr_hud.make_reader."""
    import cv2
    out = []
    for t, path in frame_paths:
        frame = cv2.imread(path)
        if frame is None:
            continue
        out.append((t, read_fn(frame)))
    return out
