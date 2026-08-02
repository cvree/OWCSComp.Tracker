"""
updates.py — finding, verifying and staging a newer installer.

Deliberately conservative. This module will:

  * ask GitHub's public releases API what the newest release is,
  * compare it to the running version,
  * download the installer asset,
  * verify it against the `SHA256SUMS` asset published beside it,
  * and stop.

It will NOT install anything by itself. Applying an update means replacing
files that are currently executing, which is the installer's job, not a
background thread's — so `apply_update()` stops the background service and
hands the verified installer to Windows, and the user sees the normal installer
UI. There is no silent self-modification anywhere in this codebase.

An update that fails its checksum is deleted, not run. That check is the whole
point of the feature: an unverified binary downloaded over the network and
executed with the user's privileges is exactly the thing this must never do.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Callable

from . import __version__, paths

#: The public repository releases live in. No token, no authentication — this
#: is an unauthenticated read of a public API.
RELEASES_URL = (
    "https://api.github.com/repos/cvree/owcscomp.tracker/releases")
INSTALLER_PATTERN = re.compile(r"^OWCSCompTracker-.*Setup\.exe$", re.I)
CHECKSUM_ASSET = "SHA256SUMS"
USER_AGENT = f"OWCSCompTracker/{__version__} (+update-check)"


# ------------------------------------------------------------- versioning
def parse_version(text: str) -> tuple[int, ...]:
    """`v1.2.3` / `1.2.3-rc1` -> (1, 2, 3). Unparseable -> (0,)."""
    match = re.search(r"(\d+(?:\.\d+)*)", str(text or ""))
    if not match:
        return (0,)
    return tuple(int(p) for p in match.group(1).split("."))


def is_newer(candidate: str, current: str = __version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    length = max(len(a), len(b))
    a = a + (0,) * (length - len(a))
    b = b + (0,) * (length - len(b))
    return a > b


# ------------------------------------------------------------- the check
def _fetch(url: str, *, timeout: int = 20,
           opener: Callable[..., Any] | None = None) -> bytes:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    open_fn = opener or urllib.request.urlopen
    with open_fn(request, timeout=timeout) as response:
        return response.read()


def check_for_update(*, channel: str = "stable", current: str = __version__,
                     fetch: Callable[[str], bytes] | None = None
                     ) -> dict[str, Any]:
    """Ask what the newest release is. Never raises — a network failure is a
    reported outcome, not an exception, because this runs on a timer."""
    fetcher = fetch or (lambda url: _fetch(url))
    try:
        raw = fetcher(RELEASES_URL)
        releases = json.loads(raw.decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        return {"ok": False, "available": False, "current": current,
                "error": f"could not reach the update service: {exc}"}
    if not isinstance(releases, list):
        return {"ok": False, "available": False, "current": current,
                "error": "unexpected response from the update service"}

    allow_prerelease = channel == "prerelease"
    best: dict[str, Any] | None = None
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        if release.get("prerelease") and not allow_prerelease:
            continue
        tag = str(release.get("tag_name") or "")
        if best is None or is_newer(tag, str(best.get("tag_name") or "")):
            best = release
    if best is None:
        return {"ok": True, "available": False, "current": current,
                "detail": "no published release on this channel yet"}

    tag = str(best.get("tag_name") or "")
    assets = {a.get("name"): a for a in best.get("assets", [])
              if isinstance(a, dict)}
    installer = next((a for name, a in assets.items()
                      if name and INSTALLER_PATTERN.match(name)), None)
    return {
        "ok": True,
        "available": is_newer(tag, current) and installer is not None,
        "current": current,
        "latest": tag,
        "name": best.get("name") or tag,
        "notes": (best.get("body") or "")[:4000],
        "publishedAt": best.get("published_at"),
        "url": best.get("html_url"),
        "prerelease": bool(best.get("prerelease")),
        "installer": None if not installer else {
            "name": installer.get("name"),
            "url": installer.get("browser_download_url"),
            "bytes": installer.get("size"),
        },
        "checksums": (assets.get(CHECKSUM_ASSET) or {}).get(
            "browser_download_url"),
        "detail": (
            "an update is available" if is_newer(tag, current) and installer
            else "up to date" if not is_newer(tag, current)
            else f"release {tag} has no Windows installer asset"),
    }


# ----------------------------------------------------------- downloading
def _sha256(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_checksums(text: str) -> dict[str, str]:
    """`<sha256>  <filename>` lines -> {filename: sha256}."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            out[parts[-1].lstrip("*")] = parts[0].lower()
    return out


def download_update(info: dict[str, Any], *, dest_dir: str | None = None,
                    fetch: Callable[[str], bytes] | None = None
                    ) -> dict[str, Any]:
    """Download the installer and verify it. A mismatch deletes the file."""
    installer = info.get("installer") or {}
    url, name = installer.get("url"), installer.get("name")
    if not url or not name:
        return {"ok": False, "error": "this release has no installer to download"}

    fetcher = fetch or (lambda u: _fetch(u, timeout=600))
    dest_dir = dest_dir or paths.sub("updates")
    os.makedirs(dest_dir, exist_ok=True)
    target = os.path.join(dest_dir, name)

    try:
        payload = fetcher(url)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {"ok": False, "error": f"download failed: {exc}"}

    tmp = target + ".part"
    try:
        with open(tmp, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
    except OSError as exc:
        return {"ok": False, "error": f"could not write the download: {exc}"}

    expected = None
    if info.get("checksums"):
        try:
            expected = parse_checksums(
                fetcher(info["checksums"]).decode("utf-8", "replace")).get(name)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            expected = None

    actual = _sha256(tmp)
    if expected is None:
        os.unlink(tmp)
        return {"ok": False, "sha256": actual,
                "error": "the release published no SHA256SUMS to verify "
                         "against — refusing to keep an unverified installer"}
    if actual != expected:
        os.unlink(tmp)
        return {"ok": False, "expected": expected, "sha256": actual,
                "error": "the downloaded installer did not match its published "
                         "checksum and was deleted"}

    os.replace(tmp, target)
    return {"ok": True, "path": target, "sha256": actual, "verified": True,
            "bytes": len(payload)}


def apply_update(path: str, *, runner=subprocess) -> dict[str, Any]:
    """Stop the service and launch the verified installer.

    Windows only — elsewhere there is nothing to hand the file to, and saying
    so is better than pretending.
    """
    from . import supervisor
    if not os.path.isfile(path):
        return {"ok": False, "error": f"installer not found: {path}"}
    if sys.platform != "win32":
        return {"ok": False,
                "error": "applying an update is a Windows operation; the "
                         f"verified installer is at {path}"}
    # Maintenance, not a pause: the installer needs the files unlocked, and
    # the service must come back by itself once it has them.
    supervisor.request_stop(reason=supervisor.STOP_MAINTENANCE)
    try:
        # The installer is interactive by design — the user sees exactly what
        # is being installed, and the app never replaces itself invisibly.
        runner.Popen([path], close_fds=True)
    except OSError as exc:
        return {"ok": False, "error": f"could not start the installer: {exc}"}
    return {"ok": True, "started": path,
            "detail": "the installer is running; the background service was "
                      "asked to stop so its files can be replaced"}
