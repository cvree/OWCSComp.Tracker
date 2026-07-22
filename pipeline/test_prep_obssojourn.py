#!/usr/bin/env python3
"""Offline (dry-run) tests for the ObsSojourn match-prep tool. Reads the
committed DB for team resolution; never writes (write=False)."""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obssojourn_source as O  # noqa: E402
import prep_obssojourn_match as P  # noqa: E402

FAILS = 0


def check(name, ok):
    global FAILS
    if not ok:
        FAILS += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")


def main() -> int:
    parsed = O.parse_description(P.CR_ZETA_KRGF_DESC)
    res = P.prep(parsed, "m-cr-zeta-krgf",
                 "https://www.youtube.com/watch?v=is7eHd0nf84",
                 "owcs-is7ehd0nf84", write=False)

    print("prep resolves the match skeleton (dry-run, nothing written):")
    check("ok + not written", res.get("ok") and res.get("written") is False)
    check("teams resolved to existing ids", res["teamA"] == "cr" and res["teamB"] == "zeta")
    check("match id carried", res["matchId"] == "m-cr-zeta-krgf")
    check("all six maps have a catalog id (none left null)",
          len(res["maps"]) == 6 and all(m["mapId"] for m in res["maps"]))
    check("every map carries its VOD start second",
          [m["start"] for m in res["maps"]] == [0, 800, 1740, 2475, 3530, 4160])
    check("POV-independent signature present",
          res["signature"] == "crazyraccoon--zetadivision--2026-07-12")
    check("actions describe the writes without performing them",
          any("upsert match" in a for a in res["actions"])
          and any("map 1: Antarctic" in a for a in res["actions"]))

    print("bad teams are refused, not guessed:")
    bad = O.parse_description("Nobody FC vs Ghost Team | E\nMatch Date: July 1, 2026\n\nNepal: 0:00\n")
    r2 = P.prep(bad, "m-x", None, None, write=False)
    check("unresolvable teams -> ok False with a clear error",
          r2.get("ok") is False and "resolve teams" in r2.get("error", ""))

    print()
    if FAILS:
        print(f"FAILED: {FAILS} check(s)")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
