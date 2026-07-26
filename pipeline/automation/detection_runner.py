"""
detection_runner.py — connect an approved map segment to the EXISTING
detector and evidence system (Roadmap Phase G).

This module never reimplements hero classification, temporal consensus, or
swap detection — `pipeline/ingest_map.py` already does all of that (HUD-
anchor gameplay filtering, per-slot template matching with UNKNOWN-not-
guessed reads, phase-gated sampling, hysteresis-based swap confirmation,
evidence crops, an annotated report). This module only builds the exact
`argparse.Namespace` `ingest_map.run()` expects from an approved
`map_segments` row + its owning job, runs it, classifies any failure the
sprint's Phase 4/5 gates call out, and records the result on the job.

Two-phase by design, matching the required closed loop exactly:
  1. `run_detection(..., write=False)` — automatic, produces evidence-backed
     composition/swap CANDIDATES for review (`NEEDS_REVIEW`). Writes nothing.
  2. `commit_approved_detection(..., write=True)` — only after a human
     approves the review, idempotently re-runs the SAME ingest_id with
     `--write` so hero_stints/hero_swaps are actually persisted.
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_DIR = os.path.dirname(_HERE)
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

import ingest_map  # noqa: E402
import db as content_db  # noqa: E402

from . import job_store as js  # noqa: E402
from . import models  # noqa: E402
from . import state_machine as sm  # noqa: E402

DEFAULT_EVERY_SECONDS = 5.0
LAYOUTS_DIR = os.path.join(content_db.REPO_ROOT, "layouts")


def layout_path(layout_id: str) -> str:
    """Accepts either a bare layout id ('owcs_jksix_qwc') or an already-
    qualified path/filename — never guesses a DIFFERENT layout than named."""
    if os.path.isabs(layout_id) or os.path.sep in layout_id:
        return layout_id
    name = layout_id if layout_id.endswith(".json") else f"{layout_id}.json"
    return os.path.join(LAYOUTS_DIR, name)


def ingest_id_for(job_key: str, segment_id: int) -> str:
    """Deterministic, stable across reruns — ingest_map.write_db only ever
    replaces ITS OWN ingest_id's rows, so reusing this id is what makes a
    re-detection (or the later write=True commit pass) idempotent."""
    safe = job_key.replace(":", "-").replace("/", "-")
    return f"beta-{safe}-seg{segment_id}"


def build_ingest_args(job: models.Job, segment: dict, *, write: bool,
                      every: float = DEFAULT_EVERY_SECONDS) -> argparse.Namespace:
    if not segment.get("extracted_path"):
        raise ValueError(f"segment {segment['id']} has no extracted clip yet "
                         f"— run segmentation.extract_segment_clip first")
    if segment.get("review_status") != "approved":
        raise ValueError(f"segment {segment['id']} is "
                         f"{segment.get('review_status')!r}, not 'approved'")
    clip_path = os.path.join(os.path.dirname(_PIPELINE_DIR), segment["extracted_path"])
    return argparse.Namespace(
        clip=clip_path,
        clip_offset=float(segment["start_time"]),
        start=float(segment["start_time"]),
        end=float(segment["end_time"]),
        layout=layout_path(segment["layout_id"]),
        source_id=job.payload.get("videoId") or segment["video_id"],
        ingest_id=ingest_id_for(job.job_key, segment["id"]),
        match=segment["candidate_match_id"],
        map_order=segment["candidate_map_order"],
        map_id=segment.get("map_name"),
        map_winner=None,
        team_a=segment["team_a"],
        team_b=segment["team_b"],
        vod_url=job.payload.get("sourceUrl"),
        every=every,
        write=write,
        no_phase_gate=False,
        ocr_guard=False,
        ocr_engine="easyocr",
    )


def classify_detection_error(exc: BaseException) -> tuple[str, str]:
    """Every Phase 4 required failure state gets an explicit, stable code —
    never a bare stack trace, and never a path that lets a bad layout/media
    produce a plausible-looking composition."""
    msg = str(exc)
    low = msg.lower()
    if isinstance(exc, SystemExit):
        if "cannot scale" in low or "aspect ratio mismatch" in low:
            return "layout_mismatch", msg
        if "cannot read a frame" in low:
            return "no_valid_gameplay_frames", msg
        return "detection_precondition_failed", msg
    if isinstance(exc, FileNotFoundError):
        if "template" in low:
            return "missing_templates", msg
        return "missing_dependency", msg
    if isinstance(exc, (OSError, IOError)):
        return "corrupt_media", msg
    return "unknown_error", f"{type(exc).__name__}: {msg}"


def run_detection(store: js.JobStore, job: models.Job, segment: dict, *,
                  write: bool = False, every: float = DEFAULT_EVERY_SECONDS,
                  worker_id: str | None = None,
                  run_fn=ingest_map.run) -> dict:
    """Automatic detection pass over one approved+extracted segment.

    write=False (the default, always run first): produces evidence-backed
    composition/swap candidates and moves the job PROCESSING -> NEEDS_REVIEW.
    Nothing is written to comp/hero tables. Any failure routes through
    `record_attempt(ok=False, ...)` (RETRY_SCHEDULED/FAILED_PERMANENT per the
    existing backoff policy) — the job never silently sits in PROCESSING
    with no trace of what went wrong.
    """
    try:
        args = build_ingest_args(job, segment, write=write, every=every)
        result = run_fn(args)
        summary = {
            "ingestId": args.ingest_id,
            "outRoot": result.get("out_root"),
            "stats": {
                k: result["stats"].get(k) for k in (
                    "frames_sampled", "gameplay_frames", "skipped_frames",
                    "rounds", "confirmed_swaps", "rejected_swaps",
                    "setup_changes", "calibration_health", "detector_version",
                ) if k in result.get("stats", {})
            },
            "written": bool(write),
        }
        if "db" in result:
            summary["db"] = {k: result["db"].get(k) for k in
                             ("map_result_id", "stints", "swaps",
                              "observations", "bans", "findings")
                             if k in result["db"]}
        store.update_payload(job.job_key, {"detection": summary})
        store.record_attempt(job.job_key, ok=True, worker_id=worker_id,
                             diagnostic_path=result.get("out_root"))
        if not write:
            store.transition(job.job_key, sm.NEEDS_REVIEW)
        return {"ok": True, "summary": summary}
    except (Exception, SystemExit) as exc:  # noqa: BLE001 — ingest_map.run()
        # raises SystemExit for hard preconditions (layout/frame failures);
        # every detection failure must be classified, never left as a crash.
        code, message = classify_detection_error(exc)
        store.record_attempt(job.job_key, ok=False, worker_id=worker_id,
                             error_code=code, error_message=message)
        return {"ok": False, "errorCode": code, "errorMessage": message}


def commit_approved_detection(store: js.JobStore, job: models.Job,
                              segment: dict, *,
                              every: float = DEFAULT_EVERY_SECONDS,
                              worker_id: str | None = None,
                              run_fn=ingest_map.run) -> dict:
    """The write=True pass — ONLY called after a human has approved the
    review (job.state == APPROVED). Idempotent: reruns the exact same
    ingest_id, so re-invoking after a partial failure never double-writes
    (ingest_map.write_db replaces only its own ingest_id's rows)."""
    if job.state != sm.APPROVED:
        raise ValueError(
            f"{job.job_key} is {job.state}, not APPROVED — detection can "
            f"only be committed to production after human review approval")
    return run_detection(store, job, segment, write=True, every=every,
                        worker_id=worker_id, run_fn=run_fn)
