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
import gameplay_state as gs  # noqa: E402
import video_ingest as vi  # noqa: E402

from . import worker as _worker  # noqa: E402  (sha256_file reuse)

DEFAULT_INTERVAL_SECONDS = 10
DEFAULT_PRE_ROLL_SECONDS = 15
DEFAULT_POST_ROLL_SECONDS = 20
# Allow one missed sample inside an otherwise-gameplay run before treating it
# as a genuine gap (setup transitions/HUD flicker shouldn't fragment a range).
DEFAULT_GAP_TOLERANCE_SAMPLES = 1
MIN_CANDIDATE_SECONDS = 30  # shorter than this is almost certainly noise
THUMBNAILS_PER_CANDIDATE = 3

# Why a sample was NOT counted as gameplay, in the operator's vocabulary.
# These map 1:1 onto gameplay_state's own verdicts — this module never
# invents a softer category to let more frames through.
REJECTION_LABELS = {
    "no-hud": "desk / transition / player-cam / full-screen graphic "
              "(no HUD structure at the layout's coordinates)",
    "partial-hud": "scoreboard or partially-covered HUD (one side only)",
    "replay": "replay / highlight / intermission banner",
    "unreadable": "frame could not be decoded",
    "anchor-miss": "HUD anchor template did not match",
}


def log(msg: str) -> None:
    print(f"[segmentation] {msg}", flush=True)


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------- candidate generation
def _frame_offset(path: str) -> int:
    return int(os.path.splitext(os.path.basename(path))[0])


def classify_sample(frame, layout: dict, *, anchor=None, replay=None,
                    rejects=None) -> tuple[bool, str, float, str]:
    """(is_gameplay, reason, score, method) for ONE frame.

    Prefers `gameplay_state.classify_frame` — the SAME structural
    chip-row/portrait-texture probe production detection (`ingest_map.py`)
    trusts — whenever the layout carries a calibrated `hud_probe`. That
    matters for correctness, not just tidiness: the only fully-proven real
    layout in this repo (`owcs_jksix_qwc`) has no anchor template at all, so
    an anchor-only classifier could not segment it.

    Falls back to `capture.is_gameplay`'s anchor-template check for layouts
    that carry an anchor but no calibrated probe (the demo/starter profiles).
    Neither path is loosened: a frame that either classifier rejects is
    rejected, with the reason recorded verbatim.
    """
    probe = layout.get("hud_probe") or {}
    if probe.get("chips_a") and probe.get("chips_b"):
        min_chips = probe.get("min_chips_per_side", gs.DEFAULT_MIN_CHIPS)
        state, reason = gs.classify_frame(frame, layout, min_chips=min_chips)
        measured = gs.probe_hud(frame, layout)
        # Score = confirmed chips out of 10, the structural evidence strength.
        score = (measured["chips"].get("a", 0)
                 + measured["chips"].get("b", 0)) / 10.0
        return (state == "gameplay", state if state != "gameplay" else reason,
                score, "hud-structural-probe")
    if anchor is not None:
        ok, reason, score = capture.is_gameplay(frame, anchor, replay,
                                               rejects or [])
        return ok, (reason or ("gameplay" if ok else "anchor-miss")), \
            float(score), "hud-anchor-template"
    raise ValueError(
        "layout can classify neither structurally nor by template: it needs "
        "a calibrated 'hud_probe' (from pipeline/calibrate_source.py) or an "
        "'anchor' region + template crop")


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
    """Sample `video_path` every `interval`s, classify each sample with the
    same gameplay-state rules production detection uses (see
    `classify_sample`), detect scene changes between consecutive samples, and
    group adjacent gameplay samples into candidate map windows expanded by
    pre/post-roll.

    `video_path` should be the 360p SCAN PROXY: every measurement here is on
    layout fractions, so resolution changes speed and not verdicts.

    Returns a list of:
        {"start_time", "end_time", "confidence", "thumbnails": [...],
         "signals": {..., "rejections": {label: count}}}

    Never raises on a single unreadable frame (skips it, records it as a
    rejection); raises only when the layout can classify nothing at all, or
    ffmpeg/the video itself is unusable (propagated from extract_frames_fn).
    """
    os.makedirs(out_dir, exist_ok=True)
    thumbs = sorted(extract_frames_fn(video_path, out_dir, interval))
    anchor = capture._load_template(layout, "anchor")
    replay = capture._load_template(layout, "replay")
    rejects = capture._load_reject_markers(layout)

    samples: list[dict] = []
    prev_gray = None
    scaled_layout = layout
    scaled = False
    method = None
    for path in thumbs:
        offset = _frame_offset(path)
        frame = cv2.imread(path)
        if frame is None:
            samples.append({"offset": offset, "gameplay": False,
                            "reason": "unreadable", "score": 0.0,
                            "sceneChange": False, "frame": path})
            continue
        if not scaled:
            h, w = frame.shape[:2]
            scaled_layout, info = capture.scale_layout_to_frame(layout, w, h)
            if not info["ok"]:
                raise ValueError(
                    f"layout cannot be applied to these frames: {info['reason']}")
            anchor = capture._load_template(scaled_layout, "anchor") or anchor
            replay = capture._load_template(scaled_layout, "replay")
            rejects = capture._load_reject_markers(scaled_layout)
            scaled = True
        ok, reason, score, method = classify_sample(
            frame, scaled_layout, anchor=anchor, replay=replay, rejects=rejects)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        scene_change = False
        if prev_gray is not None and prev_gray.shape == gray.shape:
            diff = cv2.absdiff(prev_gray, gray)
            scene_change = float(diff.mean()) > 40.0  # coarse cut/replay-swap signal
        prev_gray = gray
        samples.append({"offset": offset, "gameplay": ok, "reason": reason,
                        "score": round(float(score), 4),
                        "sceneChange": scene_change, "frame": path})

    return _group_candidates(samples, interval=interval, pre_roll=pre_roll,
                             post_roll=post_roll, gap_tolerance=gap_tolerance,
                             min_candidate_seconds=min_candidate_seconds,
                             method=method or "no-samples")


def _rejection_label(reason: str) -> str:
    """Map a classifier verdict onto the operator-facing rejection label. An
    unrecognized reason is passed through verbatim rather than bucketed into
    something friendlier — an unexplained rejection must stay visible."""
    for key, label in REJECTION_LABELS.items():
        if reason == key or reason.startswith(key):
            return label
    if "replay marker" in reason or "OCR guard" in reason:
        return REJECTION_LABELS["replay"]
    if reason.startswith("chips "):
        return REJECTION_LABELS["no-hud"]
    return reason


def _pick_thumbnails(samples: list[dict], n: int = THUMBNAILS_PER_CANDIDATE
                     ) -> list[dict]:
    """`n` evenly-spread gameplay frames from a candidate range — the first,
    the middle and the last, so a reviewer sees a map's start, mid and end
    rather than three near-identical frames."""
    gameplay = [s for s in samples if s.get("gameplay") and s.get("frame")]
    if not gameplay:
        return []
    if len(gameplay) <= n:
        picks = gameplay
    else:
        idxs = [round(i * (len(gameplay) - 1) / (n - 1)) for i in range(n)]
        picks = [gameplay[i] for i in sorted(set(idxs))]
    repo_root = os.path.dirname(_PIPELINE_DIR)
    out = []
    for s in picks:
        try:
            rel = os.path.relpath(s["frame"], repo_root).replace("\\", "/")
        except ValueError:            # different drive on Windows
            rel = s["frame"].replace("\\", "/")
        out.append({"offset": s["offset"], "path": rel,
                    "score": s["score"], "reason": s["reason"]})
    return out


def _group_candidates(samples: list[dict], *, interval: int, pre_roll: int,
                      post_roll: int, gap_tolerance: int,
                      min_candidate_seconds: int,
                      method: str = "hud-structural-probe") -> list[dict]:
    """Pure grouping logic, split out so tests can drive it directly with
    synthetic sample lists (no real video/ffmpeg needed).

    Also accounts for EVERY rejected sample: `rejections` on each candidate
    covers the samples inside its window, and the returned candidates never
    silently absorb a desk/replay/scoreboard span — that is what keeps
    coverage honest instead of inflated.
    """
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
        rejections: dict[str, int] = {}
        for s in r:
            if s["gameplay"]:
                continue
            label = _rejection_label(s["reason"])
            rejections[label] = rejections.get(label, 0) + 1
        candidates.append({
            "start_time": float(start), "end_time": float(end),
            "confidence": round(avg_score, 4),
            "thumbnails": _pick_thumbnails(r),
            "signals": {
                "method": method,
                "intervalSeconds": interval,
                "samplesInRange": len(r),
                "gameplaySamples": len(gameplay_samples),
                "avgGameplayScore": round(avg_score, 4),
                # kept under its historical name too, so any existing
                # report/consumer reading avgAnchorScore keeps working
                "avgAnchorScore": round(avg_score, 4),
                "sceneChanges": scene_changes,
                "preRollSeconds": pre_roll,
                "postRollSeconds": post_roll,
                "rejections": rejections,
            },
        })
    return candidates


def rejection_summary(samples: list[dict]) -> dict:
    """Every sample the classifier refused, grouped by reason — the honest
    denominator behind "how much of this VOD is gameplay". Used by the intake
    panel so an operator can see WHY a long stretch produced no candidate."""
    out: dict[str, dict] = {}
    for s in samples:
        if s.get("gameplay"):
            continue
        label = _rejection_label(s.get("reason") or "unknown")
        entry = out.setdefault(label, {"count": 0, "offsets": []})
        entry["count"] += 1
        if len(entry["offsets"]) < 12:
            entry["offsets"].append(s.get("offset"))
    return out


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
                signals, thumbnails, review_status, source_job_key,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (video_id, candidate_match_id, c["start_time"], c["end_time"],
             c["confidence"], json.dumps(c.get("signals") or {}),
             json.dumps(c.get("thumbnails") or []),
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

    `source_media_path` MUST be the full-resolution 720p source, never the
    360p scan proxy: this clip is what hero-portrait template matching reads,
    and template matching is the one stage that genuinely needs real pixels.
    Scanning uses the proxy (see `worker.scan_path_for`); extraction does not.
    A path that looks like a proxy is refused outright rather than quietly
    producing a low-resolution clip that would depress every later detection
    score for reasons nobody could see.
    """
    seg = _require(con, segment_id)
    if seg["review_status"] != "approved":
        raise ValueError(
            f"segment {segment_id} is {seg['review_status']!r}, not "
            f"'approved' — extraction requires human approval first")
    if ".proxy" in os.path.basename(source_media_path):
        raise ValueError(
            f"refusing to extract a detection clip from the scan proxy "
            f"({os.path.basename(source_media_path)}) — pass the "
            f"full-resolution source (worker.source_path_for)")
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
