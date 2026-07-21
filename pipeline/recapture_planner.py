#!/usr/bin/env python3
"""
recapture_planner.py — decide WHICH moments of a map actually carry a real,
trustworthy hero comp, and plan a dense recapture over only those.

A broadcast minute is not uniformly worth reading. During setup/prepare a
player may hold a random or movement hero ("speeding" to spawn) before
locking the real pick; the instant a round ends the losing side often swaps
for the next round; highlights and replays render a full HUD that is not the
live comp at that moment. Reading comps in those windows is how you get
phantom swaps and wrong openers.

This planner takes the FIRST, coarse ingestion pass — every sampled time
already classified by gameplay_state.classify_frame and located within a
round by the emblem detector — and turns it into:

  * a set of DON'T-COUNT bookmarks (setup, post-round grace, highlight,
    replay, no-hud) with the reason each was excluded, and
  * a set of COUNT windows (settled live combat) to RECAPTURE densely, so
    the second pass spends its frames only where the comp is real.

Phase model per sampled time
  setup        inside a setup/prepare span (emblem lock)         -> skip
  post-start   first `settle` s after a round unlock             -> skip
                 (heroes still finishing a setup-queued swap)
  post-round   last `grace` s before the next setup / round end  -> skip
                 (the "finish + 10s to allow the swap" window)
  highlight    reject-marker / OCR highlight+replay              -> skip
  no-hud       chip structure absent (desk, cams, transition)    -> skip
  combat       settled live gameplay                             -> COUNT

Pure Python (times + labels only): fully unit-testable, no video needed.
"""
from __future__ import annotations
import argparse
import json
import os
import sys

# defaults chosen from the user's spec + the existing post-unlock grace
SETTLE_AFTER_START = 8.0    # ignore the first N s of a round (setup swaps land)
GRACE_BEFORE_END = 10.0     # "finishing + 10 seconds to allow for the swap"
SKIP_STATES = {"no-hud", "partial-hud", "replay", "highlight", "intermission"}


def classify_time(t: float, state: str, rounds: list[dict],
                  setups: list[dict],
                  settle: float = SETTLE_AFTER_START,
                  grace: float = GRACE_BEFORE_END) -> tuple[str, bool]:
    """(phase, counts) for one sampled time.

    `rounds`/`setups` are [{start,end}] spans from the emblem detector.
    A time counts only when it is settled live combat inside a round.
    """
    if state in SKIP_STATES:
        return (state if state in ("replay", "highlight", "intermission")
                else "no-hud"), False
    for s in setups:
        if s["start"] - 2 <= t <= s["end"] + 2:
            return "setup", False
    inround = None
    for r in rounds:
        if r["start"] <= t <= r["end"]:
            inround = r
            break
    if inround is None:
        # gameplay-looking frame outside any detected round -> unsure, skip
        return "out-of-round", False
    if t < inround["start"] + settle:
        return "post-start", False
    if t > inround["end"] - grace:
        return "post-round", False
    return "combat", True


def plan(observations: list[dict], rounds: list[dict], setups: list[dict],
         *, settle: float = SETTLE_AFTER_START,
         grace: float = GRACE_BEFORE_END,
         recap_every: float = 2.0, merge_gap: float = 6.0) -> dict:
    """Turn a coarse first pass into bookmarks + dense recapture windows.

    `observations` is [{t, state}] (state from classify_frame). Returns
    count/skip tallies, the per-reason bookmark list, and the merged COUNT
    windows with a suggested dense sampling interval for pass two.
    """
    tagged = []
    for o in observations:
        phase, counts = classify_time(
            o["t"], o.get("state", "no-hud"), rounds, setups, settle, grace)
        tagged.append({"t": o["t"], "state": o.get("state"),
                       "phase": phase, "counts": counts})

    # merge contiguous COUNT samples into recapture windows (bridging small
    # gaps so one dropped frame doesn't split a fight into two windows)
    count_ts = sorted(x["t"] for x in tagged if x["counts"])
    windows = []
    for t in count_ts:
        if windows and t - windows[-1]["end"] <= merge_gap:
            windows[-1]["end"] = t
        else:
            windows.append({"start": t, "end": t})
    for w in windows:
        w["start"] = max(w["start"], _round_of(w["start"], rounds, settle, "start"))
        w["recaptureEvery"] = recap_every

    bookmarks = {}
    for x in tagged:
        if not x["counts"]:
            bookmarks.setdefault(x["phase"], []).append(round(x["t"], 1))

    return {
        "settle": settle, "grace": grace,
        "counted": sum(1 for x in tagged if x["counts"]),
        "skipped": sum(1 for x in tagged if not x["counts"]),
        "skipBreakdown": {k: len(v) for k, v in sorted(bookmarks.items())},
        "bookmarks": bookmarks,
        "recaptureWindows": windows,
        "recaptureSeconds": round(sum(w["end"] - w["start"] for w in windows), 1),
        "tagged": tagged,
    }


def _round_of(t, rounds, settle, which):
    for r in rounds:
        if r["start"] <= t <= r["end"]:
            return r["start"] + settle if which == "start" else r["end"]
    return t


def _demo() -> dict:
    """One 3-round-ish control map: setup, a round, a highlight mid-way."""
    rounds = [{"start": 100, "end": 200}, {"start": 240, "end": 330}]
    setups = [{"start": 60, "end": 100}, {"start": 200, "end": 240}]
    obs = []
    for t in range(60, 340, 5):
        state = "gameplay"
        if 150 <= t <= 160:      # a mid-round highlight cut
            state = "highlight"
        obs.append({"t": float(t), "state": state})
    return plan(obs, rounds, setups)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    if args.demo:
        d = _demo()
        d.pop("tagged", None)
        print(json.dumps(d, indent=1))
        return
    print("recapture_planner: call plan(observations, rounds, setups). "
          "Use --demo for a worked example.")


if __name__ == "__main__":
    main()
