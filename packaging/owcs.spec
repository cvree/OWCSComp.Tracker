# -*- mode: python ; coding: utf-8 -*-
"""
owcs.spec — the PyInstaller build of OWCSCompTracker.exe.

Produces a **onedir** bundle (not onefile). That is deliberate: a onefile
build unpacks the whole of OpenCV to a temporary directory on every launch,
which for a tray application that starts at sign-in is both slow and a
reliable way to trip antivirus heuristics. Onedir starts instantly and the
installer is what makes it feel like one file to the user.

What ends up inside `dist/OWCSCompTracker/`:

  * CPython, OpenCV, NumPy and yt-dlp (the last as an importable module *and*
    as the `yt-dlp` console script, because the pipeline shells out to the
    binary by name);
  * the repository payload — `pipeline/`, the HTML control room, `assets/`,
    `layouts/`, `templates/`, `config/`, `docs/` — laid down beside the exe so
    `paths.app_root()` finds it in exactly the same shape as a source
    checkout. This is why one code path serves both.
  * `vendor/bin/` — ffmpeg.exe and ffprobe.exe, fetched by
    `packaging/fetch_vendor.py` before the build.

The data payload is enumerated by `packaging/payload.py`, which is shared
with the packaging test, so the test cannot drift from the build.

Build:  pyinstaller packaging/owcs.spec --noconfirm
"""
import os
import sys

sys.path.insert(0, os.path.abspath("packaging"))
from payload import collect_datas, HIDDEN_IMPORTS, EXCLUDES  # noqa: E402
from PyInstaller.utils.hooks import collect_submodules  # noqa: E402

REPO = os.path.abspath(".")

# NumPy 2.x reaches into its own submodules lazily, so PyInstaller's static
# analysis misses some of them and the frozen build dies at import with
# "No module named 'numpy._core._exceptions'" — which then takes OpenCV down
# with it, since cv2 imports numpy. Collecting the package wholesale is the
# supported fix and costs a few MB. Caught by build_windows.py's verify stage
# running the frozen exe's own --check before the installer was wrapped.
_HIDDEN = HIDDEN_IMPORTS + collect_submodules("numpy")

a = Analysis(
    [os.path.join(REPO, "desktop", "owcs_app.py")],
    pathex=[REPO, os.path.join(REPO, "desktop"), os.path.join(REPO, "pipeline")],
    binaries=[],
    datas=collect_datas(REPO),
    hiddenimports=_HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

# TWO executables from one Analysis, for the same reason Python ships both
# python.exe and pythonw.exe.
#
# A GUI-subsystem (`console=False`) binary is what the tray app and the
# background service must be: anything else flashes a console window at every
# sign-in. But Windows does not attach such a binary to the calling console
# and the shell does not WAIT for it, so `& app.exe --check; $LASTEXITCODE`
# is meaningless — the clean-machine CI job watched `--version` print the
# right answer and then reported failure, because PowerShell had already moved
# on and was reading a stale exit code.
#
# That is not only a CI problem. `--check`, `--readiness`, `--repair` and
# `--stop-service` are real modes: the uninstaller depends on --stop-service
# actually finishing, and a support conversation depends on --check printing
# something and exiting non-zero when it fails. Those need a console binary.
#
# Same Analysis, so the interpreter, OpenCV, NumPy and the payload are
# collected once and shared; only a second small bootloader is added.
_COMMON = dict(
    exclude_binaries=True,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX-packed binaries trip more AV engines than they save
    icon=os.path.join(REPO, "desktop", "assets", "owcs.ico"),
    version=os.path.join(REPO, "packaging", "version_info.txt"),
)

#: What a user double-clicks, what autostart runs, what the tray and the
#: background service are. No console window, ever.
exe_gui = EXE(pyz, a.scripts, [], name="OWCSCompTracker",
              console=False, **_COMMON)

#: The same application, console subsystem: real stdout, and a shell that
#: waits for it and sees its exit code. Used by the installer, the CI
#: verification, and anyone diagnosing a problem.
exe_cli = EXE(pyz, a.scripts, [], name="OWCSCompTracker-cli",
              console=True, **_COMMON)

coll = COLLECT(
    exe_gui,
    exe_cli,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="OWCSCompTracker",
)
