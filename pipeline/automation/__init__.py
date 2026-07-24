"""
OWCS Comp Tracker — automation package (Roadmap Phase A foundation).

A persistent, idempotent job/state layer that sits BESIDE the existing content
pipeline (it never writes hero comps). It provides the spine the rest of the
automation roadmap hangs off:

    config          operator config (automation.yml) + curated registries
    models          deterministic job identity + value types (idempotency)
    state_machine   the explicit DISCOVERED..PUBLISHED/FAILED state graph
    job_store       persistent queue: enqueue/claim/transition/retry, no loss
    locks           database lease locks with heartbeats (no double-record)
    coverage        rolling 14-day completeness report (prove nothing is missed)

Nothing here records video or downloads a VOD; that belongs to the self-hosted
worker described in the roadmap's "Following pass". This layer makes such work
trackable, resumable and safe to run twice.
"""
from __future__ import annotations

from . import config, coverage, job_store, locks, models, state_machine

__all__ = [
    "config", "coverage", "job_store", "locks", "models", "state_machine",
]
