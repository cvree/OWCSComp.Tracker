#!/usr/bin/env python3
"""
cli.py — operator entry point for the automation foundation.

Run as a script (matches the rest of pipeline/, which are scripts, not a
package invoked with -m):

  python pipeline/automation/cli.py init-db
  python pipeline/automation/cli.py config
  python pipeline/automation/cli.py registries
  python pipeline/automation/cli.py coverage [--window 14] [--save]
  python pipeline/automation/cli.py status

Everything is offline and read-mostly; `init-db` and `coverage --save` are the
only commands that write, and both only touch the automation DB.
"""
from __future__ import annotations

import argparse
import os
import sys

# Put the pipeline dir on the path so `import automation.*` resolves whether
# this file is run directly or imported.
_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

from automation import config as cfg  # noqa: E402
from automation import coverage as cov  # noqa: E402
from automation import job_store as js  # noqa: E402


def cmd_init_db(args: argparse.Namespace) -> int:
    store = js.JobStore(args.db)
    store.close()
    print(f"[automation] job database ready: {args.db}")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    c = cfg.load_config()
    print("[automation] operator config (config/automation.yml + defaults):")
    for k in sorted(c.values):
        print(f"  {k}: {c.values[k]}")
    return 0


def cmd_registries(args: argparse.Namespace) -> int:
    comps_all = cfg.load_all_competitions()
    comps_live = cfg.load_competitions()
    chans_all = cfg.load_all_channels()
    chans_live = cfg.load_channels()
    print("[automation] FACEIT competitions (Phase B1):")
    print(f"  {len(comps_all)} configured, {len(comps_live)} enabled+ready")
    for c in comps_all:
        flag = "on " if (c.get("enabled") and c.get("championshipId")) else "off"
        print(f"    [{flag}] tier{c.get('tier')} {c.get('region'):<7} {c.get('id')}")
    print("[automation] broadcast channels (Phase C1):")
    print(f"  {len(chans_all)} configured, {len(chans_live)} enabled+ready")
    for ch in chans_all:
        flag = "on " if (ch.get("enabled") and ch.get("channelId")) else "off"
        print(f"    [{flag}] {ch.get('region'):<7} {ch.get('platform'):<8} {ch.get('id')}")
    if not comps_live and not chans_live:
        print("  (placeholders only — fill real FACEIT/YouTube ids, then enable)")
    return 0


def cmd_coverage(args: argparse.Namespace) -> int:
    report = cov.build_report(window_days=args.window, automation_db=args.db)
    print(cov.format_report(report))
    if args.save:
        rid = cov.save_snapshot(args.db, report)
        print(f"\n[automation] coverage snapshot #{rid} saved to {args.db}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    store = js.JobStore(args.db)
    try:
        counts = store.counts_by_state()
        total = sum(counts.values())
        print(f"[automation] job database: {args.db}")
        print(f"  jobs: {total}")
        for state in sorted(counts):
            print(f"    {state}: {counts[state]}")
        expired = store.con.execute(
            "SELECT COUNT(*) n FROM locks"
        ).fetchone()["n"]
        print(f"  active locks: {expired}")
    finally:
        store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="OWCS automation operator CLI")
    p.add_argument("--db", default=js.DEFAULT_DB, help="automation DB path")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create/upgrade the automation job DB").set_defaults(func=cmd_init_db)
    sub.add_parser("config", help="print resolved operator config").set_defaults(func=cmd_config)
    sub.add_parser("registries", help="print competition/channel registries").set_defaults(func=cmd_registries)
    sub.add_parser("status", help="job counts by state + locks").set_defaults(func=cmd_status)

    cvp = sub.add_parser("coverage", help="rolling completeness report (Phase D4)")
    cvp.add_argument("--window", type=int, default=14, help="lookback days")
    cvp.add_argument("--save", action="store_true", help="persist a coverage snapshot")
    cvp.set_defaults(func=cmd_coverage)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
