"""
tray.py — the thing that is actually running when the user is not looking.

The tray application owns two child processes and nothing else:

  * the **control room** (`pipeline/serve.py` bound to 127.0.0.1), which is the
    UI, and
  * the **supervisor** (`owcs_desktop.supervisor`), which is the work.

Both are children, both are restarted if they die, and neither is required for
the other to function — closing the control room window does not stop
processing, because the window was only ever a browser tab pointed at a
separate process. That is the whole design: the UI is a viewer, the service is
the appliance.

Graphical layers degrade rather than fail:

  1. `pystray` — a real system-tray icon with a menu. What the installed build
     ships and uses.
  2. `tkinter` — a small always-available control window, used when a tray is
     unavailable (some remote-desktop sessions have no notification area).
  3. headless — supervise the children and log. Used by the packaging smoke
     test, which runs on a build agent with no desktop at all, and is why the
     supervision logic is testable without a GUI.

`ProcessSupervisor` holds all of the restart logic and knows nothing about
GUIs, so the tests drive crash-and-restart directly.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from typing import Any, Callable

from . import paths, supervisor as svc
from .settings import Settings

ICON_ICO = os.path.join("desktop", "assets", "owcs.ico")
ICON_PNG = os.path.join("desktop", "assets", "owcs.png")

#: Give up restarting a child that dies this many times in a row, and say so,
#: rather than spinning forever on a broken install.
MAX_RESTARTS = 5
RESTART_BACKOFF = (2, 5, 15, 30, 60)


def icon_path(kind: str = "png") -> str:
    return os.path.join(paths.app_root(),
                        ICON_ICO if kind == "ico" else ICON_PNG)


# ------------------------------------------------------- child processes
class Child:
    """One supervised child process."""

    def __init__(self, name: str, command: list[str], *,
                 env: dict[str, str] | None = None,
                 spawn: Callable[..., Any] | None = None):
        self.name = name
        self.command = command
        self.env = env
        self.spawn = spawn or subprocess.Popen
        self.process: Any = None
        self.restarts = 0
        self.last_error: str | None = None
        self.gave_up = False

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> bool:
        kwargs: dict[str, Any] = {
            "cwd": paths.app_root(),
            "env": self.env or os.environ.copy(),
            "close_fds": True,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        try:
            self.process = self.spawn(self.command, **kwargs)
        except OSError as exc:
            self.last_error = f"{self.name}: {exc}"
            return False
        return True

    def stop(self, *, timeout: float = 10.0) -> None:
        if not self.alive():
            return
        try:
            self.process.terminate()
            self.process.wait(timeout=timeout)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass

    def status(self) -> dict[str, Any]:
        return {"name": self.name, "running": self.alive(),
                "pid": getattr(self.process, "pid", None),
                "restarts": self.restarts, "gaveUp": self.gave_up,
                "lastError": self.last_error}


class ProcessSupervisor:
    """Keeps a set of children alive. GUI-free, so the tests can drive it."""

    def __init__(self, children: list[Child], *,
                 log: Callable[[str], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.children = children
        self._log = log or (lambda m: print(f"[tray] {m}", flush=True))
        self._sleep = sleep
        self._stop = threading.Event()

    def start_all(self) -> None:
        for child in self.children:
            if child.start():
                self._log(f"started {child.name} "
                          f"(pid {getattr(child.process, 'pid', '?')})")
            else:
                self._log(f"could not start {child.name}: {child.last_error}")

    def check_once(self) -> list[dict[str, Any]]:
        """One supervision pass: restart whatever died, with backoff."""
        events = []
        for child in self.children:
            if child.alive() or child.gave_up:
                continue
            code = getattr(child.process, "returncode", None)
            child.restarts += 1
            if child.restarts > MAX_RESTARTS:
                child.gave_up = True
                child.last_error = (
                    f"stopped after {MAX_RESTARTS} failed restarts "
                    f"(last exit code {code})")
                self._log(f"{child.name}: {child.last_error}")
                events.append({"child": child.name, "action": "gave-up",
                               "exit": code})
                continue
            delay = RESTART_BACKOFF[min(child.restarts - 1,
                                        len(RESTART_BACKOFF) - 1)]
            self._log(f"{child.name} exited ({code}); restarting in {delay}s "
                      f"(attempt {child.restarts})")
            self._sleep(delay)
            child.start()
            events.append({"child": child.name, "action": "restarted",
                           "attempt": child.restarts, "exit": code})
        return events

    def run(self, *, interval: float = 5.0,
            max_iterations: int | None = None) -> int:
        self.start_all()
        iterations = 0
        while not self._stop.is_set():
            self._sleep(interval)
            self.check_once()
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            if all(c.gave_up for c in self.children):
                self._log("every child has given up — exiting")
                break
        return iterations

    def stop(self) -> None:
        self._stop.set()
        for child in self.children:
            child.stop()

    def status(self) -> list[dict[str, Any]]:
        return [c.status() for c in self.children]


# ------------------------------------------------------------- the app
def child_environment() -> dict[str, str]:
    """The environment both children inherit: per-user storage, the vendored
    binaries, and any saved API keys."""
    from . import credentials as cred
    env = dict(os.environ)
    paths.apply_environment(env=env)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in [os.path.join(paths.app_root(), "desktop"),
                    paths.app_root(), env.get("PYTHONPATH", "")] if p)
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        cred.CredentialVault().export_environment(env)
    except cred.CredentialError:
        pass  # a broken vault must not stop the service from starting
    return env


def control_room_command(port: int) -> list[str]:
    if paths.is_frozen():
        return [os.path.abspath(sys.executable), "--control-room",
                "--port", str(port)]
    return [os.path.abspath(sys.executable),
            os.path.join(paths.app_root(), "pipeline", "serve.py"),
            "--port", str(port), "--host", "127.0.0.1"]


def supervisor_command() -> list[str]:
    if paths.is_frozen():
        return [os.path.abspath(sys.executable), "--service"]
    return [os.path.abspath(sys.executable), "-m", "owcs_desktop.supervisor"]


def wait_for_control_room(port: int, *, timeout: float = 30.0,
                          opener: Callable[..., Any] | None = None) -> bool:
    """Poll the control room's ping until it answers."""
    open_fn = opener or urllib.request.urlopen
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/api/ping"
    while time.time() < deadline:
        try:
            with open_fn(url, timeout=2) as response:
                if getattr(response, "status", 200) == 200:
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.4)
    return False


class TrayApp:
    def __init__(self, *, settings: Settings | None = None,
                 open_browser: Callable[[str], Any] | None = None,
                 spawn: Callable[..., Any] | None = None):
        self.settings = settings or Settings()
        self.port = int(self.settings.get("controlRoomPort"))
        self._open = open_browser or webbrowser.open
        env = child_environment()
        self.children = ProcessSupervisor([
            Child("control-room", control_room_command(self.port),
                  env=env, spawn=spawn),
            Child("service", supervisor_command(), env=env, spawn=spawn),
        ])

    # ------------------------------------------------------------- URLs
    def url(self, page: str = "control-room.html") -> str:
        return f"http://127.0.0.1:{self.port}/{page}"

    def first_run(self) -> bool:
        return not os.path.exists(paths.first_run_marker())

    def landing_page(self) -> str:
        return "setup.html" if self.first_run() else "control-room.html"

    # ---------------------------------------------------------- actions
    def open_control_room(self) -> None:
        self._open(self.url(self.landing_page()))

    def open_setup(self) -> None:
        self._open(self.url("setup.html"))

    def open_review(self) -> None:
        self._open(self.url("control-room.html#review"))

    def open_storage_folder(self) -> None:
        root = paths.data_root()
        try:
            if sys.platform == "win32":
                os.startfile(root)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", root])
            else:
                subprocess.Popen(["xdg-open", root])
        except (OSError, AttributeError):
            self._open("file://" + root)

    def pause_processing(self) -> None:
        svc.request_stop()

    def resume_processing(self) -> None:
        svc.clear_stop()
        from . import repair
        repair.run("repair.start-worker")

    def status_line(self) -> str:
        beat = svc.read_heartbeat()
        if beat is None:
            return "Service: not running"
        if beat.get("stale"):
            return "Service: stopped"
        if beat.get("currentJob"):
            return f"Service: processing {beat['currentJob']}"
        return f"Service: idle — {beat.get('idleReason') or 'waiting for work'}"

    # ------------------------------------------------------------- run
    def start_children(self) -> None:
        self.children.start_all()
        if wait_for_control_room(self.port, timeout=30):
            self.open_control_room()

    def run(self, *, headless: bool = False) -> int:
        paths.apply_environment()
        paths.seed_from_payload()
        self.start_children()
        if headless:
            self.children.run(interval=5.0)
            return 0
        try:
            return self._run_pystray()
        except ImportError:
            pass
        try:
            return self._run_tkinter()
        except ImportError:
            print("[tray] no graphical toolkit available; supervising in the "
                  "background instead", flush=True)
            self.children.run(interval=5.0)
            return 0

    def _watch_in_background(self) -> threading.Thread:
        thread = threading.Thread(
            target=lambda: self.children.run(interval=5.0),
            name="owcs-child-supervisor", daemon=True)
        thread.start()
        return thread

    def _run_pystray(self) -> int:
        import pystray                      # noqa: WPS433
        from PIL import Image               # noqa: WPS433

        image = Image.open(icon_path("png"))
        icon_ref: list[Any] = [None]

        def _quit(icon, _item):
            svc.request_stop()
            self.children.stop()
            icon.stop()

        menu = pystray.Menu(
            pystray.MenuItem(lambda _i: self.status_line(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open control room",
                             lambda _i, _t: self.open_control_room(),
                             default=True),
            pystray.MenuItem("Review inbox", lambda _i, _t: self.open_review()),
            pystray.MenuItem("Setup & checks", lambda _i, _t: self.open_setup()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Pause processing",
                             lambda _i, _t: self.pause_processing()),
            pystray.MenuItem("Resume processing",
                             lambda _i, _t: self.resume_processing()),
            pystray.MenuItem("Open data folder",
                             lambda _i, _t: self.open_storage_folder()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", _quit),
        )
        icon_ref[0] = pystray.Icon("owcs", image, "OWCS Comp Tracker", menu)
        self._watch_in_background()

        # Refresh the status line so the menu is never stale when opened.
        def _refresh() -> None:
            while True:
                time.sleep(10)
                try:
                    icon_ref[0].update_menu()
                except Exception:
                    return
        threading.Thread(target=_refresh, daemon=True).start()

        icon_ref[0].run()
        return 0

    def _run_tkinter(self) -> int:
        import tkinter as tk                # noqa: WPS433
        from tkinter import ttk             # noqa: WPS433

        root = tk.Tk()
        root.title("OWCS Comp Tracker")
        root.geometry("420x260")
        root.minsize(380, 240)
        try:
            root.iconbitmap(icon_path("ico"))
        except Exception:
            pass

        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="OWCS Comp Tracker",
                  font=("Segoe UI", 14, "bold")).pack(anchor="w")
        status = ttk.Label(frame, text=self.status_line(), wraplength=380)
        status.pack(anchor="w", pady=(4, 12))

        for label, command in (
                ("Open control room", self.open_control_room),
                ("Review inbox", self.open_review),
                ("Setup & checks", self.open_setup),
                ("Open data folder", self.open_storage_folder)):
            ttk.Button(frame, text=label, command=command).pack(
                fill="x", pady=2)

        note = ttk.Label(
            frame, wraplength=380, foreground="#555",
            text="Closing this window leaves processing running in the "
                 "background. Use Quit to stop it.")
        note.pack(anchor="w", pady=(12, 4))

        def _quit() -> None:
            svc.request_stop()
            self.children.stop()
            root.destroy()

        ttk.Button(frame, text="Quit", command=_quit).pack(fill="x", pady=(6, 0))

        def _tick() -> None:
            status.config(text=self.status_line())
            root.after(5000, _tick)

        self._watch_in_background()
        root.after(2000, _tick)
        # Closing the window hides it; the service keeps running.
        root.protocol("WM_DELETE_WINDOW", root.withdraw)
        root.mainloop()
        return 0
