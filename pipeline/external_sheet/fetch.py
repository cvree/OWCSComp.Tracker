"""
fetch.py — download the public xlsx export once per sync.

Runs only in the pipeline / GitHub Actions. No page ever contacts Google:
the site renders the committed export (assets/data/external_sheet.v1.js).

Stdlib only (urllib), no API key: the workbook's public `export?format=xlsx`
URL is all it uses. A download that fails, times out, is too large, or is not
an xlsx (Google answers an unshared sheet with an HTML sign-in page) raises
FetchFailed with a plain-language reason — and the caller keeps the last good
import untouched.
"""
from __future__ import annotations

import hashlib
import os
import urllib.error
import urllib.request

USER_AGENT = "OWCSCompTracker-sheet-sync/1 (+https://github.com/cvree/owcscomp.tracker)"


class FetchFailed(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download(url: str, *, timeout: float = 60, max_bytes: int = 50 * 1024 * 1024,
             opener=None) -> bytes:
    """The workbook bytes, or FetchFailed. `opener` is injectable so tests
    run with no network at all."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    open_ = opener or urllib.request.urlopen
    try:
        with open_(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            if status != 200:
                raise FetchFailed(f"HTTP {status} from the export URL")
            data = resp.read(max_bytes + 1)
    except FetchFailed:
        raise
    except urllib.error.HTTPError as exc:
        raise FetchFailed(f"HTTP {exc.code} from the export URL"
                          + (" — the sheet may no longer be shared publicly"
                             if exc.code in (401, 403, 404) else "")) from exc
    except urllib.error.URLError as exc:
        raise FetchFailed(f"could not reach the export URL: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise FetchFailed(f"download failed: {exc}") from exc
    if len(data) > max_bytes:
        raise FetchFailed(f"the workbook is larger than {max_bytes} bytes — refusing it")
    if not data:
        raise FetchFailed("the export URL returned an empty body")
    if data[:2] != b"PK":
        head = data[:400].decode("utf-8", "replace").lower()
        if "<html" in head or "<!doctype" in head:
            raise FetchFailed("the export URL returned an HTML page, not a workbook — "
                              "the sheet is probably no longer shared publicly")
        raise FetchFailed("the export URL did not return an xlsx workbook")
    return data


def save_snapshot(data: bytes, cache_dir: str, date: str) -> str:
    """Keep the exact bytes, date-stamped and content-addressed, in the
    (git-ignored) work cache. The committed raw cell layer is the durable
    copy; this one lets an operator re-open the original file."""
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"owcs-sheet_{date}_{sha256(data)[:12]}.xlsx")
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(data)
    return path
