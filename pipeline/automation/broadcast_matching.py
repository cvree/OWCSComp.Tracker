"""
broadcast_matching.py — explainable YouTube-broadcast <-> match scoring
(Roadmap Phase C4).

Never auto-publishes a link and never enables unattended production linking
(the roadmap's Phase C scope boundary): every candidate this module produces
is stored for a human/later phase to confirm. A HIGH-confidence candidate is
a *proposed* automatic link (visible in dry-run output and in
`broadcast_candidates`), a MEDIUM one opens a review task, and a LOW one is
rejected by default (title/region/channel signals too weak or conflicting).

A single YouTube broadcast can cover several matches (a full "Day 3" VOD with
three best-of-fives) and a single match can have candidate broadcasts from
several channels/languages — `broadcast_candidates` is a many-to-many table
keyed on (match_id, platform, video_id), never a one-video-to-one-match model.

Scoring is a small, explainable additive model: every signal that fires is
recorded in `reasons` (persisted as JSON in `broadcast_candidates.signals`),
so a human reviewing a MEDIUM candidate sees exactly why it scored the way
it did. The weight/threshold constants below are the ONLY place these
numbers live — see docs/AUTOMATION.md "C4 scoring" for the worked rationale,
and test_automation_broadcast_matching.py for the pinned boundary behavior.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from typing import Any

from . import models
from . import owcs_calendar
from . import state_machine as sm
from .broadcast_discovery import _parse_iso  # shared ISO-8601 parser
from .job_store import JobStore

# --- Per-video classification labels (Roadmap C4 fix) ----------------------
# Every in-window video gets EXACTLY one of these — a video is never simply
# absent from the report. The accounting invariant match_broadcasts()
# guarantees: videos_scored == sum of these classification counts, always,
# even when zero matching targets exist (the bug this module fixes: a
# real dry-run found 7 in-window videos but scored 0 of them, because
# nothing was ever compared against anything — see broadcast_discovery.
# sync_broadcasts's docstring and docs/AUTOMATION.md).
CLASS_HIGH = "high"
CLASS_NEEDS_REVIEW = "needs-review"                    # MEDIUM against a real FACEIT match
CLASS_EVENT_LEVEL_CANDIDATE = "event-level-candidate"  # MEDIUM+ against an event/calendar target only
CLASS_UNMATCHED_OFFICIAL = "unmatched-official-video"  # in-window, official, but NO eligible target existed
CLASS_UNRELATED_UPLOAD = "unrelated-official-upload"   # targets existed, all scored LOW
CLASS_UNSUPPORTED_EVENT = "unsupported-event"           # video's region has no YouTube-supported coverage
CLASS_OUTSIDE_COVERAGE = "outside-configured-coverage"  # video's region isn't tracked by ANY registry at all

ALL_CLASSIFICATIONS = (
    CLASS_HIGH, CLASS_NEEDS_REVIEW, CLASS_EVENT_LEVEL_CANDIDATE,
    CLASS_UNMATCHED_OFFICIAL, CLASS_UNRELATED_UPLOAD,
    CLASS_UNSUPPORTED_EVENT, CLASS_OUTSIDE_COVERAGE,
)

# --- Score weights (points) ------------------------------------------------
WEIGHT_OFFICIAL_CHANNEL = 40
WEIGHT_UNOFFICIAL_CHANNEL_PENALTY = -30
WEIGHT_TEAM_NAME_EACH = 15               # up to 2 (team A, team B)
WEIGHT_COMPETITION_NAME = 15
WEIGHT_OWCS_TITLE_PATTERN = 10
WEIGHT_REGION_MATCH = 10
WEIGHT_LANGUAGE_MATCH = 5
WEIGHT_TIME_CLOSE = 20                    # within TIME_CLOSE_MINUTES
WEIGHT_TIME_SAME_DAY = 8                  # within TIME_SAME_DAY_HOURS (not "close")
WEIGHT_TIME_CONFLICT_PENALTY = -25        # beyond TIME_CONFLICT_HOURS
WEIGHT_LIVE_STATUS_MATCH = 15
WEIGHT_FACEIT_REFERENCE = 10
WEIGHT_DURATION_PLAUSIBLE = 5
WEIGHT_DURATION_TOO_SHORT_PENALTY = -15

TIME_CLOSE_MINUTES = 30
TIME_SAME_DAY_HOURS = 12
TIME_CONFLICT_HOURS = 48
MIN_PLAUSIBLE_DURATION_SECONDS = 20 * 60  # a sub-20-minute video isn't a full broadcast

# --- Confidence bands (Roadmap C4) -----------------------------------------
# HIGH: verified official channel + strong event/time/team agreement — safe
#       to propose an automatic link in dry-run output (never auto-applied).
# MEDIUM: likely official but incomplete/ambiguous — opens a review task.
# LOW (< MEDIUM_THRESHOLD): weak/conflicting signals — rejected by default.
HIGH_THRESHOLD = 70
MEDIUM_THRESHOLD = 35

_OWCS_TITLE_PATTERNS = (
    "owcs", "overwatch champions series", "champions clash", "open qualifier",
    "grand final", "playoffs", "stage 1", "stage 2", "stage 3",
)


def _norm_text(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _contains(haystack: str, needle: str | None) -> bool:
    n = _norm_text(needle)
    return bool(n) and n in _norm_text(haystack)


def confidence_band(score: int) -> str:
    if score >= HIGH_THRESHOLD:
        return "high"
    if score >= MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def score_candidate(video: dict, match_ctx: dict) -> dict:
    """Explainable score of one video against one scheduled-match context.

    video: normalized shape from broadcast_discovery.normalize_video (or the
           equivalent DB row projection).
    match_ctx: {"matchId", "teamA", "teamB", "region", "language",
                "scheduledAt", "completedAt", "status", "competitionName",
                "faceitUrl"}

    Returns {"score": int, "confidence": "high"|"medium"|"low", "reasons": [...]}.
    Pure function — no I/O, no writes; every fired signal is recorded so the
    result is auditable without re-deriving it.
    """
    score = 0
    reasons: list[str] = []
    text = f"{video.get('title') or ''} {video.get('description') or ''}"

    if video.get("officialChannel"):
        score += WEIGHT_OFFICIAL_CHANNEL
        reasons.append(f"+{WEIGHT_OFFICIAL_CHANNEL} official channel")
    else:
        score += WEIGHT_UNOFFICIAL_CHANNEL_PENALTY
        reasons.append(f"{WEIGHT_UNOFFICIAL_CHANNEL_PENALTY} unofficial/unverified channel")

    for label, name in (("team A", match_ctx.get("teamA")), ("team B", match_ctx.get("teamB"))):
        if name and _contains(text, name):
            score += WEIGHT_TEAM_NAME_EACH
            reasons.append(f"+{WEIGHT_TEAM_NAME_EACH} {label} name '{name}' found in title/description")

    if match_ctx.get("competitionName") and _contains(text, match_ctx["competitionName"]):
        score += WEIGHT_COMPETITION_NAME
        reasons.append(f"+{WEIGHT_COMPETITION_NAME} competition/stage name matched")

    if any(_contains(text, p) for p in _OWCS_TITLE_PATTERNS):
        score += WEIGHT_OWCS_TITLE_PATTERN
        reasons.append(f"+{WEIGHT_OWCS_TITLE_PATTERN} known OWCS title pattern")

    if match_ctx.get("region") and video.get("region") and match_ctx["region"] == video["region"]:
        score += WEIGHT_REGION_MATCH
        reasons.append(f"+{WEIGHT_REGION_MATCH} region match ({match_ctx['region']})")

    if match_ctx.get("language") and video.get("language") and match_ctx["language"] == video["language"]:
        score += WEIGHT_LANGUAGE_MATCH
        reasons.append(f"+{WEIGHT_LANGUAGE_MATCH} language match ({video['language']})")

    faceit_url = match_ctx.get("faceitUrl")
    if faceit_url and _contains(text, faceit_url.rstrip("/").split("/")[-1]):
        score += WEIGHT_FACEIT_REFERENCE
        reasons.append(f"+{WEIGHT_FACEIT_REFERENCE} FACEIT room reference found in description")

    video_time = _parse_iso(video.get("actualStartAt") or video.get("scheduledStartAt")
                            or video.get("publishedAt"))
    match_time = _parse_iso(match_ctx.get("scheduledAt") or match_ctx.get("completedAt"))
    if video_time and match_time:
        delta_hours = abs((video_time - match_time).total_seconds()) / 3600.0
        if delta_hours <= TIME_CLOSE_MINUTES / 60.0:
            score += WEIGHT_TIME_CLOSE
            reasons.append(f"+{WEIGHT_TIME_CLOSE} start time within {TIME_CLOSE_MINUTES} minutes")
        elif delta_hours <= TIME_SAME_DAY_HOURS:
            score += WEIGHT_TIME_SAME_DAY
            reasons.append(f"+{WEIGHT_TIME_SAME_DAY} start time within {TIME_SAME_DAY_HOURS} hours")
        elif delta_hours > TIME_CONFLICT_HOURS:
            score += WEIGHT_TIME_CONFLICT_PENALTY
            reasons.append(f"{WEIGHT_TIME_CONFLICT_PENALTY} start time conflicts by {delta_hours:.0f}h "
                           f"(> {TIME_CONFLICT_HOURS}h)")

    if video.get("liveBroadcastStatus") == "live" and match_ctx.get("status") == "live":
        score += WEIGHT_LIVE_STATUS_MATCH
        reasons.append(f"+{WEIGHT_LIVE_STATUS_MATCH} both match and video are currently live")

    dur = video.get("durationSeconds")
    if dur is not None:
        if dur >= MIN_PLAUSIBLE_DURATION_SECONDS:
            score += WEIGHT_DURATION_PLAUSIBLE
            reasons.append(f"+{WEIGHT_DURATION_PLAUSIBLE} duration plausible for a full broadcast ({dur // 60}m)")
        else:
            score += WEIGHT_DURATION_TOO_SHORT_PENALTY
            reasons.append(f"{WEIGHT_DURATION_TOO_SHORT_PENALTY} duration too short for a full "
                           f"broadcast ({dur}s) — likely a clip/highlight")

    return {"score": score, "confidence": confidence_band(score), "reasons": reasons}


# ------------------------------------------------------------------ linking
def link_candidates(
    store: JobStore | None, video: dict, scored_pairs: list[tuple[dict, dict]],
    *, dry_run: bool = False,
) -> dict:
    """Persist scored (match_ctx, score_result) pairs. HIGH -> a proposed
    candidate link (state DISCOVERED — still requires human/later-phase
    confirmation, never auto-applied to production). MEDIUM -> the same
    candidate row PLUS a `review_tasks` row (state NEEDS_REVIEW). LOW is
    reported but not stored (rejected by default; storing a rejected pairing
    per rerun would just accumulate noise with no operator value)."""
    summary: dict[str, Any] = {"videoId": video["videoId"], "linked": [], "reviewed": [], "rejected": []}
    for match_ctx, result in scored_pairs:
        entry = {"matchId": match_ctx["matchId"], "score": result["score"],
                 "confidence": result["confidence"], "reasons": result["reasons"]}
        if result["confidence"] == "low":
            summary["rejected"].append(entry)
            continue
        (summary["linked"] if result["confidence"] == "high" else summary["reviewed"]).append(entry)
        if dry_run or store is None:
            continue
        state = sm.NEEDS_REVIEW if result["confidence"] == "medium" else sm.DISCOVERED
        store.con.execute(
            """INSERT INTO broadcast_candidates
                 (match_id, channel_id, platform, video_id, score, confidence,
                  state, signals, updated_at)
               VALUES (?,?,?,?,?,?,?,?, CURRENT_TIMESTAMP)
               ON CONFLICT(match_id, platform, video_id) DO UPDATE SET
                 channel_id=excluded.channel_id, score=excluded.score,
                 confidence=excluded.confidence, state=excluded.state,
                 signals=excluded.signals, updated_at=CURRENT_TIMESTAMP""",
            (match_ctx["matchId"], video.get("channelId"), video.get("platform", "youtube"),
             video["videoId"], result["score"], result["confidence"], state,
             json.dumps(result["reasons"])))
        link_key = models.broadcast_match_link_key(video["videoId"], match_ctx["matchId"])
        store.enqueue(models.KIND_BROADCAST, link_key, payload={
            "videoId": video["videoId"], "matchId": match_ctx["matchId"],
            "confidence": result["confidence"], "score": result["score"]})
        if result["confidence"] == "medium":
            store.con.execute(
                """INSERT INTO review_tasks (kind, ref_key, lane, state, payload)
                   VALUES ('broadcast_link', ?, 'rapid', 'NEEDS_REVIEW', ?)
                   ON CONFLICT(kind, ref_key) DO UPDATE SET payload=excluded.payload""",
                (f"{video['videoId']}:{match_ctx['matchId']}",
                 json.dumps({"videoId": video["videoId"], "matchId": match_ctx["matchId"],
                            "score": result["score"], "reasons": result["reasons"]})))
        store.con.commit()
    return summary


# ---------------------------------------------------------------- orchestrator
def _video_row_to_dict(row: sqlite3.Row | dict) -> dict:
    r = dict(row)
    return {
        "videoId": r.get("video_id"), "platform": r.get("platform", "youtube"),
        "channelId": r.get("channel_id"), "title": r.get("title"),
        "description": r.get("description"), "publishedAt": r.get("published_at"),
        "scheduledStartAt": r.get("scheduled_start_at"), "actualStartAt": r.get("actual_start_at"),
        "actualEndAt": r.get("actual_end_at"), "liveBroadcastStatus": r.get("live_broadcast_status"),
        "durationSeconds": r.get("duration_seconds"), "region": r.get("region"),
        "language": r.get("language"), "officialChannel": bool(r.get("official_channel")),
    }


# --- Matching-target abstraction (Roadmap C4 fix) --------------------------
# A video is NEVER required to match a FACEIT match specifically. Three
# target sources feed matching, cheapest/most-specific first:
#   'match'          scheduled_matches row — a real FACEIT-verified match,
#                    team-level, has a scheduled/completed timestamp.
#   'source_event'   automation DB source_events row — mirrors the official
#                    calendar into the DB once sync_calendar has run; event-
#                    level only (no team pairing), window from its raw JSON.
#   'calendar_event' owcs_calendar.load_events() — the COMMITTED SEED file,
#                    always available with no dependency on any prior sync.
#                    This is what lets a full-day broadcast (or a dry-run
#                    against a completely empty automation DB) still have
#                    something real to score against.
def _match_target(row: dict) -> dict:
    return {
        "kind": "match", "targetId": row["id"], "teamA": row.get("team_a"),
        "teamB": row.get("team_b"), "region": row.get("region"), "language": None,
        "scheduledAt": row.get("scheduled_at"), "completedAt": row.get("completed_at"),
        "status": row.get("status"), "competitionName": None,
        "faceitUrl": row.get("faceit_room_url"),
        "windowStart": None, "windowEnd": None,
    }


def _source_event_target(row: dict) -> dict:
    try:
        raw = json.loads(row.get("raw") or "{}")
    except (ValueError, TypeError):
        raw = {}
    return {
        "kind": "source_event", "targetId": row["id"], "teamA": None, "teamB": None,
        "region": row.get("region"), "language": None, "scheduledAt": None, "completedAt": None,
        "status": None, "competitionName": row.get("name"), "faceitUrl": None,
        "windowStart": raw.get("startDate"), "windowEnd": raw.get("endDate"),
    }


def _calendar_event_target(ev: "owcs_calendar.CalendarEvent") -> dict:
    return {
        "kind": "calendar_event", "targetId": ev.id, "teamA": None, "teamB": None,
        "region": ev.region, "language": None, "scheduledAt": None, "completedAt": None,
        "status": None, "competitionName": ev.name, "faceitUrl": None,
        "windowStart": ev.start_date, "windowEnd": ev.end_date,
    }


def _target_in_window(video_time: dt.datetime | None, target: dict, time_window_hours: int) -> bool:
    """True if `target` is eligible to be scored against a video at
    `video_time`. Event-level targets (source_event/calendar_event) compare
    by DAY against their [windowStart, windowEnd] date range (they don't
    carry an exact time); match targets compare by hour-delta against their
    scheduled/completed timestamp. Unknown timing on either side is KEPT
    (never silently excluded) — mirrors broadcast_discovery.in_window's rule."""
    if target["windowStart"] or target["windowEnd"]:
        if video_time is None:
            return True
        day = video_time.date().isoformat()
        if target["windowStart"] and day < target["windowStart"]:
            return False
        if target["windowEnd"] and day > target["windowEnd"]:
            return False
        return True
    target_time = _parse_iso(target.get("scheduledAt") or target.get("completedAt"))
    if target_time and video_time:
        return abs((target_time - video_time).total_seconds()) <= time_window_hours * 3600
    return True


def _ctx_from_target(t: dict) -> dict:
    return {
        "matchId": t["targetId"], "kind": t["kind"], "teamA": t["teamA"], "teamB": t["teamB"],
        "region": t["region"], "language": t["language"], "scheduledAt": t["scheduledAt"],
        "completedAt": t["completedAt"], "status": t["status"],
        "competitionName": t["competitionName"], "faceitUrl": t["faceitUrl"],
    }


def classify_video(
    scored: list[tuple[dict, dict]], *, region_supported: bool, region_tracked: bool,
) -> str:
    """Reduce one video's scored (ctx, score_result) pairs — best score
    first — to exactly one top-level classification. Pure; never raises.

    `region_supported`: the video's region has an enabled+verified YouTube
    channel (i.e. discovery itself covers it — true for every video that
    reaches this function today, since discovery only runs on such channels;
    kept as an explicit parameter for when a channel legitimately spans
    multiple regions).
    `region_tracked`: ANY registry (FACEIT competitions, source_events,
    calendar events, or configured channels) has an entry for this region at
    all — false means we have zero configured visibility into that region's
    schedule, a distinct gap from "we track this region but found no target
    for this particular video."

    CLASS_HIGH is reserved for a genuine team-level FACEIT match (kind==
    'match') — "safe to propose an automatic link" implies a specific,
    identified match exists, which an event-only target (no team pairing)
    never provides. A high-scoring event/calendar-only target — e.g. a
    full-day broadcast whose title/region/time strongly match a known
    event but with no per-match breakdown — is CLASS_EVENT_LEVEL_CANDIDATE
    at ANY confidence above LOW, not silently downgraded to "needs-review"
    (which is reserved for an ambiguous but still match-level candidate).
    """
    if not scored:
        # A YouTube-supported region with no eligible target at all is a
        # genuine gap (unmatched); a region some target DOES reference but
        # YouTube can't cover is a platform mismatch (unsupported-event);
        # a region nothing anywhere references is truly unknown territory.
        if region_supported:
            return CLASS_UNMATCHED_OFFICIAL
        if region_tracked:
            return CLASS_UNSUPPORTED_EVENT
        return CLASS_OUTSIDE_COVERAGE
    best_ctx, best_result = scored[0]
    is_match_target = best_ctx.get("kind") == "match"
    if best_result["confidence"] == "high":
        return CLASS_HIGH if is_match_target else CLASS_EVENT_LEVEL_CANDIDATE
    if best_result["confidence"] == "medium":
        return CLASS_NEEDS_REVIEW if is_match_target else CLASS_EVENT_LEVEL_CANDIDATE
    # Every eligible target scored LOW.
    if not region_supported:
        return CLASS_UNSUPPORTED_EVENT
    if not region_tracked:
        return CLASS_OUTSIDE_COVERAGE
    return CLASS_UNRELATED_UPLOAD


def match_broadcasts(
    store: JobStore, *,
    videos: list[dict] | None = None,
    scheduled_matches: list[dict] | None = None,
    source_events: list[dict] | None = None,
    calendar_events: "list[owcs_calendar.CalendarEvent] | None" = None,
    supported_regions: "set[str] | None" = None,
    time_window_hours: int = 72,
    dry_run: bool = False,
) -> dict:
    """Score every in-window video against every eligible matching target
    and persist candidates (unless dry-run). A video is NEVER required to
    match a FACEIT match specifically — targets are pooled from THREE
    sources so a full-day broadcast with no single confirmed match pairing
    still has something to score against (Roadmap C4 fix):

      1. `scheduled_matches` (verified FACEIT matches, team-level)
      2. `source_events` (automation DB — mirrors the official calendar
         once sync_calendar has run)
      3. `owcs_calendar.load_events()` (the committed seed file — ALWAYS
         available, independent of whether any sync has ever run; this is
         what makes matching work on a fresh/empty automation DB)

    `videos` defaults to every broadcast_videos row still in a
    pre-recording coverage_state (LIVE, AWAITING_BROADCAST, ARCHIVED); pass
    the freshly-discovered list explicitly (e.g. from
    `broadcast_discovery.sync_broadcasts(...)["videos"]`) to evaluate a
    dry-run's OWN discoveries, which are never persisted to that table.

    Every video gets exactly one top-level classification (see
    `classify_video`) in addition to its per-target HIGH/MEDIUM candidate
    rows — nothing is silently omitted from the summary, so
    `videosScored == sum(classifications.values())` always holds."""
    if videos is None:
        videos = [_video_row_to_dict(r) for r in store.con.execute(
            "SELECT * FROM broadcast_videos WHERE coverage_state IN "
            "('LIVE','AWAITING_BROADCAST','ARCHIVED')")]
    if scheduled_matches is None:
        scheduled_matches = [dict(r) for r in store.con.execute("SELECT * FROM scheduled_matches")]
    if source_events is None:
        source_events = [dict(r) for r in store.con.execute("SELECT * FROM source_events")]
    if calendar_events is None:
        calendar_events = owcs_calendar.load_events()
    if supported_regions is None:
        from . import config as cfg
        supported_regions = {c["region"] for c in cfg.load_channels() if c.get("region")}

    targets = (
        [_match_target(m) for m in scheduled_matches]
        + [_source_event_target(e) for e in source_events]
        + [_calendar_event_target(e) for e in calendar_events]
    )
    # Deliberately NOT unioned with supported_regions: region_supported and
    # region_tracked must be able to disagree (a supported region trivially
    # counts as "tracked" would make CLASS_OUTSIDE_COVERAGE unreachable
    # whenever a region is YouTube-supported — see classify_video).
    tracked_regions = {t["region"] for t in targets if t.get("region")}

    summary: dict[str, Any] = {
        "dryRun": dry_run, "videosScored": 0, "linked": 0, "reviewed": 0,
        "rejected": 0, "results": [], "classifications": {},
        "targetsLoaded": {
            "matches": len(scheduled_matches), "sourceEvents": len(source_events),
            "calendarEvents": len(calendar_events),
        },
    }
    for v in videos:
        v_time = _parse_iso(v.get("actualStartAt") or v.get("scheduledStartAt") or v.get("publishedAt"))
        pairs: list[tuple[dict, dict]] = []
        for t in targets:
            if not _target_in_window(v_time, t, time_window_hours):
                continue
            ctx = _ctx_from_target(t)
            pairs.append((ctx, score_candidate(v, ctx)))
        link_summary = link_candidates(store, v, pairs, dry_run=dry_run)

        region_supported = (v.get("region") in supported_regions) if v.get("region") else True
        region_tracked = (v.get("region") in tracked_regions) if v.get("region") else True
        scored_best_first = sorted(pairs, key=lambda p: p[1]["score"], reverse=True)
        classification = classify_video(
            scored_best_first, region_supported=region_supported, region_tracked=region_tracked)

        summary["videosScored"] += 1
        summary["linked"] += len(link_summary["linked"])
        summary["reviewed"] += len(link_summary["reviewed"])
        summary["rejected"] += len(link_summary["rejected"])
        summary["classifications"][classification] = summary["classifications"].get(classification, 0) + 1
        summary["results"].append({**link_summary, "classification": classification,
                                   "targetsConsidered": len(pairs)})
    return summary
