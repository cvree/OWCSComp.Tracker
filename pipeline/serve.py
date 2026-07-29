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
  GET  /api/intake          LIVE intake report (same shape as
                            assets/data/intake.v1.json, straight from the
                            automation DB — never 500s)
  GET  /api/download-status yt-dlp/ffmpeg/JS-runtime/ejs/curl_cffi status,
                            resolved download-auth config (never values),
                            the fallback ladder, per-layout detection
                            readiness, API-key presence
  POST /api/run             start run_owcs_auto with validated params
  POST /api/intake/link     {url, autoAccept?} validate the pasted URL
                            offline, then launch `cli.py convert-link` (the
                            one match-day command) as the current job
  GET  /api/matchfinder     LIVE auto-match-finder report: every discovered
                            OWCS broadcast + its intake job state (same
                            shape as assets/data/matchfinder.v1.json)
  POST /api/action          {action, job, ...} run ONE allowlisted pipeline
                            action (retry / autopilot / approve-source /
                            approve-layout / accept-proposed / detect /
                            publish / media-probe / find-matches /
                            export-public). Audited approvals additionally
                            require a typed name.
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
# An intake conversion may legitimately download a multi-hour VOD — give it
# its own, much longer leash instead of raising the default for everything.
INTAKE_TIMEOUT = 4 * 60 * 60
HEARTBEAT_EVERY = 10.0       # seconds of silence before a heartbeat line

# Automation job DB override for tests; None = automation.job_store.DEFAULT_DB.
AUTOMATION_DB: str | None = None

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


def _cli(*args: str) -> list[str]:
    """argv for this repo's automation CLI, honouring the test DB override."""
    cmd = [sys.executable, os.path.join("pipeline", "automation", "cli.py")]
    if AUTOMATION_DB:
        cmd += ["--db", AUTOMATION_DB]
    return cmd + list(args)


# Every action the control room may launch, as a table rather than a chain
# of `if`s: name -> (argv builder, human label, job kind, timeout).
# A browser can therefore only ever start one of THESE, with arguments this
# module validated — never an arbitrary command line.
_JOB_KEY_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,120}$")
_NAME_RE = re.compile(r"^[\w .,'\-]{1,60}$")


def _job_key(p: dict) -> str | None:
    key = str(p.get("job") or "").strip()
    return key if key and _JOB_KEY_RE.match(key) else None


def build_action_cmd(action: str, p: dict
                     ) -> tuple[list[str] | None, str | None, str, float | None]:
    """Validate a control-room action -> (argv, error, label, timeout).

    `error` is None on success; the caller must not treat the label as an
    error (an earlier revision did exactly that and 400'd every action).

    Human gates are deliberately INCLUDED here (approve-source,
    approve-layout, accept-proposed, detect --write, publish): the point is
    that a person clicks them, in a local browser, on their own machine —
    which is the same audited human decision as typing the command, and it
    is recorded with their name either way. What is NOT here is anything
    that would let the page approve on its own: every one of these requires
    an explicit click plus, for the irreversible ones, a typed name.
    """
    job = _job_key(p)
    needs_job = {"retry", "autopilot", "approve-source", "reject-source",
                 "approve-layout", "resolve-layout", "propose-identity",
                 "detect", "detect-write", "publish-dry", "publish",
                 "media-probe", "show"}
    if action in needs_job and not job:
        return None, "a valid job key is required", "", None
    who = str(p.get("who") or "").strip()
    if action in ("approve-source", "reject-source", "approve-layout",
                  "publish") and not _NAME_RE.match(who or ""):
        return None, ("your name is required for an audited approval "
                      "(letters, spaces, . , ' - only)"), "", None

    if action == "retry":
        # retry-job restores the recorded resume stage; --force is the
        # explicit dead-letter override and must be asked for.
        args = ["retry-job", job] + (["--force"] if p.get("force") else [])
        return _cli(*args), None, f"retry {job}", JOB_TIMEOUT
    if action == "autopilot":
        args = ["autopilot", "--job", job]
        if p.get("autoAccept"):
            args += ["--auto-accept", "--accepted-by",
                     who or "control-room"]
        if p.get("forHarvest"):
            args += ["--for-harvest"]
        return _cli(*args), None, f"autopilot {job}", INTAKE_TIMEOUT
    if action == "approve-source":
        return _cli("approve-source", "--job", job, "--approved-by", who,
                    "--confirm",
                    *(["--reason", str(p["reason"])[:200]]
                      if p.get("reason") else [])), None, \
            f"approve source {job}", JOB_TIMEOUT
    if action == "reject-source":
        return _cli("approve-source", "--job", job, "--approved-by", who,
                    "--confirm", "--reject",
                    *(["--reason", str(p["reason"])[:200]]
                      if p.get("reason") else [])), None, \
            f"reject source {job}", JOB_TIMEOUT
    if action == "resolve-layout":
        return _cli("resolve-layout", "--job", job), None, \
            f"resolve layout {job}", INTAKE_TIMEOUT
    if action == "approve-layout":
        return _cli("approve-layout", "--job", job, "--approved-by", who,
                    "--confirm"), None, f"approve layout {job}", JOB_TIMEOUT
    if action == "propose-identity":
        return _cli("propose-identity", "--job", job), None, \
            f"propose identity {job}", INTAKE_TIMEOUT
    if action == "accept-proposed":
        try:
            seg = int(p.get("segment"))
        except (TypeError, ValueError):
            return None, "a numeric segment id is required", "", None
        return _cli("accept-proposed", "--segment", str(seg),
                    *(["--note", str(p["note"])[:200]]
                      if p.get("note") else [])), None, \
            f"accept segment {seg}", JOB_TIMEOUT
    if action == "detect":
        return _cli("detect-job", job), None, f"detect (dry run) {job}", INTAKE_TIMEOUT
    if action == "detect-write":
        # The write pass is refused by detection_runner unless the job is
        # APPROVED — the gate stays exactly where it was.
        return _cli("detect-job", job, "--write"), None, \
            f"commit detection {job}", INTAKE_TIMEOUT
    if action == "publish-dry":
        return _cli("process-approved-job", "--job", job), None, \
            f"publish dry-run {job}", INTAKE_TIMEOUT
    if action == "publish":
        return _cli("process-approved-job", "--job", job, "--publish"), None, \
            f"PUBLISH {job}", INTAKE_TIMEOUT
    if action == "media-probe":
        return _cli("media-probe", "--job", job, "--json"), None, \
            f"media probe {job}", JOB_TIMEOUT
    if action == "find-matches":
        # The auto match finder: scan verified channels on the free
        # sources, refresh the ledger + static snapshot. Read-mostly (never
        # downloads video, never approves anything), so no name required.
        return _cli("find-matches"), None, "scan for OWCS matches", JOB_TIMEOUT
    if action == "export-public":
        return ([sys.executable, os.path.join("pipeline", "export_data.py"),
                 "--public"], None, "export public data", JOB_TIMEOUT)
    if action == "intake-export":
        return _cli("intake-export", "--save"), None, \
            "refresh intake panel", JOB_TIMEOUT
    return None, f"unknown action: {action}", "", None


def build_intake_cmd(p: dict) -> tuple[list[str] | None, str | None, dict | None]:
    """Validate browser params -> exact `cli.py convert-link` argv (no shell).

    The URL is canonicalized OFFLINE by the same parser intake itself uses
    (link_intake.parse_link), so a bad paste is refused here with its stable
    code and nothing launches; the video id / deterministic job key are known
    before the job even starts and are returned to the page immediately."""
    from automation import link_intake as ali
    url = p.get("url")
    if not isinstance(url, str) or not url.strip():
        return None, "url is required", None
    try:
        parsed = ali.parse_link(url)
    except ali.LinkIntakeError as e:
        return None, f"[{e.code}] {e}", None
    cmd = [sys.executable, os.path.join("pipeline", "automation", "cli.py")]
    if AUTOMATION_DB:
        cmd += ["--db", AUTOMATION_DB]
    cmd += ["convert-link", "--url", parsed["canonicalUrl"],
            "--requested-by", "control-room"]
    if p.get("autoAccept"):
        cmd += ["--auto-accept", "--accepted-by", "control-room"]
    info = {"videoId": parsed["videoId"],
            "jobKey": ali.job_key_for(parsed["videoId"]),
            "canonicalUrl": parsed["canonicalUrl"]}
    return cmd, None, info


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
        if path == "/api/intake":
            # LIVE intake report — same builder the CLI's intake-export
            # uses, straight from the automation DB, so the page can never
            # disagree with the command line. Read-only, and a status read
            # must never 500.
            try:
                from automation import job_store as ajs
                from automation import link_intake as ali
                store = ajs.JobStore(AUTOMATION_DB or ajs.DEFAULT_DB)
                try:
                    return self._json(200, ali.build_intake_report(store))
                finally:
                    store.close()
            except Exception as e:
                return self._json(200, {
                    "schema": "intake.v1", "jobs": [],
                    "error": f"{type(e).__name__}: {e}",
                    "note": "live intake report unavailable — the static "
                            "assets/data/intake.v1.json snapshot still works"})
        if path == "/api/matchfinder":
            # LIVE match-finder report: the ledger of every discovered
            # OWCS broadcast joined with each one's intake job state.
            # Read-only; a status read must never 500 (build_report catches
            # everything and reports errors as data).
            try:
                from automation import match_finder as mf
                return self._json(200, mf.build_report(AUTOMATION_DB))
            except Exception as e:
                return self._json(200, {
                    "schema": "matchfinder.v1", "candidates": [],
                    "channels": [], "sourceErrors":
                    [f"{type(e).__name__}: {e}"],
                    "summary": {"total": 0, "likely": 0, "tracked": 0}})
        if path == "/api/download-status":
            # The download-authentication panel's data source. Everything
            # here is non-secret BY CONSTRUCTION: ytdlp_opts.describe()
            # reports whether a cookie source/profile is configured and
            # never its value, and API keys are reported as present/absent
            # only. A status read must never 500.
            try:
                sys.path.insert(0, PIPE_DIR)
                import ytdlp_opts as yo
                import detection_assets as da
                auth = yo.load_auth_config()
                deps = yo.dependency_report()
                payload = {
                    "ok": deps["ok"],
                    "dependencies": deps["entries"],
                    "requiredMissing": deps["requiredMissing"],
                    "optionalMissing": deps["optionalMissing"],
                    "auth": auth.describe(),
                    "ladder": [r.describe() for r in yo.build_ladder(auth)],
                    "detectionAssets": da.audit_all_layouts(),
                    "apiKeys": {
                        # presence only — the value is never read into the
                        # response, so it cannot reach the browser
                        "YOUTUBE_API_KEY": bool(os.environ.get("YOUTUBE_API_KEY")),
                        "FACEIT_API_KEY": bool(os.environ.get("FACEIT_API_KEY")),
                    },
                }
                return self._json(200, payload)
            except Exception as e:
                return self._json(200, {
                    "ok": False, "error": f"{type(e).__name__}: {e}",
                    "dependencies": [], "auth": {}, "ladder": [],
                    "detectionAssets": [], "apiKeys": {}})
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
        if self.path == "/api/intake/link":
            p = self._read_body()
            if p is None:
                return self._json(400, {"error": "bad JSON body"})
            cmd, err, info = build_intake_cmd(p)
            if err:
                return self._json(400, {"error": err})
            ok, err = launch([cmd], "intake", info["videoId"],
                             timeout=INTAKE_TIMEOUT)
            with LOCK:
                job = STATE["job"]
            return self._json(200 if ok else 409,
                              {"started": ok, "error": err, "job": job,
                               "cmd": " ".join(cmd), **info})
        if self.path == "/api/action":
            p = self._read_body()
            if p is None:
                return self._json(400, {"error": "bad JSON body"})
            action = str(p.get("action") or "")
            cmd, err, label, timeout = build_action_cmd(action, p)
            if err:
                return self._json(400, {"error": err})
            ok, err = launch([cmd], action, label, timeout=timeout)
            with LOCK:
                job = STATE["job"]
            return self._json(200 if ok else 409,
                              {"started": ok, "error": err, "job": job,
                               "action": action,
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
