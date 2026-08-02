"""
credentials.py — the API keys, stored the way Windows wants them stored.

The pipeline reads FACEIT_API_KEY / YOUTUBE_API_KEY from the environment and
never logs their values. This module is what puts them there: the wizard and
the control room write keys into a vault, and the supervisor exports them into
the environment of the processes it starts. A key never appears in a settings
file, a log line, an export, a job payload, or the control room's HTML.

Protection, honestly labelled:

  * **Windows** — DPAPI (`CryptProtectData`) at *user* scope with an extra
    application entropy string. The ciphertext is undecryptable by another
    Windows account, and undecryptable on another machine. No third-party
    dependency: the two calls come straight out of crypt32.dll via ctypes.
  * **Anywhere else** — there is no OS keystore we can honestly rely on, so
    the file is written 0600 and labelled `protection: "file-permissions"`.
    The UI shows that label verbatim. This code never claims encryption it
    did not perform.

The backend is injectable, which is how the Windows path is unit-tested from
a Linux CI runner without pretending DPAPI ran.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from typing import Any, Callable

from . import paths
from .settings import atomic_write_json

#: The keys the application knows about. Anything else is refused, so a typo
#: cannot quietly create a credential nothing will ever read.
KNOWN_KEYS: dict[str, dict[str, str]] = {
    "FACEIT_API_KEY": {
        "label": "FACEIT API key",
        "help": "Optional. Enables FACEIT match facts (teams, scores, bans). "
                "Everything else works without it.",
        "url": "https://developers.faceit.com/",
    },
    "GITHUB_TOKEN": {
        "label": "GitHub token (publishing)",
        "help": "Optional. Lets the app publish finished results to the "
                "public website. A fine-grained token with Contents:write on "
                "that one repository is all it needs. Everything else works "
                "without it; you just publish by hand instead.",
        "url": "https://github.com/settings/personal-access-tokens/new",
    },
    "YOUTUBE_API_KEY": {
        "label": "YouTube Data API key",
        "help": "Optional. Speeds up broadcast discovery. Without it the app "
                "falls back to the free RSS + streams-tab scan.",
        "url": "https://console.cloud.google.com/apis/library/youtube.googleapis.com",
    },
}

#: Extra entropy mixed into DPAPI so another application running as the same
#: user cannot decrypt this file just by pointing DPAPI at it. Not a secret.
_ENTROPY = b"OWCS Comp Tracker credential vault v1"

PROTECTION_DPAPI = "windows-dpapi"
PROTECTION_FILE = "file-permissions"


class CredentialError(RuntimeError):
    """Vault could not be read or written."""


# ------------------------------------------------------------------ DPAPI
def dpapi_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes  # noqa: F401
        ctypes.WinDLL("crypt32.dll")  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        return False
    return True


def _dpapi_call(which: str, payload: bytes) -> bytes:
    """CryptProtectData / CryptUnprotectData through ctypes."""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(data: bytes) -> DATA_BLOB:
        buf = ctypes.create_string_buffer(data, len(data))
        return DATA_BLOB(len(data), ctypes.cast(
            buf, ctypes.POINTER(ctypes.c_char)))

    crypt32 = ctypes.WinDLL("crypt32.dll")       # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32.dll")     # type: ignore[attr-defined]

    src, entropy, out = _blob(payload), _blob(_ENTROPY), DATA_BLOB()
    # CRYPTPROTECT_UI_FORBIDDEN(0x1): never pop a dialog — this can run inside
    # a background service with no interactive desktop.
    fn = crypt32.CryptProtectData if which == "protect" else crypt32.CryptUnprotectData
    if which == "protect":
        ok = fn(ctypes.byref(src), "OWCS Comp Tracker",
                ctypes.byref(entropy), None, None, 0x1, ctypes.byref(out))
    else:
        ok = fn(ctypes.byref(src), None,
                ctypes.byref(entropy), None, None, 0x1, ctypes.byref(out))
    if not ok:
        raise CredentialError(
            f"Windows {which} failed (error {ctypes.GetLastError()}). "
            "The credential file may have been created by a different Windows "
            "account or copied from another machine.")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def _dpapi_protect(data: bytes) -> bytes:
    return _dpapi_call("protect", data)


def _dpapi_unprotect(data: bytes) -> bytes:
    return _dpapi_call("unprotect", data)


# ----------------------------------------------------------------- backend
class Backend:
    """How bytes become stored bytes. Two real implementations below."""

    name = "abstract"

    def protect(self, data: bytes) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def unprotect(self, data: bytes) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError


class DpapiBackend(Backend):
    name = PROTECTION_DPAPI

    def protect(self, data: bytes) -> bytes:
        return _dpapi_protect(data)

    def unprotect(self, data: bytes) -> bytes:
        return _dpapi_unprotect(data)


class FilePermissionBackend(Backend):
    """No encryption — and it says so. The vault file is 0600."""

    name = PROTECTION_FILE

    def protect(self, data: bytes) -> bytes:
        return data

    def unprotect(self, data: bytes) -> bytes:
        return data


def default_backend() -> Backend:
    return DpapiBackend() if dpapi_available() else FilePermissionBackend()


# ------------------------------------------------------------------- vault
class CredentialVault:
    """Reads and writes the credential file. Values in, values out, and a
    `describe()` that is safe to render anywhere."""

    def __init__(self, path: str | None = None, backend: Backend | None = None,
                 *, backend_factory: Callable[[], Backend] | None = None):
        self.path = path or paths.credentials_file()
        if backend is not None:
            self.backend = backend
        else:
            self.backend = (backend_factory or default_backend)()

    # ------------------------------------------------------------ storage
    def _read_document(self) -> dict[str, Any]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except FileNotFoundError:
            return {"version": 1, "protection": self.backend.name, "keys": {}}
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            raise CredentialError(
                f"the credential file at {self.path} is unreadable ({exc}). "
                "Use 'Reset credentials' in the control room to start over.") from exc
        if not isinstance(doc, dict) or not isinstance(doc.get("keys"), dict):
            raise CredentialError(
                f"the credential file at {self.path} is not in the expected "
                "format. Use 'Reset credentials' in the control room.")
        return doc

    def _write_document(self, doc: dict[str, Any]) -> None:
        atomic_write_json(self.path, doc)
        if os.name == "posix":
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    # -------------------------------------------------------------- public
    def set(self, name: str, value: str) -> None:
        """Store one key. An empty value deletes it."""
        if name not in KNOWN_KEYS:
            raise CredentialError(
                f"unknown credential {name!r}; "
                f"known: {', '.join(sorted(KNOWN_KEYS))}")
        value = (value or "").strip()
        doc = self._read_document()
        if not value:
            doc["keys"].pop(name, None)
        else:
            blob = self.backend.protect(value.encode("utf-8"))
            doc["keys"][name] = {
                "protection": self.backend.name,
                "blob": base64.b64encode(blob).decode("ascii"),
            }
        doc["protection"] = self.backend.name
        self._write_document(doc)

    def delete(self, name: str) -> None:
        self.set(name, "")

    def get(self, name: str) -> str | None:
        """The decrypted value, or None. Never log the return value."""
        doc = self._read_document()
        entry = doc["keys"].get(name)
        if not entry:
            return None
        try:
            raw = base64.b64decode(entry["blob"])
            return self.backend.unprotect(raw).decode("utf-8")
        except CredentialError:
            raise
        except Exception as exc:
            raise CredentialError(
                f"stored credential {name} could not be decoded ({type(exc).__name__}). "
                "Re-enter it in the control room.") from exc

    def has(self, name: str) -> bool:
        try:
            return bool(self._read_document()["keys"].get(name))
        except CredentialError:
            return False

    def names(self) -> list[str]:
        try:
            return sorted(self._read_document()["keys"])
        except CredentialError:
            return []

    def reset(self) -> None:
        self._write_document(
            {"version": 1, "protection": self.backend.name, "keys": {}})

    # ------------------------------------------------------------ exposure
    def describe(self) -> list[dict[str, Any]]:
        """Presence and protection only — no values, ever. This is what the
        control room and the wizard render."""
        stored = set(self.names())
        out = []
        for name, meta in KNOWN_KEYS.items():
            out.append({
                "name": name,
                "label": meta["label"],
                "help": meta["help"],
                "url": meta["url"],
                "present": name in stored,
                "protection": self.backend.name,
                "encrypted": self.backend.name == PROTECTION_DPAPI,
            })
        return out

    def export_environment(self, env: dict | None = None) -> list[str]:
        """Put every stored key into `env` for a child process. Returns the
        NAMES exported (never the values), which is safe to log."""
        env = os.environ if env is None else env
        exported = []
        for name in KNOWN_KEYS:
            try:
                value = self.get(name)
            except CredentialError:
                continue
            if value:
                env[name] = value
                exported.append(name)
        return exported
