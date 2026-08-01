"""
paths.py — where the installed Windows application keeps its things.

Two roots, never mixed:

  APP ROOT   the read-only installed payload (this repository's tree, or the
             PyInstaller bundle directory once frozen). Contains the pipeline,
             the HTML control room, layouts, templates, hero assets, and the
             vendored ffmpeg/ffprobe/yt-dlp binaries. Program Files is not
             writable by a standard user, so NOTHING here is ever written to
             at runtime.

  DATA ROOT  the per-user writable state: databases, downloaded media, logs,
             evidence, quarantine, backups, credentials, settings. On Windows
             that is %LOCALAPPDATA%\\OWCS Comp Tracker; elsewhere it follows
             the XDG data dir. Overridable with OWCS_HOME (used by the tests
             and by the installer's clean-machine smoke test, so no test ever
             touches a real user profile).

There are NO developer-machine paths in this file. Every location is derived
from the interpreter's own position, a documented environment variable, or the
platform's standard per-user directory.

`apply_environment()` is the bridge to the existing pipeline: the pipeline
modules resolve their database and media locations from OWCS_DB /
OWCS_AUTOMATION_DB / OWCS_MEDIA_ROOT *at import time*, so the desktop app
calls this BEFORE importing anything under `pipeline.` — that is what moves
the whole pipeline off the installed (read-only) tree and onto per-user
storage without editing a single pipeline module's defaults.
"""
from __future__ import annotations

import os
import sys

APP_NAME = "OWCS Comp Tracker"
#: Environment variable that relocates the entire writable data root.
HOME_ENV = "OWCS_HOME"

# --------------------------------------------------------------- app root
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle (the installed .exe)."""
    return bool(getattr(sys, "frozen", False))


def app_root() -> str:
    """The installed, read-only payload root.

    Frozen: the directory holding the .exe (PyInstaller onedir), because the
    installer lays the repository payload down beside it. Unfrozen: the
    repository root, two levels up from this file
    (`desktop/owcs_desktop/paths.py`).
    """
    override = os.environ.get("OWCS_APP_ROOT")
    if override:
        return os.path.abspath(override)
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(_THIS_DIR))


def pipeline_dir() -> str:
    return os.path.join(app_root(), "pipeline")


def vendor_dir() -> str:
    """Where the installer puts ffmpeg.exe / ffprobe.exe / yt-dlp.exe."""
    return os.path.join(app_root(), "vendor", "bin")


# -------------------------------------------------------------- data root
def _platform_data_root() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            # A Windows session with no LOCALAPPDATA is broken, but refusing to
            # start is worse than falling back to the profile directory.
            base = os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, APP_NAME)
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library",
                            "Application Support", APP_NAME)
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return os.path.join(xdg, "owcs-comp-tracker")


def data_root() -> str:
    """The per-user writable root. OWCS_HOME wins when set."""
    override = os.environ.get(HOME_ENV)
    if override:
        return os.path.abspath(override)
    return _platform_data_root()


# The writable layout. Every entry is a directory created on demand by
# `ensure_layout()`; nothing else in the app invents a path outside these.
SUBDIRS = {
    "db": "db",
    "media": "media",
    "logs": "logs",
    "reports": "reports",
    "evidence": "evidence",
    "quarantine": "quarantine",
    "backups": "backups",
    "layouts": "layouts",
    "calibration": "calibration",
    "updates": "updates",
    "state": "state",
    "tmp": "tmp",
}


def sub(name: str) -> str:
    """Absolute path of one named writable subdirectory."""
    try:
        leaf = SUBDIRS[name]
    except KeyError:
        raise KeyError(
            f"unknown data subdirectory {name!r}; "
            f"known: {', '.join(sorted(SUBDIRS))}") from None
    return os.path.join(data_root(), leaf)


def ensure_layout() -> str:
    """Create the writable layout if absent and return the data root."""
    root = data_root()
    os.makedirs(root, exist_ok=True)
    for leaf in SUBDIRS.values():
        os.makedirs(os.path.join(root, leaf), exist_ok=True)
    return root


# ------------------------------------------------------------ named files
def content_db() -> str:
    """The staged content database (matches, comps, evidence)."""
    return os.path.join(sub("db"), "owcs.sqlite")


def automation_db() -> str:
    """The automation job/queue database."""
    return os.path.join(sub("db"), "automation.sqlite")


def settings_file() -> str:
    return os.path.join(data_root(), "settings.json")


def credentials_file() -> str:
    return os.path.join(data_root(), "credentials.dat")


def supervisor_log() -> str:
    return os.path.join(sub("logs"), "supervisor.log")


def heartbeat_file() -> str:
    return os.path.join(sub("state"), "supervisor.heartbeat.json")


def single_instance_lock() -> str:
    return os.path.join(sub("state"), "supervisor.lock")


def first_run_marker() -> str:
    """Written by the wizard when setup completes; its absence is what makes
    the first launch show the wizard."""
    return os.path.join(sub("state"), "setup-complete.json")


# --------------------------------------------------------------- seeding
#: Files copied once from the installed payload into the writable root, so a
#: fresh install starts from the shipped milestone database instead of an
#: empty one. Copied only when the destination does not already exist — an
#: upgrade never overwrites the user's own processed data.
SEED_FILES = (
    (os.path.join("data", "owcs.sqlite"), ("db", "owcs.sqlite")),
)


def seed_from_payload(*, app: str | None = None) -> list[str]:
    """Copy the shipped starting data into the per-user root. Returns the
    list of destination paths actually created (empty on an upgrade)."""
    import shutil

    app = app or app_root()
    ensure_layout()
    created: list[str] = []
    for rel_src, (subname, leaf) in SEED_FILES:
        src = os.path.join(app, rel_src)
        dst = os.path.join(sub(subname), leaf)
        if os.path.exists(dst) or not os.path.exists(src):
            continue
        shutil.copy2(src, dst)
        created.append(dst)
    return created


# ---------------------------------------------------------- env plumbing
def apply_environment(*, env: dict | None = None) -> dict:
    """Point the existing pipeline at per-user storage and the vendored tools.

    Mutates `env` (default: os.environ) and returns the keys it set. Call
    before importing any `pipeline.*` module: those modules read these
    variables at import time.

    Existing values are respected — an operator (or a test) who has already
    set OWCS_DB keeps it.
    """
    env = os.environ if env is None else env
    applied: dict[str, str] = {}

    def _set(key: str, value: str) -> None:
        if not env.get(key):
            env[key] = value
            applied[key] = value

    ensure_layout()
    _set("OWCS_DB", content_db())
    _set("OWCS_AUTOMATION_DB", automation_db())
    _set("OWCS_MEDIA_ROOT", sub("media"))

    # Put the vendored binaries at the FRONT of PATH so the app always uses
    # the versions it shipped with, never whatever unrelated ffmpeg happens to
    # be installed. Skipped when the directory is absent (running from source).
    vend = vendor_dir()
    if os.path.isdir(vend):
        current = env.get("PATH", "")
        parts = current.split(os.pathsep) if current else []
        if vend not in parts:
            env["PATH"] = os.pathsep.join([vend] + parts)
            applied["PATH"] = env["PATH"]
    return applied


def describe() -> dict:
    """A support-friendly snapshot of every resolved location."""
    return {
        "appName": APP_NAME,
        "frozen": is_frozen(),
        "appRoot": app_root(),
        "dataRoot": data_root(),
        "vendorDir": vendor_dir(),
        "contentDb": content_db(),
        "automationDb": automation_db(),
        "settings": settings_file(),
        "logs": sub("logs"),
        "media": sub("media"),
        "backups": sub("backups"),
        "quarantine": sub("quarantine"),
    }
