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
import sqlite3
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


# ------------------------------------------------- one org, one identity
# A HUD name is OCR. "WHIBO GAMING" and "WHO GAMING" are Weibo Gaming read
# badly, and adding every printed spelling as its own org published three
# Weibo Gamings, a team called "Team" and a "Feamrisk" as confirmed facts.
#
#   * MERGE_NAME: a spelling this close to a known team IS that team.
#   * MERGE_WITH_LOGO: a looser spelling counts only when the HUD drew the
#     same mark beside it. The name alone is not enough at this distance
#     ("JD GAMING" / "OU GAMING" is 0.75, and live_identity's own note is
#     that 0.72 once read Geekay Esports as Gen.G).
#   * Anything else is still added, as the owner asked, but a name that is
#     a bare generic word, or that resembles a team another source names
#     without matching it, is flagged needs_review with the reason, and
#     the site lists it as unconfirmed rather than as an org.
MERGE_NAME = 0.9
MERGE_WITH_LOGO = 0.8
LOGO_SAME = 0.9
RESEMBLES = 0.8
GENERIC_WORDS = {"TEAM", "GAMING", "ESPORTS", "ESPORT", "CLUB", "THE", "GG"}
SUMMARY_PATH = os.path.join(db.REPO_ROOT, "assets", "data", "external_summary.v1.js")


def _key(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (name or "").upper())


def name_ratio(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, _key(a), _key(b)).ratio()


def _alpha(img):
    import cv2
    if img is None:
        return None
    a = img[..., 3] if img.ndim == 3 and img.shape[2] == 4 else img
    if a.ndim == 3:
        a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    return cv2.resize(a, (64, 64), interpolation=cv2.INTER_AREA).astype("float32")


def logo_similarity(a, b) -> float | None:
    """Correlation of two marks' shapes, 1.0 = the same drawing. None when
    either is missing: no logo is no evidence, not disagreement."""
    a, b = _alpha(a), _alpha(b)
    if a is None or b is None:
        return None
    a, b = a - a.mean(), b - b.mean()
    den = float(((a * a).sum() * (b * b).sum()) ** 0.5)
    return float((a * b).sum() / den) if den else None


def _team_logo(teams_dir: str, tid: str):
    import cv2
    path = os.path.join(teams_dir, tid, "logo.png")
    return cv2.imread(path, cv2.IMREAD_UNCHANGED) if os.path.exists(path) else None


def _cols(con) -> set[str]:
    return {r[1] for r in con.execute("PRAGMA table_info(teams)")}


def resolve_known(con, name: str, logo=None, *, teams_dir: str = TEAMS_DIR,
                  exclude: tuple = (), only=None) -> dict | None:
    """The team a printed name is a misreading of, or None.
    -> {id, name, ratio, logo, by} for the best candidate that clears the bar."""
    best = None
    for tid, tname, aliases in con.execute("SELECT id, name, aliases FROM teams"):
        if tid in exclude or (only is not None and tid not in only):
            continue
        spellings = [tname] + (json.loads(aliases) if aliases else [])
        ratio = max(name_ratio(name, n) for n in spellings)
        if best is None or ratio > best["ratio"]:
            best = {"id": tid, "name": tname, "ratio": ratio}
    if not best or best["ratio"] < MERGE_WITH_LOGO or len(_key(name)) < 4:
        return None
    sim = logo_similarity(logo, _team_logo(teams_dir, best["id"]))
    best["logo"] = sim
    if best["ratio"] >= MERGE_NAME:
        best["by"] = "spelling"
    elif sim is not None and sim >= LOGO_SAME:
        best["by"] = "spelling + same HUD mark"
    else:
        return None
    return best


def reference_names(path: str = SUMMARY_PATH) -> list[str]:
    """Team names another source spells out (the community spreadsheet's
    team list). Used ONLY to flag a HUD spelling as doubtful, never to
    rename or merge anything: the two datasets stay separate."""
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
        data = json.loads(src[src.index("{"):src.rindex("}") + 1])
    except (OSError, ValueError):
        return []
    teams = data.get("teams") or {}
    rows = teams.values() if isinstance(teams, dict) else teams
    return sorted({t["name"] for t in rows if t.get("name")})


def doubt(name: str, reference: list[str] | None = None) -> str | None:
    """Why a printed name should not be shown as a confirmed org, or None."""
    words = name.upper().split()
    if len(words) == 1 and words[0] in GENERIC_WORDS:
        return (f"The broadcast HUD read only the word '{name}', which is part of several "
                "team names. Which team it was is not known.")
    for ref in reference or []:
        if _key(ref) == _key(name):
            return None
    near = sorted(((name_ratio(name, r), r) for r in reference or []), reverse=True)[:1]
    if near and near[0][0] >= RESEMBLES:
        return (f"The broadcast HUD read '{name}'. The community spreadsheet names "
                f"'{near[0][1]}', which may be the same team spelled differently. Not confirmed.")
    return None


def _add_alias(con, tid: str, spelling: str) -> None:
    row = con.execute("SELECT aliases FROM teams WHERE id = ?", (tid,)).fetchone()
    aliases = json.loads(row[0]) if row and row[0] else []
    if spelling not in aliases:
        con.execute("UPDATE teams SET aliases = ? WHERE id = ?",
                    (json.dumps(aliases + [spelling]), tid))


def _flag(con, tid: str, reason: str | None) -> bool:
    if not reason or "needs_review" not in _cols(con):
        return False
    if "review_reason" in _cols(con):
        con.execute("UPDATE teams SET needs_review = 1, review_reason = ? WHERE id = ?", (reason, tid))
    else:
        con.execute("UPDATE teams SET needs_review = 1 WHERE id = ?", (tid,))
    return True



def ensure_teams(con, names: set[str], logos: dict, *, now: str,
                 teams_dir: str = TEAMS_DIR, video: str | None = None,
                 url: str | None = None, sources_path: str | None = SOURCES_PATH,
                 reference: list[str] | None = None) -> list[dict]:
    """Add each printed name as a team (or find the one it already is) and
    give it its logo if it has none. Returns what was done, per name.

    A name that is a misreading of a team already on record becomes that
    team's alias instead of a second org (resolve_known); a name that is
    added but doubtful is flagged for review (doubt)."""
    import cv2
    done = []
    for name in sorted(names):
        tid = slug(name)
        row = con.execute("SELECT id, logo_url FROM teams WHERE id = ? OR upper(name) = upper(?)",
                          (tid, display(name))).fetchone()
        merged = None
        if row is None:
            merged = resolve_known(con, name, logos.get(name), teams_dir=teams_dir)
            if merged:
                _add_alias(con, merged["id"], name)
                row = con.execute("SELECT id, logo_url FROM teams WHERE id = ?", (merged["id"],)).fetchone()
        added = row is None
        if added:
            con.execute(
                "INSERT INTO teams (id, name, region, code, aliases, source_authority, "
                "identity_verified_at, status, needs_review) VALUES (?,?,?,?,?,?,?,'active',0)",
                (tid, display(name), "Unknown", code(name), json.dumps([name]), SOURCE, now))
            logo_url = None
            _flag(con, tid, doubt(name, reference))
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
        entry = {"name": name, "id": tid, "added": added, "logo": wrote}
        if merged:
            entry["mergedBy"] = merged["by"]
        done.append(entry)
    con.commit()
    return done


def _referenced(con, tid: str) -> list[str]:
    """Every table.column outside teams.id that holds this team id."""
    hits = []
    for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
        for col in con.execute(f'PRAGMA table_info("{table}")').fetchall():
            if table == "teams" and col[1] == "id":
                continue
            try:
                n = con.execute(f'SELECT count(*) FROM "{table}" WHERE "{col[1]}" = ?', (tid,)).fetchone()[0]
            except sqlite3.Error:
                continue
            if n:
                hits.append(f"{table}.{col[1]}")
    return hits


def repair(con, *, data_dir: str | None = None, teams_dir: str = TEAMS_DIR,
           sources_path: str | None = SOURCES_PATH, reference: list[str] | None = None,
           dry_run: bool = False) -> list[dict]:
    """Apply today's identity rules to the teams earlier runs already added.

    Each broadcast-hud team is checked against the teams that were on record
    before it. One that resolves to an earlier team is merged into it: its
    spelling becomes an alias, the map index points at the surviving id, its
    duplicate logo and source entry go. One that stays but is doubtful is
    flagged. A team any other table refers to is never merged here."""
    import shutil
    import broadcast_maps as bm
    data_dir = data_dir or bm.DATA_DIR
    hud = con.execute(
        "SELECT id, name, aliases, identity_verified_at FROM teams WHERE source_authority = ? "
        "ORDER BY identity_verified_at, id", (SOURCE,)).fetchall()
    earlier = {r[0] for r in con.execute(
        "SELECT id FROM teams WHERE source_authority IS NOT ? ", (SOURCE,))}
    out = []
    for tid, name, aliases, _when in hud:
        printed = (json.loads(aliases) if aliases else [name])[0]
        hit = resolve_known(con, printed, _team_logo(teams_dir, tid), teams_dir=teams_dir,
                            exclude=(tid,), only=earlier)
        refs = _referenced(con, tid) if hit else []
        if hit and not refs:
            out.append({"id": tid, "action": "merged", "into": hit["id"], "by": hit["by"],
                        "ratio": round(hit["ratio"], 3)})
            if not dry_run:
                for spelling in (json.loads(aliases) if aliases else [name]):
                    _add_alias(con, hit["id"], spelling)
                con.execute("DELETE FROM teams WHERE id = ?", (tid,))
                for path in sorted(glob.glob(os.path.join(data_dir, "*.json"))):
                    with open(path, encoding="utf-8") as f:
                        rec = json.load(f)
                    dirty = False
                    for m in rec.get("maps") or []:
                        for k in LOGO_ZONES:
                            if (m.get(k) or {}).get("id") == tid:
                                m[k]["id"] = hit["id"]
                                dirty = True
                    if dirty:
                        bm.save(rec, data_dir)
                shutil.rmtree(os.path.join(teams_dir, tid), ignore_errors=True)
                if sources_path and os.path.exists(sources_path):
                    with open(sources_path, encoding="utf-8") as f:
                        reg = json.load(f)
                    if reg.get("teams", {}).pop(tid, None) is not None:
                        with open(sources_path, "w", encoding="utf-8", newline="\n") as f:
                            json.dump(reg, f, indent=1, ensure_ascii=False)
                            f.write("\n")
            continue
        earlier.add(tid)
        reason = doubt(printed, reference)
        if hit and refs:
            reason = (f"Probably {hit['name']} misread, but other records refer to this id "
                      f"({', '.join(refs)}), so it was not merged automatically.")
        if reason:
            out.append({"id": tid, "action": "flagged", "reason": reason})
            if not dry_run:
                _flag(con, tid, reason)
    if not dry_run:
        con.commit()
    return out


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
    ap.add_argument("--timeline")
    ap.add_argument("--repair", action="store_true",
                    help="merge misread duplicates already on record and flag doubtful names")
    ap.add_argument("--dry-run", action="store_true", help="with --repair: report, change nothing")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--url")
    src.add_argument("--frames")
    args = ap.parse_args(argv)
    import broadcast_maps as bm
    if args.repair:
        con = db.connect()
        try:
            for r in repair(con, reference=reference_names(), dry_run=args.dry_run):
                print(f"[broadcast-teams] {r['id']:<16} {r['action']:<8} " +
                      (f"-> {r['into']} ({r['by']}, {r['ratio']})" if r["action"] == "merged" else r["reason"]))
        finally:
            con.close()
        return 0
    if not args.timeline or not (args.url or args.frames):
        ap.error("--timeline and one of --url / --frames are required")
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
                            url=timeline.get("url"), reference=reference_names())
        linked = link_records(con)
    finally:
        con.close()
    for d in done:
        print(f"[broadcast-teams] {d['name']:<20} -> {d['id']:<16} "
              f"{'added' if d['added'] else 'known'}{' + logo' if d['logo'] else ''}"
              f"{' (misread, merged by ' + d['mergedBy'] + ')' if d.get('mergedBy') else ''}")
    print(f"[broadcast-teams] {linked} map-index record(s) linked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
