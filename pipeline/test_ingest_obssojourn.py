#!/usr/bin/env python3
"""Offline tests for the ObsSojourn per-match ingest orchestrator (plan
building only — never launches ingest_map). Reads the committed DB, which
carries the seeded m-cr-zeta-krgf skeleton."""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import ingest_obssojourn as I  # noqa: E402

FAILS = 0


def check(name, ok):
    global FAILS
    if not ok:
        FAILS += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")


def main() -> int:
    con = db.connect()

    print("plan covers every seeded map window, in order:")
    plan = I.build_plan(con, "m-cr-zeta-krgf", "work/clips/krgf.mp4", 5.0,
                        None, end_last=5490, write=False, extra=[])
    check("ok", plan.get("ok"))
    check("six maps", len(plan["steps"]) == 6)
    check("teams resolved from the match row", plan["teamA"] == "cr" and plan["teamB"] == "zeta")
    check("layout from the registered source", plan["layout"] == "layouts/obssojourn_pov.json")
    s1 = plan["steps"][0]
    check("map 1 window is 0-800", s1["window"] == [0, 800] and s1["mapId"] == "antarctic")
    check("windows chain to the next map's start",
          [s["window"] for s in plan["steps"]][:2] == [[0, 800], [800, 1740]])
    check("last map ends at --end-last", plan["steps"][5]["window"] == [4160, 5490])

    print("each step is a correct ingest_map.py command:")
    cmd = " ".join(s1["cmd"])
    check("names the match + map order + map id + teams",
          "--match m-cr-zeta-krgf" in cmd and "--map-order 1" in cmd
          and "--map-id antarctic" in cmd and "--team-a cr --team-b zeta" in cmd)
    check("ingest id is <match>-m<order>", "--ingest-id m-cr-zeta-krgf-m1" in cmd)
    check("dry-run has no --write", "--write" not in cmd)

    print("--write adds --write to every step:")
    w = I.build_plan(con, "m-cr-zeta-krgf", "c.mp4", 5.0, None, 5490, True, [])
    check("all steps carry --write", all("--write" in s["cmd"] for s in w["steps"]))

    print("--maps filter + passthrough extras:")
    f = I.build_plan(con, "m-cr-zeta-krgf", "c.mp4", 5.0, {2, 4}, 5490, False, ["--ocr-guard"])
    check("only the requested maps", [s["order"] for s in f["steps"]] == [2, 4])
    check("extra args passed through", "--ocr-guard" in " ".join(f["steps"][0]["cmd"]))

    print("honest errors, never a wrong guess:")
    e1 = I.build_plan(con, "m-does-not-exist", "c.mp4", 5.0, None, 5490, False, [])
    check("unknown match -> clear error", not e1["ok"] and "unknown match" in e1["error"])

    print()
    if FAILS:
        print(f"FAILED: {FAILS} check(s)")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
