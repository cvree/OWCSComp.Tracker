#!/usr/bin/env python3
"""
broadcast_teams.py — every team the broadcast names becomes a team, with its logo.

The map index (`broadcast_maps.py`) reads both team names off the HUD of
every map. A team the dataset does not know is kept as the name the
broadcast printed, marked `?`. The owner's call (2026-10-30): add them
automatically, with their logo.

  * Identity: a printed name becomes a team row — display name, a short
    code, the printed spelling kept as an alias — recorded with
    source_authority 'broadcast-hud', so it is always clear where it came
    from. Only names the index already carries (each won a clear majority
    of its frames, harmonised across the broadcast) are ever added.
  * Logo: the OWCS HUD draws each team's mark in black on a white plate
    beside its name. The plate is cut from every frame the team is on
    screen, the median of those crops removes compression noise, and the
    dark mark inside the plate becomes a white mark on transparency — one
    consistent style across every team on the site's dark pages. Official
    OWCS broadcast graphics are a recognised logo source
    (assets/data/team_asset_sources.json); the crop's provenance is
    recorded beside it. A team that already has a logo keeps it.

CLI:
  # on a runner, right after live_ingest (its frames are cached)
  python3 pipeline/broadcast_teams.py --timeline probe/timeline.json --url <vod>
  # from frames on disk named *_t<seconds>.jpg
  python3 pipeline/broadcast_teams.py --timeline timeline.json --frames dir/
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

TEAMS_DIR = os.path.join(db.REPO_ROOT, "assets", "img", "teams")
# Where every team logo's provenance is registered; build_asset_manifest.py
# turns an entry with a sourceUrl and a file on disk into a verified logo.
SOURCES_PATH = os.path.join(db.REPO_ROOT, "assets", "data", "team_asset_sources.json")
# The logo plate beside each team name, at 1280x720 (scaled to the frame).
LOGO_ZONES = {"team_a": (60, 31, 44, 38), "team_b": (1176, 31, 44, 38)}
LOGO_SIZE = 128
MIN_CROPS = 3
SOURCE = "broadcast-hud"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def display(name: str) -> str:
    """'TEAM LIQUID' -> 'Team Liquid'; short tags (T1, JD, AG.AL) stay as printed."""
    words = name.split()
    return " ".join(w if (i == 0 and len(w) <= 3) or any(c.isdigit() or c == "." for c in w)
                    else w.capitalize() for i, w in enumerate(words))


def code(name: str) -> str:
    words = name.replace(".", "").split()
    if len(words) == 1:
        return words[0][:4].upper() if len(words[0]) <= 4 else words[0][:3].upper()
    if len(words[0]) <= 3:              # 'JD GAMING' -> 'JDG'
        return (words[0] + "".join(w[0] for w in words[1:])).upper()[:4]
    return "".join(w[0] for w in words).upper()[:4]


# --------------------------------------------------------------- the logo
def crop_logo(gray, key: str):
    fh, fw = gray.shape[:2]
    x, y, w, h = LOGO_ZONES[key]
    sx, sy = fw / 1280.0, fh / 720.0
    return gray[int(y * sy):int((y + h) * sy), int(x * sx):int((x + w) * sx)]


def logo_from_crops(crops: list):
    """A white-on-transparent RGBA mark from same-zone crops, or None."""
    import cv2
    import numpy as np
    if len(crops) < MIN_CROPS:
        return None
    shape = crops[0].shape
    crops = [c.astype(np.float32) for c in crops if c.shape == shape]
    med = np.median(np.stack(crops), axis=0)
    big = cv2.resize(med, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    white = (big > 165).astype(np.uint8)
    pts = cv2.findNonZero(white)
    if pts is None or len(pts) < 50:
        return None
    plate = np.zeros_like(white)
    cv2.fillConvexPoly(plate, cv2.convexHull(pts), 1)
    plate = cv2.erode(plate, np.ones((9, 9), np.uint8))
    alpha = np.clip((175 - big) * 3.0, 0, 255) * plate
    ys, xs = np.nonzero(alpha > 40)
    if len(xs) < 40:
        return None
    a = alpha[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    side = max(a.shape) + 16
    out = np.zeros((side, side, 4), np.uint8)
    out[..., :3] = 255
    oy, ox = (side - a.shape[0]) // 2, (side - a.shape[1]) // 2
    out[oy:oy + a.shape[0], ox:ox + a.shape[1], 3] = a.astype(np.uint8)
    return cv2.resize(out, (LOGO_SIZE, LOGO_SIZE), interpolation=cv2.INTER_AREA)


def collect_crops(frames, maps: list[dict]) -> dict[str, list]:
    """frames: iterable of (t seconds, gray image). maps: timeline maps with
    identity. -> {printed or known team value: [crop, ...]}"""
    out: dict[str, list] = collections.defaultdict(list)
    for t, gray in frames:
        m = next((m for m in maps if m["start"] <= t <= m["end"]), None)
        if not m or gray is None:
            continue
        for key in LOGO_ZONES:
            v = ((m.get("identity") or {}).get(key) or {}).get("value")
            if v:
                out[v].append(crop_logo(gray, key))
    return out


# ------------------------------------------------------------ the teams
def unknown_names(maps: list[dict]) -> set[str]:
    return {f["value"] for m in maps for k in LOGO_ZONES
            for f in [((m.get("identity") or {}).get(k) or {})]
            if f.get("value") and not f.get("known")}


def register_source(tid: str, name: str, url: str | None, now: str,
                    path: str = SOURCES_PATH) -> None:
    with open(path, encoding="utf-8") as f:
        reg = json.load(f)
    reg.setdefault("teams", {})[tid] = dict(
        reg.get("teams", {}).get(tid, {}),
        sourceUrl=url or "https://www.twitch.tv/ow_esports",
        sourceKind="official-owcs-broadcast",
        attribution=f"{display(name)} mark as drawn by the official OWCS broadcast HUD "
                    "(team plate), cut from the indexed broadcast and redrawn white.",
        retrievedAt=now)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(reg, f, indent=1, ensure_ascii=False)
        f.write("\n")


def ensure_teams(con, names: set[str], logos: dict, *, now: str,
                 teams_dir: str = TEAMS_DIR, video: str | None = None,
                 url: str | None = None, sources_path: str | None = SOURCES_PATH) -> list[dict]:
    """Add each printed name as a team (or find the one it already is) and
    give it its logo if it has none. Returns what was done, per name."""
    import cv2
    done = []
    for name in sorted(names):
        tid = slug(name)
        row = con.execute("SELECT id, logo_url FROM teams WHERE id = ? OR upper(name) = upper(?)",
                          (tid, display(name))).fetchone()
        added = row is None
        if added:
            con.execute(
                "INSERT INTO teams (id, name, region, code, aliases, source_authority, "
                "identity_verified_at, status, needs_review) VALUES (?,?,?,?,?,?,?,'active',0)",
                (tid, display(name), "Unknown", code(name), json.dumps([name]), SOURCE, now))
            logo_url = None
        else:
            tid, logo_url = row[0], row[1]
        wrote = False
        if not logo_url and logos.get(name) is not None:
            d = os.path.join(teams_dir, tid)
            os.makedirs(d, exist_ok=True)
            cv2.imwrite(os.path.join(d, "logo.png"), logos[name])
            with open(os.path.join(d, "logo.source.json"), "w", encoding="utf-8", newline="\n") as f:
                json.dump({"source": "Official OWCS broadcast graphics (HUD team plate)",
                           "sourceKind": SOURCE, "video": video, "method":
                           "median of the team's HUD logo plate over every indexed frame; "
                           "dark mark kept, redrawn white on transparency",
                           "recordedAt": now}, f, indent=1)
                f.write("\n")
            con.execute("UPDATE teams SET logo_url = ? WHERE id = ?",
                        (f"assets/img/teams/{tid}/logo.png", tid))
            if sources_path:
                register_source(tid, name, url, now, sources_path)
            wrote = True
        done.append({"name": name, "id": tid, "added": added, "logo": wrote})
    con.commit()
    return done


def link_records(con, data_dir: str | None = None) -> int:
    """Point every committed map-index team that is now a team at its id."""
    import broadcast_maps as bm
    data_dir = data_dir or bm.DATA_DIR
    teams = {}
    for tid, name, aliases in con.execute("SELECT id, name, aliases FROM teams"):
        for n in [name] + (json.loads(aliases) if aliases else []):
            teams[re.sub(r"[^A-Z0-9]", "", n.upper())] = tid
    changed = 0
    for path in sorted(glob.glob(os.path.join(data_dir, "*.json"))):
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        dirty = False
        for m in rec.get("maps") or []:
            for k in LOGO_ZONES:
                t = m.get(k)
                if t and not t.get("id"):
                    tid = teams.get(re.sub(r"[^A-Z0-9]", "", t["name"].upper()))
                    if tid:
                        t["id"] = tid
                        dirty = True
        if dirty:
            bm.save(rec, data_dir)
            changed += 1
    return changed


# ----------------------------------------------------------------- frames
def frames_from_dir(d: str):
    import cv2
    for p in sorted(glob.glob(os.path.join(d, "*.jpg"))):
        m = re.search(r"_t(\d+(?:\.\d+)?)", os.path.basename(p))
        if m:
            yield float(m.group(1)), cv2.imread(p, cv2.IMREAD_GRAYSCALE)


def frames_from_url(url: str, timeline: dict, per_map: int = 8):
    """Gameplay samples inside each map window — the cache live_ingest
    filled on this runner, so nothing is fetched twice."""
    import cv2
    import remote_frames as rf
    src = rf.RemoteFrameSource(url, height=720)
    offs = []
    for m in timeline.get("maps") or []:
        inside = [s["offset"] for s in timeline.get("samples") or []
                  if s.get("state") == "gameplay" and m["start"] <= s["offset"] <= m["end"]]
        step = max(1, len(inside) // per_map)
        offs += inside[::step][:per_map]
    got = src.grab(offs)
    for t in offs:
        if got.get(round(float(t), 1)):
            yield t, cv2.imread(got[round(float(t), 1)], cv2.IMREAD_GRAYSCALE)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--timeline", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url")
    src.add_argument("--frames")
    args = ap.parse_args(argv)
    import broadcast_maps as bm
    import live_identity as lid
    with open(args.timeline, encoding="utf-8") as f:
        timeline = json.load(f)
    maps = lid.harmonise_series([dict(m) for m in timeline.get("maps") or []])
    frames = frames_from_dir(args.frames) if args.frames else frames_from_url(args.url, timeline)
    crops = collect_crops(frames, maps)
    names = unknown_names(maps)
    logos = {n: logo_from_crops(crops.get(n) or []) for n in names}
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    con = db.connect()
    try:
        done = ensure_teams(con, names, logos, now=now,
                            video=bm._video_id(timeline.get("url") or ""),
                            url=timeline.get("url"))
        linked = link_records(con)
    finally:
        con.close()
    for d in done:
        print(f"[broadcast-teams] {d['name']:<20} -> {d['id']:<16} "
              f"{'added' if d['added'] else 'known'}{' + logo' if d['logo'] else ''}")
    print(f"[broadcast-teams] {linked} map-index record(s) linked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
