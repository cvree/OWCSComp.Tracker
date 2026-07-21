"""Public-site checks (tournaments / tournament / match / stats / matches).

Offline, no browser, same style as test_static_pages.py: verifies the new
public layer's pages, data contract, and — most importantly — the
credibility rules: comps only from cv/manual, only reviewed/auto-high
renders publicly, manual overrides cv, every evidence path resolves to a
real file, and the demo fixture stays separate from production exports.
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILS = 0


def check(name: str, ok: bool) -> None:
    global FAILS
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILS += 1


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def load_fixture() -> dict:
    src = read("assets/data/public_fixture.v1.js")
    # production wiring: the fixture is a guarded fallback
    # (window.OWCS_PUBLIC = window.OWCS_PUBLIC || {...})
    m = re.search(
        r"window\.OWCS_PUBLIC\s*=\s*(?:window\.OWCS_PUBLIC\s*\|\|\s*)?",
        src)
    body = src[m.end():].rstrip().rstrip(";")
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    return json.loads(body)


def main() -> None:
    print("fixture parses + declares itself demo:")
    d = load_fixture()
    check("fixture is valid JSON after the assignment", isinstance(d, dict))
    check("schema tag is public.v1", d.get("meta", {}).get("schema") == "public.v1")
    check("meta.demo is True (visible ribbon driver)", d["meta"].get("demo") is True)
    check("generatedAt is UTC ISO-8601", bool(re.match(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\+00:00|Z)", d["meta"]["generatedAt"])))
    for key in ["regions", "teams", "tournaments", "bracketRounds",
                "bracketMatches", "matches", "heroBans", "captureRuns",
                "compSnapshots", "heroes", "mapsCatalog"]:
        check(f"fixture has {key}", isinstance(d.get(key), list) and len(d[key]) > 0)
    check("all six regions present", {r["id"] for r in d["regions"]} ==
          {"all", "na", "emea", "asia", "china", "pacific"})

    print("demo/production separation:")
    exporter = read("pipeline/export_data.py")
    check("export_data.py never writes the fixture file",
          "public_fixture" not in exporter)
    check("contract doc exists", os.path.exists(
        os.path.join(ROOT, "docs/PUBLIC_DATA_CONTRACT.md")))

    print("referential integrity:")
    team_ids = {t["id"] for t in d["teams"]}
    tour_ids = {t["id"] for t in d["tournaments"]}
    match_ids = {m["id"] for m in d["matches"]}
    run_ids = {r["id"] for r in d["captureRuns"]}
    hero_ids = {h["id"] for h in d["heroes"]}
    map_ids = {m["id"] for m in d["mapsCatalog"]}
    round_ids = {r["id"] for r in d["bracketRounds"]} | {r["id"] for r in d.get("extraRounds", [])}
    node_ids = {b["id"] for b in d["bracketMatches"]}

    bad = [m["id"] for m in d["matches"] if m["tournamentId"] not in tour_ids]
    check("every match belongs to a real tournament", not bad)
    bad = [m["id"] for m in d["matches"]
           for tid in (m["teamA"], m["teamB"]) if tid is not None and tid not in team_ids]
    check("match teams resolve (or are explicit TBD nulls)", not bad)
    bad = [m["id"] for m in d["matches"]
           if m.get("captureRunId") and m["captureRunId"] not in run_ids]
    check("match captureRunId resolves to a capture run", not bad)
    bad = [b["id"] for b in d["bracketMatches"] if b["roundId"] not in round_ids]
    check("bracket nodes reference real rounds", not bad)
    bad = [b["id"] for b in d["bracketMatches"]
           for f in (b.get("feedsWinnerTo"), b.get("feedsLoserTo"))
           if f is not None and f not in node_ids]
    check("feedsWinnerTo/feedsLoserTo resolve to bracket node ids", not bad)
    bad = [b["id"] for b in d["bracketMatches"]
           if b.get("matchId") and b["matchId"] not in match_ids]
    check("bracket node matchIds resolve", not bad)
    bad = [b["id"] for b in d["heroBans"] if b["hero"] not in hero_ids]
    check("banned heroes exist in the hero catalog", not bad)
    bad = []
    for m in d["matches"]:
        for mp in m.get("maps", []):
            if mp["map"] not in map_ids:
                bad.append(mp["id"])
    check("played maps exist in the maps catalog", not bad)

    print("typed map scoreDetail:")
    valid_types = {"control", "escort", "hybrid", "push", "flashpoint", "clash"}
    problems = []
    for m in d["matches"]:
        for mp in m.get("maps", []):
            sd = mp.get("scoreDetail")
            if sd is None:
                if not mp.get("live") and m["status"] in ("completed",):
                    problems.append(f"{mp['id']} completed but null detail")
                continue
            t = sd.get("type")
            if t not in valid_types:
                problems.append(f"{mp['id']} bad type {t}")
            elif t == "control" and not sd.get("rounds"):
                problems.append(f"{mp['id']} control missing rounds")
            elif t in ("escort", "hybrid") and not (sd.get("a") and sd.get("b")):
                problems.append(f"{mp['id']} {t} missing a/b")
            elif t == "push" and not (sd.get("distanceA") and sd.get("distanceB")):
                problems.append(f"{mp['id']} push missing distances")
            elif t == "flashpoint" and "capturesA" not in sd:
                problems.append(f"{mp['id']} flashpoint missing captures")
            elif t == "clash" and "pointsA" not in sd:
                problems.append(f"{mp['id']} clash missing points")
    check(f"every scoreDetail is typed + complete ({len(problems)} problems)", not problems)
    fixture_modes = {sd["scoreDetail"]["type"]
                     for m in d["matches"] for sd in [mp for mp in m.get("maps", [])]
                     if sd.get("scoreDetail")}
    check("fixture exercises all score widget types",
          valid_types <= fixture_modes)

    print("comp credibility rules (the moat):")
    comps = d["compSnapshots"]
    check("every comp source is cv or manual — never faceit",
          all(c["source"] in ("cv", "manual") for c in comps))
    check("fixture includes a needs-review comp (public-filter proof)",
          any(c["reviewStatus"] == "needs-review" for c in comps))
    check("every comp has exactly 5 heroes",
          all(len(c["heroes"]) == 5 for c in comps))
    check("every comp hero exists",
          all(h in hero_ids for c in comps for h in c["heroes"]))
    check("every comp carries an evidence run id that resolves",
          all(c.get("evidenceRunId") in run_ids for c in comps))
    manual = [c for c in comps if c["source"] == "manual" and c.get("overridesId")]
    check("a manual correction overriding a cv row exists", bool(manual))
    if manual:
        target = next((c for c in comps if c["id"] == manual[0]["overridesId"]), None)
        check("the overridden cv row is kept in the data (never deleted)",
              target is not None and target["source"] == "cv")
        check("override is bidirectionally linked",
              target is not None and target.get("overriddenBy") == manual[0]["id"])
        check("manual correction carries a note",
              bool(manual[0].get("correction", {}).get("note")))

    core = read("assets/js/public/core.js")
    check("core.js hard-codes the approved review list",
          '"reviewed"' in core and '"auto-high"' in core
          and "APPROVED_REVIEW" in core)
    check("core.js publicComps blocks non-cv/manual sources",
          'c.source !== "cv" && c.source !== "manual"' in core)
    check("core.js publicComps drops overridden rows",
          "overriddenBy" in core or "overridesId" in core)
    stats_js = read("assets/js/public/stats.js")
    check("stats.js computes from publicComps only",
          "publicComps" in stats_js)
    check("stats rows carry evidence refs",
          "evidence" in stats_js and "snapshotIds" in stats_js)

    print("evidence paths resolve to real files:")
    missing = []
    for r in d["captureRuns"]:
        for p in filter(None, [r.get("reportPath")]):
            if not os.path.exists(os.path.join(ROOT, p)):
                missing.append(p)
        for f in r.get("frames", []):
            for p in filter(None, [f.get("file"), f.get("layoutDebug")]):
                if not os.path.exists(os.path.join(ROOT, p)):
                    missing.append(p)
        for p in r.get("crops", []):
            if not os.path.exists(os.path.join(ROOT, p)):
                missing.append(p)
    for c in comps:
        p = c.get("evidenceFrame")
        if p and not os.path.exists(os.path.join(ROOT, p)):
            missing.append(p)
    check(f"all report/frame/crop paths exist ({len(missing)} missing)", not missing)

    print("capture-status honesty:")
    ladder = {"needs-source", "queued", "capturing", "needs-review", "verified", "failed"}
    check("match captureStatus values stay on the ladder",
          all(m.get("captureStatus") in ladder for m in d["matches"]))
    check("run statuses stay on the ladder",
          all(r["status"] in ladder for r in d["captureRuns"]))
    check("a requested-vs-actual resolution mismatch exists (honesty fixture)",
          any(r.get("actualHeight") and r.get("requestedHeight")
              and r["actualHeight"] != r["requestedHeight"] for r in d["captureRuns"]))
    check("a failed run with an explanation exists",
          any(r["status"] == "failed" and r.get("note") for r in d["captureRuns"]))
    check("core.js labels every ladder state",
          all(f'"{s}"' in core for s in ladder))

    print("public pages load the public shell:")
    pages = ["tournaments.html", "tournament.html", "match.html",
             "stats.html", "matches.html", "team.html", "teams.html"]
    for p in pages:
        h = read(p)
        check(f"{p}: public.css + fixture + core + shell wired",
              "assets/css/public.css" in h
              and "assets/data/public_fixture.v1.js" in h
              and "assets/js/public/core.js" in h
              and "assets/js/public/shell.js" in h)
        check(f"{p}: body.pub + skip link + lang attr",
              '<body class="pub">' in h and "skip-link" in h
              and '<html lang="en">' in h)
        check(f"{p}: no framework, vendored motion only",
              "react" not in h.lower()
              and "assets/vendor/lenis.min.js" in h
              and "assets/vendor/gsap.min.js" in h
              and "cdn." not in h.replace("fonts.gstatic", ""))
    check("stats page loads the stats module",
          "assets/js/public/stats.js" in read("stats.html"))
    check("vendored lenis + gsap actually exist",
          os.path.getsize(os.path.join(ROOT, "assets/vendor/lenis.min.js")) > 1000
          and os.path.getsize(os.path.join(ROOT, "assets/vendor/gsap.min.js")) > 10000)
    for p in pages:
        h = read(p)
        check(f"{p}: loads the uplink motion stack (all vendored)",
              "assets/vendor/ScrollTrigger.min.js" in h
              and "assets/vendor/three.min.js" in h
              and "assets/vendor/vanta.net.min.js" in h
              and "assets/js/motion.js" in h)
    check("vendored three + vanta + ScrollTrigger actually exist",
          os.path.getsize(os.path.join(ROOT, "assets/vendor/three.min.js")) > 100000
          and os.path.getsize(os.path.join(ROOT, "assets/vendor/vanta.net.min.js")) > 5000
          and os.path.getsize(os.path.join(ROOT, "assets/vendor/ScrollTrigger.min.js")) > 10000)
    check("shell delegates ambience to the shared engine (with fallback)",
          "OWCSMotion" in read("assets/js/public/shell.js"))

    print("page-script behaviors (string-level):")
    shell = read("assets/js/public/shell.js")
    check("shell renders the demo ribbon from meta.demo",
          "demo-ribbon" in shell and "meta.demo" in shell)
    check("shell links public + control-room surfaces",
          "tournaments.html" in shell and "runs.html" in shell
          and "index.html" in shell)
    check("shell respects prefers-reduced-motion",
          "prefers-reduced-motion" in shell)
    pt = read("assets/js/public/page-tournament.js")
    check("tournament page has a mobile bracket side switcher",
          "data-side-btn" in pt and "mob-show" in pt)
    check("tournament page explains the capture ladder in plain language",
          "LADDER" in pt and "human-reviewed" in pt)
    pm = read("assets/js/public/page-match.js")
    check("match page renders correction history with the kept cv row",
          "never deleted" in pm and "overridesId" in pm)
    check("match page explains why comps are absent per capture state",
          "needs-source" in pm and "unreviewed" in pm.lower())
    check("match page review tab only shows commands, never executes",
          "run_owcs_auto.py" in pm and "never executes" in pm)
    ps = read("assets/js/public/page-stats.js")
    check("stats page persists region in the URL",
          "region" in ps and "setQs" in ps)
    check("stats table sortable with aria-sort",
          "aria-sort" in ps and "data-sort" in ps)
    check("stats evidence links open the match evidence tab",
          "tab=evidence" in ps)
    check("stats hero rows drill down to a per-team breakdown",
          "is-drillable" in ps and "drillPanel" in ps and "heroDetail" in ps)
    check("stats meta cards are clickable buttons that open a hero",
          'button type="button" class="meta-card' in ps and "data-hero" in ps)
    check("stats drill-down keeps the credibility rule (via heroDetail)",
          "S.heroDetail" in read("assets/js/public/stats.js")
          and "computeHeroStats" in read("assets/js/public/stats.js"))

    print("real hero portraits (from broadcast crops):")
    core = read("assets/js/public/core.js")
    check("core hero tile renders portraitUrl when present",
          "portraitUrl" in core)
    manifest_p = os.path.join(ROOT, "assets", "img", "heroes",
                              "manifest.json")
    check("portrait manifest exists", os.path.exists(manifest_p))
    if os.path.exists(manifest_p):
        with open(manifest_p, encoding="utf-8") as f:
            man = json.load(f)
        check("every portrait traces to a REAL per-source broadcast crop "
              "(never the synthetic starter set)",
              bool(man.get("heroes")) and all(
                  "/" in m["file"] and m["file"].startswith("templates/")
                  and m["file"].count("/") >= 2   # templates/<source>/<file>
                  for m in man["heroes"].values()))
        check("each manifest hero has its generated png on disk",
              all(os.path.exists(os.path.join(ROOT, "assets", "img",
                  "heroes", f"{h}.png")) for h in man["heroes"]))

    print("clickable teams:")
    check("core team plate can render a real anchor to the team page",
          "team-plate--link" in core and "team.html?id=" in core)
    check("team page built on the public shell + reads verified stats only",
          "OWCS_STATS" in read("assets/js/public/page-team.js")
          and "computeHeroStats" in read("assets/js/public/page-team.js"))
    check("match/tournament pages link team plates (opt.link) "
          "only outside nested anchors",
          "link: true" in read("assets/js/public/page-match.js")
          and "link: true" in read("assets/js/public/page-tournament.js"))

    print("accessibility + state markup:")
    css = read("assets/css/public.css")
    check("css: reduced-motion kill switch", "prefers-reduced-motion" in css)
    check("css: focus-visible treatment", "focus-visible" in css)
    check("css: 44px touch minimum on nav/tabs",
          "min-height: 44px" in css)
    check("css: empty/skeleton/stale states styled",
          all(s in css for s in [".empty", ".skel", ".stale-note"]))
    check("css: status chips carry glyphs, not just color",
          'content: "✓"' in css and 'content: "×"' in css)
    check("core.js: tabs use ARIA roles + arrow keys",
          'role="tab"' in core and "ArrowRight" in core and "aria-selected" in core)
    check("core.js: hero tiles carry text alternatives",
          "visually-hidden" in core)
    check("core.js: broken team logos fall back to monograms",
          "imgFallback" in core or "img-fallback" in core)
    check("core.js: stale detection helper present", "isStale" in core)

    print("old control-room surfaces untouched:")
    for p in ["index.html", "run.html", "runs.html", "sources.html",
              "admin.html", "team-prep.html", "prep.html", "fact-admin.html"]:
        h = read(p)
        check(f"{p}: still on the control-room shell",
              "assets/css/style.css" in h and "assets/js/ui.js" in h)
    check("public Teams directory links to the team detail page + Teams "
          "is in the public nav",
          "team.html?id=" in read("assets/js/public/page-teams.js")
          and '{ href: "teams.html", label: "Teams" }' in read("assets/js/public/shell.js"))

    print()
    if FAILS:
        print(f"FAILED: {FAILS} check(s)")
        sys.exit(1)
    print("all public-site checks passed")


if __name__ == "__main__":
    main()
