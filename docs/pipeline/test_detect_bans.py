#!/usr/bin/env python3
"""Offline tests for detect_bans.py — generalized OCR ban detection.

All synthetic OCR items, no image files, no OCR engine. Uses the real
data/heroes_aliases.json (already covers hero names + the 'pickban'
keyword category) via ocr_hud.load_aliases, exactly like test_ocr_hud.py.
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import detect_bans as db_  # noqa: E402
import ocr_hud  # noqa: E402

FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  {status}  {name}" + (f" — {detail}" if detail and not ok
                                   else ""))
    if not ok:
        FAILURES.append(name)


ALIASES = ocr_hud.load_aliases()
FW, FH = 1920, 1080


def _item(text, conf, box):
    return {"text": text, "conf": conf, "box": box}


def test_pickban_hits() -> None:
    print("pickban_hits:")
    items = [_item("BAN PHASE", 0.9, [800, 100, 200, 40])]
    hits = db_.pickban_hits(items, ALIASES)
    check("BAN PHASE keyword detected", len(hits) == 1, str(hits))

    items = [_item("VESTOLA", 0.9, [100, 300, 80, 30])]
    hits = db_.pickban_hits(items, ALIASES)
    check("ordinary text has no pickban hit", len(hits) == 0, str(hits))

    items = [_item("DRAFT", 0.9, [800, 100, 200, 40])]
    hits = db_.pickban_hits(items, ALIASES)
    check("DRAFT keyword detected", len(hits) == 1, str(hits))


def test_hero_candidates() -> None:
    print("hero_candidates:")
    items = [_item("TRACER", 0.9, [50, 50, 60, 20])]
    cands = db_.hero_candidates(items, ALIASES)
    check("hero text resolves", len(cands) == 1 and cands[0]["hero"] == "tracer",
          str(cands))

    # excluded because it overlaps a live slot box (that's a pick, not a ban)
    slot = [40, 40, 80, 40]
    cands = db_.hero_candidates(items, ALIASES, exclude_boxes=[slot])
    check("hero text inside a live slot box is excluded", cands == [],
          str(cands))

    # low confidence text is skipped
    low = [_item("TRACER", 0.1, [50, 50, 60, 20])]
    cands = db_.hero_candidates(low, ALIASES)
    check("low-confidence OCR is skipped", cands == [], str(cands))

    junk = [_item("XQZPLORT", 0.9, [50, 50, 60, 20])]
    cands = db_.hero_candidates(junk, ALIASES)
    check("unmatched text produces no candidate", cands == [], str(cands))


def test_side_of() -> None:
    print("_side_of:")
    check("left half -> side a", db_._side_of([10, 10, 20, 20], FW) == "a")
    check("right half -> side b",
          db_._side_of([1500, 10, 20, 20], FW) == "b")


def test_detect_bans_in_frames() -> None:
    print("detect_bans_in_frames:")

    # ---- non-pickban frames never contribute, even repeated ----------
    plain = [(float(t), [_item("TRACER", 0.9, [50, 50, 60, 20])])
             for t in range(0, 40, 5)]
    r = db_.detect_bans_in_frames(plain, ALIASES, FW)
    check("hero text in ordinary gameplay frames is never a ban",
          r["a"] == [] and r["b"] == [] and r["unresolved"] == [],
          str(r))
    check("pickban_frames counted zero", r["pickban_frames"] == 0, str(r))

    # ---- consensus: same hero, 2+ pickban frames, side a -------------
    pickban_a = [
        (10.0, [_item("BAN PHASE", 0.9, [800, 20, 150, 30]),
                _item("REAPER", 0.9, [80, 400, 70, 25])]),
        (12.0, [_item("BAN", 0.9, [800, 20, 150, 30]),
                _item("REAPER", 0.85, [80, 400, 70, 25])]),
    ]
    r = db_.detect_bans_in_frames(pickban_a, ALIASES, FW)
    check("confirmed ban on side a", len(r["a"]) == 1
          and r["a"][0]["hero"] == "reaper", str(r))
    check("n_frames reflects the two agreeing frames",
          r["a"][0]["n_frames"] == 2, str(r))
    check("side b empty", r["b"] == [], str(r))
    check("pickban_frames counted", r["pickban_frames"] == 2, str(r))

    # ---- single pickban frame -> unresolved, not confirmed -----------
    one = [(10.0, [_item("BAN", 0.9, [800, 20, 150, 30]),
                   _item("WIDOWMAKER", 0.9, [1600, 400, 90, 25])])]
    r = db_.detect_bans_in_frames(one, ALIASES, FW)
    check("single-frame candidate is unresolved, not confirmed",
          r["b"] == [] and len(r["unresolved"]) == 1
          and r["unresolved"][0]["hero"] == "widow", str(r))

    # ---- two different heroes banned by the same side -----------------
    two_bans = [
        (10.0, [_item("BAN", 0.9, [800, 20, 150, 30]),
                _item("REAPER", 0.9, [80, 400, 70, 25]),
                _item("SOMBRA", 0.9, [80, 460, 70, 25])]),
        (12.0, [_item("BAN", 0.9, [800, 20, 150, 30]),
                _item("REAPER", 0.9, [80, 400, 70, 25]),
                _item("SOMBRA", 0.9, [80, 460, 70, 25])]),
    ]
    r = db_.detect_bans_in_frames(two_bans, ALIASES, FW)
    heroes = sorted(e["hero"] for e in r["a"])
    check("two distinct bans on the same side both confirmed",
          heroes == ["reaper", "sombra"], str(r["a"]))

    # ---- hero text inside a live pick slot during a pickban frame -----
    # (e.g. the map transitions into gameplay while a stale BAN graphic
    # lingers) must not be read as a ban.
    layout = {"frame_width": FW, "frame_height": FH,
             "slots_a": [[70, 390, 90, 40]], "slots_b": []}
    contaminated = [
        (10.0, [_item("BAN", 0.9, [800, 20, 150, 30]),
                _item("REAPER", 0.9, [80, 400, 70, 25])]),
        (12.0, [_item("BAN", 0.9, [800, 20, 150, 30]),
                _item("REAPER", 0.9, [80, 400, 70, 25])]),
    ]
    r = db_.detect_bans_in_frames(contaminated, ALIASES, FW,
                                  layout=layout, fh=FH)
    check("hero text overlapping a live pick slot is excluded from bans",
          r["a"] == [], str(r))


def main() -> int:
    test_pickban_hits()
    test_hero_candidates()
    test_side_of()
    test_detect_bans_in_frames()
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURES: {FAILURES}")
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
