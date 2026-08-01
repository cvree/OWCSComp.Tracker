#!/usr/bin/env python3
"""
test_hero_board.py — the hero board's maths, executed for real.

`assets/js/public/stats.js` is run in Node against a synthetic dataset, so
these are behavioural checks on the actual shipped code rather than string
matches against it. The properties asserted here are the ones that make a
ranked hero board honest on a dataset this small:

  * a hero with no verified pick carries NULLS, never 0% — zero is a claim
    about the hero, "not sighted" is the truth about the coverage;
  * win rate is ranked by the Wilson lower bound, so 1-0 cannot outrank
    8-4, which is the entire reason the board can be sorted at all;
  * sample size is graded and reported, never hidden behind a tier letter;
  * only CONFIRMED swaps count — the rejection ledger is evidence that
    something did not happen and must never become a statistic;
  * every filter that applies to picks applies identically to swaps.

Requires Node (already needed for nothing else here, so it SKIPS cleanly
when Node is absent rather than failing a machine that only runs Python).

Run:  python pipeline/test_hero_board.py   (non-zero on failure)
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

_fails = 0


def check(name: str, cond: bool) -> None:
    global _fails
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        _fails += 1


# A dataset built to make each property visible:
#   winsalot  8-4  — a real sample
#   luckyshot 1-0  — a perfect record on one map
#   tagalong  0-1
#   ghost          — in the pool, never picked
FIXTURE = {
    "meta": {"schema": "public.v1"},
    "regions": [{"id": "all", "name": "All", "short": "All"},
                {"id": "na", "name": "North America", "short": "NA"}],
    "teams": [{"id": "ta", "name": "Team A", "code": "TA"},
              {"id": "tb", "name": "Team B", "code": "TB"}],
    "tournaments": [{"id": "t1", "name": "Cup", "region": "na"}],
    "heroes": [{"id": "winsalot", "name": "Winsalot", "role": "Tank"},
               {"id": "luckyshot", "name": "Luckyshot", "role": "Damage"},
               {"id": "tagalong", "name": "Tagalong", "role": "Support"},
               {"id": "ghost", "name": "Ghost", "role": "Support"}],
    "mapsCatalog": [{"id": "nepal", "name": "Nepal", "mode": "Control"}],
    "heroBans": [], "rejectedSwaps": [], "heroStints": [], "captureRuns": [],
    "vodSources": [], "players": [], "calendarEvents": [], "mapResults": [],
    "matches": [], "compSnapshots": [], "heroSwaps": [],
}

# 12 decided maps. winsalot is on every one and wins 8; luckyshot appears on
# exactly one (a win); tagalong on exactly one (a loss).
for i in range(12):
    map_id = f"m{i}-1"
    winner = "ta" if i < 8 else "tb"
    FIXTURE["matches"].append({
        "id": f"m{i}", "tournamentId": "t1", "teamA": "ta", "teamB": "tb",
        "status": "completed",
        "maps": [{"id": map_id, "order": 1, "map": "nepal", "mode": "Control",
                  "winner": winner}],
    })
    heroes = ["winsalot"]
    if i == 0:
        heroes.append("luckyshot")          # on a map team A won
    if i == 8:
        heroes.append("tagalong")           # on a map team A lost
    FIXTURE["compSnapshots"].append({
        "id": f"cs{i}", "matchId": f"m{i}", "mapId": map_id, "teamId": "ta",
        "side": "a", "timestamp": 100, "heroes": heroes,
        "source": "cv", "confidence": 0.95, "reviewStatus": "auto-high",
    })

# One confirmed swap and one rejected one on the SAME pair, so the two are
# only distinguishable if the code actually filters on status.
FIXTURE["heroSwaps"] = [
    {"id": "sw1", "matchId": "m0", "mapId": "m0-1", "teamId": "ta",
     "slot": 1, "fromHero": "tagalong", "toHero": "luckyshot",
     "offset": 500, "status": "confirmed"},
    {"id": "sw2", "matchId": "m1", "mapId": "m1-1", "teamId": "ta",
     "slot": 2, "fromHero": "tagalong", "toHero": "luckyshot",
     "offset": 600, "status": "rejected",
     "reason": "seen once then reverted — noise"},
]

DRIVER = r"""
const fs = require('fs'), vm = require('vm');
const root = process.argv[2], fixture = process.argv[3];
const ctx = { console, Math, Object, Array, Map, Set, Number, String, JSON,
              Date, isNaN, parseInt, parseFloat, window: {} };
ctx.window.window = ctx.window;
ctx.globalThis = ctx;
vm.createContext(ctx);
ctx.window.OWCS_PUBLIC = JSON.parse(fs.readFileSync(fixture, 'utf8'));
// core.js wires DOM listeners at load; the stats layer only needs its
// lookup helpers, so run it and tolerate the DOM half failing.
try {
  vm.runInContext(fs.readFileSync(root + '/assets/js/public/core.js', 'utf8'),
                  ctx, { filename: 'core.js' });
} catch (e) { /* DOM-only tail — the P.* helpers above it are defined */ }
vm.runInContext(fs.readFileSync(root + '/assets/js/public/stats.js', 'utf8'),
                ctx, { filename: 'stats.js' });
const S = ctx.window.OWCS_STATS;
const board = S.heroBoard({});
const by = {};
board.rows.forEach((r) => { by[r.heroId] = r; });
const sortByConfidence = board.rows.slice()
  .filter((r) => r.confidence != null)
  .sort((a, b) => b.confidence - a.confidence)
  .map((r) => r.heroId);
const sortByRawWinRate = board.rows.slice()
  .filter((r) => r.winRate != null)
  .sort((a, b) => b.winRate - a.winRate)
  .map((r) => r.heroId);
console.log(JSON.stringify({
  board: { seenCount: board.seenCount, poolCount: board.poolCount,
           totalAppearances: board.totalAppearances,
           swapCount: board.swapCount },
  rows: by,
  sortByConfidence, sortByRawWinRate,
  wilson: { perfect1: S.wilsonLower(1, 1), solid: S.wilsonLower(8, 12),
            none: S.wilsonLower(0, 0) },
  grades: [0, 1, 2, 3, 9, 10, 40].map((n) => [n, S.sampleGrade(n)]),
  maps: S.heroMaps('winsalot', {}),
  filteredOtherTeam: S.heroBoard({ teamId: 'tb' }).seenCount,
  swapsOtherTeam: S.heroBoard({ teamId: 'tb' }).swapCount,
}));
"""


def main() -> int:
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        print("  SKIP  node is not installed — hero-board maths not exercised")
        return 0

    tmp = tempfile.mkdtemp(prefix="owcs_board_")
    fixture = os.path.join(tmp, "fixture.json")
    with open(fixture, "w", encoding="utf-8") as f:
        json.dump(FIXTURE, f)
    driver = os.path.join(tmp, "driver.js")
    with open(driver, "w", encoding="utf-8") as f:
        f.write(DRIVER)

    res = subprocess.run([node, driver, ROOT, fixture],
                         capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        print("  FAIL  stats.js could not be executed")
        print((res.stderr or "")[-1500:])
        return 1
    out = json.loads(res.stdout)
    rows, board = out["rows"], out["board"]

    print("the whole roster is on the board, not only what has data:")
    check("every hero in the pool gets a row",
          board["poolCount"] == 4 and set(rows) ==
          {"winsalot", "luckyshot", "tagalong", "ghost"})
    check("only heroes with a verified pick count as sighted",
          board["seenCount"] == 3 and rows["ghost"]["seen"] is False)

    print("a hero with no verified pick is absent, never zero:")
    g = rows["ghost"]
    check("pick rate is null, not 0", g["pickRate"] is None)
    check("win rate is null, not 0", g["winRate"] is None)
    check("confidence is null, not 0", g["confidence"] is None)
    check("its sample grade says 'none'", g["grade"] == "none")

    print("win rate is ranked by what the evidence supports:")
    w = out["wilson"]
    check("a perfect 1-0 scores BELOW a solid 8-12",
          w["perfect1"] < w["solid"])
    check("nothing decided scores nothing at all", w["none"] is None)
    check("raw win rate would put the 1-0 hero first (the trap)",
          out["sortByRawWinRate"][0] == "luckyshot")
    check("the board's own ranking puts the real sample first",
          out["sortByConfidence"][0] == "winsalot")
    check("the raw percentages are still reported honestly",
          rows["luckyshot"]["winRate"] == 1.0
          and abs(rows["winsalot"]["winRate"] - 8 / 12) < 1e-9)
    check("W-L is carried so the reader can check the arithmetic",
          rows["winsalot"]["wins"] == 8 and rows["winsalot"]["losses"] == 4
          and rows["winsalot"]["decided"] == 12)

    print("sample size is graded and reported, never hidden:")
    grades = dict((n, gr) for n, gr in out["grades"])
    check("0 appearances -> none", grades[0] == "none")
    check("below the floor -> thin", grades[1] == "thin" and grades[2] == "thin")
    check("at the floor -> some", grades[3] == "some" and grades[9] == "some")
    check("at the solid mark -> solid",
          grades[10] == "solid" and grades[40] == "solid")
    check("the 12-map hero reads as the solid one",
          rows["winsalot"]["grade"] == "solid"
          and rows["luckyshot"]["grade"] == "thin")

    print("the rejection ledger never becomes a statistic:")
    check("only the CONFIRMED swap is counted", board["swapCount"] == 1)
    check("it is counted once in and once out",
          rows["luckyshot"]["swapsIn"] == 1
          and rows["tagalong"]["swapsOut"] == 1)
    check("the rejected candidate adds nothing to either hero",
          rows["luckyshot"]["swapsIn"] + rows["tagalong"]["swapsOut"] == 2)

    print("filters apply to every metric, not just picks:")
    check("filtering to a team with no verified comps empties the board",
          out["filteredOtherTeam"] == 0)
    check("...and empties the swap count with it",
          out["swapsOtherTeam"] == 0)

    print("per-hero map breakdown is evidence-backed:")
    maps = out["maps"]
    check("the map a hero was actually played on is named",
          len(maps) == 1 and maps[0]["mapId"] == "nepal"
          and maps[0]["name"] == "Nepal")
    check("its record matches the hero's overall record",
          maps[0]["picks"] == 12 and maps[0]["wins"] == 8
          and maps[0]["losses"] == 4)

    print()
    print("ALL PASS" if not _fails else f"{_fails} FAILURES")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
