#!/usr/bin/env python3
"""
template_transfer.py — does a template set work on a broadcast it was not
cut from?

This is the question that decides how much work every new broadcast costs,
and it had never been measured. Everything else in the template toolchain
scores a set against footage from its OWN package: `template_validate`
proves a template recognises frames it has not seen, but they are frames of
the same broadcast, same encoder, same colour grade, same HUD scale. If sets
transfer between packages, hero coverage is CUMULATIVE — process enough VODs
and the library fills up. If they do not, every broadcast starts from zero
and 52/52 is not a number of VODs away, it is a number of VODs times 52.

So this module takes a template set and an evidence manifest from a
DIFFERENT package, and reports three things that answer different questions:

  * **recall at the floor** — how often a foreign template names its hero
    confidently enough to be believed. This is the number that decides
    whether coverage accumulates.
  * **ranking accuracy** — how often the correct hero is ranked FIRST,
    ignoring the floor entirely. A set can be discriminative while scoring
    too low to be trusted, and those are completely different situations
    with completely different fixes.
  * **false-match rate** — of the crops whose hero the set has no template
    for, how many were confidently given a name anyway. This is the safety
    number, and it must survive the move to foreign footage or the set is
    dangerous outside its own broadcast.

### What the first run of this found, stated plainly

`templates/owcs_jksix_qwc` (8 heroes, held-out validated at 99.73% on its
own footage) against `reports/ingest/cr-zeta-ccuf-m1-scan` (a different
event, different channel, different HUD package), each side cropped to its
own portrait region:

    recall at floor 0.60 :    0 of  200      (0.0%)
    ranked first         :  165 of  200     (82.5%)   median score 0.150
    false match          :    0 of 1113      (0.0%,  ceiling 2.0%)

Three findings, and they say different things:

  * The set is **partly discriminative** across broadcasts — shown a hero on
    unfamiliar footage it still ranks that hero first 4 times in 5, against
    a 1-in-8 chance baseline. The template carries real hero identity, not
    memorised pixels.
  * The set is **not confident** across broadcasts — the correlation that
    produces that ranking is ~0.15, and the floor that keeps false matches
    at zero on its own footage is 0.60. Nothing would be published.
  * The set is **safe** across broadcasts — 1,113 portraits of heroes it has
    never seen, and it named none of them. The failure mode is UNKNOWN, not
    a wrong hero, which is the failure mode this project chose.

The conclusion is therefore NOT "lower the floor". A floor low enough to
accept 0.15 is far below the 0.35 default that named 34% of unknown
portraits as heroes when it was last measured. Coverage does not accumulate
by threshold tuning, and this module exists so the next person who wonders
can re-run it in one command instead of re-deriving it.

### The single-ROI detector cannot even see this signal

Run the same pair the way the pipeline runs today — ONE layout's
`portrait_roi` applied to template and probe alike — and ranking collapses
from 82.5% to **0.0%**. The two packages frame their slots differently
(portrait-above-name-strip vs tinted-bar-above-portrait), so one ROI crops
one side to a face and the other to a chin. Any future work on a shared
hero library has to make the ROI a property of the PACKAGE a crop came
from, not of the detector run — that is the concrete blocker, and it is
architectural rather than a matter of tuning.

### Honest limits of the numbers above

They come from ONE pair of packages, and the two overlap on exactly one
hero, so "82.5% ranked first" is 200 crops of Lúcio, not a survey of the
roster. It is enough to rule out "templates transfer as-is", and enough to
suggest a validated foreign set could LABEL a new package's harvest rather
than a human naming clusters by hand — but a labeller that is right 4 times
in 5 needs the same margin discipline everything else here uses, and 82.5%
is not a licence to skip it. Re-run this the moment a third broadcast
exists; the CLI takes any two packages and the answer updates itself.

CLI:
  python3 pipeline/template_transfer.py --dir templates/owcs_jksix_qwc \
      --evidence work/evidence/crzeta.json --layout layouts/owcs_jksix_qwc.json
  python3 pipeline/template_transfer.py --dir ... --evidence ... --json
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
import template_evidence as te  # noqa: E402
import template_validate as tv  # noqa: E402

# Ranking is measured without a floor on purpose, so cap the work: these are
# full template-library comparisons per crop and the manifest can hold
# thousands. Sampled evenly (tv._spread), never "the first N".
DEFAULT_LIMIT_PER_HERO = 200

# Ranking this good is not "no signal" — with 8 heroes in the library, blind
# guessing ranks the right one first 12.5% of the time. A set that manages
# 60%+ on footage it has never seen is carrying real hero identity, even
# when every read is correctly refused for being below the floor. Naming
# that band matters: "does not transfer" and "transfers weakly" lead to
# completely different next steps.
PARTIAL_RANKING = 0.60


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def same_package(templates_dir: str, manifest: dict) -> bool:
    """True when the set and the evidence come from the same broadcast.

    A "transfer" measurement inside one package measures nothing, so callers
    are told rather than quietly given a meaningless 100%.
    """
    layout_id = (manifest.get("layoutId") or "").strip().lower()
    if not layout_id:
        return False
    return os.path.basename(templates_dir.rstrip("/\\")).lower() == layout_id


def measure(templates_dir: str, manifest: dict, *,
            layout: dict | None = None,
            probe_layout: dict | None = None,
            limit_per_hero: int | None = DEFAULT_LIMIT_PER_HERO,
            repo_root: str = db.REPO_ROOT) -> dict:
    """Score `templates_dir` against evidence from another package.

    TWO layouts, and the reason is the whole difficulty of the question.
    Everywhere else in this pipeline one `portrait_roi` is applied to
    template and probe alike, because they come from the same package and
    the slot means the same thing on both. Across packages it does not:
    `owcs_jksix_qwc` frames portrait-above-name-strip (portrait = the top
    71%), `owcs_8c105lnzlam` frames tinted-bar-above-portrait (portrait =
    the bottom 76%). Applying either package's ROI to the other's crops
    compares a face to a chin.

    So each side is cropped to ITS OWN portrait region and the comparison
    happens between the two portraits — `match_slot_ranked` already resizes
    templates to the probe, so differing slot sizes are not an issue. Pass
    only `layout` and both sides use it, which is the honest way to measure
    what today's single-ROI detector would actually do.
    """
    import cv2
    import detect

    profile = detect.detector_profile(layout)
    probe_profile = detect.detector_profile(probe_layout) if probe_layout else profile
    # Templates are cropped by their OWN package's ROI; probes by theirs.
    lib = detect.load_templates(templates_dir, roi=profile["roi"])
    probe_roi = probe_profile["roi"]
    covered = set(lib)

    per_hero: dict[str, dict] = defaultdict(
        lambda: {"trials": 0, "recalled": 0, "rankedFirst": 0,
                 "scores": [], "rankedAs": Counter()})
    foreign = {"trials": 0, "unknown": 0, "matched": 0, "matchedAs": Counter()}
    missing = 0

    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in manifest.get("crops", []):
        if record.get("hero"):
            grouped[record["hero"]].append(record)

    for hero, records in grouped.items():
        for record in tv._spread(records, limit_per_hero):
            path = te.crop_path(manifest, record, repo_root=repo_root)
            gray = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if gray is None:
                missing += 1
                continue
            read = detect.read_slot(gray, lib, floor=profile["floor"],
                                    min_margin=profile["minMargin"],
                                    roi=probe_roi)
            if hero not in covered:
                # The set has no template for this hero. Silence is the only
                # correct answer, and this half of the test is the one that
                # says whether a shared library would be dangerous.
                foreign["trials"] += 1
                if read["hero"] == "UNKNOWN":
                    foreign["unknown"] += 1
                else:
                    foreign["matched"] += 1
                    foreign["matchedAs"][read["hero"]] += 1
                continue

            entry = per_hero[hero]
            entry["trials"] += 1
            if read["hero"] == hero:
                entry["recalled"] += 1
            # Ranking ignores the floor entirely: a set can know exactly what
            # it is looking at and still, correctly, refuse to say so.
            ranked = detect.match_slot_ranked(
                detect.apply_roi(gray, probe_roi), lib)
            if ranked:
                entry["rankedAs"][ranked[0]["hero"]] += 1
                if ranked[0]["hero"] == hero:
                    entry["rankedFirst"] += 1
                by = {r["hero"]: r["score"] for r in ranked}
                entry["scores"].append(round(float(by.get(hero, -1.0)), 4))

    heroes = {}
    for hero, e in sorted(per_hero.items()):
        scores = sorted(e["scores"])
        heroes[hero] = {
            "heroId": hero,
            "trials": e["trials"],
            "recalled": e["recalled"],
            "recallRate": (round(e["recalled"] / e["trials"], 4)
                           if e["trials"] else None),
            "rankedFirst": e["rankedFirst"],
            "rankingRate": (round(e["rankedFirst"] / e["trials"], 4)
                            if e["trials"] else None),
            "medianScore": (scores[len(scores) // 2] if scores else None),
            "rankedAs": dict(e["rankedAs"].most_common(5)),
        }

    trials = sum(h["trials"] for h in heroes.values())
    recalled = sum(h["recalled"] for h in heroes.values())
    ranked_first = sum(h["rankedFirst"] for h in heroes.values())
    fm_rate = (foreign["matched"] / foreign["trials"]
               if foreign["trials"] else None)

    return {
        "generatedAt": _utcnow_iso(),
        "templatesDir": (tv.site_paths.site_relpath(templates_dir, repo_root)
                         if os.path.isabs(templates_dir) else templates_dir),
        "evidence": {
            "sourceReport": manifest.get("sourceReport"),
            "layoutId": manifest.get("layoutId"),
            "labelPolicy": (manifest.get("labelPolicy") or {}).get("method"),
        },
        "samePackage": same_package(templates_dir, manifest),
        "detector": {
            "floor": profile["floor"], "minMargin": profile["minMargin"],
            "templateRoi": list(profile["roi"]) if profile["roi"] else None,
            "probeRoi": list(probe_roi) if probe_roi else None,
        },
        "overlapHeroes": sorted(heroes),
        "totals": {
            "trials": trials,
            "recalled": recalled,
            "recallRate": round(recalled / trials, 4) if trials else None,
            "rankedFirst": ranked_first,
            "rankingRate": round(ranked_first / trials, 4) if trials else None,
        },
        "falseMatch": {
            "trials": foreign["trials"],
            "unknown": foreign["unknown"],
            "matched": foreign["matched"],
            "rate": round(fm_rate, 4) if fm_rate is not None else None,
            "ceiling": tv.MAX_FALSE_MATCH_RATE,
            "matchedAs": dict(foreign["matchedAs"].most_common(8)),
        },
        "missingCrops": missing,
        "heroes": heroes,
        "verdict": verdict(trials, recalled, ranked_first, fm_rate),
    }


def verdict(trials: int, recalled: int, ranked_first: int,
            false_match_rate: float | None) -> dict:
    """The three-way answer, kept apart on purpose.

    Collapsing "does it work" into one word is what let the original
    coverage number mean four different things at once.
    """
    if not trials:
        return {"transfers": None, "discriminates": None, "safe": None,
                "summary": ("no overlapping heroes — this set has no template "
                            "for any hero the other broadcast contained, so "
                            "transfer cannot be measured from this pair")}
    recall = recalled / trials
    ranking = ranked_first / trials
    safe = (false_match_rate is not None
            and false_match_rate <= tv.MAX_FALSE_MATCH_RATE)
    if recall >= tv.MIN_ACCURACY:
        summary = (f"templates transfer: {recall:.1%} of foreign crops were "
                   f"named correctly at the layout's own floor")
    elif ranking >= tv.MIN_ACCURACY:
        summary = (f"templates DISCRIMINATE but do not TRANSFER: the correct "
                   f"hero ranks first {ranking:.1%} of the time, yet only "
                   f"{recall:.1%} clear the floor. Good enough to LABEL a new "
                   f"package's harvest, not good enough to publish from — and "
                   f"not fixable by lowering the floor, which is what keeps "
                   f"unknown heroes unnamed")
    elif ranking >= PARTIAL_RANKING:
        summary = (f"PARTIAL signal: {ranking:.1%} ranked first — well above "
                   f"chance, so the templates do carry hero identity across "
                   f"broadcasts, but {recall:.1%} clear the floor and a "
                   f"labeller wrong one time in "
                   f"{max(2, round(1 / max(1e-9, 1 - ranking)))} still needs "
                   f"the margin discipline used everywhere else here")
    else:
        summary = (f"templates do not transfer: {recall:.1%} recalled, "
                   f"{ranking:.1%} ranked first — this footage is different "
                   f"enough that the set carries no usable signal")
    return {
        "transfers": recall >= tv.MIN_ACCURACY,
        "discriminates": ranking >= tv.MIN_ACCURACY,
        "partialSignal": PARTIAL_RANKING <= ranking < tv.MIN_ACCURACY,
        "safe": safe,
        "summary": summary,
    }


def format_report(report: dict) -> str:
    t, fm, v = report["totals"], report["falseMatch"], report["verdict"]
    lines = [
        f"  templates     : {report['templatesDir']}",
        f"  evidence      : {report['evidence']['sourceReport']} "
        f"(layout {report['evidence']['layoutId']})",
        f"  detector      : floor {report['detector']['floor']}, "
        f"margin {report['detector']['minMargin']}",
        f"  template roi  : {report['detector']['templateRoi']}",
        f"  probe roi     : {report['detector']['probeRoi']}",
    ]
    if report["samePackage"]:
        lines.append("  WARNING       : the set and the evidence are the SAME "
                     "package — this measures nothing about transfer")
    if not report["overlapHeroes"]:
        lines.append("  overlap       : none")
    else:
        lines.append(f"  overlap       : {', '.join(report['overlapHeroes'])}")
        lines.append(f"  recall@floor  : {t['recalled']}/{t['trials']} "
                     f"({(t['recallRate'] or 0):.1%})")
        lines.append(f"  ranked first  : {t['rankedFirst']}/{t['trials']} "
                     f"({(t['rankingRate'] or 0):.1%})  (floor ignored)")
        for hero, h in report["heroes"].items():
            lines.append(f"      {hero:10s} recall {(h['recallRate'] or 0):6.1%}  "
                         f"ranked {(h['rankingRate'] or 0):6.1%}  "
                         f"median score {h['medianScore']}")
    if fm["trials"]:
        lines.append(f"  false match   : {fm['matched']}/{fm['trials']} "
                     f"({(fm['rate'] or 0):.1%}, ceiling {fm['ceiling']:.1%}) "
                     f"— crops of heroes this set has no template for")
        if fm["matchedAs"]:
            lines.append(f"                  named as: {fm['matchedAs']}")
    if report["missingCrops"]:
        lines.append(f"  missing crops : {report['missingCrops']}")
    lines.append(f"  verdict       : {v['summary']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="does a template set work on a broadcast it was not cut from?")
    ap.add_argument("--dir", required=True, help="template set directory")
    ap.add_argument("--evidence", required=True,
                    help="evidence manifest from ANOTHER package")
    ap.add_argument("--layout", help="layout json of the TEMPLATE package "
                                     "(its roi/floor/margin)")
    ap.add_argument("--probe-layout",
                    help="layout json of the EVIDENCE package. Its portrait "
                         "ROI crops the probes, so each side is compared on "
                         "its own portrait region. Omit to apply --layout to "
                         "both, which is what today's detector does.")
    ap.add_argument("--limit-per-hero", type=int, default=DEFAULT_LIMIT_PER_HERO)
    ap.add_argument("--out", help="write the report JSON here")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    manifest = te.load(args.evidence)
    def _load(path):
        if not path:
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    report = measure(args.dir, manifest, layout=_load(args.layout),
                     probe_layout=_load(args.probe_layout),
                     limit_per_hero=args.limit_per_hero)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(report, f, indent=1)
            f.write("\n")
        print(f"[transfer] wrote {args.out}")
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
