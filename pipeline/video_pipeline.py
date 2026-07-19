#!/usr/bin/env python3
"""
video_pipeline.py — orchestrate the video CV layer from video_sources.json.

Chains the four stages for each configured source:
  video_ingest      source        -> work/{match}/frames_raw
  frame_filter      frames_raw     -> work/{match}/frames   (live gameplay)
  hero_overlay_detect + video_to_snapshots
                    frames         -> comp_snapshots (source='cv')
  map_sync          snapshots      -> assigned to map_results

Then applies manual corrections (which override cv) and exports data.js —
mirroring how run_cv_batch.py finishes, so the site reflects the new data.

This is the video_sources-driven entry point. run_cv_batch.py remains the
match-driven CI entry (it scans matches with a vod_url). Both write cv
snapshots the same way; use whichever fits how you list work to do.

Two modes:
  (default)  run against the real DB; only sources whose match already
             exists are processed; skips sources needing uncalibrated
             templates instead of failing.
  --demo     fully offline, isolated DB under work/video_demo/. Seeds a
             demo match, runs ONLY the committed fixture source, and writes
             a throwaway data.js — proves the whole chain with no network,
             no yt-dlp/ffmpeg, and no touch to real data.

Usage:
  python3 pipeline/video_pipeline.py --demo
  python3 pipeline/video_pipeline.py                     # real DB, all sources
  python3 pipeline/video_pipeline.py --match m01
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import capture  # noqa: E402
import video_ingest  # noqa: E402
import frame_filter  # noqa: E402
import hero_overlay_detect as hod  # noqa: E402
import video_to_snapshots as v2s  # noqa: E402
import map_sync  # noqa: E402
import export_data  # noqa: E402

WORK_DIR = capture.WORK_DIR
DEMO_DB = os.path.join(WORK_DIR, "video_demo", "demo.sqlite")
DEMO_DATA_JS = os.path.join(WORK_DIR, "video_demo", "data.demo.js")


def log(msg: str) -> None:
    print(f"[video_pipeline] {msg}", flush=True)


def run_source(con, src: dict) -> dict | None:
    """Ingest -> filter -> detect+persist -> map_sync for one source."""
    mid = video_ingest.source_match_id(src)
    layout_path = video_ingest.abspath(src["layout"])
    layout = capture.load_layout(layout_path)
    interval = int(src.get("sampleIntervalSeconds")
                   or layout.get("sample_interval_seconds", 300))

    try:
        lib = hod.load_lib(layout)
    except FileNotFoundError as e:
        log(f"{mid}: SKIP — hero templates not built yet ({e}).")
        return None

    ing = video_ingest.ingest_source(src, interval_default=interval)
    base = os.path.join(WORK_DIR, mid)
    frames_dir = os.path.join(base, "frames")
    filt = frame_filter.filter_frames(ing["raw_dir"], frames_dir, layout)
    log(f"{mid}: {ing['frames']} raw → kept {len(filt['kept'])} gameplay, "
        f"rejected {len(filt['rejected'])}")
    if not filt["kept"]:
        log(f"{mid}: SKIP — no gameplay frames kept; check layout anchor.")
        return None

    qdir = os.path.join(base, "quarantine")
    rep = v2s.snapshots_for_match(con, mid, frames_dir, layout, lib,
                                  quarantine_dir=qdir)
    log(f"{mid}: wrote {rep['snapshots_written']} cv snapshots, "
        f"quarantined {rep['quarantined_frames']} frames.")

    map_sync.sync_match(con, mid, interval=interval, gap_factor=2.5,
                        report_only=False)
    return rep


def write_data_js(con, out_path: str) -> int:
    payload = export_data.build_payload(con)
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    body = export_data.HEADER.format(ts=ts) + json.dumps(payload, indent=1) + ";\n"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(body)
    return len(payload["matches"])


# ------------------------------------------------------------------- demo
def seed_demo_match(con) -> None:
    import init_db  # noqa: E402
    db.init_schema(con)
    init_db.seed_reference(con, init_db.load_sample())
    con.execute("""INSERT OR REPLACE INTO matches
        (id, source_ref, stage, region, date, status, team_a, team_b,
         score_a, score_b, winner_team, vod_url)
        VALUES ('vdemo01','video:vdemo01','Demo','Asia','2026-06-01','final',
                'falcons','cr',2,0,'falcons','fixture://demo')""")
    for order, mp in ((1, "busan"), (2, "kingsrow")):
        con.execute("""INSERT OR IGNORE INTO map_results
            (match_id, map_order, map_id, winner_team)
            VALUES ('vdemo01', ?, ?, 'falcons')""", (order, mp))
    con.commit()


def run_demo(sources_path: str) -> int:
    import shutil
    shutil.rmtree(os.path.dirname(DEMO_DB), ignore_errors=True)
    con = db.connect(DEMO_DB)
    seed_demo_match(con)

    srcs = [s for s in video_ingest.load_sources(sources_path)
            if s.get("mode") == "demo"]
    if not srcs:
        log("no demo source in video_sources.json.")
        return 1
    rep = run_source(con, srcs[0])
    if not rep:
        log("demo source produced no snapshots.")
        return 1

    n_cv = con.execute("SELECT COUNT(*) c FROM comp_snapshots "
                       "WHERE source='cv'").fetchone()["c"]
    unassigned = con.execute("SELECT COUNT(*) c FROM comp_snapshots "
                             "WHERE map_result_id IS NULL").fetchone()["c"]
    n_matches = write_data_js(con, DEMO_DATA_JS)
    log(f"DEMO OK — {n_cv} cv snapshots, {unassigned} unassigned, "
        f"{n_matches} match(es) exported → {DEMO_DATA_JS}")
    return 0 if unassigned == 0 else 1


# ------------------------------------------------------------------- real
def run_real(sources_path: str, only: str | None, max_sources: int) -> int:
    con = db.connect()
    srcs = [s for s in video_ingest.load_sources(sources_path)
            if (only is None or video_ingest.source_match_id(s) == only)
            and s.get("mode") != "demo"]
    done = 0
    for src in srcs[:max_sources]:
        mid = video_ingest.source_match_id(src)
        if not mid or not video_ingest.match_exists(con, mid):
            log(f"{mid}: SKIP — match not in DB (ingest FACEIT facts first).")
            continue
        try:
            if run_source(con, src):
                done += 1
        except Exception as e:  # one bad source must not kill the batch
            log(f"{mid}: ERROR — {e!r}. Continuing.")

    # manual corrections override cv; then export the real site data
    try:
        import apply_corrections  # noqa: E402
        apply_corrections.main_from(args=None)
    except Exception as e:
        log(f"corrections step failed non-fatally: {e!r}")
    n = write_data_js(con, export_data.OUT_PATH)
    log(f"Processed {done} source(s). Exported {n} matches → {export_data.OUT_PATH}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default=video_ingest.DEFAULT_SOURCES)
    ap.add_argument("--match", help="only this source's match id")
    ap.add_argument("--max", type=int, default=4)
    ap.add_argument("--demo", action="store_true",
                    help="offline isolated-DB demo using committed fixtures")
    args = ap.parse_args()
    rc = (run_demo(args.sources) if args.demo
          else run_real(args.sources, args.match, args.max))
    sys.exit(rc)


if __name__ == "__main__":
    main()
