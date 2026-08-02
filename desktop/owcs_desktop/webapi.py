"""
webapi.py — every desktop operation, as JSON, for the control room.

`pipeline/serve.py` already serves the site and the pipeline's own job API.
This module adds the *appliance* half of it — health, credentials, storage,
backups, updates, repair, the queue, the review inbox and the calibration
editor — as two pure functions:

    handle_get(path, query)  -> (status, payload) | None
    handle_post(path, body)  -> (status, payload) | None

Returning None means "not my route", so serve.py falls through to its existing
handling. Keeping the logic here rather than inside the request handler is what
lets the whole surface be tested without binding a socket, and it keeps
serve.py's diff to a single dispatch block.

Two invariants hold across every route:

  * **No secret ever leaves.** Credential routes report presence and
    protection; the GET can never return a value, and there is no route that
    returns one. `mask()` is applied to error text as a second line of
    defence.
  * **Nothing here publishes.** The routes that could change public data
    (review decisions, publish) go through the same gates the CLI uses —
    review decisions write `manual_override`, and publishing still runs the
    existing validation + backup + atomic replace path.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from typing import Any, Callable
from urllib.parse import parse_qs

from . import (__version__, autostart, backup, credentials, health, paths,
               repair, storage, supervisor, updates)
from .settings import Settings, SettingsError

PREFIX = "/api/desktop/"

#: Anything shaped like a key, redacted before it can reach a log or a page.
_SECRET_RE = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")


def mask(text: str) -> str:
    """Redact key-shaped runs from free text before returning it."""
    return _SECRET_RE.sub("[redacted]", str(text or ""))


# --------------------------------------------------------- long operations
class BackgroundTask:
    """One long-running operation (the readiness test, an update download).

    The control room polls `/api/desktop/task`. Only one runs at a time, so a
    user hammering a button cannot start five readiness tests.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state: dict[str, Any] = {"running": False, "name": None,
                                      "startedAt": None, "finishedAt": None,
                                      "result": None, "error": None}

    def start(self, name: str, fn: Callable[[], Any]) -> tuple[bool, str]:
        with self.lock:
            if self.state["running"]:
                return False, f"{self.state['name']} is already running"
            self.state = {"running": True, "name": name,
                          "startedAt": time.time(), "finishedAt": None,
                          "result": None, "error": None}

        def _run() -> None:
            try:
                result = fn()
                error = None
            except Exception as exc:
                result, error = None, mask(f"{type(exc).__name__}: {exc}")
            with self.lock:
                self.state.update(running=False, finishedAt=time.time(),
                                  result=result, error=error)

        threading.Thread(target=_run, name=f"owcs-{name}", daemon=True).start()
        return True, name

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            out = dict(self.state)
        if out.get("startedAt"):
            end = out.get("finishedAt") or time.time()
            out["elapsedSeconds"] = round(end - out["startedAt"], 1)
        return out


TASK = BackgroundTask()


# ------------------------------------------------------------------ queue
def _automation_rows(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    path = paths.automation_db()
    if not os.path.exists(path):
        return []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []
    try:
        return list(con.execute(sql, args).fetchall())
    except sqlite3.Error:
        return []
    finally:
        con.close()


def _content_rows(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    path = paths.content_db()
    if not os.path.exists(path):
        return []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []
    try:
        return list(con.execute(sql, args).fetchall())
    except sqlite3.Error:
        return []
    finally:
        con.close()


#: How far through the pipeline each state is, for the progress bar. Ordered
#: by the real forward path so a job can only ever move rightwards.
STATE_PROGRESS = {
    "DISCOVERED": 5, "SCHEDULED": 8, "AWAITING_BROADCAST": 10, "LIVE": 12,
    "RECORDING": 15, "ARCHIVED": 20, "DOWNLOADING": 35, "DOWNLOADED": 50,
    "SEGMENTING": 60, "NEEDS_LAYOUT": 62, "NEEDS_TEMPLATES": 64,
    "NEEDS_REVIEW": 70, "READY_FOR_DETECTION": 75, "PROCESSING": 85,
    "APPROVED": 95, "PUBLISHED": 100, "PARTIAL": 80,
    "FAILED": 0, "RETRY_SCHEDULED": 0, "FAILED_PERMANENT": 0,
    "IGNORED": 0, "CANCELLED": 0,
}

#: What a human must do next, in plain language, for each waiting state.
GATE_HELP = {
    "NEEDS_LAYOUT": "A new broadcast layout was calibrated and needs your "
                    "approval, or calibration could not reach confidence — "
                    "open the calibration editor.",
    "NEEDS_REVIEW": "Segments or detections are waiting in the review inbox.",
    "NEEDS_TEMPLATES": "This broadcast has no hero templates yet.",
    "APPROVED": "Ready to publish.",
    "FAILED": "Failed — retry, or open the log to see why.",
    "FAILED_PERMANENT": "Gave up after the retry limit.",
}


def queue_report() -> dict[str, Any]:
    rows = _automation_rows(
        """SELECT job_key, kind, state, priority, worker_id, attempts,
                  next_retry_at, last_error, payload, created_at, updated_at
           FROM jobs ORDER BY updated_at DESC LIMIT 200""")
    jobs, counts = [], {}
    for row in rows:
        state = row["state"]
        counts[state] = counts.get(state, 0) + 1
        try:
            payload = json.loads(row["payload"] or "{}")
        except ValueError:
            payload = {}
        jobs.append({
            "jobKey": row["job_key"],
            "kind": row["kind"],
            "state": state,
            "progress": STATE_PROGRESS.get(state, 0),
            "waitingOnYou": state in GATE_HELP and state not in (
                "FAILED", "FAILED_PERMANENT"),
            "gateHelp": GATE_HELP.get(state),
            "attempts": row["attempts"],
            "nextRetryAt": row["next_retry_at"],
            "lastError": mask(row["last_error"] or "")[:400] or None,
            "worker": row["worker_id"],
            "title": payload.get("title") or payload.get("videoTitle"),
            "url": payload.get("url"),
            "videoId": payload.get("videoId"),
            "updatedAt": row["updated_at"],
            "createdAt": row["created_at"],
        })
    active = [j for j in jobs if j["state"] not in (
        "PUBLISHED", "CANCELLED", "IGNORED", "FAILED_PERMANENT")]
    return {
        "jobs": jobs,
        "counts": counts,
        "active": len(active),
        "waitingOnYou": sum(1 for j in jobs if j["waitingOnYou"]),
        "total": len(jobs),
    }


# ---------------------------------------------------------- review inbox
def review_inbox(*, limit: int = 200) -> dict[str, Any]:
    """Everything quarantined for a human, from all three sources.

    The pipeline already refuses to publish these; this is the graphical
    surface that lets a person clear them without a command line.
    """
    stints = _content_rows(
        """SELECT s.id, s.match_id, s.map_result_id, s.team_id, s.side, s.slot,
                  s.hero_id, s.start_offset, s.end_offset, s.n_obs,
                  s.mean_conf, s.min_conf, s.status, s.evidence_start,
                  s.evidence_end, s.notes, s.detector_version
           FROM hero_stints s
           WHERE s.status = 'needs-review' AND s.manual_override = 0
           ORDER BY s.match_id, s.start_offset LIMIT ?""", (limit,))
    swaps = _content_rows(
        """SELECT id, match_id, map_result_id, team_id, side, slot, from_hero,
                  to_hero, offset_seconds, confidence, status, reason,
                  evidence_before, evidence_after, detector_version
           FROM hero_swaps
           WHERE status = 'uncertain' AND manual_override = 0
           ORDER BY match_id, offset_seconds LIMIT ?""", (limit,))
    tasks = _automation_rows(
        """SELECT id, kind, ref_key, lane, state, payload, created_at
           FROM review_tasks WHERE resolved_at IS NULL
           ORDER BY created_at DESC LIMIT ?""", (limit,))

    def _stint(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "kind": "stint", "id": r["id"], "matchId": r["match_id"],
            "mapResultId": r["map_result_id"], "teamId": r["team_id"],
            "side": r["side"], "slot": r["slot"], "heroId": r["hero_id"],
            "startOffset": r["start_offset"], "endOffset": r["end_offset"],
            "observations": r["n_obs"], "meanConfidence": r["mean_conf"],
            "minConfidence": r["min_conf"], "status": r["status"],
            "evidence": [p for p in (r["evidence_start"], r["evidence_end"]) if p],
            "detector": r["detector_version"], "notes": r["notes"],
            "why": _why_quarantined(r["mean_conf"], r["min_conf"], r["n_obs"]),
        }

    def _swap(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "kind": "swap", "id": r["id"], "matchId": r["match_id"],
            "mapResultId": r["map_result_id"], "teamId": r["team_id"],
            "side": r["side"], "slot": r["slot"], "fromHero": r["from_hero"],
            "toHero": r["to_hero"], "offsetSeconds": r["offset_seconds"],
            "confidence": r["confidence"], "status": r["status"],
            "reason": r["reason"], "detector": r["detector_version"],
            "evidence": [p for p in (r["evidence_before"], r["evidence_after"]) if p],
            "why": r["reason"] or "temporal consensus was not decisive",
        }

    def _task(r: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(r["payload"] or "{}")
        except ValueError:
            payload = {}
        return {"kind": "task", "id": r["id"], "taskKind": r["kind"],
                "refKey": r["ref_key"], "lane": r["lane"], "state": r["state"],
                "payload": payload, "createdAt": r["created_at"]}

    items = ([_stint(r) for r in stints] + [_swap(r) for r in swaps]
             + [_task(r) for r in tasks])
    return {
        "items": items,
        "counts": {"stints": len(stints), "swaps": len(swaps),
                   "tasks": len(tasks), "total": len(items)},
        "evidenceRoot": paths.app_root(),
    }


def _why_quarantined(mean_conf: Any, min_conf: Any, n_obs: Any) -> str:
    reasons = []
    settings = Settings()
    floor = float(settings.get("autoPublishMinConfidence"))
    repeats = int(settings.get("autoPublishMinRepeats"))
    try:
        if mean_conf is not None and float(mean_conf) < floor:
            reasons.append(f"mean confidence {float(mean_conf):.2f} is below "
                           f"the {floor:.2f} auto-publish floor")
        if min_conf is not None and float(min_conf) < floor * 0.9:
            reasons.append(f"weakest frame scored {float(min_conf):.2f}")
        if n_obs is not None and int(n_obs) < repeats:
            reasons.append(f"only {int(n_obs)} agreeing sample(s); "
                           f"{repeats} are required")
    except (TypeError, ValueError):
        pass
    return "; ".join(reasons) or "held for review by the detection gate"


def review_decide(*, kind: str, item_id: int, decision: str,
                  reviewer: str) -> dict[str, Any]:
    """Record a human decision. Always sets manual_override so a later
    detector run can never silently overwrite it."""
    reviewer = (reviewer or "").strip()
    if not reviewer:
        return {"ok": False, "error": "a reviewer name is required — every "
                                      "decision is attributed"}
    if decision not in ("approve", "reject"):
        return {"ok": False, "error": "decision must be approve or reject"}

    path = paths.content_db()
    if kind in ("stint", "swap"):
        if not os.path.exists(path):
            return {"ok": False, "error": "the results database does not exist yet"}
        con = sqlite3.connect(path)
        try:
            if kind == "stint":
                new = "reviewed" if decision == "approve" else "rejected"
                cur = con.execute(
                    """UPDATE hero_stints
                       SET status = ?, manual_override = 1,
                           notes = COALESCE(notes || ' | ', '') || ?,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (new, f"{decision}d by {reviewer}", int(item_id)))
            else:
                new = "confirmed" if decision == "approve" else "rejected"
                cur = con.execute(
                    """UPDATE hero_swaps
                       SET status = ?, manual_override = 1,
                           reason = COALESCE(reason || ' | ', '') || ?
                       WHERE id = ?""",
                    (new, f"{decision}d by {reviewer}", int(item_id)))
            con.commit()
        except sqlite3.Error as exc:
            return {"ok": False, "error": mask(str(exc))}
        finally:
            con.close()
        if cur.rowcount == 0:
            return {"ok": False, "error": f"no {kind} with id {item_id}"}
        return {"ok": True, "kind": kind, "id": item_id, "status": new,
                "reviewer": reviewer}

    if kind == "task":
        apath = paths.automation_db()
        if not os.path.exists(apath):
            return {"ok": False, "error": "the job database does not exist yet"}
        con = sqlite3.connect(apath)
        try:
            cur = con.execute(
                """UPDATE review_tasks
                   SET state = ?, resolved_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND resolved_at IS NULL""",
                ("APPROVED" if decision == "approve" else "REJECTED",
                 int(item_id)))
            con.commit()
        except sqlite3.Error as exc:
            return {"ok": False, "error": mask(str(exc))}
        finally:
            con.close()
        if cur.rowcount == 0:
            return {"ok": False, "error": f"no open review task with id {item_id}"}
        return {"ok": True, "kind": kind, "id": item_id, "reviewer": reviewer}

    return {"ok": False, "error": f"unknown review kind: {kind!r}"}


# ----------------------------------------------------------- calibration
_LAYOUT_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _layout_path(name: str) -> str:
    """Resolve a layout name to a path inside the layouts directory.

    Refuses anything that is not a bare name, so a crafted request cannot read
    or write outside the layouts folder.
    """
    if not _LAYOUT_NAME_RE.match(name or ""):
        raise ValueError("layout names may contain letters, digits, "
                         "underscore and hyphen only")
    return os.path.join(paths.app_root(), "layouts", f"{name}.json")


#: The top-level layout keys that carry editable geometry. A layout spells
#: geometry three different ways depending on the key — a bare `[x,y,w,h]`,
#: an object with a `rect` (so it can also carry a template path and a
#: threshold), or a list of either — so the traversal below handles all three
#: rather than assuming one. Everything NOT named here (thresholds, the
#: hud_probe provenance, template paths, the long `_note` justifications) is
#: preserved byte-for-byte: dragging a box can never rewrite the evidence
#: that says how the layout was calibrated.
GEOMETRY_KEYS = (
    ("slots_a", "Team A slot"),
    ("slots_b", "Team B slot"),
    ("anchor", "Gameplay anchor"),
    ("replay", "Replay marker"),
    ("reject", "Reject marker"),
    ("round_emblem", "Round emblem"),
    ("score_map", "Score / map region"),
    ("scoreboard", "Scoreboard"),
    ("highlight", "Highlight banner"),
)

#: How a box is coloured in the editor, by top-level key.
_BOX_KIND = {"slots_a": "a", "slots_b": "b", "anchor": "anchor",
             "replay": "replay", "reject": "replay", "highlight": "replay"}


def _is_rect(value: Any) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) == 4
            and all(isinstance(n, (int, float)) and not isinstance(n, bool)
                    for n in value)
            and value[2] > 0 and value[3] > 0
            and value[0] >= 0 and value[1] >= 0)


def _walk_geometry(node: Any, path: list) -> list[tuple[list, list]]:
    """Every rect under `node`, as (path, rect). Path is a list of keys and
    indices that `_rect_at`/`_set_rect_at` can follow back."""
    found: list[tuple[list, list]] = []
    if _is_rect(node):
        found.append((path, list(node)))
    elif isinstance(node, dict) and _is_rect(node.get("rect")):
        found.append((path + ["rect"], list(node["rect"])))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            found.extend(_walk_geometry(item, path + [i]))
    return found


def _set_rect_at(doc: Any, path: list, rect: list) -> bool:
    """Write `rect` at `path`. False when the path no longer exists (the file
    changed under the editor) — the caller reports that instead of guessing."""
    node = doc
    for step in path[:-1]:
        try:
            node = node[step]
        except (KeyError, IndexError, TypeError):
            return False
    try:
        node[path[-1]] = rect
    except (KeyError, IndexError, TypeError):
        return False
    return True


def layout_boxes(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """The flat, editable box list the calibration editor renders."""
    boxes = []
    for key, label in GEOMETRY_KEYS:
        if key not in doc:
            continue
        found = _walk_geometry(doc[key], [key])
        for n, (path, rect) in enumerate(found, start=1):
            name = label if len(found) == 1 else f"{label} {n}"
            # A reject marker names itself; use that rather than an index.
            container = doc[key]
            if isinstance(container, list) and len(path) >= 2 and \
                    isinstance(path[1], int) and \
                    isinstance(container[path[1]], dict) and \
                    container[path[1]].get("label"):
                name = f"{label}: {container[path[1]]['label']}"
            boxes.append({
                "id": "/".join(str(p) for p in path),
                "path": path,
                "key": key,
                "label": name,
                "kind": _BOX_KIND.get(key, "misc"),
                "rect": [int(round(v)) for v in rect],
            })
    return boxes


def calibration_list() -> dict[str, Any]:
    """Every layout, with whether it is really calibrated or a hand-guessed
    starter — the editor needs to show that difference honestly."""
    directory = os.path.join(paths.app_root(), "layouts")
    out = []
    if os.path.isdir(directory):
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(directory, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
            except (OSError, ValueError) as exc:
                out.append({"name": name[:-5], "error": mask(str(exc))})
                continue
            probe = doc.get("hud_probe") or {}
            calib = doc.get("calibration") or {}
            # The calibrator's own residuals are the honest confidence signal;
            # a lower row residual is a tighter grid fit.
            residuals = [r.get("residual") for r in
                         (calib.get("chip_row_a"), calib.get("chip_row_b"))
                         if isinstance(r, dict) and r.get("residual") is not None]
            out.append({
                "name": name[:-5],
                "frameWidth": doc.get("frame_width"),
                "frameHeight": doc.get("frame_height"),
                "calibrated": bool(probe),
                "version": calib.get("version"),
                "sourceId": calib.get("source_id"),
                "framesUsed": calib.get("frames_used"),
                "residual": round(max(residuals), 4) if residuals else None,
                "manualEdits": len(doc.get("manual_edits") or []),
                "slots": len(doc.get("slots_a") or []) +
                         len(doc.get("slots_b") or []),
                "boxes": len(layout_boxes(doc)),
                "matchThreshold": doc.get("match_threshold"),
            })
    return {"layouts": out, "directory": directory}


def calibration_get(name: str) -> dict[str, Any]:
    path = _layout_path(name)
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    return {
        "name": name, "path": path,
        "frameWidth": doc.get("frame_width") or 1280,
        "frameHeight": doc.get("frame_height") or 720,
        "boxes": layout_boxes(doc),
        "calibrated": bool(doc.get("hud_probe")),
        "editable": os.access(path, os.W_OK),
    }


#: Keys a layout must carry to be worth importing at all.
_REQUIRED_LAYOUT_KEYS = ("frame_width", "frame_height", "slots_a", "slots_b")


def calibration_import(layout: Any, *, name: str,
                       importer: str) -> dict[str, Any]:
    """Accept a layout built by the browser wizard (calibrate.html).

    Validated rather than trusted: the file arrives from a download folder,
    so every field that matters is checked before it can influence detection.
    A layout that fails is refused with the reason, never partially written.

    Crucially, an imported layout may NOT carry a `hud_probe`. That key is
    what `layout_registry.is_calibrated()` treats as production-calibrated
    provenance, and it belongs solely to `pipeline/calibrate_source.py`. A
    browser-built layout records `browser_probe` instead and is stamped
    `calibration_source: "browser-import"`, so results from it go through
    review rather than being trusted automatically.
    """
    importer = (importer or "").strip()
    if not importer:
        return {"ok": False, "error": "a name is required — imported layouts "
                                      "are attributed"}
    if not isinstance(layout, dict):
        return {"ok": False, "error": "that file is not a layout"}

    missing = [k for k in _REQUIRED_LAYOUT_KEYS if k not in layout]
    if missing:
        return {"ok": False,
                "error": f"that file is missing {', '.join(missing)} — it does "
                         "not look like a layout from the calibration wizard"}
    for key in ("frame_width", "frame_height"):
        if not isinstance(layout[key], int) or layout[key] <= 0:
            return {"ok": False, "error": f"{key} must be a positive whole number"}
    for key in ("slots_a", "slots_b"):
        slots = layout[key]
        if not isinstance(slots, list) or len(slots) != 5:
            return {"ok": False,
                    "error": f"{key} must hold exactly 5 hero slots, "
                             f"got {len(slots) if isinstance(slots, list) else '?'}"}
        for i, rect in enumerate(slots, 1):
            if not _is_rect(rect):
                return {"ok": False,
                        "error": f"{key} slot {i} is not a rectangle "
                                 f"[x, y, w, h] with a positive size"}
            x, y, w, h = rect
            if x + w > layout["frame_width"] or y + h > layout["frame_height"]:
                return {"ok": False,
                        "error": f"{key} slot {i} falls outside the frame"}

    target_name = name or str(layout.get("browser_probe", {}).get("source_id")
                              or "imported-broadcast")
    try:
        path = _layout_path(target_name)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if os.path.exists(path):
        return {"ok": False,
                "error": f"a layout named {target_name} already exists. Rename "
                         "it in the wizard, or edit the existing one in the "
                         "calibration editor."}

    doc = dict(layout)
    # Strip any provenance the file has no right to claim.
    doc.pop("hud_probe", None)
    doc["calibration_source"] = "browser-import"
    doc.setdefault("templates_dir", f"templates/{target_name}")
    doc["imported"] = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "by": importer,
        "note": "Built with the browser calibration wizard and imported here. "
                "Not production-calibrated: detections from this layout go "
                "through review.",
    }
    try:
        result = backup.atomic_publish(
            path, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    except OSError as exc:
        return {"ok": False, "error": mask(str(exc))}
    return {"ok": True, "name": target_name, "path": path,
            "bytes": result["bytes"],
            "detail": f"Imported {target_name}. It is ready to use, and its "
                      f"detections will go through review because it was "
                      f"calibrated in a browser rather than by the full "
                      f"pipeline."}


def calibration_save(name: str, boxes: list[dict[str, Any]], *,
                     editor: str) -> dict[str, Any]:
    """Save dragged geometry back to a layout, atomically, with a backup.

    Takes the same flat box list `calibration_get` hands out, so the editor
    never has to know how a layout nests its rectangles. Records who edited it
    and when, and marks the layout `manual-edit` so nothing downstream claims
    a hand-adjusted layout was computationally calibrated.
    """
    editor = (editor or "").strip()
    if not editor:
        return {"ok": False, "error": "a name is required — layout edits are "
                                      "attributed"}
    if not isinstance(boxes, list) or not boxes:
        return {"ok": False, "error": "no boxes supplied"}
    path = _layout_path(name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            current = json.load(f)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": mask(str(exc))}

    known = {b["id"]: b for b in layout_boxes(current)}
    changed, problems = [], []
    for box in boxes:
        box_id = str(box.get("id") or "")
        rect = box.get("rect")
        original = known.get(box_id)
        if original is None:
            problems.append(f"{box_id or '(unnamed)'} is not a box in this layout")
            continue
        if not _is_rect(rect):
            problems.append(
                f"{original['label']} must be [x, y, w, h] with a positive "
                f"size, got {rect!r}")
            continue
        rect = [int(round(v)) for v in rect]
        if rect == original["rect"]:
            continue
        if not _set_rect_at(current, original["path"], rect):
            problems.append(f"{original['label']} no longer exists in the file")
            continue
        changed.append({"id": box_id, "label": original["label"],
                        "from": original["rect"], "to": rect})

    if problems:
        return {"ok": False, "error": "; ".join(problems[:6]),
                "problems": problems}
    if not changed:
        return {"ok": True, "changed": [], "detail": "Nothing had moved."}

    history = current.setdefault("manual_edits", [])
    if isinstance(history, list):
        history.append({
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "by": editor,
            "boxes": [c["label"] for c in changed],
        })
    current["calibration_source"] = "manual-edit"

    snapshot = backup.create_snapshot(
        reason=f"layout edit: {name}",
        files=[(f"{name}.json", path)],
        keep=int(Settings().get("backupsToKeep")))
    try:
        result = backup.atomic_publish(
            path, json.dumps(current, indent=2, ensure_ascii=False) + "\n")
    except OSError as exc:
        return {"ok": False, "error": mask(str(exc))}
    return {"ok": True, "changed": changed, "path": path,
            "bytes": result["bytes"], "backup": snapshot["id"],
            "detail": f"Saved {len(changed)} box(es). The previous version is "
                      f"kept as backup {snapshot['id']}."}


# --------------------------------------------------------------- overview
def overview() -> dict[str, Any]:
    """The single call the control room's header polls."""
    beat = supervisor.read_heartbeat()
    settings = Settings()
    checks = health.run_checks(include_worker=False)
    queue = queue_report()
    inbox = review_inbox(limit=500)
    usage = storage.usage_report()
    return {
        "version": __version__,
        "appRoot": paths.app_root(),
        "dataRoot": paths.data_root(),
        "frozen": paths.is_frozen(),
        "worker": {
            "running": bool(beat and not beat.get("stale")),
            "heartbeat": beat,
        },
        "health": {"ok": checks["ok"], "counts": checks["counts"],
                   "blocking": checks["blocking"]},
        "queue": {"counts": queue["counts"], "active": queue["active"],
                  "waitingOnYou": queue["waitingOnYou"],
                  "total": queue["total"]},
        "review": inbox["counts"],
        "storage": {"totalGb": usage["totalGb"], "freeGb": usage["freeGb"],
                    "budgetGb": float(settings.get("maxStorageGb"))},
        "setupComplete": os.path.exists(paths.first_run_marker()),
        "autoStart": autostart.AutoStart().status(),
        "task": TASK.snapshot(),
    }


# ------------------------------------------------------------ publishing
def publish_status() -> dict[str, Any]:
    """What is published, when, and whether it can be rolled back."""
    export = os.path.join(paths.app_root(), "assets", "data",
                          "public_data.v1.js")
    info: dict[str, Any] = {"path": export, "exists": os.path.exists(export)}
    if info["exists"]:
        info["bytes"] = os.path.getsize(export)
        info["modified"] = time.strftime(
            "%Y-%m-%d %H:%M:%S UTC", time.gmtime(os.path.getmtime(export)))
        try:
            with open(export, "r", encoding="utf-8") as f:
                head = f.read(4000)
            demo = re.search(r'"demo"\s*:\s*(true|false)', head)
            info["demo"] = (demo.group(1) == "true") if demo else None
            generated = re.search(r"GENERATED by ([^\n]+)", head)
            info["generatedBy"] = generated.group(1).strip() if generated else None
        except OSError:
            pass
    runs = _automation_rows(
        """SELECT * FROM publication_runs ORDER BY id DESC LIMIT 20""")
    info["runs"] = [dict(r) for r in runs]
    info["backups"] = [
        {"id": s["id"], "createdAt": s["createdAt"], "reason": s["reason"],
         "valid": s["valid"], "bytes": s["totalBytes"]}
        for s in backup.list_snapshots()[:20]]
    return info


def export_public() -> dict[str, Any]:
    """Regenerate the public dataset — backup first, atomic replace, and
    verify the result parses before it is left in place."""
    app = paths.app_root()
    target = os.path.join(app, "assets", "data", "public_data.v1.js")
    snapshot = backup.create_snapshot(
        reason="pre-publish", keep=int(Settings().get("backupsToKeep")))

    env = dict(os.environ)
    paths.apply_environment(env=env)
    try:
        proc = subprocess.run(
            paths.python_command()
            + [os.path.join("pipeline", "export_data.py"), "--public"],
            cwd=app, env=env, capture_output=True, text=True, timeout=900,
            **paths.PIPE_TEXT)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": mask(str(exc)), "backup": snapshot["id"]}
    if proc.returncode != 0:
        return {"ok": False, "backup": snapshot["id"],
                "error": mask((proc.stderr or proc.stdout or "")[-3000:])}

    # The export must define the production global unconditionally; a fixture
    # fallback shape here would silently turn the live site into the demo.
    try:
        with open(target, "r", encoding="utf-8") as f:
            body = f.read()
    except OSError as exc:
        return {"ok": False, "error": mask(str(exc)), "backup": snapshot["id"]}
    if "window.OWCS_PUBLIC = {" not in body:
        rollback = backup.restore_snapshot(snapshot["id"])
        return {"ok": False, "backup": snapshot["id"], "rolledBack": rollback["ok"],
                "error": "the regenerated export did not define "
                         "window.OWCS_PUBLIC — rolled back"}
    return {"ok": True, "backup": snapshot["id"], "bytes": len(body),
            "detail": mask((proc.stdout or "")[-2000:])}


# ------------------------------------------------------------- the routes
def handle_get(path: str, query: str = "") -> tuple[int, dict[str, Any]] | None:
    if not path.startswith(PREFIX):
        return None
    route = path[len(PREFIX):].strip("/")
    params = parse_qs(query or "")

    try:
        if route == "overview":
            return 200, overview()
        if route == "health":
            return 200, health.run_checks()
        if route == "settings":
            s = Settings()
            return 200, {"values": s.values, "schema": s.schema_for_ui()}
        if route == "credentials":
            return 200, {"credentials": credentials.CredentialVault().describe()}
        if route == "storage":
            s = Settings()
            return 200, {
                "usage": storage.usage_report(),
                "plan": storage.plan_prune(
                    retention_days=int(s.get("rawMediaRetentionDays")),
                    budget_gb=float(s.get("maxStorageGb"))),
            }
        if route == "backups":
            return 200, {"backups": backup.list_snapshots()}
        if route == "updates":
            return 200, updates.check_for_update(
                channel=str(Settings().get("updateChannel")))
        if route == "repairs":
            return 200, {"actions": repair.list_actions()}
        if route == "queue":
            return 200, queue_report()
        if route == "review":
            return 200, review_inbox()
        if route == "calibration":
            name = (params.get("name") or [""])[0]
            if name:
                return 200, calibration_get(name)
            return 200, calibration_list()
        if route == "publish":
            return 200, publish_status()
        if route == "task":
            return 200, TASK.snapshot()
        if route == "logs":
            return 200, tail_logs(int((params.get("lines") or ["300"])[0]))
        if route == "paths":
            return 200, paths.describe()
        if route == "intake/classify":
            from . import intake
            return 200, intake.classify((params.get("q") or [""])[0])
    except ValueError as exc:
        return 400, {"ok": False, "error": mask(str(exc))}
    except Exception as exc:
        return 500, {"ok": False, "error": mask(f"{type(exc).__name__}: {exc}")}
    return 404, {"ok": False, "error": f"unknown desktop route: {route}"}


def tail_logs(lines: int = 300) -> dict[str, Any]:
    lines = max(10, min(int(lines), 5000))
    out = []
    log_dir = paths.sub("logs")
    if os.path.isdir(log_dir):
        for name in sorted(os.listdir(log_dir)):
            path = os.path.join(log_dir, name)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    body = f.readlines()[-lines:]
            except OSError:
                continue
            out.append({"name": name, "bytes": os.path.getsize(path),
                        "lines": [mask(ln.rstrip()) for ln in body]})
    return {"logs": out, "directory": log_dir}


def handle_post(path: str, body: dict[str, Any] | None
                ) -> tuple[int, dict[str, Any]] | None:
    if not path.startswith(PREFIX):
        return None
    route = path[len(PREFIX):].strip("/")
    body = body or {}

    try:
        if route == "settings":
            patch = body.get("settings")
            if not isinstance(patch, dict) or not patch:
                return 400, {"ok": False, "error": "no settings supplied"}
            values = Settings().update(patch)
            # Autostart is the one setting with an effect outside the file.
            if "autoStart" in patch:
                autostart.AutoStart().sync(bool(values["autoStart"]))
            return 200, {"ok": True, "values": values}

        if route == "credentials":
            name, value = body.get("name"), body.get("value")
            if not name:
                return 400, {"ok": False, "error": "no credential named"}
            vault = credentials.CredentialVault()
            vault.set(str(name), "" if value is None else str(value))
            return 200, {"ok": True, "credentials": vault.describe()}

        if route == "storage/prune":
            s = Settings()
            plan = storage.plan_prune(
                retention_days=int(s.get("rawMediaRetentionDays")),
                budget_gb=float(s.get("maxStorageGb")))
            return 200, {"ok": True, "plan": plan,
                         "applied": storage.apply_prune(plan)}

        if route == "backups/create":
            return 200, {"ok": True, "backup": backup.create_snapshot(
                reason=str(body.get("reason") or "manual"),
                keep=int(Settings().get("backupsToKeep")))}

        if route == "backups/restore":
            snapshot_id = str(body.get("id") or "")
            if not snapshot_id:
                return 400, {"ok": False, "error": "no backup id supplied"}
            return 200, backup.restore_snapshot(snapshot_id)

        if route == "updates/download":
            info = updates.check_for_update(
                channel=str(Settings().get("updateChannel")))
            if not info.get("available"):
                return 200, {"ok": False,
                             "error": info.get("detail") or "no update available"}
            started, detail = TASK.start(
                "update-download", lambda: updates.download_update(info))
            return (200 if started else 409), {"ok": started, "detail": detail}

        if route == "updates/apply":
            path_arg = str(body.get("path") or "")
            if not path_arg:
                snapshot = TASK.snapshot()
                path_arg = ((snapshot.get("result") or {}) or {}).get("path", "")
            if not path_arg:
                return 400, {"ok": False,
                             "error": "download and verify an installer first"}
            return 200, updates.apply_update(path_arg)

        if route == "repair":
            action = str(body.get("action") or "")
            kwargs = {}
            if action == "repair.autostart" and "enable" in body:
                kwargs["enable"] = bool(body["enable"])
            if action == "repair.restore-backup" and body.get("id"):
                kwargs["snapshot_id"] = str(body["id"])
            return 200, repair.run(action, **kwargs)

        if route == "worker/start":
            return 200, repair.run("repair.start-worker")
        if route == "worker/stop":
            supervisor.request_stop()
            return 200, {"ok": True,
                         "detail": "asked the background service to stop after "
                                   "its current step"}
        # There is no separate "resume" route: `worker/start` runs
        # repair.start-worker, which clears the pause flag before spawning.
        # A second route that did the same thing was one more place for the
        # two to drift apart.

        if route == "review/decide":
            return 200, review_decide(
                kind=str(body.get("kind") or ""),
                item_id=int(body.get("id") or 0),
                decision=str(body.get("decision") or ""),
                reviewer=str(body.get("reviewer") or ""))

        if route == "calibration/save":
            return 200, calibration_save(
                str(body.get("name") or ""),
                body.get("boxes") or [],
                editor=str(body.get("editor") or ""))

        if route == "calibration/import":
            return 200, calibration_import(
                body.get("layout"),
                name=str(body.get("name") or ""),
                importer=str(body.get("importer") or ""))

        if route == "readiness":
            started, detail = TASK.start("readiness", health.run_readiness_test)
            return (200 if started else 409), {"ok": started, "detail": detail}

        if route == "publish/export":
            started, detail = TASK.start("publish", export_public)
            return (200 if started else 409), {"ok": started, "detail": detail}

        if route == "intake/submit":
            from . import intake
            text = str(body.get("input") or "")
            requested_by = str(body.get("requestedBy") or "").strip()
            if not requested_by:
                return 400, {"ok": False,
                             "error": "a name is required — every intake is "
                                      "attributed in the audit trail"}
            # Submitting can take a while (a playlist listing is a network
            # call), so it runs as the shared background task.
            started, detail = TASK.start(
                "intake",
                lambda: intake.submit(text, requested_by=requested_by,
                                      limit=int(body.get("limit") or 25)))
            return (200 if started else 409), {"ok": started, "detail": detail}

        if route == "setup/complete":
            marker = paths.first_run_marker()
            from .settings import atomic_write_json
            atomic_write_json(marker, {
                "completedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "version": __version__,
                "by": str(body.get("by") or "")[:120],
            })
            return 200, {"ok": True, "marker": marker}
    except SettingsError as exc:
        return 400, {"ok": False, "error": mask(str(exc))}
    except (ValueError, credentials.CredentialError) as exc:
        return 400, {"ok": False, "error": mask(str(exc))}
    except Exception as exc:
        return 500, {"ok": False, "error": mask(f"{type(exc).__name__}: {exc}")}
    return 404, {"ok": False, "error": f"unknown desktop route: {route}"}
