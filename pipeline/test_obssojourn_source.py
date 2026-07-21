#!/usr/bin/env python3
"""Offline tests for the ObsSojourn POV-source parser + de-dup logic."""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obssojourn_source as O  # noqa: E402

FAILS = 0


def check(name, ok):
    global FAILS
    if not ok:
        FAILS += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")


def main() -> int:
    p = O.parse_description(O.DEMO_DESC)

    print("match identity from the description:")
    check("teams parsed", p["teamA"] == "Crazy Raccoon" and p["teamB"] == "ZETA Division")
    check("event parsed", "Grand Final" in (p["event"] or ""))
    check("date normalized to ISO", p["date"] == "2026-07-12")
    check("POV player's heroes parsed",
          p["heroesPlayed"] == ["Mizuki", "Juno", "Jetpack Cat", "Lucio"])

    print("map segmentation from the timestamps:")
    check("six map chapters", len(p["maps"]) == 6)
    check("windows chain start->next start",
          p["maps"][0]["start"] == 0 and p["maps"][0]["end"] == p["maps"][1]["start"])
    check("H:MM:SS parsed (1:09:20 -> 4160s)", p["maps"][5]["start"] == 4160)
    check("last map runs to video end (end is None)", p["maps"][5]["end"] is None)
    check("known maps resolved to catalog ids",
          p["maps"][0]["mapId"] == "antarctic" and p["maps"][2]["mapId"] == "kingsrow"
          and p["maps"][3]["mapId"] == "circuit" and p["maps"][4]["mapId"] == "colosseo"
          and p["maps"][1]["mapId"] == "njc")

    print("a brand-new season map is flagged, never silently dropped:")
    check("Neon Junction has no id and is listed as unknown",
          p["maps"][5]["mapId"] is None and "Neon Junction" in p["unknownMaps"])

    print("social links / non-chapter timestamps are ignored:")
    noisy = O.DEMO_DESC + "\nTwitter: 12:34\nsome video 5:00\n"
    p2 = O.parse_description(noisy)
    check("still exactly six maps (chapter run only)", len(p2["maps"]) == 6)

    print("match signature is POV-independent (THE de-dup key):")
    # a different POV of the SAME match: different heroes, teams could even be
    # listed in the other order — signature must match
    other = O.parse_description(
        "Heroes played: Winston, D.Va\n"
        "ZETA Division vs Crazy Raccoon | OWCS Korea Stage 2 Grand Final\n"
        "Match Date: July 12, 2026\n\nAntarctic Peninsula: 0:00\n")
    check("two POVs of the same game share a signature",
          O.is_same_match(p, other))
    check("signature is order-independent on team names",
          p["signature"] == other["signature"])

    print("a different match does NOT collapse:")
    diff = O.parse_description(
        "Crazy Raccoon vs ZETA Division | OWCS Champions Clash\n"
        "Match Date: May 23, 2026\n\nNepal: 0:00\n")
    check("different date -> different match", not O.is_same_match(p, diff))

    print("comp merge key de-dups identical comps across POVs:")
    k1 = O.comp_merge_key("m1", "antarctic", "cr", 1, ["tracer", "cass", "ball", "lucio", "mizuki"])
    k2 = O.comp_merge_key("m1", "antarctic", "cr", 1, ["mizuki", "lucio", "ball", "cass", "tracer"])
    check("same comp, any slot order -> same key", k1 == k2)
    k3 = O.comp_merge_key("m1", "antarctic", "cr", 2, ["tracer", "cass", "ball", "lucio", "mizuki"])
    check("a different round -> different key", k1 != k3)

    print("POV role hint from the recorded heroes:")
    roles = {"mizuki": "Support", "juno": "Support", "jetpackcat": "Support", "lucio": "Support"}
    check("all-support POV detected", O.pov_role(p["heroesPlayed"], roles) == "Support")

    print("robustness: a description with no timestamps still parses:")
    p3 = O.parse_description("Team A vs Team B | Event\nMatch Date: July 1, 2026\n")
    check("no crash, empty maps", p3["maps"] == [] and p3["teamA"] == "Team A")

    print()
    if FAILS:
        print(f"FAILED: {FAILS} check(s)")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
