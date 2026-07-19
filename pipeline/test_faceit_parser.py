#!/usr/bin/env python3
"""
test_faceit_parser.py — fixture-driven tests for faceit_parser.

Proves the parser extracts match id, teams, final score, maps, map scores,
replay codes, hero bans, and rosters — and that missing/malformed inputs
degrade to nulls/empties instead of crashing. No network.

Run: python3 pipeline/test_faceit_parser.py   (exits non-zero on failure)
"""
from __future__ import annotations
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import faceit_parser as fp  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "faceit")
_fails = 0


def check(name, cond):
    global _fails
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        _fails += 1


def load(fn):
    with open(os.path.join(FIX, fn), encoding="utf-8") as f:
        return f.read()


def test_url_extraction():
    print("url extraction")
    check("full room url",
          fp.extract_match_id_from_url(
              "https://www.faceit.com/en/ow2/room/1-abc-def?x=1") == "1-abc-def")
    check("trailing path",
          fp.extract_match_id_from_url(
              "https://faceit.com/en/ow2/room/1-xyz/scoreboard") == "1-xyz")
    check("bare id", fp.extract_match_id_from_url("1-bare-id-123") == "1-bare-id-123")
    check("None-safe", fp.extract_match_id_from_url(None) is None)
    check("garbage", fp.extract_match_id_from_url("not a url") is None)


def test_full_json():
    print("full JSON payload")
    obj = fp.parse_faceit_room_json(json.loads(load("room_full.json")),
                                    "https://faceit.com/en/ow2/room/1-abc12345")
    check("match id", obj["faceitMatchId"] == "1-abc12345-0000-0000-0000-fixturefull01")
    check("team A name", obj["teams"][0]["name"] == "Sample Falcons")
    check("team B id", obj["teams"][1]["faceitTeamId"] == "fac-B-002")
    check("final score", obj["score"] == {"teamA": 3, "teamB": 1})
    check("4 maps", len(obj["maps"]) == 4)
    check("map1 name+mode", obj["maps"][0]["name"] == "Ilios"
          and obj["maps"][0]["mode"] == "Control")
    check("map1 scores", obj["maps"][0]["scoreA"] == 2 and obj["maps"][0]["scoreB"] == 0)
    check("map1 replay code", obj["maps"][0]["replayCode"] == "AB12CD")
    check("map1 hero bans (dict form)",
          [b["hero"] for b in obj["maps"][0]["heroBans"]] == ["sombra", "sojourn"])
    check("map2 hero bans (string form)",
          [b["hero"] for b in obj["maps"][1]["heroBans"]] == ["widow", "genji"])
    check("map1 pickedBy/veto", obj["maps"][0]["pickedBy"] == "A"
          and obj["maps"][0]["vetoAction"] == "pick")
    check("rosters present (A has 5, B has 2)",
          len(obj["rosters"]) == 2
          and len(obj["rosters"][0]["players"]) == 5
          and len(obj["rosters"][1]["players"]) == 2)
    check("player role parsed",
          obj["rosters"][0]["players"][0]["role"] == "Tank")
    check("NO comp fields leaked",
          all("heroes" not in m and "comp" not in m and "openerComp" not in m
              for m in obj["maps"]))


def test_detailed_results():
    print("detailed_results shape")
    obj = fp.parse_faceit_room_json(
        json.loads(load("room_detailed_results.json")), None)
    check("3 maps from detailed_results", len(obj["maps"]) == 3)
    check("map winners mapped to sides",
          [m["winner"] for m in obj["maps"]] == ["A", "B", "A"])
    check("per-map scores", obj["maps"][1]["scoreA"] == 1
          and obj["maps"][1]["scoreB"] == 3)
    check("score present", obj["score"] == {"teamA": 2, "teamB": 1})


def test_missing_fields():
    print("missing fields")
    obj = fp.parse_faceit_room_json(
        json.loads(load("room_missing_fields.json")), None)
    check("match id still parsed", obj["faceitMatchId"].endswith("missing3"))
    check("team B name is None", obj["teams"][1]["name"] is None)
    check("score both None", obj["score"] == {"teamA": None, "teamB": None})
    check("map1 has name, null scores",
          obj["maps"][0]["name"] == "Oasis"
          and obj["maps"][0]["scoreA"] is None)
    check("map2 order backfilled", obj["maps"][1]["order"] == 2)
    check("map2 replay code kept", obj["maps"][1]["replayCode"] == "ZZ99YY")
    check("no rosters (none provided)", obj["rosters"] == [])


def test_malformed():
    print("malformed input (must not crash)")
    obj = fp.parse_faceit_room_json(
        json.loads(load("room_malformed.json")), "room/1-mal")
    check("returns dict", isinstance(obj, dict))
    check("teams normalized to 2 empty", len(obj["teams"]) == 2
          and obj["teams"][0]["name"] is None)
    check("maps empty (string was junk)", obj["maps"] == [])
    check("score coerced to nulls", obj["score"] == {"teamA": None, "teamB": None})
    # non-dict payloads
    check("None payload safe",
          isinstance(fp.parse_faceit_room_json(None, None), dict))
    check("list payload safe",
          fp.parse_faceit_room_json([1, 2, 3], None)["maps"] == [])
    check("string payload safe",
          isinstance(fp.parse_faceit_room_json("x", None), dict))


def test_html():
    print("HTML parsing")
    obj = fp.parse_faceit_room_html(load("room_embedded.html"),
                                    "https://faceit.com/en/ow2/room/1-html000")
    check("embedded state match id",
          obj["faceitMatchId"] == "1-html000-0000-0000-0000-fixturehtml004")
    check("embedded teams", obj["teams"][0]["name"] == "HTML Team A")
    check("embedded score", obj["score"] == {"teamA": 3, "teamB": 0})
    check("embedded map + replay",
          obj["maps"][0]["name"] == "Busan"
          and obj["maps"][0]["replayCode"] == "HT01ML")

    shell = fp.parse_faceit_room_html(
        load("room_js_shell.html"),
        "https://faceit.com/en/ow2/room/1-shell-id-999")
    check("JS-shell falls back to url id",
          shell["faceitMatchId"] == "1-shell-id-999")
    check("JS-shell has empty maps (no fabrication)", shell["maps"] == [])
    check("bad html string safe",
          isinstance(fp.parse_faceit_room_html(12345, None), dict))


def test_idempotent_normalize():
    print("normalize idempotency")
    obj = fp.parse_faceit_room_json(json.loads(load("room_full.json")), None)
    again = fp.normalize_faceit_match(obj)
    check("normalize(normalized) == normalized", again == obj)



def test_real_room_data_api():
    print("real FACEIT Data-API-shaped fixture (room c55d6822)")
    obj = fp.parse_faceit_room_json(
        json.loads(load("real_room_c55d6822.json")),
        "https://www.faceit.com/en/ow2/room/1-c55d6822-7ae7-4c53-b86c-015daa712dd3")
    check("match id", obj["faceitMatchId"] == "1-c55d6822-7ae7-4c53-b86c-015daa712dd3")
    check("team names from faction1/2",
          obj["teams"][0]["name"] == "SAMPLE Team Alpha"
          and obj["teams"][1]["name"] == "SAMPLE Team Beta")
    check("faction ids", obj["teams"][0]["faceitTeamId"] == "team-sample-alpha")
    check("final score from results.score", obj["score"] == {"teamA": 3, "teamB": 2})
    check("5 maps from detailed_results", len(obj["maps"]) == 5)
    check("map NAMES merged from voting.map",
          [m["name"] for m in obj["maps"]]
          == ["Busan", "King's Row", "Dorado", "Colosseo", "New Queen Street"])
    check("map modes labeled", obj["maps"][0]["mode"] == "Control")
    check("per-map scores", obj["maps"][0]["scoreA"] == 2 and obj["maps"][0]["scoreB"] == 1)
    check("per-map winners", [m["winner"] for m in obj["maps"]] == ["A","B","A","B","A"])
    check("rosters 5v5 from roster arrays",
          len(obj["rosters"][0]["players"]) == 5 and len(obj["rosters"][1]["players"]) == 5)
    check("player nicknames parsed",
          obj["rosters"][0]["players"][0]["nickname"].startswith("SAMPLE-A"))
    # THE KEY REALITY CHECK: public FACEIT does NOT expose these for OW2
    check("replay codes ABSENT (not in public API)",
          all(m["replayCode"] is None for m in obj["maps"]))
    check("hero bans ABSENT (not in public API)",
          all(m["heroBans"] == [] for m in obj["maps"]))
    check("NO comp inference (no hero/comp keys on any map)",
          all(not any(k in m for k in ("heroes","comp","openerComp","playedHeroes"))
              for m in obj["maps"]))


def test_real_room_html_shell():
    print("real public matchroom HTML shell (client-rendered)")
    obj = fp.parse_faceit_room_html(
        load("real_room_c55d6822_shell.html"),
        "https://www.faceit.com/en/ow2/room/1-c55d6822-7ae7-4c53-b86c-015daa712dd3")
    check("shell yields url-derived match id",
          obj["faceitMatchId"] == "1-c55d6822-7ae7-4c53-b86c-015daa712dd3")
    check("shell has no teams (data is JS-fetched, not in HTML)",
          obj["teams"][0]["name"] is None)
    check("shell has no maps (no fabrication)", obj["maps"] == [])


def main():
    for t in (test_url_extraction, test_full_json, test_detailed_results,
              test_missing_fields, test_malformed, test_html, test_real_room_data_api, test_real_room_html_shell,
              test_idempotent_normalize):
        t()
    if _fails:
        print(f"\n{_fails} PARSER TEST(S) FAILED")
        sys.exit(1)
    print("\nALL PARSER TESTS PASSED")


if __name__ == "__main__":
    main()
