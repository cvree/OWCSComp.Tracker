"""
payload.py — the single definition of what ships inside the application.

Both the PyInstaller spec and `pipeline/test_windows_packaging.py` import
this. That is the point: a file the build includes but the test does not know
about (or vice versa) is exactly the class of packaging bug that only shows up
on a user's clean machine, and sharing one list makes it impossible.

`PAYLOAD` names directories and files, relative to the repository root, that
must exist inside the installed application. Each entry says *why* it is
there, because "is this still needed?" is otherwise unanswerable a year later.
"""
from __future__ import annotations

import os

#: (relative path, required, reason)
PAYLOAD: tuple[tuple[str, bool, str], ...] = (
    # ---- the pipeline itself -------------------------------------------
    ("pipeline", True,
     "the whole detection/calibration/ingest pipeline, run as scripts by the "
     "supervisor and imported by the desktop layer"),
    ("desktop/owcs_desktop", True,
     "the application layer (settings, credentials, supervisor, web API)"),
    ("desktop/assets", True, "the application icon, used by the tray"),

    # ---- what detection cannot run without ------------------------------
    ("layouts", True,
     "calibrated HUD layout profiles plus their marker crops — without these "
     "no broadcast can be read"),
    ("templates", True,
     "per-broadcast hero portrait template sets — without these no hero can "
     "be identified"),
    ("config", True,
     "the operator config and the verified competition/channel registries"),
    ("data/heroes_aliases.json", True, "hero id normalisation table"),
    ("data/owcs.sqlite", True,
     "the shipped results database, seeded into per-user storage on first "
     "run so a fresh install opens on real data instead of an empty page"),
    ("data/sources", True, "the committed source lists the pipeline reads"),

    # ---- the control room and the public site ---------------------------
    ("assets/css", True, "site and application styling"),
    ("assets/js", True, "site and application scripts"),
    ("assets/data", True, "the public dataset the site renders"),
    ("assets/img", True, "hero portraits, team marks, icons"),
    ("assets/vendor", False, "vendored motion libraries, when present"),
    ("reports", False,
     "committed milestone evidence — large, and the app works without it, so "
     "it ships only when present"),
    ("docs", False, "the operator documentation the control room links to"),

    # ---- requirements, for the source-mode dependency repair ------------
    ("requirements.txt", True, "used by the dependency repair on source runs"),
)

#: Every top-level .html page is part of the control room / site payload.
HTML_GLOB = "*.html"

#: Imports PyInstaller's static analysis cannot see, because the pipeline
#: launches these as subprocesses or imports them by name at runtime.
HIDDEN_IMPORTS = [
    "cv2", "numpy",
    # NOT yt_dlp. The pipeline shells out to the `yt-dlp` BINARY (vendored as
    # vendor/bin/yt-dlp.exe), and nothing in this codebase imports the Python
    # package — the one textual reference, ytdlp_opts.ytdlp_module_version,
    # runs `python -c "import yt_dlp"` in a subprocess purely to DIAGNOSE a
    # version mismatch and returns None when it is absent. Listing it here
    # dragged in ~2,000 extractor modules plus the cryptography stack, which
    # added hundreds of megabytes to the installer to ship a second, unused
    # copy of a tool already bundled as an executable.
    "sqlite3", "urllib.request", "http.server", "webbrowser",
    "tkinter", "tkinter.ttk",
    "owcs_desktop", "owcs_desktop.supervisor", "owcs_desktop.tray",
    "owcs_desktop.webapi", "owcs_desktop.intake", "owcs_desktop.health",
    "owcs_desktop.repair", "owcs_desktop.updates", "owcs_desktop.backup",
    "owcs_desktop.storage", "owcs_desktop.credentials",
    "owcs_desktop.autostart", "owcs_desktop.settings", "owcs_desktop.paths",
]

#: Left out on purpose — large, and nothing in the shipped code path imports
#: them. Excluding them keeps the installer a few hundred MB smaller.
EXCLUDES = [
    "matplotlib", "scipy", "pandas", "PyQt5", "PyQt6", "PySide2", "PySide6",
    "IPython", "jupyter", "notebook", "pytest", "setuptools._distutils",
    "easyocr", "paddleocr", "paddle", "torch", "torchvision",
    # See HIDDEN_IMPORTS: yt-dlp ships as the vendored binary, so its Python
    # package (and the cryptography stack its extractors pull in) has no
    # reason to be in the bundle.
    "yt_dlp", "cryptography",
]


def payload_entries(repo_root: str) -> list[tuple[str, str, bool]]:
    """[(absolute source, destination relative to the app root, required)]."""
    entries = []
    for rel, required, _reason in PAYLOAD:
        src = os.path.join(repo_root, rel.replace("/", os.sep))
        entries.append((src, rel.replace("/", os.sep), required))
    return entries


def collect_datas(repo_root: str) -> list[tuple[str, str]]:
    """PyInstaller `datas`: [(source, destination directory)].

    Skips optional entries that are absent rather than failing the build, and
    raises loudly on a missing required one — a build that quietly shipped
    without the hero templates would produce an application that installs
    perfectly and can never identify a hero.
    """
    datas: list[tuple[str, str]] = []
    for src, dest, required in payload_entries(repo_root):
        if not os.path.exists(src):
            if required:
                raise FileNotFoundError(
                    f"required payload missing: {dest}. The build would "
                    f"produce an application that cannot run.")
            continue
        if os.path.isdir(src):
            datas.append((src, dest))
        else:
            datas.append((src, os.path.dirname(dest) or "."))

    # Every control-room and public page at the repository root.
    import glob
    for page in sorted(glob.glob(os.path.join(repo_root, HTML_GLOB))):
        datas.append((page, "."))

    # The vendored binaries, when fetch_vendor.py has run.
    vendor = os.path.join(repo_root, "vendor", "bin")
    if os.path.isdir(vendor):
        datas.append((vendor, os.path.join("vendor", "bin")))
    return datas


def missing_required(repo_root: str) -> list[str]:
    """Required payload entries that are not present. Empty means buildable."""
    return [dest for src, dest, required in payload_entries(repo_root)
            if required and not os.path.exists(src)]
