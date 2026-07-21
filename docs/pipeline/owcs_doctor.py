#!/usr/bin/env python3
"""
owcs_doctor.py  --  read-only preflight / "doctor" for the OWCS Comp Tracker.

WHAT IT DOES
    * Checks the things a run needs (DB, folders, layout, run artifacts,
      frames_raw, hero_crops.html, labels.json, candidate templates,
      candidate reports).
    * Prints, for the FIRST missing thing, the exact command to run next.
    * Recommends a single "next step" for the current state of the tree.

WHAT IT DOES NOT DO (by design -- keep it safe/additive)
    * Never writes to the DB. Never promotes comps.
    * Never imports the detector / capture / FACEIT modules.
    * Never edits layouts. Never OCRs.
    * Only reads file/dir existence and (for labels) counts entries.
    * `--emit-banner` writes ONE new best-effort html fragment into the run
      folder; it is off by default and touches no existing file.

USAGE
    py pipeline/owcs_doctor.py --run <run_id> --layout <layout.json>
    py pipeline/owcs_doctor.py --run owcs-8c105lnzlam_000600_000630 \
        --layout layouts/owcs_8c105lnzlam.json
    py pipeline/owcs_doctor.py --run <run_id> --layout <layout.json> --json
    py pipeline/owcs_doctor.py --run <run_id> --layout <layout.json> --next-step-only

EXIT CODE
    0  -> everything the ladder checks is present ("ready ...").
    1  -> at least one thing is missing (an action is recommended).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# CONFIG  --  EDIT HERE if your real tree uses different names/paths.
# Everything the doctor knows about the repo layout lives in this block so you
# never have to hunt through the logic below.
# ---------------------------------------------------------------------------

# How many labelled crops we consider "enough" before templates are worth
# exporting. BLUEPRINT Phase 3 suggests ~20-40.
MIN_LABELS = 20

# Repo-relative locations. {run} is the run id, {layout} the --layout value.
PATHS = {
    "db":            "data/owcs.sqlite",
    "eval_dir":      "data/eval",
    "labels":        "data/eval/labels.json",
    "layouts_dir":   "layouts",
    "reports_dir":   "reports",
    "templates_dir": "templates",
    "candidates_dir": "templates/candidates",
    "pipeline_dir":  "pipeline",
    # per-run artifacts (relative to repo root)
    "run_dir":       "reports/auto/{run}",
    "run_index":     "reports/auto/{run}/index.html",
    # frames_raw and the crop report can live under a couple of names; the
    # doctor accepts the first that exists and tells you which it found.
    "frames_raw":    ["reports/auto/{run}/frames_raw", "reports/auto/{run}/frames", "work/{run}/frames_raw"],
    "layout_debug":  ["reports/auto/{run}/layout.html", "reports/auto/{run}/layout_debug.html"],
    "hero_crops":    ["reports/auto/{run}/hero_crops.html", "reports/auto/{run}/crops.html"],
    "cand_report":   ["reports/candidates/{run}/index.html", "reports/candidates/index.html",
                      "templates/candidates/eval_report.json", "templates/candidates/report.html"],
    "cand_dryrun":   ["reports/candidates/{run}/dry_run.json", "reports/candidates/{run}/dry_run.html",
                      "templates/candidates/dry_run.json"],
}

# Frame image extensions that count as "frames present".
FRAME_EXTS = (".png", ".jpg", ".jpeg", ".webp")

# Exact "next command" strings. {run}/{layout}/{source}/{start}/{end} are
# filled from the run id. These are best-effort defaults inferred from
# HANDOFF.md + BLUEPRINT.md -- correct the SCRIPT NAMES/FLAGS to match your
# real pipeline if they differ. They are only *printed*, never executed.
COMMANDS = {
    "init_db":          "py pipeline\\video_pipeline.py --demo   # initializes data/owcs.sqlite (fixture demo)",
    "run_capture":      "py pipeline\\run_owcs_auto.py --source {source} --start {start} --end {end} --every 30",
    "calibration":      "py pipeline\\build_layout_debug.py --run {run} --layout {layout} --from-frames {frames}",
    "fix_layout":       "edit {layout}  (nudge the 10 slot boxes) then re-run build_layout_debug.py --from-frames {frames}",
    "gen_crops":        "py pipeline\\build_crop_report.py --run {run} --layout {layout}",
    "label_crops":      "python -m http.server 8000   then open  http://localhost:8000/{crops_rel}   and label crops (writes data/eval/labels.json)",
    "export_templates": "py pipeline\\build_hero_templates.py --from-labels data/eval/labels.json --out templates/candidates",
    "cand_eval":        "py pipeline\\eval_detection.py --labels data/eval/labels.json --templates templates/candidates",
    "cand_dryrun":      "py pipeline\\eval_detection.py --templates templates/candidates --run {run} --dry-run",
    "ready":            "(nothing) -- calibration inputs look complete; template approval is a future, manual step.",
}

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class Paths:
    root: str
    run: str
    layout: str
    resolved: dict = field(default_factory=dict)

    def p(self, key: str) -> str:
        """First resolved path for a key (or the sole path)."""
        v = self.resolved[key]
        return v[0] if isinstance(v, list) else v

    def all(self, key: str) -> list:
        v = self.resolved[key]
        return v if isinstance(v, list) else [v]


@dataclass
class CheckResult:
    id: str
    label: str
    ok: bool
    detail: str
    path: str


@dataclass
class Recommendation:
    step_id: str
    human: str
    command: str
    reason: str


# ---------------------------------------------------------------------------
# Path + run-id resolution
# ---------------------------------------------------------------------------


def parse_run_id(run_id: str):
    """
    'owcs-8c105lnzlam_000600_000630' -> ('owcs-8c105lnzlam', '0:06:00', '0:06:30')
    Returns (source, start_hms, end_hms); any part may be None if unparseable.
    """
    source, start, end = run_id, None, None
    m = re.match(r"^(.*?)_(\d{6})_(\d{6})$", run_id)
    if m:
        source = m.group(1)
        start = _hms(m.group(2))
        end = _hms(m.group(3))
    return source, start, end


def _hms(six: str) -> str:
    h, mnt, s = int(six[0:2]), int(six[2:4]), int(six[4:6])
    return f"{h}:{mnt:02d}:{s:02d}"


def resolve_paths(root: str, run_id: str, layout: str) -> Paths:
    root = os.path.abspath(root)
    resolved = {}
    for key, tmpl in PATHS.items():
        if isinstance(tmpl, list):
            resolved[key] = [os.path.join(root, t.format(run=run_id)) for t in tmpl]
        else:
            resolved[key] = os.path.join(root, tmpl.format(run=run_id))
    # layout is passed explicitly (may be absolute or repo-relative)
    resolved["layout"] = layout if os.path.isabs(layout) else os.path.join(root, layout)
    return Paths(root=root, run=run_id, layout=layout, resolved=resolved)


# ---------------------------------------------------------------------------
# Low-level existence helpers
# ---------------------------------------------------------------------------


def _first_existing(paths: list) -> Optional[str]:
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def _dir_has_frames(d: str) -> bool:
    if not os.path.isdir(d):
        return False
    for name in os.listdir(d):
        if name.lower().endswith(FRAME_EXTS):
            return True
    return False


def _dir_nonempty(d: str) -> bool:
    return os.path.isdir(d) and any(True for _ in os.listdir(d))


def _label_count(labels_path: str) -> int:
    """Best-effort count of labelled entries in labels.json. 0 if missing/bad."""
    if not os.path.isfile(labels_path):
        return 0
    try:
        with open(labels_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return 0
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for k in ("labels", "frames", "entries", "items"):
            if isinstance(data.get(k), (list, dict)):
                return len(data[k])
        return len(data)
    return 0


# ---------------------------------------------------------------------------
# The check ladder (order == pipeline order == recommendation priority)
# ---------------------------------------------------------------------------


def run_checks(paths: Paths) -> list:
    r: list = []

    # 0. repo skeleton
    missing_dirs = [d for d in ("pipeline_dir", "layouts_dir", "reports_dir",
                                "templates_dir", "eval_dir")
                    if not os.path.isdir(paths.p(d))]
    r.append(CheckResult(
        "folders", "Required folders exist",
        ok=not missing_dirs,
        detail="all present" if not missing_dirs
        else "missing: " + ", ".join(os.path.relpath(paths.p(d), paths.root) for d in missing_dirs),
        path=paths.root))

    # 1. DB
    db = paths.p("db")
    r.append(CheckResult("db", "Database initialized", os.path.isfile(db),
                         "found" if os.path.isfile(db) else "not found",
                         os.path.relpath(db, paths.root)))

    # 2. layout file
    lay = paths.resolved["layout"]
    r.append(CheckResult("layout", "Layout file exists", os.path.isfile(lay),
                         "found" if os.path.isfile(lay) else "not found",
                         os.path.relpath(lay, paths.root)))

    # 3. run dir / artifacts
    run_dir = paths.p("run_dir")
    r.append(CheckResult("run", "Run artifacts exist", os.path.isdir(run_dir),
                         "run folder present" if os.path.isdir(run_dir) else "no run folder",
                         os.path.relpath(run_dir, paths.root)))

    # 4. frames_raw
    frames = _first_existing(paths.all("frames_raw"))
    frames_ok = frames is not None and _dir_has_frames(frames)
    r.append(CheckResult("frames_raw", "frames_raw has frames", frames_ok,
                         f"frames in {os.path.relpath(frames, paths.root)}" if frames_ok
                         else "no frame images found",
                         os.path.relpath(frames, paths.root) if frames else
                         os.path.relpath(paths.all("frames_raw")[0], paths.root)))

    # 5. calibration / layout-debug report
    cal = _first_existing(paths.all("layout_debug"))
    r.append(CheckResult("calibration", "Calibration report exists", cal is not None,
                         os.path.relpath(cal, paths.root) if cal else "not generated",
                         os.path.relpath(cal, paths.root) if cal else
                         os.path.relpath(paths.all("layout_debug")[0], paths.root)))

    # 6. hero_crops.html
    crops = _first_existing(paths.all("hero_crops"))
    r.append(CheckResult("hero_crops", "hero_crops.html exists", crops is not None,
                         os.path.relpath(crops, paths.root) if crops else "not generated",
                         os.path.relpath(crops, paths.root) if crops else
                         os.path.relpath(paths.all("hero_crops")[0], paths.root)))

    # 7. labels.json (+ count)
    labels = paths.p("labels")
    n = _label_count(labels)
    labels_ok = n >= MIN_LABELS
    if not os.path.isfile(labels):
        detail = "labels.json missing"
    elif labels_ok:
        detail = f"{n} labels (>= {MIN_LABELS})"
    else:
        detail = f"only {n} labels (< {MIN_LABELS})"
    r.append(CheckResult("labels", "labels.json ready", labels_ok, detail,
                         os.path.relpath(labels, paths.root)))

    # 8. templates/candidates
    cand = paths.p("candidates_dir")
    cand_ok = _dir_nonempty(cand)
    r.append(CheckResult("candidates", "templates/candidates populated", cand_ok,
                         "has candidate templates" if cand_ok else "empty or missing",
                         os.path.relpath(cand, paths.root)))

    # 9. candidate eval report
    crep = _first_existing(paths.all("cand_report"))
    r.append(CheckResult("cand_report", "Candidate reports exist", crep is not None,
                         os.path.relpath(crep, paths.root) if crep else "not generated",
                         os.path.relpath(crep, paths.root) if crep else
                         os.path.relpath(paths.all("cand_report")[0], paths.root)))

    # 10. candidate dry-run detection
    dry = _first_existing(paths.all("cand_dryrun"))
    r.append(CheckResult("cand_dryrun", "Candidate dry-run detection done", dry is not None,
                         os.path.relpath(dry, paths.root) if dry else "not run",
                         os.path.relpath(dry, paths.root) if dry else
                         os.path.relpath(paths.all("cand_dryrun")[0], paths.root)))

    return r


# ---------------------------------------------------------------------------
# Next-step recommender  (first failing rung of the ladder wins)
# ---------------------------------------------------------------------------

# Maps a failing check id -> (human step, COMMANDS key, reason).
_STEP_FOR = {
    "folders":     ("initialize project folders", "init_db",
                    "core folders are missing; running the demo scaffolds them"),
    "db":          ("initialize DB", "init_db",
                    "data/owcs.sqlite not found"),
    "layout":      ("fix layout calibration", "fix_layout",
                    "the layout JSON this run needs does not exist"),
    "run":         ("run auto capture", "run_capture",
                    "no run folder yet -- capture frames first"),
    "frames_raw":  ("run auto capture", "run_capture",
                    "run folder exists but has no extracted frames"),
    "calibration": ("run calibration report", "calibration",
                    "frames exist but layout boxes are not verified yet"),
    "hero_crops":  ("generate hero crops", "gen_crops",
                    "no hero_crops.html to inspect/label"),
    "labels":      ("open hero_crops.html and label crops", "label_crops",
                    "labels.json missing or below the minimum"),
    "candidates":  ("export candidate templates", "export_templates",
                    "no candidate templates cut from labelled crops"),
    "cand_report": ("run candidate eval", "cand_eval",
                    "candidate templates exist but were not evaluated"),
    "cand_dryrun": ("run candidate dry-run detection", "cand_dryrun",
                    "eval done; do a read-only dry-run before any approval"),
}


def recommend_next(results: list, paths: Paths) -> Recommendation:
    source, start, end = parse_run_id(paths.run)
    crops = _first_existing(paths.all("hero_crops"))
    frames = _first_existing(paths.all("frames_raw")) or paths.all("frames_raw")[0]
    fmt = dict(
        run=paths.run,
        layout=paths.layout,
        source=source or "<source_id>",
        start=start or "H:MM:SS",
        end=end or "H:MM:SS",
        frames=os.path.relpath(frames, paths.root),
        crops_rel=(os.path.relpath(crops, paths.root).replace("\\", "/")
                   if crops else f"reports/auto/{paths.run}/hero_crops.html"),
    )
    by_id = {c.id: c for c in results}

    # Special case: labels present but too few -> "label more crops".
    for c in results:
        if c.ok:
            continue
        human, cmd_key, reason = _STEP_FOR[c.id]
        if c.id == "labels" and _label_count(paths.p("labels")) > 0:
            human = "label more crops"
        return Recommendation(c.id, human, COMMANDS[cmd_key].format(**fmt), reason)

    return Recommendation(
        "ready", "ready for future template approval",
        COMMANDS["ready"].format(**fmt),
        "every calibration/eval input the doctor checks is present")


# ---------------------------------------------------------------------------
# Optional additive banner (does not touch existing files)
# ---------------------------------------------------------------------------


def emit_banner(paths: Paths, rec: Recommendation) -> Optional[str]:
    """Write a NEW best-effort html fragment with the next step. Never edits
    an existing file. Returns the path written, or None on any failure."""
    run_dir = paths.p("run_dir")
    try:
        os.makedirs(run_dir, exist_ok=True)
        out = os.path.join(run_dir, "next_step.html")
        html = (
            '<div class="owcs-next-step" '
            'style="border:1px solid #888;border-radius:8px;padding:10px 14px;'
            'margin:12px 0;font-family:system-ui,Arial,sans-serif">'
            '<strong>Doctor says next:</strong> '
            f'{_esc(rec.human)}<br><small>{_esc(rec.reason)}</small>'
            f'<pre style="white-space:pre-wrap;margin:8px 0 0">{_esc(rec.command)}</pre>'
            '</div>\n'
        )
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(html)
        return out
    except Exception:
        return None  # best-effort only


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


# ---------------------------------------------------------------------------
# Rendering / CLI
# ---------------------------------------------------------------------------


def _print_human(paths: Paths, results: list, rec: Recommendation) -> None:
    print(f"OWCS doctor  --  run={paths.run}")
    print(f"             layout={paths.layout}")
    print(f"             root={paths.root}")
    print("-" * 62)
    for c in results:
        mark = "OK  " if c.ok else "MISS"
        print(f"  [{mark}] {c.label:<32} {c.detail}")
        if not c.ok:
            print(f"         -> {c.path}")
    print("-" * 62)
    print(f"NEXT STEP: {rec.human}")
    print(f"  why:     {rec.reason}")
    print(f"  run:     {rec.command}")


def build_report(paths: Paths) -> dict:
    results = run_checks(paths)
    rec = recommend_next(results, paths)
    return {
        "run": paths.run,
        "layout": paths.layout,
        "root": paths.root,
        "checks": [c.__dict__ for c in results],
        "next_step": rec.__dict__,
        "ready": rec.step_id == "ready",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="OWCS Comp Tracker preflight / doctor (read-only).")
    ap.add_argument("--run", required=True, help="run id, e.g. owcs-8c105lnzlam_000600_000630")
    ap.add_argument("--layout", required=True, help="layout json, e.g. layouts/owcs_8c105lnzlam.json")
    ap.add_argument("--root", default=None, help="repo root (default: parent of this pipeline/ dir)")
    ap.add_argument("--json", action="store_true", help="print machine-readable JSON")
    ap.add_argument("--next-step-only", action="store_true", help="print only the recommended next step")
    ap.add_argument("--emit-banner", action="store_true",
                    help="also write reports/auto/<run>/next_step.html (new file, best-effort)")
    args = ap.parse_args(argv)

    root = args.root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths = resolve_paths(root, args.run, args.layout)
    report = build_report(paths)
    rec = Recommendation(**report["next_step"])

    if args.emit_banner:
        written = emit_banner(paths, rec)
        if not args.json and not args.next_step_only:
            print(f"(banner {'written -> ' + os.path.relpath(written, paths.root) if written else 'skipped'})")

    if args.json:
        print(json.dumps(report, indent=2))
    elif args.next_step_only:
        print(rec.human)
        print(rec.command)
    else:
        results = [CheckResult(**c) for c in report["checks"]]
        _print_human(paths, results, rec)

    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
