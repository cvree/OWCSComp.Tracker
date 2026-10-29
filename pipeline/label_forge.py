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

    layout = capture.load_layout(os.path.join(repo_root, "layouts",
                                              f"{batch['layoutId']}.json"))
    staging = os.path.join(staging_root, batch_id)
    # A previous run's staged templates must not be validated as this one's.
    shutil.rmtree(staging, ignore_errors=True)
    report = tf.forge(manifest, staging, layout=layout, repo_root=repo_root)
    loo = None
    if not report["templatesWritten"]:
        validation = {"heroes": {}, "passed": False}
        promotion = None
        log("no template survived the build gate — nothing to validate")
    else:
        validation = tv.validate(staging, manifest, layout=layout,
                                 min_gap=tf.DEFAULT_HOLDOUT_GAP,
                                 repo_root=repo_root)
        # The safety half: with each hero's templates removed, do its own
        # crops read UNKNOWN? Reported beside the verdicts; it does not
        # block a hero that validated, but a failing rate is stated.
        loo = tv.leave_one_out(staging, manifest, layout=layout,
                               min_gap=tf.DEFAULT_HOLDOUT_GAP,
                               repo_root=repo_root)
        production = os.path.join(repo_root, batch["templatesDir"])
        promotion = tf.promote(staging, production, validation,
                               dry_run=not promote)
    summary = summarise(batch, labels, named, report, validation, promotion,
                        loo)
    with open(os.path.join(bdir, "forge-report.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1, default=str)
        f.write("\n")
    lb.write_index(root)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--batch", required=True)
    ap.add_argument("--promote", action="store_true",
                    help="copy VALIDATED heroes into the layout's template set")
    args = ap.parse_args(argv)
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
