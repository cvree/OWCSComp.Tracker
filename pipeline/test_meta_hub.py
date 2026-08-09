"""Meta Hub checks — the shared design system and app shell.

Offline, no browser. Companion to test_site.py, which owns the
information architecture and the credibility rules; this one owns the
design layer that sits on top of them.

The design direction is professional esports broadcast tooling: neutral
surfaces, hairline borders, one warm accent, and colour reserved for
meaning. An earlier "candy brutalist" pass was reverted; the checks
below exist partly to stop it, or anything like it, from creeping back
in through a later override section.

What this suite is for, in one sentence: a design layer is a new way for
the product to quietly start lying, so the checks are weighted towards
honesty, restraint and accessibility rather than towards taste:

  * the token block is the single source of truth — no override layer
    is appended after the components to win on cascade order;
  * the geometry is modern and hairline: real radii, 1px borders, and
    no hard offset shadows anywhere;
  * colour is semantic only — no decorative lime/magenta/violet — and
    no status is communicated by colour alone;
  * the chrome above the content is ONE nav row plus one static status
    line: no second navigation row and no scrolling marquee;
  * the dataset status line is built from the export and refuses to
    render rather than printing placeholders;
  * the guide layer never claims to execute anything, every command it
    prints names a script that exists, and every blocked state has a
    next step;
  * the safety gates (promote_detections.py) and the demo/production
    split are untouched by UI work;
  * optional platform APIs (matchMedia, fetch) can never take a module
    down — a regression guard for a bug an earlier pass found and fixed;
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

#: Pages that build the shared app shell (header + dataset line + footer).
SHELL_PAGES = [
    "index.html", "submit.html", "review.html", "games.html", "game.html",
    "stats.html", "teams.html", "team.html", "hero.html", "tools.html",
    "how-it-works.html", "guide.html", "styleguide.html",
]

#: Pages added by the guide/styleguide pass.
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
    print("design system — the tokens are the single source of truth:")
    check("no override layer is appended after the components",
          "BRUTAL LAYER" not in css
          and "OVERRIDES" not in css.upper().replace("NO OVERRIDE", ""))
    check("the token block comes first and nothing re-declares :root later",
          css.count(":root {") == 1
          and css.index("1. Tokens") < css.index("2. Base"))
    check("radii are real, modern and scaled",
          all(t in css for t in ["--r-1: 4px", "--r-2: 6px", "--r-3: 8px",
                                 "--r-4: 12px", "--r-pill: 999px"]))
    check("borders are hairlines — one weight",
          "--bw-1: 1px" in css and "--bd: 1px solid var(--line)" in css)
    check("no hard offset shadow survives anywhere in the system",
          not re.search(r"box-shadow:\s*-?\d+px\s+-?\d+px\s+0\b", css)
          and "--sh-1: 0 1px 2px" in css)
    check("elevation is restrained and blurred, not stacked blocks",
          "0 8px 30px" in css and "rgba(0, 0, 0, .28)" in css)
    check("surfaces are the neutral dark palette, not a black-out",
          all(f"{c};" in css for c in
              ["--bg: #090a0d", "--s1: #111319", "--s2: #171a21",
               "--s3: #1c2028", "--line: #272b34"]))
    check("the product accent is the warm OWCS gold",
          "--gold: #f3a72f" in css)
    check("the signal colours kept their meanings",
          all(f"--{c}:" in css for c in
              ["gold", "amber", "cyan", "emerald", "indigo", "red", "grey"]))
    check("decorative colour is gone — lime and magenta are not tokens",
          "--lime:" not in css and "--magenta:" not in css)
    check("status fills are washes under 15%, so text keeps its contrast",
          "--wash-gold:" in css and "--wash-red:" in css
          and not re.search(r"--wash-[a-z]+:\s*rgba\([^)]*,\s*\.[2-9]", css))

    print("design system — typography is broadcast, not poster:")
    check("headings are not uppercased by the system",
          "text-transform: none;" in css
          and not re.search(r"h1,\s*h2\s*\{[^}]*text-transform:\s*uppercase", css))
    check("the h1 is editorial scale with tight negative tracking",
          "clamp(2.15rem, 4.2vw, 3.4rem)" in css and "letter-spacing: -.025em" in css)
    check("all three families are still in play, each with a job",
          all(f in css for f in ["--f-display:", "--f-body:", "--f-mono:"])
          and "Saira Condensed" in css and "Archivo" in css and "IBM Plex Mono" in css)
    check("buttons are software controls, not brutalist slabs",
          re.search(r"\.btn\s*\{[^}]*text-transform:\s*none", css) is not None
          and re.search(r"\.btn\s*\{[^}]*box-shadow:\s*none", css) is not None)
    check("the highlight is accent COLOUR, never a block behind the text",
          re.search(r"\.hl\s*\{\s*color:", css) is not None
          and "box-decoration-break" not in css)

    print("design system — the components the product needs:")
    for comp in [".slab", ".dataline", ".more", ".guide", ".cmdline", ".gate",
                 ".trust", ".spine", ".numeral", ".role-key", ".hl",
                 ".stat__cta", ".game-card__fixture", ".game-card__comps"]:
        check(f"component {comp} is defined", comp + " " in css or comp + "{" in css
              or comp + "," in css or comp + ":" in css)
    check("the scrolling ticker component is gone from the stylesheet",
          ".ticker" not in css)
    check("the permanent second navigation row is gone from the stylesheet",
          ".subnav" not in css)

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
    check("reduced motion drops every transform in the system",
          "prefers-reduced-motion" in css
          and "transform: none !important" in css
          and "transition-duration: 0.001ms !important" in css)
    check("there is no perpetual animation left to stop",
          "infinite" not in css.replace("pulse-ring 2.4s var(--ease) infinite", "")
          .replace("sk 1.4s ease-in-out infinite", "")
          .replace("spin .8s linear infinite", ""))
    check("hover affordances have keyboard parity (:focus-visible / :focus-within)",
          ":focus-within" in css and ":focus-visible" in css)
    check("a stat card only takes an alert edge when its count is non-zero",
          '.stat[data-alert="1"]' in css
          and 'data-alert="1"' in read("assets/js/app/page-dashboard.js"))
    check("a print stylesheet exists so a loud page still prints",
          "@media print" in css)

    # ------------------------------------------------------------------
    print("shell — one navigation row, everything else behind it:")
    check("primary nav is still the five things a person does here",
          all(f'label: "{n}"' in shell for n in
              ["Dashboard", "Games", "Submit", "Review", "Stats"]))
    check("secondary destinations live in an overflow menu, not a second row",
          "RAIL" in shell and "more__panel" in shell and "subnav" not in shell)
    check("the overflow menu closes on Escape and on an outside click",
          "more.open = false" in shell)
    check("on a phone the overflow destinations join the one nav sheet",
          "nav--secondary" in shell and "nav__sep" in shell)
    check("the overflow menu reaches guides, tools and how-it-works",
          all(f'href: "{p}"' in shell for p in
              ["guide.html", "tools.html", "how-it-works.html"]))
    check("guides and the design system are in the footer too",
          'href="guide.html"' in shell and 'href="styleguide.html"' in shell)

    print("shell — the dataset line is built from the export, never invented:")
    check("there is no ticker left in the shell",
          "buildTicker" not in shell and "ticker__track" not in shell)
    check("the status line reads meta/patches/published comps out of the dataset",
          "buildDataline" in shell and "P.pub" in shell
          and "publishedComps" in shell)
    check("it refuses to render rather than printing placeholders",
          "if (items.length < 3) return null" in shell)
    check("staleness is stated in words, not only in amber",
          '" · stale"' in shell)
    check("demo data still raises a visible bar, not just a pill",
          "meta.demo" in shell and 'data-trust="demo"' in shell)

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
