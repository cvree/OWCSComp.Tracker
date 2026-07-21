#!/usr/bin/env python3
"""
seed_demo.py — one command to SEE the automation working, offline.

Because live FACEIT can't be fetched here, this simulates a full automated run
using the bundled fixtures as if they were real ingested rooms:

  init_db --with-sample
    -> prime the FACEIT cache from pipeline/fixtures/faceit/*.json
    -> ingest those rooms OFFLINE (real ingest code path)
    -> apply bundled demo match_facts (replay codes / bans the API lacks)
    -> apply a bundled demo comp correction (one side of one FACEIT map)
    -> validate
    -> export assets/js/data.js

Result: data.js contains the 12 sample matches PLUS automated FACEIT matches
that show up in the Missing Comps workbench and Review Progress dashboard —
one of them partially reviewed — so every M4 surface has something to show.

Run:  python3 pipeline/seed_demo.py
Then: python3 -m http.server 8000   (open prep.html to see the workbench)
"""
from __future__ import annotations
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db  # noqa: E402
import init_db  # noqa: E402
import faceit_parser as fp  # noqa: E402
import ingest_faceit as ing  # noqa: E402
import ingest_faceit_batch as batch  # noqa: E402
import apply_match_facts  # noqa: E402
import apply_corrections  # noqa: E402
import validate_data  # noqa: E402
import export_data  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "faceit")
CACHE = os.path.join(db.REPO_ROOT, "data", "raw", "faceit")

# Fixtures to present as "ingested rooms" and the canonical room url each maps to
DEMO_ROOMS = [
    ("real_room_c55d6822.json",
     "https://www.faceit.com/en/ow2/room/1-c55d6822-7ae7-4c53-b86c-015daa712dd3",
     "EMEA"),
    ("room_full.json",
     "https://www.faceit.com/en/ow2/room/1-abc12345-0000-0000-0000-fixturefull01",
     "NA"),
]


def prime_and_ingest(con):
    os.makedirs(CACHE, exist_ok=True)
    rooms = []
    for fixture, url, region in DEMO_ROOMS:
        body = open(os.path.join(FIX, fixture), encoding="utf-8").read()
        open(os.path.join(CACHE, ing.cache_key_for(url) + ".body"), "w").write(body)
        rooms.append({"url": url, "region": region, "stage": "Demo Stage"})
    summary = batch.run_batch(con, rooms, CACHE, offline=True)
    print(f"[demo] ingested {summary['ingested']} FACEIT rooms offline "
          f"({summary['maps']} maps)")
    return rooms


def demo_match_facts(con):
    """Add replay codes + bans to the c55d6822 room's map 1 (facts the API lacks)."""
    mid = "faceit-1-c55d6822-7ae7-4c53-b86c-015daa712dd3"
    m = con.execute("SELECT team_a, team_b FROM matches WHERE id=?", (mid,)).fetchone()
    if not m:
        return
    facts = {"matchFacts": [{"match": mid, "maps": [{
        "mapOrder": 1, "replayCode": "DEMO01", "pickedByTeam": m["team_a"],
        "vetoAction": "pick",
        "heroBans": [{"team": m["team_a"], "hero": "sombra", "order": 1},
                     {"team": m["team_b"], "hero": "widow", "order": 2}],
        "notes": "Demo: entered manually (public API lacks replay/bans)."}]}]}
    path = os.path.join(db.REPO_ROOT, "data", "raw", "faceit", "_demo_facts.json")
    json.dump(facts, open(path, "w"))
    apply_match_facts.apply_file(con, path)
    os.remove(path)


def demo_correction(con):
    """Correct ONE team's comp on the c55d6822 map 1 -> shows a half-reviewed map."""
    mid = "faceit-1-c55d6822-7ae7-4c53-b86c-015daa712dd3"
    m = con.execute("SELECT team_a FROM matches WHERE id=?", (mid,)).fetchone()
    if not m:
        return
    corr = {"corrections": [{
        "match": mid, "mapOrder": 1, "team": m["team_a"],
        "openerComp": ["winston", "tracer", "genji", "kiriko", "juno"],
        "note": "Demo: reviewed from replay code DEMO01."}]}
    path = os.path.join(db.REPO_ROOT, "data", "raw", "faceit", "_demo_corr.json")
    json.dump(corr, open(path, "w"))
    apply_corrections.apply_file(con, path)
    os.remove(path)


def main():
    con = db.connect()
    db.init_schema(con)
    data = init_db.load_sample()
    init_db.seed_reference(con, data)
    init_db.seed_sample_matches(con, data)
    init_db.seed_sample_rosters(con, data)
    print("[demo] seeded schema + 12 sample matches")

    prime_and_ingest(con)
    demo_match_facts(con)
    demo_correction(con)

    print("[demo] validating...")
    validate_data.run_checks(con).render(strict=False)

    payload = export_data.build_payload(con)
    import datetime as dt
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    body = export_data.HEADER.format(ts=ts) + json.dumps(payload, indent=1) + ";\n"
    open(export_data.OUT_PATH, "w", encoding="utf-8").write(body)
    faceit_matches = [m for m in payload["matches"] if m["id"].startswith("faceit-")]
    print(f"\n[demo] wrote data.js — {len(payload['matches'])} matches "
          f"({len(faceit_matches)} from automated FACEIT ingest)")
    print("[demo] open the site:  python3 -m http.server 8000  -> prep.html")


if __name__ == "__main__":
    main()
