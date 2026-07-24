"""
discovery.py — FACEIT + official-calendar sync orchestrator (Roadmap Phase B).

Ties the pieces together into a production discovery pipeline:

  enabled competitions (config)     -> only explicitly configured ids, never search
    -> FACEIT Data API (faceit_api) -> normalized match FACTS (no comps)
    -> rolling-window filter          (previous `lookback_days` + `horizon_days`)
    -> idempotent upsert into the CONTENT db (teams, players, matches)
    -> discovery ledger + broadcast-discovery jobs in the AUTOMATION db
    -> official-calendar reconciliation warnings (never overwrites)

Hard guarantees:
  * Never writes hero compositions and never infers them from FACEIT.
  * Idempotent: running twice upserts the same stable ids, never duplicates.
  * Dry-run performs all retrieval + reconciliation with ZERO db writes.
  * API failures enqueue a retry job instead of crashing the whole run.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from typing import Any

# Content DB helpers live in the pipeline dir (a script dir, not a package).
_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

from . import config as cfg
from . import faceit_api
from . import models
from . import owcs_calendar
from . import reconcile as rec
from . import state_machine as sm
from .job_store import JobStore


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _slug(text: str, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s or fallback


def _parse_iso(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# ------------------------------------------------------------- window filter
def in_window(
    m: dict, now: dt.datetime, lookback_days: int, horizon_days: int
) -> bool:
    """Keep a match if it is live, finished within the rolling lookback, or
    scheduled between the lookback floor and the future horizon. Matches whose
    time is unknown are KEPT (never silently dropped)."""
    if m.get("lifecycleStatus") == "live":
        return True
    past = now - dt.timedelta(days=lookback_days)
    future = now + dt.timedelta(days=horizon_days)
    fin = _parse_iso(m.get("finishedAt"))
    if fin is not None:
        return fin >= past
    sched = _parse_iso(m.get("scheduledAt"))
    if sched is not None:
        return past <= sched <= future
    return True


# ------------------------------------------------------------- content upsert
def content_match_id(faceit_match_id: str) -> str:
    """Stable public id for a FACEIT match — the SAME scheme ingest_faceit.py
    uses, so discovery and matchroom ingest converge on one row."""
    return f"faceit-{faceit_match_id}"


def resolve_team_id(con, name: str | None, faceit_team_id: str | None,
                    side: str, faceit_match_id: str) -> str:
    """Resolve to an existing team by faceit_team_id (alias-safe: a renamed
    team keeps its row), else a name slug, else a deterministic fallback."""
    if faceit_team_id:
        row = con.execute("SELECT id FROM teams WHERE faceit_team_id=?",
                          (faceit_team_id,)).fetchone()
        if row:
            return row["id"]
    if name:
        return _slug(name, f"team_{side.lower()}")
    if faceit_team_id:
        return f"faceit_{_slug(faceit_team_id, 'team')}"
    short = re.sub(r"[^a-zA-Z0-9]", "", faceit_match_id)[-8:].lower() or "faceit"
    return f"faceit_{short}_{side.lower()}"


def _upsert_team(con, tid: str, name: str | None, faceit_team_id: str | None, region: str) -> None:
    con.execute(
        """INSERT INTO teams (id, name, region, code, faceit_team_id)
           VALUES (?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             name=COALESCE(excluded.name, teams.name),
             region=COALESCE(excluded.region, teams.region),
             faceit_team_id=COALESCE(excluded.faceit_team_id, teams.faceit_team_id)""",
        (tid, name or tid, region, (name or tid)[:6].upper().replace(" ", ""),
         faceit_team_id))


def _upsert_player(con, nickname: str, faceit_player_id: str | None,
                   team_id: str, country: str | None) -> str:
    pid = _slug(nickname or faceit_player_id or "unknown", "unknown")
    con.execute(
        """INSERT INTO players (id, nickname, faceit_player_id, team_id, country, source)
           VALUES (?,?,?,?,?, 'faceit')
           ON CONFLICT(id) DO UPDATE SET
             nickname=excluded.nickname,
             faceit_player_id=COALESCE(excluded.faceit_player_id, players.faceit_player_id),
             team_id=COALESCE(excluded.team_id, players.team_id),
             country=COALESCE(excluded.country, players.country)""",
        (pid, nickname, faceit_player_id, team_id, country))
    return pid


def upsert_match(con, m: dict, competition: dict) -> dict:
    """Idempotently upsert one normalized match (teams, players, match row).
    Returns {id, action, rescheduled, previousScheduledAt}. Never writes comps."""
    fmid = m["faceitMatchId"]
    cid = content_match_id(fmid)
    region = (competition.get("region") or m.get("region") or "Unknown")

    tA, tB = m["teams"][0], m["teams"][1]
    slug_a = resolve_team_id(con, tA["name"], tA["faceitTeamId"], "A", fmid)
    slug_b = resolve_team_id(con, tB["name"], tB["faceitTeamId"], "B", fmid)
    _upsert_team(con, slug_a, tA["name"], tA["faceitTeamId"], region)
    _upsert_team(con, slug_b, tB["name"], tB["faceitTeamId"], region)

    sa, sb = m["score"].get("a"), m["score"].get("b")
    winner = slug_a if m.get("winnerSide") == "A" else slug_b if m.get("winnerSide") == "B" else None
    lifecycle = m["lifecycleStatus"]
    content_status = m["contentStatus"]
    capture_status = "cancelled" if lifecycle in ("cancelled", "aborted") else "pending"
    # date: prefer scheduled/finished day, else today (kept for static sorting).
    when = m.get("finishedAt") or m.get("scheduledAt") or m.get("startedAt")
    date = (when or _now().isoformat())[:10]

    existing = con.execute("SELECT scheduled_at FROM matches WHERE id=?", (cid,)).fetchone()
    prev_sched = existing["scheduled_at"] if existing else None
    rescheduled = bool(existing and prev_sched and m.get("scheduledAt") and prev_sched != m["scheduledAt"])

    con.execute(
        """INSERT INTO matches
             (id, source_ref, faceit_match_id, faceit_room_url, event_name,
              season, stage, region, date, scheduled_at, started_at, finished_at,
              status, lifecycle_status, capture_status, competition_id,
              team_a, team_b, score_a, score_b, winner_team, source_url,
              raw_source, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'faceit', CURRENT_TIMESTAMP)
           ON CONFLICT(id) DO UPDATE SET
             faceit_match_id=excluded.faceit_match_id,
             faceit_room_url=excluded.faceit_room_url,
             event_name=COALESCE(excluded.event_name, matches.event_name),
             season=COALESCE(excluded.season, matches.season),
             stage=COALESCE(excluded.stage, matches.stage),
             region=excluded.region,
             date=excluded.date,
             scheduled_at=excluded.scheduled_at,
             started_at=excluded.started_at,
             finished_at=excluded.finished_at,
             status=excluded.status,
             lifecycle_status=excluded.lifecycle_status,
             capture_status=excluded.capture_status,
             competition_id=excluded.competition_id,
             team_a=excluded.team_a, team_b=excluded.team_b,
             score_a=excluded.score_a, score_b=excluded.score_b,
             winner_team=excluded.winner_team,
             source_url=COALESCE(excluded.source_url, matches.source_url),
             updated_at=CURRENT_TIMESTAMP""",
        (cid, f"faceit:{fmid}", fmid, m.get("faceitUrl"),
         competition.get("name") or m.get("competitionName") or "OWCS",
         competition.get("season"), competition.get("stage") or m.get("round"),
         region, date, m.get("scheduledAt"), m.get("startedAt"), m.get("finishedAt"),
         content_status, lifecycle, capture_status, competition.get("id"),
         slug_a, slug_b, sa, sb, winner, m.get("faceitUrl")))

    # Rosters after the match row exists (match_rosters.match_id -> matches.id).
    for team, tid in ((tA, slug_a), (tB, slug_b)):
        con.execute("DELETE FROM match_rosters WHERE match_id=? AND team_id=? AND source='faceit'",
                    (cid, tid))
        for p in team["players"]:
            pid = _upsert_player(con, p["nickname"], p.get("faceitPlayerId"), tid, p.get("country"))
            con.execute("INSERT OR REPLACE INTO match_rosters (match_id, team_id, player_id, source) "
                        "VALUES (?,?,?, 'faceit')", (cid, tid, pid))

    action = "updated" if existing else "inserted"
    return {"id": cid, "action": action, "rescheduled": rescheduled,
            "previousScheduledAt": prev_sched if rescheduled else None}


# ---------------------------------------------------- automation-db discovery
def _upsert_scheduled_match(store: JobStore, m: dict, competition: dict, content_id: str) -> None:
    fmid = m["faceitMatchId"]
    store.con.execute(
        """INSERT INTO scheduled_matches
             (id, faceit_match_id, competition_id, region, team_a, team_b,
              scheduled_at, completed_at, status, tier, faceit_room_url,
              state, capture_status, data_status, raw, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, CURRENT_TIMESTAMP)
           ON CONFLICT(id) DO UPDATE SET
             status=excluded.status,
             scheduled_at=excluded.scheduled_at,
             completed_at=excluded.completed_at,
             capture_status=excluded.capture_status,
             faceit_room_url=excluded.faceit_room_url,
             raw=excluded.raw,
             updated_at=CURRENT_TIMESTAMP""",
        (models.match_key(fmid), fmid, competition.get("id"),
         competition.get("region"),
         m["teams"][0].get("name"), m["teams"][1].get("name"),
         m.get("scheduledAt"), m.get("finishedAt"), m["lifecycleStatus"],
         competition.get("tier"), m.get("faceitUrl"),
         sm.DISCOVERED,
         "cancelled" if m["lifecycleStatus"] in ("cancelled", "aborted") else "pending",
         "final" if m["contentStatus"] == "final" else "pending",
         json.dumps({"contentId": content_id})))
    store.con.commit()


def _queue_broadcast_job(store: JobStore, m: dict) -> bool:
    """Queue a broadcast-discovery job for a match that still needs one.
    Idempotent (deterministic key). Skips cancelled matches."""
    if m["lifecycleStatus"] in ("cancelled", "aborted"):
        return False
    key = f"broadcast:match:{models.slug(m['faceitMatchId'])}"
    before = store.get(key)
    store.enqueue(models.KIND_BROADCAST, key,
                  payload={"faceitMatchId": m["faceitMatchId"],
                           "faceitUrl": m.get("faceitUrl")},
                  source_url=m.get("faceitUrl"))
    return before is None


# ------------------------------------------------------------------- sync ops
def sync_faceit(
    *,
    con,
    store: JobStore | None,
    client: faceit_api.FaceitClient,
    config: cfg.AutomationConfig,
    competitions: list[dict] | None = None,
    lookback_days: int | None = None,
    horizon_days: int | None = None,
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> dict:
    """Sync enabled FACEIT competitions into the content + automation DBs.

    `con` is a content-DB connection (schema already initialized). `store` may
    be None only in dry-run. Returns a structured summary."""
    now = now or _now()
    lookback = lookback_days if lookback_days is not None else config.lookback_days
    horizon = horizon_days if horizon_days is not None else config.schedule_horizon_days
    competitions = competitions if competitions is not None else cfg.load_competitions()

    summary: dict[str, Any] = {
        "dryRun": dry_run, "lookbackDays": lookback, "horizonDays": horizon,
        "competitions": [c.get("id") for c in competitions],
        "matchesSeen": 0, "inWindow": 0, "upserted": 0,
        "byLifecycle": {}, "rescheduled": [], "broadcastJobsCreated": 0,
        "matches": [], "errors": [],
    }
    if not competitions:
        summary["note"] = ("no enabled competitions with a real championshipId "
                           "— nothing to sync (registries are placeholders)")
        return summary

    all_normalized: list[dict] = []
    for comp in competitions:
        champ_id = comp.get("championshipId")
        try:
            raw_matches = client.list_championship_matches(champ_id)
        except (faceit_api.FaceitApiError, faceit_api.FaceitAuthError) as exc:
            summary["errors"].append({"competitionId": comp.get("id"),
                                      "championshipId": champ_id, "error": str(exc)})
            if not dry_run and store is not None:
                _queue_discovery_retry(store, comp, str(exc), now)
            continue

        for raw in raw_matches:
            m = faceit_api.normalize_match(raw, region=comp.get("region"))
            if not m.get("faceitMatchId"):
                continue
            summary["matchesSeen"] += 1
            if not in_window(m, now, lookback, horizon):
                continue
            summary["inWindow"] += 1
            all_normalized.append(m)
            lc = m["lifecycleStatus"]
            summary["byLifecycle"][lc] = summary["byLifecycle"].get(lc, 0) + 1

            entry = {"faceitMatchId": m["faceitMatchId"], "lifecycle": lc,
                     "contentStatus": m["contentStatus"], "scheduledAt": m.get("scheduledAt")}
            if dry_run:
                entry["id"] = content_match_id(m["faceitMatchId"])
                entry["action"] = "would-upsert"
            else:
                res = upsert_match(con, m, comp)
                entry.update(res)
                summary["upserted"] += 1
                if res["rescheduled"]:
                    summary["rescheduled"].append(res["id"])
                if store is not None:
                    _upsert_scheduled_match(store, m, comp, res["id"])
                    if _queue_broadcast_job(store, m):
                        summary["broadcastJobsCreated"] += 1
            summary["matches"].append(entry)

    if not dry_run:
        con.commit()

    summary["_normalized"] = all_normalized  # consumed by reconciliation
    return summary


def _queue_discovery_retry(store: JobStore, comp: dict, error: str, now: dt.datetime) -> None:
    """Record a competition-level discovery failure as a retryable job (J1)."""
    key = models.calendar_key("faceit", comp.get("id") or comp.get("championshipId") or "unknown")
    store.enqueue(models.KIND_DISCOVERY, key,
                  payload={"competitionId": comp.get("id"),
                           "championshipId": comp.get("championshipId")})
    store.record_attempt(key, ok=False, error_code="FACEIT_API_ERROR",
                         error_message=error, now=now)


def sync_calendar(
    *,
    store: JobStore | None,
    events: list[owcs_calendar.CalendarEvent] | None = None,
    dry_run: bool = False,
) -> dict:
    """Load official-calendar events into the automation source_events ledger.
    Read-only in dry-run. Returns a summary of the events seen."""
    events = events if events is not None else owcs_calendar.load_events()
    summary = {"dryRun": dry_run, "events": len(events),
               "unverified": sum(1 for e in events if not e.verified),
               "eventIds": [e.id for e in events]}
    if not dry_run and store is not None:
        for e in events:
            store.con.execute(
                """INSERT INTO source_events
                     (id, source, external_id, name, region, tier, state, raw, updated_at)
                   VALUES (?, 'owcs_calendar', ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name, region=excluded.region,
                     raw=excluded.raw, updated_at=CURRENT_TIMESTAMP""",
                (e.id, e.faceit_competition_id, e.name, e.region, None,
                 sm.DISCOVERED, json.dumps(e.raw)))
        store.con.commit()
    return summary


def sync_all(
    *,
    con,
    store: JobStore | None,
    client: faceit_api.FaceitClient,
    config: cfg.AutomationConfig,
    competitions: list[dict] | None = None,
    events: list[owcs_calendar.CalendarEvent] | None = None,
    channels: list[dict] | None = None,
    lookback_days: int | None = None,
    horizon_days: int | None = None,
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> dict:
    """Run FACEIT + calendar sync, then reconcile. Never overwrites conflicts."""
    competitions = competitions if competitions is not None else cfg.load_competitions()
    events = events if events is not None else owcs_calendar.load_events()
    channels = channels if channels is not None else cfg.load_channels()

    faceit_summary = sync_faceit(
        con=con, store=store, client=client, config=config,
        competitions=competitions, lookback_days=lookback_days,
        horizon_days=horizon_days, dry_run=dry_run, now=now)
    calendar_summary = sync_calendar(store=store, events=events, dry_run=dry_run)

    warnings = rec.reconcile(
        faceit_summary.get("_normalized", []), events,
        channels_by_id={c.get("id"): c for c in channels},
        competitions=competitions)

    faceit_summary.pop("_normalized", None)
    return {
        "dryRun": dry_run,
        "faceit": faceit_summary,
        "calendar": calendar_summary,
        "warnings": warnings,
        "warningCount": len(warnings),
    }
