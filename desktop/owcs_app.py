#!/usr/bin/env python3
"""
owcs_app.py — the one entrypoint. Everything the user can start goes through
here, and so does everything PyInstaller freezes.

    OWCSCompTracker.exe                 tray application (the default)
    OWCSCompTracker.exe --tray          same, explicitly (what autostart uses)
    OWCSCompTracker.exe --service       the background processing service
    OWCSCompTracker.exe --control-room  the local control-room server only
    OWCSCompTracker.exe --setup         open the first-run wizard
    OWCSCompTracker.exe --check         run the health checks and print them
    OWCSCompTracker.exe --readiness     run the real end-to-end readiness test
    OWCSCompTracker.exe --repair <id>   run one repair action
    OWCSCompTracker.exe --version       print the version

A single executable with subcommands (rather than several .exe files) is what
keeps the autostart registration, the tray's child spawning and the installer's
shortcuts all pointing at one path — an upgrade that moves the install
directory only has one thing to fix.

`--check`, `--readiness` and `--repair` exist for the packaging smoke test,
which has to verify a clean machine can actually run the thing without a
desktop session. They are not the supported way to use the app; the UI is.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Make the desktop package importable however this file was started: frozen,
# `python desktop/owcs_app.py`, or from another working directory.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_REPO = os.path.dirname(_HERE)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from owcs_desktop import __version__, paths  # noqa: E402


def _force_utf8_io() -> None:
    """Make this process's stdout/stderr able to print any text at all.

    A frozen console application on Windows gets a pipe for stdout, and
    Python picks the ANSI code page for it — cp1252 on an English install.
    That encoding has no U+2192, so the readiness test crashed with a
    UnicodeEncodeError while printing its own suite label ("capture → detect")
    *after* the pipeline had run perfectly. The user saw a machine that could
    not process a broadcast; what actually happened is that a status line
    could not be spelled.

    Nothing may depend on the console's code page. `errors="replace"` keeps
    that true even for a stream that genuinely cannot be reconfigured: text
    is worth degrading, never worth crashing over.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            # Not a reconfigurable TextIOWrapper (pythonw with no console,
            # a redirected null device, a test double). Leave it alone.
            pass


_force_utf8_io()


def _prepare() -> None:
    """Everything every mode needs: writable layout, environment, payload."""
    paths.ensure_layout()
    paths.apply_environment()
    paths.seed_from_payload()
    app_root = paths.app_root()
    if app_root not in sys.path:
        sys.path.insert(0, app_root)


def _mode_tray(args: argparse.Namespace) -> int:
    from owcs_desktop.tray import TrayApp
    return TrayApp().run(headless=args.headless)


def _mode_service(args: argparse.Namespace) -> int:
    from owcs_desktop import supervisor
    return supervisor.main(
        ["--max-iterations", str(args.max_iterations)]
        if args.max_iterations else [])


def _mode_control_room(args: argparse.Namespace) -> int:
    from owcs_desktop.settings import Settings
    sys.path.insert(0, os.path.join(paths.app_root(), "pipeline"))
    import serve  # noqa: WPS433
    port = args.port or int(Settings().get("controlRoomPort"))
    return serve.main(["--port", str(port), "--host", "127.0.0.1"])


def _mode_setup(_args: argparse.Namespace) -> int:
    """Start the app and land on the wizard rather than the control room."""
    import webbrowser
    from owcs_desktop.settings import Settings
    from owcs_desktop.tray import TrayApp, wait_for_control_room

    app = TrayApp()
    app.children.start_all()
    port = int(Settings().get("controlRoomPort"))
    if not wait_for_control_room(port, timeout=45):
        print("The control room did not start. Run --check to see why.",
              file=sys.stderr)
        return 2
    webbrowser.open(f"http://127.0.0.1:{port}/setup.html")
    app.children.run(interval=5.0)
    return 0


def _mode_check(args: argparse.Namespace) -> int:
    from owcs_desktop import health
    report = health.run_checks()
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        for check in report["checks"]:
            mark = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}[check["status"]]
            print(f"{mark} {check['label']}: {check['detail']}")
        counts = report["counts"]
        print(f"\n{counts['ok']} ok, {counts['warn']} warning(s), "
              f"{counts['fail']} failure(s)")
    return 0 if report["ok"] else 1


def _mode_readiness(args: argparse.Namespace) -> int:
    from owcs_desktop import health
    report = health.run_readiness_test(timeout=args.timeout)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        for suite in report["suites"]:
            print(f"{suite['status'].upper():8} {suite['label']}")
            if suite["status"] in ("failed", "error", "timeout"):
                print("\n".join("    " + ln
                                for ln in suite["detail"].splitlines()[-20:]))
        print(f"\n{report['passed']} passed, {report['failed']} failed, "
              f"{report['skipped']} skipped")
    return 0 if report["ok"] else 1


def _mode_repair(args: argparse.Namespace) -> int:
    from owcs_desktop import repair
    if args.repair == "list":
        for action in repair.list_actions():
            print(f"{action['id']:28} {action['label']} — {action['help']}")
        return 0
    result = repair.run(args.repair)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


def _mode_stop_service(_args: argparse.Namespace) -> int:
    """Signal the service to stop and wait for its heartbeat to go quiet.

    The installer runs this before replacing files: a running supervisor holds
    ffmpeg.exe and the bundled Python open, and an upgrade that tried to
    overwrite them would fail halfway through with the app in pieces.
    """
    import time
    from owcs_desktop import supervisor

    supervisor.request_stop()
    deadline = time.time() + 45
    while time.time() < deadline:
        beat = supervisor.read_heartbeat()
        if beat is None or beat.get("stale") or not beat.get("running"):
            print("The background service has stopped.")
            return 0
        time.sleep(1.0)
    print("The background service did not stop within 45 seconds.",
          file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="OWCSCompTracker",
        description="OWCS Comp Tracker — the desktop application")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--tray", action="store_true",
                      help="run the tray application (default)")
    mode.add_argument("--service", action="store_true",
                      help="run the background processing service")
    mode.add_argument("--control-room", action="store_true",
                      help="run the local control-room server only")
    mode.add_argument("--setup", action="store_true",
                      help="open the first-run setup wizard")
    mode.add_argument("--check", action="store_true",
                      help="print the system health checks and exit")
    mode.add_argument("--readiness", action="store_true",
                      help="run the real end-to-end readiness test and exit")
    mode.add_argument("--repair", metavar="ACTION",
                      help="run one repair action ('list' to see them)")
    mode.add_argument("--stop-service", action="store_true",
                      help="ask the background service to stop and wait for it "
                           "(used by the installer before replacing files)")
    parser.add_argument("--version", action="version",
                        version=f"OWCS Comp Tracker {__version__}")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable output for --check/--readiness")
    parser.add_argument("--port", type=int, default=None,
                        help="control-room port override")
    parser.add_argument("--headless", action="store_true",
                        help="tray mode without a GUI (supervise children only)")
    parser.add_argument("--max-iterations", type=int, default=None,
                        help="bound the service loop (testing)")
    parser.add_argument("--timeout", type=int, default=1800,
                        help="per-suite timeout for --readiness")
    return parser


def _run_script(argv: list[str]) -> int:
    """`--run-script <path> [args...]` — execute a bundled Python script.

    The frozen application IS the interpreter: there is no python.exe in the
    bundle, so this is how the readiness test, the public export, the FACEIT
    ingest and the control room's job runner start a pipeline script. The
    script sees a normal `sys.argv` and a normal `__main__`, so nothing in
    `pipeline/` needs to know it is running inside a frozen app.

    Handled before argparse, deliberately: the script's own flags
    (`--source`, `--start`, …) must reach the script, not this parser.
    """
    import runpy

    if not argv:
        print("--run-script needs a script path", file=sys.stderr)
        return 2
    script, script_args = argv[0], argv[1:]
    if not os.path.isabs(script):
        script = os.path.join(paths.app_root(), script)
    if not os.path.isfile(script):
        print(f"--run-script: no such script: {script}", file=sys.stderr)
        return 2

    _prepare()
    sys.argv = [script] + list(script_args)
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit as exit_code:
        return int(exit_code.code or 0)
    return 0


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "--run-script":
        return _run_script(raw[1:])

    args = build_parser().parse_args(raw)
    _prepare()

    if args.service:
        return _mode_service(args)
    if args.control_room:
        return _mode_control_room(args)
    if args.setup:
        return _mode_setup(args)
    if args.check:
        return _mode_check(args)
    if args.readiness:
        return _mode_readiness(args)
    if args.repair:
        return _mode_repair(args)
    if args.stop_service:
        return _mode_stop_service(args)
    return _mode_tray(args)


if __name__ == "__main__":
    raise SystemExit(main())
