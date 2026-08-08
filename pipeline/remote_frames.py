#!/usr/bin/env python3
"""
remote_frames.py — sparse frames out of a remote VOD, without the VOD.

THE PROBLEM THIS EXISTS TO FIX
------------------------------
Calibrating a broadcast's HUD needs maybe a dozen good frames. The old path
(`capture.download_vod`) fetched the entire nine-hour 720p broadcast — on
the order of several gigabytes — and then threw all but a handful of frames
away. Every re-calibration paid that cost again.

Nothing about the task requires the whole file. An MP4 on YouTube's CDN is
served over HTTP with byte ranges, and ffmpeg knows how to seek in one: give
it a direct media URL and `-ss <t>` BEFORE `-i`, and it issues range
requests to land near `t` and decodes forward to the exact frame. What
arrives over the wire is a few hundred kilobytes, not a few gigabytes.

HOW THIS IS PUT TOGETHER
------------------------
1. **One** yt-dlp invocation, ever, per source: `yt-dlp -g` resolves a
   direct media URL. Everything after that is ffmpeg against that URL. This
   is the difference between one process and the several hundred that
   "one yt-dlp --download-sections per timestamp" would cost — and it is
   also why this is fast: yt-dlp's player extraction is the slow part, and
   it happens once.
2. **Video-only, highest usable quality.** Audio is irrelevant to reading a
   HUD, and skipping it halves the bytes and removes the merge step. The
   format ladder prefers 1080p video-only precisely because portrait crops
   and chip geometry are what calibration measures.
3. **Batched ranges, not per-frame processes.** Offsets that sit close
   together (a dense 1-second burst around a suspected hero swap) are
   fetched as ONE contiguous read; offsets far apart (the 60-second
   calibration scan) each get their own tiny seek, because a contiguous
   read between them would download the whole gap. `plan_batches` is the
   pure function that makes that call, and it is tested directly.
4. **A deterministic cache**, keyed by video + timestamp + resolution, so a
   re-run costs nothing and a densification only pays for the frames it
   actually adds.
5. **Honest accounting.** Every acquisition reports bytes over the wire
   (measured, on platforms that expose it), process counts, and cache hits,
   because "we no longer download the whole VOD" is a claim that should be
   measurable rather than asserted.

A direct media URL is short-lived and signed. It is never logged (see
`ytdlp_opts.redact_text`), never written to the cache manifest, and is
re-resolved automatically when it expires mid-run.

Usage:
  python pipeline/remote_frames.py --url "https://youtu.be/..." \\
      --interval 60 --limit 20            # scan, report, cache
  python pipeline/remote_frames.py --url ... --offsets 60,120,1800
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proc_text  # noqa: E402
import ytdlp_opts  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CACHE_ROOT = os.path.join(REPO_ROOT, "work", "remote_frames")

#: Calibration measures chip geometry and portrait texture, so it wants the
#: best pixels available. 1080p video-only is the target; the ladder walks
#: down only if the broadcast does not offer it.
DEFAULT_HEIGHT = 1080

#: A signed googlevideo URL outlives a scan comfortably but not a long run.
#: Re-resolve well before the usual expiry rather than discovering it as a
#: 403 in the middle of a densification pass.
URL_TTL_SECONDS = 90 * 60

#: Two offsets closer than this are cheaper to fetch as one contiguous read
#: than as two seeks: the gap costs less than a second seek's overhead and
#: its re-buffering. Above it, the gap is pure waste.
DEFAULT_BATCH_GAP = 12.0

#: However close together they are, never pull more than this in one read —
#: a runaway batch is how "sparse acquisition" quietly becomes a download.
DEFAULT_BATCH_SPAN = 180.0

#: ffmpeg seek + decode of one frame from a remote MP4. Generous: a cold CDN
#: connection at a deep offset is legitimately slow. Not unbounded.
FRAME_TIMEOUT = 120
RESOLVE_TIMEOUT = 120

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def log(msg: str) -> None:
    print(f"[remote] {msg}", flush=True)


# --------------------------------------------------------------- identity
def video_key(url: str) -> str:
    """A stable, filesystem-safe cache key for one video.

    A YouTube id when we can see one — it is the thing that actually
    identifies the video, and it survives the URL being written five
    different ways. Otherwise a hash of the URL, which is stable for
    anything else (including a local file used in tests).
    """
    raw = (url or "").strip()
    try:
        parts = urllib.parse.urlsplit(raw if "//" in raw else "//" + raw)
        host = (parts.hostname or "").lower()
        if "youtu" in host:
            vid = urllib.parse.parse_qs(parts.query).get("v", [None])[0]
            if not vid:
                segs = [s for s in parts.path.split("/") if s]
                if segs:
                    vid = segs[-1] if host.endswith("youtu.be") else (
                        segs[1] if len(segs) >= 2 and segs[0].lower() in
                        ("live", "embed", "shorts", "v") else None)
            if vid and _VIDEO_ID_RE.match(vid):
                return vid
    except ValueError:
        pass
    return "u" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def format_ladder(height: int) -> list[str]:
    """yt-dlp -f selectors, best usable quality first, all video-only.

    mp4/h264 first because ffmpeg seeks it most reliably over HTTP; the
    later rungs accept whatever the broadcast actually offers rather than
    failing because the ideal container is absent.
    """
    h = int(height)
    return [
        f"bestvideo[height<={h}][ext=mp4][vcodec^=avc1]",
        f"bestvideo[height<={h}][ext=mp4]",
        f"bestvideo[height<={h}]",
        f"best[height<={h}][ext=mp4]",
        f"best[height<={h}]",
        "best",
    ]


# ---------------------------------------------------------------- planning
def plan_batches(offsets, *, batch_gap: float = DEFAULT_BATCH_GAP,
                 batch_span: float = DEFAULT_BATCH_SPAN,
                 step: float | None = None) -> list[dict]:
    """Group timestamps into the reads that will actually be issued.

    Pure, deterministic and the whole economic argument of this module, so
    it is a function you can test rather than a shape buried in a loop:

      * offsets within `batch_gap` of each other become ONE range read —
        the bytes between them were going to be transferred anyway;
      * offsets further apart stay separate seeks, because a range read
        across a five-minute gap downloads five minutes of video;
      * a run is split when it would exceed `batch_span`, so a long dense
        pass can never silently become a full download.

    Returns a list of {"kind": "single"|"range", "offsets": [...],
    "start": t0, "end": t1, "step": s}.
    """
    ts = sorted({round(float(t), 3) for t in offsets})
    if not ts:
        return []
    batches: list[dict] = []
    run = [ts[0]]
    for t in ts[1:]:
        gap = t - run[-1]
        span = t - run[0]
        if gap <= batch_gap and span <= batch_span:
            run.append(t)
        else:
            batches.append(_batch(run, step))
            run = [t]
    batches.append(_batch(run, step))
    return batches


def _batch(run: list[float], step: float | None) -> dict:
    if len(run) == 1:
        return {"kind": "single", "offsets": list(run),
                "start": run[0], "end": run[0], "step": None}
    gaps = [round(b - a, 3) for a, b in zip(run, run[1:])]
    # A uniform run can be pulled with one fps filter. A ragged one is read
    # as a range and then each wanted timestamp is taken from it, which is
    # still one transfer instead of many.
    uniform = len(set(gaps)) == 1
    s = step if step is not None else (gaps[0] if uniform else min(gaps))
    return {"kind": "range", "offsets": list(run),
            "start": run[0], "end": run[-1], "step": s,
            "uniform": uniform}


# ------------------------------------------------------- byte accounting
def _iface_bytes() -> int | None:
    """Total bytes received by this machine's non-loopback interfaces.

    Linux only, and system-wide rather than per-process — which is exactly
    what it is reported as. It is the only measurement available without
    eBPF or a proxy, and on a quiet benchmark machine it is the honest
    answer to "how much did this actually pull". Everywhere else this
    returns None and callers report bytes as unmeasured rather than
    inventing a number.
    """
    try:
        with open("/proc/net/dev", "r", encoding="utf-8") as f:
            lines = f.readlines()[2:]
    except OSError:
        return None
    total = 0
    for line in lines:
        name, _, rest = line.partition(":")
        if name.strip() == "lo":
            continue
        fields = rest.split()
        if fields:
            try:
                total += int(fields[0])
            except ValueError:
                continue
    return total


class ByteMeter:
    """Measures bytes over the wire across a block of work, or admits it
    cannot. `delta` is None on any platform without /proc/net/dev."""

    def __init__(self) -> None:
        self.start = _iface_bytes()
        self.end: int | None = None

    def stop(self) -> int | None:
        self.end = _iface_bytes()
        return self.delta

    @property
    def delta(self) -> int | None:
        if self.start is None or self.end is None:
            return None
        return max(0, self.end - self.start)


# ------------------------------------------------------------------ source
class RemoteFrameError(RuntimeError):
    """No usable frame could be acquired, with a reason a person can act on."""


class RemoteFrameSource:
    """Sparse frames from a remote VOD, cached deterministically.

    `resolver` and `runner` are injected so every behaviour here is
    testable offline: the tests point `url` at a file:// or http://
    localhost video and pass a resolver that returns it unchanged, which
    exercises the real ffmpeg seek/batch/cache path without a network.
    """

    def __init__(self, url: str, *, height: int = DEFAULT_HEIGHT,
                 cache_root: str | None = None, source_id: str | None = None,
                 runner=subprocess, resolver=None,
                 batch_gap: float = DEFAULT_BATCH_GAP,
                 batch_span: float = DEFAULT_BATCH_SPAN,
                 ext: str = "jpg", quality: int = 2):
        self.url = url
        self.height = int(height)
        self.source_id = source_id
        self.key = video_key(url)
        self.runner = runner
        self._resolver = resolver or self._resolve_with_ytdlp
        self.batch_gap = float(batch_gap)
        self.batch_span = float(batch_span)
        self.ext = ext
        self.quality = int(quality)
        self.dir = os.path.join(cache_root or DEFAULT_CACHE_ROOT,
                                self.key, f"{self.height}p")
        os.makedirs(self.dir, exist_ok=True)
        self._direct: str | None = None
        self._direct_at: float = 0.0
        self._chosen_format: str | None = None
        self.stats = {
            "ytdlpCalls": 0, "ffmpegCalls": 0,
            "framesRequested": 0, "framesFromCache": 0, "framesFetched": 0,
            "framesMissing": 0, "batches": 0, "rangeBatches": 0,
            "seconds": 0.0, "bytesDownloaded": None,
            "bytesMeasured": _iface_bytes() is not None,
        }

    # ---------------------------------------------------------- cache keys
    def path_for(self, t: float) -> str:
        """Deterministic: video key + resolution (in the directory) and the
        timestamp to a tenth of a second (in the name). Two runs asking for
        the same instant of the same video at the same height resolve to
        the same file, on every platform."""
        return os.path.join(self.dir, f"t{float(t):09.1f}.{self.ext}")

    def cached(self, t: float) -> str | None:
        p = self.path_for(t)
        return p if os.path.exists(p) and os.path.getsize(p) > 0 else None

    # -------------------------------------------------------- URL resolve
    def _resolve_with_ytdlp(self, url: str, height: int) -> tuple[str, str]:
        """ONE yt-dlp call: a direct, video-only media URL. Returns
        (direct_url, format_selector_that_worked)."""
        auth = ytdlp_opts.load_auth_config()
        last = ""
        for fmt in format_ladder(height):
            cmd = ["yt-dlp", *auth.base_args(), *_js_args(),
                   "-g", "-f", fmt, "--no-playlist", url]
            self.stats["ytdlpCalls"] += 1
            try:
                res = self.runner.run(cmd, check=True, capture_output=True,
                                      timeout=RESOLVE_TIMEOUT,
                                      **proc_text.PIPE_TEXT)
            except FileNotFoundError as exc:
                raise RemoteFrameError(
                    "yt-dlp is not installed, so no remote frame can be "
                    "fetched. Install it: "
                    + ytdlp_opts.REMEDIES.get("yt-dlp", "pip install -U yt-dlp")
                ) from exc
            except (subprocess.CalledProcessError,
                    subprocess.TimeoutExpired) as exc:
                last = ytdlp_opts.redact_text(
                    str(getattr(exc, "stderr", "") or exc))[-400:]
                continue
            urls = [ln.strip() for ln in (res.stdout or "").splitlines()
                    if ln.strip().startswith("http")]
            if urls:
                return urls[0], fmt
            last = "yt-dlp printed no media URL"
        raise RemoteFrameError(
            f"could not resolve a direct media URL for this broadcast "
            f"(tried {len(format_ladder(height))} formats). Last reason: "
            f"{last or 'unknown'}")

    def direct_url(self, *, refresh: bool = False) -> str:
        fresh = (self._direct is not None
                 and (time.time() - self._direct_at) < URL_TTL_SECONDS)
        if fresh and not refresh:
            return self._direct
        self._direct, self._chosen_format = self._resolver(self.url, self.height)
        self._direct_at = time.time()
        return self._direct

    # ------------------------------------------------------------- ffmpeg
    def _ffmpeg(self, args: list[str]) -> bool:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
        self.stats["ffmpegCalls"] += 1
        try:
            self.runner.run(cmd, check=True, capture_output=True,
                            timeout=FRAME_TIMEOUT, **proc_text.PIPE_TEXT)
            return True
        except FileNotFoundError as exc:
            raise RemoteFrameError(
                "ffmpeg is not installed, so no frame can be decoded. "
                "Install it: "
                + ytdlp_opts.REMEDIES.get("ffmpeg", "see docs/windows-setup.md")
            ) from exc
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False

    def _grab_single(self, t: float, direct: str) -> bool:
        """One frame at `t`. `-ss` before `-i` is the whole point: ffmpeg
        seeks the INPUT (range requests) instead of decoding from zero."""
        out = self.path_for(t)
        ok = self._ffmpeg(["-ss", f"{float(t):.3f}", "-i", direct,
                           "-frames:v", "1", "-q:v", str(self.quality),
                           "-an", "-sn", out])
        return ok and os.path.exists(out) and os.path.getsize(out) > 0

    def _grab_range(self, batch: dict, direct: str) -> list[float]:
        """Every wanted offset inside one contiguous read.

        A uniform run is pulled with an fps filter in a single decode; a
        ragged one is read as a range and each wanted instant is taken from
        the same buffered transfer. Either way it is one connection.
        """
        t0, t1 = batch["start"], batch["end"]
        step = batch.get("step") or 1.0
        dur = max(step, (t1 - t0) + step)
        tmp_pattern = os.path.join(self.dir, f"_b{int(t0 * 10):010d}_%05d.{self.ext}")
        ok = self._ffmpeg(["-ss", f"{t0:.3f}", "-i", direct,
                           "-t", f"{dur:.3f}",
                           "-vf", f"fps=1/{step}", "-q:v", str(self.quality),
                           "-an", "-sn", "-start_number", "0", tmp_pattern])
        got: list[float] = []
        if not ok:
            _sweep(self.dir, f"_b{int(t0 * 10):010d}_")
            return got
        wanted = list(batch["offsets"])
        i = 0
        produced: list[tuple[float, str]] = []
        while True:
            src = tmp_pattern % i
            if not os.path.exists(src):
                break
            produced.append((round(t0 + i * step, 3), src))
            i += 1
        for want in wanted:
            # nearest produced frame within half a step — the fps filter
            # lands on the frame at or just after each tick
            best = None
            for made_t, src in produced:
                d = abs(made_t - want)
                if d <= step * 0.75 and (best is None or d < best[0]):
                    best = (d, src)
            if best is None:
                continue
            dest = self.path_for(want)
            try:
                if os.path.exists(dest):
                    os.remove(dest)
                os.replace(best[1], dest)
            except OSError:
                continue
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                got.append(want)
        _sweep(self.dir, f"_b{int(t0 * 10):010d}_")
        return got

    # --------------------------------------------------------------- grab
    def grab(self, offsets, *, progress=None) -> dict[float, str | None]:
        """Acquire every offset (seconds), reusing whatever is cached.

        Returns {offset: path or None}. A missing frame is None rather than
        an exception: one unreadable instant in a scan must not lose the
        other nineteen.
        """
        wanted = sorted({round(float(t), 1) for t in offsets})
        self.stats["framesRequested"] += len(wanted)
        out: dict[float, str | None] = {}
        todo = []
        for t in wanted:
            hit = self.cached(t)
            if hit:
                out[t] = hit
                self.stats["framesFromCache"] += 1
            else:
                todo.append(t)
        if not todo:
            return out

        meter = ByteMeter()
        t_start = time.monotonic()
        direct = self.direct_url()
        batches = plan_batches(todo, batch_gap=self.batch_gap,
                               batch_span=self.batch_span)
        self.stats["batches"] += len(batches)
        self.stats["rangeBatches"] += sum(1 for b in batches
                                          if b["kind"] == "range")
        done = 0
        # A signed media URL that expires mid-run looks exactly like a decode
        # failure, so one empty read earns one re-resolve. ONCE per call,
        # though: an offset past the end of the video also reads empty, and
        # re-resolving for each of those would be noise pretending to be
        # recovery.
        retried = False
        for b in batches:
            got: list[float] = []
            while True:
                if b["kind"] == "single":
                    t = b["offsets"][0]
                    got = [t] if self._grab_single(t, direct) else []
                else:
                    got = self._grab_range(b, direct)
                if got or retried:
                    break
                retried = True
                log("a read returned nothing — re-resolving the media URL "
                    "once, in case it expired, and retrying")
                direct = self.direct_url(refresh=True)
            for t in b["offsets"]:
                p = self.cached(t)
                out[t] = p
                if p:
                    self.stats["framesFetched"] += 1
                else:
                    self.stats["framesMissing"] += 1
            done += len(b["offsets"])
            if progress:
                progress(done, len(todo))

        self.stats["seconds"] = round(
            self.stats["seconds"] + (time.monotonic() - t_start), 2)
        delta = meter.stop()
        if delta is not None:
            self.stats["bytesDownloaded"] = (
                (self.stats["bytesDownloaded"] or 0) + delta)
        self._write_manifest()
        return out

    # ----------------------------------------------------------- manifest
    def _write_manifest(self) -> None:
        """Provenance for the cache. The signed media URL is deliberately
        NOT recorded — it is a credential with an expiry, and this file is
        written into the work tree."""
        payload = {
            "schema": "remote-frames.v1",
            "videoKey": self.key,
            "sourceId": self.source_id,
            "sourceUrl": ytdlp_opts.redact_text(self.url),
            "requestedHeight": self.height,
            "format": self._chosen_format,
            "batchGapSeconds": self.batch_gap,
            "batchSpanSeconds": self.batch_span,
            "frames": sorted(f for f in os.listdir(self.dir)
                             if f.startswith("t") and f.endswith(self.ext)),
            "stats": dict(self.stats),
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": ("Frames fetched by HTTP range from the broadcast's own "
                     "media URL. The whole VOD is never downloaded. The "
                     "signed URL is not recorded here on purpose."),
        }
        try:
            with open(os.path.join(self.dir, "manifest.json"), "w",
                      encoding="utf-8") as f:
                json.dump(payload, f, indent=1)
        except OSError:
            pass    # a manifest is provenance, not the product

    def report(self) -> dict:
        s = dict(self.stats)
        s["cacheDir"] = self.dir
        s["videoKey"] = self.key
        return s


def _sweep(directory: str, prefix: str) -> None:
    try:
        for fn in os.listdir(directory):
            if fn.startswith(prefix):
                try:
                    os.remove(os.path.join(directory, fn))
                except OSError:
                    pass
    except OSError:
        pass


def _js_args() -> list[str]:
    """Reuse the repo's single opinion about JS runtimes for yt-dlp without
    importing the heavy download machinery (video_ingest pulls in cv2)."""
    try:
        return ytdlp_opts.js_runtime_args()
    except Exception:          # noqa: BLE001 — a probe must never be fatal
        return []


# ------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    proc_text.enable_utf8_stdio()
    ap = argparse.ArgumentParser(
        description="Fetch sparse frames from a remote VOD without "
                    "downloading it")
    ap.add_argument("--url", required=True)
    ap.add_argument("--source-id")
    ap.add_argument("--interval", type=float, default=60.0,
                    help="seconds between samples (default 60)")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float,
                    help="stop here (default: --limit samples)")
    ap.add_argument("--limit", type=int, default=20,
                    help="max samples when --end is not given")
    ap.add_argument("--offsets", help="explicit comma list of seconds")
    ap.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    ap.add_argument("--cache-root")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.offsets:
        offsets = [float(x) for x in args.offsets.split(",") if x.strip()]
    else:
        end = (args.end if args.end is not None
               else args.start + args.interval * args.limit)
        offsets, t = [], args.start
        while t < end and len(offsets) < 100000:
            offsets.append(round(t, 1))
            t += args.interval

    src = RemoteFrameSource(args.url, height=args.height,
                            cache_root=args.cache_root,
                            source_id=args.source_id)
    log(f"video {src.key} · {len(offsets)} sample(s) at {args.interval}s "
        f"· <={args.height}p video-only")
    plan = plan_batches(offsets, batch_gap=src.batch_gap,
                        batch_span=src.batch_span)
    log(f"plan: {len(plan)} read(s) "
        f"({sum(1 for b in plan if b['kind'] == 'range')} batched range(s))")
    try:
        got = src.grab(offsets, progress=lambda d, n: (
            log(f"  {d}/{n}") if d % 10 == 0 or d == n else None))
    except RemoteFrameError as exc:
        log(f"FAILED — {exc}")
        return 1
    have = sum(1 for p in got.values() if p)
    rep = src.report()
    if args.json:
        print(json.dumps({"frames": {str(k): v for k, v in got.items()},
                          "stats": rep}, indent=1))
    else:
        log(f"acquired {have}/{len(offsets)} frame(s) -> {src.dir}")
        log(f"yt-dlp calls: {rep['ytdlpCalls']} · ffmpeg calls: "
            f"{rep['ffmpegCalls']} · from cache: {rep['framesFromCache']}")
        if rep["bytesDownloaded"] is not None:
            log(f"bytes over the wire: {rep['bytesDownloaded'] / 1e6:.1f} MB "
                f"(host-wide measurement)")
        else:
            log("bytes over the wire: not measurable on this platform")
    return 0 if have else 1


if __name__ == "__main__":
    raise SystemExit(main())
