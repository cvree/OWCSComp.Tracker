#!/usr/bin/env python3
"""
set_map_mode.py — set (or correct) a map's game mode in game_maps.

Used after a new season map is auto-added with mode 'Unknown' (e.g. by
prep_obssojourn_match). Modes are validated against the real OW set so a
typo can't land a bogus mode in the catalog.

  python3 pipeline/set_map_mode.py --map neonjunction --mode Control
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

MODES = ("Control", "Escort", "Hybrid", "Push", "Flashpoint", "Clash")


def set_mode(map_id: str, mode: str, write: bool = True) -> dict:
    mode = mode.strip().capitalize()
    if mode not in MODES:
        return {"ok": False, "error": f"mode must be one of {MODES}"}
    con = db.connect()
    row = con.execute("SELECT id, name, mode FROM game_maps WHERE id=?",
                      (map_id,)).fetchone()
    if row is None:
        return {"ok": False, "error": f"no map with id '{map_id}'"}
    if write:
        con.execute("UPDATE game_maps SET mode=? WHERE id=?", (mode, map_id))
        con.commit()
    return {"ok": True, "map": row["name"], "id": map_id,
            "was": row["mode"], "now": mode, "written": write}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--map", required=True, help="game_maps id (e.g. neonjunction)")
    ap.add_argument("--mode", required=True, help=" | ".join(MODES))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    res = set_mode(args.map, args.mode, write=not args.dry_run)
    if not res["ok"]:
        raise SystemExit(res["error"])
    verb = "would set" if args.dry_run else "set"
    print(f"{verb} {res['map']} ({res['id']}): {res['was']} -> {res['now']}")


if __name__ == "__main__":
    main()
