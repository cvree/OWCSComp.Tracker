#!/usr/bin/env python3
"""
template_forge.py — build a hero template set that can be *proven*, from
real footage, without a human naming a single file.

This is the piece that was missing. The repository already had:

  * `harvest_templates.py` — cut crops from a clip and cluster them,
  * `template_bootstrap.py` — measure coverage and propose labels,
  * `template_quality.py`  — judge whether a crop is fit to be a template,
  * `template_evidence.py` — assemble labeled crops from a real ingest run,
  * `template_validate.py` — score a set on frames it has never seen.

What it did not have was the thing that runs them in the one order that
produces a trustworthy result. Doing it by hand invites the mistake that
makes the whole exercise worthless: building a template from a frame and
then congratulating it for matching that frame.

The forge enforces the split structurally:

    build slice  = the earliest window of a hero's evidence
    dead zone    = `--holdout-gap` seconds, belonging to neither
    holdout      = everything after it, never seen during construction

Templates are cut ONLY from the build slice, every offset is written into
`_provenance.json`, and `template_validate.py` independently re-derives the
separation from that provenance. Validation does not take the forge's word
for which frames were used — it reads the same file an auditor would.

### The gate, and where rejected crops go

Every candidate goes through `template_quality.assess_crop` before it can
become a template:

  * REJECT  → `_rejected/`, with the failing checks written alongside. These
              are the flat fades, the motion blurs, the killfeed-obstructed
              crops. Silently dropping them would hide a harvest that is
              going wrong.
  * REVIEW  → `_review/`, the graphical review inbox. Nothing marginal
              enters production on the forge's own authority.
  * ACCEPT  → a template, with provenance.

And after all that, a hero whose templates do not survive held-out
validation is **not promoted**. `--promote` copies only the heroes that
earned it; the rest stay in staging with their report attached. A partially
promoted set is the honest outcome of a partially successful harvest.

CLI:
  # forge into staging and validate, writing nothing to production
  python3 pipeline/template_forge.py --evidence work/evidence/nepal.json \
      --out work/templates/owcs_jksix_qwc

  # promote only the heroes that validated
  python3 pipeline/template_forge.py --evidence work/evidence/nepal.json \
      --out work/templates/owcs_jksix_qwc \
      --promote-to templates/owcs_jksix_qwc
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import template_bootstrap as tb  # noqa: E402
import template_evidence as te  # noqa: E402
import template_quality as tq  # noqa: E402
import template_validate as tv  # noqa: E402

# Fraction of a hero's evidence timeline that may be used to build from. The
# rest is holdout. A third is enough to see a hero's states while leaving a
# substantial, genuinely unseen majority to be judged on.
DEFAULT_BUILD_FRACTION = 0.34
# Seconds between the last build frame and the first holdout frame. Adjacent
# broadcast samples are near-identical, so without a dead zone "held out"
# would be a technicality.
DEFAULT_HOLDOUT_GAP = 60.0
# Templates per hero. More variants cover more states (alive/dead/ult/tinted)
# but every one costs a correlation on every slot of every frame.
DEFAULT_VARIANTS = 4

REVIEW_DIRNAME = "_review"
REJECTED_DIRNAME = "_rejected"
REVIEW_INBOX = "inbox.json"


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def log(msg: str) -> None:
    print(f"[forge] {msg}", flush=True)


# ------------------------------------------------------------------- split
def split_by_time(records: list[dict], *, build_fraction: float,
                  holdout_gap: float) -> tuple[list[dict], list[dict], dict]:
    """(build, holdout, description) — a hard temporal split with a dead zone.

    The split is taken **per stint**, not once over the hero's whole
    timeline, and that distinction is worth the extra bookkeeping.

    A hero that holds one slot for the whole map is served fine either way.
    A hero that comes and goes is not: Lúcio in the committed Nepal footage
    appears in one slot for the whole map and swaps in and out of another,
    and a single global window put every Lúcio template inside the first
    appearance. The later ones looked different enough (different player,
    different team tint, different HUD state) that 9% of held-out Lúcio
    frames came back UNKNOWN — no wrong answers, but a real hole, and one
    that a global window will produce for any hero whose appearances are
    spread out.

    Taking the earliest slice of EACH stint samples the states the hero was
    actually seen in, while keeping the guarantee that matters: a crop is
    only scored if it is at least `holdout_gap` from **every** frame a
    template was cut from. Validation re-derives that from the written
    provenance rather than trusting this function, so a bug here shows up
    as a contaminated-crop count, not as a silently inflated score.
    """
    ordered = sorted(records, key=lambda r: float(r["t"]))
    if not ordered:
        return [], [], {"reason": "no evidence for this hero"}

    by_stint: dict[str, list[dict]] = defaultdict(list)
    for record in ordered:
        by_stint[record.get("stintId") or "_"].append(record)

    build: list[dict] = []
    windows: list[list[float]] = []
    for stint_id in sorted(by_stint):
        group = by_stint[stint_id]
        g0, g1 = float(group[0]["t"]), float(group[-1]["t"])
        end = g0 + (g1 - g0) * build_fraction
        chosen = [r for r in group if float(r["t"]) <= end]
        if not chosen:
            chosen = group[:1]
        build.extend(chosen)
        windows.append([g0, round(float(chosen[-1]["t"]), 1)])

    build_ts = [float(r["t"]) for r in build]
    holdout = [r for r in ordered
               if all(abs(float(r["t"]) - bt) >= holdout_gap
                      for bt in build_ts)]
    return build, holdout, {
        "tFirst": float(ordered[0]["t"]), "tLast": float(ordered[-1]["t"]),
        "buildWindows": windows,
        "buildEnds": max((w[1] for w in windows), default=0.0),
        "holdoutStarts": (round(min((float(r["t"]) for r in holdout)), 1)
                          if holdout else None),
        "buildCrops": len(build), "holdoutCrops": len(holdout),
        "stints": len(by_stint),
        "deadZoneSeconds": holdout_gap,
    }


# ------------------------------------------------------------------ picking
def pick_variants(candidates: list[tuple[dict, "object"]], n: int) -> list[int]:
    """Indices of up to n maximally-different, sharpest-first candidates.

    Same greedy min-correlation idea as `harvest_templates.pick_variants`,
    but operating on in-memory crops that have ALREADY passed the quality
    gate. The ordering matters: the old code picked maximally-different crops
    first and never checked quality, which is precisely how a fade-to-black
    frame — maximally different from a portrait by construction — ended up
    committed as `mauga.v1.png`, a 35x35 block of solid grey.
    """
    if not candidates:
        return []
    order = sorted(range(len(candidates)),
                   key=lambda i: -tq.sharpness(candidates[i][1]))
    chosen = [order[0]]
    while len(chosen) < n and len(chosen) < len(order):
        best, best_sim = None, 2.0
        for i in order:
            if i in chosen:
                continue
            sim = max(tq.correlate(candidates[c][1], candidates[i][1])
                      for c in chosen)
            if sim < best_sim:
                best_sim, best = sim, i
        if best is None or best_sim >= tq.DUPLICATE_CEIL:
            break
        chosen.append(best)
    return chosen


# -------------------------------------------------------------------- forge
def forge(manifest: dict, out_dir: str, *,
          build_fraction: float = DEFAULT_BUILD_FRACTION,
          holdout_gap: float = DEFAULT_HOLDOUT_GAP,
          variants: int = DEFAULT_VARIANTS,
          heroes: list[str] | None = None,
          layout: dict | None = None,
          repo_root: str = db.REPO_ROOT) -> dict:
    """Build a staging template set from an evidence manifest.

    `layout` supplies the detector profile. Templates are always WRITTEN as
    the full slot crop — that is the raw evidence, and narrowing it on disk
    would make the file unauditable against the frame it came from — but
    they are *judged* through the layout's `portrait_roi`, because that is
    the pixels detection will actually compare.
    """
    import cv2
    import detect

    roi = detect.portrait_roi(layout)

    def judged(gray):
        """The part of a crop the detector will actually see."""
        return detect.apply_roi(gray, roi)

    os.makedirs(out_dir, exist_ok=True)
    review_dir = os.path.join(out_dir, REVIEW_DIRNAME)
    rejected_dir = os.path.join(out_dir, REJECTED_DIRNAME)
    for d in (review_dir, rejected_dir):
        os.makedirs(d, exist_ok=True)

    by_hero: dict[str, list[dict]] = defaultdict(list)
    for record in manifest.get("crops", []):
        if record.get("hero"):
            by_hero[record["hero"]].append(record)
    if heroes:
        by_hero = {h: v for h, v in by_hero.items() if h in heroes}

    accepted_images: dict[str, "object"] = {}     # filename -> gray
    provenance_entries: list[dict] = []
    review_items: list[dict] = []
    per_hero: dict[str, dict] = {}

    source_report = manifest.get("sourceReport")
    source_video = manifest.get("sourceVideo")

    for hero_id in sorted(by_hero):
        build, holdout, split = split_by_time(
            by_hero[hero_id], build_fraction=build_fraction,
            holdout_gap=holdout_gap)
        info = {"heroId": hero_id, "split": split, "written": [],
                "rejected": [], "review": [], "skipped": None}
        per_hero[hero_id] = info

        if not build:
            info["skipped"] = "no crops in the build window"
            continue
        if not holdout:
            info["skipped"] = (
                f"every crop for {hero_id} falls inside the build window — "
                f"a template built here could never be validated on unseen "
                f"footage, so none is written")
            continue

        # ---- load + gate every build candidate
        graded: list[tuple[dict, object, dict]] = []
        for record in build:
            path = te.crop_path(manifest, record, repo_root=repo_root)
            # Read in COLOUR and keep it. Matching is grayscale, but a
            # template written as grayscale is permanently colour-blind: a
            # human reviewing it loses the strongest cue they have, and a
            # colour-based guard (see detect.chroma_centroid) becomes
            # impossible after the fact. Store the evidence, match on the
            # luminance of it.
            colour = cv2.imread(path, cv2.IMREAD_COLOR)
            if colour is None:
                continue
            gray = cv2.cvtColor(colour, cv2.COLOR_BGR2GRAY)
            verdict = tq.assess_crop(
                judged(gray), hero_id=hero_id,
                same_hero={}, other_heroes=dict(accepted_images),
                provenance={"sourceVideo": source_video or source_report,
                            "offset": record["t"]})
            graded.append((record, colour, verdict))

        usable = [(r, g) for (r, g, v) in graded
                  if v["verdict"] == tq.ACCEPT]
        marginal = [(r, g, v) for (r, g, v) in graded
                    if v["verdict"] == tq.REVIEW]
        rejected = [(r, g, v) for (r, g, v) in graded
                    if v["verdict"] == tq.REJECT]

        for record, colour, verdict in rejected[:12]:
            name = f"{hero_id}_{record['file']}"
            cv2.imwrite(os.path.join(rejected_dir, name), colour)
            info["rejected"].append({"file": name, "reasons": verdict["reasons"],
                                     "t": record["t"], "slot": record["slot"]})

        if not usable:
            # Nothing clean enough. The marginal crops are the review inbox's
            # problem now; the forge writes no template for this hero.
            info["skipped"] = (
                f"no build-window crop passed the quality gate "
                f"({len(marginal)} marginal, {len(rejected)} rejected)")
            for record, colour, verdict in marginal[:8]:
                name = f"{hero_id}_{record['file']}"
                cv2.imwrite(os.path.join(review_dir, name), colour)
                item = {"file": name, "heroId": hero_id, "t": record["t"],
                        "slot": record["slot"], "reasons": verdict["reasons"],
                        "metrics": verdict["metrics"],
                        "sourceReport": source_report,
                        "why": (f"the only candidates for {hero_id} were "
                                f"marginal; approving one would let it "
                                f"become a production template")}
                review_items.append(item)
                info["review"].append(item)
            continue

        chosen = pick_variants(
            [(r, judged(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)))
             for (r, c) in usable], variants)
        for rank, idx in enumerate(chosen):
            record, colour = usable[idx]
            gray = cv2.cvtColor(colour, cv2.COLOR_BGR2GRAY)
            suffix = "" if rank == 0 else f".v{rank}"
            fname = f"{hero_id}{suffix}.png"
            # Final cross-hero check against everything written so far. The
            # per-candidate gate saw the set as it was when the candidate was
            # graded; this sees it as it will actually ship.
            confusion = tq.assess_crop(
                judged(gray), hero_id=hero_id,
                same_hero={k: v for k, v in accepted_images.items()
                           if tq.hero_of(k) == hero_id},
                other_heroes={k: v for k, v in accepted_images.items()
                              if tq.hero_of(k) != hero_id},
                provenance={"sourceVideo": source_video or source_report,
                            "offset": record["t"]})
            if confusion["verdict"] == tq.REJECT:
                cv2.imwrite(os.path.join(rejected_dir,
                                         f"{hero_id}_{record['file']}"),
                            colour)
                info["rejected"].append({
                    "file": f"{hero_id}_{record['file']}",
                    "reasons": confusion["reasons"],
                    "t": record["t"], "slot": record["slot"]})
                continue
            cv2.imwrite(os.path.join(out_dir, fname), colour)
            accepted_images[fname] = judged(gray)
            info["written"].append(fname)
            provenance_entries.append({
                "file": fname,
                "heroId": hero_id,
                "sourceVideo": source_video,
                "sourceReport": source_report,
                "sourceCrop": record["file"],
                "offset": record["t"],
                "slot": record["slot"],
                "state": record.get("state"),
                "labelSource": record.get("labelSource"),
                "stintId": record.get("stintId"),
                "quality": confusion["metrics"],
                "buildWindows": split.get("buildWindows"),
                "holdoutStarts": split["holdoutStarts"],
            })

    if provenance_entries:
        tb.write_provenance(
            out_dir, provenance_entries,
            source_video=source_video, source_clip=source_report,
            labeled_by=f"template_forge (label source: "
                       f"{manifest.get('labelPolicy', {}).get('method')})",
            layout_id=manifest.get("layoutId"))

    inbox = {
        "generatedAt": _utcnow_iso(),
        "sourceReport": source_report,
        "layoutId": manifest.get("layoutId"),
        "items": review_items,
        "note": ("Candidates the quality gate could neither accept nor "
                 "reject. Nothing here is a template; approving one is a "
                 "human decision, and the crop is attached so it can be "
                 "made by looking rather than by trusting a number."),
    }
    with open(os.path.join(review_dir, REVIEW_INBOX), "w",
              encoding="utf-8") as f:
        json.dump(inbox, f, indent=1)
        f.write("\n")

    return {
        "generatedAt": _utcnow_iso(),
        "outDir": out_dir,
        "layoutId": manifest.get("layoutId"),
        "sourceReport": source_report,
        "settings": {"buildFraction": build_fraction,
                     "holdoutGap": holdout_gap, "variants": variants},
        "heroes": per_hero,
        "templatesWritten": len(provenance_entries),
        "heroesWritten": sorted(h for h, i in per_hero.items() if i["written"]),
        "heroesSkipped": {h: i["skipped"] for h, i in per_hero.items()
                          if i["skipped"]},
        "reviewInbox": len(review_items),
    }


# ---------------------------------------------------------------- promotion
def promote(staging_dir: str, production_dir: str, validation: dict, *,
            only_validated: bool = True, dry_run: bool = False) -> dict:
    """Copy heroes that earned it from staging into a production set.

    A hero is promoted as a unit — all of its templates or none. Promoting
    half a hero's variants would ship a set nobody could reason about.
    Anything already in production for a promoted hero is REPLACED, and the
    displaced files are kept under `_superseded/` so a promotion can be
    reasoned about (and undone) after the fact.
    """
    verdicts = {h: e["verdict"] for h, e in validation["heroes"].items()}
    eligible = [h for h, v in verdicts.items()
                if v == tv.VALIDATED or not only_validated]
    refused = {h: v for h, v in verdicts.items() if h not in eligible}

    copied: list[str] = []
    superseded: list[str] = []
    if not dry_run:
        os.makedirs(production_dir, exist_ok=True)
        sup_dir = os.path.join(production_dir, "_superseded")
    for hero_id in sorted(eligible):
        staged = [f for f in sorted(os.listdir(staging_dir))
                  if f.endswith(".png") and tq.hero_of(f) == hero_id]
        if not staged:
            continue
        old = [f for f in sorted(os.listdir(production_dir))
               if f.endswith(".png") and tq.hero_of(f) == hero_id] \
            if os.path.isdir(production_dir) else []
        if dry_run:
            copied.extend(staged)
            superseded.extend(old)
            continue
        for f in old:
            os.makedirs(sup_dir, exist_ok=True)
            shutil.move(os.path.join(production_dir, f),
                        os.path.join(sup_dir, f))
            superseded.append(f)
        for f in staged:
            shutil.copy2(os.path.join(staging_dir, f),
                         os.path.join(production_dir, f))
            copied.append(f)

    # Carry provenance for exactly the promoted files, so production never
    # claims an origin for a template it did not receive.
    staged_prov = tb.load_provenance(staging_dir) or {"entries": []}
    promoted_entries = [e for e in staged_prov.get("entries", [])
                        if e.get("file") in set(copied)]
    if promoted_entries and not dry_run:
        tb.write_provenance(
            production_dir, promoted_entries,
            source_video=staged_prov.get("harvests", [{}])[-1].get("sourceVideo")
            if staged_prov.get("harvests") else None,
            source_clip=validation["evidence"].get("sourceReport"),
            labeled_by="template_forge --promote (held-out validated)",
            layout_id=validation["evidence"].get("layoutId"))

    return {
        "promoted": sorted(copied),
        "promotedHeroes": sorted(eligible),
        "superseded": sorted(superseded),
        "refused": refused,
        "dryRun": dry_run,
        "note": ("Only heroes whose templates passed held-out validation are "
                 "promoted. A refused hero keeps whatever production already "
                 "had — a failed harvest never degrades a working set."),
    }


def format_forge(report: dict) -> str:
    lines = [
        f"  staging       : {report['outDir']}",
        f"  from          : {report['sourceReport']} "
        f"(layout {report['layoutId']})",
        f"  wrote         : {report['templatesWritten']} template(s) for "
        f"{len(report['heroesWritten'])} hero(es)",
    ]
    for hero_id in sorted(report["heroes"]):
        info = report["heroes"][hero_id]
        s = info["split"]
        if info["skipped"]:
            lines.append(f"    SKIP  {hero_id:<10} {info['skipped']}")
            continue
        lines.append(
            f"    build {hero_id:<10} {len(info['written'])} template(s) from "
            f"{s.get('stints', 1)} stint window(s) up to t{s['buildEnds']:.0f}s"
            f"; holdout "
            + (f"from t{s['holdoutStarts']:.0f}s " if s.get("holdoutStarts")
               else "")
            + f"({s['holdoutCrops']} crops)"
            + (f"; {len(info['rejected'])} rejected" if info["rejected"] else "")
            + (f"; {len(info['review'])} to review" if info["review"] else ""))
    if report["reviewInbox"]:
        lines.append(f"  review inbox  : {report['reviewInbox']} candidate(s) "
                     f"need a human decision")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="forge a provable hero template set from real evidence")
    ap.add_argument("--evidence", required=True, help="evidence manifest json")
    ap.add_argument("--out", required=True, help="staging template dir")
    ap.add_argument("--build-fraction", type=float,
                    default=DEFAULT_BUILD_FRACTION)
    ap.add_argument("--holdout-gap", type=float, default=DEFAULT_HOLDOUT_GAP)
    ap.add_argument("--variants", type=int, default=DEFAULT_VARIANTS)
    ap.add_argument("--heroes", help="comma list; default every labeled hero")
    ap.add_argument("--layout", help="layout json supplying the detector "
                    "profile (portrait ROI / thresholds) to build and "
                    "validate against")
    ap.add_argument("--promote-to", help="production dir to promote into")
    ap.add_argument("--promote-dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    manifest = te.load(args.evidence)
    layout = None
    if args.layout:
        import capture
        layout = capture.load_layout(args.layout)
    report = forge(manifest, args.out,
                   build_fraction=args.build_fraction,
                   holdout_gap=args.holdout_gap,
                   variants=args.variants,
                   layout=layout,
                   heroes=(args.heroes.split(",") if args.heroes else None))
    if not report["templatesWritten"]:
        print(format_forge(report))
        log("no templates written — nothing to validate")
        return 1

    validation = tv.validate(args.out, manifest, min_gap=args.holdout_gap,
                             layout=layout)
    report["validation"] = validation

    if args.promote_to:
        report["promotion"] = promote(args.out, args.promote_to, validation,
                                      dry_run=args.promote_dry_run)

    if args.json:
        print(json.dumps(report, indent=1, default=str))
    else:
        print(format_forge(report))
        print(tv.format_report(validation))
        if "promotion" in report:
            p = report["promotion"]
            print(f"  promotion     : {len(p['promoted'])} file(s) for "
                  f"{len(p['promotedHeroes'])} hero(es)"
                  + (" (dry run)" if p["dryRun"] else ""))
            for hero_id, verdict in sorted(p["refused"].items()):
                print(f"    refused {hero_id}: {verdict}")
    return 0 if validation["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
