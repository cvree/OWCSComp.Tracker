"""Shared DB helpers for the OWCS Comp Tracker pipeline."""
from __future__ import annotations
import os
import sqlite3
import sys


def utf8_stdout() -> None:
    """Make print() safe on Windows consoles (cp1252 by default).

    Pipeline logs contain arrows/ellipses; without this a plain terminal run
    dies with UnicodeEncodeError before the pipeline even starts. Reconfigure
    to UTF-8 with errors='replace' so output NEVER crashes a run. No-op where
    reconfigure is unavailable (very old Pythons / exotic streams)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


utf8_stdout()

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("OWCS_DB", os.path.join(REPO_ROOT, "data", "owcs.sqlite"))
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _add_missing_columns(con: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = _columns(con, table)
    for name, definition in columns.items():
        if name not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def migrate_schema(con: sqlite3.Connection) -> None:
    """Small additive migrations for users who already have an older DB.

    Fresh databases are created from schema.sql. Existing SQLite files get the
    Milestone 1 columns added in place so export/ingest do not crash.
    """
    _add_missing_columns(con, "teams", {
        "faceit_team_id": "TEXT",
        "logo_url": "TEXT",
        "prep_notes": "TEXT",
    })
    _add_missing_columns(con, "matches", {
        "faceit_match_id": "TEXT",
        "faceit_room_url": "TEXT",
        "season": "TEXT",
        "division": "TEXT",
        "round": "TEXT",
        "group_name": "TEXT",
        "scheduled_at": "TEXT",
        "started_at": "TEXT",
        "finished_at": "TEXT",
        "raw_source": "TEXT",
        "prep_notes": "TEXT",
        "updated_at": "TEXT",
    })
    _add_missing_columns(con, "map_results", {
        "score_a": "INTEGER",
        "score_b": "INTEGER",
        "picked_by_team": "TEXT",
        "veto_action": "TEXT",
        "pick_veto": "TEXT",
        "replay_code": "TEXT",
        "replay_expires_note": "TEXT",
        "vod_url": "TEXT",
        "vod_start_seconds": "INTEGER",
        "source": "TEXT",
        "confidence": "REAL",
        "notes": "TEXT",
    })
    _add_missing_columns(con, "hero_bans", {
        "ingest_id": "TEXT",
        "evidence_path": "TEXT",
    })
    _add_missing_columns(con, "ingest_runs", {
        "calibration_health": "TEXT",
        "calibration_status": "TEXT DEFAULT 'ok'",
    })
    con.commit()


def init_schema(con: sqlite3.Connection) -> None:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        con.executescript(f.read())
    migrate_schema(con)
    con.commit()
