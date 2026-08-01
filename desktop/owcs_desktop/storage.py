"""
storage.py — keeping a multi-gigabyte pipeline inside a budget the user set.

Broadcast VODs are the only large thing this application touches: a single
OWCS stream is several gigabytes, and the pipeline keeps a full-resolution
source plus a 360p scan proxy per job. Left alone that fills a drive in a
weekend. This module is what stops that happening, and it is deliberately
conservative about what it is allowed to delete.

**What may be deleted:** raw downloaded media and scan proxies belonging to
jobs that have finished (published, cancelled, permanently failed, ignored),
once older than the retention window — and, if the budget is still exceeded,
the oldest finished media first.

**What may never be deleted, by construction:** databases, evidence crops,
reports, quarantined detections, layouts, calibration frames, backups, logs.
Those are the audit trail. `PROTECTED_SUBDIRS` names them and every deletion
path asserts against it, so a future change cannot quietly widen the blast
radius — there is a test that proves a pruner pointed at the evidence
directory refuses.

`plan_prune()` decides and returns exactly what it would remove;
`apply_prune()` performs a plan. The control room shows the plan before
anything is deleted, and the supervisor applies plans automatically.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
from typing import Any, Callable

from . import paths

#: Directories whose contents are the audit trail. Never pruned.
PROTECTED_SUBDIRS = frozenset({
    "db", "evidence", "reports", "quarantine", "backups", "layouts",
    "calibration", "logs", "state",
})

#: Job states whose media is finished with and may expire.
FINISHED_STATES = frozenset({
    "PUBLISHED", "CANCELLED", "FAILED_PERMANENT", "IGNORED",
})

#: File extensions considered "raw media" for pruning purposes.
MEDIA_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".webm", ".m4a", ".ts", ".part", ".ytdl", ".f303", ".f251",
})


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def dir_size(path: str) -> int:
    """Total bytes under a directory. Missing directory is 0, not an error."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def free_gb(path: str) -> float:
    try:
        return shutil.disk_usage(path).free / (1024 ** 3)
    except OSError:
        return 0.0


def usage_report(*, root: str | None = None) -> dict[str, Any]:
    """Per-area disk usage, for the control room's storage panel."""
    root = root or paths.data_root()
    areas = []
    total = 0
    for name, leaf in sorted(paths.SUBDIRS.items()):
        p = os.path.join(root, leaf)
        size = dir_size(p)
        total += size
        areas.append({
            "area": name,
            "path": p,
            "bytes": size,
            "gb": round(size / (1024 ** 3), 3),
            "protected": name in PROTECTED_SUBDIRS,
        })
    return {
        "root": root,
        "totalBytes": total,
        "totalGb": round(total / (1024 ** 3), 3),
        "freeGb": round(free_gb(root), 2),
        "areas": areas,
    }


# ------------------------------------------------------------- job states
def _job_states(db_path: str | None = None) -> dict[str, str]:
    """{jobKey: state} from the automation DB. Empty on any problem — a
    pruner that cannot read the queue must assume every job is active and
    therefore delete nothing."""
    import sqlite3
    path = db_path or paths.automation_db()
    if not os.path.exists(path):
        return {}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    try:
        rows = con.execute("SELECT job_key, state FROM jobs").fetchall()
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    return {str(k): str(v) for k, v in rows}


def _media_dirs(media_root: str) -> list[str]:
    if not os.path.isdir(media_root):
        return []
    return [os.path.join(media_root, d) for d in sorted(os.listdir(media_root))
            if os.path.isdir(os.path.join(media_root, d))]


def _media_files(directory: str) -> list[str]:
    out = []
    for root, _dirs, files in os.walk(directory):
        for name in files:
            if os.path.splitext(name)[1].lower() in MEDIA_EXTENSIONS:
                out.append(os.path.join(root, name))
    return out


def _newest_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def plan_prune(*, media_root: str | None = None,
               retention_days: int = 3,
               budget_gb: float = 60.0,
               job_states: dict[str, str] | None = None,
               now: dt.datetime | None = None) -> dict[str, Any]:
    """Decide what to delete. Deletes nothing.

    Two passes, in order:
      1. **Expiry** — finished jobs whose media is older than the retention
         window.
      2. **Budget** — if media still exceeds the budget, the oldest remaining
         *finished* job media, oldest first, until it fits. Media belonging to
         an active job is never touched at any budget.
    """
    media_root = media_root or paths.sub("media")
    now = now or _utcnow()
    states = _job_states() if job_states is None else job_states
    cutoff = now.timestamp() - retention_days * 86400

    candidates = []
    active_bytes = 0
    for directory in _media_dirs(media_root):
        job_key = os.path.basename(directory)
        files = _media_files(directory)
        if not files:
            continue
        size = sum(os.path.getsize(f) for f in files
                   if os.path.exists(f))
        mtime = max((_newest_mtime(f) for f in files), default=0.0)
        state = states.get(job_key, "")
        finished = state in FINISHED_STATES
        if not finished:
            active_bytes += size
            continue
        candidates.append({
            "jobKey": job_key, "dir": directory, "files": files,
            "bytes": size, "mtime": mtime, "state": state,
            "expired": mtime < cutoff,
        })

    remove: list[dict[str, Any]] = []
    kept = []
    for entry in candidates:
        (remove if entry["expired"] else kept).append(entry)

    # Second pass: still over budget after expiry?
    budget_bytes = int(budget_gb * (1024 ** 3))
    remaining = active_bytes + sum(e["bytes"] for e in kept)
    if remaining > budget_bytes:
        for entry in sorted(kept, key=lambda e: e["mtime"]):
            if remaining <= budget_bytes:
                break
            entry = dict(entry)
            entry["reason"] = "over budget"
            remove.append(entry)
            remaining -= entry["bytes"]

    for entry in remove:
        entry.setdefault("reason", f"finished and older than {retention_days}d")

    return {
        "mediaRoot": media_root,
        "budgetGb": budget_gb,
        "retentionDays": retention_days,
        "activeBytes": active_bytes,
        "reclaimBytes": sum(e["bytes"] for e in remove),
        "reclaimGb": round(sum(e["bytes"] for e in remove) / (1024 ** 3), 3),
        "remove": [{"jobKey": e["jobKey"], "dir": e["dir"], "state": e["state"],
                    "bytes": e["bytes"], "gb": round(e["bytes"] / (1024 ** 3), 3),
                    "fileCount": len(e["files"]), "reason": e["reason"]}
                   for e in remove],
    }


class ProtectedPathError(RuntimeError):
    """Refused to delete something that is part of the audit trail."""


def _assert_prunable(target: str, *, root: str | None = None) -> None:
    """Refuse any path that is not inside the media area."""
    root = os.path.abspath(root or paths.data_root())
    target = os.path.abspath(target)
    media = os.path.abspath(paths.sub("media"))
    for name in PROTECTED_SUBDIRS:
        protected = os.path.abspath(os.path.join(root, paths.SUBDIRS[name]))
        if target == protected or target.startswith(protected + os.sep):
            raise ProtectedPathError(
                f"refusing to delete {target}: {name!r} holds the audit trail "
                "and is never pruned")
    if target != media and not target.startswith(media + os.sep):
        raise ProtectedPathError(
            f"refusing to delete {target}: only downloaded media under "
            f"{media} may be pruned")


def apply_prune(plan: dict[str, Any], *,
                remover: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Carry out a plan from `plan_prune`. Each removal is checked again
    against the protected list — the guard is at the point of deletion, not
    only at the point of planning."""
    remover = remover or (lambda p: shutil.rmtree(p, ignore_errors=False))
    removed, errors, freed = [], [], 0
    for entry in plan.get("remove", []):
        target = entry["dir"]
        try:
            _assert_prunable(target)
        except ProtectedPathError as exc:
            errors.append({"dir": target, "error": str(exc)})
            continue
        try:
            remover(target)
        except OSError as exc:
            errors.append({"dir": target, "error": str(exc)})
            continue
        removed.append(target)
        freed += int(entry.get("bytes", 0))
    return {
        "removed": removed, "errors": errors, "freedBytes": freed,
        "freedGb": round(freed / (1024 ** 3), 3),
    }


def enforce(settings, *, media_root: str | None = None,
            apply: bool = True) -> dict[str, Any]:
    """Plan (and by default apply) a prune using the user's settings."""
    plan = plan_prune(
        media_root=media_root,
        retention_days=int(settings.get("rawMediaRetentionDays")),
        budget_gb=float(settings.get("maxStorageGb")))
    if not apply or not plan["remove"]:
        return {"plan": plan, "applied": None}
    return {"plan": plan, "applied": apply_prune(plan)}
