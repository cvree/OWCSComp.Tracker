#!/usr/bin/env python3
"""
apply_match_facts.py — manual FACEIT-FACT overrides (NOT comps).

Public FACEIT often omits replay codes, hero bans, and pick/veto for OW2.
This applies hand-entered facts for those fields from
corrections/match_facts.json, writing them with source='manual_facts' so they
are clearly distinct from both FACEIT-ingested facts and tracker comps.

It NEVER writes comp_snapshots / snapshot_heroes — hero comps only ever come
from corrections/corrections.json (source='manual') or CV.

Per (match, mapOrder):
  - replayCode / pickedByTeam / vetoAction / notes -> update map_results
  - heroBans -> replace the manual_facts bans for that map (idempotent)

Validation (invalid rows skipped with a warning, valid ones still applied):
  - match must exist; mapOrder must exist for it
  - ban/picked team must be one of the match's two teams
  - every hero id must exist
Idempotent: re-running replaces the same map's manual_facts bans and
re-sets the same fields.

Usage:
  python3 pipeline/apply_match_facts.py
  python3 pipeline/apply_match_facts.py --file corrections/match_facts.json
  python3 pipeline/apply_match_facts.py --dry-run
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

DEFAULT_FILE = os.path.join(db.REPO_ROOT, "corrections", "match_facts.json")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", (s or "").lower()).strip("_")


def _match_row(con, match_id):
    return con.execute("SELECT team_a, team_b FROM matches WHERE id=?",
                       (match_id,)).fetchone()


def _map_row(con, match_id, order):
    return con.execute(
        "SELECT id FROM map_results WHERE match_id=? AND map_order=?",
        (match_id, order)).fetchone()


def validate_map(con, match_id, mm, heroes, teams) -> list[str]:
    errs = []
    order = mm.get("mapOrder")
    if order is None:
        return [f"{match_id}: map entry missing mapOrder"]
    if not _map_row(con, match_id, order):
        errs.append(f"{match_id} map {order}: no such map in match")
        return errs
    for b in mm.get("heroBans", []) or []:
        if slug(b.get("hero", "")) not in heroes:
            errs.append(f"{match_id} map {order}: unknown hero {b.get('hero')!r}")
        if b.get("team") and b["team"] not in teams:
            errs.append(f"{match_id} map {order}: ban team {b['team']!r} not in match")
    if mm.get("pickedByTeam") and mm["pickedByTeam"] not in teams:
        errs.append(f"{match_id} map {order}: pickedByTeam {mm['pickedByTeam']!r} not in match")
    return errs


def apply_map(con, match_id, mm) -> dict:
    order = mm["mapOrder"]
    mr = _map_row(con, match_id, order)
    mr_id = mr["id"]
    counts = {"fields": 0, "bans": 0}

    sets, params = [], []
    if mm.get("replayCode") is not None:
        sets.append("replay_code=?"); params.append(mm["replayCode"])
    if mm.get("pickedByTeam") is not None:
        sets.append("picked_by_team=?"); params.append(mm["pickedByTeam"])
    if mm.get("vetoAction") is not None:
        sets.append("veto_action=?"); params.append(mm["vetoAction"])
    if mm.get("notes") is not None:
        sets.append("notes=?"); params.append(mm["notes"])
    if sets:
        # mark map source as manual_facts only if it wasn't from FACEIT ingest;
        # keep existing source otherwise so we don't overwrite provenance of
        # scores/winner. We record fact provenance on the bans + notes instead.
        params.append(mr_id)
        con.execute(f"UPDATE map_results SET {', '.join(sets)} WHERE id=?", params)
        counts["fields"] = len(sets)

    bans = mm.get("heroBans")
    if isinstance(bans, list):
        con.execute("DELETE FROM hero_bans WHERE map_result_id=? AND "
                    "source='manual_facts'", (mr_id,))
        for i, b in enumerate(bans, start=1):
            con.execute(
                """INSERT INTO hero_bans
                   (match_id, map_result_id, map_order, team_id, hero_id,
                    ban_order, source)
                   VALUES (?,?,?,?,?,?, 'manual_facts')""",
                (match_id, mr_id, order, b.get("team"),
                 slug(b.get("hero", "")), b.get("order") or i))
            counts["bans"] += 1
    return counts


def apply_file(con, path: str, dry_run: bool = False) -> tuple[int, int]:
    if not con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                       "AND name='map_results'").fetchone():
        print("[facts] DB not initialized — run init_db.py first.")
        return (0, 0)
    if not os.path.exists(path):
        print(f"[facts] no file at {path} — nothing to apply.")
        return (0, 0)
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    entries = payload.get("matchFacts", [])
    if not entries:
        print("[facts] file present but empty — nothing to apply.")
        return (0, 0)

    heroes = {r["id"] for r in con.execute("SELECT id FROM heroes")}
    ok = bad = 0
    for entry in entries:
        match_id = entry.get("match")
        mrow = _match_row(con, match_id) if match_id else None
        if not mrow:
            print(f"[facts] SKIP unknown match {match_id!r}")
            bad += 1
            continue
        teams = {mrow["team_a"], mrow["team_b"]}
        for mm in entry.get("maps", []):
            errs = validate_map(con, match_id, mm, heroes, teams)
            if errs:
                for e in errs:
                    print(f"[facts] SKIP {e}")
                bad += 1
                continue
            if not dry_run:
                c = apply_map(con, match_id, mm)
                print(f"[facts] {match_id} map {mm['mapOrder']}: "
                      f"{c['fields']} field(s), {c['bans']} ban(s)")
            else:
                print(f"[facts] valid: {match_id} map {mm['mapOrder']}")
            ok += 1
    if not dry_run:
        con.commit()
    print(f"[facts] {ok} map(s) applied, {bad} skipped."
          + (" (dry run)" if dry_run else ""))
    return (ok, bad)


def main_from(args=None) -> None:
    apply_file(db.connect(), DEFAULT_FILE, dry_run=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=DEFAULT_FILE)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    apply_file(db.connect(), args.file, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
