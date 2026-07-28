"""
state_machine.py — the explicit automation state machine (Roadmap "Automation
state machine" + Phase A1).

Every tournament, match, broadcast and map moves through named states. The
cardinal rule from the roadmap: *no record disappears when something fails* —
FAILED, RETRY_SCHEDULED and FAILED_PERMANENT are real, terminal-ish nodes, not
deletions. This module only decides whether a transition is LEGAL; the job
store performs it and records the attempt history.

The graph is intentionally permissive toward failure/retry from any active
state (anything can fail; a failure can be rescheduled) but strict about the
forward happy path so a bug can't skip review and jump straight to PUBLISHED.
"""
from __future__ import annotations

# Canonical states (mirrors the roadmap's list, plus FAILED_PERMANENT from J2).
DISCOVERED = "DISCOVERED"
SCHEDULED = "SCHEDULED"
AWAITING_BROADCAST = "AWAITING_BROADCAST"
LIVE = "LIVE"
RECORDING = "RECORDING"
ARCHIVED = "ARCHIVED"
DOWNLOADING = "DOWNLOADING"
DOWNLOADED = "DOWNLOADED"
SEGMENTING = "SEGMENTING"
PROCESSING = "PROCESSING"
NEEDS_LAYOUT = "NEEDS_LAYOUT"
NEEDS_TEMPLATES = "NEEDS_TEMPLATES"
NEEDS_REVIEW = "NEEDS_REVIEW"
READY_FOR_DETECTION = "READY_FOR_DETECTION"
APPROVED = "APPROVED"
PUBLISHED = "PUBLISHED"
PARTIAL = "PARTIAL"
FAILED = "FAILED"
RETRY_SCHEDULED = "RETRY_SCHEDULED"
FAILED_PERMANENT = "FAILED_PERMANENT"
IGNORED = "IGNORED"
CANCELLED = "CANCELLED"

# Beta-sprint aliases: the closed-loop spec (see docs/AUTOMATION.md "Phase
# E/F/G/I") names states DISCOVERED/BROADCAST_LINKED/READY/.../RETRYABLE.
# Rather than run a second vocabulary alongside this one, the spec's states
# map onto the ones already established here (ARCHIVED == a linked official
# VOD ready to claim/download i.e. "READY"; RETRY_SCHEDULED == "RETRYABLE").
# Only three genuinely new nodes were missing and are added above:
# DOWNLOADING (visible in-progress download, resumable after a crash),
# READY_FOR_DETECTION (a segment has been human-approved and is queued for
# automatic detection), and CANCELLED (an explicit operator cancel, distinct
# from IGNORED which means "the system decided not to pursue this").

ALL_STATES = frozenset({
    DISCOVERED, SCHEDULED, AWAITING_BROADCAST, LIVE, RECORDING, ARCHIVED,
    DOWNLOADING, DOWNLOADED, SEGMENTING, PROCESSING, NEEDS_LAYOUT,
    NEEDS_TEMPLATES, NEEDS_REVIEW, READY_FOR_DETECTION, APPROVED, PUBLISHED,
    PARTIAL, FAILED, RETRY_SCHEDULED, FAILED_PERMANENT, IGNORED, CANCELLED,
})

# States that mean "this job is done and should not be picked up again".
TERMINAL_STATES = frozenset({PUBLISHED, FAILED_PERMANENT, IGNORED, CANCELLED})

# States a claimable worker may pick up (open work or a scheduled retry).
CLAIMABLE_STATES = frozenset({
    DISCOVERED, SCHEDULED, AWAITING_BROADCAST, ARCHIVED, DOWNLOADING,
    DOWNLOADED, SEGMENTING, PROCESSING, READY_FOR_DETECTION, APPROVED,
    RETRY_SCHEDULED,
})

# Forward happy-path edges. Failure/retry/cancel edges are added below for
# every non-terminal state so we don't have to repeat them by hand.
_FORWARD: dict[str, set[str]] = {
    DISCOVERED: {SCHEDULED, IGNORED},
    SCHEDULED: {AWAITING_BROADCAST, LIVE, ARCHIVED, IGNORED},
    AWAITING_BROADCAST: {LIVE, RECORDING, ARCHIVED},
    LIVE: {RECORDING, ARCHIVED},
    RECORDING: {ARCHIVED, DOWNLOADED, PARTIAL},
    ARCHIVED: {DOWNLOADING},
    DOWNLOADING: {DOWNLOADED, PARTIAL},
    # Layout resolution (Phase 3) runs immediately after download, BEFORE
    # segmentation — it is what tells segmentation where the HUD is. So a
    # freshly-downloaded job can legitimately land in NEEDS_LAYOUT (a new
    # broadcast package was calibrated and awaits approval, or calibration
    # was refused) without ever having segmented anything.
    DOWNLOADED: {SEGMENTING, NEEDS_LAYOUT},
    SEGMENTING: {PROCESSING, NEEDS_LAYOUT, NEEDS_REVIEW, PARTIAL},
    PROCESSING: {NEEDS_LAYOUT, NEEDS_TEMPLATES, NEEDS_REVIEW, APPROVED, PARTIAL},
    NEEDS_LAYOUT: {PROCESSING, NEEDS_REVIEW},
    NEEDS_TEMPLATES: {PROCESSING, NEEDS_REVIEW},
    # A segment-review approval routes to READY_FOR_DETECTION (the segment is
    # ready for automatic detection); a composition/detection-review approval
    # routes straight to APPROVED (ready to publish). Both are legal exits of
    # the one generic review state — review_tasks.kind tells them apart.
    NEEDS_REVIEW: {APPROVED, READY_FOR_DETECTION, PARTIAL, IGNORED},
    READY_FOR_DETECTION: {PROCESSING},
    APPROVED: {PUBLISHED},
    PARTIAL: {PROCESSING, NEEDS_REVIEW, PUBLISHED},
    PUBLISHED: set(),
    FAILED_PERMANENT: set(),
    IGNORED: set(),
    CANCELLED: set(),
}


def _build_transitions() -> dict[str, frozenset[str]]:
    graph: dict[str, set[str]] = {s: set(_FORWARD.get(s, set())) for s in ALL_STATES}
    for state in ALL_STATES:
        if state in TERMINAL_STATES:
            continue
        # Anything active can fail, and a failure can be rescheduled or given up.
        graph[state].add(FAILED)
        # Anything active can be explicitly cancelled by an operator — distinct
        # from IGNORED (the system's own "not pursuing this" verdict).
        graph[state].add(CANCELLED)
    # Failure lifecycle.
    graph[FAILED] |= {RETRY_SCHEDULED, FAILED_PERMANENT, IGNORED, CANCELLED}
    # A rescheduled retry re-enters the pipeline from the front of active work.
    graph[RETRY_SCHEDULED] |= {
        DISCOVERED, SCHEDULED, AWAITING_BROADCAST, RECORDING, ARCHIVED,
        DOWNLOADING, DOWNLOADED, SEGMENTING, PROCESSING, READY_FOR_DETECTION,
        FAILED, FAILED_PERMANENT, IGNORED,
    }
    return {s: frozenset(t) for s, t in graph.items()}


TRANSITIONS: dict[str, frozenset[str]] = _build_transitions()


def is_valid_state(state: str) -> bool:
    return state in ALL_STATES


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def is_claimable(state: str) -> bool:
    return state in CLAIMABLE_STATES


def can_transition(src: str, dst: str) -> bool:
    """True if src -> dst is a legal edge. A no-op (src == dst) is allowed."""
    if src == dst and src in ALL_STATES:
        return True
    return dst in TRANSITIONS.get(src, frozenset())


def assert_transition(src: str, dst: str) -> None:
    if not is_valid_state(src):
        raise ValueError(f"unknown source state: {src!r}")
    if not is_valid_state(dst):
        raise ValueError(f"unknown target state: {dst!r}")
    if not can_transition(src, dst):
        raise ValueError(f"illegal transition {src} -> {dst}")
