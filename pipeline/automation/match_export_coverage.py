"""
match_export_coverage.py — match export coverage report (Phase D2.1).

Companion to match_repair.py: where that module repairs missing metadata,
this one answers "given the DB as it stands right now, which matches
actually reach the public export, and exactly why does everything else
not?" It calls export_data.build_public_payload as the single source of
truth for "is this match exported" (never re-implements that decision, so
this report can never drift from what the site actually renders) and gives
every excluded row one explicit, human-readable reason instead of a silent
absence.

Pure read: never mutates the content DB, never regenerates the public
export file itself (see automation/cli.py's `_run_export` for that).
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from typing import Any

# export_data.py lives in the pipeline dir (a script dir, not a package) —
# same convention as discovery.py/cli.py.
_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

import export_data  # noqa: E402

from . import match_repair as mrepair


def _rv(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def _is_unsupported_region(row: Any) -> bool:
    region = (_rv(row, "region") or "").strip().lower()
    return bool(region) and region not in {r["id"] for r in export_data.REGIONS}


def _exclusion_reason(row: Any, team_a_exists: bool, team_b_exists: bool) -> str:
    """Exactly one reason a match did NOT reach the public export — never
    left implicit. Mirrors the real gate in export_data.build_public_payload
    (CV-ingest path + the status='final'/discovered-window bypass) so this
    stays a diagnosis of that code, not a second opinion."""
    fixture_kind = mrepair.classify_fixture_kind(row)
    if fixture_kind == "synthetic":
        return "synthetic-fixture"
    if not (team_a_exists and team_b_exists):
        return "missing-teams"
    status = (_rv(row, "status") or "").lower()
    lifecycle = _rv(row, "lifecycle_status")
    competition = _rv(row, "competition_id")
    if status == "unknown" and not lifecycle:
        return "needs-review"
    if not competition and not lifecycle and status != "final":
        return "no-discovery-evidence"
    return "outside-discovery-window"


def build_coverage_report(con, now: dt.datetime | None = None,
                          repaired_this_run: int = 0) -> dict[str, Any]:
    """The brief's required counters, computed directly against the real
    exporter's output. `repaired_this_run` is threaded in by a caller that
    just ran match_repair.repair_matches(write=True) in the same invocation
    (e.g. the `match-repair --write` CLI command); a standalone coverage run
    reports 0, honestly — nothing was repaired THIS run."""
    now = now or dt.datetime.now(dt.timezone.utc)
    payload = export_data.build_public_payload(con)
    exported_ids = {m["id"] for m in payload["matches"]}
    team_ids = {r["id"] for r in con.execute("SELECT id FROM teams")}

    counts: dict[str, int] = {
        "total": 0, "exported": 0, "excluded": 0,
        "missing_competition": 0, "missing_lifecycle": 0,
        "missing_date": 0, "missing_teams": 0,
        "needs_review": 0, "unsupported": 0,
        "repaired_this_run": repaired_this_run,
    }
    excluded: list[dict[str, Any]] = []

    for row in con.execute("SELECT * FROM matches ORDER BY id"):
        counts["total"] += 1
        is_exported = row["id"] in exported_ids
        if is_exported:
            counts["exported"] += 1

        if not _rv(row, "competition_id"):
            counts["missing_competition"] += 1
        if not _rv(row, "lifecycle_status"):
            counts["missing_lifecycle"] += 1
        if not (_rv(row, "date") or _rv(row, "scheduled_at") or _rv(row, "finished_at")):
            counts["missing_date"] += 1
        if _is_unsupported_region(row):
            counts["unsupported"] += 1

        team_a_exists = row["team_a"] in team_ids
        team_b_exists = row["team_b"] in team_ids
        if not (team_a_exists and team_b_exists):
            counts["missing_teams"] += 1

        if not is_exported:
            counts["excluded"] += 1
            reason = _exclusion_reason(row, team_a_exists, team_b_exists)
            if reason == "needs-review":
                counts["needs_review"] += 1
            excluded.append({
                "id": row["id"],
                "reason": reason,
                "fixtureKind": mrepair.classify_fixture_kind(row),
                "status": row["status"],
                "lifecycleStatus": _rv(row, "lifecycle_status"),
                "competitionId": _rv(row, "competition_id"),
            })

    return {
        "generatedAt": now.replace(microsecond=0).isoformat(),
        "counts": counts,
        "excluded": excluded,
    }


def format_coverage_report(report: dict[str, Any]) -> str:
    c = report["counts"]
    lines = [
        f"Match export coverage — {c['total']} match(es) in the content DB:",
        f"  exported            : {c['exported']}",
        f"  excluded            : {c['excluded']}",
        f"  repaired this run   : {c['repaired_this_run']}",
        f"  missing competition : {c['missing_competition']}",
        f"  missing lifecycle   : {c['missing_lifecycle']}",
        f"  missing date        : {c['missing_date']}",
        f"  missing teams       : {c['missing_teams']}",
        f"  needs review        : {c['needs_review']}",
        f"  unsupported region  : {c['unsupported']}",
    ]
    if report["excluded"]:
        lines.append("")
        lines.append("Excluded matches:")
        for e in report["excluded"]:
            lines.append(f"  - {e['id']} [{e['fixtureKind']}]: {e['reason']}")
    return "\n".join(lines)
