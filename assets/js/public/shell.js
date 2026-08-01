/* =====================================================================
   OWCS Comp Tracker — public/shell.js
   Shared shell for every public page: header, nav, demo ribbon, footer,
   atmosphere canvas, and progressive motion (Lenis smooth scroll + GSAP
   reveals when the vendored libraries are present; everything degrades
   to plain, fully readable pages without them). Respects
   prefers-reduced-motion throughout.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS_PUB || {};
  const D = P.data;
  const esc = P.esc || ((s) => String(s));
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* Ensure a favicon so tabs are branded and the browser stops 404-ing
     /favicon.ico (the SVG lives in the repo). Cheap, idempotent. */
  if (!document.querySelector('link[rel="icon"]')) {
    const l = document.createElement("link");
    l.rel = "icon"; l.type = "image/svg+xml"; l.href = "assets/img/favicon.svg";
    document.head.appendChild(l);
  }

  /* One site, one nav: the five result surfaces people actually open.
     Everything else (tournaments, comps, swaps, maps) stays one click away
     in the footer — heavily simplified on purpose. "How it works" sits in
     the nav because every number on this site needs its definition to be
     one click away, not buried. */
  const NAV = [
    { href: "matches.html", label: "Matches" },
    { href: "calendar.html", label: "Calendar" },
    { href: "teams.html", label: "Teams" },
    { href: "heroes.html", label: "Heroes" },
    { href: "stats.html", label: "Stats" },
    { href: "how-it-works.html", label: "How it works" },
  ];
  const ADMIN = [
    { href: "portal.html", label: "Portal" },
  ];

  /* ---- header ------------------------------------------------------ */
  function buildHeader() {
    const here = location.pathname.split("/").pop() || "index.html";
    const link = (n, cls) => {
      const cur = here === n.href || (here === "tournament.html" && n.href === "tournaments.html") ||
        (here === "match.html" && n.href === "matches.html") ||
        (here === "team.html" && n.href === "teams.html") ||
        (here === "hero.html" && n.href === "heroes.html");
      return `<a href="${n.href}" class="${cls || ""}"${cur ? ' aria-current="page"' : ""}>${esc(n.label)}</a>`;
    };
    const header = document.createElement("header");
    header.className = "pub-header";
    header.innerHTML = `
      <div class="pub-header__inner">
        <a class="pub-brand" href="index.html" aria-label="OWCS Comp Tracker home">
          <span class="pub-brand__mark" aria-hidden="true">CT</span>
          <span>OWCS Comp Tracker</span>
          <span class="pub-brand__tag">paste a link, get the comps</span>
        </a>
        <button class="pub-nav-toggle" aria-expanded="false" aria-controls="pub-nav">Menu</button>
        <nav class="pub-nav" id="pub-nav" aria-label="Primary">
          ${NAV.map((n) => link(n)).join("")}
          <span class="nav-sep" aria-hidden="true"></span>
          ${ADMIN.map((n) => link(n, "nav-admin")).join("")}
        </nav>
        <button class="pub-search-btn" id="pub-search-btn" aria-haspopup="dialog"
                title="Search matches, teams, heroes and maps (press /)">
          <span aria-hidden="true">⌕</span><span class="psb-label">Search</span>
          <kbd aria-hidden="true">/</kbd>
        </button>
        <div class="pub-header__status" data-state="${D && D.meta && D.meta.demo ? "demo" : "prod"}">
          <span class="dot" aria-hidden="true"></span>
          <span>${D && D.meta && D.meta.demo ? "demo dataset" : "production"}</span>
        </div>
      </div>`;
    /* The skip link must stay the FIRST thing a keyboard user reaches.
       Prepending the header ahead of it made "Skip to content" the tenth
       tab stop — i.e. useless, because you had to tab through the whole
       nav to reach the control that exists to skip the nav. */
    const skip = document.querySelector(".skip-link");
    if (skip) skip.after(header); else document.body.prepend(header);
    if (D && D.meta && D.meta.demo) {
      const ribbon = document.createElement("div");
      ribbon.className = "demo-ribbon";
      ribbon.setAttribute("role", "note");
      ribbon.innerHTML = `<strong>Demo data</strong> — every team, score and comp on this build is a labeled fixture. Production exports replace this dataset.`;
      header.after(ribbon);
    }
    const toggle = header.querySelector(".pub-nav-toggle");
    const nav = header.querySelector(".pub-nav");
    toggle.addEventListener("click", () => {
      const open = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && nav.classList.contains("open")) {
        nav.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
        toggle.focus();
      }
    });
  }

  /* ---- search: one box that reaches every page ----------------------
     A small site with data spread over ten surfaces is still a site you
     can get lost in. This indexes everything the dataset knows about —
     matches, teams, heroes, maps, tournaments — plus the site's own
     pages, and jumps straight there. Opens with the button, "/" or
     ⌘/Ctrl-K; arrows to move, Enter to go, Esc to leave. */
  function buildSearch() {
    const items = [];
    const add = (type, label, sub, href, keys) =>
      items.push({ type, label, sub: sub || "", href, keys: (keys || label).toLowerCase() });

    if (D) {
      (D.matches || []).forEach((m) => {
        const a = P.team(m.teamA), b = P.team(m.teamB);
        const t = P.tournament(m.tournamentId);
        const label = `${a ? a.name : "TBD"} vs ${b ? b.name : "TBD"}`;
        add("match", label, t ? t.name : "Match", `match.html?id=${m.id}`,
          [label, a && a.code, b && b.code, t && t.name].filter(Boolean).join(" "));
      });
      (D.teams || []).forEach((t) =>
        add("team", t.name, `Team · ${P.regionName(t.region)}`, `team.html?id=${t.id}`,
          [t.name, t.code, (t.aliases || []).join(" ")].join(" ")));
      (D.heroes || []).forEach((h) =>
        add("hero", h.name, `Hero · ${h.role || "—"}`, `hero.html?id=${h.id}`,
          `${h.name} ${h.id} ${h.role || ""}`));
      (D.mapsCatalog || []).forEach((m) =>
        add("map", m.name, `Map · ${m.mode || "—"}`, `maps.html#${m.id}`,
          `${m.name} ${m.mode || ""}`));
      (D.tournaments || []).forEach((t) =>
        add("event", t.name, "Tournament", `tournament.html?id=${t.id}`, t.name));
    }
    [["Matches", "matches.html", "Schedule and results"],
     ["Calendar", "calendar.html", "The season by day"],
     ["Teams", "teams.html", "Team directory"],
     ["Heroes", "heroes.html", "Hero directory"],
     ["Hero stats", "stats.html", "Pick and win rates"],
     ["Compositions", "comps.html", "Every verified line-up"],
     ["Swap evidence", "swaps.html", "Confirmed and rejected swaps"],
     ["Maps", "maps.html", "Map meta"],
     ["Tournaments", "tournaments.html", "Events and brackets"],
     ["How it works", "how-it-works.html", "What verified means"],
     ["Operator portal", "portal.html", "Paste a broadcast link"],
    ].forEach(([label, href, sub]) => add("page", label, sub, href, label + " " + sub));

    const wrap = document.createElement("div");
    wrap.className = "palette";
    wrap.hidden = true;
    wrap.innerHTML = `
      <div class="palette__scrim" data-close></div>
      <div class="palette__box" role="dialog" aria-modal="true" aria-label="Search the site">
        <input type="search" id="palette-input" autocomplete="off" spellcheck="false"
               placeholder="Search matches, teams, heroes, maps…"
               role="combobox" aria-expanded="true" aria-controls="palette-list"
               aria-autocomplete="list">
        <ul class="palette__list" id="palette-list" role="listbox"
            aria-label="Search results"></ul>
        <p class="palette__hint">↑↓ to move · Enter to open · Esc to close</p>
      </div>`;
    document.body.append(wrap);
    const input = wrap.querySelector("#palette-input");
    const list = wrap.querySelector("#palette-list");
    let shown = [], cursor = 0, lastFocus = null;

    const score = (it, q) => {
      const i = it.keys.indexOf(q);
      if (i < 0) return -1;
      return (i === 0 ? 0 : 10) + i + (it.type === "page" ? 2 : 0);
    };
    function render(q) {
      const query = q.trim().toLowerCase();
      shown = (query
        ? items.map((it) => ({ it, s: score(it, query) })).filter((r) => r.s >= 0)
          .sort((a, b) => a.s - b.s).slice(0, 12).map((r) => r.it)
        : items.filter((it) => it.type === "page").slice(0, 8));
      cursor = 0;
      if (!shown.length) {
        list.innerHTML = `<li class="palette__empty">Nothing matches “${esc(q)}”.
          The dataset only contains what has been captured so far —
          <a href="how-it-works.html">why coverage is small</a>.</li>`;
        return;
      }
      list.innerHTML = shown.map((it, i) => `
        <li role="option" id="palette-opt-${i}" aria-selected="${i === 0}"
            class="palette__row${i === 0 ? " is-on" : ""}" data-href="${esc(it.href)}">
          <span class="pr-type">${esc(it.type)}</span>
          <span class="pr-label">${esc(it.label)}</span>
          <span class="pr-sub">${esc(it.sub)}</span></li>`).join("");
    }
    function move(d) {
      if (!shown.length) return;
      cursor = (cursor + d + shown.length) % shown.length;
      Array.from(list.children).forEach((el, i) => {
        const on = i === cursor;
        el.classList.toggle("is-on", on);
        el.setAttribute("aria-selected", on ? "true" : "false");
        if (on) el.scrollIntoView({ block: "nearest" });
      });
      input.setAttribute("aria-activedescendant", "palette-opt-" + cursor);
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
    const go = (i) => { const it = shown[i]; if (it) location.href = it.href; };

    input.addEventListener("input", () => render(input.value));
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "Enter") { e.preventDefault(); go(cursor); }
      else if (e.key === "Escape") { e.preventDefault(); close(); }
    });
    list.addEventListener("click", (e) => {
      const row = e.target.closest("[data-href]");
      if (row) location.href = row.dataset.href;
    });
    wrap.addEventListener("click", (e) => { if (e.target.dataset.close !== undefined) close(); });
    const btn = document.getElementById("pub-search-btn");
    if (btn) btn.addEventListener("click", open);
    document.addEventListener("keydown", (e) => {
      const typing = /^(input|textarea|select)$/i.test((e.target.tagName || "")) ||
        e.target.isContentEditable;
      if (!wrap.hidden) return;
      if ((e.key === "/" && !typing) ||
          ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey))) {
        e.preventDefault();
        open();
      }
    });
  }

  /* ---- footer ------------------------------------------------------ */
  function buildFooter() {
    const gen = D && D.meta ? D.meta.generatedAt : null;
    const stale = P.isStale && P.isStale(24);
    const f = document.createElement("footer");
    f.className = "pub-footer";
    f.innerHTML = `
      <div class="pub-footer__inner">
        <div>
          <h2>OWCS Comp Tracker</h2>
          <p>Professional Overwatch schedules, brackets, results and — the part nobody else has — verified team compositions, extracted from broadcast video and reviewed by a human.</p>
          <p>Statistics on this site only count comps whose review status is <b>reviewed</b> or <b>auto-high</b>; everything traces back to frames, crops and match scores. Match facts come from FACEIT, official pages or manual entry; hero comps never come from FACEIT.</p>
          <p class="pub-footer__meta">
            Data generated <span data-freshness>${gen ? esc(P.fmtLocal(gen)) : "—"}</span>
            ${stale ? ' · <span class="stale-note">dataset older than 24h — re-run the export</span>' : ""}
          </p>
        </div>
        <div>
          <h2>More data</h2>
          <ul>
            <li><a href="tournaments.html">Tournaments</a></li>
            <li><a href="comps.html">Compositions</a></li>
            <li><a href="swaps.html">Swap intelligence</a></li>
            <li><a href="maps.html">Maps</a></li>
          </ul>
        </div>
        <div>
          <h2>Behind the data</h2>
          <ul>
            <li><a href="how-it-works.html">How it works (start here)</a></li>
            <li><a href="how-it-works.html#glossary">What the badges mean</a></li>
            <li><a href="portal.html">Operator portal (paste a link)</a></li>
            <li><a href="runs.html">Vision lab (runs)</a></li>
            <li><a href="sources.html">Sources</a></li>
            <li><a href="admin.html">Review &amp; corrections</a></li>
          </ul>
        </div>
      </div>`;
    document.body.append(f);
  }

  /* ---- atmosphere ----------------------------------------------------
     Best-available ambience via the shared motion engine: Vanta NET
     tactical grid (three.js/WebGL) -> lightweight 2D canvas net -> the
     static CSS gradient. All fallbacks live in assets/js/motion.js;
     without the engine (or with prefers-reduced-motion) the holder keeps
     its static gradient and the page is complete. */
  function buildAtmosphere() {
    const holder = document.createElement("div");
    holder.id = "pub-atmosphere";
    holder.setAttribute("aria-hidden", "true");
    document.body.prepend(holder);
    if (reduced) return; // static gradient only
    if (window.OWCSMotion) window.OWCSMotion.atmosphere(holder);
  }

  /* ---- motion: shared engine (Lenis/GSAP/ScrollTrigger) + .rv system --
     The engine (motion.js) owns smooth scroll, the entrance timeline,
     decrypt labels, magnetic buttons, tilt/spotlight and the progress
     hairline. This shell keeps owning the .rv reveal contract because
     page scripts re-render fragments and call P.observeReveals(root). */
  function initMotion() {
    /* The engine (motion.js) owns the ONE Lenis instance and the ONE
       GSAP ticker loop. This shell never creates its own scroller — a
       second Lenis racing the first is exactly what broke scrolling.
       Without the engine, native scrolling is the correct behavior. */
    if (window.OWCSMotion) window.OWCSMotion.boot({ ambience: false });
    if (reduced) return;
    document.documentElement.classList.add("motion-on");

    /* Content on this site is the product. A scroll reveal may delay it;
       it may NEVER be able to withhold it. Every path below therefore
       ends in a guaranteed-visible state:
         - `.rv-in` is added FIRST (CSS alone then makes the element
           visible), so an interrupted or missing GSAP tween cannot leave
           a card at opacity 0;
         - `clearProps` drops the inline styles GSAP wrote when it lands;
         - a watchdog reveals anything still pending a few seconds after
           load, so print, find-in-page, deep links that jump past a
           section, in-page anchors and headless renderers can never see
           an empty page. Round 1 measured 10 permanently-invisible blocks
           on the home page and 2 hidden match rows on /matches. */
    const pending = new Set();
    const reveal = (els) => {
      els = els.filter((e) => pending.has(e));
      if (!els.length) return;
      els.forEach((e) => { pending.delete(e); e.classList.add("rv-in"); });
      if (window.gsap) {
        window.gsap.to(els, {
          opacity: 1, y: 0, duration: 0.5, ease: "power2.out",
          stagger: 0.06, overwrite: true, clearProps: "opacity,transform",
        });
      } else {
        els.forEach((e) => { e.style.opacity = ""; e.style.transform = ""; });
      }
    };
    const revealAll = () => reveal(Array.from(pending));

    const arm = (root) => {
      const fresh = Array.from((root || document).querySelectorAll(".rv:not(.rv-in)"))
        .filter((el) => !pending.has(el));
      if (!fresh.length) return;
      fresh.forEach((el) => pending.add(el));
      if (!io) { reveal(fresh); return; }
      /* anything already in (or just under) the first screen is part of
         the page, not a scroll surprise — reveal it now so the first
         paint is complete and LCP isn't gated on an observer tick */
      const above = [], below = [];
      fresh.forEach((el) =>
        (el.getBoundingClientRect().top < innerHeight * 0.95 ? above : below).push(el));
      if (above.length) reveal(above);
      below.forEach((el) => io.observe(el));
    };

    const io = "IntersectionObserver" in window
      ? new IntersectionObserver((entries) => {
        const batch = [];
        for (const en of entries)
          if (en.isIntersecting) { batch.push(en.target); io.unobserve(en.target); }
        if (batch.length) reveal(batch);
      }, { rootMargin: "0px 0px -6% 0px" })
      : null;

    P.observeReveals = (root) => arm(root);
    arm(document);

    /* Watchdog. Two competing requirements:
         - content must never be withheld from a reader who does not
           scroll (print, find-in-page, a deep link that jumps past a
           section, a screenshot tool, some assistive tech);
         - a reader who IS scrolling should still get the reveal they
           came for, all the way down a long page.
       So: if nothing has scrolled by the deadline, show everything —
       nobody is watching, so there is no animation to lose. Once real
       scrolling is observed the observer is demonstrably working and the
       deadline is pushed out, with a hard ceiling that still guarantees
       nothing stays hidden forever. */
    let scrolled = false;
    let watchdog = null;
    const armWatchdog = (ms) => {
      clearTimeout(watchdog);
      watchdog = setTimeout(() => {
        if (!scrolled) { revealAll(); return; }
        scrolled = false;
        armWatchdog(8000);          // still scrolling — check again later
      }, ms);
    };
    armWatchdog(4000);
    addEventListener("scroll", () => { scrolled = true; }, { passive: true });
    setTimeout(revealAll, 30000);   // hard ceiling, whatever else happened
    addEventListener("beforeprint", revealAll);
    if (window.matchMedia) {
      const mq = window.matchMedia("print");
      if (mq.addEventListener) mq.addEventListener("change", (e) => { if (e.matches) revealAll(); });
    }
  }
  P.observeReveals = P.observeReveals || function () {};

  /* ---- spotlight cards (React Bits port) --------------------------- */
  function initSpotlight() {
    if (reduced) return;
    document.addEventListener("pointermove", (e) => {
      const card = e.target.closest && e.target.closest(".card--spot");
      if (!card) return;
      const r = card.getBoundingClientRect();
      card.style.setProperty("--spot-x", (e.clientX - r.left) + "px");
      card.style.setProperty("--spot-y", (e.clientY - r.top) + "px");
    }, { passive: true });
  }

  /* ---- count-up numbers (React Bits port; engine-backed) ----------- */
  P.countUp = (el) => {
    if (window.OWCSMotion) { window.OWCSMotion.countUp(el); return; }
    const target = parseFloat(el.dataset.countTo || el.textContent) || 0;
    const suffix = el.dataset.countSuffix || "";
    if (reduced || !window.gsap) { el.textContent = el.dataset.countText || (target + suffix); return; }
    const obj = { v: 0 };
    window.gsap.to(obj, {
      v: target, duration: 0.9, ease: "power2.out",
      onUpdate: () => { el.textContent = (target % 1 ? obj.v.toFixed(1) : Math.round(obj.v)) + suffix; },
    });
  };

  /* ---- boot -------------------------------------------------------- */
  document.addEventListener("DOMContentLoaded", () => {
    buildAtmosphere();
    buildHeader();
    buildSearch();
    buildFooter();
    initMotion();
    initSpotlight();
    document.querySelectorAll("[data-count-to]").forEach(P.countUp);
  });
})();
