#!/usr/bin/env python3
"""
test_corrections.py — proves manual corrections apply, override CV without
deleting it, validate inputs, and export as source='manual'. No network.
"""
from __future__ import annotations
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

TEST_DB = os.path.join(ROOT, "work", "test_corr", "t.sqlite")
os.environ["OWCS_DB"] = TEST_DB
import db  # noqa: E402
import init_db  # noqa: E402
import apply_corrections  # noqa: E402
import export_data  # noqa: E402

_fails = 0


def check(name, cond):
    global _fails
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        _fails += 1


def setup():
    shutil.rmtree(os.path.dirname(TEST_DB), ignore_errors=True)
    os.makedirs(os.path.dirname(TEST_DB), exist_ok=True)
    con = db.connect()
    db.init_schema(con)
    data = init_db.load_sample()
    init_db.seed_reference(con, data)
    init_db.seed_sample_matches(con, data)
    init_db.seed_sample_rosters(con, data)
    return con


def write_corr(entries):
    d = os.path.join(ROOT, "corrections")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "corrections.test.json")
    json.dump({"corrections": entries}, open(path, "w"))
    return path


def main():
    con = setup()
    # find a sample match+map that has a CV comp for team A
    row = con.execute(
        """SELECT cs.match_id, mr.map_order, cs.team_id
           FROM comp_snapshots cs JOIN map_results mr ON mr.id=cs.map_result_id
           WHERE cs.source='sample' LIMIT 1""").fetchone()
    match_id, order, team = row["match_id"], row["map_order"], row["team_id"]

    print("valid correction overrides CV without deleting it")
    cv_before = con.execute(
        "SELECT COUNT(*) c FROM comp_snapshots WHERE match_id=? AND source='sample'",
        (match_id,)).fetchone()["c"]
    path = write_corr([{
        "match": match_id, "mapOrder": order, "team": team,
        "openerComp": ["dva", "sojourn", "tracer", "ana", "lucio"],
        "note": "override"}])
    apply_corrections.main_from_file(path)
    cv_after = con.execute(
        "SELECT COUNT(*) c FROM comp_snapshots WHERE match_id=? AND source='sample'",
        (match_id,)).fetchone()["c"]
    man = con.execute(
        "SELECT COUNT(*) c FROM comp_snapshots WHERE match_id=? AND team_id=? "
        "AND source='manual'", (match_id, team)).fetchone()["c"]
    check("CV rows not deleted", cv_before == cv_after)
    check("manual snapshot written", man >= 1)

    print("export prefers manual over CV for that team")
    payload = export_data.build_payload(con)
    m = next(x for x in payload["matches"] if x["id"] == match_id)
    g = next(x for x in m["maps"] if x["faceit"]["mapOrder"] == order)
    side = "A" if team == m["teamA"] else "B"
    played = g["tracker"][f"playedHeroes{side}"]
    src = g["tracker"][f"source{side}"]
    check("exported comp is the manual one",
          sorted(played) == sorted(["dva", "sojourn", "tracer", "ana", "lucio"]))
    check("exported per-team source is manual", src == "manual")

    print("invalid corrections are skipped, valid ones still apply")
    path = write_corr([
        {"match": match_id, "mapOrder": order, "team": team,
         "openerComp": ["dva", "sojourn"]},                    # too few
        {"match": "no-such-match", "mapOrder": 1, "team": team,
         "openerComp": ["dva", "sojourn", "tracer", "ana", "lucio"]},  # bad ref
        {"match": match_id, "mapOrder": order, "team": team,
         "openerComp": ["ana", "ana", "tracer", "genji", "juno"]},     # dupe
    ])
    con2 = db.connect()
    ok, bad = apply_corrections.apply_file(con2, path, dry_run=True)
    check("all 3 invalid entries rejected in dry-run", ok == 0 and bad == 3)

    shutil.rmtree(os.path.dirname(TEST_DB), ignore_errors=True)
    os.remove(path)
    if _fails:
        print(f"\n{_fails} CORRECTION TEST(S) FAILED")
        sys.exit(1)
    print("\nALL CORRECTION TESTS PASSED")


if __name__ == "__main__":
    main()
