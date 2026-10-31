#!/usr/bin/env python3
"""
team_logos.py — every org gets its mark.

Most teams get their logo automatically: broadcast_teams.py cuts it from
the OWCS HUD the first time a broadcast they played on is indexed. A team
the dataset knows from somewhere else (an earlier stage, a FACEIT room)
may never have been on an indexed broadcast, so it has no mark yet and
the site draws its crest instead.

This fills those in from the org's OWN published image — an official
site, store or press kit — and records where it came from:

  * candidate URLs come from assets/data/team_asset_sources.json
    (`assetCandidates[].url`, state validated/published), or from
    `--set team=url` for a source a person just looked up;
  * the image is padded to a square and resized to 128 px (transparent
    padding, Lanczos), written to assets/img/teams/<id>/logo.png with a
    logo.source.json beside it naming the URL and the time;
  * the team's `logo_url` is set in data/owcs.sqlite, so the next
    `export_data.py --public` publishes it. A team that already has a
    logo keeps it unless --replace.

Never a third-party stats site, never a guess at which mark is whose.

CLI:
  python3 pipeline/team_logos.py --report                 # who has no logo
  python3 pipeline/team_logos.py                          # fetch every candidate
  python3 pipeline/team_logos.py --set gen=https://.../logo.png
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

TEAMS_DIR = os.path.join(db.REPO_ROOT, "assets", "img", "teams")
SOURCES_PATH = os.path.join(db.REPO_ROOT, "assets", "data", "team_asset_sources.json")
SIZE = 128
OK_STATES = {"validated", "published", None}
UA = "Mozilla/5.0 (OWCS Comp Tracker team-logo fetch; +https://github.com/cvree/OWCSComp.Tracker)"


def missing(con) -> list[tuple[str, str]]:
    """(id, name) of every team with no logo, or a logo file that is gone."""
    out = []
    for tid, name, url in con.execute("SELECT id, name, logo_url FROM teams ORDER BY id"):
        if not url or not os.path.exists(os.path.join(db.REPO_ROOT, url)):
            out.append((tid, name))
    return out


def candidates(tid: str, sources: dict, overrides: dict[str, str]) -> list[str]:
    if tid in overrides:
        return [overrides[tid]]
    entry = (sources.get("teams") or {}).get(tid) or {}
    urls = [c["url"] for c in entry.get("assetCandidates") or []
            if c.get("url") and c.get("state") in OK_STATES]
    if entry.get("logoUrl"):
        urls.insert(0, entry["logoUrl"])
    return urls


def fetch(url: str, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (fixed, reviewed URLs)
        return r.read()


def normalise(data: bytes):
    """Decode any common image and return a SIZE x SIZE BGRA image, the
    mark centred on transparency. None when it cannot be decoded."""
    import cv2
    import numpy as np
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None or img.size == 0:
        return None
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    elif img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    h, w = img.shape[:2]
    side = max(h, w)
    canvas = np.zeros((side, side, 4), np.uint8)
    y, x = (side - h) // 2, (side - w) // 2
    canvas[y:y + h, x:x + w] = img
    interp = cv2.INTER_AREA if side > SIZE else cv2.INTER_LANCZOS4
    return cv2.resize(canvas, (SIZE, SIZE), interpolation=interp)


def save(con, tid: str, url: str, img, now: str, teams_dir: str = TEAMS_DIR,
         sources_path: str | None = SOURCES_PATH) -> str:
    import cv2
    d = os.path.join(teams_dir, tid)
    os.makedirs(d, exist_ok=True)
    cv2.imwrite(os.path.join(d, "logo.png"), img)
    with open(os.path.join(d, "logo.source.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"source": url, "sourceKind": "official-website",
                   "method": f"org's own published image, padded square and resized to {SIZE}px",
                   "recordedAt": now}, f, indent=1)
        f.write("\n")
    rel = f"assets/img/teams/{tid}/logo.png"
    con.execute("UPDATE teams SET logo_url = ? WHERE id = ?", (rel, tid))
    if sources_path and os.path.exists(sources_path):
        with open(sources_path, encoding="utf-8") as f:
            reg = json.load(f)
        entry = reg.setdefault("teams", {}).setdefault(tid, {})
        entry.update(sourceUrl=url, sourceKind="official-website", retrievedAt=now)
        with open(sources_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(reg, f, indent=1, ensure_ascii=False)
            f.write("\n")
    return rel


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--report", action="store_true", help="list teams with no logo and exit")
    ap.add_argument("--set", action="append", default=[], metavar="TEAM=URL",
                    help="use this official image URL for TEAM (repeatable, or comma-separated)")
    ap.add_argument("--replace", action="store_true", help="also replace logos that exist")
    ap.add_argument("--db", default=db.DB_PATH)
    args = ap.parse_args(argv)

    overrides = {}
    for item in ",".join(args.set).split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            if k.strip() and v.strip():
                overrides[k.strip()] = v.strip()

    con = sqlite3.connect(args.db)
    todo = missing(con)
    if args.replace or overrides:
        names = dict(con.execute("SELECT id, name FROM teams"))
        for tid in overrides:
            if tid in names and tid not in dict(todo):
                todo.append((tid, names[tid]))
    if args.report:
        print(f"{len(todo)} team(s) without a logo:")
        for tid, name in todo:
            print(f"  {tid:20s} {name}")
        return 0

    with open(SOURCES_PATH, encoding="utf-8") as f:
        sources = json.load(f)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    got, failed = [], []
    for tid, name in todo:
        urls = candidates(tid, sources, overrides)
        if not urls:
            failed.append((tid, "no official source on record — pass --set %s=<url>" % tid))
            continue
        for url in urls:
            try:
                img = normalise(fetch(url))
            except Exception as e:  # noqa: BLE001 — report and try the next source
                print(f"  {tid}: {url} -> {e}")
                continue
            if img is None:
                print(f"  {tid}: {url} -> not an image")
                continue
            got.append((tid, save(con, tid, url, img, now)))
            break
        else:
            failed.append((tid, "every candidate failed"))
    con.commit()
    for tid, rel in got:
        print(f"LOGO  {tid:20s} {rel}")
    for tid, why in failed:
        print(f"NONE  {tid:20s} {why}")
    print(f"{len(got)} logo(s) written, {len(failed)} team(s) still on their crest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
