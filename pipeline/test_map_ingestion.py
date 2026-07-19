#!/usr/bin/env python3
"""Offline tests for the full-map ingestion system:

  - auto-calibration (calibrate_source): finds a synthetic HUD's ten
    portrait boxes, refuses garbage frames with reasons
  - gameplay-state filter: structural probe + reject markers
  - temporal consensus (build_stints): swap confirmation requires
    persistence + displacement; noise/dead variants rejected; setup
    changes classified; run-gap guard
  - round/emblem segmentation + side-swap detection
  - idempotent DB writes (write_db twice -> identical counts)
  - production public export (public.v1 shape + credibility rules,
    never writes the fixture)
  - template/layout packaging (every referenced template dir + marker
    file exists in the repo)

All synthetic/off-line — no network, no real VOD.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import types

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REPO = os.path.dirname(HERE)

os.environ.setdefault("OWCS_DB", os.path.join(
    tempfile.mkdtemp(prefix="owcs_test_ingest_"), "test.sqlite"))

import db  # noqa: E402
import calibrate_source as cs  # noqa: E402
import gameplay_state as gs  # noqa: E402
import ingest_map as im  # noqa: E402
import export_data  # noqa: E402

FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  {status}  {name}" + (f" — {detail}" if detail and not ok
                                   else ""))
    if not ok:
        FAILURES.append(name)


# ---------------------------------------------------------------- helpers
def synth_hud_frame(w=854, h=480, seed=0, chip_y=46, pitch=48,
                    chip=22, a1=18) -> np.ndarray:
    """A frame with two 5-cell chip+portrait rows over a noisy world."""
    rng = np.random.default_rng(seed)
    frame = rng.integers(0, 80, (h, w, 3), dtype=np.uint8)
    rng2 = np.random.default_rng(99)   # portraits identical across frames
    for side, x0, color in (("a", a1, (0, 140, 255)),
                            ("b", w - a1 - 5 * pitch + (pitch - 2 * chip),
                             (255, 120, 0))):
        for i in range(5):
            cx = x0 + i * pitch
            frame[chip_y:chip_y + chip, cx:cx + chip] = color
            art = rng2.integers(0, 255, (chip, chip, 3), dtype=np.uint8)
            frame[chip_y:chip_y + chip,
                  cx + chip + 1:cx + chip + 1 + chip] = art
    return frame


def test_calibration() -> dict | None:
    print("auto-calibration:")
    frames = [synth_hud_frame(seed=s) for s in range(4)]
    tmp = tempfile.mkdtemp(prefix="calib_")
    paths = []
    for i, f in enumerate(frames):
        p = os.path.join(tmp, f"f{i}.png")
        cv2.imwrite(p, f)
        paths.append(p)
    res = cs.calibrate(paths, "test-src")
    check("synthetic HUD calibrates", res["ok"],
          f"conf {res['confidence']}, reasons {res['reasons']}")
    if not res.get("layout"):
        return None
    lay = res["layout"]
    check("ten slots derived", len(lay["slots_a"]) == 5
          and len(lay["slots_b"]) == 5)
    # portraits sit right of chips at chip x + chip w (+1); allow slack
    boxes = res["boxes_a"]
    xs = [b[0] for b in boxes]
    gaps = [xs[i + 1] - xs[i] for i in range(4)]
    check("uniform portrait pitch", max(gaps) - min(gaps) <= 2, str(gaps))
    check("portrait lands on art (a1 near x=41)",
          abs(xs[0] - 41) <= 6, f"a1 x={xs[0]}")
    check("normalized rects recorded",
          len(lay["norm_slots_a"]) == 5
          and all(0 < v < 1 for v in lay["norm_slots_a"][0]))
    check("hud_probe chips recorded",
          len(lay["hud_probe"]["chips_a"]) == 5)
    check("calibration metadata present",
          lay["calibration"]["version"] == cs.CALIB_VERSION
          and lay["calibration"]["confidence"] > 0)

    garbage = [os.path.join(tmp, f"g{i}.png") for i in range(3)]
    for i, p in enumerate(garbage):
        cv2.imwrite(p, np.random.default_rng(i).integers(
            0, 255, (480, 854, 3), dtype=np.uint8))
    res_g = cs.calibrate(garbage, "garbage")
    check("garbage frames refused", not res_g["ok"]
          and len(res_g["reasons"]) > 0,
          f"conf {res_g['confidence']}")
    return lay


def test_gameplay_filter(lay: dict) -> None:
    print("gameplay-state filter:")
    import capture
    lay, _info = capture.scale_layout_to_frame(lay, 854, 480)
    good = synth_hud_frame(seed=1)
    state, reason = gs.classify_frame(good, lay)
    check("live HUD -> gameplay", state == "gameplay", reason)
    blank = np.zeros((480, 854, 3), np.uint8)
    state, reason = gs.classify_frame(blank, lay)
    check("blank frame -> no-hud", state == "no-hud", reason)
    # half-covered HUD (right side blacked out)
    half = synth_hud_frame(seed=2)
    half[:, 427:] = 0
    state, reason = gs.classify_frame(half, lay)
    check("covered side -> not gameplay", state != "gameplay", reason)


def _read(t, hero, score=0.9, margin=0.3, scores=None):
    return {"t": float(t), "hero": hero, "score": score, "margin": margin,
            "crop": f"t{t}_{hero}.png",
            "scores": scores or {hero: score}}


def test_temporal_consensus() -> None:
    print("temporal consensus:")
    # stable hero, one contradictory read -> no swap
    track = [_read(t, "tracer") for t in range(0, 100, 5)]
    track[10] = _read(50, "genji",
                      scores={"genji": 0.5, "tracer": 0.45})
    stints, events = im.build_stints(sorted(track, key=lambda r: r["t"]),
                                     [])
    check("isolated contradiction is not a swap",
          all(e["kind"] != "swap" for e in events)
          and len(stints) == 1 and stints[0]["hero"] == "tracer",
          str([(e['kind'], e.get('candidate')) for e in events]))
    check("contradiction recorded as rejected",
          any(e["kind"] == "rejected-swap" and e["candidate"] == "genji"
              for e in events))

    # genuine swap: persists with displacement
    track = ([_read(t, "tracer") for t in range(0, 50, 5)]
             + [_read(t, "sombra",
                      scores={"sombra": 0.9, "tracer": 0.2})
                for t in range(50, 80, 5)])
    stints, events = im.build_stints(track, [])
    swaps = [e for e in events if e["kind"] == "swap"]
    check("persistent new hero confirms a swap", len(swaps) == 1
          and swaps[0]["from"] == "tracer" and swaps[0]["to"] == "sombra")
    check("swap timestamped at first new-hero obs",
          swaps and swaps[0]["t"] == 50)
    check("swap carries before/after evidence",
          swaps and swaps[0]["evidence_before"]
          and swaps[0]["evidence_after"])

    # dead-variant lookalike: candidate does NOT displace current hero
    track = ([_read(t, "tracer") for t in range(0, 50, 5)]
             + [_read(t, "sombra", score=0.55, margin=0.01,
                      scores={"sombra": 0.55, "tracer": 0.54})
                for t in range(50, 80, 5)]
             + [_read(t, "tracer") for t in range(80, 100, 5)])
    stints, events = im.build_stints(track, [])
    check("non-displacing candidate rejected (state variant)",
          all(e["kind"] != "swap" for e in events),
          str([(e['kind'], e.get('reason', ''))[:60] for e in events]))

    # run-gap guard: stale isolated read cannot pre-date the swap
    track = ([_read(t, "tracer") for t in range(0, 50, 5)]
             + [_read(50, "sombra",
                      scores={"sombra": 0.9, "tracer": 0.2})]
             + [_read(t, "sombra",
                      scores={"sombra": 0.9, "tracer": 0.2})
                for t in range(100, 130, 5)])
    stints, events = im.build_stints(track, [])
    swaps = [e for e in events if e["kind"] == "swap"]
    check("run-gap guard re-dates the swap after the gap",
          swaps and swaps[0]["t"] == 100, str([s['t'] for s in swaps]))

    # setup-phase change is classified as setup-change, not swap
    track = ([_read(t, "tracer") for t in range(0, 50, 5)]
             + [_read(t, "sombra",
                      scores={"sombra": 0.9, "tracer": 0.2})
                for t in range(50, 80, 5)])
    stints, events = im.build_stints(track, [(45, 90)])
    check("change during setup -> setup-change",
          any(e["kind"] == "setup-change" for e in events)
          and all(e["kind"] != "swap" for e in events))


def test_rounds_and_sides() -> None:
    print("rounds + sides:")
    lockv = np.zeros((20, 20), np.float32)
    lockv[5:15, 8:12] = 200.0
    letters = []
    for k in range(3):
        v = np.zeros((20, 20), np.float32)
        v[3 + 4 * k:9 + 4 * k, 3:17] = 150.0 + 30 * k
        letters.append(v)
    obs = []
    t = 0.0

    def add(vec, n, hue_a=20.0, hue_b=110.0):
        nonlocal t
        for _ in range(n):
            obs.append({"t": t, "state": "gameplay", "_emblem": vec,
                        "hue_a": hue_a, "hue_b": hue_b, "slots": {}})
            t += 5.0
    add(lockv, 8)
    add(letters[0], 30)
    add(lockv, 6)
    add(letters[1], 30)
    add(lockv, 6)
    # sides swap colors in round 3
    add(letters[2], 30, hue_a=110.0, hue_b=20.0)
    rounds, setups = im.detect_rounds(obs, 0, t)
    check("three rounds found from emblems", len(rounds) == 3,
          str([(r['start'], r['end']) for r in rounds]))
    check("setup phases found", len(setups) == 3,
          str([(s['start'], s['end']) for s in setups]))
    dec = im.detect_side_swaps(obs, rounds)
    check("hue crossover flags the side swap",
          [d["swapped"] for d in dec] == [False, False, True],
          str(dec))
    side_map = im.build_side_map(obs, rounds, dec, "team1", "team2")
    r3_t = rounds[2]["start"] + 10
    check("side map reattaches teams after the swap",
          side_map[r3_t]["a"] == "team2"
          and side_map[r3_t]["b"] == "team1")


def _fake_args(**over):
    a = types.SimpleNamespace(
        ingest_id="test-ingest", source_id="test-src", vod_url=None,
        match="tm01", map_order=1, start=100, end=400,
        layout="layouts/test.json", map_id="nepal", map_winner="teamB",
        team_a="teamA", team_b="teamB", write=True)
    for k, v in over.items():
        setattr(a, k, v)
    return a


def test_idempotent_writes() -> None:
    print("idempotent DB writes:")
    con = db.connect()
    db.init_schema(con)
    con.execute("INSERT OR IGNORE INTO game_maps VALUES "
                "('nepal','Nepal','Control')")
    for t in ("teamA", "teamB"):
        con.execute("INSERT OR IGNORE INTO teams (id,name,region,code) "
                    "VALUES (?,?,?,?)", (t, t, "EMEA", t[:3].upper()))
    for h in ("tracer", "sombra", "winston", "kiriko", "juno", "genji"):
        con.execute("INSERT OR IGNORE INTO heroes VALUES (?,?,?)",
                    (h, h.title(), "Damage"))
    con.execute("""INSERT OR IGNORE INTO matches
        (id, date, team_a, team_b) VALUES ('tm01','2026-07-01',
        'teamA','teamB')""")
    con.commit()

    args = _fake_args()
    layout = {"calibration": {"version": "calib-test"}}
    obs = [{"t": 100.0 + 5 * i, "state": "gameplay", "reason": "",
            "frame": f"f{i}.jpg",
            "slots": {"a1": {"hero": "tracer", "score": 0.9,
                             "second": "sombra", "second_score": 0.4,
                             "margin": 0.5, "reject": None,
                             "crop": f"c{i}.png", "template": "t.png"}}}
           for i in range(10)]
    heroes5 = ["tracer", "winston", "kiriko", "juno", "genji"]
    per_slot = {}
    for i, h in enumerate(heroes5, 1):
        per_slot[f"a{i}"] = {"stints": [
            {"hero": h, "start": 100, "end": 300, "n_obs": 10,
             "mean_conf": 0.9, "min_conf": 0.85,
             "evidence_start": "c0.png", "evidence_end": "c9.png"}],
            "events": [], "n_reads": 10}
        per_slot[f"b{i}"] = {"stints": [
            {"hero": h, "start": 100, "end": 300, "n_obs": 10,
             "mean_conf": 0.9, "min_conf": 0.85,
             "evidence_start": "c0.png", "evidence_end": "c9.png"}],
            "events": [], "n_reads": 10}
    per_slot["a1"]["events"] = [
        {"kind": "swap", "from": "tracer", "to": "sombra", "t": 140,
         "confidence": 0.9, "margin": 0.5, "n_obs": 3,
         "reason": "test", "evidence_before": "c8.png",
         "evidence_after": "c9.png"}]
    rounds = [{"index": 1, "start": 100, "end": 300, "confidence": 0.8}]
    side_map = {o["t"]: {"a": "teamA", "b": "teamB"} for o in obs}
    side_map[140] = {"a": "teamA", "b": "teamB"}
    side_map[100] = {"a": "teamA", "b": "teamB"}
    stats = {"n": 1}

    w1 = im.write_db(con, args, layout, obs, per_slot, rounds, side_map,
                     stats)
    counts1 = {t: con.execute(
        f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("hero_stints", "hero_swaps", "slot_observations",
                  "map_rounds", "map_results", "ingest_runs")}
    w2 = im.write_db(con, args, layout, obs, per_slot, rounds, side_map,
                     stats)
    counts2 = {t: con.execute(
        f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in counts1}
    check("rerun creates zero duplicates", counts1 == counts2,
          f"{counts1} vs {counts2}")
    check("map winner recorded once", con.execute(
        "SELECT winner_team FROM map_results WHERE match_id='tm01'"
    ).fetchone()[0] == "teamB")
    _ = (w1, w2)

    # manual corrections survive a rerun
    con.execute("""UPDATE hero_stints SET hero_id='sombra',
                   manual_override=1, status='reviewed', source='manual'
                   WHERE slot=1""")
    con.commit()
    im.write_db(con, args, layout, obs, per_slot, rounds, side_map, stats)
    kept = con.execute(
        """SELECT COUNT(*) FROM hero_stints WHERE manual_override=1
           AND hero_id='sombra'""").fetchone()[0]
    check("manual override survives rerun", kept >= 1)


def test_public_export() -> None:
    print("production public export:")
    con = db.connect()
    con.execute("""INSERT OR REPLACE INTO ingest_runs
        (id, source_id, match_id, map_order, start_offset, end_offset,
         detector_version, calibration_version, status, report_path)
        VALUES ('test-ingest','test-src','tm01',1,100,400,'det-test',
                'calib-test','complete','reports/x.html')""")
    con.commit()
    payload = export_data.build_public_payload(con)
    check("meta says production", payload["meta"]["demo"] is False
          and payload["meta"]["schema"] == "public.v1")
    for key in ("regions", "teams", "players", "tournaments",
                "bracketRounds", "bracketMatches", "matches", "heroBans",
                "captureRuns", "compSnapshots", "vodSources", "heroes",
                "mapsCatalog", "patches"):
        check(f"key {key} present", key in payload)
    m = next((x for x in payload["matches"] if x["id"] == "tm01"), None)
    check("ingested match exported", m is not None)
    check("map winner exported", m and m["maps"][0]["winner"] == "teamB")
    snaps = [s for s in payload["compSnapshots"]
             if s["matchId"] == "tm01"]
    check("snapshots derived from stints", len(snaps) >= 1)
    for s in snaps:
        check("snapshot source rule", s["source"] in ("cv", "manual"))
        check("snapshot evidence chain", bool(s["evidenceRunId"]))
        check("snapshot review status valid",
              s["reviewStatus"] in ("auto-high", "needs-review",
                                    "reviewed"))
        break
    check("exporter never writes the fixture path",
          "public_fixture" not in export_data.PUBLIC_OUT_PATH)


def test_packaging() -> None:
    print("template/layout packaging:")
    lay_dir = os.path.join(REPO, "layouts")
    for fn in sorted(os.listdir(lay_dir)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(lay_dir, fn), encoding="utf-8") as f:
            lay = json.load(f)
        tdir = lay.get("templates_dir")
        if tdir and not tdir.startswith("templates/owcs-demo"):
            full = os.path.join(REPO, tdir)
            pngs = ([x for x in os.listdir(full) if x.endswith(".png")]
                    if os.path.isdir(full) else [])
            check(f"{fn}: templates_dir {tdir} exists with templates",
                  len(pngs) > 0, f"{full} missing/empty")
        for marker in (lay.get("reject") or []):
            tpath = marker.get("template")
            if tpath:
                check(f"{fn}: reject marker asset {tpath}",
                      os.path.exists(os.path.join(REPO, tpath)))
    # public pages: production data first, guarded fixture second
    for page in ("match.html", "stats.html", "matches.html",
                 "tournament.html", "tournaments.html"):
        with open(os.path.join(REPO, page), encoding="utf-8") as f:
            s = f.read()
        i_prod = s.find("public_data.v1.js")
        i_fix = s.find("public_fixture.v1.js")
        check(f"{page} loads production data before fixture",
              0 < i_prod < i_fix)
    with open(os.path.join(REPO, "assets", "data",
                           "public_fixture.v1.js"), encoding="utf-8") as f:
        fx = f.read()
    check("fixture never overwrites production data",
          "window.OWCS_PUBLIC = window.OWCS_PUBLIC ||" in fx)


def main() -> int:
    lay = test_calibration()
    if lay:
        test_gameplay_filter(lay)
    test_temporal_consensus()
    test_rounds_and_sides()
    test_idempotent_writes()
    test_public_export()
    test_packaging()
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURES: {FAILURES}")
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
