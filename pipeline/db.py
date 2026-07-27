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
        # Team profile enrichment (Phase D team facts).
        "description": "TEXT",
        "website": "TEXT",
        "twitter": "TEXT",
        "facebook": "TEXT",
        "member_count": "INTEGER",
        "avatar_source_url": "TEXT",
        "faceit_enriched_at": "TEXT",
        # Canonical team registry (Phase D2).
        "aliases": "TEXT",
        "previous_names": "TEXT",
        "organization": "TEXT",
        "status": "TEXT NOT NULL DEFAULT 'active'",
        "effective_start": "TEXT",
        "effective_end": "TEXT",
        "source_authority": "TEXT",
        "identity_verified_at": "TEXT",
        "roster_source": "TEXT",
        "roster_verified_at": "TEXT",
        "needs_review": "INTEGER NOT NULL DEFAULT 0",
        "review_reason": "TEXT",
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
        # Phase B discovery: precise FACEIT lifecycle (scheduled/live/finished/
        # cancelled/forfeit/aborted) plus a coarse capture state and the
        # source competition id. `status` stays within its CHECK set; these
        # carry the finer facts the public calendar renders.
        "lifecycle_status": "TEXT",
        "capture_status": "TEXT",
        "competition_id": "TEXT",
        # Phase D2.1 match-export repair.
        "fixture_kind": "TEXT",
        "lifecycle_source": "TEXT",
        "lifecycle_repaired_at": "TEXT",
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
    _widen_ingest_findings(con)
    con.commit()


# Kinds/statuses schema.sql's CHECK constraints must allow. Kept here as the
# single list the migration below compares against, so adding one is a
# two-line change in two places that cannot drift apart silently (the
# assertion in pipeline/test_ingest_findings_migration.py enforces it).
FINDING_KINDS = ("team_identity", "ban_candidate", "event_metadata",
                 "calibration_health", "segment_identity", "map_identity",
                 "player_identity", "score_candidate", "winner_candidate",
                 "series_candidate")
FINDING_STATUSES = ("candidate", "confirmed", "rejected", "unknown", "proposed")


def _widen_ingest_findings(con: sqlite3.Connection) -> None:
    """Rebuild `ingest_findings` when its CHECK constraints predate the Phase
    4/6 finding kinds.

    SQLite cannot ALTER a CHECK constraint, so an existing database has to be
    migrated by rebuild: create the new table, copy every row across, swap.
    Done inside one transaction and only when needed, so it is safe to run on
    every connect (which `init_schema` does) and is a no-op on a fresh DB
    created straight from schema.sql.

    No row is dropped or rewritten — this widens what is ALLOWED, it never
    reinterprets existing data.
    """
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ingest_findings'"
    ).fetchone()
    if row is None:
        return                                  # table not created yet
    ddl = row["sql"] if isinstance(row, sqlite3.Row) else row[0]
    if all(k in ddl for k in ("segment_identity", "score_candidate")) \
            and "'unknown'" in ddl:
        return                                  # already migrated
    cols = [r["name"] for r in con.execute("PRAGMA table_info(ingest_findings)")]
    kinds = ",".join(f"'{k}'" for k in FINDING_KINDS)
    statuses = ",".join(f"'{s}'" for s in FINDING_STATUSES)
    con.executescript(f"""
        PRAGMA foreign_keys = OFF;
        BEGIN;
        CREATE TABLE ingest_findings__new (
          id          INTEGER PRIMARY KEY AUTOINCREMENT,
          ingest_id   TEXT NOT NULL REFERENCES ingest_runs(id) ON DELETE CASCADE,
          kind        TEXT NOT NULL CHECK (kind IN ({kinds})),
          field       TEXT,
          raw_text    TEXT,
          value       TEXT,
          confidence  REAL,
          method      TEXT,
          evidence_path TEXT,
          status      TEXT NOT NULL DEFAULT 'candidate'
                      CHECK (status IN ({statuses})),
          notes       TEXT,
          created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO ingest_findings__new ({','.join(cols)})
          SELECT {','.join(cols)} FROM ingest_findings;
        DROP TABLE ingest_findings;
        ALTER TABLE ingest_findings__new RENAME TO ingest_findings;
        CREATE INDEX IF NOT EXISTS idx_findings_ingest
          ON ingest_findings(ingest_id);
        COMMIT;
        PRAGMA foreign_keys = ON;
    """)


def init_schema(con: sqlite3.Connection) -> None:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        con.executescript(f.read())
    migrate_schema(con)
    con.commit()
