"""
job_store.py — the persistent job queue (Roadmap Phase A1/A2/J1).

A thin, well-tested layer over the automation SQLite DB. It guarantees the
three properties the roadmap treats as non-negotiable:

  * Idempotency (A2): enqueue() is keyed on the deterministic job_key, so the
    same logical job is never duplicated no matter how many times discovery
    runs.
  * Nothing is lost on failure: record_attempt() writes a job_attempts row and
    keeps the error code/message, attempt count, timestamps, worker id and
    diagnostic path on the job itself. A job that exhausts its retries moves to
    FAILED_PERMANENT (J2 dead-letter) — still visible, still actionable.
  * Legal state only: every transition goes through state_machine.assert_
    transition, so a bug cannot skip review and publish straight from PROCESSING.

Backoff is data-driven from config.retry_backoff_minutes; the per-kind ceiling
comes from config.max_attempts_for(kind).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from typing import Any, Iterable

from . import models
from . import state_machine as sm
from .config import AutomationConfig, load_config

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
SCHEMA_PATH = os.path.join(_HERE, "schema.sql")
DEFAULT_DB = os.environ.get(
    "OWCS_AUTOMATION_DB", os.path.join(REPO_ROOT, "data", "automation.sqlite")
)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(t: dt.datetime) -> str:
    return t.replace(microsecond=0).isoformat()


class JobStore:
    def __init__(self, db_path: str = DEFAULT_DB, config: AutomationConfig | None = None):
        self.db_path = db_path
        self.config = config or load_config()
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.con = sqlite3.connect(db_path)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        self.init_db()

    # ------------------------------------------------------------------ setup
    def init_db(self) -> None:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            self.con.executescript(f.read())
        self.con.commit()

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "JobStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------- enqueue/get
    def enqueue(
        self,
        kind: str,
        job_key: str,
        *,
        payload: dict[str, Any] | None = None,
        priority: int = 0,
        state: str = sm.DISCOVERED,
        source_url: str | None = None,
        max_attempts: int | None = None,
    ) -> models.Job:
        """Insert a job if new; return the existing one unchanged if the key is
        already known (idempotent — A2). Never duplicates work."""
        if kind not in models.ALL_KINDS:
            raise ValueError(f"unknown job kind: {kind!r}")
        if not sm.is_valid_state(state):
            raise ValueError(f"unknown initial state: {state!r}")
        existing = self.get(job_key)
        if existing is not None:
            return existing
        if max_attempts is None:
            max_attempts = self.config.max_attempts_for(kind)
        now = _iso(_utcnow())
        self.con.execute(
            """INSERT INTO jobs
               (job_key, kind, state, priority, payload, max_attempts,
                source_url, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_key, kind, state, priority,
             json.dumps(payload or {}), max_attempts, source_url, now, now),
        )
        self.con.commit()
        return self.get(job_key)  # type: ignore[return-value]

    def get(self, job_key: str) -> models.Job | None:
        row = self.con.execute(
            "SELECT * FROM jobs WHERE job_key = ?", (job_key,)
        ).fetchone()
        return models.Job.from_row(row) if row else None

    def list_jobs(
        self, *, kind: str | None = None, state: str | None = None,
        limit: int | None = None,
    ) -> list[models.Job]:
        q = "SELECT * FROM jobs"
        clauses, args = [], []
        if kind:
            clauses.append("kind = ?"); args.append(kind)
        if state:
            clauses.append("state = ?"); args.append(state)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY priority DESC, created_at ASC"
        if limit:
            q += f" LIMIT {int(limit)}"
        return [models.Job.from_row(r) for r in self.con.execute(q, args)]

    def counts_by_state(self, kind: str | None = None) -> dict[str, int]:
        q = "SELECT state, COUNT(*) n FROM jobs"
        args: list[Any] = []
        if kind:
            q += " WHERE kind = ?"; args.append(kind)
        q += " GROUP BY state"
        return {r["state"]: r["n"] for r in self.con.execute(q, args)}

    # ------------------------------------------------------------- transitions
    def transition(self, job_key: str, new_state: str, *, allow_noop: bool = True) -> models.Job:
        job = self.get(job_key)
        if job is None:
            raise KeyError(f"no such job: {job_key}")
        if job.state == new_state and allow_noop:
            return job
        sm.assert_transition(job.state, new_state)
        self.con.execute(
            "UPDATE jobs SET state = ?, updated_at = ? WHERE job_key = ?",
            (new_state, _iso(_utcnow()), job_key),
        )
        self.con.commit()
        return self.get(job_key)  # type: ignore[return-value]

    # --------------------------------------------------------------- claiming
    def claim_next(
        self, kinds: Iterable[str], worker_id: str, *, now: dt.datetime | None = None,
    ) -> models.Job | None:
        """Hand the highest-priority ready job to a worker.

        "Ready" = a claimable state, and (if a retry was scheduled) its
        next_retry_at is due. The claimed job is stamped with the worker id so
        the operator dashboard can show who owns it.

        Contract: claiming is advisory. The worker is expected to immediately
        transition the job out of a claimable state (e.g. DISCOVERED ->
        SCHEDULED, ARCHIVED -> DOWNLOADED) and/or take a locks.py lease on the
        underlying resource; until it does, the job remains in the claimable
        pool. This keeps claim_next simple while locks.py provides the hard
        guarantee that two workers never record/process the same broadcast.
        """
        now = now or _utcnow()
        now_iso = _iso(now)
        placeholders = ",".join("?" for _ in kinds)
        kinds = list(kinds)
        if not kinds:
            return None
        states = ",".join("?" for _ in sm.CLAIMABLE_STATES)
        row = self.con.execute(
            f"""SELECT * FROM jobs
                WHERE kind IN ({placeholders})
                  AND state IN ({states})
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY priority DESC, created_at ASC
                LIMIT 1""",
            (*kinds, *sorted(sm.CLAIMABLE_STATES), now_iso),
        ).fetchone()
        if row is None:
            return None
        self.con.execute(
            "UPDATE jobs SET worker_id = ?, last_attempt_at = ?, updated_at = ? WHERE job_key = ?",
            (worker_id, now_iso, now_iso, row["job_key"]),
        )
        self.con.commit()
        return self.get(row["job_key"])

    # ------------------------------------------------------------- attempts
    def record_attempt(
        self,
        job_key: str,
        *,
        ok: bool,
        worker_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        diagnostic_path: str | None = None,
        started_at: str | None = None,
        now: dt.datetime | None = None,
    ) -> models.Job:
        """Log an attempt and advance failure bookkeeping.

        On failure: increments attempts, records the full error context, and
        either schedules a retry (RETRY_SCHEDULED with next_retry_at from the
        backoff table) or, once the per-kind ceiling is hit, moves the job to
        FAILED_PERMANENT (dead-letter, J2) — never deleted.

        Success clears the retry timer and error fields but keeps the attempt
        history row.
        """
        job = self.get(job_key)
        if job is None:
            raise KeyError(f"no such job: {job_key}")
        now = now or _utcnow()
        attempt_no = job.attempts + 1
        self.con.execute(
            """INSERT INTO job_attempts
               (job_key, attempt, worker_id, ok, error_code, error_message,
                diagnostic_path, started_at, finished_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_key, attempt_no, worker_id or job.worker_id, 1 if ok else 0,
             error_code, error_message, diagnostic_path, started_at, _iso(now)),
        )
        if ok:
            self.con.execute(
                """UPDATE jobs SET attempts = ?, last_attempt_at = ?,
                       next_retry_at = NULL, last_error_code = NULL,
                       last_error_message = NULL, diagnostic_path = ?,
                       worker_id = ?, updated_at = ?
                   WHERE job_key = ?""",
                (attempt_no, _iso(now), diagnostic_path or job.diagnostic_path,
                 worker_id or job.worker_id, _iso(now), job_key),
            )
            self.con.commit()
            return self.get(job_key)  # type: ignore[return-value]

        ceiling = job.max_attempts or self.config.max_attempts_for(job.kind)
        if attempt_no >= ceiling:
            new_state = sm.FAILED_PERMANENT
            next_retry = None
        else:
            new_state = sm.RETRY_SCHEDULED
            next_retry = _iso(now + dt.timedelta(minutes=self._backoff_minutes(attempt_no)))
        # Route through FAILED first so the graph stays honest, then settle.
        for target in (sm.FAILED, new_state):
            if sm.can_transition(self.get(job_key).state, target):  # type: ignore[union-attr]
                self.con.execute(
                    "UPDATE jobs SET state = ? WHERE job_key = ?", (target, job_key)
                )
        self.con.execute(
            """UPDATE jobs SET attempts = ?, last_attempt_at = ?, next_retry_at = ?,
                   last_error_code = ?, last_error_message = ?, diagnostic_path = ?,
                   worker_id = ?, updated_at = ?
               WHERE job_key = ?""",
            (attempt_no, _iso(now), next_retry, error_code, error_message,
             diagnostic_path or job.diagnostic_path, worker_id or job.worker_id,
             _iso(now), job_key),
        )
        self.con.commit()
        return self.get(job_key)  # type: ignore[return-value]

    def _backoff_minutes(self, attempt_no: int) -> int:
        table = self.config.retry_backoff_minutes
        idx = min(attempt_no - 1, len(table) - 1)
        return table[max(idx, 0)]

    def attempts_for(self, job_key: str) -> list[sqlite3.Row]:
        return list(self.con.execute(
            "SELECT * FROM job_attempts WHERE job_key = ? ORDER BY attempt ASC",
            (job_key,),
        ))
