#!/usr/bin/env python3
"""
test_promote_and_cache.py — clip cache-safety + the safe comp-promotion gate.

Two concerns, both fully offline (no yt-dlp / ffmpeg / network):

  CACHE SAFETY (download_vod_clip.download_clip + video_ingest.probe_clip_valid)
    * a tiny/corrupt cached clip (e.g. 8-byte stub) is NOT reused — it's
      deleted and re-downloaded
    * a validly-sized cached clip IS reused
    * a fresh download that produces a corrupt file raises InvalidClip with a
      clear message (never a misleading 'ffmpeg not found')
    * the byte-floor works without ffprobe; ffprobe verdict is used when present

  PROMOTE GATE (promote_detections)
    * classify: all-strong + consistent consecutive snapshots -> high; weak or
      single-frame -> needs-review
    * dry run writes ZERO comps and a review_queue.json
    * writing without pairing refuses (no match structure)
    * writing a paired run inserts source='cv' comps and is idempotent
    * manual rows are never touched / overridden by cv

Run:  python3 pipeline/test_promote_and_cache.py   (non-zero on failure)
"""
from __future__ import annotations
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

os.environ["OWCS_DB"] = os.path.join(ROOT, "work", "test_promote", "t.sqlite")
import db  # noqa: E402
import video_ingest as vi  # noqa: E402
import download_vod_clip as dvc  # noqa: E402
import promote_detections as pd  # noqa: E402

FAILS = 0
WORK = os.path.join(ROOT, "work", "test_promote")


def check(name, cond):
    global FAILS
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS += 1


# --------------------------------------------------------------------------
def test_cache_safety():
    print("clip cache safety")
    os.makedirs(WORK, exist_ok=True)
    clip = os.path.join(WORK, "clip.mp4")

    # a validating stub: treat >=64 bytes as a valid "video", else invalid
    def fake_validate(path, min_bytes=vi.MIN_CLIP_BYTES, runner=None):
        if not os.path.exists(path):
            return False, "missing"
        n = os.path.getsize(path)
        return (n >= 64, f"{n} bytes")

    # 1. tiny/corrupt cache is NOT reused -> deleted + redownloaded
    with open(clip, "wb") as f:
        f.write(b"\x00" * 8)              # the classic 8-byte stub
    dl_calls = []

    def good_download(url, s, e, out, height, **kw):
        dl_calls.append(out)
        with open(out, "wb") as f:
            f.write(b"\x00" * 128)        # a "real" clip

    res = dvc.download_clip("u", 0, 20, clip, download_fn=good_download,
                            validate_fn=fake_validate)
    check("8-byte cached clip was NOT reused", res["reused"] is False)
    check("invalid cache triggered a re-download", len(dl_calls) == 1)
    check("re-downloaded clip is now valid size", res["sizeBytes"] >= 64)

    # 2. a valid cached clip IS reused (no download)
    dl_calls.clear()
    res2 = dvc.download_clip("u", 0, 20, clip, download_fn=good_download,
                             validate_fn=fake_validate)
    check("valid cached clip is reused", res2["reused"] is True)
    check("no re-download when cache is valid", len(dl_calls) == 0)

    # 3. a fresh download that stays corrupt -> InvalidClip (clear message)
    os.remove(clip)

    def corrupt_download(url, s, e, out, height, **kw):
        with open(out, "wb") as f:
            f.write(b"\x00" * 8)          # still an 8-byte stub

    raised = None
    try:
        dvc.download_clip("u", 0, 20, clip, download_fn=corrupt_download,
                          validate_fn=fake_validate)
    except vi.InvalidClip as e:
        raised = str(e)
    check("corrupt fresh download raises InvalidClip", raised is not None)
    check("InvalidClip message says invalid/corrupt (not ffmpeg)",
          raised is not None and "invalid/corrupt" in raised.lower()
          and "ffmpeg not found" not in raised.lower())

    # 4. probe_clip_valid byte floor works with no ffprobe available
    class NoFfprobe:
        def run(self, *a, **k):
            raise FileNotFoundError("ffprobe")
    big = os.path.join(WORK, "big.bin")
    with open(big, "wb") as f:
        f.write(b"\x00" * (vi.MIN_CLIP_BYTES + 10))
    ok, reason = vi.probe_clip_valid(big, runner=NoFfprobe())
    check("size-only validation passes big file w/o ffprobe", ok)
    small = os.path.join(WORK, "small.bin")
    with open(small, "wb") as f:
        f.write(b"\x00" * 8)
    ok2, reason2 = vi.probe_clip_valid(small, runner=NoFfprobe())
    check("byte-floor rejects the 8-byte stub", not ok2 and "too small" in reason2)


# --------------------------------------------------------------------------
def _det_snapshot(offset, a_heroes, b_heroes, a_score, b_score, fhash):
    def side(heroes, score):
        return {"heroes": heroes, "confidence": score,
                "slots": [{"slot": i, "hero": h, "score": score}
                          for i, h in enumerate(heroes, start=1)]}
    return {"offset": offset, "frame": f"f{offset}.png", "frame_hash": fhash,
            "a": side(a_heroes, a_score), "b": side(b_heroes, b_score)}


def _write_detections(run, snaps, threshold=0.6):
    d = os.path.join(pd.REPORTS_DIR, run)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "detections.json"), "w") as f:
        json.dump({"accepted": snaps, "quarantined": [],
                   "match_threshold": threshold}, f)


def test_promote_gate():
    print("promote gate: classify")
    FIVE_A = ["dva", "winston", "genji", "tracer", "ana"]
    FIVE_B = ["reinhardt", "sigma", "sojourn", "ashe", "kiriko"]
    # two consistent strong snapshots for both teams -> high;
    # plus one weak snapshot (low score) -> needs-review
    run = "unit-classify"
    snaps = [
        _det_snapshot(600, FIVE_A, FIVE_B, 0.9, 0.9, "h1"),
        _det_snapshot(610, FIVE_A, FIVE_B, 0.88, 0.87, "h2"),
        _det_snapshot(620, FIVE_A, ["reinhardt", "sigma", "sojourn", "ashe",
                                    "mercy"], 0.9, 0.3, "h3"),
    ]
    _write_detections(run, snaps)
    det = pd.load_detections(run)
    res = pd.classify(det, threshold=0.6)
    # side a: 3 consistent strong -> all high; side b: 2 strong + 1 weak
    high_offsets_a = sorted(x["offset"] for x in res["high"]
                            if x["team_side"] == "a")
    check("consistent strong team-A snapshots all promote to high",
          high_offsets_a == [600, 610, 620])
    check("weak team-B snapshot is needs-review, not high",
          any(x["team_side"] == "b" and x["offset"] == 620
              for x in res["review"]))
    check("strong consistent team-B snapshots promote to high",
          {x["offset"] for x in res["high"] if x["team_side"] == "b"}
          == {600, 610})

    # single strong-but-lonely snapshot must NOT promote (needs 2 consecutive)
    run2 = "unit-lonely"
    _write_detections(run2, [_det_snapshot(600, FIVE_A, FIVE_B,
                                           0.9, 0.9, "x1")])
    res2 = pd.classify(pd.load_detections(run2), threshold=0.6,
                       min_consecutive=2)
    check("a single strong snapshot does NOT promote (needs consistency)",
          res2["counts"]["high"] == 0
          and res2["counts"]["needsReview"] == 2)

    print("promote gate: dry run writes nothing to DB")
    out = pd.promote(run, write=False,
                     pairing={})   # unpaired dry run
    check("dry run wrote no comps", out["written"] is None)
    check("dry run produced a review queue file",
          os.path.exists(out["reviewQueue"]))
    q = json.load(open(out["reviewQueue"]))
    check("review queue records high + needsReview counts",
          q["counts"]["high"] >= 1 and q["counts"]["needsReview"] >= 1)

    print("promote gate: writing requires pairing")
    con = db.connect()
    # ensure a clean schema
    before = con.execute("SELECT COUNT(*) c FROM comp_snapshots "
                         "WHERE source='cv'").fetchone()["c"]
    out_np = pd.promote(run, write=True, pairing={}, con=con)
    after = con.execute("SELECT COUNT(*) c FROM comp_snapshots "
                        "WHERE source='cv'").fetchone()["c"]
    check("write without pairing writes zero cv comps", after == before
          and out_np["written"] is None)

    print("promote gate: paired write is idempotent + keeps manual")
    # seed minimal match/teams/heroes/map so a paired write can land
    con.execute("INSERT OR IGNORE INTO teams (id, name, code) VALUES "
                "('cr','Crazy Raccoon','CR'),('zeta','ZETA DIVISION','ZETA')")
    for h in FIVE_A + FIVE_B:
        con.execute("INSERT OR IGNORE INTO heroes (id, name, role) "
                    "VALUES (?,?, 'Damage')", (h, h))
    con.execute("INSERT OR IGNORE INTO matches (id, team_a, team_b, date) "
                "VALUES ('m1','cr','zeta','2026-01-01')")
    con.execute("INSERT OR IGNORE INTO game_maps (id, name, mode) "
                "VALUES ('kings-row','Kings Row','Hybrid')")
    cur = con.execute("INSERT INTO map_results "
                      "(match_id, map_order, map_id) "
                      "VALUES ('m1', 1, 'kings-row')")
    con.commit()

    pairing = {"match": "m1", "mapOrder": 1, "teamA": "cr", "teamB": "zeta"}
    r1 = pd.promote(run, write=True, pairing=pairing, con=con)
    n1 = con.execute("SELECT COUNT(*) c FROM comp_snapshots "
                    "WHERE source='cv'").fetchone()["c"]
    check("paired write inserted cv comps", r1["written"]["written"] > 0
          and n1 > 0)
    # idempotent: run again -> no new rows
    r2 = pd.promote(run, write=True, pairing=pairing, con=con)
    n2 = con.execute("SELECT COUNT(*) c FROM comp_snapshots "
                    "WHERE source='cv'").fetchone()["c"]
    check("re-promote is idempotent (no double-write)", n2 == n1
          and r2["written"]["written"] == 0)

    # manual override untouched: insert a manual row, re-promote, still there
    con.execute(
        "INSERT INTO comp_snapshots (match_id, map_result_id, team_id, "
        "stream_offset_seconds, overall_confidence, frame_hash, source) "
        "VALUES ('m1', ?, 'cr', 0, 1.0, 'man-x', 'manual')",
        (cur.lastrowid,))
    con.commit()
    pd.promote(run, write=True, pairing=pairing, con=con)
    man = con.execute("SELECT COUNT(*) c FROM comp_snapshots "
                     "WHERE source='manual'").fetchone()["c"]
    check("manual row preserved across cv promotion", man == 1)
    con.close()


def main():
    import shutil
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    # fresh DB
    if os.path.exists(os.environ["OWCS_DB"]):
        os.remove(os.environ["OWCS_DB"])
    import init_db
    init_db.main()
    test_cache_safety()
    test_promote_gate()
    print()
    if FAILS:
        print(f"FAIL — {FAILS} check(s) failed")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
