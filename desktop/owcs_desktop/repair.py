"""
repair.py — the buttons behind every failed health check.

`health.run_checks()` attaches a `repair` id to any check it knows how to fix.
This module is the other half: one function per id, each returning
`{"ok", "detail", ...}`. The control room renders a "Fix this" button for any
check carrying a repair id, calls `run(id)`, and re-runs the checks.

The rules every action here follows:

  * **Do the work, don't print instructions.** No action returns "run this
    command in a terminal". If a fix genuinely cannot be performed by the
    application (a corrupted installation, say), it says what it will do about
    it and offers the installer — it does not hand out a command line.
  * **Never destroy evidence.** Repairing storage prunes finished downloads,
    never crops, reports, quarantine or backups. Repairing databases creates
    what is missing and never overwrites what exists.
  * **Report failure as failure.** An action that could not do its job returns
    ok=False with the reason. Nothing here returns a placeholder success.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any, Callable

from . import backup, paths, storage
from .settings import Settings


def _ok(detail: str, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "detail": detail, **extra}


def _fail(detail: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "detail": detail, **extra}


# ------------------------------------------------------------ dependencies
def repair_dependencies(*, runner=subprocess) -> dict[str, Any]:
    """Make the external tools and Python modules usable again.

    Frozen (installed) build: every dependency was shipped inside the
    installation, so a missing one means damaged files — the honest fix is to
    re-run the installer, and the action reports which files are missing so
    the user knows why.

    Source build: install the pinned requirements into the running
    interpreter. That is a real repair, performed here, with the result
    reported from the actual exit code.
    """
    from . import health

    missing = [name for name in health.REQUIRED_BINARIES
               if not health.resolve_binary(name)]
    modules = [m for m in ("cv2", "numpy")
               if health._check_module(m, m)["status"] == health.FAIL]

    if not missing and not modules:
        return _ok("Every dependency is already present.")

    if paths.is_frozen():
        return _fail(
            "These dependencies ship inside the application, so their absence "
            "means installed files are missing or damaged: "
            + ", ".join(missing + modules)
            + ". Use 'Repair installation' to restore them.",
            missingBinaries=missing, missingModules=modules,
            suggested="repair.reinstall")

    requirements = os.path.join(paths.app_root(), "requirements.txt")
    if modules and os.path.exists(requirements):
        try:
            proc = runner.run(
                [sys.executable, "-m", "pip", "install", "-r", requirements],
                capture_output=True, text=True, timeout=1800,
                **paths.PIPE_TEXT)
        except (OSError, subprocess.SubprocessError) as exc:
            return _fail(f"Could not run the dependency installer: {exc}")
        if proc.returncode != 0:
            tail = ((proc.stderr or proc.stdout or "").strip().splitlines() or [""])[-8:]
            return _fail("Installing the Python dependencies failed:\n"
                         + "\n".join(tail))
        modules = [m for m in modules
                   if health._check_module(m, m)["status"] == health.FAIL]

    still_missing = [n for n in missing if not health.resolve_binary(n)]
    if still_missing or modules:
        return _fail(
            "Still missing after repair: " + ", ".join(still_missing + modules)
            + ". ffmpeg, ffprobe and yt-dlp are shipped by the Windows "
              "installer; on a source checkout they must be on PATH.",
            missingBinaries=still_missing, missingModules=modules)
    return _ok("Dependencies restored.")


# ---------------------------------------------------------------- storage
def repair_storage(*, settings: Settings | None = None) -> dict[str, Any]:
    """Recreate the storage layout and reclaim space from finished jobs."""
    settings = settings or Settings()
    try:
        paths.ensure_layout()
    except OSError as exc:
        return _fail(f"Could not create the storage folders: {exc}")

    result = storage.enforce(settings, apply=True)
    plan, applied = result["plan"], result["applied"]
    freed = (applied or {}).get("freedGb", 0.0)
    free_now = storage.free_gb(settings.storage_root())
    if applied and applied.get("errors"):
        return _fail(
            f"Reclaimed {freed:.2f} GB but {len(applied['errors'])} folder(s) "
            "could not be removed (a file may still be open).",
            freedGb=freed, errors=applied["errors"], freeGb=round(free_now, 2))
    if not plan["remove"]:
        return _ok(
            f"Storage folders are in place. Nothing was eligible for cleanup "
            f"— {free_now:.1f} GB free.", freeGb=round(free_now, 2))
    return _ok(f"Reclaimed {freed:.2f} GB from {len(plan['remove'])} finished "
               f"job(s). {free_now:.1f} GB free.",
               freedGb=freed, freeGb=round(free_now, 2))


# -------------------------------------------------------------- databases
def repair_databases() -> dict[str, Any]:
    """Create/upgrade both databases. Never overwrites existing data."""
    paths.ensure_layout()
    created = paths.seed_from_payload()
    sys.path.insert(0, paths.app_root())
    made = []
    try:
        from pipeline.automation import job_store as js
        store = js.JobStore(paths.automation_db())
        store.init_db()
        store.close()
        made.append("job queue")
    except Exception as exc:
        return _fail(f"Could not create the job queue database: {exc}")
    try:
        import pipeline.db as pdb
        con = pdb.connect(paths.content_db())
        try:
            pdb.init_schema(con)
        finally:
            con.close()
        made.append("results")
    except Exception as exc:
        return _fail(f"Could not create the results database: {exc}")
    detail = f"Prepared the {' and '.join(made)} database(s)."
    if created:
        detail += f" Seeded {len(created)} file(s) from the installation."
    return _ok(detail, seeded=created)


def repair_restore_backup(snapshot_id: str | None = None) -> dict[str, Any]:
    """Roll back to the newest snapshot that still verifies."""
    snapshots = backup.list_snapshots()
    if not snapshots:
        return _fail("There are no backups to restore from yet. A backup is "
                     "taken automatically before each publish.")
    if snapshot_id:
        chosen = next((s for s in snapshots if s["id"] == snapshot_id), None)
        if chosen is None:
            return _fail(f"No backup named {snapshot_id}.")
        if not chosen.get("valid"):
            return _fail(f"Backup {snapshot_id} failed verification and was "
                         "not restored.")
    else:
        chosen = next((s for s in snapshots if s.get("valid")), None)
        if chosen is None:
            return _fail("Every stored backup failed verification; none was "
                         "restored.")
    result = backup.restore_snapshot(chosen["id"])
    if not result["ok"]:
        return _fail(f"Restore of {chosen['id']} failed: "
                     f"{result.get('error') or result.get('errors')}",
                     **result)
    return _ok(f"Restored {len(result['restored'])} file(s) from backup "
               f"{chosen['id']}. The previous state was saved as "
               f"{result['preRestoreSnapshot']}.", **result)


# -------------------------------------------------------------- autostart
def repair_autostart(enable: bool = True) -> dict[str, Any]:
    from . import autostart as a
    status = a.AutoStart().sync(enable)
    if not status["supported"]:
        return _fail(status.get("detail", "not supported on this platform"))
    if status.get("error"):
        return _fail(f"Could not update the startup entry: {status['error']}")
    Settings().set("autoStart", enable)
    return _ok("The app will start with Windows." if enable
               else "The app will no longer start with Windows.", **status)


# ----------------------------------------------------------------- worker
def repair_start_worker(*, spawn: Callable[..., Any] | None = None
                        ) -> dict[str, Any]:
    """Start the background service if it is not already running."""
    from . import supervisor

    beat = supervisor.read_heartbeat()
    if beat and not beat.get("stale"):
        return _ok(f"The background service is already running "
                   f"(pid {beat.get('pid')}).")

    supervisor.clear_stop()
    command = ([os.path.abspath(sys.executable), "--service"]
               if paths.is_frozen()
               else [os.path.abspath(sys.executable), "-m",
                     "owcs_desktop.supervisor"])
    env = dict(os.environ)
    paths.apply_environment(env=env)
    env["PYTHONPATH"] = os.pathsep.join(
        [os.path.join(paths.app_root(), "desktop"), paths.app_root(),
         env.get("PYTHONPATH", "")]).strip(os.pathsep)
    spawner = spawn or subprocess.Popen
    try:
        kwargs: dict[str, Any] = {"cwd": paths.app_root(), "env": env,
                                  "close_fds": True}
        if sys.platform == "win32":  # no console window for a background service
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        spawner(command, **kwargs)
    except OSError as exc:
        return _fail(f"Could not start the background service: {exc}")
    return _ok("Started the background service.")


def repair_reset_credentials() -> dict[str, Any]:
    from . import credentials as cred
    try:
        cred.CredentialVault().reset()
    except OSError as exc:
        return _fail(f"Could not reset the credential store: {exc}")
    return _ok("Cleared the saved credentials. Re-enter any API keys you use.")


def repair_reinstall() -> dict[str, Any]:
    """Restore the installed payload — by fetching and running the installer.

    Nothing is printed for the user to type: if an update is reachable this
    downloads and verifies it and reports the path, ready for one click.
    """
    from . import updates
    info = updates.check_for_update(
        channel=str(Settings().get("updateChannel")))
    if not info.get("ok"):
        return _fail(
            "Installed files are missing and the installer could not be "
            f"reached to restore them ({info.get('error')}). "
            "Re-run the OWCS Comp Tracker installer to repair the "
            "installation.")
    if not (info.get("installer") or {}).get("url"):
        return _fail("Installed files are missing and no installer is "
                     "published to restore them from.")
    got = updates.download_update(info)
    if not got.get("ok"):
        return _fail(f"Could not obtain a verified installer: {got.get('error')}")
    return _ok("Downloaded and verified a fresh installer. Use 'Install now' "
               "to repair the installation.",
               installer=got["path"], sha256=got["sha256"])


# ------------------------------------------------------------------ table
ACTIONS: dict[str, dict[str, Any]] = {
    "repair.dependencies": {
        "label": "Repair dependencies",
        "help": "Restore ffmpeg, ffprobe, yt-dlp, OpenCV and NumPy.",
        "fn": repair_dependencies,
    },
    "repair.storage": {
        "label": "Free up space",
        "help": "Recreate the storage folders and delete finished downloads. "
                "Evidence, reports and backups are never touched.",
        "fn": repair_storage,
    },
    "repair.databases": {
        "label": "Rebuild databases",
        "help": "Create any missing database. Existing data is left alone.",
        "fn": repair_databases,
    },
    "repair.restore-backup": {
        "label": "Restore from backup",
        "help": "Roll back to the newest verified snapshot.",
        "fn": repair_restore_backup,
    },
    "repair.autostart": {
        "label": "Fix automatic startup",
        "help": "Re-register the app to start with Windows.",
        "fn": repair_autostart,
    },
    "repair.start-worker": {
        "label": "Start the background service",
        "help": "Begin processing the queue again.",
        "fn": repair_start_worker,
    },
    "repair.reset-credentials": {
        "label": "Reset saved credentials",
        "help": "Clear the credential store when it cannot be read.",
        "fn": repair_reset_credentials,
    },
    "repair.reinstall": {
        "label": "Repair installation",
        "help": "Download and verify a fresh installer for damaged files.",
        "fn": repair_reinstall,
    },
}


def list_actions() -> list[dict[str, Any]]:
    return [{"id": key, "label": meta["label"], "help": meta["help"]}
            for key, meta in ACTIONS.items()]


def run(action_id: str, **kwargs: Any) -> dict[str, Any]:
    """Run one repair action by id. Unknown ids are refused, never guessed."""
    meta = ACTIONS.get(action_id)
    if meta is None:
        return _fail(f"unknown repair action: {action_id!r}",
                     known=sorted(ACTIONS))
    try:
        return {"action": action_id, **meta["fn"](**kwargs)}
    except Exception as exc:  # a crashing repair is still a reported outcome
        return {"action": action_id, "ok": False,
                "detail": f"{type(exc).__name__}: {exc}"}
