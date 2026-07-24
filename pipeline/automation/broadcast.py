"""
broadcast.py — official YouTube broadcast discovery + matching (Roadmap Phase C).

Read-only discovery that locates the OFFICIAL broadcast for each scheduled match
without ever downloading a byte of video or writing a single hero composition.

Pipeline:

  enabled official channels (config)      -> never a broad, unscoped search
    -> channel uploads playlist (cheap)   -> videos.list details (1u each)
    -> rolling window filter               (prev `lookback_days` + horizon)
    -> classify: upcoming / live / completed / vod
    -> score each (video, match) pair      (teams, event, region, language,
                                            scheduled time, FACEIT hint,
                                            official-channel authority)
    -> confidence: high (auto-linkable) / medium (review) / rejected (mirror)
    -> broadcast_candidates + broadcast_coverage in the AUTOMATION db
    -> auto-link ONLY high-confidence official broadcasts, and ONLY when the
       broadcast_auto_link master switch is on (off by default)
    -> everything uncertain goes to review; every uncovered match is recorded
       as an EXPLICIT missing-broadcast state, never a silent gap

Hard guarantees (asserted in tests):
  * Never downloads video; never writes comp/snapshot/hero rows.
  * Idempotent: rerunning upserts the same rows and never duplicates a job.
  * Dry-run does full retrieval + scoring with ZERO writes.
  * Unofficial mirrors are rejected by default (channelId not in the verified
    official set) even when their title text matches perfectly.
  * A full-day broadcast can cover several matches (one video -> many links).
  * YouTube quota is tracked; quota exhaustion stops cleanly (nothing invented).
"""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from . import models
from . import state_machine as sm
from . import youtube_api as yt
from .config import AutomationConfig
from .job_store import JobStore

# ------------------------------------------------------------------ scoring
# Additive signal weights. Official-channel authority is a boost, NOT enough on
# its own to cross a threshold: a candidate must carry a real CONTENT signal
# (team / event / FACEIT) or it is not recorded at all — so an unrelated upload
# (a hero trailer) on an official channel never links to a match.
SIG_OFFICIAL = 30
SIG_TEAM_BOTH = 40
SIG_TEAM_ONE = 18
SIG_EVENT = 20
SIG_REGION = 10
SIG_LANGUAGE = 5
SIG_FACEIT = 25
SIG_TIME_WINDOW = 20   # broadcast time within N hours of the match time
SIG_TIME_DAY = 12      # same calendar day (full-day broadcast coverage)
PEN_UNOFFICIAL = -100  # video from a channel not in the verified official set

_STOPWORDS = {
    "the", "team", "esports", "gaming", "official", "vs", "and", "owcs",
    "overwatch", "champions", "series", "stage", "group", "day", "week",
    "match", "game", "grand", "final", "finals", "playoffs", "vod", "live",
}


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _sig_tokens(name: str | None) -> set[str]:
    """Significant tokens of a team/event name (len>=3, minus stopwords)."""
    return {t for t in _norm(name).split() if len(t) >= 3 and t not in _STOPWORDS}


def _name_present(name: str | None, haystack: str) -> bool:
    """True if a team/event name is present in normalized haystack text: either
    the full normalized name appears, or a distinctive token (len>=4) does."""
    n = _norm(name)
    if not n:
        return False
    if n in haystack:
        return True
    toks = {t for t in n.split() if len(t) >= 4 and t not in _STOPWORDS}
    return any(re.search(rf"\b{re.escape(t)}\b", haystack) for t in toks)


def _parse_iso(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def region_compatible(channel_region: str | None, match_region: str | None) -> bool:
    """A global channel broadcasts every region; a regional channel only its own.
    A match with an unknown region is only paired with a global channel."""
    cr = (channel_region or "").lower()
    mr = (match_region or "").lower()
    if cr in ("global", "", "world"):
        return True
    return cr == mr


def match_time(match: dict) -> str | None:
    return match.get("scheduledAt") or match.get("startedAt") or match.get("finishedAt")


def score_candidate(
    video: dict, match: dict, channel: dict, *,
    official_channel_ids: set[str], config: AutomationConfig,
    now: dt.datetime | None = None,
) -> dict:
    """Score one (video, match) pair. Returns a structured verdict:

        {score, confidence, rejected, reason, signals, fullDay}

    confidence is one of high / medium / low / rejected. `rejected` is set when
    the video's channel is not in the verified official set (an unofficial
    mirror) — such a candidate is never linked, regardless of how well its title
    text matches.
    """
    signals: list[str] = []
    haystack = _norm((video.get("title") or "") + " \n " + (video.get("description") or ""))

    # 1. Official-channel gate: reject anything not from a verified official id.
    vid_channel = video.get("channelId")
    is_official = bool(vid_channel and vid_channel in official_channel_ids)
    if not is_official:
        return {
            "score": PEN_UNOFFICIAL, "confidence": "rejected", "rejected": True,
            "reason": "unofficial-mirror (channel not in verified official set)",
            "signals": ["UNOFFICIAL_CHANNEL"], "fullDay": False,
        }
    score = SIG_OFFICIAL
    signals.append("OFFICIAL_CHANNEL")

    # 2. Team signals.
    a_present = _name_present(match.get("teamA"), haystack)
    b_present = _name_present(match.get("teamB"), haystack)
    if a_present and b_present:
        score += SIG_TEAM_BOTH
        signals.append("TEAM_BOTH")
    elif a_present or b_present:
        score += SIG_TEAM_ONE
        signals.append("TEAM_ONE")

    # 3. Event/competition signal.
    event_present = _name_present(match.get("eventName"), haystack) or bool(
        _sig_tokens(match.get("eventName")) & set(haystack.split()))
    if event_present:
        score += SIG_EVENT
        signals.append("EVENT")

    # 4. Region signal (channel already known region-compatible before scoring).
    if match.get("region") and _norm(match.get("region")) in haystack:
        score += SIG_REGION
        signals.append("REGION_IN_TITLE")
    elif region_compatible(channel.get("region"), match.get("region")):
        score += SIG_REGION
        signals.append("REGION_CHANNEL")

    # 5. Language signal.
    exp_lang = match.get("language")
    if exp_lang and channel.get("language") and exp_lang == channel.get("language"):
        score += SIG_LANGUAGE
        signals.append("LANGUAGE")

    # 6. FACEIT hint (room slug or match id echoed in the description).
    fmid = match.get("faceitMatchId")
    if fmid and (fmid.lower() in haystack or _norm(fmid) in haystack):
        score += SIG_FACEIT
        signals.append("FACEIT_ID")

    # 7. Time proximity.
    mt = _parse_iso(match_time(match))
    bt = _parse_iso(yt.broadcast_time(video))
    if mt and bt:
        delta_h = abs((bt - mt).total_seconds()) / 3600.0
        if delta_h <= config.broadcast_time_window_hours:
            score += SIG_TIME_WINDOW
            signals.append("TIME_WINDOW")
        elif bt.date() == mt.date():
            score += SIG_TIME_DAY
            signals.append("TIME_SAME_DAY")

    # A candidate MUST carry a real content signal; official+region+time alone is
    # not enough to be linked to a match.
    content_signals = {"TEAM_BOTH", "TEAM_ONE", "EVENT", "FACEIT_ID"}
    if not (content_signals & set(signals)):
        return {"score": 0, "confidence": "low", "rejected": False,
                "reason": "no content signal", "signals": signals, "fullDay": False}

    # A "full-day" broadcast covers a match by event+region+day but names no team.
    full_day = ("EVENT" in signals and "TEAM_BOTH" not in signals
                and "TEAM_ONE" not in signals
                and ("TIME_SAME_DAY" in signals or "TIME_WINDOW" in signals))

    if score >= config.broadcast_high_score:
        conf = "high"
    elif score >= config.broadcast_medium_score:
        conf = "medium"
    else:
        conf = "low"
    return {"score": score, "confidence": conf, "rejected": False,
            "reason": "", "signals": signals, "fullDay": full_day}


# ----------------------------------------------------------- channel fetching
def official_youtube_channels(channels: list[dict]) -> list[dict]:
    """Enabled, verified (channelId present) YouTube channels only."""
    return [c for c in channels
            if c.get("platform", "youtube") == "youtube"
            and c.get("enabled") and c.get("channelId")]


def fetch_channel_videos(
    client: yt.YoutubeClient, channel: dict, *, config: AutomationConfig,
    now: dt.datetime, lookback_days: int, horizon_days: int,
) -> list[dict]:
    """Collect normalized broadcast videos for ONE official channel, preferring
    its uploads playlist (1u/page) over search (100u). Filtered to the rolling
    window. Never raises on an empty channel."""
    past = now - dt.timedelta(days=lookback_days)
    future = now + dt.timedelta(days=horizon_days)

    playlist_id = channel.get("uploadsPlaylistId")
    video_ids: list[str] = []
    if playlist_id:
        items = client.list_playlist_items(
            playlist_id, page_size=50, max_pages=config.broadcast_playlist_pages)
        for it in items:
            vid = yt.playlist_item_video_id(it)
            # Cheap pre-filter on the playlist snippet's publishedAt so we only
            # pay for videos.list on plausibly in-window items.
            pub = _parse_iso((it.get("snippet") or {}).get("publishedAt"))
            if vid and (pub is None or past - dt.timedelta(days=1) <= pub <= future):
                video_ids.append(vid)

    videos: list[dict] = []
    if video_ids:
        for raw in client.get_videos(video_ids):
            videos.append(yt.normalize_video(raw))

    # Fallback: a channel with no discoverable uploads playlist (rare) uses a
    # channel-scoped search — still official, but the expensive path.
    if not videos and channel.get("channelId"):
        for raw in client.search_channel_videos(
                channel["channelId"], published_after=past.replace(microsecond=0).isoformat()):
            videos.append(yt.normalize_video(raw))

    # Attach the source channel + rolling-window filter on the real broadcast time.
    out: list[dict] = []
    seen: set[str] = set()
    for v in videos:
        if not v.get("videoId") or v["videoId"] in seen:
            continue  # de-dupe duplicate video entries within a channel
        seen.add(v["videoId"])
        bt = _parse_iso(yt.broadcast_time(v))
        if bt is not None and not (past <= bt <= future):
            continue
        v["_sourceChannel"] = channel["id"]
        out.append(v)
    return out


# ------------------------------------------------------------- persistence
def _record_candidate(store: JobStore, match: dict, video: dict, channel: dict,
                      verdict: dict) -> None:
    store.con.execute(
        """INSERT INTO broadcast_candidates
             (match_id, channel_id, platform, video_id, url, score, confidence,
              state, signals, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?, CURRENT_TIMESTAMP)
           ON CONFLICT(match_id, platform, video_id) DO UPDATE SET
             channel_id=excluded.channel_id, url=excluded.url,
             score=excluded.score, confidence=excluded.confidence,
             state=excluded.state, signals=excluded.signals,
             updated_at=CURRENT_TIMESTAMP""",
        (match["id"], channel["id"], "youtube", video["videoId"], video.get("url"),
         int(verdict["score"]), verdict["confidence"],
         sm.NEEDS_REVIEW if verdict["confidence"] == "medium" else sm.DISCOVERED,
         json.dumps({"signals": verdict["signals"], "broadcastType": video.get("broadcastType"),
                     "fullDay": verdict["fullDay"], "reason": verdict["reason"]})))


def _upsert_coverage(store: JobStore, match: dict, *, state: str, best: dict | None,
                     candidate_count: int, auto_linked: bool, reason: str) -> None:
    store.con.execute(
        """INSERT INTO broadcast_coverage
             (match_id, region, state, best_video_id, best_channel_id,
              best_confidence, best_score, candidate_count, auto_linked, reason, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?, CURRENT_TIMESTAMP)
           ON CONFLICT(match_id) DO UPDATE SET
             region=excluded.region, state=excluded.state,
             best_video_id=excluded.best_video_id,
             best_channel_id=excluded.best_channel_id,
             best_confidence=excluded.best_confidence,
             best_score=excluded.best_score,
             candidate_count=excluded.candidate_count,
             auto_linked=excluded.auto_linked, reason=excluded.reason,
             updated_at=CURRENT_TIMESTAMP""",
        (match["id"], match.get("region"), state,
         (best or {}).get("videoId"), (best or {}).get("channelId"),
         (best or {}).get("confidence"), (best or {}).get("score"),
         candidate_count, 1 if auto_linked else 0, reason))


def _add_review_task(store: JobStore, match: dict, best: dict) -> None:
    store.con.execute(
        """INSERT INTO review_tasks (kind, ref_key, lane, state, payload)
           VALUES ('broadcast_link', ?, 'rapid', 'NEEDS_REVIEW', ?)
           ON CONFLICT(kind, ref_key) DO UPDATE SET payload=excluded.payload""",
        (f"{match['id']}::{best['videoId']}",
         json.dumps({"matchId": match["id"], "videoId": best["videoId"],
                     "url": best.get("url"), "confidence": best.get("confidence"),
                     "score": best.get("score"), "channelId": best.get("channelId"),
                     "signals": best.get("signals")})))


def _enqueue_broadcast_job(store: JobStore, match: dict, best: dict) -> bool:
    key = models.broadcast_key(best["videoId"])
    before = store.get(key)
    store.enqueue(models.KIND_BROADCAST, key,
                  payload={"matchId": match["id"], "videoId": best["videoId"],
                           "confidence": best.get("confidence")},
                  source_url=best.get("url"))
    return before is None


# ---------------------------------------------------------------- orchestrator
def discover_broadcasts(
    *,
    store: JobStore | None,
    client: yt.YoutubeClient,
    config: AutomationConfig,
    channels: list[dict],
    matches: list[dict],
    content_con=None,
    lookback_days: int | None = None,
    horizon_days: int | None = None,
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> dict:
    """Discover and match official broadcasts for a set of scheduled matches.

    `matches` are normalized match dicts:
        {id, contentId, faceitMatchId, region, eventName, teamA, teamB,
         scheduledAt, finishedAt, lifecycle, language?}
    `store` (automation db) and `content_con` (content db, for auto-link) may be
    None in dry-run. Returns a structured, JSON-serializable summary. Writes
    nothing when dry_run is True.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    lookback = lookback_days if lookback_days is not None else config.lookback_days
    horizon = horizon_days if horizon_days is not None else config.schedule_horizon_days

    official = official_youtube_channels(channels)
    official_ids = {c["channelId"] for c in official}
    channel_by_priority = sorted(official, key=lambda c: -int(c.get("priority") or 0))
    channels_with_region = {(_norm(c.get("region")) or "global") for c in official}

    summary: dict[str, Any] = {
        "dryRun": dry_run, "lookbackDays": lookback, "horizonDays": horizon,
        "now": now.replace(microsecond=0).isoformat(),
        "officialChannels": [c["id"] for c in official],
        "channelsScanned": 0, "videosSeen": 0,
        "matchesConsidered": len(matches),
        "located": [], "review": [], "missing": [], "unsupported": [],
        "rejectedMirrors": [], "autoLinked": [],
        "broadcastJobsCreated": 0,
        "quotaUsed": 0, "quotaBudget": client.quota_budget,
        "byBroadcastType": {}, "errors": [],
        "autoLinkEnabled": config.broadcast_auto_link,
    }
    if not official:
        summary["note"] = ("no enabled, API-verified official YouTube channels "
                            "— broadcast discovery cannot run (registry ids are "
                            "null/disabled until verify-channels confirms them)")
        # Still record every in-window finished match as an explicit MISSING or
        # UNSUPPORTED state so nothing is silently skipped.
        _finalize_missing(store, matches, official, now, lookback, horizon,
                          summary, dry_run)
        return summary

    # --- gather the video pool from official channels (quota-tracked) --------
    pool: list[dict] = []
    quota_exhausted = False
    for ch in channel_by_priority:
        if quota_exhausted:
            break
        try:
            vids = fetch_channel_videos(
                client, ch, config=config, now=now,
                lookback_days=lookback, horizon_days=horizon)
        except yt.YoutubeQuotaError as exc:
            quota_exhausted = True
            summary["errors"].append({"channel": ch["id"], "error": str(exc),
                                      "code": "QUOTA_EXCEEDED"})
            if not dry_run and store is not None:
                _enqueue_channel_retry(store, ch, str(exc), "QUOTA_EXCEEDED", now)
            break
        except (yt.YoutubeApiError, yt.YoutubeAuthError) as exc:
            summary["errors"].append({"channel": ch["id"], "error": str(exc),
                                      "code": "YOUTUBE_API_ERROR"})
            if not dry_run and store is not None:
                _enqueue_channel_retry(store, ch, str(exc), "YOUTUBE_API_ERROR", now)
            continue
        summary["channelsScanned"] += 1
        for v in vids:
            summary["byBroadcastType"][v["broadcastType"]] = (
                summary["byBroadcastType"].get(v["broadcastType"], 0) + 1)
        pool.extend(vids)
    summary["videosSeen"] = len(pool)
    summary["quotaUsed"] = client.quota_used

    # --- score every (match, video) pair ------------------------------------
    for match in matches:
        region = _norm(match.get("region"))
        region_supported = any(region_compatible(c.get("region"), match.get("region"))
                               for c in official)
        if not region_supported:
            summary["unsupported"].append({"matchId": match["id"], "region": match.get("region")})
            if not dry_run and store is not None:
                _upsert_coverage(store, match, state="UNSUPPORTED", best=None,
                                 candidate_count=0, auto_linked=False,
                                 reason="no enabled official channel for this region")
            continue

        candidates: list[dict] = []
        for ch in channel_by_priority:
            if not region_compatible(ch.get("region"), match.get("region")):
                continue
            for v in pool:
                if v.get("_sourceChannel") != ch["id"]:
                    continue
                verdict = score_candidate(
                    v, match, ch, official_channel_ids=official_ids,
                    config=config, now=now)
                if verdict["rejected"]:
                    summary["rejectedMirrors"].append(
                        {"matchId": match["id"], "videoId": v.get("videoId"),
                         "channelId": v.get("channelId")})
                    continue
                if verdict["confidence"] in ("high", "medium"):
                    candidates.append({
                        "videoId": v["videoId"], "url": v.get("url"),
                        "channelId": v.get("channelId"), "channelPriority": int(ch.get("priority") or 0),
                        "confidence": verdict["confidence"], "score": verdict["score"],
                        "signals": verdict["signals"], "fullDay": verdict["fullDay"],
                        "broadcastType": v.get("broadcastType"),
                        "_video": v, "_channel": ch, "_verdict": verdict})

        if not candidates:
            # Explicit missing-broadcast state — never a silent gap.
            reason = ("no official broadcast candidate scored at/above the review "
                      "threshold in the rolling window")
            summary["missing"].append({"matchId": match["id"], "region": match.get("region"),
                                       "teams": [match.get("teamA"), match.get("teamB")]})
            if not dry_run and store is not None:
                _upsert_coverage(store, match, state="MISSING", best=None,
                                 candidate_count=0, auto_linked=False, reason=reason)
            continue

        # De-dupe by video id (a full-day video may appear once per channel),
        # keep the strongest verdict per video.
        by_video: dict[str, dict] = {}
        for c in candidates:
            cur = by_video.get(c["videoId"])
            if cur is None or (c["score"], c["channelPriority"]) > (cur["score"], cur["channelPriority"]):
                by_video[c["videoId"]] = c
        deduped = list(by_video.values())
        # Best candidate: highest score, then channel priority.
        best = max(deduped, key=lambda c: (c["score"], c["channelPriority"]))
        high = best["confidence"] == "high"

        auto_linked = False
        if high and config.broadcast_auto_link:
            auto_linked = True
        state = "LOCATED" if (high and auto_linked) else "NEEDS_REVIEW"

        record = {"matchId": match["id"], "videoId": best["videoId"],
                  "url": best.get("url"), "confidence": best["confidence"],
                  "score": best["score"], "channelId": best["channelId"],
                  "broadcastType": best["broadcastType"], "fullDay": best["fullDay"],
                  "candidateCount": len(deduped)}
        if state == "LOCATED":
            summary["located"].append(record)
            summary["autoLinked"].append(record)
        else:
            summary["review"].append(record)

        if not dry_run and store is not None:
            for c in deduped:
                _record_candidate(store, match, c["_video"], c["_channel"], c["_verdict"])
            _upsert_coverage(store, match, state=state, best=best,
                             candidate_count=len(deduped), auto_linked=auto_linked,
                             reason="")
            if state == "NEEDS_REVIEW":
                _add_review_task(store, match, best)
            if _enqueue_broadcast_job(store, match, best):
                summary["broadcastJobsCreated"] += 1
            if auto_linked and content_con is not None and match.get("contentId"):
                _auto_link_vod(content_con, match["contentId"], best["url"])

    if not dry_run and store is not None:
        store.con.commit()
        _record_quota(store, client, now, mode="discover")
    if not dry_run and content_con is not None:
        content_con.commit()

    summary["quotaUsed"] = client.quota_used
    summary["quotaCalls"] = len(client.calls)
    return summary


def _finalize_missing(store, matches, official, now, lookback, horizon, summary, dry_run):
    """When no official channel is available, still record explicit state for
    every in-window match so coverage is honest."""
    past = now - dt.timedelta(days=lookback)
    for match in matches:
        summary["missing"].append({"matchId": match["id"], "region": match.get("region"),
                                   "teams": [match.get("teamA"), match.get("teamB")]})
        if not dry_run and store is not None:
            _upsert_coverage(store, match, state="MISSING", best=None,
                             candidate_count=0, auto_linked=False,
                             reason="no enabled official channel configured")
    if not dry_run and store is not None:
        store.con.commit()


def _auto_link_vod(content_con, content_id: str, url: str | None) -> None:
    """Write a discovered official VOD url onto the content match row. This is
    the ONLY content-db write broadcast discovery ever makes, and it is gated by
    the broadcast_auto_link master switch + high confidence. It never touches a
    composition/snapshot/hero table."""
    if not url:
        return
    content_con.execute(
        "UPDATE matches SET vod_url=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (url, content_id))


def _enqueue_channel_retry(store: JobStore, channel: dict, error: str,
                           code: str, now: dt.datetime) -> None:
    key = models.calendar_key("youtube", channel.get("id") or "channel")
    store.enqueue(models.KIND_DISCOVERY, key,
                  payload={"channelId": channel.get("channelId"), "channel": channel.get("id")})
    store.record_attempt(key, ok=False, error_code=code, error_message=error, now=now)


def _record_quota(store: JobStore, client: yt.YoutubeClient, now: dt.datetime,
                  *, mode: str) -> None:
    store.con.execute(
        """INSERT INTO youtube_quota (day, units, calls, budget, mode)
           VALUES (?, ?, ?, ?, ?)""",
        (now.date().isoformat(), client.quota_used, len(client.calls),
         client.quota_budget, mode))
    store.con.commit()


# ------------------------------------------------------ channel verification
def verify_channels(
    client: yt.YoutubeClient, channels: list[dict], *, now: dt.datetime | None = None,
) -> dict:
    """Resolve/verify each configured official channel against the live YouTube
    Data API (channels.list, 1u each). READ-ONLY: it prints/returns the confirmed
    channel id + exact title + uploads playlist so a human can commit them; it
    never writes video, compositions, or the registry file itself.

    A channel with a `channelId` is verified by id; otherwise its
    `officialSourceUrl` handle (@name) is resolved. Bilibili/non-YouTube channels
    are reported as unsupported-by-this-tool (not an error)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    results: list[dict] = []
    for ch in channels:
        entry = {"id": ch["id"], "configuredChannelId": ch.get("channelId"),
                 "platform": ch.get("platform", "youtube"),
                 "region": ch.get("region"), "language": ch.get("language"),
                 "officialSourceUrl": ch.get("officialSourceUrl"),
                 "status": "unchecked", "resolvedChannelId": None,
                 "resolvedTitle": None, "uploadsPlaylistId": None, "error": None}
        if entry["platform"] != "youtube":
            entry["status"] = "unsupported-platform"
            results.append(entry)
            continue
        try:
            raw = None
            if ch.get("channelId"):
                items = client.get_channels_by_ids([ch["channelId"]])
                raw = items[0] if items else None
            else:
                handle = _handle_from_url(ch.get("officialSourceUrl"))
                if handle:
                    raw = client.get_channel_by_handle(handle)
                else:
                    entry["status"] = "no-handle"
                    results.append(entry)
                    continue
            if not raw:
                entry["status"] = "not-found"
            else:
                norm = yt.normalize_channel(raw)
                entry.update({
                    "status": "verified",
                    "resolvedChannelId": norm["channelId"],
                    "resolvedTitle": norm["title"],
                    "uploadsPlaylistId": norm["uploadsPlaylistId"],
                    "verificationDate": now.date().isoformat(),
                })
        except yt.YoutubeQuotaError as exc:
            entry["status"] = "quota-exceeded"
            entry["error"] = str(exc)
            results.append(entry)
            break
        except (yt.YoutubeApiError, yt.YoutubeAuthError) as exc:
            entry["status"] = "error"
            entry["error"] = str(exc)
        results.append(entry)
    return {
        "checkedAt": now.replace(microsecond=0).isoformat(),
        "channels": results,
        "quotaUsed": client.quota_used,
        "quotaBudget": client.quota_budget,
        "verified": sum(1 for r in results if r["status"] == "verified"),
    }


_HANDLE_RE = re.compile(r"youtube\.com/@([A-Za-z0-9_.\-]+)")


def _handle_from_url(url: str | None) -> str | None:
    if not url:
        return None
    m = _HANDLE_RE.search(url)
    return m.group(1) if m else None


# -------------------------------------------- scheduled-match readers (real)
def scheduled_matches_from_store(
    store: JobStore, *, content_con=None, now: dt.datetime | None = None,
    lookback_days: int = 14, horizon_days: int = 30,
) -> list[dict]:
    """Build normalized match dicts from the automation db's scheduled_matches,
    filtered to the rolling window, enriched with the content event name when a
    content-db connection is supplied. Cancelled matches are skipped."""
    now = now or dt.datetime.now(dt.timezone.utc)
    past = (now - dt.timedelta(days=lookback_days)).replace(microsecond=0).isoformat()
    future = (now + dt.timedelta(days=horizon_days)).replace(microsecond=0).isoformat()
    rows = store.con.execute("SELECT * FROM scheduled_matches").fetchall()
    out: list[dict] = []
    for r in rows:
        if (r["status"] or "") in ("cancelled", "aborted"):
            continue
        when = r["scheduled_at"] or r["completed_at"]
        if when is not None and not (past <= when <= future) and r["status"] != "live":
            continue
        content_id = None
        event_name = None
        try:
            raw = json.loads(r["raw"]) if r["raw"] else {}
            content_id = raw.get("contentId")
        except (ValueError, TypeError):
            pass
        if content_con is not None and content_id:
            row = content_con.execute(
                "SELECT event_name FROM matches WHERE id=?", (content_id,)).fetchone()
            if row:
                event_name = row["event_name"]
        out.append({
            "id": r["id"], "contentId": content_id,
            "faceitMatchId": r["faceit_match_id"], "region": r["region"],
            "eventName": event_name or r["competition_id"],
            "teamA": r["team_a"], "teamB": r["team_b"],
            "scheduledAt": r["scheduled_at"], "finishedAt": r["completed_at"],
            "lifecycle": r["status"],
        })
    return out
