"""
faceit_api.py — FACEIT Data API client + match normalizer (Roadmap Phase B2).

FACEIT is authoritative for match FACTS ONLY. This module fetches championship
/ tournament / match / team / player facts and normalizes them to a small,
explicit shape. It NEVER extracts, infers or fabricates hero compositions,
swaps, timelines or rates — the normalized output has no field for them.

Two hard rules from the roadmap:
  * "Do not rely on unrestricted text search for every run." This client only
    ever fetches EXPLICITLY configured championship ids. There is no search().
  * FACEIT must never supply compositions.

The HTTP transport is injectable so the whole discovery pipeline is testable
offline with fixtures — no network, no API key. The real transport reads the
key from the FACEIT_API_KEY environment variable (never committed).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

API_ROOT = "https://open.faceit.com/data/v4"
USER_AGENT = "OWCS-Comp-Tracker/0.4 (+fan project; official OWCS discovery)"

# Transport contract: (url, headers) -> (status:int|None, text:str|None, error:str|None)
Transport = Callable[[str, dict], "tuple[int | None, str | None, str | None]"]


class FaceitAuthError(RuntimeError):
    """Raised when a real network call is attempted without an API key."""


# ------------------------------------------------------------ lifecycle maps
# FACEIT status string -> (lifecycle, content-match status). content status is
# constrained by the DB CHECK to {upcoming, live, final, unknown}; lifecycle is
# the precise word the public site renders.
_STATUS_MAP = {
    "SCHEDULED": ("scheduled", "upcoming"),
    "READY": ("scheduled", "upcoming"),
    "CONFIGURING": ("scheduled", "upcoming"),
    "ONGOING": ("live", "live"),
    "LIVE": ("live", "live"),
    "FINISHED": ("finished", "final"),
    "CANCELLED": ("cancelled", "unknown"),
    "ABORTED": ("aborted", "unknown"),
}


def map_status(raw_status: str | None, *, forfeit: bool = False) -> tuple[str, str]:
    if forfeit:
        return ("forfeit", "final")
    lifecycle, content = _STATUS_MAP.get((raw_status or "").upper(), ("unknown", "unknown"))
    return (lifecycle, content)


def _epoch_to_iso(value: Any) -> str | None:
    """FACEIT timestamps are unix seconds (sometimes ms). Return ISO-UTC."""
    if value in (None, 0, ""):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        # Already a string timestamp? Pass through if it looks ISO-ish.
        s = str(value)
        return s if "T" in s else None
    if v > 1e12:  # milliseconds
        v /= 1000.0
    return dt.datetime.fromtimestamp(v, dt.timezone.utc).replace(microsecond=0).isoformat()


def _clean(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


# ------------------------------------------------------------------ transport
def urllib_transport(api_key: str) -> Transport:
    def _t(url: str, headers: dict) -> "tuple[int | None, str | None, str | None]":
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            **headers,
        })
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                status = getattr(resp, "status", None) or 200
                return status, resp.read().decode("utf-8", "ignore"), None
        except urllib.error.HTTPError as exc:
            return exc.code, None, f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return None, None, str(exc)
    return _t


def fixture_transport(fixture_dir: str) -> Transport:
    """Offline transport that serves committed/cached JSON instead of the API.

    A championship-matches request for `/championships/<id>/matches` is answered
    from `<fixture_dir>/<id>.json` (a `{"items":[...]}` payload). A championship
    detail request reads `<fixture_dir>/<id>.championship.json` when present.
    Anything missing returns a 404 so the caller's error/retry path is exercised
    exactly as it would be against the real API. No network, no key.
    """
    def _t(url: str, headers: dict) -> "tuple[int | None, str | None, str | None]":
        m = re.search(r"/championships/([^/?]+)(/matches)?", url)
        if not m:
            return 404, None, "unmapped fixture url"
        champ_id, is_matches = m.group(1), bool(m.group(2))
        fname = f"{champ_id}.json" if is_matches else f"{champ_id}.championship.json"
        path = os.path.join(fixture_dir, fname)
        if not os.path.exists(path):
            return 404, None, f"no fixture: {fname}"
        return 200, Path(path).read_text(encoding="utf-8"), None
    return _t


class FaceitClient:
    """Thin FACEIT Data API client. Caches raw responses for auditability."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        transport: Transport | None = None,
        cache_dir: str | None = None,
    ):
        self.api_key = api_key or os.environ.get("FACEIT_API_KEY")
        if transport is not None:
            self._transport = transport
        elif self.api_key:
            self._transport = urllib_transport(self.api_key)
        else:
            self._transport = None  # set on first real call -> clear error
        self.cache_dir = cache_dir
        self.calls: list[dict] = []  # audit trail of every request made

    # -- low level ---------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict:
        url = API_ROOT + path
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        if self._transport is None:
            raise FaceitAuthError(
                "FACEIT_API_KEY is not set and no transport was injected; "
                "cannot make a live FACEIT request. Set the secret or run "
                "against fixtures.")
        status, text, error = self._transport(url, {})
        record = {"url": url, "status": status, "error": error,
                  "sha256": hashlib.sha256((text or "").encode()).hexdigest() if text else None}
        self.calls.append(record)
        if self.cache_dir and text:
            self._cache(url, text)
        if error or not text:
            raise FaceitApiError(url, status, error)
        try:
            return json.loads(text)
        except ValueError as exc:
            raise FaceitApiError(url, status, f"invalid JSON: {exc}") from exc

    def _cache(self, url: str, text: str) -> None:
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(url.encode()).hexdigest()[:20]
        Path(os.path.join(self.cache_dir, f"{key}.json")).write_text(text, encoding="utf-8")

    # -- endpoints ---------------------------------------------------------
    def get_championship(self, championship_id: str) -> dict:
        return self._get(f"/championships/{championship_id}")

    def list_championship_matches(
        self, championship_id: str, *, match_type: str = "all",
        page_size: int = 50, max_pages: int = 20,
    ) -> list[dict]:
        """All matches for a championship, paginated. Returns raw match dicts."""
        out: list[dict] = []
        offset = 0
        for _ in range(max_pages):
            payload = self._get(
                f"/championships/{championship_id}/matches",
                {"type": match_type, "offset": offset, "limit": page_size},
            )
            items = payload.get("items", []) or []
            out.extend(items)
            if len(items) < page_size:
                break
            offset += page_size
        return out


class FaceitApiError(RuntimeError):
    def __init__(self, url: str, status: int | None, error: str | None):
        self.url = url
        self.status = status
        self.error = error
        super().__init__(f"FACEIT API error ({status}) for {url}: {error}")


# --------------------------------------------------------------- normalizer
def _faction(raw_match: dict, key: str) -> dict:
    teams = raw_match.get("teams") or {}
    return teams.get(key) or {}


def _roster(faction: dict) -> list[dict]:
    out = []
    for p in faction.get("roster") or []:
        nick = _clean(p.get("nickname") or p.get("game_player_name"))
        if not nick:
            continue
        out.append({
            "nickname": nick,
            "faceitPlayerId": _clean(p.get("player_id")),
            "country": _clean(p.get("country")),
        })
    return out


def _detect_forfeit(raw_match: dict, lifecycle_status: str) -> bool:
    """A finished match with a winner but no real score, or an empty roster on
    one side, is treated as a forfeit. FACEIT has no single forfeit flag, so we
    infer conservatively and record it as a lifecycle fact only."""
    if lifecycle_status != "FINISHED".lower() and (raw_match.get("status") or "").upper() != "FINISHED":
        return False
    results = raw_match.get("results") or {}
    score = (results.get("score") or {}) if isinstance(results, dict) else {}
    winner = results.get("winner") if isinstance(results, dict) else None
    if raw_match.get("faceit_forfeit") is True:
        return True
    if winner and score:
        vals = [score.get("faction1"), score.get("faction2")]
        if all(v in (0, None) for v in vals):
            return True
    return False


def normalize_match(raw_match: dict, *, region: str | None = None) -> dict:
    """Turn one raw FACEIT match into normalized facts. No compositions.

    Output shape:
      {
        faceitMatchId, competitionId, competitionName, region, round, group,
        lifecycleStatus, contentStatus, scheduledAt, startedAt, finishedAt,
        faceitUrl,
        teams: [{side, name, faceitTeamId, players:[{nickname,faceitPlayerId,country}]}],
        score: {a, b}, winnerSide,
        raw: <the original dict, for audit>
      }
    """
    raw_status = raw_match.get("status")
    forfeit = _detect_forfeit(raw_match, (raw_status or "").lower())
    lifecycle, content = map_status(raw_status, forfeit=forfeit)

    f1, f2 = _faction(raw_match, "faction1"), _faction(raw_match, "faction2")
    results = raw_match.get("results") or {}
    score = results.get("score") or {} if isinstance(results, dict) else {}
    sa = score.get("faction1")
    sb = score.get("faction2")
    winner_key = results.get("winner") if isinstance(results, dict) else None
    winner_side = {"faction1": "A", "faction2": "B"}.get(winner_key)

    url = _clean(raw_match.get("faceit_url"))
    if url:
        url = url.replace("{lang}", "en")

    return {
        "faceitMatchId": _clean(raw_match.get("match_id")),
        "competitionId": _clean(raw_match.get("competition_id")),
        "competitionName": _clean(raw_match.get("competition_name")),
        "region": region or _clean(raw_match.get("region")),
        "round": _clean(raw_match.get("round")),
        "group": _clean(raw_match.get("group")),
        "lifecycleStatus": lifecycle,
        "contentStatus": content,
        "scheduledAt": _epoch_to_iso(raw_match.get("scheduled_at")),
        "startedAt": _epoch_to_iso(raw_match.get("started_at")),
        "finishedAt": _epoch_to_iso(raw_match.get("finished_at")),
        "faceitUrl": url,
        "teams": [
            {"side": "A", "name": _clean(f1.get("name")),
             "faceitTeamId": _clean(f1.get("team_id")) or _clean(f1.get("faction_id")),
             "players": _roster(f1)},
            {"side": "B", "name": _clean(f2.get("name")),
             "faceitTeamId": _clean(f2.get("team_id")) or _clean(f2.get("faction_id")),
             "players": _roster(f2)},
        ],
        "score": {"a": sa, "b": sb},
        "winnerSide": winner_side,
        "raw": raw_match,
    }
