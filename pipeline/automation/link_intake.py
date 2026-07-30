"""
link_intake.py — the URL-only operator front door (Phase 1).

One pasted OWCS broadcast link is the ONLY thing an operator should have to
supply. Everything the older `create-job` command demanded by hand (match id,
channel id, video id, teams, layout id) is either derived here from the URL
and its public YouTube metadata, or deliberately left UNKNOWN for a later
stage to propose with evidence.

What this module guarantees:

  * Canonical parsing. `watch?v=`, `youtu.be/<id>`, `/live/<id>`,
    `/embed/<id>`, `/shorts/<id>`, `?t=`/`#t=` timestamps, playlist and
    tracking query params all collapse to the SAME canonical video id and
    the SAME canonical URL. Nothing else is accepted — never an arbitrary
    host, never a bare string taken on faith.
  * Deterministic identity. The job key is `models.record_key(video_id)`,
    built only from the YouTube video id, so pasting the same broadcast
    twice (in any URL spelling, with or without a timestamp) attaches to the
    existing job instead of creating a second one.
  * Explicit authorization. A video from a channel in the VERIFIED
    `config/broadcast_channels.json` registry is auto-approved and says so.
    Anything else stays `pending-approval` until a human runs
    `approve-source --confirm`, which is recorded with who approved it, when,
    and why. Nothing downloads before that.
  * Evidence before a human gate. The Data API is the preferred metadata
    provider, but a missing `YOUTUBE_API_KEY` is not a human decision — it
    is a missing lookup. When the API cannot answer, intake falls back to
    `keyless_metadata` (yt-dlp's own metadata probe, then the public
    per-video Atom feed), which returns the SAME public `channelId` the API
    would have. The authorization rule below is unchanged; it just gets to
    run. Provenance (`metadata.source`) is recorded on the job either way.
  * Honest metadata failure. When YouTube metadata cannot be retrieved by
    ANY provider (no key and no keyless source, quota, network, deleted
    video), intake still records the job with
    `metadataStatus="unavailable"` and the classified reason — the link is
    never silently dropped, and the source is never auto-approved on
    missing evidence.
  * Broadcast-likeness warnings. The existing `broadcast_matching.
    broadcast_likeness` gate (already tuned against real promos/guides/
    shorts) is reused, never re-invented. An unlikely video is still
    recorded — with a loud warning and, for a non-registry channel, no path
    to auto-approval.

This module is import-light on purpose (no cv2, no ffmpeg): pasting a link
must work on any machine, and the CV dependency only matters once a worker
picks the job up.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import urllib.parse
from typing import Any, Callable

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_DIR = os.path.dirname(_HERE)
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

from . import broadcast_matching as bmatch
from . import config as cfg
from . import job_store as js
from . import models
from . import state_machine as sm
from . import youtube_api as yt

# Hosts a pasted link may use. Deliberately the same set worker.py accepts,
# plus the youtube-nocookie embed host, so intake can never admit a source
# the downloader would later reject as unofficial.
ALLOWED_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be", "youtube-nocookie.com",
    "www.youtube-nocookie.com",
})

# Path prefixes that carry the video id as the next path element.
_ID_PATH_PREFIXES = ("live", "embed", "v", "shorts")

# A YouTube video id is 11 chars of [A-Za-z0-9_-]. Validating the SHAPE is
# what stops a typo/tracking fragment from becoming a distinct "job".
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Approval states an intake job's source can be in. Only APPROVED ever
# permits a download.
SOURCE_PENDING = "pending-approval"
SOURCE_APPROVED = "approved"
SOURCE_REJECTED = "rejected"

# A metadata provider that carries no duration/live status (the keyless
# per-video feed) cannot prove a broadcast has ENDED. Within this window of
# its publish time the stream may still be running, so the source stays a
# human decision rather than being auto-approved into a download of an
# in-progress stream. Outside it, "published N hours ago" is evidence enough
# that whatever was streamed is over.
PARTIAL_METADATA_LIVE_WINDOW_SECONDS = 6 * 3600


class LinkIntakeError(ValueError):
    """A pasted link cannot be turned into a job. Always carries a stable
    `code` so callers/tests never parse prose."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------- URL parsing
def _timestamp_seconds(raw: str | None) -> int | None:
    """Parse a YouTube time offset ('90', '90s', '1m30s', '1h2m3s') to
    seconds. Returns None for anything unparseable — a timestamp is a
    convenience hint, never allowed to fail an intake."""
    if not raw:
        return None
    t = str(raw).strip().lower()
    if not t:
        return None
    if t.isdigit():
        return int(t)
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", t)
    if not m or not any(m.groups()):
        return None
    h, mi, s = (int(g or 0) for g in m.groups())
    return h * 3600 + mi * 60 + s


def parse_link(url: str) -> dict[str, Any]:
    """Canonicalize one pasted URL.

    Returns {"videoId", "canonicalUrl", "startSeconds", "playlistId",
             "host", "originalUrl", "droppedParams"}.

    Raises LinkIntakeError with a stable code for every rejection:
      empty_url / unsupported_scheme / unsupported_host / no_video_id /
      malformed_video_id.
    """
    raw = (url or "").strip()
    if not raw:
        raise LinkIntakeError("empty_url", "no URL given")
    # A bare 'youtu.be/xyz' or 'www.youtube.com/watch?v=xyz' paste is common;
    # add the scheme so urlsplit sees a host instead of a path.
    if "//" not in raw.split("?", 1)[0]:
        if re.match(r"^(?:www\.|m\.)?(?:youtube\.com|youtu\.be|youtube-nocookie\.com)/", raw, re.I):
            raw = "https://" + raw
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme.lower() not in ("http", "https"):
        raise LinkIntakeError(
            "unsupported_scheme",
            f"unsupported URL scheme {parsed.scheme!r} — only http/https "
            f"YouTube links are accepted")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise LinkIntakeError(
            "unsupported_host",
            f"unsupported host {host!r} — only official YouTube hosts "
            f"({', '.join(sorted(ALLOWED_HOSTS))}) are ever accepted")

    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    fragment = urllib.parse.parse_qs(parsed.fragment, keep_blank_values=True)
    parts = [p for p in parsed.path.split("/") if p]

    video_id: str | None = None
    if host in ("youtu.be", "www.youtu.be"):
        video_id = parts[0] if parts else None
    else:
        video_id = (query.get("v") or [None])[0]
        if not video_id and len(parts) >= 2 and parts[0].lower() in _ID_PATH_PREFIXES:
            video_id = parts[1]
        if not video_id and len(parts) == 1 and parts[0].lower() not in _ID_PATH_PREFIXES:
            # /<id> is not a documented YouTube video path; refuse rather
            # than guess a channel handle is a video.
            video_id = None
    if not video_id:
        raise LinkIntakeError(
            "no_video_id",
            f"could not find a video id in {url!r} — paste a watch?v=, "
            f"youtu.be/, /live/ or /embed/ broadcast link")
    video_id = video_id.strip()
    if not _VIDEO_ID_RE.match(video_id):
        raise LinkIntakeError(
            "malformed_video_id",
            f"{video_id!r} is not a valid 11-character YouTube video id")

    start = (_timestamp_seconds((query.get("t") or [None])[0])
             or _timestamp_seconds((query.get("start") or [None])[0])
             or _timestamp_seconds((fragment.get("t") or [None])[0]))
    dropped = sorted(k for k in query if k not in ("v", "t", "start"))
    return {
        "videoId": video_id,
        "canonicalUrl": canonical_url(video_id),
        "startSeconds": start,
        "playlistId": (query.get("list") or [None])[0],
        "host": host,
        "originalUrl": raw,
        "droppedParams": dropped,
    }


def canonical_url(video_id: str) -> str:
    """The ONE spelling of a broadcast URL this system stores and compares."""
    return f"https://www.youtube.com/watch?v={video_id}"


def job_key_for(video_id: str) -> str:
    """Deterministic job identity — the video id and nothing else, so the
    same broadcast pasted in five URL spellings is one job."""
    return models.record_key(video_id)


# ------------------------------------------------------------- registry view
def registry_channel_index(channels: list[dict] | None = None) -> dict[str, dict]:
    """{youtube channel id -> registry row} for VERIFIED, enabled, official
    channels only. A registry row without a confirmed channelId cannot
    authorize anything, by design."""
    rows = channels if channels is not None else cfg.load_all_channels()
    out: dict[str, dict] = {}
    for ch in rows:
        cid = ch.get("channelId")
        if not cid or not ch.get("enabled"):
            continue
        if (ch.get("verifiedStatus") or "verified") == "failed":
            continue
        out[cid] = ch
    return out


# ---------------------------------------------------------------- metadata
def fetch_metadata(client: "yt.YouTubeClient", video_id: str) -> dict[str, Any]:
    """Retrieve one video's public metadata.

    Never raises: every failure becomes
    {"status": "unavailable"|"not_found", "errorCode", "error"} so a link is
    recorded either way and authorization decisions stay evidence-based.
    """
    try:
        items = client.list_videos([video_id])
    except yt.YouTubeAuthError as exc:
        return {"status": "unavailable", "errorCode": "no_api_key",
                "error": str(exc)}
    except yt.YouTubeQuotaExceeded as exc:
        return {"status": "unavailable", "errorCode": "quota_exceeded",
                "error": str(exc)}
    except yt.YouTubeApiError as exc:
        code = yt.classify_error(exc.status, exc.reason)
        return {"status": "unavailable",
                "errorCode": f"api_{code}", "error": str(exc)}
    except OSError as exc:  # transport-level failure the client didn't wrap
        return {"status": "unavailable", "errorCode": "network_error",
                "error": f"{type(exc).__name__}: {exc}"}
    if not items:
        return {"status": "not_found", "errorCode": "video_not_found",
                "error": f"YouTube returned no video for id {video_id!r} "
                         f"(deleted, private, or a bad id)"}
    item = items[0]
    snippet = item.get("snippet") or {}
    live = item.get("liveStreamingDetails") or {}
    content = item.get("contentDetails") or {}
    from . import broadcast_discovery as bdisc
    from . import keyless_metadata as km
    return {
        "status": "ok",
        "source": km.SOURCE_DATA_API,
        "completeness": "full",
        "videoId": item.get("id") or video_id,
        "channelId": snippet.get("channelId"),
        "channelTitle": snippet.get("channelTitle"),
        "title": snippet.get("title"),
        "description": snippet.get("description"),
        "publishedAt": snippet.get("publishedAt"),
        "scheduledStartAt": live.get("scheduledStartTime"),
        "actualStartAt": live.get("actualStartTime"),
        "actualEndAt": live.get("actualEndTime"),
        "liveBroadcastStatus": bdisc._resolve_live_status(
            live, snippet.get("liveBroadcastContent")),
        "durationSeconds": bdisc._parse_duration(content.get("duration")),
        "privacyStatus": (item.get("status") or {}).get("privacyStatus"),
    }


def _with_keyless_fallback(metadata: dict[str, Any], video_id: str,
                           keyless: "Callable[[str], dict] | None",
                           warnings: list[str]) -> dict[str, Any]:
    """Ask the keyless ladder when the Data API could not answer.

    The API stays preferred (it is the only provider with exact
    liveStreamingDetails), so this runs ONLY on an API failure and never
    overwrites a successful API answer. Whatever happens is recorded:
      * fallback succeeded -> the returned metadata carries `source`,
        `completeness` and `keylessAttempts`, plus `primaryError` (why the
        API could not answer), and a warning names the swap;
      * fallback failed    -> the ORIGINAL API error is kept as the headline
        (it is the one an operator can fix by setting the secret), with the
        keyless attempts attached so the failure is not mysterious.
    """
    if metadata.get("status") == "ok" or keyless is None:
        return metadata
    primary = {"errorCode": metadata.get("errorCode"),
               "error": metadata.get("error"),
               "status": metadata.get("status")}
    try:
        fallback = keyless(video_id)
    except Exception as exc:  # noqa: BLE001 — a fallback must never break intake
        return dict(metadata, keylessAttempts=[{
            "rung": "keyless", "ok": False, "errorCode": "keyless_crashed",
            "error": f"{type(exc).__name__}: {exc}"}])
    attempts = fallback.get("attempts") or []
    if fallback.get("status") == "ok":
        from . import keyless_metadata as km
        warnings.append(
            f"YouTube Data API metadata was unavailable "
            f"[{primary['errorCode']}], so the public keyless source "
            f"'{fallback.get('source')}' was used instead — the channel id "
            f"it reports is the same public fact the API returns "
            f"({km.format_attempts(attempts)})")
        return dict(fallback, keylessAttempts=attempts, primaryError=primary)
    if fallback.get("status") == "not_found":
        # A keyless source that positively says "this video does not exist"
        # is BETTER evidence than the API's "I could not ask", so it becomes
        # the headline verdict.
        return dict(fallback, keylessAttempts=attempts, primaryError=primary)
    return dict(metadata, keylessAttempts=attempts,
                keylessError=fallback.get("error"))


# ----------------------------------------------------------- authorization
def authorize_source(metadata: dict, *,
                     registry: dict[str, dict],
                     likeness: dict | None = None,
                     now: str | None = None) -> dict[str, Any]:
    """Decide whether a source is authorized to download WITHOUT a human.

    Auto-approval requires all of:
      * metadata retrieved successfully by SOME provider (never approve on
        missing evidence — but a keyless provider's channel id is the same
        public fact the Data API would have returned, so it counts),
      * a channelId present in the verified official registry,
      * the video not failing the broadcast-likeness gate,
      * enough evidence to rule out an in-progress stream: a provider that
        reports no duration and no live status (`completeness: "partial"`)
        can only do that for a video published longer ago than
        PARTIAL_METADATA_LIVE_WINDOW_SECONDS.

    Everything else -> pending-approval with an explicit reason. This
    function is pure; it writes nothing.
    """
    if metadata.get("status") != "ok":
        return {
            "state": SOURCE_PENDING,
            "auto": False,
            "reasonCode": metadata.get("errorCode") or "metadata_unavailable",
            "reason": ("source metadata could not be retrieved by any "
                       "provider, so the channel cannot be checked against "
                       "the verified official registry — manual approval "
                       "required"),
            "registryChannel": None,
        }
    channel_id = metadata.get("channelId")
    row = registry.get(channel_id) if channel_id else None
    if row is None:
        return {
            "state": SOURCE_PENDING,
            "auto": False,
            "reasonCode": "channel_not_in_registry",
            "reason": (f"channel {channel_id or 'UNKNOWN'!r} "
                       f"({metadata.get('channelTitle') or 'unknown title'}) "
                       f"is not a verified official OWCS broadcast channel"),
            "registryChannel": None,
        }
    if likeness and likeness.get("confidence") == "unlikely":
        return {
            "state": SOURCE_PENDING,
            "auto": False,
            "reasonCode": "broadcast_likeness_failed",
            "reason": (f"channel {channel_id} is official, but this video "
                       f"does not look like a match broadcast "
                       f"(likeness score {likeness.get('score')}) — approve "
                       f"manually only if it really is one"),
            "registryChannel": row.get("id"),
        }
    if (metadata.get("completeness") == "partial"
            and _maybe_still_live(metadata, now)):
        return {
            "state": SOURCE_PENDING,
            "auto": False,
            "reasonCode": "live_status_unknown",
            "reason": (f"channel {channel_id} is the verified official "
                       f"registry entry {row.get('id')!r}, but the only "
                       f"metadata source that answered "
                       f"({metadata.get('source')}) carries no duration and "
                       f"no live status, and this video was published less "
                       f"than {PARTIAL_METADATA_LIVE_WINDOW_SECONDS // 3600}h "
                       f"ago — it may still be streaming. Wait for the VOD, "
                       f"install yt-dlp, or set YOUTUBE_API_KEY; approve "
                       f"manually if you can see the broadcast has ended"),
            "registryChannel": row.get("id"),
        }
    from . import keyless_metadata as km
    via = ("" if metadata.get("source") in (None, km.SOURCE_DATA_API)
           else f", confirmed without an API key via {metadata['source']}")
    return {
        "state": SOURCE_APPROVED,
        "auto": True,
        "reasonCode": "registry_channel",
        "reason": (f"channel {channel_id} is the verified official "
                   f"registry entry {row.get('id')!r} "
                   f"({row.get('region') or 'unknown region'}){via}"),
        "registryChannel": row.get("id"),
    }


def _maybe_still_live(metadata: dict, now: str | None) -> bool:
    """True when partial metadata cannot rule out an in-progress stream.

    Unknown or unparseable publish time counts as "cannot rule out" — a
    missing timestamp is not permission.
    """
    published = metadata.get("publishedAt")
    if not published:
        return True
    try:
        when = dt.datetime.fromisoformat(str(published).replace("Z", "+00:00"))
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    try:
        ref = (dt.datetime.fromisoformat(str(now).replace("Z", "+00:00"))
               if now else dt.datetime.now(dt.timezone.utc))
    except ValueError:
        ref = dt.datetime.now(dt.timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=dt.timezone.utc)
    return (ref - when).total_seconds() < PARTIAL_METADATA_LIVE_WINDOW_SECONDS


# ------------------------------------------------------------------ intake
def ingest_link(store: js.JobStore, url: str, *,
                client: "yt.YouTubeClient | None" = None,
                channels: list[dict] | None = None,
                dry_run: bool = False,
                requested_by: str | None = None,
                now: str | None = None,
                keyless: "Callable[[str], dict] | None" = None
                ) -> dict[str, Any]:
    """Turn one pasted URL into (at most) one job.

    Returns a result dict describing exactly what happened:
      {"ok", "videoId", "jobKey", "canonicalUrl", "created", "duplicate",
       "dryRun", "state", "source": {...}, "metadata": {...},
       "likeness": {...}, "warnings": [...], "nextCommand": "..."}

    `client` is the YouTube Data API client (preferred provider). `keyless`
    is the no-API-key fallback — a `(video_id) -> metadata` callable, built
    by `keyless_metadata.resolver()`; it is only consulted when the API
    client is absent or could not answer. BOTH default to None (no provider
    is ever conjured inside this function), so a caller — the CLI, a test —
    always decides explicitly what this intake is allowed to touch.

    `dry_run=True` writes NOTHING (no job row, no payload update) — it still
    parses, fetches metadata, and reports the decision it would have made.
    Re-running against the same URL is idempotent: the existing job is
    returned with `duplicate=True` and its payload gets any newly-available
    metadata merged in, never a second job.
    """
    parsed = parse_link(url)                      # raises LinkIntakeError
    video_id = parsed["videoId"]
    job_key = job_key_for(video_id)
    ts = now or _utcnow_iso()
    warnings: list[str] = []
    if parsed["droppedParams"]:
        warnings.append(
            f"ignored non-identifying query parameter(s): "
            f"{', '.join(parsed['droppedParams'])}")

    metadata = ({"status": "unavailable", "errorCode": "no_client",
                 "error": "no YouTube client supplied to intake"}
                if client is None else fetch_metadata(client, video_id))
    metadata = _with_keyless_fallback(metadata, video_id, keyless, warnings)

    likeness = None
    if metadata.get("status") == "ok":
        likeness = bmatch.broadcast_likeness({
            "title": metadata.get("title"),
            "description": metadata.get("description"),
            "liveBroadcastStatus": metadata.get("liveBroadcastStatus"),
            "durationSeconds": metadata.get("durationSeconds"),
        })
        if likeness["confidence"] == "unlikely":
            warnings.append(
                f"broadcast-likeness WARNING (score {likeness['score']}): "
                f"this looks like a promo/guide/short/unrelated upload, not "
                f"a match broadcast — " + "; ".join(likeness["reasons"][:4]))
        if metadata.get("liveBroadcastStatus") in ("live", "upcoming"):
            warnings.append(
                f"video is {metadata['liveBroadcastStatus']} — only COMPLETED "
                f"VODs are processed; re-run intake once the stream has ended")
        if metadata.get("completeness") == "partial":
            warnings.append(
                f"metadata source '{metadata.get('source')}' carries no "
                f"duration and no live status — the broadcast-likeness score "
                f"is computed from the title/description alone")
    else:
        keyless_note = ""
        if metadata.get("keylessAttempts") is not None:
            from . import keyless_metadata as km
            keyless_note = (f" (keyless fallback also failed: "
                            f"{km.format_attempts(metadata['keylessAttempts'])})")
        elif keyless is None:
            keyless_note = (" (no keyless fallback was offered to this "
                            "intake call)")
        warnings.append(
            f"metadata unavailable [{metadata.get('errorCode')}]: "
            f"{metadata.get('error')}{keyless_note} — the link is recorded, "
            f"but the source cannot be auto-approved without verifiable "
            f"channel evidence")

    registry = registry_channel_index(channels)
    decision = authorize_source(metadata, registry=registry,
                                likeness=likeness, now=ts)

    source_block = {
        "state": decision["state"],
        "autoApproved": decision["auto"],
        "reasonCode": decision["reasonCode"],
        "reason": decision["reason"],
        "registryChannel": decision["registryChannel"],
        "decidedAt": ts,
        "decidedBy": "automatic-registry-check" if decision["auto"] else None,
    }

    existing = store.get(job_key)
    payload_patch: dict[str, Any] = {
        "intake": {
            "originalUrl": parsed["originalUrl"],
            "canonicalUrl": parsed["canonicalUrl"],
            "startSecondsHint": parsed["startSeconds"],
            "playlistId": parsed["playlistId"],
            "requestedBy": requested_by,
            "requestedAt": ts,
            "warnings": warnings,
        },
        "videoId": video_id,
        "sourceUrl": parsed["canonicalUrl"],
        "channelId": metadata.get("channelId"),
        "broadcastAuthority": metadata.get("channelId"),
        "metadata": metadata,
        "likeness": likeness,
        # Identity resolution is a LATER stage's job; intake records the
        # honest UNKNOWNs rather than inventing them (see resolve.py).
        "matchId": (existing.payload.get("matchId") if existing else None),
        "tournamentId": (existing.payload.get("tournamentId") if existing else None),
        "teamA": (existing.payload.get("teamA") if existing else None),
        "teamB": (existing.payload.get("teamB") if existing else None),
        "expectedLayoutId": (existing.payload.get("expectedLayoutId")
                             if existing else None),
        "region": (metadata.get("region")
                   or (registry.get(metadata.get("channelId")) or {}).get("region")),
        "language": (registry.get(metadata.get("channelId")) or {}).get("language"),
    }

    result: dict[str, Any] = {
        "ok": True,
        "videoId": video_id,
        "jobKey": job_key,
        "canonicalUrl": parsed["canonicalUrl"],
        "dryRun": dry_run,
        "duplicate": existing is not None,
        "created": False,
        "metadata": metadata,
        "likeness": likeness,
        "warnings": warnings,
        "source": source_block,
    }

    if dry_run:
        result["state"] = existing.state if existing else "(would be) DISCOVERED"
        result["nextCommand"] = (
            f"python pipeline/automation/cli.py ingest-link "
            f"--url \"{parsed['canonicalUrl']}\"  (re-run without --dry-run)")
        return result

    if existing is None:
        # DISCOVERED, not ARCHIVED: a link is a discovered candidate, and it
        # only becomes downloadable ("ready") once the source is authorized.
        job = store.enqueue(models.KIND_RECORD, job_key,
                            payload=payload_patch, state=sm.DISCOVERED,
                            source_url=parsed["canonicalUrl"])
        result["created"] = True
    else:
        # Duplicate paste: attach to the SAME job. Never overwrite an
        # already-recorded human approval with a fresh automatic verdict.
        prior = (existing.payload.get("source") or {})
        if prior.get("state") == SOURCE_APPROVED and not prior.get("autoApproved"):
            source_block = prior
            result["source"] = prior
            warnings.append(
                "source was already manually approved — keeping the recorded "
                "human approval, not re-deciding it automatically")
        payload_patch["source"] = source_block
        # Preserve the first-seen intake record; append this paste to history.
        prior_intake = existing.payload.get("intake") or {}
        history = list(prior_intake.get("history") or [])
        history.append({"url": parsed["originalUrl"], "at": ts,
                        "requestedBy": requested_by})
        payload_patch["intake"] = dict(
            prior_intake,
            history=history[-20:],
            canonicalUrl=parsed["canonicalUrl"],
            warnings=warnings,
            lastSeenAt=ts,
        )
        job = store.update_payload(job_key, payload_patch)

    if result["created"]:
        store.update_payload(job_key, {"source": source_block})

    job = store.get(job_key)
    # An authorized source moves DISCOVERED -> SCHEDULED -> ARCHIVED ("linked
    # official VOD, download not started"), which is what a worker claims.
    if (job.payload.get("source") or {}).get("state") == SOURCE_APPROVED:
        _advance_to_ready(store, job)
        job = store.get(job_key)

    result["state"] = job.state
    result["nextCommand"] = next_command(job)
    return result


def _advance_to_ready(store: js.JobStore, job: models.Job) -> models.Job:
    """DISCOVERED -> SCHEDULED -> ARCHIVED, every hop a legal edge. Called
    only for an approved source; a no-op once the job has moved past
    ARCHIVED (a re-paste must never rewind a job that's already processing)."""
    order = [sm.DISCOVERED, sm.SCHEDULED, sm.ARCHIVED]
    if job.state not in order:
        return job
    idx = order.index(job.state)
    for target in order[idx + 1:]:
        job = store.transition(job.job_key, target)
    return job


# ----------------------------------------------------------- manual approval
def approve_source(store: js.JobStore, job_key: str, *,
                   approved_by: str, reason: str | None = None,
                   confirm: bool = False, reject: bool = False,
                   now: str | None = None) -> dict[str, Any]:
    """The ONE step a human must take for a non-registry source.

    Requires `confirm=True` — there is no default that approves anything.
    Records who approved it, when, and why, on the job payload (the audit
    trail), then advances an approved job to ARCHIVED so a worker can claim
    it. `reject=True` records an explicit refusal instead and leaves the job
    un-downloadable.
    """
    job = store.get(job_key)
    if job is None:
        raise LinkIntakeError("no_such_job", f"no such job: {job_key}")
    if not confirm:
        raise LinkIntakeError(
            "confirmation_required",
            "pass --confirm — there is no default that approves a source")
    if not (approved_by or "").strip():
        raise LinkIntakeError(
            "approver_required",
            "--approved-by is required: an approval with no accountable "
            "human is not an approval")
    ts = now or _utcnow_iso()
    prior = job.payload.get("source") or {}
    state = SOURCE_REJECTED if reject else SOURCE_APPROVED
    source_block = {
        "state": state,
        "autoApproved": False,
        "reasonCode": "manual_rejection" if reject else "manual_approval",
        "reason": reason or ("rejected by operator" if reject
                             else "approved by operator after review"),
        "registryChannel": prior.get("registryChannel"),
        "decidedAt": ts,
        "decidedBy": approved_by.strip(),
        "priorReasonCode": prior.get("reasonCode"),
        "priorState": prior.get("state"),
    }
    store.update_payload(job_key, {"source": source_block})
    job = store.get(job_key)
    if reject:
        # IGNORED is the system-visible "not pursuing this" verdict; the row
        # and its whole audit trail stay in the DB forever.
        if sm.can_transition(job.state, sm.IGNORED):
            job = store.transition(job_key, sm.IGNORED)
    else:
        job = _advance_to_ready(store, job)
    return {"ok": True, "jobKey": job_key, "state": job.state,
            "source": source_block, "nextCommand": next_command(job)}


# --------------------------------------------------------------- status view
def next_command(job: models.Job) -> str:
    """The EXACT next command an operator should run for this job. The intake
    panel and `link-status` both render this — one authoritative answer."""
    key = job.job_key
    source = job.payload.get("source") or {}
    if source.get("state") == SOURCE_REJECTED:
        return "none — this source was explicitly rejected"
    if source.get("state") != SOURCE_APPROVED:
        return (f"python pipeline/automation/cli.py approve-source "
                f"--job {key} --approved-by \"<your name>\" --confirm")
    mapping = {
        sm.DISCOVERED: f"python pipeline/automation/cli.py link-status --job {key}",
        sm.SCHEDULED: f"python pipeline/automation/cli.py worker-run --max-jobs 1",
        sm.ARCHIVED: "python pipeline/automation/cli.py worker-run --max-jobs 1",
        sm.DOWNLOADING: (f"python pipeline/automation/cli.py resume-job "
                         f"(if the worker crashed)"),
        sm.DOWNLOADED: f"python pipeline/automation/cli.py resolve-layout --job {key}",
        sm.NEEDS_LAYOUT: (f"python pipeline/automation/cli.py approve-layout "
                          f"--job {key} --confirm"),
        sm.SEGMENTING: "wait — candidate generation in progress",
        sm.NEEDS_REVIEW: (f"open intake.html (or: python pipeline/automation/cli.py "
                          f"segment-list --video-id {job.payload.get('videoId')})"),
        sm.READY_FOR_DETECTION: f"python pipeline/automation/cli.py detect-job {key}",
        sm.PROCESSING: "wait — detection in progress",
        sm.NEEDS_TEMPLATES: (f"python pipeline/automation/cli.py template-coverage "
                             f"--job {key}"),
        sm.APPROVED: (f"python pipeline/automation/cli.py process-approved-job "
                      f"--job {key} --publish"),
        sm.PUBLISHED: "none — published; confirm the PR merged and Pages deployed",
        sm.RETRY_SCHEDULED: f"python pipeline/automation/cli.py retry-job {key}",
        sm.FAILED: f"python pipeline/automation/cli.py retry-job {key}",
        sm.FAILED_PERMANENT: (f"investigate the dead letter, then: "
                              f"python pipeline/automation/cli.py retry-job "
                              f"{key} --force"),
        sm.IGNORED: "none — job was ignored",
        sm.CANCELLED: "none — job was cancelled",
    }
    return mapping.get(job.state, f"no defined next step for state {job.state}")


def blocking_reasons(job: models.Job) -> list[str]:
    """Every reason this job cannot move forward right now, in plain
    language. Empty list = nothing is blocking it."""
    out: list[str] = []
    source = job.payload.get("source") or {}
    if source.get("state") == SOURCE_PENDING:
        out.append(f"source not authorized: {source.get('reason')}")
    if source.get("state") == SOURCE_REJECTED:
        out.append(f"source rejected by {source.get('decidedBy')}: "
                   f"{source.get('reason')}")
    meta = job.payload.get("metadata") or {}
    if meta.get("status") != "ok":
        detail = ""
        if meta.get("keylessAttempts"):
            from . import keyless_metadata as km
            attempts = meta["keylessAttempts"]
            # Only name the remedy that actually applies: a missing yt-dlp is
            # an install away, while a network failure is not.
            remedy = (" (install yt-dlp to make the keyless path work)"
                      if any(a.get("errorCode") == "ytdlp_missing"
                             for a in attempts) else "")
            detail = (f"; keyless fallback: {km.format_attempts(attempts)}"
                      f"{remedy}")
        out.append(f"YouTube metadata unavailable [{meta.get('errorCode')}]: "
                   f"{meta.get('error')}{detail}")
    if (meta.get("liveBroadcastStatus") in ("live", "upcoming")):
        out.append(f"broadcast is {meta['liveBroadcastStatus']} — only "
                   f"completed VODs are processed")
    likeness = job.payload.get("likeness") or {}
    if likeness.get("confidence") == "unlikely":
        out.append(f"broadcast-likeness gate failed (score {likeness.get('score')})")
    if job.state == sm.NEEDS_REVIEW:
        out.append("awaiting human review of segments/compositions")
    if job.state == sm.NEEDS_LAYOUT:
        out.append("no known broadcast layout matched — calibration awaiting approval")
    if job.state == sm.NEEDS_TEMPLATES:
        out.append("hero-template coverage insufficient for this broadcast package")
    if job.last_error_code and job.state not in sm.TERMINAL_STATES:
        out.append(f"last error [{job.last_error_code}]: {job.last_error_message}")
    return out


def link_status(store: js.JobStore, *, job_key: str | None = None,
                video_id: str | None = None, url: str | None = None
                ) -> list[dict[str, Any]]:
    """Operator-facing status for one link (by job key, video id or URL) or
    for every intake job when nothing is specified."""
    if url:
        video_id = parse_link(url)["videoId"]
    if video_id and not job_key:
        job_key = job_key_for(video_id)
    if job_key:
        job = store.get(job_key)
        jobs = [job] if job else []
    else:
        jobs = [j for j in store.list_jobs(kind=models.KIND_RECORD)
                if j.payload.get("intake")]
    out = []
    for j in jobs:
        intake = j.payload.get("intake") or {}
        media = j.payload.get("media") or {}
        source = j.payload.get("source") or {}
        meta = j.payload.get("metadata") or {}
        out.append({
            "jobKey": j.job_key,
            "videoId": j.payload.get("videoId"),
            "state": j.state,
            "sourceState": source.get("state"),
            "sourceReason": source.get("reason"),
            "sourceDecidedBy": source.get("decidedBy"),
            "canonicalUrl": intake.get("canonicalUrl") or j.source_url,
            "title": meta.get("title"),
            "channelId": meta.get("channelId"),
            "channelTitle": meta.get("channelTitle"),
            "durationSeconds": meta.get("durationSeconds"),
            "metadataStatus": meta.get("status"),
            "metadataSource": meta.get("source"),
            "metadataCompleteness": meta.get("completeness"),
            "metadataAttempts": meta.get("keylessAttempts") or [],
            "likeness": j.payload.get("likeness"),
            "warnings": intake.get("warnings") or [],
            "pastes": 1 + len(intake.get("history") or []),
            "attempts": j.attempts,
            "lastErrorCode": j.last_error_code,
            "lastErrorMessage": j.last_error_message,
            "downloaded": bool(media.get("localPath")),
            "proxyPath": (media.get("proxy") or {}).get("localPath"),
            "layout": j.payload.get("layout"),
            # Download diagnostics. Everything here was already sanitized
            # by ytdlp_opts before being written to the payload (signed
            # URLs, cookie sources and profile paths are redacted at the
            # source), so it is safe to export and to render in a browser.
            "mediaProbe": j.payload.get("mediaProbe"),
            "downloadAttempts": j.payload.get("downloadAttempts") or [],
            "detectionAssets": j.payload.get("detectionAssets"),
            "lastFailure": j.payload.get("lastFailure"),
            "resumeState": j.payload.get("resumeState"),
            "nextRetryAt": j.next_retry_at,
            "qualityDowngrade": (media.get("qualityDowngrade")
                                 if isinstance(media, dict) else None),
            "blocking": blocking_reasons(j),
            "nextCommand": next_command(j),
        })
    return out


# ------------------------------------------------------- intake review panel
def build_intake_report(store: js.JobStore, *, job_key: str | None = None
                        ) -> dict[str, Any]:
    """Everything the operator-facing `intake.html` panel renders, in one
    JSON-serializable structure: per job the stage, the exact next command,
    every blocking reason, the segment timeline with thumbnails, and each
    segment's proposed identity.

    Read-only, and deliberately built from the SAME functions the CLI uses
    (`link_status`, `next_command`, `blocking_reasons`,
    `segment_identity.load_proposals`) so the page can never disagree with
    the command line about what state a job is in.

    Nothing secret can reach this file: job payloads carry only public
    broadcast metadata (video ids, titles, channel ids, public URLs), match/
    team ids, and local media paths.
    """
    from . import segment_identity as si
    from . import segmentation as seg

    rows = link_status(store, job_key=job_key)
    jobs_out = []
    for r in rows:
        job = store.get(r["jobKey"])
        segments = seg.list_segments(store.con, video_id=r["videoId"])
        seg_out = []
        for s in segments:
            proposal = si.load_proposals(store.con, s["id"])
            try:
                thumbs = json.loads(s.get("thumbnails") or "[]")
            except (ValueError, TypeError):
                thumbs = []
            try:
                signals = json.loads(s.get("signals") or "{}")
            except (ValueError, TypeError):
                signals = {}
            seg_out.append({
                "id": s["id"],
                "start": s["start_time"], "end": s["end_time"],
                "durationSeconds": round((s["end_time"] or 0)
                                         - (s["start_time"] or 0), 1),
                "confidence": s["confidence"],
                "reviewStatus": s["review_status"],
                "identityStatus": s.get("identity_status"),
                "gameplaySamples": signals.get("gameplaySamples"),
                "method": signals.get("method"),
                "rejections": signals.get("rejections") or {},
                "thumbnails": thumbs,
                "confirmed": {"mapOrder": s.get("candidate_map_order"),
                              "map": s.get("map_name"), "mode": s.get("map_mode"),
                              "teamA": s.get("team_a"), "teamB": s.get("team_b"),
                              "side": s.get("side_assignment"),
                              "layoutId": s.get("layout_id"),
                              "note": s.get("reviewer_note")},
                "proposed": ({k: proposal.get(k) for k in
                              ("map", "mode", "teamA", "teamB",
                               "sideAssignment", "mapOrder", "players")}
                             if proposal else None),
                "reviewTasks": (proposal or {}).get("reviewTasks") or [],
                "actions": _segment_actions(s, proposal),
            })
        layout = (job.payload.get("layout") or {}) if job else {}
        jobs_out.append(dict(r, segments=seg_out, layout={
            "layoutId": layout.get("layoutId"),
            "decision": layout.get("decision"),
            "reason": layout.get("reason"),
            "source": layout.get("source"),
            "approvalRequired": layout.get("approvalRequired"),
            "candidates": layout.get("candidates") or [],
            "calibration": {k: (layout.get("calibration") or {}).get(k)
                            for k in ("confidence", "floor", "reviewSheet",
                                      "layoutPath", "refusal")},
            "markers": (layout.get("markers") or {}).get("harvested") or {},
        }))
    return {
        "schema": "intake.v1",
        "generatedAt": _utcnow_iso(),
        "note": ("Read-only operator snapshot generated by "
                 "`pipeline/automation/cli.py intake-export --save`. Every "
                 "action listed is a CLI command an operator runs — this "
                 "static page never writes anything."),
        "jobs": jobs_out,
    }


def _segment_actions(segment: dict, proposal: dict | None) -> list[dict]:
    """The exact commands available for one segment right now. A command is
    only offered when it would actually be accepted — an operator should never
    be handed a command that is going to refuse."""
    sid = segment["id"]
    status = segment["review_status"]
    actions: list[dict] = []
    if status == "pending":
        blocking = [t for t in (proposal or {}).get("reviewTasks", [])
                    if t["severity"] == "blocking"]
        if proposal and not blocking:
            actions.append({
                "label": "Accept proposed",
                "command": f"accept-proposed --segment {sid}",
                "note": "approves using the machine's proposal, unchanged"})
        elif proposal:
            actions.append({
                "label": "Accept proposed (BLOCKED)",
                "command": None,
                "note": "; ".join(t["reason"] for t in blocking)})
        else:
            actions.append({
                "label": "Propose identity",
                "command": f"propose-identity --job <job> --segment-id {sid}",
                "note": "read map/teams/players off the broadcast first"})
        actions.append({
            "label": "Edit and approve",
            "command": (f"segment-approve {sid} --map-order N --map-name X "
                        f"--map-mode Y --team-a A --team-b B "
                        f"--side team_a_left --layout-id L"),
            "note": "correct any proposed value by hand"})
        actions.append({
            "label": "Split",
            "command": f"segment-split {sid} --at <seconds>",
            "note": "the window spans more than one map"})
        actions.append({
            "label": "Merge",
            "command": f"segment-merge {sid} <other-id>",
            "note": "one map was split across two windows"})
        actions.append({
            "label": "Reject",
            "command": f"segment-reject {sid} --reason \"<why>\"",
            "note": "desk content, replay, or not a map"})
    elif status == "approved":
        actions.append({
            "label": "Run detection",
            "command": "detect-job <job>",
            "note": "dry run first — nothing is written"})
    return actions


def format_status(rows: list[dict]) -> str:
    if not rows:
        return "  (no intake jobs — paste one with `ingest-link --url ...`)"
    lines = []
    for r in rows:
        lines.append(f"  {r['jobKey']}")
        lines.append(f"    video      : {r['videoId']}  {r['canonicalUrl']}")
        lines.append(f"    title      : {r['title'] or '(metadata unavailable)'}")
        lines.append(f"    channel    : {r['channelTitle'] or '?'} ({r['channelId'] or '?'})"
                     + (f" [via {r['metadataSource']}]" if r.get("metadataSource") else ""))
        lines.append(f"    state      : {r['state']}")
        lines.append(f"    source     : {r['sourceState']} — {r['sourceReason']}"
                     + (f" [by {r['sourceDecidedBy']}]" if r["sourceDecidedBy"] else ""))
        if r["durationSeconds"]:
            lines.append(f"    duration   : {r['durationSeconds']}s")
        lines.append(f"    pastes     : {r['pastes']} (duplicates attach, never duplicate)")
        if r["downloaded"]:
            lines.append(f"    media      : downloaded"
                         + (f", proxy {r['proxyPath']}" if r["proxyPath"] else ""))
        for w in r["warnings"]:
            lines.append(f"    WARNING    : {w}")
        for b in r["blocking"]:
            lines.append(f"    BLOCKED    : {b}")
        lines.append(f"    next       : {r['nextCommand']}")
    return "\n".join(lines)
