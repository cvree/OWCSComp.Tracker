"""
segmentation.py — assisted map segmentation (Roadmap Phase F).

Turns one downloaded VOD into a short list of candidate map windows a human
can approve in minutes, then extracts the approved clip. Deliberately NOT
"perfect automatic segmentation" (out of scope this sprint) — it reuses the
EXISTING gameplay classifier (`capture.py`'s HUD-anchor/reject-marker
template matching, already proven on real broadcasts) to propose likely
gameplay ranges, and a human confirms/adjusts/rejects/splits/merges them in
the control room before anything is extracted or ever reaches detection.

Storage: one row per candidate in `map_segments` (schema.sql, Phase F —
previously unused; see job_store.JobStore._migrate for the review/team/
extraction columns this module actually needs). Never writes hero
compositions — that is `detection_runner.py`'s job, gated on an APPROVED
segment.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_DIR = os.path.dirname(_HERE)
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

import capture  # noqa: E402  (pipeline/capture.py)
import cv2  # noqa: E402
import video_ingest as vi  # noqa: E402

from . import worker as _worker  # noqa: E402  (sha256_file reuse)

DEFAULT_INTERVAL_SECONDS = 10
DEFAULT_PRE_ROLL_SECONDS = 15
DEFAULT_POST_ROLL_SECONDS = 20
# Allow one missed sample inside an otherwise-gameplay run before treating it
# as a genuine gap (setup transitions/HUD flicker shouldn't fragment a range).
DEFAULT_GAP_TOLERANCE_SAMPLES = 1
MIN_CANDIDATE_SECONDS = 30  # shorter than this is almost certainly noise


def log(msg: str) -> None:
    print(f"[segmentation] {msg}", flush=True)


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------- candidate generation
def _frame_offset(path: str) -> int:
    return int(os.path.splitext(os.path.basename(path))[0])


def generate_candidates(
    video_path: str, layout: dict, *,
    out_dir: str,
    interval: int = DEFAULT_INTERVAL_SECONDS,
    pre_roll: int = DEFAULT_PRE_ROLL_SECONDS,
    post_roll: int = DEFAULT_POST_ROLL_SECONDS,
    gap_tolerance: int = DEFAULT_GAP_TOLERANCE_SAMPLES,
    min_candidate_seconds: int = MIN_CANDIDATE_SECONDS,
    extract_frames_fn=capture.extract_frames,
) -> list[dict]:
    """Sample `video_path` every `interval`s, classify each sample as likely
    gameplay with the SAME HUD-anchor/reject-marker classifier `capture.py`
    uses in production, detect scene changes between consecutive samples, and
    group adjacent probable-gameplay samples into candidate ranges expanded
    by pre/post-roll. Returns a list of:

        {"start_time", "end_time", "confidence", "signals": {...}}

    Never raises on a single unreadable frame (skips it, notes it in
    signals); raises only if the layout has no anchor template (a
    precondition failure, not a per-frame one) or ffmpeg/the video itself is
    unusable (propagated from extract_frames_fn).
    """
    anchor = capture._load_template(layout, "anchor")
    if anchor is None:
        raise ValueError("layout must define an 'anchor' region+template "
                         "to evaluate likely gameplay")
    replay = capture._load_template(layout, "replay")
    rejects = capture._load_reject_markers(layout)

    os.makedirs(out_dir, exist_ok=True)
    thumbs = sorted(extract_frames_fn(video_path, out_dir, interval))
    samples: list[dict] = []
    prev_gray = None
    scaled_layout = layout
    scaled = False
    for path in thumbs:
        offset = _frame_offset(path)
        frame = cv2.imread(path)
        if frame is None:
            samples.append({"offset": offset, "gameplay": False,
                            "reason": "unreadable", "score": 0.0,
                            "sceneChange": False})
            continue
        if not scaled:
            h, w = frame.shape[:2]
            scaled_layout, _info = capture.scale_layout_to_frame(layout, w, h)
            anchor = capture._load_template(scaled_layout, "anchor") or anchor
            replay = capture._load_template(scaled_layout, "replay")
            rejects = capture._load_reject_markers(scaled_layout)
            scaled = True
        ok, reason, score = capture.is_gameplay(frame, anchor, replay, rejects)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        scene_change = False
        if prev_gray is not None and prev_gray.shape == gray.shape:
            diff = cv2.absdiff(prev_gray, gray)
            scene_change = float(diff.mean()) > 40.0  # coarse cut/replay-swap signal
        prev_gray = gray
        samples.append({"offset": offset, "gameplay": ok, "reason": reason,
                        "score": round(float(score), 4),
                        "sceneChange": scene_change})

    return _group_candidates(samples, interval=interval, pre_roll=pre_roll,
                             post_roll=post_roll, gap_tolerance=gap_tolerance,
                             min_candidate_seconds=min_candidate_seconds)


def _group_candidates(samples: list[dict], *, interval: int, pre_roll: int,
                      post_roll: int, gap_tolerance: int,
                      min_candidate_seconds: int) -> list[dict]:
    """Pure grouping logic, split out so tests can drive it directly with
    synthetic sample lists (no real video/ffmpeg needed)."""
    if not samples:
        return []
    samples = sorted(samples, key=lambda s: s["offset"])
    ranges: list[list[dict]] = []
    current: list[dict] = []
    miss_streak = 0
    for s in samples:
        if s["gameplay"]:
            current.append(s)
            miss_streak = 0
        else:
            if current and miss_streak < gap_tolerance:
                miss_streak += 1
                current.append(s)  # tolerated gap sample, kept for context only
                continue
            if current:
                ranges.append(current)
            current = []
            miss_streak = 0
    if current:
        ranges.append(current)

    candidates = []
    for r in ranges:
        gameplay_samples = [s for s in r if s["gameplay"]]
        if not gameplay_samples:
            continue
        raw_start = gameplay_samples[0]["offset"]
        raw_end = gameplay_samples[-1]["offset"] + interval
        start = max(0, raw_start - pre_roll)
        end = raw_end + post_roll
        if end - start < min_candidate_seconds:
            continue
        avg_score = sum(s["score"] for s in gameplay_samples) / len(gameplay_samples)
        scene_changes = sum(1 for s in r if s["sceneChange"])
        candidates.append({
            "start_time": float(start), "end_time": float(end),
            "confidence": round(avg_score, 4),
            "signals": {
                "method": "hud-anchor-interval",
                "intervalSeconds": interval,
                "samplesInRange": len(r),
                "gameplaySamples": len(gameplay_samples),
                "avgAnchorScore": round(avg_score, 4),
                "sceneChanges": scene_changes,
                "preRollSeconds": pre_roll,
                "postRollSeconds": post_roll,
            },
        })
    return candidates


# ----------------------------------------------------------------- storage
def store_candidates(con: sqlite3.Connection, video_id: str,
                     candidate_match_id: str | None,
                     candidates: list[dict], *,
                     source_job_key: str | None = None) -> list[int]:
    """Insert new candidate rows (never de-dupes against existing ones by
    time overlap — a rerun of segmentation is expected to propose fresh
    candidates for a human to reconcile with anything already reviewed;
    approved/rejected rows are never touched here)."""
    ids = []
    now = _utcnow_iso()
    for c in candidates:
        cur = con.execute(
            """INSERT INTO map_segments
               (video_id, candidate_match_id, start_time, end_time, confidence,
                signals, review_status, source_job_key, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (video_id, candidate_match_id, c["start_time"], c["end_time"],
             c["confidence"], json.dumps(c.get("signals") or {}),
             source_job_key, now, now),
        )
        ids.append(cur.lastrowid)
    con.commit()
    return ids


def list_segments(con: sqlite3.Connection, *, video_id: str | None = None,
                  review_status: str | None = None) -> list[dict]:
    q = "SELECT * FROM map_segments"
    clauses, args = [], []
    if video_id:
        clauses.append("video_id = ?"); args.append(video_id)
    if review_status:
        clauses.append("review_status = ?"); args.append(review_status)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY start_time ASC"
    return [dict(r) for r in con.execute(q, args)]


def get_segment(con: sqlite3.Connection, segment_id: int) -> dict | None:
    row = con.execute("SELECT * FROM map_segments WHERE id = ?",
                      (segment_id,)).fetchone()
    return dict(row) if row else None


class SegmentNotFound(KeyError):
    pass


def _require(con: sqlite3.Connection, segment_id: int) -> dict:
    seg = get_segment(con, segment_id)
    if seg is None:
        raise SegmentNotFound(f"no such segment: {segment_id}")
    return seg


# ------------------------------------------------------------ review actions
def adjust_boundaries(con: sqlite3.Connection, segment_id: int, *,
                      start_time: float | None = None,
                      end_time: float | None = None) -> dict:
    seg = _require(con, segment_id)
    start = seg["start_time"] if start_time is None else float(start_time)
    end = seg["end_time"] if end_time is None else float(end_time)
    if end <= start:
        raise ValueError("end_time must be after start_time")
    con.execute(
        "UPDATE map_segments SET start_time=?, end_time=?, updated_at=? WHERE id=?",
        (start, end, _utcnow_iso(), segment_id))
    con.commit()
    return get_segment(con, segment_id)  # type: ignore[return-value]


def approve_segment(con: sqlite3.Connection, segment_id: int, *,
                    map_order: int, map_name: str, map_mode: str,
                    team_a: str, team_b: str, side_assignment: str,
                    layout_id: str, reviewer_note: str | None = None) -> dict:
    """The one place a candidate becomes a confirmed map segment. Requires
    every field the closed loop needs downstream (map identity, teams,
    sides, layout) — a segment can never reach detection half-described."""
    _require(con, segment_id)
    con.execute(
        """UPDATE map_segments SET review_status='approved',
               candidate_map_order=?, map_name=?, map_mode=?, team_a=?,
               team_b=?, side_assignment=?, layout_id=?, reviewer_note=?,
               updated_at=? WHERE id=?""",
        (map_order, map_name, map_mode, team_a, team_b, side_assignment,
         layout_id, reviewer_note, _utcnow_iso(), segment_id))
    con.commit()
    return get_segment(con, segment_id)  # type: ignore[return-value]


def reject_segment(con: sqlite3.Connection, segment_id: int, *,
                   reason: str) -> dict:
    _require(con, segment_id)
    con.execute(
        """UPDATE map_segments SET review_status='rejected',
               reviewer_note=?, updated_at=? WHERE id=?""",
        (reason, _utcnow_iso(), segment_id))
    con.commit()
    return get_segment(con, segment_id)  # type: ignore[return-value]


def mark_invalid(con: sqlite3.Connection, segment_id: int, *, reason: str) -> dict:
    """Distinct from reject_segment: the candidate itself was structurally
    unusable (e.g. spans a replay/highlight, corrupt window) rather than
    simply not wanted."""
    _require(con, segment_id)
    con.execute(
        """UPDATE map_segments SET review_status='invalid',
               reviewer_note=?, updated_at=? WHERE id=?""",
        (reason, _utcnow_iso(), segment_id))
    con.commit()
    return get_segment(con, segment_id)  # type: ignore[return-value]


def split_segment(con: sqlite3.Connection, segment_id: int, *,
                  split_time: float) -> tuple[dict, dict]:
    """Replace one pending candidate with two pending candidates on either
    side of `split_time`. The original row is marked 'split' (kept, never
    deleted) so its history and confidence provenance survive."""
    seg = _require(con, segment_id)
    if not (seg["start_time"] < split_time < seg["end_time"]):
        raise ValueError("split_time must fall strictly inside the segment")
    now = _utcnow_iso()
    con.execute(
        "UPDATE map_segments SET review_status='split', updated_at=? WHERE id=?",
        (now, segment_id))
    signals = json.loads(seg["signals"] or "{}")
    signals = dict(signals, splitFrom=segment_id)
    first_id, second_id = store_candidates(
        con, seg["video_id"], seg["candidate_match_id"],
        [{"start_time": seg["start_time"], "end_time": split_time,
          "confidence": seg["confidence"], "signals": signals},
         {"start_time": split_time, "end_time": seg["end_time"],
          "confidence": seg["confidence"], "signals": signals}],
        source_job_key=seg["source_job_key"])
    return get_segment(con, first_id), get_segment(con, second_id)  # type: ignore[return-value]


def merge_segments(con: sqlite3.Connection, segment_id_a: int,
                   segment_id_b: int) -> dict:
    """Replace two pending candidates spanning the same video with one
    covering their union. Both originals are marked 'merged' and kept."""
    a = _require(con, segment_id_a)
    b = _require(con, segment_id_b)
    if a["video_id"] != b["video_id"]:
        raise ValueError("cannot merge segments from different videos")
    now = _utcnow_iso()
    for sid in (segment_id_a, segment_id_b):
        con.execute(
            "UPDATE map_segments SET review_status='merged', updated_at=? WHERE id=?",
            (now, sid))
    start = min(a["start_time"], b["start_time"])
    end = max(a["end_time"], b["end_time"])
    confidence = (a["confidence"] + b["confidence"]) / 2.0
    signals = {"mergedFrom": [segment_id_a, segment_id_b]}
    new_id = store_candidates(
        con, a["video_id"], a["candidate_match_id"],
        [{"start_time": start, "end_time": end, "confidence": confidence,
          "signals": signals}],
        source_job_key=a["source_job_key"])[0]
    return get_segment(con, new_id)  # type: ignore[return-value]


# --------------------------------------------------------------- extraction
def extract_segment_clip(con: sqlite3.Connection, segment_id: int,
                         source_media_path: str, out_dir: str, *,
                         runner=subprocess,
                         cut_fn=vi._ffmpeg_cut_from_url,
                         validate_fn=vi.probe_clip_valid,
                         resolution_fn=vi.probe_clip_resolution) -> dict:
    """Cut the approved segment's [start_time, end_time] window out of the
    already-downloaded full VOD with ffmpeg (stream copy first, re-encode
    fallback — reuses `video_ingest`'s own cut helper since it works
    identically against a local path or a remote URL). Verifies the result,
    records hash/duration/resolution/path on the segment row. Refuses to
    extract a segment that hasn't been human-approved.
    """
    seg = _require(con, segment_id)
    if seg["review_status"] != "approved":
        raise ValueError(
            f"segment {segment_id} is {seg['review_status']!r}, not "
            f"'approved' — extraction requires human approval first")
    os.makedirs(out_dir, exist_ok=True)
    out_name = f"segment_{segment_id}_map{seg['candidate_map_order'] or 0}.mp4"
    out_path = os.path.join(out_dir, out_name)
    cut_fn(source_media_path, int(seg["start_time"]), int(seg["end_time"]),
          out_path, runner=runner)
    ok, reason = validate_fn(out_path)
    if not ok:
        raise vi.InvalidClip(f"extracted segment clip is invalid: {reason}")
    digest = _worker.sha256_file(out_path)
    res = resolution_fn(out_path) or {}
    con.execute(
        """UPDATE map_segments SET extracted_path=?, extracted_hash=?,
               extracted_width=?, extracted_height=?, duration_seconds=?,
               updated_at=? WHERE id=?""",
        (_worker._site_relpath(out_path, os.path.dirname(_PIPELINE_DIR)),
         digest, res.get("width"), res.get("height"),
         seg["end_time"] - seg["start_time"], _utcnow_iso(), segment_id))
    con.commit()
    log(f"segment {segment_id}: extracted {seg['end_time'] - seg['start_time']:.0f}s "
       f"clip, sha256={digest[:12]}… -> {out_path}")
    return get_segment(con, segment_id)  # type: ignore[return-value]
