"""
lp_dates.py — the day each PLAYED spreadsheet series was played, from Liquipedia.

The spreadsheet records no dates: a series is placed only by its tab and its
card label (Week N | Day N | Game N), which config/external/sheet_schedule.json
turns into an estimate. That estimate is wrong when the stage's days are not
published (Pacific Stage 3: no date at all, so the series fall out of every
"last N days" window) and when the editors copy a card label (China Stage 3:
the Nov 1 cards say "Week 4 | Day 2" and were dated Oct 25).

Liquipedia (data/external/liquipedia/matches.json, fetched on a GitHub
runner by pipeline/liquipedia_schedule.py) lists every series with its start
time and score. Its parser cannot read a "finished" flag, so a series counts
as played here when its score is decisive for its format (3 in a Bo5), or,
when the page gives no format, has a winner with at least two maps.

A sheet series takes a Liquipedia date only when all of these hold:
  * same event (region/stage),
  * the same two teams (Liquipedia's names resolved through the alias book,
    else matched by name among that event's own teams),
  * the same series score, side for side.
Two series of the same pair and score in one stage pair up in order: the
sheet's k-th with Liquipedia's k-th by start time. A series the sheet already
has a result for while Liquipedia's last fetch still lists it as scheduled
(the editors were faster) takes the day of that pair's first open slot.

Used by tiers.py (Home / Tiers time windows) and schedule_build.py (Matches)
so the two pages put a series on the same day. Pure: the result depends on
the series, the Liquipedia file and the alias book only.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from typing import Callable, Iterable

LIQUIPEDIA_REL = ("data", "external", "liquipedia", "matches.json")


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def read_liquipedia(root: str) -> dict | None:
    try:
        with open(os.path.join(root, *LIQUIPEDIA_REL), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def local_date(iso: str) -> str:
    """The calendar day in the match's own time zone (the day it was played
    where it was played), as the Matches page shows it."""
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).date().isoformat()


def lp_played(x: dict) -> bool:
    """A Liquipedia series with a decisive score for its format."""
    a, b = x.get("scoreA"), x.get("scoreB")
    if not isinstance(a, int) or not isinstance(b, int) or a == b:
        return False
    if x.get("finished"):
        return True
    bo = x.get("bestOf")
    if isinstance(bo, int) and bo > 0:
        return max(a, b) >= bo // 2 + 1
    # Most pages give no format. OWCS plays no Bo1, and a series only takes
    # a date from a Liquipedia score equal to its own final score.
    return max(a, b) >= 2


def lp_day(x: dict) -> str | None:
    if x.get("startsAt"):
        return local_date(x["startsAt"])
    return x.get("date") or None


class _EventTeams:
    """Resolve a Liquipedia team name to one of an event's own team ids."""

    def __init__(self, ids: Iterable[str], names: dict[str, set[str]],
                 resolve: Callable[[str], str | None] | None):
        self.ids = set(ids)
        self.names = names            # id -> normalised names the sheet uses for it
        self.resolve_alias = resolve

    def __call__(self, name: str | None) -> str | None:
        k = _norm(name)
        if not k:
            return None
        if self.resolve_alias is not None:
            tid = self.resolve_alias(name)
            if tid in self.ids:
                return tid
        exact = [t for t in self.ids if k in self.names.get(t, ())]
        if len(exact) == 1:
            return exact[0]
        if len(k) >= 4:
            # "Solus Victorem" / "Solus Victorem Crush", "Zanside Gaming" /
            # "RØDE ZANSIDE GAMING": one of the event's teams, and only one,
            # whose name contains Liquipedia's or is contained in it.
            near = [t for t in self.ids if any(k in n or (len(n) >= 4 and n in k)
                                                for n in self.names.get(t, ()))]
            if len(near) == 1:
                return near[0]
        return None


def series_dates(series: list[dict], lp: dict | None,
                 resolve: Callable[[str], str | None] | None = None,
                 imported_at: str | None = None) -> tuple[dict, set]:
    """({series id: {"date", "startsAt", "url", "streams"}}, {Liquipedia match indexes used}).

    `series`: one dict per played sheet series with keys id, event
    ('korea/stage-3'), teamA, teamB, scoreA, scoreB, order (sortable, the
    sheet's own order) and optionally names ({team id: [names it prints]}).
    `resolve`: name -> team id through the alias book, or None.
    `imported_at`: when the sheet was imported (ISO). A result it records was
    played by then, so an open Liquipedia slot later than that (local day,
    one day of time-zone slack) never dates it. None: no open slot is used."""
    out: dict[str, dict] = {}
    used: set[int] = set()
    if not lp or not series:
        return out, used

    by_event: dict[str, list[dict]] = {}
    for s in series:
        if s.get("event") and s.get("teamA") and s.get("teamB"):
            by_event.setdefault(s["event"], []).append(s)

    lp_by_event: dict[str, list[tuple[int, dict]]] = {}
    for i, x in enumerate(lp.get("matches") or []):
        if x.get("event") and lp_day(x):
            lp_by_event.setdefault(x["event"], []).append((i, x))

    for ev, rows in by_event.items():
        cands = sorted(lp_by_event.get(ev) or [],
                       key=lambda ix: (ix[1].get("startsAt") or ix[1].get("date") or "",
                                       ix[1].get("order") or 0, ix[0]))
        if not cands:
            continue
        names: dict[str, set[str]] = {}
        for s in rows:
            for side in ("A", "B"):
                tid = s["team" + side]
                bucket = names.setdefault(tid, set())
                bucket.add(_norm(tid[2:] if tid.startswith("x-") else tid))
                for n in (s.get("names") or {}).get(tid) or ():
                    if _norm(n):
                        bucket.add(_norm(n))
        teams = _EventTeams(names.keys(), names, resolve)

        # Liquipedia's played series, keyed like the sheet's: the pair and
        # the score each team got.
        lp_keyed: dict[tuple, list[tuple[int, dict]]] = {}
        lp_open: dict[frozenset, list[tuple[int, dict]]] = {}   # pair -> not played yet on Liquipedia
        for i, x in cands:
            ta, tb = teams(x.get("teamA")), teams(x.get("teamB"))
            if not ta or not tb or ta == tb:
                continue
            if lp_played(x):
                key = frozenset({(ta, x["scoreA"]), (tb, x["scoreB"])})
                lp_keyed.setdefault(key, []).append((i, x))
            else:
                lp_open.setdefault(frozenset({ta, tb}), []).append((i, x))

        sheet_keyed: dict[tuple, list[dict]] = {}
        for s in sorted(rows, key=lambda s: (s.get("order") or (), s["id"])):
            if not isinstance(s.get("scoreA"), int) or not isinstance(s.get("scoreB"), int):
                continue
            key = frozenset({(s["teamA"], s["scoreA"]), (s["teamB"], s["scoreB"])})
            sheet_keyed.setdefault(key, []).append(s)

        def take(s: dict, i: int, x: dict) -> None:
            used.add(i)
            out[s["id"]] = {"date": lp_day(x), "startsAt": x.get("startsAt"),
                            "url": x.get("url"), "streams": x.get("streams") or None}

        for key, ss in sheet_keyed.items():
            for s, (i, x) in zip(ss, lp_keyed.get(key) or []):
                take(s, i, x)

        # Played on the sheet, still scheduled in Liquipedia's last fetch (the
        # sheet's editors were faster): the pair's first open slot on
        # Liquipedia is the day it was played, if that day is not after the
        # import and sits between the days of the sheet's neighbours already
        # dated above (at least one earlier). A pair that meets again in the
        # playoffs never lends a regular-season series the playoff's day.
        if not imported_at:
            continue
        latest = (dt.date.fromisoformat(imported_at[:10]) + dt.timedelta(days=1)).isoformat()
        anchored = sorted(((s.get("order") or (), out[s["id"]]["date"]) for s in rows if s["id"] in out),
                          key=lambda od: od[0])
        for s in sorted((s for ss in sheet_keyed.values() for s in ss if s["id"] not in out),
                        key=lambda s: (s.get("order") or (), s["id"])):
            slot = next(((i, x) for i, x in lp_open.get(frozenset({s["teamA"], s["teamB"]}), [])
                         if i not in used), None)
            if not slot:
                continue
            day, order = lp_day(slot[1]), s.get("order") or ()
            before = [d for o, d in anchored if o < order]
            after = [d for o, d in anchored if o > order]
            if before and max(before) <= day <= latest and (not after or day <= min(after)):
                take(s, *slot)
    return out, used

    by_event: dict[str, list[dict]] = {}
    for s in series:
        if s.get("event") and s.get("teamA") and s.get("teamB"):
            by_event.setdefault(s["event"], []).append(s)

    lp_by_event: dict[str, list[tuple[int, dict]]] = {}
    for i, x in enumerate(lp.get("matches") or []):
        if x.get("event") and lp_day(x):
            lp_by_event.setdefault(x["event"], []).append((i, x))

    for ev, rows in by_event.items():
        cands = sorted(lp_by_event.get(ev) or [],
                       key=lambda ix: (ix[1].get("startsAt") or ix[1].get("date") or "",
                                       ix[1].get("order") or 0, ix[0]))
        if not cands:
            continue
        names: dict[str, set[str]] = {}
        for s in rows:
            for side in ("A", "B"):
                tid = s["team" + side]
                bucket = names.setdefault(tid, set())
                bucket.add(_norm(tid[2:] if tid.startswith("x-") else tid))
                for n in (s.get("names") or {}).get(tid) or ():
                    if _norm(n):
                        bucket.add(_norm(n))
        teams = _EventTeams(names.keys(), names, resolve)

        # Liquipedia's played series, keyed like the sheet's: the pair and
        # the score each team got.
        lp_keyed: dict[tuple, list[tuple[int, dict]]] = {}
        lp_open: dict[frozenset, list[tuple[int, dict]]] = {}   # pair -> not played yet on Liquipedia
        for i, x in cands:
            ta, tb = teams(x.get("teamA")), teams(x.get("teamB"))
            if not ta or not tb or ta == tb:
                continue
            if lp_played(x):
                key = frozenset({(ta, x["scoreA"]), (tb, x["scoreB"])})
                lp_keyed.setdefault(key, []).append((i, x))
            else:
                lp_open.setdefault(frozenset({ta, tb}), []).append((i, x))

        sheet_keyed: dict[tuple, list[dict]] = {}
        for s in sorted(rows, key=lambda s: (s.get("order") or (), s["id"])):
            if not isinstance(s.get("scoreA"), int) or not isinstance(s.get("scoreB"), int):
                continue
            key = frozenset({(s["teamA"], s["scoreA"]), (s["teamB"], s["scoreB"])})
            sheet_keyed.setdefault(key, []).append(s)

        def take(s: dict, i: int, x: dict) -> None:
            used.add(i)
            out[s["id"]] = {"date": lp_day(x), "startsAt": x.get("startsAt"),
                            "url": x.get("url"), "streams": x.get("streams") or None}

        for key, ss in sheet_keyed.items():
            for s, (i, x) in zip(ss, lp_keyed.get(key) or []):
                take(s, i, x)

        # Played on the sheet, still scheduled in Liquipedia's last fetch (the
        # sheet's editors were faster): the pair's first open slot on
        # Liquipedia, if it is not after the import, is the day it was played.
        if not imported_at:
            continue
        latest = (dt.date.fromisoformat(imported_at[:10]) + dt.timedelta(days=1)).isoformat()
        for s in sorted((s for ss in sheet_keyed.values() for s in ss if s["id"] not in out),
                        key=lambda s: (s.get("order") or (), s["id"])):
            slot = next(((i, x) for i, x in lp_open.get(frozenset({s["teamA"], s["teamB"]}), [])
                         if i not in used), None)
            if slot and lp_day(slot[1]) <= latest:
                take(s, *slot)
    return out, used
