#!/usr/bin/env python3
"""
run_batch.py — the automation orchestrator (the $0 pipeline entrypoint).

Runs the data pipeline end to end, in order:
  1. init_db          (schema; --with-sample for demo data)
  2. ingest_faceit_batch   (FACEIT facts from data/sources/faceit_rooms.json)
  2b. apply_match_facts    (manual FACEIT-fact overrides: replay/bans/veto)
  3. apply_corrections     (manual comps -> source='manual')
  4. validate_data         (warnings OK; hard errors flagged)
  5. export_data           (write assets/js/data.js)

Each step is optional/skippable and one failing step is reported but does not
silently corrupt the others. This is what GitHub Actions calls; it also runs
identically on your laptop.

The CV capture->detect->map_sync loop lives separately in run_cv_batch.py and
is NOT part of this orchestrator yet (it needs calibrated layouts/templates).

Usage:
  python3 pipeline/run_batch.py                    # full pipeline, sample seed
  python3 pipeline/run_batch.py --no-sample        # CI: real data only, no demo
  python3 pipeline/run_batch.py --skip-ingest      # e.g. corrections-only rebuild
  python3 pipeline/run_batch.py --limit 5          # cap FACEIT rooms this run
  python3 pipeline/run_batch.py --offline          # only cached FACEIT bodies
  python3 pipeline/run_batch.py --strict-validate  # fail if validation errors
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import init_db  # noqa: E402
import ingest_faceit_batch as batch  # noqa: E402
import apply_match_facts  # noqa: E402
import apply_corrections  # noqa: E402
import validate_data  # noqa: E402
import export_data  # noqa: E402

CORRECTIONS = os.path.join(db.REPO_ROOT, "corrections", "corrections.json")
MATCH_FACTS = os.path.join(db.REPO_ROOT, "corrections", "match_facts.json")
SOURCE = os.path.join(db.REPO_ROOT, "data", "sources", "faceit_rooms.json")


def log(msg: str) -> None:
    print(f"[run_batch] {msg}", flush=True)


def step_init(con, with_sample: bool) -> None:
    db.init_schema(con)
    data = init_db.load_sample()
    init_db.seed_reference(con, data)
    if with_sample:
        init_db.seed_sample_matches(con, data)
        init_db.seed_sample_rosters(con, data)
        log("init_db: schema + reference + sample matches/rosters")
    else:
        log("init_db: schema + reference only (no sample matches)")


def step_ingest(con, source, cache_dir, limit, offline) -> dict:
    rooms = batch.load_rooms(source)
    if not rooms:
        log("ingest: no FACEIT rooms in source list — skipping")
        return {}
    summary = batch.run_batch(con, rooms, cache_dir, dry_run=False,
                              limit=limit, offline=offline)
    log(f"ingest: {summary['ingested']} ingested, {summary['skipped']} skipped, "
        f"{summary['failed']} failed, {summary['maps']} maps")
    return summary


def step_match_facts(con) -> None:
    ok, bad = apply_match_facts.apply_file(con, MATCH_FACTS, dry_run=False)
    log(f"match_facts: {ok} maps applied, {bad} skipped")


def step_corrections(con) -> None:
    ok, bad = apply_corrections.apply_file(con, CORRECTIONS, dry_run=False)
    log(f"corrections: {ok} applied, {bad} skipped")


def step_validate(con, strict: bool) -> int:
    report = validate_data.run_checks(con)
    code = report.render(strict=strict)
    log(f"validate: exit {code}")
    return code


def step_export(con, allow_empty: bool = False) -> None:
    import datetime as dt
    import json
    payload = export_data.build_payload(con)
    # Safety: don't overwrite an existing populated data.js with an empty one
    # (e.g. a fresh CI DB before real FACEIT rooms are configured). Prevents
    # the automation from publishing a blank site by accident.
    if not payload["matches"] and not allow_empty and os.path.exists(export_data.OUT_PATH):
        log("export: 0 matches — keeping existing data.js (use --allow-empty to override)")
        return
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    body = export_data.HEADER.format(ts=ts) + json.dumps(payload, indent=1) + ";\n"
    os.makedirs(os.path.dirname(export_data.OUT_PATH), exist_ok=True)
    with open(export_data.OUT_PATH, "w", encoding="utf-8") as f:
        f.write(body)
    log(f"export: wrote {export_data.OUT_PATH} — {len(payload['matches'])} matches, "
        f"{sum(len(m['maps']) for m in payload['matches'])} maps")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-sample", action="store_true",
                    help="do not seed demo matches (CI with real data)")
    ap.add_argument("--skip-ingest", action="store_true")
    ap.add_argument("--skip-facts", action="store_true")
    ap.add_argument("--skip-corrections", action="store_true")
    ap.add_argument("--skip-validate", action="store_true")
    ap.add_argument("--source", default=SOURCE)
    ap.add_argument("--cache-dir", default=batch.ing.DEFAULT_CACHE)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--strict-validate", action="store_true",
                    help="treat validation warnings as failures too")
    ap.add_argument("--allow-empty", action="store_true",
                    help="permit exporting an empty data.js (overwrites existing)")
    args = ap.parse_args()

    con = db.connect()
    validate_code = 0

    step_init(con, with_sample=not args.no_sample)

    if not args.skip_ingest:
        try:
            step_ingest(con, args.source, args.cache_dir, args.limit, args.offline)
        except Exception as e:
            log(f"ingest: FAILED non-fatally: {e!r}")

    if not args.skip_facts:
        try:
            step_match_facts(con)
        except Exception as e:
            log(f"match_facts: FAILED non-fatally: {e!r}")

    if not args.skip_corrections:
        try:
            step_corrections(con)
        except Exception as e:
            log(f"corrections: FAILED non-fatally: {e!r}")

    if not args.skip_validate:
        validate_code = step_validate(con, strict=args.strict_validate)

    step_export(con, allow_empty=args.allow_empty)

    # Exit nonzero only if strict validation was requested and failed. Normal
    # runs deploy even with warnings (e.g. FACEIT-only maps missing comps).
    if args.strict_validate and validate_code != 0:
        log("strict validation failed — exiting nonzero")
        sys.exit(validate_code)
    log("pipeline complete.")


if __name__ == "__main__":
    main()
