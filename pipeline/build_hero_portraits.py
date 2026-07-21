#!/usr/bin/env python3
"""
build_hero_portraits.py — turn REAL broadcast template crops into the
public site's hero portrait assets.

Honesty rule: only per-source template directories (templates/<source>/)
are used — those crops were cut from actual OWCS broadcast frames during
template harvesting. The root-level templates/*.png synthetic starter set
is never used (a synthetic drawing is not "a picture of the hero").

For each hero with at least one real crop the best variant is chosen by a
deterministic quality score (source resolution, colorfulness, sharpness —
dead/desaturated portrait states lose), upscaled to PORTRAIT_SIZE px with
Lanczos, and written to assets/img/heroes/<hero>.png. A manifest records
exactly which broadcast crop each portrait came from, so every portrait
has a provenance chain just like every stat.

Usage:
  python3 pipeline/build_hero_portraits.py            # write portraits
  python3 pipeline/build_hero_portraits.py --dry-run  # report only
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

TEMPLATES_ROOT = os.path.join(db.REPO_ROOT, "templates")
OUT_DIR = os.path.join(db.REPO_ROOT, "assets", "img", "heroes")
MANIFEST_PATH = os.path.join(OUT_DIR, "manifest.json")
PORTRAIT_SIZE = 96

# <hero>.png, <hero>.v1.png, <hero>.a.png ... one hero id, many variants
_NAME_RE = re.compile(r"^([a-z0-9_-]+?)(?:\.(?:v\d+|[a-z]))?\.png$")


def real_template_files() -> list[tuple[str, str, str]]:
    """[(hero_id, source_id, abs_path)] for every per-source crop.
    Root-level PNGs (the synthetic starter set) are excluded on purpose."""
    out = []
    if not os.path.isdir(TEMPLATES_ROOT):
        return out
    for entry in sorted(os.listdir(TEMPLATES_ROOT)):
        d = os.path.join(TEMPLATES_ROOT, entry)
        if not os.path.isdir(d) or entry.startswith("_"):
            continue
        for fn in sorted(os.listdir(d)):
            m = _NAME_RE.match(fn)
            if m:
                out.append((m.group(1), entry, os.path.join(d, fn)))
    return out


def face_region(img):
    """Trim the player nameplate. Harvested cells at >=32px (the 720p
    Nepal set) include the name bar under the face — the face is the top
    ~65%. The 24px sets are portrait-only cells and pass through whole."""
    h, w = img.shape[:2]
    if h >= 32:
        img = img[: int(h * 0.65), :]
        h = img.shape[0]
        if w > h:  # center square so the upscale doesn't stretch
            x0 = (w - h) // 2
            img = img[:, x0:x0 + h]
    return img


def score_candidate(img) -> dict:
    """Deterministic quality score for one (nameplate-trimmed) crop.
    Dead/ult/damage-flash portrait states are desaturated, washed out or
    near-uniform — hue diversity + a sharpness floor push the alive base
    portrait to the top."""
    import cv2
    import numpy as np
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    sat = float(np.mean(hsv[:, :, 1])) / 255.0
    hue_div = float(np.std(hsv[:, :, 0].astype(np.float64))) / 90.0
    sharp = float(cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY),
                                cv2.CV_64F).var())
    val = float(np.mean(hsv[:, :, 2])) / 255.0
    bright_ok = 1.0 - abs(val - 0.5)
    score = (min(h, w) / 48.0) * 2.0 + sat * 1.5 + hue_div * 2.0 \
        + min(sharp / 300.0, 1.5) * 2.0 + bright_ok
    if sharp < 30.0:  # near-uniform fill (damage flash) — not a face
        score -= 4.0
    return {"score": round(score, 4), "size": min(h, w),
            "saturation": round(sat, 3), "sharpness": round(sharp, 1)}


def build(dry_run: bool = False) -> dict:
    import cv2
    candidates: dict[str, list[dict]] = {}
    for hero_id, source_id, path in real_template_files():
        img = cv2.imread(path)
        if img is None:
            continue
        img = face_region(img)
        rel = os.path.relpath(path, db.REPO_ROOT).replace(os.sep, "/")
        entry = {"file": rel, "source": source_id, "img": img}
        entry.update(score_candidate(img))
        candidates.setdefault(hero_id, []).append(entry)

    manifest: dict[str, dict] = {}
    for hero_id in sorted(candidates):
        # stable tie-break on the file path keeps regeneration deterministic
        best = sorted(candidates[hero_id],
                      key=lambda c: (-c["score"], c["file"]))[0]
        manifest[hero_id] = {k: best[k] for k in
                             ("file", "source", "score", "size")}
        if not dry_run:
            os.makedirs(OUT_DIR, exist_ok=True)
            up = cv2.resize(best["img"], (PORTRAIT_SIZE, PORTRAIT_SIZE),
                            interpolation=cv2.INTER_LANCZOS4)
            cv2.imwrite(os.path.join(OUT_DIR, f"{hero_id}.png"), up)

    if not dry_run:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "note": ("Hero portraits upscaled from REAL broadcast "
                         "template crops (see 'file' per hero). The "
                         "synthetic starter templates are never used."),
                "size": PORTRAIT_SIZE,
                "heroes": manifest,
            }, f, indent=1, ensure_ascii=False)
            f.write("\n")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    manifest = build(dry_run=args.dry_run)
    verb = "would write" if args.dry_run else "wrote"
    for hero_id, m in manifest.items():
        print(f"  {hero_id:<10s} <- {m['file']}  (score {m['score']})")
    print(f"{verb} {len(manifest)} portrait(s) to "
          f"{os.path.relpath(OUT_DIR, db.REPO_ROOT)}/ "
          f"+ manifest.json")


if __name__ == "__main__":
    main()
