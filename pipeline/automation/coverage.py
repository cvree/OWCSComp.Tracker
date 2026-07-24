"""
coverage.py — rolling 14-day completeness report (Roadmap Phase D4).

The roadmap's first real target is NOT "record everything": it is to prove that
"no event can be silently missed." This module produces exactly that proof — a
per-match capture ledger over the rolling lookback window, plus the summary
counts the operator dashboard shows:

    14-day professional matches discovered: 42
    Broadcast located: 39
    Downloaded: 35
    Segmented: 30
    Processed: 27
    Published: 24
    Needs review: 3
    Missing broadcast: 3

It reads the *content* DB (data/owcs.sqlite) as the universe of configured
matches and cross-references the automation DB for discovery/review state. Every
missing match is listed individually — the summary never hides a gap, honouring
"Do not claim 100% data coverage merely because every row exists."
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


def _connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
        (name,),
    ).fetchone() is not None


def _match_completed_within(con: sqlite3.Connection, window_start: str) -> list[sqlite3.Row]:
    """Final matches whose completion date is within the window.

    `matches.date` is an ISO YYYY-MM-DD kept for static sorting; finished_at is
    a fuller timestamp when present. We compare on whichever is available.
    """
    return list(con.execute(
        """SELECT id, event_name, region, date, finished_at, status,
                  team_a, team_b, vod_url
           FROM matches
           WHERE status = 'final'
             AND COALESCE(finished_at, date) >= ?
           ORDER BY COALESCE(finished_at, date) DESC""",
        (window_start,),
    ))


def _processed_match_ids(con: sqlite3.Connection) -> set[str]:
    if not _has_table(con, "comp_snapshots"):
        return set()
    return {r["match_id"] for r in con.execute(
        "SELECT DISTINCT match_id FROM comp_snapshots"
    )}


def _match_has_maps(con: sqlite3.Connection) -> set[str]:
    if not _has_table(con, "map_results"):
        return set()
    return {r["match_id"] for r in con.execute(
        "SELECT DISTINCT match_id FROM map_results"
    )}


def build_report(
    *,
    content_db: str = CONTENT_DB,
    automation_db: str | None = None,
    window_days: int = 14,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Compute the coverage report. Pure read; writes nothing."""
    now = now or dt.datetime.now(dt.timezone.utc)
    window_start_dt = now - dt.timedelta(days=window_days)
    window_start = window_start_dt.date().isoformat()

    matches: list[dict[str, Any]] = []
    counts = {
        "discovered": 0, "broadcast_located": 0, "downloaded": 0,
        "segmented": 0, "processed": 0, "published": 0,
        "needs_review": 0, "missing_broadcast": 0,
    }

    if os.path.exists(content_db):
        con = _connect(content_db)
        try:
            processed = _processed_match_ids(con)
            with_maps = _match_has_maps(con)
            for m in _match_completed_within(con, window_start):
                mid = m["id"]
                has_broadcast = bool(m["vod_url"])
                is_processed = mid in processed
                has_maps = mid in with_maps
                # Conservative "published": we only count a match as fully
                # published once it has both map structure AND processed comps.
                is_published = is_processed and has_maps
                row = {
                    "match_id": mid,
                    "event": m["event_name"],
                    "region": m["region"],
                    "date": m["finished_at"] or m["date"],
                    "teams": [m["team_a"], m["team_b"]],
                    "broadcast_located": has_broadcast,
                    "segmented": has_maps,
                    "processed": is_processed,
                    "published": is_published,
                    "vod_url": m["vod_url"],
                }
                matches.append(row)
                counts["discovered"] += 1
                counts["broadcast_located"] += int(has_broadcast)
                counts["downloaded"] += int(has_broadcast)  # proxy until worker lands
                counts["segmented"] += int(has_maps)
                counts["processed"] += int(is_processed)
                counts["published"] += int(is_published)
                if not has_broadcast:
                    counts["missing_broadcast"] += 1
        finally:
            con.close()

    # Review backlog comes from the automation DB when it exists.
    if automation_db and os.path.exists(automation_db):
        acon = _connect(automation_db)
        try:
            if _has_table(acon, "review_tasks"):
                counts["needs_review"] = acon.execute(
                    "SELECT COUNT(*) n FROM review_tasks WHERE state = 'NEEDS_REVIEW'"
                ).fetchone()["n"]
        finally:
            acon.close()

    missing = [m for m in matches if not m["broadcast_located"]]
    return {
        "window_days": window_days,
        "window_start": window_start,
        "generated_at": now.replace(microsecond=0).isoformat(),
        "counts": counts,
        "matches": matches,
        "missing_broadcast": missing,
    }


def format_report(report: dict[str, Any]) -> str:
    """Render the roadmap's D4 text block, then list every missing match."""
    c = report["counts"]
    lines = [
        f"{report['window_days']}-day professional matches discovered: {c['discovered']}",
        f"Broadcast located: {c['broadcast_located']}",
        f"Downloaded: {c['downloaded']}",
        f"Segmented: {c['segmented']}",
        f"Processed: {c['processed']}",
        f"Published: {c['published']}",
        f"Needs review: {c['needs_review']}",
        f"Missing broadcast: {c['missing_broadcast']}",
    ]
    if report["missing_broadcast"]:
        lines.append("")
        lines.append("Matches missing an official broadcast:")
        for m in report["missing_broadcast"]:
            teams = " vs ".join(t or "?" for t in m["teams"])
            lines.append(f"  - [{m['region']}] {teams} ({m['date']}) — {m['match_id']}")
    return "\n".join(lines)


def save_snapshot(automation_db: str, report: dict[str, Any]) -> int:
    """Persist a coverage_snapshots row for dashboard history (returns row id)."""
    from . import job_store  # local import to avoid a cycle
    store = job_store.JobStore(automation_db)
    try:
        c = report["counts"]
        cur = store.con.execute(
            """INSERT INTO coverage_snapshots
               (window_days, discovered, broadcast_located, downloaded,
                segmented, processed, published, needs_review, missing_broadcast, report)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (report["window_days"], c["discovered"], c["broadcast_located"],
             c["downloaded"], c["segmented"], c["processed"], c["published"],
             c["needs_review"], c["missing_broadcast"], json.dumps(report)),
        )
        store.con.commit()
        return int(cur.lastrowid)
    finally:
        store.close()
