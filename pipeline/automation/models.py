"""
models.py — value types + deterministic job identity (Roadmap Phase A2).

Idempotency is the whole point: "Running the system twice must not duplicate
matches, maps, snapshots, evidence, recording jobs or publication commits."
Every job therefore has a deterministic key built ONLY from stable inputs, so
the same logical work always maps to the same `jobs.job_key`.

Key grammar (from the roadmap's examples):
    calendar:<source>:<external-event-id>
    match:<faceit-match-id>
    broadcast:<youtube-video-id>
    record:<youtube-video-id>:<quality>
    process:<video-id>:<layout-version>
    map:<match-id>:<map-order>
    publish:<database-hash>
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Job kinds correspond to the queues the worker drains
# (--queues discovery,recording,processing ...).
KIND_CALENDAR = "calendar"
KIND_DISCOVERY = "discovery"
KIND_BROADCAST = "broadcast"
KIND_RECORD = "record"
KIND_PROCESS = "process"
KIND_SEGMENT = "segment"
KIND_PUBLISH = "publish"

ALL_KINDS = frozenset({
    KIND_CALENDAR, KIND_DISCOVERY, KIND_BROADCAST, KIND_RECORD,
    KIND_PROCESS, KIND_SEGMENT, KIND_PUBLISH,
})

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(value: Any) -> str:
    """Lowercase, collapse non-alphanumerics to '-', strip edges.

    Keeps job keys stable and shell/URL-safe regardless of how a source
    formats a name. An empty/None input becomes 'unknown' so a key is never
    silently truncated to a bare prefix (which could collide across matches).
    """
    s = _SLUG_RE.sub("-", str(value or "").strip().lower()).strip("-")
    return s or "unknown"


# --- Deterministic key builders (Phase A2) ---------------------------------
def calendar_key(source: str, external_event_id: str) -> str:
    return f"calendar:{slug(source)}:{slug(external_event_id)}"


def match_key(faceit_match_id: str) -> str:
    return f"match:{slug(faceit_match_id)}"


def broadcast_key(video_id: str) -> str:
    return f"broadcast:{slug(video_id)}"


def broadcast_discovery_key(channel_id: str, window_start: str, window_end: str) -> str:
    """One key per (channel, rolling window) scan — Phase C5. Reusing the
    same window twice (e.g. an hourly rerun before the window has moved)
    must not re-enqueue duplicate scan jobs."""
    return f"broadcast-discovery:{slug(channel_id)}:{slug(window_start)}:{slug(window_end)}"


def broadcast_match_link_key(video_id: str, match_id: str) -> str:
    """One key per (video, match) proposed link — Phase C5. A single
    full-day broadcast can legitimately link to many matches, and a single
    match can have candidate links from several videos; the pair is what
    must stay unique."""
    return f"broadcast-match-link:{slug(video_id)}:{slug(match_id)}"


def record_key(video_id: str, quality: str = "source") -> str:
    return f"record:{slug(video_id)}:{slug(quality)}"


def process_key(video_id: str, layout_version: str) -> str:
    return f"process:{slug(video_id)}:{slug(layout_version)}"


def map_key(match_id: str, map_order: int | str) -> str:
    return f"map:{slug(match_id)}:{slug(map_order)}"


def publish_key(database_hash: str) -> str:
    return f"publish:{slug(database_hash)}"


@dataclass
class Job:
    """In-memory view of a `jobs` row. Failure fields are first-class so a
    failed job carries everything an operator needs, exactly as the roadmap
    requires (error code/message, attempts, timestamps, worker, source, diag).
    """
    job_key: str
    kind: str
    state: str
    priority: int = 0
    payload: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    max_attempts: int | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    last_attempt_at: str | None = None
    next_retry_at: str | None = None
    worker_id: str | None = None
    source_url: str | None = None
    diagnostic_path: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "Job":
        import json
        payload = row["payload"]
        try:
            payload = json.loads(payload) if payload else {}
        except (ValueError, TypeError):
            payload = {}
        return cls(
            job_key=row["job_key"],
            kind=row["kind"],
            state=row["state"],
            priority=row["priority"],
            payload=payload,
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            last_error_code=row["last_error_code"],
            last_error_message=row["last_error_message"],
            last_attempt_at=row["last_attempt_at"],
            next_retry_at=row["next_retry_at"],
            worker_id=row["worker_id"],
            source_url=row["source_url"],
            diagnostic_path=row["diagnostic_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
