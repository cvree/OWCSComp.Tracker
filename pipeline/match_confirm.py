#!/usr/bin/env python3
"""
match_confirm.py — is this footage actually the match we think it is?

FACEIT (or the operator) tells the pipeline WHICH match a VOD window
belongs to: the two teams, and — when available — the hero bans. The HUD
independently shows the same facts: the team-name plates, and the ban
subsection (a smaller strip, usually off to the side, not in line with the
comp portraits). Reading those and checking them against the expected match
is a cheap, high-value guard against pointing the ingester at the wrong VOD
or the wrong game in a series.

This module fuses two already-existing detectors into one verdict:
  * team names   -> team_identify.identify_teams  (per side)
  * hero bans    -> detect_bans.detect_bans_in_frames

It is pure logic over their outputs (no video / OCR here), so it unit-tests
cleanly and never blocks a run — it only raises or lowers confidence and
explains why, exactly like the rest of the honest pipeline. Bans are a
CONFIRMATION signal; the authoritative ban list still comes from FACEIT.
"""
from __future__ import annotations
import argparse
import json


def _team_signal(detected_teams: dict, expected_a: str,
                 expected_b: str) -> dict:
    """Do the two detected name plates match the expected pair (in either
    screen orientation)?"""
    a = (detected_teams or {}).get("a") or {}
    b = (detected_teams or {}).get("b") or {}
    da, db = a.get("team"), b.get("team")
    ca = a.get("confidence") or 0.0
    cb = b.get("confidence") or 0.0
    expected = {expected_a, expected_b}
    if da and db and {da, db} == expected:
        orientation = "aligned" if (da == expected_a) else "sides-swapped"
        return {"signal": "team-names", "agrees": True,
                "orientation": orientation,
                "strength": round(min(ca, cb), 3),
                "note": (f"both name plates match the expected teams "
                         f"({orientation}; {min(ca, cb):.0%} min confidence)")}
    if da in expected or db in expected:
        return {"signal": "team-names", "agrees": None, "orientation": None,
                "strength": round(max(ca, cb) * 0.5, 3),
                "note": (f"one name plate matches (a={da!r}, b={db!r}); "
                         "the other is unread or off — partial confirmation")}
    if not da and not db:
        return {"signal": "team-names", "agrees": None, "orientation": None,
                "strength": 0.0, "note": "no team-name signal read"}
    return {"signal": "team-names", "agrees": False, "orientation": None,
            "strength": 0.0,
            "note": (f"name plates ({da!r}, {db!r}) don't match the expected "
                     f"pair ({expected_a!r}, {expected_b!r}) — wrong VOD/game?")}


def _ban_signal(detected_bans: dict, expected_bans: list | None) -> dict:
    """Overlap between detected bans and the FACEIT-expected ban list."""
    if not expected_bans:
        return {"signal": "bans", "agrees": None, "strength": 0.0,
                "note": "no expected ban list to confirm against"}
    got = set()
    for side in ("a", "b"):
        for b in (detected_bans or {}).get(side, []) or []:
            got.add(b["hero"])
    exp = set(expected_bans)
    if not got:
        return {"signal": "bans", "agrees": None, "strength": 0.0,
                "note": f"no bans read from the HUD (expected {len(exp)})"}
    overlap = got & exp
    frac = len(overlap) / len(exp)
    agrees = True if frac >= 0.5 else (False if got.isdisjoint(exp) else None)
    return {"signal": "bans", "agrees": agrees, "strength": round(frac, 3),
            "overlap": sorted(overlap), "detected": sorted(got),
            "note": (f"{len(overlap)}/{len(exp)} expected bans also read "
                     f"from the HUD ({sorted(overlap)})")}


def confirm(detected_teams: dict, detected_bans: dict,
            expected: dict) -> dict:
    """Overall match-confirmation verdict.

    expected = {'teamA': id, 'teamB': id, 'bans': [hero_id, ...] | None}
    Returns {confirmed, confidence, orientation, signals, note}. `confirmed`
    is True / False / None (no signal) — None never blocks, it just means the
    operator's pairing stands unchallenged.
    """
    ts = _team_signal(detected_teams, expected.get("teamA"),
                      expected.get("teamB"))
    bs = _ban_signal(detected_bans, expected.get("bans"))

    # any hard contradiction (names clearly wrong) dominates
    if ts["agrees"] is False:
        confirmed, confidence = False, 0.0
    elif ts["agrees"] is True:
        # names carry the verdict; bans can only reinforce it
        confirmed = True
        confidence = round(min(1.0, ts["strength"] + 0.3 * bs["strength"]), 3)
    elif bs["agrees"] is True:
        # no full name match but bans line up -> soft confirmation
        confirmed, confidence = True, round(0.4 + 0.4 * bs["strength"], 3)
    elif bs["agrees"] is False:
        confirmed, confidence = False, 0.0
    else:
        confirmed, confidence = None, round(
            0.5 * ts["strength"] + 0.3 * bs["strength"], 3)

    notes = "; ".join(s["note"] for s in (ts, bs))
    return {
        "confirmed": confirmed,
        "confidence": confidence,
        "orientation": ts.get("orientation"),
        "signals": [ts, bs],
        "note": notes,
    }


def _demo() -> dict:
    detected_teams = {
        "a": {"team": "qadsiah", "confidence": 0.82, "n_frames": 6},
        "b": {"team": "twis", "confidence": 0.78, "n_frames": 5},
    }
    detected_bans = {"a": [{"hero": "sombra"}], "b": [{"hero": "widow"}]}
    expected = {"teamA": "qadsiah", "teamB": "twis",
                "bans": ["sombra", "widow", "mercy", "hog"]}
    return confirm(detected_teams, detected_bans, expected)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    if args.demo:
        print(json.dumps(_demo(), indent=1))
        return
    print("match_confirm: call confirm(detected_teams, detected_bans, "
          "expected). Use --demo for a worked example.")


if __name__ == "__main__":
    main()
