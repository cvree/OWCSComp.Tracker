/* =====================================================================
   OWCS Comp Tracker — app/shell.js
   The shared chrome: header, five-item nav, search, footer, reveals.

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
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const NAV = [
    { href: "index.html", label: "Dashboard", match: ["index.html", ""] },
    { href: "games.html", label: "Games", match: ["games.html", "game.html"] },
    { href: "submit.html", label: "Submit", match: ["submit.html"] },
    { href: "review.html", label: "Review", match: ["review.html"], pip: "review" },
    { href: "stats.html", label: "Stats",
      match: ["stats.html", "hero.html", "team.html", "teams.html"] },
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
        '<nav class="nav" id="nav" aria-label="Primary">' + NAV.map(link).join("") + "</nav>" +
        '<div class="hdr__tools">' +
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

    const nav = hdr.querySelector("#nav");
    const toggle = hdr.querySelector("#nav-toggle");
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
    /* A tap outside the sheet closes it — on a phone the toggle is the
       only other way out and it is easy to miss. */
    document.addEventListener("click", (e) => {
      if (!nav.classList.contains("open")) return;
      if (nav.contains(e.target) || toggle.contains(e.target)) return;
      nav.classList.remove("open");
      toggle.setAttribute("aria-expanded", "false");
    });
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
          '<li><a href="tools.html">Tools &amp; diagnostics</a></li>' +
          '<li><a href="calibrate.html">Calibrate a broadcast</a></li>' +
          '<li><a href="how-it-works.html#run-it-yourself">Run your own copy</a></li>' +
        "</ul></div>" +
      "</div>" +
      '<div class="ftr__in ftr__meta">' +
        "<span>Data generated " + esc(gen ? P.fmtDateTime(gen) : "—") + "</span>" +
        (stale ? '<span style="color:#ffcc82">dataset older than 24h</span>' : "") +
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
