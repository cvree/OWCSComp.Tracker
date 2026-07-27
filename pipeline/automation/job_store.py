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
        self._migrate()
        self.con.commit()

    def _migrate(self) -> None:
        """Small additive migrations (mirrors pipeline/db.py's own pattern) —
        `map_segments` (Phase F) shipped schema-only with no writers; these
        are the fields a real assisted-segmentation workflow needs. The
        automation DB is gitignored/regenerable, but existing() guards keep
        `init_db()` safe to run against an already-migrated file too."""
        existing = {row["name"] for row in
                   self.con.execute("PRAGMA table_info(map_segments)")}
        additions = {
            "updated_at": "TEXT",
            "duration_seconds": "REAL",
            "map_name": "TEXT",
            "map_mode": "TEXT",
            "team_a": "TEXT",
            "team_b": "TEXT",
            "side_assignment": "TEXT",
            "layout_id": "TEXT",
            "reviewer_note": "TEXT",
            "source_job_key": "TEXT",
            "extracted_path": "TEXT",
            "extracted_hash": "TEXT",
            "extracted_width": "INTEGER",
            "extracted_height": "INTEGER",
            # Phase 3/4: reviewer-facing evidence and automatic identity
            # proposals, kept beside the human's confirmed values so an
            # operator can always see what the machine proposed vs what was
            # accepted (`proposals` is never read as truth by publication).
            "thumbnails": "TEXT",
            "proposals": "TEXT",
            "identity_status": "TEXT",
        }
        for name, decl in additions.items():
            if name not in existing:
                self.con.execute(f"ALTER TABLE map_segments ADD COLUMN {name} {decl}")

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

    # ------------------------------------------------------- payload/worker
    def update_payload(self, job_key: str, patch: dict[str, Any]) -> models.Job:
        """Merge `patch` into the job's JSON payload (shallow update — a key
        in `patch` overwrites the same key in the stored payload, everything
        else is kept). This is how a worker records download/segmentation/
        detection metadata onto the job without a schema migration per field.
        """
        job = self.get(job_key)
        if job is None:
            raise KeyError(f"no such job: {job_key}")
        merged = dict(job.payload)
        merged.update(patch)
        self.con.execute(
            "UPDATE jobs SET payload = ?, updated_at = ? WHERE job_key = ?",
            (json.dumps(merged), _iso(_utcnow()), job_key),
        )
        self.con.commit()
        return self.get(job_key)  # type: ignore[return-value]

    def clear_worker(self, job_key: str) -> models.Job:
        """Release a job back to the claimable pool (the "Release job"
        operator action). Does not touch state or the job's lock row — call
        `locks.LockManager.release` separately for the underlying resource."""
        job = self.get(job_key)
        if job is None:
            raise KeyError(f"no such job: {job_key}")
        self.con.execute(
            "UPDATE jobs SET worker_id = NULL, updated_at = ? WHERE job_key = ?",
            (_iso(_utcnow()), job_key),
        )
        self.con.commit()
        return self.get(job_key)  # type: ignore[return-value]

    def record_error(self, job_key: str, *, error_code: str,
                     error_message: str) -> models.Job:
        """Record a same-state validation/gate rejection (e.g. a publish
        precondition refusal) WITHOUT touching state, attempts, or the retry
        timer. Distinct from `record_attempt`: that method drives the
        worker retry-with-backoff lifecycle (download/detection failures,
        which legitimately move a job to RETRY_SCHEDULED/FAILED_PERMANENT).
        A publish refusal is a human-fixable gate a job can simply be
        re-submitted against from the SAME state — nothing here should ever
        strand an APPROVED job somewhere RETRY_SCHEDULED can't return it to.
        """
        job = self.get(job_key)
        if job is None:
            raise KeyError(f"no such job: {job_key}")
        self.con.execute(
            """UPDATE jobs SET last_error_code = ?, last_error_message = ?,
                   updated_at = ? WHERE job_key = ?""",
            (error_code, error_message, _iso(_utcnow()), job_key),
        )
        self.con.commit()
        return self.get(job_key)  # type: ignore[return-value]

    def cancel(self, job_key: str, *, reason: str | None = None) -> models.Job:
        """Explicit operator cancel (distinct from IGNORED, the system's own
        "not pursuing this" verdict). Legal from any non-terminal state; a
        no-op if the job is already CANCELLED. Never deletes the row."""
        job = self.get(job_key)
        if job is None:
            raise KeyError(f"no such job: {job_key}")
        if job.state == sm.CANCELLED:
            return job
        sm.assert_transition(job.state, sm.CANCELLED)
        now = _iso(_utcnow())
        self.con.execute(
            """UPDATE jobs SET state = ?, last_error_code = ?,
                   last_error_message = ?, updated_at = ? WHERE job_key = ?""",
            (sm.CANCELLED, "cancelled", reason or "cancelled by operator",
             now, job_key),
        )
        self.con.commit()
        return self.get(job_key)  # type: ignore[return-value]

    def retry_job(self, job_key: str, *, force: bool = False,
                  now: dt.datetime | None = None) -> models.Job:
        """The "Retry failed job" operator action.

        - RETRY_SCHEDULED: expedites the existing backoff timer to now, so
          claim_next picks it up immediately instead of waiting.
        - FAILED: advances it into RETRY_SCHEDULED (a legal edge already) with
          next_retry_at = now.
        - FAILED_PERMANENT: refuses by default (it is a deliberate dead
          letter — "still visible, still actionable", not silently revived).
          `force=True` is the one explicit operator override: it does NOT
          bypass the graph silently — it re-enters through RETRY_SCHEDULED
          (a state every kind already knows how to resume from) and keeps
          the full attempt history untouched. Any other state raises, since
          "retry" only makes sense for a job that has actually failed.
        """
        job = self.get(job_key)
        if job is None:
            raise KeyError(f"no such job: {job_key}")
        now = now or _utcnow()
        now_iso = _iso(now)
        if job.state == sm.RETRY_SCHEDULED:
            self.con.execute(
                "UPDATE jobs SET next_retry_at = ?, updated_at = ? WHERE job_key = ?",
                (now_iso, now_iso, job_key),
            )
            self.con.commit()
            return self.get(job_key)  # type: ignore[return-value]
        if job.state == sm.FAILED:
            sm.assert_transition(job.state, sm.RETRY_SCHEDULED)
            self.con.execute(
                """UPDATE jobs SET state = ?, next_retry_at = ?, updated_at = ?
                       WHERE job_key = ?""",
                (sm.RETRY_SCHEDULED, now_iso, now_iso, job_key),
            )
            self.con.commit()
            return self.get(job_key)  # type: ignore[return-value]
        if job.state == sm.FAILED_PERMANENT:
            if not force:
                raise ValueError(
                    f"{job_key} is FAILED_PERMANENT (dead-lettered after "
                    f"{job.attempts} attempt(s): {job.last_error_message}). "
                    "Pass force=True to explicitly re-open it.")
            self.con.execute(
                """UPDATE jobs SET state = ?, next_retry_at = ?, updated_at = ?
                       WHERE job_key = ?""",
                (sm.RETRY_SCHEDULED, now_iso, now_iso, job_key),
            )
            self.con.commit()
            return self.get(job_key)  # type: ignore[return-value]
        raise ValueError(
            f"{job_key} is {job.state}, not a failed/retryable state")
