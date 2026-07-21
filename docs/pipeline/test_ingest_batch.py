#!/usr/bin/env python3
"""
test_ingest_batch.py — proves the batch ingest reads the source list, skips
duplicates and already-ingested matches, survives a failing room, and that
run_batch orchestrates the steps in order. No network. Exits nonzero on fail.
"""
from __future__ import annotations
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

TEST_DB = os.path.join(ROOT, "work", "test_batch", "t.sqlite")
os.environ["OWCS_DB"] = TEST_DB
import db  # noqa: E402
import init_db  # noqa: E402
import ingest_faceit as ing  # noqa: E402
import ingest_faceit_batch as batch  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "faceit")
CACHE = os.path.join(ROOT, "work", "test_batch", "cache")
_fails = 0


def check(name, cond):
    global _fails
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        _fails += 1


def prime_cache(url, fixture):
    os.makedirs(CACHE, exist_ok=True)
    body = open(os.path.join(FIX, fixture)).read()
    open(os.path.join(CACHE, ing.cache_key_for(url) + ".body"), "w").write(body)


_open_cons: list = []


def fresh_db():
    # Close any connection from a previous test first. On Windows an open
    # SQLite file cannot be deleted, so the rmtree below would silently fail
    # (ignore_errors=True) and the "fresh" db would still hold stale rows —
    # e.g. run_batch would then SKIP "already ingested" rooms and the
    # "1 still ingested after the failure" check would fail.
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
    init_db.seed_reference(con, init_db.load_sample())
    return con


URL_FULL = "https://www.faceit.com/en/ow2/room/1-abc12345-0000-0000-0000-fixturefull01"
URL_DET = "https://www.faceit.com/en/ow2/room/1-def67890-0000-0000-0000-fixturedetail2"


def test_reads_source_list(tmp_source):
    print("reads source list")
    rooms = batch.load_rooms(tmp_source)
    check("blank url filtered (4 of 5 kept)", len(rooms) == 4)
    check("missing file -> empty list, no crash",
          batch.load_rooms("/no/such/file.json") == [])


def test_batch_offline_dedupe_and_resilience(tmp_source):
    print("dedupe + resilience + already-ingested skip")
    con = fresh_db()
    prime_cache(URL_FULL, "room_full.json")
    prime_cache(URL_DET, "room_detailed_results.json")
    rooms = batch.load_rooms(tmp_source)
    s = batch.run_batch(con, rooms, CACHE, offline=True)
    check("2 ingested", s["ingested"] == 2)
    check("1 duplicate url skipped", s["skipped"] >= 1)
    check("0 failed (bad/uncached room skipped, not failed-hard)", s["failed"] == 0)
    check("maps counted", s["maps"] == 7)
    # re-run: everything already ingested
    s2 = batch.run_batch(con, rooms, CACHE, offline=True)
    check("re-run ingests nothing (idempotent)", s2["ingested"] == 0)
    check("re-run skips all", s2["skipped"] == len(rooms))


def test_one_failure_does_not_stop_rest():
    print("one failing room does not stop the batch")
    con = fresh_db()
    prime_cache(URL_FULL, "room_full.json")
    # room 1 will raise inside upsert via a monkeypatched failure; room 2 is fine
    rooms = [
        {"url": "https://www.faceit.com/en/ow2/room/1-willfail-xxxx"},
        {"url": URL_FULL},
    ]
    prime_cache(URL_FULL, "room_full.json")
    orig = batch.ing.upsert
    calls = {"n": 0}

    def flaky(con, parsed, room_url, region):
        calls["n"] += 1
        if "willfail" in room_url:
            raise RuntimeError("simulated upsert failure")
        return orig(con, parsed, room_url, region)

    batch.ing.upsert = flaky
    # prime the failing room's cache too so it reaches upsert
    prime_cache("https://www.faceit.com/en/ow2/room/1-willfail-xxxx", "room_full.json")
    try:
        s = batch.run_batch(con, rooms, CACHE, offline=True)
    finally:
        batch.ing.upsert = orig
    check("1 failed", s["failed"] == 1)
    check("1 still ingested after the failure", s["ingested"] == 1)


def test_run_batch_step_order(monkeypatch_source):
    print("run_batch orchestrates steps in order")
    import run_batch
    order = []
    orig = {
        "init": run_batch.step_init, "ingest": run_batch.step_ingest,
        "corr": run_batch.step_corrections, "val": run_batch.step_validate,
        "exp": run_batch.step_export,
    }
    run_batch.step_init = lambda con, with_sample: order.append("init")
    run_batch.step_ingest = lambda *a, **k: order.append("ingest") or {}
    run_batch.step_corrections = lambda con: order.append("corrections")
    run_batch.step_validate = lambda con, strict: order.append("validate") or 0
    run_batch.step_export = lambda con, allow_empty=False: order.append("export")
    sys.argv = ["run_batch", "--source", monkeypatch_source, "--offline"]
    try:
        run_batch.main()
    finally:
        run_batch.step_init, run_batch.step_ingest = orig["init"], orig["ingest"]
        run_batch.step_corrections, run_batch.step_validate = orig["corr"], orig["val"]
        run_batch.step_export = orig["exp"]
    check("order is init->ingest->corrections->validate->export",
          order == ["init", "ingest", "corrections", "validate", "export"])


def main():
    src = os.path.join(ROOT, "work", "test_batch_src.json")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    json.dump({"rooms": [
        {"url": URL_FULL, "region": "EMEA"},
        {"url": URL_FULL, "region": "EMEA", "stage": "dup"},   # duplicate
        {"url": URL_DET, "region": "EMEA"},
        {"url": "https://www.faceit.com/en/ow2/room/1-uncached"},  # offline skip
        {"url": "", "notes": "blank"},                          # filtered by loader
    ]}, open(src, "w"))

    test_reads_source_list(src)
    test_batch_offline_dedupe_and_resilience(src)
    test_one_failure_does_not_stop_rest()
    test_run_batch_step_order(src)

    while _open_cons:  # release the sqlite file so rmtree works on Windows
        try:
            _open_cons.pop().close()
        except Exception:
            pass
    shutil.rmtree(os.path.join(ROOT, "work", "test_batch"), ignore_errors=True)
    if _fails:
        print(f"\n{_fails} BATCH TEST(S) FAILED")
        sys.exit(1)
    print("\nALL BATCH TESTS PASSED")


if __name__ == "__main__":
    main()
