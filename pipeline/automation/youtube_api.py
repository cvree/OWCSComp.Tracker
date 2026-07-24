"""
youtube_api.py — YouTube Data API v3 client + normalizer (Roadmap Phase C2).

Discovery reads broadcasts from official OWCS YouTube channels. YouTube is a
BROADCAST-LOCATION source only: it tells us WHERE a match was streamed, never
WHAT was played. This module fetches channels, upload playlists, video details
and (as a costly fallback) search results, and normalizes them to a small,
explicit shape. It NEVER extracts, infers or fabricates hero compositions,
swaps, timelines or rates, and it NEVER downloads a byte of video.

Hard rules baked in here (mirroring faceit_api.py):

  * Prefer official channel upload playlists over broad search. `videos.list`
    and `playlistItems.list` cost 1 quota unit; `search.list` costs ~100. The
    client tracks every unit it spends so a run can prove it stayed in budget.
  * The API key is a secret. It is read from YOUTUBE_API_KEY and is NEVER
    printed, cached, logged, or written into the request-audit trail — every
    recorded/cached URL has the `key=` parameter redacted first.
  * The HTTP transport is injectable, so the whole broadcast pipeline is
    testable offline with fixtures — no network, no key.

The normalized video shape classifies each item into exactly one broadcast
lifecycle:  upcoming (scheduled livestream) | live (streaming now) |
completed (finished livestream, has an archived VOD) | vod (plain upload).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

API_ROOT = "https://www.googleapis.com/youtube/v3"
USER_AGENT = "OWCS-Comp-Tracker/0.5 (+fan project; official OWCS broadcast discovery)"

# Transport contract: (url, headers) -> (status:int|None, text:str|None, error:str|None)
Transport = Callable[[str, dict], "tuple[int | None, str | None, str | None]"]

# Quota cost per endpoint (YouTube Data API v3 published costs). Discovery is
# built to stay on the 1-unit endpoints; search is the 100-unit last resort.
QUOTA_COST = {
    "channels": 1,
    "playlistItems": 1,
    "videos": 1,
    "search": 100,
}

# The default free daily quota YouTube grants a project. Used only as a safety
# ceiling if the operator does not override it; discovery aborts cleanly with a
# YoutubeQuotaError rather than burning past the budget.
DEFAULT_DAILY_QUOTA = 10000


class YoutubeAuthError(RuntimeError):
    """Raised when a real network call is attempted without an API key."""


class YoutubeApiError(RuntimeError):
    def __init__(self, url: str, status: int | None, error: str | None):
        self.url = redact_key(url)
        self.status = status
        self.error = error
        super().__init__(f"YouTube API error ({status}) for {self.url}: {error}")


class YoutubeQuotaError(RuntimeError):
    """Raised when the configured quota budget would be exceeded, or when the
    API itself reports quotaExceeded. Discovery treats this as a soft stop:
    everything found so far is kept; nothing is invented to fill the gap."""

    def __init__(self, used: int, budget: int, endpoint: str):
        self.used = used
        self.budget = budget
        self.endpoint = endpoint
        super().__init__(
            f"YouTube quota budget exhausted: {used}/{budget} units used, "
            f"next {endpoint} call ({QUOTA_COST.get(endpoint, 1)}u) would exceed it")


# ------------------------------------------------------------------ redaction
_KEY_RE = re.compile(r"([?&])key=[^&]*")


def redact_key(url: str) -> str:
    """Strip the API key from a URL so it never reaches a log, cache file, audit
    record or exception message. Safe to call on any string."""
    return _KEY_RE.sub(r"\1key=REDACTED", url or "")


# ------------------------------------------------------------------ time utils
def _clean(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _iso_z(value: Any) -> str | None:
    """YouTube timestamps are RFC 3339 (e.g. 2026-07-20T18:00:00Z). Normalize to
    a +00:00 offset ISO string; pass through anything already ISO-ish."""
    s = _clean(value)
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).replace(
            microsecond=0).isoformat()
    except ValueError:
        return s


# ------------------------------------------------------------------ transport
def urllib_transport(api_key: str) -> Transport:
    def _t(url: str, headers: dict) -> "tuple[int | None, str | None, str | None]":
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            **headers,
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                status = getattr(resp, "status", None) or 200
                return status, resp.read().decode("utf-8", "ignore"), None
        except urllib.error.HTTPError as exc:
            # Read the body so a quotaExceeded reason can be surfaced, but never
            # leak the key (it is in the request URL, not the body).
            body = ""
            try:
                body = exc.read().decode("utf-8", "ignore")
            except Exception:  # pragma: no cover - defensive
                pass
            return exc.code, body or None, f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return None, None, str(exc)
    return _t


def fixture_transport(fixture_dir: str) -> Transport:
    """Offline transport that serves committed JSON instead of the API.

    Files are keyed by endpoint + a stable discriminator so a test can express a
    whole scenario as flat JSON with no network and no key:

      channels.list?forHandle=X   -> handle_<x>.json
      channels.list?id=X          -> channel_<x>.json
      playlistItems.list?...=P     -> playlist_<p>.json
      videos.list?id=A,B          -> videos_<sha8-of-sorted-ids>.json,
                                      else a per-id videos_<id>.json merge
      search.list?...             -> search.json (or search_<channelId>.json)

    A missing fixture returns 404 so the caller's error/retry path is exercised
    exactly as it would be against the real API.
    """
    def _serve(fname: str):
        path = os.path.join(fixture_dir, fname)
        if not os.path.exists(path):
            return 404, None, f"no fixture: {fname}"
        return 200, Path(path).read_text(encoding="utf-8"), None

    def _slug(v: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]+", "_", (v or "").strip().lower()).strip("_") or "x"

    def _t(url: str, headers: dict) -> "tuple[int | None, str | None, str | None]":
        parsed = urllib.parse.urlparse(url)
        endpoint = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        q = urllib.parse.parse_qs(parsed.query)

        if endpoint == "channels":
            if "forHandle" in q:
                return _serve(f"handle_{_slug(q['forHandle'][0])}.json")
            if "id" in q:
                return _serve(f"channel_{_slug(q['id'][0])}.json")
            return 404, None, "channels.list needs id or forHandle"

        if endpoint == "playlistItems":
            pid = (q.get("playlistId") or ["x"])[0]
            return _serve(f"playlist_{_slug(pid)}.json")

        if endpoint == "videos":
            ids = (q.get("id") or [""])[0]
            id_list = [x for x in ids.split(",") if x]
            combo = "videos_" + hashlib.sha1(",".join(sorted(id_list)).encode()
                                             ).hexdigest()[:8] + ".json"
            if os.path.exists(os.path.join(fixture_dir, combo)):
                return _serve(combo)
            # Fall back to merging per-id fixtures so a test can describe one
            # video per file and query any subset of them.
            items: list[dict] = []
            for vid in id_list:
                p = os.path.join(fixture_dir, f"videos_{_slug(vid)}.json")
                if os.path.exists(p):
                    payload = json.loads(Path(p).read_text(encoding="utf-8"))
                    items.extend(payload.get("items", []) or [])
            if items:
                return 200, json.dumps({"items": items}), None
            return _serve(combo)  # 404 with a clear name

        if endpoint == "search":
            ch = (q.get("channelId") or [None])[0]
            if ch and os.path.exists(os.path.join(fixture_dir, f"search_{_slug(ch)}.json")):
                return _serve(f"search_{_slug(ch)}.json")
            return _serve("search.json")

        return 404, None, f"unmapped fixture endpoint: {endpoint}"
    return _t


# --------------------------------------------------------------------- client
class YoutubeClient:
    """Thin YouTube Data API v3 client with quota accounting + key redaction.

    `quota_budget` caps how many units a single run may spend; when the next
    call would exceed it the client raises YoutubeQuotaError before making the
    request. Raw responses are cached (key-redacted URLs) for auditability when
    `cache_dir` is set — that directory is gitignored (`data/raw/`).
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        transport: Transport | None = None,
        cache_dir: str | None = None,
        quota_budget: int = DEFAULT_DAILY_QUOTA,
    ):
        self.api_key = api_key or os.environ.get("YOUTUBE_API_KEY")
        if transport is not None:
            self._transport = transport
        elif self.api_key:
            self._transport = urllib_transport(self.api_key)
        else:
            self._transport = None  # set on first real call -> clear error
        self.cache_dir = cache_dir
        self.quota_budget = int(quota_budget)
        self.quota_used = 0
        self.calls: list[dict] = []  # audit trail (key-redacted)

    # -- low level ---------------------------------------------------------
    def _get(self, endpoint: str, params: dict) -> dict:
        cost = QUOTA_COST.get(endpoint, 1)
        if self.quota_used + cost > self.quota_budget:
            raise YoutubeQuotaError(self.quota_used, self.quota_budget, endpoint)

        query = dict(params)
        if self.api_key:
            query["key"] = self.api_key
        url = f"{API_ROOT}/{endpoint}?" + urllib.parse.urlencode(query, doseq=True)

        if self._transport is None:
            raise YoutubeAuthError(
                "YOUTUBE_API_KEY is not set and no transport was injected; "
                "cannot make a live YouTube request. Set the secret or run "
                "against fixtures.")

        status, text, error = self._transport(url, {})
        # Charge quota for every attempt that actually reached the API (a 4xx/5xx
        # still consumes units on the real service, except transport-level
        # failures where status is None).
        if status is not None:
            self.quota_used += cost
        safe_url = redact_key(url)
        self.calls.append({
            "endpoint": endpoint, "url": safe_url, "status": status,
            "cost": cost, "error": error,
            "sha256": hashlib.sha256((text or "").encode()).hexdigest() if text else None,
        })
        if self.cache_dir and text:
            self._cache(safe_url, text)

        # Detect an explicit quota-exceeded response from the API body.
        if status == 403 and text and "quotaexceeded" in text.lower():
            raise YoutubeQuotaError(self.quota_used, self.quota_budget, endpoint)
        if error or not text:
            raise YoutubeApiError(safe_url, status, error)
        try:
            return json.loads(text)
        except ValueError as exc:
            raise YoutubeApiError(safe_url, status, f"invalid JSON: {exc}") from exc

    def _cache(self, safe_url: str, text: str) -> None:
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(safe_url.encode()).hexdigest()[:20]
        Path(os.path.join(self.cache_dir, f"{key}.json")).write_text(text, encoding="utf-8")

    # -- endpoints ---------------------------------------------------------
    def get_channels_by_ids(self, channel_ids: list[str]) -> list[dict]:
        """channels.list by id (batched up to 50). 1 unit per call."""
        out: list[dict] = []
        ids = [c for c in channel_ids if c]
        for i in range(0, len(ids), 50):
            payload = self._get("channels", {
                "part": "snippet,contentDetails,statistics,status",
                "id": ",".join(ids[i:i + 50]),
                "maxResults": 50,
            })
            out.extend(payload.get("items", []) or [])
        return out

    def get_channel_by_handle(self, handle: str) -> dict | None:
        """Resolve an @handle to a channel resource (1 unit). Returns None if the
        handle resolves to nothing."""
        h = handle.lstrip("@")
        payload = self._get("channels", {
            "part": "snippet,contentDetails,statistics,status",
            "forHandle": h,
        })
        items = payload.get("items", []) or []
        return items[0] if items else None

    def list_playlist_items(
        self, playlist_id: str, *, page_size: int = 50, max_pages: int = 6,
    ) -> list[dict]:
        """All items of a playlist (typically a channel's uploads playlist),
        paginated. 1 unit per page. Newest uploads come first."""
        out: list[dict] = []
        page_token: str | None = None
        for _ in range(max_pages):
            params = {
                "part": "snippet,contentDetails",
                "playlistId": playlist_id,
                "maxResults": page_size,
            }
            if page_token:
                params["pageToken"] = page_token
            payload = self._get("playlistItems", params)
            out.extend(payload.get("items", []) or [])
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        return out

    def get_videos(self, video_ids: list[str]) -> list[dict]:
        """videos.list by id (batched up to 50). 1 unit per call. Includes
        liveStreamingDetails so a livestream's real start/end are known."""
        out: list[dict] = []
        ids = [v for v in dict.fromkeys(video_ids) if v]  # de-dupe, keep order
        for i in range(0, len(ids), 50):
            payload = self._get("videos", {
                "part": "snippet,contentDetails,liveStreamingDetails,status",
                "id": ",".join(ids[i:i + 50]),
                "maxResults": 50,
            })
            out.extend(payload.get("items", []) or [])
        return out

    def search_channel_videos(
        self, channel_id: str, query: str | None = None, *,
        event_type: str | None = None, published_after: str | None = None,
        published_before: str | None = None, limit: int = 25,
    ) -> list[dict]:
        """search.list scoped to ONE channel (100 units — fallback only).

        Used when a channel exposes no uploads playlist, or to catch a
        members-only / unlisted-from-uploads livestream. Scoping to the channel
        keeps results official; it is still charged the full 100 units, so the
        orchestrator only calls it when the cheap path came up empty.
        """
        params: dict[str, Any] = {
            "part": "snippet", "type": "video", "channelId": channel_id,
            "order": "date", "maxResults": min(limit, 50),
        }
        if query:
            params["q"] = query
        if event_type:  # 'live' | 'upcoming' | 'completed'
            params["eventType"] = event_type
        if published_after:
            params["publishedAfter"] = published_after
        if published_before:
            params["publishedBefore"] = published_before
        payload = self._get("search", params)
        return payload.get("items", []) or []


# --------------------------------------------------------------- normalizers
def normalize_channel(raw: dict) -> dict:
    """Normalize a channels.list item into the fields the registry records."""
    snip = raw.get("snippet") or {}
    content = raw.get("contentDetails") or {}
    related = content.get("relatedPlaylists") or {}
    stats = raw.get("statistics") or {}
    return {
        "channelId": _clean(raw.get("id")),
        "title": _clean(snip.get("title")),
        "customUrl": _clean(snip.get("customUrl")),
        "country": _clean(snip.get("country")),
        "publishedAt": _iso_z(snip.get("publishedAt")),
        "uploadsPlaylistId": _clean(related.get("uploads")),
        "subscriberCount": stats.get("subscriberCount"),
        "videoCount": stats.get("videoCount"),
        "raw": raw,
    }


def playlist_item_video_id(raw: dict) -> str | None:
    """Extract the video id from a playlistItems entry (it lives under
    contentDetails.videoId, or snippet.resourceId.videoId)."""
    content = raw.get("contentDetails") or {}
    vid = content.get("videoId")
    if vid:
        return _clean(vid)
    rid = (raw.get("snippet") or {}).get("resourceId") or {}
    return _clean(rid.get("videoId"))


def classify_broadcast(raw: dict) -> str:
    """Classify a videos.list item into one broadcast lifecycle bucket:
    'upcoming' | 'live' | 'completed' | 'vod'.

    liveBroadcastContent is authoritative for upcoming/live. A finished
    livestream keeps its liveStreamingDetails (with actualEndTime) and reports
    liveBroadcastContent == 'none' — that is a 'completed' broadcast, distinct
    from a plain uploaded 'vod' which has no liveStreamingDetails at all.
    """
    snip = raw.get("snippet") or {}
    lbc = (snip.get("liveBroadcastContent") or "none").lower()
    lsd = raw.get("liveStreamingDetails") or {}
    if lbc == "live":
        return "live"
    if lbc == "upcoming":
        return "upcoming"
    if lsd:
        # Streaming details present but not currently live/upcoming -> finished.
        if lsd.get("actualEndTime") or lsd.get("actualStartTime"):
            return "completed"
        # Scheduled time only (edge case): treat as upcoming.
        if lsd.get("scheduledStartTime"):
            return "upcoming"
    return "vod"


def normalize_video(raw: dict) -> dict:
    """Turn one videos.list (or playlistItems/search) item into normalized
    broadcast facts. No compositions — the shape has no field for a hero.

    Output shape:
      {
        videoId, channelId, channelTitle, title, description, publishedAt,
        broadcastType (upcoming|live|completed|vod),
        liveBroadcastContent, scheduledStartTime, actualStartTime,
        actualEndTime, privacyStatus, durationSeconds, url, raw
      }
    """
    snip = raw.get("snippet") or {}
    lsd = raw.get("liveStreamingDetails") or {}
    status = raw.get("status") or {}
    content = raw.get("contentDetails") or {}

    # video id can come from videos.list (id string), search (id.videoId) or
    # playlistItems (contentDetails.videoId).
    vid = raw.get("id")
    if isinstance(vid, dict):
        vid = vid.get("videoId")
    vid = _clean(vid) or playlist_item_video_id(raw)

    return {
        "videoId": vid,
        "channelId": _clean(snip.get("channelId")),
        "channelTitle": _clean(snip.get("channelTitle")),
        "title": _clean(snip.get("title")),
        "description": _clean(snip.get("description")),
        "publishedAt": _iso_z(snip.get("publishedAt")),
        "broadcastType": classify_broadcast(raw),
        "liveBroadcastContent": (snip.get("liveBroadcastContent") or "none").lower(),
        "scheduledStartTime": _iso_z(lsd.get("scheduledStartTime")),
        "actualStartTime": _iso_z(lsd.get("actualStartTime")),
        "actualEndTime": _iso_z(lsd.get("actualEndTime")),
        "privacyStatus": _clean(status.get("privacyStatus")),
        "duration": _clean(content.get("duration")),
        "url": f"https://www.youtube.com/watch?v={vid}" if vid else None,
        "raw": raw,
    }


def broadcast_time(video: dict) -> str | None:
    """The most meaningful single timestamp for a broadcast, in priority order:
    when it actually started, else when it is scheduled to start, else when it
    was published. Used for rolling-window filtering and time matching."""
    return (video.get("actualStartTime") or video.get("scheduledStartTime")
            or video.get("publishedAt"))
