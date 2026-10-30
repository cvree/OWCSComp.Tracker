#!/usr/bin/env python3
"""
broadcast_maps.py — every map of every broadcast, ready to watch.

An OWCS broadcast is six to twelve hours long and nobody wants to scrub it.
`live_ingest.py` finds each map's window and `live_identity.py` names it
(map, game number, both teams) off the broadcast's own HUD, with nobody
involved. This module keeps that result as public data:

  data/broadcast_maps/<videoId>.json   one broadcast, slim, committed
  assets/data/broadcast_maps.v1.js     what the site loads
                                       (window.OWCS_BROADCAST_MAPS)

Nothing here is a hero composition or a result. It is an index of where
each map starts, with how sure the reading is: a value carried by 90% or
fewer of its frames is marked `check` (the owner's rule), and a team the
database does not know keeps the name the broadcast printed, marked
`known: false`, until a person confirms it.

CLI:
  python3 pipeline/broadcast_maps.py --from-timeline probe/timeline.json
  python3 pipeline/broadcast_maps.py --index          # rebuild the site file
  python3 pipeline/broadcast_maps.py --index --check  # fail if it is stale
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

SCHEMA = "broadcast-maps.v1"
DATA_DIR = os.path.join(db.REPO_ROOT, "data", "broadcast_maps")
INDEX_PATH = os.path.join(db.REPO_ROOT, "assets", "data", "broadcast_maps.v1.js")
CHECK_SHARE = 0.9
FIELDS = ("map", "game", "team_a", "team_b")


def _video_id(url: str) -> str | None:
    m = re.search(r"twitch\.tv/videos/(\d+)", url or "") or \
        re.search(r"(?:v=|youtu\.be/|/live/)([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else None


def _share(field: dict) -> float:
    if field.get("share") is not None:
        return float(field["share"])
    # timelines written before `share` existed: votes over frames read
    return round(field.get("votes", 0) / field["of"], 3) if field.get("of") else 0.0


def slim(timeline: dict) -> dict:
    """The committed record of one broadcast: map windows and identities."""
    import live_identity as lid
    maps = lid.harmonise_series([dict(m) for m in timeline.get("maps") or []])
    series_of = {}
    for n, run in enumerate(lid.series(maps), 1):
        for m in run:
            series_of[m["index"]] = n
    out = []
    for m in maps:
        ident = m.get("identity") or {}
        row = {"index": m["index"], "start": round(m["start"]), "end": round(m["end"]),
               "series": series_of.get(m["index"])}
        check = []
        for k in FIELDS:
            f = ident.get(k) or {}
            if not f.get("value"):
                row[k] = None
                continue
            share = _share(f)
            # a known team is its id; an unknown one is the broadcast-wide,
            # series-corrected spelling (not one window's raw reading)
            row[k] = ({"name": f.get("printed") if f.get("known") else f["value"],
                       "id": f["value"] if f.get("known") else None}
                      if k.startswith("team") else f["value"])
            if share <= CHECK_SHARE:
                check.append(k)
        row["check"] = check
        if m.get("mergedPause"):
            row["paused"] = True
        out.append(row)
    url = timeline.get("url")
    return {"schema": SCHEMA, "videoId": _video_id(url), "url": url,
            "layout": timeline.get("layout"),
            "durationSeconds": timeline.get("durationSeconds"),
            "step": timeline.get("step"), "maps": out}


# Which broadcast graphics package an event uses, by the title it airs
# under. Only packages whose layout declares identity_zones belong here:
# a map index without names is not worth publishing. Add a line when a
# new event's HUD has been calibrated and its zones measured.
LAYOUT_BY_TITLE = (
    (re.compile(r"Midseason Championship", re.I), "owcs_8c105lnzlam"),
)
DISCOVERED = os.path.join(db.REPO_ROOT, "assets", "data", "discovered.v1.js")


def _discovered(path: str = DISCOVERED) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []
    return json.loads(text[text.index("{"):text.rindex("}") + 1]).get("broadcasts") or []


def next_broadcast(broadcasts: list[dict] | None = None,
                   data_dir: str = DATA_DIR) -> tuple[str, str] | None:
    """(url, layout id) of the newest broadcast worth indexing, or None.

    Twitch only (YouTube refuses GitHub's runners), an original rather than
    a rebroadcast, at least an hour long, an event whose HUD has measured
    identity zones, and never indexed before — an attempt that found no
    maps is recorded too, so nothing is probed twice."""
    done = {fn[:-5] for fn in os.listdir(data_dir)} if os.path.isdir(data_dir) else set()
    rows = broadcasts if broadcasts is not None else _discovered()
    cands = []
    for b in rows:
        url, vid = b.get("url") or "", b.get("videoId")
        if "twitch.tv/videos/" not in url or not vid or vid in done:
            continue
        if b.get("state") == "ignored" or re.search(r"rebroadcast", b.get("title") or "", re.I):
            continue
        if (b.get("durationSeconds") or 0) < 3600:
            continue
        layout = next((lay for rx, lay in LAYOUT_BY_TITLE if rx.search(b.get("title") or "")), None)
        if layout:
            cands.append((b.get("publishedAt") or "", url, layout))
    if not cands:
        return None
    _, url, layout = max(cands)
    return url, layout


def save(record: dict, data_dir: str = DATA_DIR) -> str:
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, f"{record['videoId']}.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(record, f, indent=1, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    return path


def build_index(data_dir: str = DATA_DIR) -> dict:
    """A pure function of the committed records — no clock — so --check can
    gate it byte for byte."""
    broadcasts = {}
    if os.path.isdir(data_dir):
        for fn in sorted(os.listdir(data_dir)):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(data_dir, fn), encoding="utf-8") as f:
                rec = json.load(f)
            if rec.get("schema") == SCHEMA and rec.get("videoId"):
                broadcasts[rec["videoId"]] = {k: rec[k] for k in
                                              ("url", "durationSeconds", "maps")}
    return {"schema": SCHEMA, "broadcasts": broadcasts}


def render(index: dict) -> str:
    return ("/* GENERATED by pipeline/broadcast_maps.py — where every map of every\n"
            "   indexed broadcast starts, read off the broadcast HUD. Not results,\n"
            "   not compositions. Do not hand-edit. */\n"
            "window.OWCS_BROADCAST_MAPS = "
            + json.dumps(index, indent=1, ensure_ascii=False, sort_keys=True) + ";\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from-timeline", help="a live_ingest timeline.json to record")
    ap.add_argument("--index", action="store_true", help="rebuild the site file")
    ap.add_argument("--check", action="store_true", help="with --index: fail if stale")
    ap.add_argument("--next", action="store_true",
                    help="print '<url> <layout id>' of the next broadcast to index")
    args = ap.parse_args(argv)
    if args.next:
        nxt = next_broadcast()
        if nxt:
            print(*nxt)
        return 0
    if args.from_timeline:
        with open(args.from_timeline, encoding="utf-8") as f:
            rec = slim(json.load(f))
        if not rec["videoId"]:
            print("broadcast-maps: the timeline's url names no video", file=sys.stderr)
            return 1
        print(f"broadcast-maps: wrote {save(rec)} ({len(rec['maps'])} maps)")
    if args.index:
        text = render(build_index())
        if args.check:
            try:
                with open(INDEX_PATH, encoding="utf-8") as f:
                    current = f.read()
            except OSError:
                current = None
            if current != text:
                print(f"{os.path.relpath(INDEX_PATH, db.REPO_ROOT)} is stale — run "
                      f"python3 pipeline/broadcast_maps.py --index", file=sys.stderr)
                return 1
            print(f"{os.path.relpath(INDEX_PATH, db.REPO_ROOT)} is current")
            return 0
        with open(INDEX_PATH, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print(f"broadcast-maps: {INDEX_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
