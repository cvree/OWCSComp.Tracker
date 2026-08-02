"""
autostart.py — "start with Windows", without an elevated installer service.

The application registers itself under
`HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`. That location is
deliberate:

  * It needs no administrator rights, so enabling and disabling it is a plain
    checkbox in the control room rather than a UAC prompt.
  * It is per-user, so an installed machine with two accounts does not run two
    copies fighting over the same job queue.
  * It is the one place a user can see and remove it themselves (Task Manager
    → Startup), which matters for something that runs unattended.

The registry access goes through a `Backend` so the logic is tested on any
platform. On non-Windows the default backend is a no-op that reports
`supported: False` rather than silently pretending it worked.
"""
from __future__ import annotations

import os
import sys
from typing import Any

from . import paths

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
ENTRY_NAME = "OWCSCompTracker"


def launch_command() -> str:
    """The command Windows should run at sign-in.

    Frozen: the installed executable, quoted, with `--tray`. From source: the
    current interpreter running the desktop entrypoint. Both are absolute —
    the Run key has no working directory.
    """
    if paths.is_frozen():
        return f'"{os.path.abspath(sys.executable)}" --tray'
    entry = os.path.join(paths.app_root(), "desktop", "owcs_app.py")
    return f'"{os.path.abspath(sys.executable)}" "{entry}" --tray'


class Backend:
    """Read/write/delete one registry value."""

    supported = False
    name = "unsupported"

    def read(self) -> str | None:  # pragma: no cover - interface
        raise NotImplementedError

    def write(self, command: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def delete(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class WindowsRegistryBackend(Backend):
    supported = True
    name = "hkcu-run"

    def __init__(self, key_path: str = RUN_KEY, entry: str = ENTRY_NAME):
        self.key_path = key_path
        self.entry = entry

    def _open(self, write: bool):
        import winreg  # type: ignore[import-not-found]
        access = winreg.KEY_WRITE if write else winreg.KEY_READ
        return winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self.key_path,
                                  0, access | winreg.KEY_READ)

    def read(self) -> str | None:
        import winreg  # type: ignore[import-not-found]
        try:
            with self._open(False) as key:
                value, _ = winreg.QueryValueEx(key, self.entry)
                return str(value)
        except FileNotFoundError:
            return None
        except OSError:
            return None

    def write(self, command: str) -> None:
        import winreg  # type: ignore[import-not-found]
        with self._open(True) as key:
            winreg.SetValueEx(key, self.entry, 0, winreg.REG_SZ, command)

    def delete(self) -> None:
        import winreg  # type: ignore[import-not-found]
        try:
            with self._open(True) as key:
                winreg.DeleteValue(key, self.entry)
        except FileNotFoundError:
            pass
        except OSError:
            pass


class NullBackend(Backend):
    """Non-Windows. Reports honestly instead of faking success."""

    supported = False
    name = "unsupported"

    def read(self) -> str | None:
        return None

    def write(self, command: str) -> None:
        raise OSError(
            "automatic startup is a Windows feature; this platform has no "
            "supported per-user startup registration in this application")

    def delete(self) -> None:
        return None


class MemoryBackend(Backend):
    """An in-memory registry, for the tests."""

    supported = True
    name = "memory"

    def __init__(self) -> None:
        self.value: str | None = None

    def read(self) -> str | None:
        return self.value

    def write(self, command: str) -> None:
        self.value = command

    def delete(self) -> None:
        self.value = None


def default_backend() -> Backend:
    return WindowsRegistryBackend() if sys.platform == "win32" else NullBackend()


class AutoStart:
    def __init__(self, backend: Backend | None = None):
        self.backend = backend or default_backend()

    @property
    def supported(self) -> bool:
        return self.backend.supported

    def is_enabled(self) -> bool:
        return self.backend.read() is not None

    def enable(self, command: str | None = None) -> str:
        """Register (or re-register) startup. Returns the stored command.

        Always rewrites, even when already present: after an upgrade that
        moved the install directory, a stale command would point at an
        executable that no longer exists, and the app would silently stop
        starting with Windows.
        """
        cmd = command or launch_command()
        self.backend.write(cmd)
        return cmd

    def disable(self) -> None:
        self.backend.delete()

    def sync(self, want_enabled: bool) -> dict[str, Any]:
        """Make reality match the setting. Never raises on an unsupported
        platform — reports it."""
        if not self.supported:
            return {"supported": False, "enabled": False,
                    "backend": self.backend.name,
                    "detail": "automatic startup is only available on Windows"}
        try:
            if want_enabled:
                cmd = self.enable()
                return {"supported": True, "enabled": True,
                        "backend": self.backend.name, "command": cmd}
            self.disable()
            return {"supported": True, "enabled": False,
                    "backend": self.backend.name}
        except OSError as exc:
            return {"supported": True, "enabled": self.is_enabled(),
                    "backend": self.backend.name, "error": str(exc)}

    def status(self) -> dict[str, Any]:
        current = self.backend.read()
        expected = launch_command()
        return {
            "supported": self.supported,
            "backend": self.backend.name,
            "enabled": current is not None,
            "command": current,
            "expectedCommand": expected,
            # A command that no longer matches means the app moved (upgrade,
            # reinstall to a different folder). `enable()` repairs it.
            "stale": current is not None and current != expected,
        }
