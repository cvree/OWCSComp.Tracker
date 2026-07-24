"""
owcs_calendar.py — official OWCS calendar adapter (Roadmap Phase B3).

The official Overwatch Esports schedule is the EVENT-LEVEL source of truth:
competition dates, regional stage windows, major-event dates and official
broadcast destinations. This adapter loads that schedule as normalized events
so the reconciliation layer (B4) can cross-check FACEIT match facts against it.

It never supplies compositions and never overwrites FACEIT. By default it reads
the committed seed at config/owcs_calendar.json; a live fetcher can be injected
(the transport contract mirrors faceit_api) for a future scraping/API source,
but the committed file keeps CI and offline reconciliation network-free.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
CALENDAR_PATH = os.path.join(REPO_ROOT, "config", "owcs_calendar.json")

# Optional live source: () -> list[raw event dict]. Injected for tests/future.
Fetcher = Callable[[], "list[dict]"]


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
    return CalendarEvent(
        id=str(raw.get("id") or "").strip(),
        name=raw.get("name"),
        region=(raw.get("region") or None),
        stage=raw.get("stage"),
        start_date=raw.get("startDate"),
        end_date=raw.get("endDate"),
        faceit_competition_id=raw.get("faceitCompetitionId"),
        broadcast_channels=list(raw.get("broadcastChannels") or []),
        verified=bool(raw.get("verified", False)),
        raw=raw,
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
