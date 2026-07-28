#!/usr/bin/env python3
"""
score_read.py — read SCORES, WINNERS and SERIES STATE off a broadcast (Phase 6).

Everything here is a number printed on an overlay, so everything here is an
OCR problem with the same discipline the rest of this pipeline uses: temporal
consensus, explicit UNKNOWN, and never a manufactured value.

The specific rules that make a score trustworthy — none of which a single
frame can satisfy:

  * A map score is read as a PAIR (left, right) from the two numerals either
    side of the centre clock. A lone numeral is not a score.
  * A map score is MONOTONIC: it only ever increases. A pair that would
    decrease is discarded as OCR noise, with the reason recorded. This single
    rule removes most misreads (an '8' read as a '3', a ticker digit caught in
    the zone) without any per-broadcast tuning.
  * The reported map score is the LAST pair that survived consensus, because
    a map's score is its final one.
  * A winner is DERIVED from the final score, never OCR'd separately: whoever
    has more. An equal score yields no winner (a real draw, or an incomplete
    read) — never a coin flip.
  * A series score is read the same way from the between-maps card, and
    cross-checked against the map results already established. A contradiction
    is reported, not silently resolved.
  * Best-of is only inferred when the evidence forces it (e.g. a side has 3
    map wins in a 5-map series). "Probably Bo5" is not evidence.

`operator_value` is the honest escape hatch: when OCR cannot resolve a fact,
an operator supplies it and the value is recorded with source='operator' and
their name — visibly different from a CV read, never blended into one.

Pure analysis, no DB writes (matching detect.py / detect_bans.py /
team_identify.py). The caller persists.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ocr_hud  # noqa: E402

MIN_AGREE_FRAMES = 2       # frames that must agree on a pair
MIN_OCR_CONF = 0.30
# A map score above this is not a map score — Overwatch map scores are small
# (Control to 2/3, Push/Escort to 1-3). A big number in the zone is a timer,
# a ticker figure, or a viewer count.
MAX_PLAUSIBLE_MAP_SCORE = 9
MAX_PLAUSIBLE_SERIES_SCORE = 4
# Best-of formats a series can be, smallest first.
KNOWN_BEST_OF = (1, 3, 5, 7)

# Where the score numerals live, as frame fractions. The two map-score
# numerals flank the centre clock in the top HUD strip; the series score is
# printed on the between-maps card.
DEFAULT_SCORE_ZONES = {
    "score_left": [0.36, 0.00, 0.10, 0.11],
    "score_right": [0.54, 0.00, 0.10, 0.11],
    "series_card": [0.20, 0.20, 0.60, 0.60],
}

_INT_RE = re.compile(r"^\d{1,2}$")
# A series card usually prints "2 - 1" as one string.
_PAIR_RE = re.compile(r"\b(\d)\s*[-–:]\s*(\d)\b")


def _none(reason: str, **extra) -> dict:
    return {"scoreA": None, "scoreB": None, "confidence": 0.0,
            "source": "cv-ocr", "reason": reason, **extra}


def zones_for(layout: dict | None, fw: int, fh: int) -> dict:
    """Pixel zones, allowing a layout to override the defaults via
    `score_zones` — same validation shape team_identify/map_identify use."""
    fracs = dict(DEFAULT_SCORE_ZONES)
    if layout and isinstance(layout.get("score_zones"), dict):
        for k, v in layout["score_zones"].items():
            ok = (isinstance(v, (list, tuple)) and len(v) == 4
                  and all(isinstance(n, (int, float)) for n in v)
                  and 0 <= v[0] < 1 and 0 <= v[1] < 1
                  and 0 < v[2] <= 1 and 0 < v[3] <= 1)
            if ok:
                fracs[k] = [float(n) for n in v]
    return {k: ocr_hud.zone_px(v, fw, fh) for k, v in fracs.items()}


def _int_in_zone(items: list[dict], zone_px: list[int],
                 max_value: int) -> dict | None:
    """The best-confidence small integer centered in a zone, or None."""
    best = None
    for it in items:
        if it.get("conf", 0) < MIN_OCR_CONF:
            continue
        if not ocr_hud._center_in(it["box"], zone_px):
            continue
        text = (it.get("text") or "").strip()
        if not _INT_RE.match(text):
            continue
        value = int(text)
        if value > max_value:
            continue
        if best is None or it["conf"] > best["conf"]:
            best = {"value": value, "conf": float(it["conf"]), "raw": text}
    return best


def read_score_pair(ocr_items: list[dict], zones: dict, *,
                    max_value: int = MAX_PLAUSIBLE_MAP_SCORE
                    ) -> tuple[int, int, float] | None:
    """One frame's (left, right, confidence) map score, or None.

    Requires BOTH numerals: half a score is not a score. This is what stops a
    single visible digit (a timer, a ticker) from becoming "2-None".
    """
    left = _int_in_zone(ocr_items, zones["score_left"], max_value)
    right = _int_in_zone(ocr_items, zones["score_right"], max_value)
    if left is None or right is None:
        return None
    return left["value"], right["value"], min(left["conf"], right["conf"])


def read_series_pair(ocr_items: list[dict], zones: dict
                     ) -> tuple[int, int, float] | None:
    """A between-maps card's series score, read from a single 'N - M' string
    or from two separate numerals in the card zone."""
    zone = zones["series_card"]
    for it in sorted(ocr_items, key=lambda i: -i.get("conf", 0)):
        if it.get("conf", 0) < MIN_OCR_CONF:
            continue
        if not ocr_hud._center_in(it["box"], zone):
            continue
        m = _PAIR_RE.search(it.get("text") or "")
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a <= MAX_PLAUSIBLE_SERIES_SCORE and b <= MAX_PLAUSIBLE_SERIES_SCORE:
                return a, b, float(it["conf"])
    return None


# ------------------------------------------------------------ map score
def identify_map_score(ocr_per_frame: list[tuple[float, list[dict]]], *,
                       layout: dict | None = None, fw: int = 1920,
                       fh: int = 1080, min_agree: int = MIN_AGREE_FRAMES
                       ) -> dict:
    """Resolve ONE map's final score across frames.

    Returns {"scoreA", "scoreB", "confidence", "source", "reason",
             "nFrames", "progression", "discarded", "readAt"}.
    `scoreA`/`scoreB` are None (UNKNOWN) whenever the evidence does not
    support a pair — with `reason` saying which rule refused it.
    """
    zones = zones_for(layout, fw, fh)
    accepted: list[dict] = []
    discarded: list[dict] = []
    best_seen = (0, 0)
    for t, items in ocr_per_frame:
        pair = read_score_pair(items, zones)
        if pair is None:
            continue
        a, b, conf = pair
        # MONOTONICITY: a map score never goes down. A pair below what we
        # have already established is a misread, not a rewind.
        if a < best_seen[0] or b < best_seen[1]:
            discarded.append({"t": t, "scoreA": a, "scoreB": b, "conf": conf,
                              "reason": (f"would decrease the score from "
                                         f"{best_seen[0]}-{best_seen[1]} — "
                                         f"discarded as OCR noise")})
            continue
        best_seen = (max(best_seen[0], a), max(best_seen[1], b))
        accepted.append({"t": t, "scoreA": a, "scoreB": b, "conf": conf})

    if not accepted:
        return _none(
            f"no complete score pair read in any of {len(ocr_per_frame)} "
            f"frame(s)" + (f"; {len(discarded)} non-monotonic read(s) discarded"
                           if discarded else ""),
            nFrames=0, progression=[], discarded=discarded[:8], readAt=None)

    # The FINAL score is what a map result records. Consensus is applied to
    # that final pair: it must have been read in >= min_agree frames.
    final = (accepted[-1]["scoreA"], accepted[-1]["scoreB"])
    agreeing = [r for r in accepted
                if (r["scoreA"], r["scoreB"]) == final]
    if len(agreeing) < min_agree:
        return _none(
            f"final score {final[0]}-{final[1]} was only read in "
            f"{len(agreeing)} frame(s) (< {min_agree} required) — the map may "
            f"have ended between samples",
            nFrames=len(agreeing), weakCandidate=list(final),
            progression=[[r["t"], r["scoreA"], r["scoreB"]] for r in accepted[-8:]],
            discarded=discarded[:8], readAt=agreeing[-1]["t"] if agreeing else None)

    conf = sum(r["conf"] for r in agreeing) / len(agreeing)
    return {
        "scoreA": final[0], "scoreB": final[1],
        "confidence": round(min(conf, 1.0), 3), "source": "cv-ocr",
        "nFrames": len(agreeing),
        "reason": (f"final score {final[0]}-{final[1]} read in "
                   f"{len(agreeing)} frame(s), monotonic across "
                   f"{len(accepted)} accepted read(s)"),
        "progression": [[r["t"], r["scoreA"], r["scoreB"]] for r in accepted[-12:]],
        "discarded": discarded[:8],
        "readAt": agreeing[-1]["t"],
    }


def map_winner(score_a: int | None, score_b: int | None,
               team_a: str | None, team_b: str | None) -> dict:
    """Derive a map winner from its final score. Never OCR'd separately —
    a "winner" signal that can disagree with the score it came from is worse
    than no signal."""
    if score_a is None or score_b is None:
        return {"winner": None, "source": "derived-from-score",
                "confidence": 0.0,
                "reason": "map score is UNKNOWN, so no winner can be derived"}
    if not team_a or not team_b:
        return {"winner": None, "source": "derived-from-score",
                "confidence": 0.0,
                "reason": (f"score is {score_a}-{score_b} but the team "
                           f"identities are not both resolved")}
    if score_a == score_b:
        return {"winner": None, "source": "derived-from-score",
                "confidence": 0.0,
                "reason": (f"score is level at {score_a}-{score_b} — a draw, "
                           f"or the map was not read to completion; no winner "
                           f"is claimed either way")}
    winner = team_a if score_a > score_b else team_b
    return {"winner": winner, "source": "derived-from-score", "confidence": 1.0,
            "reason": (f"{winner} won {max(score_a, score_b)}-"
                       f"{min(score_a, score_b)}")}


# ---------------------------------------------------------- series score
def identify_series_score(ocr_per_frame: list[tuple[float, list[dict]]], *,
                          layout: dict | None = None, fw: int = 1920,
                          fh: int = 1080, min_agree: int = MIN_AGREE_FRAMES
                          ) -> dict:
    """The running series score printed between maps, under the same
    consensus + monotonicity rules as a map score."""
    zones = zones_for(layout, fw, fh)
    accepted: list[dict] = []
    discarded: list[dict] = []
    best_seen = (0, 0)
    for t, items in ocr_per_frame:
        pair = read_series_pair(items, zones)
        if pair is None:
            continue
        a, b, conf = pair
        if a < best_seen[0] or b < best_seen[1]:
            discarded.append({"t": t, "scoreA": a, "scoreB": b, "conf": conf,
                              "reason": "would decrease the series score"})
            continue
        best_seen = (max(best_seen[0], a), max(best_seen[1], b))
        accepted.append({"t": t, "scoreA": a, "scoreB": b, "conf": conf})
    if not accepted:
        return _none("no series score card read in any frame",
                     nFrames=0, progression=[], discarded=discarded[:8],
                     readAt=None)
    final = (accepted[-1]["scoreA"], accepted[-1]["scoreB"])
    agreeing = [r for r in accepted if (r["scoreA"], r["scoreB"]) == final]
    if len(agreeing) < min_agree:
        return _none(
            f"series score {final[0]}-{final[1]} read in only "
            f"{len(agreeing)} frame(s) (< {min_agree})",
            nFrames=len(agreeing), weakCandidate=list(final),
            progression=[[r["t"], r["scoreA"], r["scoreB"]] for r in accepted],
            discarded=discarded[:8], readAt=None)
    conf = sum(r["conf"] for r in agreeing) / len(agreeing)
    return {"scoreA": final[0], "scoreB": final[1],
            "confidence": round(min(conf, 1.0), 3), "source": "cv-ocr",
            "nFrames": len(agreeing),
            "reason": (f"series score {final[0]}-{final[1]} read in "
                       f"{len(agreeing)} frame(s)"),
            "progression": [[r["t"], r["scoreA"], r["scoreB"]] for r in accepted],
            "discarded": discarded[:8], "readAt": agreeing[-1]["t"]}


def cross_check_series(series: dict, map_results: list[dict]) -> dict:
    """Compare an OCR'd series score against the map results established so
    far. Reports agreement or a contradiction; never rewrites either side."""
    wins_a = sum(1 for m in map_results if m.get("winner") == m.get("teamA")
                 and m.get("winner"))
    wins_b = sum(1 for m in map_results if m.get("winner") == m.get("teamB")
                 and m.get("winner"))
    if series.get("scoreA") is None:
        return {"agrees": None,
                "note": (f"no OCR series score to compare; map results give "
                         f"{wins_a}-{wins_b}")}
    if (series["scoreA"], series["scoreB"]) == (wins_a, wins_b):
        return {"agrees": True,
                "note": (f"OCR series score {series['scoreA']}-"
                         f"{series['scoreB']} matches the {wins_a}-{wins_b} "
                         f"from established map winners")}
    return {"agrees": False,
            "note": (f"OCR read the series as {series['scoreA']}-"
                     f"{series['scoreB']} but established map winners give "
                     f"{wins_a}-{wins_b} — one of them is wrong; review "
                     f"before recording either")}


def series_result(map_results: list[dict], *, team_a: str | None = None,
                  team_b: str | None = None) -> dict:
    """Final match winner + best-of structure, from established map results.

    Only claims a winner when one side has a MAJORITY of a determinable
    best-of. A 2-1 series is 2-1 — it is only a Bo3 victory if the format is
    known, and if it is not, the winner stays UNKNOWN rather than assumed.
    """
    decided = [m for m in map_results if m.get("winner")]
    wins_a = sum(1 for m in decided if m["winner"] == team_a) if team_a else 0
    wins_b = sum(1 for m in decided if m["winner"] == team_b) if team_b else 0
    played = len(map_results)
    out = {
        "winner": None, "seriesScoreA": wins_a, "seriesScoreB": wins_b,
        "mapsPlayed": played, "mapsDecided": len(decided),
        "bestOf": None, "source": "derived-from-map-results",
        "confidence": 0.0, "reason": "",
    }
    if played and len(decided) < played:
        out["reason"] = (f"{played - len(decided)} of {played} map(s) have no "
                         f"winner yet — series result is UNKNOWN")
        return out
    if not decided:
        out["reason"] = "no map has a winner yet"
        return out

    # Best-of is only inferred when a side's win count FORCES it: N wins
    # require a format of at least 2N-1 maps.
    needed = 2 * max(wins_a, wins_b) - 1
    candidates = [b for b in KNOWN_BEST_OF if b >= max(needed, played)]
    if not candidates:
        out["reason"] = (f"{wins_a}-{wins_b} over {played} map(s) fits no known "
                         f"best-of format {KNOWN_BEST_OF} — refusing to guess")
        return out
    best_of = candidates[0]
    out["bestOf"] = best_of
    majority = best_of // 2 + 1
    if max(wins_a, wins_b) < majority:
        out["reason"] = (f"{wins_a}-{wins_b} in a Bo{best_of} — no side has the "
                         f"{majority} map wins needed; the series is not over "
                         f"(or more maps are unprocessed)")
        return out
    if wins_a == wins_b:
        out["reason"] = f"series level at {wins_a}-{wins_b} — no winner"
        return out
    out["winner"] = team_a if wins_a > wins_b else team_b
    out["confidence"] = 1.0
    out["reason"] = (f"{out['winner']} took {max(wins_a, wins_b)} of "
                     f"{played} map(s), reaching the Bo{best_of} majority "
                     f"({majority})")
    return out


# --------------------------------------------------------- map ordering
def map_result_order(segments: list[dict]) -> list[dict]:
    """Map-result order + VOD timestamps for a series, straight from segment
    windows. Broadcast time IS map order, so this is a fact with evidence
    rather than an inference."""
    live = [s for s in segments
            if s.get("review_status") not in ("rejected", "invalid", "split",
                                              "merged")]
    live.sort(key=lambda s: s.get("start_time") or 0)
    return [{
        "mapOrder": i,
        "segmentId": s.get("id"),
        "map": s.get("map_name"),
        "mode": s.get("map_mode"),
        "vodStartSeconds": s.get("start_time"),
        "vodEndSeconds": s.get("end_time"),
        "teamA": s.get("team_a"), "teamB": s.get("team_b"),
        "source": "segment-window",
        "reason": (f"map {i} of {len(live)}, broadcast window "
                   f"{s.get('start_time')}-{s.get('end_time')}s"),
    } for i, s in enumerate(live, start=1)]


# ------------------------------------------------------ operator fallback
def operator_value(field: str, value, *, operator: str,
                   reason: str | None = None) -> dict:
    """Record a fact an operator supplied because OCR could not resolve it.

    Deliberately shaped like a CV result so callers treat both uniformly, but
    with `source='operator'` and the operator's name, so a human-supplied
    score is never mistaken for a measured one anywhere downstream.
    """
    if not (operator or "").strip():
        raise ValueError("an operator-supplied value requires an operator name "
                         "— an unattributed fact is not evidence")
    return {"field": field, "value": value, "source": "operator",
            "operator": operator.strip(), "confidence": 1.0,
            "reason": (reason or f"supplied by {operator.strip()} because OCR "
                                 f"could not resolve {field}")}


def format_map_score(result: dict) -> str:
    if result.get("scoreA") is None:
        return (f"  map score: UNKNOWN — {result['reason']}"
                + (f"\n    weak candidate: {result['weakCandidate']}"
                   if result.get("weakCandidate") else ""))
    lines = [f"  map score: {result['scoreA']}-{result['scoreB']} "
             f"conf={result['confidence']:.2f} over {result['nFrames']} frame(s)"]
    for t, a, b in (result.get("progression") or [])[-5:]:
        lines.append(f"    t={t:.0f}s  {a}-{b}")
    for d in (result.get("discarded") or [])[:3]:
        lines.append(f"    DISCARDED t={d['t']:.0f}s {d['scoreA']}-{d['scoreB']}: "
                     f"{d['reason']}")
    return "\n".join(lines)


def format_series(result: dict) -> str:
    lines = [f"  series   : {result['seriesScoreA']}-{result['seriesScoreB']} "
             f"over {result['mapsPlayed']} map(s)"
             + (f", Bo{result['bestOf']}" if result.get("bestOf") else "")]
    lines.append(f"  winner   : {result['winner'] or 'UNKNOWN'} — {result['reason']}")
    return "\n".join(lines)
