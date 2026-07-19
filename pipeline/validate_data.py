#!/usr/bin/env python3
"""
validate_data.py — data-quality checks over the SQLite DB.

Warns, does not crash. Distinguishes WARNINGS (data is imperfect but usable)
from ERRORS (referential integrity broken). Exit code: 0 for warnings-only,
1 if any hard error is found. Run it before export to catch problems.

  python3 pipeline/validate_data.py
  python3 pipeline/validate_data.py --strict   # treat warnings as errors too
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

WARN, ERROR = "WARN", "ERROR"


class Report:
    def __init__(self):
        self.items = []  # (level, check, message)
        self.ok = []

    def warn(self, check, msg):
        self.items.append((WARN, check, msg))

    def error(self, check, msg):
        self.items.append((ERROR, check, msg))

    def passed(self, check):
        self.ok.append(check)

    def render(self, strict=False) -> int:
        for c in self.ok:
            print(f"  OK    {c}")
        for level, check, msg in self.items:
            print(f"  {level}  {check}: {msg}")
        n_warn = sum(1 for i in self.items if i[0] == WARN)
        n_err = sum(1 for i in self.items if i[0] == ERROR)
        print(f"\nSummary: {len(self.ok)} checks OK, {n_warn} warnings, "
              f"{n_err} errors.")
        if n_err or (strict and n_warn):
            print("RESULT: FAIL")
            return 1
        print("RESULT: PASS" + (" (warnings present)" if n_warn else ""))
        return 0


def q(con, sql, *params):
    return con.execute(sql, params).fetchall()


def run_checks(con) -> Report:
    r = Report()
    heroes = {x["id"] for x in q(con, "SELECT id FROM heroes")}
    maps = {x["id"] for x in q(con, "SELECT id FROM game_maps")}
    teams = {x["id"] for x in q(con, "SELECT id FROM teams")}
    players = {x["id"] for x in q(con, "SELECT id FROM players")}

    # 1. FACEIT match with no maps
    rows = q(con, """SELECT m.id FROM matches m
                     WHERE NOT EXISTS (SELECT 1 FROM map_results mr
                                       WHERE mr.match_id=m.id)""")
    (r.warn("faceit_match_no_maps",
            f"{len(rows)} match(es) have no maps: {[x['id'] for x in rows][:5]}")
     if rows else r.passed("faceit_match_no_maps"))

    # 2. map with replay code but no score
    rows = q(con, """SELECT match_id, map_order FROM map_results
                     WHERE replay_code IS NOT NULL
                       AND score_a IS NULL AND score_b IS NULL""")
    (r.warn("replay_code_no_score",
            f"{len(rows)} map(s) have a replay code but no score")
     if rows else r.passed("replay_code_no_score"))

    # 3. duplicate replay codes
    rows = q(con, """SELECT replay_code, COUNT(*) c FROM map_results
                     WHERE replay_code IS NOT NULL
                     GROUP BY replay_code HAVING c > 1""")
    (r.warn("duplicate_replay_codes",
            f"{len(rows)} replay code(s) used on multiple maps: "
            f"{[x['replay_code'] for x in rows][:5]}")
     if rows else r.passed("duplicate_replay_codes"))

    # 4. FACEIT-only map with replay code but no tracker comp
    rows = q(con, """SELECT mr.match_id, mr.map_order FROM map_results mr
                     WHERE mr.replay_code IS NOT NULL
                       AND NOT EXISTS (SELECT 1 FROM comp_snapshots cs
                                       WHERE cs.map_result_id=mr.id)""")
    (r.warn("replay_available_no_comp",
            f"{len(rows)} map(s) have replay codes but no tracker comp yet "
            f"(these are the manual-correction queue)")
     if rows else r.passed("replay_available_no_comp"))

    # 5. manual correction with invalid hero
    rows = q(con, """SELECT DISTINCT sh.hero_id FROM snapshot_heroes sh
                     JOIN comp_snapshots cs ON cs.id=sh.snapshot_id
                     WHERE cs.source='manual' AND sh.hero_id NOT IN
                       (SELECT id FROM heroes)""")
    (r.error("manual_invalid_hero",
             f"manual comps reference unknown heroes: {[x['hero_id'] for x in rows]}")
     if rows else r.passed("manual_invalid_hero"))

    # 6. tracker snapshot referencing unknown match/map/team
    rows = q(con, """SELECT id FROM comp_snapshots
                     WHERE match_id NOT IN (SELECT id FROM matches)
                        OR team_id NOT IN (SELECT id FROM teams)
                        OR (map_result_id IS NOT NULL AND map_result_id NOT IN
                            (SELECT id FROM map_results))""")
    (r.error("snapshot_bad_ref",
             f"{len(rows)} comp snapshot(s) reference unknown match/map/team")
     if rows else r.passed("snapshot_bad_ref"))

    # 7. roster row with unknown team/player
    rows = q(con, """SELECT match_id, team_id, player_id FROM match_rosters
                     WHERE team_id NOT IN (SELECT id FROM teams)
                        OR player_id NOT IN (SELECT id FROM players)""")
    (r.error("roster_bad_ref",
             f"{len(rows)} roster row(s) reference unknown team/player")
     if rows else r.passed("roster_bad_ref"))

    # 8. match score not matching map winner counts
    bad = []
    for m in q(con, """SELECT id, team_a, team_b, score_a, score_b
                       FROM matches WHERE score_a IS NOT NULL"""):
        wins_a = q(con, """SELECT COUNT(*) c FROM map_results
                           WHERE match_id=? AND winner_team=?""",
                   m["id"], m["team_a"])[0]["c"]
        wins_b = q(con, """SELECT COUNT(*) c FROM map_results
                           WHERE match_id=? AND winner_team=?""",
                   m["id"], m["team_b"])[0]["c"]
        # only compare when we actually have per-map winners
        if (wins_a + wins_b) > 0 and (wins_a != m["score_a"] or wins_b != m["score_b"]):
            bad.append(f"{m['id']} (score {m['score_a']}-{m['score_b']} vs "
                       f"map wins {wins_a}-{wins_b})")
    (r.warn("score_vs_map_winners",
            f"{len(bad)} match(es) where final score != map winners: {bad[:3]}")
     if bad else r.passed("score_vs_map_winners"))

    # 9. map_result with unknown map id
    rows = q(con, "SELECT id, map_id FROM map_results WHERE map_id NOT IN "
                  "(SELECT id FROM game_maps)")
    (r.error("map_result_unknown_map",
             f"{len(rows)} map_result(s) use unknown map ids: "
             f"{sorted({x['map_id'] for x in rows})[:5]}")
     if rows else r.passed("map_result_unknown_map"))

    # 10. hero_ban with unknown hero id
    rows = q(con, "SELECT id, hero_id FROM hero_bans WHERE hero_id NOT IN "
                  "(SELECT id FROM heroes)")
    (r.warn("hero_ban_unknown_hero",
            f"{len(rows)} hero ban(s) reference unknown heroes: "
            f"{sorted({x['hero_id'] for x in rows})[:5]}")
     if rows else r.passed("hero_ban_unknown_hero"))

    # 11. map_veto_event with unknown map/team
    rows = q(con, """SELECT id FROM map_veto_events
                     WHERE (map_id IS NOT NULL AND map_id NOT IN (SELECT id FROM game_maps))
                        OR (team_id IS NOT NULL AND team_id NOT IN (SELECT id FROM teams))""")
    (r.warn("veto_bad_ref", f"{len(rows)} veto event(s) reference unknown map/team")
     if rows else r.passed("veto_bad_ref"))

    # 12. tracker opener with != 5 heroes (first snapshot per map+team)
    bad = []
    firsts = q(con, """SELECT cs.id, cs.match_id, cs.team_id,
                              MIN(cs.stream_offset_seconds) moff
                       FROM comp_snapshots cs
                       WHERE cs.map_result_id IS NOT NULL
                       GROUP BY cs.map_result_id, cs.team_id""")
    for f in firsts:
        n = q(con, "SELECT COUNT(*) c FROM snapshot_heroes WHERE snapshot_id=?",
              f["id"])[0]["c"]
        if n != 5:
            bad.append(f"{f['match_id']}/{f['team_id']} opener has {n} heroes")
    (r.warn("opener_not_five", f"{len(bad)} opener comp(s) not exactly 5: {bad[:3]}")
     if bad else r.passed("opener_not_five"))

    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="treat warnings as failures too")
    args = ap.parse_args()
    con = db.connect()
    if not con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                       "AND name='matches'").fetchone():
        print("DB not initialized — run init_db.py first.")
        sys.exit(1)
    print("Validating data quality...\n")
    report = run_checks(con)
    sys.exit(report.render(strict=args.strict))


if __name__ == "__main__":
    main()
