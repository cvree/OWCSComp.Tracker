#!/usr/bin/env python3
"""
build_windows.py — one command from a checkout to a signed-shaped installer.

    python packaging/build_windows.py                # everything
    python packaging/build_windows.py --skip-vendor  # reuse vendor/bin
    python packaging/build_windows.py --stage exe    # stop after PyInstaller

Stages, in order:

  1. **preflight**  — the payload is complete, the icon matches its generator,
     the version resource matches `owcs_desktop.__version__`. A build that
     would ship without hero templates fails here rather than three steps
     later on a user's machine.
  2. **vendor**     — fetch ffmpeg, ffprobe and yt-dlp into `vendor/bin`.
  3. **exe**        — PyInstaller onedir build.
  4. **verify**     — run the frozen executable's own `--check`, on the build
     agent, before it is wrapped. This is what catches a missing hidden
     import: `--check` imports OpenCV, resolves the vendored binaries and
     reads the layouts, so an exe that cannot do its job cannot be shipped.
  5. **installer**  — Inno Setup.
  6. **checksums**  — SHA256SUMS beside the installer, which is what the
     application's own updater verifies a download against.

Every stage prints what it did and fails loudly. Nothing is skipped silently.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DIST = os.path.join(REPO, "dist")
BUNDLE = os.path.join(DIST, "OWCSCompTracker")
INSTALLER_DIR = os.path.join(DIST, "installer")

STAGES = ("preflight", "vendor", "exe", "verify", "installer", "checksums")


def log(stage: str, message: str) -> None:
    print(f"[build:{stage}] {message}", flush=True)


def run(cmd: list[str], *, cwd: str = REPO, stage: str = "run") -> None:
    log(stage, "$ " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=cwd)
    if proc.returncode != 0:
        raise SystemExit(f"[build:{stage}] failed with exit code {proc.returncode}")


def app_version() -> str:
    sys.path.insert(0, os.path.join(REPO, "desktop"))
    import owcs_desktop
    return owcs_desktop.__version__


# ------------------------------------------------------------- preflight
def stage_preflight() -> None:
    sys.path.insert(0, HERE)
    import payload

    missing = payload.missing_required(REPO)
    if missing:
        raise SystemExit(
            "[build:preflight] required payload is missing, so the build "
            "would produce an application that cannot run:\n  - "
            + "\n  - ".join(missing))
    log("preflight", f"payload complete ({len(payload.PAYLOAD)} entries)")

    icon_check = subprocess.run(
        [sys.executable, os.path.join(REPO, "desktop", "build_icon.py"), "--check"],
        cwd=REPO, capture_output=True, text=True)
    if icon_check.returncode != 0:
        raise SystemExit("[build:preflight] the committed icon does not match "
                         "desktop/build_icon.py:\n" + icon_check.stdout)
    log("preflight", "icon matches its generator")

    version = app_version()
    sync_version_resource(version)
    log("preflight", f"version {version}")


def sync_version_resource(version: str) -> None:
    """Rewrite version_info.txt so the exe's Windows metadata is never stale."""
    path = os.path.join(HERE, "version_info.txt")
    parts = [int(p) for p in version.split(".")[:3]]
    while len(parts) < 3:
        parts.append(0)
    quad = tuple(parts + [0])
    dotted = ".".join(str(n) for n in quad)
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r"filevers=\([\d, ]+\)", f"filevers={quad}", text)
    text = re.sub(r"prodvers=\([\d, ]+\)", f"prodvers={quad}", text)
    text = re.sub(r"StringStruct\('FileVersion', '[^']*'\)",
                  f"StringStruct('FileVersion', '{dotted}')", text)
    text = re.sub(r"StringStruct\('ProductVersion', '[^']*'\)",
                  f"StringStruct('ProductVersion', '{dotted}')", text)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ---------------------------------------------------------------- vendor
def stage_vendor(skip: bool = False) -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "fetch_vendor", os.path.join(HERE, "fetch_vendor.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)      # type: ignore[union-attr]

    if skip:
        report = module.verify()
        bad = {n: i for n, i in report.items() if not i["ok"]}
        if bad:
            raise SystemExit(f"[build:vendor] --skip-vendor was used but the "
                             f"binaries are not usable: {bad}")
        log("vendor", "reusing the existing vendor/bin")
        return
    if module.main([]) != 0:
        raise SystemExit("[build:vendor] could not fetch the bundled binaries")


# ------------------------------------------------------------------- exe
def stage_exe() -> None:
    if os.path.isdir(BUNDLE):
        shutil.rmtree(BUNDLE, ignore_errors=True)
    run([sys.executable, "-m", "PyInstaller",
         os.path.join("packaging", "owcs.spec"), "--noconfirm",
         "--distpath", DIST, "--workpath", os.path.join(DIST, "build")],
        stage="exe")
    suffix = ".exe" if os.name == "nt" else ""
    for name in ("OWCSCompTracker", "OWCSCompTracker-cli"):
        produced = os.path.join(BUNDLE, name + suffix)
        if not os.path.exists(produced):
            raise SystemExit(f"[build:exe] PyInstaller did not produce {produced}")
    size = sum(
        os.path.getsize(os.path.join(root, name))
        for root, _dirs, files in os.walk(BUNDLE) for name in files)
    log("exe", f"bundle built: {size / (1024 ** 2):.0f} MB")


# ---------------------------------------------------------------- verify
def stage_verify() -> None:
    """Run the frozen application's own checks, here, before shipping it."""
    # The CONSOLE twin. A GUI-subsystem binary is not awaited by the caller
    # and its exit code cannot be read, so verifying with it would report
    # success no matter what the application actually said.
    suffix = ".exe" if os.name == "nt" else ""
    exe = os.path.join(BUNDLE, "OWCSCompTracker-cli" + suffix)
    if not os.path.exists(exe):
        raise SystemExit(f"[build:verify] the console companion is missing: {exe}")
    windowed = os.path.join(BUNDLE, "OWCSCompTracker" + suffix)
    if not os.path.exists(windowed):
        raise SystemExit(f"[build:verify] the main executable is missing: {windowed}")

    # A throwaway data root so the build agent's own profile is untouched and
    # the checks see a genuinely fresh installation.
    sandbox = os.path.join(DIST, "verify-home")
    shutil.rmtree(sandbox, ignore_errors=True)
    os.makedirs(sandbox, exist_ok=True)
    env = dict(os.environ, OWCS_HOME=sandbox)

    log("verify", "--version")
    version = subprocess.run([exe, "--version"], capture_output=True,
                             text=True, env=env, timeout=120)
    if version.returncode != 0:
        raise SystemExit("[build:verify] the frozen exe cannot even report its "
                         f"version:\n{version.stderr}")
    log("verify", version.stdout.strip())

    log("verify", "--check (imports OpenCV, resolves ffmpeg, reads layouts)")
    check = subprocess.run([exe, "--check"], capture_output=True, text=True,
                           env=env, timeout=600)
    print(check.stdout)
    if check.returncode != 0:
        raise SystemExit(
            "[build:verify] the frozen application reports failing checks, so "
            f"it would not work on a user's machine:\n{check.stdout}\n{check.stderr}")
    log("verify", "the frozen application passes its own health checks")


# ------------------------------------------------------------- installer
def find_iscc() -> str:
    found = shutil.which("iscc") or shutil.which("ISCC")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.environ.get("ProgramFiles", r"C:\Program Files")):
        candidate = os.path.join(base, "Inno Setup 6", "ISCC.exe")
        if os.path.exists(candidate):
            return candidate
    raise SystemExit(
        "[build:installer] Inno Setup 6 (ISCC.exe) was not found. Install it "
        "with `choco install innosetup` or from https://jrsoftware.org/isdl.php")


def stage_installer(version: str) -> str:
    os.makedirs(INSTALLER_DIR, exist_ok=True)
    run([find_iscc(), f"/DAppVersion={version}",
         os.path.join("packaging", "installer.iss")], stage="installer")
    expected = os.path.join(INSTALLER_DIR,
                            f"OWCSCompTracker-{version}-Setup.exe")
    if not os.path.exists(expected):
        raise SystemExit(f"[build:installer] expected {expected}")
    log("installer", f"{expected} "
                     f"({os.path.getsize(expected) / (1024 ** 2):.0f} MB)")
    return expected


# ------------------------------------------------------------- checksums
def stage_checksums() -> str:
    """SHA256SUMS — what the in-app updater verifies a download against."""
    lines = []
    for name in sorted(os.listdir(INSTALLER_DIR)):
        if not name.lower().endswith(".exe"):
            continue
        path = os.path.join(INSTALLER_DIR, name)
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        lines.append(f"{h.hexdigest()}  {name}")
        log("checksums", lines[-1])
    target = os.path.join(INSTALLER_DIR, "SHA256SUMS")
    with open(target, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return target


# ------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-vendor", action="store_true",
                        help="reuse vendor/bin instead of re-downloading")
    parser.add_argument("--stage", choices=STAGES,
                        help="stop after this stage")
    args = parser.parse_args(argv)

    started = time.time()
    version = app_version()

    def done(stage: str) -> bool:
        return args.stage == stage

    stage_preflight()
    if done("preflight"):
        return 0
    stage_vendor(skip=args.skip_vendor)
    if done("vendor"):
        return 0
    stage_exe()
    if done("exe"):
        return 0
    stage_verify()
    if done("verify"):
        return 0
    installer = stage_installer(version)
    if done("installer"):
        return 0
    checksums = stage_checksums()

    print()
    log("done", f"installer:  {installer}")
    log("done", f"checksums:  {checksums}")
    log("done", f"took {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
