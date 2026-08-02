"""
supervisor.py — the background service that keeps processing without anyone
watching.

This is the piece that turns a collection of commands into an appliance. It
runs as a normal user process (started by the tray app, and at sign-in by the
Run key), and it is responsible for exactly one thing: *the queue keeps
draining, whatever happens*.

What "whatever happens" covers, and how:

  crash / kill / power loss
      Every effect is already in SQLite through the existing job store and
      state machine — this module adds no shadow state of its own. On startup
      it clears leases whose owner is gone, calls the pipeline's own
      `resume_interrupted` to recover jobs abandoned mid-download, and carries
      on. A supervisor that dies mid-job loses nothing but the current step.

  reboot
      Identical to a crash: the Run key starts it again, startup recovery runs
      again. Nothing is kept in memory across a job.

  a job that keeps failing
      Failures go through `record_attempt`, which schedules a retry with the
      configured backoff and gives up permanently at the configured ceiling.
      The supervisor never retries in a tight loop and never re-runs a job
      whose retry is not yet due.

  a full disk
      Checked *before* claiming work. Below the floor the supervisor idles and
      says so in its heartbeat instead of filling the drive and corrupting a
      download.

  two copies started at once
      A single-instance lock file holding the owning pid. A second supervisor
      exits immediately rather than racing for jobs.

  the control room window closing
      Irrelevant — the supervisor is a separate process and the UI is only a
      viewer of the same database.

The loop body itself is one function, `process_once()`, which claims at most
one job and advances it. It is fully injectable, which is how the tests drive
crash/retry/backoff/storage scenarios without any video.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import socket
import sys
import threading
import time
import traceback
from typing import Any, Callable

from . import paths
from .settings import Settings, atomic_write_json

SUPERVISOR_VERSION = "1.0.0"

#: Considered dead after this many seconds without a heartbeat write.
HEARTBEAT_STALE_SECONDS = 120
#: How often the loop refreshes the heartbeat even while idle.
HEARTBEAT_INTERVAL_SECONDS = 15


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(t: dt.datetime | None = None) -> str:
    return (t or _utcnow()).replace(microsecond=0).isoformat()


# ------------------------------------------------------------ pid liveness
def pid_alive(pid: int) -> bool:
    """Is a process with this pid running? Used only to decide whether a lock
    file is stale — never to signal anything."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.WinDLL("kernel32.dll")  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                # STILL_ACTIVE == 259
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == 259
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # exists, owned by someone else
    except OSError:
        return False
    return True


# -------------------------------------------------------- single instance
class SingleInstance:
    """A pid-bearing lock file. `acquire()` is False when another live
    supervisor already owns it."""

    def __init__(self, path: str | None = None):
        self.path = path or paths.single_instance_lock()
        self.acquired = False

    def _owner(self) -> int | None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return int(json.load(f).get("pid", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        owner = self._owner()
        if owner and owner != os.getpid() and pid_alive(owner):
            return False
        # Either free, stale, or already ours — take it.
        atomic_write_json(self.path, {"pid": os.getpid(), "since": _iso(),
                                      "host": socket.gethostname()})
        self.acquired = True
        return True

    def release(self) -> None:
        if not self.acquired:
            return
        if self._owner() == os.getpid():
            try:
                os.unlink(self.path)
            except OSError:
                pass
        self.acquired = False

    def __enter__(self) -> "SingleInstance":
        if not self.acquire():
            raise RuntimeError(
                "another OWCS Comp Tracker background service is already "
                "running on this account")
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()


# ------------------------------------------------------------- heartbeat
def write_heartbeat(payload: dict[str, Any], *, path: str | None = None) -> None:
    doc = dict(payload)
    doc["at"] = _iso()
    doc["pid"] = os.getpid()
    doc["version"] = SUPERVISOR_VERSION
    try:
        atomic_write_json(path or paths.heartbeat_file(), doc)
    except OSError:
        pass  # a heartbeat that cannot be written must never kill the loop


def read_heartbeat(*, path: str | None = None) -> dict[str, Any] | None:
    """The last heartbeat, annotated with `ageSeconds` and `stale`. None when
    the service has never run or its file is gone."""
    path = path or paths.heartbeat_file()
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    try:
        at = dt.datetime.fromisoformat(str(doc.get("at")))
        if at.tzinfo is None:
            at = at.replace(tzinfo=dt.timezone.utc)
        age = int((_utcnow() - at).total_seconds())
    except (TypeError, ValueError):
        age = HEARTBEAT_STALE_SECONDS + 1
    doc["ageSeconds"] = age
    pid = int(doc.get("pid") or 0)
    # Stale means either the clock says so or the process is simply gone.
    doc["stale"] = age > HEARTBEAT_STALE_SECONDS or not pid_alive(pid)
    return doc


# ------------------------------------------------------------- stop flag
def request_stop(*, path: str | None = None) -> str:
    """Ask a running supervisor to finish its current step and exit. Used by
    the tray's Quit and by the updater before it replaces files."""
    p = path or os.path.join(paths.sub("state"), "supervisor.stop")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(_iso())
    return p


def clear_stop(*, path: str | None = None) -> None:
    p = path or os.path.join(paths.sub("state"), "supervisor.stop")
    try:
        os.unlink(p)
    except OSError:
        pass


def stop_requested(*, path: str | None = None) -> bool:
    return os.path.exists(
        path or os.path.join(paths.sub("state"), "supervisor.stop"))


# ------------------------------------------------------------- the engine
class Supervisor:
    """The processing loop.

    Every collaborator is injectable so the tests can drive real scenarios
    (a claim that raises, a disk that is full, a retry that is not yet due)
    without video, network, or a real queue.
    """

    def __init__(self, *, settings: Settings | None = None,
                 store_factory: Callable[[], Any] | None = None,
                 advance: Callable[..., dict] | None = None,
                 free_gb: Callable[[str], float] | None = None,
                 log: Callable[[str], None] | None = None,
                 clock: Callable[[], dt.datetime] = _utcnow):
        self.settings = settings or Settings()
        self._store_factory = store_factory
        self._advance = advance
        self._free_gb = free_gb or self._default_free_gb
        self._log = log or self._default_log
        self._clock = clock

        self.started_at = self._clock()
        self.processed = 0
        self.failures = 0
        self.current_job: str | None = None
        self.last_error: str | None = None
        self.last_result: dict[str, Any] | None = None
        self.idle_reason: str | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _default_free_gb(path: str) -> float:
        import shutil
        try:
            return shutil.disk_usage(path).free / (1024 ** 3)
        except OSError:
            return 0.0

    def _default_log(self, message: str) -> None:
        line = f"{_iso()} [supervisor] {message}"
        print(line, flush=True)
        try:
            os.makedirs(paths.sub("logs"), exist_ok=True)
            with open(paths.supervisor_log(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def worker_id(self) -> str:
        return f"desktop-{socket.gethostname()}-{os.getpid()}"

    # ------------------------------------------------------- pipeline glue
    @staticmethod
    def _ensure_pipeline_importable() -> None:
        """Put the installed payload on sys.path.

        `process_once` imports `pipeline.automation.*` lazily. Relying on the
        caller having arranged sys.path first made the supervisor work only
        when started through owcs_app.py — importing the class directly (a
        test, a repair action, an embedded run) failed on the first claim.
        """
        root = paths.app_root()
        if root not in sys.path:
            sys.path.insert(0, root)

    def _open_store(self):
        self._ensure_pipeline_importable()
        if self._store_factory is not None:
            return self._store_factory()
        from pipeline.automation import job_store as js  # noqa: WPS433
        store = js.JobStore(paths.automation_db())
        store.init_db()
        return store

    def _advance_job(self, store, job_key: str) -> dict:
        """Advance one job by running the real autopilot until a human gate."""
        if self._advance is not None:
            return self._advance(store, job_key)
        from pipeline.automation import autopilot, locks as lk  # noqa: WPS433
        lock_mgr = lk.LockManager(
            store.con, lease_seconds=self.settings.get("pollSeconds") * 15)
        return autopilot.run_autopilot(
            store, lock_mgr, job_key,
            worker_id=self.worker_id(),
            media_root=paths.sub("media"),
            # Machine identity proposals are accepted only when they are
            # clean; the autopilot still stops at every human gate that
            # matters, and low-confidence detections still go to review.
            auto_accept=bool(self.settings.get("autoPublish")),
            accepted_by=self.worker_id())

    # ------------------------------------------------------------- startup
    def recover(self) -> dict[str, Any]:
        """Startup recovery: expired leases, jobs abandoned mid-download.

        Runs before the first claim on every start, which is what makes a
        reboot indistinguishable from a pause.
        """
        report: dict[str, Any] = {"clearedLeases": 0, "resumed": [], "errors": []}
        try:
            store = self._open_store()
        except Exception as exc:
            report["errors"].append(f"cannot open job database: {exc}")
            return report
        try:
            from pipeline.automation import locks as lk, ops  # noqa: WPS433
            lock_mgr = lk.LockManager(store.con)
            report["clearedLeases"] = lock_mgr.clear_expired()
            resumed = ops.resume_interrupted_job(
                store, lock_mgr, worker_id=self.worker_id(),
                media_root=paths.sub("media"))
            report["resumed"] = [r.get("jobKey") or r.get("job_key")
                                 for r in (resumed or [])]
        except Exception as exc:
            # Recovery is best-effort by design: a recovery bug must not stop
            # the service from starting and doing useful work.
            report["errors"].append(f"{type(exc).__name__}: {exc}")
        finally:
            try:
                store.close()
            except Exception:
                pass
        if report["clearedLeases"] or report["resumed"]:
            self._log(f"recovery: cleared {report['clearedLeases']} stale "
                      f"lease(s), resumed {len(report['resumed'])} job(s)")
        for err in report["errors"]:
            self._log(f"recovery problem: {err}")
        return report

    # ---------------------------------------------------------- guardrails
    def storage_ok(self) -> tuple[bool, str]:
        floor = float(self.settings.get("minFreeDiskGb"))
        free = self._free_gb(self.settings.storage_root())
        if free < floor:
            return False, (f"paused: {free:.1f} GB free is below the "
                           f"{floor:.1f} GB floor")
        return True, f"{free:.1f} GB free"

    # ------------------------------------------------------------ one step
    def process_once(self) -> dict[str, Any]:
        """Claim at most one job and advance it. Never raises.

        Returns {"action": "idle"|"advanced"|"error"|"paused", ...}. The loop
        treats every outcome the same way — sleep, heartbeat, go again — which
        is why a bad job can never wedge the service.
        """
        ok, detail = self.storage_ok()
        if not ok:
            self.idle_reason = detail
            return {"action": "paused", "reason": detail}

        try:
            store = self._open_store()
        except Exception as exc:
            self.last_error = f"job database unavailable: {exc}"
            self._log(self.last_error)
            return {"action": "error", "reason": self.last_error}

        try:
            self._ensure_pipeline_importable()
            from pipeline.automation import models  # noqa: WPS433
            kinds = [models.KIND_RECORD, models.KIND_PROCESS,
                     models.KIND_SEGMENT, models.KIND_PUBLISH]
            job = store.claim_next(kinds, self.worker_id(), now=self._clock())
            if job is None:
                self.current_job = None
                self.idle_reason = "no work ready"
                return {"action": "idle", "reason": "no work ready"}

            self.current_job = job.job_key
            self.idle_reason = None
            self._log(f"claimed {job.job_key} ({job.state})")
            try:
                result = self._advance_job(store, job.job_key)
            except Exception as exc:
                # A crashing stage is a job failure, not a service failure.
                # Record it through the store so the retry policy applies.
                self.failures += 1
                trace = traceback.format_exc(limit=6)
                self.last_error = f"{job.job_key}: {type(exc).__name__}: {exc}"
                self._log(f"stage crashed for {self.last_error}\n{trace}")
                try:
                    store.record_attempt(job.job_key, ok=False,
                                         detail=str(exc)[:500])
                except Exception as inner:
                    self._log(f"could not record the failed attempt: {inner}")
                return {"action": "error", "job": job.job_key,
                        "reason": self.last_error}

            self.processed += 1
            self.last_result = result
            summary = (result or {}).get("stopDetail") or (result or {}).get("reason") or ""
            self._log(f"advanced {job.job_key} -> "
                      f"{(result or {}).get('state', '?')} {summary}"[:400])
            return {"action": "advanced", "job": job.job_key, "result": result}
        finally:
            try:
                store.close()
            except Exception:
                pass

    # ------------------------------------------------------------ the loop
    def heartbeat_payload(self) -> dict[str, Any]:
        return {
            "startedAt": _iso(self.started_at),
            "processed": self.processed,
            "failures": self.failures,
            "currentJob": self.current_job,
            "idleReason": self.idle_reason,
            "lastError": self.last_error,
            "storage": self.storage_ok()[1],
            "running": not self._stop.is_set(),
        }

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self, *, max_iterations: int | None = None,
                    sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
        """Drain the queue until asked to stop.

        `max_iterations` bounds the loop for the tests; production passes
        None. The loop body never propagates an exception — the service is
        expected to outlive every individual failure.
        """
        # The stop flag is NOT cleared here. "Pause processing" has to survive
        # a restart or a reboot, or the tray's Pause would silently undo
        # itself the next time Windows signed the user in. The flag is cleared
        # deliberately by Resume (repair.start-worker), which is the only place
        # that should mean "start working again".
        self._log(f"starting (version {SUPERVISOR_VERSION}, pid {os.getpid()})")
        self.recover()
        write_heartbeat(self.heartbeat_payload())

        iterations = 0
        poll = max(5, int(self.settings.get("pollSeconds")))
        if stop_requested():
            self._log("processing is paused — exiting without claiming work")
        while not self._stop.is_set():
            if stop_requested():
                self._log("stop requested — finishing")
                break
            try:
                outcome = self.process_once()
            except Exception as exc:  # belt and braces; process_once catches
                outcome = {"action": "error", "reason": str(exc)}
                self._log(f"loop error: {type(exc).__name__}: {exc}")
            write_heartbeat(self.heartbeat_payload())

            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            # Work back-to-back while there is work; idle politely otherwise.
            if outcome.get("action") == "advanced":
                continue
            if outcome.get("action") == "paused":
                sleep(min(poll * 3, 300))
            else:
                sleep(poll)

        self._stop.set()
        write_heartbeat({**self.heartbeat_payload(), "running": False})
        self._log(f"stopped after {iterations} iteration(s), "
                  f"{self.processed} advanced, {self.failures} failed")
        return {"iterations": iterations, "processed": self.processed,
                "failures": self.failures}


# --------------------------------------------------------------- entrypoint
def main(argv: list[str] | None = None) -> int:
    """`python -m owcs_desktop.supervisor` — run the service in the foreground.

    The tray app starts this as a child process; the installer's scheduled
    entry starts the tray, which starts this. Refuses to start a second copy.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="OWCS Comp Tracker background processing service")
    parser.add_argument("--once", action="store_true",
                        help="advance a single job and exit")
    parser.add_argument("--max-iterations", type=int, default=None)
    args = parser.parse_args(argv)

    paths.apply_environment()
    paths.seed_from_payload()
    sys.path.insert(0, paths.app_root())

    lock = SingleInstance()
    if not lock.acquire():
        print("[supervisor] another background service already owns this "
              "account's queue — exiting.", flush=True)
        return 3
    try:
        sup = Supervisor()
        if args.once:
            outcome = sup.process_once()
            write_heartbeat(sup.heartbeat_payload())
            print(json.dumps(outcome, indent=2, default=str))
            return 0 if outcome.get("action") != "error" else 1
        sup.run_forever(max_iterations=args.max_iterations)
        return 0
    finally:
        lock.release()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
