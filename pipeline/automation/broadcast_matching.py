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
from . import state_machine as sm
from .broadcast_discovery import _parse_iso  # shared ISO-8601 parser
from .job_store import JobStore

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


def match_broadcasts(
    store: JobStore, *,
    videos: list[dict] | None = None,
    time_window_hours: int = 72,
    dry_run: bool = False,
) -> dict:
    """Score every (undecided video) x (nearby scheduled match) pair and
    persist candidates (unless dry-run). `videos` defaults to every
    broadcast_videos row still in a pre-recording coverage_state (LIVE,
    AWAITING_BROADCAST, ARCHIVED) — i.e. discovered but not yet linked/
    advanced by a later phase. A cheap time-window pre-filter (default 72h)
    avoids scoring every match in the DB against every video."""
    if videos is None:
        videos = [_video_row_to_dict(r) for r in store.con.execute(
            "SELECT * FROM broadcast_videos WHERE coverage_state IN "
            "('LIVE','AWAITING_BROADCAST','ARCHIVED')")]
    matches = [dict(r) for r in store.con.execute("SELECT * FROM scheduled_matches")]
    comp_names = {r["id"]: r["name"] for r in store.con.execute("SELECT id, name FROM source_events")}

    summary: dict[str, Any] = {
        "dryRun": dry_run, "videosScored": 0, "linked": 0, "reviewed": 0,
        "rejected": 0, "results": [],
    }
    for v in videos:
        v_time = _parse_iso(v.get("actualStartAt") or v.get("scheduledStartAt") or v.get("publishedAt"))
        pairs: list[tuple[dict, dict]] = []
        for m in matches:
            m_time = _parse_iso(m.get("scheduled_at") or m.get("completed_at"))
            if m_time and v_time and abs((m_time - v_time).total_seconds()) > time_window_hours * 3600:
                continue
            ctx = {
                "matchId": m["id"], "teamA": m.get("team_a"), "teamB": m.get("team_b"),
                "region": m.get("region"), "language": None,
                "scheduledAt": m.get("scheduled_at"), "completedAt": m.get("completed_at"),
                "status": m.get("status"), "competitionName": comp_names.get(m.get("competition_id")),
                "faceitUrl": m.get("faceit_room_url"),
            }
            pairs.append((ctx, score_candidate(v, ctx)))
        link_summary = link_candidates(store, v, pairs, dry_run=dry_run)
        summary["videosScored"] += 1
        summary["linked"] += len(link_summary["linked"])
        summary["reviewed"] += len(link_summary["reviewed"])
        summary["rejected"] += len(link_summary["rejected"])
        summary["results"].append(link_summary)
    return summary
