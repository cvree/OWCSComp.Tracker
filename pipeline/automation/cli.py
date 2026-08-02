#!/usr/bin/env python3
"""
cli.py — operator entry point for the automation foundation.

Run as a script (matches the rest of pipeline/, which are scripts, not a
package invoked with -m):

  python pipeline/automation/cli.py init-db
  python pipeline/automation/cli.py config
  python pipeline/automation/cli.py registries
  python pipeline/automation/cli.py coverage [--window 30] [--save]
  python pipeline/automation/cli.py status

Everything is offline and read-mostly; `init-db` and `coverage --save` are the
only commands that write, and both only touch the automation DB.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

# Put the pipeline dir on the path so `import automation.*` resolves whether
# this file is run directly or imported.
_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)
import proc_text  # noqa: E402  (UTF-8 subprocess decoding)

import db as content_db  # noqa: E402  (pipeline/db.py)
from automation import broadcast_discovery as bdisc  # noqa: E402
from automation import broadcast_matching as bmatch  # noqa: E402
from automation import config as cfg  # noqa: E402
from automation import coverage as cov  # noqa: E402
from automation import discovery as disc  # noqa: E402
from automation import faceit_api  # noqa: E402
from automation import job_store as js  # noqa: E402
from automation import link_intake as li  # noqa: E402
from automation import locks as lk  # noqa: E402
from automation import match_export_coverage as mec  # noqa: E402
from automation import match_repair as mrepair  # noqa: E402
from automation import models  # noqa: E402
from automation import owcs_calendar  # noqa: E402
from automation import reconcile as rec  # noqa: E402
from automation import state_machine as sm  # noqa: E402
from automation import team_assets as tassets  # noqa: E402
from automation import team_coverage as tcov  # noqa: E402
from automation import team_enrichment as tenrich  # noqa: E402
from automation import youtube_api as yt  # noqa: E402

# NOT imported at module level, on purpose: `ops`, `worker`, `segmentation`,
# `detection_runner`, `publish`. Each transitively touches computer-vision /
# recording / detection code (capture.py/video_ingest.py/ingest_map.py ->
# cv2), which a lightweight discovery/registry/coverage command never needs
# and a bare-Python CI runner may not have installed. Every command handler
# that actually needs one of these imports it locally, right where it's
# used — see cmd_create_job, cmd_list_jobs, cmd_claim_job, cmd_release_job,
# cmd_retry_job, cmd_cancel_job, cmd_reset_stale_lock, cmd_resume_job,
# cmd_run_job, cmd_job_coverage, cmd_worker_doctor, cmd_worker_run,
# cmd_segment_list, cmd_segment_approve, cmd_segment_reject, cmd_detect_job,
# cmd_process_approved_job. `python cli.py --help` and every discovery-only
# command (verify-channels, calendar-dryrun, broadcast-dryrun, discover-
# broadcasts, coverage, sync-faceit, sync-calendar, sync-all, list-
# championships, list-organizers, verify-competition, verify-registry) must
# stay runnable on a machine with no OpenCV installed.


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


def _build_client(args: argparse.Namespace) -> faceit_api.FaceitClient:
    """Real API client (FACEIT_API_KEY), or an offline fixture client when
    --fixture-dir is given. Fixtures never touch the network."""
    if getattr(args, "fixture_dir", None):
        return faceit_api.FaceitClient(
            transport=faceit_api.fixture_transport(args.fixture_dir))
    # Read-only/dry commands don't cache into the repo; only a live sync does.
    cache = None if getattr(args, "dry_run", True) else os.path.join(
        content_db.REPO_ROOT, "data", "raw", "faceit_api")
    return faceit_api.FaceitClient(cache_dir=cache)


def _open_content_db():
    con = content_db.connect()
    content_db.init_schema(con)
    return con


def _print_faceit_summary(s: dict) -> None:
    print(f"  competitions   : {len(s['competitions'])} "
          f"({', '.join(s['competitions']) or 'none enabled'})")
    if s.get("note"):
        print(f"  note           : {s['note']}")
    print(f"  matches seen   : {s['matchesSeen']}  in-window: {s['inWindow']}")
    print(f"  upserted       : {s['upserted']}  "
          f"({'dry-run — no writes' if s['dryRun'] else 'written'})")
    if s.get("byLifecycle"):
        print(f"  by lifecycle   : {s['byLifecycle']}")
    if s.get("rescheduled"):
        print(f"  rescheduled    : {len(s['rescheduled'])} match(es)")
    if not s["dryRun"]:
        print(f"  broadcast jobs : {s['broadcastJobsCreated']} created")
    for e in s.get("errors", []):
        print(f"  API ERROR      : {e['competitionId']}: {e['error']}")


def cmd_sync_faceit(args: argparse.Namespace) -> int:
    config = cfg.load_config()
    con = _open_content_db()
    store = None if args.dry_run else js.JobStore(args.db, config=config)
    try:
        summary = disc.sync_faceit(
            con=con, store=store, client=_build_client(args), config=config,
            lookback_days=args.lookback_days, horizon_days=args.horizon_days,
            dry_run=args.dry_run)
        print(f"[automation] sync-faceit ({'dry-run' if args.dry_run else 'live'}):")
        _print_faceit_summary(summary)
        if args.export and not args.dry_run:
            _run_export()
    finally:
        con.close()
        if store:
            store.close()
    return 0


def cmd_sync_calendar(args: argparse.Namespace) -> int:
    store = None if args.dry_run else js.JobStore(args.db)
    try:
        events = owcs_calendar.load_events()
        summary = disc.sync_calendar(store=store, events=events, dry_run=args.dry_run)
        print(f"[automation] sync-calendar ({'dry-run' if args.dry_run else 'live'}):")
        print(f"  events         : {summary['events']} "
              f"({summary['unverified']} unverified)")
        for eid in summary["eventIds"]:
            print(f"    - {eid}")
    finally:
        if store:
            store.close()
    return 0


def cmd_sync_all(args: argparse.Namespace) -> int:
    config = cfg.load_config()
    con = _open_content_db()
    store = None if args.dry_run else js.JobStore(args.db, config=config)
    try:
        result = disc.sync_all(
            con=con, store=store, client=_build_client(args), config=config,
            lookback_days=args.lookback_days, horizon_days=args.horizon_days,
            dry_run=args.dry_run)
        print(f"[automation] sync-all ({'dry-run' if args.dry_run else 'live'}):")
        _print_faceit_summary(result["faceit"])
        print(f"  calendar events: {result['calendar']['events']}")
        print(f"  reconciliation : {result['warningCount']} warning(s)")
        for w in result["warnings"][:20]:
            print(f"    [{w['code']}] {w['message']}")
        if args.export and not args.dry_run:
            _run_export()
    finally:
        con.close()
        if store:
            store.close()
    return 0


def cmd_list_championships(args: argparse.Namespace) -> int:
    """Read-only candidate discovery: search OW2 championships (optionally an
    organizer's) so a human can confirm official ids before enabling them.
    Prints facts only; never writes and never enables anything."""
    client = _build_client(args)
    rows: list[dict] = []
    if args.organizer:
        try:
            org = faceit_api.normalize_organizer(client.get_organizer(args.organizer))
            print(f"[automation] organizer {args.organizer}: {org['name']}")
        except (faceit_api.FaceitApiError, faceit_api.FaceitAuthError) as exc:
            print(f"[automation] organizer {args.organizer}: (details unavailable: {exc})")
        raw = client.list_organizer_championships(args.organizer, game=args.game)
        rows = [faceit_api.normalize_championship(c) for c in raw]
        header = f"organizer {args.organizer} championships (game={args.game})"
    else:
        raw = client.search_championships(args.query, game=args.game, ctype=args.type,
                                          limit=args.limit)
        rows = [faceit_api.normalize_championship(c) for c in raw]
        header = f"search championships name~'{args.query}' game={args.game} type={args.type}"
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    print(f"[automation] {header}: {len(rows)} result(s)")
    print(f"  {'championshipId':<40} {'region':<8} {'status':<10} name")
    for r in rows:
        print(f"  {(r['championshipId'] or '-'):<40} {(r['region'] or '-'):<8} "
              f"{(r['status'] or '-'):<10} {r['name'] or '-'}  "
              f"[org={r['organizerId'] or '-'}]")
    print("\n  NOTE: verify each id with `verify-competition <id>` and confirm the")
    print("  organizer is the OFFICIAL OWCS organizer before setting enabled=true.")
    return 0


def cmd_list_organizers(args: argparse.Namespace) -> int:
    client = _build_client(args)
    rows = [faceit_api.normalize_organizer(o) for o in client.search_organizers(args.query)]
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    print(f"[automation] organizers name~'{args.query}': {len(rows)} result(s)")
    for r in rows:
        print(f"  {(r['organizerId'] or '-'):<40} {r['name'] or '-'}")
    return 0


def cmd_verify_competition(args: argparse.Namespace) -> int:
    """Retrieve a championship's official FACEIT details to verify it before/after
    enabling. Prints the exact name, organizer, region and dates."""
    client = _build_client(args)
    try:
        raw = client.get_championship(args.championship_id)
    except (faceit_api.FaceitApiError, faceit_api.FaceitAuthError) as exc:
        print(f"[automation] verify FAILED for {args.championship_id}: {exc}")
        return 1
    c = faceit_api.normalize_championship(raw)
    if args.json:
        print(json.dumps(c, indent=2))
        return 0
    print(f"[automation] championship {args.championship_id}:")
    for k in ("name", "organizerId", "game", "region", "status", "startDate", "endDate", "faceitUrl"):
        print(f"  {k:<13}: {c.get(k)}")
    return 0


def cmd_verify_registry(args: argparse.Namespace) -> int:
    """Verify EVERY enabled competition in config/faceit_competitions.json by
    retrieving its official FACEIT details. Non-zero exit if any fails."""
    comps = cfg.load_competitions()
    if not comps:
        print("[automation] no enabled competitions to verify "
              "(registry entries are placeholders/disabled).")
        return 0
    client = _build_client(args)
    failures = 0
    for comp in comps:
        cid = comp.get("championshipId")
        try:
            c = faceit_api.normalize_championship(client.get_championship(cid))
            print(f"  OK  {comp['id']:<26} {cid}  ->  {c['name']} "
                  f"[org={c['organizerId']}, region={c['region']}]")
        except (faceit_api.FaceitApiError, faceit_api.FaceitAuthError) as exc:
            failures += 1
            print(f"  ERR {comp['id']:<26} {cid}  ->  {exc}")
    print(f"[automation] verified {len(comps) - failures}/{len(comps)} enabled competitions")
    return 1 if failures else 0


def cmd_enrich_teams(args: argparse.Namespace) -> int:
    """Populate team FACTS (bio/website/socials/roster size) from the FACEIT
    team API for every team discovery already resolved a faceit_team_id for.
    Never searches, never writes a logo — image URLs land only as candidate
    sources in assets/data/team_asset_sources.json for human verification."""
    con = _open_content_db()
    try:
        client = _build_client(args)
        summary = tenrich.enrich_teams(
            con=con, client=client,
            team_ids=args.team_id or None,
            dry_run=args.dry_run)
        print(f"[automation] enrich-teams ({'dry-run' if args.dry_run else 'live'}):")
        print(tenrich.format_summary(summary))
        if args.export and not args.dry_run:
            _run_export()
    finally:
        con.close()
    return 0


def cmd_team_coverage(args: argparse.Namespace) -> int:
    """Per-team coverage ledger (Phase D2): identity/roster/logo/broadcast/
    capture states, one row per registered team, every gap named."""
    supported = {r.lower() for r in cfg.load_config().regions} or None
    report = tcov.build_report(window_days=args.window, automation_db=args.db,
                               supported_regions=supported)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(tcov.format_report(report))
    if args.save:
        _write_team_coverage_export(window_days=args.window)
        print(f"\n[automation] wrote {os.path.relpath(TEAM_COVERAGE_EXPORT_PATH, content_db.REPO_ROOT)}")
    return 0


def cmd_collect_team_assets(args: argparse.Namespace) -> int:
    """Mechanically promote already-recorded FACEIT avatar/cover candidates
    into the ranked assetCandidates list, then print every team's ranked
    candidates + pipeline state. Read-only unless --save (only ever adds
    NEW candidate entries — never approves or publishes anything)."""
    registry = tassets.load_registry()
    added = tassets.collect_from_enrichment(registry)
    print(f"[automation] collect-team-assets: {len(added)} new candidate(s) "
          f"promoted from FACEIT-sourced avatar/cover URLs")
    team_ids = args.team_id or sorted(registry.get("teams", {}).keys())
    for tid in team_ids:
        cands = tassets.ranked_candidates(registry, tid)
        if not cands:
            continue
        print(f"  {tid}:")
        for c in cands:
            print(f"    [{c['state']:<14}] rank={c['authorityRank']} {c['sourceKind']:<16} {c['url']}")
    if args.save:
        tassets.save_registry(registry)
        print(f"[automation] saved {os.path.relpath(tassets.DEFAULT_ASSET_SOURCES, content_db.REPO_ROOT)}")
    return 0


def cmd_approve_team_asset(args: argparse.Namespace) -> int:
    """The ONE step in the logo pipeline a human must explicitly take.
    Requires --confirm; there is no default that approves anything."""
    registry = tassets.load_registry()
    try:
        cand = tassets.approve_candidate(
            registry, args.team_id, args.url,
            approved_by=args.approved_by, confirm=args.confirm)
    except (KeyError, ValueError) as exc:
        print(f"[automation] approve-team-asset FAILED: {exc}")
        return 1
    tassets.save_registry(registry)
    print(f"[automation] {args.team_id}: {args.url} -> human-approved by {args.approved_by}")
    print(f"  (run publish-team-assets --publish to generate variants and go live)")
    return 0


def cmd_publish_team_assets(args: argparse.Namespace) -> int:
    """Publish every candidate already in 'human-approved' state (or
    re-publish an already-'published' one after a rerun). Never approves a
    new candidate — that gate is approve_candidate()'s alone. Default is a
    dry-run listing; pass --publish to actually write files + logo_url."""
    registry = tassets.load_registry()
    con = _open_content_db()
    ready = [(tid, c) for tid, entry in registry.get("teams", {}).items()
             for c in entry.get("assetCandidates", [])
             if c["state"] in ("human-approved", "published")]
    print(f"[automation] publish-team-assets: {len(ready)} candidate(s) "
          f"{'ready to publish' if not args.publish else 'to publish'}")
    for tid, c in ready:
        print(f"  {tid}: {c['url']} (state={c['state']})")
        if args.publish:
            out = tassets.publish_candidate(con, registry, tid, c["url"])
            print(f"    -> published: {out['variants']}")
    if args.publish:
        tassets.save_registry(registry)
    else:
        print("  (dry-run — pass --publish to write variants + set logo_url)")
    con.close()
    return 0


TEAM_COVERAGE_EXPORT_PATH = os.path.join(
    content_db.REPO_ROOT, "assets", "data", "team_coverage.v1.json")


def _write_team_coverage_export(window_days: int) -> None:
    """Small, non-sensitive, committed JSON (team names/regions/coverage
    states/blocking issues — nothing that isn't already public) the static
    Team Coverage ops page reads. Regenerated as part of the same reviewed
    export step as the public dataset, never on its own."""
    report = tcov.build_report(window_days=window_days, automation_db=js.DEFAULT_DB)
    os.makedirs(os.path.dirname(TEAM_COVERAGE_EXPORT_PATH), exist_ok=True)
    with open(TEAM_COVERAGE_EXPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
        f.write("\n")


def _run_export() -> None:
    """Regenerate the production public export so calendar.html updates."""
    import subprocess
    script = os.path.join(_PIPELINE_DIR, "export_data.py")
    print("[automation] regenerating public export (export_data.py --public)…")
    subprocess.run([sys.executable, script, "--public"], check=False)
    print("[automation] regenerating team coverage export (assets/data/team_coverage.v1.json)…")
    _write_team_coverage_export(window_days=cfg.load_config().lookback_days)


def cmd_match_audit(args: argparse.Namespace) -> int:
    """Read-only per-match audit (Phase D2.1): current fixture_kind/lifecycle
    state and any proposed repair, with an explicit blocking reason for
    anything unresolved. Never writes — use `match-repair --write` to apply."""
    con = _open_content_db()
    try:
        report = mrepair.repair_matches(con, write=False)
    finally:
        con.close()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(mrepair.format_repair_report(report))
    return 0


def cmd_match_repair(args: argparse.Namespace) -> int:
    """Idempotent match-metadata repair (Phase D2.1). Dry-run by default;
    --write actually backfills fixture_kind/lifecycle_status, and ONLY ever
    fills a currently-NULL field (never overwrites a value FACEIT sync, a
    human, or a previous repair run already set)."""
    con = _open_content_db()
    try:
        report = mrepair.repair_matches(con, write=args.write)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(mrepair.format_repair_report(report))
        if args.coverage:
            cov_report = mec.build_coverage_report(
                con, repaired_this_run=len(report["repaired"]))
            print()
            print(mec.format_coverage_report(cov_report))
    finally:
        con.close()
    return 0


def cmd_export_coverage(args: argparse.Namespace) -> int:
    """Match export coverage report (Phase D2.1): exactly why every excluded
    match didn't reach the public dataset, computed straight from the real
    exporter (export_data.build_public_payload) — never a second opinion."""
    con = _open_content_db()
    try:
        report = mec.build_coverage_report(con)
    finally:
        con.close()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(mec.format_coverage_report(report))
    return 0


def cmd_coverage(args: argparse.Namespace) -> int:
    report = cov.build_report(window_days=args.window, automation_db=args.db)
    print(cov.format_report(report))
    if args.save:
        rid = cov.save_snapshot(args.db, report)
        print(f"\n[automation] coverage snapshot #{rid} saved to {args.db}")
    # Phase C6 — broadcast coverage over the same window, from the
    # automation DB's scheduled_matches/broadcast_candidates/broadcast_videos.
    channels = cfg.load_all_channels()
    supported_regions = {c["region"] for c in channels if c.get("platform") == "youtube" and c.get("region")}
    bcov = cov.build_broadcast_coverage(args.db, window_days=args.window, supported_regions=supported_regions)
    print()
    print(cov.format_broadcast_coverage(bcov))
    return 0


# ------------------------------------------------- Phase C1/C2/C3/C4 (YouTube)
def _build_youtube_client(args: argparse.Namespace, store: "js.JobStore | None" = None) -> yt.YouTubeClient:
    """Real API client (YOUTUBE_API_KEY), or an offline fixture client when
    --fixture-dir is given. Fixtures never touch the network. When `store` is
    given, every call's quota cost is persisted into the automation DB's
    quota_usage table (Phase C2) so `coverage` can report spend across runs.

    The response cache (data/raw/youtube_api/, gitignored, never committed)
    is enabled EVEN in dry-run — caching a read is not a production write,
    and it's what lets a repeated dry-run skip network calls/quota entirely
    within the cache TTL (see YouTubeClient's cache_ttl_seconds)."""
    quota_sink = None
    if store is not None:
        quota_sink = bdisc._record_quota(store, dt.datetime.now(dt.timezone.utc).date().isoformat())
    if getattr(args, "fixture_dir", None):
        return yt.YouTubeClient(transport=yt.fixture_transport(args.fixture_dir), quota_sink=quota_sink)
    cache = os.path.join(content_db.REPO_ROOT, "data", "raw", "youtube_api")
    return yt.YouTubeClient(cache_dir=cache, quota_sink=quota_sink)


def cmd_verify_channels(args: argparse.Namespace) -> int:
    """Verify every configured channel (enabled or not) against the live
    YouTube API (Phase C1). Read-only: NEVER edits
    config/broadcast_channels.json — a human applies the result, exactly
    like the FACEIT registry pass (see docs/FACEIT-REGISTRY.md)."""
    channels = cfg.load_all_channels()
    if not channels:
        print("[automation] no channels configured in config/broadcast_channels.json.")
        return 0
    client = _build_youtube_client(args)
    report = bdisc.verify_channels(client, channels)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"[automation] verify-channels: {report['verifiedCount']} verified, "
          f"{report['skippedCount']} skipped, {report['errorCount']} error/not-found")
    for r in report["channels"]:
        if r["status"] == "verified":
            print(f"  OK    {r['id']:<20} -> {r['channelId']}  {r['title']!r} "
                  f"(uploads={r['uploadsPlaylistId']})")
        elif r["status"] == "skipped":
            print(f"  SKIP  {r['id']:<20} {r['reason']}")
        else:
            print(f"  {r['status'].upper():<6}{r['id']:<20} {r.get('error') or ''}")
    if client.quota_used:
        print(f"  quota used: {client.quota_used} units {dict(client.quota_by_endpoint)}")
    print("\n  NOTE: this command never edits config/broadcast_channels.json —")
    print("  apply a verified channelId by hand (or a follow-up PR) after review.")
    return 0


def cmd_calendar_dryrun(args: argparse.Namespace) -> int:
    """Rolling official-calendar dry-run + reconciliation (Phase C7). Never
    writes. `--lookback-days` is accepted for CLI symmetry with the other
    dry-run commands; the official calendar is an EVENT-level source with no
    per-match rolling window, so it does not filter events by that value."""
    events = owcs_calendar.load_events()
    summary = disc.sync_calendar(store=None, events=events, dry_run=True)
    print(f"[automation] calendar-dryrun ({summary['events']} events, "
          f"{summary['unverified']} unverified; lookback-days={args.lookback_days} "
          f"accepted but not applied — event-level source has no rolling window):")
    for eid in summary["eventIds"]:
        print(f"    - {eid}")
    comps = cfg.load_all_competitions()
    channels = cfg.load_all_channels()
    warnings = rec.reconcile([], events, channels_by_id={c["id"]: c for c in channels},
                            competitions=[c for c in comps if c.get("enabled") and c.get("championshipId")])
    print(f"  reconciliation: {len(warnings)} warning(s)")
    for w in warnings[:20]:
        print(f"    [{w['code']}] {w['message']}")
    return 0


def _sanitize_title(title: str | None, max_len: int = 80) -> str:
    """Collapse newlines/control chars and cap length for the report — a
    title is public broadcast metadata, not a secret, but a report line
    should never let one blow up into multiple lines or an unbounded string."""
    t = " ".join((title or "(no title)").split())
    return t if len(t) <= max_len else t[: max_len - 1] + "…"


def _classify_video_kind(v: dict) -> str:
    """Human-readable one-liner: completed livestream / ordinary upload /
    upcoming stream / currently live — the exact distinction the C3
    diagnostic report needs per video."""
    status = v.get("liveBroadcastStatus")
    if status == "live":
        return "currently live"
    if status == "upcoming":
        return "upcoming stream"
    if status == "completed":
        return "completed livestream"
    return "ordinary upload (never a livestream)"


def _run_broadcast_discovery(args: argparse.Namespace) -> int:
    config = cfg.load_config()
    store = js.JobStore(args.db, config=config)
    try:
        client = _build_youtube_client(args, store=None if args.dry_run else store)
        channels = cfg.load_channels()
        disc_summary = bdisc.sync_broadcasts(
            client=client, store=store, channels=channels,
            lookback_days=args.lookback_days or config.lookback_days,
            horizon_days=args.horizon_days or config.schedule_horizon_days,
            dry_run=args.dry_run, allow_search_fallback=args.allow_search_fallback,
            full_history=getattr(args, "full_history", False))
        print(f"[automation] broadcast discovery ({'dry-run' if args.dry_run else 'live'}):")
        if disc_summary.get("note"):
            print(f"  note: {disc_summary['note']}")
        print(f"  channels discovered : {len(disc_summary['channels'])}")
        print(f"  pages fetched       : {disc_summary['pagesFetched']}")
        print(f"  cache hits          : {disc_summary['cacheHits']}")
        print(f"  videos inspected    : {disc_summary['videosSeen']}")
        print(f"  videos in window    : {disc_summary['inWindow']}")
        print(f"  upserted            : {disc_summary['upserted']} "
              f"({'dry-run — no writes' if args.dry_run else 'written'})")
        for e in disc_summary["errors"]:
            print(f"  API ERROR  {e['channelId']}: {e['error']}")

        # Root-cause fix: pass the videos discovered THIS run into matching —
        # a dry-run never persists to broadcast_videos, so relying on the DB
        # table here previously left matching with nothing to score (see
        # broadcast_discovery.sync_broadcasts's docstring).
        match_summary = bmatch.match_broadcasts(
            store, videos=disc_summary["videos"], dry_run=args.dry_run)
        print("[automation] broadcast matching:")
        t = match_summary["targetsLoaded"]
        total_targets = t["matches"] + t["sourceEvents"] + t["calendarEvents"]
        print(f"  matching targets loaded : {total_targets} total "
              f"({t['matches']} FACEIT match(es), {t['sourceEvents']} source_event(s), "
              f"{t['calendarEvents']} calendar event(s))")
        print(f"  videos scored            : {match_summary['videosScored']} "
              f"(every in-window video — a distinct-video count)")
        print(f"  candidate pairs evaluated: {match_summary['totalCandidatePairsEvaluated']} total "
              f"(video x target comparisons — NOT a video count; one video can pair with several targets)")
        print(f"    high-confidence pairs  : {match_summary['linked']}")
        print(f"    medium-confidence pairs: {match_summary['reviewed']}")
        print(f"    low-confidence pairs   : {match_summary['rejected']} (not stored)")
        dv = match_summary["distinctVideos"]
        print("  distinct videos by classification:")
        print(f"    high                     : {dv['high']}")
        print(f"    medium / review          : {dv['mediumOrReview']}")
        print(f"    rejected / unrelated     : {dv['rejectedOrUnrelated']}")
        print("  full classification breakdown:")
        for label in bmatch.ALL_CLASSIFICATIONS:
            n = match_summary["classifications"].get(label, 0)
            if n:
                print(f"    {label:<28}: {n}")
        accounted = sum(match_summary["classifications"].values())
        print(f"  videos accounted         : {accounted} / {disc_summary['inWindow']} in-window "
              f"({'OK — every video classified' if accounted == disc_summary['inWindow'] else 'MISMATCH'})")

        if disc_summary["videos"]:
            print("[automation] per-video diagnostic (every in-window video, no raw API response):")
            for v, r in zip(disc_summary["videos"], match_summary["results"]):
                print(f"  - {v['videoId']}  \"{_sanitize_title(v['title'])}\"")
                print(f"      kind            : {_classify_video_kind(v)}")
                print(f"      liveBroadcastContent-derived status: {v['liveBroadcastStatus']}")
                print(f"      publishedAt     : {v.get('publishedAt')}")
                print(f"      scheduledStartAt: {v.get('scheduledStartAt')}")
                print(f"      actualStartAt   : {v.get('actualStartAt')}")
                print(f"      actualEndAt     : {v.get('actualEndAt')}")
                print(f"      durationSeconds : {v.get('durationSeconds')}")
                print(f"      classification  : {r['classification']}")
                print("      reasons         :")
                for reason in r["reasons"]:
                    print(f"        - {reason}")
                print(f"      targets considered: {r['targetsConsidered']}"
                      + (f"  ({len(r['targetsFiltered'])} filtered out — see below)"
                         if r["targetsFiltered"] else ""))
                for f in r["targetsFiltered"][:10]:
                    print(f"        filtered {f['kind']} '{f['targetId']}': {f['reason']}")

        if client.quota_used or client.cache_hits:
            print(f"  quota used          : {client.quota_used} units {dict(client.quota_by_endpoint)}")
            print(f"  client cache hits   : {client.cache_hits}")
    finally:
        store.close()
    return 0


def cmd_broadcast_dryrun(args: argparse.Namespace) -> int:
    """`broadcast-dryrun` always forces --dry-run (Phase C3/C4 read-only
    demonstration): discover + score, write nothing."""
    args.dry_run = True
    return _run_broadcast_discovery(args)


def cmd_discover_broadcasts(args: argparse.Namespace) -> int:
    """`discover-broadcasts [--dry-run]` — the production broadcast
    discovery + matching entry point (Phase C3/C4/C5)."""
    return _run_broadcast_discovery(args)


# ----------------------------------------------- Phase 1 URL-only intake
def cmd_ingest_link(args: argparse.Namespace) -> int:
    """`ingest-link --url "<youtube-url>"` — THE operator entry point. One
    pasted broadcast URL becomes exactly one deterministic job; pasting it
    again (in any spelling) attaches to the same job. Never downloads
    anything: it records the link, retrieves public metadata, and either
    auto-approves a verified official channel or blocks on manual
    approval."""
    config = cfg.load_config()
    store = js.JobStore(args.db, config=config)
    try:
        client = None if args.no_metadata else _build_youtube_client(
            args, store=None if args.dry_run else store)
        try:
            result = li.ingest_link(
                store, args.url, client=client, dry_run=args.dry_run,
                requested_by=args.requested_by)
        except li.LinkIntakeError as exc:
            print(f"[intake] REFUSED [{exc.code}] {exc}")
            return 1
        if args.json:
            print(json.dumps(result, indent=2))
            return 0
        mode = "dry-run — nothing written" if args.dry_run else (
            "new job created" if result["created"] else
            "duplicate link — attached to the existing job")
        print(f"[intake] {mode}")
        print(f"  video id     : {result['videoId']}")
        print(f"  canonical url: {result['canonicalUrl']}")
        print(f"  job key      : {result['jobKey']}")
        print(f"  job state    : {result['state']}")
        src = result["source"]
        print(f"  source       : {src['state']} "
              f"({'automatic' if src['autoApproved'] else 'needs a human'})")
        print(f"    reason     : [{src['reasonCode']}] {src['reason']}")
        meta = result["metadata"]
        if meta.get("status") == "ok":
            print(f"  title        : {meta.get('title')}")
            print(f"  channel      : {meta.get('channelTitle')} ({meta.get('channelId')})")
            print(f"  duration     : {meta.get('durationSeconds')}s "
                  f"[{meta.get('liveBroadcastStatus')}]")
        else:
            print(f"  metadata     : {meta.get('status')} "
                  f"[{meta.get('errorCode')}] {meta.get('error')}")
        if result.get("likeness"):
            lk_ = result["likeness"]
            print(f"  broadcast-likeness: {lk_['confidence']} (score {lk_['score']})")
        for w in result["warnings"]:
            print(f"  WARNING      : {w}")
        print(f"  next command : {result['nextCommand']}")
        return 0
    finally:
        store.close()


def cmd_link_status(args: argparse.Namespace) -> int:
    """`link-status [--job <key> | --video-id <id> | --url <url>]` — the
    operator's read-only view of every pasted link: stage, source
    authorization, warnings, every blocking reason, and the exact next
    command."""
    store = js.JobStore(args.db)
    try:
        try:
            rows = li.link_status(store, job_key=args.job,
                                  video_id=args.video_id, url=args.url)
        except li.LinkIntakeError as exc:
            print(f"[intake] link-status FAILED [{exc.code}] {exc}")
            return 1
        if args.json:
            print(json.dumps(rows, indent=2))
            return 0
        print(f"[intake] {len(rows)} link job(s):")
        print(li.format_status(rows))
        return 0
    finally:
        store.close()


def cmd_approve_source(args: argparse.Namespace) -> int:
    """`approve-source --job <key> --approved-by <name> --confirm` — the one
    audited human gate for a source that is not on a verified official
    channel. `--reject` records an explicit refusal instead."""
    store = js.JobStore(args.db)
    try:
        try:
            result = li.approve_source(
                store, args.job, approved_by=args.approved_by,
                reason=args.reason, confirm=args.confirm, reject=args.reject)
        except li.LinkIntakeError as exc:
            print(f"[intake] approve-source FAILED [{exc.code}] {exc}")
            return 1
        verb = "REJECTED" if args.reject else "APPROVED"
        print(f"[intake] {args.job}: source {verb} by {args.approved_by}")
        print(f"  job state   : {result['state']}")
        print(f"  reason      : {result['source']['reason']}")
        print(f"  next command: {result['nextCommand']}")
        return 0
    finally:
        store.close()


# --------------------------------------------- Phase 3 layout resolution
def cmd_resolve_layout(args: argparse.Namespace) -> int:
    """`resolve-layout --job <id>` — fingerprint the broadcast against every
    committed layout and either reuse the matching one automatically or
    calibrate a NEW one for human approval. Reads the 360p scan proxy."""
    from automation import layout_resolver as lr
    store = js.JobStore(args.db)
    try:
        job = _job_or_exit(store, args.job)
        channels = {c.get("channelId"): c for c in cfg.load_all_channels()}
        preferred = ((channels.get(job.payload.get("channelId")) or {})
                     .get("preferredLayout"))
        try:
            result = lr.resolve_layout(
                store, job, preferred_layout=preferred,
                sample_count=args.samples, harvest=not args.no_harvest)
        except lr.LayoutRefusal as exc:
            print(f"[layout] REFUSED [{exc.code}] {exc}")
            return 1
        if args.json:
            print(json.dumps(result, indent=2))
            return 0
        print(f"[layout] resolve-layout {args.job}:")
        print(lr.format_resolution(result["record"]))
        return 0 if result.get("ok") else 1
    finally:
        store.close()


def cmd_approve_layout(args: argparse.Namespace) -> int:
    """`approve-layout --job <id> --confirm` — promote a generated layout
    into layouts/ after reviewing its sheet. Never approves a calibration
    the calibrator itself refused."""
    from automation import layout_resolver as lr
    store = js.JobStore(args.db)
    try:
        try:
            result = lr.approve_layout(store, args.job, confirm=args.confirm,
                                       approved_by=args.approved_by)
        except lr.LayoutRefusal as exc:
            print(f"[layout] approve-layout FAILED [{exc.code}] {exc}")
            return 1
        print(f"[layout] {args.job}: layout {result.get('layoutId')} approved")
        if result.get("layoutPath"):
            print(f"  layout file : {result['layoutPath']}")
        if result.get("templatesDir"):
            print(f"  templates   : {result['templatesDir']} "
                  f"(run template-coverage next)")
        if result.get("note"):
            print(f"  note        : {result['note']}")
        return 0
    finally:
        store.close()


# ------------------------------------------------ Phase 4 segment identity
def cmd_propose_identity(args: argparse.Namespace) -> int:
    """`propose-identity --job <id>` — read map, mode, teams, sides, order and
    player nameplates off the broadcast for every pending segment, store the
    proposals with evidence, and name every disagreement as a review task.
    Writes proposals only; never confirms anything."""
    from automation import segment_identity as si
    from automation import segmentation as seg
    import capture
    import ocr_hud
    from automation import worker as wk
    store = js.JobStore(args.db)
    con = _open_content_db()
    try:
        job = _job_or_exit(store, args.job)
        video_id = job.payload.get("videoId")
        layout_id = job.payload.get("expectedLayoutId")
        if not layout_id:
            print(f"[identity] {args.job} has no resolved layout — "
                  f"run resolve-layout first")
            return 1
        scan_path = wk.scan_path_for(job)
        if not scan_path or not os.path.exists(scan_path):
            print(f"[identity] {args.job} has no 360p scan proxy on disk — "
                  f"re-run the worker")
            return 1
        from automation import detection_runner as dr
        layout = capture.load_layout(dr.layout_path(layout_id))
        segments = [s for s in seg.list_segments(store.con, video_id=video_id)
                    if s["review_status"] in ("pending", "approved")]
        if args.segment_id:
            segments = [s for s in segments if s["id"] == args.segment_id]
        if not segments:
            print(f"[identity] no pending/approved segment for {args.job}")
            return 1
        read_fn = ocr_hud.make_reader(args.ocr_engine)
        rc = 0
        for s in segments:
            frames = si_sample_frames(scan_path, s, args.samples)
            proposal = si.propose_identity(
                store.con, con, s, layout=layout, frames=frames,
                read_fn=read_fn, match_id=job.payload.get("matchId"))
            si.store_proposals(store.con, s["id"], proposal)
            print(f"[identity] segment #{s['id']} "
                  f"[{s['start_time']:.0f}-{s['end_time']:.0f}]:")
            print(si.format_proposal(proposal))
            if proposal["identityStatus"] == "blocked":
                rc = 1
        return rc
    finally:
        con.close()
        store.close()


def si_sample_frames(scan_path: str, segment: dict, count: int
                     ) -> list[tuple[float, str]]:
    """Sample frames from INSIDE one segment window, on the scan proxy."""
    from automation import layout_resolver as lr
    import subprocess
    start, end = float(segment["start_time"]), float(segment["end_time"])
    span = max(end - start, 1.0)
    out_dir = os.path.join(content_db.REPO_ROOT, "reports", "identity",
                           str(segment["video_id"]), f"seg{segment['id']}")
    os.makedirs(out_dir, exist_ok=True)
    step = span / (count + 1)
    frames: list[tuple[float, str]] = []
    for i in range(count):
        t = start + step * (i + 1)
        path = os.path.join(out_dir, f"frame_{int(t):08d}.png")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-ss", str(t), "-i", scan_path, "-frames:v", "1", path]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True,
                           **proc_text.PIPE_TEXT)
        except Exception:  # noqa: BLE001 — one bad seek is not fatal
            continue
        if os.path.exists(path):
            frames.append((t, path))
    return frames


def cmd_accept_proposed(args: argparse.Namespace) -> int:
    """`accept-proposed --segment <id>` — approve a segment using the values
    the machine proposed. Refuses a proposal with any blocking review task or
    any still-UNKNOWN required field."""
    from automation import segment_identity as si
    store = js.JobStore(args.db)
    try:
        try:
            row = si.accept_proposed(store.con, args.segment,
                                     reviewer_note=args.note,
                                     layout_id=args.layout_id)
        except ValueError as exc:
            print(f"[identity] accept-proposed REFUSED: {exc}")
            return 1
        print(f"[identity] segment {args.segment} approved from proposal: "
              f"{row['map_name']} ({row['map_mode']}) map {row['candidate_map_order']}, "
              f"{row['team_a']} vs {row['team_b']}, side={row['side_assignment']}")
        return 0
    finally:
        store.close()


def _job_or_exit(store: "js.JobStore", job_key: str) -> models.Job:
    job = store.get(job_key)
    if job is None:
        raise SystemExit(f"[automation] no such job: {job_key}")
    return job


def _print_job(job: models.Job) -> None:
    print(f"  job_key      : {job.job_key}")
    print(f"  kind/state   : {job.kind} / {job.state}")
    print(f"  worker_id    : {job.worker_id}")
    print(f"  attempts     : {job.attempts} (max {job.max_attempts})")
    print(f"  last_error   : [{job.last_error_code}] {job.last_error_message}")
    print(f"  next_retry_at: {job.next_retry_at}")
    print(f"  source_url   : {job.source_url}")
    print(f"  updated_at   : {job.updated_at}")
    print(f"  payload      : {json.dumps(job.payload, indent=2)}")


# ------------------------------------------------------- Phase 1 job spine
def cmd_create_job(args: argparse.Namespace) -> int:
    from automation import ops
    store = js.JobStore(args.db)
    try:
        job = ops.create_job_from_broadcast(
            store, match_id=args.match, video_id=args.video_id,
            source_url=args.source_url, channel_id=args.channel_id,
            team_a=args.team_a, team_b=args.team_b,
            tournament_id=args.tournament, region=args.region,
            language=args.language, broadcast_authority=args.channel_id,
            expected_layout_id=args.layout_id)
        print(f"[automation] job ready: {job.job_key} (state={job.state})")
        return 0
    finally:
        store.close()


def cmd_list_jobs(args: argparse.Namespace) -> int:
    from automation import ops
    store = js.JobStore(args.db)
    try:
        jobs = ops.list_jobs(store, state=args.state)
        if args.json:
            print(json.dumps([j.__dict__ for j in jobs], indent=2, default=str))
            return 0
        print(f"[automation] {len(jobs)} job(s)"
              + (f" in state {args.state}" if args.state else ""))
        for j in jobs:
            print(f"  {j.job_key:<40} {j.state:<20} worker={j.worker_id} "
                  f"attempts={j.attempts} next_action={ops.recommended_next_action(j)}")
        return 0
    finally:
        store.close()


def cmd_show_job(args: argparse.Namespace) -> int:
    store = js.JobStore(args.db)
    try:
        job = _job_or_exit(store, args.job)
        _print_job(job)
        return 0
    finally:
        store.close()


def cmd_claim_job(args: argparse.Namespace) -> int:
    from automation import ops
    store = js.JobStore(args.db)
    try:
        locks = lk.LockManager(store.con)
        job = ops.claim_next_job(store, locks, args.worker_id)
        if job is None:
            print("[automation] no eligible job to claim.")
            return 0
        print(f"[automation] claimed {job.job_key} (state={job.state}) "
              f"for worker {args.worker_id}")
        return 0
    finally:
        store.close()


def cmd_release_job(args: argparse.Namespace) -> int:
    from automation import ops
    store = js.JobStore(args.db)
    try:
        locks = lk.LockManager(store.con)
        ops.release_job(store, locks, args.job, args.worker_id)
        print(f"[automation] released {args.job}")
        return 0
    finally:
        store.close()


def cmd_retry_job(args: argparse.Namespace) -> int:
    from automation import ops
    store = js.JobStore(args.db)
    try:
        job = ops.retry_job(store, args.job, force=args.force)
        print(f"[automation] {args.job} -> {job.state} (next_retry_at={job.next_retry_at})")
        return 0
    except (KeyError, ValueError) as exc:
        print(f"[automation] retry-job FAILED: {exc}")
        return 1
    finally:
        store.close()


def cmd_cancel_job(args: argparse.Namespace) -> int:
    from automation import ops
    store = js.JobStore(args.db)
    try:
        locks = lk.LockManager(store.con)
        job = ops.cancel_job(store, locks, args.job, reason=args.reason)
        print(f"[automation] {args.job} -> {job.state}")
        return 0
    finally:
        store.close()


def cmd_reset_stale_lock(args: argparse.Namespace) -> int:
    from automation import ops
    store = js.JobStore(args.db)
    try:
        locks = lk.LockManager(store.con)
        cleared = ops.reset_stale_lock(store, locks, args.job)
        print(f"[automation] {args.job}: "
              f"{'stale lock cleared' if cleared else 'no stale lock found'}")
        return 0
    finally:
        store.close()


def cmd_resume_job(args: argparse.Namespace) -> int:
    from automation import ops
    store = js.JobStore(args.db)
    try:
        locks = lk.LockManager(store.con)
        results = ops.resume_interrupted_job(store, locks, worker_id=args.worker_id)
        print(f"[automation] resumed {len(results)} interrupted job(s)")
        for r in results:
            print(f"  {r}")
        return 0
    finally:
        store.close()


def cmd_run_job(args: argparse.Namespace) -> int:
    from automation import ops
    store = js.JobStore(args.db)
    try:
        locks = lk.LockManager(store.con)
        # ops.run_one_job's segment lookups (map_segments) live in the
        # AUTOMATION db — the same store.con already open here, never a
        # separate content-db connection.
        result = ops.run_one_job(store, locks, store.con, args.job,
                                 worker_id=args.worker_id)
        print(f"[automation] run-job {args.job}: {result}")
        return 0 if result.get("ok") else 1
    finally:
        store.close()


JOB_COVERAGE_EXPORT_PATH = os.path.join(
    content_db.REPO_ROOT, "assets", "data", "job_coverage.v1.json")


def cmd_job_coverage(args: argparse.Namespace) -> int:
    from automation import ops
    store = js.JobStore(args.db)
    try:
        report = ops.build_job_coverage_report(store, window_hours=args.window_hours)
        # every job in the report, with the dashboard's recommended next action
        report["jobs"] = [
            dict(jobKey=j.job_key, kind=j.kind, state=j.state,
                workerId=j.worker_id, attempts=j.attempts,
                lastErrorCode=j.last_error_code,
                lastErrorMessage=j.last_error_message,
                updatedAt=j.updated_at,
                match=j.payload.get("matchId"), teamA=j.payload.get("teamA"),
                teamB=j.payload.get("teamB"), videoId=j.payload.get("videoId"),
                nextAction=ops.recommended_next_action(j))
            for j in store.list_jobs()
        ]
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"[automation] job coverage (last {report['windowHours']}h):")
            print(f"  total jobs      : {report['totalJobs']}")
            print(f"  recently updated: {report['recentlyUpdated']}")
            print(f"  by state        : {report['countsByState']}")
            print(f"  blocked         : {len(report['blocked'])}")
            for b in report["blocked"]:
                print(f"    {b['jobKey']:<40} [{b['state']}] {b['issue']}")
        if args.save:
            os.makedirs(os.path.dirname(JOB_COVERAGE_EXPORT_PATH), exist_ok=True)
            with open(JOB_COVERAGE_EXPORT_PATH, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=1)
                f.write("\n")
            print(f"\n[automation] wrote "
                  f"{os.path.relpath(JOB_COVERAGE_EXPORT_PATH, content_db.REPO_ROOT)}")
        return 0
    finally:
        store.close()


def cmd_worker_doctor(args: argparse.Namespace) -> int:
    """Windows-worker preflight checklist: Python, repo deps, yt-dlp/ffmpeg/
    ffprobe + versions, disk space, worker-cache + artifact directory
    writability, gh CLI auth, and API-key presence. Read-only (a self-
    deleting write probe only); never prints or logs a secret value."""
    from automation import worker
    media_root = args.media_root if args.media_root is not None else worker.DEFAULT_MEDIA_ROOT
    min_free_gb = args.min_free_gb if args.min_free_gb is not None else worker.DEFAULT_MIN_FREE_GB
    report = worker.doctor_report(media_root=media_root, min_free_gb=min_free_gb)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("[worker] doctor:")
        print(worker.format_doctor_report(report))
    return 0 if report["ok"] else 1


def cmd_worker_run(args: argparse.Namespace) -> int:
    """The self-hosted worker's main entry point (Phase E): register an
    identity, run preflight, claim + lock the next eligible job, process it
    one step (download/segment/detect/commit — see ops.run_one_job), release,
    repeat up to --max-jobs times (default: process exactly one and exit)."""
    from automation import ops
    from automation import worker
    media_root = args.media_root if args.media_root is not None else worker.DEFAULT_MEDIA_ROOT
    store = js.JobStore(args.db)
    wid = args.worker_id or worker.worker_identity()
    print(f"[worker] identity: {wid}")
    report = worker.preflight(media_root=media_root)
    print(f"[worker] preflight: {report}")
    if not report["ok"]:
        print("[worker] preflight FAILED — fix missing dependencies/disk before running.")
        return 1
    locks = lk.LockManager(store.con)
    try:
        # Recover any job a previous crashed worker left stuck mid-download.
        ops.resume_interrupted_job(store, locks, worker_id=wid,
                                   media_root=media_root)
        processed = 0
        while processed < args.max_jobs:
            job = ops.claim_next_job(store, locks, wid)
            if job is None:
                print("[worker] no eligible job — nothing to do.")
                break
            print(f"[worker] processing {job.job_key} ({job.state})")
            # ops.run_one_job's segment lookups (map_segments) live in the
            # AUTOMATION db — the same store.con, never a separate
            # content-db connection.
            result = ops.run_one_job(store, locks, store.con, job.job_key,
                                     worker_id=wid, media_root=media_root)
            print(f"[worker] result: {result}")
            locks.release(worker.resource_for(job), wid)
            processed += 1
        return 0
    finally:
        store.close()


# --------------------------------------------------------- Phase F segments
# NOTE: `map_segments` lives in the AUTOMATION database (schema.sql under
# pipeline/automation/, alongside jobs/locks/publication_runs) — NOT the
# content database (data/owcs.sqlite, matches/teams/heroes). Every segment
# command below therefore reuses the already-open JobStore's `.con`, never
# `_open_content_db()`.
def cmd_segment_list(args: argparse.Namespace) -> int:
    from automation import segmentation as seg
    store = js.JobStore(args.db)
    try:
        rows = seg.list_segments(store.con, video_id=args.video_id,
                                 review_status=args.status)
        if args.json:
            print(json.dumps(rows, indent=2))
            return 0
        print(f"[automation] {len(rows)} segment(s)")
        for r in rows:
            print(f"  #{r['id']:<4} {r['video_id']:<15} "
                  f"[{r['start_time']:.0f}-{r['end_time']:.0f}] "
                  f"conf={r['confidence']:.2f} status={r['review_status']} "
                  f"map={r.get('map_name')}")
        return 0
    finally:
        store.close()


def cmd_segment_approve(args: argparse.Namespace) -> int:
    from automation import segmentation as seg
    store = js.JobStore(args.db)
    try:
        row = seg.approve_segment(
            store.con, args.segment_id, map_order=args.map_order,
            map_name=args.map_name, map_mode=args.map_mode,
            team_a=args.team_a, team_b=args.team_b,
            side_assignment=args.side, layout_id=args.layout_id,
            reviewer_note=args.note)
        print(f"[automation] segment {args.segment_id} approved: {row['map_name']} "
              f"({row['team_a']} vs {row['team_b']})")
        return 0
    finally:
        store.close()


def cmd_segment_reject(args: argparse.Namespace) -> int:
    from automation import segmentation as seg
    store = js.JobStore(args.db)
    try:
        seg.reject_segment(store.con, args.segment_id, reason=args.reason)
        print(f"[automation] segment {args.segment_id} rejected: {args.reason}")
        return 0
    finally:
        store.close()


def cmd_segment_split(args: argparse.Namespace) -> int:
    """Replace one candidate with two, at `--at` seconds. The original row is
    kept (marked 'split') so its confidence provenance survives."""
    from automation import segmentation as seg
    store = js.JobStore(args.db)
    try:
        first, second = seg.split_segment(store.con, args.segment_id,
                                          split_time=args.at)
        print(f"[automation] segment {args.segment_id} split at {args.at}s -> "
              f"#{first['id']} [{first['start_time']:.0f}-{first['end_time']:.0f}] "
              f"and #{second['id']} [{second['start_time']:.0f}-{second['end_time']:.0f}]")
        return 0
    except (ValueError, seg.SegmentNotFound) as exc:
        print(f"[automation] segment-split FAILED: {exc}")
        return 1
    finally:
        store.close()


def cmd_segment_merge(args: argparse.Namespace) -> int:
    """Replace two candidates with one covering their union. Both originals
    are kept (marked 'merged')."""
    from automation import segmentation as seg
    store = js.JobStore(args.db)
    try:
        merged = seg.merge_segments(store.con, args.segment_id, args.other)
        print(f"[automation] merged {args.segment_id}+{args.other} -> "
              f"#{merged['id']} [{merged['start_time']:.0f}-{merged['end_time']:.0f}]")
        return 0
    except (ValueError, seg.SegmentNotFound) as exc:
        print(f"[automation] segment-merge FAILED: {exc}")
        return 1
    finally:
        store.close()


INTAKE_EXPORT_PATH = os.path.join(
    content_db.REPO_ROOT, "assets", "data", "intake.v1.json")


def _save_intake_export(store: "js.JobStore",
                        job_key: str | None = None) -> None:
    """Write assets/data/intake.v1.json (what intake.html renders) — shared
    by intake-export --save, convert-link and autopilot so the panel is
    never stale after an automatic pass."""
    report = li.build_intake_report(store, job_key=job_key)
    os.makedirs(os.path.dirname(INTAKE_EXPORT_PATH), exist_ok=True)
    with open(INTAKE_EXPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
        f.write("\n")
    print(f"[intake] wrote "
          f"{os.path.relpath(INTAKE_EXPORT_PATH, content_db.REPO_ROOT)} "
          f"({len(report['jobs'])} job(s))")


def cmd_intake_export(args: argparse.Namespace) -> int:
    """`intake-export [--save]` — the operator review panel's data source:
    every pasted link's stage, next command, blockers, segment timeline,
    thumbnails and proposed identity. Read-only; `--save` writes the small,
    non-sensitive JSON `intake.html` reads."""
    store = js.JobStore(args.db)
    try:
        report = li.build_intake_report(store, job_key=args.job)
        if args.json or not args.save:
            print(json.dumps(report, indent=1))
        if args.save:
            _save_intake_export(store, job_key=args.job)
        return 0
    finally:
        store.close()


# ------------------------------------------------------- Phase G/I actions
def cmd_detect_job(args: argparse.Namespace) -> int:
    from automation import detection_runner as dr
    from automation import segmentation as seg
    store = js.JobStore(args.db)
    try:
        job = _job_or_exit(store, args.job)
        segments = seg.list_segments(store.con, video_id=job.payload.get("videoId"),
                                     review_status="approved")
        if not segments:
            print(f"[automation] no approved segment for {args.job}")
            return 1
        if job.state == sm.READY_FOR_DETECTION:
            store.transition(job.job_key, sm.PROCESSING)
            job = store.get(job.job_key)
        if args.write:
            result = dr.commit_approved_detection(store, job, segments[0])
        else:
            result = dr.run_detection(store, job, segments[0], write=False)
        print(f"[automation] detect-job {args.job}: {result}")
        return 0 if result.get("ok") else 1
    finally:
        store.close()


def cmd_process_approved_job(args: argparse.Namespace) -> int:
    """`process-approved-job --job <id> [--publish]` — the one supervised
    command that coordinates promotion + export + validation + publication.
    Default is a dry run (validate everything, write/push nothing); pass
    --publish to actually create + push the publication commit.

    Two databases, wired deliberately: `seg.list_segments` reads the
    approved segment from the AUTOMATION db (store.con — map_segments lives
    there); `pub.publish_job`'s match/team precondition checks need the
    CONTENT db (con — matches/teams live there)."""
    from automation import publish as pub
    from automation import segmentation as seg
    store = js.JobStore(args.db)
    con = _open_content_db()
    try:
        job = _job_or_exit(store, args.job)
        segments = seg.list_segments(store.con, video_id=job.payload.get("videoId"),
                                     review_status="approved")
        if not segments:
            print(f"[automation] no approved segment for {args.job} — refusing.")
            return 1
        result = pub.publish_job(store, con, job, segments[0],
                                 dry_run=not args.publish)
        print(f"[automation] process-approved-job {args.job}: {result}")
        return 0 if result.get("ok") else 1
    finally:
        con.close()
        store.close()


# ------------------------------------------------- autopilot / convert-link
def _run_autopilot(store: "js.JobStore", args: argparse.Namespace,
                   job_key: str) -> dict:
    """Shared driver for `autopilot` and `convert-link`: run the free-agent
    loop, then refresh the intake panel export so intake.html is never
    stale after an automatic pass."""
    from automation import autopilot as ap
    from automation import worker
    locks = lk.LockManager(store.con)
    wid = args.worker_id or worker.worker_identity("autopilot")
    result = ap.run_autopilot(
        store, locks, job_key, worker_id=wid,
        media_root=args.media_root or None,
        auto_accept=args.auto_accept,
        accepted_by=args.accepted_by or args.worker_id,
        for_harvest=getattr(args, "for_harvest", False),
        max_steps=args.max_steps or ap.DEFAULT_MAX_STEPS,
        samples=args.samples, ocr_engine=args.ocr_engine)
    if not args.no_export:
        _save_intake_export(store)
    return result


def cmd_autopilot(args: argparse.Namespace) -> int:
    """`autopilot --job <key> | --url <url>` — advance an EXISTING intake job
    through every automatic stage in one command, stopping honestly at the
    first human gate (source/layout/detection review, publication)."""
    store = js.JobStore(args.db)
    try:
        if bool(args.job) == bool(args.url):
            print("[autopilot] provide exactly one of --job / --url")
            return 1
        try:
            job_key = args.job or li.job_key_for(
                li.parse_link(args.url)["videoId"])
        except li.LinkIntakeError as exc:
            print(f"[autopilot] REFUSED [{exc.code}] {exc}")
            return 1
        try:
            result = _run_autopilot(store, args, job_key)
        except KeyError as exc:
            print(f"[autopilot] {exc.args[0]} — paste the link first: "
                  f"ingest-link/convert-link --url \"<youtube-url>\"")
            return 1
        from automation import autopilot as ap
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"[autopilot] {job_key}:")
            print(ap.format_result(result))
        return 0 if result["ok"] else 1
    finally:
        store.close()


def cmd_convert_link(args: argparse.Namespace) -> int:
    """`convert-link --url "<youtube-url>"` — the ONE match-day command:
    ingest the pasted link, then run the autopilot loop as far as the
    evidence allows, then refresh the intake panel. Ends either at a human
    gate (printed, with the exact next command) or at a blocker (printed,
    with the reason)."""
    from automation import autopilot as ap
    config = cfg.load_config()
    store = js.JobStore(args.db, config=config)
    try:
        client = None if args.no_metadata else _build_youtube_client(
            args, store=store)
        try:
            ingest = li.ingest_link(store, args.url, client=client,
                                    requested_by=args.requested_by)
        except li.LinkIntakeError as exc:
            print(f"[convert] REFUSED [{exc.code}] {exc}")
            return 1
        print(f"[convert] link {'attached to existing job' if ingest['duplicate'] else 'ingested'}: "
              f"{ingest['jobKey']} ({ingest['canonicalUrl']})")
        src = ingest["source"]
        print(f"  source     : {src['state']} [{src['reasonCode']}] {src['reason']}")
        for w in ingest["warnings"]:
            print(f"  WARNING    : {w}")
        result = _run_autopilot(store, args, ingest["jobKey"])
        if args.json:
            print(json.dumps({"ingest": ingest, "autopilot": result}, indent=2))
        else:
            print(f"[convert] autopilot for {ingest['jobKey']}:")
            print(ap.format_result(result))
        return 0 if result["ok"] else 1
    finally:
        store.close()


def cmd_find_matches(args: argparse.Namespace) -> int:
    """`find-matches` — the free auto match finder. Scans every VERIFIED
    broadcast channel on two permanently-free, no-key sources (the
    channel's public RSS feed + its /streams tab via yt-dlp), scores every
    video with the SAME tuned broadcast-likeness gate intake trusts, and
    keeps an idempotent ledger of every OWCS broadcast ever seen
    (data/match_finder.json) plus the static portal snapshot
    (assets/data/matchfinder.v1.json). Never downloads video, never
    approves anything. --queue-likely registers likely broadcasts through
    the SAME ingest_link gate as a hand-pasted URL — source approval rules
    unchanged."""
    from automation import match_finder as mf
    chans = mf.scan_channels()
    if not chans and not args.channel_url:
        print("[match-finder] no verified+enabled channel with a confirmed "
              "channelId in config/broadcast_channels.json — run "
              "verify-channels first, or pass --channel-url")
        return 1
    ledger = mf.scan(
        chans, limit=args.limit,
        extra_channel_urls=[args.channel_url] if args.channel_url else None)
    if not args.dry_run:
        mf.save_ledger(ledger)
    queued: list[dict] = []
    if args.queue_likely:
        config = cfg.load_config()
        store = js.JobStore(args.db, config=config)
        try:
            client = None if args.no_metadata else _build_youtube_client(
                args, store=None if args.dry_run else store)
            for cand in ledger["candidates"]:
                if (cand.get("likeness") or {}).get("confidence") != "likely":
                    continue
                if store.get(li.job_key_for(cand["videoId"])) is not None:
                    continue
                try:
                    res = li.ingest_link(
                        store, cand["url"], client=client,
                        dry_run=args.dry_run,
                        requested_by=args.requested_by or "match-finder")
                    queued.append({"videoId": cand["videoId"],
                                   "jobKey": res["jobKey"],
                                   "sourceState": res["source"]["state"]})
                except li.LinkIntakeError as exc:
                    print(f"[match-finder] queue refused {cand['videoId']} "
                          f"[{exc.code}] {exc}")
        finally:
            store.close()
    report = mf.build_report(args.db, ledger=ledger)
    if not args.dry_run:
        mf.export_snapshot(report)
    if args.json:
        print(json.dumps({"report": report, "queued": queued}, indent=2))
        return 0
    print(mf.format_report(report))
    for q in queued:
        print(f"[match-finder] queued {q['videoId']} -> {q['jobKey']} "
              f"(source {q['sourceState']})")
    if args.dry_run:
        print("[match-finder] dry run — nothing written")
    return 0


def cmd_media_probe(args: argparse.Namespace) -> int:
    """`media-probe --url <url>` — prove REAL video bytes can be downloaded
    before committing to a multi-hour broadcast, and report which fallback
    rung worked. Metadata extraction alone is not proof and is not used."""
    import video_ingest as vi
    import ytdlp_opts as yo
    url = args.url
    if not url and args.job:
        store = js.JobStore(args.db)
        try:
            job = _job_or_exit(store, args.job)
            url = job.payload.get("sourceUrl")
        finally:
            store.close()
    if not url:
        print("[probe] provide --url or --job")
        return 1
    try:
        result = vi.media_download_probe(url, height=args.height,
                                         seconds=args.seconds)
    except vi.MediaDownloadError as exc:
        payload = {"ok": False, "errorCode": exc.code,
                   "errorMessage": yo.redact_text(str(exc)),
                   "remedy": exc.remedy, "attempts": exc.attempts}
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"[probe] FAILED [{exc.code}] {yo.redact_text(str(exc))}")
            if exc.remedy:
                print(f"  remedy: {exc.remedy}")
            print("  attempts:")
            for a in exc.attempts:
                mark = "ok" if a.get("ok") else (
                    "skip" if a.get("note") == "skipped" else "FAIL")
                print(f"    [{mark:>4}] {a.get('rung'):<28} "
                      f"{a.get('errorCode') or a.get('errorMessage') or ''}")
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"[probe] OK via rung {result['rung']} — {result['bytes']} bytes, "
              f"{result.get('width')}x{result.get('height')}")
        if result.get("qualityDowngrade"):
            print("  WARNING: quality downgrade — no working stream at the "
                  "requested height")
    # Record the verdict on the job when we were given one.
    if args.job:
        store = js.JobStore(args.db)
        try:
            store.update_payload(args.job, {"mediaProbe": {
                "ok": True, "rung": result["rung"],
                "bytes": result.get("bytes"),
                "width": result.get("width"), "height": result.get("height"),
                "qualityDowngrade": result.get("qualityDowngrade"),
                "attempts": result.get("attempts") or [],
                "checkedAt": dt.datetime.now(dt.timezone.utc)
                    .replace(microsecond=0).isoformat()}})
        finally:
            store.close()
    return 0


def cmd_download_status(args: argparse.Namespace) -> int:
    """`download-status` — the download stack + resolved auth config + the
    fallback ladder + which layouts can actually detect. Read-only; never
    installs anything and never prints a secret."""
    import ytdlp_opts as yo
    import detection_assets as da
    report = yo.dependency_report()
    auth = yo.load_auth_config()
    ladder = [r.describe() for r in yo.build_ladder(auth)]
    assets = da.audit_all_layouts()
    payload = {"download": report, "ladder": ladder,
               "detectionAssets": assets}
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0 if report["ok"] else 1
    print("[download] stack:")
    print(yo.format_dependency_report(report))
    print("\n[download] fallback ladder:")
    for i, rung in enumerate(ladder, start=1):
        state = "ready" if rung["runnable"] else f"SKIP — {rung['skipReason']}"
        print(f"  {i}. {rung['rung']:<28} {state}")
    print("\n[download] detection assets:")
    for a in assets:
        print(da.format_report(a))
    return 0 if report["ok"] else 1


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
    cvp.add_argument("--window", type=int, default=30, help="lookback days")
    cvp.add_argument("--save", action="store_true", help="persist a coverage snapshot")
    cvp.set_defaults(func=cmd_coverage)

    # ---- Phase D2.1 match export repair ----------------------------------
    ma_p = sub.add_parser("match-audit",
                          help="read-only per-match fixture/lifecycle audit (D2.1)")
    ma_p.add_argument("--json", action="store_true")
    ma_p.set_defaults(func=cmd_match_audit)

    mr_p = sub.add_parser("match-repair",
                          help="idempotent match fixture_kind/lifecycle_status repair (D2.1)")
    mr_p.add_argument("--write", action="store_true",
                      help="actually backfill (default: dry-run, zero writes)")
    mr_p.add_argument("--json", action="store_true")
    mr_p.add_argument("--coverage", action="store_true",
                      help="also print the match export coverage report afterward")
    mr_p.set_defaults(func=cmd_match_repair)

    ec_p = sub.add_parser("export-coverage",
                          help="why every excluded match isn't in the public export (D2.1)")
    ec_p.add_argument("--json", action="store_true")
    ec_p.set_defaults(func=cmd_export_coverage)

    # ---- Phase B discovery sync commands --------------------------------
    def _add_sync_opts(sp):
        sp.add_argument("--dry-run", action="store_true",
                        help="fetch + reconcile, write nothing")
        sp.add_argument("--lookback-days", type=int, default=None)
        sp.add_argument("--horizon-days", type=int, default=None)
        sp.add_argument("--fixture-dir", default=None,
                        help="serve FACEIT responses from local fixtures (offline)")
        sp.add_argument("--export", action="store_true",
                        help="regenerate public_data.v1.js after a live sync")

    sf = sub.add_parser("sync-faceit", help="sync enabled FACEIT competitions (B2)")
    _add_sync_opts(sf)
    sf.set_defaults(func=cmd_sync_faceit)

    sc = sub.add_parser("sync-calendar", help="load official OWCS calendar (B3)")
    sc.add_argument("--dry-run", action="store_true")
    sc.set_defaults(func=cmd_sync_calendar)

    sa = sub.add_parser("sync-all", help="FACEIT + calendar sync + reconcile (B)")
    _add_sync_opts(sa)
    sa.set_defaults(func=cmd_sync_all)

    # ---- read-only candidate discovery / verification (registry config) --
    lc = sub.add_parser("list-championships",
                        help="search OW2 championships to confirm ids (read-only)")
    lc.add_argument("--query", default="OWCS", help="name search (default: OWCS)")
    lc.add_argument("--game", default="ow2")
    lc.add_argument("--type", default="all",
                    choices=["all", "upcoming", "ongoing", "past"])
    lc.add_argument("--organizer", default=None,
                    help="list this organizer's championships instead of searching")
    lc.add_argument("--limit", type=int, default=20)
    lc.add_argument("--fixture-dir", default=None)
    lc.add_argument("--json", action="store_true")
    lc.set_defaults(func=cmd_list_championships)

    lo = sub.add_parser("list-organizers", help="search organizers (read-only)")
    lo.add_argument("--query", default="Overwatch")
    lo.add_argument("--fixture-dir", default=None)
    lo.add_argument("--json", action="store_true")
    lo.set_defaults(func=cmd_list_organizers)

    vc = sub.add_parser("verify-competition",
                        help="retrieve one championship's official details")
    vc.add_argument("championship_id")
    vc.add_argument("--fixture-dir", default=None)
    vc.add_argument("--json", action="store_true")
    vc.set_defaults(func=cmd_verify_competition)

    vr = sub.add_parser("verify-registry",
                        help="verify every ENABLED competition via the FACEIT API")
    vr.add_argument("--fixture-dir", default=None)
    vr.set_defaults(func=cmd_verify_registry)

    # ---- Phase C1/C2/C3/C4 broadcast discovery commands -------------------
    vc2 = sub.add_parser("verify-channels",
                         help="verify every configured YouTube channel via the Data API (C1)")
    vc2.add_argument("--fixture-dir", default=None,
                     help="serve YouTube responses from local fixtures (offline)")
    vc2.add_argument("--json", action="store_true")
    vc2.set_defaults(func=cmd_verify_channels)

    cd = sub.add_parser("calendar-dryrun",
                        help="rolling official-calendar dry-run + reconciliation (C7)")
    cd.add_argument("--lookback-days", type=int, default=14)
    cd.set_defaults(func=cmd_calendar_dryrun)

    bd_p = sub.add_parser("broadcast-dryrun",
                          help="YouTube broadcast discovery + matching, read-only (C3/C4)")
    bd_p.add_argument("--lookback-days", type=int, default=None)
    bd_p.add_argument("--horizon-days", type=int, default=None)
    bd_p.add_argument("--fixture-dir", default=None,
                      help="serve YouTube responses from local fixtures (offline)")
    bd_p.add_argument("--allow-search-fallback", action="store_true",
                      help="permit the quota-expensive search.list fallback (C2/C4)")
    bd_p.add_argument("--full-history", action="store_true",
                      help="walk a channel's ENTIRE upload history instead of stopping "
                           "pagination at lookback+buffer (expensive; off by default)")
    bd_p.set_defaults(dry_run=True, func=cmd_broadcast_dryrun)

    db_p = sub.add_parser("discover-broadcasts",
                          help="YouTube broadcast discovery + matching (C3/C4/C5)")
    db_p.add_argument("--dry-run", action="store_true", help="fetch + score, write nothing")
    db_p.add_argument("--lookback-days", type=int, default=None)
    db_p.add_argument("--horizon-days", type=int, default=None)
    db_p.add_argument("--fixture-dir", default=None,
                      help="serve YouTube responses from local fixtures (offline)")
    db_p.add_argument("--allow-search-fallback", action="store_true",
                      help="permit the quota-expensive search.list fallback (C2/C4)")
    db_p.add_argument("--full-history", action="store_true",
                      help="walk a channel's ENTIRE upload history instead of stopping "
                           "pagination at lookback+buffer (expensive; off by default)")
    db_p.set_defaults(func=cmd_discover_broadcasts)

    # ---- Phase D team profile enrichment -----------------------------
    et = sub.add_parser("enrich-teams",
                        help="populate team facts (bio/website/socials/roster) via FACEIT")
    et.add_argument("--dry-run", action="store_true", help="fetch + score, write nothing")
    et.add_argument("--team-id", action="append", default=None,
                    help="limit to this team id (repeatable); default: every team "
                         "with a known faceit_team_id")
    et.add_argument("--fixture-dir", default=None,
                    help="serve FACEIT responses from local fixtures (offline)")
    et.add_argument("--export", action="store_true",
                    help="regenerate public_data.v1.js after a live run")
    et.set_defaults(func=cmd_enrich_teams)

    # ---- Phase D2 team coverage + verified logo pipeline -----------------
    tc_p = sub.add_parser("team-coverage",
                          help="per-team identity/roster/logo/broadcast/capture ledger (D2)")
    tc_p.add_argument("--window", type=int, default=30, help="lookback days")
    tc_p.add_argument("--json", action="store_true")
    tc_p.add_argument("--save", action="store_true",
                      help="write assets/data/team_coverage.v1.json")
    tc_p.set_defaults(func=cmd_team_coverage)

    ca_p = sub.add_parser("collect-team-assets",
                          help="promote FACEIT-sourced candidate logo URLs into the ranked registry")
    ca_p.add_argument("--team-id", action="append", default=None)
    ca_p.add_argument("--save", action="store_true",
                      help="write assets/data/team_asset_sources.json")
    ca_p.set_defaults(func=cmd_collect_team_assets)

    aa_p = sub.add_parser("approve-team-asset",
                          help="explicit human approval of one validated logo candidate")
    aa_p.add_argument("--team-id", required=True)
    aa_p.add_argument("--url", required=True)
    aa_p.add_argument("--approved-by", required=True, help="your name/handle, recorded in the registry")
    aa_p.add_argument("--confirm", action="store_true",
                      help="required — there is no default that approves a candidate")
    aa_p.set_defaults(func=cmd_approve_team_asset)

    pa_p = sub.add_parser("publish-team-assets",
                          help="publish already human-approved logo candidates")
    pa_p.add_argument("--publish", action="store_true",
                      help="actually write variants + set logo_url (default: dry-run listing)")
    pa_p.set_defaults(func=cmd_publish_team_assets)

    # ---- Phase 1: URL-only operator intake --------------------------------
    il_p = sub.add_parser("ingest-link",
                          help="THE entry point: paste one OWCS broadcast URL")
    il_p.add_argument("--url", required=True,
                      help="a YouTube broadcast link in any spelling "
                           "(watch?v=, youtu.be/, /live/, with timestamps)")
    il_p.add_argument("--dry-run", action="store_true",
                      help="parse + fetch metadata + report the decision, write nothing")
    il_p.add_argument("--requested-by", default=None,
                      help="your name/handle, recorded on the intake record")
    il_p.add_argument("--no-metadata", action="store_true",
                      help="skip the YouTube metadata call (offline); the source "
                           "then cannot be auto-approved")
    il_p.add_argument("--fixture-dir", default=None,
                      help="serve YouTube responses from local fixtures (offline)")
    il_p.add_argument("--json", action="store_true")
    il_p.set_defaults(func=cmd_ingest_link)

    ls_p = sub.add_parser("link-status",
                          help="stage/authorization/blockers/next command per pasted link")
    ls_p.add_argument("--job", default=None)
    ls_p.add_argument("--video-id", default=None)
    ls_p.add_argument("--url", default=None)
    ls_p.add_argument("--json", action="store_true")
    ls_p.set_defaults(func=cmd_link_status)

    as_p = sub.add_parser("approve-source",
                          help="audited manual approval of a non-registry source")
    as_p.add_argument("--job", required=True)
    as_p.add_argument("--approved-by", required=True,
                      help="your name/handle — recorded in the audit trail")
    as_p.add_argument("--reason", default=None)
    as_p.add_argument("--reject", action="store_true",
                      help="record an explicit refusal instead of an approval")
    as_p.add_argument("--confirm", action="store_true",
                      help="required — there is no default that approves a source")
    as_p.set_defaults(func=cmd_approve_source)

    # ---- Beta closed-loop: Phase 1 job spine + Phase E worker ------------
    cj_p = sub.add_parser("create-job", help="create a processing job from a matched official broadcast")
    cj_p.add_argument("--match", required=True, help="internal match id (must already exist)")
    cj_p.add_argument("--video-id", required=True)
    cj_p.add_argument("--source-url", required=True)
    cj_p.add_argument("--channel-id", required=True, help="the verified official channel id")
    cj_p.add_argument("--team-a", required=True)
    cj_p.add_argument("--team-b", required=True)
    cj_p.add_argument("--tournament", default=None)
    cj_p.add_argument("--region", default=None)
    cj_p.add_argument("--language", default=None)
    cj_p.add_argument("--layout-id", default=None, help="expected broadcast layout id")
    cj_p.set_defaults(func=cmd_create_job)

    lj_p = sub.add_parser("list-jobs", help="list processing jobs")
    lj_p.add_argument("--state", default=None)
    lj_p.add_argument("--json", action="store_true")
    lj_p.set_defaults(func=cmd_list_jobs)

    sj_p = sub.add_parser("show-job", help="show one job's full state/payload")
    sj_p.add_argument("job")
    sj_p.set_defaults(func=cmd_show_job)

    clj_p = sub.add_parser("claim-job", help="claim the next eligible job for a worker")
    clj_p.add_argument("--worker-id", required=True)
    clj_p.set_defaults(func=cmd_claim_job)

    rj_p = sub.add_parser("release-job", help="release a claimed job back to the pool")
    rj_p.add_argument("job")
    rj_p.add_argument("--worker-id", required=True)
    rj_p.set_defaults(func=cmd_release_job)

    rt_p = sub.add_parser("retry-job", help="retry a failed/retry-scheduled job")
    rt_p.add_argument("job")
    rt_p.add_argument("--force", action="store_true",
                      help="explicitly re-open a FAILED_PERMANENT (dead-lettered) job")
    rt_p.set_defaults(func=cmd_retry_job)

    ca_p = sub.add_parser("cancel-job", help="explicitly cancel a job")
    ca_p.add_argument("job")
    ca_p.add_argument("--reason", default=None)
    ca_p.set_defaults(func=cmd_cancel_job)

    rsl_p = sub.add_parser("reset-stale-lock", help="clear a job's lock if its lease has expired")
    rsl_p.add_argument("job")
    rsl_p.set_defaults(func=cmd_reset_stale_lock)

    res_p = sub.add_parser("resume-job", help="resume any job interrupted mid-download")
    res_p.add_argument("--worker-id", default=None)
    res_p.set_defaults(func=cmd_resume_job)

    runj_p = sub.add_parser("run-job", help="advance one job by exactly one automatic step")
    runj_p.add_argument("job")
    runj_p.add_argument("--worker-id", default=None)
    runj_p.set_defaults(func=cmd_run_job)

    jc_p = sub.add_parser("job-coverage", help="rolling job-health report (Phase 7)")
    jc_p.add_argument("--window-hours", type=int, default=24)
    jc_p.add_argument("--json", action="store_true")
    jc_p.add_argument("--save", action="store_true",
                      help="write assets/data/job_coverage.v1.json for beta-ops.html")
    jc_p.set_defaults(func=cmd_job_coverage)

    # --media-root/--min-free-gb default to None here (never `worker.X` —
    # that would force-import worker.py, and transitively cv2, merely to
    # build the argparse tree for ANY cli.py invocation, not just this
    # subcommand). The real defaults are resolved lazily inside
    # cmd_worker_doctor/cmd_worker_run, after `worker` is actually imported.
    wd_p = sub.add_parser("worker-doctor",
                          help="Windows-worker preflight checklist (deps/disk/gh-auth/API-key presence)")
    wd_p.add_argument("--media-root", default=None,
                      help="worker media cache root (default: data/worker/jobs under the repo)")
    wd_p.add_argument("--min-free-gb", type=float, default=None,
                      help="minimum required free disk space in GB (default: 5.0)")
    wd_p.add_argument("--json", action="store_true")
    wd_p.set_defaults(func=cmd_worker_doctor)

    wr_p = sub.add_parser("worker-run", help="the self-hosted worker main loop (Phase E)")
    wr_p.add_argument("--worker-id", default=None)
    wr_p.add_argument("--media-root", default=None,
                      help="worker media cache root (default: data/worker/jobs under the repo)")
    wr_p.add_argument("--max-jobs", type=int, default=1)
    wr_p.set_defaults(func=cmd_worker_run)

    # ---- Phase 3 layout resolution ---------------------------------------
    rl_p = sub.add_parser("resolve-layout",
                          help="fingerprint the broadcast and reuse or calibrate a layout")
    rl_p.add_argument("--job", required=True)
    rl_p.add_argument("--samples", type=int, default=24,
                      help="representative frames to fingerprint against")
    rl_p.add_argument("--no-harvest", action="store_true",
                      help="skip marker harvesting")
    rl_p.add_argument("--json", action="store_true")
    rl_p.set_defaults(func=cmd_resolve_layout)

    al_p = sub.add_parser("approve-layout",
                          help="promote a generated layout into layouts/ (human gate)")
    al_p.add_argument("--job", required=True)
    al_p.add_argument("--approved-by", default=None)
    al_p.add_argument("--confirm", action="store_true",
                      help="required — a generated layout is never auto-approved")
    al_p.set_defaults(func=cmd_approve_layout)

    # ---- Phase F assisted segmentation -----------------------------------
    sl_p = sub.add_parser("segment-list", help="list candidate/reviewed map segments")
    sl_p.add_argument("--video-id", default=None)
    sl_p.add_argument("--status", default=None)
    sl_p.add_argument("--json", action="store_true")
    sl_p.set_defaults(func=cmd_segment_list)

    sa_p = sub.add_parser("segment-approve", help="approve a candidate map segment")
    sa_p.add_argument("segment_id", type=int)
    sa_p.add_argument("--map-order", type=int, required=True)
    sa_p.add_argument("--map-name", required=True)
    sa_p.add_argument("--map-mode", required=True)
    sa_p.add_argument("--team-a", required=True)
    sa_p.add_argument("--team-b", required=True)
    sa_p.add_argument("--side", required=True, help="e.g. team_a_left")
    sa_p.add_argument("--layout-id", required=True)
    sa_p.add_argument("--note", default=None)
    sa_p.set_defaults(func=cmd_segment_approve)

    sr_p = sub.add_parser("segment-reject", help="reject a candidate map segment")
    sr_p.add_argument("segment_id", type=int)
    sr_p.add_argument("--reason", required=True)
    sr_p.set_defaults(func=cmd_segment_reject)

    # ---- Phase 4 automatic segment identity ------------------------------
    pi_p = sub.add_parser("propose-identity",
                          help="propose map/mode/teams/sides/order/players with evidence")
    pi_p.add_argument("--job", required=True)
    pi_p.add_argument("--segment-id", type=int, default=None,
                      help="limit to one segment (default: every pending one)")
    pi_p.add_argument("--samples", type=int, default=8,
                      help="frames to sample inside each segment window")
    pi_p.add_argument("--ocr-engine", default="easyocr")
    pi_p.set_defaults(func=cmd_propose_identity)

    ap_p = sub.add_parser("accept-proposed",
                          help="approve a segment using the proposed identity")
    ap_p.add_argument("--segment", type=int, required=True)
    ap_p.add_argument("--layout-id", default=None)
    ap_p.add_argument("--note", default=None)
    ap_p.set_defaults(func=cmd_accept_proposed)

    ss_p = sub.add_parser("segment-split", help="split one candidate into two")
    ss_p.add_argument("segment_id", type=int)
    ss_p.add_argument("--at", type=float, required=True,
                      help="split point in VOD seconds (must be inside the window)")
    ss_p.set_defaults(func=cmd_segment_split)

    smg_p = sub.add_parser("segment-merge", help="merge two candidates into one")
    smg_p.add_argument("segment_id", type=int)
    smg_p.add_argument("other", type=int)
    smg_p.set_defaults(func=cmd_segment_merge)

    ie_p = sub.add_parser("intake-export",
                          help="operator review-panel snapshot (intake.html)")
    ie_p.add_argument("--job", default=None, help="limit to one job key")
    ie_p.add_argument("--save", action="store_true",
                      help="write assets/data/intake.v1.json")
    ie_p.add_argument("--json", action="store_true")
    ie_p.set_defaults(func=cmd_intake_export)

    # ---- Phase G detection + Phase I publication -------------------------
    dj_p = sub.add_parser("detect-job", help="run detection on a job's approved segment")
    dj_p.add_argument("job")
    dj_p.add_argument("--write", action="store_true",
                      help="commit to production (requires job state APPROVED)")
    dj_p.set_defaults(func=cmd_detect_job)

    paj_p = sub.add_parser("process-approved-job",
                           help="the supervised publication command: promote, export, "
                                "validate, and (with --publish) commit + push")
    paj_p.add_argument("--job", required=True)
    paj_p.add_argument("--publish", action="store_true",
                       help="actually commit + push (default: dry-run validation only)")
    paj_p.set_defaults(func=cmd_process_approved_job)

    # ---- the free-agent loop: convert-link / autopilot -------------------
    def _add_autopilot_args(sp) -> None:
        sp.add_argument("--auto-accept", action="store_true",
                        help="accept clean machine identity proposals for "
                             "pending SEGMENTS through the accept-proposed "
                             "gate (source/layout/detection review and "
                             "publication always stay human)")
        sp.add_argument("--accepted-by", default=None,
                        help="your name/handle, recorded on every "
                             "auto-accepted segment's reviewer note")
        sp.add_argument("--max-steps", type=int,
                        default=None,  # resolved below to autopilot's default
                        help="safety cap on automatic steps per run")
        sp.add_argument("--worker-id", default=None)
        sp.add_argument("--media-root", default=None,
                        help="worker media cache root (default: "
                             "data/worker/jobs under the repo)")
        sp.add_argument("--samples", type=int, default=8,
                        help="frames sampled per segment for identity proposals")
        sp.add_argument("--ocr-engine", default="easyocr")
        sp.add_argument("--for-harvest", action="store_true",
                        help="download this broadcast even though its layout "
                             "has no hero templates / a placeholder anchor — "
                             "the explicit escape hatch for harvesting those "
                             "assets FROM this VOD. Never the default.")
        sp.add_argument("--no-export", action="store_true",
                        help="skip refreshing assets/data/intake.v1.json")
        sp.add_argument("--json", action="store_true")

    cl_p = sub.add_parser("convert-link",
                          help="match-day one-liner: ingest one pasted URL and "
                               "autopilot it to the first human gate")
    cl_p.add_argument("--url", required=True,
                      help="a YouTube broadcast link in any spelling")
    cl_p.add_argument("--requested-by", default=None,
                      help="your name/handle, recorded on the intake record")
    cl_p.add_argument("--no-metadata", action="store_true",
                      help="skip the YouTube metadata call (offline); the "
                           "source then cannot be auto-approved")
    cl_p.add_argument("--fixture-dir", default=None,
                      help="serve YouTube responses from local fixtures (offline)")
    _add_autopilot_args(cl_p)
    cl_p.set_defaults(func=cmd_convert_link)

    ap_run_p = sub.add_parser("autopilot",
                              help="advance an existing intake job through every "
                                   "automatic stage to the first human gate")
    ap_run_p.add_argument("--job", default=None, help="job key (record:<video-id>)")
    ap_run_p.add_argument("--url", default=None,
                          help="or the pasted URL (resolves to the same job)")
    _add_autopilot_args(ap_run_p)
    ap_run_p.set_defaults(func=cmd_autopilot)

    fm_p = sub.add_parser("find-matches",
                          help="auto match finder: scan verified channels on "
                               "free sources (RSS + streams tab), score "
                               "broadcast-likeness, keep the ledger")
    fm_p.add_argument("--limit", type=int, default=60,
                      help="max videos per channel from the streams tab "
                           "(default %(default)s)")
    fm_p.add_argument("--channel-url", default=None,
                      help="scan one extra channel URL (in addition to the "
                           "verified registry)")
    fm_p.add_argument("--queue-likely", action="store_true",
                      help="register likely broadcasts through the same "
                           "ingest_link gate as a pasted URL (metadata only "
                           "— never downloads, never approves)")
    fm_p.add_argument("--requested-by", default=None,
                      help="name recorded on queued intake records "
                           "(default: match-finder)")
    fm_p.add_argument("--no-metadata", action="store_true",
                      help="with --queue-likely: skip the YouTube metadata "
                           "call (offline); sources then cannot auto-approve")
    fm_p.add_argument("--fixture-dir", default=None,
                      help="serve YouTube responses from local fixtures (offline)")
    fm_p.add_argument("--dry-run", action="store_true",
                      help="scan and print, write nothing")
    fm_p.add_argument("--json", action="store_true")
    fm_p.set_defaults(func=cmd_find_matches)

    mp_p = sub.add_parser("media-probe",
                          help="prove real video BYTES download (not just "
                               "metadata) and report the working rung")
    mp_p.add_argument("--url", default=None)
    mp_p.add_argument("--job", default=None,
                      help="take the URL from this job and record the verdict")
    mp_p.add_argument("--height", type=int, default=720)
    mp_p.add_argument("--seconds", type=int, default=6,
                      help="seconds of real media to pull (default 6)")
    mp_p.add_argument("--json", action="store_true")
    mp_p.set_defaults(func=cmd_media_probe)

    ds_p = sub.add_parser("download-status",
                          help="download stack + auth config + fallback "
                               "ladder + per-layout detection readiness")
    ds_p.add_argument("--json", action="store_true")
    ds_p.set_defaults(func=cmd_download_status)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
