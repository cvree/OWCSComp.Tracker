#!/usr/bin/env python3
"""Offline tests for ingest_map.py's calibration_health() — a per-run,
runtime measurement of whether the calibration is actually working on
THIS capture, distinct from calibrate_source.py's one-time offline
confidence. See ingest_map.calibration_health's docstring.
"""
from __future__ import annotations
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("OWCS_DB", os.path.join(
    tempfile.mkdtemp(prefix="owcs_test_calibhealth_"), "test.sqlite"))
import db  # noqa: E402
import ingest_map as im  # noqa: E402

FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  {status}  {name}" + (f" — {detail}" if detail and not ok
                                   else ""))
    if not ok:
        FAILURES.append(name)


def _slot(hero="tracer", score=0.9, reject=None):
    return {"hero": hero, "score": score, "reject": reject}


def _obs(t, state, slots=None):
    return {"t": t, "state": state, "slots": slots or {}}


def test_no_gameplay_frames() -> None:
    print("calibration_health: no gameplay frames:")
    h = im.calibration_health([_obs(t, "no-hud") for t in range(5)])
    check("status is suspect with zero gameplay frames",
          h["status"] == "suspect", str(h))
    check("reason names the zero-frame case",
          any("no gameplay frames" in r for r in h["reasons"]), str(h))


def test_healthy_calibration() -> None:
    print("calibration_health: strong, consistent reads:")
    obs = []
    for t in range(20):
        slots = {f"a{i}": _slot(score=0.9) for i in range(1, 6)}
        slots.update({f"b{i}": _slot(score=0.88) for i in range(1, 6)})
        obs.append(_obs(t, "gameplay", slots))
    h = im.calibration_health(obs)
    check("status is ok", h["status"] == "ok", str(h))
    check("full_house_rate is 1.0 (every frame had all 10 slots)",
          h["metrics"]["full_house_rate"] == 1.0, str(h))
    check("median_top_score reflects the strong scores",
          h["metrics"]["median_top_score"] >= 0.85, str(h))
    check("unknown_rate is 0", h["metrics"]["unknown_rate"] == 0.0, str(h))
    check("no reasons when healthy", h["reasons"] == [], str(h))


def test_unhealthy_calibration() -> None:
    print("calibration_health: mostly UNKNOWN/rejected reads:")
    obs = []
    for t in range(20):
        # only 2 of 10 slots ever resolve, and weakly
        slots = {f"a{i}": _slot(hero="UNKNOWN", score=0.2, reject="low-conf")
                 for i in range(1, 6)}
        slots.update({f"b{i}": _slot(hero="UNKNOWN", score=0.2,
                                     reject="low-conf") for i in range(1, 6)})
        slots["a1"] = _slot(score=0.5)
        obs.append(_obs(t, "gameplay", slots))
    h = im.calibration_health(obs)
    check("status is suspect", h["status"] == "suspect", str(h))
    check("low full_house_rate flagged",
          any("all 10" in r for r in h["reasons"]), str(h))
    check("high unknown_rate flagged",
          any("UNKNOWN/rejected" in r for r in h["reasons"]), str(h))
    check("recommends a recalibration action",
          any("recalibrate" in r.lower() or "calibrate_source" in r
              for r in h["reasons"]), str(h))


def test_metrics_math() -> None:
    print("calibration_health: metrics arithmetic:")
    # 3 frames, each with exactly one accepted slot (score .8, .6, .4) and
    # one rejected slot -> full_house never hits (2 slots, 1 accepted each
    # frame), median score .6, unknown_rate = 3/6 = 0.5
    obs = [
        _obs(0, "gameplay", {"a1": _slot(score=0.8),
                             "a2": _slot(hero="UNKNOWN", reject="x")}),
        _obs(1, "gameplay", {"a1": _slot(score=0.6),
                             "a2": _slot(hero="UNKNOWN", reject="x")}),
        _obs(2, "gameplay", {"a1": _slot(score=0.4),
                             "a2": _slot(hero="UNKNOWN", reject="x")}),
    ]
    h = im.calibration_health(obs)
    m = h["metrics"]
    check("gameplay_frames counted", m["gameplay_frames"] == 3, str(m))
    check("full_house_rate is 0 (half the slots always rejected)",
          m["full_house_rate"] == 0.0, str(m))
    check("median_top_score is 0.6", m["median_top_score"] == 0.6, str(m))
    check("unknown_rate is 0.5", m["unknown_rate"] == 0.5, str(m))
    check("total_slot_checks is 6", m["total_slot_checks"] == 6, str(m))


def _fake_args(**over):
    a = types.SimpleNamespace(
        ingest_id="test-calib-ingest", source_id="test-src", vod_url=None,
        match="tm-calib", map_order=1, start=1000, end=1400,
        layout="layouts/test.json", map_id="nepal", map_winner="teamB",
        team_a="teamA", team_b="teamB", write=True)
    for k, v in over.items():
        setattr(a, k, v)
    return a


def test_write_db_suspect_calibration_caps_all_stints() -> None:
    print("write_db: suspect calibration caps every stint, not just "
          "grace ones:")
    con = db.connect()
    db.init_schema(con)
    con.execute("INSERT OR IGNORE INTO game_maps VALUES "
                "('nepal','Nepal','Control')")
    for t in ("teamA", "teamB"):
        con.execute("INSERT OR IGNORE INTO teams (id,name,region,code) "
                    "VALUES (?,?,?,?)", (t, t, "EMEA", t[:3].upper()))
    con.execute("INSERT OR IGNORE INTO heroes VALUES ('tracer','Tracer',"
                "'Damage')")
    con.execute("""INSERT OR IGNORE INTO matches
        (id, date, team_a, team_b) VALUES ('tm-calib','2026-07-01',
        'teamA','teamB')""")
    con.commit()

    args = _fake_args()
    layout = {"calibration": {"version": "calib-test"}}
    obs = [{"t": 1000.0 + i, "state": "gameplay", "reason": "",
            "frame": f"f{i}.jpg", "slots": {}} for i in range(10)]
    rounds = [{"index": 1, "start": 1000, "end": 1400, "confidence": 0.85}]
    # an otherwise-excellent stint, deep into the round (no grace issue)
    per_slot = {"a1": {"stints": [
        {"hero": "tracer", "start": 1100, "end": 1350, "n_obs": 25,
         "mean_conf": 0.95, "min_conf": 0.9, "evidence_start": "c0.png",
         "evidence_end": "c9.png", "early_grace": False,
         "round_start": None, "round_end": None}],
        "events": [], "n_reads": 25}}
    side_map = {o["t"]: {"a": "teamA", "b": "teamB"} for o in obs}
    stats = {"n": 1}
    suspect_health = {"status": "suspect",
                      "reasons": ["median accepted match score 0.31 is low"],
                      "metrics": {}}

    im.write_db(con, args, layout, obs, per_slot, rounds, side_map, stats,
               calib_health=suspect_health)
    row = con.execute(
        "SELECT status, notes FROM hero_stints WHERE slot=1"
    ).fetchone()
    check("an otherwise-perfect stint is STILL capped when calibration "
          "is suspect", row["status"] == "needs-review", str(dict(row)))
    check("notes explain the calibration-health reason",
          "calibration health suspect" in row["notes"].lower(),
          str(dict(row)))

    run_row = con.execute(
        "SELECT calibration_status, calibration_health FROM ingest_runs "
        "WHERE id=?", (args.ingest_id,)).fetchone()
    check("ingest_runs.calibration_status recorded as suspect",
          run_row["calibration_status"] == "suspect", str(dict(run_row)))
    check("ingest_runs.calibration_health JSON stored",
          run_row["calibration_health"] and "suspect" in
          run_row["calibration_health"], str(dict(run_row)))


def main() -> int:
    test_no_gameplay_frames()
    test_healthy_calibration()
    test_unhealthy_calibration()
    test_metrics_math()
    test_write_db_suspect_calibration_caps_all_stints()
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURES: {FAILURES}")
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
