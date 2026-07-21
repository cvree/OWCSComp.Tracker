#!/usr/bin/env python3
"""
_gen_demo_fixtures.py — (re)generate the self-contained video demo bundle.

Produces small synthetic "broadcast frames" and a matching template set so
the whole video pipeline (ingest -> filter -> detect -> snapshots) can run
offline, with no yt-dlp/ffmpeg/network and no real footage. The synthetic
icons here are the same deterministic ones the pipeline tests use, so a
frame's slot crop matches templates/<hero>.png by construction.

Outputs (all under this folder):
  templates/<hero>.png        grayscale hero icons (fixture-only roster)
  anchor.png / replay.png     grayscale HUD-anchor / replay-marker crops
  demo_match/frames/*.png      color frames: 2 maps + break + replay + bad
  demo-layout.json             layout pointing at the fixtures above

Run:  python3 pipeline/fixtures/video/_gen_demo_fixtures.py
Nothing here touches the real templates/ or layouts/ folders or the DB.
"""
from __future__ import annotations
import json
import os

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
FRAMES_DIR = os.path.join(HERE, "demo_match", "frames")
TEMPLATES_DIR = os.path.join(HERE, "templates")

W, H = 1280, 720
SLOT_W, SLOT_H = 64, 64
SLOTS_A = [[40 + i * 80, 20, SLOT_W, SLOT_H] for i in range(5)]
SLOTS_B = [[800 + i * 80, 20, SLOT_W, SLOT_H] for i in range(5)]
ANCHOR_RECT = [560, 8, 160, 40]
REPLAY_RECT = [20, 620, 120, 60]

# Map 1 (team A shows a mid-map swap: genji -> sojourn at offset 600)
A_OPENER = ["winston", "tracer", "genji", "kiriko", "juno"]
A_SWAP = ["winston", "tracer", "sojourn", "kiriko", "juno"]
B_MAP1 = ["dva", "hazard", "freja", "ana", "lucio"]
# Map 2
A_MAP2 = ["rein", "reaper", "mei", "ana", "lucio"]
B_MAP2 = ["sigma", "widow", "ashe", "bap", "kiriko"]

ROSTER = sorted(set(A_OPENER + A_SWAP + B_MAP1 + A_MAP2 + B_MAP2))


def hero_icon(hero_id: str) -> np.ndarray:
    rng = np.random.default_rng(abs(hash(hero_id)) % (2**32))
    img = rng.integers(30, 226, size=(SLOT_H, SLOT_W, 3), dtype=np.uint8)
    img = cv2.GaussianBlur(img, (5, 5), 0)
    cv2.putText(img, hero_id[:3].upper(), (4, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return img


def paint_anchor(frame: np.ndarray) -> None:
    x, y, w, h = ANCHOR_RECT
    frame[y:y + h, x:x + w] = (40, 40, 40)
    cv2.rectangle(frame, (x + 4, y + 4), (x + w - 4, y + h - 4), (0, 180, 255), 2)
    cv2.putText(frame, "OBJ", (x + 50, y + 30), cv2.FONT_HERSHEY_SIMPLEX,
                0.8, (255, 255, 255), 2)


def paint_replay(frame: np.ndarray) -> None:
    x, y, w, h = REPLAY_RECT
    frame[y:y + h, x:x + w] = (0, 0, 160)
    cv2.putText(frame, "R", (x + 40, y + 45), cv2.FONT_HERSHEY_SIMPLEX,
                1.4, (255, 255, 255), 3)


def make_frame(comp_a, comp_b, gameplay=True, replay=False, offset=0) -> np.ndarray:
    # Flat background keeps the committed PNGs tiny (a few KB). A small
    # per-offset tag in an unused corner keeps every frame byte-distinct so
    # frame-hash dedup has real, different frames to work with.
    frame = np.full((H, W, 3), 18, dtype=np.uint8)
    cv2.putText(frame, f"t={offset}", (980, 700),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (90, 90, 90), 2)
    if gameplay or replay:
        paint_anchor(frame)
        for slots, comp in ((SLOTS_A, comp_a), (SLOTS_B, comp_b)):
            for (x, y, w, h), hid in zip(slots, comp):
                frame[y:y + h, x:x + w] = hero_icon(hid)
    if replay:
        paint_replay(frame)
    return frame


def main() -> None:
    os.makedirs(FRAMES_DIR, exist_ok=True)
    os.makedirs(TEMPLATES_DIR, exist_ok=True)

    # templates (self-consistent with the frame icons)
    for hid in ROSTER:
        cv2.imwrite(os.path.join(TEMPLATES_DIR, f"{hid}.png"),
                    cv2.cvtColor(hero_icon(hid), cv2.COLOR_BGR2GRAY))

    # anchor / replay crops (inset by 6px like the pipeline test)
    anchor = make_frame([], [], gameplay=True, offset=0)[
        ANCHOR_RECT[1] + 6:ANCHOR_RECT[1] + ANCHOR_RECT[3] - 6,
        ANCHOR_RECT[0] + 6:ANCHOR_RECT[0] + ANCHOR_RECT[2] - 6]
    replay = make_frame(A_OPENER, B_MAP1, gameplay=False, replay=True)[
        REPLAY_RECT[1] + 6:REPLAY_RECT[1] + REPLAY_RECT[3] - 6,
        REPLAY_RECT[0] + 6:REPLAY_RECT[0] + REPLAY_RECT[2] - 6]
    cv2.imwrite(os.path.join(HERE, "anchor.png"),
                cv2.cvtColor(anchor, cv2.COLOR_BGR2GRAY))
    cv2.imwrite(os.path.join(HERE, "replay.png"),
                cv2.cvtColor(replay, cv2.COLOR_BGR2GRAY))

    # frames: map1 {0,300,600 (swap)}, break 900, replay 1200, bad 1500,
    # map2 {2100,2400}
    plan = [
        (0,    A_OPENER, B_MAP1, dict()),
        (300,  A_OPENER, B_MAP1, dict()),
        (600,  A_SWAP,   B_MAP1, dict()),
        (900,  [],       [],     dict(gameplay=False)),          # break
        (1200, A_OPENER, B_MAP1, dict(gameplay=False, replay=True)),  # replay
        (1500, [],       [],     dict(gameplay=True)),           # bad/blank
        (2100, A_MAP2,   B_MAP2, dict()),
        (2400, A_MAP2,   B_MAP2, dict()),
    ]
    for off, a, b, kw in plan:
        img = make_frame(a, b, offset=off, **kw)
        cv2.imwrite(os.path.join(FRAMES_DIR, f"{off:06d}.png"), img)

    layout = {
        "frame_width": W, "frame_height": H,
        "sample_interval_seconds": 300,
        "anchor": {"rect": ANCHOR_RECT,
                   "template": "pipeline/fixtures/video/anchor.png",
                   "min_score": 0.7},
        "replay": {"rect": REPLAY_RECT,
                   "template": "pipeline/fixtures/video/replay.png",
                   "min_score": 0.7},
        "slots_a": SLOTS_A, "slots_b": SLOTS_B,
        "match_threshold": 0.6,
        "templates_dir": "pipeline/fixtures/video/templates",
    }
    with open(os.path.join(HERE, "demo-layout.json"), "w") as f:
        json.dump(layout, f, indent=1)

    print(f"Wrote {len(ROSTER)} templates, anchor/replay crops, "
          f"{len(plan)} frames, and demo-layout.json under {HERE}")


if __name__ == "__main__":
    main()
