#!/usr/bin/env python3
"""Offline tests for the phase-aware recapture planner."""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recapture_planner as R  # noqa: E402

FAILS = 0
ROUNDS = [{"start": 100, "end": 200}, {"start": 240, "end": 330}]
SETUPS = [{"start": 60, "end": 100}, {"start": 200, "end": 240}]


def check(name, ok):
    global FAILS
    if not ok:
        FAILS += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")


def phase(t, state="gameplay"):
    return R.classify_time(t, state, ROUNDS, SETUPS)


def main() -> int:
    print("per-time phase gating:")
    check("setup/prepare never counts", phase(80)[1] is False and phase(80)[0] == "setup")
    check("first seconds after a round unlock are skipped (setup swaps land)",
          phase(103)[0] == "post-start" and phase(103)[1] is False)
    check("finish + grace window before round end is skipped",
          phase(195)[0] == "post-round" and phase(195)[1] is False)
    check("settled mid-round combat counts",
          phase(150)[0] == "combat" and phase(150)[1] is True)
    check("a highlight frame in live combat is refused",
          phase(150, "highlight")[1] is False)
    check("replay frame is refused", phase(150, "replay")[1] is False)
    check("no-hud frame is refused", phase(150, "no-hud")[1] is False)
    check("gameplay outside any detected round is refused (unsure)",
          phase(335)[0] == "out-of-round" and phase(335)[1] is False)

    print("grace length is the spec'd finish + 10s:")
    check("t at end-10 is the boundary (still counted just before)",
          phase(189)[1] is True and phase(191)[1] is False)

    print("planner turns a coarse pass into windows + bookmarks:")
    d = R._demo()
    check("some samples counted, some skipped", d["counted"] > 0 and d["skipped"] > 0)
    check("setup + post-round + highlight all bookmarked with reasons",
          {"setup", "post-round", "highlight"} <= set(d["skipBreakdown"]))
    check("the mid-round highlight splits combat into two windows",
          len(d["recaptureWindows"]) == 3)
    check("every recapture window carries a dense interval",
          all(w.get("recaptureEvery") for w in d["recaptureWindows"]))
    check("recapture windows never start inside the post-start settle",
          all(w["start"] >= 108 for w in d["recaptureWindows"]))
    check("no counted sample falls in a setup span",
          all(not (SETUPS[0]["start"] <= x["t"] <= SETUPS[0]["end"])
              for x in d["tagged"] if x["counts"]))

    print("tunable settle/grace:")
    p2, c2 = R.classify_time(103, "gameplay", ROUNDS, SETUPS, settle=2.0)
    check("shorter settle counts an earlier post-start moment", c2 is True)

    print()
    if FAILS:
        print(f"FAILED: {FAILS} check(s)")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
