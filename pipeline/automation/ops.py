"""
ops.py — the closed-loop job's operator surface (Roadmap Phase 1 required
behaviors + the Phase 7 dashboard's data source).

One job travels the WHOLE loop end to end (DISCOVERED/ARCHIVED "ready" ->
DOWNLOADING -> DOWNLOADED -> SEGMENTING -> NEEDS_REVIEW -> READY_FOR_DETECTION
-> PROCESSING -> NEEDS_REVIEW -> APPROVED -> PUBLISHED), rather than a
separate job per phase — this module is what a control room or CLI drives
that job through. It never writes hero compositions itself: every state
change here either delegates to worker.py/segmentation.py/detection_runner.py/
publish.py (which own the actual gates) or is a pure bookkeeping action
(claim/release/retry/cancel/reset-lock) job_store.py/locks.py already
provide safely.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_DIR = os.path.dirname(_HERE)
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

# `capture` (pipeline/capture.py) and `segmentation` transitively import cv2.
# NEVER import them at module level — only `run_one_job`'s DOWNLOADED/
# READY_FOR_DETECTION/APPROVED branches actually need them, imported lazily
# right there, so merely `import ops` (and every non-segmentation job
# action: create/list/show/claim/release/retry/cancel/reset-lock) stays
# runnable with no OpenCV installed.

from . import detection_runner as dr
from . import job_store as js
from . import locks as lk
from . import models
from . import state_machine as sm
from . import worker


# ------------------------------------------------------------ job creation
def create_job_from_broadcast(store: js.JobStore, *, match_id: str,
                              video_id: str, source_url: str, channel_id: str,
                              team_a: str, team_b: str,
                              tournament_id: str | None = None,
                              region: str | None = None,
                              language: str | None = None,
                              broadcast_authority: str | None = None,
                              scheduled_at: str | None = None,
                              expected_layout_id: str | None = None,
                              priority: int = 0) -> models.Job:
    """"Create job from a matched broadcast" (Phase 1). One match + one
    approved official broadcast source = one job, keyed on the video id
    (`models.record_key`) so calling this twice for the same broadcast is a
    pure no-op — never a duplicate. Starts in ARCHIVED ("official broadcast
    linked, download not yet started" — this sprint's "READY" state)."""
    payload = {
        "matchId": match_id, "tournamentId": tournament_id, "region": region,
        "language": language, "teamA": team_a, "teamB": team_b,
        "videoId": video_id, "sourceUrl": source_url, "channelId": channel_id,
        "broadcastAuthority": broadcast_authority, "scheduledAt": scheduled_at,
        "expectedLayoutId": expected_layout_id,
    }
    job_key = models.record_key(video_id)
    return store.enqueue(models.KIND_RECORD, job_key, payload=payload,
                         priority=priority, state=sm.ARCHIVED,
                         source_url=source_url)


# --------------------------------------------------------------- thin ops
def list_jobs(store: js.JobStore, *, state: str | None = None) -> list[models.Job]:
    return store.list_jobs(state=state)


def show_job(store: js.JobStore, job_key: str) -> models.Job | None:
    return store.get(job_key)


def claim_next_job(store: js.JobStore, lock_mgr: lk.LockManager,
                   worker_id: str, *,
                   kinds: tuple[str, ...] = (models.KIND_RECORD, models.KIND_PROCESS)
                   ) -> models.Job | None:
    return worker.claim_and_lock(store, lock_mgr, list(kinds), worker_id)


def release_job(store: js.JobStore, lock_mgr: lk.LockManager,
                job_key: str, worker_id: str) -> models.Job:
    job = store.get(job_key)
    if job is None:
        raise KeyError(f"no such job: {job_key}")
    lock_mgr.release(worker.resource_for(job), worker_id)
    return store.clear_worker(job_key)


def retry_job(store: js.JobStore, job_key: str, *, force: bool = False
              ) -> models.Job:
    """Retry a failed job AND restore it to the stage it can actually run
    from.

    `store.retry_job` only clears the backoff timer, leaving the job in
    RETRY_SCHEDULED — a state no automatic step knows how to advance, so
    the loop dead-ended after every retry. Here the job is additionally
    moved back to the `resumeState` recorded when it failed (default:
    ARCHIVED, the front of the automatic work).

    The audited human decisions are NOT re-opened: source approval lives on
    the payload, not in the state, so restoring the download stage never
    asks for a second approval — and this refuses to rewind a job whose
    source is not (still) approved.
    """
    job = store.retry_job(job_key, force=force)
    return resume_after_retry(store, job.job_key)


def resume_after_retry(store: js.JobStore, job_key: str) -> models.Job:
    """Move a RETRY_SCHEDULED job to its recorded resume stage.

    A no-op for any other state. Returns the job either way.
    """
    from . import link_intake as li
    job = store.get(job_key)
    if job is None:
        raise KeyError(f"no such job: {job_key}")
    if job.state != sm.RETRY_SCHEDULED:
        return job
    source = job.payload.get("source") or {}
    if source.get("state") != li.SOURCE_APPROVED:
        # Never hand an unapproved source back to the downloader.
        return job
    target = job.payload.get("resumeState") or sm.ARCHIVED
    if not sm.can_transition(job.state, target):
        target = sm.ARCHIVED
    if not sm.can_transition(job.state, target):
        return job
    return store.transition(job_key, target)


def cancel_job(store: js.JobStore, lock_mgr: lk.LockManager, job_key: str, *,
              reason: str | None = None) -> models.Job:
    job = store.get(job_key)
    if job is None:
        raise KeyError(f"no such job: {job_key}")
    lock_mgr.reset_stale(worker.resource_for(job))  # best-effort; never forces a live lock
    return store.cancel(job_key, reason=reason)


def reset_stale_lock(store: js.JobStore, lock_mgr: lk.LockManager,
                     job_key: str) -> bool:
    job = store.get(job_key)
    if job is None:
        raise KeyError(f"no such job: {job_key}")
    cleared = lock_mgr.reset_stale(worker.resource_for(job))
    if cleared:
        store.clear_worker(job_key)
    return cleared


def resume_interrupted_job(store: js.JobStore, lock_mgr: lk.LockManager, *,
                          worker_id: str, **worker_kwargs) -> list[dict]:
    return worker.resume_interrupted(store, lock_mgr, worker_id=worker_id,
                                     **worker_kwargs)


# ------------------------------------------------- layout + segmentation step
def _resolve_and_segment(store: js.JobStore, content_con, job: models.Job, *,
                         worker_id: str, media_root: str,
                         segment_interval: int | None = None) -> dict:
    """Resolve the broadcast layout (automatically, Phase 3) and propose every
    gameplay/map window from the 360p SCAN PROXY (Phase 3 segmentation).

    Called for a DOWNLOADED job, and again for a PROCESSING job whose
    freshly-calibrated layout a human has just approved. Idempotent in the
    sense that matters: it refuses rather than guesses whenever the evidence
    it needs (proxy on disk, a resolved layout) is missing.
    """
    job_key = job.job_key
    media = job.payload.get("media") or {}
    if not media.get("localPath"):
        return {"ok": False, "reason": "no downloaded media on this job — "
                                       "run the worker's download step first"}
    # SCANNING always reads the 360p proxy, never the full-resolution source
    # (worker.scan_path_for returns None rather than silently falling back, so
    # a missing proxy stays a visible, recoverable failure).
    scan_path = worker.scan_path_for(job)
    if not scan_path or not os.path.exists(scan_path):
        proxy_err = (media.get("proxy") or {}).get("error")
        return {"ok": False, "reason": (
            "no 360p scan proxy on disk"
            + (f" ({proxy_err})" if proxy_err else "")
            + " — re-run the worker to regenerate it; scanning never falls "
              "back to the full-resolution VOD")}

    layout_id = job.payload.get("expectedLayoutId")
    if not layout_id:
        from . import layout_resolver as lr
        try:
            res = lr.resolve_layout(store, job, scan_path=scan_path,
                                    worker_id=worker_id)
        except lr.LayoutRefusal as exc:
            store.record_error(job_key, error_code=exc.code,
                               error_message=str(exc))
            return {"ok": False, "stage": "layout",
                    "reason": f"[{exc.code}] {exc}"}
        if not res.get("ok") or res.get("needsApproval"):
            return {"ok": False, "stage": "layout",
                    "reason": (res.get("reason")
                               or "a NEW layout was calibrated and needs "
                                  "`approve-layout --job <id> --confirm`"),
                    "record": res.get("record")}
        job = store.get(job_key)
        layout_id = job.payload.get("expectedLayoutId")

    import capture
    from . import segmentation as seg
    interval = (segment_interval if segment_interval is not None
                else seg.DEFAULT_INTERVAL_SECONDS)
    layout = capture.load_layout(dr.layout_path(layout_id))
    thumbs_dir = os.path.join(media_root, job_key.replace(":", "_"), "thumbs")
    if sm.can_transition(job.state, sm.SEGMENTING):
        store.transition(job_key, sm.SEGMENTING)
    try:
        candidates = seg.generate_candidates(
            scan_path, layout, out_dir=thumbs_dir, interval=interval)
        ids = seg.store_candidates(content_con, media.get("videoId"),
                                   job.payload.get("matchId"), candidates,
                                   source_job_key=job_key)
        store.transition(job_key, sm.NEEDS_REVIEW)
        store.record_attempt(job_key, ok=True, worker_id=worker_id)
        return {"ok": True, "candidates": len(candidates), "segmentIds": ids,
                "layoutId": layout_id,
                "scannedProxy": os.path.basename(scan_path)}
    except Exception as exc:  # noqa: BLE001
        store.record_attempt(job_key, ok=False, worker_id=worker_id,
                             error_code="segmentation_failed",
                             error_message=f"{type(exc).__name__}: {exc}")
        return {"ok": False, "reason": str(exc)}


# --------------------------------------------------------- automatic driver
def run_one_job(store: js.JobStore, lock_mgr: lk.LockManager, content_con,
                job_key: str, *, worker_id: str,
                media_root: str = worker.DEFAULT_MEDIA_ROOT,
                official_channel_ids: set | None = None,
                manual_approved_video_ids: set | None = None,
                segment_interval: int | None = None,
                for_harvest: bool = False) -> dict:
    """Advance ONE job by exactly one automatic step, whatever that means for
    its current state. Stops (returns ok=False with a clear reason) whenever
    the next step needs a human — segment/detection review, final approval,
    or an explicit `--publish`. Idempotent: re-running against a job that's
    waiting on a human is always safe (a no-op describing what's pending)."""
    job = store.get(job_key)
    if job is None:
        raise KeyError(f"no such job: {job_key}")

    if job.state == sm.ARCHIVED:
        return worker.download_job(
            store, lock_mgr, job, worker_id=worker_id, media_root=media_root,
            official_channel_ids=official_channel_ids,
            manual_approved_video_ids=manual_approved_video_ids,
            for_harvest=for_harvest)

    if job.state == sm.DOWNLOADING:
        return {"ok": False, "reason": "download already in progress "
                                       "(or crashed — try resume_interrupted_job)"}

    if job.state == sm.DOWNLOADED:
        return _resolve_and_segment(store, content_con, job,
                                    worker_id=worker_id, media_root=media_root,
                                    segment_interval=segment_interval)

    if job.state == sm.NEEDS_LAYOUT:
        return {"ok": False, "reason": "a calibrated layout is awaiting human "
                                       "approval — run `approve-layout --job "
                                       "<id> --confirm` (or fix the refusal "
                                       "recorded on the job)"}

    if job.state == sm.NEEDS_REVIEW:
        return {"ok": False, "reason": "waiting on human review "
                                       "(segment or detection) — nothing "
                                       "automatic left to do"}

    if job.state == sm.PROCESSING:
        # PROCESSING is reached two ways: detection is running (nothing to do
        # here), or a human just approved a freshly-calibrated layout via
        # `approve-layout`, which is exactly the moment segmentation becomes
        # possible. Distinguish them by whether this video has any candidate
        # segment yet — never by guessing.
        from . import segmentation as seg
        existing = seg.list_segments(content_con,
                                     video_id=job.payload.get("videoId"))
        if existing:
            return {"ok": False, "reason": "detection in progress — wait, or "
                                           "use detect-job for this stage"}
        if not job.payload.get("expectedLayoutId"):
            return {"ok": False, "reason": "no layout resolved for this job yet"}
        return _resolve_and_segment(store, content_con, job,
                                    worker_id=worker_id, media_root=media_root,
                                    segment_interval=segment_interval)

    if job.state == sm.READY_FOR_DETECTION:
        from . import segmentation as seg
        segments = seg.list_segments(content_con, video_id=job.payload.get("videoId"),
                                     review_status="approved")
        if not segments:
            return {"ok": False, "reason": "no approved segment found for this job"}
        store.transition(job_key, sm.PROCESSING)
        job = store.get(job_key)
        return dr.run_detection(store, job, segments[0], write=False,
                                worker_id=worker_id)

    if job.state == sm.APPROVED:
        from . import segmentation as seg
        segments = seg.list_segments(content_con, video_id=job.payload.get("videoId"),
                                     review_status="approved")
        if not segments:
            return {"ok": False, "reason": "no approved segment found for this job"}
        return dr.commit_approved_detection(store, job, segments[0],
                                            worker_id=worker_id)

    return {"ok": False, "reason": f"no automatic action defined for state {job.state} "
                                   f"— use the dedicated command for this stage"}


# --------------------------------------------------------------- coverage
def build_job_coverage_report(store: js.JobStore, *,
                              window_hours: int = 24,
                              now: dt.datetime | None = None) -> dict:
    """Rolling job-health report, mirroring `coverage.py`'s pattern: explicit
    counts by state, every stuck/blocked job named individually (nothing
    silently disappears), and the primary blocking issue per job — feeds the
    Phase 7 dashboard."""
    now = now or dt.datetime.now(dt.timezone.utc)
    window_start = now - dt.timedelta(hours=window_hours)
    window_start_iso = window_start.replace(microsecond=0).isoformat()

    all_jobs = store.list_jobs()
    counts = store.counts_by_state()
    recent = [j for j in all_jobs if (j.updated_at or "") >= window_start_iso]

    blocked = []
    for j in all_jobs:
        issue = _blocking_issue(j)
        if issue:
            blocked.append({"jobKey": j.job_key, "state": j.state,
                           "issue": issue, "lastError": j.last_error_message})

    return {
        "generatedAt": now.replace(microsecond=0).isoformat(),
        "windowHours": window_hours,
        "totalJobs": len(all_jobs),
        "recentlyUpdated": len(recent),
        "countsByState": counts,
        "blocked": blocked,
    }


def _blocking_issue(job: models.Job) -> str | None:
    if job.state == sm.FAILED_PERMANENT:
        return f"dead-lettered after {job.attempts} attempt(s): {job.last_error_message}"
    if job.state == sm.RETRY_SCHEDULED:
        return f"awaiting retry at {job.next_retry_at}: {job.last_error_message}"
    if job.state == sm.NEEDS_REVIEW:
        return "awaiting human review"
    if job.state == sm.APPROVED and not job.payload.get("detection", {}).get("db"):
        return "approved but detection not yet committed to production"
    if job.last_error_code and job.state not in sm.TERMINAL_STATES:
        return f"last error [{job.last_error_code}]: {job.last_error_message}"
    return None


def recommended_next_action(job: models.Job) -> str:
    """One-line operator guidance — the Phase 7 dashboard's "recommended
    next action" column."""
    actions = {
        sm.ARCHIVED: "run the worker (download)",
        sm.DOWNLOADING: "wait, or resume if the worker crashed",
        sm.DOWNLOADED: "generate segment candidates",
        sm.SEGMENTING: "wait for candidate generation",
        sm.NEEDS_REVIEW: "open segment or detection review in the control room",
        sm.READY_FOR_DETECTION: "run detection",
        sm.PROCESSING: "wait for detection to finish",
        sm.APPROVED: "commit detection, then publish when ready",
        sm.PUBLISHED: "confirm PR merged and Pages deployed",
        sm.RETRY_SCHEDULED: "wait for the scheduled retry, or retry now",
        sm.FAILED_PERMANENT: "investigate the dead-letter, then force-retry if fixed",
        sm.CANCELLED: "none — job was explicitly cancelled",
    }
    return actions.get(job.state, "none")
