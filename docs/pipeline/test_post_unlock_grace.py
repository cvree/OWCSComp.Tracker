#!/usr/bin/env python3
"""Offline tests for ingest_map.py's post-unlock grace window: a hero
established in the first POST_UNLOCK_GRACE seconds of a round is capped
at needs-review unless a later observation (by the POST_UNLOCK_RECHECK
deadline) corroborates it — implementing "wait ~30s after a control point
unlocks, or recheck ~30s after that, because players speed-boosting or
teleporting out of spawn can still swap heroes."
"""
from __future__ import annotations
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("OWCS_DB", os.path.join(
    tempfile.mkdtemp(prefix="owcs_test_grace_"), "test.sqlite"))
import db  # noqa: E402
import ingest_map as im  # noqa: E402

FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  {status}  {name}" + (f" — {detail}" if detail and not ok
                                   else ""))
    if not ok:
        FAILURES.append(name)


def _read(t, hero, score=0.9, margin=0.3, scores=None):
    return {"t": float(t), "hero": hero, "score": score, "margin": margin,
            "crop": f"t{t}_{hero}.png", "scores": scores or {hero: score}}


ROUND = [{"index": 1, "start": 1000.0, "end": 1400.0, "confidence": 0.85}]


def test_post_unlock_windows() -> None:
    print("post_unlock_windows:")
    w = im.post_unlock_windows(ROUND, 900, 1500)
    check("two windows per round (grace + recheck)", len(w) == 2, str(w))
    grace_win = min(w, key=lambda p: p[0])
    recheck_win = max(w, key=lambda p: p[0])
    check("grace window starts at round start",
          abs(grace_win[0] - 1000.0) < 0.01, str(grace_win))
    check("grace window covers the ~30s grace period",
          grace_win[1] >= 1000.0 + im.POST_UNLOCK_GRACE, str(grace_win))
    check("recheck window brackets the ~60s recheck deadline",
          recheck_win[0] <= 1000.0 + im.POST_UNLOCK_GRACE
          <= recheck_win[1] or recheck_win[0] <=
          1000.0 + im.POST_UNLOCK_GRACE + im.POST_UNLOCK_RECHECK,
          str(recheck_win))

    # windows clip to the [start, end] ingestion bounds
    w2 = im.post_unlock_windows(ROUND, 1000, 1010)
    for (a, b) in w2:
        check(f"window ({a},{b}) clipped inside [1000,1010]",
              1000 <= a and b <= 1010, str((a, b)))


def test_grace_ok_for_stint() -> None:
    print("grace_ok_for_stint:")
    ordinary = {"early_grace": False}
    check("ordinary stint (no early_grace) is always ok",
          im.grace_ok_for_stint(ordinary) is True)

    uncorroborated = {"early_grace": True, "round_start": 1000.0,
                      "round_end": 1400.0, "end": 1010.0}
    check("early stint that never persists to the recheck deadline "
          "is NOT ok", im.grace_ok_for_stint(uncorroborated) is False)

    corroborated = {"early_grace": True, "round_start": 1000.0,
                    "round_end": 1400.0,
                    "end": 1000.0 + im.POST_UNLOCK_GRACE
                    + im.POST_UNLOCK_RECHECK + 1}
    check("early stint that persists past the recheck deadline IS ok",
          im.grace_ok_for_stint(corroborated) is True)

    short_round = {"early_grace": True, "round_start": 1000.0,
                   "round_end": 1000.0 + im.POST_UNLOCK_GRACE
                   + im.POST_UNLOCK_RECHECK - 5,  # round too short to recheck
                   "end": 1010.0}
    check("round too short for a recheck to be possible -> not penalized",
          im.grace_ok_for_stint(short_round) is True)

    no_round_context = {"early_grace": True, "round_start": None,
                        "end": 1010.0}
    check("no round context -> not penalized (can't evaluate)",
          im.grace_ok_for_stint(no_round_context) is True)


def test_build_stints_tags_early_grace() -> None:
    print("build_stints: early_grace tagging end-to-end:")
    # hero swaps to 'sombra' at t=1010, ten seconds after the round starts
    # at t=1000 (inside the 30s grace window), and is NEVER seen again
    # after 1030 -> should stay tagged early_grace and fail corroboration.
    track = ([_read(t, "tracer") for t in range(970, 1010, 5)]
             + [_read(t, "sombra", scores={"sombra": 0.9, "tracer": 0.2})
                for t in range(1010, 1030, 5)])
    stints, events = im.build_stints(track, [], ROUND)
    sombra_stints = [s for s in stints if s["hero"] == "sombra"]
    check("sombra stint exists", len(sombra_stints) == 1, str(stints))
    st = sombra_stints[0]
    check("sombra stint is tagged early_grace",
          st.get("early_grace") is True, str(st))
    check("round_start/round_end attached to the stint",
          st.get("round_start") == 1000.0 and st.get("round_end") == 1400.0,
          str(st))
    check("uncorroborated -> grace_ok_for_stint is False",
          im.grace_ok_for_stint(st) is False, str(st))

    # same scenario, but sombra PERSISTS all the way past the recheck
    # deadline (t=1000+30+30=1060) -> corroborated, should read as ok
    track2 = ([_read(t, "tracer") for t in range(970, 1010, 5)]
              + [_read(t, "sombra", scores={"sombra": 0.9, "tracer": 0.2})
                 for t in range(1010, 1080, 5)])
    stints2, _ = im.build_stints(track2, [], ROUND)
    sombra2 = [s for s in stints2 if s["hero"] == "sombra"][0]
    check("corroborated stint (persisted past recheck) reads ok",
          im.grace_ok_for_stint(sombra2) is True, str(sombra2))

    # a swap happening WELL AFTER the grace window (e.g. mid-round, t=1200)
    # must never be tagged early_grace at all
    track3 = ([_read(t, "tracer") for t in range(970, 1200, 5)]
              + [_read(t, "sombra", scores={"sombra": 0.9, "tracer": 0.2})
                 for t in range(1200, 1220, 5)])
    stints3, _ = im.build_stints(track3, [], ROUND)
    sombra3 = [s for s in stints3 if s["hero"] == "sombra"][0]
    check("mid-round swap (outside grace window) is never tagged",
          sombra3.get("early_grace") is False, str(sombra3))

    # no rounds passed at all (back-compat / no round_emblem layout) ->
    # nothing is ever tagged early_grace
    stints4, _ = im.build_stints(track, [])
    sombra4 = [s for s in stints4 if s["hero"] == "sombra"][0]
    check("build_stints with no rounds arg never tags early_grace "
          "(backward compatible)", sombra4.get("early_grace") is False,
          str(sombra4))


def _fake_args(**over):
    a = types.SimpleNamespace(
        ingest_id="test-grace-ingest", source_id="test-src", vod_url=None,
        match="tm-grace", map_order=1, start=1000, end=1400,
        layout="layouts/test.json", map_id="nepal", map_winner="teamB",
        team_a="teamA", team_b="teamB", write=True)
    for k, v in over.items():
        setattr(a, k, v)
    return a


def test_write_db_caps_grace_status() -> None:
    print("write_db: grace capping reaches the database:")
    con = db.connect()
    db.init_schema(con)
    con.execute("INSERT OR IGNORE INTO game_maps VALUES "
                "('nepal','Nepal','Control')")
    for t in ("teamA", "teamB"):
        con.execute("INSERT OR IGNORE INTO teams (id,name,region,code) "
                    "VALUES (?,?,?,?)", (t, t, "EMEA", t[:3].upper()))
    for h in ("tracer", "sombra"):
        con.execute("INSERT OR IGNORE INTO heroes VALUES (?,?,?)",
                    (h, h.title(), "Damage"))
    con.execute("""INSERT OR IGNORE INTO matches
        (id, date, team_a, team_b) VALUES ('tm-grace','2026-07-01',
        'teamA','teamB')""")
    con.commit()

    args = _fake_args()
    layout = {"calibration": {"version": "calib-test"}}
    obs = [{"t": 1000.0 + i, "state": "gameplay", "reason": "",
            "frame": f"f{i}.jpg", "slots": {}} for i in range(30)]
    rounds = [{"index": 1, "start": 1000, "end": 1400, "confidence": 0.85}]
    # a1: an ordinary, well-corroborated stint -> should stay auto-high
    # a2: a stint that began 10s after unlock and never persisted to the
    #     recheck deadline -> should be capped needs-review, with a swap
    #     event (from the paired 'events' entry) downgraded to uncertain
    per_slot = {
        "a1": {"stints": [
            {"hero": "tracer", "start": 1000, "end": 1350, "n_obs": 25,
             "mean_conf": 0.9, "min_conf": 0.85,
             "evidence_start": "c0.png", "evidence_end": "c9.png",
             "early_grace": False, "round_start": None, "round_end": None}],
            "events": [], "n_reads": 25},
        "a2": {"stints": [
            {"hero": "sombra", "start": 1010, "end": 1025, "n_obs": 4,
             "mean_conf": 0.9, "min_conf": 0.85,
             "evidence_start": "c1.png", "evidence_end": "c2.png",
             "early_grace": True, "round_start": 1000, "round_end": 1400}],
            "events": [
                {"kind": "swap", "from": "tracer", "to": "sombra",
                 "t": 1010, "confidence": 0.9, "margin": 0.5, "n_obs": 4,
                 "reason": "test swap", "evidence_before": "c0.png",
                 "evidence_after": "c1.png"}],
            "n_reads": 4},
    }
    side_map = {o["t"]: {"a": "teamA", "b": "teamB"} for o in obs}
    stats = {"n": 1}

    im.write_db(con, args, layout, obs, per_slot, rounds, side_map, stats)

    a1_status = con.execute(
        "SELECT status, notes FROM hero_stints WHERE slot=1 AND "
        "hero_id='tracer'").fetchone()
    check("ordinary stint stays auto-high", a1_status["status"] == "auto-high",
          str(dict(a1_status)))

    a2_status = con.execute(
        "SELECT status, notes FROM hero_stints WHERE slot=2 AND "
        "hero_id='sombra'").fetchone()
    check("uncorroborated grace stint capped at needs-review",
          a2_status["status"] == "needs-review", str(dict(a2_status)))
    check("grace stint carries an explanatory note",
          a2_status["notes"] is not None
          and "post-unlock grace" in a2_status["notes"].lower(),
          str(dict(a2_status)))

    swap_row = con.execute(
        "SELECT status, reason FROM hero_swaps WHERE slot=2 AND "
        "to_hero='sombra'").fetchone()
    check("paired swap event downgraded to uncertain",
          swap_row["status"] == "uncertain", str(dict(swap_row)))
    check("swap reason explains the grace downgrade",
          "post-unlock grace" in swap_row["reason"].lower(),
          str(dict(swap_row)))


def main() -> int:
    test_post_unlock_windows()
    test_grace_ok_for_stint()
    test_build_stints_tags_early_grace()
    test_write_db_caps_grace_status()
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURES: {FAILURES}")
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
