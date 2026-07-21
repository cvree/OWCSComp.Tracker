#!/usr/bin/env python3
"""Offline tests for match confirmation (team names + bans vs FACEIT)."""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import match_confirm as M  # noqa: E402

FAILS = 0


def check(name, ok):
    global FAILS
    if not ok:
        FAILS += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")


def T(a, b, ca=0.8, cb=0.8):
    return {"a": {"team": a, "confidence": ca, "n_frames": 5},
            "b": {"team": b, "confidence": cb, "n_frames": 5}}


def main() -> int:
    exp = {"teamA": "qadsiah", "teamB": "twis", "bans": ["sombra", "widow"]}

    print("both names + bans agree -> confirmed, high confidence:")
    r = M.confirm(T("qadsiah", "twis"), {"a": [{"hero": "sombra"}], "b": [{"hero": "widow"}]}, exp)
    check("confirmed", r["confirmed"] is True and r["confidence"] > 0.8)
    check("orientation is aligned", r["orientation"] == "aligned")

    print("names match but sides are swapped on screen:")
    r = M.confirm(T("twis", "qadsiah"), {}, exp)
    check("still confirmed", r["confirmed"] is True)
    check("orientation flagged sides-swapped", r["orientation"] == "sides-swapped")

    print("wrong teams -> hard contradiction, not confirmed:")
    r = M.confirm(T("crazy-raccoon", "zeta"), {}, exp)
    check("confirmed is False", r["confirmed"] is False and r["confidence"] == 0.0)
    check("note warns about wrong VOD/game", "wrong VOD" in r["signals"][0]["note"])

    print("no team signal but bans line up -> soft confirmation:")
    r = M.confirm({"a": {}, "b": {}}, {"a": [{"hero": "sombra"}], "b": [{"hero": "widow"}]}, exp)
    check("soft-confirmed via bans", r["confirmed"] is True)
    check("confidence is moderate, not high", 0.3 < r["confidence"] < 0.85)

    print("no signals at all -> None (operator pairing stands):")
    r = M.confirm({"a": {}, "b": {}}, {}, {"teamA": "qadsiah", "teamB": "twis"})
    check("verdict is None (never blocks)", r["confirmed"] is None)

    print("partial: one name matches, no bans -> unconfirmed but not wrong:")
    r = M.confirm(T("qadsiah", None), {}, exp)
    check("agrees is None (partial)", r["signals"][0]["agrees"] is None)
    check("confirmed None", r["confirmed"] is None)

    print("bans present but disjoint from expected -> contradiction:")
    r = M.confirm({"a": {}, "b": {}}, {"a": [{"hero": "genji"}], "b": [{"hero": "mei"}]}, exp)
    check("ban mismatch reads as not-confirmed", r["confirmed"] is False)

    print("demo is stable:")
    check("deterministic", M._demo() == M._demo())

    print()
    if FAILS:
        print(f"FAILED: {FAILS} check(s)")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
