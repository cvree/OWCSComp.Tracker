#!/usr/bin/env python3
"""
test_clip_stall.py — YouTube clip-download stall guard, fully offline.

The real bug: `yt-dlp --download-sections` prints its "Destination" banner and
then never sends another byte; the old runner heart-beated forever. These
tests drive that exact shape with a FAKE Popen (no yt-dlp, ffmpeg, or network)
and assert the new behavior:

  * a stalled process is KILLED and raises StallTimeout (heartbeats alone
    never keep it alive)
  * genuine byte progress resets the stall clock (a slow-but-moving download
    is NOT killed)
  * on a stall the downloader walks the FORMAT fallback ladder and can
    recover when a later format flows
  * --fast uses a shorter stall timeout than a normal run
  * the "No supported JavaScript runtime" warning is surfaced as advice
  * run_owcs_auto maps a stall to a clear timeout status + remedy string

Run:  python3 pipeline/test_clip_stall.py   (non-zero on failure)
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

os.environ.setdefault("OWCS_DB", os.path.join(ROOT, "work", "test_stall",
                                              "test.sqlite"))
import video_ingest as vi  # noqa: E402
import download_vod_clip as dvc  # noqa: E402
import run_owcs_auto as roa  # noqa: E402

FAILS = 0


def check(name, cond):
    global FAILS
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS += 1


# --------------------------------------------------------------------------
# Fake Popen infrastructure: emits a scripted list of (delay, line) then
# either blocks "forever" (stall) or exits. kill() unblocks the reader.
# --------------------------------------------------------------------------
class _FakeStream:
    def __init__(self, proc):
        self.proc = proc

    def __iter__(self):
        return self

    def __next__(self):
        p = self.proc
        while True:
            if p._killed:
                raise StopIteration
            if p._script:
                delay, line = p._script[0]
                if time.monotonic() - p._t0 >= delay:
                    p._script.pop(0)
                    return line
                time.sleep(0.02)
                continue
            # script exhausted
            if p._stall:
                # block until killed (the hang we are testing)
                if p._killed:
                    raise StopIteration
                time.sleep(0.02)
                continue
            raise StopIteration


class FakePopen:
    """Scripted fake of subprocess.Popen for _run_live.

    script: list of (seconds_after_start, output_line).
    stall:  if True, after the script the stdout iterator blocks until kill().
    rc:     returncode reported once finished (ignored while stalling).
    """

    def __init__(self, script, stall=False, rc=0):
        self._script = list(script)
        self._stall = stall
        self._rc = rc
        self._killed = False
        self._t0 = time.monotonic()
        self.pid = 4242
        self.returncode = None
        self.stdout = _FakeStream(self)

    def wait(self, timeout=None):
        self.returncode = self._rc if not self._killed else -9
        return self.returncode

    def kill(self):
        self._killed = True
        self.returncode = -9

    def poll(self):
        return self.returncode


class FakeRunner:
    """A runner object exposing .Popen so _run_live takes the live path."""

    def __init__(self, popen_factory):
        self._factory = popen_factory
        self.calls = 0

    def Popen(self, cmd, **kw):
        self.calls += 1
        return self._factory(cmd, self.calls)


def main():
    # avoid killing anything real: patch the tree-killer to just mark killed
    killed = {"n": 0}
    orig_kill = vi._kill_proc_tree

    def fake_kill(proc):
        killed["n"] += 1
        try:
            proc.kill()
        except Exception:
            pass

    vi._kill_proc_tree = fake_kill

    # ---- 1. a stalled process is killed + raises StallTimeout ------------
    print("stall detection")
    t0 = time.monotonic()

    def stall_factory(cmd, n):
        # prints banner lines fast, then hangs forever
        return FakePopen(script=[(0.0, "[download] Destination: clip.mp4")],
                         stall=True)

    runner = FakeRunner(stall_factory)
    raised = False
    try:
        vi._run_live(["yt-dlp"], "[yt-dlp]", runner=runner,
                     heartbeat_every=0.3, stall_timeout=0.6)
    except vi.StallTimeout as e:
        raised = True
        waited = e.waited
    elapsed = time.monotonic() - t0
    check("stalled run raises StallTimeout", raised)
    check("process was killed on stall", killed["n"] >= 1)
    check("killed promptly (well under the old forever)", elapsed < 5)

    # ---- 2. heartbeats do NOT count as progress --------------------------
    print("heartbeats are not progress")
    check("our own heartbeat line is not progress",
          not vi._is_progress("[yt-dlp] still downloading... elapsed 40s"))
    check("banner/metadata line is not progress",
          not vi._is_progress("[download] Destination: clip.mp4"))
    check("real byte line IS progress",
          vi._is_progress("[download]  12.3% of 40.00MiB at 3.1MiB/s"))
    check("ffmpeg frame line IS progress",
          vi._is_progress("frame=  25 fps=25 q=2.0 size=10kB"))

    # ---- 3. genuine progress resets the clock (not killed) ---------------
    print("progress resets the stall clock")
    killed["n"] = 0

    def moving_factory(cmd, n):
        # a byte-progress line every 0.2s for ~1s, then clean exit — the
        # stall timeout is 0.5s but progress keeps arriving inside it.
        script = [(i * 0.2, f"[download] {i*20}.0% of 10MiB at 1MiB/s")
                  for i in range(1, 5)]
        return FakePopen(script=script, stall=False, rc=0)

    runner = FakeRunner(moving_factory)
    ok = False
    try:
        vi._run_live(["yt-dlp"], "[yt-dlp]", runner=runner,
                     heartbeat_every=0.3, stall_timeout=0.5)
        ok = True
    except vi.StallTimeout:
        ok = False
    check("moving download is not killed", ok and killed["n"] == 0)

    # ---- 4. fallback ladder is attempted on stall ------------------------
    print("format fallback after a stall")
    killed["n"] = 0
    out = os.path.join(ROOT, "work", "test_stall", "clip.mp4")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if os.path.exists(out):
        os.remove(out)

    def ladder_factory(cmd, n):
        # first format stalls; second format flows + "creates" the file
        if n == 1:
            return FakePopen([(0.0, "[download] Destination: clip.mp4")],
                             stall=True)
        # write the file as this attempt "downloads"
        with open(out, "wb") as f:
            f.write(b"\x00" * 32)
        return FakePopen([(0.0, "[download] 100% of 1MiB at 5MiB/s")],
                         stall=False, rc=0)

    runner = FakeRunner(ladder_factory)
    recovered = False
    try:
        vi._download_youtube_clip("u", 0, 20, out, height=480, runner=runner,
                                  stall_timeout=0.5)
        recovered = os.path.exists(out)
    except vi.StallTimeout:
        recovered = False
    check("first format stalled, second recovered", recovered)
    check("more than one format attempted", runner.calls >= 2)

    # ---- 5. every format stalls -> StallTimeout with fallbacks tried -----
    print("all formats stall")
    killed["n"] = 0
    if os.path.exists(out):
        os.remove(out)

    def all_stall_factory(cmd, n):
        return FakePopen([(0.0, "[download] Destination: clip.mp4"),
                          (0.0, "WARNING: No supported JavaScript runtime "
                                "found")],
                         stall=True)

    runner = FakeRunner(all_stall_factory)
    got = None
    try:
        vi._download_youtube_clip("u", 0, 20, out, height=480, runner=runner,
                                  stall_timeout=0.4,
                                  formats=["fmtA", "fmtB", "fmtC"])
    except vi.StallTimeout as e:
        got = e
    check("all-stall raises StallTimeout", got is not None)
    check("tried every fallback format", runner.calls == 3)
    check("JS-runtime warning surfaced in the error",
          got is not None and getattr(got, "js_runtime", False)
          and "javascript runtime" in str(got).lower())

    # ---- 6. JS-runtime warning detector ----------------------------------
    print("JS runtime warning detection")
    check("detects the yt-dlp phrasing",
          vi._saw_js_runtime_warning(
              "WARNING: No supported JavaScript runtime found. Some formats"))
    check("plain progress text is not a JS warning",
          not vi._saw_js_runtime_warning("[download] 5% of 10MiB"))

    # ---- 7. --fast uses a shorter stall timeout than normal --------------
    print("--fast shortens the stall timeout")
    captured = {}

    def spy_clip(url, s, e, out_, height=720, force=False, **kw):
        captured.setdefault("timeouts", []).append(kw.get("stall_timeout"))
        # pretend the clip exists so the pipeline proceeds a bit
        os.makedirs(os.path.dirname(out_), exist_ok=True)
        with open(out_, "wb") as f:
            f.write(b"\x00" * 8)
        return {"path": out_, "reused": False, "sizeBytes": 8}

    def boom_frame(clip, off, cs, out_, runner=None):
        raise RuntimeError("stop after clip for this test")

    fake_pf = lambda **kw: {"ok": True, "failed": [], "warned": [],
                            "checks": []}
    common = dict(probe_fn=lambda x: {"title": "t", "duration": 9999},
                  clip_fn=spy_clip, frame_fn=boom_frame,
                  status_fn=lambda r: None,
                  export_fn=lambda: {"out": "x"},
                  preflight_fn=fake_pf,
                  source="owcs-afcxdimpsle", start=600, end=630, every=10)
    # need a real youtube source id present; fall back to injecting url probe
    try:
        roa.run_auto(fast=True, **common)
    except SystemExit:
        # source id may not resolve in this env — retry via a stub source file
        pass
    try:
        roa.run_auto(fast=False, **common)
    except SystemExit:
        pass
    ts = captured.get("timeouts", [])
    check("clip downloader received a stall timeout", any(t for t in ts))
    if len([t for t in ts if t]) >= 2:
        fast_t, norm_t = ts[0], ts[1]
        check("fast timeout < normal timeout", fast_t < norm_t)
        check("fast timeout is the 75s default", fast_t == roa.FAST_STALL_TIMEOUT)
    else:
        # source id didn't resolve; assert the constants directly instead
        check("fast timeout constant < normal constant",
              roa.FAST_STALL_TIMEOUT < roa.DEFAULT_STALL_TIMEOUT)

    # ---- 8. run_owcs_auto: stall -> timeout status + remedy --------------
    print("run status + remedy for a stall")

    def stalling_clip(url, s, e, out_, height=720, force=False, **kw):
        raise vi.StallTimeout(["yt-dlp"], 75.0,
                              "No supported JavaScript runtime found")

    rec = roa.run_auto(fast=True, source="owcs-afcxdimpsle",
                       start=600, end=630, every=10,
                       probe_fn=lambda x: {"title": "t", "duration": 9999},
                       clip_fn=stalling_clip,
                       status_fn=lambda r: None,
                       export_fn=lambda: {"out": "x"},
                       preflight_fn=fake_pf)
    check("stall marks the run not-ok", rec.get("ok") is False)
    check("stall run status is 'timeout'",
          roa.run_status_of(rec) == "timeout")
    rem = roa.remedy_for(rec.get("error", ""))
    check("remedy mentions the stall recovery options",
          "stalled" in rem.lower()
          and ("local mp4" in rem.lower() or "--local" in rem.lower()))

    # remedy table also has a JS-runtime entry
    check("JS-runtime remedy exists",
          "runtime" in roa.remedy_for("no supported javascript runtime").lower())

    vi._kill_proc_tree = orig_kill

    print()
    if FAILS:
        print(f"FAIL — {FAILS} check(s) failed")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
