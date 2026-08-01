"""
backup.py — snapshots, atomic publishing, and a rollback that actually works.

Publishing is the one moment this application can damage something a user
cares about: it rewrites the public dataset that the website renders. So it is
the one place that gets the full treatment.

  **Snapshot first.** `create_snapshot()` copies the content database, the job
  database and the current public dataset into a timestamped folder, records a
  SHA-256 of each file, and writes a manifest. Snapshots are pruned by count,
  oldest first, never below one.

  **Publish atomically.** `atomic_publish()` writes the new bytes to a
  temporary file in the *same directory* (so the replace is a same-filesystem
  rename), fsyncs it, verifies the bytes it just wrote read back identical,
  and only then `os.replace`s it over the target. `os.replace` is atomic on
  both Windows and POSIX, so a reader either sees the whole old file or the
  whole new one. A crash mid-publish cannot produce a half-written dataset,
  which for a `window.OWCS_PUBLIC = {…}` script would mean a website that
  throws on load.

  **Roll back by hash.** `restore_snapshot()` puts every file in a snapshot
  back — itself through `atomic_publish` — and verifies each restored file
  against the manifest's hash. A snapshot whose bytes no longer match its
  manifest is refused rather than restored, because restoring a corrupted
  backup over live data is worse than leaving the live data alone.

Nothing here deletes user data: a restore snapshots the *current* state first,
so rolling back is itself reversible.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import tempfile
from typing import Any

from . import paths
from .settings import atomic_write_json

MANIFEST_NAME = "manifest.json"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _stamp() -> str:
    return _utcnow().strftime("%Y%m%d-%H%M%S")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------- atomic publish
def atomic_publish(target: str, data: bytes | str, *,
                   encoding: str = "utf-8") -> dict[str, Any]:
    """Replace `target` with `data`, atomically, verifying what was written.

    Returns {"path", "bytes", "sha256", "replaced"}. Raises OSError on a
    failure — and on any failure the original file is still intact, because
    the target is only touched by the final `os.replace`.
    """
    payload = data.encode(encoding) if isinstance(data, str) else data
    target = os.path.abspath(target)
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    existed = os.path.exists(target)

    fd, tmp = tempfile.mkstemp(prefix=".publish-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        # Read the temp file back before it becomes the live file. A disk that
        # silently truncated the write is caught here, not by a visitor.
        with open(tmp, "rb") as f:
            written = f.read()
        if written != payload:
            raise OSError(
                f"verification failed writing {target}: wrote {len(payload)} "
                f"bytes, read back {len(written)}")
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return {"path": target, "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(), "replaced": existed}


def publish_file(target: str, source: str) -> dict[str, Any]:
    """Atomically publish the contents of an existing file."""
    with open(source, "rb") as f:
        return atomic_publish(target, f.read())


# ----------------------------------------------------------- what we back up
def default_backup_set(*, app: str | None = None) -> list[tuple[str, str]]:
    """[(logical name, absolute path)] — the files a snapshot captures."""
    app = app or paths.app_root()
    return [
        ("content.sqlite", paths.content_db()),
        ("automation.sqlite", paths.automation_db()),
        ("public_data.v1.js",
         os.path.join(app, "assets", "data", "public_data.v1.js")),
        ("settings.json", paths.settings_file()),
    ]


# ------------------------------------------------------------- snapshots
def create_snapshot(*, reason: str = "manual", keep: int = 10,
                    files: list[tuple[str, str]] | None = None,
                    root: str | None = None) -> dict[str, Any]:
    """Copy every backed-up file into a new timestamped snapshot."""
    root = root or paths.sub("backups")
    os.makedirs(root, exist_ok=True)
    files = files if files is not None else default_backup_set()

    snapshot_id = _stamp()
    directory = os.path.join(root, snapshot_id)
    suffix = 1
    while os.path.exists(directory):  # two snapshots inside one second
        snapshot_id = f"{_stamp()}-{suffix}"
        directory = os.path.join(root, snapshot_id)
        suffix += 1
    os.makedirs(directory)

    entries = []
    for name, src in files:
        if not os.path.exists(src):
            entries.append({"name": name, "source": src, "present": False})
            continue
        dst = os.path.join(directory, name)
        # SQLite: copy through the sqlite3 backup API when we can, so a
        # snapshot taken while the supervisor is mid-write is still a
        # consistent database rather than a torn page copy.
        if src.endswith(".sqlite"):
            _copy_sqlite(src, dst)
        else:
            shutil.copy2(src, dst)
        entries.append({
            "name": name, "source": src, "present": True,
            "bytes": os.path.getsize(dst), "sha256": sha256_file(dst),
        })

    manifest = {
        "id": snapshot_id,
        "createdAt": _utcnow().replace(microsecond=0).isoformat(),
        "reason": reason,
        "files": entries,
    }
    atomic_write_json(os.path.join(directory, MANIFEST_NAME), manifest)
    pruned = prune_snapshots(keep=keep, root=root)
    manifest["pruned"] = pruned
    return manifest


def _copy_sqlite(src: str, dst: str) -> None:
    import sqlite3
    try:
        source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        try:
            target = sqlite3.connect(dst)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
        return
    except sqlite3.Error:
        # Not a usable SQLite file (or locked in a way backup cannot handle) —
        # a byte copy is still better than no backup.
        shutil.copy2(src, dst)


def list_snapshots(*, root: str | None = None) -> list[dict[str, Any]]:
    """Every snapshot, newest first, each annotated with `valid`."""
    root = root or paths.sub("backups")
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root), reverse=True):
        directory = os.path.join(root, name)
        manifest_path = os.path.join(directory, MANIFEST_NAME)
        if not os.path.isfile(manifest_path):
            continue
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, ValueError):
            continue
        manifest["path"] = directory
        manifest["valid"] = verify_snapshot(name, root=root)["ok"]
        manifest["totalBytes"] = sum(
            int(e.get("bytes") or 0) for e in manifest.get("files", []))
        out.append(manifest)
    return out


def verify_snapshot(snapshot_id: str, *, root: str | None = None) -> dict[str, Any]:
    """Re-hash every file in a snapshot against its manifest."""
    root = root or paths.sub("backups")
    directory = os.path.join(root, snapshot_id)
    manifest_path = os.path.join(directory, MANIFEST_NAME)
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError) as exc:
        return {"ok": False, "id": snapshot_id, "problems": [str(exc)]}

    problems = []
    for entry in manifest.get("files", []):
        if not entry.get("present"):
            continue
        path = os.path.join(directory, entry["name"])
        if not os.path.exists(path):
            problems.append(f"{entry['name']} is missing from the snapshot")
            continue
        if sha256_file(path) != entry.get("sha256"):
            problems.append(f"{entry['name']} does not match its recorded hash")
    return {"ok": not problems, "id": snapshot_id, "problems": problems}


def restore_snapshot(snapshot_id: str, *, root: str | None = None,
                     files: list[tuple[str, str]] | None = None
                     ) -> dict[str, Any]:
    """Roll every backed-up file back to a snapshot.

    Refuses a snapshot that fails verification. Takes a snapshot of the
    CURRENT state first, so an unwanted rollback is itself reversible.
    """
    root = root or paths.sub("backups")
    check = verify_snapshot(snapshot_id, root=root)
    if not check["ok"]:
        return {"ok": False, "id": snapshot_id, "restored": [],
                "error": "snapshot failed verification and was not restored",
                "problems": check["problems"]}

    directory = os.path.join(root, snapshot_id)
    with open(os.path.join(directory, MANIFEST_NAME), "r", encoding="utf-8") as f:
        manifest = json.load(f)
    destinations = dict(files if files is not None else default_backup_set())

    # Snapshot the CURRENT state before overwriting it, over the SAME file set
    # we are about to restore. Taking it over the default set instead would
    # mean a restore of a narrower snapshot (a single layout, say) recorded a
    # "previous state" that did not contain the file being replaced — so the
    # advertised "a rollback is itself reversible" would quietly not hold for
    # exactly the restores most likely to be wrong.
    pre = create_snapshot(reason=f"pre-restore of {snapshot_id}", root=root,
                          files=list(destinations.items()))

    restored, errors = [], []
    for entry in manifest.get("files", []):
        if not entry.get("present"):
            continue
        name = entry["name"]
        target = destinations.get(name) or entry.get("source")
        if not target:
            errors.append({"name": name, "error": "no destination known"})
            continue
        src = os.path.join(directory, name)
        try:
            result = publish_file(target, src)
        except OSError as exc:
            errors.append({"name": name, "error": str(exc)})
            continue
        if result["sha256"] != entry.get("sha256"):
            errors.append({"name": name,
                           "error": "restored bytes did not match the manifest"})
            continue
        restored.append({"name": name, "target": target})

    return {"ok": not errors, "id": snapshot_id, "restored": restored,
            "errors": errors, "preRestoreSnapshot": pre["id"]}


def prune_snapshots(*, keep: int = 10, root: str | None = None) -> list[str]:
    """Delete the oldest snapshots beyond `keep`. Never goes below one."""
    root = root or paths.sub("backups")
    keep = max(1, int(keep))
    if not os.path.isdir(root):
        return []
    names = sorted(
        n for n in os.listdir(root)
        if os.path.isfile(os.path.join(root, n, MANIFEST_NAME)))
    doomed = names[:-keep] if len(names) > keep else []
    removed = []
    for name in doomed:
        shutil.rmtree(os.path.join(root, name), ignore_errors=True)
        removed.append(name)
    return removed
