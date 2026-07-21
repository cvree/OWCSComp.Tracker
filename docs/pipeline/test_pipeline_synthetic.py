#!/usr/bin/env python3
"""
test_pipeline_synthetic.py — end-to-end pipeline test with no real VOD.

Builds synthetic 1280x720 "broadcast frames": a distinctive icon per hero
placed in the layout's slot positions, a HUD anchor bar, and (for one
frame) a replay marker. Then exercises the real code paths:

  1. capture.is_gameplay  → accepts gameplay frames, rejects break/replay
  2. detect               → reads both comps correctly from every frame
  3. map_sync             → segments offsets into blocks matching 2 maps
  4. export_data          → emits data.js containing the detected comps

Run:  python3 pipeline/test_pipeline_synthetic.py
Exits non-zero on any failure.
"""
from __future__ import annotations
import json
import os
import shutil
import sqlite3
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

# Isolated test DB + dirs so we never touch real data
os.environ["OWCS_DB"] = os.path.join(ROOT, "work", "test", "test.sqlite")
import db  # noqa: E402
import detect  # noqa: E402
import capture  # noqa: E402
import map_sync  # noqa: E402
import init_db  # noqa: E402

TEST_DIR = os.path.join(ROOT, "work", "test")
LAYOUT_PATH = os.path.join(ROOT, "layouts", "owcs-demo.json")

W, H = 1280, 720
SLOT_W, SLOT_H = 64, 64
SLOTS_A = [[40 + i * 80, 20, SLOT_W, SLOT_H] for i in range(5)]
SLOTS_B = [[800 + i * 80, 20, SLOT_W, SLOT_H] for i in range(5)]
ANCHOR_RECT = [560, 8, 160, 40]     # "objective bar" region
REPLAY_RECT = [20, 620, 120, 60]    # "replay wipe" region

COMP_A1 = ["winston", "tracer", "genji", "kiriko", "juno"]
COMP_B1 = ["dva", "sojourn", "freja", "ana", "lucio"]
COMP_A2 = ["rein", "reaper", "mei", "ana", "lucio"]
COMP_B2 = ["sigma", "widow", "ashe", "bap", "kiriko"]
ALL_HEROES = sorted(set(COMP_A1 + COMP_B1 + COMP_A2 + COMP_B2))


def hero_icon(hero_id: str) -> np.ndarray:
    """Deterministic, visually distinct 64x64 icon per hero id."""
    rng = np.random.default_rng(abs(hash(hero_id)) % (2**32))
    img = rng.integers(30, 226, size=(SLOT_H, SLOT_W, 3), dtype=np.uint8)
    img = cv2.GaussianBlur(img, (5, 5), 0)
    cv2.putText(img, hero_id[:3].upper(), (4, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return img


def paint_anchor(frame: np.ndarray) -> None:
    x, y, w, h = ANCHOR_RECT
    frame[y:y+h, x:x+w] = (40, 40, 40)
    cv2.rectangle(frame, (x+4, y+4), (x+w-4, y+h-4), (0, 180, 255), 2)
    cv2.putText(frame, "OBJ", (x+50, y+30), cv2.FONT_HERSHEY_SIMPLEX,
                0.8, (255, 255, 255), 2)


def paint_replay(frame: np.ndarray) -> None:
    x, y, w, h = REPLAY_RECT
    frame[y:y+h, x:x+w] = (0, 0, 160)
    cv2.putText(frame, "R", (x+40, y+45), cv2.FONT_HERSHEY_SIMPLEX,
                1.4, (255, 255, 255), 3)


def make_frame(comp_a, comp_b, gameplay=True, replay=False,
               noise=6, offset=0) -> np.ndarray:
    frame = np.full((H, W, 3), 18, dtype=np.uint8)
    # background clutter so matching isn't trivially clean; seeded per
    # offset so every frame file is unique (real frames always differ —
    # identical files are exactly what frame-hash dedup should collapse)
    rng = np.random.default_rng(7 + offset)
    frame = np.clip(frame.astype(int)
                    + rng.integers(-noise, noise, frame.shape), 0, 255
                    ).astype(np.uint8)
    if gameplay:
        paint_anchor(frame)
        for slots, comp in ((SLOTS_A, comp_a), (SLOTS_B, comp_b)):
            for (x, y, w, h), hid in zip(slots, comp):
                frame[y:y+h, x:x+w] = hero_icon(hid)
    if replay:
        paint_anchor(frame)          # replays re-show the HUD...
        paint_replay(frame)          # ...but carry the marker
        for slots, comp in ((SLOTS_A, comp_a), (SLOTS_B, comp_b)):
            for (x, y, w, h), hid in zip(slots, comp):
                frame[y:y+h, x:x+w] = hero_icon(hid)
    return frame


def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        sys.exit(1)


def main() -> None:
    shutil.rmtree(TEST_DIR, ignore_errors=True)
    os.makedirs(TEST_DIR, exist_ok=True)

    # ---- layout + reference templates -------------------------------
    os.makedirs(os.path.join(ROOT, "layouts"), exist_ok=True)
    anchor_tpl = make_frame([], [], gameplay=True)[
        ANCHOR_RECT[1]+6:ANCHOR_RECT[1]+ANCHOR_RECT[3]-6,
        ANCHOR_RECT[0]+6:ANCHOR_RECT[0]+ANCHOR_RECT[2]-6]
    replay_frame = make_frame(COMP_A1, COMP_B1, gameplay=False, replay=True)
    replay_tpl = replay_frame[
        REPLAY_RECT[1]+6:REPLAY_RECT[1]+REPLAY_RECT[3]-6,
        REPLAY_RECT[0]+6:REPLAY_RECT[0]+REPLAY_RECT[2]-6]
    cv2.imwrite(os.path.join(ROOT, "layouts", "demo-anchor.png"),
                cv2.cvtColor(anchor_tpl, cv2.COLOR_BGR2GRAY))
    cv2.imwrite(os.path.join(ROOT, "layouts", "demo-replay.png"),
                cv2.cvtColor(replay_tpl, cv2.COLOR_BGR2GRAY))
    layout = {
        "frame_width": W, "frame_height": H,
        "sample_interval_seconds": 300,
        "anchor": {"rect": ANCHOR_RECT, "template": "layouts/demo-anchor.png",
                   "min_score": 0.7},
        "replay": {"rect": REPLAY_RECT, "template": "layouts/demo-replay.png",
                   "min_score": 0.7},
        "slots_a": SLOTS_A, "slots_b": SLOTS_B,
        "match_threshold": 0.6,
    }
    with open(LAYOUT_PATH, "w") as f:
        json.dump(layout, f, indent=1)

    # hero templates (as if cropped from broadcast frames).
    # ONLY the root-level synthetic set is replaced — per-source template
    # directories (templates/owcs_*/) are real calibration assets and
    # must survive test runs (a wholesale rmtree here is what silently
    # deleted templates/owcs_8c105lnzlam from earlier release zips).
    tdir = os.path.join(ROOT, "templates")
    os.makedirs(tdir, exist_ok=True)
    for fn in os.listdir(tdir):
        p = os.path.join(tdir, fn)
        if os.path.isfile(p) and fn.endswith(".png"):
            os.remove(p)
    for hid in ALL_HEROES:
        icon = hero_icon(hid)
        cv2.imwrite(os.path.join(tdir, f"{hid}.png"),
                    cv2.cvtColor(icon, cv2.COLOR_BGR2GRAY))

    # ---- 1) gameplay classifier --------------------------------------
    print("1) capture.is_gameplay")
    lay = capture.load_layout(LAYOUT_PATH)
    anchor = capture._load_template(lay, "anchor")
    replay = capture._load_template(lay, "replay")

    gp = make_frame(COMP_A1, COMP_B1)
    brk = make_frame([], [], gameplay=False)                 # break screen
    rp = make_frame(COMP_A1, COMP_B1, gameplay=False, replay=True)

    check("accepts live gameplay frame", capture.is_gameplay(gp, anchor, replay)[0])
    check("rejects break/caster frame", not capture.is_gameplay(brk, anchor, replay)[0])
    check("rejects replay frame (HUD present, marker present)",
          not capture.is_gameplay(rp, anchor, replay)[0])

    # ---- 2) detection on a two-map "match" ---------------------------
    print("2) detect.py on synthetic frames")
    con = db.connect()
    db.init_schema(con)
    data = init_db.load_sample()
    init_db.seed_reference(con, data)
    con.execute("""INSERT OR REPLACE INTO matches
        (id, source_ref, stage, region, date, status, team_a, team_b,
         score_a, score_b, winner_team)
        VALUES ('t01','test:t01','Test','Asia','2026-06-01','final',
                'falcons','cr',2,0,'falcons')""")
    con.execute("""INSERT INTO map_results (match_id,map_order,map_id,winner_team)
                   VALUES ('t01',1,'busan','falcons')""")
    con.execute("""INSERT INTO map_results (match_id,map_order,map_id,winner_team)
                   VALUES ('t01',2,'kingsrow','falcons')""")
    con.commit()

    frames_dir = os.path.join(ROOT, "work", "t01", "frames")
    shutil.rmtree(os.path.join(ROOT, "work", "t01"), ignore_errors=True)
    os.makedirs(frames_dir)
    # map 1: offsets 0,300,600 — map 2 after a 1500s break: 2100,2400
    for off in (0, 300, 600):
        cv2.imwrite(os.path.join(frames_dir, f"{off:06d}.png"),
                    make_frame(COMP_A1, COMP_B1, offset=off))
    for off in (2100, 2400):
        cv2.imwrite(os.path.join(frames_dir, f"{off:06d}.png"),
                    make_frame(COMP_A2, COMP_B2, offset=off))
    # one corrupted frame that must be quarantined (blank slots)
    cv2.imwrite(os.path.join(frames_dir, "000900.png"),
                make_frame([], [], gameplay=True))

    lib = detect.load_templates()
    detect.process_match(con, "t01", frames_dir, lay, lib)

    snaps = con.execute("SELECT COUNT(*) c FROM comp_snapshots "
                        "WHERE match_id='t01'").fetchone()["c"]
    check("5 good frames → 10 snapshots (2 teams each)", snaps == 10)
    qdir = os.path.join(ROOT, "work", "t01", "quarantine")
    check("bad frame quarantined, not written",
          os.path.isdir(qdir) and len(
              [f for f in os.listdir(qdir) if f.endswith(".png")]) == 1)
    got = [r["hero_id"] for r in con.execute(
        """SELECT sh.hero_id FROM snapshot_heroes sh
           JOIN comp_snapshots cs ON cs.id=sh.snapshot_id
           WHERE cs.match_id='t01' AND cs.team_id='falcons'
             AND cs.stream_offset_seconds=0 ORDER BY sh.slot""")]
    check(f"comp read exactly right ({got})", got == COMP_A1)

    # ---- 3) map sync ---------------------------------------------------
    print("3) map_sync.py")
    map_sync.sync_match(con, "t01", interval=300, gap_factor=2.5,
                        report_only=False)
    unassigned = con.execute(
        "SELECT COUNT(*) c FROM comp_snapshots WHERE match_id='t01' "
        "AND map_result_id IS NULL").fetchone()["c"]
    check("all snapshots assigned to a map", unassigned == 0)
    m2 = con.execute(
        """SELECT DISTINCT sh.hero_id FROM snapshot_heroes sh
           JOIN comp_snapshots cs ON cs.id=sh.snapshot_id
           JOIN map_results mr ON mr.id=cs.map_result_id
           WHERE mr.map_order=2 AND cs.team_id='falcons'""").fetchall()
    check("map 2 holds the second comp",
          sorted(r["hero_id"] for r in m2) == sorted(COMP_A2))

    # ---- 4) export ------------------------------------------------------
    print("4) export_data.py")
    import export_data
    payload = export_data.build_payload(con)
    t01 = [m for m in payload["matches"] if m["id"] == "t01"]
    check("exported match present", len(t01) == 1)
    m0, m1 = t01[0]["maps"][0], t01[0]["maps"][1]
    check("exported tracker comps match detection",
          m0["tracker"]["playedHeroesA"] == COMP_A1
          and m1["tracker"]["playedHeroesA"] == COMP_A2)
    check("tracker/faceit separation present",
          m0["tracker"]["detected"] is True
          and "heroBans" in m0["faceit"] and "mapScores" in m0["faceit"]
          and m0["tracker"]["openerCompA"] == COMP_A1
          and m0["tracker"]["compTimeline"]["a"][0]["heroes"] == COMP_A1)
    check("winner side correct", t01[0]["maps"][0]["winner"] == "a")

    print("\nALL PIPELINE TESTS PASSED")


if __name__ == "__main__":
    main()
