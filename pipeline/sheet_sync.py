#!/usr/bin/env python3
"""
sheet_sync.py — import the community OWCS spreadsheet as an external source.

The sheet is a manually maintained record of OWCS series: map picks, map
results, hero bans and the heroes listed for each player. It is imported as
its OWN layer (data/external/gsheet/ -> assets/data/external_sheet.v1.js),
never merged into the VOD-verified dataset, and every number the site shows
from it is labelled 'spreadsheet imported'. See docs/EXTERNAL-SHEET.md.

Commands:

  sync        download the public xlsx export, skip if its SHA-256 is unchanged,
              otherwise import + validate + reconcile + export. A failed
              download or a changed layout keeps the last good import and
              writes reports/external/gsheet/last_failure.md (exit 2).
              --force re-imports identical bytes; --accept-structure-change
              lets a reviewed tab rename/removal through.
  import      the same, from a local .xlsx file (offline):  import --xlsx FILE
  rebuild     re-parse the COMMITTED raw cell layer with today's parser,
              aliases, layout and decisions; re-reconcile; re-export. Run it
              after editing config/external/*.json, after a reconciliation
              decision, and after `export_data.py --public`.
  check       CI gate: exit 1 if the committed normalized layer or export is
              not what `rebuild` would produce. Writes nothing.
  inventory   print the source inventory of a local workbook without
              importing it:  inventory --xlsx FILE   (no writes)
  status      print the last sync, counts and reconciliation summary.
  fixture     build the whole chain from the synthetic test workbook into
              --out DIR (default work/gsheet-fixture/) for previews. It can
              never write the real export.

Exit codes: 0 ok/unchanged · 1 check drift · 2 sync/import failed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from external_sheet import sync as S  # noqa: E402
from external_sheet.normalize import ImportFailed  # noqa: E402
from external_sheet.store import Paths, read_json, read_normalized  # noqa: E402
from external_sheet.workbook import WorkbookError, read_xlsx  # noqa: E402

FIXTURE_XLSX = os.path.join(HERE, "fixtures", "gsheet", "owcs_sheet_fixture.xlsx")


def _print_result(res: dict) -> int:
    st = res.get("status")
    if st == "unchanged":
        print(f"[sheet-sync] unchanged — workbook sha256 {res['sha256'][:12]}… matches the last "
              "good import; nothing written")
        return 0
    if st == "failed":
        print(f"[sheet-sync] FAILED at {res['stage']}: {res['reason']}", file=sys.stderr)
        print("[sheet-sync] the last good import was kept; see "
              "reports/external/gsheet/last_failure.md", file=sys.stderr)
        return 2
    c, ch, rc = res["counts"], res["changes"], res["reconciliation"]
    print(f"[sheet-sync] imported sha256 {res['sha256'][:12]}… — {c['matches']} matches, "
          f"{c['maps']} maps, {c['bans']} bans, {c['appearances']} hero appearances, "
          f"{c['compositions']} composition observations")
    print(f"[sheet-sync] changes: {ch['new']} new, {ch['changed']} changed, {ch['removed']} removed maps")
    print("[sheet-sync] reconciliation: " + ", ".join(f"{k} {v}" for k, v in rc.items()))
    return 0


def cmd_sync(args, paths: Paths) -> int:
    return _print_result(S.sync(paths, force=args.force,
                                accept_structure_change=args.accept_structure_change,
                                source_url=args.url))


def cmd_import(args, paths: Paths) -> int:
    with open(args.xlsx, "rb") as f:
        data = f.read()
    return _print_result(S.sync(paths, data=data, force=args.force,
                                source_url=args.source_url or "file:" + os.path.basename(args.xlsx),
                                accept_structure_change=args.accept_structure_change))


def cmd_rebuild(args, paths: Paths) -> int:
    try:
        out = S.rebuild(paths)
    except ImportFailed as exc:
        print(f"[sheet-sync] rebuild FAILED: {exc}", file=sys.stderr)
        return 2
    if out["normalized"] is None:
        print("[sheet-sync] no raw layer committed yet — wrote the 'never synced' export")
        return 0
    c = out["normalized"]["inventory"]["counts"]
    print(f"[sheet-sync] rebuilt from the committed raw layer: {c['maps']} maps; reconciliation "
          + ", ".join(f"{k} {v}" for k, v in out["recon"]["summary"].items()))
    return 0


def cmd_check(args, paths: Paths) -> int:
    try:
        problems = S.check(paths)
    except ImportFailed as exc:
        print(f"[sheet-sync] the committed raw layer no longer parses: {exc}", file=sys.stderr)
        return 1
    if problems:
        for p in problems:
            print(f"[sheet-sync] DRIFT: {p}", file=sys.stderr)
        return 1
    print("[sheet-sync] committed external layer matches a fresh rebuild")
    return 0


def cmd_inventory(args, paths: Paths) -> int:
    bundle = S.Bundle.load(paths)
    with open(args.xlsx, "rb") as f:
        data = f.read()
    try:
        raw = read_xlsx(data)
    except WorkbookError as exc:
        print(f"[sheet-sync] {exc}", file=sys.stderr)
        return 2
    from external_sheet.export import inventory_markdown
    from external_sheet.normalize import import_workbook
    try:
        normalized = import_workbook(raw, bundle.aliases(), bundle.layout())
        inv = normalized["inventory"]
    except ImportFailed as exc:
        inv = exc.inventory
        print(f"[sheet-sync] this workbook would FAIL validation: {exc}", file=sys.stderr)
    print(json.dumps(inv, indent=1, ensure_ascii=False) if args.json else
          inventory_markdown(inv, None, bundle.source))
    return 0


def cmd_status(args, paths: Paths) -> int:
    m = read_json(paths.manifest) or {}
    snaps = m.get("snapshots") or []
    good = next((s for s in snaps if s.get("sha256") == m.get("lastGood")), None)
    if not good:
        print("[sheet-sync] never synced — run: python3 pipeline/sheet_sync.py sync")
    else:
        print(f"last good import: {good['importedAt']}  sha256 {good['sha256']}")
        print(f"  {len(good['sheetNames'])} tabs, {good['sourceRowCount']} source rows, "
              f"parser {good['parserVersion']}")
        print("  counts: " + ", ".join(f"{k} {v}" for k, v in (good.get("counts") or {}).items()))
    att = m.get("lastAttempt")
    if att:
        print(f"last attempt: {att.get('at')} — {att.get('status')}"
              + (f" at {att.get('stage')}: {att.get('reason')}" if att.get("status") == "failed" else ""))
    rec = read_json(os.path.join(paths.reports, "reconciliation.json")) or {}
    if rec.get("summary"):
        print("reconciliation: " + ", ".join(f"{k} {v}" for k, v in rec["summary"].items()))
    return 0


def cmd_fixture(args, paths: Paths) -> int:
    out = os.path.abspath(args.out)
    if os.path.abspath(paths.root) == out:
        print("[sheet-sync] refusing: the fixture build may not write into the repository root",
              file=sys.stderr)
        return 2
    target = Paths(root=out)
    for rel in ("config/external", "corrections"):
        os.makedirs(os.path.join(out, rel), exist_ok=True)
    for src, dst in ((paths.source_cfg, target.source_cfg), (paths.layout_cfg, target.layout_cfg),
                     (paths.aliases_cfg, target.aliases_cfg), (paths.decisions, target.decisions)):
        shutil.copyfile(src, dst)
    os.makedirs(os.path.dirname(target.public), exist_ok=True)
    shutil.copyfile(paths.public, target.public)
    with open(args.xlsx or FIXTURE_XLSX, "rb") as f:
        data = f.read()
    res = S.sync(target, data=data, force=True, source_url="fixture:owcs_sheet_fixture.xlsx",
                 at="2026-10-31T00:00:00+00:00")
    if res["status"] == "imported":
        # mark it as a fixture so no page can mistake it for the real sheet
        bundle = S.Bundle.load(target)
        _, payload, details = S.payload_for(read_normalized(target), read_json(target.manifest),
                                            bundle, fixture=True)
        S.write_export(target, payload, details)
        print(f"[sheet-sync] fixture build written under {out}")
    return _print_result(res)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sync", help="download + import the public workbook")
    p.add_argument("--force", action="store_true")
    p.add_argument("--accept-structure-change", action="store_true")
    p.add_argument("--url", default=None, help="override the export URL")
    p = sub.add_parser("import", help="import a local .xlsx")
    p.add_argument("--xlsx", required=True)
    p.add_argument("--source-url", default=None)
    p.add_argument("--force", action="store_true")
    p.add_argument("--accept-structure-change", action="store_true")
    sub.add_parser("rebuild", help="re-parse the committed raw layer")
    sub.add_parser("check", help="CI drift gate (writes nothing)")
    sub.add_parser("status", help="print sync health")
    p = sub.add_parser("inventory", help="inventory a local workbook (no writes)")
    p.add_argument("--xlsx", required=True)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("fixture", help="preview build from the synthetic workbook")
    p.add_argument("--out", default=os.path.join(os.path.dirname(HERE), "work", "gsheet-fixture"))
    p.add_argument("--xlsx", default=None)
    args = ap.parse_args(argv)
    paths = Paths(root=args.root) if args.root else Paths()
    return {"sync": cmd_sync, "import": cmd_import, "rebuild": cmd_rebuild, "check": cmd_check,
            "status": cmd_status, "inventory": cmd_inventory, "fixture": cmd_fixture}[args.cmd](args, paths)


if __name__ == "__main__":
    raise SystemExit(main())
