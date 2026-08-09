"""Meta Hub checks — the candy-brutalist design pass over the app shell.

Offline, no browser. Companion to test_site.py, which owns the
information architecture and the credibility rules; this one owns the
design layer that was added on top of them.

What this suite is for, in one sentence: the pass added a design layer,
a secondary rail, a ticker, a guide system and two new pages, and every
one of those is a new way for the product to quietly start lying. So the
checks below are weighted towards the honesty and accessibility rules
rather than towards the aesthetics:

  * the geometry tokens really are re-cut at the token level, so the
    1,000 lines of component CSS underneath inherit it untouched;
  * no status is communicated by colour alone — every trust state and
    every game state carries a glyph or a word;
  * the ticker is built from the export and refuses to render rather
    than scrolling placeholders;
  * the guide layer never claims to execute anything, every command it
    prints names a script that exists, and every blocked state has a
    next step;
  * the safety gates (promote_detections.py) and the demo/production
    split are untouched by UI work;
  * optional platform APIs (matchMedia, fetch) can never take a module
    down — a regression guard for a bug this pass found and fixed;
  * every internal link and local asset reference resolves.

Run: py pipeline\\test_meta_hub.py
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILS = 0

#: Pages that build the shared app shell (header + rail + ticker + footer).
SHELL_PAGES = [
    "index.html", "submit.html", "review.html", "games.html", "game.html",
    "stats.html", "teams.html", "team.html", "hero.html", "tools.html",
    "how-it-works.html", "guide.html", "styleguide.html",
]

#: Pages added by this pass.
NEW_PAGES = ["guide.html", "styleguide.html"]


def check(name: str, ok: bool) -> None:
    global FAILS
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILS += 1


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def exists(rel: str) -> bool:
    return os.path.exists(os.path.join(ROOT, rel))


def main() -> None:
    css = read("assets/css/owcs.css")
    shell = read("assets/js/app/shell.js")
    core = read("assets/js/app/core.js")
    motion = read("assets/js/app/motion.js")

    # ------------------------------------------------------------------
    print("design system — the geometry is re-cut at the token level:")
    check("the brutal layer exists and is last in the cascade",
          "BRUTAL LAYER" in css
          and css.index("BRUTAL LAYER") > css.index("1. Tokens"))
    check("radii are zeroed as tokens, so every existing border-radius "
          "declaration inherits it untouched",
          "--r-1: 0px" in css and "--r-4: 0px" in css and "--r-pill: 0px" in css)
    check("shadows are hard offsets with no blur radius",
          "--sh-1: 3px 3px 0 0" in css
          and "--sh-2: 6px 6px 0 0" in css
          and "--sh-3: 10px 10px 0 0" in css)
    check("there is no glass left in the system", "--glass: none" in css)
    check("structural border weights are tokenised",
          "--bw-1:" in css and "--bw-2:" in css and "--bw-3:" in css and "--bd:" in css)
    check("borders are opaque, not 10%-alpha hairlines",
          re.search(r"--line:\s*#", css) is not None
          and re.search(r"--line-2:\s*#", css) is not None)
    check("candy accents and the ink/bone pair are defined",
          all(f"--{c}:" in css for c in ["lime", "magenta", "bone", "ink"]))
    check("the signal colours kept their meanings",
          all(f"--{c}:" in css for c in
              ["gold", "amber", "cyan", "emerald", "violet", "red", "grey"]))

    print("design system — the components this pass added:")
    for comp in [".slab", ".ticker", ".subnav", ".guide", ".cmdline", ".gate",
                 ".trust", ".spine", ".numeral", ".role-key", ".hl"]:
        check(f"component {comp} is defined", comp + " " in css or comp + "{" in css
              or comp + "," in css or comp + ":" in css)

    # ------------------------------------------------------------------
    print("accessibility — status is never colour alone:")
    trust_states = set(re.findall(r'\.trust\[data-trust="([a-z\-]+)"\]\s*\{', css))
    glyphed = set(re.findall(
        r'\.trust\[data-trust="([a-z\-]+)"\]::before\s*\{\s*content:', css))
    missing = sorted(trust_states - glyphed)
    check(f"every trust state carries a glyph ({len(glyphed)} states, "
          f"missing: {missing or 'none'})", not missing)
    check("the five game states still carry a word and a dot, not a colour",
          "STATE_WORDS" in core
          and all(f'{s}: {{ label:' in core for s in
                  ["published", "review", "working", "queued", "blocked"]))
    check("reduced motion stops the ticker",
          "prefers-reduced-motion" in css
          and ".ticker__track { animation: none !important" in css)
    check("under reduced motion the ticker KEEPS its text (wraps, never hides)",
          "white-space: normal" in css and "flex-wrap: wrap" in css)
    check("hover pressure has keyboard parity (:focus-visible / :focus-within)",
          ":focus-within" in css and ":focus-visible" in css)
    check("a print stylesheet exists so a loud page still prints",
          "@media print" in css)

    # ------------------------------------------------------------------
    print("shell — the secondary rail:")
    check("primary nav is still the five things a person does here",
          all(f'label: "{n}"' in shell for n in
              ["Dashboard", "Games", "Submit", "Review", "Stats"]))
    check("a secondary rail carries the data layers on every page",
          "RAIL" in shell and "subnav" in shell)
    check("the rail reaches guides, tools and how-it-works",
          all(f'href: "{p}"' in shell for p in
              ["guide.html", "tools.html", "how-it-works.html"]))
    check("guides and the design system are in the footer too",
          'href="guide.html"' in shell and 'href="styleguide.html"' in shell)

    print("shell — the ticker is built from the export, never invented:")
    check("ticker reads meta/patches/published comps out of the dataset",
          "buildTicker" in shell and "P.pub" in shell
          and "publishedComps" in shell)
    check("ticker refuses to render rather than scrolling placeholders",
          "if (items.length < 3) return null" in shell)
    check("ticker cannot break a page if the tally throws",
          "catch (e)" in shell and "may never break a page" in shell)
    check("the duplicated marquee track is hidden from assistive tech",
          'aria-hidden="true"' in shell and "reads each fact once" in shell)
    check("demo data still raises a visible bar, not just a pill",
          "meta.demo" in shell and "slab--magenta" in shell)

    print("shell — the guide layer:")
    check("P.guide and P.cmd are exposed to every page",
          "P.guide = " in core and "P.cmd = " in core)
    check("the guide layer states in code that nothing is executed",
          "NEVER EXECUTES" in core or "never executes" in core.lower())
    check("copy reuses the product's own helper (which has a fallback)",
          "P.copy(" in core and "execCommand" in core)

    # ------------------------------------------------------------------
    print("optional platform APIs can never take a module down:")
    # Found during headless QA: shell.js called window.matchMedia unguarded
    # at module top level. In any renderer without it — jsdom, some
    # embedded webviews, several headless screenshot tools — that threw
    # before a single line of the module ran, so the header, nav, search
    # AND footer silently never built and every page rendered as bare
    # unstyled sections. This is the regression guard.
    check("core.js exposes guarded media-query helpers",
          "P.media = " in core and "P.onMedia = " in core)
    offenders = []
    for rel in ["assets/js/app/shell.js", "assets/js/app/motion.js",
                "assets/js/app/core.js"]:
        src = read(rel)
        for m in re.finditer(r"window\.matchMedia\(", src):
            line = src[:m.start()].count("\n") + 1
            # A guard on the same line OR either of the two lines above
            # (`if (!window.matchMedia) return;` then the call) counts.
            lines = src.splitlines()
            ctx = "\n".join(lines[max(0, line - 3):line])
            if "window.matchMedia &&" in ctx or "!window.matchMedia" in ctx:
                continue
            offenders.append(f"{rel}:{line}")
    check(f"no unguarded window.matchMedia call "
          f"({', '.join(offenders) or 'none'})", not offenders)
    check("shell.js reads reduced-motion through the guard",
          'P.media("(prefers-reduced-motion: reduce)")' in shell)
    check("motion.js carries its own guard (it defers, so it can run "
          "before core.js defines P.media)",
          "const mq = (q) =>" in motion and "catch (e) { return false; }" in motion)

    # ------------------------------------------------------------------
    print("new pages are wired exactly like the existing ones:")
    for p in NEW_PAGES:
        check(f"{p}: exists", exists(p))
        if not exists(p):
            continue
        h = read(p)
        check(f"{p}: owcs.css + core + shell wired",
              "assets/css/owcs.css" in h
              and "assets/js/app/core.js" in h
              and "assets/js/app/shell.js" in h)
        check(f"{p}: skip link, lang attr, one h1",
              "skip-link" in h and '<html lang="en">' in h and h.count("<h1") == 1)
        check(f"{p}: no framework, no CDN, vendored motion only",
              "react" not in h.lower()
              and "assets/vendor/lenis.min.js" in h
              and "assets/js/app/motion.js" in h
              and "cdn." not in h.replace("fonts.gstatic", ""))
    for page, script in [("guide.html", "page-guide.js"),
                         ("styleguide.html", "page-styleguide.js")]:
        check(f"{script} exists", exists(f"assets/js/app/{script}"))
        check(f"{page} loads {script}", f"assets/js/app/{script}" in read(page))

    print("every shell page really builds the shell:")
    for p in SHELL_PAGES:
        if not exists(p):
            check(f"{p}: exists", False)
            continue
        h = read(p)
        check(f"{p}: core before shell, data before core",
              h.index("assets/js/app/core.js") < h.index("assets/js/app/shell.js")
              and h.index("public_data.v1.js") < h.index("assets/js/app/core.js"))
        check(f"{p}: skip link points at a real #main",
              'href="#main"' in h and 'id="main"' in h)

    # ------------------------------------------------------------------
    print("guides: real commands, nothing executed, every block has a way out:")
    guide = read("assets/js/app/page-guide.js")
    check("the guide page states it executes nothing",
          "NOTHING HERE IS EXECUTED BY THE PAGE" in guide)
    # A guide that tells you to run a script that does not exist is worse
    # than no guide: it burns the reader's trust on the first command.
    cmds = [c.rstrip(";") for c in re.findall(r"python3 ([^ \"';]+)", guide)]
    bad = sorted({c for c in cmds if c.endswith(".py") and not exists(c)})
    check(f"every command names a script that exists "
          f"({len(set(cmds))} distinct; broken: {bad or 'none'})", not bad)
    check("the five human gates are all named",
          all(g in guide for g in ["Source approval", "Layout approval",
                                   "Segment approval", "Detection review",
                                   "Promote gate"]))
    check("every state a game can stop in has a next step",
          all(f'["{s}"' in guide.replace("'", '"') or f'"{s}",' in guide
              for s in ["queued", "working", "review", "blocked", "published"]))
    check("guides say UNKNOWN is a valid answer", "UNKNOWN" in guide)
    check("the promote guide shows the dry run BEFORE the write",
          guide.index("Promote — dry run first")
          < guide.index("Promote — write, with the pairing"))
    check("the promote guide says a zero-row promotion is the gate working",
          "gate doing its job" in guide)
    check("the guide page tells you whether a tracker is actually reachable",
          "mountStatus" in guide)

    print("the styleguide is rendered from the product's own helpers:")
    sg = read("assets/js/app/page-styleguide.js")
    check("it calls the real component helpers, not copies",
          all(h in sg for h in ["P.stateChip", "P.teamPlate", "P.compStrip",
                                "P.confMeter", "P.scorePlate", "P.empty",
                                "P.note", "P.guide", "P.cmd"]))
    check("it says so, so nobody 'improves' it into a static mock",
          "cannot drift" in sg)
    check("it uses real entities and admits when the export has none",
          "No team in this export" in sg and "No hero in this export" in sg)

    # ------------------------------------------------------------------
    print("safety gates and the data split are untouched by UI work:")
    promote = read("pipeline/promote_detections.py")
    check("promote_detections.py still classifies high vs needs-review",
          "needs-review" in promote and "classify" in promote)
    check("promote_detections.py still refuses to auto-promote weak reads",
          "NEVER become comps automatically" in promote)
    check("export_data.py still never writes a demo fixture",
          "public_fixture" not in read("pipeline/export_data.py"))
    check("core.js still hard-codes the approved review list",
          "P.APPROVED" in core and '"reviewed"' in core and '"auto-high"' in core)
    check("published comps still go through the client-side mirror of the gate",
          "publishedComps" in core
          and 'c.source !== "cv" && c.source !== "manual"' in core)

    # ------------------------------------------------------------------
    print("every in-repo page link resolves:")
    pages = [f for f in sorted(os.listdir(ROOT)) if f.endswith(".html")]
    broken = []
    for p in pages:
        for href in re.findall(r'href="([^"#?:]+\.html)[^"]*"', read(p)):
            if "${" in href or "{{" in href or "+" in href:
                continue
            if not exists(href):
                broken.append(f"{p} -> {href}")
    check(f"no dead page link in {len(pages)} pages "
          f"({', '.join(broken) or 'none'})", not broken)

    print("page links built inside JS resolve too:")
    js_broken = []
    js_dir = os.path.join(ROOT, "assets", "js", "app")
    js_files = ["assets/js/app/" + f for f in sorted(os.listdir(js_dir))
                if f.endswith(".js")]
    for rel in js_files:
        src = read(rel)
        for href in re.findall(r'href="([a-z0-9\-]+\.html)', src):
            if not exists(href):
                js_broken.append(f"{rel} -> {href}")
        for href in re.findall(r'href: "([a-z0-9\-]+\.html)', src):
            if not exists(href):
                js_broken.append(f"{rel} -> {href}")
    check(f"no dead page link in {len(js_files)} scripts "
          f"({', '.join(sorted(set(js_broken))) or 'none'})", not js_broken)

    print("local asset references resolve:")
    asset_broken = []
    for p in pages:
        for ref in re.findall(r'(?:src|href)="(assets/[^"?#]+)"', read(p)):
            if not exists(ref):
                asset_broken.append(f"{p} -> {ref}")
    check(f"every assets/ reference exists "
          f"({', '.join(sorted(set(asset_broken))) or 'none'})", not asset_broken)

    print()
    if FAILS:
        print(f"FAILED: {FAILS} check(s)")
        sys.exit(1)
    print("all Meta Hub checks passed")


if __name__ == "__main__":
    main()
