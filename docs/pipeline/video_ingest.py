#!/usr/bin/env python3
"""
video_ingest.py — Stage V1 of the video CV layer: source -> raw frames.

Reads data/sources/video_sources.json and, for each source, resolves the
broadcast video and extracts sampled frames to work/{match}/frames_raw/.
It does NOT classify or detect anything — it only produces frames. The
next stage (frame_filter.py) keeps the live-gameplay ones.

There are two kinds of source:

  match-paired sources (fixtureFrames / localFile / vodUrl + 'match'):
      resolve to work/{match}/frames_raw for the CV chain. Order:
        fixtureFrames -> copy a committed PNG folder   (offline / demo / CI)
        localFile     -> ffmpeg-extract a local .mp4    (dev)
        vodUrl        -> yt-dlp download + ffmpeg        (production)

  YouTube VOD sources ('platform': 'youtube' + 'url' + 'id'):
      long broadcast streams sampled by TIMESTAMP. yt-dlp reads metadata
      (title/duration), then frames are pulled with one of two clip modes
      (--clip-mode, default local-window):
        local-window   ONE yt-dlp download covering the whole [start,end]
                       window, then local ffmpeg seeks per sample offset.
                       Reliable at any offset — fixes yt-dlp/ffmpeg failing
                       to remote-seek deep into a long VOD ("could not seek
                       to position ...").
        per-timestamp  one remote yt-dlp seek+download per sample offset.
                       Simpler, but unreliable far into a long VOD; kept as
                       an explicit fallback.
      Either way a 6-hour VOD is NEVER fully downloaded. Frames land in
      work/vods/{id}/frames_raw. This is ingestion only — no comps here.

yt-dlp/ffmpeg are only touched for the localFile/vodUrl/youtube paths, so
demo and CI runs need neither installed and never hit the network.

This module never creates or edits matches and never touches comps. Pairing
YouTube VODs to FACEIT match/map structure comes later (see
docs/video-pipeline.md); here we just extract frames.

Usage:
  python3 pipeline/video_ingest.py                          # match-paired batch
  python3 pipeline/video_ingest.py --match vdemo01          # one match source
  python3 pipeline/video_ingest.py --source owcs-afcxdimpsle --dry-run
  python3 pipeline/video_ingest.py --source owcs-afcxdimpsle \
        --start 1:30:00 --end 1:45:00 --sample-interval 60
  python3 pipeline/video_ingest.py --source owcs-afcxdimpsle \
        --start 1:30:00 --end 1:32:00 --sample-interval 30 \
        --clip-mode local-window
"""
from __future__ import annotations
import argparse
import glob
import os
import re
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import capture  # noqa: E402
import json  # noqa: E402

DEFAULT_SOURCES = os.path.join(db.REPO_ROOT, "data", "sources", "video_sources.json")
WORK_DIR = capture.WORK_DIR


def log(msg: str) -> None:
    print(f"[video_ingest] {msg}", flush=True)


# ------------------------------------------------------------- sources io
def load_sources(path: str = DEFAULT_SOURCES) -> list[dict]:
    """Return the list of source dicts (empty if the file is missing)."""
    if not os.path.exists(path):
        log(f"no sources file at {path} — nothing to ingest.")
        return []
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload.get("sources", []) or []


def source_match_id(src: dict) -> str | None:
    return src.get("match") or src.get("matchId")


def source_id(src: dict) -> str | None:
    return src.get("id") or source_match_id(src)


def is_youtube_source(src: dict) -> bool:
    plat = (src.get("platform") or "").lower()
    if plat in ("youtube", "yt"):
        return True
    url = src.get("url") or src.get("vodUrl") or ""
    return "youtube.com" in url or "youtu.be" in url


def abspath(p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(db.REPO_ROOT, p)


def match_exists(con, match_id: str) -> bool:
    return con.execute("SELECT 1 FROM matches WHERE id=? LIMIT 1",
                       (match_id,)).fetchone() is not None


# ---------------------------------------------------------------- resolve
def _copy_fixture_frames(fixture_dir: str, out_dir: str) -> list[str]:
    src = abspath(fixture_dir)
    if not os.path.isdir(src):
        raise FileNotFoundError(f"fixtureFrames dir not found: {src}")
    os.makedirs(out_dir, exist_ok=True)
    kept = []
    for fn in sorted(os.listdir(src)):
        if fn.lower().endswith(".png"):
            shutil.copy(os.path.join(src, fn), os.path.join(out_dir, fn))
            kept.append(os.path.join(out_dir, fn))
    if not kept:
        raise FileNotFoundError(f"no .png frames in fixtureFrames dir: {src}")
    return kept


def ingest_source(src: dict, interval_default: int = 300,
                  max_download_height: int = 720) -> dict:
    """Resolve one source to work/{match}/frames_raw. Returns a report dict.

    Raises on hard errors (missing file, ffmpeg/yt-dlp failure) so the caller
    can decide whether a single bad source should stop the batch.
    """
    mid = source_match_id(src)
    if not mid:
        raise ValueError("source is missing 'match' (internal match id)")
    interval = int(src.get("sampleIntervalSeconds") or interval_default)
    raw_dir = os.path.join(WORK_DIR, mid, "frames_raw")
    shutil.rmtree(raw_dir, ignore_errors=True)
    os.makedirs(raw_dir, exist_ok=True)

    if src.get("fixtureFrames"):
        frames = _copy_fixture_frames(src["fixtureFrames"], raw_dir)
        via = f"fixtureFrames ({len(frames)} committed frames)"
    elif src.get("localFile"):
        path = abspath(src["localFile"])
        if not os.path.isfile(path):
            raise FileNotFoundError(f"localFile not found: {path}")
        frames = capture.extract_frames(path, raw_dir, interval)
        via = f"localFile ffmpeg @ every {interval}s"
    elif src.get("vodUrl"):
        video = os.path.join(WORK_DIR, mid, "vod.mp4")
        os.makedirs(os.path.dirname(video), exist_ok=True)
        capture.download_vod(src["vodUrl"], video)
        frames = capture.extract_frames(video, raw_dir, interval)
        if os.path.exists(video):
            os.remove(video)  # free-CI disk hygiene
        via = f"vodUrl yt-dlp<= {max_download_height}p, ffmpeg @ every {interval}s"
    else:
        raise ValueError(
            f"source '{mid}' has no fixtureFrames, localFile, or vodUrl")

    return {"match": mid, "raw_dir": raw_dir, "frames": len(frames),
            "interval": interval, "via": via}


# =====================================================================
# YouTube VOD support — sample a long stream by timestamp.
# =====================================================================
VODS_DIR = os.path.join(WORK_DIR, "vods")


def parse_time(value) -> int:
    """Accept 3600, '3600', '1:00:00', or '02:30' -> seconds (int)."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if not s:
        return 0
    if ":" in s:
        parts = [float(p) for p in s.split(":")]
        secs = 0.0
        for p in parts:
            secs = secs * 60 + p
        return int(secs)
    return int(float(s))


def fmt_hms(seconds: int) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}"


PROBE_RETRIES = 2      # extra attempts after the first probe failure
PROBE_RETRY_WAIT = 3.0  # seconds between attempts


def _ytdlp_dump_json(url: str, retries: int = PROBE_RETRIES,
                     retry_wait: float = PROBE_RETRY_WAIT) -> dict:
    """Real metadata probe. Never downloads media (--skip-download).

    RETRIES transient failures: YouTube intermittently answers a metadata
    request with "Video unavailable" or an empty error and succeeds seconds
    later (observed live) — a single flake must not kill a capture run at
    the probe step. After the retries are exhausted the last error is
    raised with yt-dlp's own stderr attached, so the caller can tell a
    genuinely missing binary (FileNotFoundError) apart from a
    network/SSL/geo/age-gate failure — the two need different remedies.
    """
    cmd = ["yt-dlp", *js_runtime_args(), "--dump-single-json",
           "--no-playlist", "--skip-download", url]
    last: subprocess.CalledProcessError | None = None
    for attempt in range(1 + max(0, retries)):
        try:
            out = subprocess.run(cmd, check=True, capture_output=True,
                                 text=True)
            return json.loads(out.stdout)
        except subprocess.CalledProcessError as e:
            last = e
            if attempt < retries:
                tail = (e.stderr or e.stdout or "").strip()[-200:]
                log(f"probe attempt {attempt + 1}/{retries + 1} failed "
                    f"({tail or 'no output'}) — retrying in "
                    f"{retry_wait:g}s...")
                time.sleep(retry_wait)
    tail = ((last.stderr or last.stdout or "").strip()[-600:]
            if last else "")
    # Re-raise as a clear ValueError so it doesn't get mistaken for a
    # missing-yt-dlp error (whose remedy is "install yt-dlp").
    raise ValueError(
        f"yt-dlp could not read VOD metadata after {retries + 1} "
        f"attempt(s) (exit {last.returncode if last else '?'}). "
        f"yt-dlp said: {tail or '(no output)'}") from last


def load_probe_file(path: str) -> dict:
    """Load a saved `yt-dlp --dump-single-json` blob (offline dry-run/tests)."""
    with open(abspath(path), "r", encoding="utf-8") as f:
        return json.load(f)


def probe_vod(url: str, dump_fn=_ytdlp_dump_json) -> dict:
    """Return normalized VOD metadata: {title, duration, id, uploader, url}."""
    meta = dump_fn(url)
    return {
        "title": meta.get("title") or meta.get("fulltitle") or "(unknown title)",
        "duration": int(meta.get("duration") or 0),
        "id": meta.get("id"),
        "uploader": meta.get("uploader") or meta.get("channel"),
        "url": meta.get("webpage_url") or url,
    }


def plan_frames(duration: int, start=0, end=None, interval: int = 300,
                max_frames: int | None = None) -> dict:
    """Pure planner: which sample offsets fall in [start, end) every interval.

    end defaults to (and is clamped to) the VOD duration. Returns a dict with
    the effective window, the offset list, count, and a 'capped' flag if
    max_frames trimmed the plan.
    """
    if interval <= 0:
        raise ValueError("sample interval must be > 0")
    duration = int(duration or 0)
    start = max(0, parse_time(start))
    end = duration if end is None else parse_time(end)
    if duration:
        end = min(end, duration)
    if end <= start:
        offsets: list[int] = []
    else:
        offsets = list(range(start, end, interval))
    capped = False
    if max_frames is not None and len(offsets) > max_frames:
        offsets = offsets[:max_frames]
        capped = True
    return {"start": start, "end": end, "interval": interval,
            "offsets": offsets, "count": len(offsets),
            "duration": duration, "capped": capped}


def _download_section_frame(url: str, offset: int, out_path: str,
                            height: int, pad: int, runner=subprocess) -> bool:
    """Download only a ~pad-second section at `offset` and grab one frame.

    Uses yt-dlp --download-sections so we fetch seconds, not hours. Returns
    True on success. Any failure is swallowed by the caller (a missing sample
    should not kill the whole VOD run).

    This is the PER-TIMESTAMP clip mode: one yt-dlp remote-seek per offset.
    Reliable near the start of a VOD, but yt-dlp/ffmpeg often cannot seek
    directly to a deep offset in a remote stream ("could not seek to
    position ..."), which is why LOCAL-WINDOW mode (below) is the default.
    """
    clip = out_path + ".section.mp4"
    section = f"*{fmt_hms(offset)}-{fmt_hms(offset + pad)}"
    cmd = ["yt-dlp", *js_runtime_args(), "-f",
           f"bv*[height<={height}]+ba/b[height<={height}]/b",
           "--no-playlist", "--force-keyframes-at-cuts",
           "--download-sections", section, "-o", clip, url]
    try:
        runner.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        log(f"  per-timestamp: yt-dlp failed at offset {fmt_hms(offset)}. "
            f"exact command:")
        log("    " + " ".join(cmd))
        if getattr(e, "stderr", None):
            log(f"  stderr: {str(e.stderr).strip()[-800:]}")
        raise
    # first frame of the tiny clip == the frame at `offset`
    runner.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", clip, "-frames:v", "1", out_path],
               check=True)
    if os.path.exists(clip):
        try:
            os.remove(clip)
        except OSError:
            pass  # best-effort cleanup; a leftover temp clip isn't fatal
    return os.path.exists(out_path)


def extract_youtube_frames_per_timestamp(url: str, out_dir: str,
                                         offsets: list[int], height: int = 720,
                                         pad: int = 2, ext: str = "png",
                                         frame_fn=_download_section_frame
                                         ) -> list[str]:
    """PER-TIMESTAMP clip mode: one remote yt-dlp seek+download per offset.

    Never downloads the full VOD, but each sample is its own remote seek —
    unreliable deep into long VODs. Kept as an explicit fallback; the default
    is LOCAL-WINDOW (extract_youtube_frames_local_window), which downloads
    one contiguous clip and seeks locally instead.
    """
    os.makedirs(out_dir, exist_ok=True)
    made = []
    for off in offsets:
        out_path = os.path.join(out_dir, f"{off:06d}.{ext}")
        try:
            if frame_fn(url, off, out_path, height, pad):
                made.append(out_path)
        except (subprocess.CalledProcessError, FileNotFoundError,
                OSError) as e:
            log(f"  offset {fmt_hms(off)}: skipped ({e})")
    return made


# --------------------------------------------------------- local-window mode
class StallTimeout(Exception):
    """A live subprocess made no real download progress before a deadline.

    Carries the command, the seconds waited, and the captured output tail so
    callers can log the exact stall and try a fallback / surface a remedy.
    Distinct from CalledProcessError (a clean non-zero exit) — a stall means
    the process was still 'alive' but stuck (the classic yt-dlp
    --download-sections hang: prints 'Destination' then never a byte again).
    """

    def __init__(self, cmd, waited, output=""):
        self.cmd = cmd
        self.waited = waited
        self.output = output
        super().__init__(
            f"no download progress for {waited:.0f}s — process killed")


# yt-dlp/ffmpeg progress lines carry a byte count or a percentage. Only these
# reset the stall clock; metadata lines ("Downloading 1 format(s)",
# "Destination: ...", "still downloading..." heartbeats) do NOT — otherwise a
# hung download that keeps printing its banner would look alive forever.
_PROGRESS_RE = re.compile(
    r"(\d+(?:\.\d+)?\s*(?:%|[KMG]i?B))"      # "37.2%"  "4.19MiB"  "512KiB/s"
    r"|frame=\s*\d+"                          # ffmpeg   "frame=  12"
    r"|\[download\]\s+\d")                     # yt-dlp    "[download]  4.1%"


def _is_progress(line: str) -> bool:
    """True if `line` is genuine byte/frame progress (resets the stall clock).

    Heartbeat lines we print ourselves never count, so a stalled process can
    never keep its own clock alive.
    """
    if "still downloading" in line or "still running" in line:
        return False
    return bool(_PROGRESS_RE.search(line))


def _run_live(cmd: list[str], prefix: str, runner=subprocess,
              heartbeat_every: float = 12.0,
              idle_msg: str = "still running",
              stall_timeout: float | None = None):
    """Run cmd, streaming its output live, killing it if it stalls.

    With the real subprocess module this uses Popen so long yt-dlp/ffmpeg
    downloads show live progress instead of looking frozen. If the process
    emits NO output for `heartbeat_every` seconds, a heartbeat line is
    printed ("still running... elapsed Xs") so it never looks hung.

    STALL GUARD: if `stall_timeout` is set and no *real progress* line (bytes
    or frames — see _is_progress) arrives within that many seconds, the whole
    process tree is killed and StallTimeout is raised. Heartbeats and metadata
    banners do NOT count as progress, so the classic yt-dlp section-download
    hang (prints 'Destination' then goes silent) is caught instead of looping
    heartbeats forever. Until the FIRST progress line, the process is given a
    grace period of max(stall_timeout, 20s) to start (metadata + format
    negotiation can be slow) before the stall clock applies.

    Injected fake runners (tests) usually only implement .run(), so anything
    without a Popen attribute falls back to the old captured one-shot call.
    On non-zero exit raises subprocess.CalledProcessError with the last output
    lines attached as .output so callers can log them.
    """
    if not hasattr(runner, "Popen"):
        return runner.run(cmd, check=True, capture_output=True, text=True)
    import queue
    import threading
    import time
    # New session/process-group on POSIX so a stall kill takes the child
    # ffmpeg yt-dlp spawns with it. On Windows taskkill /T handles the tree.
    popen_kw = {}
    if os.name != "nt":
        popen_kw["start_new_session"] = True
    try:
        proc = runner.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1,
                            **popen_kw)
    except TypeError:  # a fake Popen (tests) may not accept start_new_session
        proc = runner.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    q: queue.Queue = queue.Queue()

    def _reader():
        assert proc.stdout is not None
        for line in proc.stdout:
            q.put(line)
        q.put(None)

    threading.Thread(target=_reader, daemon=True).start()
    tail: list[str] = []
    t0 = last_line = last_beat = time.monotonic()
    last_progress = t0
    seen_progress = False
    # Grace before the FIRST byte: metadata/format negotiation can be slow, so
    # give the download the full stall window to start flowing. After the first
    # progress line arrives, the same window applies between progress updates.
    start_grace = stall_timeout or 0
    while True:
        try:
            line = q.get(timeout=max(0.05, heartbeat_every / 4))
        except queue.Empty:
            now = time.monotonic()
            if stall_timeout:
                deadline = stall_timeout if seen_progress else start_grace
                stuck = now - (last_progress if seen_progress else t0)
                if stuck >= deadline:
                    _kill_proc_tree(proc)
                    waited = now - (last_progress if seen_progress else t0)
                    print(f"{prefix} STALL — no download progress for "
                          f"{int(waited)}s, killed", flush=True)
                    raise StallTimeout(cmd, waited, "\n".join(tail))
            if (now - last_line >= heartbeat_every
                    and now - last_beat >= heartbeat_every):
                print(f"{prefix} {idle_msg}... elapsed {int(now - t0)}s "
                      f"(no output for {int(now - last_line)}s)", flush=True)
                last_beat = now
            continue
        if line is None:
            break
        line = line.rstrip()
        if line:
            print(f"{prefix} {line}", flush=True)
            tail.append(line)
            if len(tail) > 30:
                tail.pop(0)
            last_line = time.monotonic()
            if _is_progress(line):
                seen_progress = True
                last_progress = last_line
    proc.wait()
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd,
                                            output="\n".join(tail))
    return proc


def _kill_proc_tree(proc) -> None:
    """Best-effort kill of a live process and its children (yt-dlp spawns
    ffmpeg). Never raises — a failed kill must not mask the StallTimeout."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def clip_format(height: int, with_audio: bool = False) -> str:
    """yt-dlp -f selector. Video-only by default: frame extraction needs no
    audio, and skipping the audio stream + merge step is much faster."""
    if with_audio:
        return f"bv*[height<={height}]+ba/b[height<={height}]/b"
    return f"bestvideo[height<={height}]/best[height<={height}]/best"


def clip_format_ladder(height: int, with_audio: bool = False,
                       prefer_muxed: bool = False) -> list[str]:
    """Ordered -f selectors to try in turn when a download stalls.

    The first is the normal height-capped video-only pick. Each later rung is
    simpler / smaller / more universally available, because a stall is usually
    a specific format's fragment server being slow — a plainer format often
    starts flowing immediately. Progressive `best[...]` (muxed) formats avoid
    the separate-audio merge entirely, and `worst` is the last resort that
    almost always downloads. With --with-audio the first rung keeps audio; the
    fallbacks drop to muxed/worst which already include audio when present.

    prefer_muxed=True (used by --fast smoke runs) puts the muxed/progressive
    selector FIRST: observed live, YouTube's DASH video-only section
    downloads (e.g. format 397) can print 'Destination' then stall for the
    whole guard window, while the progressive format flows instantly. A
    smoke run cares about finishing fast, not about resolution — the actual
    downloaded resolution is always reported.
    """
    rungs = [clip_format(height, with_audio)]
    if prefer_muxed:
        rungs.insert(0, f"best[height<={height}]")
    for h in (480, 720):
        if h < height:
            rungs.append(f"best[height<={h}]/bestvideo[height<={h}]")
    rungs.append("best[height<=720]")
    rungs.append("worst")
    # de-dup, keep order
    seen, out = set(), []
    for r in rungs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


# yt-dlp prints this when it can't run the JS needed to unscramble some
# formats. It's a warning, not a hard error, but it's the usual reason a
# section download stalls — so we surface it as concrete setup advice.
_JS_RUNTIME_HINT = (
    "yt-dlp reported: no supported JavaScript runtime — some YouTube formats "
    "can't be unscrambled without one. Install Deno (recommended) or Node.js "
    "and re-run: https://github.com/yt-dlp/yt-dlp/wiki/EJS")


def detect_js_runtime(which=shutil.which) -> tuple[str | None, str | None]:
    """(runtime_name, path) of a yt-dlp-usable JS runtime, or (None, None).

    Modern yt-dlp only ENABLES Deno by default; an installed Node.js is
    ignored unless opted in with `--js-runtimes node`. Without a working JS
    runtime YouTube format URLs can't be unscrambled and section downloads
    print 'Destination:' then stall with zero bytes — the #1 capture failure
    on a normal Windows machine."""
    for name in ("deno", "node"):
        p = which(name)
        if p:
            return name, p
    return None, None


def js_runtime_args(which=shutil.which) -> list[str]:
    """Extra yt-dlp argv so an installed JS runtime is actually USED.

    Deno needs nothing (enabled by default). Node must be opted in."""
    name, _ = detect_js_runtime(which)
    if name == "node":
        return ["--js-runtimes", "node"]
    return []


def _saw_js_runtime_warning(text: str) -> bool:
    low = (text or "").lower()
    return ("no supported javascript runtime" in low
            or "no js runtime" in low
            or ("javascript runtime" in low and "not" in low))


# --------------------------------------------- direct-URL last-resort path
DIRECT_URL_TIMEOUT = 90     # seconds for `yt-dlp -g` to print a media URL


def _direct_media_url(url: str, height: int, runner=subprocess,
                      timeout: float = DIRECT_URL_TIMEOUT) -> str:
    """Ask yt-dlp for ONE direct googlevideo media URL (no download).

    Prefers muxed/progressive formats because the URL is fed straight to
    ffmpeg, which wants a single input stream. Raises on any failure."""
    fmt = f"best[height<={height}]/bestvideo[height<={height}]/best/worst"
    cmd = ["yt-dlp", *js_runtime_args(), "-g", "-f", fmt,
           "--no-playlist", url]
    res = runner.run(cmd, check=True, capture_output=True, text=True,
                     timeout=timeout)
    lines = [ln.strip() for ln in (res.stdout or "").splitlines()
             if ln.strip().startswith("http")]
    if not lines:
        raise ValueError("yt-dlp -g printed no direct media URL")
    return lines[0]


def _ffmpeg_cut_from_url(direct_url: str, start: int, end: int,
                         out_path: str, runner=subprocess,
                         stall_timeout: float | None = None) -> str:
    """Cut [start,end] straight from a direct media URL with ffmpeg.

    Video-only (frame extraction never needs audio). Tries a stream copy
    first (no re-encode); if the container refuses, re-encodes the short
    window. Both run under the same stall guard as yt-dlp downloads.
    Returns a note describing which variant worked; raises if both fail."""
    dur = max(1, int(end) - int(start))
    last_err: Exception | None = None
    for args, note in ((["-c", "copy"], "stream copy"),
                       (["-c:v", "libx264", "-preset", "veryfast"],
                        "re-encoded")):
        for p in (out_path, out_path + ".part"):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
               "-ss", str(int(start)), "-i", direct_url, "-t", str(dur),
               "-an", *args, "-progress", "pipe:1", "-nostats", out_path]
        try:
            _run_live(cmd, "[ffmpeg-direct]", runner,
                      idle_msg="still cutting",
                      stall_timeout=stall_timeout)
        except (subprocess.CalledProcessError, StallTimeout, OSError) as e:
            last_err = e
            log(f"direct-url: ffmpeg {note} cut failed "
                f"({type(e).__name__}) — "
                + ("trying re-encode..." if note == "stream copy"
                   else "giving up on the direct-url path."))
            continue
        if os.path.exists(out_path) and \
                os.path.getsize(out_path) >= MIN_CLIP_BYTES:
            return note
        last_err = ValueError(
            f"ffmpeg produced no usable clip ({note})")
    raise last_err or RuntimeError("direct-url cut failed")


def _download_youtube_clip(url: str, start: int, end: int, out_path: str,
                           height: int, runner=subprocess,
                           with_audio: bool = False,
                           stall_timeout: float | None = None,
                           formats: list[str] | None = None,
                           direct_fallback: bool = True,
                           prefer_muxed: bool = False) -> dict:
    """Download ONE contiguous local clip covering [start, end] (seconds).

    This is the LOCAL-WINDOW clip mode. Instead of asking yt-dlp/ffmpeg to
    remote-seek to each sample offset individually (unreliable far into a
    long VOD — "could not seek to position ..."), it issues a single
    --download-sections request for the whole requested window, then every
    frame extraction happens locally against the downloaded file with plain
    ffmpeg -ss seeks, which are fast and reliable at any offset.

    VIDEO-ONLY by default (with_audio=False): no audio stream, no merge.

    STALL HANDLING: if `stall_timeout` is set, a download that prints its
    'Destination' banner then stops sending bytes is killed after that many
    seconds (see _run_live) instead of heart-beating forever. On a stall we
    walk a fallback FORMAT LADDER (clip_format_ladder / `formats`) — simpler,
    smaller formats that usually start flowing right away. If every rung
    stalls, StallTimeout is raised with the JS-runtime hint attached when
    yt-dlp emitted that warning, so the caller can show a real remedy.

    LAST RESORT (direct_fallback=True): if EVERY yt-dlp rung stalls or errors,
    ask yt-dlp for a direct media URL (`-g`) and cut the window with plain
    ffmpeg. Only if that fails too does the original error propagate.

    Returns {"attempts": [...], "sizeBytes": N} — one attempt record per
    strategy tried, so callers/reports can show exactly what happened.
    Raises subprocess.CalledProcessError (clean non-zero exit), StallTimeout
    (every attempt stalled), or FileNotFoundError (yt-dlp silently produced
    nothing) after logging the exact failing command.
    """
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    section = f"*{fmt_hms(start)}-{fmt_hms(end)}"
    fmts = formats or clip_format_ladder(height, with_audio,
                                         prefer_muxed=prefer_muxed)
    js_args = js_runtime_args()
    rt_name, _rt_path = detect_js_runtime()
    log(f"local-window: downloading clip {fmt_hms(start)}-{fmt_hms(end)} "
        f"({'video+audio' if with_audio else 'video-only'}, <={height}p) "
        f"-> {out_path}"
        + (f"  [stall guard {int(stall_timeout)}s]" if stall_timeout else ""))
    log(f"local-window: JS runtime for yt-dlp: "
        + (f"{rt_name} (enabled)" if rt_name else
           "NONE — YouTube formats may stall; install Deno or Node.js"))

    attempts: list[dict] = []
    js_warned = False
    last_err: Exception | None = None
    got_clip = False
    cmd: list[str] = []
    for i, fmt in enumerate(fmts, start=1):
        # clear any partial from a previous stalled rung
        for p in (out_path, out_path + ".part"):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        cmd = ["yt-dlp", *js_args, "-f", fmt,
               "--no-playlist", "--force-keyframes-at-cuts",
               "--newline", "--progress",
               "--download-sections", section, "-o", out_path, url]
        if len(fmts) > 1:
            log(f"local-window: attempt {i}/{len(fmts)} — format {fmt!r}")
        t0 = time.monotonic()
        try:
            res = _run_live(cmd, "[yt-dlp]", runner,
                            idle_msg="still downloading",
                            stall_timeout=stall_timeout)
            attempts.append({"strategy": "yt-dlp section", "format": fmt,
                             "outcome": "ok",
                             "seconds": round(time.monotonic() - t0, 1)})
            if _saw_js_runtime_warning(getattr(res, "output", "") or ""):
                log(_JS_RUNTIME_HINT)
            got_clip = True
            break  # success
        except StallTimeout as e:
            last_err = e
            if _saw_js_runtime_warning(e.output):
                js_warned = True
            attempts.append({"strategy": "yt-dlp section", "format": fmt,
                             "outcome": "stalled",
                             "seconds": round(e.waited, 1),
                             "note": f"no download progress for "
                                     f"{int(e.waited)}s — killed"})
            log(f"local-window: attempt {i}/{len(fmts)} STALLED after "
                f"{int(e.waited)}s (format {fmt!r}).")
            if i < len(fmts):
                log("local-window: trying a simpler format...")
        except subprocess.CalledProcessError as e:
            last_err = e
            err = getattr(e, "stderr", None) or getattr(e, "output", None)
            tail = str(err).strip()[-300:] if err else f"exit {e.returncode}"
            attempts.append({"strategy": "yt-dlp section", "format": fmt,
                             "outcome": "error",
                             "seconds": round(time.monotonic() - t0, 1),
                             "note": tail})
            if _saw_js_runtime_warning(str(err or "")):
                js_warned = True
            log(f"local-window: attempt {i}/{len(fmts)} FAILED "
                f"(exit {e.returncode}, format {fmt!r}).")
            if err:
                log(f"  output tail: {str(err).strip()[-800:]}")
            if i < len(fmts):
                log("local-window: trying a simpler format...")

    if not got_clip and direct_fallback:
        # every yt-dlp section attempt failed — last resort: direct media
        # URL + plain ffmpeg cut. Errors here NEVER mask the original
        # failure; they are recorded and the original error is re-raised.
        log("local-window: all yt-dlp section attempts failed — "
            "trying direct media URL + ffmpeg cut (last resort)...")
        t0 = time.monotonic()
        try:
            direct = _direct_media_url(url, height, runner)
            log(f"local-window: got direct URL — cutting "
                f"{fmt_hms(start)}-{fmt_hms(end)} with ffmpeg...")
            note = _ffmpeg_cut_from_url(direct, start, end, out_path,
                                        runner, stall_timeout)
            attempts.append({"strategy": "direct-url + ffmpeg",
                             "format": f"best[height<={height}] (direct)",
                             "outcome": "ok",
                             "seconds": round(time.monotonic() - t0, 1),
                             "note": note})
            got_clip = True
            log(f"local-window: direct-url fallback SUCCEEDED ({note}).")
        except Exception as e:
            attempts.append({"strategy": "direct-url + ffmpeg",
                             "format": f"best[height<={height}] (direct)",
                             "outcome": "error",
                             "seconds": round(time.monotonic() - t0, 1),
                             "note": f"{type(e).__name__}: "
                                     f"{str(e)[:300]}"})
            log(f"local-window: direct-url fallback failed too "
                f"({type(e).__name__}: {e})")

    if not got_clip:
        log("local-window: every capture strategy failed. exact last "
            "yt-dlp command:")
        log("    " + " ".join(cmd))
        if js_warned:
            log(_JS_RUNTIME_HINT)
        if isinstance(last_err, StallTimeout):
            msg = str(last_err)
            if js_warned:
                msg += " — " + _JS_RUNTIME_HINT
            stall = StallTimeout(cmd, last_err.waited, last_err.output)
            stall.args = (msg,)
            stall.js_runtime = js_warned
            stall.attempts = attempts
            raise stall
        if last_err is not None:
            last_err.attempts = attempts
            raise last_err
        raise RuntimeError("clip download failed with no recorded error")

    if not os.path.exists(out_path):
        # yt-dlp sometimes appends the real container ext (clip.mp4.webm /
        # clip.mp4.mkv). Rename the single candidate back to the expected
        # path so ffmpeg and the cache keep working (ffmpeg sniffs content,
        # not extension).
        cands = sorted(c for c in glob.glob(out_path + ".*")
                       if not c.endswith(".part"))
        if len(cands) == 1:
            os.replace(cands[0], out_path)
            log(f"local-window: yt-dlp wrote {os.path.basename(cands[0])} — "
                f"renamed to expected {os.path.basename(out_path)}")
    if not os.path.exists(out_path):
        raise FileNotFoundError(
            f"yt-dlp reported success but clip is missing: {out_path}")
    size = os.path.getsize(out_path)
    log(f"local-window: clip downloaded ({size} bytes) -> {out_path}")
    return {"attempts": attempts, "sizeBytes": size}


# A real ~30s 480p video clip is tens of KB at the very least; anything under
# this is a stub/corrupt write (e.g. the 8-byte placeholder a stalled/aborted
# download can leave behind), never a usable clip.
MIN_CLIP_BYTES = 4096


class InvalidClip(Exception):
    """A clip file exists but is too small or unreadable to use as video."""


def probe_clip_valid(path: str, min_bytes: int = MIN_CLIP_BYTES,
                     runner=subprocess) -> tuple[bool, str]:
    """Return (ok, reason) for whether `path` is a usable video clip.

    Cheap byte-size floor first (catches the 8-byte stubs), then — if ffprobe
    is available — a real container/stream check. If ffprobe isn't installed
    we DON'T fail the clip on that alone (size passed, ffmpeg will surface any
    deeper problem later); we only report ffprobe's verdict when we can get it.
    Never raises: a probe error is returned as (False, reason) so callers can
    decide to delete + redownload.
    """
    if not os.path.exists(path):
        return False, "missing"
    size = os.path.getsize(path)
    if size < min_bytes:
        return False, f"too small ({size} bytes < {min_bytes})"
    # ffprobe: does it contain at least one video stream with a codec?
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=codec_type", "-of",
           "default=nw=1:nk=1", path]
    try:
        res = runner.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        # ffprobe not installed — size check passed, don't block on this.
        return True, f"ok ({size} bytes; ffprobe unavailable, size-checked)"
    except subprocess.CalledProcessError as e:
        err = (getattr(e, "stderr", "") or "").strip()[-160:]
        return False, f"ffprobe rejected the file ({err or 'no video stream'})"
    out = (getattr(res, "stdout", "") or "").strip()
    if "video" not in out:
        return False, "no decodable video stream"
    return True, f"ok ({size} bytes, video stream present)"


def probe_clip_resolution(path: str, runner=subprocess) -> dict | None:
    """{"width", "height", "codec", "duration"} of a clip via ffprobe.

    Best-effort: returns None when ffprobe is unavailable or the file can't
    be read — callers report 'unknown' instead of failing the run."""
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height,codec_name",
           "-show_entries", "format=duration",
           "-of", "json", path]
    try:
        res = runner.run(cmd, check=True, capture_output=True, text=True)
        data = json.loads(getattr(res, "stdout", "") or "{}")
        stream = (data.get("streams") or [{}])[0]
        w, h = stream.get("width"), stream.get("height")
        if not (w and h):
            return None
        dur = None
        try:
            dur = round(float((data.get("format") or {}).get("duration")), 1)
        except (TypeError, ValueError):
            pass
        return {"width": int(w), "height": int(h),
                "codec": stream.get("codec_name"), "duration": dur}
    except (FileNotFoundError, subprocess.CalledProcessError,
            ValueError, OSError, KeyError, IndexError):
        return None


def _extract_frame_local(clip_path: str, offset: int, clip_start: int,
                         out_path: str, runner=subprocess) -> bool:
    """Grab one frame from an already-downloaded local clip at `offset`.

    `clip_start` is the absolute VOD offset the clip begins at, so the local
    seek time is `offset - clip_start`. This is a plain local ffmpeg -ss
    seek — fast and reliable regardless of how deep `offset` is in the VOD,
    because it never touches the network.
    """
    local_t = max(0.0, offset - clip_start)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-ss", str(local_t), "-i", clip_path, "-frames:v", "1", out_path]
    try:
        runner.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        log(f"local-window: ffmpeg frame extract failed at offset "
            f"{fmt_hms(offset)}. exact command:")
        log("    " + " ".join(cmd))
        if getattr(e, "stderr", None):
            log(f"  stderr: {str(e.stderr).strip()[-500:]}")
        raise
    return os.path.exists(out_path)


def extract_youtube_frames_local_window(url: str, out_dir: str,
                                        offsets: list[int], height: int = 720,
                                        ext: str = "png", clip_pad: int = 2,
                                        download_fn=_download_youtube_clip,
                                        frame_fn=_extract_frame_local
                                        ) -> list[str]:
    """LOCAL-WINDOW clip mode (default): one download, then local seeks.

    Downloads a single clip spanning [min(offsets), max(offsets)+clip_pad]
    with `download_fn`, then extracts every planned offset from that local
    file with `frame_fn`. Fixes deep-offset failures like
    "could not seek to position 5400.000" because only the FIRST seek is
    remote (against the stream); every per-frame seek after that is local.

    Any failure (missing yt-dlp/ffmpeg, no network, bad clip) is raised so
    the caller (e.g. run_capture_trial.py) can fall back to fixtures; a
    single bad clip should not silently produce a half-empty frame set.
    """
    os.makedirs(out_dir, exist_ok=True)
    if not offsets:
        return []
    window_start = min(offsets)
    window_end = max(offsets) + clip_pad
    clip_path = os.path.join(out_dir, "_window_clip.mp4")
    log(f"local-window: {len(offsets)} planned frame(s) in window "
        f"{fmt_hms(window_start)}-{fmt_hms(window_end)}")
    download_fn(url, window_start, window_end, clip_path, height)

    made = []
    log(f"local-window: extracting {len(offsets)} frame(s) locally from "
        f"{clip_path}")
    try:
        for i, off in enumerate(offsets, start=1):
            out_path = os.path.join(out_dir, f"{off:06d}.{ext}")
            try:
                ok = frame_fn(clip_path, off, window_start, out_path)
            except (subprocess.CalledProcessError, FileNotFoundError,
                    OSError) as e:
                log(f"  [{i}/{len(offsets)}] offset {fmt_hms(off)}: "
                    f"skipped ({e})")
                continue
            if ok:
                made.append(out_path)
                log(f"  [{i}/{len(offsets)}] frame @ {fmt_hms(off)} -> "
                    f"{os.path.basename(out_path)}")
            else:
                log(f"  [{i}/{len(offsets)}] frame @ {fmt_hms(off)}: "
                    f"ffmpeg produced no output")
    finally:
        if os.path.exists(clip_path):
            try:
                os.remove(clip_path)  # disk hygiene — don't keep the clip around
            except OSError as e:
                log(f"local-window: could not remove temp clip {clip_path} "
                    f"({e}) — leaving it on disk.")
    log(f"local-window: extracted {len(made)}/{len(offsets)} frames "
        f"-> {out_dir}")
    return made


def extract_youtube_frames(url: str, out_dir: str, offsets: list[int],
                           height: int = 720, pad: int = 2, ext: str = "png",
                           clip_mode: str = "local-window",
                           frame_fn=_download_section_frame,
                           download_fn=_download_youtube_clip,
                           local_frame_fn=_extract_frame_local) -> list[str]:
    """Dispatch to the requested clip mode. Never downloads the full VOD.

    clip_mode:
      "local-window"   (default) one contiguous download + local ffmpeg
                        seeks. Reliable at any offset — see
                        extract_youtube_frames_local_window.
      "per-timestamp"   one remote yt-dlp seek per offset. Simpler, but
                        remote seeks to deep offsets in long VODs are
                        unreliable — kept as an explicit fallback.
    """
    if clip_mode == "local-window":
        return extract_youtube_frames_local_window(
            url, out_dir, offsets, height=height, ext=ext, clip_pad=pad,
            download_fn=download_fn, frame_fn=local_frame_fn)
    if clip_mode == "per-timestamp":
        return extract_youtube_frames_per_timestamp(
            url, out_dir, offsets, height=height, pad=pad, ext=ext,
            frame_fn=frame_fn)
    raise ValueError(f"unknown clip_mode: {clip_mode!r} "
                     f"(expected 'local-window' or 'per-timestamp')")


def ingest_vod(src: dict, start=0, end=None, interval: int | None = None,
               height: int = 720, dry_run: bool = False,
               max_frames: int | None = None, probe_override: dict | None = None,
               dump_fn=_ytdlp_dump_json,
               clip_mode: str = "local-window",
               frame_fn=_download_section_frame,
               download_fn=_download_youtube_clip,
               local_frame_fn=_extract_frame_local) -> dict:
    """Ingest one YouTube VOD source by timestamp sampling.

    dry_run=True probes metadata and prints the plan (title, duration, planned
    frame count) without downloading any frames. probe_override lets callers
    (and tests) supply metadata instead of hitting the network.

    clip_mode selects how frames are fetched (see extract_youtube_frames):
    "local-window" (default, one clip download + local seeks) or
    "per-timestamp" (one remote seek per offset, explicit fallback).
    frame_fn is only used in "per-timestamp" mode; download_fn/local_frame_fn
    are only used in "local-window" mode — all three exist purely so tests
    can inject fakes instead of touching yt-dlp/ffmpeg/network.
    """
    sid = source_id(src)
    if not sid:
        raise ValueError("youtube source is missing 'id'")
    if not src.get("enabled", True):
        log(f"{sid}: DISABLED in video_sources.json — skipping.")
        return {"id": sid, "skipped": "disabled"}
    url = src.get("url") or src.get("vodUrl")
    if not url:
        raise ValueError(f"youtube source '{sid}' is missing 'url'")

    interval = int(interval or src.get("sampleIntervalSeconds") or 300)
    meta = probe_override or probe_vod(url, dump_fn=dump_fn)
    plan = plan_frames(meta["duration"], start=start, end=end,
                       interval=interval, max_frames=max_frames)

    log(f"{sid}: \"{meta['title']}\"")
    log(f"  duration {fmt_hms(meta['duration'])} "
        f"({meta['duration']}s), uploader {meta.get('uploader')}")
    log(f"  window {fmt_hms(plan['start'])}–{fmt_hms(plan['end'])} "
        f"every {interval}s → {plan['count']} planned frames"
        + ("  [capped by --max-frames]" if plan["capped"] else ""))

    if dry_run:
        return {"id": sid, "title": meta["title"], "duration": meta["duration"],
                "plan": plan, "raw_dir": None, "frames": 0, "dry_run": True}

    raw_dir = os.path.join(VODS_DIR, sid, "frames_raw")
    shutil.rmtree(raw_dir, ignore_errors=True)
    os.makedirs(raw_dir, exist_ok=True)
    log(f"  clip-mode: {clip_mode}")
    made = extract_youtube_frames(url, raw_dir, plan["offsets"], height=height,
                                  clip_mode=clip_mode, frame_fn=frame_fn,
                                  download_fn=download_fn,
                                  local_frame_fn=local_frame_fn)
    log(f"  extracted {len(made)}/{plan['count']} frames → {raw_dir}")
    return {"id": sid, "title": meta["title"], "duration": meta["duration"],
            "plan": plan, "raw_dir": raw_dir, "frames": len(made),
            "dry_run": False}


def find_source(sources_path: str, wanted_id: str) -> dict | None:
    for s in load_sources(sources_path):
        if source_id(s) == wanted_id:
            return s
    return None


# ------------------------------------------------------------------- main
def ingest_all(sources_path: str, only: str | None, max_sources: int,
               require_match: bool = True) -> list[dict]:
    con = db.connect()
    reports = []
    todo = [s for s in load_sources(sources_path)
            if not is_youtube_source(s)          # VOD sources use --source
            and (only is None or source_match_id(s) == only)]
    for src in todo[:max_sources]:
        mid = source_match_id(src) or "<no-match>"
        if require_match and (mid == "<no-match>" or not match_exists(con, mid)):
            log(f"{mid}: SKIP — match not in DB yet (ingest FACEIT facts "
                f"first). A video source never invents a match.")
            continue
        try:
            rep = ingest_source(src)
            log(f"{rep['match']}: {rep['frames']} raw frames via {rep['via']} "
                f"→ {rep['raw_dir']}")
            reports.append(rep)
        except (FileNotFoundError, ValueError,
                subprocess.CalledProcessError) as e:
            log(f"{mid}: SKIP — {e}")
    if not reports:
        log("no frames ingested.")
    return reports


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Extract sample frames from video sources (no comps here).")
    ap.add_argument("--sources", default=DEFAULT_SOURCES)
    # match-paired batch
    ap.add_argument("--match", help="only this match-paired source's match id")
    ap.add_argument("--max", type=int, default=4,
                    help="max match-paired sources per run (free-CI budget)")
    ap.add_argument("--allow-missing-match", action="store_true",
                    help="ingest frames even if the match is not in the DB")
    # YouTube VOD sampling (by id)
    ap.add_argument("--source", help="ingest this VOD source id (video_sources.json)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print VOD title, duration, and planned frame count; "
                         "download nothing")
    ap.add_argument("--start", default=0,
                    help="window start (seconds or H:MM:SS), default 0")
    ap.add_argument("--end", default=None,
                    help="window end (seconds or H:MM:SS), default full duration")
    ap.add_argument("--sample-interval", type=int, default=None,
                    help="seconds between samples (default: source value)")
    ap.add_argument("--height", type=int, default=720,
                    help="max video height to fetch per section")
    ap.add_argument("--max-frames", type=int, default=None,
                    help="safety cap on planned frames")
    ap.add_argument("--probe-file",
                    help="use a saved `yt-dlp --dump-single-json` blob instead "
                         "of the network (offline dry-run)")
    ap.add_argument("--clip-mode", choices=["local-window", "per-timestamp"],
                    default="local-window",
                    help="local-window (default): one yt-dlp download for "
                         "the whole [start,end] window + local ffmpeg seeks "
                         "(reliable at any offset). per-timestamp: one "
                         "remote yt-dlp seek per sample offset (fallback; "
                         "unreliable deep into long VODs).")
    args = ap.parse_args()

    if args.source:
        src = find_source(args.sources, args.source)
        if src is None:
            raise SystemExit(f"no source with id '{args.source}' in {args.sources}")
        if not is_youtube_source(src):
            raise SystemExit(f"source '{args.source}' is not a youtube/VOD source; "
                             f"use --match for match-paired sources.")
        probe_override = None
        if args.probe_file:
            probe_override = probe_vod(str(args.probe_file),
                                       dump_fn=lambda _u: load_probe_file(args.probe_file))
        ingest_vod(src, start=args.start, end=args.end,
                   interval=args.sample_interval, height=args.height,
                   dry_run=args.dry_run, max_frames=args.max_frames,
                   probe_override=probe_override, clip_mode=args.clip_mode)
        return

    ingest_all(args.sources, args.match, args.max,
               require_match=not args.allow_missing_match)


if __name__ == "__main__":
    main()
