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

import db as content_db  # noqa: E402  (pipeline/db.py)
from automation import broadcast_discovery as bdisc  # noqa: E402
from automation import broadcast_matching as bmatch  # noqa: E402
from automation import config as cfg  # noqa: E402
from automation import coverage as cov  # noqa: E402
from automation import discovery as disc  # noqa: E402
from automation import faceit_api  # noqa: E402
from automation import job_store as js  # noqa: E402
from automation import match_export_coverage as mec  # noqa: E402
from automation import match_repair as mrepair  # noqa: E402
from automation import owcs_calendar  # noqa: E402
from automation import reconcile as rec  # noqa: E402
from automation import team_assets as tassets  # noqa: E402
from automation import team_coverage as tcov  # noqa: E402
from automation import team_enrichment as tenrich  # noqa: E402
from automation import youtube_api as yt  # noqa: E402


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
        print(f"  matching targets    : {t['matches']} FACEIT match(es), "
              f"{t['sourceEvents']} source_event(s), {t['calendarEvents']} calendar event(s)")
        print(f"  videos scored       : {match_summary['videosScored']}")
        print(f"  linked (high)       : {match_summary['linked']}")
        print(f"  review (medium)     : {match_summary['reviewed']}")
        print(f"  rejected (low)      : {match_summary['rejected']}")
        print("  classifications     :")
        for label in bmatch.ALL_CLASSIFICATIONS:
            n = match_summary["classifications"].get(label, 0)
            if n:
                print(f"    {label:<28}: {n}")
        accounted = sum(match_summary["classifications"].values())
        print(f"  videos accounted    : {accounted} / {disc_summary['inWindow']} in-window "
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
                print(f"      classification  : {r['classification']} "
                      f"(targets considered: {r['targetsConsidered']})")

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

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
