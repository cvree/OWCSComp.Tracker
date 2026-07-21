#!/usr/bin/env python3
"""
test_apply_match_facts.py — manual FACEIT facts apply correctly, validate,
stay idempotent, create NO comps, appear in export, and run_batch calls the
step in the right order.
"""
from __future__ import annotations
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

TEST_DB = os.path.join(ROOT, "work", "test_facts", "t.sqlite")
os.environ["OWCS_DB"] = TEST_DB
import db  # noqa: E402
import init_db  # noqa: E402
import apply_match_facts as amf  # noqa: E402
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


def write(entries):
    p = os.path.join(os.path.dirname(TEST_DB), "facts.json")
    json.dump({"matchFacts": entries}, open(p, "w"))
    return p


def main():
    con = setup()
    teams = con.execute("SELECT team_a, team_b FROM matches WHERE id='m12'").fetchone()
    ta, tb = teams["team_a"], teams["team_b"]

    print("valid facts apply, no comps created")
    comps_before = con.execute("SELECT COUNT(*) c FROM comp_snapshots WHERE match_id='m12'").fetchone()["c"]
    p = write([{"match": "m12", "maps": [{
        "mapOrder": 1, "replayCode": "ZZ11AA", "pickedByTeam": ta, "vetoAction": "pick",
        "heroBans": [{"team": ta, "hero": "tracer", "order": 1},
                     {"team": tb, "hero": "juno", "order": 2}],
        "notes": "test"}]}])
    amf.apply_file(con, p)
    mr = con.execute("SELECT id, replay_code, picked_by_team, veto_action, notes "
                     "FROM map_results WHERE match_id='m12' AND map_order=1").fetchone()
    check("replay code set", mr["replay_code"] == "ZZ11AA")
    check("pickedBy + veto set", mr["picked_by_team"] == ta and mr["veto_action"] == "pick")
    check("2 manual_facts bans", con.execute(
        "SELECT COUNT(*) c FROM hero_bans WHERE map_result_id=? AND source='manual_facts'",
        (mr["id"],)).fetchone()["c"] == 2)
    comps_after = con.execute("SELECT COUNT(*) c FROM comp_snapshots WHERE match_id='m12'").fetchone()["c"]
    check("NO comp snapshots created", comps_before == comps_after)

    print("idempotent re-apply (bans stay 2, not 4)")
    amf.apply_file(con, p)
    check("still 2 bans", con.execute(
        "SELECT COUNT(*) c FROM hero_bans WHERE map_result_id=? AND source='manual_facts'",
        (mr["id"],)).fetchone()["c"] == 2)

    print("invalid rows skipped, valid still applied")
    p2 = write([
        {"match": "nope", "maps": [{"mapOrder": 1, "replayCode": "X"}]},      # bad match
        {"match": "m12", "maps": [{"mapOrder": 99, "replayCode": "Y"}]},      # bad map
        {"match": "m12", "maps": [{"mapOrder": 1,
            "heroBans": [{"team": ta, "hero": "NOTAHERO"}]}]},                # bad hero
        {"match": "m12", "maps": [{"mapOrder": 2, "replayCode": "GOOD12"}]},  # valid
    ])
    ok, bad = amf.apply_file(con, p2)
    check("1 valid applied", ok == 1)
    check("3 skipped", bad == 3)
    check("valid one landed", con.execute(
        "SELECT replay_code FROM map_results WHERE match_id='m12' AND map_order=2"
        ).fetchone()["replay_code"] == "GOOD12")

    print("facts appear in export map.faceit, tracker.detected stays false")
    payload = export_data.build_payload(con)
    m = next(x for x in payload["matches"] if x["id"] == "m12")
    g = next(x for x in m["maps"] if x["faceit"]["mapOrder"] == 1)
    check("export replay code", g["faceit"]["replayCode"] == "ZZ11AA")
    check("export factSource manual_facts", g["faceit"]["factSource"] == "manual_facts")
    check("facts did NOT flip tracker.detected", g["tracker"]["detected"] is False)

    print("run_batch calls apply_match_facts between ingest and corrections")
    import run_batch
    order = []
    saved = {k: getattr(run_batch, k) for k in
             ("step_init", "step_ingest", "step_match_facts",
              "step_corrections", "step_validate", "step_export")}
    run_batch.step_init = lambda con, with_sample: order.append("init")
    run_batch.step_ingest = lambda *a, **k: order.append("ingest") or {}
    run_batch.step_match_facts = lambda con: order.append("match_facts")
    run_batch.step_corrections = lambda con: order.append("corrections")
    run_batch.step_validate = lambda con, strict: order.append("validate") or 0
    run_batch.step_export = lambda con, allow_empty=False: order.append("export")
    sys.argv = ["run_batch", "--skip-ingest"]
    # keep ingest step recorded despite skip flag: emulate by not skipping
    sys.argv = ["run_batch", "--offline"]
    try:
        run_batch.main()
    finally:
        for k, v in saved.items():
            setattr(run_batch, k, v)
    check("order has match_facts after ingest, before corrections",
          order == ["init", "ingest", "match_facts", "corrections", "validate", "export"])

    shutil.rmtree(os.path.dirname(TEST_DB), ignore_errors=True)
    if _fails:
        print(f"\n{_fails} MATCH-FACTS TEST(S) FAILED"); sys.exit(1)
    print("\nALL MATCH-FACTS TESTS PASSED")


if __name__ == "__main__":
    main()
