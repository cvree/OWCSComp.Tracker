"""
layout_resolver.py — automatic reuse of a known broadcast package (Phase 3).

Before this existed, every job had to be told its `expectedLayoutId` by hand.
That is the wrong default: OWCS reuses the same broadcast overlay across a
whole stage, so the layout for a new VOD is almost always one already
committed under `layouts/`. This module decides which one, from evidence.

How a layout is FINGERPRINTED (no new detector, no new heuristics):
  A committed layout's `hud_probe` block records exactly where the two
  ult-charge chip rows and the ten hero portraits sit. `gameplay_state.
  probe_hud` already measures whether that structure is really present in a
  frame (chip saturation + portrait Laplacian texture), and it is the same
  measurement production detection trusts. So: sample representative frames
  from the scan proxy, run every candidate layout's probe over all of them,
  and score each layout by how often its OWN structure actually appears.
  A layout that belongs to this broadcast lights up on most gameplay frames;
  one that belongs to a different overlay package does not.

  This is deliberately a structural match rather than an image hash: a
  broadcast changes its background art, casters and ticker constantly, but
  the HUD geometry is the thing detection depends on, so the HUD geometry is
  what must match.

Decision policy (never a guess):
  score >= AUTO_REUSE_SCORE          -> reuse that committed layout
  otherwise                          -> calibrate a NEW layout with
                                        pipeline/calibrate_source.py
  calibration confidence < CONFIDENCE_FLOOR (calibrate_source's OWN existing
  threshold, never lowered here)     -> HARD REFUSAL, job -> NEEDS_LAYOUT
  calibration confidence >= floor    -> layout + review sheet written to
                                        reports/, job -> NEEDS_LAYOUT, and a
                                        human must run `approve-layout
                                        --job <id> --confirm` before the
                                        generated layout is ever used.

A generated layout is NEVER used automatically. Reuse of an ALREADY-COMMITTED
layout is automatic, because a human already approved that file when it was
committed.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_DIR = os.path.dirname(_HERE)
REPO_ROOT = os.path.dirname(_PIPELINE_DIR)
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

import capture  # noqa: E402
import site_paths  # noqa: E402  (cross-drive-safe relative paths)
import cv2  # noqa: E402
import gameplay_state as gs  # noqa: E402

from . import job_store as js  # noqa: E402
from . import models  # noqa: E402
from . import state_machine as sm  # noqa: E402

LAYOUTS_DIR = os.path.join(REPO_ROOT, "layouts")
REPORTS_DIR = os.path.join(REPO_ROOT, "reports", "layout")

# How many frames to fingerprint against. Enough to span desk/gameplay/replay
# variety in a multi-hour broadcast without turning resolution into a scan.
DEFAULT_SAMPLE_COUNT = 24
# A layout must reproduce its own HUD structure on at least this fraction of
# sampled frames to be reused automatically. A full broadcast is mostly NOT
# gameplay (desk, replays, breaks), so this is a deliberately achievable bar
# for the right layout and an unreachable one for a foreign overlay: a
# mismatched layout probes ~0 because its rects land on unrelated pixels.
AUTO_REUSE_SCORE = 0.15
# Below this, a match is too weak to distinguish from noise; calibrate.
MIN_CANDIDATE_SCORE = 0.05
# The winner must beat the runner-up by this much, or the result is ambiguous
# and goes to a human rather than being picked by a coin flip.
MIN_SCORE_MARGIN = 0.05

# Broadcast states worth harvesting a marker crop for. `gameplay` is included
# on purpose: an anchor template cut from a confirmed gameplay frame is what
# makes the cheap pre-OCR gameplay filter work for a NEW package.
MARKER_STATES = ("gameplay", "replay", "highlight", "scoreboard", "break",
                 "round_emblem")


def log(msg: str) -> None:
    print(f"[layout] {msg}", flush=True)


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


class LayoutRefusal(Exception):
    """A layout could not be resolved and no guess will be made. Carries a
    stable `code`."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


# ------------------------------------------------------------ candidate set
def layout_id_from_path(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def load_candidate_layouts(layouts_dir: str = LAYOUTS_DIR,
                           *, prefer: str | None = None) -> list[dict]:
    """Every committed layout that can actually be fingerprinted — i.e. one
    carrying a real `hud_probe` chip geometry. A layout without it (the
    placeholder starter profile) is skipped with a reason rather than scored
    on rects nobody has calibrated.

    `prefer` (e.g. a channel registry's `preferredLayout`) is moved to the
    front so the most likely package is evaluated first; order never changes
    the verdict, only the log.
    """
    out: list[dict] = []
    if not os.path.isdir(layouts_dir):
        return out
    for fn in sorted(os.listdir(layouts_dir)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(layouts_dir, fn)
        try:
            layout = capture.load_layout(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            log(f"skipping {fn}: unreadable ({exc})")
            continue
        probe = layout.get("hud_probe") or {}
        if not (probe.get("chips_a") and probe.get("chips_b")):
            continue
        if not (layout.get("slots_a") and layout.get("slots_b")):
            continue
        out.append({"layoutId": layout_id_from_path(path), "path": path,
                    "layout": layout})
    if prefer:
        out.sort(key=lambda c: 0 if c["layoutId"] == prefer else 1)
    return out


# ------------------------------------------------------------ frame sampling
def sample_frame_times(duration_seconds: float, count: int = DEFAULT_SAMPLE_COUNT
                       ) -> list[float]:
    """Evenly spread sample offsets across the MIDDLE of a broadcast.

    The first and last few percent of an OWCS VOD are reliably countdown/
    outro screens with no HUD at all, so sampling them would only depress
    every candidate's score equally while wasting decode time.
    """
    if duration_seconds <= 0 or count <= 0:
        return []
    lo = duration_seconds * 0.05
    hi = duration_seconds * 0.95
    if hi <= lo:
        return [duration_seconds / 2.0]
    step = (hi - lo) / count
    return [round(lo + step * (i + 0.5), 2) for i in range(count)]


def extract_sample_frames(video_path: str, out_dir: str, *,
                          count: int = DEFAULT_SAMPLE_COUNT,
                          duration_seconds: float | None = None,
                          runner=None) -> list[tuple[float, str]]:
    """Pull `count` representative frames out of a LOCAL video (the scan
    proxy) with plain local ffmpeg seeks. Returns [(offset, path), ...] for
    the frames that were really written — never raises on one bad seek."""
    import subprocess
    import video_ingest as vi
    runner = runner or subprocess
    os.makedirs(out_dir, exist_ok=True)
    if duration_seconds is None:
        info = vi.probe_clip_resolution(video_path, runner=runner) or {}
        duration_seconds = info.get("duration") or 0.0
    times = sample_frame_times(float(duration_seconds), count)
    out: list[tuple[float, str]] = []
    for t in times:
        path = os.path.join(out_dir, f"sample_{int(t):08d}.png")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-ss", str(t), "-i", video_path, "-frames:v", "1", path]
        try:
            runner.run(cmd, check=True, capture_output=True, text=True)
        except Exception:  # noqa: BLE001 — one unreadable offset is not fatal
            continue
        if os.path.exists(path) and os.path.getsize(path) > 0:
            out.append((t, path))
    return out


# ------------------------------------------------------------- fingerprinting
def fingerprint_layout(frames_bgr: list, layout: dict) -> dict:
    """Measure how strongly ONE layout's own HUD structure appears across
    frames. Pure measurement — no thresholds applied here, so a caller (or a
    test) can see the raw evidence behind any verdict.

    Returns {"frames", "gameplay", "partial", "noHud", "replay",
             "score", "chipRateA", "chipRateB", "medianTextureA/B"}.
    """
    counts = {"gameplay": 0, "partial-hud": 0, "no-hud": 0, "replay": 0}
    chips_a: list[float] = []
    chips_b: list[float] = []
    tex_a: list[float] = []
    tex_b: list[float] = []
    for frame in frames_bgr:
        h, w = frame.shape[:2]
        scaled, info = capture.scale_layout_to_frame(layout, w, h)
        if not info["ok"]:
            # An aspect-ratio mismatch means this layout cannot describe this
            # broadcast at all. Report score 0 with the reason instead of
            # probing unscaled rects, which would produce a meaningless
            # number that happens to look like a weak match.
            return {"frames": len(frames_bgr), "gameplay": 0, "partial": 0,
                    "noHud": len(frames_bgr), "replay": 0, "score": 0.0,
                    "chipRateA": 0.0, "chipRateB": 0.0,
                    "medianTextureA": 0.0, "medianTextureB": 0.0,
                    "note": info["reason"]}
        state, _reason = gs.classify_frame(frame, dict(scaled))
        counts[state] = counts.get(state, 0) + 1
        probe = gs.probe_hud(frame, scaled)
        chips_a.append(probe["chips"].get("a", 0) / 5.0)
        chips_b.append(probe["chips"].get("b", 0) / 5.0)
        tex_a.append(probe["portrait_tex"].get("a", 0.0))
        tex_b.append(probe["portrait_tex"].get("b", 0.0))
    n = max(len(frames_bgr), 1)

    def _median(xs):
        if not xs:
            return 0.0
        s = sorted(xs)
        mid = len(s) // 2
        return float(s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2)

    return {
        "frames": len(frames_bgr),
        "gameplay": counts["gameplay"],
        "partial": counts["partial-hud"],
        "noHud": counts["no-hud"],
        "replay": counts["replay"],
        # The score is the gameplay-confirmation rate: only a frame where
        # BOTH chip rows and BOTH portrait rows are structurally present at
        # this layout's coordinates counts. Partial credit is deliberately
        # withheld — half a HUD match is how a wrong layout sneaks in.
        "score": round(counts["gameplay"] / n, 4),
        "chipRateA": round(_median(chips_a), 4),
        "chipRateB": round(_median(chips_b), 4),
        "medianTextureA": round(_median(tex_a), 1),
        "medianTextureB": round(_median(tex_b), 1),
    }


def match_layouts(frames_bgr: list, candidates: list[dict]) -> dict:
    """Fingerprint every candidate and rank them.

    Returns {"ranked": [{layoutId, path, fingerprint}, ...] (best first),
             "best", "runnerUp", "margin", "decision", "reason"}.
    `decision` is one of: "reuse" / "ambiguous" / "no_match".
    """
    scored = []
    for cand in candidates:
        fp = fingerprint_layout(frames_bgr, cand["layout"])
        scored.append({"layoutId": cand["layoutId"], "path": cand["path"],
                       "fingerprint": fp})
    scored.sort(key=lambda s: s["fingerprint"]["score"], reverse=True)
    best = scored[0] if scored else None
    runner_up = scored[1] if len(scored) > 1 else None
    best_score = best["fingerprint"]["score"] if best else 0.0
    runner_score = runner_up["fingerprint"]["score"] if runner_up else 0.0
    margin = round(best_score - runner_score, 4)

    if not best or best_score < MIN_CANDIDATE_SCORE:
        decision, reason = "no_match", (
            f"no committed layout reproduced its own HUD structure on these "
            f"frames (best {best['layoutId'] if best else 'n/a'} scored "
            f"{best_score:.3f} < {MIN_CANDIDATE_SCORE}) — this looks like a "
            f"broadcast package the repo has never calibrated")
    elif best_score < AUTO_REUSE_SCORE:
        decision, reason = "no_match", (
            f"best candidate {best['layoutId']} scored {best_score:.3f}, "
            f"below the {AUTO_REUSE_SCORE} automatic-reuse bar — refusing to "
            f"reuse a layout on weak evidence")
    elif runner_up and margin < MIN_SCORE_MARGIN:
        decision, reason = "ambiguous", (
            f"{best['layoutId']} ({best_score:.3f}) and "
            f"{runner_up['layoutId']} ({runner_score:.3f}) are within "
            f"{margin:.3f} — too close to choose automatically")
    else:
        decision, reason = "reuse", (
            f"{best['layoutId']} reproduced its own HUD structure on "
            f"{best['fingerprint']['gameplay']}/{best['fingerprint']['frames']} "
            f"sampled frames (score {best_score:.3f}, margin {margin:.3f})")
    return {"ranked": scored, "best": best, "runnerUp": runner_up,
            "margin": margin, "decision": decision, "reason": reason}


# --------------------------------------------------------- marker harvesting
def harvest_markers(frames: list[tuple[float, str]], layout: dict,
                    out_dir: str) -> dict:
    """Sort sampled frames into the broadcast states a layout's rejection
    rules care about, and save one full frame per state as a candidate marker
    source.

    This does NOT write reject markers into a layout: cutting the exact
    banner rectangle out of a frame is a human judgement (it decides what the
    detector will reject forever). What it does is stop that human from
    having to hunt through hours of VOD — every state that occurred in the
    sample is on disk with its offset, ready to crop.
    """
    os.makedirs(out_dir, exist_ok=True)
    buckets: dict[str, list[dict]] = {s: [] for s in MARKER_STATES}
    for offset, path in frames:
        frame = cv2.imread(path)
        if frame is None:
            continue
        h, w = frame.shape[:2]
        scaled, info = capture.scale_layout_to_frame(layout, w, h)
        if not info["ok"]:
            continue
        state, reason = gs.classify_frame(frame, dict(scaled))
        # gameplay_state's vocabulary is deliberately small (it only decides
        # "may this frame feed the timeline"). Map it onto the marker states
        # an operator actually needs to cut, keeping the extra ones as
        # honestly-unclassified rather than inventing a detector for them.
        bucket = {"gameplay": "gameplay", "replay": "replay",
                  "partial-hud": "scoreboard", "no-hud": "break"}.get(state)
        if bucket is None:
            continue
        buckets[bucket].append({"offset": offset, "frame": path,
                                "state": state, "reason": reason})
    saved: dict[str, dict] = {}
    for state, rows in buckets.items():
        if not rows:
            continue
        pick = rows[len(rows) // 2]     # a mid-run example, not an edge case
        dest = os.path.join(out_dir, f"marker_{state}_{int(pick['offset'])}.png")
        shutil.copyfile(pick["frame"], dest)
        saved[state] = {"framePath": site_paths.site_relpath(dest, REPO_ROOT),
                        "offset": pick["offset"], "detectedState": pick["state"],
                        "reason": pick["reason"], "examples": len(rows)}
    missing = [s for s in MARKER_STATES if s not in saved]
    return {"harvested": saved, "missingStates": missing,
            "note": ("candidate marker frames only — a human crops the exact "
                     "banner rect; nothing here is written into a layout "
                     "automatically. round_emblem/highlight have no automatic "
                     "classifier, so they are always listed as missing and "
                     "must be cut by hand if that package needs them.")}


# ------------------------------------------------------------- calibration
def calibrate_new_layout(frames: list[tuple[float, str]], source_id: str, *,
                         out_dir: str) -> dict:
    """Run the EXISTING `pipeline/calibrate_source.py` over the sampled
    frames and write its layout + review sheet under `out_dir`.

    Nothing here re-implements or relaxes calibration: the confidence floor
    enforced below is `calibrate_source.CONFIDENCE_FLOOR` itself.
    """
    import calibrate_source as cs
    os.makedirs(out_dir, exist_ok=True)
    result = cs.calibrate([p for _t, p in frames], source_id)
    payload = {
        "sourceId": source_id,
        "confidence": round(float(result.get("confidence") or 0.0), 3),
        "floor": cs.CONFIDENCE_FLOOR,
        "ok": bool(result.get("ok")),
        "reasons": list(result.get("reasons") or []),
        "framesUsed": len(frames),
    }
    if not result.get("layout"):
        payload["refusal"] = (
            f"calibration produced no layout: "
            f"{'; '.join(payload['reasons']) or 'no chip rows found'}")
        return payload
    layout_path = os.path.join(out_dir, f"{source_id}.json")
    with open(layout_path, "w", encoding="utf-8") as f:
        json.dump(result["layout"], f, indent=1)
        f.write("\n")
    payload["layoutPath"] = site_paths.site_relpath(layout_path, REPO_ROOT)
    if result.get("frames_bgr"):
        sheet = os.path.join(out_dir, "sheet.png")
        try:
            cs.draw_sheet(result["frames_bgr"],
                          {"slots_a": result["boxes_a"],
                           "slots_b": result["boxes_b"]}, sheet)
            payload["reviewSheet"] = site_paths.site_relpath(sheet, REPO_ROOT)
        except Exception as exc:  # noqa: BLE001 — a missing sheet must not
            # lose a good calibration; the numbers are the evidence of record.
            payload["reviewSheetError"] = f"{type(exc).__name__}: {exc}"
    if payload["confidence"] < cs.CONFIDENCE_FLOOR:
        payload["refusal"] = (
            f"calibration confidence {payload['confidence']} is below the "
            f"existing floor {cs.CONFIDENCE_FLOOR} — REFUSING to propose this "
            f"layout: {'; '.join(payload['reasons']) or 'low-confidence fit'}")
    return payload


# ------------------------------------------------------------------ resolve
def resolve_layout(store: js.JobStore, job: models.Job, *,
                   scan_path: str | None = None,
                   layouts_dir: str = LAYOUTS_DIR,
                   reports_dir: str = REPORTS_DIR,
                   sample_count: int = DEFAULT_SAMPLE_COUNT,
                   preferred_layout: str | None = None,
                   worker_id: str | None = None,
                   frames: list[tuple[float, str]] | None = None,
                   harvest: bool = True) -> dict:
    """Resolve the broadcast layout for one DOWNLOADED job.

    Reads the 360p SCAN PROXY (never the full-resolution source — see
    `worker.scan_path_for`). Records the whole decision on the job payload
    under `layout` so it is auditable, then either leaves the job ready for
    segmentation (reuse) or moves it to NEEDS_LAYOUT (a human must approve a
    generated layout, or fix the refusal).
    """
    from . import worker as wk
    video_id = job.payload.get("videoId") or "unknown"
    scan_path = scan_path or wk.scan_path_for(job)
    if frames is None:
        if not scan_path or not os.path.exists(scan_path):
            raise LayoutRefusal(
                "no_scan_proxy",
                f"job {job.job_key} has no 360p scan proxy on disk "
                f"({scan_path or 'not recorded'}) — re-run the worker to "
                f"generate one; scanning never falls back to the full VOD")
    work_dir = os.path.join(reports_dir, video_id)
    if frames is None:
        media = job.payload.get("media") or {}
        frames = extract_sample_frames(
            scan_path, os.path.join(work_dir, "frames"),
            count=sample_count,
            duration_seconds=(media.get("proxy") or {}).get("durationSeconds")
            or media.get("durationSeconds"))
    if not frames:
        raise LayoutRefusal(
            "no_readable_frames",
            f"could not read a single sample frame from {scan_path}")

    frames_bgr = [f for f in (cv2.imread(p) for _t, p in frames) if f is not None]
    if not frames_bgr:
        raise LayoutRefusal(
            "no_readable_frames",
            f"every sampled frame from {scan_path} failed to decode")

    candidates = load_candidate_layouts(layouts_dir, prefer=preferred_layout)
    match = match_layouts(frames_bgr, candidates) if candidates else {
        "ranked": [], "best": None, "runnerUp": None, "margin": 0.0,
        "decision": "no_match",
        "reason": "no committed layout carries a calibrated hud_probe block"}

    record: dict = {
        "resolvedAt": _utcnow_iso(),
        "scanPath": (site_paths.site_relpath(scan_path, REPO_ROOT)
                     if scan_path else None),
        "framesSampled": len(frames_bgr),
        "candidates": [{"layoutId": r["layoutId"], "score": r["fingerprint"]["score"],
                        "gameplayFrames": r["fingerprint"]["gameplay"],
                        "chipRateA": r["fingerprint"]["chipRateA"],
                        "chipRateB": r["fingerprint"]["chipRateB"]}
                       for r in match["ranked"]],
        "decision": match["decision"],
        "reason": match["reason"],
        "margin": match["margin"],
        "autoReuseScore": AUTO_REUSE_SCORE,
    }

    if match["decision"] == "reuse":
        best = match["best"]
        record.update({
            "layoutId": best["layoutId"],
            "layoutPath": site_paths.site_relpath(best["path"], REPO_ROOT),
            "source": "committed-layout-reuse",
            "approvalRequired": False,
            "confidence": best["fingerprint"]["score"],
        })
        if harvest:
            reused_layout = capture.load_layout(best["path"])
            record["markers"] = harvest_markers(
                frames, reused_layout, os.path.join(work_dir, "markers"))
        store.update_payload(job.job_key, {
            "layout": record, "expectedLayoutId": best["layoutId"]})
        log(f"{job.job_key}: reusing committed layout {best['layoutId']} "
            f"— {match['reason']}")
        return {"ok": True, "layoutId": best["layoutId"], "record": record}

    # No usable committed layout -> calibrate a candidate for HUMAN approval.
    source_id = f"owcs_{video_id}".lower().replace("-", "_")
    calib = calibrate_new_layout(frames, source_id,
                                 out_dir=os.path.join(work_dir, "calibration"))
    record["calibration"] = calib
    record["source"] = "generated-calibration"
    record["approvalRequired"] = True
    if calib.get("refusal"):
        record["blocked"] = calib["refusal"]
        store.update_payload(job.job_key, {"layout": record})
        store.record_error(job.job_key, error_code="layout_calibration_refused",
                           error_message=calib["refusal"])
        if sm.can_transition(job.state, sm.NEEDS_LAYOUT):
            store.transition(job.job_key, sm.NEEDS_LAYOUT)
        log(f"{job.job_key}: REFUSED — {calib['refusal']}")
        return {"ok": False, "code": "layout_calibration_refused",
                "reason": calib["refusal"], "record": record}

    if harvest and calib.get("layoutPath"):
        try:
            generated = capture.load_layout(os.path.join(REPO_ROOT, calib["layoutPath"]))
            record["markers"] = harvest_markers(
                frames, generated, os.path.join(work_dir, "markers"))
        except (OSError, ValueError) as exc:
            record["markersError"] = f"{type(exc).__name__}: {exc}"

    store.update_payload(job.job_key, {"layout": record})
    if sm.can_transition(job.state, sm.NEEDS_LAYOUT):
        store.transition(job.job_key, sm.NEEDS_LAYOUT)
    log(f"{job.job_key}: calibrated a NEW layout at confidence "
        f"{calib['confidence']} — awaiting `approve-layout --job "
        f"{job.job_key} --confirm`")
    return {"ok": True, "needsApproval": True, "record": record}


# ------------------------------------------------------------ human approval
def approve_layout(store: js.JobStore, job_key: str, *, confirm: bool = False,
                   approved_by: str | None = None,
                   layouts_dir: str = LAYOUTS_DIR) -> dict:
    """`approve-layout --job <id> --confirm` — promote a GENERATED layout
    into `layouts/` so detection may use it.

    Requires `--confirm`; refuses a calibration that was itself refused for
    low confidence (there is no override that publishes a layout the
    calibrator would not stand behind). Copying the file into `layouts/` is
    the act of approval: from then on the layout is a committed, reusable
    broadcast package like any other.
    """
    job = store.get(job_key)
    if job is None:
        raise LayoutRefusal("no_such_job", f"no such job: {job_key}")
    record = job.payload.get("layout") or {}
    if not record:
        raise LayoutRefusal(
            "not_resolved",
            f"{job_key} has no layout resolution yet — run resolve-layout first")
    if not record.get("approvalRequired"):
        return {"ok": True, "layoutId": record.get("layoutId"),
                "note": "layout was an automatic reuse of an already-committed "
                        "package — nothing to approve"}
    if not confirm:
        raise LayoutRefusal(
            "confirmation_required",
            "pass --confirm — a generated layout is never approved by default")
    calib = record.get("calibration") or {}
    if calib.get("refusal"):
        raise LayoutRefusal(
            "calibration_refused",
            f"refusing to approve a refused calibration: {calib['refusal']}")
    rel = calib.get("layoutPath")
    if not rel or not os.path.exists(os.path.join(REPO_ROOT, rel)):
        raise LayoutRefusal(
            "layout_file_missing",
            f"generated layout file {rel!r} is not on disk")
    layout_id = calib["sourceId"]
    os.makedirs(layouts_dir, exist_ok=True)
    dest = os.path.join(layouts_dir, f"{layout_id}.json")
    shutil.copyfile(os.path.join(REPO_ROOT, rel), dest)
    approved = dict(record, approvalRequired=False, approvedAt=_utcnow_iso(),
                    approvedBy=approved_by, layoutId=layout_id,
                    layoutPath=site_paths.site_relpath(dest, REPO_ROOT),
                    source="approved-calibration")
    store.update_payload(job_key, {"layout": approved,
                                   "expectedLayoutId": layout_id})
    job = store.get(job_key)
    if job.state == sm.NEEDS_LAYOUT:
        store.transition(job_key, sm.PROCESSING)
    log(f"{job_key}: layout {layout_id} APPROVED by {approved_by} -> {dest}")
    return {"ok": True, "layoutId": layout_id, "layoutPath": dest,
            "state": store.get(job_key).state,
            "templatesDir": (calib.get("layoutPath") and
                             _templates_dir_of(dest))}


def _templates_dir_of(layout_path: str) -> str | None:
    try:
        with open(layout_path, encoding="utf-8") as f:
            return (json.load(f) or {}).get("templates_dir")
    except (OSError, ValueError):
        return None


def format_resolution(record: dict) -> str:
    lines = [f"  decision      : {record.get('decision')} — {record.get('reason')}",
             f"  frames sampled: {record.get('framesSampled')}"]
    for c in record.get("candidates", [])[:8]:
        lines.append(f"    {c['layoutId']:<28} score={c['score']:.3f} "
                     f"gameplay={c['gameplayFrames']} "
                     f"chips a/b={c['chipRateA']:.2f}/{c['chipRateB']:.2f}")
    if record.get("layoutId"):
        lines.append(f"  layout        : {record['layoutId']} "
                     f"({record.get('source')})")
    calib = record.get("calibration")
    if calib:
        lines.append(f"  calibration   : confidence {calib.get('confidence')} "
                     f"(floor {calib.get('floor')})")
        for r in calib.get("reasons", [])[:6]:
            lines.append(f"    warning     : {r}")
        if calib.get("layoutPath"):
            lines.append(f"    layout file : {calib['layoutPath']}")
        if calib.get("reviewSheet"):
            lines.append(f"    review sheet: {calib['reviewSheet']}")
    markers = record.get("markers") or {}
    for state, info in (markers.get("harvested") or {}).items():
        lines.append(f"  marker {state:<12}: {info['framePath']} "
                     f"(t={info['offset']:.0f}s, {info['examples']} example(s))")
    if markers.get("missingStates"):
        lines.append(f"  markers missing: {', '.join(markers['missingStates'])}")
    if record.get("blocked"):
        lines.append(f"  BLOCKED       : {record['blocked']}")
    if record.get("approvalRequired"):
        lines.append("  ACTION        : run `approve-layout --job <id> --confirm`")
    return "\n".join(lines)
