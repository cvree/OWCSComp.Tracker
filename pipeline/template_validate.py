#!/usr/bin/env python3
"""
template_validate.py — prove a hero template works on frames it has never
seen, or say honestly that it is unproven.

A template set's coverage number ("8/52 heroes") answers *do we have a
picture*. It does not answer *does the picture work*, and those come apart
in both directions: a set can have a template for every hero and still
mis-read half of them, and a set can look thin while every hero in it is
rock solid. This module answers the second question, and it is built around
one rule that the rest of the design falls out of:

    A template is never scored against a frame it was cut from.

That rule is enforced structurally, not by convention. Every template
carries provenance (`_provenance.json`: which source, which offset, which
slot). Every evidence crop carries a source and a timestamp
(`template_evidence.py`). A crop is **held out** for hero H only when no
template of H was cut from the same source within `--min-gap` seconds of
it. Everything else is *contaminated* and excluded from the score, counted
separately so the exclusion is visible rather than silent.

If a hero's templates have no provenance at all, separation cannot be
proven, and the verdict is `UNVERIFIABLE` — not "passed", not "failed".
This is the current honest state of template sets harvested before
provenance existed, and rounding it up to a pass would be the single most
misleading thing this file could do.

### The four verdicts

  VALIDATED    — enough held-out trials, accuracy at or above the bar, and
                 **zero wrong answers**. A wrong answer is disqualifying on
                 its own: an UNKNOWN costs a data point, a confident wrong
                 hero corrupts a published composition.
  WEAK         — no wrong answers, but too few trials or too many UNKNOWNs.
                 Usable, not proven. Needs more evidence or a better crop.
  FAILED       — the template confidently returned the wrong hero on a
                 held-out frame. This is the one that must never ship.
  UNVERIFIABLE — no held-out evidence, or no provenance to prove separation.

### Negative evidence

Recall is only half the test. `false-match` trials feed the detector crops
of heroes the set has **no** template for, and require `UNKNOWN` back. A
set that scores 100% on its own heroes while confidently calling every
unknown portrait "lucio" is worse than useless, and only this half of the
test can see that.

CLI:
  python3 pipeline/template_validate.py --dir templates/owcs_jksix_qwc \
      --evidence work/evidence/nepal.json
  python3 pipeline/template_validate.py --dir ... --evidence ... --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import template_bootstrap as tb  # noqa: E402
import template_evidence as te  # noqa: E402

VALIDATED, WEAK, FAILED, UNVERIFIABLE = (
    "VALIDATED", "WEAK", "FAILED", "UNVERIFIABLE")

# Held-out trials a hero needs before "it works" means anything. Ten frames
# spread across a map is a real test; two adjacent frames is a coincidence.
MIN_TRIALS = 12
# correct / trials. UNKNOWNs count against this (they are a miss), wrong
# answers are handled separately and are always disqualifying.
MIN_ACCURACY = 0.90
# A held-out crop must be at least this far in time from every frame the
# hero's templates were cut from. Broadcast portraits barely change between
# adjacent samples, so a 5-second gap proves nothing.
MIN_GAP_SECONDS = 30.0
# Of the false-match trials (heroes with no template), at most this fraction
# may come back as a confident hero instead of UNKNOWN.
MAX_FALSE_MATCH_RATE = 0.02


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


# ------------------------------------------------------------- provenance
def provenance_index(templates_dir: str) -> dict[str, list[dict]]:
    """{hero_id: [provenance entry, ...]} for a template set."""
    prov = tb.load_provenance(templates_dir) or {}
    out: dict[str, list[dict]] = defaultdict(list)
    for entry in prov.get("entries", []):
        fn = entry.get("file") or ""
        if not fn.endswith(".png"):
            continue
        out[fn[:-4].split(".")[0]].append(entry)
    return dict(out)


def _source_key(value: str | None) -> str:
    """Normalize a source identifier so a report dir and its manifest agree."""
    if not value:
        return ""
    return str(value).replace("\\", "/").strip().rstrip("/").lower()


def hero_provenance_state(hero_id: str, template_files: list[str],
                          prov: dict[str, list[dict]]) -> tuple[bool, str]:
    """(can prove separation?, why not)."""
    entries = prov.get(hero_id) or []
    have = {e.get("file") for e in entries}
    missing = [f for f in template_files if f not in have]
    if not entries:
        return False, (f"no provenance recorded for any {hero_id} template — "
                       f"separation from the frames they were cut from cannot "
                       f"be proven")
    if missing:
        return False, (f"{len(missing)} {hero_id} template(s) have no "
                       f"provenance entry ({', '.join(sorted(missing)[:4])})"
                       f" — one untraced template is enough to make the whole "
                       f"hero unprovable")
    without_offset = [e.get("file") for e in entries
                      if e.get("offset") is None]
    if without_offset:
        return False, (f"{len(without_offset)} {hero_id} template(s) record a "
                       f"source but no timestamp, so a held-out frame cannot "
                       f"be shown to be a different frame")
    return True, ""


def is_contaminated(record: dict, entries: list[dict], *,
                    manifest_sources: set[str],
                    min_gap: float) -> str | None:
    """Reason this crop is NOT held out for its hero, or None if it is."""
    for e in entries:
        e_src = _source_key(e.get("sourceVideo") or e.get("sourceClip")
                            or e.get("sourceReport"))
        if e_src and e_src not in manifest_sources:
            continue          # different broadcast entirely — genuinely held out
        offset = e.get("offset")
        if offset is None:
            return (f"template {e.get('file')} records no timestamp, so this "
                    f"crop cannot be shown to be a different frame")
        gap = abs(float(record["t"]) - float(offset))
        if gap < min_gap:
            return (f"{gap:.1f}s from the frame template {e.get('file')} was "
                    f"cut from (needs {min_gap:.0f}s)")
    return None


def _spread(records: list[dict], limit: int | None) -> list[dict]:
    """At most `limit` records, sampled EVENLY across the timeline.

    Taking the first N instead would be a quiet lie whenever the thing being
    measured varies over a map: the opening minute of a broadcast (spawn
    room, hero-select dissolve, an ult flash during the first fight) is not
    representative of it. A capped run that only ever sees the beginning
    reports the beginning's accuracy as the package's.
    """
    if not limit or len(records) <= limit:
        return list(records)
    ordered = sorted(records, key=lambda r: float(r.get("t", 0.0)))
    step = len(ordered) / float(limit)
    return [ordered[min(len(ordered) - 1, int(i * step))]
            for i in range(limit)]


# -------------------------------------------------------------- validation
def validate(templates_dir: str, manifest: dict, *,
             min_trials: int = MIN_TRIALS,
             min_accuracy: float = MIN_ACCURACY,
             min_gap: float = MIN_GAP_SECONDS,
             max_false_match_rate: float = MAX_FALSE_MATCH_RATE,
             repo_root: str = db.REPO_ROOT,
             limit_per_hero: int | None = None,
             layout: dict | None = None) -> dict:
    """Score a template set against a labeled evidence manifest.

    `layout` supplies the detector profile (portrait ROI, UNKNOWN floor,
    margin) so validation measures the detector the pipeline will actually
    run, not a default-configured stand-in. Omit it and module defaults
    apply, which is right for a set whose layout declares nothing special.
    """
    import cv2
    import detect

    profile = detect.detector_profile(layout)
    lib = detect.load_templates(templates_dir, roi=profile["roi"])
    files_by_hero: dict[str, list[str]] = defaultdict(list)
    for hero_id, tpls in lib.items():
        for _img, fn in tpls:
            files_by_hero[hero_id].append(fn)
    prov = provenance_index(templates_dir)

    manifest_sources = {
        _source_key(manifest.get("sourceReport")),
        _source_key(manifest.get("sourceVideo")),
        _source_key(manifest.get("cropsDir")),
    } - {""}

    # Even sampling for the cap, computed up front so both the scoring and
    # the contamination bookkeeping see the same set of crops. Keyed by the
    # crop's position in the manifest rather than by filename, because two
    # slots can legitimately hold the same hero and produce distinct crops.
    kept_indices: set[int] | None = None
    if limit_per_hero:
        grouped: dict[str, list[int]] = defaultdict(list)
        for i, record in enumerate(manifest.get("crops", [])):
            if record.get("hero"):
                grouped[record["hero"]].append(i)
        kept_indices = set()
        for hero, indices in grouped.items():
            crops = manifest["crops"]
            chosen = _spread([dict(crops[i], _i=i) for i in indices],
                             limit_per_hero)
            kept_indices.update(r["_i"] for r in chosen)

    per_hero: dict[str, dict] = {}
    for hero_id in sorted(files_by_hero):
        provable, why = hero_provenance_state(
            hero_id, files_by_hero[hero_id], prov)
        per_hero[hero_id] = {
            "heroId": hero_id,
            "templates": sorted(files_by_hero[hero_id]),
            "separationProvable": provable,
            "separationNote": why,
            "trials": 0, "correct": 0, "unknown": 0, "wrong": 0,
            "contaminated": 0,
            "confusedWith": Counter(),
            "scores": [], "margins": [],
            "byState": defaultdict(lambda: {"trials": 0, "correct": 0}),
            "byLabelSource": Counter(),
            "examples": [],
        }

    false_match = {"trials": 0, "unknown": 0, "matched": 0,
                   "matchedAs": Counter(), "byHero": Counter(),
                   "examples": []}
    missing_crops = 0

    for index, record in enumerate(manifest.get("crops", [])):
        hero = record.get("hero")
        if not hero:
            continue
        if kept_indices is not None and index not in kept_indices:
            continue
        path = te.crop_path(manifest, record, repo_root=repo_root)
        if not os.path.exists(path):
            missing_crops += 1
            continue

        known = hero in files_by_hero
        if known:
            entry = per_hero[hero]
            reason = is_contaminated(
                record, prov.get(hero) or [],
                manifest_sources=manifest_sources, min_gap=min_gap)
            if reason:
                entry["contaminated"] += 1
                continue

        colour = cv2.imread(path, cv2.IMREAD_COLOR)
        if colour is None:
            missing_crops += 1
            continue
        gray = cv2.cvtColor(colour, cv2.COLOR_BGR2GRAY)
        read = detect.read_slot(gray, lib, floor=profile["floor"],
                                min_margin=profile["minMargin"],
                                roi=profile["roi"])
        got = read["hero"]

        if not known:
            false_match["trials"] += 1
            false_match["byHero"][hero] += 1
            if got == "UNKNOWN":
                false_match["unknown"] += 1
            else:
                false_match["matched"] += 1
                false_match["matchedAs"][got] += 1
                if len(false_match["examples"]) < 20:
                    false_match["examples"].append({
                        "file": record["file"], "trueHero": hero,
                        "readAs": got, "score": read["score"],
                        "margin": read["margin"],
                        "t": record["t"], "slot": record["slot"]})
            continue

        entry = per_hero[hero]
        entry["trials"] += 1
        entry["scores"].append(read["score"])
        entry["margins"].append(read["margin"])
        entry["byLabelSource"][record.get("labelSource", "?")] += 1
        state = record.get("state", "?")
        entry["byState"][state]["trials"] += 1
        if got == hero:
            entry["correct"] += 1
            entry["byState"][state]["correct"] += 1
        elif got == "UNKNOWN":
            entry["unknown"] += 1
            if len(entry["examples"]) < 12:
                entry["examples"].append({
                    "file": record["file"], "outcome": "unknown",
                    "score": read["score"], "margin": read["margin"],
                    "reject": read["reject"], "t": record["t"],
                    "slot": record["slot"]})
        else:
            entry["wrong"] += 1
            entry["confusedWith"][got] += 1
            if len(entry["examples"]) < 12:
                entry["examples"].append({
                    "file": record["file"], "outcome": "wrong",
                    "readAs": got, "score": read["score"],
                    "margin": read["margin"], "t": record["t"],
                    "slot": record["slot"]})

    # ---- verdicts
    for hero_id, e in per_hero.items():
        trials, correct = e["trials"], e["correct"]
        accuracy = (correct / trials) if trials else 0.0
        e["accuracy"] = round(accuracy, 4)
        e["meanScore"] = round(sum(e["scores"]) / len(e["scores"]), 4) \
            if e["scores"] else None
        e["minScore"] = round(min(e["scores"]), 4) if e["scores"] else None
        e["minMargin"] = round(min(e["margins"]), 4) if e["margins"] else None
        e["confusedWith"] = dict(e["confusedWith"])
        e["byLabelSource"] = dict(e["byLabelSource"])
        e["byState"] = {k: dict(v, accuracy=round(v["correct"] / v["trials"], 3)
                                if v["trials"] else 0.0)
                        for k, v in e["byState"].items()}
        del e["scores"], e["margins"]

        if not e["separationProvable"]:
            e["verdict"] = UNVERIFIABLE
            e["why"] = e["separationNote"]
        elif e["wrong"]:
            e["verdict"] = FAILED
            e["why"] = (f"{e['wrong']} held-out frame(s) read as the WRONG "
                        f"hero ({', '.join(sorted(e['confusedWith']))}) — a "
                        f"confident wrong answer corrupts a published "
                        f"composition and is disqualifying on its own")
        elif trials < min_trials:
            e["verdict"] = WEAK if trials else UNVERIFIABLE
            e["why"] = (f"only {trials} held-out trial(s); {min_trials} are "
                        f"needed before 'it works' means anything"
                        if trials else
                        "no held-out evidence for this hero in this manifest")
        elif accuracy < min_accuracy:
            e["verdict"] = WEAK
            e["why"] = (f"{correct}/{trials} correct ({accuracy * 100:.0f}%) "
                        f"with {e['unknown']} UNKNOWN — below the "
                        f"{min_accuracy * 100:.0f}% bar. No wrong answers, so "
                        f"this loses data rather than corrupting it")
        else:
            e["verdict"] = VALIDATED
            e["why"] = (f"{correct}/{trials} held-out frame(s) correct, zero "
                        f"wrong, min margin {e['minMargin']}")

    fm_rate = (false_match["matched"] / false_match["trials"]
               if false_match["trials"] else 0.0)
    false_match["rate"] = round(fm_rate, 4)
    false_match["matchedAs"] = dict(false_match["matchedAs"])
    false_match["byHero"] = dict(false_match["byHero"])
    false_match["passed"] = (false_match["trials"] == 0
                             or fm_rate <= max_false_match_rate)
    false_match["note"] = (
        f"{false_match['trials']} crop(s) of heroes this set has NO template "
        f"for. Every one should read UNKNOWN; {false_match['matched']} did "
        f"not ({fm_rate * 100:.1f}%, ceiling "
        f"{max_false_match_rate * 100:.1f}%)."
        if false_match["trials"] else
        "no crops of uncovered heroes in this manifest — the false-match half "
        "of the test did not run, so this set is unproven against portraits "
        "it has no template for")

    counts = Counter(e["verdict"] for e in per_hero.values())
    return {
        "generatedAt": _utcnow_iso(),
        "templatesDir": os.path.relpath(templates_dir, repo_root).replace("\\", "/")
        if os.path.isabs(templates_dir) else templates_dir,
        "evidence": {
            "sourceReport": manifest.get("sourceReport"),
            "layoutId": manifest.get("layoutId"),
            "labeledCrops": manifest.get("counts", {}).get("labeled"),
            "labelMethod": manifest.get("labelPolicy", {}).get("method"),
        },
        "thresholds": {
            "minTrials": min_trials, "minAccuracy": min_accuracy,
            "minGapSeconds": min_gap,
            "maxFalseMatchRate": max_false_match_rate,
        },
        "detectorProfile": {
            "portraitRoi": list(profile["roi"]) if profile["roi"] else None,
            "unknownFloor": profile["floor"],
            "minMargin": profile["minMargin"],
        },
        "heroes": per_hero,
        "counts": {v: counts.get(v, 0)
                   for v in (VALIDATED, WEAK, FAILED, UNVERIFIABLE)},
        "falseMatch": false_match,
        "missingCrops": missing_crops,
        "passed": (counts.get(FAILED, 0) == 0 and false_match["passed"]),
    }


# ------------------------------------------------- negative evidence (LOO)
def leave_one_out(templates_dir: str, manifest: dict, *,
                  min_gap: float = MIN_GAP_SECONDS,
                  max_false_match_rate: float = MAX_FALSE_MATCH_RATE,
                  layout: dict | None = None,
                  limit_per_hero: int | None = 120,
                  repo_root: str = db.REPO_ROOT) -> dict:
    """Does the set stay quiet about heroes it has no template for?

    The straightforward false-match test needs crops of uncovered heroes,
    and a set harvested from one broadcast usually has a template for
    everything that broadcast contained — so the test silently does not run,
    which is the worst possible outcome for a safety check.

    Leave-one-out manufactures the missing negative evidence from the same
    real footage: drop hero H's templates, then feed H's own crops back in.
    Every one of them **must** come back UNKNOWN, because a set without H
    genuinely cannot know what H is. Anything else is the detector inventing
    a hero, and it is the exact failure mode that turns an unharvested hero
    into wrong published data rather than an honest gap.

    This also measures the real cost of partial coverage, which is the
    situation every package is actually in: 8 of 52 heroes covered means 44
    heroes' portraits will be presented to a set that has no template for
    them, on every single frame.
    """
    import cv2
    import detect

    profile = detect.detector_profile(layout)
    full = detect.load_templates(templates_dir, roi=profile["roi"])
    prov = provenance_index(templates_dir)
    manifest_sources = {
        _source_key(manifest.get("sourceReport")),
        _source_key(manifest.get("sourceVideo")),
        _source_key(manifest.get("cropsDir")),
    } - {""}

    by_hero: dict[str, list[dict]] = defaultdict(list)
    for record in manifest.get("crops", []):
        if record.get("hero") in full:
            by_hero[record["hero"]].append(record)
    by_hero = {h: _spread(v, limit_per_hero) for h, v in by_hero.items()}

    per_hero: dict[str, dict] = {}
    for hero_id in sorted(by_hero):
        reduced = {h: t for h, t in full.items() if h != hero_id}
        if not reduced:
            per_hero[hero_id] = {
                "trials": 0, "unknown": 0, "matched": 0, "matchedAs": {},
                "passed": True,
                "note": "only one hero in the set — nothing to leave out"}
            continue
        trials = unknown = matched = 0
        matched_as: Counter = Counter()
        examples: list[dict] = []
        # Held-out frames only, for the same reason as the positive test: a
        # crop the template was cut from is not evidence about anything.
        for record in by_hero[hero_id]:
            if limit_per_hero and trials >= limit_per_hero:
                break
            if is_contaminated(record, prov.get(hero_id) or [],
                               manifest_sources=manifest_sources,
                               min_gap=min_gap):
                continue
            colour = cv2.imread(te.crop_path(manifest, record,
                                             repo_root=repo_root),
                                cv2.IMREAD_COLOR)
            if colour is None:
                continue
            gray = cv2.cvtColor(colour, cv2.COLOR_BGR2GRAY)
            trials += 1
            read = detect.read_slot(gray, reduced, floor=profile["floor"],
                                    min_margin=profile["minMargin"],
                                    roi=profile["roi"])
            if read["hero"] == "UNKNOWN":
                unknown += 1
            else:
                matched += 1
                matched_as[read["hero"]] += 1
                if len(examples) < 8:
                    examples.append({
                        "file": record["file"], "readAs": read["hero"],
                        "score": read["score"], "margin": read["margin"],
                        "t": record["t"], "slot": record["slot"]})
        rate = (matched / trials) if trials else 0.0
        per_hero[hero_id] = {
            "trials": trials, "unknown": unknown, "matched": matched,
            "rate": round(rate, 4),
            "matchedAs": dict(matched_as), "examples": examples,
            "passed": trials == 0 or rate <= max_false_match_rate,
            "note": (f"with {hero_id} removed, {matched}/{trials} of its own "
                     f"crops were still confidently named "
                     f"({', '.join(sorted(matched_as)) or 'n/a'})"),
        }

    total_trials = sum(e["trials"] for e in per_hero.values())
    total_matched = sum(e["matched"] for e in per_hero.values())
    rate = (total_matched / total_trials) if total_trials else 0.0

    # Which heroes this package cannot tell apart, named. A leak rate is a
    # number nobody can act on; "juno reads as kiriko 42% of the time when
    # juno is missing" is a specific thing an operator can watch for and a
    # future harvest can target with a better juno template.
    confusable = []
    for hero_id, e in per_hero.items():
        for other, n in sorted(e.get("matchedAs", {}).items(),
                               key=lambda kv: -kv[1]):
            if not e["trials"]:
                continue
            share = n / e["trials"]
            confusable.append({
                "hero": hero_id, "readAs": other, "count": n,
                "trials": e["trials"], "share": round(share, 3),
                "note": (f"with no {hero_id} template in the set, "
                         f"{n} of {e['trials']} real {hero_id} portraits "
                         f"({share * 100:.0f}%) were confidently read as "
                         f"{other} instead of UNKNOWN"),
            })
    confusable.sort(key=lambda c: -c["share"])

    return {
        "confusablePairs": confusable,
        "generatedAt": _utcnow_iso(),
        "templatesDir": templates_dir,
        "heroes": per_hero,
        "trials": total_trials,
        "matched": total_matched,
        "rate": round(rate, 4),
        "ceiling": max_false_match_rate,
        "passed": total_trials > 0 and rate <= max_false_match_rate,
        "note": (
            f"Leave-one-out false-match test over {total_trials} real crops: "
            f"{total_matched} ({rate * 100:.2f}%) were given a confident hero "
            f"name by a set that had no template for them. Ceiling "
            f"{max_false_match_rate * 100:.1f}%."
            if total_trials else
            "no held-out crops available — the leave-one-out test did not run"),
    }


def format_leave_one_out(report: dict) -> str:
    lines = [f"  {report['note']}"]
    for hero_id in sorted(report["heroes"]):
        e = report["heroes"][hero_id]
        flag = "ok  " if e["passed"] else "FAIL"
        lines.append(f"    {flag} without {hero_id:<10} "
                     f"{e['unknown']}/{e['trials']} correctly UNKNOWN"
                     + (f"; leaked as {e['matchedAs']}" if e["matched"] else ""))
    for pair in report.get("confusablePairs", [])[:6]:
        lines.append(f"    confusable: {pair['note']}")
    return "\n".join(lines)


def format_report(report: dict) -> str:
    c = report["counts"]
    lines = [
        f"  templates     : {report['templatesDir']}",
        f"  evidence      : {report['evidence']['sourceReport']} "
        f"({report['evidence']['labeledCrops']} labeled crops)",
        f"  verdicts      : {c[VALIDATED]} validated, {c[WEAK]} weak, "
        f"{c[FAILED]} failed, {c[UNVERIFIABLE]} unverifiable",
    ]
    for hero_id in sorted(report["heroes"]):
        e = report["heroes"][hero_id]
        detail = (f"{e['correct']}/{e['trials']} held-out"
                  if e["trials"] else "no held-out trials")
        extra = ""
        if e["contaminated"]:
            extra += f", {e['contaminated']} contaminated (excluded)"
        if e["unknown"]:
            extra += f", {e['unknown']} unknown"
        if e["wrong"]:
            extra += f", {e['wrong']} WRONG"
        lines.append(f"    {e['verdict']:<12} {hero_id:<10} {detail}{extra}")
        if e["verdict"] != VALIDATED:
            lines.append(f"                 -> {e['why']}")
    fm = report["falseMatch"]
    lines.append(f"  false match   : {fm['note']}")
    if report["missingCrops"]:
        lines.append(f"  missing crops : {report['missingCrops']} evidence "
                     f"file(s) referenced but not on disk")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="held-out validation for a hero template set")
    ap.add_argument("--dir", required=True, help="template set directory")
    ap.add_argument("--evidence", required=True, help="evidence manifest json")
    ap.add_argument("--min-trials", type=int, default=MIN_TRIALS)
    ap.add_argument("--min-accuracy", type=float, default=MIN_ACCURACY)
    ap.add_argument("--min-gap", type=float, default=MIN_GAP_SECONDS)
    ap.add_argument("--limit-per-hero", type=int,
                    help="cap trials per hero (faster smoke run)")
    ap.add_argument("--layout", help="layout json whose detector profile "
                    "(portrait ROI / thresholds) validation should use")
    ap.add_argument("--out", help="write the report JSON here")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fail-on-unvalidated", action="store_true",
                    help="exit 1 unless every hero is VALIDATED")
    ap.add_argument("--leave-one-out", action="store_true",
                    help="also run the leave-one-hero-out false-match test")
    args = ap.parse_args(argv)

    manifest = te.load(args.evidence)
    layout = None
    if args.layout:
        import capture
        layout = capture.load_layout(args.layout)
    report = validate(args.dir, manifest, min_trials=args.min_trials,
                      min_accuracy=args.min_accuracy, min_gap=args.min_gap,
                      limit_per_hero=args.limit_per_hero, layout=layout)
    if args.leave_one_out:
        report["leaveOneOut"] = leave_one_out(
            args.dir, manifest, min_gap=args.min_gap, layout=layout)
        report["passed"] = report["passed"] and report["leaveOneOut"]["passed"]
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
            f.write("\n")
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        print(format_report(report))
        if "leaveOneOut" in report:
            print(format_leave_one_out(report["leaveOneOut"]))
    if args.fail_on_unvalidated:
        return 0 if report["counts"][VALIDATED] == len(report["heroes"]) else 1
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
