#!/usr/bin/env python3
"""
hero_coverage.py — one honest answer to "is this thing ready?"

Readiness for hero detection is not one number, and every previous attempt
to make it one has lied in a different direction. Four separate questions
have to be answered per hero, per broadcast package, and a package is only
as good as the weakest of them:

  1. **Covered?**   Is there a template at all? No template means every
                    frame of that hero reads UNKNOWN, forever.
  2. **Sound?**     Does the template pass the quality gate, or is it a
                    fade-to-black that got harvested by accident?
                    (`template_quality`)
  3. **Traceable?** Does provenance say which frame it came from? Without
                    that, nothing about it can be validated or audited.
  4. **Proven?**    Has it been scored on held-out frames it never saw?
                    (`template_validate`)

A hero that is covered but unproven is not ready. A hero that is covered,
sound and traceable but has no held-out evidence is not ready either — it
is *unblocked*, which is a different and much weaker statement. This module
computes all four and refuses to collapse them into a single percentage
without saying which one it is reporting.

It also names the **blocker** and the **next action** for every hero that
is not ready, because a coverage report whose output is "44 heroes
missing" tells an operator nothing they did not already know.

`readiness_line()` produces the string the desktop UI shows. It says
`8/52 covered · 8 validated` — never `52/52` unless all four questions are
answered yes for all 52 heroes, which no package in this repository
currently achieves and which this module will not pretend.

CLI:
  python3 pipeline/hero_coverage.py
  python3 pipeline/hero_coverage.py --layout owcs_jksix_qwc --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import template_bootstrap as tb  # noqa: E402

LAYOUTS_DIR = os.path.join(db.REPO_ROOT, "layouts")
# Where a validation report for a package is looked for. Written by
# `template_validate.py --out`, committed so the desktop UI and CI can both
# read the same evidence without re-running a 2,000-trial scoring pass.
VALIDATION_DIRNAME = "validation"

READY, UNPROVEN, BLOCKED, MISSING = "READY", "UNPROVEN", "BLOCKED", "MISSING"


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def validation_report_path(layout_id: str, *,
                           repo_root: str = db.REPO_ROOT) -> str:
    return os.path.join(repo_root, "reports", VALIDATION_DIRNAME,
                        f"{layout_id}.json")


def load_validation(layout_id: str, *, repo_root: str = db.REPO_ROOT) -> dict:
    path = validation_report_path(layout_id, repo_root=repo_root)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _quality_by_file(templates_dir: str, provenance: dict | None) -> dict:
    """Quality verdict per template file, or {} if cv2 is unavailable.

    Coverage reporting must survive on a machine without OpenCV — the
    desktop control room asks for this on every page load, and a missing
    optional dependency should degrade the report, not break the page.
    """
    try:
        import template_quality as tq
    except Exception:
        return {}
    try:
        audit = tq.audit_set(templates_dir, provenance=provenance)
    except Exception:
        return {}
    return {fn: r["verdict"] for fn, r in audit["results"].items()}


def layout_coverage(con, layout_id: str, *,
                    repo_root: str = db.REPO_ROOT) -> dict:
    """The four-question readiness report for one broadcast package."""
    layout_path = os.path.join(LAYOUTS_DIR, f"{layout_id}.json")
    layout = {}
    if os.path.exists(layout_path):
        with open(layout_path, encoding="utf-8") as f:
            layout = json.load(f)

    roster = tb.full_roster(con)
    rel = layout.get("templates_dir")
    templates_dir = os.path.join(repo_root, rel) if rel else None
    status = (tb.template_set_status(templates_dir, roster,
                                     repo_root=repo_root)
              if templates_dir else None)
    files = tb.scan_template_dir(templates_dir) if templates_dir else {}
    provenance = tb.load_provenance(templates_dir) if templates_dir else None
    prov_files = {e.get("file") for e in (provenance or {}).get("entries", [])}
    quality = _quality_by_file(templates_dir, provenance) if templates_dir else {}
    validation = load_validation(layout_id, repo_root=repo_root)
    verdicts = {h: e.get("verdict")
                for h, e in (validation.get("heroes") or {}).items()}

    heroes: list[dict] = []
    for hero in roster:
        hid = hero["id"]
        variants = files.get(hid) or []
        names = [v["file"] for v in variants]
        traced = [n for n in names if n in prov_files]
        bad = [n for n in names if quality.get(n) == "REJECT"]
        marginal = [n for n in names if quality.get(n) == "REVIEW"]
        verdict = verdicts.get(hid)

        if not variants:
            state, blocker, action = MISSING, (
                f"no template — every frame of {hero['name']} reads UNKNOWN"
            ), "harvest this hero from a broadcast that contains it"
        elif bad:
            state, blocker, action = BLOCKED, (
                f"{len(bad)} template(s) fail the quality gate "
                f"({', '.join(sorted(bad)[:3])})"
            ), "re-harvest, or delete the failing template files"
        elif len(traced) != len(names):
            # UNPROVEN, not BLOCKED. A template harvested before provenance
            # existed is very likely fine — it is simply unprovable, and
            # calling that "broken" would push an operator to delete working
            # assets. BLOCKED is reserved for evidence that something IS
            # wrong: a failing quality gate, or a held-out wrong answer.
            state, blocker, action = UNPROVEN, (
                f"{len(names) - len(traced)} of {len(names)} template(s) have "
                f"no provenance, so separation from their own source frames "
                f"cannot be shown and they cannot be validated"
            ), "re-forge this package with pipeline/template_forge.py"
        elif verdict == "VALIDATED":
            state, blocker, action = READY, None, None
        elif verdict in ("FAILED",):
            state, blocker, action = BLOCKED, (
                "held-out validation caught a confident WRONG answer"
            ), "review the validation report and re-harvest this hero"
        else:
            state = UNPROVEN
            blocker = ("no held-out validation evidence"
                       if verdict is None else
                       f"held-out validation says {verdict}")
            action = ("capture footage containing this hero and run "
                      "pipeline/template_validate.py")

        heroes.append({
            "id": hid, "name": hero["name"], "role": hero["role"],
            "state": state,
            "templates": names,
            "variantStates": sorted({v["variant"] or "default"
                                     for v in variants}),
            "traced": len(traced), "failingQuality": bad,
            "marginalQuality": marginal,
            "validation": verdict,
            "validationDetail": (validation.get("heroes") or {}).get(hid),
            "blocker": blocker, "action": action,
        })

    counts = {s: sum(1 for h in heroes if h["state"] == s)
              for s in (READY, UNPROVEN, BLOCKED, MISSING)}
    covered = sum(1 for h in heroes if h["templates"])
    return {
        "layoutId": layout_id,
        "layoutExists": bool(layout),
        "templatesDir": rel,
        "rosterSize": len(roster),
        "covered": covered,
        "validated": counts[READY],
        "counts": counts,
        "heroes": heroes,
        "detectorProfile": {
            "portraitRoi": layout.get("portrait_roi"),
            "unknownFloor": layout.get("unknown_floor"),
            "minMargin": layout.get("min_margin"),
        },
        "validationRun": {
            "generatedAt": validation.get("generatedAt"),
            "evidence": validation.get("evidence"),
            "falseMatch": (validation.get("leaveOneOut")
                           or validation.get("falseMatch") or {}),
            "confusablePairs": (validation.get("leaveOneOut") or {})
            .get("confusablePairs", []),
        } if validation else None,
        "provenanceEntries": len(prov_files),
        "note": (status or {}).get("note"),
        "readiness": readiness_line(covered, counts[READY], len(roster)),
    }


def readiness_line(covered: int, validated: int, roster_size: int) -> str:
    """The one-line status the UI shows. Never rounds up.

    `covered` and `validated` are reported separately and always both, so
    that a package cannot present a coverage number as if it were a
    reliability number.
    """
    if roster_size and covered == roster_size and validated == roster_size:
        return f"{roster_size}/{roster_size} heroes covered and validated"
    return (f"{covered}/{roster_size} heroes covered · "
            f"{validated} validated on held-out frames")


def all_layouts(con, *, repo_root: str = db.REPO_ROOT) -> dict:
    """Readiness across every committed layout, plus the honest headline."""
    reports = []
    for fn in sorted(os.listdir(os.path.join(repo_root, "layouts"))):
        if not fn.endswith(".json"):
            continue
        reports.append(layout_coverage(con, fn[:-5], repo_root=repo_root))
    roster_size = reports[0]["rosterSize"] if reports else 0
    detectable = sorted({h["id"] for r in reports for h in r["heroes"]
                         if h["templates"]})
    proven = sorted({h["id"] for r in reports for h in r["heroes"]
                     if h["state"] == READY})
    best = max(reports, key=lambda r: (r["validated"], r["covered"])) \
        if reports else None
    return {
        "generatedAt": _utcnow_iso(),
        "rosterSize": roster_size,
        "layouts": reports,
        # Union across packages: a hero is "detectable somewhere" if ANY
        # package can see it. Deliberately reported next to the per-layout
        # numbers and never instead of them — detection runs against one
        # package at a time, so the union is an upper bound on capability,
        # not a description of any single broadcast.
        "detectableSomewhere": detectable,
        "provenSomewhere": proven,
        "headline": (f"{len(detectable)}/{roster_size} heroes have a template "
                     f"in at least one package · {len(proven)} proven on "
                     f"held-out frames"),
        "bestLayout": best["layoutId"] if best else None,
        "bestLayoutReadiness": best["readiness"] if best else None,
        "note": ("Per-layout numbers are what a real broadcast run gets. "
                 "The union across layouts is an upper bound on what the "
                 "repository can detect anywhere, not what any one package "
                 "can detect."),
    }


def format_layout(report: dict) -> str:
    c = report["counts"]
    lines = [
        f"  {report['layoutId']}: {report['readiness']}",
        f"    ready {c[READY]} · unproven {c[UNPROVEN]} · "
        f"blocked {c[BLOCKED]} · missing {c[MISSING]}",
    ]
    if report["detectorProfile"]["portraitRoi"]:
        lines.append(f"    portrait ROI "
                     f"{report['detectorProfile']['portraitRoi']}, "
                     f"floor {report['detectorProfile']['unknownFloor']}")
    for hero in report["heroes"]:
        if hero["state"] in (READY,):
            continue
        if hero["state"] == MISSING:
            continue          # summarised below; listing 44 helps nobody
        lines.append(f"    {hero['state']:<9} {hero['id']:<10} "
                     f"{hero['blocker']}")
    missing = [h["id"] for h in report["heroes"] if h["state"] == MISSING]
    if missing:
        lines.append(f"    MISSING   {len(missing)} hero(es): "
                     + ", ".join(missing[:20])
                     + (" …" if len(missing) > 20 else ""))
    fm = (report.get("validationRun") or {}).get("falseMatch") or {}
    if fm.get("note"):
        lines.append(f"    false match: {fm['note']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="52-hero detection readiness per broadcast package")
    ap.add_argument("--layout", help="one layout id (default: all)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)

    con = db.connect()
    db.init_schema(con)
    try:
        if args.layout:
            report = layout_coverage(con, args.layout)
        else:
            report = all_layouts(con)
        if args.out:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)),
                        exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=1)
                f.write("\n")
        if args.json:
            print(json.dumps(report, indent=1))
        elif args.layout:
            print(format_layout(report))
        else:
            print(f"[coverage] {report['headline']}")
            for lay in report["layouts"]:
                print(format_layout(lay))
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
