"""
website.py — putting a processed result on the public site.

Until this module existed, "publish" meant regenerating
`assets/data/public_data.v1.js` *inside the installed application* and
stopping there. That updates the copy the local control room serves and
nothing else: the public site is served from the repository, so the one
thing a user would call publishing — other people can see it — could not be
done from the app at all. It had to be done by a developer, from a terminal,
with a git checkout. That is precisely the situation this application exists
to end.

So: upload the generated dataset to the repository over the GitHub Contents
API, which rebuilds the site.

Why the HTTP API and not git
----------------------------
The Contents API needs nothing but `urllib` — no git binary to vendor, no
credential helper, no working copy of a 400 MB repository on a user's laptop,
and no merge to resolve. One authenticated PUT per file, and GitHub makes the
commit. It also fails cleanly: a rejected request is a status code, not a
half-finished rebase in a directory the user has never heard of.

What is NOT weakened
--------------------
Publishing here is the last step of the existing path, never a way around it.
`webapi.export_public()` still takes a backup, regenerates through
`pipeline/export_data.py`, verifies the result and rolls back on failure.
This module refuses to upload anything that has not passed that:

  * every file must exist and be non-empty;
  * the public dataset must assign `window.OWCS_PUBLIC = {` outright. The
    fixture uses `window.OWCS_PUBLIC = window.OWCS_PUBLIC || {…}` so that it
    yields to real data, and uploading THAT shape would quietly replace the
    live site with the demo. It is refused by name;
  * the upload is verified by reading the file back from the remote and
    comparing hashes, so "published" means observed, not assumed;
  * every attempt writes an audit record — what was sent, its hash, who asked,
    and the resulting commit — whether it succeeded or not.

The token is a stored credential like any other: it lives in the DPAPI vault,
`describe()` reports only that it is present, and every error string from this
module goes through the same masking as the rest of the API.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from . import __version__, paths
from .settings import Settings, atomic_write_json

API_ROOT = "https://api.github.com"
USER_AGENT = f"OWCSCompTracker/{__version__} (+publish)"

#: The credential this module needs. A fine-grained token with Contents:write
#: on the one repository is enough; nothing here reads anything else.
TOKEN_KEY = "GITHUB_TOKEN"

#: The generated files that together are "the public site's data", each with
#: the global it must define. Both are written by `export_data.py --public`.
#: A file listed here with no marker is uploaded but not shape-checked.
PUBLISHED_FILES: tuple[tuple[str, str], ...] = (
    ("assets/data/public_data.v1.js", "window.OWCS_PUBLIC = {"),
    ("assets/js/data.js", "window.OWCS_DATA"),
)

#: The fixture's assignment shape. Present in a file we are about to upload,
#: it means the export did not run and we are holding the demo dataset.
FIXTURE_MARKER = "window.OWCS_PUBLIC = window.OWCS_PUBLIC ||"


class PublishError(Exception):
    """Anything that stops a publish. Carries text safe to show a user."""


# ------------------------------------------------------------ local checks
def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_local(*, app: str | None = None) -> dict[str, Any]:
    """Are the generated files present, real, and production-shaped?

    Returns {"ok", "files": [{path, bytes, sha256}], "problems": [...]}.
    Never raises: the control room shows the problems as a list.
    """
    app = app or paths.app_root()
    files: list[dict[str, Any]] = []
    problems: list[str] = []
    for rel, marker in PUBLISHED_FILES:
        full = os.path.join(app, *rel.split("/"))
        if not os.path.isfile(full):
            problems.append(f"{rel} has not been generated yet")
            continue
        try:
            body = _read(full)
        except OSError as exc:
            problems.append(f"{rel} could not be read: {exc}")
            continue
        if not body.strip():
            problems.append(f"{rel} is empty")
            continue
        # The fixture check comes FIRST, and the order is the whole value of
        # it. The fixture assigns `window.OWCS_PUBLIC = window.OWCS_PUBLIC ||
        # {…}`, which does not contain `window.OWCS_PUBLIC = {`, so the
        # generic marker check below already rejects it — but it rejects it
        # saying "does not define window.OWCS_PUBLIC", which is both untrue
        # and useless. Someone reading that would go looking for a broken
        # export instead of the demo data they are actually holding.
        if FIXTURE_MARKER in body:
            problems.append(
                f"{rel} is the demo fixture, not a real export — publishing "
                "it would replace the live site with sample data")
            continue
        if marker and marker not in body:
            problems.append(f"{rel} does not define {marker.strip(' ={')}")
            continue
        files.append({"path": rel, "bytes": len(body.encode("utf-8")),
                      "sha256": sha256(body), "body": body})
    return {"ok": not problems and bool(files),
            "files": files, "problems": problems}


# ------------------------------------------------------------- the remote
def target() -> dict[str, str]:
    """Which repository and branch the site is published from."""
    settings = Settings()
    repo = str(settings.get("publishRepo") or "").strip().strip("/")
    branch = str(settings.get("publishBranch") or "").strip() or "main"
    owner, _, name = repo.partition("/")
    return {"repo": repo, "owner": owner, "name": name, "branch": branch}


def _request(url: str, token: str, *, method: str = "GET",
             payload: dict[str, Any] | None = None,
             timeout: int = 30,
             opener: Callable[..., Any] | None = None) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    open_fn = opener or urllib.request.urlopen
    with open_fn(request, timeout=timeout) as response:
        body = response.read()
    return json.loads(body.decode("utf-8")) if body else {}


def _contents_url(where: dict[str, str], rel: str) -> str:
    return (f"{API_ROOT}/repos/{where['owner']}/{where['name']}"
            f"/contents/{rel}")


def remote_sha(where: dict[str, str], rel: str, token: str, *,
               opener: Callable[..., Any] | None = None) -> str | None:
    """The blob sha of the file as it stands, or None if it is not there.

    Required by the Contents API to update an existing file — without it the
    request is a "create", and GitHub rejects that for a path that exists.
    """
    url = f"{_contents_url(where, rel)}?ref={where['branch']}"
    try:
        doc = _request(url, token, opener=opener)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    sha = doc.get("sha")
    return str(sha) if sha else None


def remote_body(where: dict[str, str], rel: str, token: str, *,
                opener: Callable[..., Any] | None = None) -> str | None:
    """The file's current content, decoded. Used to VERIFY a publish."""
    url = f"{_contents_url(where, rel)}?ref={where['branch']}"
    try:
        doc = _request(url, token, opener=opener)
    except urllib.error.HTTPError:
        return None
    encoded = doc.get("content")
    if not encoded:
        return None
    try:
        return base64.b64decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


# ------------------------------------------------------------- the publish
def audit_path() -> str:
    return os.path.join(paths.sub("reports"), "publish-history.json")


def record(entry: dict[str, Any]) -> None:
    """Append one publish attempt to the local history. Never raises.

    Kept whether the attempt succeeded or failed — a refused publish is
    exactly the kind of thing someone needs to be able to look up later.
    """
    history: list[dict[str, Any]] = []
    try:
        with open(audit_path(), "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, list):
            history = loaded
    except (OSError, ValueError):
        history = []
    history.append(entry)
    try:
        atomic_write_json(audit_path(), history[-200:])
    except OSError:
        pass


def history(limit: int = 25) -> list[dict[str, Any]]:
    try:
        with open(audit_path(), "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, ValueError):
        return []
    if not isinstance(loaded, list):
        return []
    return list(reversed(loaded))[:limit]


def describe() -> dict[str, Any]:
    """Everything the control room needs to render the publish panel, with
    no secrets and no network calls."""
    from . import credentials as cred

    where = target()
    try:
        has_token = bool(cred.CredentialVault().get(TOKEN_KEY))
    except cred.CredentialError:
        has_token = False
    local = validate_local()
    blockers = list(local["problems"])
    if not where["owner"] or not where["name"]:
        blockers.append(
            "no repository is configured — set 'Website repository' on the "
            "Settings page to owner/name")
    if not has_token:
        blockers.append(
            "no GitHub token is saved — add one on the Credentials page. A "
            "fine-grained token with Contents:write on that one repository "
            "is enough")
    return {
        "repo": where["repo"], "branch": where["branch"],
        "hasToken": has_token,
        "files": [{k: v for k, v in f.items() if k != "body"}
                  for f in local["files"]],
        "ready": not blockers,
        "blockers": blockers,
        "history": history(10),
    }


def publish(*, message: str | None = None, by: str = "anonymous",
            opener: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Upload the generated dataset to the site's repository.

    Every failure mode returns a dict rather than raising, because this runs
    as a background task whose result is rendered straight into the page.
    """
    started = time.time()
    where = target()
    entry: dict[str, Any] = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "by": by, "repo": where["repo"], "branch": where["branch"],
        "ok": False, "files": [], "error": None,
    }

    from . import credentials as cred
    try:
        token = cred.CredentialVault().get(TOKEN_KEY)
    except cred.CredentialError as exc:
        entry["error"] = f"the credential vault could not be read: {exc}"
        record(entry)
        return {"ok": False, "error": entry["error"]}

    state = describe()
    if not state["ready"]:
        entry["error"] = "; ".join(state["blockers"])
        record(entry)
        return {"ok": False, "error": entry["error"],
                "blockers": state["blockers"]}

    local = validate_local()
    if not local["ok"]:  # re-checked: describe() and this must agree
        entry["error"] = "; ".join(local["problems"])
        record(entry)
        return {"ok": False, "error": entry["error"]}

    note = (message or "").strip() or (
        f"data: publish from OWCS Comp Tracker {__version__}")
    results: list[dict[str, Any]] = []
    for spec in local["files"]:
        rel, body = spec["path"], spec["body"]
        try:
            existing = remote_sha(where, rel, token, opener=opener)
            if existing and remote_body(
                    where, rel, token, opener=opener) == body:
                results.append({"path": rel, "status": "unchanged",
                                "sha256": spec["sha256"]})
                continue
            payload: dict[str, Any] = {
                "message": f"{note}\n\nPublished by: {by}",
                "content": base64.b64encode(
                    body.encode("utf-8")).decode("ascii"),
                "branch": where["branch"],
            }
            if existing:
                payload["sha"] = existing
            response = _request(_contents_url(where, rel), token,
                                method="PUT", payload=payload, opener=opener)
        except urllib.error.HTTPError as exc:
            detail = _http_detail(exc)
            entry["error"] = f"{rel}: GitHub refused the upload ({detail})"
            entry["files"] = results
            record(entry)
            return {"ok": False, "error": entry["error"], "files": results}
        except (urllib.error.URLError, OSError, ValueError,
                TimeoutError) as exc:
            entry["error"] = f"{rel}: {type(exc).__name__}: {exc}"
            entry["files"] = results
            record(entry)
            return {"ok": False, "error": entry["error"], "files": results}

        commit = (response.get("commit") or {}).get("sha")
        # Verified, not assumed: read it back and compare.
        landed = remote_body(where, rel, token, opener=opener)
        if landed is not None and landed != body:
            entry["error"] = (f"{rel}: the upload reported success but the "
                              "file on the site does not match what was sent")
            entry["files"] = results
            record(entry)
            return {"ok": False, "error": entry["error"], "files": results}
        results.append({"path": rel, "status": "published",
                        "sha256": spec["sha256"], "commit": commit,
                        "verified": landed is not None})

    entry.update(ok=True, files=results,
                 seconds=round(time.time() - started, 1))
    record(entry)
    changed = [r for r in results if r["status"] == "published"]
    return {
        "ok": True, "files": results, "repo": where["repo"],
        "branch": where["branch"],
        "detail": (f"Published {len(changed)} file(s) to "
                   f"{where['repo']}@{where['branch']}. The public site "
                   "rebuilds in a minute or two."
                   if changed else
                   "The site is already showing this data — nothing to send."),
    }


def _http_detail(exc: urllib.error.HTTPError) -> str:
    """A GitHub error a person can act on, without leaking the token."""
    try:
        body = json.loads(exc.read().decode("utf-8"))
        message = str(body.get("message") or "")
    except Exception:
        message = ""
    if exc.code == 401:
        return "401 — the saved GitHub token was rejected. Replace it on the "\
               "Credentials page."
    if exc.code == 403:
        return "403 — the token is valid but not allowed to write to this "\
               "repository. It needs Contents:write."
    if exc.code == 404:
        return "404 — the repository or branch does not exist, or the token "\
               "cannot see it. Check the Settings page."
    if exc.code == 409:
        return "409 — the file changed on the site since this copy was read. "\
               "Publish again to pick up the newer version."
    return f"{exc.code} {message}".strip()
