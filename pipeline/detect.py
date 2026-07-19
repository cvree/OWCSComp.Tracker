#!/usr/bin/env python3
"""
detect.py — Stage 3B: gameplay frames → comp snapshots.

For each kept frame in work/{match_id}/frames/:
  1. crop the 10 hero-portrait slots (5 per team) from the layout config,
  2. template-match each slot against templates/{hero_id}.png,
  3. validate: every slot beats the threshold AND no duplicate hero
     within a team — otherwise the frame goes to work/{match}/quarantine/
     with a JSON sidecar of scores and is never written to the DB,
  4. write comp_snapshots + snapshot_heroes (deduped by frame hash).

Matching runs on grayscale (luminance), which makes it robust to the
red/blue team tint broadcasts apply to icons; if a season's package
tints too aggressively, add per-team template variants named
templates/{hero_id}.a.png / .b.png and they are used automatically.

Also includes the one-time template builder:
  python3 pipeline/detect.py --layout L.json --build-templates frame.png
     → dumps every slot crop to templates/_candidates/ for you to rename
       to {hero_id}.png. ~8 clean frames covers the whole roster.

Usage:
  python3 pipeline/detect.py --layout layouts/owcs-demo.json --match m01
  python3 pipeline/detect.py --layout ... --match m01 --frames-dir some/dir
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
from capture import load_layout  # noqa: E402

TEMPLATES_DIR = os.path.join(db.REPO_ROOT, "templates")
WORK_DIR = os.path.join(db.REPO_ROOT, "work")


# ------------------------------------------------------------- templates
def load_templates(templates_dir: str | None = None) -> dict:
    """{hero_id: [gray_img, ...]} — supports optional .a/.b team variants.

    templates_dir defaults to the repo templates/ directory. Passing an
    explicit dir lets the video layer use an isolated fixture template set
    without touching the production templates/ folder.
    """
    tdir = templates_dir or TEMPLATES_DIR
    lib: dict[str, list] = {}
    if not os.path.isdir(tdir):
        raise FileNotFoundError(
            f"{tdir} missing. Build it with --build-templates first.")
    for fn in sorted(os.listdir(tdir)):
        if not fn.lower().endswith(".png") or fn.startswith("_"):
            continue
        # '<hero>.png' or '<hero>.<variant>.png' — any variant tag works
        # (state variants: alive/dead art, ult flash, mech/hamster, ...)
        hero_id = fn[:-4].split(".")[0]
        img = cv2.imread(os.path.join(tdir, fn), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            lib.setdefault(hero_id, []).append((img, fn))
    if not lib:
        raise FileNotFoundError(f"No hero templates in {tdir}.")
    return lib


def build_templates(frame_path: str, layout: dict) -> None:
    """Dump all 10 slot crops from one frame for manual renaming."""
    out = os.path.join(TEMPLATES_DIR, "_candidates")
    os.makedirs(out, exist_ok=True)
    frame = cv2.imread(frame_path)
    if frame is None:
        raise FileNotFoundError(frame_path)
    base = os.path.splitext(os.path.basename(frame_path))[0]
    for side in ("slots_a", "slots_b"):
        for i, (x, y, w, h) in enumerate(layout[side], start=1):
            crop = frame[y:y + h, x:x + w]
            path = os.path.join(out, f"{base}_{side[-1]}{i}.png")
            cv2.imwrite(path, crop)
    print(f"Wrote 10 slot crops to {out}/ — rename the clean ones to "
          f"templates/<hero_id>.png")


# --------------------------------------------------------------- matching
UNKNOWN_FLOOR = 0.35      # below this the slot is UNKNOWN, full stop
MIN_MARGIN = 0.04         # top must beat runner-up by this to be trusted


def match_slot_ranked(slot_gray, lib: dict) -> list[dict]:
    """All heroes ranked by best-variant score for one slot crop.

    Templates are normalized to the slot's actual size (both directions),
    so a template cut at one capture resolution still matches slots cropped
    at another. Returns [{hero, score, template}] sorted best-first."""
    ranked = []
    for hero_id, tpls in lib.items():
        best_score, best_fn = -1.0, ""
        for tpl, fn in tpls:
            t = tpl
            if t.shape[:2] != slot_gray.shape[:2]:
                t = cv2.resize(t, (slot_gray.shape[1], slot_gray.shape[0]))
            res = cv2.matchTemplate(slot_gray, t, cv2.TM_CCOEFF_NORMED)
            score = float(res.max())
            if score > best_score:
                best_score, best_fn = score, fn
        ranked.append({"hero": hero_id, "score": best_score,
                       "template": best_fn})
    ranked.sort(key=lambda r: -r["score"])
    return ranked


def read_slot(slot_gray, lib: dict,
              floor: float = UNKNOWN_FLOOR,
              min_margin: float = MIN_MARGIN) -> dict:
    """Honest single-slot read: top + runner-up + margin + rejection reason.

    hero == 'UNKNOWN' whenever the evidence is insufficient — a weak match
    is NEVER silently converted into a confident hero label."""
    ranked = match_slot_ranked(slot_gray, lib)
    top = ranked[0] if ranked else {"hero": "", "score": -1.0, "template": ""}
    second = ranked[1] if len(ranked) > 1 else {"hero": "", "score": -1.0}
    margin = top["score"] - second["score"]
    out = {"hero": top["hero"], "score": round(top["score"], 3),
           "template": top["template"],
           "second": second["hero"], "second_score": round(second["score"], 3),
           "margin": round(margin, 3), "reject": None,
           "scores": {r["hero"]: round(r["score"], 3) for r in ranked}}
    if top["score"] < floor:
        out["hero"] = "UNKNOWN"
        out["reject"] = (f"no-match: best {top['hero']}@{top['score']:.2f} "
                         f"below floor {floor}")
    elif margin < min_margin:
        out["hero"] = "UNKNOWN"
        out["reject"] = (f"ambiguous: {top['hero']}@{top['score']:.2f} vs "
                         f"{second['hero']}@{second['score']:.2f} "
                         f"(margin {margin:.3f} < {min_margin})")
    return out


def match_slot(slot_gray, lib: dict) -> tuple[str, float]:
    """Best (hero_id, score) for one slot crop (legacy simple API)."""
    ranked = match_slot_ranked(slot_gray, lib)
    if not ranked:
        return "", -1.0
    return ranked[0]["hero"], ranked[0]["score"]


def read_frame_comps(frame_bgr, layout: dict, lib: dict) -> dict:
    """Both teams' comps from one frame, with per-slot scores."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    out = {}
    for side, key in (("a", "slots_a"), ("b", "slots_b")):
        slots = []
        for (x, y, w, h) in layout[key]:
            hero, score = match_slot(gray[y:y + h, x:x + w], lib)
            slots.append({"hero": hero, "score": round(score, 3)})
        out[side] = slots
    return out


def validate(slots: list[dict], threshold: float) -> str | None:
    """None if valid, else a rejection reason."""
    heroes = [s["hero"] for s in slots]
    low = [s for s in slots if s["score"] < threshold]
    if low:
        return ("low-confidence: "
                + ", ".join(f"{s['hero']}@{s['score']}" for s in low))
    if len(set(heroes)) != len(heroes):
        return f"duplicate hero within team: {heroes}"
    return None


# ---------------------------------------------------------------- persist
def write_snapshot(con, match_id: str, team_id: str, offset: int,
                   slots: list[dict], frame_hash: str) -> bool:
    conf = float(np.mean([s["score"] for s in slots]))
    try:
        cur = con.execute(
            """INSERT INTO comp_snapshots
               (match_id, map_result_id, team_id, stream_offset_seconds,
                overall_confidence, frame_hash)
               VALUES (?,?,?,?,?,?)""",
            (match_id, None, team_id, offset, conf, frame_hash),
        )
    except Exception:
        return False  # duplicate frame_hash+team → already recorded
    snap_id = cur.lastrowid
    con.executemany(
        "INSERT INTO snapshot_heroes (snapshot_id, slot, hero_id, confidence)"
        " VALUES (?,?,?,?)",
        [(snap_id, i, s["hero"], s["score"])
         for i, s in enumerate(slots, start=1)],
    )
    return True


def process_match(con, match_id: str, frames_dir: str, layout: dict,
                  lib: dict) -> None:
    m = con.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    if m is None:
        raise SystemExit(f"unknown match id: {match_id}")
    threshold = layout.get("match_threshold", 0.6)
    qdir = os.path.join(WORK_DIR, match_id, "quarantine")

    frames = sorted(f for f in os.listdir(frames_dir) if f.endswith(".png"))
    n_ok = n_q = 0
    for fn in frames:
        fp = os.path.join(frames_dir, fn)
        frame = cv2.imread(fp)
        if frame is None:
            continue
        offset = int(os.path.splitext(fn)[0])
        comps = read_frame_comps(frame, layout, lib)
        reasons = {s: validate(comps[s], threshold) for s in ("a", "b")}

        if any(reasons.values()):
            os.makedirs(qdir, exist_ok=True)
            shutil.copy(fp, os.path.join(qdir, fn))
            with open(os.path.join(qdir, fn + ".json"), "w") as f:
                json.dump({"reasons": reasons, "read": comps}, f, indent=1)
            n_q += 1
            continue

        fhash = hashlib.sha1(open(fp, "rb").read()).hexdigest()[:16]
        write_snapshot(con, match_id, m["team_a"], offset, comps["a"], fhash)
        write_snapshot(con, match_id, m["team_b"], offset, comps["b"], fhash)
        n_ok += 1

    con.commit()
    print(f"[{match_id}] frames accepted: {n_ok}, quarantined: {n_q}"
          + (f" (see {qdir})" if n_q else ""))
    if n_ok:
        print(f"[{match_id}] next: python3 pipeline/map_sync.py --match {match_id}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", required=True)
    ap.add_argument("--match", help="match id whose frames to process")
    ap.add_argument("--frames-dir", help="override frames dir "
                    "(default work/{match}/frames)")
    ap.add_argument("--build-templates", metavar="FRAME",
                    help="dump slot crops from one frame, then exit")
    args = ap.parse_args()

    layout = load_layout(args.layout)

    if args.build_templates:
        build_templates(args.build_templates, layout)
        return

    if not args.match:
        raise SystemExit("--match is required (or use --build-templates)")

    frames_dir = args.frames_dir or os.path.join(WORK_DIR, args.match, "frames")
    if not os.path.isdir(frames_dir):
        raise SystemExit(f"no frames at {frames_dir} — run capture.py first")

    con = db.connect()
    lib = load_templates()
    print(f"Loaded templates for {len(lib)} heroes.")
    process_match(con, args.match, frames_dir, layout, lib)


if __name__ == "__main__":
    main()
