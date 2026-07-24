"""
owcs_calendar.py — official OWCS calendar adapter (Roadmap Phase B3/C7).

The official Overwatch Esports schedule is the EVENT-LEVEL source of truth:
competition dates, regional stage windows, major-event dates and official
broadcast destinations. This adapter loads that schedule as normalized events
so the reconciliation layer (B4) can cross-check FACEIT match facts against it.

It never supplies compositions, never fabricates individual match pairings or
exact match times from an event-level source, and never overwrites FACEIT. By
default it reads the committed seed at config/owcs_calendar.json; `http_
fetcher` (C7) can fetch the live page and is injected exactly like the seed
fetcher, but the committed file remains what CI and offline reconciliation
run against — network-free and reproducible.

The official schedule (esports.overwatch.com/en-us/schedule) is a Next.js
app: data is hydrated from a `__NEXT_DATA__` JSON blob embedded in the initial
HTML, not from static markup. `http_fetcher` parses that blob defensively
(walks for event-shaped objects rather than one hard-coded JSON path) so a
page reshape degrades to "fetched nothing new" rather than a crash or, worse,
a wrong parse silently accepted as fact — the committed seed stays
authoritative until a human reviews and confirms new extraction output.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
CALENDAR_PATH = os.path.join(REPO_ROOT, "config", "owcs_calendar.json")
OFFICIAL_SCHEDULE_URL = "https://esports.overwatch.com/en-us/schedule"

# Optional live source: () -> list[raw event dict]. Injected for tests/future.
Fetcher = Callable[[], "list[dict]"]
# Transport contract for the live HTTP fetch (mirrors faceit_api/youtube_api):
# (url) -> (status:int|None, html:str|None, error:str|None).
HttpTransport = Callable[[str], "tuple[int | None, str | None, str | None]"]


@dataclass
class CalendarEvent:
    id: str
    name: str | None
    region: str | None
    stage: str | None
    start_date: str | None
    end_date: str | None
    faceit_competition_id: str | None
    broadcast_channels: list[str]
    verified: bool
    raw: dict
    # Phase C7 additions — event-level facts only, never a fabricated match.
    season: str | None = None
    scheduled_time: str | None = None       # ISO time-of-day when the official
                                             # source actually publishes one
    tournament_format: str | None = None    # e.g. 'single elimination', 'bo5'
    source_url: str | None = None
    retrieved_at: str | None = None
    source_hash: str | None = None
    verification_status: str = "unverified"  # unverified/verified/stale/failed

    def covers(self, iso_datetime: str | None) -> bool:
        """True if a scheduled/finished time falls within [start, end] (dates
        compared lexicographically as YYYY-MM-DD; endpoints inclusive)."""
        if not iso_datetime:
            return False
        day = iso_datetime[:10]
        if self.start_date and day < self.start_date:
            return False
        if self.end_date and day > self.end_date:
            return False
        return True


def _normalize_event(raw: dict) -> CalendarEvent:
    verified = bool(raw.get("verified", False))
    return CalendarEvent(
        id=str(raw.get("id") or "").strip(),
        name=raw.get("name"),
        region=(raw.get("region") or None),
        stage=raw.get("stage"),
        start_date=raw.get("startDate"),
        end_date=raw.get("endDate"),
        faceit_competition_id=raw.get("faceitCompetitionId"),
        broadcast_channels=list(raw.get("broadcastChannels") or []),
        verified=verified,
        raw=raw,
        season=raw.get("season"),
        scheduled_time=raw.get("scheduledTime"),
        tournament_format=raw.get("tournamentFormat"),
        source_url=raw.get("sourceUrl"),
        retrieved_at=raw.get("retrievedAt"),
        source_hash=raw.get("sourceHash"),
        verification_status=raw.get("verificationStatus") or ("verified" if verified else "unverified"),
    )


def load_events(path: str = CALENDAR_PATH, *, fetcher: Fetcher | None = None) -> list[CalendarEvent]:
    """Load normalized official-calendar events.

    A live `fetcher` (if given) takes precedence; otherwise the committed seed
    file is read. A missing file yields an empty list (reconciliation then
    simply reports that no official event backs a FACEIT competition)."""
    if fetcher is not None:
        raw_events = fetcher() or []
    else:
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            raw_events = json.load(f).get("events", []) or []
    events = [_normalize_event(e) for e in raw_events]
    return [e for e in events if e.id]


def event_for_competition(events: list[CalendarEvent], competition_id: str) -> CalendarEvent | None:
    for e in events:
        if e.faceit_competition_id == competition_id:
            return e
    return None


# ============================================================ Phase C7 =====
# Live official-schedule fetcher: parses the Next.js `__NEXT_DATA__` blob
# embedded in the page's initial HTML. Resilient by design — any failure
# (network, missing blob, reshaped JSON) returns an empty list rather than
# raising, so a live-fetch attempt can never crash discovery; the committed
# seed file remains the fallback of record until a human reviews new output.
# =============================================================================
_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)

_EVENT_NAME_KEYS = ("name", "title", "eventname")
_EVENT_DATE_KEYS = ("startdate", "start_date", "date", "scheduledat", "scheduled_at")


def _http_get(url: str) -> "tuple[int | None, str | None, str | None]":
    req = urllib.request.Request(url, headers={
        "User-Agent": "OWCS-Comp-Tracker/0.4 (+fan project; official OWCS calendar)",
        "Accept": "text/html",
    })
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            status = getattr(resp, "status", None) or 200
            return status, resp.read().decode("utf-8", "ignore"), None
    except urllib.error.HTTPError as exc:
        return exc.code, None, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, None, str(exc)


def parse_next_data(html: str) -> dict | None:
    """Extract the `__NEXT_DATA__` JSON blob from a page's raw HTML. None if
    the tag is absent or the content isn't valid JSON (a page reshape, not
    an error to raise on)."""
    m = _NEXT_DATA_RE.search(html or "")
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except (ValueError, TypeError):
        return None


def _get_ci(node: dict, *keys: str) -> Any:
    """Case-insensitive dict lookup — the page's prop casing is not a stable
    public contract, so match defensively rather than one exact spelling."""
    lower = {k.lower(): v for k, v in node.items()}
    for k in keys:
        if k in lower:
            return lower[k]
    return None


def _looks_like_event(node: dict) -> bool:
    keys = {k.lower() for k in node.keys()}
    return any(k in keys for k in _EVENT_NAME_KEYS) and any(k in keys for k in _EVENT_DATE_KEYS)


def _coerce_event(node: dict) -> dict:
    return {
        "id": _get_ci(node, "id", "eventid", "slug"),
        "name": _get_ci(node, *_EVENT_NAME_KEYS),
        "region": _get_ci(node, "region"),
        "stage": _get_ci(node, "stage"),
        "season": _get_ci(node, "season"),
        "startDate": _get_ci(node, *_EVENT_DATE_KEYS),
        "endDate": _get_ci(node, "enddate", "end_date"),
        "scheduledTime": _get_ci(node, "scheduledat", "scheduled_at", "starttime", "start_time"),
        "tournamentFormat": _get_ci(node, "format", "tournamentformat"),
        "faceitCompetitionId": _get_ci(node, "faceitcompetitionid"),
        "broadcastChannels": _get_ci(node, "broadcastchannels") or [],
        "raw": node,
    }


def extract_events_from_next_data(data: dict, *, max_depth: int = 12) -> list[dict]:
    """Resilient walk of the hydration blob for event-shaped objects (name +
    date fields), rather than one hard-coded JSON path — the page's internal
    prop structure is not a stable public contract and WILL drift; this
    degrades to 'found nothing' instead of raising when it does."""
    events: list[dict] = []
    seen_ids: set[int] = set()

    def _walk(node: Any, depth: int) -> None:
        if depth > max_depth:
            return
        if isinstance(node, dict):
            if id(node) in seen_ids:
                return
            seen_ids.add(id(node))
            if _looks_like_event(node):
                events.append(_coerce_event(node))
            for v in node.values():
                _walk(v, depth + 1)
        elif isinstance(node, list):
            for item in node:
                _walk(item, depth + 1)

    _walk(data, 0)
    return events


def http_fetcher(
    url: str = OFFICIAL_SCHEDULE_URL, *, transport: HttpTransport | None = None,
) -> "list[dict]":
    """Live official-schedule fetch (C7). NEVER fabricates match pairings or
    times — only whatever event-level fields the page itself exposes.
    `transport` is injectable for offline tests (mirrors faceit_api /
    youtube_api); the real transport is a plain unauthenticated GET (this is
    a public page, no credentials involved). Every failure mode (network
    error, missing/changed hydration blob, empty result) returns `[]` rather
    than raising — discovery falls back to the committed seed file."""
    now_iso = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    get = transport or _http_get
    status, html, error = get(url)
    if error or not html:
        return []
    data = parse_next_data(html)
    if data is None:
        return []
    events = extract_events_from_next_data(data)
    source_hash = hashlib.sha256(html.encode("utf-8", "ignore")).hexdigest()
    for e in events:
        e["sourceUrl"] = url
        e["retrievedAt"] = now_iso
        e["sourceHash"] = source_hash
        # A successful parse is not the same as human verification — Phase
        # C7 leaves that judgment to the reconciliation step / an operator.
        e["verificationStatus"] = "unverified"
    return events
