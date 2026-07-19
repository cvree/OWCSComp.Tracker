#!/usr/bin/env python3
"""
check_packaging.py — reproducibility / packaging gate.

Verifies that a fresh checkout OR a freshly-extracted ZIP contains
everything the tracker needs to render its verified milestone, with no
dangling references. Runs offline, no deps beyond the stdlib + the repo.

Checks:
  1. every layout's templates_dir exists and holds >=1 template PNG
  2. every layout reject-marker / anchor / replay template file exists
  3. the SQLite DB exists and carries the ingested Nepal milestone
     (map_result winner=twis, hero_stints, confirmed hero_swaps)
  4. the production public export exists, is non-demo, and its evidence
     paths (capture runs' frames, comp-snapshot evidence frames) resolve
  5. the dev fixture's evidence paths resolve (click-through rule)
  6. public HTML pages load public_data.v1.js BEFORE public_fixture.v1.js
  7. the ingest report + review pages exist for the milestone

Exit 0 = packaged correctly; exit 1 = something is missing/broken.

Usage:
  python pipeline/check_packaging.py            # check the current tree
  python pipeline/check_packaging.py --root DIR # check an extracted copy
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sqlite3
import sys

FAILS: list[str] = []
WARNS: list[str] = []
OKS = 0


def ok(msg: str) -> None:
    global OKS
    OKS += 1
    print(f"  PASS  {msg}")


def warn(msg: str) -> None:
    WARNS.append(msg)
    print(f"  WARN  {msg}")


def bad(msg: str) -> None:
    FAILS.append(msg)
    print(f"  FAIL  {msg}")


def exists(root: str, rel: str) -> bool:
    return os.path.exists(os.path.join(root, rel))


def load_public(root: str, fname: str) -> dict | None:
    """Parse a window.OWCS_PUBLIC = {...}; assignment file to a dict."""
    p = os.path.join(root, "assets", "data", fname)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        src = f.read()
    m = re.search(
        r"window\.OWCS_PUBLIC\s*=\s*(?:window\.OWCS_PUBLIC\s*\|\|\s*)?", src)
    if not m:
        return None
    body = src[m.end():].strip()
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S).strip().rstrip(";")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def check_layouts(root: str) -> None:
    print("layouts -> templates + marker assets:")
    lay_dir = os.path.join(root, "layouts")
    if not os.path.isdir(lay_dir):
        bad("layouts/ directory missing")
        return
    for fn in sorted(os.listdir(lay_dir)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(lay_dir, fn), encoding="utf-8") as f:
            lay = json.load(f)
        tdir = lay.get("templates_dir")
        # The starter youtube layout intentionally points at the shared
        # root templates/ dir; per-source layouts get their own dir.
        if tdir:
            full = os.path.join(root, tdir)
            pngs = ([x for x in os.listdir(full) if x.endswith(".png")]
                    if os.path.isdir(full) else [])
            if pngs:
                ok(f"{fn}: templates_dir '{tdir}' has {len(pngs)} templates")
            else:
                bad(f"{fn}: templates_dir '{tdir}' missing or empty")
        # anchor / replay templates are OPTIONAL: layouts document them as
        # placeholders and the gameplay filter honestly falls back to the
        # structural chip probe when they are absent. Missing -> warn, not
        # fail. reject markers (below) and templates_dir (above) are hard
        # requirements for the working pipeline.
        for key in ("anchor", "replay"):
            cfg = lay.get(key)
            if isinstance(cfg, dict) and cfg.get("template"):
                if exists(root, cfg["template"]):
                    ok(f"{fn}: {key} template '{cfg['template']}'")
                else:
                    warn(f"{fn}: optional {key} template '{cfg['template']}'"
                         " not cut (placeholder — filter falls back)")
        for marker in (lay.get("reject") or []):
            tpath = marker.get("template")
            if tpath:
                if exists(root, tpath):
                    ok(f"{fn}: reject '{marker.get('label')}' asset ok")
                else:
                    bad(f"{fn}: reject asset '{tpath}' missing")


def check_db(root: str) -> None:
    print("database -> Nepal milestone:")
    dbp = os.path.join(root, "data", "owcs.sqlite")
    if not os.path.exists(dbp):
        bad("data/owcs.sqlite missing")
        return
    con = sqlite3.connect(dbp)
    con.row_factory = sqlite3.Row
    try:
        mr = con.execute(
            """SELECT * FROM map_results
               WHERE match_id='m-qad-twis-s2po' AND map_order=1"""
        ).fetchone()
        if mr and mr["winner_team"] == "twis":
            ok("Nepal map_result present, winner=twis")
        else:
            bad(f"Nepal map_result missing or wrong winner "
                f"({mr['winner_team'] if mr else 'no row'})")
        n_stints = con.execute(
            "SELECT COUNT(*) FROM hero_stints WHERE ingest_id="
            "'qad-twis-nepal'").fetchone()[0]
        ok(f"{n_stints} hero stints") if n_stints >= 10 else bad(
            f"too few hero stints ({n_stints})")
        swaps = con.execute(
            "SELECT from_hero,to_hero,offset_seconds FROM hero_swaps "
            "WHERE ingest_id='qad-twis-nepal' AND status='confirmed' "
            "ORDER BY offset_seconds").fetchall()
        if len(swaps) == 2 and all(
                s["from_hero"] == "juno" and s["to_hero"] == "lucio"
                for s in swaps):
            ok(f"2 confirmed ZOX Juno->Lucio swaps at "
               f"{[s['offset_seconds'] for s in swaps]}")
        else:
            bad(f"expected 2 confirmed juno->lucio swaps, got "
                f"{[(s['from_hero'], s['to_hero']) for s in swaps]}")
    finally:
        con.close()


def check_public_export(root: str) -> None:
    print("production public export -> evidence resolves:")
    d = load_public(root, "public_data.v1.js")
    if d is None:
        bad("assets/data/public_data.v1.js missing or unparseable")
        return
    if d.get("meta", {}).get("demo") is False:
        ok("public_data.v1.js meta.demo=false (production)")
    else:
        bad("public_data.v1.js is not marked production (meta.demo)")
    m = next((x for x in d.get("matches", [])
              if x["id"] == "m-qad-twis-s2po"), None)
    if m and any(g.get("winner") == "twis" for g in m.get("maps", [])):
        ok("Nepal match exported with Twisted Minds map win")
    else:
        bad("Nepal match/winner missing from public export")
    dangling = 0
    for r in d.get("captureRuns", []):
        for fr in r.get("frames", []):
            if fr.get("file") and not exists(root, fr["file"]):
                dangling += 1
        for c in r.get("crops", []):
            if c and not exists(root, c):
                dangling += 1
    for s in d.get("compSnapshots", []):
        ev = s.get("evidenceFrame")
        if ev and not exists(root, ev):
            dangling += 1
    if dangling == 0:
        ok("all production evidence paths resolve")
    else:
        bad(f"{dangling} production evidence path(s) do not resolve")


def check_fixture(root: str) -> None:
    print("dev fixture -> evidence resolves:")
    d = load_public(root, "public_fixture.v1.js")
    if d is None:
        bad("public_fixture.v1.js missing or unparseable")
        return
    dangling = 0
    for r in d.get("captureRuns", []):
        for path in ([r.get("reportPath")]
                     + [fr.get("file") for fr in r.get("frames", [])]
                     + list(r.get("crops", []))):
            if path and not exists(root, path):
                dangling += 1
    if dangling == 0:
        ok("all fixture evidence paths resolve")
    else:
        bad(f"{dangling} fixture evidence path(s) do not resolve")


def check_pages(root: str) -> None:
    print("public pages -> data load order:")
    for page in ("match.html", "stats.html", "matches.html",
                 "tournament.html", "tournaments.html"):
        p = os.path.join(root, page)
        if not os.path.exists(p):
            bad(f"{page} missing")
            continue
        s = open(p, encoding="utf-8").read()
        i_prod = s.find("public_data.v1.js")
        i_fix = s.find("public_fixture.v1.js")
        if 0 <= i_prod < i_fix:
            ok(f"{page}: production data before fixture")
        else:
            bad(f"{page}: production data not loaded before fixture")


def check_reports(root: str) -> None:
    print("milestone report pages:")
    for rel in ("reports/ingest/qad-twis-nepal/report.html",
                "reports/ingest/qad-twis-nepal/review.html",
                "reports/calibration/owcs-jksix-qwc/sheet.png"):
        ok(f"{rel} present") if exists(root, rel) else bad(
            f"{rel} missing")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))),
        help="repo root to check (default: this repo)")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)
    print(f"packaging check on {root}\n")
    check_layouts(root)
    check_db(root)
    check_public_export(root)
    check_fixture(root)
    check_pages(root)
    check_reports(root)
    print()
    if WARNS:
        print(f"{len(WARNS)} warning(s) (non-fatal):")
        for w in WARNS:
            print(f"  - {w}")
        print()
    if FAILS:
        print(f"PACKAGING CHECK FAILED - {len(FAILS)} problem(s):")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print(f"PACKAGING OK - {OKS} checks passed"
          + (f", {len(WARNS)} warning(s)." if WARNS else "."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
