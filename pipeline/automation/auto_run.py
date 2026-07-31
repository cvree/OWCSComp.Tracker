"""
auto_run.py — one unattended pass, start to finish, on the machine running it.

The nightly loop, in the order that fails loudest first:

  1. **doctor** — `worker-doctor`. A pass that CANNOT work must say so
     instead of quietly doing nothing for six hours. If yt-dlp, ffmpeg or a
     writable cache is missing, the pass stops here with a non-zero exit and
     a log naming the missing piece. A silent no-op is the single worst
     outcome an unattended system can produce, because it looks exactly like
     "there was nothing to do".
  2. **scan** — `find-matches --queue-likely`. Discovers new broadcasts on
     the free, no-key sources and queues the likely ones through intake.
  3. **advance** — every open job through `autopilot`, with whichever gates
     the operator enabled. A job that stops at a held gate is a NORMAL
     outcome and never fails the pass.

This lives in Python rather than in the PowerShell that schedules it for two
reasons. It can be tested — the whole pass runs here against an injected
step runner, which is why the lock, the power check, the retention sweep and
the exit codes have real coverage instead of being trusted. And it runs the
same way on whatever machine ends up hosting it; the `.ps1` is a thin shim
that exists because Task Scheduler needs something to point at.

Nothing here loosens a gate. It decides only WHEN to run and WHETHER the
machine is in a fit state to run at all; every approval decision still
belongs to `gates.py`.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_DIR = os.path.dirname(_HERE)
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

REPO_ROOT = os.path.dirname(_PIPELINE_DIR)
CLI_PATH = os.path.join(_PIPELINE_DIR, "automation", "cli.py")
LOG_DIR = os.path.join(REPO_ROOT, "data", "auto-run-logs")
LOCK_NAME = ".running.lock"

# A pass that has held the lock this long is not running any more — the
# machine slept, was killed, or bluescrewed. Twelve hours is comfortably
# longer than the slowest legitimate pass and short enough that one crash
# does not disable the schedule until somebody notices.
STALE_LOCK_HOURS = 12
# Keep this many logs. An unattended system that fills its own disk with
# logs has found a novel way to stop working.
KEEP_LOGS = 30
DEFAULT_MAX_JOBS = 10

# Exit codes, so the scheduler (and a human reading Task Scheduler's
# "last result" column) can tell the three outcomes apart.
EXIT_OK = 0
EXIT_DOCTOR_FAILED = 1
EXIT_SKIPPED = 2

TERMINAL_STATES = ("PUBLISHED", "FAILED_PERMANENT", "IGNORED", "CANCELLED")


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


# ------------------------------------------------------------------ logging
class PassLog:
    """Writes a human log and collects structured events for the summary.

    Both, deliberately: the text log is what somebody reads at 9am, and the
    events are what `auto-run --last` can report without parsing prose.
    """

    def __init__(self, path: str | None = None, echo: bool = True):
        self.path = path
        self.echo = echo
        self.events: list[dict] = []
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)

    def __call__(self, message: str, level: str = "INFO") -> None:
        line = f"{_utcnow().strftime('%H:%M:%S')} [{level}] {message}"
        if self.echo:
            print(line, flush=True)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        self.events.append({"at": _utcnow().isoformat(), "level": level,
                            "message": message})


# --------------------------------------------------------------- power state
def on_battery() -> bool | None:
    """True on battery, False on AC, None when it cannot be determined.

    None is NOT treated as battery: a desktop with no battery at all must
    not skip every pass forever. Best-effort by design — the cost of getting
    this wrong is a laptop losing some charge, not a corrupted result.
    """
    # Windows: GetSystemPowerStatus. ACLineStatus 0 = offline (battery).
    if os.name == "nt":
        try:
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [("ACLineStatus", ctypes.c_byte),
                            ("BatteryFlag", ctypes.c_byte),
                            ("BatteryLifePercent", ctypes.c_byte),
                            ("SystemStatusFlag", ctypes.c_byte),
                            ("BatteryLifeTime", ctypes.c_ulong),
                            ("BatteryFullLifeTime", ctypes.c_ulong)]

            status = _Status()
            if not ctypes.windll.kernel32.GetSystemPowerStatus(
                    ctypes.pointer(status)):
                return None
            if status.ACLineStatus == 1:
                return False
            if status.ACLineStatus == 0:
                return True
            return None
        except Exception:  # noqa: BLE001 — a power check never breaks a pass
            return None
    # Linux/macOS: sysfs. Absent on most desktops, which is why None matters.
    ac_root = "/sys/class/power_supply"
    try:
        if not os.path.isdir(ac_root):
            return None
        for name in sorted(os.listdir(ac_root)):
            type_path = os.path.join(ac_root, name, "type")
            online_path = os.path.join(ac_root, name, "online")
            if not (os.path.exists(type_path) and os.path.exists(online_path)):
                continue
            with open(type_path, encoding="utf-8") as f:
                if f.read().strip() != "Mains":
                    continue
            with open(online_path, encoding="utf-8") as f:
                return f.read().strip() == "0"
    except OSError:
        return None
    return None


# ---------------------------------------------------------------- the lock
def lock_state(lock_path: str, *, now: _dt.datetime | None = None) -> dict:
    """{"held", "stale", "since", "pid"} for the pass lock.

    A live lock means the machine woke for the next pass before the last one
    finished; two concurrent passes would fight over job locks and the media
    cache, so the new one steps aside rather than racing.
    """
    if not os.path.exists(lock_path):
        return {"held": False, "stale": False, "since": None, "pid": None}
    now = now or _utcnow()
    try:
        with open(lock_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    since = data.get("since")
    age_hours = None
    if since:
        try:
            started = _dt.datetime.fromisoformat(since)
            age_hours = (now - started).total_seconds() / 3600.0
        except ValueError:
            age_hours = None
    stale = age_hours is None or age_hours >= STALE_LOCK_HOURS
    return {"held": True, "stale": stale, "since": since,
            "pid": data.get("pid"), "ageHours": age_hours}


def acquire_lock(lock_path: str) -> None:
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as f:
        json.dump({"since": _utcnow().isoformat(), "pid": os.getpid(),
                   "host": os.environ.get("COMPUTERNAME")
                           or os.environ.get("HOSTNAME")}, f)


def release_lock(lock_path: str) -> None:
    try:
        os.remove(lock_path)
    except OSError:
        pass


def prune_logs(log_dir: str, keep: int = KEEP_LOGS) -> list[str]:
    """Delete all but the newest `keep` pass logs. Returns what it removed."""
    if not os.path.isdir(log_dir):
        return []
    logs = sorted(
        (os.path.join(log_dir, n) for n in os.listdir(log_dir)
         if n.startswith("auto-run_") and n.endswith(".log")),
        key=lambda p: os.path.getmtime(p), reverse=True)
    removed = []
    for path in logs[keep:]:
        try:
            os.remove(path)
            removed.append(path)
        except OSError:
            continue
    return removed


# ------------------------------------------------------------- step runner
def default_step(cli_args: list[str], *, db: str | None = None,
                 timeout: int = 3600) -> dict:
    """Run one CLI subcommand in a child process.

    A child process rather than an in-process call on purpose: the pass must
    survive a stage that segfaults in a native CV library, which an
    in-process call would not.
    """
    cmd = [sys.executable, CLI_PATH]
    if db:
        cmd += ["--db", db]
    cmd += cli_args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, cwd=REPO_ROOT)
        return {"ok": proc.returncode == 0, "code": proc.returncode,
                "stdout": proc.stdout, "stderr": proc.stderr}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": 124, "stdout": "",
                "stderr": f"timed out after {timeout}s"}


# --------------------------------------------------------------- the pass
def run_pass(*, gate_flags: list[str] | None = None,
             operator: str = "", max_jobs: int = DEFAULT_MAX_JOBS,
             what_if: bool = False, ignore_battery: bool = False,
             auto_accept: bool = True, skip_doctor: bool = False,
             db: str | None = None,
             log_dir: str | None = None,
             step=None, power_fn=None, now: _dt.datetime | None = None,
             echo: bool = True) -> dict:
    """Run one unattended pass. Returns a structured report.

    `step` and `power_fn` are injectable so the whole pass — including the
    doctor refusal, the lock, and the battery skip — is testable offline
    without running a single real stage.
    """
    gate_flags = list(gate_flags or ["--unattended"])
    step = step or (lambda args: default_step(args, db=db))
    power_fn = power_fn or on_battery
    now = now or _utcnow()
    log_dir = log_dir or LOG_DIR
    lock_path = os.path.join(log_dir, LOCK_NAME)
    stamp = now.strftime("%Y-%m-%d_%H%M%S")
    log_path = None if what_if else os.path.join(log_dir, f"auto-run_{stamp}.log")
    log = PassLog(log_path, echo=echo)

    report: dict = {"startedAt": now.isoformat(), "gateFlags": gate_flags,
                    "operator": operator, "whatIf": what_if,
                    "log": log_path, "steps": [], "jobs": [],
                    "outcome": None, "exitCode": EXIT_OK}

    log(f"unattended pass starting (operator: {operator or 'unset'})")
    log(f"repo: {REPO_ROOT}")
    log(f"gates: {' '.join(gate_flags)}")

    # ---- the machine is allowed to decline -------------------------------
    state = lock_state(lock_path, now=now)
    if state["held"] and not state["stale"]:
        age = state.get("ageHours")
        log(f"a previous pass is still running (started {state['since']}"
            + (f", {age * 60:.0f}m ago" if age is not None else "")
            + ") — skipping this one", "WARN")
        report.update(outcome="skipped-locked", exitCode=EXIT_SKIPPED)
        return report
    if state["held"]:
        log(f"stale lock from {state['since']} — taking over", "WARN")

    if not ignore_battery:
        battery = power_fn()
        if battery is True:
            log("running on battery — skipping (use --ignore-battery to "
                "override). Downloading and decoding a VOD drains a laptop "
                "fast and the nightly pass is never urgent.", "WARN")
            report.update(outcome="skipped-battery", exitCode=EXIT_SKIPPED)
            return report
        if battery is None:
            log("power state unknown (no battery detected) — proceeding")

    if what_if:
        log(f"WHAT-IF: would run worker-doctor, find-matches --queue-likely, "
            f"then advance up to {max_jobs} job(s) with "
            f"{' '.join(gate_flags)}")
        log("nothing was touched")
        report.update(outcome="what-if")
        return report

    acquire_lock(lock_path)
    try:
        # ---- 1. doctor ---------------------------------------------------
        if skip_doctor:
            log("--- 1/3 worker-doctor SKIPPED (--skip-doctor) ---", "WARN")
            report["steps"].append({"step": "worker-doctor", "ok": None,
                                    "code": None, "skipped": True})
        else:
            log("--- 1/3 worker-doctor ---")
            res = step(["worker-doctor"])
            report["steps"].append({"step": "worker-doctor", "ok": res["ok"],
                                    "code": res.get("code")})
            if not res["ok"]:
                # The doctor's own summary is the useful part; the layout
                # inventory it also prints is advisory and not what failed.
                for line in (res.get("stdout") or "").splitlines():
                    if "MISSING" in line or "NOT OK" in line:
                        log(f"    {line.strip()}")
                log("worker-doctor FAILED — this pass could not have done "
                    "real work, so it stops here rather than reporting a "
                    "quiet success. Install what it named (see "
                    "`tools/setup-machine.ps1`), or pass --skip-doctor if "
                    "you know this machine is fine.", "ERROR")
                report.update(outcome="doctor-failed",
                              exitCode=EXIT_DOCTOR_FAILED)
                return report
            log("worker-doctor ok")

        # ---- 2. scan + queue --------------------------------------------
        log("--- 2/3 find-matches --queue-likely ---")
        res = step(["find-matches", "--queue-likely"])
        report["steps"].append({"step": "find-matches", "ok": res["ok"],
                                "code": res.get("code")})
        if not res["ok"]:
            # Discovery is a nice-to-have: jobs already in the queue still
            # deserve to be advanced, so a failed scan is a warning.
            log(f"find-matches failed (exit {res.get('code')}) — continuing "
                f"with jobs already queued", "WARN")

        # ---- 3. advance ---------------------------------------------------
        log("--- 3/3 advance open jobs ---")
        res = step(["list-jobs", "--json"])
        if not res["ok"]:
            log(f"could not list jobs (exit {res.get('code')})", "ERROR")
            report.update(outcome="list-failed", exitCode=EXIT_DOCTOR_FAILED)
            return report
        try:
            rows = json.loads(res["stdout"] or "[]")
        except ValueError as exc:
            log(f"could not parse list-jobs JSON: {exc}", "ERROR")
            report.update(outcome="list-failed", exitCode=EXIT_DOCTOR_FAILED)
            return report

        open_jobs = [r for r in rows if r.get("state") not in TERMINAL_STATES]
        log(f"{len(open_jobs)} open job(s)")

        advanced = 0
        for row in open_jobs:
            if advanced >= max_jobs:
                log(f"reached --max-jobs {max_jobs} — leaving "
                    f"{len(open_jobs) - advanced} for the next pass")
                break
            key = row.get("job_key") or row.get("jobKey")
            if not key:
                continue
            log(f"advancing {key} (state {row.get('state')})")
            args = ["autopilot", "--job", key]
            if auto_accept:
                args += ["--auto-accept"]
            if operator:
                args += ["--accepted-by", operator]
            args += gate_flags
            r = step(args)
            for line in (r.get("stdout") or "").splitlines():
                if "[autopilot]" in line:
                    log(f"    {line.strip()}")
            # A held gate exits non-zero and is a NORMAL resting point, not a
            # failure of the pass — recorded, never escalated.
            report["jobs"].append({"jobKey": key, "state": row.get("state"),
                                   "ok": r["ok"], "code": r.get("code")})
            advanced += 1

        report["advanced"] = advanced
        log(f"pass complete: {advanced} job(s) advanced")
        report.update(outcome="ok")
        return report
    finally:
        release_lock(lock_path)
        removed = prune_logs(log_dir)
        if removed:
            log(f"pruned {len(removed)} old log(s)")
        report["events"] = log.events
        if log_path:
            summary = log_path.replace(".log", ".json")
            try:
                with open(summary, "w", encoding="utf-8") as f:
                    json.dump(report, f, indent=1, default=str)
            except OSError:
                pass


def last_pass(log_dir: str | None = None) -> dict | None:
    """The most recent pass summary, for `auto-run --last`."""
    log_dir = log_dir or LOG_DIR
    if not os.path.isdir(log_dir):
        return None
    summaries = sorted(
        (os.path.join(log_dir, n) for n in os.listdir(log_dir)
         if n.startswith("auto-run_") and n.endswith(".json")),
        key=lambda p: os.path.getmtime(p), reverse=True)
    if not summaries:
        return None
    try:
        with open(summaries[0], encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def format_report(report: dict) -> str:
    lines = [
        f"  started : {report.get('startedAt')}",
        f"  gates   : {' '.join(report.get('gateFlags') or [])}",
        f"  outcome : {report.get('outcome')} (exit {report.get('exitCode')})",
    ]
    for s in report.get("steps") or []:
        lines.append(f"    [{'ok ' if s['ok'] else 'FAIL'}] {s['step']}")
    jobs = report.get("jobs") or []
    if jobs:
        lines.append(f"  jobs    : {len(jobs)} advanced")
        for j in jobs:
            # autopilot exits 0 for a GOOD stop — a held gate or a terminal
            # state — and non-zero only when something is actually wrong.
            # Labelling both "stopped at a gate" hid the difference that
            # matters: one is the system working, the other needs a human.
            outcome = "ok" if j["ok"] else "BLOCKED — needs attention"
            lines.append(f"    {j['jobKey']:<34} {j.get('state', ''):<18} "
                         f"{outcome}")
    if report.get("log"):
        lines.append(f"  log     : {report['log']}")
    return "\n".join(lines)
