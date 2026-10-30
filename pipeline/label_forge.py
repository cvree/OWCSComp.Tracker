#!/usr/bin/env python3
"""
label_forge.py — from a person's card labels to promoted hero templates.

The second half of the labelling loop (`label_batch.py` is the first). A
person names the cards of a batch on `label.html`; the page exports
`labelling/<batch>/labels.json`. This module turns that into templates
through the forge the repository already trusts, adding nothing to its
rules:

    labels.json ──► evidence manifest  (every member crop of a named card,
                    │                    labelSource "human-cluster")
                    ▼
    template_forge.forge     build slice → quality gate → dead zone
    template_validate.validate   held-out frames, leave-one-hero-out
    template_forge.promote   ONLY heroes that VALIDATED reach production

### What "human-cluster" means, stated plainly

A person looked at a card — its prototype and samples spread across its
time span — and named it. They did not look at every crop in it. The label
therefore rests on two things: the person's eye, and the card's purity
(99.3 % on the Nepal crops, against the detector's own readings). Every
crop records `labelSource: "human-cluster"` and its `clusterId`, so a
validation report can never present it as per-crop human ground truth.
Held-out validation is what catches a card that was impure: a template cut
from a mixed card does not recognise its hero on frames it never saw.

### What it refuses

  * a hero id that is not on the roster — a typo must not become a hero;
  * a label for a card the batch does not have;
  * a batch whose member crops are not on disk (on a runner they arrive as
    the harvest's workflow artifact) — it says where they should be.

CLI:
  python3 pipeline/label_forge.py --batch <id>              # forge + validate
  python3 pipeline/label_forge.py --batch <id> --promote    # and promote
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import label_batch as lb  # noqa: E402

LABELS_SCHEMA = "label-labels.v1"
LABEL_SOURCE = "human-cluster"
SKIP = "skip"
STAGING_ROOT = os.path.join(db.REPO_ROOT, "work", "label-forge")
# Bumped when the forge's rules change in a way that should re-forge every
# batch already forged under the old ones. 2: clean, tune, safety-checked
# set. 3: the set may hold no template that looks like another hero.
# 4: role lock — each slot reads only heroes of its role; off-role crops
# leave their card; a person double-checks what lands at or below 90%.
FORGE_VERSION = 4


class ForgeRefusal(Exception):
    """A reason this batch cannot be forged, phrased for the person."""


def log(msg: str) -> None:
    print(f"[label-forge] {msg}", flush=True)


def roster_ids(con=None) -> set[str]:
    own = con is None
    con = con or db.connect()
    try:
        return {r[0] for r in con.execute("SELECT id FROM heroes")}
    finally:
        if own:
            con.close()


def resolve_labels(batch: dict, labels: dict, roster: set[str]) -> dict[str, str]:
    """{cardId: heroId} for every card a person named, after checking it.

    A card left unnamed but `nestedIn` a named card is NOT filled in here:
    the page offers that link, and the person either accepted it (so it is
    in labels.json) or did not."""
    if labels.get("batchId") not in (None, batch["batchId"]):
        raise ForgeRefusal(
            f"labels.json is for batch {labels.get('batchId')!r}, not "
            f"{batch['batchId']!r}")
    cards = {c["id"] for c in batch.get("cards") or []}
    named: dict[str, str] = {}
    problems = []
    for card, hero in sorted((labels.get("labels") or {}).items()):
        if card not in cards:
            problems.append(f"{card}: no such card in this batch")
        elif not hero or hero == SKIP:
            continue
        elif hero not in roster:
            problems.append(f"{card}: {hero!r} is not a hero id on the roster")
        else:
            named[card] = hero
    if problems:
        raise ForgeRefusal("; ".join(problems))
    if not named:
        raise ForgeRefusal("no card is named yet — nothing to forge")
    return named


def build_manifest(batch: dict, named: dict[str, str], *,
                   repo_root: str = db.REPO_ROOT) -> dict:
    """The evidence manifest the forge and the validator already read."""
    crops = []
    missing = 0
    for card in batch.get("cards") or []:
        hero = named.get(card["id"])
        if not hero:
            continue
        for fname, t, slot in card.get("members") or []:
            if not os.path.exists(os.path.join(repo_root, batch["cropsDir"], fname)):
                missing += 1
                continue
            crops.append({"file": fname, "slot": slot, "side": slot[0],
                          "t": float(t), "state": "gameplay", "hero": hero,
                          "labelSource": LABEL_SOURCE,
                          "clusterId": card["id"]})
    if not crops:
        art = batch.get("cropsArtifact") or {}
        raise ForgeRefusal(
            f"none of the named cards' member crops are on disk under "
            f"{batch['cropsDir']}"
            + (f" — download workflow artifact {art.get('name')!r} from run "
               f"{art.get('runId')} there first" if art else
               " — re-run label_batch.py for this broadcast to regenerate them"))
    by_hero: dict[str, int] = {}
    for c in crops:
        by_hero[c["hero"]] = by_hero.get(c["hero"], 0) + 1
    src = batch.get("source") or {}
    return {
        "evidenceSetVersion": 1,
        "sourceReport": f"labelling/{batch['batchId']}",
        "sourceVideo": src.get("url") or src.get("videoId"),
        "cropsDir": batch["cropsDir"],
        "layoutId": batch.get("layoutId"),
        "labelPolicy": {
            "method": "a person named each card on label.html",
            "labelSource": LABEL_SOURCE,
            "honesty": ("A person named a card from its prototype and "
                        "time-spread samples, not every crop in it. Held-out "
                        "validation, not this label, decides promotion."),
        },
        "counts": {"labeled": len(crops), "excluded": missing,
                   "heroes": len(by_hero), "byHero": dict(sorted(by_hero.items())),
                   "byState": {"gameplay": len(crops)},
                   "byLabelSource": {LABEL_SOURCE: len(crops)}},
        "stints": [],
        "crops": crops,
        "excluded": [],
    }


# ------------------------------------------------------------ card cleaning
# A member must correlate at least this well with its own card's centre to
# be used. Cards are 99% pure on average, but the 1% is not spread evenly:
# on the first live Twitch batch four named cards carried a stray portrait
# of another hero, and a template cut from that stray teaches the wrong face.
CARD_KEEP = 0.45


def clean_cards(manifest: dict, layout: dict, *,
                repo_root: str = db.REPO_ROOT) -> tuple[dict, dict]:
    """Drop members that do not look like the card they were named with.

    The centre is the mean of the card's normalised members, so one stray
    cannot move it far; a member below CARD_KEEP against it is set aside
    with its card id, never relabelled. Returns (manifest, report)."""
    import cv2

    by_card: dict[str, list[int]] = {}
    for i, c in enumerate(manifest["crops"]):
        by_card.setdefault(c["clusterId"], []).append(i)
    imgs = {}
    for i, c in enumerate(manifest["crops"]):
        img = cv2.imread(os.path.join(repo_root, manifest["cropsDir"], c["file"]),
                         cv2.IMREAD_COLOR)
        if img is not None:
            imgs[i] = img
    roi = layout.get("portrait_roi")
    dropped: dict[str, int] = {}
    keep: set[int] = set()
    # Role lock first: the broadcast prints each role in fixed slots, so a
    # crop from a slot of another role is a stray whatever it looks like.
    import detect
    roles = detect.slot_roles(layout)
    off_role: set[int] = set()
    if roles:
        hero_role = detect.hero_roles()
        for i, c in enumerate(manifest["crops"]):
            idx_slot = int(str(c.get("slot", "0"))[-1] or 0)
            if 1 <= idx_slot <= 5 and hero_role.get(c["hero"]) not in (None, roles[idx_slot - 1]):
                off_role.add(i)
                dropped[c["clusterId"]] = dropped.get(c["clusterId"], 0) + 1
    for card, idx in by_card.items():
        idx = [i for i in idx if i in imgs and i not in off_role]
        if len(idx) < 3:
            keep.update(idx)
            continue
        feats = lb.features([{"img": imgs[i]} for i in idx], roi)
        centre = feats.mean(axis=0)
        norm = float((centre ** 2).sum() ** 0.5) or 1.0
        sims = feats @ (centre / norm)
        for i, s in zip(idx, sims):
            if s >= CARD_KEEP:
                keep.add(i)
            else:
                dropped[card] = dropped.get(card, 0) + 1
    out = dict(manifest)
    out["crops"] = [c for i, c in enumerate(manifest["crops"]) if i in keep]
    out["excluded"] = [dict(c, excludedBecause=(
                           "sits in a slot of another role" if i in off_role else
                           "does not look like the rest of its card"))
                       for i, c in enumerate(manifest["crops"]) if i not in keep]
    by_hero: dict[str, int] = {}
    for c in out["crops"]:
        by_hero[c["hero"]] = by_hero.get(c["hero"], 0) + 1
    out["counts"] = dict(manifest["counts"], labeled=len(out["crops"]),
                         excluded=manifest["counts"].get("excluded", 0) + len(out["excluded"]),
                         heroes=len(by_hero), byHero=dict(sorted(by_hero.items())),
                         byLabelSource={LABEL_SOURCE: len(out["crops"])})
    return out, {"threshold": CARD_KEEP, "dropped": dict(sorted(dropped.items())),
                 "droppedTotal": sum(dropped.values()), "offRole": len(off_role),
                 "kept": len(out["crops"])}


# ------------------------------------------------------------ safety tuning
# The detector reads a slot as a hero only when its best match clears
# `unknown_floor` and beats the runner-up by `min_margin`. A layout that
# never measured its own sets the module defaults (0.35 / 0.12), which on
# the live Twitch package read 78% of Vendetta portraits as Ramattra. These
# are the values tried; only ones at least as strict as the layout's
# current setting are ever considered, so tuning can raise the bar and can
# never lower it.
FLOOR_GRID = (0.45, 0.50, 0.55, 0.60, 0.65, 0.70)
MARGIN_GRID = (0.15, 0.20, 0.25)
TUNE_SAMPLE = 60        # held-out crops per hero while searching


def _profile(layout: dict) -> tuple[float, float]:
    import detect
    p = detect.detector_profile(layout)
    return float(p["floor"]), float(p["minMargin"])


def _with_profile(layout: dict, floor: float, margin: float) -> dict:
    out = dict(layout)
    out["unknown_floor"], out["min_margin"] = floor, margin
    out.pop("_gs_cache", None)
    return out


def apply_approvals(validation: dict, labels: dict) -> dict:
    """A person's double-check: heroes they approved on label.html move from
    REVIEW to VALIDATED (and still face the set's safety checks); FAILED is
    not approvable. Returns a copy; the measured numbers are untouched."""
    import template_validate as tv
    approved = set(labels.get("approved") or [])
    if not approved:
        return validation
    who = labels.get("labelledBy") or "a person"
    heroes = {}
    for h, e in (validation.get("heroes") or {}).items():
        if h in approved and e.get("verdict") == tv.REVIEW:
            e = dict(e, verdict=tv.VALIDATED, approvedBy=who,
                     why=f"{e.get('why')} — approved by {who} on double-check")
        heroes[h] = e
    return dict(validation, heroes=heroes)


def _candidate_set(staging: str, production: str, validation: dict, tmp: str) -> str:
    """What production would hold after promoting what `validation` passed:
    the templates detection would actually run with."""
    import template_forge as tf
    shutil.rmtree(tmp, ignore_errors=True)
    if os.path.isdir(production):
        shutil.copytree(production, tmp,
                        ignore=shutil.ignore_patterns("_superseded"))
    else:
        os.makedirs(tmp)
    tf.promote(staging, tmp, validation, dry_run=False)
    return tmp


# A template that matches another hero's template this well, and clearly
# better than any of its own hero's, is that other hero under the wrong name.
# The hand-made starter set carried two Symmetra portraits as tracer.a/.b for
# months: 0.91 against sym, 0.55 against tracer; every Symmetra crop then
# read as Tracer. Genuine look-alikes (Ana/Mauga, Sym/Tracer variants) stay
# well inside these bounds on every committed set.
SUSPECT_SCORE = 0.80
SUSPECT_GAP = 0.15


def suspect_templates(set_dir: str, layout: dict) -> list[dict]:
    """Templates in `set_dir` that look like another hero, not their own."""
    import cv2
    import detect
    try:
        lib = detect.load_templates(set_dir, roi=detect.portrait_roi(layout))
    except FileNotFoundError:
        return []
    out = []
    for hero, tpls in sorted(lib.items()):
        for img, fn in tpls:
            best: dict[str, float] = {}
            for other, others in lib.items():
                for img2, fn2 in others:
                    if other == hero and fn2 == fn:
                        continue
                    t = img2 if img2.shape == img.shape else \
                        cv2.resize(img2, (img.shape[1], img.shape[0]))
                    sc = float(cv2.matchTemplate(img, t, cv2.TM_CCOEFF_NORMED).max())
                    best[other] = max(best.get(other, -1.0), sc)
            own = best.get(hero, -1.0)
            near = max(((sc, h) for h, sc in best.items() if h != hero),
                       default=(-1.0, ""))
            if near[0] >= SUSPECT_SCORE and near[0] - own >= SUSPECT_GAP:
                out.append({"file": fn, "hero": hero, "looksLike": near[1],
                            "score": round(near[0], 3), "ownScore": round(own, 3)})
    return out


def _safety(set_dir: str, manifest: dict, layout: dict, *, repo_root: str,
            sample: int | None) -> dict:
    """Does this set stay quiet about heroes it cannot name? Both halves:
    crops of heroes with no template in the set, and leave-one-out."""
    import template_forge as tf
    import template_validate as tv
    val = tv.validate(set_dir, manifest, layout=layout, min_gap=tf.DEFAULT_HOLDOUT_GAP,
                      repo_root=repo_root, limit_per_hero=sample)
    loo = tv.leave_one_out(set_dir, manifest, layout=layout,
                           min_gap=tf.DEFAULT_HOLDOUT_GAP, repo_root=repo_root,
                           limit_per_hero=sample or 120)
    fm = val.get("falseMatch") or {}
    fm_ok = not fm.get("trials") or bool(fm.get("passed"))
    suspects = suspect_templates(set_dir, layout)
    return {"validation": val, "loo": loo, "falseMatchOk": fm_ok,
            "suspects": suspects,
            "safe": fm_ok and bool(loo.get("passed")) and not suspects}


def tune_profile(staging: str, production: str, manifest: dict, layout: dict, *,
                 repo_root: str, scratch: str) -> dict:
    """The least strict (floor, margin) — never looser than the layout's own
    — at which the set detection would run with stays under the false-name
    ceiling, keeping as many validated heroes as possible.

    Returns {"base", "chosen", "tried": [...]}; `chosen` is None when no
    setting on the grid is safe, which blocks promotion entirely."""
    import template_forge as tf
    import template_validate as tv
    base_f, base_m = _profile(layout)
    floors = sorted({base_f} | {f for f in FLOOR_GRID if f >= base_f})
    margins = sorted({base_m} | {m for m in MARGIN_GRID if m >= base_m})
    tried = []
    for f in floors:
        for m in margins:
            lay = _with_profile(layout, f, m)
            val = tv.validate(staging, manifest, layout=lay,
                              min_gap=tf.DEFAULT_HOLDOUT_GAP, repo_root=repo_root,
                              limit_per_hero=TUNE_SAMPLE)
            validated = sorted(h for h, e in val["heroes"].items()
                               if e["verdict"] == tv.VALIDATED)
            cand = _candidate_set(staging, production, val, os.path.join(scratch, "cand"))
            s = _safety(cand, manifest, lay, repo_root=repo_root, sample=TUNE_SAMPLE)
            tried.append({"floor": f, "margin": m, "validated": validated,
                          "safe": s["safe"], "looRate": s["loo"].get("rate"),
                          "falseMatchRate": (s["validation"].get("falseMatch") or {}).get("rate")})
            log(f"  floor {f:.2f} margin {m:.2f}: {len(validated)} validated, "
                f"leave-one-out {s['loo'].get('rate')}, "
                f"{'safe' if s['safe'] else 'NOT safe'}")
    safe = [t for t in tried if t["safe"]]
    chosen = max(safe, key=lambda t: (len(t["validated"]), -t["floor"], -t["margin"])) \
        if safe else None
    return {"base": {"floor": base_f, "margin": base_m}, "chosen": chosen,
            "tried": tried}


def write_layout_profile(path: str, floor: float, margin: float,
                         provenance: dict) -> None:
    """Set unknown_floor / min_margin in a hand-formatted layout file by
    editing those lines only, so the diff shows exactly what moved."""
    import re
    with open(path, encoding="utf-8") as f:
        text = f.read()
    # Layouts are hand-formatted with different indents (one space in some,
    # two in others); take the file's own top-level indent.
    m = re.search(r'^\{\n([ \t]*)"', text)
    indent = m.group(1) if m else "  "
    for key, value in (("unknown_floor", floor), ("min_margin", margin),
                       ("detector_tuning", provenance)):
        rendered = json.dumps(value, ensure_ascii=False)
        pat = re.compile(rf'^({re.escape(indent)}"{re.escape(key)}": ).*?(,?)$', re.M)
        if pat.search(text):
            # Replace the value, keep whatever comma the line already had.
            text = pat.sub(lambda m: m.group(1) + rendered + m.group(2), text, count=1)
        else:
            text = text.replace("{\n", "{\n" + f'{indent}"{key}": {rendered},' + "\n", 1)
    if json.loads(text).get("unknown_floor") != floor:
        raise RuntimeError(f"could not set unknown_floor in {path}")
    json.loads(text)        # never write a layout that does not parse
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def labels_digest(path: str) -> str | None:
    """What the forge records about the labels it read, so a later run can
    tell whether they changed since."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def stale_batches(root: str = lb.LABEL_ROOT) -> list[str]:
    """Batches whose labels were never promoted by this forge as they stand.

    A batch is stale when it has labels.json and its forge-report.json is
    missing, was a dry run, came from an older FORGE_VERSION, or read labels
    that have changed since. Forging only what the last commit touched lost
    batches whenever a queued run was replaced or a push carried two; this
    makes every run pick up whatever an earlier one missed."""
    out = []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        digest = labels_digest(os.path.join(root, name, "labels.json"))
        if not digest:
            continue
        rep = lb._read(os.path.join(root, name, "forge-report.json")) or {}
        if (rep.get("forgeVersion") != FORGE_VERSION or not rep.get("promote")
                or rep.get("labelsDigest") != digest):
            out.append(name)
    return out


def summarise(batch: dict, labels: dict, named: dict[str, str],
              report: dict, validation: dict, promotion: dict | None,
              loo: dict | None = None) -> dict:
    """What gets committed beside the batch: verdicts, never images."""
    heroes = {}
    for hero_id, e in sorted((validation.get("heroes") or {}).items()):
        trials = e.get("trials") or 0
        heroes[hero_id] = {
            "verdict": e.get("verdict"),
            "trials": trials, "correct": e.get("correct"),
            "unknown": e.get("unknown"), "wrong": e.get("wrong"),
            "accuracy": round((e.get("correct") or 0) / trials, 3) if trials else None,
            "confusedWith": dict(e.get("confusedWith") or {}),
            "cards": sorted(c for c, h in named.items() if h == hero_id),
        }
    return {
        "schema": "label-forge-report.v1",
        "batchId": batch["batchId"],
        "layoutId": batch.get("layoutId"),
        "templatesDir": batch.get("templatesDir"),
        "labelledBy": labels.get("labelledBy"),
        "labelledAt": labels.get("labelledAt"),
        "cardsNamed": len(named),
        "templatesWritten": report.get("templatesWritten") or 0,
        "passed": bool(validation.get("passed")),
        "falseMatch": {k: v for k, v in (validation.get("falseMatch") or {}).items()
                       if k != "examples"} or None,
        "leaveOneOut": ({k: loo[k] for k in ("trials", "matched", "rate",
                                             "ceiling", "passed", "note",
                                             "confusablePairs")}
                        if loo else None),
        "heroes": heroes,
        "promotion": ({k: promotion[k] for k in
                       ("promotedHeroes", "promoted", "superseded", "refused",
                        "dryRun")} if promotion else None),
    }


def run(batch_id: str, *, promote: bool = False, root: str = lb.LABEL_ROOT,
        repo_root: str = db.REPO_ROOT, roster: set[str] | None = None,
        staging_root: str = STAGING_ROOT) -> dict:
    import capture
    import template_forge as tf
    import template_validate as tv

    bdir = os.path.join(root, batch_id)
    batch = lb._read(os.path.join(bdir, "batch.json"))
    if not batch:
        raise ForgeRefusal(f"no batch at {bdir}")
    labels = lb._read(os.path.join(bdir, "labels.json"))
    if not labels:
        raise ForgeRefusal(f"{batch_id} has no labels.json yet — name its "
                           f"cards on label.html?batch={batch_id}")
    named = resolve_labels(batch, labels,
                           roster if roster is not None else roster_ids())
    manifest = build_manifest(batch, named, repo_root=repo_root)
    log(f"{len(named)} named card(s) → {manifest['counts']['labeled']} crop(s) "
        f"across {manifest['counts']['heroes']} hero(es)")

    layout_path = os.path.join(repo_root, "layouts", f"{batch['layoutId']}.json")
    layout = capture.load_layout(layout_path)
    manifest, cleaning = clean_cards(manifest, layout, repo_root=repo_root)
    if cleaning["droppedTotal"]:
        log(f"set aside {cleaning['droppedTotal']} crop(s) that do not look like "
            f"their card: {cleaning['dropped']}")
    staging = os.path.join(staging_root, batch_id)
    # A previous run's staged templates must not be validated as this one's.
    shutil.rmtree(staging, ignore_errors=True)
    report = tf.forge(manifest, staging, layout=layout, repo_root=repo_root)
    production = os.path.join(repo_root, batch["templatesDir"])
    loo = None
    tuning = None
    production_check = None
    promotion = None
    if not report["templatesWritten"]:
        validation = {"heroes": {}, "passed": False}
        log("no template survived the build gate — nothing to validate")
    else:
        # Tune the detector's bar on the set it would actually run with —
        # production as it stands plus whatever this batch would promote —
        # because a hero can only be misread as one that is IN the set.
        log("tuning the detector's bar against the set it would run with:")
        tuning = tune_profile(staging, production, manifest, layout,
                              repo_root=repo_root,
                              scratch=os.path.join(staging_root, f"_{batch_id}"))
        chosen = tuning["chosen"]
        if chosen:
            layout = _with_profile(layout, chosen["floor"], chosen["margin"])
        validation = tv.validate(staging, manifest, layout=layout,
                                 min_gap=tf.DEFAULT_HOLDOUT_GAP, repo_root=repo_root)
        loo = tv.leave_one_out(staging, manifest, layout=layout,
                               min_gap=tf.DEFAULT_HOLDOUT_GAP, repo_root=repo_root)
        validation = apply_approvals(validation, labels)
        cand = _candidate_set(staging, production, validation,
                              os.path.join(staging_root, f"_{batch_id}", "final"))
        production_check = _safety(cand, manifest, layout, repo_root=repo_root,
                                   sample=None)
        safe = bool(chosen) and production_check["safe"]
        if not safe:
            for sus in production_check.get("suspects") or []:
                log(f"REFUSED: {sus['file']} (named {sus['hero']}) matches "
                    f"{sus['looksLike']} at {sus['score']} but its own hero at "
                    f"{sus['ownScore']} — move it to _superseded/ if it is "
                    f"really {sus['looksLike']}")
            log("REFUSED: no detector bar on the grid keeps this set under the "
                "false-name ceiling — nothing is promoted")
            promotion = {"promotedHeroes": [], "promoted": [], "superseded": [],
                         "refused": {h: e["verdict"] for h, e in validation["heroes"].items()},
                         "dryRun": not promote,
                         "refusedForSafety": True}
        else:
            promotion = tf.promote(staging, production, validation, dry_run=not promote)
            if promote:
                base = tuning["base"]
                if (chosen["floor"], chosen["margin"]) != (base["floor"], base["margin"]):
                    write_layout_profile(layout_path, chosen["floor"], chosen["margin"], {
                        "measuredOn": f"labelling/{batch_id}",
                        "from": base, "rule": "raised only, never lowered",
                        "leaveOneOutRate": production_check["loo"].get("rate"),
                        "validatedHeroes": chosen["validated"]})
                    log(f"raised {batch['layoutId']} to floor {chosen['floor']} "
                        f"margin {chosen['margin']}")
                # What hero_coverage and the site read as this package's proof.
                vpath = os.path.join(repo_root, "reports", "validation",
                                     f"{batch['layoutId']}.json")
                os.makedirs(os.path.dirname(vpath), exist_ok=True)
                proof = dict(production_check["validation"],
                             leaveOneOut=production_check["loo"])
                with open(vpath, "w", encoding="utf-8") as f:
                    json.dump(proof, f, indent=1, default=str)
                    f.write("\n")
    summary = summarise(batch, labels, named, report, validation, promotion, loo)
    summary["cleaning"] = cleaning
    summary["tuning"] = tuning
    summary["forgeVersion"] = FORGE_VERSION
    summary["promote"] = bool(promote)
    summary["labelsDigest"] = labels_digest(os.path.join(bdir, "labels.json"))
    if production_check:
        pv = production_check["validation"]
        summary["productionSet"] = {
            "safe": production_check["safe"],
            "falseMatch": {k: v for k, v in (pv.get("falseMatch") or {}).items()
                           if k != "examples"} or None,
            "leaveOneOut": {k: production_check["loo"].get(k)
                            for k in ("trials", "matched", "rate", "ceiling",
                                      "passed", "note", "confusablePairs")},
            "verdicts": {h: e.get("verdict") for h, e in (pv.get("heroes") or {}).items()},
            "suspectTemplates": production_check.get("suspects") or [],
        }
    with open(os.path.join(bdir, "forge-report.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1, default=str)
        f.write("\n")
    lb.write_index(root)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--batch")
    ap.add_argument("--promote", action="store_true",
                    help="copy VALIDATED heroes into the layout's template set")
    ap.add_argument("--stale", action="store_true",
                    help="print the batches whose labels this forge has not "
                         "promoted as they stand, and exit")
    args = ap.parse_args(argv)
    if args.stale:
        print(" ".join(stale_batches()))
        return 0
    if not args.batch:
        ap.error("--batch is required (or --stale)")
    try:
        s = run(args.batch, promote=args.promote)
    except ForgeRefusal as exc:
        log(f"refused: {exc}")
        return 2
    for hero_id, h in s["heroes"].items():
        acc = "—" if h["accuracy"] is None else f"{h['accuracy'] * 100:.0f}%"
        log(f"  {hero_id:10} {h['verdict']:13} {acc:>5} over {h['trials']} "
            f"held-out crop(s)")
    p = s["promotion"] or {}
    log(("promoted" if args.promote else "would promote") + ": "
        + (", ".join(p.get("promotedHeroes") or []) or "nothing"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
