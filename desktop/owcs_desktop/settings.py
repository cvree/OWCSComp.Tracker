"""
settings.py — the application's non-secret configuration.

Everything the first-run wizard and the control room can change lives here:
storage budget, retention, autostart, confidence gates, update policy. Secrets
never do — those go through `credentials.py` and the Windows credential store.

Two properties matter and are tested:

  * **Atomic.** A write goes to a temp file in the same directory, is flushed
    and fsync'd, then replaced over the target. A power cut during a save can
    leave the old file or the new file, never a truncated one.
  * **Forgiving on read, strict on write.** A corrupted or partial settings
    file falls back to defaults (the app must still start), but unknown keys
    are rejected on write so a typo can't silently do nothing.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from typing import Any

from . import paths

#: Every setting, its default, and what it means. The wizard and the control
#: room both render from this table, so a new setting appears in the UI by
#: being added here — there is no second list to keep in sync.
SCHEMA: dict[str, dict[str, Any]] = {
    "storageRoot": {
        "default": "",
        "type": str,
        "label": "Storage folder",
        "help": "Where downloads, evidence and databases are kept. Empty means "
                "the default per-user folder.",
    },
    "maxStorageGb": {
        "default": 60.0,
        "type": float,
        "min": 5.0,
        "label": "Storage budget (GB)",
        "help": "The app prunes its own finished downloads to stay under this.",
    },
    "minFreeDiskGb": {
        "default": 10.0,
        "type": float,
        "min": 1.0,
        "label": "Keep free on disk (GB)",
        "help": "Processing pauses rather than filling the drive.",
    },
    "rawMediaRetentionDays": {
        "default": 3,
        "type": int,
        "min": 0,
        "label": "Keep raw video for (days)",
        "help": "Evidence crops and results are kept forever; only the large "
                "source downloads expire.",
    },
    "autoStart": {
        "default": True,
        "type": bool,
        "label": "Start with Windows",
        "help": "Run the processing service in the background when you sign in.",
    },
    "startMinimized": {
        "default": True,
        "type": bool,
        "label": "Start minimised to the tray",
        "help": "Keep working without opening the control room window.",
    },
    "autoPublish": {
        "default": True,
        "type": bool,
        "label": "Publish high-confidence results automatically",
        "help": "Only detections that clear the repeat + confidence gate. "
                "Everything else goes to review.",
    },
    "autoPublishMinConfidence": {
        "default": 0.86,
        "type": float,
        "min": 0.5,
        "max": 0.999,
        "label": "Auto-publish confidence floor",
        "help": "Below this a detection is quarantined for review, never published.",
    },
    "autoPublishMinRepeats": {
        "default": 3,
        "type": int,
        "min": 2,
        "label": "Auto-publish repeat floor",
        "help": "How many consecutive agreeing samples a reading needs before it "
                "can be promoted without a human.",
    },
    "maxConcurrentJobs": {
        "default": 1,
        "type": int,
        "min": 1,
        "max": 4,
        "label": "Jobs at once",
        "help": "Video decoding is CPU-heavy; one at a time is the safe default.",
    },
    "pollSeconds": {
        "default": 20,
        "type": int,
        "min": 5,
        "label": "Queue poll interval (s)",
        "help": "How often the background service looks for new work.",
    },
    "controlRoomPort": {
        "default": 8753,
        "type": int,
        "min": 1024,
        "max": 65535,
        "label": "Control room port",
        "help": "The local-only port the control room is served on.",
    },
    "checkForUpdates": {
        "default": True,
        "type": bool,
        "label": "Check for updates",
        "help": "Looks for a newer released installer. Never installs silently.",
    },
    "updateChannel": {
        "default": "stable",
        "type": str,
        "choices": ["stable", "prerelease"],
        "label": "Update channel",
        "help": "Prerelease opts into release candidates.",
    },
    "backupsToKeep": {
        "default": 10,
        "type": int,
        "min": 1,
        "label": "Database backups to keep",
        "help": "A backup is taken before every publish, so a bad result can be "
                "rolled back.",
    },
    "telemetry": {
        "default": False,
        "type": bool,
        "label": "Send usage data",
        "help": "Off, and there is nowhere for it to go — the app makes no "
                "analytics requests. Kept visible so the answer is verifiable.",
    },
}

DEFAULTS: dict[str, Any] = {k: v["default"] for k, v in SCHEMA.items()}


class SettingsError(ValueError):
    """A rejected write — unknown key, wrong type, or out of range."""


def _coerce(key: str, value: Any) -> Any:
    spec = SCHEMA.get(key)
    if spec is None:
        raise SettingsError(f"unknown setting: {key!r}")
    want = spec["type"]
    if want is bool:
        if isinstance(value, bool):
            out: Any = value
        elif isinstance(value, str) and value.lower() in (
                "true", "false", "1", "0", "yes", "no", "on", "off"):
            out = value.lower() in ("true", "1", "yes", "on")
        else:
            raise SettingsError(f"{key} must be true or false, got {value!r}")
    elif want is int:
        # bool is an int subclass; reject it explicitly so True != 1 here.
        if isinstance(value, bool):
            raise SettingsError(f"{key} must be a whole number, got {value!r}")
        try:
            out = int(value)
        except (TypeError, ValueError):
            raise SettingsError(
                f"{key} must be a whole number, got {value!r}") from None
    elif want is float:
        if isinstance(value, bool):
            raise SettingsError(f"{key} must be a number, got {value!r}")
        try:
            out = float(value)
        except (TypeError, ValueError):
            raise SettingsError(f"{key} must be a number, got {value!r}") from None
    else:
        out = "" if value is None else str(value)

    choices = spec.get("choices")
    if choices is not None and out not in choices:
        raise SettingsError(
            f"{key} must be one of {', '.join(map(str, choices))}, got {out!r}")
    lo, hi = spec.get("min"), spec.get("max")
    if lo is not None and out < lo:
        raise SettingsError(f"{key} must be at least {lo}, got {out}")
    if hi is not None and out > hi:
        raise SettingsError(f"{key} must be at most {hi}, got {out}")
    return out


def atomic_write_json(path: str, payload: Any) -> None:
    """Write JSON so an interrupted save can never truncate the target."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Never leave the scratch file behind on a failed save.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Settings:
    """The settings document. Cheap to construct; reads on demand."""

    def __init__(self, path: str | None = None):
        self.path = path or paths.settings_file()
        self._values: dict[str, Any] | None = None

    # ------------------------------------------------------------- reading
    def load(self) -> dict[str, Any]:
        values = dict(DEFAULTS)
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                stored = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
            # A missing OR corrupt file must not stop the app from starting.
            self._values = values
            return dict(values)
        if isinstance(stored, dict):
            for key, raw in stored.items():
                if key not in SCHEMA:
                    continue  # forward/backward compatible: ignore strangers
                try:
                    values[key] = _coerce(key, raw)
                except SettingsError:
                    pass  # keep the default rather than refuse to boot
        self._values = values
        return dict(values)

    @property
    def values(self) -> dict[str, Any]:
        if self._values is None:
            self.load()
        return dict(self._values or {})

    def get(self, key: str, default: Any = None) -> Any:
        if key not in SCHEMA:
            raise SettingsError(f"unknown setting: {key!r}")
        return self.values.get(key, DEFAULTS.get(key, default))

    # ------------------------------------------------------------- writing
    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Validate every key, then save all of them. Rejects the whole patch
        if any key is bad, so a half-applied change is impossible."""
        clean = {k: _coerce(k, v) for k, v in patch.items()}
        values = self.values
        values.update(clean)
        atomic_write_json(self.path, values)
        self._values = values
        return dict(values)

    def set(self, key: str, value: Any) -> dict[str, Any]:
        return self.update({key: value})

    def reset(self) -> dict[str, Any]:
        atomic_write_json(self.path, dict(DEFAULTS))
        self._values = dict(DEFAULTS)
        return dict(DEFAULTS)

    # ------------------------------------------------------------ derived
    def storage_root(self) -> str:
        """Where bulk data goes — the override if set, else the data root."""
        custom = str(self.get("storageRoot") or "").strip()
        return os.path.abspath(custom) if custom else paths.data_root()

    def schema_for_ui(self) -> list[dict[str, Any]]:
        """The settings, in declaration order, shaped for a form renderer."""
        out = []
        current = self.values
        for key, spec in SCHEMA.items():
            entry = {
                "key": key,
                "label": spec["label"],
                "help": spec["help"],
                "type": spec["type"].__name__,
                "value": current.get(key, spec["default"]),
                "default": spec["default"],
            }
            for extra in ("min", "max", "choices"):
                if extra in spec:
                    entry[extra] = copy.deepcopy(spec[extra])
            out.append(entry)
        return out
