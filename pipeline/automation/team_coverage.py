"""
team_coverage.py — per-team coverage states (Roadmap Phase D2).

Companion to coverage.py's per-MATCH ledger: this is the per-TEAM ledger the
guide's "Team Coverage" control room reads. Every team discovered by
match/schedule automation gets exactly one row with an explicit state for
each of identity, roster, logo, broadcasts, and captured maps — nothing
disappears silently, and nothing is guessed to look more complete than it
is. A team missing a piece always carries a human-readable `blockingIssue`
naming the single next thing that would move it forward.

Pure read: this module never mutates the content DB, the automation DB, or
any asset registry file.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
CONTENT_DB = os.environ.get("OWCS_DB", os.path.join(REPO_ROOT, "data", "owcs.sqlite"))
ASSET_SOURCES_PATH = os.path.join(REPO_ROOT, "assets", "data", "team_asset_sources.json")

# The guide's explicit per-team coverage states — every team gets exactly the
# subset that applies, never a silent absence.
STATES = (
    "schedule-discovered", "identity-verified", "roster-verified",
    "logo-candidate", "logo-verified", "broadcast-located",
    "composition-captured", "needs-review", "unsupported",
)


def _connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (name,)).fetchone() is not None


def _load_asset_sources(path: str) -> dict:
    if not os.path.exists(path):
        return {"teams": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _logo_state(row: sqlite3.Row, sources: dict) -> str:
    if row["logo_url"]:
        return "logo-verified"
    entry = sources.get("teams", {}).get(row["id"], {})
    has_candidates = bool(entry.get("candidateSources") or entry.get("assetCandidates"))
    return "logo-candidate" if has_candidates else "none"


def _team_matches(con: sqlite3.Connection, team_id: str, window_start: str) -> list[sqlite3.Row]:
    return list(con.execute(
        """SELECT * FROM matches WHERE (team_a=? OR team_b=?)
           AND COALESCE(scheduled_at, finished_at, date) >= ?
           ORDER BY COALESCE(scheduled_at, finished_at, date) DESC""",
        (team_id, team_id, window_start)))


def _broadcast_located_count(acon: sqlite3.Connection | None, match_ids: list[str]) -> int:
    if not acon or not match_ids or not _has_table(acon, "broadcast_candidates"):
        return 0
    q = ",".join("?" for _ in match_ids)
    rows = acon.execute(
        f"""SELECT DISTINCT match_id FROM broadcast_candidates
            WHERE match_id IN ({q}) AND confidence IN ('high','medium')""",
        tuple(match_ids)).fetchall()
    return len(rows)


def _captured_maps_count(con: sqlite3.Connection, team_id: str) -> int:
    # hero_stints is the real, current full-map CV pipeline's output (see
    # export_data.py's build_public_payload, which is built from hero_stints
    # too) — comp_snapshots is an older, separate capture path still written
    # by other tools but not what publicly counts as "captured" today.
    if not _has_table(con, "hero_stints"):
        return 0
    return con.execute(
        "SELECT COUNT(DISTINCT map_result_id) FROM hero_stints WHERE team_id=?",
        (team_id,)).fetchone()[0]


def _blocking_issue(states: set[str], row: sqlite3.Row, matches_n: int) -> str | None:
    if row["needs_review"]:
        return row["review_reason"] or "identity or roster fact needs a human look"
    if "identity-verified" not in states:
        return "no faceit_team_id / official source has verified this identity yet"
    if matches_n == 0:
        return "no matches for this team inside the current window"
    if "roster-verified" not in states:
        return "no roster on record for this team"
    if "logo-verified" not in states:
        return ("awaiting human-approved official logo"
                if "logo-candidate" in states else
                "no candidate logo source recorded yet")
    if "broadcast-located" not in states:
        return "no official broadcast located for this team's matches yet"
    if "composition-captured" not in states:
        return "composition tracking pending — no maps captured yet"
    return None


def build_report(
    *,
    content_db: str = CONTENT_DB,
    automation_db: str | None = None,
    asset_sources_path: str = ASSET_SOURCES_PATH,
    window_days: int = 30,
    supported_regions: "set[str] | None" = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """One row per team in the registry. Pure read; writes nothing."""
    now = now or dt.datetime.now(dt.timezone.utc)
    window_start = (now - dt.timedelta(days=window_days)).date().isoformat()
    report: dict[str, Any] = {
        "window_days": window_days,
        "generated_at": now.replace(microsecond=0).isoformat(),
        "teams": [],
        "counts": {s.replace("-", "_"): 0 for s in STATES},
    }
    if not os.path.exists(content_db):
        return report

    sources = _load_asset_sources(asset_sources_path)
    con = _connect(content_db)
    acon = _connect(automation_db) if automation_db and os.path.exists(automation_db) else None
    try:
        for row in con.execute("SELECT * FROM teams ORDER BY name"):
            states: set[str] = set()
            matches = _team_matches(con, row["id"], window_start)
            match_ids = [m["id"] for m in matches]

            # A team always at least "schedule-discovered" once it's in the
            # registry at all — its very existence came from a discovered
            # match, calendar event, or broadcast candidate.
            states.add("schedule-discovered")
            if row["identity_verified_at"] or row["faceit_team_id"]:
                states.add("identity-verified")
            has_roster = con.execute(
                "SELECT 1 FROM match_rosters WHERE team_id=? LIMIT 1", (row["id"],)
            ).fetchone() is not None
            if row["roster_verified_at"] and has_roster:
                states.add("roster-verified")

            logo_state = _logo_state(row, sources)
            if logo_state == "logo-verified":
                states.add("logo-verified")
            elif logo_state == "logo-candidate":
                states.add("logo-candidate")

            broadcasts = _broadcast_located_count(acon, match_ids)
            has_vod = any(m["vod_url"] for m in matches)
            if broadcasts or has_vod:
                states.add("broadcast-located")

            captured_maps = _captured_maps_count(con, row["id"])
            if captured_maps:
                states.add("composition-captured")

            if row["needs_review"]:
                states.add("needs-review")
            if supported_regions is not None and (row["region"] or "").lower() not in supported_regions:
                states.add("unsupported")

            for s in states:
                report["counts"][s.replace("-", "_")] += 1

            report["teams"].append({
                "id": row["id"], "name": row["name"], "region": row["region"],
                "status": row["status"], "states": sorted(states),
                "matches": len(matches),
                "broadcastsLocated": broadcasts,
                "capturedMaps": captured_maps,
                "blockingIssue": _blocking_issue(states, row, len(matches)),
            })
    finally:
        con.close()
        if acon:
            acon.close()
    return report


def format_report(report: dict[str, Any]) -> str:
    c = report["counts"]
    lines = [f"Team coverage — rolling {report['window_days']}-day window "
             f"({len(report['teams'])} teams):"]
    for s in STATES:
        key = s.replace("-", "_")
        if c.get(key):
            lines.append(f"  {s}: {c[key]}")
    blocked = [t for t in report["teams"] if t["blockingIssue"]]
    if blocked:
        lines.append("")
        lines.append("Teams with a blocking issue:")
        for t in blocked:
            lines.append(f"  - [{t['region']}] {t['name']} ({t['id']}): {t['blockingIssue']}")
    return "\n".join(lines)
