#!/usr/bin/env python3
"""
hero_roles.py — heroes whose role changed during the season.

The heroes table holds ONE role per hero: the role it has today. A rework
can move a hero between roles mid-season (Sombra went from Damage to
Support with Season 5 on 2026-10-06), so a VOD or a spreadsheet card from
before the change still shows that hero in its old role's slot.

The reference catalog (`sample_data.json`) records the old role under
`pastRoles` ([{"role": "Damage", "until": "2026-10-06"}], `until` being
the first day of the new role). This module reads it so the detector, the
comp solver and the sheet importer can accept a hero in any role it has
held, and name the role it played on a given date.
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "sample_data.json")

_PAST: dict[str, list[dict]] | None = None


def past_roles() -> dict[str, list[dict]]:
    """{hero_id: [{'role', 'until'}, ...]} for heroes that changed role."""
    global _PAST
    if _PAST is None:
        try:
            with open(SAMPLE, "r", encoding="utf-8") as f:
                heroes = json.load(f).get("heroes") or []
        except (OSError, ValueError):
            heroes = []
        _PAST = {h["id"]: sorted(h["pastRoles"], key=lambda r: r.get("until") or "")
                 for h in heroes if h.get("pastRoles")}
    return _PAST


def roles_held(hero_id: str | None, current: str | None) -> set[str]:
    """Every role `hero_id` has played this season, current one included."""
    out = {current} if current else set()
    out.update(r["role"] for r in past_roles().get(hero_id or "", []) if r.get("role"))
    return out


def can_play(hero_id: str | None, role: str, current: str | None) -> bool:
    """True when `hero_id` has held `role` (now or before a rework)."""
    return role in roles_held(hero_id, current)


def role_on(hero_id: str | None, date: str | None, current: str | None) -> str | None:
    """The role `hero_id` played on ISO `date` (YYYY-MM-DD). With no date,
    or a date after every change, the current role."""
    if date:
        for r in past_roles().get(hero_id or "", []):
            if r.get("until") and str(date)[:10] < r["until"]:
                return r.get("role") or current
    return current
