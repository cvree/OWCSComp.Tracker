#!/usr/bin/env python3
"""
template_quality.py — the gate a harvested crop must pass to become a
production hero template.

Why this exists: `harvest_templates.py --labels` used to copy whatever crop
a cluster happened to contain straight into `templates/<hero>.png`. That is
the single highest-leverage place to introduce a permanent, silent
detection defect, because a bad template does not fail — it *matches*.
Concretely, the four ways a harvest poisons a template set:

  * **a flat crop** (a black killcam fade, a full-screen overlay, a
    letterbox bar) correlates weakly but *evenly* with every slot, so it
    wins ties and turns UNKNOWN into a confident wrong answer;
  * **a blurred crop** (motion, a scene wipe mid-cut) does the same, more
    subtly, because blur destroys exactly the high-frequency detail that
    distinguishes two portraits;
  * **a mislabeled crop** — the cluster was called `reaper` but the pixels
    are `ana`'s portrait — is indistinguishable from a correct template by
    every metric except *comparison against the rest of the set*, which is
    why `distinct_from_other_heroes` exists;
  * **an untraceable crop** cannot be audited later. If a hero starts
    mis-detecting six months from now, "which frame did this template come
    from" must have an answer.

Nothing here decides what a crop *is*. It decides whether a crop is good
enough to be trusted as evidence for the label a human already gave it.

Three verdicts, because they need three different actions:

  ACCEPT  — every check passed; safe to write into the template set.
  REVIEW  — nothing is provably wrong, but something is marginal. Goes to
            the graphical review inbox with the crop attached. NEVER
            written to production without a human decision.
  REJECT  — a check failed outright. Writing this would degrade detection.

The thresholds below are deliberately conservative: the cost of rejecting a
usable crop is one more harvest, and the cost of accepting a bad one is
wrong data in a published composition.

CLI:
  python3 pipeline/template_quality.py --dir templates/owcs_jksix_qwc
  python3 pipeline/template_quality.py --dir templates/... --json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ACCEPT, REVIEW, REJECT = "ACCEPT", "REVIEW", "REJECT"
OK, WARN, FAIL = "ok", "warn", "fail"

# --------------------------------------------------------------- thresholds
# A portrait smaller than this cannot carry enough detail to distinguish 52
# heroes; broadcast HUD portraits are ~40-90px on the short side at 1080p.
MIN_SIDE = 20
# Below this the crop is too small to be anything but a scaling artifact.
HARD_MIN_SIDE = 12

# Laplacian variance. Real portrait art is full of edges; a scene wipe or a
# motion-blurred frame is not. Measured on the 17 committed root templates
# (which came from real broadcast frames) the floor sits far below any of
# them, so this rejects mush without rejecting legitimately soft captures.
MIN_SHARPNESS = 25.0
WARN_SHARPNESS = 60.0

# Grayscale standard deviation. A near-uniform crop (overlay, fade, empty
# slot) has almost none.
MIN_CONTRAST = 10.0
WARN_CONTRAST = 18.0

# Fraction of pixels within +/-6 grey levels of the modal value. A crop that
# is 90% one colour is an overlay, not a portrait.
MAX_FLAT_FRACTION = 0.86
WARN_FLAT_FRACTION = 0.70

# Correlation against an accepted template of a DIFFERENT hero. Two distinct
# heroes' portraits do not look 0.93-alike; at that level one of the two
# labels is wrong, or the crop is an overlay that covers both.
CONFUSION_REJECT = 0.93
CONFUSION_WARN = 0.86

# Correlation against another template of the SAME hero. Above this the crop
# adds no information — it is the same picture again, and every extra
# template costs match time on every slot of every frame.
DUPLICATE_CEIL = 0.985

# A crop whose largest single-colour connected band covers this much of its
# area is obstructed (killfeed card, ult banner, scoreboard wipe).
MAX_OBSTRUCTION = 0.45
WARN_OBSTRUCTION = 0.28


def _cv2():
    import cv2  # local: coverage reporting must stay importable without cv2
    return cv2


def _np():
    import numpy as np
    return np


def _check(name: str, status: str, detail: str, **extra) -> dict:
    out = {"name": name, "status": status, "detail": detail}
    out.update(extra)
    return out


# ------------------------------------------------------------------ metrics
def sharpness(gray) -> float:
    cv2 = _cv2()
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def contrast(gray) -> float:
    return float(gray.std())


def flat_fraction(gray, tol: int = 6) -> float:
    """Fraction of pixels within `tol` grey levels of the modal value."""
    np = _np()
    hist = np.bincount(gray.reshape(-1), minlength=256)
    mode = int(hist.argmax())
    lo, hi = max(0, mode - tol), min(255, mode + tol)
    return float(hist[lo:hi + 1].sum()) / float(gray.size)


def obstruction_fraction(gray) -> float:
    """Largest axis-aligned run of near-identical rows/columns, as a
    fraction of the crop.

    An overlay that covers a portrait almost always does so as a *band* — a
    killfeed card, an ult-charge banner, a scoreboard wipe. Those produce
    contiguous rows (or columns) with near-zero internal variance, which a
    whole-crop statistic like `contrast` happily averages away.
    """
    np = _np()
    g = gray.astype(np.float32)
    best = 0.0
    for axis, extent in ((1, g.shape[0]), (0, g.shape[1])):
        var = g.var(axis=axis)          # per-row (axis=1) / per-column
        flat = var < 12.0
        run = longest = 0
        for f in flat:
            run = run + 1 if f else 0
            longest = max(longest, run)
        best = max(best, longest / float(extent))
    return float(best)


def correlate(a, b) -> float:
    """Peak normalized cross-correlation, size-normalized like detect.py."""
    cv2 = _cv2()
    if a.shape[:2] != b.shape[:2]:
        b = cv2.resize(b, (a.shape[1], a.shape[0]))
    return float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED).max())


# -------------------------------------------------------------------- gate
def assess_crop(gray, *, hero_id: str,
                same_hero: "dict[str, object] | None" = None,
                other_heroes: "dict[str, object] | None" = None,
                provenance: dict | None = None,
                require_provenance: bool = True) -> dict:
    """Judge ONE candidate crop for ONE hero.

    `same_hero`   {label: gray} already-accepted templates for this hero.
    `other_heroes` {label: gray} accepted templates for every OTHER hero.
    `provenance`  {"sourceVideo"|"sourceClip": str, "offset": float, ...}

    Returns {"verdict", "checks": [...], "metrics": {...}, "reasons": [...]}.
    """
    checks: list[dict] = []
    metrics: dict = {}

    h, w = gray.shape[:2]
    short = min(h, w)
    metrics["width"], metrics["height"] = int(w), int(h)
    if short < HARD_MIN_SIDE:
        checks.append(_check(
            "size", FAIL,
            f"{w}x{h} — smaller than {HARD_MIN_SIDE}px on the short side is "
            f"not a portrait crop, it is a scaling artifact"))
    elif short < MIN_SIDE:
        checks.append(_check(
            "size", WARN,
            f"{w}x{h} — under {MIN_SIDE}px on the short side; detection will "
            f"upscale it and lose the detail that separates similar heroes"))
    else:
        checks.append(_check("size", OK, f"{w}x{h}"))

    sh = sharpness(gray)
    metrics["sharpness"] = round(sh, 2)
    if sh < MIN_SHARPNESS:
        checks.append(_check(
            "sharpness", FAIL,
            f"Laplacian variance {sh:.1f} < {MIN_SHARPNESS} — a blurred or "
            f"smeared crop matches everything weakly and wins ties it "
            f"should lose"))
    elif sh < WARN_SHARPNESS:
        checks.append(_check(
            "sharpness", WARN,
            f"Laplacian variance {sh:.1f} is soft (below {WARN_SHARPNESS})"))
    else:
        checks.append(_check("sharpness", OK, f"Laplacian variance {sh:.1f}"))

    ct = contrast(gray)
    metrics["contrast"] = round(ct, 2)
    if ct < MIN_CONTRAST:
        checks.append(_check(
            "contrast", FAIL,
            f"grey std {ct:.1f} < {MIN_CONTRAST} — a near-uniform crop "
            f"(overlay, fade, empty slot), not a portrait"))
    elif ct < WARN_CONTRAST:
        checks.append(_check("contrast", WARN, f"grey std {ct:.1f} is low"))
    else:
        checks.append(_check("contrast", OK, f"grey std {ct:.1f}"))

    ff = flat_fraction(gray)
    metrics["flatFraction"] = round(ff, 3)
    if ff > MAX_FLAT_FRACTION:
        checks.append(_check(
            "not-flat", FAIL,
            f"{ff * 100:.0f}% of pixels are one shade — this is an overlay "
            f"or a blank slot"))
    elif ff > WARN_FLAT_FRACTION:
        checks.append(_check(
            "not-flat", WARN,
            f"{ff * 100:.0f}% of pixels are one shade"))
    else:
        checks.append(_check("not-flat", OK, f"{ff * 100:.0f}% modal"))

    ob = obstruction_fraction(gray)
    metrics["obstruction"] = round(ob, 3)
    if ob > MAX_OBSTRUCTION:
        checks.append(_check(
            "unobstructed", FAIL,
            f"a flat band covers {ob * 100:.0f}% of the crop — a killfeed "
            f"card, ult banner or scene wipe is sitting on the portrait"))
    elif ob > WARN_OBSTRUCTION:
        checks.append(_check(
            "unobstructed", WARN,
            f"a flat band covers {ob * 100:.0f}% of the crop"))
    else:
        checks.append(_check("unobstructed", OK,
                             f"largest flat band {ob * 100:.0f}%"))

    # ---- against the rest of the set
    other_heroes = other_heroes or {}
    worst_other, worst_score = None, -1.0
    for label, img in other_heroes.items():
        s = correlate(gray, img)
        if s > worst_score:
            worst_score, worst_other = s, label
    metrics["nearestOtherHero"] = worst_other
    metrics["nearestOtherScore"] = round(worst_score, 4) if worst_other else None
    if worst_other is not None and worst_score >= CONFUSION_REJECT:
        checks.append(_check(
            "distinct-from-other-heroes", FAIL,
            f"correlates {worst_score:.3f} with {worst_other} — two different "
            f"heroes do not look this alike, so either this crop is "
            f"mislabeled as {hero_id} or {worst_other} is mislabeled",
            other=worst_other, score=round(worst_score, 4)))
    elif worst_other is not None and worst_score >= CONFUSION_WARN:
        checks.append(_check(
            "distinct-from-other-heroes", WARN,
            f"correlates {worst_score:.3f} with {worst_other} — close enough "
            f"that detection will need a clean margin to separate them",
            other=worst_other, score=round(worst_score, 4)))
    else:
        checks.append(_check(
            "distinct-from-other-heroes", OK,
            (f"nearest other hero {worst_other} at {worst_score:.3f}"
             if worst_other else "no other hero templates to compare against"),
            other=worst_other,
            score=round(worst_score, 4) if worst_other else None))

    same_hero = same_hero or {}
    dup_of, dup_score = None, -1.0
    for label, img in same_hero.items():
        s = correlate(gray, img)
        if s > dup_score:
            dup_score, dup_of = s, label
    metrics["nearestSameScore"] = round(dup_score, 4) if dup_of else None
    if dup_of is not None and dup_score >= DUPLICATE_CEIL:
        checks.append(_check(
            "adds-information", FAIL,
            f"correlates {dup_score:.3f} with {dup_of}, an existing template "
            f"for the same hero — it is the same picture again and costs "
            f"match time on every slot without covering a new state",
            other=dup_of, score=round(dup_score, 4)))
    else:
        checks.append(_check(
            "adds-information", OK,
            (f"most similar existing {hero_id} template {dup_of} at "
             f"{dup_score:.3f}" if dup_of else
             f"first template for {hero_id}"),
            other=dup_of, score=round(dup_score, 4) if dup_of else None))

    # ---- provenance
    prov = provenance or {}
    src = prov.get("sourceVideo") or prov.get("sourceClip")
    has_offset = prov.get("offset") is not None
    if not require_provenance:
        checks.append(_check("provenance", OK, "not required in this mode"))
    elif not src:
        checks.append(_check(
            "provenance", FAIL,
            "no source video or clip recorded — an untraceable template "
            "cannot be audited when the hero it covers starts mis-detecting"))
    elif _looks_official(src):
        checks.append(_check(
            "provenance", FAIL,
            f"source {src!r} is official hero art, not a broadcast frame. "
            f"Official renders are for LABELLING clusters only; a template "
            f"cut from one looks plausible and quietly degrades every "
            f"detection it takes part in"))
    elif not has_offset:
        checks.append(_check(
            "provenance", WARN,
            f"source {src!r} recorded but no timestamp — the exact frame "
            f"cannot be re-cut"))
    else:
        checks.append(_check(
            "provenance", OK,
            f"{src} @ {float(prov['offset']):.1f}s"))

    statuses = {c["status"] for c in checks}
    if FAIL in statuses:
        verdict = REJECT
    elif WARN in statuses:
        verdict = REVIEW
    else:
        verdict = ACCEPT
    reasons = [f"{c['name']}: {c['detail']}" for c in checks
               if c["status"] != OK]
    return {"verdict": verdict, "heroId": hero_id, "checks": checks,
            "metrics": metrics, "reasons": reasons}


_OFFICIAL_MARKERS = ("assets/img/heroes/official", "assets\\img\\heroes\\official",
                     "heroes/official", "heroes\\official")


def _looks_official(source: str) -> bool:
    s = str(source).replace("\\", "/").lower()
    return any(m.replace("\\", "/") in s for m in _OFFICIAL_MARKERS)


# ------------------------------------------------- whole-set audit (CLI-ish)
def load_set(templates_dir: str) -> dict:
    """{filename: gray} for every hero template PNG in a set.

    Mirrors `detect.load_templates`' filename grammar: `_`-prefixed files are
    ignored, `<hero>.<variant>.png` belongs to `<hero>`.
    """
    cv2 = _cv2()
    out: dict = {}
    for path in sorted(glob.glob(os.path.join(templates_dir, "*.png"))):
        fn = os.path.basename(path)
        if fn.startswith("_"):
            continue
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is not None:
            out[fn] = img
    return out


def hero_of(filename: str) -> str:
    return os.path.basename(filename)[:-4].split(".")[0]


def audit_set(templates_dir: str, *, provenance: dict | None = None,
              require_provenance: bool = False) -> dict:
    """Run the gate over every template already in a set.

    Used two ways: as a pre-flight on a set someone else built, and as the
    regression guard that a committed set has not silently rotted. Defaults
    to `require_provenance=False` because sets harvested before provenance
    existed are legacy, not broken — the coverage report names them
    separately rather than failing them here.
    """
    imgs = load_set(templates_dir)
    prov_by_file = {}
    if provenance:
        for e in provenance.get("entries", []):
            prov_by_file[e.get("file")] = e

    results: dict[str, dict] = {}
    for fn, img in imgs.items():
        hero = hero_of(fn)
        same = {k: v for k, v in imgs.items()
                if k != fn and hero_of(k) == hero}
        other = {k: v for k, v in imgs.items() if hero_of(k) != hero}
        results[fn] = assess_crop(
            img, hero_id=hero, same_hero=same, other_heroes=other,
            provenance=prov_by_file.get(fn),
            require_provenance=require_provenance)

    counts = {ACCEPT: 0, REVIEW: 0, REJECT: 0}
    for r in results.values():
        counts[r["verdict"]] += 1
    return {
        "templatesDir": templates_dir,
        "files": len(imgs),
        "counts": counts,
        "results": results,
        "rejected": sorted(f for f, r in results.items()
                           if r["verdict"] == REJECT),
        "review": sorted(f for f, r in results.items()
                         if r["verdict"] == REVIEW),
        "note": ("Every template in a production set should be ACCEPT. A "
                 "REJECT is a template actively degrading detection; a "
                 "REVIEW is one a human should look at before trusting."),
    }


def format_audit(report: dict) -> str:
    c = report["counts"]
    lines = [f"  {report['templatesDir']}: {report['files']} template(s) — "
             f"{c[ACCEPT]} accept, {c[REVIEW]} review, {c[REJECT]} reject"]
    for fn in report["rejected"]:
        for reason in report["results"][fn]["reasons"]:
            lines.append(f"    REJECT {fn}: {reason}")
    for fn in report["review"]:
        for reason in report["results"][fn]["reasons"]:
            lines.append(f"    review {fn}: {reason}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="quality gate for hero portrait templates")
    ap.add_argument("--dir", required=True, help="template set directory")
    ap.add_argument("--require-provenance", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fail-on-reject", action="store_true",
                    help="exit 1 if any template would be rejected")
    args = ap.parse_args(argv)

    import template_bootstrap as tb
    prov = tb.load_provenance(args.dir)
    report = audit_set(args.dir, provenance=prov,
                       require_provenance=args.require_provenance)
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        print(format_audit(report))
    if args.fail_on_reject and report["counts"][REJECT]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
