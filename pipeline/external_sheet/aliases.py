"""
aliases.py — the one canonical alias layer for external sources.

Every spelling question the importer asks ("is 'Lucio' a hero, which one?",
"is 'TM' Twisted Minds?", "is 'KOTH' a mode?") is answered here and nowhere
else, from three inputs, in this order:

  1. reviewed approvals  corrections/external_reconciliation.json
  2. curated aliases     config/external/aliases.json
  3. the published vocabulary  assets/data/public_data.v1.js (heroes,
     mapsCatalog, teams + their aliases/previous names, players)

Every answer carries the METHOD that produced it, so a normalised value can
always be traced back to why it was chosen. A value nothing recognises is
returned as unresolved — never guessed by fuzzy match. Teams and players get
a deterministic slug so they can still be grouped (case-insensitively) and
listed as unmapped; heroes, maps and modes do not, because an unrecognised
hero must never be counted as a hero.
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def norm(value: Any) -> str:
    """Lower-case, accent-free, punctuation-free, single-spaced text."""
    if value is None:
        return ""
    s = unicodedata.normalize("NFKD", str(value))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = s.replace("&", " and ").replace("'", "").replace("#", " no ")
    return _NON_ALNUM.sub(" ", s).strip()


def slugify(value: Any) -> str:
    return norm(value).replace(" ", "-")


@dataclass(frozen=True)
class Resolution:
    id: str | None          # canonical id, or None when unresolved
    method: str             # approved | alias | catalog | id | history | slug | unresolved | empty
    raw: str                # the original text, verbatim

    @property
    def resolved(self) -> bool:
        return self.id is not None and self.method not in ("slug", "unresolved", "empty")


class AliasBook:
    """Canonical lookups. Build once per import with `AliasBook.build`."""

    def __init__(self) -> None:
        self.version: Any = None
        self.ignore: set[str] = set()
        self.heroes: dict[str, str] = {}
        self.hero_roles: dict[str, str] = {}
        self.hero_names: dict[str, str] = {}
        self.maps: dict[str, str] = {}
        self.map_modes: dict[str, str] = {}
        self.map_names: dict[str, str] = {}
        self.modes: dict[str, str] = {}
        self.regions: dict[str, str] = {}
        self.stages: dict[str, str] = {}
        self.phase_stages: set[str] = set()
        self.pictures: dict[str, str] = {}
        self.tournaments: dict[str, str] = {}
        self.teams: dict[str, str] = {}
        self.team_names: dict[str, str] = {}
        self.team_history: list[dict] = []
        self.players: dict[str, str] = {}
        self.player_names: dict[str, str] = {}
        self.approved: dict[tuple[str, str], str] = {}
        self.date_formats: list[str] = []
        self.month_first = True
        self._methods: dict[tuple[str, str], str] = {}

    # ------------------------------------------------------------ build
    @classmethod
    def build(cls, config: dict, public: dict | None = None,
              approvals: list[dict] | None = None) -> "AliasBook":
        book = cls()
        public = public or {}
        book.version = config.get("version")
        book.ignore = {norm(v) for v in config.get("ignoreValues") or []}
        book.date_formats = list(config.get("dateFormats") or [])
        book.month_first = (config.get("ambiguousDateDefault") or "month-first") == "month-first"

        def add(table: dict, kind: str, key: Any, cid: str, method: str) -> None:
            k = norm(key)
            if not k or k in table:
                return
            table[k] = cid
            book._methods[(kind, k)] = method

        for h in public.get("heroes") or []:
            book.hero_roles[h["id"]] = h.get("role") or ""
            book.hero_names[h["id"]] = h.get("name") or h["id"]
            add(book.heroes, "hero", h.get("name"), h["id"], "catalog")
            add(book.heroes, "hero", h["id"], h["id"], "id")
        for hid, spellings in (config.get("heroes") or {}).items():
            for sp in spellings:
                add(book.heroes, "hero", sp, hid, "alias")

        for m in public.get("mapsCatalog") or []:
            book.map_modes[m["id"]] = m.get("mode") or ""
            book.map_names[m["id"]] = m.get("name") or m["id"]
            add(book.maps, "map", m.get("name"), m["id"], "catalog")
            add(book.maps, "map", m["id"], m["id"], "id")
        for extra in config.get("extraMaps") or []:
            book.map_modes.setdefault(extra["id"], extra.get("mode") or "")
            book.map_names.setdefault(extra["id"], extra.get("name") or extra["id"])
            add(book.maps, "map", extra.get("name"), extra["id"], "alias")
            for sp in extra.get("aliases") or []:
                add(book.maps, "map", sp, extra["id"], "alias")
        for mid, spellings in (config.get("maps") or {}).items():
            for sp in spellings:
                add(book.maps, "map", sp, mid, "alias")

        for mode, spellings in (config.get("modes") or {}).items():
            add(book.modes, "mode", mode, mode, "catalog")
            for sp in spellings:
                add(book.modes, "mode", sp, mode, "alias")
        for rid, spellings in (config.get("regions") or {}).items():
            add(book.regions, "region", rid, rid, "id")
            for sp in spellings:
                add(book.regions, "region", sp, rid, "alias")
        for r in public.get("regions") or []:
            add(book.regions, "region", r.get("name"), r["id"], "catalog")
            add(book.regions, "region", r["id"], r["id"], "id")
        for sid, spellings in (config.get("stages") or {}).items():
            for sp in spellings:
                add(book.stages, "stage", sp, sid, "alias")
        book.phase_stages = set(config.get("phaseStages") or [])
        # Reviewed picture -> hero overrides for match-card sheets that show
        # heroes as images ({content hash: hero id}).
        book.pictures = {str(h): str(v) for h, v in (config.get("pictures") or {}).items()
                         if not str(h).startswith("_")}
        for tid, spellings in (config.get("tournaments") or {}).items():
            for sp in spellings:
                add(book.tournaments, "tournament", sp, tid, "alias")

        book.team_history = list(config.get("teamHistory") or [])
        for tid, spellings in (config.get("teams") or {}).items():
            for sp in spellings:
                add(book.teams, "team", sp, tid, "alias")
        for t in public.get("teams") or []:
            book.team_names[t["id"]] = t.get("name") or t["id"]
            add(book.teams, "team", t.get("name"), t["id"], "catalog")
            add(book.teams, "team", t["id"], t["id"], "id")
            add(book.teams, "team", t.get("code"), t["id"], "catalog")
            for sp in (t.get("aliases") or []) + (t.get("previousNames") or []):
                add(book.teams, "team", sp, t["id"], "catalog")

        for pid, spellings in (config.get("players") or {}).items():
            for sp in spellings:
                add(book.players, "player", sp, pid, "alias")
        for p in public.get("players") or []:
            book.player_names.setdefault(p["id"], p.get("handle") or p["id"])
            add(book.players, "player", p.get("handle"), p["id"], "catalog")
            add(book.players, "player", p["id"], p["id"], "id")

        for a in approvals or []:
            kind, raw, to = a.get("kind"), norm(a.get("raw")), a.get("to")
            if kind and raw and to:
                book.approved[(kind, raw)] = to
        return book

    # ---------------------------------------------------------- lookups
    def is_blank(self, raw: Any) -> bool:
        return norm(raw) in self.ignore or norm(raw) == ""

    def _lookup(self, kind: str, table: dict, raw: Any) -> Resolution:
        text = "" if raw is None else str(raw).strip()
        k = norm(text)
        if not k or k in self.ignore:
            return Resolution(None, "empty", text)
        if (kind, k) in self.approved:
            return Resolution(self.approved[(kind, k)], "approved", text)
        if k in table:
            return Resolution(table[k], self._methods.get((kind, k), "alias"), text)
        return Resolution(None, "unresolved", text)

    def hero(self, raw: Any) -> Resolution:
        return self._lookup("hero", self.heroes, raw)

    def map(self, raw: Any) -> Resolution:
        return self._lookup("map", self.maps, raw)

    def mode(self, raw: Any) -> Resolution:
        return self._lookup("mode", self.modes, raw)

    def region(self, raw: Any) -> Resolution:
        return self._lookup("region", self.regions, raw)

    def stage(self, raw: Any) -> Resolution:
        r = self._lookup("stage", self.stages, raw)
        if r.id or r.method == "empty":
            return r
        # Stage text is free-form ("Stage 2 Week 3 Day 1"): accept the
        # longest known stage spelling it CONTAINS, as a whole phrase.
        k = f" {norm(raw)} "
        best = None
        for spelling, sid in self.stages.items():
            if f" {spelling} " in k and (best is None or len(spelling) > len(best[0])):
                best = (spelling, sid)
        return Resolution(best[1], "contains", r.raw) if best else r

    def stage_key(self, *texts: Any) -> str | None:
        """Which stage of the season a text names, ignoring phase words:
        'Stage 2 Playoffs' and 'EMEA - Stage 2' are both 'stage-2'. Used
        to compare stages when there is no date to go on. None when the
        text names no stage."""
        k = f" {norm(' '.join(str(t) for t in texts if t))} "
        best = None
        for spelling, sid in self.stages.items():
            if sid in self.phase_stages:
                continue
            if f" {spelling} " in k and (best is None or len(spelling) > len(best[0])):
                best = (spelling, sid)
        return best[1] if best else None

    def region_in(self, raw: Any) -> Resolution:
        """A region named anywhere in a text, as a whole phrase ('North
        America - Stage 1' -> na). The longest known spelling wins."""
        r = self.region(raw)
        if r.id or r.method == "empty":
            return r
        k = f" {norm(raw)} "
        best = None
        for spelling, rid in self.regions.items():
            if f" {spelling} " in k and (best is None or len(spelling) > len(best[0])):
                best = (spelling, rid)
        return Resolution(best[1], "contains", r.raw) if best else r

    def tournament(self, raw: Any) -> Resolution:
        r = self._lookup("tournament", self.tournaments, raw)
        if r.id or r.method == "empty":
            return r
        return Resolution("xt-" + slugify(raw), "slug", r.raw)

    def team(self, raw: Any, date: str | None = None) -> Resolution:
        text = "" if raw is None else str(raw).strip()
        k = norm(text)
        if not k or k in self.ignore:
            return Resolution(None, "empty", text)
        if ("team", k) in self.approved:
            return Resolution(self.approved[("team", k)], "approved", text)
        for h in self.team_history:
            if norm(h.get("name")) != k:
                continue
            since, until = h.get("from") or "0000", h.get("until") or "9999"
            if date is None or since <= date <= until:
                return Resolution(h.get("team"), "history", text)
        if k in self.teams:
            return Resolution(self.teams[k], self._methods.get(("team", k), "alias"), text)
        return Resolution("x-" + slugify(text), "slug", text)

    def player(self, raw: Any) -> Resolution:
        text = "" if raw is None else str(raw).strip()
        k = norm(text)
        if not k or k in self.ignore:
            return Resolution(None, "empty", text)
        if ("player", k) in self.approved:
            return Resolution(self.approved[("player", k)], "approved", text)
        if k in self.players:
            return Resolution(self.players[k], self._methods.get(("player", k), "alias"), text)
        # Case-insensitive grouping is still useful when no roster knows
        # the handle: 'ZOX' and 'zox' become one player, labelled unmapped.
        return Resolution("p-" + slugify(text), "slug", text)

    def role_of(self, hero_id: str | None) -> str | None:
        return self.hero_roles.get(hero_id or "") or None

    # ------------------------------------------------------------ dates
    def date(self, value: Any, year_hint: int | None = None) -> tuple[str | None, str, bool]:
        """(ISO date | None, method, ambiguous). A cell the spreadsheet
        stores as a real date is exact; text is parsed against the
        configured formats, and a day/month order that both readings would
        accept is flagged ambiguous rather than silently resolved."""
        if value is None or value == "":
            return None, "empty", False
        if isinstance(value, dict) and value.get("date"):
            return str(value["date"])[:10], "cell-date", False
        if isinstance(value, (dt.datetime, dt.date)):
            return value.isoformat()[:10], "cell-date", False
        if isinstance(value, (int, float)) and 20000 < float(value) < 80000:
            base = dt.date(1899, 12, 30)       # spreadsheet serial day number
            return (base + dt.timedelta(days=int(value))).isoformat(), "serial", False
        text = str(value).strip()
        text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text)
        text = re.sub(r"^(mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+", "", text, flags=re.I)
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
        if m:
            try:
                return dt.date.fromisoformat(m.group(1)).isoformat(), "iso", False
            except ValueError:
                return None, "unparsed", False
        hits: list[tuple[str, str]] = []
        for fmt in self.date_formats:
            try:
                d = dt.datetime.strptime(text, fmt).date()
            except ValueError:
                continue
            if d.isoformat() not in [h[0] for h in hits]:
                hits.append((d.isoformat(), fmt))
        if hits:
            numeric = [h for h in hits if re.fullmatch(r"[%mdyY/.\-]+", h[1])]
            if len(numeric) > 1:
                pick = numeric[0] if self.month_first else numeric[-1]
                return pick[0], f"text:{pick[1]}", True
            return hits[0][0], f"text:{hits[0][1]}", False
        if year_hint:
            for fmt in ("%b %d", "%B %d", "%d %b", "%d %B", "%m/%d", "%d/%m"):
                try:
                    d = dt.datetime.strptime(f"{text} {year_hint}", f"{fmt} %Y").date()
                except ValueError:
                    continue
                return d.isoformat(), f"text:{fmt}+year-from-context", fmt in ("%m/%d", "%d/%m")
        return None, "unparsed", False


def year_in(*texts: Any) -> int | None:
    for t in texts:
        m = re.search(r"\b(20[2-3]\d)\b", str(t or ""))
        if m:
            return int(m.group(1))
    return None
