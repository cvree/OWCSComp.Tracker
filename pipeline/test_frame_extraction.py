#!/usr/bin/env python3
"""
test_frame_extraction.py — offline tests for the two ffmpeg stages that run
unattended for the longest and used to fail worst.

Neither ffmpeg nor ffprobe is ever invoked; every subprocess is a scripted
fake, so this suite is fast and runs anywhere.

Covers:
  * capture.extract_frames walks a long video in WINDOWS, so a crash costs
    one window rather than the whole scan
  * a window that crashes (exit 3221225477 == 0xC0000005, the real Windows
    failure this replaced) is retried, then recovered one seek at a time,
    and the frames written before the crash are kept
  * a hung ffmpeg is killed on a deadline instead of hanging forever
  * a genuinely unreadable file raises FrameExtractionError carrying a
    stable code and an operator remedy — not a bare CalledProcessError
  * an unknown duration degrades to one guarded pass rather than failing
  * video_ingest.FfmpegProgress turns `-progress pipe:1` into one readable
    percentage/ETA line, never hides a real ffmpeg error, and keeps the
    stall clock fed
  * make_scan_proxy actually asks for that progress and runs under a stall
    guard, so a silent 25-minute transcode can no longer be mistaken for a
    hang (or hang forever undetected)

Run:  python pipeline/test_frame_extraction.py   (non-zero on failure)
"""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import capture                    # noqa: E402
import video_ingest as vi         # noqa: E402

_fails = 0


def check(name: str, cond: bool) -> None:
    global _fails
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        _fails += 1


class Res:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class FakeFfmpeg:
    """ffprobe reports `duration`; ffmpeg writes the PNGs a real pass would.

    `crash_at` names window START offsets that die with a Windows access
    violation. `hang_at` names ones that never return. `single_ok` decides
    whether the per-frame seek fallback works.
    """

    def __init__(self, duration=3000.0, crash_at=(), hang_at=(),
                 single_ok=True, crash_writes=True):
        self.duration = duration
        self.crash_at, self.hang_at = set(crash_at), set(hang_at)
        self.single_ok = single_ok
        # A real crash usually leaves the frames it already wrote behind;
        # crash_writes=False is the harsher case where nothing survives.
        self.crash_writes = crash_writes
        self.filter_passes, self.single_seeks = [], []

    def run(self, cmd, **kw):
        if cmd[0] == "ffprobe":
            return Res(0, "" if self.duration is None
                       else f"{self.duration}\n")
        start = int(cmd[cmd.index("-ss") + 1]) if "-ss" in cmd else 0
        if "-frames:v" in cmd:                      # one-seek fallback
            self.single_seeks.append(start)
            if not self.single_ok:
                return Res(1, err="still broken")
            with open(cmd[-1], "wb") as f:
                f.write(b"png")
            return Res(0)
        self.filter_passes.append(start)            # fps= filter pass
        if start in self.hang_at:
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 1))
        span = int(cmd[cmd.index("-t") + 1])
        interval = int(cmd[cmd.index("-vf") + 1].split("/")[1])
        pattern = cmd[-1]
        written = span // interval
        if start in self.crash_at:
            written = written // 2 if self.crash_writes else 0
        for k in range(written):
            with open(pattern % k, "wb") as f:
                f.write(b"png")
        if start in self.crash_at:
            return Res(3221225477, err="Access violation")
        return Res(0)


def offsets(frames):
    return [int(os.path.splitext(os.path.basename(p))[0]) for p in frames]


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="owcs_frames_")

    print("a long video is sampled in windows, not one unbounded pass:")
    r = FakeFfmpeg(duration=3000.0)
    frames = capture.extract_frames("v.mp4", os.path.join(tmp, "a"), 10,
                                    window_seconds=1200, runner=r)
    check("every 10s offset across 50 minutes is produced",
          offsets(frames) == list(range(0, 3000, 10)))
    check("it ran one pass per 20-minute window (3), not one giant pass",
          r.filter_passes == [0, 1200, 2400])
    check("frames are named by ABSOLUTE offset, not window-relative index",
          frames[-1].endswith("002990.png"))

    print("a window that crashes costs that window, never the whole scan:")
    r = FakeFfmpeg(duration=3000.0, crash_at=(1200,))
    frames = capture.extract_frames("v.mp4", os.path.join(tmp, "b"), 10,
                                    window_seconds=1200, runner=r)
    check("the scan still yields every sample", offsets(frames)
          == list(range(0, 3000, 10)))
    check("the crashed window was retried once before falling back",
          r.filter_passes.count(1200) == 2)
    check("only the crashed window needed per-frame seeks",
          r.single_seeks and all(1200 <= o < 2400 for o in r.single_seeks))
    check("frames written BEFORE the crash were kept, not re-fetched",
          len(r.single_seeks) < 120)

    print("a hung ffmpeg is killed on a deadline, not waited on forever:")
    r = FakeFfmpeg(duration=1200.0, hang_at=(0,))
    frames = capture.extract_frames("v.mp4", os.path.join(tmp, "c"), 10,
                                    window_seconds=1200, runner=r)
    check("the hang is survived and the window recovered per frame",
          len(frames) == 120)

    print("an unreadable file fails with a code and a remedy:")
    r = FakeFfmpeg(duration=3000.0, crash_at=(0, 1200, 2400),
                   single_ok=False, crash_writes=False)
    try:
        capture.extract_frames("v.mp4", os.path.join(tmp, "d"), 10,
                               window_seconds=1200, runner=r)
        check("raises rather than returning an empty frame list", False)
    except capture.FrameExtractionError as exc:
        check("raises FrameExtractionError once nothing at all was sampled",
              True)
        check("the code names the crash, so the job records something usable",
              exc.code == "ffmpeg_crashed")
        check("the remedy tells the operator what to actually do",
              "re-download" in exc.remedy)

    print("an unknown duration degrades to one guarded pass:")
    r = FakeFfmpeg(duration=None)
    frames = capture.extract_frames("v.mp4", os.path.join(tmp, "e"), 10,
                                    window_seconds=1200, runner=r)
    check("it still samples rather than refusing", len(frames) > 0)
    check("it made exactly one pass (there is no end to chunk against)",
          len(r.filter_passes) == 1)

    print("ffmpeg progress becomes a readable line with an ETA:")
    p = vi.FfmpegProgress(3600, every=0.0)
    swallowed = [p("frame=100"), p("out_time_us=900000000"), p("speed=4.0x")]
    check("machine progress keys are swallowed, never printed",
          swallowed == [None, None, None])
    line = p("progress=continue")
    check("one summary line carries position, percent, speed and ETA",
          line and "0:15:00" in line and "25%" in line and "4.0x" in line
          and "eta" in line)
    quiet = vi.FfmpegProgress(3600, every=999)
    quiet("out_time_us=1000000")
    first = quiet("progress=continue")
    quiet("out_time_us=2000000")
    check("the first block reports immediately, then summaries are "
          "rate-limited rather than one per block",
          first and quiet("progress=continue") is None)
    check("a real ffmpeg error is passed through untouched",
          quiet("[libx264 @ 0x1] height not divisible by 2")
          == "[libx264 @ 0x1] height not divisible by 2")
    check("the end of the transcode is announced",
          "done" in (vi.FfmpegProgress(60, every=0.0)("progress=end") or ""))

    print("the scan proxy asks for progress and runs under a stall guard:")
    seen = {}

    class ProxyRunner:
        """No .Popen, so _run_live takes its captured one-shot path."""
        def run(self, cmd, **kw):
            if cmd[0] == "ffprobe":
                if "codec_name" in " ".join(cmd):
                    return Res(0, '{"streams":[{"width":640,"height":360,'
                                  '"codec_name":"h264"}],'
                                  '"format":{"duration":"3000.0"}}')
                return Res(0, "video\n")
            seen["cmd"] = list(cmd)
            with open(cmd[-1], "wb") as f:
                f.write(b"0" * 8192)
            return Res(0)

    src = os.path.join(tmp, "src.mp4")
    with open(src, "wb") as f:
        f.write(b"0" * 65536)
    out = os.path.join(tmp, "proxy.mp4")
    vi.make_scan_proxy(src, out, height=360, runner=ProxyRunner())
    check("the transcode is asked to report progress",
          "-progress" in seen["cmd"] and "pipe:1" in seen["cmd"])
    check("stats are off so ONLY the parsed progress stream is emitted",
          "-nostats" in seen["cmd"])
    check("a default stall guard exists (a silent transcode can't hang "
          "forever)", vi.PROXY_STALL_TIMEOUT and vi.PROXY_STALL_TIMEOUT > 0)

    print()
    print("ALL PASS" if not _fails else f"{_fails} FAILURES")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
