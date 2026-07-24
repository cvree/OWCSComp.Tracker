"""
youtube_api.py — YouTube Data API v3 client (Roadmap Phase C2).

Broadcast discovery is read-only and quota-conscious. This client never
downloads video and never calls search.list except as an explicit fallback
that a caller opts into (search.list costs ~100 quota units vs 1 for the
other three endpoints). The preferred path, cheapest-first:

  1. channels.list   — resolve a configured channel's uploads playlist id
  2. playlistItems.list — enumerate that playlist's uploads (paginated)
  3. videos.list      — batch-hydrate status/liveStreamingDetails for the
                         video ids found (up to 50 ids/call)
  4. search.list       — LAST RESORT, only when uploads can't satisfy
                         discovery (e.g. a multi-channel simulcast search)

Quota cost assumptions (Data API v3, documented so a future change is a
deliberate diff, not a silent guess):
  channels.list        = 1 unit / call
  playlistItems.list   = 1 unit / call (up to 50 items/page)
  videos.list          = 1 unit / call (up to 50 ids/call, batched)
  search.list          = 100 units / call (fallback only)
The default YouTube Data API v3 project quota is 10,000 units/day; quota
accounting here (`quota_used`, `quota_by_endpoint`) lets an operator or
coverage.py see exactly how much of that a run spent.

The HTTP transport is injectable (mirrors faceit_api.py) so the whole client
is testable offline: no network, no API key. The real transport reads the
key from the YOUTUBE_API_KEY environment variable — the key is NEVER logged,
cached, or included in any recorded call/error: every URL is sanitized
(the `key` query param stripped) before it is stored in `self.calls`, used
as a cache key, or embedded in an exception message.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

API_ROOT = "https://www.googleapis.com/youtube/v3"
USER_AGENT = "OWCS-Comp-Tracker/0.4 (+fan project; official OWCS broadcast discovery)"

QUOTA_COSTS: dict[str, int] = {
    "channels.list": 1,
    "playlistItems.list": 1,
    "videos.list": 1,
    "search.list": 100,
}

_PATHS: dict[str, str] = {
    "channels.list": "/channels",
    "playlistItems.list": "/playlistItems",
    "videos.list": "/videos",
    "search.list": "/search",
}

# Transport contract: (url, headers) -> (status:int|None, text:str|None, error:str|None)
# `url` already carries the API key as a query param (real calls only); a
# fixture/test transport never sees a real key.
Transport = Callable[[str, dict], "tuple[int | None, str | None, str | None]"]


class YouTubeAuthError(RuntimeError):
    """Raised when a real network call is attempted without an API key."""


class YouTubeApiError(RuntimeError):
    def __init__(self, endpoint: str, status: int | None, reason: str | None, message: str | None):
        self.endpoint = endpoint
        self.status = status
        self.reason = reason  # YouTube's machine-readable reason, e.g. 'quotaExceeded'
        super().__init__(
            f"YouTube API error ({status}) on {endpoint}: {reason or message or 'unknown error'}")


class YouTubeQuotaExceeded(YouTubeApiError):
    """Raised specifically when the API reports quota exhaustion (403
    quotaExceeded/dailyLimitExceeded) — distinct from other 403s so a caller
    can stop the whole run instead of retrying a doomed request."""


# Reasons the real API returns in error.errors[0].reason.
_RETRYABLE_REASONS = {"backendError", "internalError", "rateLimitExceeded", "userRateLimitExceeded"}
_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded"}


def classify_error(status: int | None, reason: str | None) -> str:
    """Classify a failed call as 'quota_exceeded', 'retryable', or
    'permanent'. Pure, never raises — used by discovery to decide whether to
    enqueue a retry job or fail the channel outright."""
    if reason in _QUOTA_REASONS:
        return "quota_exceeded"
    if reason in _RETRYABLE_REASONS:
        return "retryable"
    if status in (429, 500, 502, 503, 504):
        return "retryable"
    return "permanent"


def _slug(value: Any) -> str:
    import re
    s = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip()).strip("_").lower()
    return s or "x"


def _sanitize_url(url: str) -> str:
    """Strip the `key` query param so it can never leak into logs, the audit
    trail, cache keys, or exception messages."""
    parsed = urllib.parse.urlsplit(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
         if k.lower() != "key"]
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(q)))


def _extract_error(text: str | None) -> "tuple[str | None, str | None]":
    """Pull (reason, message) out of YouTube's standard error JSON body."""
    if not text:
        return None, None
    try:
        body = json.loads(text)
    except (ValueError, TypeError):
        return None, None
    err = body.get("error") or {}
    errs = err.get("errors") or []
    reason = errs[0].get("reason") if errs else None
    return reason, err.get("message")


# ------------------------------------------------------------------ transport
def urllib_transport(api_key: str) -> Transport:
    def _t(url: str, headers: dict) -> "tuple[int | None, str | None, str | None]":
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            **headers,
        })
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                status = getattr(resp, "status", None) or 200
                return status, resp.read().decode("utf-8", "ignore"), None
        except urllib.error.HTTPError as exc:
            body = None
            try:
                body = exc.read().decode("utf-8", "ignore")
            except (OSError, ValueError):
                pass
            return exc.code, body, f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return None, None, str(exc)
    return _t


def fixture_transport(fixture_dir: str) -> Transport:
    """Offline transport that serves committed/cached JSON instead of the
    real API. No network, no key. Fixture filenames are deterministic from
    the endpoint + its stable parameters (see each `_serve` branch); anything
    unmapped or missing returns a 404 so the caller's error/retry path is
    exercised exactly as it would be against the real API."""
    def _serve(fname: str):
        path = os.path.join(fixture_dir, fname)
        if not os.path.exists(path):
            return 404, None, f"no fixture: {fname}"
        return 200, Path(path).read_text(encoding="utf-8"), None

    def _t(url: str, headers: dict) -> "tuple[int | None, str | None, str | None]":
        parsed = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(parsed.query))
        q.pop("key", None)
        if parsed.path.endswith("/channels"):
            if "forHandle" in q:
                return _serve(f"channels_handle_{_slug(q['forHandle'])}.json")
            if "id" in q:
                return _serve(f"channels_id_{_slug(q['id'])}.json")
            return 404, None, "channels.list needs id or forHandle"
        if parsed.path.endswith("/playlistItems"):
            pid = _slug(q.get("playlistId", ""))
            token = _slug(q.get("pageToken") or "page1")
            return _serve(f"playlistItems_{pid}_{token}.json")
        if parsed.path.endswith("/videos"):
            ids = _slug(",".join(sorted((q.get("id", "")).split(","))))
            return _serve(f"videos_{ids}.json")
        if parsed.path.endswith("/search"):
            key = _slug(f"{q.get('channelId', '')}_{q.get('q', '')}_{q.get('pageToken') or 'page1'}")
            return _serve(f"search_{key}.json")
        return 404, None, "unmapped fixture url"
    return _t


class YouTubeClient:
    """Thin YouTube Data API v3 client. Caches raw responses for auditability
    and tracks quota spend per endpoint. `quota_sink`, if given, is called
    as `quota_sink(endpoint_name, units)` after every successful accounting
    step (used to persist into the automation DB's `quota_usage` table)."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        transport: Transport | None = None,
        cache_dir: str | None = None,
        quota_sink: "Callable[[str, int], None] | None" = None,
    ):
        self.api_key = api_key or os.environ.get("YOUTUBE_API_KEY")
        if transport is not None:
            self._transport = transport
        elif self.api_key:
            self._transport = urllib_transport(self.api_key)
        else:
            self._transport = None  # set on first real call -> clear error
        self.cache_dir = cache_dir
        self.quota_sink = quota_sink
        self.calls: list[dict] = []  # sanitized audit trail (no key, ever)
        self.quota_used = 0
        self.quota_by_endpoint: dict[str, int] = {}

    # -- low level ---------------------------------------------------------
    def _get(self, endpoint_name: str, params: dict) -> dict:
        path = _PATHS[endpoint_name]
        q = dict(params)
        if self.api_key:
            q["key"] = self.api_key
        url = API_ROOT + path + "?" + urllib.parse.urlencode(q)
        sanitized = _sanitize_url(url)
        if self._transport is None:
            raise YouTubeAuthError(
                "YOUTUBE_API_KEY is not set and no transport was injected; "
                "cannot make a live YouTube request. Set the secret or run "
                "against fixtures.")
        status, text, error = self._transport(url, {})
        cost = QUOTA_COSTS[endpoint_name]
        self.quota_used += cost
        self.quota_by_endpoint[endpoint_name] = self.quota_by_endpoint.get(endpoint_name, 0) + cost
        if self.quota_sink:
            self.quota_sink(endpoint_name, cost)
        record = {
            "endpoint": endpoint_name, "url": sanitized, "status": status,
            "error": error, "units": cost,
            "sha256": hashlib.sha256((text or "").encode()).hexdigest() if text else None,
        }
        self.calls.append(record)
        if self.cache_dir and text:
            self._cache(sanitized, text)
        if error or status is None or status >= 400:
            reason, message = _extract_error(text)
            if reason in _QUOTA_REASONS:
                raise YouTubeQuotaExceeded(endpoint_name, status, reason, message)
            raise YouTubeApiError(endpoint_name, status, reason, message or error)
        try:
            return json.loads(text)
        except (ValueError, TypeError) as exc:
            raise YouTubeApiError(endpoint_name, status, None, f"invalid JSON: {exc}") from exc

    def _cache(self, url: str, text: str) -> None:
        """Deterministic cache key from the sanitized (key-free) URL, so the
        same logical request always lands on the same file — repeat runs hit
        cache instead of re-spending quota. Never checked into git (the
        caller passes a path under data/raw/, which is gitignored)."""
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(url.encode()).hexdigest()[:20]
        Path(os.path.join(self.cache_dir, f"{key}.json")).write_text(text, encoding="utf-8")

    # -- endpoints -----------------------------------------------------------
    def get_channel_by_id(self, channel_id: str) -> dict | None:
        payload = self._get("channels.list", {
            "part": "snippet,contentDetails", "id": channel_id})
        items = payload.get("items") or []
        return items[0] if items else None

    def get_channel_by_handle(self, handle: str) -> dict | None:
        """Resolve a channel by its @handle (the only stable public identifier
        for a channel before its UC… id is confirmed)."""
        h = handle if handle.startswith("@") else f"@{handle}"
        payload = self._get("channels.list", {
            "part": "snippet,contentDetails", "forHandle": h})
        items = payload.get("items") or []
        return items[0] if items else None

    def list_playlist_items(
        self, playlist_id: str, *, max_pages: int = 20, page_size: int = 50,
    ) -> list[dict]:
        out: list[dict] = []
        token: str | None = None
        for _ in range(max_pages):
            params = {"part": "snippet,contentDetails",
                      "playlistId": playlist_id, "maxResults": page_size}
            if token:
                params["pageToken"] = token
            payload = self._get("playlistItems.list", params)
            out.extend(payload.get("items") or [])
            token = payload.get("nextPageToken")
            if not token:
                break
        return out

    def list_videos(self, video_ids: list[str]) -> list[dict]:
        """Batch-hydrate video status/liveStreamingDetails. Deduplicates
        input ids (a full-day multi-match broadcast can otherwise appear
        twice in an uploads scan) while preserving first-seen order."""
        seen: list[str] = []
        for v in video_ids:
            if v and v not in seen:
                seen.append(v)
        out: list[dict] = []
        for i in range(0, len(seen), 50):
            batch = seen[i:i + 50]
            payload = self._get("videos.list", {
                "part": "snippet,status,liveStreamingDetails,contentDetails",
                "id": ",".join(batch),
            })
            out.extend(payload.get("items") or [])
        return out

    def search_channel_videos(
        self, channel_id: str, *, query: str | None = None,
        published_after: str | None = None, max_pages: int = 5, page_size: int = 50,
    ) -> list[dict]:
        """LAST-RESORT fallback (C4) — costs ~100 units/call. Only call this
        when a channel's uploads playlist cannot satisfy discovery (e.g. an
        unofficial/unregistered source, used solely to surface review
        candidates, never to auto-link)."""
        out: list[dict] = []
        token: str | None = None
        for _ in range(max_pages):
            params = {"part": "snippet", "channelId": channel_id, "type": "video",
                      "maxResults": page_size, "order": "date"}
            if query:
                params["q"] = query
            if published_after:
                params["publishedAfter"] = published_after
            if token:
                params["pageToken"] = token
            payload = self._get("search.list", params)
            out.extend(payload.get("items") or [])
            token = payload.get("nextPageToken")
            if not token:
                break
        return out


def uploads_playlist_id(channel_item: dict) -> str | None:
    """Extract the uploads-playlist id from a channels.list item — the entry
    point for the cheap discovery path (C2/C3)."""
    related = (channel_item.get("contentDetails") or {}).get("relatedPlaylists") or {}
    return related.get("uploads")
