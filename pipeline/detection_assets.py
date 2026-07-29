#!/usr/bin/env python3
"""
detection_assets.py — is this layout actually able to detect anything?

A layout file can be syntactically perfect and still be useless: it can
point at a hero-template directory that was never harvested, or declare a
HUD anchor whose PNG was never cut. Both failures are silent in exactly the
worst way — the pipeline downloads a multi-hour broadcast, builds a proxy,
segments it, and only then reports `detect: skipped — no hero templates`.

This module answers the question BEFORE a byte is downloaded, and answers it
with the exact command that fixes it.

Three distinct verdicts, because they need different remedies:

  * **templates** — `templates_dir` missing, or present but containing no
    per-hero PNG. Remedy: harvest + label from real broadcast frames.
  * **anchor** — the layout declares `anchor.template` but the file is not
    on disk, or the layout's own `_adjust` note still says PLACEHOLDER.
    A declared-but-missing anchor is worse than no anchor: `frame_filter`
    scores it at ~0 and rejects real gameplay as `no-hud`.
  * **structural probe** — a layout with NO anchor is fine when it carries a
    verified `hud_probe` (that is how `owcs_jksix_qwc` works, and it is the
    one fully-proven package in this repo). No anchor AND no probe is not.

Nothing here fabricates an asset or claims a calibration succeeded; it only
reports what is on disk.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

LAYOUTS_DIR = os.path.join(db.REPO_ROOT, "layouts")

# A layout comment containing any of these means a human left the field as a
# stub. Trusting it would produce confident-looking garbage.
_PLACEHOLDER_MARKERS = ("placeholder", "replace_with", "todo", "tbd",
                        "not yet cut", "example only")

OK, WARN, FAIL = "ok", "warn", "fail"


def layout_path(layout_id: str) -> str:
    """Accept a bare id, a filename, or a path — never guess a different
    layout than the one named."""
    if os.path.isabs(layout_id) or "/" in layout_id or "\\" in layout_id:
        return layout_id if os.path.isabs(layout_id) else \
            os.path.join(db.REPO_ROOT, layout_id)
    name = layout_id if layout_id.endswith(".json") else f"{layout_id}.json"
    return os.path.join(LAYOUTS_DIR, name)


def _looks_placeholder(value) -> bool:
    text = json.dumps(value).lower() if not isinstance(value, str) \
        else value.lower()
    return any(marker in text for marker in _PLACEHOLDER_MARKERS)


def _resolve(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(db.REPO_ROOT, path)


def hero_template_files(templates_dir: str) -> list[str]:
    """Per-hero template PNGs in a template set. Sub-variants
    (`dva.v2.png`, `ball.a.png`) count — they are real harvested crops."""
    root = _resolve(templates_dir)
    if not os.path.isdir(root):
        return []
    return sorted(glob.glob(os.path.join(root, "*.png")))


def hero_ids_covered(templates_dir: str) -> set[str]:
    """Distinct hero ids a template set covers (`dva.v2.png` -> `dva`)."""
    return {os.path.basename(p).split(".")[0]
            for p in hero_template_files(templates_dir)}


def check_templates(layout: dict, layout_id: str) -> dict:
    templates_dir = layout.get("templates_dir")
    if not templates_dir:
        return {
            "name": "templates", "status": FAIL,
            "detail": f"layout {layout_id} declares no templates_dir — "
                      f"detection cannot load any hero portrait",
            "remedy": (f"add \"templates_dir\": \"templates/{layout_id}\" to "
                       f"layouts/{layout_id}.json, then harvest it"),
        }
    files = hero_template_files(templates_dir)
    heroes = hero_ids_covered(templates_dir)
    if not os.path.isdir(_resolve(templates_dir)):
        return {
            "name": "templates", "status": FAIL,
            "detail": (f"templates_dir {templates_dir!r} does not exist — "
                       f"every slot would read UNKNOWN, so a full download "
                       f"and segmentation would produce zero compositions"),
            "remedy": (f"python pipeline/harvest_templates.py --clip <clip> "
                       f"--layout layouts/{layout_id}.json --out "
                       f"{templates_dir} --cluster   "
                       f"(then re-run with --labels to emit per-hero "
                       f"templates)"),
        }
    if not files:
        return {
            "name": "templates", "status": FAIL,
            "detail": f"templates_dir {templates_dir!r} exists but contains "
                      f"no template PNG",
            "remedy": (f"python pipeline/harvest_templates.py --clip <clip> "
                       f"--layout layouts/{layout_id}.json --out "
                       f"{templates_dir} --cluster"),
        }
    return {
        "name": "templates", "status": OK,
        "detail": (f"{len(files)} template file(s) covering "
                   f"{len(heroes)} hero id(s) in {templates_dir}"),
        "remedy": "", "heroCount": len(heroes), "fileCount": len(files),
    }


def check_anchor(layout: dict, layout_id: str) -> dict:
    """A DECLARED anchor must be real. No anchor at all is acceptable only
    when the layout carries a structural `hud_probe` instead."""
    anchor = layout.get("anchor")
    probe = layout.get("hud_probe")
    if not anchor:
        if probe:
            return {
                "name": "anchor", "status": OK,
                "detail": (f"no anchor template, but layout {layout_id} "
                           f"carries a structural hud_probe — the same "
                           f"gameplay test ingest_map.py trusts"),
                "remedy": "",
            }
        return {
            "name": "anchor", "status": FAIL,
            "detail": (f"layout {layout_id} has neither an anchor template "
                       f"nor a hud_probe — nothing can tell live gameplay "
                       f"from a replay, and replays render a PAST comp"),
            "remedy": (f"python pipeline/build_layout_debug.py --layout "
                       f"layouts/{layout_id}.json --frames-dir <frames>   "
                       f"then cut an anchor crop from a live-HUD region"),
        }
    if isinstance(anchor, dict):
        template = anchor.get("template")
        note = anchor.get("_adjust") or ""
    else:
        template, note = str(anchor), ""
    if _looks_placeholder(note):
        return {
            "name": "anchor", "status": FAIL,
            "detail": (f"layout {layout_id}'s anchor is still marked a "
                       f"PLACEHOLDER by its own note — it has never been cut "
                       f"from a real frame"),
            "remedy": (f"cut {template} from a live-gameplay frame of this "
                       f"broadcast, then verify it against a DIFFERENT match "
                       f"in the same VOD before trusting it"),
        }
    if not template:
        return {"name": "anchor", "status": FAIL,
                "detail": f"layout {layout_id} declares an anchor with no "
                          f"template path",
                "remedy": f"set anchor.template in layouts/{layout_id}.json"}
    if not os.path.exists(_resolve(template)):
        return {
            "name": "anchor", "status": FAIL,
            "detail": (f"anchor template {template!r} is declared but NOT on "
                       f"disk — frame_filter would score it ~0 and reject "
                       f"real gameplay as 'no-hud', reporting 0 crops as if "
                       f"the window simply had no play in it"),
            "remedy": (f"cut {template} from a real live-gameplay frame "
                       f"(build_layout_debug.py renders candidates), or "
                       f"remove the anchor block and rely on hud_probe"),
        }
    return {"name": "anchor", "status": OK,
            "detail": f"real anchor template on disk: {template}",
            "remedy": ""}


def check_layout_assets(layout_id: str) -> dict:
    """Full verdict for one layout: {"ok", "layoutId", "checks", "failed"}.

    `ok` is False when ANY check failed — the caller is expected to refuse
    the download rather than discover this hours later.
    """
    path = layout_path(layout_id)
    if not os.path.exists(path):
        return {"ok": False, "layoutId": layout_id, "layoutPath": path,
                "failed": ["layout"],
                "checks": [{"name": "layout", "status": FAIL,
                            "detail": f"layout file not found: {path}",
                            "remedy": ("python pipeline/automation/cli.py "
                                       "resolve-layout --job <job>")}]}
    try:
        with open(path, "r", encoding="utf-8") as f:
            layout = json.load(f)
    except ValueError as exc:
        return {"ok": False, "layoutId": layout_id, "layoutPath": path,
                "failed": ["layout"],
                "checks": [{"name": "layout", "status": FAIL,
                            "detail": f"layout is not valid JSON: {exc}",
                            "remedy": f"fix the syntax in {path}"}]}
    checks = [check_templates(layout, layout_id),
              check_anchor(layout, layout_id)]
    failed = [c["name"] for c in checks if c["status"] == FAIL]
    return {"ok": not failed, "layoutId": layout_id, "layoutPath": path,
            "checks": checks, "failed": failed}


def format_report(report: dict) -> str:
    icon = {OK: "OK  ", WARN: "WARN", FAIL: "FAIL"}
    lines = [f"  layout: {report['layoutId']}"]
    for c in report["checks"]:
        lines.append(f"    [{icon[c['status']]}] {c['name']:<10} {c['detail']}")
        if c["remedy"]:
            lines.append(f"             -> {c['remedy']}")
    return "\n".join(lines)


def audit_all_layouts() -> list[dict]:
    """Every committed layout's asset verdict — what `worker-doctor` shows
    so an operator learns which packages are detection-ready BEFORE match
    day, not during it."""
    out = []
    for path in sorted(glob.glob(os.path.join(LAYOUTS_DIR, "*.json"))):
        out.append(check_layout_assets(
            os.path.splitext(os.path.basename(path))[0]))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Are this layout's detection assets real and present?")
    ap.add_argument("--layout", help="layout id (default: audit every one)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)
    reports = ([check_layout_assets(args.layout)] if args.layout
               else audit_all_layouts())
    if args.as_json:
        print(json.dumps(reports, indent=1))
    else:
        print("[assets] detection readiness:")
        for r in reports:
            print(format_report(r))
        ready = [r["layoutId"] for r in reports if r["ok"]]
        print(f"\n  detection-ready layouts: "
              f"{', '.join(ready) if ready else 'NONE'}")
    return 0 if all(r["ok"] for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
