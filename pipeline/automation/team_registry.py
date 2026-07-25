"""
team_registry.py — canonical team identity (Roadmap Phase D2).

Match discovery only ever wrote the minimum a team row needs to exist (name,
region, a crude code, faceit_team_id). This module turns that into a real
identity registry:

  * a rename (same faceit_team_id, new name) is preserved: the OLD name
    moves into previous_names, it is never just dropped;
  * a name collision between two DIFFERENT regions with no faceit_team_id
    linking them is treated as two DISTINCT organizations, never merged;
  * a conflicting fact from a second source (e.g. a region that actually
    CHANGES, not just fills in from Unknown) never overwrites silently — it
    sets needs_review with an explicit, human-readable reason instead of
    guessing which source wins;
  * a roster made of unsigned/mix/academy-looking names gets an explicit
    status instead of being treated as a normal signed org.

This module owns identity + roster PROVENANCE only. It never writes a hero
composition and never touches a logo (see team_assets.py for the separate,
human-gated asset pipeline).
"""
from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

# Heuristic markers for a temporary/unsigned roster. Best-effort only — a
# false negative just leaves status at the default 'active', never wrong in
# a way that fabricates an organization; a human can always correct it via
# the coverage dashboard's needs-review queue.
_UNSIGNED_MARKERS = ("mix team", "mix squad", "unsigned", "academy", "amateur", " tbd")


def _now_iso(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.timezone.utc)).replace(microsecond=0).isoformat()


def _json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except (TypeError, ValueError):
        return []


def looks_unsigned(name: str | None) -> bool:
    n = f" {(name or '').lower()} "
    return any(marker in n for marker in _UNSIGNED_MARKERS)


def resolve_identity_slug(
    con, name: str | None, faceit_team_id: str | None, region: str | None,
    side: str, faceit_match_id: str, slugger,
) -> str:
    """Resolve to a stable team id, never merging two different orgs.

    Precedence:
      1. An existing row already keyed by this faceit_team_id — rename-safe,
         always the same team regardless of what its name is today.
      2. A plain name slug — UNLESS that slug already belongs to a row whose
         region differs from this one and which carries no faceit_team_id
         tying the two together, in which case this is a DIFFERENT
         organization and gets a region-scoped id instead of overwriting it.
      3. Deterministic id fallbacks when no name is known at all.
    """
    if faceit_team_id:
        row = con.execute("SELECT id FROM teams WHERE faceit_team_id=?",
                          (faceit_team_id,)).fetchone()
        if row:
            return row["id"]
    if name:
        base = slugger(name, f"team_{side.lower()}")
        row = con.execute(
            "SELECT region, faceit_team_id FROM teams WHERE id=?", (base,)).fetchone()
        same_org = (
            row is None
            or not row["region"] or row["region"] in ("Unknown", region)
            or (faceit_team_id and row["faceit_team_id"] == faceit_team_id))
        if same_org:
            return base
        scoped = f"{base}_{re.sub(r'[^a-z0-9]+', '', (region or 'rg').lower()) or 'rg'}"
        return scoped
    if faceit_team_id:
        return f"faceit_{slugger(faceit_team_id, 'team')}"
    short = re.sub(r"[^a-zA-Z0-9]", "", faceit_match_id)[-8:].lower() or "faceit"
    return f"faceit_{short}_{side.lower()}"


def upsert_identity(
    con, team_id: str, *, name: str | None, region: str | None,
    faceit_team_id: str | None, source_authority: str,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Idempotent identity upsert preserving history instead of overwriting.

    Returns {teamId, created, renamed, conflict} for callers/tests — never
    raises on a conflict, just records it for the coverage dashboard.
    """
    now_iso = _now_iso(now)
    existing = con.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
    result = {"teamId": team_id, "created": existing is None,
              "renamed": False, "conflict": False}
    status = "unsigned" if looks_unsigned(name) else None

    if existing is None:
        con.execute(
            """INSERT INTO teams (id, name, region, code, faceit_team_id,
                                   status, source_authority, identity_verified_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (team_id, name or team_id, region or "Unknown",
             (name or team_id)[:6].upper().replace(" ", ""), faceit_team_id,
             status or "active", source_authority,
             now_iso if faceit_team_id else None))
        return result

    updates: dict[str, Any] = {}
    prev_names = _json_list(existing["previous_names"])

    if name and existing["name"] and name != existing["name"]:
        if existing["name"] not in prev_names:
            prev_names.append(existing["name"])
        updates["previous_names"] = json.dumps(prev_names)
        updates["name"] = name
        result["renamed"] = True
    elif name and not existing["name"]:
        updates["name"] = name

    cur_region = existing["region"]
    if region and cur_region and cur_region not in ("Unknown", region):
        updates["needs_review"] = 1
        updates["review_reason"] = (
            f"region conflict: had '{cur_region}', {source_authority} "
            f"reports '{region}' — kept '{cur_region}', needs a human look")
        result["conflict"] = True
    elif region and (not cur_region or cur_region == "Unknown"):
        updates["region"] = region

    if faceit_team_id and not existing["faceit_team_id"]:
        updates["faceit_team_id"] = faceit_team_id
    if faceit_team_id and not existing["identity_verified_at"]:
        updates["identity_verified_at"] = now_iso
    if not existing["source_authority"]:
        updates["source_authority"] = source_authority
    if status and existing["status"] == "active":
        updates["status"] = status

    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        con.execute(f"UPDATE teams SET {set_clause} WHERE id=?",
                   (*updates.values(), team_id))
    return result


def record_roster(con, team_id: str, had_players: bool, source: str = "faceit",
                  now: dt.datetime | None = None) -> None:
    """Stamp roster provenance the moment match_rosters rows are (re)written
    for this team. A no-op when the source had no roster to report — an
    empty roster is never treated as a verified one."""
    if not had_players:
        return
    con.execute(
        "UPDATE teams SET roster_source=?, roster_verified_at=? WHERE id=?",
        (source, _now_iso(now), team_id))
