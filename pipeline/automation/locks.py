"""
locks.py — database lease locks with heartbeats (Roadmap Phase A3).

"Prevent two workers from recording or processing the same broadcast." A lock
is a row in `locks` keyed by a resource string (e.g. record:<video-id>). The
holder must heartbeat within the lease TTL; if it crashes, the lease expires
and another worker can safely steal it — so a dead worker never wedges a
resource forever (safe job recovery after crashes).

All time comparisons use ISO-8601 UTC strings, which sort lexicographically,
so expiry checks work in plain SQL without a clock function.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(t: dt.datetime) -> str:
    return t.replace(microsecond=0).isoformat()


@dataclass
class Lease:
    resource: str
    worker_id: str
    acquired_at: str
    heartbeat_at: str
    expires_at: str


class LockManager:
    def __init__(self, con: sqlite3.Connection, lease_seconds: int = 300):
        self.con = con
        self.lease_seconds = lease_seconds

    def acquire(
        self, resource: str, worker_id: str, *,
        lease_seconds: int | None = None, now: dt.datetime | None = None,
    ) -> bool:
        """Try to take the lease. Succeeds if the resource is free, already
        held by this same worker (re-entrant refresh), or held by someone whose
        lease has expired (steal). Returns True on success."""
        ttl = lease_seconds or self.lease_seconds
        now = now or _utcnow()
        now_iso = _iso(now)
        expires = _iso(now + dt.timedelta(seconds=ttl))
        row = self.con.execute(
            "SELECT worker_id, expires_at FROM locks WHERE resource = ?", (resource,)
        ).fetchone()
        if row is None:
            self.con.execute(
                """INSERT INTO locks (resource, worker_id, acquired_at, heartbeat_at, expires_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (resource, worker_id, now_iso, now_iso, expires),
            )
            self.con.commit()
            return True
        holder, holder_expires = row["worker_id"], row["expires_at"]
        if holder == worker_id or holder_expires <= now_iso:
            # Ours to refresh, or the previous holder's lease has expired.
            self.con.execute(
                """UPDATE locks SET worker_id = ?, acquired_at = ?,
                       heartbeat_at = ?, expires_at = ? WHERE resource = ?""",
                (worker_id, now_iso, now_iso, expires, resource),
            )
            self.con.commit()
            return True
        return False

    def heartbeat(
        self, resource: str, worker_id: str, *,
        lease_seconds: int | None = None, now: dt.datetime | None = None,
    ) -> bool:
        """Extend the lease. Only the current holder may heartbeat; returns
        False (without touching the row) if someone else owns it now."""
        ttl = lease_seconds or self.lease_seconds
        now = now or _utcnow()
        now_iso = _iso(now)
        expires = _iso(now + dt.timedelta(seconds=ttl))
        cur = self.con.execute(
            """UPDATE locks SET heartbeat_at = ?, expires_at = ?
               WHERE resource = ? AND worker_id = ?""",
            (now_iso, expires, resource, worker_id),
        )
        self.con.commit()
        return cur.rowcount > 0

    def release(self, resource: str, worker_id: str) -> bool:
        """Release the lease if held by this worker."""
        cur = self.con.execute(
            "DELETE FROM locks WHERE resource = ? AND worker_id = ?",
            (resource, worker_id),
        )
        self.con.commit()
        return cur.rowcount > 0

    def holder(self, resource: str, *, now: dt.datetime | None = None) -> Lease | None:
        """Return the live lease for a resource, or None if free/expired."""
        now = now or _utcnow()
        row = self.con.execute(
            "SELECT * FROM locks WHERE resource = ?", (resource,)
        ).fetchone()
        if row is None or row["expires_at"] <= _iso(now):
            return None
        return Lease(
            resource=row["resource"], worker_id=row["worker_id"],
            acquired_at=row["acquired_at"], heartbeat_at=row["heartbeat_at"],
            expires_at=row["expires_at"],
        )

    def clear_expired(self, *, now: dt.datetime | None = None) -> int:
        """Delete expired leases; returns how many were reaped."""
        now = now or _utcnow()
        cur = self.con.execute(
            "DELETE FROM locks WHERE expires_at <= ?", (_iso(now),)
        )
        self.con.commit()
        return cur.rowcount
