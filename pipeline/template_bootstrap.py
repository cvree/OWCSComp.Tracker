#!/usr/bin/env python3
"""
template_bootstrap.py — hero-template coverage, honestly measured (Phase 5).

Template coverage is the REAL detection ceiling. A broadcast package can only
identify a hero it has a template for; every other hero is `UNKNOWN`, and no
amount of tuning changes that. So this module's job is not to maximize a
number — it is to make the ceiling visible and to make raising it cheap.

What it does:

  * `template_set_status` — for one broadcast package's template directory,
    report heroes covered out of the FULL roster, variants per hero, and
    every uncovered hero by name. An empty/missing directory is reported as
    0% covered, never as "fine".
  * `reuse_existing` — a package that already has a template set reuses it.
    Re-harvesting a working set is wasted work and risks regressing it.
  * `harvest_clusters` — harvest real portrait crops from APPROVED gameplay
    and cluster visually-similar ones, delegating entirely to the proven
    `harvest_templates.py` (its `collect_crops`/`cluster_slot`/`pick_variants`
    are already tuned against real broadcasts).
  * `suggest_labels` — rank the official hero ICON assets against each
    cluster prototype to PROPOSE a label. Official art is used ONLY to help
    a human name a cluster; it is never written into a template set, because
    an official splash render does not look like a broadcast's HUD portrait
    and a template cut from one would quietly poison detection.
  * low-margin clusters are surfaced for review rather than auto-labeled.
  * `write_provenance` — every template file's origin (source video, offset,
    cluster, who labeled it) recorded next to the templates.

Nothing here promotes a template into production. `stage_labels` in
harvest_templates.py (a human running it with a reviewed label map) is still
the only writer of hero PNGs.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

# cv2 is needed only by the harvesting/labeling paths; coverage reporting and
# provenance are pure filesystem/DB work and must stay runnable without it.
OFFICIAL_ASSETS_DIR = os.path.join(db.REPO_ROOT, "assets", "img", "heroes",
                                   "official")
PROVENANCE_FILENAME = "_provenance.json"

# A label suggestion must beat the runner-up by this much to be offered as a
# confident proposal. Below it, the cluster is surfaced for human review —
# the same best-vs-runner-up margin discipline detect.py uses for slots.
LABEL_MARGIN = 0.06
# Below this similarity, no suggestion is offered at all.
LABEL_MIN_SCORE = 0.25
# Variants to keep per hero: the alive portrait plus its dead/ult-state
# appearances when the harvest actually saw them.
DEFAULT_VARIANTS = 3

# Variant suffixes a template set may carry, and what they mean. `detect.
# load_templates` already accepts ANY '<hero>.<tag>.png' tag; this list is
# what the coverage report names so an operator can see which states are
# represented rather than only how many files exist.
VARIANT_MEANINGS = {
    "": "default (alive) portrait",
    "a": "team-A tinted portrait",
    "b": "team-B tinted portrait",
    "dead": "dead/greyed portrait",
    "ult": "ultimate-ready flash",
}


def log(msg: str) -> None:
    print(f"[templates] {msg}", flush=True)


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


# ------------------------------------------------------------------- roster
def full_roster(con) -> list[dict]:
    """Every hero the game has, from the content DB — the honest denominator.
    Coverage measured against "heroes we happen to have templates for" would
    always read 100%."""
    return [{"id": r["id"], "name": r["name"], "role": r["role"]}
            for r in con.execute("SELECT id, name, role FROM heroes ORDER BY role, name")]


# ----------------------------------------------------------------- coverage
def scan_template_dir(templates_dir: str) -> dict[str, list[dict]]:
    """{hero_id: [{file, variant, sizeBytes}, ...]} for a template directory.

    Mirrors `detect.load_templates`'s filename grammar exactly
    ('<hero>.png' / '<hero>.<variant>.png', leading '_' ignored) so the
    coverage report can never claim a file detection would not load.
    """
    out: dict[str, list[dict]] = {}
    if not os.path.isdir(templates_dir):
        return out
    for fn in sorted(os.listdir(templates_dir)):
        if not fn.lower().endswith(".png") or fn.startswith("_"):
            continue
        stem = fn[:-4]
        parts = stem.split(".")
        hero_id, variant = parts[0], (".".join(parts[1:]) if len(parts) > 1 else "")
        out.setdefault(hero_id, []).append({
            "file": fn, "variant": variant,
            "meaning": VARIANT_MEANINGS.get(variant, f"custom state '{variant}'"),
            "sizeBytes": os.path.getsize(os.path.join(templates_dir, fn)),
        })
    return out


def template_set_status(templates_dir: str, roster: list[dict], *,
                        repo_root: str = db.REPO_ROOT) -> dict:
    """Coverage report for ONE broadcast package's template set.

    `covered` heroes can be detected; `uncovered` heroes will read UNKNOWN and
    are listed by name so nobody has to guess what the ceiling costs.
    """
    files = scan_template_dir(templates_dir)
    roster_ids = {h["id"] for h in roster}
    covered = sorted(roster_ids & set(files))
    uncovered = sorted(roster_ids - set(files))
    # A template file for a hero the roster doesn't know about is a real
    # problem (a typo'd filename detection will happily load as a "hero"),
    # so it is reported rather than ignored.
    unknown_files = sorted(set(files) - roster_ids)
    single_variant = [h for h in covered if len(files[h]) == 1]
    try:
        rel = os.path.relpath(templates_dir, repo_root).replace("\\", "/")
    except ValueError:
        rel = templates_dir
    return {
        "templatesDir": rel,
        "exists": os.path.isdir(templates_dir),
        "rosterSize": len(roster_ids),
        "coveredCount": len(covered),
        "coveragePct": (round(100.0 * len(covered) / len(roster_ids), 1)
                        if roster_ids else 0.0),
        "covered": covered,
        "uncovered": uncovered,
        "uncoveredNames": [h["name"] for h in roster if h["id"] in set(uncovered)],
        "variantsByHero": {h: files[h] for h in covered},
        "singleVariantHeroes": single_variant,
        "unknownTemplateFiles": unknown_files,
        "provenance": load_provenance(templates_dir),
        "note": (f"{len(uncovered)} hero(es) have no template in this package "
                 f"and will be reported UNKNOWN by detection — never guessed"
                 if uncovered else
                 "every roster hero has at least one template in this package"),
    }


def format_status(status: dict) -> str:
    lines = [
        f"  templates dir : {status['templatesDir']}"
        + ("" if status["exists"] else "  (MISSING)"),
        f"  coverage      : {status['coveredCount']}/{status['rosterSize']} heroes "
        f"({status['coveragePct']}%)",
    ]
    if status["singleVariantHeroes"]:
        lines.append(f"  single-variant : {len(status['singleVariantHeroes'])} hero(es) "
                     f"have only one template (no dead/ult-state variant)")
    if status["unknownTemplateFiles"]:
        lines.append(f"  UNKNOWN FILES : {', '.join(status['unknownTemplateFiles'])} "
                     f"— filenames that match no hero id")
    if status["uncovered"]:
        lines.append(f"  UNCOVERED ({len(status['uncovered'])}): "
                     + ", ".join(status["uncovered"][:24])
                     + (" …" if len(status["uncovered"]) > 24 else ""))
    lines.append(f"  {status['note']}")
    prov = status.get("provenance") or {}
    if prov.get("entries"):
        lines.append(f"  provenance    : {len(prov['entries'])} template file(s) "
                     f"traced to a source video/offset")
    else:
        lines.append("  provenance    : none recorded for this package")
    return "\n".join(lines)


def coverage_for_layout(con, layout_path: str) -> dict:
    """Coverage for the package a LAYOUT points at, resolving its
    `templates_dir` the same way `ingest_map` does."""
    with open(layout_path, encoding="utf-8") as f:
        layout = json.load(f)
    rel = layout.get("templates_dir")
    if not rel:
        return {"templatesDir": None, "exists": False, "coveragePct": 0.0,
                "coveredCount": 0, "rosterSize": 0, "covered": [],
                "uncovered": [], "uncoveredNames": [], "variantsByHero": {},
                "singleVariantHeroes": [], "unknownTemplateFiles": [],
                "provenance": None,
                "note": (f"layout {os.path.basename(layout_path)} declares no "
                         f"templates_dir — detection cannot run against it")}
    return template_set_status(os.path.join(db.REPO_ROOT, rel), full_roster(con))


# ---------------------------------------------------------------- provenance
def load_provenance(templates_dir: str) -> dict | None:
    path = os.path.join(templates_dir, PROVENANCE_FILENAME)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_provenance(templates_dir: str, entries: list[dict], *,
                     source_video: str | None = None,
                     source_clip: str | None = None,
                     labeled_by: str | None = None,
                     layout_id: str | None = None) -> str:
    """Record where every template in this package came from.

    Merged, not overwritten: a package built over several harvests keeps the
    history of each file. Without this, a template set is an unattributable
    blob and a bad template can never be traced back to the frame it came
    from.
    """
    os.makedirs(templates_dir, exist_ok=True)
    existing = load_provenance(templates_dir) or {"entries": [], "harvests": []}
    by_file = {e["file"]: e for e in existing.get("entries", [])}
    for e in entries:
        by_file[e["file"]] = dict(e, recordedAt=_utcnow_iso())
    existing["entries"] = [by_file[k] for k in sorted(by_file)]
    existing["harvests"] = (existing.get("harvests") or []) + [{
        "at": _utcnow_iso(), "sourceVideo": source_video,
        "sourceClip": source_clip, "labeledBy": labeled_by,
        "layoutId": layout_id, "files": len(entries),
    }]
    existing["note"] = (
        "Provenance for this broadcast package's hero templates. Every entry "
        "traces one template PNG back to the real gameplay frame it was cut "
        "from. Official hero art is NEVER a template source — it is only used "
        "to help a human label a cluster (see template_bootstrap.py).")
    path = os.path.join(templates_dir, PROVENANCE_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=1)
        f.write("\n")
    return path


# ------------------------------------------------------------ label suggestion
def official_icon_index(roster: list[dict], *,
                        assets_dir: str = OFFICIAL_ASSETS_DIR) -> dict[str, str]:
    """{hero_id: icon_path} from the committed official hero assets.

    Used ONLY to suggest a name for a cluster a human then confirms. These
    files are deliberately never copied into a template set: an official
    render is not what a broadcast HUD portrait looks like, and a template cut
    from one would look plausible while quietly degrading every detection.
    """
    out: dict[str, str] = {}
    for hero in roster:
        for name in ("icon.png", "portrait.png"):
            path = os.path.join(assets_dir, hero["id"], name)
            if os.path.exists(path):
                out[hero["id"]] = path
                break
    return out


def suggest_labels(cluster_protos: dict[str, "object"], icon_index: dict[str, str],
                   *, margin: float = LABEL_MARGIN,
                   min_score: float = LABEL_MIN_SCORE) -> dict:
    """Propose a hero label per cluster from official-icon similarity.

    `cluster_protos`: {cluster_name: prototype_gray_image}.

    Returns {"suggestions": {cluster: {hero, score, runnerUp, runnerUpScore,
    margin, confident}}, "needsReview": [...], "unmatched": [...]}.

    `confident=False` (a low margin or a weak best score) means the cluster
    MUST be reviewed by a human — a wrong label here becomes a permanently
    wrong template.
    """
    import cv2

    icons: dict[str, object] = {}
    for hero_id, path in icon_index.items():
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is not None:
            icons[hero_id] = img

    suggestions: dict[str, dict] = {}
    needs_review: list[str] = []
    unmatched: list[str] = []
    for name, proto in cluster_protos.items():
        if proto is None or not len(icons):
            unmatched.append(name)
            continue
        scored = []
        for hero_id, icon in icons.items():
            resized = cv2.resize(icon, (proto.shape[1], proto.shape[0]))
            score = float(cv2.matchTemplate(proto, resized,
                                            cv2.TM_CCOEFF_NORMED).max())
            scored.append((score, hero_id))
        scored.sort(reverse=True)
        best_score, best = scored[0]
        runner_score, runner = (scored[1] if len(scored) > 1 else (0.0, None))
        gap = round(best_score - runner_score, 4)
        confident = best_score >= min_score and gap >= margin
        suggestions[name] = {
            "hero": best if confident else None,
            "bestGuess": best,
            "score": round(best_score, 4),
            "runnerUp": runner, "runnerUpScore": round(runner_score, 4),
            "margin": gap, "confident": confident,
            "reason": (f"official icon for {best} correlates {best_score:.3f} "
                       f"(runner-up {runner} {runner_score:.3f}, margin {gap:.3f})"
                       + ("" if confident else
                          f" — below the {margin} margin / {min_score} score bar, "
                          f"so a human must label this cluster")),
        }
        if not confident:
            needs_review.append(name)
    return {"suggestions": suggestions, "needsReview": needs_review,
            "unmatched": unmatched,
            "note": ("Official hero art is used ONLY to propose a label. No "
                     "official asset is ever written into a template set.")}


# --------------------------------------------- labeled-template referencing
def labeled_template_index(*, repo_root: str = db.REPO_ROOT,
                           exclude_dir: str | None = None,
                           packages: list[str] | None = None
                           ) -> dict[str, list[str]]:
    """{hero_id: [template png paths]} from every OTHER committed package.

    These are the strongest reference this project owns: real HUD portraits
    a human already labeled, cut from real broadcasts. An official splash
    render is a weak proxy for a broadcast portrait (which is exactly why
    `suggest_labels` only ever proposes with it); a portrait cut from
    another broadcast of the same game is the same kind of image as the
    cluster being identified.

    `exclude_dir` is the package being built — matching it against itself
    would be circular.
    """
    roots = packages if packages is not None else _candidate_package_dirs(repo_root)
    excl = os.path.abspath(exclude_dir) if exclude_dir else None
    out: dict[str, list[str]] = {}
    for root in roots:
        if not os.path.isdir(root) or (excl and os.path.abspath(root) == excl):
            continue
        for hero_id, files in scan_template_dir(root).items():
            for f in files:
                out.setdefault(hero_id, []).append(os.path.join(root, f["file"]))
    return out


def _candidate_package_dirs(repo_root: str) -> list[str]:
    """Every directory that could hold a labeled template set: the root
    `templates/` plus each per-source package under it."""
    base = os.path.join(repo_root, "templates")
    dirs = [base]
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name)
            if os.path.isdir(path) and not name.startswith("_"):
                dirs.append(path)
    return dirs


def match_against_labeled(cluster_protos: dict[str, "object"],
                          template_index: dict[str, list[str]], *,
                          margin: float = LABEL_MARGIN,
                          min_score: float = LABEL_MIN_SCORE) -> dict:
    """Score each cluster against every already-labeled hero template.

    Same shape as `suggest_labels`, so the two evidence sources can be
    compared field-for-field. A hero's score is its BEST variant's score:
    packages carry alive/dead/tinted variants, and a cluster only has to
    look like one of them.
    """
    import cv2

    loaded: dict[str, list] = {}
    for hero_id, paths in template_index.items():
        for path in paths:
            img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if img is not None:
                loaded.setdefault(hero_id, []).append(img)

    suggestions: dict[str, dict] = {}
    needs_review: list[str] = []
    unmatched: list[str] = []
    for name, proto in cluster_protos.items():
        if proto is None or not loaded:
            unmatched.append(name)
            continue
        scored = []
        for hero_id, images in loaded.items():
            best = max(
                float(cv2.matchTemplate(
                    proto, cv2.resize(img, (proto.shape[1], proto.shape[0])),
                    cv2.TM_CCOEFF_NORMED).max())
                for img in images)
            scored.append((best, hero_id))
        scored.sort(reverse=True)
        best_score, best = scored[0]
        runner_score, runner = (scored[1] if len(scored) > 1 else (0.0, None))
        gap = round(best_score - runner_score, 4)
        confident = best_score >= min_score and gap >= margin
        suggestions[name] = {
            "hero": best if confident else None,
            "bestGuess": best,
            "score": round(best_score, 4),
            "runnerUp": runner, "runnerUpScore": round(runner_score, 4),
            "margin": gap, "confident": confident,
            "source": "labeled-template",
            "reason": (f"real labeled portrait of {best} correlates "
                       f"{best_score:.3f} (runner-up {runner} "
                       f"{runner_score:.3f}, margin {gap:.3f})"
                       + ("" if confident else
                          f" — below the {margin} margin / {min_score} score "
                          f"bar, so a human must label this cluster")),
        }
        if not confident:
            needs_review.append(name)
    return {"suggestions": suggestions, "needsReview": needs_review,
            "unmatched": unmatched,
            "note": ("Scored against hero templates a human already labeled "
                     "in other broadcast packages — real HUD portraits, not "
                     "official art.")}


def combine_evidence(from_templates: dict, from_icons: dict, *,
                     min_score: float, min_margin: float) -> dict:
    """Merge both label sources into ONE decision per cluster.

    The rule, in order of trust:
      1. A confident labeled-template match that the official-icon guess
         does not CONTRADICT wins. (No icon opinion is not a contradiction —
         many heroes have no usable icon, and a cluster is often a state the
         icon cannot show.)
      2. A confident labeled-template match contradicted by a confident icon
         match is a DISAGREEMENT and goes to a human. Two independent
         sources naming different heroes is exactly the case where guessing
         is worst.
      3. Icon-only confidence is never enough on its own to write a
         template. Official art does not look like a broadcast portrait, so
         the one source that can be systematically wrong never decides
         alone; it is recorded as a suggestion for review.

    Returns {cluster: {hero, accept, reasonCode, reason, evidence{...}}}.
    """
    out: dict[str, dict] = {}
    names = set(from_templates.get("suggestions") or {}) | set(
        from_icons.get("suggestions") or {})
    for name in sorted(names):
        tpl = (from_templates.get("suggestions") or {}).get(name) or {}
        icon = (from_icons.get("suggestions") or {}).get(name) or {}
        evidence = {"labeledTemplate": tpl or None, "officialIcon": icon or None}
        tpl_ok = bool(tpl.get("confident")
                      and tpl.get("score", 0) >= min_score
                      and tpl.get("margin", 0) >= min_margin)
        if tpl_ok and icon.get("confident") and icon.get("hero") \
                and icon["hero"] != tpl["hero"]:
            out[name] = {
                "hero": None, "accept": False, "reasonCode": "sources_disagree",
                "reason": (f"labeled templates say {tpl['hero']} "
                           f"({tpl['score']}), official art says "
                           f"{icon['hero']} ({icon['score']}) — a human "
                           f"decides when the evidence disagrees"),
                "evidence": evidence}
            continue
        if tpl_ok:
            out[name] = {
                "hero": tpl["hero"], "accept": True,
                "reasonCode": "labeled_template_match",
                "reason": (f"{tpl['reason']}"
                           + (f"; official art agrees ({icon['hero']})"
                              if icon.get("hero") == tpl["hero"] else
                              "; no contradicting official-art opinion")),
                "evidence": evidence}
            continue
        why = (tpl.get("reason") or "no labeled-template match")
        out[name] = {
            "hero": None, "accept": False,
            "reasonCode": ("below_floor" if tpl else "no_reference"),
            "reason": (f"not written automatically: {why}"
                       + (f". Official art suggests {icon['bestGuess']} "
                          f"({icon['score']}) — review it."
                          if icon.get("bestGuess") else "")),
            "evidence": evidence}
    return out


# ---------------------------------------------------------------- bootstrap
def bootstrap(con, templates_dir: str, *, clip: str | None = None,
              layout: dict | None = None, times: list[float] | None = None,
              layout_id: str | None = None, source_video: str | None = None,
              min_coverage_pct: float = 0.0,
              max_clusters: int = 12) -> dict:
    """Decide what a broadcast package needs, and do the safe part of it.

    1. If the package already covers enough of the roster, REUSE it and say so
       (no re-harvest, no risk of regressing a working set).
    2. Otherwise, if a clip + layout are supplied, harvest real portrait crops
       from approved gameplay, cluster them, and propose labels from official
       icons — writing candidate crops and a montage for review.
    3. Never write a hero template. That stays a human action
       (`harvest_templates.py --labels <reviewed-map.json>`).
    """
    roster = full_roster(con)
    status = template_set_status(templates_dir, roster)
    result = {"status": status, "action": None, "reused": False}

    if status["coveredCount"] and status["coveragePct"] >= min_coverage_pct:
        result["action"] = "reuse-existing"
        result["reused"] = True
        result["message"] = (
            f"reusing the existing template set: {status['coveredCount']}/"
            f"{status['rosterSize']} heroes ({status['coveragePct']}%) covered"
            + (f"; {len(status['uncovered'])} hero(es) will read UNKNOWN"
               if status["uncovered"] else ""))
        return result

    if not clip or not layout:
        result["action"] = "needs-harvest"
        result["message"] = (
            f"template coverage is {status['coveragePct']}% and no clip/layout "
            f"was supplied to harvest from — detection would report UNKNOWN for "
            f"{len(status['uncovered'])} hero(es). Supply an approved gameplay "
            f"clip to harvest candidates.")
        return result

    import harvest_templates as ht
    slot_keys = [f"{s}{i}" for s in ("a", "b") for i in range(1, 6)]
    times = times or ht.parse_times("60:600:15")
    crops = ht.collect_crops(clip, times, layout, slot_keys)
    cand_dir = os.path.join(templates_dir, "_candidates")
    os.makedirs(cand_dir, exist_ok=True)
    protos: dict[str, object] = {}
    meta: dict[str, dict] = {}
    import cv2
    for key in slot_keys:
        clusters = ht.cluster_slot(crops.get(key) or [])[:max_clusters]
        for k, c in enumerate(clusters):
            name = f"{key}_c{k}"
            fn = f"{name}_n{len(c['members'])}.png"
            cv2.imwrite(os.path.join(cand_dir, fn), c["proto"])
            ts = sorted(m[0] for m in c["members"])
            meta[name] = {"file": fn, "count": len(c["members"]),
                          "tFirst": ts[0], "tLast": ts[-1], "slot": key}
            protos[name] = c["proto_gray"]

    labels = suggest_labels(protos, official_icon_index(roster))
    with open(os.path.join(cand_dir, "clusters.json"), "w", encoding="utf-8") as f:
        json.dump({"clusters": meta, "suggestions": labels["suggestions"],
                   "needsReview": labels["needsReview"],
                   "sourceClip": clip, "sourceVideo": source_video,
                   "layoutId": layout_id, "generatedAt": _utcnow_iso(),
                   "note": labels["note"]}, f, indent=1)
        f.write("\n")
    result["action"] = "harvested-candidates"
    result["clusters"] = len(meta)
    result["suggestions"] = labels["suggestions"]
    result["needsReview"] = labels["needsReview"]
    result["candidatesDir"] = os.path.relpath(cand_dir, db.REPO_ROOT).replace("\\", "/")
    result["message"] = (
        f"harvested {len(meta)} cluster(s) from {len(times)} sampled frame(s); "
        f"{len(labels['needsReview'])} need a human label. Review "
        f"{result['candidatesDir']}/clusters.json, then run "
        f"pipeline/harvest_templates.py --labels <your-map.json> to write "
        f"templates.")
    return result


# ------------------------------------------------------------- auto-labeling
def auto_label(con, templates_dir: str, *, clip: str, layout: dict,
               times: list[float] | None = None,
               layout_id: str | None = None, source_video: str | None = None,
               min_score: float = 0.55, min_margin: float = 0.12,
               min_cluster_members: int = 4,
               variants: int = DEFAULT_VARIANTS,
               max_clusters: int = 12,
               labeled_by: str = "auto-label",
               dry_run: bool = False) -> dict:
    """Harvest a package's missing hero templates WITHOUT a human labeler.

    This is the one path in this repo that writes a hero template without a
    person naming it, so every safeguard is deliberate:

      * ADDITIVE ONLY. A hero the package already covers is never touched,
        never overwritten, never deleted. (`harvest_templates.stage_labels`
        clears the directory first — correct for a human doing a full
        reviewed pass, catastrophic for an unattended one that happened to
        label fewer heroes.)
      * Two independent evidence sources, and the weak one cannot decide
        alone (see `combine_evidence`).
      * Score AND margin floors, plus a minimum cluster size: a cluster seen
        in a couple of frames is as likely to be a killcam or a transition
        as a portrait.
      * One hero per write. If two clusters both claim the same hero, the
        higher-scoring one wins and the other is left for review — the same
        hero cannot legitimately be two different portraits in one package.
      * Everything not written is reported with the reason, so the leftovers
        are a review list, not a silence.
      * `dry_run=True` decides and reports without writing a single file.

    Returns {"written": [...], "skipped": [...], "decisions": {...},
             "status": <coverage after>, "candidatesDir": ...}.
    """
    import cv2
    import harvest_templates as ht

    roster = full_roster(con)
    before = template_set_status(templates_dir, roster)
    already = set(before.get("covered") or [])
    slot_keys = [f"{s}{i}" for s in ("a", "b") for i in range(1, 6)]
    times = times or ht.parse_times("60:600:15")
    crops = ht.collect_crops(clip, times, layout, slot_keys)

    protos: dict[str, object] = {}
    members: dict[str, list] = {}
    meta: dict[str, dict] = {}
    for key in slot_keys:
        for k, c in enumerate(ht.cluster_slot(crops.get(key) or [])[:max_clusters]):
            name = f"{key}_c{k}"
            protos[name] = c["proto_gray"]
            members[name] = c["members"]
            ts = sorted(m[0] for m in c["members"])
            meta[name] = {"slot": key, "count": len(c["members"]),
                          "tFirst": ts[0], "tLast": ts[-1]}

    from_templates = match_against_labeled(
        protos, labeled_template_index(exclude_dir=templates_dir),
        margin=min_margin, min_score=min_score)
    from_icons = suggest_labels(protos, official_icon_index(roster))
    decisions = combine_evidence(from_templates, from_icons,
                                 min_score=min_score, min_margin=min_margin)

    # Resolve competing claims BEFORE writing anything: best score wins the
    # hero, everything else becomes a review item with the reason.
    claims: dict[str, list[tuple[float, str]]] = {}
    for name, d in decisions.items():
        if d["accept"] and meta.get(name, {}).get("count", 0) >= min_cluster_members:
            score = ((d.get("evidence") or {}).get("labeledTemplate") or {}
                     ).get("score", 0.0)
            claims.setdefault(d["hero"], []).append((score, name))
    winners: dict[str, str] = {}
    for hero, rows in claims.items():
        rows.sort(reverse=True)
        winners[rows[0][1]] = hero

    written: list[dict] = []
    skipped: list[dict] = []
    cand_dir = os.path.join(templates_dir, "_candidates")
    for name in sorted(decisions):
        d = decisions[name]
        info = meta.get(name, {})
        count = info.get("count", 0)
        hero = winners.get(name)
        if hero is None:
            reason = d["reason"]
            code = d["reasonCode"]
            if d["accept"] and count < min_cluster_members:
                code = "cluster_too_small"
                reason = (f"{d['hero']} matched, but this cluster has only "
                          f"{count} member frame(s) (< {min_cluster_members}) "
                          f"— too thin to trust as a portrait")
            elif d["accept"]:
                code = "duplicate_hero_claim"
                reason = (f"another cluster matched {d['hero']} more "
                          f"strongly; this one is left for review")
            skipped.append({"cluster": name, "reasonCode": code,
                            "reason": reason, "members": count,
                            "bestGuess": ((d.get("evidence") or {})
                                          .get("labeledTemplate") or {}
                                          ).get("bestGuess")})
            continue
        if hero in already:
            skipped.append({"cluster": name, "reasonCode": "already_covered",
                            "reason": (f"{hero} already has a template in this "
                                       f"package — never overwritten"),
                            "members": count})
            continue
        files = _write_cluster_variants(
            cv2, ht, templates_dir, hero, members[name], variants,
            dry_run=dry_run)
        already.add(hero)
        written.append({"cluster": name, "hero": hero, "files": files,
                        "members": count,
                        "score": ((d.get("evidence") or {})
                                  .get("labeledTemplate") or {}).get("score"),
                        "reason": d["reason"]})

    if not dry_run:
        if written:
            write_provenance(
                templates_dir,
                [{"file": f, "hero": w["hero"], "cluster": w["cluster"],
                  "sourceClip": clip, "sourceVideo": source_video,
                  "tFirst": meta.get(w["cluster"], {}).get("tFirst"),
                  "tLast": meta.get(w["cluster"], {}).get("tLast"),
                  "labeledBy": labeled_by, "autoLabeled": True,
                  "matchScore": w["score"], "evidence": w["reason"]}
                 for w in written for f in w["files"]],
                source_video=source_video, source_clip=clip,
                labeled_by=labeled_by, layout_id=layout_id)
        # Written even when NOTHING was labeled: the skipped list is the
        # human's review list, and "nothing could be decided" is exactly
        # when they need to see the clusters and the reasons.
        _write_review_manifest(cand_dir, meta, decisions, written, skipped,
                               clip=clip, source_video=source_video,
                               layout_id=layout_id)

    after = template_set_status(templates_dir, roster)
    return {
        "written": written, "skipped": skipped, "decisions": decisions,
        "clusters": len(meta), "dryRun": dry_run,
        "coverageBefore": before["coveragePct"],
        "coverageAfter": after["coveragePct"],
        "status": after,
        "candidatesDir": (os.path.relpath(cand_dir, db.REPO_ROOT)
                          .replace("\\", "/") if not dry_run else None),
        "message": (
            f"auto-labeled {len(written)} hero(es) from {len(meta)} cluster(s); "
            f"coverage {before['coveragePct']}% -> {after['coveragePct']}%; "
            f"{len(skipped)} cluster(s) left for review"
            + (" (dry run — nothing written)" if dry_run else "")),
    }


def _write_cluster_variants(cv2, ht, templates_dir: str, hero: str,
                            cluster_members: list, variants: int, *,
                            dry_run: bool) -> list[str]:
    """Write up to `variants` maximally-different crops of one cluster as
    `<hero>.png` / `<hero>.v1.png` …, reusing harvest_templates' own variant
    picker so an auto-labeled file is cut exactly like a human-labeled one."""
    crops = [m[1] for m in cluster_members]
    grays = [m[2] for m in cluster_members]
    order = sorted(range(len(crops)), key=lambda i: -ht.sharpness(grays[i]))
    chosen = [order[0]]
    while len(chosen) < variants and len(chosen) < len(order):
        best_i, best_sim = None, 2.0
        for i in order:
            if i in chosen:
                continue
            sim = max(ht.corr(grays[c], grays[i]) for c in chosen)
            if sim < best_sim:
                best_sim, best_i = sim, i
        if best_i is None or best_sim > 0.995:
            break
        chosen.append(best_i)
    out: list[str] = []
    if not dry_run:
        os.makedirs(templates_dir, exist_ok=True)
    for n, idx in enumerate(chosen):
        fname = f"{hero}.png" if n == 0 else f"{hero}.v{n}.png"
        out.append(fname)
        if not dry_run:
            cv2.imwrite(os.path.join(templates_dir, fname), crops[idx])
    return out


def _write_review_manifest(cand_dir: str, meta: dict, decisions: dict,
                           written: list, skipped: list, *, clip: str,
                           source_video: str | None,
                           layout_id: str | None) -> None:
    """Everything the auto-labeler decided, written next to the package so a
    human can audit it after the fact — which clusters became templates, and
    every cluster that did not, with the reason."""
    os.makedirs(cand_dir, exist_ok=True)
    payload = {
        "generatedAt": _utcnow_iso(), "sourceClip": clip,
        "sourceVideo": source_video, "layoutId": layout_id,
        "clusters": meta, "decisions": decisions,
        "written": written, "skipped": skipped,
        "note": ("Auto-labeling record. Every 'written' entry was accepted by "
                 "labeled-template similarity above the score+margin floors "
                 "with no contradicting official-art opinion; every 'skipped' "
                 "entry names why it was NOT written. Official hero art is "
                 "never itself written into a template set."),
    }
    with open(os.path.join(cand_dir, "auto_labels.json"), "w",
              encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
        f.write("\n")


def format_auto_label(result: dict) -> str:
    lines = [f"  {result.get('message')}"]
    for w in result.get("written") or []:
        lines.append(f"    WROTE  {w['hero']:10} <- {w['cluster']} "
                     f"(score {w.get('score')}, {w['members']} frames, "
                     f"{len(w['files'])} variant(s))")
    for s in (result.get("skipped") or [])[:20]:
        lines.append(f"    review {s['cluster']:10} [{s['reasonCode']}] "
                     f"{s['reason'][:100]}")
    extra = len(result.get("skipped") or []) - 20
    if extra > 0:
        lines.append(f"    … and {extra} more cluster(s) for review")
    return "\n".join(lines)


# --------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="hero-template coverage + bootstrap for a broadcast package")
    ap.add_argument("--templates-dir", help="package template dir "
                    "(default: resolved from --layout)")
    ap.add_argument("--layout", help="layout json whose templates_dir to use")
    ap.add_argument("--all-layouts", action="store_true",
                    help="report coverage for every committed layout")
    ap.add_argument("--clip", help="approved gameplay clip to harvest from")
    ap.add_argument("--times", default="60:600:15",
                    help="'start:end:step' or comma list of clip seconds")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    con = db.connect()
    db.init_schema(con)
    try:
        if args.all_layouts:
            layouts_dir = os.path.join(db.REPO_ROOT, "layouts")
            reports = []
            for fn in sorted(os.listdir(layouts_dir)):
                if not fn.endswith(".json"):
                    continue
                rep = coverage_for_layout(con, os.path.join(layouts_dir, fn))
                rep["layout"] = fn
                reports.append(rep)
            if args.json:
                print(json.dumps(reports, indent=1))
            else:
                for rep in reports:
                    print(f"[templates] {rep['layout']}:")
                    print(format_status(rep))
            return 0

        if args.layout and not args.templates_dir:
            rep = coverage_for_layout(con, args.layout)
            if args.json:
                print(json.dumps(rep, indent=1))
            else:
                print(format_status(rep))
            return 0 if rep.get("exists") else 1

        tdir = args.templates_dir or os.path.join(db.REPO_ROOT, "templates")
        layout = None
        if args.layout:
            import capture
            layout = capture.load_layout(args.layout)
        result = bootstrap(con, tdir, clip=args.clip, layout=layout,
                           times=(None if not args.clip else
                                  __import__("harvest_templates").parse_times(args.times)))
        if args.json:
            print(json.dumps(result, indent=1, default=str))
        else:
            print(f"[templates] action: {result['action']}")
            print(format_status(result["status"]))
            print(f"  {result['message']}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
