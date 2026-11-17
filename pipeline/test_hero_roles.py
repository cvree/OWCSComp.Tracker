#!/usr/bin/env python3
"""Offline tests for mid-season role changes (pipeline/hero_roles.py) and
the current map catalog: a reworked hero (Sombra, Damage -> Support with
Season 5 on 2026-10-06) is read in either role, and the season's newest
maps are in the reference catalog with their real modes."""
from __future__ import annotations
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import comp_solver as C  # noqa: E402
import hero_roles as R  # noqa: E402
from external_sheet.aliases import AliasBook  # noqa: E402

FAILS = 0


def check(name, ok):
    global FAILS
    if not ok:
        FAILS += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")


def slot(top, score, scores):
    return {"hero": top, "score": score, "scores": scores}


def main() -> int:
    sample = json.load(open(os.path.join(HERE, "sample_data.json"), encoding="utf-8"))
    roles = {h["id"]: h["role"] for h in sample["heroes"]}
    maps = {m["id"]: m for m in sample["maps"]}

    print("catalog is current:")
    check("Sombra is Support today", roles.get("sombra") == "Support")
    check("Watchpoint: Grímsvötn is an Escort map",
          maps.get("grimsvotn", {}).get("mode") == "Escort"
          and maps["grimsvotn"]["name"] == "Watchpoint: Grímsvötn")
    check("Neon Junction is a Hybrid map", maps.get("neonjunction", {}).get("mode") == "Hybrid")
    check("every map has a real mode",
          all(m["mode"] in ("Control", "Escort", "Hybrid", "Push", "Flashpoint", "Clash")
              for m in sample["maps"]))

    print("role history:")
    check("Sombra has held Damage and Support",
          R.roles_held("sombra", "Support") == {"Damage", "Support"})
    check("an unchanged hero holds one role", R.roles_held("tracer", "Damage") == {"Damage"})
    check("Sombra was Damage before Season 5", R.role_on("sombra", "2026-08-01", "Support") == "Damage")
    check("Sombra is Support from Season 5", R.role_on("sombra", "2026-10-06", "Support") == "Support")
    check("no date means today's role", R.role_on("sombra", None, "Support") == "Support")

    print("comp solver accepts a reworked hero in either role:")
    base = [slot("mauga", .8, {"mauga": .8}), slot("tracer", .8, {"tracer": .8})]
    old = C.solve(base + [slot("sombra", .8, {"sombra": .8}),
                          slot("lucio", .8, {"lucio": .8}), slot("kiriko", .8, {"kiriko": .8})], roles)
    check("pre-rework comp (Sombra as Damage) is legal",
          old["valid"] and old["slots"][2]["role"] == "Damage")
    new = C.solve(base + [slot("sojourn", .8, {"sojourn": .8}),
                          slot("sombra", .8, {"sombra": .8}), slot("kiriko", .8, {"kiriko": .8})], roles)
    check("post-rework comp (Sombra as Support) is legal",
          new["valid"] and new["slots"][3]["role"] == "Support")

    print("sheet importer takes the sheet's own role for a reworked hero:")
    book = AliasBook.build({}, {"heroes": sample["heroes"]})
    check("Damage column -> Damage", book.role_of("sombra", "Damage") == "Damage")
    check("Support icon -> Support", book.role_of("sombra", "support") == "Support")
    check("no hint, old date -> Damage", book.role_of("sombra", None, "2026-05-01") == "Damage")
    check("no hint, no date -> today's role", book.role_of("sombra") == "Support")
    check("unchanged hero ignores the hint", book.role_of("tracer", "Support") == "Damage")

    print()
    if FAILS:
        print(f"FAILED: {FAILS} check(s)")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
