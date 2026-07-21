#!/usr/bin/env python3
"""Offline tests for the role-aware 1/2/2 composition solver."""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comp_solver as C  # noqa: E402

FAILS = 0
ROLES = {"mauga": "Tank", "dva": "Tank", "winston": "Tank",
         "shion": "Damage", "sojourn": "Damage", "sym": "Damage",
         "tracer": "Damage", "cass": "Damage",
         "lucio": "Support", "kiriko": "Support", "juno": "Support",
         "ana": "Support"}


def check(name, ok):
    global FAILS
    if not ok:
        FAILS += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")


def slot(top, score, scores):
    return {"hero": top, "score": score, "scores": scores}


def main() -> int:
    print("all-confident, already legal 1/2/2:")
    r = C.solve([
        slot("mauga", .8, {"mauga": .8}),
        slot("shion", .8, {"shion": .8}),
        slot("sym", .8, {"sym": .8}),
        slot("lucio", .8, {"lucio": .8}),
        slot("kiriko", .8, {"kiriko": .8}),
    ], ROLES)
    check("valid", r["valid"] is True and r["anomaly"] is None)
    check("roles are 1/2/2", r["roles"] == {"Tank": 1, "Damage": 2, "Support": 2})
    check("every slot is direct", all(s["source"] == "direct" for s in r["slots"]))

    print("the struggled a5 case — UNKNOWN support inferred by constraint:")
    r = C._demo()
    check("comp is valid + complete", r["valid"] and "UNKNOWN" not in r["heroes"])
    check("exactly one slot was role-inferred", r["inferred"] == 1)
    s5 = r["slots"][4]
    check("a5 filled with the best SUPPORT (kiriko), not the damage reads",
          s5["hero"] == "kiriko" and s5["role"] == "Support")
    check("a5 reported at its real score, marked role-inferred",
          s5["source"] == "role-inferred" and s5["raw"] == "UNKNOWN"
          and abs(s5["score"] - 0.47) < 1e-6)

    print("no hero is used twice:")
    r = C.solve([
        slot("mauga", .8, {"mauga": .8}),
        slot("shion", .7, {"shion": .7, "sym": .69}),
        slot("sym", .68, {"sym": .68, "shion": .5}),
        slot("lucio", .8, {"lucio": .8}),
        slot("kiriko", .8, {"kiriko": .8}),
    ], ROLES)
    heroes = [s["hero"] for s in r["slots"]]
    check("resolved comp has 5 distinct heroes", len(set(heroes)) == 5)

    print("anomaly: two strong tank reads can't both be right in 1/2/2:")
    r = C.solve([
        slot("mauga", .82, {"mauga": .82, "shion": .2}),
        slot("dva", .80, {"dva": .80, "sym": .2}),      # 2nd strong tank
        slot("sym", .8, {"sym": .8}),
        slot("lucio", .8, {"lucio": .8}),
        slot("kiriko", .8, {"kiriko": .8}),
    ], ROLES)
    check("flagged as an anomaly (a strong read was overruled)",
          r["anomaly"] is not None and not r["valid"])
    check("anomaly names the overruled strong read",
          "overruled" in r["anomaly"])

    print("honest incompleteness: the 5th support is below the infer floor:")
    r = C.solve([
        slot("mauga", .8, {"mauga": .8}),
        slot("shion", .8, {"shion": .8}),
        slot("sym", .8, {"sym": .8}),
        slot("lucio", .8, {"lucio": .8}),
        # a legal support exists (ana) but only at 0.22 < INFER_FLOOR 0.30
        slot("UNKNOWN", .24, {"tracer": .24, "cass": .18, "ana": .22}),
    ], ROLES)
    check("5th slot stays UNKNOWN (below infer floor), comp not valid",
          r["slots"][4]["hero"] == "UNKNOWN" and not r["valid"])
    check("confidence still reported from the confident slots",
          r["confidence"] is not None)

    print("ingest integration: resolve_sides runs the solver per team:")
    import ingest_map  # noqa: E402
    slots = {
        "a1": slot("mauga", .7, {"mauga": .7}),
        "a2": slot("shion", .6, {"shion": .6}),
        "a3": slot("sym", .64, {"sym": .64}),
        "a4": slot("lucio", .66, {"lucio": .66}),
        "a5": slot("UNKNOWN", .53, {"sojourn": .53, "sym": .50, "kiriko": .47}),
        "b1": slot("winston", .7, {"winston": .7}),
        "b2": slot("tracer", .6, {"tracer": .6}),
        "b3": slot("cass", .6, {"cass": .6}),
        "b4": slot("ana", .6, {"ana": .6}),
        "b5": slot("juno", .6, {"juno": .6}),
    }
    res = ingest_map.resolve_sides(slots, ROLES)
    check("both sides resolved", set(res) == {"a", "b"})
    check("side a completed the UNKNOWN support seat",
          res["a"]["valid"] and "UNKNOWN" not in res["a"]["heroes"]
          and res["a"]["slots"][4]["role"] == "Support")

    print("determinism: same input -> same output:")
    a = C._demo(); b = C._demo()
    check("stable", a == b)

    print("custom target (e.g. open-queue 6-stack would differ) is honored:")
    r = C.solve([
        slot("mauga", .8, {"mauga": .8}),
        slot("dva", .8, {"dva": .8}),
        slot("sym", .8, {"sym": .8}),
        slot("lucio", .8, {"lucio": .8}),
        slot("kiriko", .8, {"kiriko": .8}),
    ], ROLES, target={"Tank": 2, "Damage": 1, "Support": 2})
    check("2/1/2 target respected", r["roles"] == {"Tank": 2, "Damage": 1, "Support": 2})

    print()
    if FAILS:
        print(f"FAILED: {FAILS} check(s)")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
