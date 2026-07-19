#!/usr/bin/env python3
"""
init_db.py — create the SQLite schema and seed reference/sample data.

Usage:
  python3 pipeline/init_db.py                 # schema + heroes/maps/teams
  python3 pipeline/init_db.py --with-sample   # also load demo matches,
                                              # FACEIT-style metadata, bans,
                                              # replay codes, and comp snapshots
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "sample_data.json")


def load_sample() -> dict:
    with open(SAMPLE, "r", encoding="utf-8") as f:
        return json.load(f)


def seed_reference(con, data) -> None:
    con.executemany(
        """INSERT OR REPLACE INTO heroes (id, name, role) VALUES (?,?,?)""",
        [(h["id"], h["name"], h["role"]) for h in data["heroes"]],
    )
    con.executemany(
        """INSERT OR REPLACE INTO game_maps (id, name, mode) VALUES (?,?,?)""",
        [(m["id"], m["name"], m["mode"]) for m in data["maps"]],
    )
    con.executemany(
        """INSERT OR REPLACE INTO teams
           (id, name, region, code, faceit_team_id, logo_url, prep_notes)
           VALUES (?,?,?,?,?,?,?)""",
        [(
            t["id"], t["name"], t.get("region", "Unknown"), t["code"],
            t.get("faceitTeamId"), t.get("logoUrl"), t.get("prepNotes"),
        ) for t in data["teams"]],
    )
    con.commit()
    print(f"Seeded {len(data['heroes'])} heroes, {len(data['maps'])} maps, "
          f"{len(data['teams'])} teams.")


def migrate(con) -> None:
    """Idempotent column adds for DBs created before this version."""
    try:
        con.execute("ALTER TABLE comp_snapshots ADD COLUMN source TEXT DEFAULT 'cv'")
        print("migrate: added comp_snapshots.source")
    except Exception:
        pass
    con.commit()


def seed_sample_rosters(con, data) -> None:
    """Placeholder FACEIT-style rosters: five clearly-sample players per team
    (never real player names). Real rosters come from the FACEIT ingest."""
    roles = ["Tank", "Damage", "Damage", "Support", "Support"]
    n = 0
    for t in data["teams"]:
        for i, role in enumerate(roles, start=1):
            pid = f"{t['id']}_p{i}"
            con.execute(
                """INSERT OR REPLACE INTO players
                   (id, nickname, faceit_player_id, team_id, role, source)
                   VALUES (?,?,?,?,?, 'sample')""",
                (pid, f"{t['code']}-{role[:3].upper()}{i}", f"sample-{pid}",
                 t["id"], role),
            )
            n += 1
    for m in data["matches"]:
        for team_key in ("teamA", "teamB"):
            tid = m[team_key]
            for i in range(1, 6):
                con.execute(
                    """INSERT OR REPLACE INTO match_rosters
                       (match_id, team_id, player_id, source)
                       VALUES (?,?,?, 'sample')""",
                    (m["id"], tid, f"{tid}_p{i}"),
                )
    con.commit()
    print(f"Seeded {n} sample players + per-match rosters.")


def seed_sample_matches(con, data) -> None:
    """Load demo matches as if FACEIT ingest + vision had produced them:
    matchroom metadata, map results, bans, replay codes, map veto notes, and
    one comp snapshot per team per map."""
    n_snap = n_bans = n_veto = 0
    # The two most recent matches stay comp-less on purpose: they demo the
    # "FACEIT metadata only — hero picks not detected yet" product state.
    newest = {m["id"] for m in sorted(data["matches"],
                                      key=lambda x: x["date"])[-2:]}
    for m in data["matches"]:
        winner = m["teamA"] if m.get("scoreA", 0) > m.get("scoreB", 0) else m["teamB"]
        con.execute(
            """INSERT OR REPLACE INTO matches
               (id, source_ref, faceit_match_id, faceit_room_url, event_name,
                season, stage, division, round, group_name, region, date, status,
                team_a, team_b, score_a, score_b, winner_team, source_url, vod_url,
                raw_source, prep_notes, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (
                m["id"], f"sample:{m['id']}", m.get("faceitMatchId"),
                m.get("faceitRoomUrl"), m.get("eventName", "OWCS 2026"),
                m.get("season"), m.get("stage"), m.get("division"),
                m.get("round"), m.get("groupName"), m.get("region", "Unknown"),
                m["date"], m.get("status", "final"), m["teamA"], m["teamB"],
                m.get("scoreA", 0), m.get("scoreB", 0), winner,
                m.get("faceitRoomUrl"), m.get("vodUrl"), "sample", m.get("prepNotes"),
            ),
        )
        # replace prior sample rows for idempotency
        con.execute("DELETE FROM hero_bans WHERE match_id=?", (m["id"],))
        con.execute("DELETE FROM map_veto_events WHERE match_id=?", (m["id"],))
        con.execute("DELETE FROM map_results WHERE match_id=?", (m["id"],))
        con.execute("DELETE FROM comp_snapshots WHERE match_id=?", (m["id"],))

        for order, g in enumerate(m["maps"], start=1):
            win_team = m["teamA"] if g.get("winner") == "a" else m["teamB"] if g.get("winner") == "b" else None
            cur = con.execute(
                """INSERT INTO map_results
                   (match_id, map_order, map_id, score_a, score_b, winner_team,
                    picked_by_team, veto_action, pick_veto, replay_code,
                    replay_expires_note, vod_url, vod_start_seconds, source,
                    confidence, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    m["id"], g.get("mapOrder", order), g["map"], g.get("scoreA"),
                    g.get("scoreB"), win_team, g.get("pickedByTeam"),
                    g.get("vetoAction"), g.get("pickVeto"), g.get("replayCode"),
                    g.get("replayExpiresNote"), g.get("vodUrl"),
                    g.get("vodStartSeconds"), "sample", 0.99, g.get("notes"),
                ),
            )
            mr_id = cur.lastrowid
            if g.get("pickedByTeam") or g.get("vetoAction"):
                con.execute(
                    """INSERT OR REPLACE INTO map_veto_events
                       (match_id, order_index, team_id, map_id, action, source, notes)
                       VALUES (?,?,?,?,?,?,?)""",
                    (m["id"], order, g.get("pickedByTeam"), g["map"],
                     g.get("vetoAction", "unknown"), "sample", g.get("pickVeto")),
                )
                n_veto += 1
            for ban in g.get("heroBans", []):
                hero_id = ban.get("hero") or ban.get("heroId")
                if not hero_id:
                    continue
                role_row = con.execute("SELECT role FROM heroes WHERE id=?", (hero_id,)).fetchone()
                con.execute(
                    """INSERT INTO hero_bans
                       (match_id, map_result_id, map_order, team_id, hero_id, role,
                        ban_order, source, confidence, notes)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (m["id"], mr_id, order, ban.get("teamId"), hero_id,
                     ban.get("role") or (role_row["role"] if role_row else None),
                     ban.get("order"), ban.get("source", "sample"),
                     ban.get("confidence", 0.99), ban.get("notes")),
                )
                n_bans += 1
            for team_key, comp in (("teamA", g.get("compA", [])), ("teamB", g.get("compB", []))):
                if not comp or m["id"] in newest:
                    continue
                team_id = m[team_key]
                fh = hashlib.sha1(
                    f"{m['id']}:{order}:{team_id}".encode()).hexdigest()[:16]
                cur = con.execute(
                    """INSERT INTO comp_snapshots
                       (match_id, map_result_id, team_id, stream_offset_seconds,
                        overall_confidence, frame_hash, source)
                       VALUES (?,?,?,?,?,?, 'sample')""",
                    (m["id"], mr_id, team_id, order * 600, 0.99, fh),
                )
                snap_id = cur.lastrowid
                con.executemany(
                    """INSERT INTO snapshot_heroes
                       (snapshot_id, slot, hero_id, confidence)
                       VALUES (?,?,?,?)""",
                    [(snap_id, slot, hid, 0.99)
                     for slot, hid in enumerate(comp, start=1)],
                )
                n_snap += 1

    con.execute("DELETE FROM team_prep_notes WHERE source='sample'")
    for note in data.get("teamPrepNotes", []):
        con.execute(
            """INSERT INTO team_prep_notes
               (team_id, opponent_team_id, map_id, note_type, note, source)
               VALUES (?,?,?,?,?,?)""",
            (note["teamId"], note.get("opponentTeamId"), note.get("mapId"),
             note.get("noteType", "general"), note["note"], note.get("source", "sample")),
        )
    con.commit()
    print(f"Loaded {len(data['matches'])} sample matches, {n_snap} comp snapshots, "
          f"{n_bans} hero bans, {n_veto} map pick/veto rows.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-sample", action="store_true",
                    help="also load demo matches/comps/FACEIT-style metadata")
    args = ap.parse_args()

    con = db.connect()
    db.init_schema(con)
    migrate(con)
    data = load_sample()
    seed_reference(con, data)
    if args.with_sample:
        seed_sample_matches(con, data)
        seed_sample_rosters(con, data)  # after matches: FK + REPLACE cascade
    print(f"DB ready at {db.DB_PATH}")


if __name__ == "__main__":
    main()
