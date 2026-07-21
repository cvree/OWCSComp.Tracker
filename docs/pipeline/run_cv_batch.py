#!/usr/bin/env python3
"""
run_cv_batch.py — CV capture->detect->map_sync loop (see run_batch.py orchestrator).
every pending match, then export data.js for the site.

Designed for GitHub Actions (and identical local runs):
  - Idempotent & resumable: a match with leftover frames from a failed run
    resumes at detection; a match with snapshots is never re-captured;
    reruns never duplicate rows (frame-hash dedup, upserts).
  - Graceful degradation, exit 0: no pending matches, missing hero
    templates (pre-calibration), or an unavailable VOD are logged skips,
    not failures — a scheduled run should only fail on real errors.
  - Budgeted: at most --max VODs per run (free-runner time/disk budget);
    frames are deleted after a successful map sync, quarantine is kept.

Usage:
  python3 pipeline/run_batch.py --layout layouts/owcs-asia-2026.json
  python3 pipeline/run_batch.py --layout ... --match m01     # just one
  python3 pipeline/run_batch.py --layout ... --max 3
"""
from __future__ import annotations
import argparse
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import capture  # noqa: E402
import detect  # noqa: E402
import map_sync  # noqa: E402
import export_data  # noqa: E402
import apply_corrections  # noqa: E402

WORK_DIR = capture.WORK_DIR


def log(msg: str) -> None:
    print(f"[run_batch] {msg}", flush=True)


def frames_exist(match_id: str) -> bool:
    d = os.path.join(WORK_DIR, match_id, "frames")
    return os.path.isdir(d) and any(f.endswith(".png") for f in os.listdir(d))


def snapshots_exist(con, match_id: str) -> bool:
    return con.execute(
        "SELECT 1 FROM comp_snapshots WHERE match_id=? LIMIT 1", (match_id,)
    ).fetchone() is not None


def pending(con, only: str | None):
    rows = con.execute(
        """SELECT * FROM matches
           WHERE status='final' AND vod_url IS NOT NULL
           ORDER BY date, id"""
    ).fetchall()
    out = []
    for r in rows:
        if only and r["id"] != only:
            continue
        # pending = needs any stage: no snapshots yet, or leftover frames,
        # or snapshots still unassigned to maps
        unassigned = con.execute(
            "SELECT 1 FROM comp_snapshots WHERE match_id=? "
            "AND map_result_id IS NULL LIMIT 1", (r["id"],)).fetchone()
        if (not snapshots_exist(con, r["id"])) or frames_exist(r["id"]) \
                or unassigned:
            out.append(r)
    return out


def process_one(con, m, layout: dict, layout_path: str, interval: int) -> bool:
    """Run whatever stages this match still needs. True if it reached sync."""
    mid = m["id"]
    mdir = os.path.join(WORK_DIR, mid)
    frames_dir = os.path.join(mdir, "frames")

    # ---- capture (skip if resuming with frames, or already detected) ----
    if not frames_exist(mid) and not snapshots_exist(con, mid):
        video = os.path.join(mdir, "vod.mp4")
        os.makedirs(mdir, exist_ok=True)
        log(f"{mid}: downloading VOD")
        try:
            capture.download_vod(m["vod_url"], video)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            log(f"{mid}: SKIP — VOD not available yet ({e}). Will retry "
                f"next run.")
            return False
        log(f"{mid}: extracting + classifying frames")
        res = capture.process_video(video, frames_dir, layout)
        if os.path.exists(video):
            os.remove(video)
        log(f"{mid}: kept {len(res['kept'])} gameplay frames, "
            f"rejected {len(res['rejected'])}")
        if not res["kept"]:
            log(f"{mid}: SKIP — no gameplay frames kept; check layout "
                f"anchor rect/threshold with capture.py --dry-run.")
            return False

    # ---- detect (skip if this run is only finishing an unassigned sync) --
    if frames_exist(mid):
        lib = detect.load_templates()
        log(f"{mid}: detecting comps ({len(lib)} hero templates)")
        detect.process_match(con, mid, frames_dir, layout, lib)
        if not snapshots_exist(con, mid):
            log(f"{mid}: SKIP — every frame quarantined; review "
                f"work/{mid}/quarantine and tune match_threshold.")
            return False

    # ---- map sync ---------------------------------------------------------
    log(f"{mid}: assigning snapshots to maps")
    map_sync.sync_match(con, mid, interval=interval,
                        gap_factor=2.5, report_only=False)
    still = con.execute(
        "SELECT COUNT(*) c FROM comp_snapshots WHERE match_id=? "
        "AND map_result_id IS NULL", (mid,)).fetchone()["c"]
    if still:
        log(f"{mid}: FLAGGED — {still} snapshots unassigned (block/map "
            f"mismatch). Frames kept for inspection; not exported.")
        return False

    # success: clean frames, keep quarantine
    shutil.rmtree(frames_dir, ignore_errors=True)
    log(f"{mid}: done.")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", required=True)
    ap.add_argument("--match", help="process only this match id")
    ap.add_argument("--max", type=int, default=2,
                    help="max matches per run (free-CI budget)")
    args = ap.parse_args()

    con = db.connect()
    layout = capture.load_layout(args.layout)
    interval = layout.get("sample_interval_seconds", 300)

    # Pre-calibration guard: no templates yet → informative no-op.
    try:
        detect.load_templates()
    except FileNotFoundError as e:
        log(f"NO-OP — hero templates not built yet ({e}). "
            f"See pipeline/README.md 'One-time calibration'.")
        return

    todo = pending(con, args.match)[: args.max]
    if not todo:
        log("Nothing to do: no final matches with a vod_url awaiting comps.")
    done = 0
    for m in todo:
        try:
            if process_one(con, m, layout, args.layout, interval):
                done += 1
        except Exception as e:  # one bad match must not kill the run
            log(f"{m['id']}: ERROR — {e!r}. Continuing with next match.")

    # Manual corrections (corrections/corrections.json) override CV data.
    try:
        apply_corrections.main_from(args=None)
    except Exception as e:
        log(f"corrections step failed non-fatally: {e!r}")
    log(f"Processed {done}/{len(todo)} matches. Exporting site data.")
    payload = export_data.build_payload(con)
    export_data_main_write(payload)


def export_data_main_write(payload) -> None:
    import datetime as dt
    import json
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    body = export_data.HEADER.format(ts=ts) + json.dumps(payload, indent=1) + ";\n"
    os.makedirs(os.path.dirname(export_data.OUT_PATH), exist_ok=True)
    with open(export_data.OUT_PATH, "w", encoding="utf-8") as f:
        f.write(body)
    log(f"Wrote {export_data.OUT_PATH}: {len(payload['matches'])} matches.")


if __name__ == "__main__":
    main()
