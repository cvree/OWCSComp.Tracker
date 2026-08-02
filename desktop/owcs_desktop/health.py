"""
health.py — "is this machine actually able to process a broadcast?"

Two things live here:

  * `run_checks()` — a fast (sub-second, no network) sweep of everything the
    pipeline needs: interpreter, OpenCV/NumPy, the three external binaries,
    writable storage, free disk, the databases, the calibrated layouts and
    hero templates, credentials, autostart. Each check reports `ok` /
    `warn` / `fail` with a plain-English `detail` and, where one exists, a
    `repair` action id the control room can offer as a button.

  * `run_readiness_test()` — the real end-to-end proof. It runs the
    repository's own offline end-to-end suite, which builds a synthetic
    broadcast with ffmpeg and drives the actual pipeline through intake →
    download bookkeeping → scan proxy → layout resolution → segmentation →
    identity → clip extraction → export. If that passes on this machine, the
    machine can process a broadcast. Nothing here reports success it did not
    observe: a skipped suite is reported as skipped, not as a pass.

Severity vocabulary, used by the wizard to decide whether setup may finish:
    ok    — nothing to do
    warn  — degraded but usable (a missing optional API key, say)
    fail  — the pipeline cannot run until this is fixed
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from typing import Any

from . import paths

OK, WARN, FAIL = "ok", "warn", "fail"

#: Minimum interpreter the pipeline is tested against.
MIN_PYTHON = (3, 11)
#: Free space below this and a full broadcast download will not fit.
MIN_FREE_GB = 10.0

REQUIRED_BINARIES = ("ffmpeg", "ffprobe", "yt-dlp")


def _check(check_id: str, label: str, status: str, detail: str,
           *, repair: str | None = None, **extra: Any) -> dict[str, Any]:
    out = {"id": check_id, "label": label, "status": status, "detail": detail}
    if repair:
        out["repair"] = repair
    out.update(extra)
    return out


# ------------------------------------------------------------------ tools
def resolve_binary(name: str) -> str | None:
    """Find a required tool: vendored copy first, then PATH.

    The vendored copy wins so the installed application always runs the ffmpeg
    it shipped with, whatever else is on the machine.
    """
    exe = f"{name}.exe" if sys.platform == "win32" else name
    vendored = os.path.join(paths.vendor_dir(), exe)
    if os.path.isfile(vendored) and os.access(vendored, os.X_OK):
        return vendored
    return shutil.which(name)


def binary_version(path: str) -> str:
    """First line of `<tool> -version` / `--version`, or an empty string."""
    for flag in ("-version", "--version"):
        try:
            proc = subprocess.run([path, flag], capture_output=True, text=True,
                                  timeout=20, **paths.PIPE_TEXT)
        except (OSError, subprocess.SubprocessError):
            continue
        text = (proc.stdout or proc.stderr or "").strip()
        if text:
            return text.splitlines()[0][:200]
    return ""


def _check_binaries() -> list[dict[str, Any]]:
    out = []
    for name in REQUIRED_BINARIES:
        found = resolve_binary(name)
        if not found:
            out.append(_check(
                f"bin.{name}", name, FAIL,
                f"{name} was not found. Video cannot be downloaded or decoded "
                "without it.",
                repair="repair.dependencies"))
            continue
        version = binary_version(found)
        vendored = os.path.dirname(found) == paths.vendor_dir()
        out.append(_check(
            f"bin.{name}", name, OK,
            f"{version or 'present'}", path=found, bundled=vendored))
    return out


# --------------------------------------------------------------- runtime
def _check_python() -> dict[str, Any]:
    v = sys.version_info
    current = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) < MIN_PYTHON:
        return _check("runtime.python", "Python runtime", FAIL,
                      f"Python {current} is older than the required "
                      f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}.")
    return _check("runtime.python", "Python runtime", OK,
                  f"Python {current}"
                  + (" (bundled)" if paths.is_frozen() else ""))


def _check_module(mod: str, label: str) -> dict[str, Any]:
    """Is this module importable, and does it actually work?

    Just imports it. It used to call `importlib.util.find_spec()` first, to
    tell "not installed" apart from "installed but broken" — which is a real
    distinction, but not worth what it cost: inside a PyInstaller bundle,
    probing a C-extension package with find_spec and THEN importing it makes
    the frozen importer load the extension twice, and it refuses with
    "cannot load module more than once per process". NumPy then appears
    broken, and OpenCV fails behind it because it imports NumPy.

    ImportError still separates the two cases well enough for the message,
    and one import cannot trip over itself.
    """
    try:
        module = importlib.import_module(mod)
    except ImportError as exc:
        return _check(f"runtime.{mod}", label, FAIL,
                      f"{label} could not be imported: {exc}. "
                      f"Detection cannot run.",
                      repair="repair.dependencies")
    except Exception as exc:  # imports, then explodes on its own initialisation
        return _check(f"runtime.{mod}", label, FAIL,
                      f"{label} is installed but failed to load: "
                      f"{type(exc).__name__}: {exc}",
                      repair="repair.dependencies")
    version = getattr(module, "__version__", "") or "present"
    return _check(f"runtime.{mod}", label, OK, f"{label} {version}")


# --------------------------------------------------------------- storage
def _free_gb(path: str) -> float:
    try:
        return shutil.disk_usage(path).free / (1024 ** 3)
    except OSError:
        return 0.0


def _check_storage(min_free_gb: float = MIN_FREE_GB) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    root = paths.data_root()
    try:
        paths.ensure_layout()
        probe = os.path.join(paths.sub("tmp"), ".writetest")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.unlink(probe)
        out.append(_check("storage.writable", "Storage folder", OK,
                          root, path=root))
    except OSError as exc:
        out.append(_check("storage.writable", "Storage folder", FAIL,
                          f"Cannot write to {root}: {exc}",
                          repair="repair.storage", path=root))
        return out

    free = _free_gb(root)
    if free < min_free_gb:
        out.append(_check(
            "storage.free", "Free disk space", WARN,
            f"{free:.1f} GB free — below the {min_free_gb:.0f} GB a full "
            "broadcast download needs. Processing will pause instead of "
            "filling the drive.",
            repair="repair.storage", freeGb=round(free, 2)))
    else:
        out.append(_check("storage.free", "Free disk space", OK,
                          f"{free:.1f} GB free", freeGb=round(free, 2)))
    return out


# -------------------------------------------------------------- pipeline
def _check_databases() -> list[dict[str, Any]]:
    import sqlite3
    out = []
    for check_id, label, path in (
            ("db.content", "Results database", paths.content_db()),
            ("db.automation", "Job queue database", paths.automation_db())):
        if not os.path.exists(path):
            out.append(_check(
                check_id, label, WARN,
                "Not created yet — it is created the first time the app runs.",
                repair="repair.databases", path=path))
            continue
        try:
            con = sqlite3.connect(path)
            try:
                con.execute("PRAGMA quick_check").fetchone()
                tables = con.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='table'"
                ).fetchone()[0]
            finally:
                con.close()
        except sqlite3.Error as exc:
            out.append(_check(check_id, label, FAIL,
                              f"Database is damaged: {exc}",
                              repair="repair.restore-backup", path=path))
            continue
        size_mb = os.path.getsize(path) / (1024 ** 2)
        out.append(_check(check_id, label, OK,
                          f"{tables} tables, {size_mb:.1f} MB", path=path))
    return out


def _check_assets() -> list[dict[str, Any]]:
    """Calibrated layouts and hero template sets must be in the payload."""
    out = []
    app = paths.app_root()

    layout_dir = os.path.join(app, "layouts")
    layouts = [f for f in sorted(os.listdir(layout_dir))
               if f.endswith(".json")] if os.path.isdir(layout_dir) else []
    calibrated = []
    for name in layouts:
        try:
            import json
            with open(os.path.join(layout_dir, name), "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        # A layout is only usable if it carries real probe geometry from
        # calibrate_source.py — a hand-guessed starter file is not.
        if doc.get("hud_probe"):
            calibrated.append(name)
    if not calibrated:
        out.append(_check("assets.layouts", "Broadcast layouts", FAIL,
                          "No calibrated layout is installed, so no broadcast "
                          "can be read.", repair="repair.reinstall",
                          count=0))
    else:
        out.append(_check("assets.layouts", "Broadcast layouts", OK,
                          f"{len(calibrated)} calibrated "
                          f"({', '.join(n[:-5] for n in calibrated[:4])}"
                          f"{'…' if len(calibrated) > 4 else ''})",
                          count=len(calibrated), names=calibrated))

    tpl_root = os.path.join(app, "templates")
    sets = []
    if os.path.isdir(tpl_root):
        for name in sorted(os.listdir(tpl_root)):
            d = os.path.join(tpl_root, name)
            if os.path.isdir(d):
                pngs = [p for p in os.listdir(d) if p.endswith(".png")]
                if pngs:
                    sets.append((name, len(pngs)))
    if not sets:
        out.append(_check("assets.templates", "Hero templates", FAIL,
                          "No hero template set is installed, so heroes cannot "
                          "be identified.", repair="repair.reinstall", count=0))
    else:
        total = sum(n for _, n in sets)
        out.append(_check(
            "assets.templates", "Hero templates", OK,
            f"{len(sets)} broadcast set(s), {total} templates",
            count=len(sets), total=total,
            sets=[{"name": n, "templates": c} for n, c in sets]))
    return out


# ----------------------------------------------------------- credentials
def _check_credentials() -> list[dict[str, Any]]:
    from . import credentials as cred
    vault = cred.CredentialVault()
    out = []
    try:
        described = vault.describe()
    except cred.CredentialError as exc:
        return [_check("credentials.vault", "Saved credentials", FAIL,
                       str(exc), repair="repair.reset-credentials")]
    protection = described[0]["protection"] if described else "unknown"
    out.append(_check(
        "credentials.vault", "Credential protection",
        OK if protection == cred.PROTECTION_DPAPI else WARN,
        "Windows DPAPI — encrypted for this Windows account only"
        if protection == cred.PROTECTION_DPAPI else
        "Stored with file permissions only. Windows DPAPI encryption is "
        "unavailable on this platform.",
        protection=protection))
    for entry in described:
        out.append(_check(
            f"credentials.{entry['name']}", entry["label"],
            OK if entry["present"] else WARN,
            "Saved" if entry["present"] else f"Not set — optional. {entry['help']}",
            repair=None if entry["present"] else "open.credentials"))
    return out


def _check_autostart() -> dict[str, Any]:
    from . import autostart as a
    status = a.AutoStart().status()
    if not status["supported"]:
        return _check("autostart", "Start with Windows", WARN,
                      "Only available on Windows.", **status)
    if status["stale"]:
        return _check("autostart", "Start with Windows", WARN,
                      "Registered, but pointing at an old install location.",
                      repair="repair.autostart", **status)
    return _check("autostart", "Start with Windows",
                  OK if status["enabled"] else WARN,
                  "Enabled" if status["enabled"] else
                  "Disabled — the app will not process jobs until you open it.",
                  repair=None if status["enabled"] else "repair.autostart",
                  **status)


def _check_supervisor() -> dict[str, Any]:
    from . import supervisor
    beat = supervisor.read_heartbeat()
    if beat is None:
        return _check("worker.heartbeat", "Background service", WARN,
                      "Not running.", repair="repair.start-worker")
    if beat.get("stale"):
        return _check(
            "worker.heartbeat", "Background service", WARN,
            f"Last heartbeat {beat.get('ageSeconds', '?')}s ago — the service "
            "looks stopped or wedged.",
            repair="repair.start-worker", **beat)
    return _check("worker.heartbeat", "Background service", OK,
                  f"Running (pid {beat.get('pid')}), "
                  f"{beat.get('processed', 0)} job(s) processed this session.",
                  **beat)


# -------------------------------------------------------------- the sweep
def run_checks(*, min_free_gb: float | None = None,
               include_worker: bool = True) -> dict[str, Any]:
    """Every check, in display order, plus a rolled-up verdict."""
    checks: list[dict[str, Any]] = [_check_python()]
    checks.append(_check_module("cv2", "OpenCV"))
    checks.append(_check_module("numpy", "NumPy"))
    checks.extend(_check_binaries())
    checks.extend(_check_storage(min_free_gb if min_free_gb is not None
                                 else MIN_FREE_GB))
    checks.extend(_check_databases())
    checks.extend(_check_assets())
    checks.extend(_check_credentials())
    checks.append(_check_autostart())
    if include_worker:
        checks.append(_check_supervisor())

    failures = [c for c in checks if c["status"] == FAIL]
    warnings = [c for c in checks if c["status"] == WARN]
    return {
        "ok": not failures,
        "canProcess": not failures,
        "counts": {
            "ok": sum(1 for c in checks if c["status"] == OK),
            "warn": len(warnings),
            "fail": len(failures),
        },
        "checks": checks,
        "blocking": [c["id"] for c in failures],
        "paths": paths.describe(),
    }


# ----------------------------------------------------- the readiness test
#: The suites the readiness test runs, in order. These are the repository's
#: own tests — the readiness check is literally "can this machine pass the
#: pipeline's end-to-end proof", not a bespoke imitation of one.
READINESS_SUITES = (
    ("pipeline/test_pipeline_synthetic.py",
     "Synthetic broadcast: capture → detect → sync → export"),
    ("pipeline/test_end_to_end_offline.py",
     "Full intake loop: link → download → proxy → layout → segments → clip"),
)


def run_readiness_test(*, timeout: int = 1800, app: str | None = None,
                       runner=subprocess) -> dict[str, Any]:
    """Run the real end-to-end proof on this machine.

    Returns a per-suite report. A suite that reports itself skipped (missing
    ffmpeg, say) is recorded as `skipped` — never counted as a pass.
    """
    app = app or paths.app_root()
    env = dict(os.environ)
    paths.apply_environment(env=env)
    # Keep the readiness run entirely out of the user's real databases.
    import tempfile
    sandbox = tempfile.mkdtemp(prefix="owcs-readiness-", dir=paths.sub("tmp"))
    env["OWCS_DB"] = os.path.join(sandbox, "owcs.sqlite")
    env["OWCS_AUTOMATION_DB"] = os.path.join(sandbox, "automation.sqlite")
    env["OWCS_MEDIA_ROOT"] = os.path.join(sandbox, "media")

    results = []
    for rel, label in READINESS_SUITES:
        script = os.path.join(app, rel)
        if not os.path.exists(script):
            results.append({"suite": rel, "label": label, "status": "missing",
                            "detail": f"{rel} is not installed"})
            continue
        try:
            proc = runner.run(paths.python_command() + [script],
                              cwd=app, env=env,
                              capture_output=True, text=True, timeout=timeout,
                              **paths.PIPE_TEXT)
        except subprocess.TimeoutExpired:
            results.append({"suite": rel, "label": label, "status": "timeout",
                            "detail": f"did not finish within {timeout}s"})
            continue
        except OSError as exc:
            results.append({"suite": rel, "label": label, "status": "error",
                            "detail": str(exc)})
            continue
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        tail = "\n".join(output.splitlines()[-40:])
        if proc.returncode == 0:
            skipped = ("SKIP" in output or "skipped" in output.lower()) and \
                      "OK (skipped" in output
            results.append({
                "suite": rel, "label": label,
                "status": "skipped" if skipped else "passed",
                "detail": tail[-4000:],
            })
        else:
            results.append({"suite": rel, "label": label, "status": "failed",
                            "detail": tail[-4000:],
                            "returncode": proc.returncode})

    shutil.rmtree(sandbox, ignore_errors=True)
    passed = [r for r in results if r["status"] == "passed"]
    failed = [r for r in results if r["status"] in ("failed", "error", "timeout")]
    return {
        # Honest: green needs at least one real pass and zero failures. An
        # all-skipped run is NOT a ready machine.
        "ok": bool(passed) and not failed,
        "passed": len(passed),
        "failed": len(failed),
        "skipped": sum(1 for r in results if r["status"] in ("skipped", "missing")),
        "suites": results,
    }
