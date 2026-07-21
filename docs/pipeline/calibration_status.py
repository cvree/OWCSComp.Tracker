#!/usr/bin/env python3
"""
calibration_status.py — one honest health readout per video source for the
Calibration Lab (calibration.html / GET /api/calibration).

For every real source in data/sources/video_sources.json this reports what
the auto-calibration + template pipeline actually has on disk and in the
DB — nothing is guessed:

  layout      file exists / valid JSON / native frame size
  confidence  the auto-calibrator's stored confidence (layout.calibration)
  hud_probe   present? (without it the gameplay filter reads zero frames)
  rejects     cut-from-broadcast marker count + whether the PNGs resolve
  emblem      round_emblem rect present (round segmentation)
  templates   per-source template dir: heroes covered / variant files
  roster      total heroes in the DB → coverage percentage
  reports     calibration sheet + ingest reports found for this source
  ingests     ingest_runs rows for this source (status counts)

Overall status: ok (calibrated + probed + templates), warn (usable but
incomplete), fail (missing/broken layout). Each status carries reasons.

CLI:  python3 pipeline/calibration_status.py [--json]
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

SOURCES_PATH = os.path.join(db.REPO_ROOT, "data", "sources",
                            "video_sources.json")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _load_sources() -> list[dict]:
    try:
        with open(SOURCES_PATH, "r", encoding="utf-8") as f:
            entries = json.load(f).get("sources", []) or []
    except (OSError, ValueError):
        return []
    return [s for s in entries if s.get("id")]


def _roster_size() -> int | None:
    db_path = os.path.join(db.REPO_ROOT, "data", "owcs.sqlite")
    if not os.path.exists(db_path):
        return None
    try:
        con = db.connect()
        return con.execute("SELECT COUNT(*) FROM heroes").fetchone()[0]
    except Exception:
        return None


def _ingest_counts(source_id: str) -> dict:
    db_path = os.path.join(db.REPO_ROOT, "data", "owcs.sqlite")
    if not os.path.exists(db_path):
        return {}
    try:
        con = db.connect()
        rows = con.execute(
            "SELECT status, COUNT(*) FROM ingest_runs WHERE source_id=? "
            "GROUP BY status", (source_id,)).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


def _template_stats(templates_dir: str | None) -> dict:
    out = {"dir": templates_dir, "exists": False, "heroes": 0, "files": 0,
           "heroIds": []}
    if not templates_dir:
        return out
    d = os.path.join(db.REPO_ROOT, templates_dir)
    if not os.path.isdir(d):
        return out
    name_re = re.compile(r"^([a-z0-9_-]+?)(?:\.(?:v\d+|[a-z]))?\.png$")
    heroes = set()
    files = 0
    for fn in os.listdir(d):
        m = name_re.match(fn)
        if m:
            heroes.add(m.group(1))
            files += 1
    out.update(exists=True, heroes=len(heroes), files=files,
               heroIds=sorted(heroes))
    return out


def _calibration_reports(source_id: str) -> list[str]:
    """Report folders whose name matches the source id loosely
    (owcs-jksix-qwc vs owcs_jksix_qwc)."""
    want = _norm(source_id)
    hits = []
    for d in sorted(glob.glob(os.path.join(db.REPO_ROOT, "reports",
                                           "calibration", "*"))):
        if os.path.isdir(d) and _norm(os.path.basename(d)) == want:
            for fn in ("sheet.png", "report.html", "index.html"):
                p = os.path.join(d, fn)
                if os.path.exists(p):
                    hits.append(os.path.relpath(p, db.REPO_ROOT)
                                .replace(os.sep, "/"))
    return hits


def source_status(src: dict, roster: int | None) -> dict:
    sid = src["id"]
    layout_rel = src.get("layout")
    entry: dict = {
        "id": sid,
        "title": src.get("title") or sid,
        "url": src.get("url"),
        "enabled": src.get("enabled", True),
        "mode": src.get("mode"),
        "layoutPath": layout_rel,
    }
    reasons: list[str] = []
    layout = None
    if not layout_rel:
        reasons.append("no layout assigned in video_sources.json")
    else:
        p = os.path.join(db.REPO_ROOT, layout_rel)
        if not os.path.exists(p):
            reasons.append(f"layout file missing: {layout_rel}")
        else:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    layout = json.load(f)
            except ValueError as e:
                reasons.append(f"layout is not valid JSON: {e}")

    calib = (layout or {}).get("calibration") or {}
    entry["confidence"] = calib.get("confidence")
    entry["calibratedAt"] = calib.get("calibrated_at") or calib.get("date")
    entry["frameSize"] = ([layout.get("frame_width"),
                           layout.get("frame_height")] if layout else None)
    entry["hudProbe"] = bool(layout and layout.get("hud_probe"))
    entry["roundEmblem"] = bool(layout and layout.get("round_emblem"))

    rejects = (layout or {}).get("reject") or []
    missing_markers = []
    for r in rejects:
        tpl = r.get("template")
        if tpl and not os.path.exists(os.path.join(db.REPO_ROOT, tpl)):
            missing_markers.append(tpl)
    entry["rejectMarkers"] = len(rejects)
    entry["rejectMarkersMissing"] = missing_markers

    tstats = _template_stats((layout or {}).get("templates_dir"))
    entry["templates"] = tstats
    entry["rosterSize"] = roster
    entry["rosterCoverage"] = (round(tstats["heroes"] / roster, 3)
                               if roster and tstats["heroes"] else 0.0)

    entry["calibrationReports"] = _calibration_reports(sid)
    entry["ingestRuns"] = _ingest_counts(sid)

    # honest overall grade
    if layout is None:
        status = "fail"
    else:
        if not entry["hudProbe"]:
            reasons.append("no hud_probe — the gameplay filter will read "
                           "zero frames; run the auto-calibrator")
        if entry["confidence"] is None:
            reasons.append("layout has no stored auto-calibration "
                           "confidence (hand-made or pre-calibrator)")
        if not tstats["exists"] or tstats["heroes"] == 0:
            reasons.append("no per-source hero templates harvested")
        elif roster and tstats["heroes"] < 10:
            reasons.append(f"template coverage is partial "
                           f"({tstats['heroes']} heroes) — enough only for "
                           "maps whose comps stay inside the harvested set")
        if not rejects:
            reasons.append("no cut-from-broadcast reject markers — "
                           "replays/highlights can fake comps")
        if missing_markers:
            reasons.append(f"{len(missing_markers)} reject marker PNG(s) "
                           "missing on disk")
        status = "ok" if not reasons else "warn"
        # a probe-less layout can't ingest at all — that outranks warn
        if not entry["hudProbe"]:
            status = "warn"
    entry["status"] = status
    entry["reasons"] = reasons
    return entry


def build_status() -> dict:
    roster = _roster_size()
    sources = [source_status(s, roster) for s in _load_sources()]
    return {
        "rosterSize": roster,
        "sources": sources,
        "counts": {
            "ok": sum(1 for s in sources if s["status"] == "ok"),
            "warn": sum(1 for s in sources if s["status"] == "warn"),
            "fail": sum(1 for s in sources if s["status"] == "fail"),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    status = build_status()
    if args.json:
        json.dump(status, sys.stdout, indent=1)
        print()
        return
    for s in status["sources"]:
        flag = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}[s["status"]]
        conf = (f"conf {s['confidence']:.2f}" if s["confidence"] is not None
                else "conf —")
        tpl = s["templates"]
        print(f"[{flag}] {s['id']:<22s} {conf}  probe "
              f"{'✓' if s['hudProbe'] else '✗'}  markers "
              f"{s['rejectMarkers']}  templates {tpl['heroes']} heroes/"
              f"{tpl['files']} files")
        for r in s["reasons"]:
            print(f"        - {r}")


if __name__ == "__main__":
    main()
