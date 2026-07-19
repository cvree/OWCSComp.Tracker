#!/usr/bin/env python3
"""
promote_detections.py — the SAFE gate from CV detections to DB comps.

Detections become comps only THROUGH this gate, never around it. It reads one
auto run's reports/auto/<run>/detections.json (written by run_owcs_auto's
detect step — CV only, no DB writes there) and splits every per-frame snapshot
into two buckets by confidence:

  high          all 10 slots >= the layout match_threshold AND the team's
                overall confidence >= the promote floor (default 0.60) AND (by
                default) at least 2 CONSISTENT consecutive snapshots agree on
                the same 5 heroes for that team — cross-frame agreement, so a
                single lucky frame can't promote itself.
  needs-review  anything else. These NEVER become comps automatically; they go
                to a review queue JSON that admin.html lists for a human.

Even a `high` snapshot is only WRITTEN to the DB when the run is explicitly
paired to a match + map + teams (--match/--map-order/--team-a/--team-b) and
--write is given. Without pairing the tool is a dry run: it classifies,
writes the review queue + a promotion report, and writes ZERO comps — the
honest default while this VOD is still in calibration. This preserves every
hard rule:

  * FACEIT never infers comps — pairing only supplies match/map/team STRUCTURE.
  * comps are source='cv' here or source='manual' via apply_corrections.
  * manual overrides cv at export; this tool never deletes manual rows and is
    idempotent per (frame_hash, team) so re-running never double-writes.

Usage:
  # dry run — classify + build review queue, write nothing to the DB
  python pipeline/promote_detections.py --run owcs-8c105lnzlam_000600_000640

  # actually write the high-confidence cv comps for a PAIRED run
  python pipeline/promote_detections.py --run <run> --write \
      --match <match_id> --map-order 1 \
      --team-a <team_id> --team-b <team_id>
"""
from __future__ import annotations
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

REPORTS_DIR = os.path.join(db.REPO_ROOT, "reports", "auto")
DEFAULT_PROMOTE_FLOOR = 0.60      # overall-confidence floor for 'high'
DEFAULT_MIN_CONSECUTIVE = 2       # consistent snapshots required to promote


def log(msg: str) -> None:
    print(f"[promote] {msg}", flush=True)


# --------------------------------------------------------------- load + gate
def load_detections(run: str) -> dict:
    path = os.path.join(REPORTS_DIR, run, "detections.json")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"no detections.json for run '{run}' at {path} — the run's detect "
            "step was skipped (templates not ready?) or the run doesn't exist.")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _slots_all_strong(slots: list[dict], threshold: float) -> bool:
    """Every one of the 5 slots present and >= threshold (per-slot floor)."""
    if len(slots) != 5:
        return False
    return all(s.get("score", 0) >= threshold for s in slots)


def classify(detections: dict, threshold: float,
             promote_floor: float = DEFAULT_PROMOTE_FLOOR,
             min_consecutive: int = DEFAULT_MIN_CONSECUTIVE) -> dict:
    """Split accepted snapshots into high / needs-review, PER TEAM.

    A team-snapshot is a candidate for 'high' when all 5 slots clear the
    per-slot threshold and the team overall confidence clears promote_floor.
    It only actually becomes 'high' when min_consecutive candidate snapshots
    in a row (ordered by offset) agree on the exact same 5 heroes — so
    promotion needs cross-frame agreement, not one good frame.

    Returns {high: [...], review: [...], counts: {...}} where each item is
    {team_side, offset, frame, frame_hash, heroes, confidence, slots, reason}.
    """
    accepted = detections.get("accepted", []) or []
    quarantined = detections.get("quarantined", []) or []

    # candidates per side, in offset order
    per_side: dict[str, list[dict]] = {"a": [], "b": []}
    review: list[dict] = []
    for snap in sorted(accepted, key=lambda s: s.get("offset", 0)):
        for side in ("a", "b"):
            team = snap.get(side) or {}
            slots = team.get("slots", []) or []
            heroes = team.get("heroes", []) or []
            conf = team.get("confidence", 0)
            item = {
                "team_side": side,
                "offset": snap.get("offset"),
                "frame": snap.get("frame"),
                "frame_hash": snap.get("frame_hash"),
                "heroes": heroes,
                "confidence": conf,
                "slots": slots,
            }
            strong = _slots_all_strong(slots, threshold)
            if strong and conf >= promote_floor:
                per_side[side].append(item)
            else:
                if not strong:
                    weak = [s for s in slots if s.get("score", 0) < threshold]
                    item["reason"] = (
                        f"{len(weak)} slot(s) below {threshold} "
                        f"or missing (have {len(slots)}/5)")
                else:
                    item["reason"] = (
                        f"overall confidence {conf} < promote floor "
                        f"{promote_floor}")
                review.append(item)

    # cross-frame consistency: within each side, find runs of >=min_consecutive
    # candidates that share the same 5-hero set; promote those, review the rest.
    high: list[dict] = []
    for side, cands in per_side.items():
        i = 0
        while i < len(cands):
            j = i
            key = tuple(sorted(cands[i]["heroes"]))
            while (j + 1 < len(cands)
                   and tuple(sorted(cands[j + 1]["heroes"])) == key):
                j += 1
            group = cands[i:j + 1]
            if len(group) >= min_consecutive:
                for it in group:
                    it = dict(it)
                    it["reason"] = (
                        f"high — {len(group)} consecutive consistent "
                        f"snapshots agree")
                    high.append(it)
            else:
                for it in group:
                    it = dict(it)
                    it["reason"] = (
                        f"only {len(group)} consistent snapshot(s) < "
                        f"{min_consecutive} required — needs review")
                    review.append(it)
            i = j + 1

    return {
        "high": high,
        "review": review,
        "counts": {
            "accepted_frames": len(accepted),
            "quarantined_frames": len(quarantined),
            "high": len(high),
            "needsReview": len(review),
        },
    }


# ------------------------------------------------------------------- writing
def _pairing_ok(pairing: dict) -> tuple[bool, str]:
    need = ("match", "mapOrder", "teamA", "teamB")
    missing = [k for k in need if not pairing.get(k)]
    if missing:
        return False, ("run is not paired to a match — missing "
                       + ", ".join(missing)
                       + ". Pair with --match/--map-order/--team-a/--team-b "
                       "(structure only; FACEIT never infers the comp).")
    return True, ""


def write_high(con, run: str, high: list[dict], pairing: dict) -> dict:
    """Idempotently write source='cv' comps for high snapshots of a PAIRED run.

    Structure (match/map_result/team) comes from `pairing`; heroes + scores
    come from CV. UNIQUE(frame_hash, team_id) makes re-runs idempotent, and
    manual rows are never touched. Returns {written, skippedExisting, teams}.
    """
    ok, why = _pairing_ok(pairing)
    if not ok:
        raise ValueError(why)
    mr = con.execute(
        "SELECT id FROM map_results WHERE match_id=? AND map_order=?",
        (pairing["match"], pairing["mapOrder"])).fetchone()
    if not mr:
        raise ValueError(
            f"no map {pairing['mapOrder']} in match '{pairing['match']}' "
            "— ingest FACEIT match facts first (this tool never invents them).")
    m = con.execute("SELECT team_a, team_b FROM matches WHERE id=?",
                    (pairing["match"],)).fetchone()
    if not m:
        raise ValueError(f"match '{pairing['match']}' not in DB")
    side_team = {"a": pairing["teamA"], "b": pairing["teamB"]}
    for side, tid in side_team.items():
        if tid not in (m["team_a"], m["team_b"]):
            raise ValueError(
                f"team '{tid}' (side {side}) did not play match "
                f"'{pairing['match']}'")
    known = {r["id"] for r in con.execute("SELECT id FROM heroes")}

    written = skipped = 0
    for it in high:
        team_id = side_team[it["team_side"]]
        heroes = it["heroes"]
        bad = [h for h in heroes if h not in known]
        if bad:
            log(f"  offset {it['offset']} team {team_id}: unknown hero ids "
                f"{bad} — skipped (needs template/name fix)")
            continue
        fh = it.get("frame_hash") or f"cv-{run}-{it['offset']}-{it['team_side']}"
        exists = con.execute(
            "SELECT 1 FROM comp_snapshots WHERE frame_hash=? AND team_id=?",
            (fh, team_id)).fetchone()
        if exists:
            skipped += 1
            continue
        cur = con.execute(
            """INSERT INTO comp_snapshots
               (match_id, map_result_id, team_id, stream_offset_seconds,
                overall_confidence, frame_hash, source)
               VALUES (?,?,?,?,?,?, 'cv')""",
            (pairing["match"], mr["id"], team_id, it["offset"],
             it.get("confidence"), fh))
        con.executemany(
            "INSERT INTO snapshot_heroes (snapshot_id, slot, hero_id, "
            "confidence) VALUES (?,?,?,?)",
            [(cur.lastrowid, s.get("slot", i), s.get("hero"), s.get("score"))
             for i, s in enumerate(it.get("slots", []), start=1)])
        written += 1
    return {"written": written, "skippedExisting": skipped,
            "teams": sorted(set(side_team.values()))}


# ------------------------------------------------------------------- reports
def write_review_queue(run: str, result: dict, pairing: dict,
                       wrote: dict | None) -> str:
    """Write reports/auto/<run>/review_queue.json (the human's to-do list)."""
    out = os.path.join(REPORTS_DIR, run, "review_queue.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    payload = {
        "run": run,
        "counts": result["counts"],
        "paired": _pairing_ok(pairing)[0],
        "pairing": pairing,
        "written": wrote,
        "needsReview": [
            {k: it.get(k) for k in ("team_side", "offset", "heroes",
                                    "confidence", "reason", "frame_hash")}
            for it in result["review"]
        ],
        "high": [
            {k: it.get(k) for k in ("team_side", "offset", "heroes",
                                    "confidence", "reason", "frame_hash")}
            for it in result["high"]
        ],
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    return out


# ---------------------------------------------------------------------- main
def promote(run: str, write: bool = False, pairing: dict | None = None,
            promote_floor: float = DEFAULT_PROMOTE_FLOOR,
            min_consecutive: int = DEFAULT_MIN_CONSECUTIVE,
            con=None) -> dict:
    """Full gate: classify -> (optionally) write high comps -> review queue."""
    pairing = pairing or {}
    detections = load_detections(run)
    threshold = float(detections.get("match_threshold")
                      or detections.get("threshold") or 0.6)
    result = classify(detections, threshold, promote_floor, min_consecutive)
    log(f"run {run}: {result['counts']['accepted_frames']} accepted frame(s) "
        f"-> {result['counts']['high']} high, "
        f"{result['counts']['needsReview']} needs-review "
        f"({result['counts']['quarantined_frames']} quarantined)")

    wrote = None
    paired, why = _pairing_ok(pairing)
    if write:
        if not paired:
            log(f"NOT writing comps — {why}")
        elif not result["high"]:
            log("NOT writing comps — no high-confidence snapshots this run.")
        else:
            own = con is None
            con = con or db.connect()
            try:
                wrote = write_high(con, run, result["high"], pairing)
                con.commit()
                log(f"WROTE {wrote['written']} cv comp(s), "
                    f"skipped {wrote['skippedExisting']} already-present "
                    f"(idempotent). Manual rows untouched.")
            finally:
                if own:
                    con.close()
    else:
        if result["high"]:
            log(f"dry run — {len(result['high'])} snapshot(s) WOULD promote "
                "if paired + --write. Nothing written.")
        else:
            log("dry run — nothing would promote yet (needs review/calibration).")

    qpath = write_review_queue(run, result, pairing, wrote)
    log(f"review queue -> {os.path.relpath(qpath, db.REPO_ROOT)}")
    return {"run": run, "result": result, "written": wrote,
            "reviewQueue": qpath, "paired": paired}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Safely promote CV detections to comps (gated + reviewed)")
    ap.add_argument("--run", required=True,
                    help="auto run id (folder under reports/auto/)")
    ap.add_argument("--write", action="store_true",
                    help="actually write high-confidence cv comps (requires "
                    "pairing); without this it's a dry run")
    ap.add_argument("--match", help="internal match id to pair to")
    ap.add_argument("--map-order", type=int, dest="map_order",
                    help="which map in the match (1-based)")
    ap.add_argument("--team-a", dest="team_a",
                    help="team id for the LEFT side (slots_a)")
    ap.add_argument("--team-b", dest="team_b",
                    help="team id for the RIGHT side (slots_b)")
    ap.add_argument("--promote-floor", type=float,
                    default=DEFAULT_PROMOTE_FLOOR,
                    help=f"overall-confidence floor for 'high' "
                    f"(default {DEFAULT_PROMOTE_FLOOR})")
    ap.add_argument("--min-consecutive", type=int,
                    default=DEFAULT_MIN_CONSECUTIVE,
                    help="consistent consecutive snapshots required to promote "
                    f"(default {DEFAULT_MIN_CONSECUTIVE})")
    args = ap.parse_args(argv)

    pairing = {"match": args.match, "mapOrder": args.map_order,
               "teamA": args.team_a, "teamB": args.team_b}
    try:
        promote(args.run, write=args.write, pairing=pairing,
                promote_floor=args.promote_floor,
                min_consecutive=args.min_consecutive)
    except FileNotFoundError as e:
        log(f"FAILED — {e}")
        return 1
    except ValueError as e:
        log(f"FAILED — {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
