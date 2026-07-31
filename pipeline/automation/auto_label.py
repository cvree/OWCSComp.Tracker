"""
auto_label.py — unattended hero-template labelling that can only ADD.

`--auto-templates` exists because template coverage is the real detection
ceiling: a package can only identify a hero it has a template for, and every
other hero reads UNKNOWN forever. At 13–33% coverage the detection gate will
refuse almost everything, so the sequence that actually produces hands-off
output is coverage first, detection second.

Two design decisions are load-bearing here, and both are deliberate
departures from the code this module sits next to.

**Official hero art suggests, it never decides.**
`template_bootstrap.suggest_labels` scores clusters against official splash
renders, and its own docstring is right about why that is only a suggestion:
a render does not look like a broadcast HUD portrait, and a template cut from
one would quietly poison detection. So the primary score here is against REAL
LABELLED PORTRAITS — the hero PNGs a human already reviewed in the repo's
other broadcast packages. Same kind of image, same HUD, same compression.
Official art is still consulted, but only as a VETO: when the portrait match
and the official-art match name different heroes, the cluster is held. Two
independent sources disagreeing about identity is the worst possible moment
to pick one.

**Labelling only ever adds.**
`harvest_templates.stage_labels` deletes every `*.png` in the output
directory before writing. For a human doing a full reviewed pass that is
correct — it is how you replace a bad set cleanly. For an unattended pass it
is catastrophic: a run that confidently labels six heroes would delete the
twenty a human labelled last month. So this module writes hero PNGs directly,
never calls `stage_labels`, and refuses to overwrite a hero that already has
a template in the package. A run that clears nothing writes no PNG at all and
still leaves a review manifest behind saying what it looked at and why each
cluster was held.

Everything heavy (cv2, harvesting) is imported inside the functions that
need it, so importing this module — and printing its refusals — works on a
machine with no OpenCV, like every other module in this package.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_DIR = os.path.dirname(_HERE)
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

import db as content_db  # noqa: E402

from . import gates as gt  # noqa: E402

MANIFEST_FILENAME = "_auto_label_review.json"
# Sampling window for the harvest, in clip-relative seconds. Matches the
# default `template_bootstrap.bootstrap` uses.
DEFAULT_TIMES = "60:600:15"
DEFAULT_MAX_CLUSTERS = 12


def log(msg: str) -> None:
    print(f"[auto-label] {msg}", flush=True)


def _utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------- labelled-portrait index
def portrait_reference_index(exclude_dir: str | None = None, *,
                             templates_root: str | None = None
                             ) -> dict[str, list[str]]:
    """{hero_id: [portrait png paths]} from every OTHER broadcast package.

    These are the references the gate scores against: images a human already
    labelled, cut from real broadcasts. The package being labelled is
    excluded — scoring a cluster against the very set we are extending would
    be circular, and would happily confirm an existing mistake.

    Both layouts of the repo's template tree are read: the flat legacy set at
    `templates/*.png` and the per-package sets at `templates/<package>/*.png`.
    """
    import template_bootstrap as tb

    root = templates_root or os.path.join(content_db.REPO_ROOT, "templates")
    exclude = os.path.abspath(exclude_dir) if exclude_dir else None
    out: dict[str, list[str]] = {}
    if not os.path.isdir(root):
        return out

    dirs = [root] + [os.path.join(root, d) for d in sorted(os.listdir(root))
                     if os.path.isdir(os.path.join(root, d))
                     and not d.startswith("_")]
    for d in dirs:
        if exclude and os.path.abspath(d) == exclude:
            continue
        for hero_id, files in tb.scan_template_dir(d).items():
            for entry in files:
                out.setdefault(hero_id, []).append(
                    os.path.join(d, entry["file"]))
    return out


def score_against_portraits(proto_gray, references: dict[str, list[str]]
                            ) -> list[tuple[float, str]]:
    """[(score, hero_id), ...] best first, one score per hero.

    A hero with several reference variants keeps its BEST match — a portrait
    set legitimately contains alive/dead/ult states, and a cluster only ever
    depicts one of them.
    """
    import cv2

    scored: list[tuple[float, str]] = []
    for hero_id, paths in references.items():
        best = None
        for path in paths:
            ref = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if ref is None:
                continue
            resized = cv2.resize(ref, (proto_gray.shape[1],
                                       proto_gray.shape[0]))
            score = float(cv2.matchTemplate(proto_gray, resized,
                                            cv2.TM_CCOEFF_NORMED).max())
            best = score if best is None else max(best, score)
        if best is not None:
            scored.append((round(best, 4), hero_id))
    scored.sort(reverse=True)
    return scored


def build_candidate(cluster_name: str, proto_gray, *, frames: int,
                    references: dict[str, list[str]],
                    official: dict | None = None) -> dict:
    """The dict `gates.evaluate_templates_gate` judges, for one cluster."""
    ranked = score_against_portraits(proto_gray, references)
    best_score, best_hero = (ranked[0] if ranked else (None, None))
    runner_score = ranked[1][0] if len(ranked) > 1 else None
    opinion = official or {}
    return {
        "clusterId": cluster_name,
        "hero": best_hero,
        "score": best_score,
        "runnerUpHero": ranked[1][1] if len(ranked) > 1 else None,
        "runnerUpScore": runner_score,
        "frames": frames,
        "referenceKind": "labeled-portrait",
        # Official art is consulted only to contradict, never to elect: an
        # opinion is attached whether or not it agrees, and the gate holds
        # the cluster when the two disagree.
        "officialOpinion": ({"hero": opinion.get("bestGuess"),
                             "score": opinion.get("score"),
                             "confident": opinion.get("confident")}
                            if opinion else {}),
    }


# ------------------------------------------------------------- the labeller
def _templates_dir_for(job) -> str | None:
    """The template directory of this job's resolved broadcast package."""
    from . import detection_runner as dr

    layout_id = (job.payload.get("expectedLayoutId")
                 or (job.payload.get("layout") or {}).get("layoutId"))
    if not layout_id:
        return None
    path = dr.layout_path(layout_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            rel = (json.load(f) or {}).get("templates_dir")
    except (OSError, ValueError):
        return None
    if not rel:
        return None
    return (rel if os.path.isabs(rel)
            else os.path.join(content_db.REPO_ROOT, rel))


def _harvest_clip_for(store, job) -> str | None:
    """An approved segment's extracted clip is the best harvest source — it
    is full-resolution and already known to be gameplay. Falls back to
    nothing rather than harvesting from the 360p scan proxy, whose portraits
    are too small to cut usable templates from."""
    from . import segmentation as seg

    segments = seg.list_segments(store.con,
                                 video_id=job.payload.get("videoId"),
                                 review_status="approved")
    for s in segments:
        rel = s.get("extracted_path")
        if not rel:
            continue
        path = os.path.join(content_db.REPO_ROOT, rel)
        if os.path.exists(path):
            return path
    return None


def label_package(store, job, *, floors: dict | None = None,
                  dry_run: bool = False, max_clusters: int = DEFAULT_MAX_CLUSTERS,
                  times_spec: str = DEFAULT_TIMES) -> dict:
    """Harvest, score and (unless `dry_run`) ADD templates for one job.

    Returns {"labelled": [...], "held": [...], "skippedCovered": [...],
             "coverageBefore", "coverageAfter", "manifest", "dryRun"}.

    Writes no hero PNG for a cluster whose gate verdict refused, and none for
    a hero the package already covers. Always writes the review manifest —
    including when nothing was labelled, which is the case an operator most
    needs a record of.
    """
    import capture
    import cv2
    import harvest_templates as ht
    import template_bootstrap as tb

    from . import detection_runner as dr

    floors = floors or gt.floor_values()
    templates_dir = _templates_dir_for(job)
    if not templates_dir:
        raise ValueError("this job has no resolved layout with a "
                         "templates_dir — resolve/approve a layout first")

    con = content_db.connect()
    try:
        roster = tb.full_roster(con)
    finally:
        con.close()
    before = tb.template_set_status(templates_dir, roster)
    covered = set(before["covered"])

    clip = _harvest_clip_for(store, job)
    if not clip:
        raise ValueError("no approved, extracted segment clip to harvest "
                         "portraits from — approve a segment first")
    layout_id = (job.payload.get("expectedLayoutId")
                 or (job.payload.get("layout") or {}).get("layoutId"))
    layout = capture.load_layout(dr.layout_path(layout_id))

    slot_keys = [f"{s}{i}" for s in ("a", "b") for i in range(1, 6)]
    times = ht.parse_times(times_spec)
    crops = ht.collect_crops(clip, times, layout, slot_keys)

    references = portrait_reference_index(exclude_dir=templates_dir)
    if not references:
        raise ValueError(
            "no labelled portraits exist in any other broadcast package, so "
            "there is nothing to score against — the first package's "
            "templates must be labelled by a human")

    # Grayscale prototypes drive the matching; the colour ones are what a
    # template file is actually written from. Kept in separate maps so the
    # two can never be confused at the point of writing a PNG.
    protos_gray: dict[str, object] = {}
    protos_bgr: dict[str, object] = {}
    frames_by_cluster: dict[str, int] = {}
    for key in slot_keys:
        for k, c in enumerate(ht.cluster_slot(crops.get(key) or [])[:max_clusters]):
            name = f"{key}_c{k}"
            protos_gray[name] = c["proto_gray"]
            protos_bgr[name] = c["proto"]
            frames_by_cluster[name] = len(c["members"])

    # Official art, consulted for veto purposes only. Its absence weakens the
    # check (the veto cannot fire) but never blocks the pass, so a missing
    # asset directory does not take the whole unattended path down with it.
    official = {}
    try:
        icon_index = tb.official_icon_index(roster)
        if icon_index:
            official = tb.suggest_labels(protos_gray, icon_index
                                         ).get("suggestions", {})
    except Exception as exc:  # noqa: BLE001 — a missing veto is not fatal
        log(f"official-art opinion unavailable ({exc}) — proceeding with "
            f"portrait scores only; the veto simply cannot fire")

    labelled: list[str] = []
    held: list[dict] = []
    skipped_covered: list[str] = []
    written: list[dict] = []

    for name in sorted(protos_gray):
        candidate = build_candidate(
            name, protos_gray[name], frames=frames_by_cluster.get(name, 0),
            references=references, official=official.get(name))
        verdict = gt.evaluate_templates_gate(candidate, floors=floors)
        hero = candidate.get("hero")

        if not verdict.passed:
            held.append({"cluster": name, "hero": hero,
                         "reasonCode": verdict.reason_code,
                         "reason": verdict.reason,
                         "metrics": verdict.metrics})
            continue

        # Additive-only: a hero a human already labelled is never touched,
        # and never counted as a failure — it simply is not this pass's job.
        if hero in covered:
            skipped_covered.append(hero)
            held.append({"cluster": name, "hero": hero,
                         "reasonCode": "already_covered",
                         "reason": f"{hero} already has a template in this "
                                   f"package — auto-labelling never "
                                   f"overwrites a covered hero",
                         "metrics": verdict.metrics})
            continue

        dest = os.path.join(templates_dir, f"{hero}.png")
        if os.path.exists(dest):
            held.append({"cluster": name, "hero": hero,
                         "reasonCode": "file_exists",
                         "reason": f"{dest} already exists — refusing to "
                                   f"overwrite",
                         "metrics": verdict.metrics})
            continue

        if not dry_run:
            os.makedirs(templates_dir, exist_ok=True)
            cv2.imwrite(dest, protos_bgr[name])
        labelled.append(hero)
        covered.add(hero)
        written.append({"cluster": name, "hero": hero,
                        "file": os.path.basename(dest),
                        "metrics": verdict.metrics,
                        "reason": verdict.reason})

    after = tb.template_set_status(templates_dir, roster)
    manifest_path = os.path.join(templates_dir, MANIFEST_FILENAME)
    manifest = {
        "generatedAt": _utcnow_iso(),
        "jobKey": job.job_key,
        "layoutId": layout_id,
        "sourceClip": os.path.relpath(clip, content_db.REPO_ROOT
                                      ).replace("\\", "/"),
        "dryRun": dry_run,
        "floors": {k: v for k, v in floors.items()
                   if k.startswith("auto_templates_")},
        "referencePackages": sorted({os.path.basename(os.path.dirname(p))
                                     for paths in references.values()
                                     for p in paths}),
        "labelled": written,
        "held": held,
        "skippedCovered": sorted(set(skipped_covered)),
        "coverageBefore": f"{before['coveredCount']}/{before['rosterSize']} "
                          f"({before['coveragePct']}%)",
        "coverageAfter": f"{after['coveredCount']}/{after['rosterSize']} "
                         f"({after['coveragePct']}%)",
        "note": ("Scored against real labelled broadcast portraits from other "
                 "packages. Official hero art was used only as a veto. This "
                 "pass never overwrites an existing template."),
    }
    if not dry_run:
        os.makedirs(templates_dir, exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=1)
            f.write("\n")
        # Provenance for every PNG this pass added, next to the templates.
        if written:
            tb.write_provenance(templates_dir, [
                {"hero": w["hero"], "file": w["file"], "cluster": w["cluster"],
                 "sourceVideo": job.payload.get("videoId"),
                 "labeledBy": "automatic-gate:templates",
                 "labeledAt": manifest["generatedAt"],
                 "reason": w["reason"]}
                for w in written])

    log(f"{job.job_key}: labelled {len(labelled)}, held {len(held)}, "
        f"coverage {manifest['coverageBefore']} -> "
        f"{manifest['coverageAfter']}"
        + (" (DRY RUN — nothing written)" if dry_run else ""))

    return {
        "labelled": labelled,
        "held": held,
        "skippedCovered": sorted(set(skipped_covered)),
        "coverageBefore": manifest["coverageBefore"],
        "coverageAfter": manifest["coverageAfter"],
        "manifest": (None if dry_run else
                     os.path.relpath(manifest_path, content_db.REPO_ROOT
                                     ).replace("\\", "/")),
        "manifestPreview": manifest if dry_run else None,
        "dryRun": dry_run,
        "templatesDir": templates_dir,
    }
