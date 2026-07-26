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

import download_vod_clip as dvc  # noqa: E402  (pipeline/download_vod_clip.py)
import video_ingest as vi  # noqa: E402  (pipeline/video_ingest.py)

from . import job_store as js
from . import locks as lk
from . import models
from . import state_machine as sm

WORKER_VERSION = "1.0.0"

# Only these domains are ever accepted as an "official" broadcast source —
# never an arbitrary URL, never a shell string, never an unofficial mirror.
ALLOWED_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
})

REQUIRED_TOOLS = ("ffmpeg", "ffprobe", "yt-dlp")

DEFAULT_MEDIA_ROOT = os.path.join(
    os.path.dirname(_PIPELINE_DIR), "data", "worker", "jobs")
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
                             timeout=15)
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


# ------------------------------------------------------------ source safety
class SourceValidationError(ValueError):
    """A job's declared source is not an approved official broadcast. Raised
    instead of ever guessing/downloading an unverified or unofficial URL."""


def _video_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
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
    video_id = payload.get("videoId") or _video_id_from_url(url)
    if not video_id or not str(video_id).strip():
        raise SourceValidationError(f"could not resolve a video id from {url!r}")
    video_id = str(video_id).strip()

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
    sprint's worker spec calls out gets an explicit, stable code."""
    if isinstance(exc, FileNotFoundError):
        return "missing_dependency", str(exc)
    if isinstance(exc, vi.InvalidClip):
        return "corrupt_media", str(exc)
    if isinstance(exc, vi.StallTimeout):
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
    """Forward-slash relative path regardless of platform. A stored Windows
    backslash path silently fails to resolve anywhere paths are joined with
    '/' — the same real bug already found and fixed once in
    `team_assets.publish_candidate`; normalized here from the start."""
    return os.path.relpath(path, start).replace("\\", "/")


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


def download_job(store: js.JobStore, lock_mgr: lk.LockManager, job: models.Job,
                 *, worker_id: str, media_root: str = DEFAULT_MEDIA_ROOT,
                 min_free_gb: float = DEFAULT_MIN_FREE_GB,
                 official_channel_ids: set | None = None,
                 manual_approved_video_ids: set | None = None,
                 probe_fn=vi.probe_vod,
                 download_clip_fn=dvc.download_clip,
                 resolution_fn=vi.probe_clip_resolution,
                 height: int = 1080,
                 which=shutil.which, runner=subprocess,
                 now: dt.datetime | None = None) -> dict:
    """Drive one ARCHIVED (broadcast linked, ready) job through DOWNLOADING
    to DOWNLOADED. Reuses `download_vod_clip.download_clip` (itself built on
    `video_ingest.py`'s yt-dlp/ffmpeg machinery) — this function only adds
    job-state bookkeeping, source validation, and metadata capture around it.

    On any failure the job is routed through `record_attempt(ok=False, ...)`
    (RETRY_SCHEDULED or FAILED_PERMANENT per the existing backoff/ceiling
    policy) and the lock is released so a retry can reclaim it. Returns
    {"ok": True, "path", "metadata"} on success.
    """
    resource = resource_for(job)
    try:
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

        store.transition(job.job_key, sm.DOWNLOADING)
        url = job.payload.get("sourceUrl") or job.payload.get("videoUrl")
        meta = probe_fn(url)

        out_dir = media_dir_for(job, media_root)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{video_id}.mp4")
        duration = int(meta.get("duration") or 0)
        dres = download_clip_fn(url, 0, duration or 1, out_path, height=height)

        digest = sha256_file(dres["path"])
        res_info = resolution_fn(dres["path"]) or {}
        media = {
            "videoId": video_id,
            "channelId": job.payload.get("channelId"),
            "originalTitle": meta.get("title"),
            "downloadedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            "durationSeconds": duration or res_info.get("duration"),
            "width": res_info.get("width"),
            "height": res_info.get("height"),
            "codec": res_info.get("codec"),
            "fileSizeBytes": dres.get("sizeBytes"),
            "sha256": digest,
            "localPath": _site_relpath(dres["path"], os.path.dirname(_PIPELINE_DIR)),
            "reusedCache": dres.get("reused", False),
            "audioAvailable": None,  # video-only download by design (frame extraction never needs audio)
            "container": os.path.splitext(dres["path"])[1].lstrip("."),
            "workerVersion": WORKER_VERSION,
            "workerId": worker_id,
            "ytDlpVersion": tool_version("yt-dlp", runner=runner),
            "ffmpegVersion": tool_version("ffmpeg", runner=runner),
        }
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
        store.record_attempt(job.job_key, ok=False, worker_id=worker_id,
                             error_code=code, error_message=message, now=now)
        lock_mgr.release(resource, worker_id)
        log(f"{job.job_key}: FAILED [{code}] {message}")
        return {"ok": False, "errorCode": code, "errorMessage": message}


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
                         text=True, timeout=15)
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
    report["ok"] = (not report["missingTools"] and disk_ok
                    and cache_ok and evidence_ok)
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
