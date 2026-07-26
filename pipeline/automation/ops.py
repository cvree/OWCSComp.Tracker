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

import capture  # noqa: E402  (pipeline/capture.py — layout loading for segmentation)

from . import detection_runner as dr
from . import job_store as js
from . import locks as lk
from . import models
from . import segmentation as seg
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


def retry_job(store: js.JobStore, job_key: str, *, force: bool = False) -> models.Job:
    return store.retry_job(job_key, force=force)


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


# --------------------------------------------------------- automatic driver
def run_one_job(store: js.JobStore, lock_mgr: lk.LockManager, content_con,
                job_key: str, *, worker_id: str,
                media_root: str = worker.DEFAULT_MEDIA_ROOT,
                official_channel_ids: set | None = None,
                manual_approved_video_ids: set | None = None,
                segment_interval: int = seg.DEFAULT_INTERVAL_SECONDS) -> dict:
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
            manual_approved_video_ids=manual_approved_video_ids)

    if job.state == sm.DOWNLOADING:
        return {"ok": False, "reason": "download already in progress "
                                       "(or crashed — try resume_interrupted_job)"}

    if job.state == sm.DOWNLOADED:
        media = job.payload.get("media") or {}
        local_path = media.get("localPath")
        layout_id = job.payload.get("expectedLayoutId")
        if not local_path or not layout_id:
            return {"ok": False, "reason": "missing downloaded media path or "
                                           "expectedLayoutId — cannot generate "
                                           "segment candidates automatically"}
        repo_root = os.path.dirname(_PIPELINE_DIR)
        video_path = os.path.join(repo_root, local_path)
        layout = capture.load_layout(dr.layout_path(layout_id))
        thumbs_dir = os.path.join(media_root, job_key.replace(":", "_"), "thumbs")
        store.transition(job_key, sm.SEGMENTING)
        try:
            candidates = seg.generate_candidates(
                video_path, layout, out_dir=thumbs_dir, interval=segment_interval)
            seg.store_candidates(content_con, media.get("videoId"),
                                 job.payload.get("matchId"), candidates,
                                 source_job_key=job_key)
            store.transition(job_key, sm.NEEDS_REVIEW)
            store.record_attempt(job_key, ok=True, worker_id=worker_id)
            return {"ok": True, "candidates": len(candidates)}
        except Exception as exc:  # noqa: BLE001
            store.record_attempt(job_key, ok=False, worker_id=worker_id,
                                 error_code="segmentation_failed",
                                 error_message=f"{type(exc).__name__}: {exc}")
            return {"ok": False, "reason": str(exc)}

    if job.state == sm.NEEDS_REVIEW:
        return {"ok": False, "reason": "waiting on human review "
                                       "(segment or detection) — nothing "
                                       "automatic left to do"}

    if job.state == sm.READY_FOR_DETECTION:
        segments = seg.list_segments(content_con, video_id=job.payload.get("videoId"),
                                     review_status="approved")
        if not segments:
            return {"ok": False, "reason": "no approved segment found for this job"}
        store.transition(job_key, sm.PROCESSING)
        job = store.get(job_key)
        return dr.run_detection(store, job, segments[0], write=False,
                                worker_id=worker_id)

    if job.state == sm.APPROVED:
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
