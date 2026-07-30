"""
keyless_metadata.py — public video metadata WITHOUT a YouTube API key.

Intake's authorization rule is "never approve a source on missing evidence".
That rule is right, but until now the only way to GET the evidence was the
YouTube Data API, so a machine with no `YOUTUBE_API_KEY` stopped at a human
gate on every link — including a link from the verified official channel,
whose channel id is public and freely readable. That gate was not a real
human decision; it was a missing lookup.

This module is that lookup, on the same permanently-free, no-key, no-quota
sources `match_finder.py` already trusts for discovery:

  1. `yt-dlp --dump-single-json` (rung "yt-dlp") — the complete answer:
     channel id, title, description, duration AND live status, straight out
     of YouTube's own player response. yt-dlp is already a hard requirement
     of this pipeline (it is what downloads the VOD), so on any machine that
     can do the work at all, this rung is available.
  2. The per-video Atom feed
     `https://www.youtube.com/feeds/videos.xml?video_id=<id>` (rung
     "youtube-video-feed") — stdlib urllib + ElementTree, no yt-dlp needed.
     It carries the channel id, title, description and publish time, but
     NOT duration or live status, so it is reported as `completeness:
     "partial"` and intake treats it accordingly.

What this module does NOT do:

  * It never guesses. A rung either produces a channel id from YouTube's own
    response or it fails with a named error, and every attempt (rung, ok,
    errorCode, error) is recorded so the operator can see exactly what was
    tried.
  * It never widens what counts as authority. It only supplies the same
    `channelId` the Data API would have; whether that channel is allowed to
    authorize a download is still `link_intake.authorize_source`'s decision
    against the verified registry.
  * It never raises. Every failure becomes the same
    `{"status": "unavailable"|"not_found", "errorCode", "error", "attempts"}`
    shape `link_intake.fetch_metadata` returns, so a caller's error path is
    identical whichever provider answered.

Output is normalized to EXACTLY the shape `link_intake.fetch_metadata`
returns for the Data API, plus provenance (`source`, `completeness`,
`attempts`, `rawLiveStatus`), so nothing downstream has to know which
provider answered.

Stdlib + subprocess only (no cv2, no numpy, no network at import time):
pasting a link must keep working on a machine with none of the CV stack.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Callable

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_DIR = os.path.dirname(_HERE)
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

# Rung names. Stable strings — they are recorded on the job payload, printed
# by the CLI and rendered by intake.html, so tests/prose never re-spell them.
RUNG_YTDLP = "yt-dlp"
RUNG_FEED = "youtube-video-feed"
DEFAULT_RUNGS = (RUNG_YTDLP, RUNG_FEED)

# Provider name used for metadata that came from the Data API, so the
# `source` field has one vocabulary across every provider.
SOURCE_DATA_API = "youtube-data-api"

VIDEO_FEED_URL = "https://www.youtube.com/feeds/videos.xml?video_id={vid}"
FEED_TIMEOUT = 15
YTDLP_TIMEOUT = 90
USER_AGENT = ("owcs-comp-tracker/1.0 (keyless video metadata; public feed, "
              "no credentials)")

_ATOM = "{http://www.w3.org/2005/Atom}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"
_MEDIA = "{http://search.yahoo.com/mrss/}"

# yt-dlp live_status -> the liveBroadcastStatus vocabulary the Data API path
# and broadcast_likeness were tuned on. Identical mapping to match_finder's,
# deliberately: two sources must not disagree about what "completed" means.
_LIVE_STATUS = {"was_live": "completed", "is_live": "live",
                "post_live": "completed", "is_upcoming": "upcoming"}

# yt-dlp `availability` -> the Data API's privacyStatus vocabulary.
_PRIVACY = {"public": "public", "unlisted": "unlisted", "private": "private",
            "premium_only": "private", "subscriber_only": "private",
            "needs_auth": "private"}

# Substrings in yt-dlp's stderr that mean "this video is not retrievable",
# not "the probe failed" — a different remedy, so a different code.
_NOT_FOUND_MARKERS = ("video unavailable", "private video", "removed by the uploader",
                      "this video has been removed", "does not exist",
                      "is not available", "members-only")


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _redact(text: str | None) -> str:
    """Strip credential-shaped things out of yt-dlp output before it is
    stored on a job or shown in a browser. Imported lazily so this module
    stays usable if ytdlp_opts is ever unavailable."""
    try:
        import ytdlp_opts as yo
    except ImportError:
        return (text or "")[:600]
    return yo.redact_text(text)[:600]


def _js_runtime_args() -> list[str]:
    """The same JS-runtime flags every other yt-dlp call in this repo uses;
    [] if the policy module cannot be imported."""
    try:
        import ytdlp_opts as yo
    except ImportError:
        return []
    try:
        return yo.js_runtime_args()
    except Exception:  # noqa: BLE001 — a probe must not die on doctor logic
        return []


def _base_args() -> list[str]:
    """Operator-configured baseline args (forced IPv4, allowlisted extras).
    Cookies/impersonation stay OUT, exactly as in the download ladder — a
    metadata read never opts into credentials on its own."""
    try:
        import ytdlp_opts as yo
        return yo.load_auth_config().base_args()
    except Exception:  # noqa: BLE001
        return []


# --------------------------------------------------------------- rung: yt-dlp
def _ytdlp_probe(video_id: str, *, runner=subprocess,
                 timeout: float = YTDLP_TIMEOUT) -> dict[str, Any]:
    """`yt-dlp --dump-single-json` for one video. Never downloads media.

    Returns the normalized metadata dict, or an `unavailable`/`not_found`
    dict with a named errorCode. Never raises.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = ["yt-dlp", *_base_args(), *_js_runtime_args(), "--dump-single-json",
           "--no-playlist", "--skip-download", "--no-warnings", url]
    try:
        res = runner.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"status": "unavailable", "errorCode": "ytdlp_missing",
                "error": ("yt-dlp is not on PATH — install it "
                          "(python -m pip install -U --pre yt-dlp) or set "
                          "YOUTUBE_API_KEY")}
    except subprocess.TimeoutExpired:
        return {"status": "unavailable", "errorCode": "ytdlp_timeout",
                "error": f"yt-dlp metadata probe timed out after {timeout:g}s"}
    except Exception as exc:  # noqa: BLE001 — a probe failure is data, not a crash
        return {"status": "unavailable", "errorCode": "ytdlp_error",
                "error": f"{type(exc).__name__}: {exc}"}
    if getattr(res, "returncode", 1) != 0:
        tail = _redact((getattr(res, "stderr", "") or
                        getattr(res, "stdout", "") or "").strip())
        low = tail.lower()
        if any(m in low for m in _NOT_FOUND_MARKERS):
            return {"status": "not_found", "errorCode": "video_not_found",
                    "error": f"yt-dlp says the video is not retrievable: {tail}"}
        return {"status": "unavailable", "errorCode": "ytdlp_failed",
                "error": f"yt-dlp exit {res.returncode}: {tail or '(no output)'}"}
    try:
        raw = json.loads(getattr(res, "stdout", "") or "{}")
    except ValueError as exc:
        return {"status": "unavailable", "errorCode": "ytdlp_bad_json",
                "error": f"unparseable yt-dlp JSON: {exc}"}
    if not isinstance(raw, dict) or not raw.get("id"):
        return {"status": "unavailable", "errorCode": "ytdlp_bad_json",
                "error": "yt-dlp returned no video object"}
    return _normalize_ytdlp(raw, video_id)


def _normalize_ytdlp(raw: dict, video_id: str) -> dict[str, Any]:
    dur = raw.get("duration")
    live_raw = raw.get("live_status")
    started = _epoch_iso(raw.get("release_timestamp") or raw.get("timestamp"))
    live_status = _LIVE_STATUS.get(live_raw)
    return {
        "status": "ok",
        "source": RUNG_YTDLP,
        "completeness": "full",
        "videoId": raw.get("id") or video_id,
        "channelId": raw.get("channel_id"),
        "channelTitle": raw.get("channel") or raw.get("uploader"),
        "title": raw.get("title") or raw.get("fulltitle"),
        "description": raw.get("description"),
        "publishedAt": started or _upload_date_iso(raw.get("upload_date")),
        "scheduledStartAt": (started if live_raw == "is_upcoming" else None),
        "actualStartAt": (started if live_raw in ("is_live", "was_live",
                                                  "post_live") else None),
        "actualEndAt": None,   # not exposed by yt-dlp; never invented
        "liveBroadcastStatus": live_status,
        "rawLiveStatus": live_raw,
        "durationSeconds": int(dur) if isinstance(dur, (int, float)) and dur else None,
        "privacyStatus": _PRIVACY.get(raw.get("availability")),
    }


def _epoch_iso(ts: Any) -> str | None:
    if not isinstance(ts, (int, float)) or ts <= 0:
        return None
    return dt.datetime.fromtimestamp(
        ts, dt.timezone.utc).replace(microsecond=0).isoformat()


def _upload_date_iso(raw: Any) -> str | None:
    """yt-dlp's 'YYYYMMDD' upload_date -> an ISO timestamp (UTC midnight).
    A date is weaker evidence than a timestamp, which is why it is only the
    fallback for publishedAt."""
    s = str(raw or "").strip()
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return dt.datetime(int(s[:4]), int(s[4:6]), int(s[6:8]),
                           tzinfo=dt.timezone.utc).isoformat()
    except ValueError:
        return None


# ----------------------------------------------------------- rung: video feed
def _http_fetch(url: str, timeout: float = FEED_TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _feed_probe(video_id: str, *,
                fetch: Callable[[str], bytes] = _http_fetch) -> dict[str, Any]:
    """One video's public Atom feed. No key, no yt-dlp, stdlib only.

    Carries the channel id (the authorization evidence) but NOT duration or
    live status, so the result is `completeness: "partial"` and intake
    refuses to conclude a broadcast has ended from it.
    """
    url = VIDEO_FEED_URL.format(vid=video_id)
    try:
        body = fetch(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"status": "not_found", "errorCode": "video_not_found",
                    "error": (f"the public video feed has no entry for "
                              f"{video_id!r} (deleted, private, or a bad id)")}
        return {"status": "unavailable", "errorCode": "feed_http_error",
                "error": f"video feed HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "errorCode": "feed_error",
                "error": f"{type(exc).__name__}: {exc}"}
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        return {"status": "unavailable", "errorCode": "feed_parse_error",
                "error": f"unparseable video feed: {exc}"}
    entry = root.find(f"{_ATOM}entry")
    if entry is None:
        return {"status": "not_found", "errorCode": "video_not_found",
                "error": f"the public video feed has no entry for {video_id!r}"}
    channel_id = _text(entry.find(f"{_YT}channelId")) or _text(
        root.find(f"{_YT}channelId"))
    desc = None
    group = entry.find(f"{_MEDIA}group")
    if group is not None:
        desc = _text(group.find(f"{_MEDIA}description"))
    if not channel_id:
        return {"status": "unavailable", "errorCode": "feed_no_channel",
                "error": ("the video feed carried no channel id — there is no "
                          "authorization evidence in it")}
    return {
        "status": "ok",
        "source": RUNG_FEED,
        "completeness": "partial",
        "videoId": _text(entry.find(f"{_YT}videoId")) or video_id,
        "channelId": channel_id,
        "channelTitle": _text(entry.find(f"{_ATOM}author/{_ATOM}name")),
        "title": _text(entry.find(f"{_ATOM}title")),
        "description": desc,
        "publishedAt": _text(entry.find(f"{_ATOM}published")),
        "scheduledStartAt": None,
        "actualStartAt": None,
        "actualEndAt": None,
        # The feed says nothing about either. NOT knowing is recorded as
        # None, never as "completed".
        "liveBroadcastStatus": None,
        "rawLiveStatus": None,
        "durationSeconds": None,
        "privacyStatus": None,
    }


def _text(el) -> str | None:
    if el is None or el.text is None:
        return None
    t = el.text.strip()
    return t or None


# ------------------------------------------------------------------ the ladder
def fetch_metadata(video_id: str, *, rungs: "tuple[str, ...]" = DEFAULT_RUNGS,
                   runner=subprocess,
                   fetch: Callable[[str], bytes] = _http_fetch,
                   ytdlp_timeout: float = YTDLP_TIMEOUT) -> dict[str, Any]:
    """Walk the keyless rungs in order and return the FIRST complete answer.

    Every attempt is recorded in `attempts`, successful or not, so an
    operator can see precisely which sources were tried and why each one
    failed. A `not_found` verdict stops the ladder: another source is not
    going to make a deleted video exist, and re-probing it is pure latency.

    Never raises.
    """
    attempts: list[dict[str, Any]] = []
    terminal: dict[str, Any] | None = None
    for rung in rungs:
        if rung == RUNG_YTDLP:
            res = _ytdlp_probe(video_id, runner=runner, timeout=ytdlp_timeout)
        elif rung == RUNG_FEED:
            res = _feed_probe(video_id, fetch=fetch)
        else:
            res = {"status": "unavailable", "errorCode": "unknown_rung",
                   "error": f"no keyless rung named {rung!r}"}
        attempts.append({"rung": rung, "ok": res.get("status") == "ok",
                         "status": res.get("status"),
                         "errorCode": res.get("errorCode"),
                         "error": res.get("error")})
        if res.get("status") == "ok":
            return dict(res, attempts=attempts, checkedAt=_utcnow_iso())
        if res.get("status") == "not_found":
            terminal = res
            break
    if terminal is not None:
        return dict(terminal, source="keyless", attempts=attempts,
                    checkedAt=_utcnow_iso())
    tried = ", ".join(f"{a['rung']} [{a['errorCode']}]" for a in attempts) or "none"
    return {"status": "unavailable", "source": "keyless",
            "errorCode": "keyless_unavailable",
            "error": (f"no keyless source could read this video's public "
                      f"metadata (tried: {tried})"),
            "attempts": attempts, "checkedAt": _utcnow_iso()}


def resolver(*, rungs: "tuple[str, ...]" = DEFAULT_RUNGS, runner=subprocess,
             fetch: Callable[[str], bytes] = _http_fetch,
             ytdlp_timeout: float = YTDLP_TIMEOUT
             ) -> Callable[[str], dict[str, Any]]:
    """A `(video_id) -> metadata` callable for injection into
    `link_intake.ingest_link(..., keyless=...)`. The CLI builds one; tests
    inject a fake runner/fetch through the same door."""
    def _resolve(video_id: str) -> dict[str, Any]:
        return fetch_metadata(video_id, rungs=rungs, runner=runner,
                              fetch=fetch, ytdlp_timeout=ytdlp_timeout)
    return _resolve


def format_attempts(attempts: list[dict] | None) -> str:
    """One-line operator rendering of what the ladder tried."""
    if not attempts:
        return "(no keyless attempt)"
    return "; ".join(
        f"{a.get('rung')}: {'ok' if a.get('ok') else a.get('errorCode') or 'failed'}"
        for a in attempts)
