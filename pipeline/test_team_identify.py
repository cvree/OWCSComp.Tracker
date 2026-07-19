#!/usr/bin/env python3
"""Offline tests for team_identify.py — OCR-based team identity.

All synthetic OCR items, no image files, no OCR engine. Mirrors
test_map_ingestion.py's check()/main() harness.
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team_identify as ti  # noqa: E402

FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  {status}  {name}" + (f" — {detail}" if detail and not ok
                                   else ""))
    if not ok:
        FAILURES.append(name)


TEAMS = [
    {"id": "qadsiah", "name": "Al Qadsiah", "code": "QAD"},
    {"id": "twis", "name": "Twisted Minds", "code": "TM"},
    {"id": "falcons", "name": "Team Falcons", "code": "FLC"},
    {"id": "cr", "name": "Crazy Raccoon", "code": "CR"},
]


def _item(text, conf, box):
    return {"text": text, "conf": conf, "box": box}


def test_match_team() -> None:
    print("match_team:")
    m = ti.match_team("TWISTED MINDS", TEAMS)
    check("exact name match", m["team"] == "twis" and m["method"] == "exact",
          str(m))
    m = ti.match_team("TM", TEAMS)
    check("exact code match", m["team"] == "twis" and m["method"] == "code",
          str(m))
    m = ti.match_team("QAD", TEAMS)
    check("code match (Al Qadsiah)", m["team"] == "qadsiah", str(m))
    m = ti.match_team("TWISTEDMIND", TEAMS)   # OCR dropped the trailing S
    check("fuzzy match tolerates OCR noise", m["team"] == "twis"
          and m["method"] == "fuzzy", str(m))
    m = ti.match_team("CR", TEAMS)
    check("short code exact match unambiguous", m["team"] == "cr", str(m))
    m = ti.match_team("XYZQ NOTATEAM", TEAMS)
    check("no match -> None, not a guess", m["team"] is None, str(m))
    m = ti.match_team("", TEAMS)
    check("empty text -> None", m["team"] is None, str(m))
    m = ti.match_team("QAD", [])
    check("no known teams -> None", m["team"] is None, str(m))


def test_identify_side() -> None:
    print("identify_side (temporal consensus):")
    zone = [0, 0, 300, 90]   # team_left-ish px rect
    # three frames all reading "TWISTED MINDS" in-zone -> confident
    frames = [
        (100.0, [_item("TWISTED MINDS", 0.9, [10, 10, 150, 20])]),
        (105.0, [_item("TWISTED MINDS", 0.85, [10, 10, 150, 20])]),
        (110.0, [_item("TWISTED MINDS", 0.88, [10, 10, 150, 20])]),
    ]
    r = ti.identify_side(frames, zone, TEAMS)
    check("3-frame consensus resolves the team", r["team"] == "twis",
          str(r))
    check("confidence is populated", r["confidence"] > 0, str(r))
    check("n_frames counts agreeing frames", r["n_frames"] == 3, str(r))

    # single-frame misread must NOT resolve (consensus floor)
    one_frame = [(100.0, [_item("TWISTED MINDS", 0.9, [10, 10, 150, 20])])]
    r = ti.identify_side(one_frame, zone, TEAMS)
    check("single frame is not enough for consensus", r["team"] is None,
          str(r))
    check("weak candidate surfaced for review",
          r.get("weak_candidate") == "twis", str(r))

    # noisy OCR that never matches any known team
    noisy = [(t, [_item("XQZ BLORP", 0.9, [10, 10, 150, 20])])
             for t in (100.0, 105.0)]
    r = ti.identify_side(noisy, zone, TEAMS)
    check("no matching text -> no team, reason explains why",
          r["team"] is None and "no OCR team-name match" not in r["reason"],
          str(r))

    # empty OCR (no team-name zone hit at all, e.g. covered by an overlay)
    r = ti.identify_side([(100.0, [])], zone, TEAMS)
    check("no OCR items at all -> no team", r["team"] is None, str(r))

    # box outside the zone must not count
    outside = [(t, [_item("TWISTED MINDS", 0.9, [900, 900, 50, 20])])
               for t in (100.0, 105.0)]
    r = ti.identify_side(outside, zone, TEAMS)
    check("text outside the zone is ignored", r["team"] is None, str(r))

    # two DIFFERENT teams each seen twice -> the more-agreed one wins,
    # but this also proves votes are tallied per-team, not globally
    split = [
        (100.0, [_item("TWISTED MINDS", 0.9, [10, 10, 150, 20])]),
        (105.0, [_item("TWISTED MINDS", 0.9, [10, 10, 150, 20])]),
        (110.0, [_item("AL QADSIAH", 0.9, [10, 10, 150, 20])]),
    ]
    r = ti.identify_side(split, zone, TEAMS)
    check("majority team wins when votes split", r["team"] == "twis",
          str(r))


def test_identify_teams() -> None:
    print("identify_teams (both sides):")
    layout = {}
    fw, fh = 1920, 1080
    a_frames = [(t, [_item("AL QADSIAH", 0.9, [50, 20, 200, 40])])
               for t in (100.0, 105.0)]
    b_frames = [(t, [_item("TWISTED MINDS", 0.9, [1600, 20, 200, 40])])
               for t in (100.0, 105.0)]
    res = ti.identify_teams(a_frames, b_frames, layout, TEAMS, fw, fh)
    check("side a resolves", res["a"]["team"] == "qadsiah", str(res["a"]))
    check("side b resolves", res["b"]["team"] == "twis", str(res["b"]))

    layout_custom = {"ocr_zones": {"team_left": [0.0, 0.0, 0.2, 0.05]}}
    res2 = ti.identify_teams(a_frames, b_frames, layout_custom, TEAMS,
                             fw, fh)
    check("layout ocr_zones override is honored (still resolves side a)",
          res2["a"]["team"] == "qadsiah", str(res2["a"]))


def test_cross_check() -> None:
    print("cross_check:")
    detected_ok = {"team": "twis", "confidence": 0.9, "n_frames": 3}
    r = ti.cross_check(detected_ok, "twis")
    check("agreement -> agrees True", r["agrees"] is True, str(r))

    detected_conflict = {"team": "twis", "confidence": 0.9, "n_frames": 3}
    r = ti.cross_check(detected_conflict, "qadsiah")
    check("disagreement -> agrees False, explains both sides",
          r["agrees"] is False and "twis" in r["note"]
          and "qadsiah" in r["note"], str(r))

    r = ti.cross_check(None, "qadsiah")
    check("no CV signal -> agrees None (operator claim stands)",
          r["agrees"] is None, str(r))

    r = ti.cross_check({"team": None, "reason": "no OCR text"}, "qadsiah")
    check("unresolved candidate -> agrees None", r["agrees"] is None,
          str(r))


def test_known_teams_from_db() -> None:
    print("known_teams_from_db:")
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE teams (id TEXT, name TEXT, code TEXT)")
    con.execute("INSERT INTO teams VALUES ('twis','Twisted Minds','TM')")
    con.commit()
    out = ti.known_teams_from_db(con)
    check("reads teams table into id/name/code dicts",
          out == [{"id": "twis", "name": "Twisted Minds", "code": "TM"}],
          str(out))


def main() -> int:
    test_match_team()
    test_identify_side()
    test_identify_teams()
    test_cross_check()
    test_known_teams_from_db()
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURES: {FAILURES}")
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
