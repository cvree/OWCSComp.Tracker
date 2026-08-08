#!/usr/bin/env python3
"""
calibrate_remote.py — calibrate a broadcast's HUD without downloading it.

This is the default calibration workflow. It replaces "download nine hours
of 720p video, extract a handful of frames, throw the rest away" with an
adaptive sparse scan that fetches only the frames it turns out to need.

THE LOOP
--------
    60s ladder  →  screen for HUD evidence  →  enough clean, DIVERSE frames?
        │                                            │ yes → calibrate → stop
        │ no
        └→ 30s, in the gaps only
            └→ 15s, ONLY around offsets that already showed HUD structure

Three ideas do the work:

**Densify where it can pay off.** A broadcast that is mostly desk and
replays does not get denser everywhere — the second densification only
looks near offsets that already showed chip structure, because HUD frames
cluster: if 32:00 had half a HUD, 32:15 probably has a whole one, whereas
nothing near the pre-show is going to improve by sampling it twice as hard.

**Stop early.** Calibration wants roughly a dozen good frames spread across
the broadcast, not a thousand. Once there is enough diverse evidence AND a
trial calibration clears the confidence floor with margin, acquisition
stops. Scanning a whole broadcast after the answer is already known is just
a slower way to get the same layout.

**Diversity, not just count.** Twelve frames from one teamfight measure one
lighting condition and one team colour. The scan tracks how many distinct
regions of the broadcast contributed, and refuses to call itself finished
on a single cluster.

FILTERING, AND THE CHICKEN AND EGG
----------------------------------
`gameplay_state.classify_frame` is the project's real filter, and it needs
a calibrated layout — which is what we are trying to produce. So this runs
in two passes, and the second one is the real one:

  * **Screen** (layout-free): a frame is a candidate if
    `calibrate_source.find_chip_blobs` — the exact detector calibration
    itself uses — finds chip structure on both sides. This is a candidate
    filter, never a verdict.
  * **Verify** (the existing filter, unchanged): once a provisional layout
    exists, every acquired frame is re-classified by
    `gameplay_state.classify_frame` against it, and only frames it calls
    `gameplay` survive. Desk segments, replays, scoreboards and breaks are
    rejected by the same code production detection trusts.

The surviving frames are written to a directory and handed to
`calibrate_source.py --frames-dir` — the real CLI, in-process, with its own
confidence floor, refusal, marker preservation, annotated sheet and
provenance. Nothing about calibration is re-implemented or relaxed here;
this module only decides which frames it gets to see.

Usage:
  python pipeline/calibrate_remote.py --url "https://youtu.be/..." \\
      --source-id owcs-jksix-qwc --out layouts/owcs_jksix_qwc.json
  python pipeline/calibrate_remote.py --source owcs-jksix-qwc --out ...
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proc_text  # noqa: E402
import calibrate_source as cs  # noqa: E402
import capture  # noqa: E402
import gameplay_state as gs  # noqa: E402
import remote_frames as rf  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The densification ladder, coarsest first. 60 seconds is the documented
#: starting point; each rung is only reached when the one above it did not
#: produce enough clean, diverse evidence.
DEFAULT_LADDER = (60.0, 30.0, 15.0)

#: Enough frames to fit a chip grid with outlier rejection and still have
#: agreement to measure. calibrate_source itself needs 2; a dozen is what
#: makes the medians stable.
TARGET_CLEAN_FRAMES = 12

#: ...spread over at least this many distinct regions of the broadcast, so
#: the layout is not fitted to one teamfight's lighting.
TARGET_REGIONS = 4

#: How the broadcast is divided into regions for that diversity test.
REGION_COUNT = 8

#: Stop when a trial calibration clears the existing floor by this much. A
#: bare pass is worth one more densification; a comfortable pass is not.
STOP_MARGIN = 0.08

#: Never scan more than this many samples, whatever the ladder says. A guard
#: against a pathological broadcast turning a sparse scan into a download.
DEFAULT_MAX_SAMPLES = 400

#: Only the second densification is targeted; the first is a plain global
#: halving, because with very few hits there is nothing to target yet.
NEIGHBOURHOOD = 90.0     # seconds either side of a promising offset


def log(msg: str) -> None:
    print(f"[calib-remote] {msg}", flush=True)


# ------------------------------------------------------------- screening
def screen_frame(path: str) -> dict:
    """Layout-free candidate screen: does this frame show chip structure?

    Uses `calibrate_source.find_chip_blobs`, the same detector calibration
    fits its grid to — so a frame that passes here is a frame calibration
    can actually use, and the screen can never disagree with the thing it
    is feeding. Returns evidence, not a verdict.
    """
    img = cv2.imread(path)
    if img is None:
        return {"ok": False, "reason": "unreadable", "left": 0, "right": 0}
    h, w = img.shape[:2]
    blobs = cs.find_chip_blobs(img)
    left = sum(1 for b in blobs if b[0] + b[2] / 2 < w / 2)
    right = sum(1 for b in blobs if b[0] + b[2] / 2 >= w / 2)
    # Both sides must show something. One-sided structure is a scoreboard,
    # a player cam or a graphic that happens to saturate — promising enough
    # to densify around, not good enough to calibrate on.
    ok = left >= 2 and right >= 2
    return {"ok": ok, "left": left, "right": right,
            "promising": (left + right) >= 2,
            "size": (w, h),
            "reason": ("chip structure both sides" if ok
                       else f"chips l:{left} r:{right} — not both sides")}


def verify_frames(paths: list[str], layout: dict) -> tuple[list[str], list[tuple[str, str]]]:
    """The real filter: `gameplay_state.classify_frame` against a layout.

    Returns (gameplay_paths, [(path, reason), ...]) — the rejects keep their
    reason so a refusal can explain itself.
    """
    kept, rejected = [], []
    scaled_cache: dict[tuple[int, int], dict] = {}
    for p in paths:
        img = cv2.imread(p)
        if img is None:
            rejected.append((p, "unreadable"))
            continue
        h, w = img.shape[:2]
        if (w, h) not in scaled_cache:
            scaled, info = capture.scale_layout_to_frame(layout, w, h)
            if not info["ok"]:
                rejected.append((p, info["reason"]))
                continue
            scaled_cache[(w, h)] = dict(scaled)
        state, reason = gs.classify_frame(img, scaled_cache[(w, h)])
        if state == "gameplay":
            kept.append(p)
        else:
            rejected.append((p, f"{state}: {reason}"))
    return kept, rejected


# ------------------------------------------------------- diversity + stop
def region_of(t: float, duration: float) -> int:
    if duration <= 0:
        return 0
    return min(REGION_COUNT - 1, int((t / duration) * REGION_COUNT))


def enough_evidence(clean: dict[float, str], duration: float) -> tuple[bool, str]:
    """Enough clean frames, spread widely enough to trust."""
    if len(clean) < TARGET_CLEAN_FRAMES:
        return False, (f"{len(clean)}/{TARGET_CLEAN_FRAMES} clean frames")
    regions = {region_of(t, duration) for t in clean}
    if len(regions) < TARGET_REGIONS:
        return False, (f"{len(clean)} clean frames but only {len(regions)}/"
                       f"{TARGET_REGIONS} regions of the broadcast — too "
                       f"clustered to calibrate from")
    return True, (f"{len(clean)} clean frames across {len(regions)} regions")


def pick_diverse(clean: dict[float, str], duration: float,
                 limit: int = 16) -> list[str]:
    """At most `limit` frames, spread as evenly as the evidence allows.

    Round-robin across regions rather than "the first sixteen", so a long
    scan does not calibrate on the pre-show just because it sampled it
    first."""
    buckets: dict[int, list[float]] = {}
    for t in sorted(clean):
        buckets.setdefault(region_of(t, duration), []).append(t)
    picked: list[float] = []
    while len(picked) < limit and any(buckets.values()):
        for r in sorted(buckets):
            if buckets[r] and len(picked) < limit:
                picked.append(buckets[r].pop(0))
    return [clean[t] for t in sorted(picked)]


# ------------------------------------------------------------- the ladder
#: How many samples to fetch before asking "is that enough yet?".
#:
#: This number is the difference between a sparse scan and a slow download.
#: A nine-hour broadcast has 540 offsets on the 60-second ladder; fetching
#: all of them before evaluating would cost ~135 MB to answer a question
#: that twenty-four well-spread frames usually settle. Evidence is checked
#: after every chunk, so the scan's cost tracks how quickly it finds what
#: it needs rather than how long the broadcast is.
CHUNK = 24


def spread_first(offsets: list[float]) -> list[float]:
    """Re-order a ladder so any prefix of it still covers the whole span.

    Taking the first twenty-four offsets of a nine-hour broadcast in time
    order samples the first twenty-four minutes — all pre-show, no
    gameplay, and no diversity. Recursive bisection instead visits the
    ends, then the middle, then the quarters, so the first chunk is a
    coarse sweep of the ENTIRE broadcast and the next chunk refines it.
    Same set of offsets, and every one is still eventually visited.
    """
    if len(offsets) <= 2:
        return list(offsets)
    out: list[float] = []
    seen: set[float] = set()

    def take(i: int) -> None:
        if 0 <= i < len(offsets) and offsets[i] not in seen:
            seen.add(offsets[i])
            out.append(offsets[i])

    take(0)
    take(len(offsets) - 1)
    spans = [(0, len(offsets) - 1)]
    while spans:
        nxt = []
        for lo, hi in spans:
            mid = (lo + hi) // 2
            if mid != lo and mid != hi:
                take(mid)
                nxt.append((lo, mid))
                nxt.append((mid, hi))
        spans = nxt
    for t in offsets:                 # anything bisection missed
        take(offsets.index(t))
    return out


def next_offsets(interval: float, start: float, end: float,
                 already: set[float]) -> list[float]:
    # `t < end`, never `<=`: the instant AT the end of a VOD has no frame,
    # and asking for it costs a read that can only fail.
    out = []
    t = start
    while t < end:
        r = round(t, 1)
        if r not in already:
            out.append(r)
        t += interval
    return out


def targeted_offsets(interval: float, promising: list[float],
                     start: float, end: float,
                     already: set[float]) -> list[float]:
    """Densify ONLY around offsets that already showed HUD structure.

    This is the "only densify further where required" rule. HUD frames
    cluster — a broadcast is minutes of play, then minutes of desk — so
    halving the rate near a half-seen HUD is likely to find a whole one,
    while halving it over the pre-show cannot."""
    out: set[float] = set()
    for p in promising:
        lo = max(start, p - NEIGHBOURHOOD)
        hi = min(end, p + NEIGHBOURHOOD)
        t = lo
        while t < hi:
            r = round(t, 1)
            if r not in already:
                out.add(r)
            t += interval
    return sorted(out)


# ------------------------------------------------------- gameplay windows
def gameplay_windows(clean_offsets: list[float], *, gap: float = 180.0,
                     min_span: float = 120.0) -> list[dict]:
    """Contiguous stretches of the broadcast that showed live gameplay.

    The sparse scan already knows roughly where the games are, and that is
    exactly the information the deep pass needs: it tells the next stage
    which 15-minute window to download instead of which nine hours. Runs
    separated by more than `gap` are different games."""
    ts = sorted(clean_offsets)
    if not ts:
        return []
    runs: list[list[float]] = [[ts[0]]]
    for t in ts[1:]:
        if t - runs[-1][-1] <= gap:
            runs[-1].append(t)
        else:
            runs.append([t])
    out = []
    for run in runs:
        span = run[-1] - run[0]
        if span < min_span and len(run) < 3:
            continue
        out.append({"start": run[0], "end": run[-1], "samples": len(run),
                    "spanSeconds": round(span, 1)})
    return out


# ----------------------------------------------------------------- driver
def acquire(source: rf.RemoteFrameSource, duration: float, *,
            start: float = 0.0, end: float | None = None,
            ladder=DEFAULT_LADDER, max_samples: int = DEFAULT_MAX_SAMPLES,
            target_clean: int = TARGET_CLEAN_FRAMES,
            trial_fn=None, source_id: str = "source") -> dict:
    """Run the adaptive scan. Returns everything the caller needs to decide."""
    end = duration if end is None else min(end, duration or end)
    seen: set[float] = set()
    clean: dict[float, str] = {}
    promising: list[float] = []
    screened: dict[float, dict] = {}
    passes: list[dict] = []
    stop_reason = "ladder exhausted"

    for rung, interval in enumerate(ladder, start=1):
        if len(seen) >= max_samples:
            stop_reason = f"sample budget reached ({max_samples})"
            break
        if rung <= 2 or not promising:
            offsets = next_offsets(interval, start, end, seen)
            scope = "whole broadcast"
        else:
            offsets = targeted_offsets(interval, promising, start, end, seen)
            scope = (f"{len(promising)} promising region(s) "
                     f"±{int(NEIGHBOURHOOD)}s")
        offsets = offsets[: max(0, max_samples - len(seen))]
        if not offsets:
            continue

        # Spread-first, in chunks: a prefix of this order already covers
        # the whole broadcast, so the scan can stop after twenty-four
        # frames instead of five hundred and forty.
        ordered = spread_first(offsets)
        log(f"pass {rung}: {interval:g}s over {scope} — "
            f"{len(ordered)} new sample(s), "
            f"{-(-len(ordered) // CHUNK)} chunk(s) of up to {CHUNK}")
        pass_rec = {"interval": interval, "requested": len(ordered),
                    "fetched": 0, "newClean": 0, "totalClean": len(clean),
                    "scope": scope, "chunks": 0, "evidence": ""}
        passes.append(pass_rec)
        satisfied = False

        for i in range(0, len(ordered), CHUNK):
            chunk = ordered[i:i + CHUNK]
            got = source.grab(chunk)
            seen.update(chunk)
            pass_rec["chunks"] += 1
            pass_rec["fetched"] += len(chunk)

            for t in chunk:
                path = got.get(t)
                if not path:
                    screened[t] = {"ok": False, "reason": "not acquired"}
                    continue
                ev = screen_frame(path)
                screened[t] = ev
                if ev["ok"]:
                    clean[t] = path
                    pass_rec["newClean"] += 1
                elif ev.get("promising"):
                    promising.append(t)

            ok, detail = enough_evidence(clean, end or duration or 1.0)
            pass_rec["totalClean"] = len(clean)
            pass_rec["evidence"] = detail
            log(f"pass {rung} chunk {pass_rec['chunks']}: "
                f"{len(clean)} clean so far — {detail}")
            if not ok:
                continue

            if trial_fn is None:
                stop_reason = f"enough evidence: {detail}"
                log(stop_reason)
                satisfied = True
                break
            frames = pick_diverse(clean, end or duration or 1.0)
            trial = trial_fn(frames)
            conf = float(trial.get("confidence") or 0.0)
            pass_rec["trialConfidence"] = round(conf, 3)
            if trial.get("ok") and conf >= cs.CONFIDENCE_FLOOR + STOP_MARGIN:
                stop_reason = (
                    f"stopped early after {len(seen)} sample(s): trial "
                    f"calibration reached {conf:.2f}, clear of the "
                    f"{cs.CONFIDENCE_FLOOR} floor by "
                    f"{conf - cs.CONFIDENCE_FLOOR:.2f}")
                log(stop_reason)
                satisfied = True
                break
            log(f"trial calibration {conf:.2f} — not clear of the floor by "
                f"{STOP_MARGIN}; keeping going")

        if satisfied:
            break

    return {"clean": clean, "screened": screened, "seen": sorted(seen),
            "promising": sorted(set(promising)), "passes": passes,
            "stopReason": stop_reason}


def select_and_calibrate(frames_by_offset: dict, source_id: str,
                         out_path: str, *, duration: float,
                         templates_dir: str | None = None,
                         sheet: str | None = None, force: bool = False,
                         stage_dir: str, precomputed: dict | None = None,
                         clean: dict | None = None) -> dict:
    """Turn acquired frames into a written layout.

    Deliberately acquisition-agnostic: it takes {offset: image path} and
    does not care whether those came from an HTTP range read or from a
    fully downloaded file. That is not incidental tidiness — it is what
    makes the benchmark honest, because the old and new paths can then be
    compared on acquisition alone with every downstream decision held
    identical.

    Screen → provisional layout → the REAL gameplay filter → hand the
    survivors to `calibrate_source.py --frames-dir`.
    """
    screened = dict(clean or {})
    if clean is None:
        screened = {t: p for t, p in frames_by_offset.items()
                    if screen_frame(p)["ok"]}
    if not screened:
        return {"ok": False, "fatal": True, "clean": {}, "verified": [],
                "used": {}, "windows": [], "confidence": 0.0,
                "exitCode": 2,
                "reason": ("no acquired frame showed HUD chip structure on "
                           "both sides — either this broadcast has no "
                           "ult-chip row, or none of these frames is live "
                           "play")}

    picked = pick_diverse(screened, duration)
    prov = None
    if precomputed and precomputed.get("key") == tuple(picked):
        log(f"provisional layout: reusing the scan's last trial "
            f"({len(picked)} frame(s)) — same frames, same answer")
        prov = precomputed["result"]
    if prov is None:
        log(f"provisional calibration from {len(picked)} screened frame(s)")
        prov = cs.calibrate(picked, source_id, templates_dir)
    if not prov.get("layout"):
        return {"ok": False, "fatal": True, "clean": screened, "verified": [],
                "used": {}, "windows": [], "exitCode": 2,
                "confidence": prov.get("confidence", 0.0),
                "reasons": prov.get("reasons", []),
                "reason": ("the screened frames did not yield a chip grid — "
                           + "; ".join(prov.get("reasons") or []))}

    verified, rejected = verify_frames(
        [frames_by_offset[t] for t in sorted(frames_by_offset)],
        prov["layout"])
    log(f"gameplay filter: {len(verified)} of {len(frames_by_offset)} "
        f"acquired frame(s) are live gameplay ({len(rejected)} rejected)")
    for _p, why in rejected[:6]:
        log(f"  rejected — {why}")
    if len(rejected) > 6:
        log(f"  ... and {len(rejected) - 6} more")

    if len(verified) < cs.MIN_GOOD_FRAMES:
        return {"ok": False, "fatal": True, "clean": screened,
                "verified": verified, "used": {}, "windows": [],
                "exitCode": 2, "confidence": 0.0,
                "reason": (f"only {len(verified)} acquired frame(s) survived "
                           f"the gameplay filter (need at least "
                           f"{cs.MIN_GOOD_FRAMES}). Structure was found but "
                           f"the existing filter did not agree it was live "
                           f"play.")}

    keep = set(verified)
    used = {t: p for t, p in frames_by_offset.items() if p in keep}
    _stage(sorted(used.items()), stage_dir)
    log(f"staged {len(used)} verified frame(s) -> {stage_dir}")

    argv = ["--frames-dir", stage_dir, "--source-id", source_id,
            "--out", out_path]
    if sheet:
        argv += ["--sheet", sheet]
    if templates_dir:
        argv += ["--templates-dir", templates_dir]
    if force:
        argv += ["--force"]
    log("handing off to calibrate_source.py --frames-dir "
        "(its confidence floor, refusal and sheet are unchanged)")
    code = cs.main(argv)

    conf = None
    if os.path.exists(out_path):
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                conf = json.load(f).get("calibration", {}).get("confidence")
        except (OSError, ValueError):
            conf = None

    return {"ok": code == 0, "fatal": False, "exitCode": code,
            "confidence": conf, "clean": screened, "verified": verified,
            "used": used, "windows": gameplay_windows(sorted(used)),
            "reason": None if code == 0 else
            "calibrate_source refused this layout (see its reasons above)"}


def run(url: str, source_id: str, out_path: str, *,
        duration: float | None = None, start: float = 0.0,
        end: float | None = None, height: int = rf.DEFAULT_HEIGHT,
        cache_root: str | None = None, frames_dir: str | None = None,
        ladder=DEFAULT_LADDER, max_samples: int = DEFAULT_MAX_SAMPLES,
        sheet: str | None = None, templates_dir: str | None = None,
        force: bool = False, resolver=None, runner=None,
        probe_fn=None, windows_out: str | None = None) -> dict:
    """The whole workflow: scan → screen → calibrate → verify → recalibrate."""
    t0 = time.monotonic()
    src = rf.RemoteFrameSource(
        url, height=height, cache_root=cache_root, source_id=source_id,
        resolver=resolver, **({"runner": runner} if runner else {}))

    if duration is None:
        duration = _probe_duration(url, probe_fn)
    if not duration:
        return {"ok": False, "reason":
                "could not determine the broadcast's duration, so there is "
                "nothing to sample across. Pass --duration."}
    log(f"broadcast {src.key}: {duration:.0f}s "
        f"({duration / 3600:.1f}h) · sampling <={height}p video-only")

    work = frames_dir or os.path.join(REPO_ROOT, "work", "calib",
                                      source_id, "frames")
    trial_dir = work + "_trial"

    # Calibration is the expensive step (a RANSAC grid fit over every
    # frame), and the scan's last trial is usually run on exactly the frame
    # set the provisional pass would use. Remembering it turns three full
    # calibrations into two.
    last_trial: dict = {}

    def trial(frame_paths: list[str]) -> dict:
        """A cheap in-process calibration used only to decide whether to
        stop acquiring. It never writes a layout."""
        res = cs.calibrate(frame_paths, source_id, templates_dir)
        last_trial.clear()
        last_trial.update({"key": tuple(frame_paths), "result": res})
        return res

    scan = acquire(src, duration, start=start, end=end, ladder=ladder,
                   max_samples=max_samples, trial_fn=trial,
                   source_id=source_id)
    clean = scan["clean"]
    if not clean:
        return {"ok": False, "scan": scan, "acquisition": src.report(),
                "reason": ("no frame in the sparse scan showed HUD chip "
                           "structure on both sides. Either this broadcast "
                           "uses a package with no ult-chip row, or the "
                           "sampled window contains no live play — try "
                           "--start/--end around a game.")}

    # ---- everything from here is acquisition-agnostic --------------------
    acquired = {t: p for t, p in ((t, src.cached(t)) for t in scan["seen"]) if p}
    sel = select_and_calibrate(
        acquired, source_id, out_path, duration=(end or duration),
        templates_dir=templates_dir, sheet=sheet, force=force,
        stage_dir=work, precomputed=last_trial, clean=clean)
    if not sel["ok"] and sel.get("fatal"):
        shutil.rmtree(trial_dir, ignore_errors=True)
        return {"ok": False, "scan": scan, "acquisition": src.report(),
                "confidence": sel.get("confidence", 0.0),
                "reasons": sel.get("reasons", []),
                "reason": sel["reason"]}

    verified = sel["verified"]
    by_offset = sel["used"]
    code = sel["exitCode"]
    conf = sel["confidence"]
    windows = sel["windows"]
    if windows_out:
        os.makedirs(os.path.dirname(os.path.abspath(windows_out)),
                    exist_ok=True)
        with open(windows_out, "w", encoding="utf-8") as f:
            json.dump({"schema": "gameplay-windows.v1", "sourceId": source_id,
                       "videoKey": src.key, "durationSeconds": duration,
                       "windows": windows,
                       "note": ("Stretches of the broadcast that showed live "
                                "gameplay in the sparse scan. Download one of "
                                "these for the deep pass instead of the whole "
                                "VOD.")}, f, indent=1)
        log(f"gameplay windows -> {windows_out}")

    shutil.rmtree(trial_dir, ignore_errors=True)
    return {
        "ok": code == 0,
        "exitCode": code,
        "confidence": conf,
        "layoutPath": out_path if code == 0 else None,
        "framesAcquired": len(scan["seen"]),
        "framesScreenedClean": len(sel["clean"]),
        "framesVerifiedGameplay": len(verified),
        "framesUsed": len(by_offset),
        "gameplayWindows": windows,
        "scan": scan,
        "acquisition": src.report(),
        "wallSeconds": round(time.monotonic() - t0, 1),
    }


def _stage(items, out_dir: str) -> None:
    """Copy the surviving frames into a --frames-dir, named by offset.

    PNG because that is what calibrate_source's --frames-dir reads, and
    because re-encoding a JPEG once at the very end costs nothing next to
    the transfer this whole module exists to avoid."""
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)
    for t, path in items:
        img = cv2.imread(path)
        if img is None:
            continue
        cv2.imwrite(os.path.join(out_dir, f"calib_{int(round(t)):06d}.png"),
                    img)


def _probe_duration(url: str, probe_fn=None) -> float:
    if probe_fn is not None:
        return float(probe_fn(url) or 0.0)
    try:
        import video_ingest as vi
        return float(vi.probe_vod(url).get("duration") or 0.0)
    except Exception as exc:      # noqa: BLE001 — a probe failure is data
        log(f"could not probe the broadcast duration ({exc!r})")
        return 0.0


# ------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    proc_text.enable_utf8_stdio()
    ap = argparse.ArgumentParser(
        description="Calibrate a broadcast HUD from sparse remote frames — "
                    "without downloading the VOD")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="broadcast URL")
    g.add_argument("--source", help="source id from video_sources.json")
    ap.add_argument("--source-id", help="calibration id "
                    "(defaults to --source, or the video key)")
    ap.add_argument("--out", required=True, help="layouts/<profile>.json")
    ap.add_argument("--duration", type=float,
                    help="broadcast length in seconds (probed when omitted)")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float)
    ap.add_argument("--height", type=int, default=rf.DEFAULT_HEIGHT)
    ap.add_argument("--interval", type=float, default=DEFAULT_LADDER[0],
                    help="starting sample interval in seconds (default 60)")
    ap.add_argument("--max-samples", type=int, default=DEFAULT_MAX_SAMPLES)
    ap.add_argument("--frames-dir", help="where to stage the surviving "
                    "frames (default work/calib/<source>/frames)")
    ap.add_argument("--cache-root", help="frame cache root "
                    "(default work/remote_frames)")
    ap.add_argument("--sheet")
    ap.add_argument("--templates-dir")
    ap.add_argument("--windows-out", help="write the gameplay windows the "
                    "scan found (feeds the deep pass)")
    ap.add_argument("--force", action="store_true",
                    help="write the layout even below the confidence floor")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    url = args.url
    source_id = args.source_id
    if args.source:
        import video_ingest as vi
        src = vi.find_source(vi.DEFAULT_SOURCES, args.source)
        if not src:
            log(f"no source id '{args.source}' in {vi.DEFAULT_SOURCES}")
            return 1
        url = src.get("url") or src.get("vodUrl")
        source_id = source_id or args.source
    source_id = source_id or rf.video_key(url)

    ladder = tuple(x for x in (args.interval, args.interval / 2,
                               args.interval / 4) if x >= 1)
    res = run(url, source_id, args.out, duration=args.duration,
              start=args.start, end=args.end, height=args.height,
              cache_root=args.cache_root, frames_dir=args.frames_dir,
              ladder=ladder, max_samples=args.max_samples, sheet=args.sheet,
              templates_dir=args.templates_dir, force=args.force,
              windows_out=args.windows_out)

    if args.json:
        print(json.dumps(res, indent=1, default=str))
        return 0 if res.get("ok") else 2

    acq = res.get("acquisition") or {}
    log("—— acquisition ——")
    log(f"  frames acquired: {res.get('framesAcquired', 0)} "
        f"({acq.get('framesFromCache', 0)} from cache)")
    log(f"  yt-dlp calls: {acq.get('ytdlpCalls', 0)} · "
        f"ffmpeg reads: {acq.get('ffmpegCalls', 0)} "
        f"({acq.get('rangeBatches', 0)} batched)")
    if acq.get("bytesDownloaded") is not None:
        log(f"  bytes over the wire: "
            f"{acq['bytesDownloaded'] / 1e6:.1f} MB")
    else:
        log("  bytes over the wire: not measurable on this platform")
    log(f"  {res.get('scan', {}).get('stopReason', '')}")
    if res.get("gameplayWindows"):
        log("—— gameplay windows found (download one of these, not the VOD) ——")
        for w in res["gameplayWindows"]:
            log(f"  {int(w['start'] // 3600)}:{int(w['start'] % 3600 // 60):02d}"
                f":{int(w['start'] % 60):02d}"
                f" → {int(w['end'] // 3600)}:{int(w['end'] % 3600 // 60):02d}"
                f":{int(w['end'] % 60):02d}  ({w['samples']} samples)")
    if not res.get("ok"):
        log(f"REFUSED — {res.get('reason') or 'see the reasons above'}")
        return 2
    log(f"OK — confidence {res.get('confidence')} -> {res.get('layoutPath')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
