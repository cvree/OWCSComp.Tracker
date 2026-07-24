"""
config.py — load the operator config + curated registries (Roadmap A4/B1/C1).

CI installs no PyYAML (only opencv + numpy), so this ships a tiny, dependency-
free parser for the SIMPLE subset used by config/automation.yml: `key: value`
scalars and one level of `- item` lists under a key. That is all automation.yml
uses, and keeping it dependency-free means the config loads in exactly the same
environment the tests and the site build run in.

Everything degrades safely: a missing file yields documented defaults, a
malformed file raises a clear error rather than silently returning garbage.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
CONFIG_DIR = os.path.join(REPO_ROOT, "config")

AUTOMATION_YML = os.path.join(CONFIG_DIR, "automation.yml")
FACEIT_COMPETITIONS = os.path.join(CONFIG_DIR, "faceit_competitions.json")
BROADCAST_CHANNELS = os.path.join(CONFIG_DIR, "broadcast_channels.json")

DEFAULTS: dict[str, Any] = {
    "lookback_days": 14,
    "schedule_horizon_days": 30,
    "recording_pre_roll_minutes": 15,
    "recording_post_roll_minutes": 30,
    "raw_video_retention_days": 2,
    "max_recording_retries": 5,
    "max_processing_retries": 3,
    "max_discovery_retries": 6,
    "retry_backoff_minutes": [10, 30, 60, 180, 720],
    "lock_lease_seconds": 300,
    "lock_heartbeat_seconds": 60,
    "publish_mode": "pull_request",
    "auto_publish_confidence": "high",
    "regions": ["na", "emea", "korea", "japan", "pacific", "china", "global"],
    # Phase C broadcast discovery.
    "youtube_daily_quota": 10000,
    "broadcast_auto_link": False,
    "broadcast_high_score": 90,
    "broadcast_medium_score": 45,
    "broadcast_time_window_hours": 6,
    "broadcast_playlist_pages": 6,
}


# --------------------------------------------------------------- yaml subset
def _coerce_scalar(text: str) -> Any:
    """Turn a YAML scalar token into int/float/bool/null/str."""
    t = text.strip()
    if len(t) >= 2 and t[0] in "\"'" and t[-1] == t[0]:
        return t[1:-1]
    low = t.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", ""):
        return None
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return t


def parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the flat `key: value` + one-level `- list` subset we use.

    Not a general YAML parser — deliberately small and predictable. Raises
    ValueError on structure it does not understand so a typo in automation.yml
    surfaces loudly instead of being silently dropped.
    """
    result: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw in text.splitlines():
        bare = raw.strip()
        # Whole-line comments and blanks are skipped outright (a '#' line may
        # itself contain apostrophes/quotes, so test the stripped line first).
        if not bare or bare.startswith("#"):
            continue
        line = raw.rstrip()
        # Strip a trailing ` # ...` inline comment only when the line carries
        # no quotes (our config values never embed '#').
        if '"' not in line and "'" not in line and " #" in line:
            line = line.split(" #", 1)[0].rstrip()
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if stripped.startswith("- "):
            if current_list_key is None:
                raise ValueError(f"list item without a parent key: {raw!r}")
            result[current_list_key].append(_coerce_scalar(stripped[2:]))
            continue
        if ":" not in stripped:
            raise ValueError(f"expected 'key: value' or '- item', got: {raw!r}")
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if indent != 0:
            raise ValueError(f"unexpected indentation for key {key!r}: {raw!r}")
        if value == "":
            # A bare 'key:' opens a list that following '- ' lines fill.
            result[key] = []
            current_list_key = key
        else:
            result[key] = _coerce_scalar(value)
            current_list_key = None
    return result


# --------------------------------------------------------------- config type
@dataclass
class AutomationConfig:
    values: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        val = self.values.get(key, DEFAULTS.get(key, default))
        return val

    # Convenience accessors used across the pipeline.
    @property
    def lookback_days(self) -> int:
        return int(self.get("lookback_days"))

    @property
    def schedule_horizon_days(self) -> int:
        return int(self.get("schedule_horizon_days"))

    @property
    def regions(self) -> list[str]:
        return list(self.get("regions") or [])

    @property
    def retry_backoff_minutes(self) -> list[int]:
        return [int(x) for x in (self.get("retry_backoff_minutes") or [10])]

    @property
    def lock_lease_seconds(self) -> int:
        return int(self.get("lock_lease_seconds"))

    @property
    def publish_mode(self) -> str:
        return str(self.get("publish_mode"))

    # -- Phase C broadcast discovery ---------------------------------------
    @property
    def youtube_daily_quota(self) -> int:
        return int(self.get("youtube_daily_quota"))

    @property
    def broadcast_auto_link(self) -> bool:
        return bool(self.get("broadcast_auto_link"))

    @property
    def broadcast_high_score(self) -> int:
        return int(self.get("broadcast_high_score"))

    @property
    def broadcast_medium_score(self) -> int:
        return int(self.get("broadcast_medium_score"))

    @property
    def broadcast_time_window_hours(self) -> int:
        return int(self.get("broadcast_time_window_hours"))

    @property
    def broadcast_playlist_pages(self) -> int:
        return int(self.get("broadcast_playlist_pages"))

    def max_attempts_for(self, kind: str) -> int:
        """Per-kind retry ceiling (Phase J1)."""
        from . import models
        if kind == models.KIND_RECORD:
            return int(self.get("max_recording_retries"))
        if kind == models.KIND_PROCESS:
            return int(self.get("max_processing_retries"))
        return int(self.get("max_discovery_retries"))


def load_config(path: str = AUTOMATION_YML) -> AutomationConfig:
    values = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            parsed = parse_simple_yaml(f.read())
        values.update(parsed)
    return AutomationConfig(values=values)


# ----------------------------------------------------------- registries (JSON)
def _load_json(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_competitions(path: str = FACEIT_COMPETITIONS) -> list[dict[str, Any]]:
    """Enabled, non-placeholder FACEIT competitions (Phase B1).

    An entry must be enabled AND carry a real championshipId to drive
    discovery; disabled/placeholder rows are intentionally excluded here so
    the discovery layer never ingests on a guess. Source reconciliation (B4)
    reads the full file separately to warn about the gaps.
    """
    data = _load_json(path)
    out = []
    for c in data.get("competitions", []) or []:
        if c.get("enabled") and c.get("championshipId"):
            out.append(c)
    return out


def load_all_competitions(path: str = FACEIT_COMPETITIONS) -> list[dict[str, Any]]:
    """Every competition row, placeholders included (for reconciliation)."""
    return list(_load_json(path).get("competitions", []) or [])


def load_channels(path: str = BROADCAST_CHANNELS) -> list[dict[str, Any]]:
    """Enabled official channels with a confirmed channelId (Phase C1/C2)."""
    data = _load_json(path)
    out = []
    for ch in data.get("channels", []) or []:
        if ch.get("enabled") and ch.get("channelId"):
            out.append(ch)
    return out


def load_all_channels(path: str = BROADCAST_CHANNELS) -> list[dict[str, Any]]:
    return list(_load_json(path).get("channels", []) or [])
