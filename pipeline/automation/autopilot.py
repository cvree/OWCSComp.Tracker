"""
autopilot.py — the agentic "free agent" driver: one job, advanced through
EVERY automatic stage in a row, stopping honestly at the first gate that
belongs to a human.

`ops.run_one_job` advances a job by exactly one step and was designed to be
called repeatedly; until now the "repeatedly" was an operator re-typing
`run-job` between stages. This module is that loop, plus the two pieces of
glue the closed loop was missing (both previously required a Python console):

  * a job whose segments are all reviewed (>=1 approved, none pending) now
    advances NEEDS_REVIEW -> READY_FOR_DETECTION automatically, and
  * an approved segment with no extracted clip gets
    `segmentation.extract_segment_clip` run on it (from the full-resolution
    source — extraction refuses the scan proxy, unchanged) before detection
    is attempted, instead of detection failing with "no extracted clip yet".

What the autopilot NEVER does, deliberately:

  * approve a SOURCE — a non-registry link always stops on
    `approve-source --confirm` (the audited human gate);
  * approve a LAYOUT — a freshly-calibrated layout always stops on
    `approve-layout --confirm` after a human has looked at the sheet;
  * approve a DETECTION review — the gate that lets hero compositions reach
    production is always a human decision, `--auto-accept` or not;
  * publish — `process-approved-job --publish` stays a supervised command.

`auto_accept=True` covers exactly one gate: SEGMENT identity review. It runs
the identity proposer on every pending segment and accepts each proposal
through the SAME `segment_identity.accept_proposed` gate a human uses — a
proposal with a blocking review task or any UNKNOWN required field is
refused there, recorded here, and the loop stops for a human. Nothing gets
looser; a person just isn't retyping values the machine already proved.

Import-light on purpose (no cv2/ffmpeg at module level): the CLI must be
able to print an autopilot refusal on a machine with no OpenCV. The heavy
stages (segmentation, identity OCR, extraction) are imported lazily inside
the steps that actually run them.
"""
from __future__ import annotations

import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_DIR = os.path.dirname(_HERE)
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)
import proc_text  # noqa: E402  (UTF-8 subprocess decoding)

from . import job_store as js  # noqa: E402
from . import link_intake as li  # noqa: E402
from . import locks as lk  # noqa: E402
from . import ops  # noqa: E402
from . import state_machine as sm  # noqa: E402
from . import worker  # noqa: E402

# A full pass is bounded: ARCHIVED->DOWNLOADED (1), segmentation (1), review
# handling (1), detection dry-run (1), detection review (human), commit (1)
# — 16 leaves generous room for retries/no-ops without ever spinning.
DEFAULT_MAX_STEPS = 16

# Stop kinds. "human-gate" and "terminal" are the two GOOD endings — the
# loop did everything automation is allowed to do. Everything else means
# the operator has a problem to look at, not just a review to do.
STOP_HUMAN_GATE = "human-gate"
STOP_TERMINAL = "terminal"
STOP_BLOCKED = "blocked"
STOP_LOCKED = "locked"
STOP_MAX_STEPS = "max-steps"
STOP_NO_PROGRESS = "no-progress"

_GOOD_STOPS = frozenset({STOP_HUMAN_GATE, STOP_TERMINAL})


def log(msg: str) -> None:
    print(f"[autopilot] {msg}", flush=True)


def _retry_due(job) -> bool:
    """True when a RETRY_SCHEDULED job's backoff has elapsed.

    Taking a retry early would hammer the same expired signed URL — the
    exact spin the backoff exists to prevent — so the loop waits and says
    so instead."""
    import datetime as _dt
    when = getattr(job, "next_retry_at", None)
    if not when:
        return True
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    return str(when) <= now


# ------------------------------------------------------------ default hooks
def _sample_segment_frames(scan_path: str, segment: dict, count: int
                           ) -> list[tuple[float, str]]:
    """Sample frames from inside one segment window, on the 360p scan proxy
    (mirrors cli.si_sample_frames — the identity proposer wants evenly-spread
    evidence frames, and one bad seek is never fatal)."""
    import db as content_db
    start, end = float(segment["start_time"]), float(segment["end_time"])
    span = max(end - start, 1.0)
    out_dir = os.path.join(content_db.REPO_ROOT, "reports", "identity",
                           str(segment["video_id"]), f"seg{segment['id']}")
    os.makedirs(out_dir, exist_ok=True)
    step = span / (count + 1)
    frames: list[tuple[float, str]] = []
    for i in range(count):
        t = start + step * (i + 1)
        path = os.path.join(out_dir, f"frame_{int(t):08d}.png")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-ss", str(t), "-i", scan_path, "-frames:v", "1", path]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True,
                           **proc_text.PIPE_TEXT)
        except Exception:  # noqa: BLE001 — one bad seek is not fatal
            continue
        if os.path.exists(path):
            frames.append((t, path))
    return frames


def default_propose(store: js.JobStore, job, segment: dict, *,
                    samples: int = 8, ocr_engine: str = "easyocr") -> dict:
    """Run the Phase 4 identity proposer for one pending segment and store
    the proposal — exactly what `propose-identity` does for that segment.
    Heavy imports live here, not at module level."""
    import capture
    import db as content_db
    import ocr_hud
    from . import detection_runner as dr
    from . import segment_identity as si

    layout_id = job.payload.get("expectedLayoutId")
    if not layout_id:
        raise ValueError("no resolved layout on this job — run resolve-layout")
    scan_path = worker.scan_path_for(job)
    if not scan_path or not os.path.exists(scan_path):
        raise ValueError("no 360p scan proxy on disk — re-run the worker")
    layout = capture.load_layout(dr.layout_path(layout_id))
    frames = _sample_segment_frames(scan_path, segment, samples)
    con = content_db.connect()
    try:
        content_db.init_schema(con)
        proposal = si.propose_identity(
            store.con, con, segment, layout=layout, frames=frames,
            read_fn=ocr_hud.make_reader(ocr_engine),
            match_id=job.payload.get("matchId"))
    finally:
        con.close()
    si.store_proposals(store.con, segment["id"], proposal)
    return proposal


def default_accept(store: js.JobStore, job, segment: dict, *,
                   accepted_by: str) -> dict:
    """Accept one segment's machine proposal through the SAME
    `accept_proposed` gate a human uses — it refuses blocked or incomplete
    proposals, and that refusal propagates to the caller unchanged."""
    from . import segment_identity as si
    return si.accept_proposed(
        store.con, segment["id"],
        reviewer_note=(f"autopilot --auto-accept (run by {accepted_by}): "
                       f"accepted the machine proposal after the "
                       f"accept-proposed completeness gate passed"),
        layout_id=job.payload.get("expectedLayoutId"))


def default_extract(store: js.JobStore, job, segment: dict, *,
                    media_root: str) -> dict:
    """Cut one approved segment's detection clip from the full-resolution
    source (extract_segment_clip refuses the proxy — unchanged)."""
    from . import segmentation as seg
    source = worker.source_path_for(job)
    if not source or not os.path.exists(source):
        raise ValueError("full-resolution source media is not on disk — "
                         "re-run the worker download step")
    out_dir = os.path.join(media_root,
                           job.job_key.replace(":", "_").replace("/", "_"),
                           "segments")
    return seg.extract_segment_clip(store.con, segment["id"], source, out_dir)


# ------------------------------------------------------------------ helpers
def _list_segments(store: js.JobStore, video_id: str | None,
                   review_status: str | None = None) -> list[dict]:
    from . import segmentation as seg
    return seg.list_segments(store.con, video_id=video_id,
                             review_status=review_status)


def _step(steps: list[dict], state: str, action: str, ok: bool,
          detail: str) -> None:
    steps.append({"state": state, "action": action, "ok": ok,
                  "detail": detail})
    log(f"{state}: {action} — {'ok' if ok else 'STOP'} · {detail}")


def _summarize(result: dict) -> str:
    if result.get("ok"):
        keys = [k for k in ("candidates", "summary", "path", "segmentIds")
                if k in result]
        return ", ".join(f"{k}={result[k]!r}" for k in keys)[:200] or "ok"
    return (result.get("reason") or result.get("errorMessage")
            or "no reason given")[:300]


# -------------------------------------------------------------- the driver
def run_autopilot(store: js.JobStore, lock_mgr: lk.LockManager,
                  job_key: str, *, worker_id: str,
                  media_root: str | None = None,
                  auto_accept: bool = False,
                  accepted_by: str | None = None,
                  for_harvest: bool = False,
                  max_steps: int = DEFAULT_MAX_STEPS,
                  official_channel_ids: set | None = None,
                  manual_approved_video_ids: set | None = None,
                  samples: int = 8, ocr_engine: str = "easyocr",
                  run_one=None, propose_fn=None, accept_fn=None,
                  extract_fn=None) -> dict:
    """Drive one job through every automatic stage until a human gate,
    a terminal state, or a blocker. Returns a full report:

      {"ok", "jobKey", "state", "stop", "stopDetail", "steps": [...],
       "blocking": [...], "nextCommand": "..."}

    `ok` is True only when the loop ended at a legitimate resting point
    (human gate / terminal state); False means something needs fixing, and
    `stopDetail` + `blocking` say what. The job's resource lock is held for
    the whole loop (re-entrant with the per-stage acquire/release inside
    worker.download_job) and always released on the way out.

    `run_one`/`propose_fn`/`accept_fn`/`extract_fn` are injectable for
    offline tests; the defaults are the real stages.
    """
    media_root = media_root or worker.DEFAULT_MEDIA_ROOT
    run_one = run_one or ops.run_one_job
    propose_fn = propose_fn or (
        lambda s, j, g: default_propose(s, j, g, samples=samples,
                                        ocr_engine=ocr_engine))
    accept_fn = accept_fn or (
        lambda s, j, g: default_accept(s, j, g,
                                       accepted_by=accepted_by or worker_id))
    extract_fn = extract_fn or (
        lambda s, j, g: default_extract(s, j, g, media_root=media_root))

    job = store.get(job_key)
    if job is None:
        raise KeyError(f"no such job: {job_key}")

    steps: list[dict] = []
    stop, detail = STOP_MAX_STEPS, f"stopped after {max_steps} steps"
    resource = worker.resource_for(job)
    if not lock_mgr.acquire(resource, worker_id):
        holder = lock_mgr.holder(resource)
        return _report(store, job_key, steps, STOP_LOCKED,
                       f"resource {resource} is held by "
                       f"{getattr(holder, 'worker_id', 'another worker')} — "
                       f"not stealing a live lease")
    try:
        for _ in range(max_steps):
            job = store.get(job_key)
            state = job.state

            if state in sm.TERMINAL_STATES:
                stop, detail = STOP_TERMINAL, f"job is {state} — nothing left"
                break

            # Gate 0: source authorization. The audited human gate — the
            # autopilot never approves a source, it stops and says so.
            source = job.payload.get("source") or {}
            if source.get("state") != li.SOURCE_APPROVED:
                stop = STOP_HUMAN_GATE
                detail = ("source is not authorized "
                          f"({source.get('state') or 'unknown'}: "
                          f"{source.get('reason') or 'no reason recorded'}) — "
                          "approve-source --confirm is a human decision")
                break

            # An approved source still sitting in DISCOVERED/SCHEDULED (e.g.
            # approved manually after intake) just needs the bookkeeping
            # advance to ARCHIVED that ingest_link performs on auto-approval.
            if state in (sm.DISCOVERED, sm.SCHEDULED):
                job = li._advance_to_ready(store, job)
                _step(steps, state, "advance-to-ready", True,
                      f"approved source advanced to {job.state}")
                continue

            if state == sm.NEEDS_LAYOUT:
                stop = STOP_HUMAN_GATE
                detail = ("a calibrated layout awaits human review — "
                          "approve-layout --confirm after checking the sheet")
                break

            if state == sm.NEEDS_TEMPLATES:
                stop = STOP_HUMAN_GATE
                detail = ("hero-template coverage is insufficient for this "
                          "broadcast package — harvest + label templates "
                          "(a human step by design)")
                break

            if state == sm.DOWNLOADING:
                stop = STOP_BLOCKED
                detail = ("a download is already in progress (or crashed) — "
                          "resume-job recovers a stale one")
                break

            # A retry that has come due is automatic work, not a dead end.
            # Before this, `run_one_job` had no branch for RETRY_SCHEDULED
            # and every retried job stopped with "no automatic action".
            if state == sm.RETRY_SCHEDULED:
                if not _retry_due(job):
                    stop = STOP_BLOCKED
                    detail = (f"a retry is scheduled for "
                              f"{job.next_retry_at} — run `retry-job "
                              f"{job_key}` to take it now")
                    break
                resumed = ops.resume_after_retry(store, job_key)
                if resumed.state == sm.RETRY_SCHEDULED:
                    stop = STOP_BLOCKED
                    detail = ("retry is due but the job could not be "
                              "restored to a runnable stage — check "
                              f"`show-job {job_key}`")
                    break
                _step(steps, state, "resume-after-retry", True,
                      f"restored to {resumed.state} "
                      f"(attempt {resumed.attempts + 1}; source approval "
                      f"untouched)")
                continue

            if state == sm.NEEDS_REVIEW:
                verdict, why = _handle_review(
                    store, job, steps, auto_accept=auto_accept,
                    propose_fn=propose_fn, accept_fn=accept_fn)
                if verdict == "continue":
                    continue
                stop, detail = verdict, why
                break

            if state in (sm.READY_FOR_DETECTION, sm.APPROVED):
                ok, why = _ensure_extracted(store, job, steps,
                                            extract_fn=extract_fn)
                if not ok:
                    stop, detail = STOP_BLOCKED, why
                    break

            before = state
            result = run_one(store, lock_mgr, store.con, job_key,
                             worker_id=worker_id, media_root=media_root,
                             official_channel_ids=official_channel_ids,
                             manual_approved_video_ids=manual_approved_video_ids,
                             for_harvest=for_harvest)
            _step(steps, before, "run-one-job", bool(result.get("ok")),
                  _summarize(result))
            # worker.download_job releases the resource lock itself when its
            # stage ends; re-take it (re-entrant) so the rest of the loop
            # still owns the job.
            lock_mgr.acquire(resource, worker_id)
            if not result.get("ok"):
                stop = STOP_BLOCKED
                detail = _summarize(result)
                break
            after = store.get(job_key).state
            if after == sm.APPROVED and before == sm.APPROVED:
                # Detection committed. Publication is a supervised command —
                # a legitimate resting point, not a loop.
                stop = STOP_HUMAN_GATE
                detail = ("detection committed — publication stays "
                          "supervised: process-approved-job --publish")
                break
            if after == before:
                stop = STOP_NO_PROGRESS
                detail = (f"step reported ok but the job is still {after} — "
                          f"refusing to spin; inspect show-job {job_key}")
                break
    finally:
        lock_mgr.release(resource, worker_id)
    return _report(store, job_key, steps, stop, detail)


def _handle_review(store: js.JobStore, job, steps: list[dict], *,
                   auto_accept: bool, propose_fn, accept_fn
                   ) -> tuple[str, str]:
    """NEEDS_REVIEW handling. Returns ("continue", why) when the job was
    legally advanced, or (stop_kind, why) when the loop must stop."""
    video_id = job.payload.get("videoId")
    pending = _list_segments(store, video_id, "pending")
    approved = _list_segments(store, video_id, "approved")

    # Detection review: the job ran detection (payload carries the summary)
    # and no segment is pending — the review on the table is whether hero
    # compositions reach production. ALWAYS a human decision.
    if job.payload.get("detection") and not pending:
        return (STOP_HUMAN_GATE,
                "detection candidates await HUMAN review — approving comps "
                "into production is never automatic (review the ingest "
                "report, then transition the job to APPROVED)")

    if pending:
        if not auto_accept:
            return (STOP_HUMAN_GATE,
                    f"{len(pending)} segment(s) await identity review — "
                    f"re-run with --auto-accept to accept clean machine "
                    f"proposals, or review them in intake.html")
        from . import segment_identity as si
        refused: list[str] = []
        for segment in pending:
            sid = segment["id"]
            try:
                if not si.load_proposals(store.con, sid):
                    propose_fn(store, job, segment)
                    _step(steps, sm.NEEDS_REVIEW, f"propose-identity #{sid}",
                          True, "proposal stored")
                accept_fn(store, job, segment)
                _step(steps, sm.NEEDS_REVIEW, f"accept-proposed #{sid}",
                      True, "accepted through the accept-proposed gate")
            except (ValueError, RuntimeError) as exc:
                refused.append(f"segment #{sid}: {exc}")
                _step(steps, sm.NEEDS_REVIEW, f"accept-proposed #{sid}",
                      False, str(exc))
        if refused:
            return (STOP_HUMAN_GATE,
                    "auto-accept refused (the gate held): "
                    + " | ".join(refused))
        pending = _list_segments(store, video_id, "pending")
        approved = _list_segments(store, video_id, "approved")

    if approved and not pending:
        store.transition(job.job_key, sm.READY_FOR_DETECTION)
        _step(steps, sm.NEEDS_REVIEW, "advance", True,
              f"{len(approved)} approved segment(s), none pending -> "
              f"READY_FOR_DETECTION")
        return ("continue", "advanced")

    return (STOP_BLOCKED,
            f"no approved segment to detect ({len(pending)} pending, "
            f"{len(approved)} approved) — review or re-segment first")


def _ensure_extracted(store: js.JobStore, job, steps: list[dict], *,
                      extract_fn) -> tuple[bool, str]:
    """Every approved segment gets its detection clip cut before detection —
    the glue step the closed loop was missing (detection refuses a segment
    with no extracted_path, and nothing used to call the extractor)."""
    approved = _list_segments(store, job.payload.get("videoId"), "approved")
    missing = [s for s in approved if not s.get("extracted_path")]
    for segment in missing:
        try:
            extract_fn(store, job, segment)
            _step(steps, job.state, f"extract-segment #{segment['id']}",
                  True, "detection clip cut from the full-resolution source")
        except Exception as exc:  # noqa: BLE001 — surfaced, never swallowed
            _step(steps, job.state, f"extract-segment #{segment['id']}",
                  False, str(exc))
            return False, (f"could not extract segment "
                           f"#{segment['id']}: {exc}")
    return True, "extracted"


def _report(store: js.JobStore, job_key: str, steps: list[dict],
            stop: str, detail: str) -> dict:
    job = store.get(job_key)
    return {
        "ok": stop in _GOOD_STOPS,
        "jobKey": job_key,
        "state": job.state if job else None,
        "stop": stop,
        "stopDetail": detail,
        "steps": steps,
        "blocking": li.blocking_reasons(job) if job else [],
        "nextCommand": li.next_command(job) if job else None,
    }


def format_result(result: dict) -> str:
    lines = [f"  state       : {result['state']}",
             f"  outcome     : {result['stop']} — {result['stopDetail']}"]
    if result["steps"]:
        lines.append(f"  steps taken : {len(result['steps'])}")
        for s in result["steps"]:
            mark = "ok " if s["ok"] else "STOP"
            lines.append(f"    [{mark}] {s['state']:<20} {s['action']:<24} "
                         f"{s['detail']}")
    for b in result["blocking"]:
        lines.append(f"  BLOCKED     : {b}")
    lines.append(f"  next command: {result['nextCommand']}")
    return "\n".join(lines)
