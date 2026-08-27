#!/usr/bin/env python3
"""
test_site.py — the product surface, offline, no browser.

Replaces test_public_site.py and test_static_pages.py, which between them
asserted the shape of twenty-nine pages across three competing front-end
stacks. There is one stack now, one navigation, and one workflow, so
there is one suite.

What it guards, in order of how much it would hurt to lose:

  1. THE CREDIBILITY RULES. Everything the DETECTOR accepted renders,
     carrying the tier it earned, and 'rejected' never does — the page's
     gate and the exporter's must BE the same set; comps come from cv or
     manual and never from FACEIT; a manual correction never deletes the
     detection it corrects; every published claim resolves to an evidence
     file that exists on disk.
  2. THE INFORMATION ARCHITECTURE. Five nav entries, one page per job, no
     dead links, no orphan pages, no page reintroducing a deleted one.
  3. NO DEMO DATA CAN REACH A PAGE. The fixture is a test asset now.
  4. THE BASICS THAT ROT SILENTLY. Skip links, lang, focus styles,
     reduced motion, empty states, self-hosted assets only.

Run: python3 pipeline/test_site.py
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILS = 0

#: The whole product. Every page a person can reach, and what it is for.
PRODUCT_PAGES = {
    "index.html": "Dashboard",
    "games.html": "Games",
    "submit.html": "Submit",
    "review.html": "Review",
    "stats.html": "Stats",
    "game.html": "One game",
    "team.html": "One team",
    "teams.html": "Team directory",
    "hero.html": "One hero",
    "how-it-works.html": "Explainer",
    "tools.html": "Operator tools",
    # Added by the candy-brutalist pass. Both build the standard shell, so
    # they are product pages, not standalone tools. `guide.html` is the
    # walkthrough surface — every technical step, with the exact command;
    # `styleguide.html` is the design system rendered from the product's
    # own component helpers, so it cannot drift from what ships.
    "guide.html": "Guides",
    "styleguide.html": "Design system",
}
#: Reachable, but not part of the product shell: the calibration wizard
#: (its own self-contained tool), the packaged desktop app's two screens,
#: and the legacy-URL redirect map.
STANDALONE_PAGES = {"calibrate.html", "control-room.html", "setup.html", "404.html"}

#: Pages that were deleted in the redesign. Reintroducing one by accident
#: is how an information architecture rots back into a page-per-dataset.
DELETED_PAGES = {
    "matches.html", "match.html", "runs.html", "sources.html", "portal.html",
    "run.html", "admin.html", "fact-admin.html", "calibration.html",
    "heroes.html", "maps.html", "comps.html", "swaps.html", "calendar.html",
    "tournaments.html", "tournament.html", "prep.html", "team-prep.html",
    "team-coverage.html", "beta-ops.html", "intake.html",
}


def check(name: str, ok: bool) -> None:
    global FAILS
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILS += 1


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def load_public() -> dict:
    src = read("assets/data/public_data.v1.js")
    return json.loads(src[src.index("{"):src.rindex("}") + 1])


# --------------------------------------------------------------------------
def test_pages_exist() -> None:
    print("the product is exactly these pages:")
    on_disk = {f for f in os.listdir(ROOT) if f.endswith(".html")}
    expected = set(PRODUCT_PAGES) | STANDALONE_PAGES
    check(f"no page missing ({sorted(expected - on_disk) or 'none'})",
          not (expected - on_disk))
    check(f"no unexpected page ({sorted(on_disk - expected) or 'none'})",
          not (on_disk - expected))
    back = on_disk & DELETED_PAGES
    check(f"no deleted page has come back ({sorted(back) or 'none'})", not back)


def test_navigation() -> None:
    print("navigation: five entries, in workflow order:")
    shell = read("assets/js/app/shell.js")
    nav = re.search(r"const NAV = \[(.*?)\n  \];", shell, re.S)
    check("the nav is declared in one place", nav is not None)
    if not nav:
        return
    labels = re.findall(r'label: "([^"]+)"', nav.group(1))
    check(f"exactly five entries ({labels})", len(labels) == 5)
    check("in workflow order: Dashboard, Games, Submit, Review, Stats",
          labels == ["Dashboard", "Games", "Submit", "Review", "Stats"])
    for gone in ("Tournaments", "Heroes", "Maps", "Calendar", "Portal",
                 "Vision Lab", "Admin", "Sources"):
        check(f"“{gone}” is not a top-level destination",
              f'label: "{gone}"' not in shell)
    check("Review shows a count when work is waiting",
          'pip: "review"' in shell and "counts.review" in shell)
    check("secondary surfaces stay reachable from the shared footer",
          all(p in shell for p in ("teams.html", "tools.html",
                                   "calibrate.html", "how-it-works.html")))


def test_shared_shell() -> None:
    print("every product page loads one shell and one stylesheet:")
    for page in PRODUCT_PAGES:
        h = read(page)
        check(f"{page}: owcs.css + core + shell",
              "assets/css/owcs.css" in h
              and "assets/js/app/core.js" in h
              and "assets/js/app/shell.js" in h)
        check(f"{page}: skip link, lang, viewport",
              'class="skip-link"' in h and '<html lang="en">' in h
              and 'name="viewport"' in h)
        check(f"{page}: a title that names the product",
              re.search(r"<title>[^<]*OWCS Comp Tracker[^<]*</title>", h) is not None)
        check(f"{page}: no superseded stylesheet",
              "public.css" not in h and "portal-guide.css" not in h
              and "assets/css/style.css" not in h)
    check("the design system exists and is substantial",
          os.path.getsize(os.path.join(ROOT, "assets/css/owcs.css")) > 20000)


def test_no_demo_data_reaches_a_page() -> None:
    print("the published site shows real data or an honest nothing:")
    leaked = [p for p in sorted(os.listdir(ROOT)) if p.endswith(".html")
              and "public_fixture" in read(p)]
    check(f"no page loads the demo fixture ({leaked or 'none'})", not leaked)
    check("the fixture is not even under assets/ any more",
          not os.path.exists(os.path.join(ROOT, "assets/data/public_fixture.v1.js")))
    check("it lives with the tests that use it",
          os.path.exists(os.path.join(ROOT, "pipeline/fixtures/public_fixture.v1.js")))
    check("the production export declares itself production",
          load_public()["meta"]["demo"] is False)
    check("exporter still never writes the fixture",
          "public_fixture" not in read("pipeline/export_data.py"))
    for page, marker in (("index.html", "recent"), ("games.html", "games"),
                         ("stats.html", "coverage")):
        check(f"{page} has an empty state to fall back to",
              "P.empty" in read("assets/js/app/page-" +
                                {"index.html": "dashboard", "games.html": "games",
                                 "stats.html": "stats"}[page] + ".js"))
        del marker


def test_credibility_rules() -> None:
    print("the credibility rules, in the code that renders:")
    core = read("assets/js/app/core.js")
    # Stronger than pinning a literal: the page's gate and the exporter's
    # must BE the same set. A literal only catches an edit to this file;
    # this catches the two drifting apart in either direction, which is the
    # failure that would actually publish something a page then hides (or
    # worse, render something the export never published).
    import ast
    import re as _re

    def _py_states(name: str) -> list:
        src = read("pipeline/export_data.py")
        m = _re.search(rf"^{name} = (\(.*?\))", src, _re.M | _re.S)
        return sorted(ast.literal_eval(m.group(1))) if m else []

    def _js_states(name: str) -> list:
        m = _re.search(rf"P\.{name} = (\[[^\]]*\]);", core)
        return sorted(json.loads(m.group(1))) if m else []

    check("the page's publication gate is the exporter's, exactly",
          _js_states("PUBLISHED") == _py_states("PUBLISHED_REVIEW_STATES")
          != [])
    check("the audited subset agrees too, so a tier label cannot drift",
          _js_states("AUDITED") == _py_states("AUDITED_REVIEW_STATES") != [])
    check("a rejected reading is never publishable, on either side",
          "rejected" not in _js_states("PUBLISHED")
          and "rejected" not in _py_states("PUBLISHED_REVIEW_STATES"))
    check("an unknown review status reads as the weakest tier",
          '|| "provisional"' in core)
    check("a non-cv, non-manual source can never render as a comp",
          'c.source !== "cv" && c.source !== "manual"' in core)
    check("an overridden detection is dropped in favour of the correction",
          "overridesId" in core and "overridden.has" in core)
    check("published pages compute from publishedComps only",
          "publishedComps" in read("assets/js/app/page-stats.js")
          and "publishedComps" in read("assets/js/app/page-game.js"))

    review = read("assets/js/app/page-review.js")
    check("the review workspace states the never-overwrite rule",
          "never overwrites raw detection evidence" in review)
    check("a decision records BOTH the detection and the correction",
          "detected:" in review and "corrected:" in review)
    check("a decision records who and when",
          "reviewer:" in review and "decidedAt:" in review)
    check("hero corrections go through corrections.json, not a live write",
          "corrections.json" in review and "apply_corrections.py" in review)
    check("live approvals set manual_override via the desktop API",
          "reviewDecide" in review
          and "manual_override" in read("assets/js/app/page-review.js")
          .replace("manual override", "manual_override"))
    check("the clean-approval threshold matches the pipeline's own floor",
          "CLEAN_FLOOR = 0.90" in review)
    check("rejected swap candidates stay visible",
          "rejectedSwaps" in read("assets/js/app/page-game.js")
          and "rejectedSwaps" in read("assets/js/app/page-stats.js"))


def test_data_integrity() -> None:
    print("the production export holds together:")
    d = load_public()
    hero_ids = {h["id"] for h in d["heroes"]}
    team_ids = {t["id"] for t in d["teams"]}
    map_ids = {m["id"] for m in d["mapsCatalog"]}
    run_ids = {r["id"] for r in d["captureRuns"]}
    tour_ids = {t["id"] for t in d["tournaments"]}

    check("every match belongs to a real tournament",
          all(m["tournamentId"] in tour_ids for m in d["matches"]))
    check("match teams resolve (or are explicit nulls)",
          all(t is None or t in team_ids
              for m in d["matches"] for t in (m["teamA"], m["teamB"])))
    check("played maps exist in the catalog",
          all(mp["map"] in map_ids for m in d["matches"] for mp in m["maps"]))

    comps = d["compSnapshots"]
    check("every comp source is cv or manual — never faceit",
          all(c["source"] in ("cv", "manual") for c in comps))
    check("every published comp is reviewed or auto-high",
          all(c["reviewStatus"] in ("reviewed", "auto-high") for c in comps))
    check("every comp has exactly 5 heroes",
          all(len(c["heroes"]) == 5 for c in comps))
    check("every comp hero exists in the catalog",
          all(h in hero_ids for c in comps for h in c["heroes"]))
    check("every comp carries an evidence run that resolves",
          all(c.get("evidenceRunId") in run_ids for c in comps))

    print("every evidence path is a file that exists:")
    missing = []
    for r in d["captureRuns"]:
        for p in [r.get("reportPath")] + [f.get("file") for f in r.get("frames", [])]:
            if p and not os.path.exists(os.path.join(ROOT, p)):
                missing.append(p)
    for c in comps:
        p = c.get("evidenceFrame")
        if p and not os.path.exists(os.path.join(ROOT, p)):
            missing.append(p)
    for s in d.get("heroStints", []):
        for p in (s.get("evidenceStart"), s.get("evidenceEnd")):
            if p and not os.path.exists(os.path.join(ROOT, p)):
                missing.append(p)
    check(f"all evidence paths resolve ({len(missing)} missing)", not missing)


def test_the_six_steps() -> None:
    print("one progression, defined once:")
    games = read("assets/js/app/games.js")
    steps = re.findall(r'label: "([^"]+)",\n\s+say:', games)
    check(f"the six steps are declared in one place ({steps})",
          steps == ["Source found", "Video captured", "Gameplay detected",
                    "Heroes detected", "Match linked",
                    "Published, open for audit"])
    check("the last step is publication, not a queue waiting on a person",
          "Ready for review" not in games)
    check("the explainer renders the same definitions, so it cannot drift",
          "P.games.STEPS" in read("assets/js/app/page-how.js"))
    check("every step is explained in words a newcomer can read",
          "say:" in games and "computer vision" not in games.lower())

    print("plain language, not pipeline vocabulary:")
    jargon = ["ROI", "template matching", "OCR", "bounding box", "argmax",
              "quarantine", "idempotent"]
    for page in PRODUCT_PAGES:
        body = read(page)
        hits = [j for j in jargon if j.lower() in body.lower()]
        check(f"{page}: no unexplained jargon ({hits or 'none'})", not hits)


def test_accessibility_and_motion() -> None:
    print("accessibility and motion:")
    css = read("assets/css/owcs.css")
    check("reduced-motion kill switch", "prefers-reduced-motion" in css)
    check("visible focus ring on every interactive element",
          ":focus-visible" in css)
    check("empty, skeleton and spinner states are styled",
          all(s in css for s in (".empty", ".skeleton", ".spinner")))
    check("state is never carried by colour alone (each chip has a label)",
          'data-state="published"' in css and ".chip .dot" in css)
    check("print stylesheet reveals anything a reveal is holding",
          "@media print" in css and ".rv { opacity: 1 !important" in css)

    core = read("assets/js/app/core.js")
    check("tabs use ARIA roles and arrow keys",
          'role="tab"' in core and "ArrowRight" in core and "aria-selected" in core)
    check("hero tiles carry a text alternative",
          "visually-hidden" in core)
    check("a broken image becomes a monogram, never a hole",
          "dataset.fallback" in core)

    motion = read("assets/js/app/motion.js")
    check("motion respects reduced-motion AND Save-Data",
          "prefers-reduced-motion" in motion and "saveData" in motion)
    check("every motion layer is crash-isolated", "safely(" in motion)
    check("inner scroll regions keep native scrolling",
          "data-lenis-prevent" in motion)
    check("keyboard focus is scrolled into view under smooth scroll",
          "focusin" in motion)
    check("the heavy ambience layer is gone (three.js/Vanta removed)",
          "VANTA" not in motion and "three.min.js" not in motion
          and not os.path.exists(os.path.join(ROOT, "assets/vendor/three.min.js")))

    shell = read("assets/js/app/shell.js")
    check("reveals can never permanently withhold content",
          "revealAll" in shell and "beforeprint" in shell and "watchdog" in shell)


def test_review_workspace() -> None:
    print("the review workspace is built for reviewing many games:")
    r = read("assets/js/app/page-review.js")
    check("hero portraits, not hero names, carry the prediction",
          "heroTile" in r and "compStrip" in r)
    check("confidence is shown as a meter", "confMeter" in r)
    check("the evidence crop sits beside the prediction",
          "P.evidence.thumb" in r)
    check("a whole clean section can be approved at once",
          "approveClean" in r and "approveMany" in r)
    check("map boundaries can be confirmed", "boundaryBlock" in r
          and "decideBoundary" in r)
    check("swaps can be confirmed or rejected", "decideSwap" in r)
    check("uncertain frames can be flagged", '"flagged"' in r)
    check("the CV prediction and the correction are shown side by side",
          "diff__was" in r)
    check("the keyboard drives it end to end",
          all(k in r for k in ('e.key === "j"', 'e.key === "a"',
                               'e.key === "c"', 'e.key === "f"')))
    check("work survives a reload", "localStorage" in r and "persist()" in r)
    check("the audit log can be exported whole", "review-log.v1" in r)
    css = read("assets/css/owcs.css")
    check("the workspace has its own layout and picker styles",
          ".rw__queue" in css and ".slot" in css and ".picker__grid" in css)


def test_submit_flow() -> None:
    print("the submit flow:")
    page = read("submit.html")
    js = read("assets/js/app/page-submit.js")
    check("one required field", page.count('type="url"') >= 1
          and 'id="link"' in page)
    check("advanced options are collapsed by default",
          '<details class="diag" id="advanced">' in page
          and "<details open" not in page)
    check("exactly one final action",
          page.count('type="submit"') == 1 and "Start processing" in page)
    check("the link is validated as it is typed, offline",
          "function classify" in js and "accepted" in js)
    check("the local classifier defers to the tracker's own when connected",
          "P.api.classify" in js)
    check("known broadcasts autofill the form from the record",
          "autofillFromLink" in js)
    check("a read-only submission is kept and turned into a command",
          "savePending" in js and "handoffCommand" in js)
    check("FACEIT is described as facts-only, never comps",
          "never supplies hero compositions" in page.lower()
          or "never supply hero compositions" in page.lower()
          or "never supplies hero compositions" in page)


def test_links_resolve() -> None:
    print("every in-repo link resolves:")
    pages = sorted(f for f in os.listdir(ROOT) if f.endswith(".html"))
    broken = []
    for p in pages:
        for href in re.findall(r'href="([^"#?:]+\.html)[^"]*"', read(p)):
            if "${" in href or "{{" in href:
                continue
            if not os.path.exists(os.path.join(ROOT, href)):
                broken.append(f"{p} -> {href}")
    check(f"no dead page link in {len(pages)} pages ({broken or 'none'})", not broken)

    print("every script and stylesheet a page loads exists:")
    missing = []
    for p in pages:
        body = read(p)
        for rel in re.findall(r'(?:src|href)="(assets/[^"?#]+)"', body):
            if not os.path.exists(os.path.join(ROOT, rel)):
                missing.append(f"{p} -> {rel}")
    check(f"no dead asset reference ({missing or 'none'})", not missing)

    # This used to carve out fonts.googleapis.com/fonts.gstatic.com, which
    # meant every page opened with a render-blocking stylesheet on a
    # third-party origin and did not look like itself wherever that CDN is
    # unreachable — while owcs.css's own header promised the product
    # degrades "no JS, no webfont, no colour". The three faces are
    # vendored under assets/fonts now (SIL OFL, see assets/css/fonts.css),
    # so the exception is gone and this is an absolute rule again.
    print("nothing at all is loaded from a third party:")
    offenders = []
    for p in pages:
        for url in re.findall(r'(?:src|href)="(https?://[^"]+)"', read(p)):
            offenders.append(f"{p} -> {url}")
    check(f"no third-party script or stylesheet ({offenders or 'none'})",
          not offenders)

    print("the webfonts a page declares are on disk:")
    css = read("assets/css/fonts.css")
    faces = re.findall(r'url\("\.\./fonts/([^"]+)"', css)
    check(f"fonts.css declares faces ({len(faces)} url(s))", bool(faces))
    absent = [f for f in set(faces)
              if not os.path.exists(os.path.join(ROOT, "assets", "fonts", f))]
    check(f"every declared font file exists ({absent or 'none'})", not absent)
    # Only the pages that ask for those families. The packaged desktop
    # screens (appliance.css) deliberately name families they do not ship
    # and fall back to the host's own UI font, because that build has to
    # stay byte-small and works on a machine with nothing installed.
    wants_fonts = [p for p in pages if "assets/css/owcs.css" in read(p)]
    unwired = [p for p in wants_fonts if "assets/css/fonts.css" not in read(p)]
    check(f"every page on owcs.css loads fonts.css ({unwired or 'none'})", not unwired)


def test_legacy_redirects() -> None:
    print("old bookmarks land somewhere useful:")
    page = read("404.html")
    for gone in sorted(DELETED_PAGES):
        check(f"{gone} has a destination", f'"{gone}"' in page)
    targets = set(re.findall(r'\["([a-z0-9\-]+\.html)[^"]*",', page))
    dead = [t for t in targets if not os.path.exists(os.path.join(ROOT, t))]
    check(f"every redirect target exists ({dead or 'none'})", not dead)
    check("the redirect is announced, not instant",
          "setTimeout" in page and "clearTimeout" in page)


def test_generated_reports_point_at_real_pages() -> None:
    print("generated reports link into the product, not deleted pages:")
    # The three report builders sit on top of the vision stack (capture ->
    # detect -> hero_overlay_detect), every layer of which imports OpenCV at
    # module scope. ci.yml installs requirements.txt and so runs this check
    # in full on every push and PR — which is the only way these builders can
    # ever change. The LEAN scheduled workflows (discovery.yml hourly,
    # match-finder.yml 6-hourly) deliberately install no OpenCV and no ffmpeg
    # because nothing they do decodes video; before this guard, importing
    # them here crashed the hourly discovery run outright and took the whole
    # automation down with it. Announce the skip rather than passing quietly.
    try:
        import cv2  # noqa: F401
    except ModuleNotFoundError:
        # ci.yml sets this. There, OpenCV is installed from requirements.txt
        # and a skip could only mean the dependency quietly went missing —
        # so the full suite refuses the skip instead of shrinking itself.
        if os.environ.get("OWCS_REQUIRE_FULL_SITE_TESTS") == "1":
            check("OpenCV present for the report-builder checks", False)
            return
        print("  SKIP  report-builder checks (OpenCV absent — this run is "
              "the no-video workflow; ci.yml runs them in full)")
        return
    import build_crop_report as bcr
    import build_layout_debug as bld
    import run_owcs_auto as roa
    html = roa.build_report_html({"run": "t", "steps": [], "ok": True}, [])
    check("run report links the processing history", "tools.html#runs" in html)
    check("run report has no link to a deleted page",
          not any(g in html for g in DELETED_PAGES))
    check("layout viewer stays on theme", "#060b15" in bld._LAYOUT_HTML_CSS)
    check("crop report stays on theme", "#060b15" in bcr._CSS)


def main() -> None:
    test_pages_exist()
    test_navigation()
    test_shared_shell()
    test_no_demo_data_reaches_a_page()
    test_credibility_rules()
    test_data_integrity()
    test_the_six_steps()
    test_accessibility_and_motion()
    test_review_workspace()
    test_submit_flow()
    test_links_resolve()
    test_legacy_redirects()
    test_generated_reports_point_at_real_pages()

    print()
    if FAILS:
        print(f"FAILED — {FAILS} check(s)")
        sys.exit(1)
    print("all site checks passed")


if __name__ == "__main__":
    main()
