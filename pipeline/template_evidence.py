#!/usr/bin/env python3
"""
template_evidence.py — turn a real ingest run into a labeled evidence set
that hero templates can be *validated against*, not merely built from.

The distinction matters more than anything else in this file. A template
that matches the frame it was cut from proves nothing: correlation with
yourself is 1.0 by construction. The only question worth answering is
whether a template recognises the hero in frames it has never seen. That
needs two things this module produces:

  1. **crops with a label that does not come from the template under test**,
  2. **a timestamp for every crop**, so validation can prove separation from
     whatever frames the template was cut from.

### Where the labels come from, stated plainly

There is no human-labeled OWCS portrait corpus in this repository, and
inventing one would be exactly the "fake evidence" this project refuses. So
labels come from **stint consensus**, and every crop records that fact in
`labelSource` so no downstream report can quietly present it as human
ground truth.

A *stint* is a run of consecutive observations of one slot that agree on one
hero — in the committed Nepal ingest, slot a1 reads `shion` for 307
consecutive samples spanning 16 minutes at a mean confidence of 0.95. The
consensus label for that stint is `shion`, and it applies to **every** crop
in the stint, including the handful the detector read as UNKNOWN or read
wrong. That last part is what makes the label independent of any single
frame's detector output: a template being validated cannot influence the
label of the crop it is being tested on, because the label came from 306
other frames.

A stint only qualifies when it is big enough and clean enough to carry that
weight (`MIN_STINT_OBS`, `MIN_AGREEMENT`, `MIN_MEAN_CONF`). Everything else
— short stints, low-confidence stints, the frames straddling a swap — is
**excluded with a reason** rather than labeled optimistically. Excluded
crops are still emitted, in `excluded`, because they are exactly the crops a
human reviewer should look at.

Human labels, when they exist, always win: pass `--labels human.json`
(`{"t0001805.0_a1.png": "shion"}`) and those crops are marked
`labelSource: "human"`. A validation report distinguishes the two.

### What this module deliberately does NOT do

It does not create templates, and it does not decide whether a template is
good. It only assembles evidence. Keeping those apart is what stops the
circularity: the thing that builds templates and the thing that judges them
must not share a source of truth.

CLI:
  python3 pipeline/template_evidence.py --report reports/ingest/qad-twis-nepal \
      --layout owcs_jksix_qwc --out work/evidence/nepal.json
  python3 pipeline/template_evidence.py --manifest work/evidence/nepal.json --summary
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

MANIFEST_VERSION = 1

# A stint must be at least this many observations before its consensus label
# is trusted. Twelve samples of a 5s-cadence capture is a minute of agreeing
# footage — enough that a single bad frame cannot carry the label.
MIN_STINT_OBS = 12
# Fraction of the stint's observations that must actually read the consensus
# hero. Below this it is not a stint, it is a contested slot.
MIN_AGREEMENT = 0.80
# Mean confidence over the agreeing observations.
MIN_MEAN_CONF = 0.70
# Crops within this many seconds of a stint boundary are dropped: a swap does
# not happen exactly on a sample, so the frames either side of one can show
# either hero (or a transition animation of both).
BOUNDARY_GUARD_S = 6.0

LABEL_HUMAN = "human"
LABEL_CONSENSUS = "stint-consensus"


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


# ------------------------------------------------------------ observations
def read_observations(report_dir: str) -> list[dict]:
    """Parse `observations.jsonl` from an ingest report directory."""
    path = os.path.join(report_dir, "observations.jsonl")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found — an evidence set needs a real ingest run's "
            f"per-frame observations, not a summary")
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _slot_series(rows: list[dict]) -> dict[str, list[dict]]:
    """{slot_key: [{t, hero, score, crop, state}, ...]} sorted by time."""
    series: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        t = float(row.get("t", 0.0))
        state = row.get("state") or "unknown"
        for key, obs in (row.get("slots") or {}).items():
            series[key].append({
                "t": t,
                "state": state,
                "hero": obs.get("hero"),
                "score": float(obs.get("score") or 0.0),
                "margin": float(obs.get("margin") or 0.0),
                "crop": obs.get("crop"),
                "template": obs.get("template"),
            })
    for key in series:
        series[key].sort(key=lambda o: o["t"])
    return dict(series)


def _segment(obs: list[dict]) -> list[list[dict]]:
    """Split a slot's timeline wherever the named hero changes.

    UNKNOWN reads do NOT split a segment — they are exactly the frames a
    stint is meant to carry through, and they are the ones worth validating
    against. Any *named* hero change does, **regardless of its confidence**.

    That last clause is load-bearing and was learned the hard way. An earlier
    version only split on reads above 0.6, reasoning that a weak read is
    noise. On the committed Nepal footage, slot a5 genuinely shows Lúcio for
    the first ten seconds before swapping to Juno, and the detector read
    those frames as `lucio` at ~0.51 — correctly. Absorbing them into the
    Juno stint produced five crops labeled `juno` that a human can see are
    Lúcio, and validation then reported the *detector* as wrong for getting
    them right. Ground truth built by consensus must never let a confidence
    threshold decide what counts as a hero change; a short run is handled
    honestly by `MIN_STINT_OBS` disqualifying it, not by hiding it inside its
    neighbour.
    """
    segments: list[list[dict]] = []
    current: list[dict] = []
    anchor: str | None = None
    for o in obs:
        hero = o["hero"]
        named = bool(hero) and hero != "UNKNOWN"
        if named and anchor is not None and hero != anchor:
            segments.append(current)
            current, anchor = [], None
        if named and anchor is None:
            anchor = hero
        current.append(o)
    if current:
        segments.append(current)
    return segments


def _consensus(segment: list[dict]) -> tuple[str | None, float, float]:
    """(hero, agreement fraction, mean confidence of agreeing reads)."""
    votes = Counter(o["hero"] for o in segment
                    if o["hero"] and o["hero"] != "UNKNOWN")
    if not votes:
        return None, 0.0, 0.0
    hero, n = votes.most_common(1)[0]
    agreeing = [o["score"] for o in segment if o["hero"] == hero]
    return hero, n / float(len(segment)), (sum(agreeing) / len(agreeing))


# --------------------------------------------------------------- build set
def build_from_report(report_dir: str, *, layout_id: str,
                      source_video: str | None = None,
                      human_labels: dict[str, str] | None = None,
                      crops_subdir: str = "evidence") -> dict:
    """Assemble a labeled evidence manifest from one ingest report."""
    rows = read_observations(report_dir)
    human_labels = human_labels or {}
    crops_dir = os.path.join(report_dir, crops_subdir)
    if not os.path.isdir(crops_dir):
        alt = os.path.join(report_dir, "crops")
        crops_dir = alt if os.path.isdir(alt) else crops_dir

    crops: list[dict] = []
    excluded: list[dict] = []
    stints: list[dict] = []

    for key, obs in sorted(_slot_series(rows).items()):
        side = key[0]
        for idx, segment in enumerate(_segment(obs)):
            stint_id = f"{key}#{idx}"
            hero, agreement, mean_conf = _consensus(segment)
            t_first = segment[0]["t"]
            t_last = segment[-1]["t"]
            reason = None
            if hero is None:
                reason = "no confident reading anywhere in this segment"
            elif len(segment) < MIN_STINT_OBS:
                reason = (f"only {len(segment)} observation(s); a consensus "
                          f"label needs {MIN_STINT_OBS}")
            elif agreement < MIN_AGREEMENT:
                reason = (f"only {agreement * 100:.0f}% of reads agree on "
                          f"{hero}; a contested slot is not ground truth")
            elif mean_conf < MIN_MEAN_CONF:
                reason = (f"mean confidence {mean_conf:.2f} on {hero} is "
                          f"below {MIN_MEAN_CONF}")
            stints.append({
                "stintId": stint_id, "slot": key, "side": side,
                "hero": hero, "tFirst": t_first, "tLast": t_last,
                "observations": len(segment),
                "agreement": round(agreement, 3),
                "meanConfidence": round(mean_conf, 3),
                "qualified": reason is None,
                "disqualifiedBecause": reason,
            })

            for o in segment:
                fname = o.get("crop")
                if not fname:
                    continue
                path = os.path.join(crops_dir, fname)
                record = {
                    "file": fname,
                    "slot": key, "side": side, "t": o["t"],
                    "state": o["state"],
                    "stintId": stint_id,
                    "detectorHero": o["hero"],
                    "detectorScore": round(o["score"], 3),
                    "detectorTemplate": o.get("template"),
                }
                if fname in human_labels:
                    record["hero"] = human_labels[fname]
                    record["labelSource"] = LABEL_HUMAN
                    crops.append(record)
                    continue
                if reason is not None:
                    excluded.append(dict(record, excludedBecause=reason))
                    continue
                # The guard applies at BOTH ends of EVERY segment, including
                # the first. A capture can start mid-transition just as
                # easily as a swap can land between samples, and six seconds
                # of crops per slot is a cheap price for not labeling a
                # dissolve.
                near_start = (o["t"] - t_first) < BOUNDARY_GUARD_S
                near_end = (t_last - o["t"]) < BOUNDARY_GUARD_S
                if near_start or near_end:
                    excluded.append(dict(
                        record,
                        excludedBecause=(
                            f"within {BOUNDARY_GUARD_S:.0f}s of a stint "
                            f"boundary — a swap does not land on a sample, "
                            f"so this frame can legitimately show either "
                            f"hero")))
                    continue
                if not os.path.exists(path):
                    excluded.append(dict(
                        record,
                        excludedBecause=f"crop file missing at {path}"))
                    continue
                record["hero"] = hero
                record["labelSource"] = LABEL_CONSENSUS
                crops.append(record)

    by_hero = Counter(c["hero"] for c in crops)
    by_state = Counter(c["state"] for c in crops)
    return {
        "evidenceSetVersion": MANIFEST_VERSION,
        "generatedAt": _utcnow_iso(),
        "sourceReport": os.path.relpath(report_dir, db.REPO_ROOT).replace("\\", "/"),
        "cropsDir": os.path.relpath(crops_dir, db.REPO_ROOT).replace("\\", "/"),
        "sourceVideo": source_video,
        "layoutId": layout_id,
        "labelPolicy": {
            "method": "stint consensus over consecutive same-slot reads",
            "minStintObservations": MIN_STINT_OBS,
            "minAgreement": MIN_AGREEMENT,
            "minMeanConfidence": MIN_MEAN_CONF,
            "boundaryGuardSeconds": BOUNDARY_GUARD_S,
            "humanLabelsOverride": True,
            "honesty": (
                "These labels are detector consensus over a temporal stint, "
                "NOT a human's eyes. A stint's label is decided by hundreds "
                "of agreeing frames, so it is independent of the single "
                "frame being validated — but a systematically mislabeled "
                "template set would produce a systematically mislabeled "
                "stint, and this method cannot catch that. Human labels, "
                "where supplied, override and are marked as such."),
        },
        "counts": {
            "labeled": len(crops),
            "excluded": len(excluded),
            "heroes": len(by_hero),
            "byHero": dict(sorted(by_hero.items())),
            "byState": dict(sorted(by_state.items())),
            "byLabelSource": dict(Counter(c["labelSource"] for c in crops)),
        },
        "stints": stints,
        "crops": crops,
        "excluded": excluded,
    }


# -------------------------------------------------------------------- I/O
def save(manifest: dict, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
        f.write("\n")
    return path


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    if manifest.get("evidenceSetVersion") != MANIFEST_VERSION:
        raise ValueError(
            f"{path}: evidence set version "
            f"{manifest.get('evidenceSetVersion')!r}, expected "
            f"{MANIFEST_VERSION}")
    return manifest


def crop_path(manifest: dict, record: dict, *,
              repo_root: str = db.REPO_ROOT) -> str:
    return os.path.join(repo_root, manifest["cropsDir"], record["file"])


def format_summary(manifest: dict) -> str:
    c = manifest["counts"]
    lines = [
        f"  source        : {manifest['sourceReport']}"
        f"  (layout {manifest['layoutId']})",
        f"  labeled crops : {c['labeled']} across {c['heroes']} hero(es)",
        f"  excluded      : {c['excluded']} (reasons recorded per crop)",
        f"  label sources : " + ", ".join(
            f"{k}={v}" for k, v in sorted(c["byLabelSource"].items())),
        f"  by state      : " + ", ".join(
            f"{k}={v}" for k, v in sorted(c["byState"].items())),
        f"  by hero       : " + ", ".join(
            f"{k}={v}" for k, v in sorted(c["byHero"].items())),
    ]
    unqualified = [s for s in manifest["stints"] if not s["qualified"]]
    if unqualified:
        lines.append(f"  {len(unqualified)} stint(s) did NOT qualify as ground "
                     f"truth:")
        for s in unqualified[:8]:
            lines.append(f"    {s['stintId']} ({s['hero']}): "
                         f"{s['disqualifiedBecause']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="build a labeled evidence set from a real ingest run")
    ap.add_argument("--report", help="ingest report dir (has observations.jsonl)")
    ap.add_argument("--layout", help="layout id this footage was captured with")
    ap.add_argument("--source-video", help="the broadcast URL, for the record")
    ap.add_argument("--labels", help="human label JSON {crop_file: hero_id}")
    ap.add_argument("--crops-subdir", default="evidence")
    ap.add_argument("--out", help="write the manifest here")
    ap.add_argument("--manifest", help="load an existing manifest instead")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.manifest:
        manifest = load(args.manifest)
    else:
        if not args.report or not args.layout:
            ap.error("--report and --layout are required (or use --manifest)")
        human = {}
        if args.labels:
            with open(args.labels, encoding="utf-8") as f:
                human = json.load(f)
        manifest = build_from_report(
            args.report, layout_id=args.layout,
            source_video=args.source_video, human_labels=human,
            crops_subdir=args.crops_subdir)
        if args.out:
            save(manifest, args.out)
            print(f"[evidence] wrote {args.out}")

    if args.json:
        print(json.dumps(manifest, indent=1))
    elif args.summary or not args.out:
        print(format_summary(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
