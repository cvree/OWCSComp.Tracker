"""
team_enrichment.py — populate team profile FACTS from the FACEIT team API
(Roadmap Phase D — team automation).

Match discovery only ever wrote the minimum a team row needs to exist (name,
region, a crude code, faceit_team_id). This module fills the rest in from
FACEIT's own `/teams/{id}` resource — the SAME authority match facts already
come from — bio, website, socials, and roster size. It never searches: a team
is only enriched once discovery has already resolved its `faceit_team_id`
from a real match roster, so nothing here can attach the wrong team's facts.

Hard rule carried over from the asset-registry workflow (never hotlink, never
guess a logo): FACEIT's `avatar`/`cover_image` URLs are recorded ONLY as
candidate sources in `assets/data/team_asset_sources.json` for a human to
verify and download. They are never written to `teams.logo_url` and never
rendered directly. This module's only DB write is to `teams` FACT columns
(description, website, twitter, facebook, member_count, faceit_enriched_at).

Idempotent: rerunning upserts the same facts (COALESCE never nulls out a
previously-known value on a thin response) and never adds a duplicate
candidate-source line. One team's API failure never blocks the rest.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from . import faceit_api

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
DEFAULT_ASSET_SOURCES = os.path.join(
    REPO_ROOT, "assets", "data", "team_asset_sources.json")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _teams_to_enrich(con, team_ids: list[str] | None) -> list[dict]:
    """Every team with a known faceit_team_id — never a guess, never a search.
    An explicit team_ids filter narrows to those rows (still id-known only)."""
    rows = con.execute(
        "SELECT id, faceit_team_id FROM teams WHERE faceit_team_id IS NOT NULL"
    ).fetchall()
    out = [dict(r) for r in rows]
    if team_ids:
        wanted = set(team_ids)
        out = [r for r in out if r["id"] in wanted]
    return out


def _upsert_team_facts(con, team_id: str, facts: dict, now_iso: str) -> None:
    """COALESCE so a thin/partial FACEIT response never blanks a previously
    known fact — only a real non-null value ever overwrites."""
    con.execute(
        """UPDATE teams SET
             description = COALESCE(?, description),
             website     = COALESCE(?, website),
             twitter     = COALESCE(?, twitter),
             facebook    = COALESCE(?, facebook),
             member_count = COALESCE(?, member_count),
             avatar_source_url = COALESCE(?, avatar_source_url),
             faceit_enriched_at = ?
           WHERE id = ?""",
        (facts.get("description"), facts.get("website"), facts.get("twitter"),
         facts.get("facebook"), facts.get("memberCount"), facts.get("avatarUrl"),
         now_iso, team_id))


# ------------------------------------------------------- candidate sources
def _load_asset_sources(path: str) -> dict:
    if not os.path.exists(path):
        return {"_note": "", "teams": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _add_candidate_source(registry: dict, team_id: str, line: str) -> bool:
    """Append `line` to team_id's candidateSources if not already present.
    Returns True if it was actually added (for idempotency reporting)."""
    teams = registry.setdefault("teams", {})
    entry = teams.setdefault(team_id, {"candidateSources": []})
    sources = entry.setdefault("candidateSources", [])
    if line in sources:
        return False
    sources.append(line)
    return True


def _candidate_lines(team_id: str, facts: dict, today: str) -> list[str]:
    lines = []
    if facts.get("avatarUrl"):
        lines.append(
            f"FACEIT team avatar (unverified candidate, auto-discovered "
            f"{today} via FACEIT teams API for {team_id}): {facts['avatarUrl']}")
    if facts.get("coverUrl"):
        lines.append(
            f"FACEIT team cover image (unverified candidate, auto-discovered "
            f"{today} via FACEIT teams API for {team_id}): {facts['coverUrl']}")
    return lines


# ------------------------------------------------------------- orchestrator
def enrich_teams(
    *,
    con,
    client: faceit_api.FaceitClient,
    team_ids: list[str] | None = None,
    asset_sources_path: str = DEFAULT_ASSET_SOURCES,
    dry_run: bool = False,
    now: dt.datetime | None = None,
) -> dict:
    """Fetch + upsert FACEIT team facts for every team with a known
    faceit_team_id. Pure read + score in dry-run; writes nothing."""
    now = now or _now()
    now_iso = now.replace(microsecond=0).isoformat()
    today = now.date().isoformat()

    candidates = _teams_to_enrich(con, team_ids)
    summary: dict[str, Any] = {
        "dryRun": dry_run, "teamsConsidered": len(candidates),
        "enriched": [], "errors": [], "newCandidateSources": [],
    }
    if not candidates:
        summary["note"] = "no teams with a known faceit_team_id to enrich"
        return summary

    registry = _load_asset_sources(asset_sources_path)
    registry_changed = False

    for row in candidates:
        tid, fid = row["id"], row["faceit_team_id"]
        try:
            raw = client.get_team(fid)
        except (faceit_api.FaceitApiError, faceit_api.FaceitAuthError) as exc:
            summary["errors"].append({"teamId": tid, "faceitTeamId": fid, "error": str(exc)})
            continue

        facts = faceit_api.normalize_team(raw)
        entry = {"teamId": tid, "faceitTeamId": fid,
                 "hasDescription": bool(facts.get("description")),
                 "hasWebsite": bool(facts.get("website")),
                 "memberCount": facts.get("memberCount")}
        summary["enriched"].append(entry)

        for line in _candidate_lines(tid, facts, today):
            if dry_run:
                # Report what WOULD be added without mutating the registry dict
                # used for the actual (possibly later, non-dry) write.
                existing = registry.get("teams", {}).get(tid, {}).get("candidateSources", [])
                if line not in existing:
                    summary["newCandidateSources"].append({"teamId": tid, "line": line})
            elif _add_candidate_source(registry, tid, line):
                registry_changed = True
                summary["newCandidateSources"].append({"teamId": tid, "line": line})

        if not dry_run:
            _upsert_team_facts(con, tid, facts, now_iso)

    if not dry_run:
        con.commit()
        if registry_changed:
            Path(asset_sources_path).parent.mkdir(parents=True, exist_ok=True)
            with open(asset_sources_path, "w", encoding="utf-8") as f:
                json.dump(registry, f, indent=1)
                f.write("\n")

    return summary


def format_summary(summary: dict) -> str:
    lines = [
        f"teams considered : {summary['teamsConsidered']}",
        f"enriched         : {len(summary['enriched'])} "
        f"({'dry-run — no writes' if summary['dryRun'] else 'written'})",
        f"new candidate srcs: {len(summary['newCandidateSources'])}",
    ]
    if summary.get("note"):
        lines.append(f"note             : {summary['note']}")
    for e in summary["errors"]:
        lines.append(f"  API ERROR      : {e['teamId']} ({e['faceitTeamId']}): {e['error']}")
    for c in summary["newCandidateSources"][:20]:
        lines.append(f"  candidate src  : {c['teamId']}: {c['line']}")
    return "\n".join(lines)
