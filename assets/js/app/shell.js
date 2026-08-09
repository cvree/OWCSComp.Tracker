/* =====================================================================
   OWCS Comp Tracker — app/shell.js
   The shared chrome: one header row, five-item nav with an overflow
   menu, a quiet dataset provenance line, search, footer, reveals.

   The chrome above the content is deliberately one row plus one thin
   status line. It used to be three (nav, secondary rail, scrolling
   ticker), which meant a person arriving at any page met roughly 150px
   of product furniture before a single fact about Overwatch.

   The navigation is the product's answer to "what is this for". It has
   exactly five entries and they are the five things a person does here,
   in the order they do them:

       Dashboard · Games · Submit · Review · Stats

   Everything else — teams, heroes, tournaments, sources, calibration,
   diagnostics, the evidence archive — is reached from the screen it
   belongs to, or from Tools. A dataset does not earn a nav slot.
   ===================================================================== */
(function () {
  "use strict";

  const P = window.OWCS;
  const esc = P.esc;
  /* Guarded. An unguarded matchMedia call here threw on every renderer
     that does not implement it, which killed this whole module before it
     built the header, nav, search or footer. See P.media in core.js. */
  const reduced = P.media("(prefers-reduced-motion: reduce)");

  const NAV = [
    { href: "index.html", label: "Dashboard", match: ["index.html", ""] },
    { href: "games.html", label: "Games", match: ["games.html", "game.html"] },
    { href: "submit.html", label: "Submit", match: ["submit.html"] },
    { href: "review.html", label: "Review", match: ["review.html"], pip: "review" },
    { href: "stats.html", label: "Stats",
      match: ["stats.html", "hero.html", "team.html", "teams.html"] },
  ];

  /* The five-item nav is the product's answer to "what is this for" and
     it stays five. RAIL is NOT a second nav and must never become one:
     hero, map and composition data are TABS on the stats page, not
     destinations, and putting them here would rebuild exactly the
     page-per-dataset architecture the redesign removed (test_site.py
     enforces that, deliberately).

     What RAIL carries is the set of real destinations that existed only
     in the footer — which is "reachable" in the sense that a basement is
     reachable. It used to occupy a permanent second row under the
     header; that row cost every page a strip of chrome before any
     content and made the product look like a directory. It now lives in
     an overflow menu at the end of the primary nav, and is appended to
     the mobile sheet where there is no room for a second control. */
  const RAIL = [
    { href: "teams.html", label: "Teams", note: "Every org in the dataset",
      match: ["teams.html", "team.html"] },
    { href: "guide.html", label: "Guides", note: "Every task, walked through",
      match: ["guide.html"] },
    { href: "how-it-works.html", label: "How it works", note: "In plain language",
      match: ["how-it-works.html"] },
    { href: "tools.html", label: "Tools & diagnostics", note: "For operators",
      match: ["tools.html"] },
    { href: "calibrate.html", label: "Calibrate", note: "Teach the tracker a new HUD",
      match: ["calibrate.html"] },
  ];

  /* --------------------------------------------------------- header */
  function buildHeader() {
    const here = location.pathname.split("/").pop() || "index.html";
    const counts = P.games ? P.games.counts() : { review: 0 };

    const link = (n) => {
      const cur = n.match.indexOf(here) >= 0;
      const pip = n.pip === "review" && counts.review
        ? '<span class="pip" aria-hidden="true">' + counts.review + "</span>" +
          '<span class="visually-hidden">' + counts.review + " waiting</span>"
        : "";
      return '<a href="' + n.href + '"' + (cur ? ' aria-current="page"' : "") + ">" +
        esc(n.label) + pip + "</a>";
    };

    const hdr = document.createElement("header");
    hdr.className = "hdr";
    hdr.innerHTML =
      '<div class="hdr__in">' +
        '<a class="brand" href="index.html">' +
          '<span class="brand__mark" aria-hidden="true">CT</span>' +
          '<span class="brand__name">OWCS Comp Tracker' +
            "<small>reviewed match data</small></span>" +
        "</a>" +
        '<nav class="nav" id="nav" aria-label="Primary">' + NAV.map(link).join("") +
          /* The overflow destinations, appended to the same sheet on a
             phone. Hidden at desktop widths by CSS, where the <details>
             menu in hdr__tools carries them instead — one list of links
             in the DOM per breakpoint, never two competing ones. */
          '<p class="nav__sep" aria-hidden="true">More</p>' +
          RAIL.map((r) =>
            '<a class="nav--secondary" href="' + r.href + '"' +
            (r.match.indexOf(here) >= 0 ? ' aria-current="page"' : "") + ">" +
            esc(r.label) + "</a>").join("") +
        "</nav>" +
        '<div class="hdr__tools">' +
          '<details class="more" id="more-menu">' +
            '<summary aria-label="More destinations">More</summary>' +
            '<div class="more__panel">' +
              RAIL.map((r) =>
                '<a href="' + r.href + '"' +
                (r.match.indexOf(here) >= 0 ? ' aria-current="page"' : "") + ">" +
                esc(r.label) + "<small>" + esc(r.note) + "</small></a>").join("") +
              '<span class="more__sep" aria-hidden="true"></span>' +
              '<a href="styleguide.html">Design system<small>Every component, in one place</small></a>' +
            "</div>" +
          "</details>" +
          /* An inline SVG, not the "⌕" character: that glyph is missing
             from a lot of families and falls back to a smudge at 12px,
             which is what the icon-only phone header would have shown. */
          '<button class="btn btn--ghost btn--sm" id="search-btn" aria-haspopup="dialog" ' +
            'aria-label="Search games, teams and heroes" ' +
            'title="Search games, teams and heroes (press /)">' +
            '<svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true" fill="none" ' +
            'stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
            '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 4.5 4.5"/></svg>' +
            '<span class="u-nowrap">Search</span>' +
            '<kbd aria-hidden="true">/</kbd></button>' +
          '<button class="btn btn--ghost btn--sm nav-toggle" id="nav-toggle" ' +
            'aria-expanded="false" aria-controls="nav">Menu</button>' +
        "</div>" +
      "</div>";

    const skip = document.querySelector(".skip-link");
    if (skip) skip.after(hdr); else document.body.prepend(hdr);

    /* One quiet provenance line, not a scrolling marquee. */
    const line = buildDataline();
    let tail = hdr;
    if (line) { hdr.after(line); tail = line; }

    /* Demo data must never be mistakable for production. The provenance
       line says so in words; this bar says so at a glance. It renders
       only when the loaded export says so, so it cannot be left on by
       accident. */
    if (P.pub && P.pub.meta && P.pub.meta.demo) {
      const ribbon = document.createElement("div");
      ribbon.className = "slab slab--amber";
      ribbon.setAttribute("role", "note");
      ribbon.style.cssText =
        "padding:10px var(--s-5);border-radius:0;border-left:0;border-right:0;border-top:0";
      ribbon.innerHTML = '<p><span class="trust" data-trust="demo">Demo data</span> ' +
        "Every team, score and composition on this build is a labelled fixture. " +
        "Production exports replace this dataset. " +
        '<a href="how-it-works.html">What that means →</a></p>';
      tail.after(ribbon);
    }

    const nav = hdr.querySelector("#nav");
    const toggle = hdr.querySelector("#nav-toggle");
    const more = hdr.querySelector("#more-menu");
    toggle.addEventListener("click", () => {
      const open = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (more && more.open) {
        more.open = false;
        const s = more.querySelector("summary");
        if (s) s.focus();
        return;
      }
      if (nav.classList.contains("open")) {
        nav.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
        toggle.focus();
      }
    });
    /* A tap outside either sheet closes it — on a phone the toggle is
       the only other way out and it is easy to miss, and a <details>
       menu that stays open after you click the page feels broken. */
    document.addEventListener("click", (e) => {
      if (more && more.open && !more.contains(e.target)) more.open = false;
      if (!nav.classList.contains("open")) return;
      if (nav.contains(e.target) || toggle.contains(e.target)) return;
      nav.classList.remove("open");
      toggle.setAttribute("aria-expanded", "false");
    });
  }

  /* ------------------------------------------------------- dataline
     Where the dataset stands, said once and quietly. This replaces the
     scrolling broadcast ticker that used to sit under the header: the
     facts were worth keeping, the marquee was not. It does not move, it
     does not repeat itself, and it costs one 30px row.

         ● Production dataset · Updated 1d ago · 8 published compositions

     Every value is READ FROM THE EXPORT. There is no placeholder copy
     in here, and if the dataset has too little to say the line is not
     rendered at all rather than printing a row of dashes — an invented
     status line would be the most visible lie on the site. */
  function buildDataline() {
    const D = P.pub;
    if (!D) return null;
    const items = [];
    const push = (value, opt) => {
      const o = opt || {};
      items.push('<span class="dataline__item"' +
        (o.tone ? ' data-tone="' + o.tone + '"' : "") + ">" +
        (o.dot === false ? "" : '<span class="dataline__dot" aria-hidden="true"></span>') +
        (o.strong ? "<b>" + esc(value) + "</b>" : esc(value)) + "</span>");
    };

    const demo = !!(D.meta && D.meta.demo);
    push(demo ? "Demo fixture" : "Production dataset",
      { tone: demo ? "warn" : "ok", strong: true });

    const gen = P.dataAge();
    if (gen) {
      /* The word "stale" is added, not implied by the colour: a person
         who cannot see the amber still learns the same fact. */
      const stale = P.isStale(24);
      push("Updated " + (P.fmtRel(gen) || P.fmtDateTime(gen)) + (stale ? " · stale" : ""),
        { dot: false, tone: stale ? "warn" : "" });
    }

    /* current patch: newest entry in the exported patch list, or nothing */
    const patches = (D.patches || []).slice()
      .sort((a, b) => String(b.from || "").localeCompare(String(a.from || "")));
    if (patches[0]) push("Patch " + (patches[0].name || patches[0].id), { dot: false });

    const comps = P.publishedComps();
    push(comps.length + " published composition" + (comps.length === 1 ? "" : "s"),
      { dot: false });

    if (P.games) {
      const c = P.games.counts();
      push(c.published + " of " + c.total + " games published", { dot: false });
      if (c.review) push(c.review + " waiting on review", { tone: "warn" });
      if (c.working) push(c.working + " processing", { tone: "live" });
    }

    if (items.length < 3) return null;
    const el = document.createElement("div");
    el.className = "dataline";
    el.setAttribute("aria-label", "Dataset status");
    el.innerHTML = '<div class="dataline__in">' + items.join("") +
      '<a href="how-it-works.html" style="margin-left:auto">Where this data comes from →</a>' +
      "</div>";
    return el;
  }

  /* --------------------------------------------------------- search */
  function buildSearch() {
    const items = [];
    const add = (type, label, sub, href, keys) =>
      items.push({ type: type, label: label, sub: sub || "", href: href,
        keys: String(keys || label).toLowerCase() });

    if (P.games) {
      P.games.all().forEach((g) => {
        const a = P.team(g.teamA), b = P.team(g.teamB);
        add("game", g.title, P.STATE_WORDS[g.state].label, g.href,
          [g.title, a && a.code, b && b.code, g.tournamentName].filter(Boolean).join(" "));
      });
    }
    P.teams().forEach((t) =>
      add("team", t.name, "Team · " + P.regionName(t.region),
        "team.html?id=" + encodeURIComponent(t.id),
        [t.name, t.code, (t.aliases || []).join(" ")].join(" ")));
    P.heroes().forEach((h) =>
      add("hero", h.name, "Hero · " + (h.role || "—"),
        "hero.html?id=" + encodeURIComponent(h.id), h.name + " " + h.id + " " + (h.role || "")));

    [["Dashboard", "index.html", "Where everything stands"],
     ["Games", "games.html", "Every game, whatever state it is in"],
     ["Submit a game", "submit.html", "Paste a broadcast link"],
     ["Review", "review.html", "Confirm or correct detections"],
     ["Stats", "stats.html", "Hero, map and composition data"],
     ["Teams", "teams.html", "Team directory"],
     ["How it works", "how-it-works.html", "In plain language"],
     ["Tools & diagnostics", "tools.html", "For operators"],
     ["Calibrate a broadcast", "calibrate.html", "Teach the tracker a new HUD"],
     ["Guides", "guide.html", "Step-by-step walkthroughs for every task"],
     ["Guide: submit a game", "guide.html#submit", "From a pasted link to a queued job"],
     ["Guide: review detections", "guide.html#review", "The human gates, in order"],
     ["Guide: publish", "guide.html#publish", "Promote reviewed detections and export"],
     ["Guide: when something is blocked", "guide.html#troubleshoot", "Each blocked state and the next command"],
     ["Design system", "styleguide.html", "Every component, chip and evidence state"],
    ].forEach((r) => add("page", r[0], r[2], r[1], r[0] + " " + r[2]));

    const wrap = document.createElement("div");
    wrap.className = "palette";
    wrap.hidden = true;
    wrap.innerHTML =
      '<div class="palette__scrim" data-close></div>' +
      '<div class="palette__box" role="dialog" aria-modal="true" aria-label="Search">' +
        '<input type="search" id="palette-input" autocomplete="off" spellcheck="false" ' +
          'placeholder="Search games, teams, heroes…" role="combobox" aria-expanded="true" ' +
          'aria-controls="palette-list" aria-autocomplete="list">' +
        '<ul class="palette__list" id="palette-list" role="listbox" aria-label="Results"></ul>' +
        '<p class="palette__hint">↑↓ move · Enter open · Esc close</p>' +
      "</div>";
    document.body.appendChild(wrap);

    const input = wrap.querySelector("#palette-input");
    const list = wrap.querySelector("#palette-list");
    let shown = [], cursor = 0, lastFocus = null;

    const score = (it, q) => {
      const i = it.keys.indexOf(q);
      return i < 0 ? -1 : (i === 0 ? 0 : 10) + i + (it.type === "page" ? 2 : 0);
    };
    function render(q) {
      const query = q.trim().toLowerCase();
      shown = query
        ? items.map((it) => ({ it: it, s: score(it, query) })).filter((r) => r.s >= 0)
          .sort((a, b) => a.s - b.s).slice(0, 12).map((r) => r.it)
        : items.filter((it) => it.type === "page").slice(0, 8);
      cursor = 0;
      if (!shown.length) {
        list.innerHTML = '<li class="palette__empty">Nothing here matches “' + esc(q) +
          "”. Only games that have been submitted appear in search.</li>";
        return;
      }
      list.innerHTML = shown.map((it, i) =>
        '<li role="option" id="p-opt-' + i + '" aria-selected="' + (i === 0) + '" ' +
        'class="palette__row' + (i === 0 ? " on" : "") + '" data-href="' + esc(it.href) + '">' +
        '<span class="k">' + esc(it.type) + '</span><span class="l">' + esc(it.label) +
        '</span><span class="s">' + esc(it.sub) + "</span></li>").join("");
    }
    function move(d) {
      if (!shown.length) return;
      cursor = (cursor + d + shown.length) % shown.length;
      Array.prototype.forEach.call(list.children, (el, i) => {
        const on = i === cursor;
        el.classList.toggle("on", on);
        el.setAttribute("aria-selected", on ? "true" : "false");
        if (on) el.scrollIntoView({ block: "nearest" });
      });
      input.setAttribute("aria-activedescendant", "p-opt-" + cursor);
    }
    function open() {
      lastFocus = document.activeElement;
      wrap.hidden = false;
      document.documentElement.classList.add("palette-open");
      input.value = "";
      render("");
      input.focus();
    }
    function close() {
      wrap.hidden = true;
      document.documentElement.classList.remove("palette-open");
      if (lastFocus && lastFocus.focus) lastFocus.focus();
    }
    const go = (i) => { if (shown[i]) location.href = shown[i].href; };

    input.addEventListener("input", () => render(input.value));
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "Enter") { e.preventDefault(); go(cursor); }
      else if (e.key === "Escape") { e.preventDefault(); close(); }
      else if (e.key === "Tab") {
        /* the dialog holds focus: one input, so Tab cycles back to it */
        e.preventDefault();
      }
    });
    list.addEventListener("click", (e) => {
      const row = e.target.closest("[data-href]");
      if (row) location.href = row.dataset.href;
    });
    wrap.addEventListener("click", (e) => {
      if (e.target.dataset.close !== undefined) close();
    });
    const btn = document.getElementById("search-btn");
    if (btn) btn.addEventListener("click", open);
    document.addEventListener("keydown", (e) => {
      if (!wrap.hidden) return;
      const typing = /^(input|textarea|select)$/i.test(e.target.tagName || "") ||
        e.target.isContentEditable;
      if ((e.key === "/" && !typing) || ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey))) {
        e.preventDefault();
        open();
      }
    });
  }

  /* --------------------------------------------------------- footer */
  function buildFooter() {
    const gen = P.dataAge();
    const stale = P.isStale(24);
    const f = document.createElement("footer");
    f.className = "ftr";
    f.innerHTML =
      '<div class="ftr__in">' +
        "<div><h2>OWCS Comp Tracker</h2>" +
        "<p>The portal that turns competitive Overwatch broadcasts into reviewed, " +
        "trustworthy structured data. A composition is published only after a person " +
        "has confirmed what the detector read, and every published fact links back to " +
        "the frame it came from.</p>" +
        '<p class="u-mt-3"><a href="how-it-works.html">How it works, in plain language →</a></p></div>' +
        "<div><h2>Product</h2><ul>" +
          '<li><a href="index.html">Dashboard</a></li>' +
          '<li><a href="games.html">Games</a></li>' +
          '<li><a href="submit.html">Submit a game</a></li>' +
          '<li><a href="review.html">Review</a></li>' +
          '<li><a href="stats.html">Stats</a></li>' +
        "</ul></div>" +
        "<div><h2>Explore</h2><ul>" +
          '<li><a href="teams.html">Teams</a></li>' +
          '<li><a href="stats.html?tab=heroes">Heroes</a></li>' +
          '<li><a href="stats.html?tab=maps">Maps</a></li>' +
          '<li><a href="stats.html?tab=comps">Compositions</a></li>' +
        "</ul></div>" +
        "<div><h2>Operators</h2><ul>" +
          '<li><a href="guide.html">Guides — every step, walked through</a></li>' +
          '<li><a href="tools.html">Tools &amp; diagnostics</a></li>' +
          '<li><a href="calibrate.html">Calibrate a broadcast</a></li>' +
          '<li><a href="styleguide.html">Design system</a></li>' +
          '<li><a href="how-it-works.html#run-it-yourself">Run your own copy</a></li>' +
        "</ul></div>" +
      "</div>" +
      '<div class="ftr__in ftr__meta">' +
        "<span>Data generated " + esc(gen ? P.fmtDateTime(gen) : "—") + "</span>" +
        (stale ? '<span style="color:var(--amber)">dataset older than 24h</span>' : "") +
        "<span>Independent fan project · not affiliated with Blizzard or the OWCS</span>" +
      "</div>";
    document.body.appendChild(f);
  }

  /* -------------------------------------------------------- reveals
     Content is the product. A reveal may delay it; it may never be able
     to withhold it. Every path adds `.rv-in` FIRST (CSS alone then makes
     the element visible), so an interrupted or missing GSAP tween cannot
     leave a card at opacity 0, and a watchdog reveals anything still
     pending — for print, find-in-page, deep links that jump past a
     section, screenshot tools and assistive technology. */
  function initReveals() {
    if (reduced) { P.observeReveals = function () {}; return; }
    document.documentElement.classList.add("motion-on");

    const pending = new Set();
    const reveal = (els) => {
      els = els.filter((e) => pending.has(e));
      if (!els.length) return;
      els.forEach((e) => { pending.delete(e); e.classList.add("rv-in"); });
      if (window.gsap) {
        window.gsap.to(els, {
          opacity: 1, y: 0, duration: 0.45, ease: "power2.out",
          stagger: 0.05, overwrite: true, clearProps: "opacity,transform",
        });
      } else {
        els.forEach((e) => { e.style.opacity = ""; e.style.transform = ""; });
      }
    };
    const revealAll = () => reveal(Array.from(pending));

    const io = "IntersectionObserver" in window
      ? new IntersectionObserver((entries) => {
        const batch = [];
        entries.forEach((en) => {
          if (en.isIntersecting) { batch.push(en.target); io.unobserve(en.target); }
        });
        if (batch.length) reveal(batch);
      }, { rootMargin: "0px 0px -6% 0px" })
      : null;

    const arm = (root) => {
      const fresh = P.$$(".rv:not(.rv-in)", root || document).filter((el) => !pending.has(el));
      if (!fresh.length) return;
      fresh.forEach((el) => pending.add(el));
      if (!io) { reveal(fresh); return; }
      const above = [], below = [];
      fresh.forEach((el) =>
        (el.getBoundingClientRect().top < innerHeight * 0.95 ? above : below).push(el));
      if (above.length) reveal(above);
      below.forEach((el) => io.observe(el));
    };

    P.observeReveals = arm;
    arm(document);

    /* Page scripts render asynchronously and some set innerHTML directly
       rather than going through P.mount, so a `.rv` can appear long after
       the initial arming. An un-armed `.rv` is stuck at opacity 0 forever —
       content silently withheld, which is the one thing this system is not
       allowed to do. Watching the document closes that hole for every call
       site at once, including ones written later. */
    if ("MutationObserver" in window) {
      let queued = null;
      new MutationObserver(() => {
        if (queued) return;
        queued = requestAnimationFrame(() => { queued = null; arm(document); });
      }).observe(document.body, { childList: true, subtree: true });
    }

    let scrolled = false, watchdog = null;
    const armWatchdog = (ms) => {
      clearTimeout(watchdog);
      watchdog = setTimeout(() => {
        if (!scrolled) { revealAll(); return; }
        scrolled = false;
        armWatchdog(8000);
      }, ms);
    };
    armWatchdog(4000);
    addEventListener("scroll", () => { scrolled = true; }, { passive: true });
    setTimeout(revealAll, 30000);
    addEventListener("beforeprint", revealAll);
  }
  P.observeReveals = P.observeReveals || function () {};

  /* ----------------------------------------------------------- boot */
  document.addEventListener("DOMContentLoaded", () => {
    buildHeader();
    buildSearch();
    buildFooter();
    initReveals();
    if (window.OWCSMotion) window.OWCSMotion.boot();
    /* Probe for a local tracker on every page: the nav, the dashboard and
       the submit form all change shape depending on the answer. */
    if (P.api) P.api.probe();
  });
})();
