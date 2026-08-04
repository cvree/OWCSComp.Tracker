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
import site_paths  # noqa: E402  (cross-drive-safe relative paths)

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
    rel = site_paths.site_relpath(templates_dir, repo_root)
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
    result["candidatesDir"] = site_paths.site_relpath(cand_dir, db.REPO_ROOT)
    result["message"] = (
        f"harvested {len(meta)} cluster(s) from {len(times)} sampled frame(s); "
        f"{len(labels['needsReview'])} need a human label. Review "
        f"{result['candidatesDir']}/clusters.json, then run "
        f"pipeline/harvest_templates.py --labels <your-map.json> to write "
        f"templates.")
    return result


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
