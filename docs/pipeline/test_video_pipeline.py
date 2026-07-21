#!/usr/bin/env python3
"""
test_video_pipeline.py — the video CV layer on small committed fixtures.

Uses pipeline/fixtures/video/ (a handful of tiny synthetic PNGs + a matching
template set), never a real video, and an isolated DB, so it runs offline in
CI in well under a second. Exercises the real stage code paths:

  V1 video_ingest         fixtureFrames -> raw frames (no yt-dlp/ffmpeg)
  V2 frame_filter         drops the break + replay frames
  V3 hero_overlay_detect  reads comps, quarantines the blank frame
  V4 video_to_snapshots   writes source='cv' rows, idempotent on rerun
     map_sync             assigns snapshots to the two maps
     export_data          opener/played/swaps + 'cv' provenance
     manual override      manual correction beats cv; removing it restores cv

Run:  python3 pipeline/test_video_pipeline.py   (exits non-zero on failure)
"""
from __future__ import annotations
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

# Isolated DB so the test never touches real data.
os.environ["OWCS_DB"] = os.path.join(ROOT, "work", "test_video", "test.sqlite")
import db  # noqa: E402
import init_db  # noqa: E402
import capture  # noqa: E402
import video_ingest  # noqa: E402
import frame_filter  # noqa: E402
import hero_overlay_detect as hod  # noqa: E402
import video_to_snapshots as v2s  # noqa: E402
import map_sync  # noqa: E402
import export_data  # noqa: E402
import apply_corrections  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "video")
LAYOUT_PATH = os.path.join(FIX, "demo-layout.json")
TEST_WORK = os.path.join(ROOT, "work", "test_video")

MATCH = "vtest01"
A_OPENER = ["winston", "tracer", "genji", "kiriko", "juno"]
A_SWAP_HERO = "sojourn"
B_MAP1 = ["dva", "hazard", "freja", "ana", "lucio"]
A_MAP2 = ["rein", "reaper", "mei", "ana", "lucio"]


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        sys.exit(1)


def seed(con):
    db.init_schema(con)
    init_db.seed_reference(con, init_db.load_sample())
    con.execute("""INSERT OR REPLACE INTO matches
        (id, source_ref, stage, region, date, status, team_a, team_b,
         score_a, score_b, winner_team)
        VALUES (?, 'video:vtest01','Test','Asia','2026-06-01','final',
                'falcons','cr',2,0,'falcons')""", (MATCH,))
    for order, mp in ((1, "busan"), (2, "kingsrow")):
        con.execute("INSERT OR IGNORE INTO map_results "
                    "(match_id,map_order,map_id,winner_team) "
                    "VALUES (?,?,?,'falcons')", (MATCH, order, mp))
    con.commit()


def map1_falcons_comp(con):
    payload = export_data.build_payload(con)
    m = next(x for x in payload["matches"] if x["id"] == MATCH)
    return m["maps"][0]["tracker"], payload


def main():
    shutil.rmtree(TEST_WORK, ignore_errors=True)
    os.makedirs(TEST_WORK, exist_ok=True)
    con = db.connect()
    seed(con)
    layout = capture.load_layout(LAYOUT_PATH)
    lib = hod.load_lib(layout)  # reads layout['templates_dir'] fixture set

    # ---- V1 ingest (offline: committed fixtureFrames, no network) --------
    print("V1) video_ingest (fixtureFrames)")
    src = {"match": MATCH, "layout": "pipeline/fixtures/video/demo-layout.json",
           "fixtureFrames": "pipeline/fixtures/video/demo_match/frames"}
    ing = video_ingest.ingest_source(src)
    check("8 raw frames copied, no VOD download", ing["frames"] == 8)

    # ---- V2 filter (break + replay dropped) ------------------------------
    print("V2) frame_filter")
    frames_dir = os.path.join(TEST_WORK, "frames")
    filt = frame_filter.filter_frames(ing["raw_dir"], frames_dir, layout)
    rej_reasons = " ".join(r for _, r in filt["rejected"])
    check("kept 6 gameplay, rejected 2 (break+replay)",
          len(filt["kept"]) == 6 and len(filt["rejected"]) == 2)
    check("rejections name no-hud and replay",
          "no-hud" in rej_reasons and "replay" in rej_reasons)

    # ---- V3 detect (blank frame quarantined; comps read exactly) ---------
    print("V3) hero_overlay_detect")
    qdir = os.path.join(TEST_WORK, "quarantine")
    res = hod.detect_dir(frames_dir, layout, lib, quarantine_dir=qdir)
    check("5 accepted frames, 1 quarantined (blank slots)",
          len(res["accepted"]) == 5 and len(res["quarantined"]) == 1)
    first = min(res["accepted"], key=lambda a: a["offset"])
    check(f"opener comp read exactly ({first['a']['heroes']})",
          first["a"]["heroes"] == A_OPENER and first["b"]["heroes"] == B_MAP1)
    check("confidence present and high", first["a"]["confidence"] >= 0.6)
    check("quarantine sidecar written",
          os.path.isdir(qdir) and any(f.endswith(".json") for f in os.listdir(qdir)))

    # ---- V4 persist (source='cv', idempotent) ----------------------------
    print("V4) video_to_snapshots")
    rep = v2s.snapshots_for_match(con, MATCH, frames_dir, layout, lib)
    check("10 cv snapshots written (5 frames x 2 teams)",
          rep["snapshots_written"] == 10)
    total = con.execute("SELECT COUNT(*) c FROM comp_snapshots "
                        "WHERE match_id=? AND source='cv'", (MATCH,)).fetchone()["c"]
    check("all snapshots labelled source='cv'", total == 10)
    rep2 = v2s.snapshots_for_match(con, MATCH, frames_dir, layout, lib)
    check("rerun is idempotent (frame-hash dedup, 0 new)",
          rep2["snapshots_written"] == 0)

    # ---- map_sync + export ----------------------------------------------
    print("map_sync + export_data")
    map_sync.sync_match(con, MATCH, interval=300, gap_factor=2.5,
                        report_only=False)
    unassigned = con.execute("SELECT COUNT(*) c FROM comp_snapshots "
                             "WHERE match_id=? AND map_result_id IS NULL",
                             (MATCH,)).fetchone()["c"]
    check("all snapshots assigned to a map", unassigned == 0)
    tracker, _ = map1_falcons_comp(con)
    check("map1 played = opener + swap (sojourn)",
          set(tracker["playedHeroesA"]) == set(A_OPENER + [A_SWAP_HERO]))
    check("opener preserved as the first-seen five",
          tracker["openerCompA"] == A_OPENER)
    check("swap detected", tracker["swapsA"] == [A_SWAP_HERO])
    check("provenance labelled cv", tracker["sourceA"] == "cv")

    # ---- manual override beats cv, and is reversible ---------------------
    print("manual override")
    manual_opener = ["rein", "dva", "ashe", "ana", "kiriko"]  # different comp
    corr_path = os.path.join(TEST_WORK, "corrections.json")
    with open(corr_path, "w") as f:
        json.dump({"corrections": [{
            "match": MATCH, "mapOrder": 1, "team": "falcons",
            "openerComp": manual_opener, "note": "read from replay code",
            "author": "test"}]}, f)
    apply_corrections.apply_file(con, corr_path)
    tracker, _ = map1_falcons_comp(con)
    check("manual correction overrides cv at export",
          tracker["openerCompA"] == manual_opener and tracker["sourceA"] == "manual")
    cv_still = con.execute("SELECT COUNT(*) c FROM comp_snapshots WHERE "
                           "match_id=? AND source='cv'", (MATCH,)).fetchone()["c"]
    check("cv rows are NOT deleted by a manual correction", cv_still == 10)

    # remove the manual correction -> cv data surfaces again
    con.execute("DELETE FROM comp_snapshots WHERE match_id=? AND source='manual'",
                (MATCH,))
    con.commit()
    tracker, _ = map1_falcons_comp(con)
    check("removing manual correction restores cv comp",
          tracker["openerCompA"] == A_OPENER and tracker["sourceA"] == "cv")

    print("\nALL VIDEO PIPELINE TESTS PASSED")


if __name__ == "__main__":
    main()
