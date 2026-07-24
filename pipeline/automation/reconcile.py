"""
reconcile.py — source reconciliation warnings (Roadmap Phase B4).

Cross-checks FACEIT match facts against the official OWCS calendar and against
each match's own completeness. The cardinal rule: *do not silently overwrite
conflicting data*. This module NEVER mutates anything — it only emits warnings
for a human (or the dashboard) to act on.

Warning codes:
  FACEIT_MATCH_NO_CALENDAR_EVENT  FACEIT has a match no official event covers
  CALENDAR_EVENT_NO_FACEIT_COMP   official event with no known FACEIT competition
  START_TIME_MISMATCH             FACEIT vs calendar start differ significantly
  TEAM_UNRESOLVED                 a match team name could not be resolved
  COMPETITION_NO_BROADCAST        competition has no known broadcast channel
  COMPLETED_NO_RESULT             a finished match carries no result
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from .owcs_calendar import CalendarEvent, event_for_competition

# A start-time delta beyond this many minutes is worth flagging (B4).
START_TIME_TOLERANCE_MINUTES = 90


@dataclass
class Warning_:
    code: str
    message: str
    refs: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "refs": self.refs}


def _parse_iso(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def reconcile(
    normalized_matches: list[dict],
    events: list[CalendarEvent],
    *,
    channels_by_id: dict[str, dict] | None = None,
    competitions: list[dict] | None = None,
) -> list[dict]:
    """Return a list of warning dicts. Pure; writes nothing.

    normalized_matches: output of faceit_api.normalize_match
    events:             official calendar events
    channels_by_id:     id -> channel dict (for the no-broadcast check)
    competitions:       enabled competition rows (for the calendar-linkage check)
    """
    warnings: list[Warning_] = []
    channels_by_id = channels_by_id or {}
    competitions = competitions or []

    # 1. Every enabled competition should map to an official calendar event
    #    that itself has a broadcast channel.
    for comp in competitions:
        ev = event_for_competition(events, comp.get("id"))
        if ev is None:
            warnings.append(Warning_(
                "CALENDAR_EVENT_NO_FACEIT_COMP",
                f"competition {comp.get('id')} has no official calendar event",
                {"competitionId": comp.get("id")}))
            continue
        if not ev.broadcast_channels:
            warnings.append(Warning_(
                "COMPETITION_NO_BROADCAST",
                f"event {ev.id} has no official broadcast channel listed",
                {"eventId": ev.id, "competitionId": comp.get("id")}))

    # 2. Per-match checks.
    for m in normalized_matches:
        mid = m.get("faceitMatchId")
        comp_id = m.get("competitionId")

        # Team names resolvable?
        for team in m.get("teams", []):
            if not team.get("name"):
                warnings.append(Warning_(
                    "TEAM_UNRESOLVED",
                    f"match {mid} side {team.get('side')} has no team name",
                    {"matchId": mid, "side": team.get("side")}))

        # Completed but no result?
        if m.get("contentStatus") == "final" and m.get("lifecycleStatus") == "finished":
            sa, sb = m["score"].get("a"), m["score"].get("b")
            if sa is None and sb is None:
                warnings.append(Warning_(
                    "COMPLETED_NO_RESULT",
                    f"finished match {mid} has no recorded score",
                    {"matchId": mid}))

        # Calendar coverage + start-time reconciliation.
        when = m.get("scheduledAt") or m.get("startedAt") or m.get("finishedAt")
        ev = None
        # Prefer the event linked to the match's competition; fall back to any
        # event that covers the time window.
        if comp_id:
            ev = next((e for e in events if e.faceit_competition_id and
                       (competitions and any(c.get("id") == e.faceit_competition_id
                                             and c.get("championshipId") == comp_id
                                             for c in competitions))), None)
        covering = [e for e in events if e.covers(when)]
        if ev is None and covering:
            ev = covering[0]
        if ev is None:
            warnings.append(Warning_(
                "FACEIT_MATCH_NO_CALENDAR_EVENT",
                f"match {mid} ({when}) is covered by no official calendar event",
                {"matchId": mid, "when": when}))
        else:
            # If the event has a window and the match falls outside it, flag as
            # a start-time / date mismatch (don't overwrite either source).
            if when and not ev.covers(when):
                warnings.append(Warning_(
                    "START_TIME_MISMATCH",
                    f"match {mid} at {when} falls outside event {ev.id} "
                    f"window {ev.start_date}..{ev.end_date}",
                    {"matchId": mid, "eventId": ev.id, "when": when}))

    return [w.as_dict() for w in warnings]


def start_time_conflict(faceit_iso: str | None, calendar_iso: str | None) -> bool:
    """True if two start times differ by more than the tolerance. Used when a
    calendar source provides a specific per-match time to compare against."""
    a, b = _parse_iso(faceit_iso), _parse_iso(calendar_iso)
    if a is None or b is None:
        return False
    return abs((a - b).total_seconds()) > START_TIME_TOLERANCE_MINUTES * 60
