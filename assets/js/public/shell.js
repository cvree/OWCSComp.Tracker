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

  const NAV = [
    { href: "tournaments.html", label: "Tournaments" },
    { href: "matches.html", label: "Matches" },
    { href: "teams.html", label: "Teams" },
    { href: "maps.html", label: "Maps" },
    { href: "stats.html", label: "Stats" },
  ];
  const ADMIN = [
    { href: "index.html", label: "Control room" },
    { href: "runs.html", label: "Vision lab" },
  ];

  /* ---- header ------------------------------------------------------ */
  function buildHeader() {
    const here = location.pathname.split("/").pop() || "tournaments.html";
    const link = (n, cls) => {
      const cur = here === n.href || (here === "tournament.html" && n.href === "tournaments.html") ||
        (here === "match.html" && n.href === "matches.html") ||
        (here === "team.html" && n.href === "teams.html");
      return `<a href="${n.href}" class="${cls || ""}"${cur ? ' aria-current="page"' : ""}>${esc(n.label)}</a>`;
    };
    const header = document.createElement("header");
    header.className = "pub-header";
    header.innerHTML = `
      <div class="pub-header__inner">
        <a class="pub-brand" href="tournaments.html" aria-label="OWCS Comp Tracker home">
          <span class="pub-brand__mark" aria-hidden="true">CT</span>
          <span>OWCS Comp Tracker</span>
          <span class="pub-brand__tag">every comp, with receipts</span>
        </a>
        <button class="pub-nav-toggle" aria-expanded="false" aria-controls="pub-nav">Menu</button>
        <nav class="pub-nav" id="pub-nav" aria-label="Primary">
          ${NAV.map((n) => link(n)).join("")}
          <span class="nav-sep" aria-hidden="true"></span>
          ${ADMIN.map((n) => link(n, "nav-admin")).join("")}
        </nav>
        <div class="pub-header__status" data-state="${D && D.meta && D.meta.demo ? "demo" : "prod"}">
          <span class="dot" aria-hidden="true"></span>
          <span>${D && D.meta && D.meta.demo ? "demo dataset" : "production"}</span>
        </div>
      </div>`;
    document.body.prepend(header);
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

  /* ---- footer ------------------------------------------------------ */
  function buildFooter() {
    const gen = D && D.meta ? D.meta.generatedAt : null;
    const stale = P.isStale && P.isStale(24);
    const f = document.createElement("footer");
    f.className = "pub-footer";
    f.innerHTML = `
      <div class="pub-footer__inner">
        <div>
          <h3>OWCS Comp Tracker</h3>
          <p>Professional Overwatch schedules, brackets, results and — the part nobody else has — verified team compositions, extracted from broadcast video and reviewed by a human.</p>
          <p>Statistics on this site only count comps whose review status is <b>reviewed</b> or <b>auto-high</b>; everything traces back to frames, crops and match scores. Match facts come from FACEIT, official pages or manual entry; hero comps never come from FACEIT.</p>
          <p class="pub-footer__meta">
            Data generated <span data-freshness>${gen ? esc(P.fmtLocal(gen)) : "—"}</span>
            ${stale ? ' · <span class="stale-note">dataset older than 24h — re-run the export</span>' : ""}
          </p>
        </div>
        <div>
          <h3>Explore</h3>
          <ul>
            <li><a href="tournaments.html">Tournaments</a></li>
            <li><a href="matches.html">Matches</a></li>
            <li><a href="stats.html">Statistics</a></li>
          </ul>
        </div>
        <div>
          <h3>Behind the data</h3>
          <ul>
            <li><a href="index.html">Control room</a></li>
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
    if (window.OWCSMotion) window.OWCSMotion.boot({ ambience: false });
    if (reduced) return;
    document.documentElement.classList.add("motion-on");
    if (!window.OWCSMotion && typeof window.Lenis === "function") {
      try {
        const lenis = new window.Lenis({ duration: 0.9, smoothWheel: true });
        const loop = (t) => { lenis.raf(t); requestAnimationFrame(loop); };
        requestAnimationFrame(loop);
      } catch (_) { /* plain scrolling is fine */ }
    }
    const reveal = (els) => {
      if (window.gsap) {
        window.gsap.to(els, {
          opacity: 1, y: 0, duration: 0.5, ease: "power2.out",
          stagger: 0.06, overwrite: true,
          onComplete: () => els.forEach((e) => e.classList.add("rv-in")),
        });
      } else {
        els.forEach((e) => e.classList.add("rv-in"));
      }
    };
    const pending = new Set(document.querySelectorAll(".rv"));
    if (!("IntersectionObserver" in window)) { reveal(Array.from(pending)); return; }
    const io = new IntersectionObserver((entries) => {
      const batch = [];
      for (const en of entries)
        if (en.isIntersecting) { batch.push(en.target); io.unobserve(en.target); }
      if (batch.length) reveal(batch);
    }, { rootMargin: "0px 0px -6% 0px" });
    pending.forEach((el) => io.observe(el));
    P.observeReveals = (root) => {
      (root || document).querySelectorAll(".rv:not(.rv-in)").forEach((el) => io.observe(el));
    };
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
    buildFooter();
    initMotion();
    initSpotlight();
    document.querySelectorAll("[data-count-to]").forEach(P.countUp);
  });
})();
