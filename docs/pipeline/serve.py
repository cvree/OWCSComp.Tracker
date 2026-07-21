#!/usr/bin/env python3
"""
serve.py — the local control room: the static website PLUS a local-only API
so runs, evidence rebuilds, and the test suite can be started and watched
from the browser instead of the terminal.

  python pipeline/serve.py            ->  http://localhost:8000/run.html

This replaces `python -m http.server 8000`. It is NOT a hosted backend:
stdlib only, binds 127.0.0.1 by default, executes only this repo's own
pipeline scripts with the same Python, one job at a time. The public/static
site remains fully functional without it (pages fall back to showing the
copy-pasteable commands when the API is absent).

API (all JSON):
  GET  /api/ping            {ok, running}
  GET  /api/sources         saved youtube sources for the run form
  GET  /api/status?since=N  job state + log lines from index N (live tail)
  POST /api/run             start run_owcs_auto with validated params
  POST /api/evidence        {run} re-run layout.html + crops.html for a run
                            from its already-extracted frames (no download)
  POST /api/test            run every pipeline/test_*.py suite in order
  POST /api/cancel          cancel the current job (kills its yt-dlp/ffmpeg
                            children too); 409 when nothing is running
Only one job runs at a time; a second start returns 409. Every job ends in
one of: ok / partial / failed / canceled / timeout — the UI can never spin
forever. Silent children produce "[serve] heartbeat" lines; a job past
--timeout (default 30 min) is killed and marked timeout.
"""
from __future__ import annotations
import argparse
import glob as globmod
import http.server
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time

PIPE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PIPE_DIR)
import db  # noqa: E402

REPO = db.REPO_ROOT
AUTO_RUNS_PATH = os.path.join(REPO, "data", "auto_runs.json")
SOURCES_PATH = os.path.join(REPO, "data", "sources", "video_sources.json")
RUNNER = subprocess          # injectable for offline tests
MAX_LOG = 4000
_TIME_RE = re.compile(r"^\d{1,2}(:\d{1,2}){0,2}$")
_REPORT_RE = re.compile(r"\b(OK|PARTIAL) — report:")
# hero-crop review API (JSON sidecar edits — never launches a job, never
# writes comps). run/crop ids are restricted so they can't escape the tree.
_HC_LIST_RE = re.compile(r"^/api/runs/([A-Za-z0-9_.\-]+)/hero-crops/?$")
_HC_ACT_RE = re.compile(
    r"^/api/runs/([A-Za-z0-9_.\-]+)/hero-crops/([A-Za-z0-9_\-]+)/(label|reject)$")

JOB_TIMEOUT = 30 * 60        # wall-clock seconds before a job is killed
HEARTBEAT_EVERY = 10.0       # seconds of silence before a heartbeat line

# status: idle | running | ok | partial | failed | canceled | timeout
STATE: dict = {"running": False, "status": "idle", "job": 0, "kind": None,
               "label": None, "cmds": None, "startedAt": None,
               "finishedAt": None, "returncode": None, "timeout": None,
               "log": []}
LOCK = threading.Lock()
CANCEL = threading.Event()
_PROC: list = [None]         # current child process (for cancel/timeout kill)


def log(msg: str) -> None:
    print(f"[serve] {msg}", flush=True)


def _append_log(line: str) -> None:
    with LOCK:
        STATE["log"].append(line)
        if len(STATE["log"]) > MAX_LOG:
            STATE["log"] = STATE["log"][-(MAX_LOG // 2):]


def _spawn(runner, cmd):
    """Start a child so its output arrives live and never crashes on
    encoding: PYTHONUNBUFFERED (Python children flush every line),
    PYTHONIOENCODING=utf-8 (Windows pipes default to cp1252 — a '→' in a
    log line would otherwise raise UnicodeEncodeError inside the child),
    and utf-8/replace on our reading side. New process group/session so a
    cancel or timeout can kill yt-dlp/ffmpeg grandchildren too."""
    kw: dict = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    bufsize=1, cwd=REPO,
                    env={**os.environ, "PYTHONUNBUFFERED": "1",
                         "PYTHONIOENCODING": "utf-8"})
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess,
                                      "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kw["start_new_session"] = True
    return runner.Popen(cmd, **kw)


def _kill_tree(proc) -> None:
    """Best-effort kill of the child AND its yt-dlp/ffmpeg descendants."""
    pid = getattr(proc, "pid", None)
    try:
        if pid and os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True)
        elif pid:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def _final_status(outcome: str | None, rc: int) -> str:
    if outcome:                       # canceled / timeout
        return outcome
    if rc != 0:
        return "failed"
    with LOCK:                        # run's own last line says OK/PARTIAL
        for ln in reversed(STATE["log"][-50:]):
            m = _REPORT_RE.search(ln)
            if m:
                return "partial" if m.group(1) == "PARTIAL" else "ok"
    return "ok"


def launch(cmds: list[list[str]], kind: str, label: str,
           runner=None, timeout: float | None = None
           ) -> tuple[bool, str | None]:
    """Run one job (a sequence of commands) in a worker thread.

    Streams every output line into STATE["log"], emits heartbeat lines when
    the child is silent, enforces a wall-clock timeout, honors cancel, and
    stops at the first failing command. Refuses to start while another job
    is running. The UI can therefore never spin forever: every job ends in
    ok / partial / failed / canceled / timeout with an explained last line.
    """
    runner = runner or RUNNER
    timeout = JOB_TIMEOUT if timeout is None else timeout
    with LOCK:
        if STATE["running"]:
            return False, (f"a job is already running "
                           f"({STATE['kind']}: {STATE['label']})")
        CANCEL.clear()
        STATE.update(running=True, status="running", job=STATE["job"] + 1,
                     kind=kind, label=label,
                     cmds=[" ".join(c) for c in cmds], log=[],
                     returncode=None, startedAt=time.time(),
                     finishedAt=None, timeout=timeout)
        job_id = STATE["job"]

    def worker() -> None:
        rc, outcome, t0 = 0, None, time.monotonic()
        last_out = [t0]
        for i, cmd in enumerate(cmds, start=1):
            _append_log(f"[serve] [{i}/{len(cmds)}] $ {' '.join(cmd)}")
            try:
                proc = _spawn(runner, cmd)
                _PROC[0] = proc
                done = threading.Event()

                def _reader(p=proc, d=done) -> None:
                    try:
                        for line in p.stdout:
                            if line.rstrip():
                                _append_log(line.rstrip())
                                last_out[0] = time.monotonic()
                    except Exception as e:
                        _append_log("[serve] output reader error: "
                                    f"{type(e).__name__}: {e}")
                    finally:
                        d.set()

                threading.Thread(target=_reader, daemon=True).start()
                last_beat = t0
                while not done.wait(0.2):
                    now = time.monotonic()
                    if CANCEL.is_set():
                        _append_log("[serve] cancel requested — "
                                    "stopping job and its children...")
                        _kill_tree(proc)
                        outcome = "canceled"
                        done.wait(3)
                        break
                    if timeout and now - t0 > timeout:
                        _append_log(f"[serve] TIMEOUT — no finish after "
                                    f"{int(timeout)}s; killing job. Remedy: "
                                    "use a shorter window / --fast, check "
                                    "your network, or raise --timeout on "
                                    "serve.py.")
                        _kill_tree(proc)
                        outcome = "timeout"
                        done.wait(3)
                        break
                    if (now - last_out[0] >= HEARTBEAT_EVERY
                            and now - last_beat >= HEARTBEAT_EVERY):
                        _append_log("[serve] heartbeat — job still "
                                    f"running... elapsed {int(now - t0)}s "
                                    f"(no output for "
                                    f"{int(now - last_out[0])}s)")
                        last_beat = now
                try:
                    rc = proc.wait(timeout=5) if outcome else proc.wait()
                except Exception:
                    rc = -9
                if rc is None:
                    rc = -9
            except FileNotFoundError as e:
                _append_log(f"[serve] command not found: {e}")
                rc = 127
            except Exception as e:  # job crash must never kill the server
                _append_log(f"[serve] job crashed: {type(e).__name__}: {e}")
                rc = 1
            finally:
                _PROC[0] = None
            if outcome:
                break
            if rc != 0:
                _append_log(f"[serve] step {i} FAILED (exit {rc}) — "
                            "remaining steps skipped")
                break
        status = _final_status(outcome, rc)
        _append_log(f"[serve] job finished — {status.upper()} (exit {rc})")
        with LOCK:
            STATE.update(running=False, status=status, returncode=rc,
                         finishedAt=time.time())

    threading.Thread(target=worker, daemon=True).start()
    del job_id  # job id is read from STATE by the endpoints
    return True, None


# ------------------------------------------------------------ job builders
def _py(script: str, *args: str) -> list[str]:
    return [sys.executable, os.path.join("pipeline", script), *args]


def load_sources() -> list[dict]:
    try:
        with open(SOURCES_PATH, "r", encoding="utf-8") as f:
            srcs = json.load(f).get("sources", [])
    except (OSError, ValueError):
        return []
    return [{"id": s.get("id"), "title": s.get("title") or s.get("id"),
             "enabled": s.get("enabled", True)}
            for s in srcs if s.get("platform") == "youtube"]


def _valid_time(v) -> bool:
    return isinstance(v, str) and bool(_TIME_RE.match(v.strip()))


def build_run_cmd(p: dict) -> tuple[list[str] | None, str | None]:
    """Validate browser params -> exact run_owcs_auto argv (no shell)."""
    source, local = p.get("source"), p.get("local")
    if bool(source) == bool(local):
        return None, "provide exactly one of source / local"
    if source and source not in {s["id"] for s in load_sources()}:
        return None, f"unknown source id: {source}"
    if local:
        lp = local if os.path.isabs(local) else os.path.join(REPO, local)
        if not os.path.isfile(lp):
            return None, f"local file not found: {local}"
    if not _valid_time(p.get("start")) or not _valid_time(p.get("end")):
        return None, "start/end must be seconds or H:MM:SS"
    try:
        every = int(p.get("every", 30))
        if every <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return None, "every must be a positive integer"
    cmd = _py("run_owcs_auto.py",
              *(["--source", source] if source else ["--local", local]),
              "--start", p["start"].strip(), "--end", p["end"].strip(),
              "--every", str(every))
    if p.get("fast"):
        cmd.append("--fast")
    if p.get("force"):
        cmd.append("--force-clip")
    if p.get("withAudio"):
        cmd.append("--with-audio")
    if p.get("height"):
        try:
            cmd += ["--height", str(int(p["height"]))]
        except (TypeError, ValueError):
            return None, "height must be an integer"
    return cmd, None


def find_run(run_name: str) -> dict | None:
    try:
        with open(AUTO_RUNS_PATH, "r", encoding="utf-8") as f:
            runs = json.load(f).get("runs", [])
    except (OSError, ValueError):
        return None
    for r in runs:
        if r.get("run") == run_name:
            return r
    return None


def build_evidence_cmds(run_name: str) -> tuple[list | None, str | None]:
    """Re-generate layout.html + crops.html + report index for one run,
    from its already-extracted frames — the calibrate loop, no download."""
    rec = find_run(run_name)
    if not rec:
        return None, f"unknown run: {run_name}"
    layout = rec.get("layout")
    if not layout:
        return None, f"run {run_name} has no recorded layout"
    frames = os.path.join("work", "auto", run_name, "frames_raw")
    if not os.path.isdir(os.path.join(REPO, frames)):
        return None, (f"frames for {run_name} are gone ({frames}) — "
                      "re-run the window instead (clip is cached)")
    report = os.path.join("reports", "auto", run_name)
    return [
        _py("build_layout_debug.py", "--layout", layout,
            "--frames-dir", frames,
            "--out", os.path.join(report, "layout_debug")),
        _py("build_crop_report.py", "--layout", layout,
            "--frames-dir", frames, "--report-dir", report),
        _py("vision_dashboard.py", "--run", run_name, "--layout", layout),
    ], None


def _hero_report_dir(run: str) -> str:
    """reports/auto/<run> for the hero-crop review endpoints."""
    return os.path.join(REPO, "reports", "auto", run)


def build_test_cmds() -> list[list[str]]:
    tests = sorted(globmod.glob(os.path.join(PIPE_DIR, "test_*.py")))
    return [[sys.executable, os.path.relpath(t, REPO)] for t in tests]


# ----------------------------------------------------------------- handler
class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=REPO, **kw)

    def log_message(self, fmt, *args):  # quiet static-file noise
        first = str(args[0]) if args else ""
        if first.startswith("POST /api/"):
            log(f"{self.client_address[0]} {first}")

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict | None:
        try:
            n = min(int(self.headers.get("Content-Length", 0)), 65536)
            return json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, OSError):
            return None

    def do_GET(self):  # noqa: N802
        path, _, query = self.path.partition("?")
        if path == "/api/ping":
            with LOCK:
                return self._json(200, {"ok": True,
                                        "running": STATE["running"],
                                        "status": STATE["status"],
                                        "job": STATE["job"]})
        if path == "/api/sources":
            return self._json(200, {"sources": load_sources()})
        if path == "/api/calibration":
            # per-source calibration health for the Calibration Lab page.
            # Read-only: reports what the calibrator/harvester left on disk
            # and in the DB, never mutates anything.
            import calibration_status as cs
            try:
                return self._json(200, cs.build_status())
            except Exception as e:      # a status read must never 500
                return self._json(200, {"sources": [], "rosterSize": None,
                                        "counts": {}, "error":
                                        f"{type(e).__name__}: {e}"})
        if path == "/api/portraits":
            # provenance of the generated hero portraits (which real
            # broadcast crop each one came from), for the Lab's asset panel.
            mp = os.path.join(REPO, "assets", "img", "heroes",
                              "manifest.json")
            try:
                with open(mp, "r", encoding="utf-8") as f:
                    return self._json(200, json.load(f))
            except (OSError, ValueError):
                return self._json(200, {"heroes": {}, "size": None,
                                        "note": "no portraits generated yet "
                                        "— run pipeline/build_hero_portraits.py"})
        if path == "/api/preflight":
            # read-only readiness snapshot for the Run page panel. Never
            # mutates anything (fix_db=False) — the run itself auto-inits
            # a missing DB at its preflight step.
            import preflight as pf
            src = None
            m = re.search(r"source=([A-Za-z0-9_.\-]+)", query)
            if m:
                src = m.group(1)
            try:
                res = pf.run_checks(source=src, fix_db=False)
            except Exception as e:      # a readiness CHECK must never 500
                return self._json(200, {
                    "ok": False, "failed": ["preflight"], "warned": [],
                    "checks": [{"name": "preflight", "status": "fail",
                                "detail": f"{type(e).__name__}: {e}",
                                "remedy": "run `python pipeline/preflight.py`"
                                          " in a terminal for details"}]})
            return self._json(200, res)
        if path == "/api/latest-run":
            try:
                with open(AUTO_RUNS_PATH, "r", encoding="utf-8") as f:
                    runs = json.load(f).get("runs", [])
            except (OSError, ValueError):
                runs = []
            latest = runs[0] if runs else None
            slim = None
            if latest:
                slim = {k: latest.get(k) for k in
                        ("run", "runStatus", "reportDir", "startedAt",
                         "window", "source", "mode")}
            return self._json(200, {"latest": slim})
        if path == "/api/status":
            since = 0
            m = re.search(r"since=(\d+)", query)
            if m:
                since = int(m.group(1))
            with LOCK:
                elapsed = None
                if STATE["startedAt"]:
                    end = STATE["finishedAt"] or time.time()
                    elapsed = int(end - STATE["startedAt"])
                return self._json(200, {
                    "running": STATE["running"], "status": STATE["status"],
                    "job": STATE["job"], "kind": STATE["kind"],
                    "label": STATE["label"],
                    "returncode": STATE["returncode"],
                    "startedAt": STATE["startedAt"],
                    "finishedAt": STATE["finishedAt"],
                    "elapsed": elapsed, "timeout": STATE["timeout"],
                    "next": len(STATE["log"]),
                    "lines": STATE["log"][since:since + 500]})
        m = _HC_LIST_RE.match(path)
        if m:
            import capture_hero_crops as chc
            meta = chc.load_meta(_hero_report_dir(m.group(1)))
            if meta is None:
                return self._json(404, {"error": (
                    "no captured hero crops for this run — run "
                    "pipeline/capture_hero_crops.py --run <run> first")})
            return self._json(200, meta)
        return super().do_GET()

    def do_POST(self):  # noqa: N802
        if self.path == "/api/run":
            p = self._read_body()
            if p is None:
                return self._json(400, {"error": "bad JSON body"})
            cmd, err = build_run_cmd(p)
            if err:
                return self._json(400, {"error": err})
            ok, err = launch([cmd], "run",
                             p.get("source") or p.get("local"))
            with LOCK:
                job = STATE["job"]
            return self._json(200 if ok else 409,
                              {"started": ok, "error": err, "job": job,
                               "cmd": " ".join(cmd)})
        if self.path == "/api/cancel":
            with LOCK:
                running, job = STATE["running"], STATE["job"]
            if not running:
                return self._json(409, {"error": "no job running"})
            CANCEL.set()
            log(f"cancel requested for job {job}")
            return self._json(200, {"canceling": True, "job": job})
        if self.path == "/api/evidence":
            p = self._read_body() or {}
            cmds, err = build_evidence_cmds(str(p.get("run", "")))
            if err:
                code = 404 if "unknown run" in err else 400
                return self._json(code, {"error": err})
            ok, err = launch(cmds, "evidence", p.get("run"))
            return self._json(200 if ok else 409,
                              {"started": ok, "error": err})
        if self.path == "/api/test":
            cmds = build_test_cmds()
            if not cmds:
                return self._json(400, {"error": "no test suites found"})
            ok, err = launch(cmds, "test", f"{len(cmds)} suites")
            return self._json(200 if ok else 409,
                              {"started": ok, "error": err,
                               "suites": len(cmds)})
        m = _HC_ACT_RE.match(self.path)
        if m:
            import capture_hero_crops as chc
            run, crop_id, act = m.group(1), m.group(2), m.group(3)
            report_dir = _hero_report_dir(run)
            if act == "label":
                body = self._read_body()
                if body is None:
                    return self._json(400, {"error": "bad JSON body"})
                entry, err = chc.set_label(report_dir, crop_id,
                                           str(body.get("hero", "")))
            else:
                entry, err = chc.reject_crop(report_dir, crop_id)
            if err:
                code = 404 if "unknown crop" in err or "no captured" in err \
                    else 400
                return self._json(code, {"error": err})
            return self._json(200, entry)
        return self._json(404, {"error": "unknown endpoint"})


def main(argv=None) -> int:
    global JOB_TIMEOUT
    ap = argparse.ArgumentParser(description="OWCS local control room")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address (localhost only by default)")
    ap.add_argument("--timeout", type=int, default=JOB_TIMEOUT,
                    help="kill a job after this many seconds "
                         "(default %(default)s)")
    args = ap.parse_args(argv)
    JOB_TIMEOUT = args.timeout
    httpd = http.server.ThreadingHTTPServer((args.host, args.port), Handler)
    log(f"control room: http://{args.host}:{args.port}/run.html")
    log(f"serving {REPO} · one job at a time · Ctrl+C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
