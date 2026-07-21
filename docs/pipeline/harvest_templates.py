#!/usr/bin/env python3
"""
harvest_templates.py — build a per-source hero template set from real
broadcast frames, with human-verifiable evidence at every step.

Stage 1 (--cluster): sample frames across the map window, cut every slot
crop through the calibrated layout, and greedily cluster each slot's crops
by correlation. Distinct clusters = distinct portrait STATES seen in that
slot (different heroes after swaps, alive/dead art, ult flash, mech/pilot
forms). Writes:
    <out>/_candidates/<slot>_c<k>_n<count>.png   one representative each
    <out>/_candidates/montage.png                all clusters, labeled
    <out>/_candidates/clusters.json              metadata incl. time spans

Stage 2 (--labels): a human (or the calibration session) maps clusters to
hero ids in a JSON file {"a1_c0": "freja", "b3_c1": "dva", ...}; every
labeled cluster contributes up to --variants-per-cluster maximally
different member crops as <hero>.png / <hero>.v1.png / ... so state
variants ride along automatically. Unlabeled clusters are skipped loudly.

Usage:
  python pipeline/harvest_templates.py --clip work/clips/nepal_720p.mp4 \
      --times 60:980:10 --layout layouts/owcs_jksix_qwc.json \
      --out templates/owcs_jksix_qwc --cluster
  python pipeline/harvest_templates.py --layout layouts/owcs_jksix_qwc.json \
      --out templates/owcs_jksix_qwc --labels work/nepal_labels.json
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture  # noqa: E402

CLUSTER_SIM = 0.62      # min correlation to join an existing cluster


def corr(g1, g2) -> float:
    if g1.shape != g2.shape:
        g2 = cv2.resize(g2, (g1.shape[1], g1.shape[0]))
    return float(cv2.matchTemplate(g1, g2, cv2.TM_CCOEFF_NORMED).max())


def sharpness(gray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def parse_times(spec: str) -> list[float]:
    """'60:980:10' -> range; '10,20,30' -> list."""
    if ":" in spec:
        a, b, s = (float(x) for x in spec.split(":"))
        out, t = [], a
        while t <= b:
            out.append(round(t, 1))
            t += s
        return out
    return [float(x) for x in spec.split(",")]


def collect_crops(clip: str, times: list[float], layout: dict,
                  slot_keys: list[str]) -> dict:
    """{slot: [(t, crop_bgr, gray)]} via one ffmpeg call per frame."""
    crops: dict[str, list] = {k: [] for k in slot_keys}
    tmp = tempfile.mkdtemp(prefix="harvest_")
    for t in times:
        fp = os.path.join(tmp, f"f{t:.1f}.png")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error",
                        "-ss", str(t), "-i", clip, "-frames:v", "1",
                        "-y", fp], check=False)
        frame = cv2.imread(fp)
        if frame is None:
            continue
        fh, fw = frame.shape[:2]
        lay, info = capture.scale_layout_to_frame(layout, fw, fh)
        if not info["ok"]:
            continue
        for side in ("a", "b"):
            for i, (x, y, w, h) in enumerate(lay[f"slots_{side}"], 1):
                key = f"{side}{i}"
                crop = frame[y:y + h, x:x + w]
                if crop.size == 0:
                    continue
                gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                crops[key].append((t, crop, gray))
        os.remove(fp)
    return crops


def cluster_slot(entries: list) -> list[dict]:
    """Greedy correlation clustering; returns clusters sorted by size."""
    clusters: list[dict] = []
    for (t, crop, gray) in entries:
        best, best_sim = None, -1.0
        for c in clusters:
            sim = corr(c["proto_gray"], gray)
            if sim > best_sim:
                best_sim, best = sim, c
        if best is not None and best_sim >= CLUSTER_SIM:
            best["members"].append((t, crop, gray))
            # keep the sharpest member as prototype
            if sharpness(gray) > sharpness(best["proto_gray"]):
                best["proto_gray"], best["proto"] = gray, crop
        else:
            clusters.append({"proto": crop, "proto_gray": gray,
                             "members": [(t, crop, gray)]})
    clusters.sort(key=lambda c: -len(c["members"]))
    return clusters


def stage_cluster(args, layout, slot_keys) -> None:
    times = parse_times(args.times)
    crops = collect_crops(args.clip, times, layout, slot_keys)
    cand_dir = os.path.join(args.out, "_candidates")
    os.makedirs(cand_dir, exist_ok=True)
    meta = {}
    tiles = []
    tile_h = 64
    for key in slot_keys:
        clusters = cluster_slot(crops[key])
        row = []
        for k, c in enumerate(clusters):
            ts = sorted(m[0] for m in c["members"])
            name = f"{key}_c{k}"
            fn = f"{name}_n{len(c['members'])}.png"
            cv2.imwrite(os.path.join(cand_dir, fn), c["proto"])
            meta[name] = {"file": fn, "count": len(c["members"]),
                          "t_first": ts[0], "t_last": ts[-1]}
            tile = cv2.resize(c["proto"], (tile_h, tile_h),
                              interpolation=cv2.INTER_NEAREST)
            cv2.putText(tile, f"{key}c{k}", (1, 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1)
            cv2.putText(tile, f"n{len(c['members'])}", (1, tile_h - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1)
            row.append(tile)
        while len(row) < args.max_clusters:
            row.append(np.zeros((tile_h, tile_h, 3), np.uint8))
        tiles.append(np.hstack(row[:args.max_clusters]))
        # persist member list for stage 2 variant extraction
        mdir = os.path.join(cand_dir, "members", key)
        os.makedirs(mdir, exist_ok=True)
        for k, c in enumerate(clusters):
            for (t, crop, _g) in c["members"]:
                cv2.imwrite(os.path.join(mdir, f"c{k}_t{t:.1f}.png"), crop)
    cv2.imwrite(os.path.join(cand_dir, "montage.png"), np.vstack(tiles))
    with open(os.path.join(cand_dir, "clusters.json"), "w",
              encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    print(f"[harvest] clusters -> {cand_dir} "
          f"({len(meta)} clusters across {len(slot_keys)} slots)")
    print(f"[harvest] label them in a JSON mapping and run stage 2 "
          f"(--labels)")


def pick_variants(files: list[str], n: int) -> list[str]:
    """Greedy min-correlation pick of up to n maximally-different crops."""
    if not files:
        return []
    grays = {f: cv2.imread(f, cv2.IMREAD_GRAYSCALE) for f in files}
    files = [f for f in files if grays[f] is not None]
    files.sort(key=lambda f: -sharpness(grays[f]))
    chosen = [files[0]]
    while len(chosen) < n and len(chosen) < len(files):
        best, best_sim = None, 2.0
        for f in files:
            if f in chosen:
                continue
            sim = max(corr(grays[c], grays[f]) for c in chosen)
            if sim < best_sim:
                best_sim, best = sim, f
        if best is None or best_sim > 0.995:
            break
        chosen.append(best)
    return chosen


def stage_labels(args, slot_keys) -> None:
    with open(args.labels, "r", encoding="utf-8") as f:
        labels = json.load(f)
    cand_dir = os.path.join(args.out, "_candidates")
    members_root = os.path.join(cand_dir, "members")
    by_hero: dict[str, list[str]] = {}
    unlabeled = []
    with open(os.path.join(cand_dir, "clusters.json"), encoding="utf-8") as f:
        meta = json.load(f)
    for name, info in meta.items():
        hero = labels.get(name)
        key, ck = name.rsplit("_c", 1)
        if not hero:
            unlabeled.append(f"{name} (n={info['count']})")
            continue
        if hero in ("skip", "-"):
            continue
        mdir = os.path.join(members_root, key)
        files = [os.path.join(mdir, f) for f in os.listdir(mdir)
                 if f.startswith(f"c{ck}_")]
        by_hero.setdefault(hero, []).extend(files)
    # clean previous hero pngs (keep _candidates)
    for fn in os.listdir(args.out):
        if fn.endswith(".png"):
            os.remove(os.path.join(args.out, fn))
    n_files = 0
    for hero, files in sorted(by_hero.items()):
        chosen = pick_variants(files, args.variants)
        for i, f in enumerate(chosen):
            suffix = "" if i == 0 else f".v{i}"
            dst = os.path.join(args.out, f"{hero}{suffix}.png")
            cv2.imwrite(dst, cv2.imread(f))
            n_files += 1
        print(f"[harvest] {hero}: {len(chosen)} variant(s) "
              f"from {len(files)} member crops")
    print(f"[harvest] wrote {n_files} template files -> {args.out}")
    if unlabeled:
        print(f"[harvest] WARNING {len(unlabeled)} unlabeled clusters "
              f"(review montage.png): " + ", ".join(unlabeled[:12]))
    _ = slot_keys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip")
    ap.add_argument("--times", help="'start:end:step' or comma list "
                    "(clip-relative seconds)")
    ap.add_argument("--layout", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cluster", action="store_true")
    ap.add_argument("--labels", help="cluster->hero JSON (stage 2)")
    ap.add_argument("--variants", type=int, default=3)
    ap.add_argument("--max-clusters", type=int, default=10)
    args = ap.parse_args(argv)

    layout = capture.load_layout(args.layout)
    slot_keys = [f"{s}{i}" for s in ("a", "b") for i in range(1, 6)]
    if args.cluster:
        if not args.clip or not args.times:
            raise SystemExit("--cluster needs --clip and --times")
        stage_cluster(args, layout, slot_keys)
    elif args.labels:
        stage_labels(args, slot_keys)
    else:
        raise SystemExit("choose --cluster or --labels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
