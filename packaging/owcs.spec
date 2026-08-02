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

REPO = os.path.abspath(".")

a = Analysis(
    [os.path.join(REPO, "desktop", "owcs_app.py")],
    pathex=[REPO, os.path.join(REPO, "desktop"), os.path.join(REPO, "pipeline")],
    binaries=[],
    datas=collect_datas(REPO),
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OWCSCompTracker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX-packed binaries trip more AV engines than they save
    # windowed=True: no console window when the tray app or the service
    # starts. Diagnostics go to the log file the control room shows, which is
    # where a non-technical user can actually find them.
    console=False,
    icon=os.path.join(REPO, "desktop", "assets", "owcs.ico"),
    version=os.path.join(REPO, "packaging", "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="OWCSCompTracker",
)
