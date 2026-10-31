"""
layout.py — find a worksheet's header and decide what the worksheet is.

No tab name and no column letter is hard-coded anywhere in the importer.
For each worksheet this module:

  1. scans the first `headerScanRows` rows for the row whose cells match the
     most known header names (config/external/gsheet_layout.json). A merged
     group label in the row above ('Team 1' over 'Ban | Tank | DPS 1 …') is
     combined with the sub-header, so two-row headers read as 'team 1 ban';
  2. maps each column to a canonical single field ('map', 'winner', …) or to
     a member of a repeated family (ban n of side a, player p hero n, …);
  3. classifies the tab by which fields it has: map_rows, player_rows,
     ban_rows, match_rows — or 'unrecognised', which is reported in the
     source inventory and preserved in the raw layer, never guessed at.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .aliases import norm


@dataclass
class Column:
    col: int
    header: str                 # header text as written (combined if two-row)
    key: str                    # normalised header used for matching
    field: str | None = None    # canonical single field
    kind: str | None = None     # group kind: ban, comp, player_name, player_hero, …
    side: str | None = None     # 'a' | 'b'
    p: int | None = None        # player position within a side
    n: int | None = None        # list position (hero 1/2/3, ban 1/2)
    role: str | None = None
    cell: str = ""              # the header cell's own text (bottom row)

    def label(self) -> str:
        if self.field:
            return self.field
        bits = [self.kind or "?"]
        if self.side:
            bits.append(f"side {self.side}")
        if self.role:
            bits.append(self.role)
        if self.p:
            bits.append(f"p{self.p}")
        if self.n:
            bits.append(f"#{self.n}")
        return " ".join(bits)


@dataclass
class Header:
    row: int                                   # 0-based header row
    columns: list[Column] = field(default_factory=list)
    unmapped: list[tuple[int, str]] = field(default_factory=list)

    @property
    def fields(self) -> set[str]:
        out = {c.field for c in self.columns if c.field}
        out |= {c.kind for c in self.columns if c.kind}
        return out

    def by_field(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.field == name), None)

    def group(self, kind: str) -> list[Column]:
        return [c for c in self.columns if c.kind == kind]


ROLE_WORDS = {
    "tank": "Tank", "dps": "Damage", "damage": "Damage", "hitscan": "Damage",
    "projectile": "Damage", "support": "Support", "sup": "Support",
    "supp": "Support", "healer": "Support", "main support": "Support",
    "flex support": "Support", "flex": None,
}


class Layout:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.version = cfg.get("version")
        self.scan_rows = int(cfg.get("headerScanRows") or 40)
        self.min_fields = int(cfg.get("minHeaderFields") or 3)
        self.max_ignored = float(cfg.get("maxIgnoredRowShare") or 0.5)
        self.separators = list(cfg.get("listSeparators") or ["/", ","])
        self.forward_fill = list(cfg.get("forwardFill") or [])
        self.purposes = cfg.get("purposes") or {}
        self.credit_res = [re.compile(r"\b" + p, re.I) for p in cfg.get("creditPatterns") or []]
        self.field_names: dict[str, str] = {}
        for fname, spellings in (cfg.get("fields") or {}).items():
            for sp in spellings:
                self.field_names.setdefault(norm(sp), fname)
        side_tokens = cfg.get("sideTokens") or {}
        alt = {s: "|".join(sorted((re.escape(norm(t)) for t in toks), key=len, reverse=True))
               for s, toks in side_tokens.items()}
        self.side_of = {norm(t): s for s, toks in side_tokens.items() for t in toks}
        self.groups: list[tuple[str, re.Pattern]] = []
        for g in cfg.get("groups") or []:
            for pat in g.get("patterns") or []:
                expanded = pat.replace("{side}", "(?P<side>" + "|".join(alt.values()) + ")")
                self.groups.append((g["kind"], re.compile(expanded)))

    # ------------------------------------------------------ one column
    def match(self, key: str) -> Column | None:
        if not key:
            return None
        for kind, rx in self.groups:
            m = rx.match(key)
            if not m:
                continue
            gd = m.groupdict()
            side = self.side_of.get(gd.get("side") or "") if gd.get("side") else None
            role_word = gd.get("role")
            return Column(col=-1, header="", key=key, kind=kind, side=side,
                          p=int(gd["p"]) if gd.get("p") else None,
                          n=int(gd["n"]) if gd.get("n") else None,
                          role=ROLE_WORDS.get(role_word) if role_word else None)
        if key in self.field_names:
            return Column(col=-1, header="", key=key, field=self.field_names[key])
        return None

    def _interpret(self, top: Any, cell: Any) -> Column | None:
        """Best reading of one header cell, given the cell above it."""
        k_cell, k_top = norm(cell), norm(top)
        tries = []
        if k_top and k_cell and k_top != k_cell:
            tries.append((f"{k_top} {k_cell}", f"{top} {cell}"))
        if k_cell:
            tries.append((k_cell, str(cell)))
        for key, text in tries:
            c = self.match(key)
            if c:
                c.header = text.strip()
                c.cell = str(cell).strip()
                return c
        return None

    # ------------------------------------------------------ the header
    def find_header(self, g: list[list[Any]]) -> Header | None:
        best: Header | None = None
        best_score = 0
        for r in range(min(self.scan_rows, len(g))):
            row = g[r]
            if sum(1 for v in row if v not in (None, "")) < self.min_fields:
                continue
            above = g[r - 1] if r > 0 else [None] * len(row)
            cols, unmapped = [], []
            taken: set[str] = set()
            for c, cell in enumerate(row):
                if cell in (None, ""):
                    continue
                top = above[c] if c < len(above) else None
                col = self._interpret(top, cell)
                if col is None or (col.field and col.field in taken):
                    # a second column claiming the same single field is
                    # reported, never silently preferred over the first
                    unmapped.append((c, str(cell) + (" (duplicate of an earlier column)"
                                                      if col is not None else "")))
                    continue
                if col.field:
                    taken.add(col.field)
                col.col = c
                cols.append(col)
            score = len({(x.field or x.label()) for x in cols})
            if score > best_score:
                best_score, best = score, Header(row=r, columns=cols, unmapped=unmapped)
        if best is None or best_score < self.min_fields:
            return None
        self._fill_group_gaps(best)
        return best

    @staticmethod
    def _fill_group_gaps(h: Header) -> None:
        """Number repeated family members that the header left implicit:
        three columns all headed 'Team 1 Ban' become ban #1, #2, #3."""
        seen: dict[tuple, int] = {}
        for c in sorted(h.columns, key=lambda c: c.col):
            if not c.kind:
                continue
            if c.kind in ("ban", "ban_noside", "long_hero", "comp") and c.n is None:
                key = (c.kind, c.side)
                seen[key] = seen.get(key, 0) + 1
                c.n = seen[key]
            if c.kind in ("player_hero", "player_name") and c.p is None:
                key = (c.kind, c.side, c.role, c.n)
                seen[key] = seen.get(key, 0) + 1
                c.p = seen[key]
        # Role-headed hero columns ("Team 1 DPS 1") give positions per role;
        # turn them into a stable player index 1..n within the side.
        for side in ("a", "b"):
            members = sorted({(c.role or "", c.p or 0) for c in h.columns
                              if c.kind in ("player_hero", "player_name") and c.side == side and c.role})
            order = {"Tank": 0, "Damage": 1, "Support": 2, "": 3}
            members.sort(key=lambda m: (order.get(m[0], 3), m[1]))
            index = {m: i + 1 for i, m in enumerate(members)}
            for c in h.columns:
                if c.kind in ("player_hero", "player_name") and c.side == side and c.role:
                    c.p = index[(c.role or "", c.p or 0)]

    # ------------------------------------------------------ purpose
    def purpose(self, h: Header | None) -> str:
        if h is None:
            return "unrecognised"
        have = set(h.fields)
        if h.group("player_hero") or h.group("comp"):
            have.add("hero_columns")
        # Long formats first: a per-player tab also names a map and both
        # teams, so it would otherwise read as a per-map tab.
        for name in ("player_rows", "ban_rows", "map_rows", "match_rows"):
            spec = self.purposes.get(name) or {}
            if not all(f in have for f in spec.get("requiresAll") or []):
                continue
            if any(f in have for f in spec.get("excludes") or []):
                continue
            anys = spec.get("requiresAny") or []
            if anys and not any(all(f in have for f in group) for group in anys):
                continue
            return name
        return "unrecognised"

    def purpose_label(self, name: str) -> str:
        return (self.purposes.get(name) or {}).get("label") or {
            "unrecognised": "Not recognised — preserved in the raw layer only",
            "credits": "Credits / about text",
            "empty": "Empty",
        }.get(name, name)

    # ------------------------------------------------------ cell lists
    def split(self, value: Any) -> list[str]:
        """'Juno / Lúcio' -> ['Juno', 'Lúcio']; a cell with one value is a
        list of one. Separators are configurable."""
        if value in (None, ""):
            return []
        text = str(value)
        parts = [text]
        for sep in self.separators:
            nxt = []
            for p in parts:
                if sep.strip() and sep.strip().isalpha():
                    nxt.extend(re.split(r"(?i)\s+" + re.escape(sep.strip()) + r"\s+", p))
                else:
                    nxt.extend(p.split(sep))
            parts = nxt
        return [p.strip() for p in parts if p and p.strip()]

    def credits(self, sheet_name: str, g: list[list[Any]], a1) -> list[dict]:
        out = []
        for r, row in enumerate(g[:200]):
            for c, v in enumerate(row):
                if isinstance(v, str) and any(rx.search(v) for rx in self.credit_res):
                    out.append({"sheet": sheet_name, "cell": a1(r, c), "text": v[:300]})
        return out[:10]
