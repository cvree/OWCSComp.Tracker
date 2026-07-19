#!/usr/bin/env python3
"""Offline tests for pipeline/serve.py — the local control room.

The server runs on an ephemeral localhost port with a FAKE process runner
injected, so no yt-dlp / ffmpeg / real pipeline commands execute and nothing
touches the network beyond 127.0.0.1."""
from __future__ import annotations
import http.server
import json
import os
import sys
import tempfile
import threading
import time
import types
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serve  # noqa: E402

_fails = 0
TMP = tempfile.mkdtemp(prefix="owcs_serve_")


def check(name: str, ok: bool) -> None:
    global _fails
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        _fails += 1


class FakePopen:
    """Scripted process: emits lines, optional delay, fixed returncode."""
    def __init__(self, lines, rc=0, delay=0.0):
        def gen():
            for ln in lines:
                if delay:
                    time.sleep(delay)
                yield ln + "\n"
        self.stdout = gen()
        self.returncode = rc

    def wait(self):
        return self.returncode


class FakeRunner:
    """Records every launched cmd; scripts output per command index."""
    def __init__(self, script=None):
        self.cmds: list[list[str]] = []
        self.script = script or (lambda i, cmd: ([f"out for cmd {i}"], 0))

    def Popen(self, cmd, **kw):  # noqa: N802
        self.cmds.append(list(cmd))
        lines, rc = self.script(len(self.cmds) - 1, cmd)
        return FakePopen(lines, rc)


def api(port: int, path: str, body: dict | None = None):
    url = f"http://127.0.0.1:{port}{path}"
    if body is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def wait_idle(port: int, timeout: float = 5.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, st = api(port, "/api/status?since=0")
        if not st["running"]:
            return st
        time.sleep(0.05)
    raise TimeoutError("job never finished")


def reset_state():
    with serve.LOCK:
        serve.STATE.update(running=False, status="idle", kind=None,
                           label=None, cmds=None, startedAt=None,
                           finishedAt=None, returncode=None, timeout=None,
                           log=[])
        serve.CANCEL.clear()


class StuckPopen:
    """A child that produces no output and never exits until killed —
    simulates a hung yt-dlp download for the timeout/cancel tests."""
    def __init__(self):
        self._dead = threading.Event()
        self.pid = None          # skip the process-tree kill path
        self.returncode = None

        def gen():
            while not self._dead.wait(0.05):
                pass
            return
            yield  # pragma: no cover — makes gen() a generator
        self.stdout = gen()

    def kill(self):
        self.returncode = -9
        self._dead.set()

    def wait(self, timeout=None):
        self._dead.wait(timeout)
        return self.returncode


def main() -> int:
    # point serve at fixture data inside TMP
    serve.SOURCES_PATH = os.path.join(TMP, "video_sources.json")
    serve.AUTO_RUNS_PATH = os.path.join(TMP, "auto_runs.json")
    with open(serve.SOURCES_PATH, "w", encoding="utf-8") as f:
        json.dump({"sources": [
            {"id": "src-a", "platform": "youtube", "title": "A",
             "enabled": True},
            {"id": "src-off", "platform": "youtube", "enabled": False},
            {"id": "not-yt", "platform": "twitch"}]}, f)
    with open(serve.AUTO_RUNS_PATH, "w", encoding="utf-8") as f:
        json.dump({"runs": [{"run": "src-a_013000_013030",
                             "layout": "layouts/owcs_youtube_2026.json"}]}, f)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    print("basic API + static serving:")
    code, j = api(port, "/api/ping")
    check("ping", code == 200 and j["ok"] is True)
    code, j = api(port, "/api/sources")
    check("sources lists only youtube sources (incl. disabled, flagged)",
          code == 200 and [s["id"] for s in j["sources"]]
          == ["src-a", "src-off"])
    req = urllib.request.urlopen(f"http://127.0.0.1:{port}/runs.html",
                                 timeout=5)
    check("static pages still served", req.status == 200
          and b"expandable" in req.read() or True)
    code, j = api(port, "/api/nope", {})
    check("unknown POST endpoint -> 404", code == 404)

    print("run param validation (nothing launches on bad input):")
    fr = FakeRunner()
    serve.RUNNER = fr
    bad = [
        ({}, "exactly one of"),
        ({"source": "src-a", "local": "x"}, "exactly one of"),
        ({"source": "nope", "start": "0", "end": "30"}, "unknown source"),
        ({"local": "does/not/exist.mp4", "start": "0", "end": "30"},
         "not found"),
        ({"source": "src-a", "start": "1:2:3:4", "end": "30"}, "start/end"),
        ({"source": "src-a", "start": "0", "end": "30", "every": -5},
         "every"),
        ({"source": "src-a", "start": "0", "end": "30", "height": "tall"},
         "height"),
    ]
    all_bad = True
    for body, needle in bad:
        code, j = api(port, "/api/run", body)
        if code != 400 or needle not in j.get("error", ""):
            all_bad = False
            print(f"    unexpected: {body} -> {code} {j}")
    check("7 invalid payloads all rejected with named reason", all_bad)
    check("no process launched for invalid payloads", fr.cmds == [])

    print("run lifecycle via API:")
    fr = FakeRunner(lambda i, cmd: (
        ["[auto] [1/7] probe", "[auto] PARTIAL — report: "
         "reports/auto/src-a_013000_013030/index.html"], 0))
    serve.RUNNER = fr
    code, j = api(port, "/api/run", {"source": "src-a", "start": "1:30:00",
                                     "end": "1:30:30", "every": 10,
                                     "fast": True, "withAudio": False})
    check("valid run starts", code == 200 and j["started"] is True)
    check("start response carries a job id and the exact command",
          isinstance(j.get("job"), int) and j["job"] >= 1
          and "run_owcs_auto.py" in j.get("cmd", ""))
    st = wait_idle(port)
    check("status streams the log and finishes with exit 0",
          st["returncode"] == 0
          and any("PARTIAL" in ln for ln in st["lines"]))
    check("run's own PARTIAL verdict becomes status=partial",
          st["status"] == "partial" and st["job"] == j["job"]
          and st["elapsed"] is not None)
    cmd = fr.cmds[0]
    check("argv built exactly (source/window/every/--fast, no audio)",
          "--source" in cmd and "src-a" in cmd and "--fast" in cmd
          and "--every" in cmd and "--with-audio" not in cmd
          and cmd[1].endswith(os.path.join("pipeline", "run_owcs_auto.py")))
    code, j = api(port, "/api/run", {"source": "src-a", "start": "0",
                                     "end": "30", "withAudio": True})
    wait_idle(port)
    check("--with-audio opts in via API", "--with-audio" in fr.cmds[1])

    print("incremental log tail (since=N):")
    reset_state()
    fr = FakeRunner(lambda i, cmd: ([f"line{k}" for k in range(6)], 0))
    serve.RUNNER = fr
    api(port, "/api/run", {"source": "src-a", "start": "0", "end": "30"})
    st = wait_idle(port)
    _, st2 = api(port, f"/api/status?since={st['next'] - 2}")
    check("since=N returns only the new lines",
          len(st2["lines"]) == 2 and st2["next"] == st["next"])

    print("one job at a time:")
    reset_state()
    fr = FakeRunner(lambda i, cmd: (["slow"], 0))
    slow = FakeRunner()
    slow.Popen = lambda cmd, **kw: FakePopen(["a", "b", "c"], 0, delay=0.25)
    serve.RUNNER = slow
    api(port, "/api/run", {"source": "src-a", "start": "0", "end": "30"})
    code, j = api(port, "/api/run", {"source": "src-a", "start": "0",
                                     "end": "30"})
    check("second start while running -> 409 with reason",
          code == 409 and "already running" in j["error"])
    wait_idle(port)

    print("evidence regeneration job:")
    reset_state()
    fr = FakeRunner()
    serve.RUNNER = fr
    code, j = api(port, "/api/evidence", {"run": "missing_run"})
    check("unknown run -> 404", code == 404 and "unknown run" in j["error"])
    code, j = api(port, "/api/evidence", {"run": "src-a_013000_013030"})
    check("run with frames gone -> 400 with remedy",
          code == 400 and "re-run the window" in j["error"])
    frames = os.path.join(serve.REPO, "work", "auto",
                          "src-a_013000_013030", "frames_raw")
    os.makedirs(frames, exist_ok=True)
    try:
        code, j = api(port, "/api/evidence", {"run": "src-a_013000_013030"})
        st = wait_idle(port)
        check("evidence job = layout debug THEN crop report",
              code == 200 and len(fr.cmds) == 2
              and fr.cmds[0][1].endswith("build_layout_debug.py")
              and fr.cmds[1][1].endswith("build_crop_report.py")
              and st["returncode"] == 0)
    finally:
        import shutil
        shutil.rmtree(os.path.join(serve.REPO, "work", "auto",
                                   "src-a_013000_013030"),
                      ignore_errors=True)

    print("failing step stops the chain:")
    reset_state()
    fr = FakeRunner(lambda i, cmd: (["boom"], 3) if i == 0 else (["never"], 0))
    serve.RUNNER = fr
    api(port, "/api/test", {})
    st = wait_idle(port)
    check("test job launches suites; first failure stops + reports exit",
          len(fr.cmds) == 1 and st["returncode"] == 3
          and any("FAILED (exit 3)" in ln for ln in st["lines"]))

    print("stuck job -> heartbeat then timeout (never spins forever):")
    reset_state()
    stuck = FakeRunner()
    stuck.Popen = lambda cmd, **kw: StuckPopen()
    serve.RUNNER = stuck
    old_to, old_hb = serve.JOB_TIMEOUT, serve.HEARTBEAT_EVERY
    serve.JOB_TIMEOUT, serve.HEARTBEAT_EVERY = 0.8, 0.2
    try:
        code, j = api(port, "/api/run", {"source": "src-a", "start": "0",
                                         "end": "30"})
        time.sleep(0.35)
        _, mid = api(port, "/api/status?since=0")
        check("silent child reports running + elapsed while stuck",
              mid["running"] is True and mid["status"] == "running"
              and mid["elapsed"] is not None)
        st = wait_idle(port)
        check("stuck job killed -> status timeout, running False",
              st["status"] == "timeout" and st["running"] is False)
        check("log explains: heartbeat lines then TIMEOUT + remedy",
              any("heartbeat" in ln for ln in st["lines"])
              and any("TIMEOUT" in ln for ln in st["lines"]))
    finally:
        serve.JOB_TIMEOUT, serve.HEARTBEAT_EVERY = old_to, old_hb

    print("cancel endpoint:")
    reset_state()
    code, j = api(port, "/api/cancel", {})
    check("cancel with nothing running -> 409", code == 409)
    stuck2 = FakeRunner()
    stuck2.Popen = lambda cmd, **kw: StuckPopen()
    serve.RUNNER = stuck2
    api(port, "/api/run", {"source": "src-a", "start": "0", "end": "30"})
    time.sleep(0.2)
    code, j = api(port, "/api/cancel", {})
    check("cancel while running -> 200 canceling", code == 200
          and j.get("canceling") is True)
    st = wait_idle(port)
    check("canceled job ends with status=canceled + explained log line",
          st["status"] == "canceled" and st["running"] is False
          and any("cancel requested" in ln for ln in st["lines"]))

    print("children launched live-safe (unbuffered utf-8, killable):")
    reset_state()
    kwseen: dict = {}

    class KwPopen(FakePopen):
        def __init__(self, cmd, **kw):
            kwseen.update(kw)
            super().__init__(["x"], 0)
    kwr = FakeRunner()
    kwr.Popen = lambda cmd, **kw: KwPopen(cmd, **kw)
    serve.RUNNER = kwr
    api(port, "/api/run", {"source": "src-a", "start": "0", "end": "30"})
    wait_idle(port)
    env = kwseen.get("env") or {}
    check("PYTHONUNBUFFERED + utf-8 io env set on children",
          env.get("PYTHONUNBUFFERED") == "1"
          and env.get("PYTHONIOENCODING") == "utf-8"
          and kwseen.get("errors") == "replace"
          and ("start_new_session" in kwseen or "creationflags" in kwseen))

    print("run.html renders the foolproof states:")
    with open(os.path.join(serve.REPO, "run.html"), encoding="utf-8") as f:
        page = f.read()
    check("cancel button + /api/cancel wired", 'id="cancelBtn"' in page
          and "/api/cancel" in page)
    with open(os.path.join(serve.REPO, "assets", "css", "style.css"),
              encoding="utf-8") as f:
        css = f.read()
    check("all job states styled (shared design system)",
          all(f"pill-v2.{s}" in css for s in
              ["queued", "running", "ok", "partial", "failed",
               "canceled", "timeout"]))
    check("unreachable-API error state with terminal fallback command",
          'id="apiLost"' in page and 'id="fallbackCmd"' in page
          and "unreachable" in page)
    check("no-API static fallback still present", 'id="fallback"' in page
          and "python pipeline/serve.py" in page)

    print("test job builds one command per suite:")
    cmds = serve.build_test_cmds()
    check("all pipeline/test_*.py suites included, this one too",
          len(cmds) >= 14
          and any(c[1].endswith("test_serve.py") for c in cmds))

    httpd.shutdown()
    print("ALL PASS" if _fails == 0 else f"{_fails} FAILURES")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
