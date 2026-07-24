"""
broadcast_discovery.py — YouTube channel verification + broadcast discovery
(Roadmap Phase C1/C3).

Two responsibilities, kept in one module because they share the client and
the "never guess, never silently overwrite" discipline the rest of the
automation layer follows:

  * verify_channels()   — C1. Resolve/re-confirm each configured channel's
    real channelId via channels.list (by @handle when channelId is still
    null, or by id for periodic re-verification). NEVER writes the registry
    file itself — a human reviews the report and edits
    config/broadcast_channels.json (mirrors how the FACEIT registry pass
    works; see docs/FACEIT-REGISTRY.md).

  * discover_channel_videos() + sync_broadcasts() — C3. For each enabled,
    verified channel: resolve its uploads playlist (cheap), enumerate
    uploads, batch-hydrate video/livestream status, normalize, and
    idempotently upsert into `broadcast_videos` + enqueue a
    `broadcast:<video-id>` job. Broad search.list is used ONLY when a
    caller explicitly opts in (uploads can't satisfy discovery), per C2/C4.

Hard guarantees (mirrors discovery.py's Phase B guarantees):
  * Never downloads video, never records, never writes hero compositions.
  * Idempotent: rerunning the same channel/window never duplicates a video
    row or a job.
  * Dry-run performs all retrieval with ZERO db writes.
  * API failures (incl. quota exhaustion) enqueue/record a retry rather than
    crashing the whole run.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Any

from . import models
from . import state_machine as sm
from . import youtube_api as yt
from .job_store import JobStore

_DUR_RE = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(t: dt.datetime) -> str:
    return t.replace(microsecond=0).isoformat()


def _parse_iso(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_duration(s: str | None) -> int | None:
    """ISO-8601 duration (e.g. 'PT1H42M30S') -> seconds. None if unparsable."""
    if not s:
        return None
    m = _DUR_RE.match(s)
    if not m:
        return None
    d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


# ---------------------------------------------------------- C1: verification
def _handle_from_source_url(url: str | None) -> str | None:
    """Extract a public @handle from a channel URL. Never derives/guesses a
    channelId — only a handle string to hand to channels.list(forHandle=)."""
    if not url:
        return None
    m = re.search(r"youtube\.com/(@[\w.\-]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"youtube\.com/(?:c|user)/([\w.\-]+)", url)
    if m:
        return "@" + m.group(1)
    # Legacy bare custom URL (youtube.com/<Name>, no /c/ or /user/ prefix).
    # Many channels' handles today match their old custom URL 1:1, so this is
    # worth *trying* against the live API — verify_channels never writes
    # anything, so a wrong guess here just yields a harmless 'not_found'.
    m = re.search(r"youtube\.com/([\w.\-]+)/?$", url)
    if m:
        return "@" + m.group(1)
    return None


def verify_channels(
    client: yt.YouTubeClient, channels: list[dict], *, now: dt.datetime | None = None,
) -> dict:
    """Verify every configured channel's real-world existence via the live
    YouTube API. Read-only: never edits config/broadcast_channels.json (a
    human applies the result, exactly like the FACEIT registry pass).

    Each input channel dict is a raw row from config/broadcast_channels.json
    (`load_all_channels()` — including disabled ones, since verification is
    how a disabled entry gets enough evidence to enable). A channel with
    neither a channelId nor a resolvable sourceUrl handle is skipped with
    its documented disabledReason — never guessed.
    """
    now = now or _now()
    results: list[dict] = []
    for ch in channels:
        cid = ch.get("channelId")
        handle = _handle_from_source_url(ch.get("sourceUrl"))
        if not cid and not handle:
            results.append({
                "id": ch["id"], "status": "skipped",
                "reason": ch.get("disabledReason") or "no channelId or resolvable sourceUrl handle",
            })
            continue
        try:
            item = client.get_channel_by_id(cid) if cid else client.get_channel_by_handle(handle)
        except yt.YouTubeQuotaExceeded as exc:
            results.append({"id": ch["id"], "status": "quota_exceeded", "error": str(exc)})
            continue
        except (yt.YouTubeApiError, yt.YouTubeAuthError) as exc:
            results.append({"id": ch["id"], "status": "error", "error": str(exc)})
            continue
        if not item:
            results.append({
                "id": ch["id"], "status": "not_found",
                "channelId": cid, "handle": handle, "sourceUrl": ch.get("sourceUrl"),
            })
            continue
        snippet = item.get("snippet") or {}
        results.append({
            "id": ch["id"], "status": "verified",
            "channelId": item.get("id"),
            "title": snippet.get("title"),
            "customUrl": snippet.get("customUrl"),
            "uploadsPlaylistId": yt.uploads_playlist_id(item),
            "verifiedDate": now.date().isoformat(),
        })
    return {
        "verifiedAt": _iso(now),
        "channels": results,
        "verifiedCount": sum(1 for r in results if r["status"] == "verified"),
        "skippedCount": sum(1 for r in results if r["status"] == "skipped"),
        "errorCount": sum(1 for r in results if r["status"] in ("error", "quota_exceeded", "not_found")),
    }


# ------------------------------------------------------------ C3: normalize
def _resolve_live_status(live: dict, snippet_live_content: str | None) -> str:
    """'upcoming' | 'live' | 'completed' | 'none' (an ordinary uploaded VOD
    that was never a livestream)."""
    if live.get("actualEndTime"):
        return "completed"
    if live.get("actualStartTime"):
        return "live"
    if live.get("scheduledStartTime"):
        return "upcoming"
    if snippet_live_content in ("live", "upcoming"):
        return snippet_live_content
    return "none"


def normalize_video(item: dict, *, channel: dict, discovered_at: dt.datetime | None = None) -> dict:
    """Turn one raw videos.list item into the normalized broadcast shape
    (Phase C3's required field list). `channel` is the registry row this
    video was discovered through (drives region/language/official-status)."""
    vid = item.get("id")
    snippet = item.get("snippet") or {}
    live = item.get("liveStreamingDetails") or {}
    content = item.get("contentDetails") or {}
    raw_text = json.dumps(item, sort_keys=True)
    return {
        "videoId": vid,
        "platform": "youtube",
        "channelId": channel.get("id"),
        "title": snippet.get("title"),
        "description": snippet.get("description"),
        "publishedAt": snippet.get("publishedAt"),
        "scheduledStartAt": live.get("scheduledStartTime"),
        "actualStartAt": live.get("actualStartTime"),
        "actualEndAt": live.get("actualEndTime"),
        "liveBroadcastStatus": _resolve_live_status(live, snippet.get("liveBroadcastContent")),
        "durationSeconds": _parse_duration(content.get("duration")),
        "thumbnailUrl": ((snippet.get("thumbnails") or {}).get("high")
                         or (snippet.get("thumbnails") or {}).get("default") or {}).get("url"),
        "sourceUrl": f"https://www.youtube.com/watch?v={vid}" if vid else None,
        "region": channel.get("region"),
        "language": channel.get("language"),
        "officialChannel": bool(channel.get("official")),
        "responseHash": hashlib.sha256(raw_text.encode()).hexdigest(),
        "discoveredAt": _iso(discovered_at or _now()),
    }


def in_window(video: dict, now: dt.datetime, lookback_days: int, horizon_days: int) -> bool:
    """Keep a video if it is live, ended/published within the rolling
    lookback, or scheduled between the lookback floor and the future
    horizon. Unknown timing is KEPT (never silently dropped) — mirrors
    discovery.in_window's rule for FACEIT matches."""
    if video["liveBroadcastStatus"] == "live":
        return True
    past = now - dt.timedelta(days=lookback_days)
    future = now + dt.timedelta(days=horizon_days)
    end = _parse_iso(video.get("actualEndAt"))
    if end is not None:
        return end >= past
    pub = _parse_iso(video.get("publishedAt"))
    if pub is not None:
        return past <= pub <= future
    start = _parse_iso(video.get("scheduledStartAt"))
    if start is not None:
        return past <= start <= future
    return True


def _initial_coverage_state(video: dict) -> str:
    if video["liveBroadcastStatus"] == "live":
        return sm.LIVE
    if video["liveBroadcastStatus"] == "upcoming":
        return sm.AWAITING_BROADCAST
    return sm.ARCHIVED  # completed livestream or an ordinary uploaded VOD


# ------------------------------------------------------------- C3: discovery
def discover_channel_videos(
    client: yt.YouTubeClient, channel: dict, *,
    lookback_days: int, horizon_days: int,
    now: dt.datetime | None = None, allow_search_fallback: bool = False,
) -> dict:
    """Discover one channel's videos via the cheap uploads-playlist path,
    falling back to search.list ONLY if `allow_search_fallback` is set AND
    the channel has no resolvable uploads playlist (C2's documented cost
    order: channels.list -> playlistItems.list -> videos.list -> search.list
    last). Returns a summary; performs no DB writes itself (see
    sync_broadcasts for the write path) so it is safe to call in dry-run."""
    now = now or _now()
    summary: dict[str, Any] = {
        "channelId": channel["id"], "videosSeen": 0, "inWindow": 0,
        "videos": [], "error": None, "usedSearchFallback": False,
    }
    cid = channel.get("channelId")
    if not cid:
        summary["error"] = "no confirmed channelId (disabled/unverified) — skipped"
        return summary
    try:
        ch_item = client.get_channel_by_id(cid)
    except yt.YouTubeQuotaExceeded as exc:
        summary["error"] = f"quota_exceeded: {exc}"
        return summary
    except (yt.YouTubeApiError, yt.YouTubeAuthError) as exc:
        summary["error"] = str(exc)
        return summary
    if not ch_item:
        summary["error"] = f"channel {cid} not found via API"
        return summary

    playlist_id = yt.uploads_playlist_id(ch_item)
    video_ids: list[str] = []
    try:
        if playlist_id:
            items = client.list_playlist_items(playlist_id)
            video_ids = [(i.get("contentDetails") or {}).get("videoId") for i in items]
            video_ids = [v for v in video_ids if v]
        if not video_ids and allow_search_fallback:
            found = client.search_channel_videos(cid)
            video_ids = [((i.get("id") or {}).get("videoId")) for i in found]
            video_ids = [v for v in video_ids if v]
            summary["usedSearchFallback"] = True
        videos_raw = client.list_videos(video_ids) if video_ids else []
    except yt.YouTubeQuotaExceeded as exc:
        summary["error"] = f"quota_exceeded: {exc}"
        return summary
    except (yt.YouTubeApiError, yt.YouTubeAuthError) as exc:
        summary["error"] = str(exc)
        return summary

    for raw in videos_raw:
        v = normalize_video(raw, channel=channel, discovered_at=now)
        if not v["videoId"]:
            continue
        summary["videosSeen"] += 1
        if in_window(v, now, lookback_days, horizon_days):
            summary["inWindow"] += 1
            summary["videos"].append(v)
    return summary


def upsert_broadcast_video(store: JobStore, v: dict) -> str:
    """Idempotent upsert of one normalized video + its `broadcast:<id>` job.
    Never regresses an already-advanced coverage_state (e.g. a rerun must
    not un-ARCHIVE a video a human already moved to NEEDS_REVIEW)."""
    existing = store.con.execute(
        "SELECT video_id, coverage_state FROM broadcast_videos WHERE video_id=?",
        (v["videoId"],)).fetchone()
    action = "updated" if existing else "inserted"
    new_state = _initial_coverage_state(v)
    if existing and sm.is_valid_state(existing["coverage_state"]):
        # Keep the existing state unless the fresh read is a legal forward
        # move from it (state_machine still governs correctness).
        if not sm.can_transition(existing["coverage_state"], new_state):
            new_state = existing["coverage_state"]
    store.con.execute(
        """INSERT INTO broadcast_videos
             (video_id, platform, channel_id, title, description, published_at,
              scheduled_start_at, actual_start_at, actual_end_at, live_broadcast_status,
              duration_seconds, thumbnail_url, source_url, region, language,
              official_channel, response_hash, coverage_state, discovered_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, CURRENT_TIMESTAMP)
           ON CONFLICT(video_id) DO UPDATE SET
             title=excluded.title, description=excluded.description,
             scheduled_start_at=excluded.scheduled_start_at,
             actual_start_at=excluded.actual_start_at,
             actual_end_at=excluded.actual_end_at,
             live_broadcast_status=excluded.live_broadcast_status,
             duration_seconds=excluded.duration_seconds,
             thumbnail_url=excluded.thumbnail_url,
             response_hash=excluded.response_hash,
             coverage_state=excluded.coverage_state,
             updated_at=CURRENT_TIMESTAMP""",
        (v["videoId"], v["platform"], v["channelId"], v["title"], v["description"],
         v["publishedAt"], v["scheduledStartAt"], v["actualStartAt"], v["actualEndAt"],
         v["liveBroadcastStatus"], v["durationSeconds"], v["thumbnailUrl"], v["sourceUrl"],
         v["region"], v["language"], int(v["officialChannel"]), v["responseHash"],
         new_state, v["discoveredAt"]))
    store.con.commit()
    store.enqueue(models.KIND_BROADCAST, models.broadcast_key(v["videoId"]),
                  payload={"videoId": v["videoId"], "channelId": v["channelId"]},
                  source_url=v["sourceUrl"])
    return action


def _record_quota(store: JobStore | None, day: str):
    def _sink(endpoint: str, units: int) -> None:
        if store is None:
            return
        store.con.execute(
            """INSERT INTO quota_usage (day, endpoint, units, calls)
               VALUES (?, ?, ?, 1)
               ON CONFLICT(day, endpoint) DO UPDATE SET
                 units = quota_usage.units + excluded.units,
                 calls = quota_usage.calls + 1,
                 updated_at = CURRENT_TIMESTAMP""",
            (day, endpoint, units))
        store.con.commit()
    return _sink


def sync_broadcasts(
    *,
    client: yt.YouTubeClient,
    store: JobStore | None,
    channels: list[dict],
    lookback_days: int,
    horizon_days: int,
    dry_run: bool = False,
    allow_search_fallback: bool = False,
    now: dt.datetime | None = None,
) -> dict:
    """Discover + (unless dry-run) persist broadcasts for every enabled,
    channelId-verified channel. Idempotent: reruns never duplicate a video
    row or a `broadcast:<id>` job; a `broadcast-discovery:<channel>:<window>`
    scan job is enqueued per channel so repeated scans of an unchanged
    window are visibly deduplicated too (C5)."""
    now = now or _now()
    window_start = (now - dt.timedelta(days=lookback_days)).date().isoformat()
    window_end = (now + dt.timedelta(days=horizon_days)).date().isoformat()
    summary: dict[str, Any] = {
        "dryRun": dry_run, "lookbackDays": lookback_days, "horizonDays": horizon_days,
        "channels": [], "videosSeen": 0, "inWindow": 0, "upserted": 0,
        "scanJobsCreated": 0, "errors": [],
    }
    if not channels:
        summary["note"] = "no enabled+verified channels — nothing to discover"
        return summary

    for ch in channels:
        result = discover_channel_videos(
            client, ch, lookback_days=lookback_days, horizon_days=horizon_days,
            now=now, allow_search_fallback=allow_search_fallback)
        summary["channels"].append({
            "channelId": ch["id"], "videosSeen": result["videosSeen"],
            "inWindow": result["inWindow"], "error": result["error"],
            "usedSearchFallback": result["usedSearchFallback"],
        })
        summary["videosSeen"] += result["videosSeen"]
        summary["inWindow"] += result["inWindow"]
        if result["error"]:
            summary["errors"].append({"channelId": ch["id"], "error": result["error"]})
            if not dry_run and store is not None:
                key = models.broadcast_discovery_key(ch["id"], window_start, window_end)
                store.enqueue(models.KIND_BROADCAST, key, payload={"channelId": ch["id"]})
                store.record_attempt(key, ok=False, error_code="YOUTUBE_API_ERROR",
                                     error_message=result["error"], now=now)
            continue
        if not dry_run and store is not None:
            key = models.broadcast_discovery_key(ch["id"], window_start, window_end)
            before = store.get(key)
            store.enqueue(models.KIND_BROADCAST, key, payload={
                "channelId": ch["id"], "windowStart": window_start, "windowEnd": window_end})
            if before is None:
                summary["scanJobsCreated"] += 1
                store.record_attempt(key, ok=True, now=now)
            for v in result["videos"]:
                action = upsert_broadcast_video(store, v)
                if action == "inserted":
                    summary["upserted"] += 1
    if client.quota_used:
        summary["quotaUsed"] = client.quota_used
        summary["quotaByEndpoint"] = dict(client.quota_by_endpoint)
    return summary
