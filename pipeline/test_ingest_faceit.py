#!/usr/bin/env python3
"""
test_ingest_faceit.py — proves the FACEIT ingest writes facts correctly,
is idempotent, preserves tracker comps, and keeps FACEIT-only maps
tracker-undetected. No network. Exits nonzero on failure.
"""
from __future__ import annotations
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

TEST_DB = os.path.join(ROOT, "work", "test_ingest", "t.sqlite")
os.environ["OWCS_DB"] = TEST_DB
import db  # noqa: E402
import init_db  # noqa: E402
import faceit_parser as fp  # noqa: E402
import ingest_faceit as ing  # noqa: E402
import apply_corrections  # noqa: E402
import export_data  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "faceit")
_fails = 0


def check(name, cond):
    global _fails
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        _fails += 1


_open_cons: list = []


def fresh_db():
    # Close any connection from a previous test first. On Windows an open
    # SQLite file cannot be deleted, so the rmtree below would silently fail
    # (ignore_errors=True) and the "fresh" db would still hold stale rows.
    while _open_cons:
        try:
            _open_cons.pop().close()
        except Exception:
            pass
    shutil.rmtree(os.path.dirname(TEST_DB), ignore_errors=True)
    os.makedirs(os.path.dirname(TEST_DB), exist_ok=True)
    con = db.connect()
    _open_cons.append(con)
    db.init_schema(con)
    data = init_db.load_sample()
    init_db.seed_reference(con, data)
    return con


def load_fix(fn):
    return json.load(open(os.path.join(FIX, fn), encoding="utf-8"))


def ingest_fix(con, fn, url, region="EMEA", dry=False):
    parsed = fp.parse_faceit_room_json(load_fix(fn), url)
    return ing.ingest(con, parsed, url, region, dry_run=dry)


def test_fixture_ingests():
    print("fixture ingests into SQLite")
    con = fresh_db()
    counts, warns = ingest_fix(con, "room_full.json",
                               "https://faceit.com/en/ow2/room/1-abc12345")
    mid = "faceit-1-abc12345-0000-0000-0000-fixturefull01"
    m = con.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
    check("match row written", m is not None)
    check("final score stored", m["score_a"] == 3 and m["score_b"] == 1)
    check("winner resolved", m["winner_team"] == m["team_a"])
    check("4 map_results", con.execute(
        "SELECT COUNT(*) c FROM map_results WHERE match_id=?", (mid,)).fetchone()["c"] == 4)
    check("4 replay codes stored", con.execute(
        "SELECT COUNT(*) c FROM map_results WHERE match_id=? AND replay_code IS NOT NULL",
        (mid,)).fetchone()["c"] == 4)
    check("4 hero bans stored", con.execute(
        "SELECT COUNT(*) c FROM hero_bans WHERE match_id=?", (mid,)).fetchone()["c"] == 4)
    check("7 roster players", con.execute(
        "SELECT COUNT(*) c FROM match_rosters WHERE match_id=?", (mid,)).fetchone()["c"] == 7)
    check("no warnings on clean fixture", warns == [])


def test_dry_run_writes_nothing():
    print("dry-run writes nothing")
    con = fresh_db()
    before = con.execute("SELECT COUNT(*) c FROM matches").fetchone()["c"]
    counts, warns = ingest_fix(con, "room_full.json",
                               "https://faceit.com/en/ow2/room/1-abc12345", dry=True)
    after = con.execute("SELECT COUNT(*) c FROM matches").fetchone()["c"]
    check("counts is None on dry-run", counts is None)
    check("match count unchanged", before == after)


def test_idempotent():
    print("idempotent re-ingest")
    con = fresh_db()
    url = "https://faceit.com/en/ow2/room/1-abc12345"
    ingest_fix(con, "room_full.json", url)
    ingest_fix(con, "room_full.json", url)
    ingest_fix(con, "room_full.json", url)
    mid = "faceit-1-abc12345-0000-0000-0000-fixturefull01"
    check("still 1 match", con.execute(
        "SELECT COUNT(*) c FROM matches WHERE id=?", (mid,)).fetchone()["c"] == 1)
    check("still 4 maps (no dupes)", con.execute(
        "SELECT COUNT(*) c FROM map_results WHERE match_id=?", (mid,)).fetchone()["c"] == 4)
    check("still 4 bans (no dupes)", con.execute(
        "SELECT COUNT(*) c FROM hero_bans WHERE match_id=?", (mid,)).fetchone()["c"] == 4)
    check("still 7 roster rows", con.execute(
        "SELECT COUNT(*) c FROM match_rosters WHERE match_id=?", (mid,)).fetchone()["c"] == 7)


def test_preserves_manual_comps():
    print("manual comps preserved + map ids stable across re-ingest")
    con = fresh_db()
    url = "https://faceit.com/en/ow2/room/1-abc12345"
    ingest_fix(con, "room_full.json", url)
    mid = "faceit-1-abc12345-0000-0000-0000-fixturefull01"
    m = con.execute("SELECT team_a FROM matches WHERE id=?", (mid,)).fetchone()
    mr = con.execute("SELECT id FROM map_results WHERE match_id=? AND map_order=1",
                     (mid,)).fetchone()
    # add a manual tracker snapshot on map 1 for team A
    cur = con.execute(
        """INSERT INTO comp_snapshots
           (match_id, map_result_id, team_id, stream_offset_seconds,
            overall_confidence, frame_hash, source)
           VALUES (?,?,?,0,1.0,'man-test','manual')""",
        (mid, mr["id"], m["team_a"]))
    con.executemany(
        "INSERT INTO snapshot_heroes (snapshot_id, slot, hero_id, confidence)"
        " VALUES (?,?,?,1.0)",
        [(cur.lastrowid, i, h) for i, h in enumerate(
            ["winston", "tracer", "genji", "kiriko", "juno"], start=1)])
    con.commit()
    # re-ingest — must not wipe the snapshot, and map_result id must be reused
    ingest_fix(con, "room_full.json", url)
    still = con.execute(
        "SELECT COUNT(*) c FROM comp_snapshots WHERE match_id=? AND source='manual'",
        (mid,)).fetchone()["c"]
    check("manual snapshot survives re-ingest", still == 1)
    mr2 = con.execute("SELECT id FROM map_results WHERE match_id=? AND map_order=1",
                      (mid,)).fetchone()
    check("map_result id stable (snapshot stays linked)", mr2["id"] == mr["id"])
    linked = con.execute(
        "SELECT COUNT(*) c FROM comp_snapshots WHERE map_result_id=?",
        (mr["id"],)).fetchone()["c"]
    check("snapshot still linked to its map", linked == 1)


def test_faceit_only_maps_undetected():
    print("FACEIT-only maps export tracker.detected=false")
    con = fresh_db()
    url = "https://faceit.com/en/ow2/room/1-abc12345"
    ingest_fix(con, "room_full.json", url)
    payload = export_data.build_payload(con)
    mid = "faceit-1-abc12345-0000-0000-0000-fixturefull01"
    match = next(m for m in payload["matches"] if m["id"] == mid)
    check("match exported", match is not None)
    check("has faceit block", "faceit" in match and "matchId" in match["faceit"])
    check("all maps undetected (no tracker comp yet)",
          all(g["tracker"]["detected"] is False for g in match["maps"]))
    check("faceit map facts present",
          match["maps"][0]["faceit"]["replayCode"] == "AB12CD"
          and len(match["maps"][0]["faceit"]["heroBans"]) == 2)
    check("rosters exported", len(match["faceit"]["rosters"]["a"]) == 5)


def test_malformed_graceful():
    print("malformed fixture ingests with warnings, no crash")
    con = fresh_db()
    parsed = fp.parse_faceit_room_json(load_fix("room_malformed.json"),
                                       "https://faceit.com/en/ow2/room/1-mal")
    counts, warns = ing.ingest(con, parsed, "room/1-mal", "EMEA")
    check("ingest returned counts", counts is not None)
    check("warns about missing maps", any("no maps" in w for w in warns))
    check("match still created from id", con.execute(
        "SELECT COUNT(*) c FROM matches WHERE faceit_match_id=?",
        (parsed["faceitMatchId"],)).fetchone()["c"] == 1)



def test_real_room_fixture_ingests():
    print("real room fixture ingests as FACEIT facts (no comps)")
    con = fresh_db()
    url = "https://www.faceit.com/en/ow2/room/1-c55d6822-7ae7-4c53-b86c-015daa712dd3"
    ingest_fix(con, "real_room_c55d6822.json", url, region="EMEA")
    mid = "faceit-1-c55d6822-7ae7-4c53-b86c-015daa712dd3"
    m = con.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
    check("match row written", m is not None)
    check("final score 3-2", m["score_a"] == 3 and m["score_b"] == 2)
    check("5 map_results", con.execute(
        "SELECT COUNT(*) c FROM map_results WHERE match_id=?", (mid,)).fetchone()["c"] == 5)
    check("map names stored (Busan first)", con.execute(
        "SELECT map_id FROM map_results WHERE match_id=? AND map_order=1",
        (mid,)).fetchone()["map_id"] == "busan")
    check("10 roster players (5v5)", con.execute(
        "SELECT COUNT(*) c FROM match_rosters WHERE match_id=?", (mid,)).fetchone()["c"] == 10)
    check("NO comp snapshots created by ingest", con.execute(
        "SELECT COUNT(*) c FROM comp_snapshots WHERE match_id=?", (mid,)).fetchone()["c"] == 0)
    check("no replay codes (public API lacks them)", con.execute(
        "SELECT COUNT(*) c FROM map_results WHERE match_id=? AND replay_code IS NOT NULL",
        (mid,)).fetchone()["c"] == 0)
    check("no hero bans (public API lacks them)", con.execute(
        "SELECT COUNT(*) c FROM hero_bans WHERE match_id=?", (mid,)).fetchone()["c"] == 0)


def main():
    for t in (test_fixture_ingests, test_dry_run_writes_nothing, test_idempotent,
              test_preserves_manual_comps, test_faceit_only_maps_undetected, test_real_room_fixture_ingests,
              test_malformed_graceful):
        t()
    while _open_cons:  # release the sqlite file so rmtree works on Windows
        try:
            _open_cons.pop().close()
        except Exception:
            pass
    shutil.rmtree(os.path.dirname(TEST_DB), ignore_errors=True)
    if _fails:
        print(f"\n{_fails} INGEST TEST(S) FAILED")
        sys.exit(1)
    print("\nALL INGEST TESTS PASSED")


if __name__ == "__main__":
    main()
