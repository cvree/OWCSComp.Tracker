#!/usr/bin/env python3
"""
hero_gap_finder.py — find the heroes a package cannot see, and say where to
go and get them.

`hero_coverage.py` answers *which heroes are missing*. That list is 44 names
long and, on its own, useless: it tells an operator what they already knew
and nothing about what to do next. This module closes that gap two ways,
and both of them are things the machine can do without a human guessing.

### 1. Where a missing hero has already been seen (`plan_from_db`)

The content database records every hero stint the pipeline has ever read,
against a match, against a VOD URL, at a timestamp. So for any uncovered
hero the question "where do I find footage of it" usually has an exact
answer already sitting in the database — this broadcast, this offset. What
comes out is a harvest plan: hero, source, the offsets to sample, and the
command that does it.

For a hero that has never been seen at all, the plan says so plainly rather
than inventing a source. That is the honest state of a hero the project has
no footage of, and no amount of processing changes it.

### 2. What is in footage that the package cannot name (`scan_footage`)

The other direction, and the one that finds heroes nobody has recorded yet.
Sample a clip, read all ten slots, and keep the **UNKNOWN** ones. An UNKNOWN
that appears once is noise — a dissolve, an ult flash, a killfeed card
sitting on a portrait. An UNKNOWN that appears in the same slot, looking the
same, for thirty consecutive samples is *a hero this package has no template
for*, and it is exactly the crop a harvest wants.

So the UNKNOWNs are clustered by appearance and filtered by persistence, and
each surviving cluster becomes a candidate with its own crop, its time span,
and its slot. Nothing is labelled: the module cannot know which hero it is
looking at, and a plausible-looking guess written into a template set is the
single worst thing this tool could produce. Candidates go to the review
inbox, where a human names them and `template_forge.py` does the rest.

CLI:
  python3 pipeline/hero_gap_finder.py --layout owcs_jksix_qwc
  python3 pipeline/hero_gap_finder.py --layout owcs_jksix_qwc \\
      --frames work/ingest/<id>/frames --out work/candidates/<id>
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import hero_coverage as hc  # noqa: E402
import template_bootstrap as tb  # noqa: E402

# An UNKNOWN cluster must persist for at least this many samples before it
# is offered as a candidate hero. One frame is a dissolve; a run of them in
# one slot is something the package genuinely cannot name.
MIN_PERSISTENCE = 8
# Correlation at which two UNKNOWN crops are "the same thing".
CLUSTER_SIM = 0.62
# Offsets to suggest sampling around a known stint, as fractions of it. The
# middle of a stint is safest (no swap dissolve), the quarter points give
# state variety.
SAMPLE_POINTS = (0.25, 0.5, 0.75)


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------- where to find them
def _table_exists(con, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone() is not None


def sightings(con, hero_ids: set[str]) -> dict[str, list[dict]]:
    """{hero_id: [{matchId, vodUrl, start, end, observations}, ...]}.

    Read from `hero_stints` — the pipeline's own record of what it saw,
    where, and when. A sighting is not proof the hero is harvestable (the
    VOD may be gone, the stint may have been a misread), so every entry
    carries the confidence the detector had at the time and the caller is
    expected to show it.
    """
    out: dict[str, list[dict]] = defaultdict(list)
    if not _table_exists(con, "hero_stints"):
        return {}
    rows = con.execute(
        """SELECT s.hero_id, s.match_id, s.slot, s.side, s.start_offset,
                  s.end_offset, s.n_obs, s.mean_conf, s.status,
                  m.vod_url, m.event_name
           FROM hero_stints s
           LEFT JOIN matches m ON m.id = s.match_id
           ORDER BY s.n_obs DESC""")
    for r in rows:
        if r["hero_id"] not in hero_ids:
            continue
        out[r["hero_id"]].append({
            "matchId": r["match_id"], "event": r["event_name"],
            "vodUrl": r["vod_url"], "side": r["side"], "slot": r["slot"],
            "start": r["start_offset"], "end": r["end_offset"],
            "observations": r["n_obs"], "meanConfidence": r["mean_conf"],
            "status": r["status"],
        })
    return dict(out)


def _sample_offsets(start, end) -> list[float]:
    if start is None or end is None or end <= start:
        return [float(start)] if start is not None else []
    span = float(end) - float(start)
    return [round(float(start) + span * f, 1) for f in SAMPLE_POINTS]


def plan_from_db(con, layout_id: str, *,
                 repo_root: str = db.REPO_ROOT) -> dict:
    """A harvest plan for every hero this package cannot currently see."""
    coverage = hc.layout_coverage(con, layout_id, repo_root=repo_root)
    missing = {h["id"] for h in coverage["heroes"]
               if h["state"] == hc.MISSING}
    unproven = [h["id"] for h in coverage["heroes"]
                if h["state"] == hc.UNPROVEN]
    seen = sightings(con, missing)

    reachable, unseen = [], []
    for hero in coverage["heroes"]:
        if hero["id"] not in missing:
            continue
        found = seen.get(hero["id"]) or []
        if not found:
            unseen.append({"id": hero["id"], "name": hero["name"],
                           "role": hero["role"]})
            continue
        best = found[0]
        reachable.append({
            "id": hero["id"], "name": hero["name"], "role": hero["role"],
            "sightings": len(found),
            "bestSource": best,
            "sampleOffsets": _sample_offsets(best["start"], best["end"]),
            "command": (
                f"python3 pipeline/ingest_map.py --layout "
                f"layouts/{layout_id}.json --ingest-id <id> "
                f"--start {best['start']} --end {best['end']}   "
                f"# then: template_evidence -> template_forge"),
        })

    return {
        "generatedAt": _utcnow_iso(),
        "layoutId": layout_id,
        "rosterSize": coverage["rosterSize"],
        "covered": coverage["covered"],
        "validated": coverage["validated"],
        "readiness": coverage["readiness"],
        "reachable": sorted(reachable, key=lambda h: -h["sightings"]),
        "neverSeen": sorted(unseen, key=lambda h: h["id"]),
        "unprovenHeroes": unproven,
        "note": (
            f"{len(reachable)} missing hero(es) have already been seen by "
            f"this pipeline and can be harvested from recorded footage; "
            f"{len(unseen)} have never appeared in any processed broadcast, "
            f"so no amount of reprocessing will cover them — they need "
            f"footage that contains them."),
    }


# --------------------------------------------- what the package cannot name
def scan_frames(frames_dir: str, layout: dict, templates_dir: str, *,
                every: int = 1, limit: int | None = None) -> dict:
    """Cluster the UNKNOWN slot reads in a directory of gameplay frames.

    Returns clusters that persisted long enough to be a hero rather than a
    transition. Nothing is labelled — see the module docstring.
    """
    import cv2
    import capture
    import detect

    profile = detect.detector_profile(layout)
    lib = detect.load_templates(templates_dir, roi=profile["roi"])

    files = sorted(glob.glob(os.path.join(frames_dir, "*.png"))
                   + glob.glob(os.path.join(frames_dir, "*.jpg")))[::every]
    if limit:
        files = files[:limit]

    per_slot: dict[str, list[dict]] = defaultdict(list)
    scanned = 0
    for path in files:
        frame = cv2.imread(path)
        if frame is None:
            continue
        fh, fw = frame.shape[:2]
        scaled, info = capture.scale_layout_to_frame(layout, fw, fh)
        if not info["ok"]:
            continue
        scanned += 1
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        for side in ("a", "b"):
            for i, (x, y, w, h) in enumerate(scaled[f"slots_{side}"], 1):
                crop = gray[y:y + h, x:x + w]
                if crop.size == 0:
                    continue
                read = detect.read_slot(crop, lib, floor=profile["floor"],
                                        min_margin=profile["minMargin"],
                                        roi=profile["roi"])
                if read["hero"] != "UNKNOWN":
                    continue
                per_slot[f"{side}{i}"].append({
                    "file": os.path.basename(path),
                    "gray": detect.apply_roi(crop, profile["roi"]),
                    "colour": frame[y:y + h, x:x + w],
                    "bestGuess": read["second"] or read["template"],
                    "topScore": read["score"],
                    "reject": read["reject"],
                })

    clusters = []
    for slot, entries in sorted(per_slot.items()):
        for group in _cluster(entries):
            if len(group) < MIN_PERSISTENCE:
                continue
            rep = max(group, key=lambda e: _sharp(e["gray"]))
            clusters.append({
                "slot": slot,
                "samples": len(group),
                "firstFrame": group[0]["file"],
                "lastFrame": group[-1]["file"],
                "representative": rep,
                "meanTopScore": round(
                    sum(e["topScore"] for e in group) / len(group), 3),
                "why": (
                    f"{len(group)} samples in slot {slot} showed the same "
                    f"portrait and none of them matched any template in this "
                    f"package (best non-match averaged "
                    f"{sum(e['topScore'] for e in group) / len(group):.2f}). "
                    f"That is what an uncovered hero looks like."),
            })
    clusters.sort(key=lambda c: -c["samples"])
    return {"framesScanned": scanned, "clusters": clusters,
            "unknownReads": sum(len(v) for v in per_slot.values())}


def _sharp(gray) -> float:
    import template_quality as tq
    return tq.sharpness(gray)


def _cluster(entries: list[dict]) -> list[list[dict]]:
    import template_quality as tq
    groups: list[list[dict]] = []
    protos: list = []
    for entry in entries:
        placed = False
        for i, proto in enumerate(protos):
            if tq.correlate(proto, entry["gray"]) >= CLUSTER_SIM:
                groups[i].append(entry)
                placed = True
                break
        if not placed:
            groups.append([entry])
            protos.append(entry["gray"])
    return groups


def write_candidates(scan: dict, out_dir: str, *, layout_id: str,
                     source: str | None = None) -> dict:
    """Put the clusters in the review inbox, as crops plus an inbox.json.

    Deliberately the same shape `template_forge` writes, so the control
    room's template-review view renders both without knowing which produced
    them.
    """
    import cv2
    review = os.path.join(out_dir, "_review")
    os.makedirs(review, exist_ok=True)
    items = []
    for n, cluster in enumerate(scan["clusters"]):
        name = f"unknown_{cluster['slot']}_{n}.png"
        cv2.imwrite(os.path.join(review, name),
                    cluster["representative"]["colour"])
        items.append({
            "file": name, "heroId": None, "slot": cluster["slot"],
            "t": None, "samples": cluster["samples"],
            "reasons": [cluster["why"]],
            "metrics": {"meanTopScore": cluster["meanTopScore"]},
            "sourceReport": source,
            "why": ("an unnamed portrait this package has no template for — "
                    "label it and run template_forge.py; it is NOT a "
                    "template until then"),
        })
    payload = {
        "generatedAt": _utcnow_iso(), "layoutId": layout_id,
        "sourceReport": source, "items": items,
        "note": ("Uncovered heroes found by scanning footage. Nothing here "
                 "is labelled — this tool cannot know which hero it is "
                 "looking at, and a plausible guess written into a template "
                 "set is the worst thing it could produce."),
    }
    with open(os.path.join(review, "inbox.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
        f.write("\n")
    return payload


def format_plan(plan: dict) -> str:
    lines = [f"  {plan['layoutId']}: {plan['readiness']}", f"  {plan['note']}"]
    for hero in plan["reachable"][:20]:
        src = hero["bestSource"]
        lines.append(
            f"    HARVEST  {hero['id']:<10} seen in {src['matchId']} "
            f"slot {src['slot']} t{src['start']}-{src['end']} "
            f"({src['observations']} obs)"
            + (f"  {src['vodUrl']}" if src.get("vodUrl") else
               "  (no VOD URL recorded)"))
    if plan["neverSeen"]:
        lines.append(f"    NO FOOTAGE {len(plan['neverSeen'])} hero(es) have "
                     f"never appeared in a processed broadcast: "
                     + ", ".join(h["id"] for h in plan["neverSeen"][:20])
                     + (" …" if len(plan["neverSeen"]) > 20 else ""))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="find the heroes a package cannot see, and where to get them")
    ap.add_argument("--layout", required=True, help="layout id")
    ap.add_argument("--frames", help="scan a directory of gameplay frames for "
                                     "portraits this package cannot name")
    ap.add_argument("--every", type=int, default=1)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out", help="write candidate crops here (with --frames)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    con = db.connect()
    db.init_schema(con)
    try:
        plan = plan_from_db(con, args.layout)
    finally:
        con.close()

    if args.frames:
        import capture
        layout = capture.load_layout(
            os.path.join(db.REPO_ROOT, "layouts", f"{args.layout}.json"))
        tdir = os.path.join(db.REPO_ROOT, layout["templates_dir"])
        scan = scan_frames(args.frames, layout, tdir,
                           every=args.every, limit=args.limit)
        plan["scan"] = {k: v for k, v in scan.items() if k != "clusters"}
        plan["scan"]["clusters"] = [
            {k: v for k, v in c.items() if k != "representative"}
            for c in scan["clusters"]]
        if args.out:
            written = write_candidates(scan, args.out,
                                       layout_id=args.layout,
                                       source=args.frames)
            plan["candidatesWritten"] = len(written["items"])

    if args.json:
        print(json.dumps(plan, indent=1, default=str))
    else:
        print(format_plan(plan))
        if "scan" in plan:
            s = plan["scan"]
            print(f"  scanned {s['framesScanned']} frame(s): "
                  f"{s['unknownReads']} unknown slot read(s), "
                  f"{len(s['clusters'])} persistent candidate(s)")
            for c in s["clusters"][:10]:
                print(f"    candidate slot {c['slot']}: {c['why']}")
            if plan.get("candidatesWritten"):
                print(f"  wrote {plan['candidatesWritten']} candidate crop(s) "
                      f"to {args.out}/_review — they appear in the control "
                      f"room's template review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
