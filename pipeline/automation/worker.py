"""
worker.py — the self-hosted processing worker (Roadmap Phase E).

Handles ONE completed official VOD at a time: claims a ready (ARCHIVED —
"official broadcast linked, download not yet started") job, takes a lease on
its resource, validates the source is actually an approved official one,
downloads it with the EXISTING yt-dlp/ffmpeg wrappers (video_ingest.py /
download_vod_clip.py — this module never reimplements that machinery),
records real media metadata onto the job payload, and advances the job to
DOWNLOADED. Never records a live stream — that is explicitly out of scope for
this sprint (only completed official VODs); see docs/AUTOMATION.md "Phase E".

This is the first caller of `job_store.claim_next` / `locks.LockManager` in
the codebase (Phase A built the primitives; nothing had driven them yet).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import shutil
import socket
import subprocess
import sys
import uuid
from urllib.parse import urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_DIR = os.path.dirname(_HERE)
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)
import proc_text  # noqa: E402  (UTF-8 subprocess decoding)

# `video_ingest`/`download_vod_clip` transitively import cv2 (via
# capture.py). NEVER import them at module level — this module must stay
# importable, and every lightweight CLI command that merely references
# worker.* constants must stay runnable, on a machine with no OpenCV
# installed. Only `download_job` (and `classify_download_error`, best-
# effort) actually need them, lazily, only when a real download runs.

import ytdlp_opts  # noqa: E402  (stdlib-only: auth/redaction/classification)

from . import job_store as js
from . import locks as lk
from . import models
from . import state_machine as sm

WORKER_VERSION = "1.1.0"

# The state a job must return to when a retry is taken, keyed by the state
# it failed in. Recorded on the job at failure time (`resumeState`) so
# `retry-job` restores the RIGHT stage instead of guessing — and never
# rewinds past an audited human decision such as source approval.
RESUME_TARGETS: dict[str, str] = {
    sm.ARCHIVED: sm.ARCHIVED,
    sm.DOWNLOADING: sm.ARCHIVED,      # re-enter the download stage
    sm.DOWNLOADED: sm.DOWNLOADED,
    sm.SEGMENTING: sm.DOWNLOADED,     # re-segment from the downloaded media
    sm.PROCESSING: sm.READY_FOR_DETECTION,
    sm.READY_FOR_DETECTION: sm.READY_FOR_DETECTION,
    sm.APPROVED: sm.APPROVED,
}


def resume_target_for(state: str) -> str:
    """Where a job in `state` should resume after a retry. Defaults to
    ARCHIVED (the front of the automatic work) only for states that
    precede the download; anything else keeps its own stage."""
    return RESUME_TARGETS.get(state, sm.ARCHIVED)

# Only these domains are ever accepted as an "official" broadcast source —
# never an arbitrary URL, never a shell string, never an unofficial mirror.
#
# Twitch is here because it is the only source unattended hardware can
# actually fetch: a GitHub-hosted runner is bot-checked by YouTube on every
# player client and serves a Twitch VOD with no credential at all
# (measured — see docs/UNATTENDED.md). This list is deliberately kept as
# its own gate rather than deferring to link_intake: intake decides what
# may be RECORDED, this decides what may be FETCHED, and one should not be
# able to widen the other by accident.
YOUTUBE_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
})
TWITCH_HOSTS = frozenset({"twitch.tv", "www.twitch.tv", "m.twitch.tv"})
ALLOWED_HOSTS = YOUTUBE_HOSTS | TWITCH_HOSTS

REQUIRED_TOOLS = ("ffmpeg", "ffprobe", "yt-dlp")

# Where downloaded broadcast media lands. Repo-relative by default (what a
# source checkout and CI expect); OWCS_MEDIA_ROOT relocates it, which is how
# the installed Windows application keeps multi-gigabyte downloads on per-user
# writable storage instead of under Program Files. Same override shape as
# OWCS_DB / OWCS_AUTOMATION_DB elsewhere in the pipeline.
DEFAULT_MEDIA_ROOT = os.environ.get(
    "OWCS_MEDIA_ROOT",
    os.path.join(os.path.dirname(_PIPELINE_DIR), "data", "worker", "jobs"))
DEFAULT_MIN_FREE_GB = 5.0


def log(msg: str) -> None:
    print(f"[worker] {msg}", flush=True)


def worker_identity(prefix: str = "beta-worker") -> str:
    """A stable-enough-per-process identity for job/lock ownership. Not a
    secret, safe to print/log anywhere."""
    host = (socket.gethostname() or "unknown-host").split(".")[0]
    return f"{prefix}-{host}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------- dependencies
def check_dependencies(which=shutil.which) -> dict[str, str | None]:
    """{"ffmpeg": "/usr/bin/ffmpeg", "yt-dlp": None, ...} — every required
    tool's resolved path, or None if missing. Never raises."""
    return {tool: which(tool) for tool in REQUIRED_TOOLS}


def missing_dependencies(deps: dict[str, str | None]) -> list[str]:
    return [tool for tool, path in deps.items() if not path]


def tool_version(tool: str, *, runner=subprocess) -> str | None:
    """Best-effort version string for a diagnostic/metadata record. Returns
    None (never raises) if the tool is missing or refuses --version."""
    for flag in ("--version", "-version"):
        try:
            res = runner.run([tool, flag], capture_output=True, text=True,
                             timeout=15, **proc_text.PIPE_TEXT)
            out = (res.stdout or res.stderr or "").strip().splitlines()
            if out:
                return out[0][:200]
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return None


# --------------------------------------------------------------------- disk
def check_disk_space(path: str, min_free_gb: float = DEFAULT_MIN_FREE_GB
                     ) -> tuple[bool, float]:
    """(ok, free_gb) for the filesystem holding `path` (its nearest existing
    ancestor directory, so a not-yet-created media dir is still checkable)."""
    probe = path
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe.rstrip(os.sep))
        if parent == probe:
            break
        probe = parent
    probe = probe or "."
    usage = shutil.disk_usage(probe)
    free_gb = usage.free / (1024 ** 3)
    return free_gb >= min_free_gb, round(free_gb, 2)


# ------------------------------------------------- disk space estimation (P2)
# Observed video-only bitrates for YouTube VOD renditions, bits/second. These
# are deliberately GENEROUS (the high end of what a 60fps esports broadcast
# with a busy HUD actually produces) because the failure mode we must avoid is
# starting a multi-hour download that runs the disk out mid-way. A refusal
# before downloading is cheap; a full disk halfway through is not.
ESTIMATED_BITRATE_BPS: dict[int, int] = {
    360: 1_000_000,
    480: 2_000_000,
    720: 5_000_000,
    1080: 9_000_000,
    1440: 20_000_000,
    2160: 45_000_000,
}
# Multiply the estimate by this before comparing against free space: container
# overhead, a bitrate spike, and the scan proxy (~1/5 of the source) all have
# to fit alongside the source file.
DISK_SAFETY_FACTOR = 1.5
# Never let an estimate claim a broadcast needs less than this.
MIN_ESTIMATED_BYTES = 64 * 1024 * 1024


def estimated_bitrate_bps(height: int) -> int:
    """Bits/second for the nearest known rendition at or above `height`."""
    for h in sorted(ESTIMATED_BITRATE_BPS):
        if height <= h:
            return ESTIMATED_BITRATE_BPS[h]
    return ESTIMATED_BITRATE_BPS[max(ESTIMATED_BITRATE_BPS)]


def estimate_download_bytes(duration_seconds: float | None, height: int = 720,
                            *, safety_factor: float = DISK_SAFETY_FACTOR
                            ) -> int | None:
    """Bytes a full download of `duration_seconds` at `height` is expected to
    need, including the safety factor and the scan proxy. Returns None when
    the duration is unknown — the caller must then treat the requirement as
    UNKNOWN rather than assume it fits."""
    if not duration_seconds or duration_seconds <= 0:
        return None
    raw = (estimated_bitrate_bps(height) / 8.0) * float(duration_seconds)
    return int(max(raw * safety_factor, MIN_ESTIMATED_BYTES))


def disk_preflight(media_root: str, *, duration_seconds: float | None,
                   height: int = 720, min_free_gb: float = DEFAULT_MIN_FREE_GB,
                   safety_factor: float = DISK_SAFETY_FACTOR) -> dict:
    """Decide, BEFORE a single byte is fetched, whether this broadcast fits.

    Returns a report dict with `ok` plus every number behind the decision, so
    a refusal is explainable ("4.2GB needed for 4h12m at 720p, 1.8GB free")
    rather than a bare failure. An UNKNOWN duration is not treated as "fits":
    it falls back to the flat `min_free_gb` floor and says so.
    """
    free_ok, free_gb = check_disk_space(media_root, min_free_gb)
    needed = estimate_download_bytes(duration_seconds, height,
                                     safety_factor=safety_factor)
    needed_gb = round(needed / (1024 ** 3), 2) if needed else None
    if needed is None:
        return {
            "ok": free_ok, "freeGb": free_gb, "neededGb": None,
            "minFreeGb": min_free_gb, "durationSeconds": duration_seconds,
            "height": height, "safetyFactor": safety_factor,
            "reason": (f"duration unknown — could only check the flat "
                       f"{min_free_gb}GB floor ({free_gb}GB free)"),
        }
    ok = free_gb >= max(needed_gb, min_free_gb)
    return {
        "ok": ok, "freeGb": free_gb, "neededGb": needed_gb,
        "minFreeGb": min_free_gb, "durationSeconds": duration_seconds,
        "height": height, "safetyFactor": safety_factor,
        "reason": (f"{needed_gb}GB estimated for {int(duration_seconds)}s at "
                   f"{height}p (x{safety_factor} safety incl. scan proxy), "
                   f"{free_gb}GB free at {media_root}"
                   + ("" if ok else " — REFUSING before download")),
    }


# ------------------------------------------------------------ source safety
class SourceValidationError(ValueError):
    """A job's declared source is not an approved official broadcast. Raised
    instead of ever guessing/downloading an unverified or unofficial URL."""


def _video_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in TWITCH_HOSTS:
        # /videos/<id> is the only Twitch path that names a VOD. A channel
        # URL names whatever is live right now and a clip is not the
        # broadcast — neither is a thing this pipeline can download, so
        # both resolve to no id and are refused by the caller.
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0].lower() == "videos":
            vid = parts[1].lstrip("vV")
            return vid if vid.isdigit() else None
        return None
    if host in ("youtu.be",):
        vid = parsed.path.strip("/")
        return vid or None
    if host.endswith("youtube.com"):
        from urllib.parse import parse_qs
        qs = parse_qs(parsed.query or "")
        vid = (qs.get("v") or [None])[0]
        if vid:
            return vid
        # /live/<id> or /embed/<id> style paths
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in ("live", "embed", "v"):
            return parts[1]
    return None


def validate_source(payload: dict, *, official_channel_ids: set | None = None,
                    manual_approved_video_ids: set | None = None) -> str:
    """Raise SourceValidationError unless `payload` describes an approved
    official broadcast. Returns the resolved video id on success.

    Only allows: a verified official YouTube URL whose channel id is in
    `official_channel_ids` (the verified `config/broadcast_channels.json`
    registry), OR a video id explicitly present in
    `manual_approved_video_ids` (an operator's explicit manual-URL approval —
    never a bare user-supplied string taken on faith). Rejects: empty/missing
    URLs, non-http(s) schemes, unsupported domains, unparseable video ids, and
    a channel id that conflicts with the job's own expected authority.
    """
    url = (payload.get("sourceUrl") or payload.get("videoUrl") or "").strip()
    if not url:
        raise SourceValidationError("empty source URL")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SourceValidationError(f"unsupported URL scheme: {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise SourceValidationError(
            f"unsupported/unofficial domain: {host!r} "
            f"(only {sorted(ALLOWED_HOSTS)} are ever accepted)")
    from_url = _video_id_from_url(url)
    video_id = payload.get("videoId") or from_url
    if not video_id or not str(video_id).strip():
        raise SourceValidationError(f"could not resolve a video id from {url!r}")
    video_id = str(video_id).strip()
    # A payload id and a URL that disagree is  never both right, and this is
    # the gate that decides what gets fetched. It used to take the payload
    # id on faith, which was survivable while every id came from one
    # namespace; with two, a Twitch id beside a YouTube URL (or the
    # reverse) would authorize one broadcast and download another.
    if from_url and video_id != from_url:
        raise SourceValidationError(
            f"payload video id {video_id!r} disagrees with the id in its own "
            f"source URL ({from_url!r}) — refusing rather than choosing one")

    manual_ok = manual_approved_video_ids and video_id in manual_approved_video_ids
    if manual_ok:
        return video_id

    channel_id = payload.get("channelId")
    if official_channel_ids is not None:
        if not channel_id:
            raise SourceValidationError(
                f"video {video_id} has no channelId to verify against the "
                f"official broadcast-channel registry")
        if channel_id not in official_channel_ids:
            raise SourceValidationError(
                f"channel {channel_id!r} is not in the verified official "
                f"registry, and video {video_id} has no manual approval")
    expected_channel = payload.get("expectedChannelId") or payload.get("broadcastAuthority")
    if expected_channel and channel_id and expected_channel != channel_id:
        raise SourceValidationError(
            f"channel {channel_id!r} conflicts with the job's expected "
            f"authority {expected_channel!r}")
    return video_id


# --------------------------------------------------------------- error taxonomy
def classify_download_error(exc: BaseException) -> tuple[str, str]:
    """(error_code, message) for record_attempt — every failure mode the
    sprint's worker spec calls out gets an explicit, stable code.

    A YouTube media 403 resolves to `youtube_media_forbidden`, NOT the
    generic `download_failed`: the two have completely different remedies
    (refresh the signed URL / force IPv4 / supply browser cookies vs.
    "look at the logs"), and an opaque code is what made the previous
    failure un-actionable. Codes come from `ytdlp_opts.classify_ytdlp_error`
    so the downloader, the worker and the control room all agree.
    """
    # The ladder already classified this precisely — never re-guess it.
    code = getattr(exc, "code", None)
    if code and isinstance(code, str):
        return code, ytdlp_opts.redact_text(str(exc))
    text = (getattr(exc, "output", "") or getattr(exc, "stderr", "")
            or str(exc))
    classified, _remedy = ytdlp_opts.classify_ytdlp_error(text)
    if classified:
        return classified, ytdlp_opts.redact_text(str(exc))
    if isinstance(exc, FileNotFoundError):
        return "missing_dependency", str(exc)
    if isinstance(exc, ModuleNotFoundError):
        # e.g. cv2/opencv not installed on this machine, so video_ingest
        # itself couldn't be imported — same remedy as a missing binary.
        return "missing_dependency", str(exc)
    try:
        import video_ingest as vi
    except ImportError:
        vi = None
    if vi is not None and isinstance(exc, vi.InvalidClip):
        return "corrupt_media", str(exc)
    if vi is not None and isinstance(exc, vi.StallTimeout):
        return "network_stall", str(exc)
    if isinstance(exc, SourceValidationError):
        return "invalid_source", str(exc)
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:  # ENOSPC
        return "insufficient_disk", str(exc)
    if isinstance(exc, subprocess.CalledProcessError):
        tail = (getattr(exc, "stderr", None) or getattr(exc, "output", None) or "")
        return "download_failed", f"exit {exc.returncode}: {str(tail)[-300:]}"
    if isinstance(exc, subprocess.TimeoutExpired):
        return "download_timeout", str(exc)
    return "unknown_error", f"{type(exc).__name__}: {exc}"


def _site_relpath(path: str, start: str) -> str:
    """Forward-slash relative path regardless of platform, and total.

    See pipeline/site_paths.py: besides the backslash problem this always
    guarded against, os.path.relpath RAISES across Windows drives, which took
    out ten suites on the first real Windows CI run and would take out any
    user whose media root is on a second drive."""
    import site_paths
    return site_paths.site_relpath(path, start)


# ------------------------------------------------------------------- hashing
def sha256_file(path: str, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


# --------------------------------------------------------------- job resource
def resource_for(job: models.Job) -> str:
    """The lock resource a job's underlying broadcast maps to. Two jobs for
    the same video (e.g. a record job and a later process job) intentionally
    share NOTHING here — each kind gets its own resource string, matching the
    roadmap's record:<video-id> / process:<video-id> namespacing."""
    video_id = job.payload.get("videoId") or job.job_key.split(":")[-1]
    return f"{job.kind}:{video_id}"


def claim_and_lock(store: js.JobStore, lock_mgr: lk.LockManager,
                   kinds: list[str], worker_id: str, *,
                   lease_seconds: int = 300) -> models.Job | None:
    """Claim the next eligible job AND take its resource lease in one step,
    so "claimed" always means both "worker_id stamped" and "lock held" — the
    hard mutual-exclusion guarantee locks.py provides. If the lock is
    (rarely) already held by someone else despite the state being claimable,
    the claim is released back to the pool rather than silently proceeding.
    """
    job = store.claim_next(kinds, worker_id)
    if job is None:
        return None
    resource = resource_for(job)
    if not lock_mgr.acquire(resource, worker_id, lease_seconds=lease_seconds):
        store.clear_worker(job.job_key)
        return None
    return job


# ------------------------------------------------------------------ download
class WorkerPreflightError(RuntimeError):
    """Dependency/disk check failed before any job-specific work started."""


class DetectionAssetsMissing(RuntimeError):
    """The job's layout cannot detect: no hero templates, or a declared-but-
    absent / placeholder HUD anchor.

    Raised BEFORE the download so a multi-hour broadcast is not fetched only
    to report `detect: skipped — no hero templates` at the end. Carries the
    stable code `detection_assets_missing`, which is deliberately NOT
    retryable — retrying changes nothing; a human must harvest the assets.
    """
    code = "detection_assets_missing"

    def __init__(self, message: str, *, report: dict | None = None):
        self.report = report or {}
        super().__init__(message)


def check_detection_assets(job: models.Job, *, allow_missing: bool = False
                           ) -> dict:
    """Is this job's resolved layout actually able to produce compositions?

    Returns {"checked", "ok", "layoutId", "failed", "checks", "reason"}.
    `checked=False` when the job has no layout yet — layout resolution runs
    AFTER the download by design, so there is nothing to verify at that
    point and this reports so honestly instead of inventing a verdict.
    """
    layout_id = job.payload.get("expectedLayoutId")
    if not layout_id:
        return {"checked": False, "ok": True, "layoutId": None,
                "failed": [], "checks": [],
                "reason": ("no layout resolved yet — layout resolution runs "
                           "after the download, so detection assets cannot "
                           "be verified at this point")}
    import detection_assets as da
    report = da.check_layout_assets(layout_id)
    return {"checked": True, "ok": report["ok"] or allow_missing,
            "hardOk": report["ok"], "layoutId": layout_id,
            "failed": report["failed"], "checks": report["checks"],
            "reason": ("detection assets present" if report["ok"] else
                       "; ".join(f"{c['name']}: {c['detail']}"
                                 for c in report["checks"]
                                 if c["status"] == da.FAIL))}


def assert_detection_assets(job: models.Job, *, allow_missing: bool = False
                            ) -> dict:
    """Raise DetectionAssetsMissing unless this job can actually detect.

    `allow_missing=True` is the deliberate harvest escape hatch: hero
    templates and anchor crops can only be cut FROM this broadcast's own
    frames, so demanding them before the download would be an unresolvable
    chicken-and-egg. The opt-out is explicit, recorded on the job, and never
    the default.
    """
    report = check_detection_assets(job, allow_missing=allow_missing)
    if not report["checked"] or report.get("hardOk", True):
        return report
    if allow_missing:
        log(f"{job.job_key}: PROCEEDING WITHOUT DETECTION ASSETS "
            f"(--for-harvest): {report['reason']}")
        return report
    remedies = "\n    ".join(
        c["remedy"] for c in report["checks"]
        if c.get("remedy") and c["status"] == "fail")
    raise DetectionAssetsMissing(
        f"layout {report['layoutId']!r} cannot detect "
        f"({', '.join(report['failed'])}): {report['reason']}\n"
        f"  Fix it before downloading hours of video:\n    {remedies}\n"
        f"  Or, if you are downloading this broadcast IN ORDER to harvest "
        f"those assets from it, say so explicitly:\n"
        f"    python pipeline/automation/cli.py autopilot --job "
        f"{job.job_key} --for-harvest",
        report=report)


def preflight(media_root: str = DEFAULT_MEDIA_ROOT,
             min_free_gb: float = DEFAULT_MIN_FREE_GB,
             which=shutil.which) -> dict:
    """Confirm required dependencies + free disk space (spec steps 2-3).
    Returns a report dict; never raises — callers decide whether to proceed
    (download_job always re-checks and raises WorkerPreflightError itself)."""
    deps = check_dependencies(which=which)
    missing = missing_dependencies(deps)
    os.makedirs(media_root, exist_ok=True)
    disk_ok, free_gb = check_disk_space(media_root, min_free_gb)
    return {
        "dependencies": deps, "missing": missing,
        "diskOk": disk_ok, "freeGb": free_gb, "minFreeGb": min_free_gb,
        "ok": not missing and disk_ok,
    }


def media_dir_for(job: models.Job, media_root: str = DEFAULT_MEDIA_ROOT) -> str:
    safe_id = job.job_key.replace(":", "_").replace("/", "_")
    return os.path.join(media_root, safe_id, "media")


DEFAULT_SOURCE_HEIGHT = 720   # the acquisition target: one 720p source file
DEFAULT_PROXY_HEIGHT = 360    # the local scan proxy every scanning pass reads


def download_job(store: js.JobStore, lock_mgr: lk.LockManager, job: models.Job,
                 *, worker_id: str, media_root: str = DEFAULT_MEDIA_ROOT,
                 min_free_gb: float = DEFAULT_MIN_FREE_GB,
                 official_channel_ids: set | None = None,
                 manual_approved_video_ids: set | None = None,
                 probe_fn=None,
                 download_full_fn=None,
                 probe_media_fn=None,
                 proxy_fn=None,
                 resolution_fn=None,
                 height: int = DEFAULT_SOURCE_HEIGHT,
                 proxy_height: int = DEFAULT_PROXY_HEIGHT,
                 make_proxy: bool = True,
                 for_harvest: bool = False,
                 which=shutil.which, runner=subprocess,
                 now: dt.datetime | None = None) -> dict:
    """Drive one ARCHIVED (broadcast linked, ready) job through DOWNLOADING
    to DOWNLOADED, acquiring the WHOLE broadcast once.

    Order of operations, each step a hard gate before the next:
      1. tool + flat-disk preflight (unchanged)
      2. source validation — never an unapproved/unofficial URL
      3. duration probe FIRST (`video_ingest.probe_vod`), before any bytes
      4. duration-aware disk preflight — refuses up front when the estimated
         720p footprint (plus safety factor and proxy) will not fit
      5. ONE resumable 720p full download (`video_ingest.download_full_video`,
         `--continue`; a killed download resumes, never restarts)
      6. real media metadata: sha256, duration, resolution, codec, container,
         source URL, tool versions, timestamps
      7. ONE local 360p scan proxy (`video_ingest.make_scan_proxy`) that every
         later scanning pass reads instead of the full-resolution source

    `probe_fn`/`download_full_fn`/`proxy_fn`/`resolution_fn` default to
    `video_ingest`'s real functions, imported lazily below (never at module
    level — see the module docstring) so a missing cv2 only ever surfaces as
    a classified failure of THIS call, never an import crash at CLI startup.

    On any failure the job is routed through `record_attempt(ok=False, ...)`
    (RETRY_SCHEDULED or FAILED_PERMANENT per the existing backoff/ceiling
    policy) and the lock is released so a retry can reclaim it. Returns
    {"ok": True, "path", "metadata"} on success.
    """
    resource = resource_for(job)
    try:
        import video_ingest as vi
        if probe_fn is None:
            probe_fn = vi.probe_vod
        if download_full_fn is None:
            download_full_fn = vi.download_full_video
        if probe_media_fn is None:
            probe_media_fn = vi.media_download_probe
        if proxy_fn is None:
            proxy_fn = vi.make_scan_proxy
        if resolution_fn is None:
            resolution_fn = vi.probe_clip_resolution

        preflight_report = preflight(media_root, min_free_gb, which=which)
        if preflight_report["missing"]:
            raise FileNotFoundError(
                f"missing required tool(s): {', '.join(preflight_report['missing'])}")
        if not preflight_report["diskOk"]:
            raise OSError(28, f"only {preflight_report['freeGb']}GB free, "
                              f"need >= {min_free_gb}GB at {media_root}")

        video_id = validate_source(
            job.payload, official_channel_ids=official_channel_ids,
            manual_approved_video_ids=manual_approved_video_ids)

        # Detection-asset gate BEFORE any bytes: a layout with no hero
        # templates (or a placeholder anchor) can never produce a comp, and
        # discovering that after a multi-hour download is the expensive way
        # to learn it.
        assets = assert_detection_assets(job, allow_missing=for_harvest)
        store.update_payload(job.job_key, {"detectionAssets": {
            **{k: assets.get(k) for k in
               ("checked", "ok", "hardOk", "layoutId", "failed", "reason")},
            "forHarvest": bool(for_harvest),
        }})

        url = job.payload.get("sourceUrl") or job.payload.get("videoUrl")
        # Duration BEFORE the download, so the space check below is real.
        meta = probe_fn(url)
        duration = int(meta.get("duration") or 0)
        disk = disk_preflight(media_root, duration_seconds=duration or None,
                              height=height, min_free_gb=min_free_gb)
        store.update_payload(job.job_key, {"diskPreflight": disk})
        if not disk["ok"]:
            raise OSError(28, f"insufficient disk space: {disk['reason']}")

        # A short REAL media probe before committing to hours of download:
        # metadata succeeding proves nothing about whether the signed media
        # URL will serve bytes (the exact 403 this pipeline hit). The probe
        # also reports which ladder rung works, so the full download starts
        # there instead of re-walking the failures.
        probe_report: dict | None = None
        proven_rung: str | None = None
        if probe_media_fn is not None:
            try:
                probe_report = probe_media_fn(url, height=height)
                proven_rung = probe_report.get("rung")
                store.update_payload(job.job_key, {"mediaProbe": {
                    "ok": True, "rung": proven_rung,
                    "bytes": probe_report.get("bytes"),
                    "width": probe_report.get("width"),
                    "height": probe_report.get("height"),
                    "format": probe_report.get("format"),
                    "qualityDowngrade": probe_report.get("qualityDowngrade"),
                    "attempts": probe_report.get("attempts") or [],
                    "checkedAt": dt.datetime.now(dt.timezone.utc)
                        .replace(microsecond=0).isoformat(),
                }})
            except Exception as exc:  # noqa: BLE001 — classified below
                code, message = classify_download_error(exc)
                store.update_payload(job.job_key, {"mediaProbe": {
                    "ok": False, "errorCode": code,
                    "errorMessage": ytdlp_opts.redact_text(message)[:600],
                    "attempts": getattr(exc, "attempts", []),
                    "checkedAt": dt.datetime.now(dt.timezone.utc)
                        .replace(microsecond=0).isoformat(),
                }})
                # Re-raise so the normal failure path records the attempt,
                # sets resumeState and schedules the retry — refusing here
                # is the whole point: no multi-hour download on a dead URL.
                raise

        store.transition(job.job_key, sm.DOWNLOADING)
        out_dir = media_dir_for(job, media_root)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{video_id}.mp4")
        dres = download_full_fn(url, out_path, height=height,
                                start_rung=proven_rung)

        digest = sha256_file(dres["path"])
        res_info = resolution_fn(dres["path"]) or {}
        repo_root = os.path.dirname(_PIPELINE_DIR)
        media = {
            "videoId": video_id,
            "channelId": job.payload.get("channelId"),
            "originalTitle": meta.get("title"),
            "sourceUrl": url,
            "downloadedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            "durationSeconds": duration or res_info.get("duration"),
            "requestedHeight": height,
            "width": res_info.get("width"),
            "height": res_info.get("height"),
            "codec": res_info.get("codec"),
            "fileSizeBytes": dres.get("sizeBytes"),
            "sha256": digest,
            "localPath": _site_relpath(dres["path"], repo_root),
            "reusedCache": bool(dres.get("reused", False)),
            "resumed": bool(dres.get("resumed", False)),
            "partialBytesBefore": dres.get("partialBytesBefore"),
            "formatSelector": dres.get("format"),
            "audioAvailable": None,  # video-only download by design (frame extraction never needs audio)
            "container": os.path.splitext(dres["path"])[1].lstrip("."),
            "workerVersion": WORKER_VERSION,
            "workerId": worker_id,
            "ytDlpVersion": tool_version("yt-dlp", runner=runner),
            "ffmpegVersion": tool_version("ffmpeg", runner=runner),
            "diskPreflight": disk,
        }

        # The scan proxy: a proxy FAILURE must not lose a good multi-hour
        # download, so it is recorded as an explicit proxy error on the job
        # and the source file stays. Segmentation refuses to run without a
        # proxy, which keeps the failure visible and recoverable.
        if make_proxy:
            proxy_path = os.path.join(out_dir, f"{video_id}.proxy{proxy_height}p.mp4")
            try:
                pres = proxy_fn(dres["path"], proxy_path, height=proxy_height)
                media["proxy"] = {
                    "localPath": _site_relpath(pres["path"], repo_root),
                    "width": pres.get("width"), "height": pres.get("height"),
                    "sizeBytes": pres.get("sizeBytes"),
                    "reused": bool(pres.get("reused", False)),
                    "sha256": sha256_file(pres["path"]),
                    "purpose": ("scanning/OCR/layout-matching/segmentation only "
                                "— detection and clip cuts use the full-resolution "
                                "source"),
                }
            except Exception as exc:  # noqa: BLE001
                code, message = classify_download_error(exc)
                media["proxy"] = {"error": message, "errorCode": code,
                                  "localPath": None}
                log(f"{job.job_key}: proxy generation FAILED [{code}] {message} "
                    f"— the full download is intact; re-run to retry the proxy")

        store.update_payload(job.job_key, {"media": media})
        store.record_attempt(job.job_key, ok=True, worker_id=worker_id,
                             diagnostic_path=out_path, now=now)
        store.transition(job.job_key, sm.DOWNLOADED)
        lock_mgr.release(resource, worker_id)
        log(f"{job.job_key}: downloaded {media['fileSizeBytes']} bytes, "
            f"sha256={digest[:12]}… -> {out_path}")
        return {"ok": True, "path": out_path, "metadata": media}
    except Exception as exc:  # noqa: BLE001 — every failure must be classified, never silently swallowed
        code, message = classify_download_error(exc)
        message = ytdlp_opts.redact_text(message)
        # Record WHERE to resume before the state moves to FAILED/
        # RETRY_SCHEDULED, so `retry-job` restores the right stage instead
        # of guessing — and never rewinds past the audited source approval.
        failed_in = (store.get(job.job_key) or job).state
        patch: dict = {
            "resumeState": resume_target_for(failed_in),
            "failedInState": failed_in,
            "lastFailure": {
                "errorCode": code, "errorMessage": message[:600],
                "remedy": getattr(exc, "remedy", "") or
                          ytdlp_opts.classify_ytdlp_error(message)[1],
                "at": dt.datetime.now(dt.timezone.utc)
                    .replace(microsecond=0).isoformat(),
            },
        }
        attempts = getattr(exc, "attempts", None)
        if attempts:
            patch["downloadAttempts"] = attempts
        store.update_payload(job.job_key, patch)
        store.record_attempt(job.job_key, ok=False, worker_id=worker_id,
                             error_code=code, error_message=message, now=now)
        lock_mgr.release(resource, worker_id)
        log(f"{job.job_key}: FAILED [{code}] {message}")
        remedy = patch["lastFailure"]["remedy"]
        if remedy:
            log(f"{job.job_key}: remedy — {remedy}")
        return {"ok": False, "errorCode": code, "errorMessage": message,
                "remedy": remedy, "resumeState": patch["resumeState"],
                "attempts": attempts or []}


def scan_path_for(job: models.Job, *, repo_root: str | None = None) -> str | None:
    """The path every SCANNING pass must read for this job: the 360p proxy
    when one exists, else None.

    Returning None rather than silently falling back to the full-resolution
    source is deliberate — a missing proxy is a visible, recoverable failure,
    not something to paper over with a slower scan that behaves differently
    from every other run.
    """
    media = job.payload.get("media") or {}
    proxy = media.get("proxy") or {}
    rel = proxy.get("localPath")
    if not rel:
        return None
    root = repo_root or os.path.dirname(_PIPELINE_DIR)
    return os.path.join(root, rel)


def source_path_for(job: models.Job, *, repo_root: str | None = None) -> str | None:
    """The full-resolution source path — what DETECTION and clip extraction
    must use (never the proxy)."""
    media = job.payload.get("media") or {}
    rel = media.get("localPath")
    if not rel:
        return None
    root = repo_root or os.path.dirname(_PIPELINE_DIR)
    return os.path.join(root, rel)


# --------------------------------------------------------------- doctor
# Presence-only — the doctor NEVER reads, logs, or returns the value of any
# of these, only whether they're set (Windows worker prep, "never print or
# reveal any secret").
REQUIRED_API_KEYS = ("FACEIT_API_KEY", "YOUTUBE_API_KEY")

_SECRET_LIKE = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]


def _redact(text: str) -> str:
    """Defense in depth: even though `gh auth status` never prints a token,
    strip anything token-shaped before it's ever surfaced in a report/log."""
    out = text
    for pat in _SECRET_LIKE:
        out = pat.sub("[REDACTED]", out)
    return out


def check_python() -> dict:
    return {"version": sys.version.split()[0], "executable": sys.executable}


def check_repo_dependencies() -> dict:
    """requirements.txt's real deps are importable. Reports only package
    name + public version string — never anything secret."""
    report: dict[str, str | None] = {}
    for mod, name in (("cv2", "opencv-python-headless"), ("numpy", "numpy")):
        try:
            m = __import__(mod)
            report[name] = getattr(m, "__version__", "unknown")
        except ImportError:
            report[name] = None
    return report


def check_writable(path: str) -> tuple[bool, str]:
    """True if `path` (created if missing) accepts a real write + delete.
    Never raises; returns (ok, reason)."""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".owcs_write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True, "writable"
    except OSError as e:
        return False, str(e)


def check_gh_auth(*, runner=subprocess, which=shutil.which) -> dict:
    """GitHub CLI authentication status. `gh auth status` itself never
    prints a token; output is still redacted defensively before being
    surfaced anywhere (report/log/JSON)."""
    if not which("gh"):
        return {"installed": False, "authenticated": False,
                "detail": "gh CLI not found on PATH"}
    try:
        res = runner.run(["gh", "auth", "status"], capture_output=True,
                         text=True, timeout=15, **proc_text.PIPE_TEXT)
        text = _redact((res.stdout or "") + (res.stderr or ""))
        return {"installed": True, "authenticated": res.returncode == 0,
                "detail": text.strip()[:500]}
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"installed": True, "authenticated": False,
                "detail": f"{type(e).__name__}: {e}"}


def check_api_keys_present(names: tuple[str, ...] = REQUIRED_API_KEYS) -> dict[str, bool]:
    """{"FACEIT_API_KEY": True, ...} — presence only, value NEVER read into
    the report. A missing key here just means discovery/broadcast-matching
    stays read-only; the worker itself needs no API key (only yt-dlp/ffmpeg)."""
    return {name: bool(os.environ.get(name)) for name in names}


def doctor_report(*, media_root: str = DEFAULT_MEDIA_ROOT,
                  min_free_gb: float = DEFAULT_MIN_FREE_GB,
                  which=shutil.which, runner=subprocess) -> dict:
    """The Windows-worker preflight checklist in one call: Python, repo
    Python deps, yt-dlp/ffmpeg/ffprobe + versions, disk space, worker cache
    + artifact/evidence directory writability, `gh` auth, and API-key
    presence (never value). Safe to run any number of times — read-only
    except for a self-deleting write probe in each directory it checks."""
    deps = check_dependencies(which=which)
    versions = {tool: tool_version(tool, runner=runner)
               for tool in REQUIRED_TOOLS if deps.get(tool)}
    disk_ok, free_gb = check_disk_space(media_root, min_free_gb)
    cache_ok, cache_reason = check_writable(media_root)
    reports_dir = os.path.join(os.path.dirname(_PIPELINE_DIR), "reports")
    evidence_ok, evidence_reason = check_writable(reports_dir)
    report = {
        "python": check_python(),
        "repoDependencies": check_repo_dependencies(),
        "tools": deps,
        "toolVersions": versions,
        "missingTools": missing_dependencies(deps),
        "disk": {"ok": disk_ok, "freeGb": free_gb, "minFreeGb": min_free_gb,
                 "path": media_root},
        "workerCacheWritable": {"ok": cache_ok, "reason": cache_reason,
                               "path": media_root},
        "artifactDirWritable": {"ok": evidence_ok, "reason": evidence_reason,
                                "path": reports_dir},
        "githubCli": check_gh_auth(runner=runner, which=which),
        "apiKeysPresent": check_api_keys_present(),
    }
    # The download stack (yt-dlp version + PATH/interpreter agreement,
    # ffmpeg/ffprobe, JS runtime, yt-dlp-ejs, curl_cffi) and the resolved
    # download-auth configuration. Detection only — never installs.
    report["download"] = ytdlp_opts.dependency_report(which=which,
                                                      runner=runner)
    # Which broadcast packages can actually detect, so an operator learns
    # this BEFORE match day rather than after a multi-hour download.
    try:
        import detection_assets as da
        audits = da.audit_all_layouts()
        report["detectionAssets"] = {
            "ready": [a["layoutId"] for a in audits if a["ok"]],
            "notReady": [{"layoutId": a["layoutId"], "failed": a["failed"],
                          "remedies": [c["remedy"] for c in a["checks"]
                                       if c["status"] == da.FAIL and c["remedy"]]}
                         for a in audits if not a["ok"]],
        }
    except Exception as exc:  # noqa: BLE001 — a doctor must never crash
        report["detectionAssets"] = {"error": f"{type(exc).__name__}: {exc}",
                                     "ready": [], "notReady": []}
    report["ok"] = (not report["missingTools"] and disk_ok
                    and cache_ok and evidence_ok
                    and report["download"]["ok"])
    return report


def format_doctor_report(report: dict) -> str:
    lines = [f"  python           : {report['python']['version']}"]
    for name, ver in report["repoDependencies"].items():
        lines.append(f"  {name:<26}: {'OK ' + str(ver) if ver else 'MISSING'}")
    for tool, path in report["tools"].items():
        ver = report["toolVersions"].get(tool)
        lines.append(f"  {tool:<26}: {('OK ' + ver if ver else 'OK') if path else 'MISSING'}")
    d = report["disk"]
    lines.append(f"  disk free        : {d['freeGb']}GB (need >= {d['minFreeGb']}GB) — "
                f"{'OK' if d['ok'] else 'LOW'}")
    c = report["workerCacheWritable"]
    lines.append(f"  worker cache dir : {'OK' if c['ok'] else 'NOT WRITABLE — ' + c['reason']} ({c['path']})")
    a = report["artifactDirWritable"]
    lines.append(f"  artifact dir     : {'OK' if a['ok'] else 'NOT WRITABLE — ' + a['reason']} ({a['path']})")
    gh = report["githubCli"]
    lines.append("  gh CLI           : NOT INSTALLED" if not gh["installed"]
                else f"  gh CLI auth      : {'OK' if gh['authenticated'] else 'NOT AUTHENTICATED'}")
    for key, present in report["apiKeysPresent"].items():
        lines.append(f"  {key:<26}: {'present' if present else 'missing'} (value never shown)")
    if report.get("download"):
        lines.append("  --- download stack ---")
        lines.append(ytdlp_opts.format_dependency_report(report["download"]))
    assets = report.get("detectionAssets") or {}
    if assets.get("ready") or assets.get("notReady"):
        lines.append("  --- detection assets ---")
        lines.append(f"    detection-ready layouts : "
                     f"{', '.join(assets['ready']) if assets['ready'] else 'NONE'}")
        for row in assets.get("notReady", []):
            lines.append(f"    NOT READY {row['layoutId']:<22} "
                         f"({', '.join(row['failed'])})")
            for remedy in row["remedies"]:
                lines.append(f"      -> {remedy}")
    lines.append(f"  OVERALL          : {'READY' if report['ok'] else 'NOT READY — see above'}")
    return "\n".join(lines)


def resume_interrupted(store: js.JobStore, lock_mgr: lk.LockManager, *,
                       worker_id: str, media_root: str = DEFAULT_MEDIA_ROOT,
                       **download_kwargs) -> list[dict]:
    """Find every KIND_RECORD job stuck in DOWNLOADING whose lease has gone
    stale (worker crashed mid-download) and safely resume it. Safe because
    `download_vod_clip.download_clip` validates and reuses a partial/complete
    cached file rather than blindly re-downloading (see download_vod_clip.py).
    """
    stuck = store.list_jobs(kind=models.KIND_RECORD, state=sm.DOWNLOADING)
    results = []
    for job in stuck:
        resource = resource_for(job)
        if not lock_mgr.reset_stale(resource):
            continue  # still actively held by a live worker — not stuck
        if not lock_mgr.acquire(resource, worker_id):
            continue
        log(f"{job.job_key}: resuming interrupted download")
        # download_job's own DOWNLOADING transition is a legal no-op re-entry
        # from the same state (state_machine.can_transition allows src==dst).
        results.append(download_job(store, lock_mgr, store.get(job.job_key),
                                    worker_id=worker_id, media_root=media_root,
                                    **download_kwargs))
    return results
