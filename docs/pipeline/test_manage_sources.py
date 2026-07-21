#!/usr/bin/env python3
"""test_manage_sources.py — CRUD + dedupe + validation for the source list."""
from __future__ import annotations
import json
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import manage_sources as ms  # noqa: E402

TMP = os.path.join(ROOT, "work", "test_sources")
SRC = os.path.join(TMP, "rooms.json")
_fails = 0
URL1 = "https://www.faceit.com/en/ow2/room/1-aaaa1111-0000-0000-0000-000000000001"
URL2 = "https://www.faceit.com/en/ow2/room/1-bbbb2222-0000-0000-0000-000000000002"


def check(name, cond):
    global _fails
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        _fails += 1


def args(**kw):
    base = {"source": SRC, "url": None, "match_id": None,
            "region": None, "stage": None, "notes": None}
    base.update(kw)
    return types.SimpleNamespace(**base)


def rooms():
    return ms.load(SRC)["rooms"]


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP, exist_ok=True)
    json.dump({"rooms": []}, open(SRC, "w"))

    print("add")
    ms.cmd_add(ms.load(SRC), args(url=URL1, region="NA", stage="Stage 2"))
    check("added 1", len(rooms()) == 1)
    check("tags stored", rooms()[0]["region"] == "NA")

    print("add duplicate url updates tags, no dupe")
    ms.cmd_add(ms.load(SRC), args(url=URL1, region="EMEA"))
    check("still 1 room", len(rooms()) == 1)
    check("region updated", rooms()[0]["region"] == "EMEA")

    print("add invalid url rejected")
    rc = ms.cmd_add(ms.load(SRC), args(url="garbage"))
    check("invalid returns nonzero", rc == 1)
    check("still 1 room", len(rooms()) == 1)

    print("list runs")
    check("list returns 0", ms.cmd_list(ms.load(SRC), args()) == 0)

    print("dedupe collapses hand-added duplicates")
    data = ms.load(SRC)
    data["rooms"].append({"url": URL1})            # manual dup
    data["rooms"].append({"url": URL2})
    json.dump(data, open(SRC, "w"))
    ms.cmd_dedupe(ms.load(SRC), args())
    check("2 unique remain", len(rooms()) == 2)

    print("remove by match-id")
    ms.cmd_remove(ms.load(SRC), args(match_id="1-bbbb2222-0000-0000-0000-000000000002"))
    check("1 remains", len(rooms()) == 1)

    print("remove by url")
    ms.cmd_remove(ms.load(SRC), args(url=URL1))
    check("0 remain", len(rooms()) == 0)

    print("validate flags bad url")
    json.dump({"rooms": [{"url": "not-a-url"}]}, open(SRC, "w"))
    check("validate nonzero on bad url", ms.cmd_validate(ms.load(SRC), args()) == 1)

    shutil.rmtree(TMP, ignore_errors=True)
    if _fails:
        print(f"\n{_fails} SOURCE TEST(S) FAILED"); sys.exit(1)
    print("\nALL SOURCE TESTS PASSED")


if __name__ == "__main__":
    main()
