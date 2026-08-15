/* =====================================================================
   OWCS Comp Tracker — app/core.js
   The one data + rendering layer the whole product shares.

   Two datasets exist and they mean different things. Keeping them
   separate is the credibility model, so this file never merges them:

     window.OWCS_PUBLIC (assets/data/public_data.v1.js)
        PUBLISHED data. Human-approved, evidence-backed, safe to show a
        fan as fact. Everything on a published game page comes from here.

     window.OWCS_DATA (assets/js/data.js)
        The WORKING record — processing runs, registered sources, the
        internal match list. Used to answer "what is happening", never
        to state a result.

   Vanilla JS, no framework, no build step. Every renderer returns an
   HTML string and escapes everything that came from data.
   ===================================================================== */
(function () {
  "use strict";

  const P = (window.OWCS = window.OWCS || {});
  const D = window.OWCS_PUBLIC || null;
  const W = window.OWCS_DATA || null;
  P.pub = D;
  P.work = W;

  /* The THIRD dataset, and the only one that fills itself: what the
     unattended scan has found on the official broadcast channels
     (assets/data/discovered.v1.js, rebuilt by the scheduled match-finder
     workflow). It is neither published fact nor local working state — it
     is "these broadcasts exist and here is what their own titles say" —
     so it is kept separate from both and every renderer that touches it
     labels it as machine-discovered. */
  P.disc = window.OWCS_DISCOVERED || null;
  P.discovered = () => (P.disc && P.disc.broadcasts) || [];
  P.discoveredEvents = () => (P.disc && P.disc.events) || [];
  /* Hours since the scan that produced the discovery layer, or null when
     no scan has ever run. Computed here, not stored in the artifact: the
     artifact is a pure function of its inputs and holds no wall clock. */
  P.scanAgeHours = () => {
    const when = P.disc && P.disc.scan && P.disc.scan.generatedAt;
    if (!when) return null;
    const ms = new Date(when).getTime();
    return isNaN(ms) ? null : (Date.now() - ms) / 3600000;
  };
  P.scanStale = () => {
    const age = P.scanAgeHours();
    const limit = (P.disc && P.disc.scan && P.disc.scan.staleAfterHours) || 24;
    return age == null ? true : age > limit;
  };

  /* ---------------------------------------------------------- escaping */
  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  P.esc = esc;

  /* ------------------------------------------------------------ lookups */
  const index = (arr) => {
    const m = new Map();
    (arr || []).forEach((x) => x && x.id != null && m.set(x.id, x));
    return m;
  };
  const IDX = P.idx = {
    teams: index(D && D.teams),
    heroes: index(D && D.heroes),
    maps: index(D && D.mapsCatalog),
    tournaments: index(D && D.tournaments),
    matches: index(D && D.matches),
    runs: index(D && D.captureRuns),
    regions: index(D && D.regions),
  };
  P.team = (id) => IDX.teams.get(id) || null;
  P.hero = (id) => IDX.heroes.get(id) || { id: id, name: id || "Unknown", role: "" };
  P.mapInfo = (id) => IDX.maps.get(id) || { id: id, name: id || "Unknown map", mode: "" };
  P.tournament = (id) => IDX.tournaments.get(id) || null;
  P.match = (id) => IDX.matches.get(id) || null;
  P.captureRun = (id) => IDX.runs.get(id) || null;
  P.regionName = (id) => (IDX.regions.get(id) || { name: id || "—" }).name;
  P.heroes = () => (D && D.heroes) || [];
  P.teams = () => (D && D.teams) || [];

  /* Everything the product will show a fan as a fact. The gate that keeps
     an unreviewed detection out of production lives in the exporter; this
     mirrors it so nothing slips through client-side either. */
  P.APPROVED = ["reviewed", "auto-high"];
  P.publishedComps = (filter) => {
    if (!D) return [];
    const overridden = new Set(
      (D.compSnapshots || []).filter((c) => c.overridesId).map((c) => c.overridesId));
    return (D.compSnapshots || []).filter((c) => {
      if (P.APPROVED.indexOf(c.reviewStatus) < 0) return false;
      if (c.source !== "cv" && c.source !== "manual") return false;
      if (overridden.has(c.id)) return false;
      return !filter || filter(c);
    });
  };

  /* The YouTube video id inside a URL, in every form the datasets use
     (watch?v=, youtu.be/, /live/, /embed/). Returns null for anything
     else — this is how a discovered broadcast is recognised as one the
     site already has a match or a run for. */
  P.videoId = (url) => {
    const s = String(url || "");
    const m = s.match(/[?&]v=([A-Za-z0-9_-]{6,})/) ||
      s.match(/youtu\.be\/([A-Za-z0-9_-]{6,})/) ||
      s.match(/youtube\.com\/(?:live|embed|shorts)\/([A-Za-z0-9_-]{6,})/);
    return m ? m[1] : null;
  };

  /* --------------------------------------------------------------- time */
  P.fmtDate = (iso) => {
    if (!iso) return "Date unknown";
    const d = new Date(iso);
    return isNaN(d) ? "Date unknown"
      : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  };
  P.fmtDateTime = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return isNaN(d) ? "—" : d.toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
    });
  };
  P.fmtRel = (iso) => {
    if (!iso) return "";
    const ms = new Date(iso).getTime();
    if (isNaN(ms)) return "";
    const diff = Date.now() - ms;
    const abs = Math.abs(diff);
    const units = [[86400000, "d"], [3600000, "h"], [60000, "m"]];
    for (let i = 0; i < units.length; i++) {
      if (abs >= units[i][0]) {
        const n = Math.round(abs / units[i][0]);
        return diff >= 0 ? n + units[i][1] + " ago" : "in " + n + units[i][1];
      }
    }
    return diff >= 0 ? "just now" : "any moment";
  };
  /* seconds into a VOD -> 1:02:03 */
  P.fmtClock = (s) => {
    if (s == null || isNaN(s)) return "—";
    const t = Math.max(0, Math.round(Number(s)));
    const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return h ? h + ":" + pad(m) + ":" + pad(sec) : m + ":" + pad(sec);
  };
  P.dataAge = () => (D && D.meta && D.meta.generatedAt) || null;
  P.isStale = (hours) => {
    const gen = P.dataAge();
    if (!gen) return true;
    return Date.now() - new Date(gen).getTime() > (hours || 24) * 3600000;
  };

  /* ------------------------------------------------------- hero artwork */
  const ART = new Set(window.OWCS_HERO_ART || []);
  const ROLE_HUE = { Tank: 210, Damage: 4, Support: 145 };
  P.roleHue = (role) => (role in ROLE_HUE ? ROLE_HUE[role] : 265);
  P.heroInitials = (h) =>
    String((h && h.name) || "?").replace(/[^A-Za-z0-9. ]/g, "")
      .split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();

  /* The face inside a portrait cell. Official presentation art when we
     have it (so an operator recognises a hero instantly), otherwise a
     role-tinted monogram — a designed fallback, never a broken image and
     never a guess at who this is. */
  P.heroFace = (hero, px) => {
    px = px || 62;
    if (hero && ART.has(hero.id)) {
      const base = "assets/img/heroes/official/" + encodeURIComponent(hero.id) + "/";
      const file = px <= 40 ? "icon.webp" : "portrait.webp";
      return '<img src="' + base + file + '" alt="" loading="lazy" decoding="async"' +
        ' width="' + px + '" height="' + px + '" data-fallback="' + esc(P.heroInitials(hero)) + '">';
    }
    return '<span class="hero-mono" style="--mono-h:' + P.roleHue(hero && hero.role) + '"' +
      ' aria-hidden="true">' + esc(P.heroInitials(hero)) + "</span>";
  };
  P.heroArtwork = (heroId) =>
    ART.has(heroId) ? "assets/img/heroes/official/" + encodeURIComponent(heroId) + "/artwork.webp" : null;

  /* --------------------------------------------------- team accent hues */
  const TEAM_HUES = {
    qadsiah: 43, twis: 275, cr: 203, zeta: 152, falcons: 88,
    gen: 38, nrg: 12, ssg: 320, quick: 190,
  };
  const hashHue = (str) => {
    let h = 0;
    const s = String(str || "");
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
    return h;
  };
  P.teamHue = (id) => (id in TEAM_HUES ? TEAM_HUES[id] : hashHue(id));

  const CREST = "M12 1 H36 L47 12 V30 L24 47 L1 30 V12 Z";
  P.teamCrest = (team) => {
    const id = team ? team.id : "tbd";
    const code = (team && team.code) || "?";
    const hue = P.teamHue(id);
    const fs = code.length >= 4 ? 12 : code.length === 3 ? 14 : 17;
    const gid = "cg-" + String(id).replace(/[^a-z0-9-]/gi, "");
    return '<svg class="crest" viewBox="0 0 48 48" aria-hidden="true" focusable="false">' +
      '<defs><radialGradient id="' + gid + '" cx="50%" cy="30%" r="75%">' +
      '<stop offset="0%" stop-color="hsl(' + hue + ' 46% 24%)"/>' +
      '<stop offset="100%" stop-color="hsl(232 30% 8%)"/></radialGradient></defs>' +
      '<path d="' + CREST + '" fill="url(#' + gid + ')" stroke="hsl(' + hue + ' 52% 44%)" stroke-width="1.3"/>' +
      '<text x="24" y="25" text-anchor="middle" dominant-baseline="central"' +
      ' font-family="Saira Condensed, Arial Narrow, sans-serif" font-weight="700"' +
      ' font-size="' + fs + '" fill="hsl(' + hue + ' 70% 78%)">' + esc(code) + "</text></svg>";
  };

  /* --------------------------------------------------------- components */
  P.teamPlate = (teamOrId, opt) => {
    opt = opt || {};
    const t = typeof teamOrId === "string" ? P.team(teamOrId) : teamOrId;
    const size = opt.size ? " team--" + opt.size : "";
    const win = opt.win ? " team--win" : "";
    if (!t) {
      return '<span class="team' + size + '"><span class="team__logo">' +
        P.teamCrest(null) + '</span><span class="team__name">' +
        esc(opt.tbd || "Not identified") + "</span></span>";
    }
    const mark = t.logoUrl
      ? '<img src="' + esc(t.logoUrl) + '" alt="" loading="lazy" decoding="async"' +
        ' data-fallback="' + esc(t.code || "?") + '">'
      : P.teamCrest(t);
    const inner = '<span class="team__logo">' + mark + "</span>" +
      '<span class="team__name">' + esc(opt.short ? (t.code || t.name) : t.name) + "</span>";
    if (opt.link) {
      return '<a class="team' + size + win + '" href="team.html?id=' + encodeURIComponent(t.id) +
        '">' + inner + "</a>";
    }
    return '<span class="team' + size + win + '">' + inner + "</span>";
  };

  P.heroTile = (heroId, opt) => {
    opt = opt || {};
    const h = P.hero(heroId);
    const size = opt.size ? " hero--" + opt.size : "";
    const px = opt.size === "xs" ? 28 : opt.size === "sm" ? 40 : opt.size === "lg" ? 84 : 62;
    const body = '<span class="hero__face">' + P.heroFace(h, px) + "</span>" +
      '<span class="hero__name">' + esc(h.name) + "</span>" +
      '<span class="visually-hidden">' + esc(h.name) + (h.role ? ", " + esc(h.role) : "") + "</span>";
    const attrs = ' data-role="' + esc(h.role || "") + '" title="' + esc(h.name) +
      (h.role ? " — " + esc(h.role) : "") + '"';
    if (opt.link) {
      return '<a class="hero' + size + '" href="hero.html?id=' + encodeURIComponent(h.id) + '"' +
        attrs + ">" + body + "</a>";
    }
    return '<span class="hero' + size + '"' + attrs + ">" + body + "</span>";
  };

  P.compStrip = (heroIds, opt) =>
    '<span class="comp' + ((opt && opt.tight) ? " comp--tight" : "") + '">' +
    (heroIds || []).map((h) => P.heroTile(h, opt)).join("") + "</span>";

  /* Confidence is a bar, not a sentence. Bands match the pipeline's own
     auto-publish floor so the colour means the same thing the gate does. */
  P.confBand = (v) => (v == null ? "unknown" : v >= 0.9 ? "high" : v >= 0.75 ? "medium" : "low");
  P.confMeter = (v, opt) => {
    opt = opt || {};
    if (v == null) {
      return '<span class="conf" data-band="unknown"><span class="conf__bar"></span>' +
        '<span>not scored</span></span>';
    }
    const pct = Math.max(0, Math.min(100, Math.round(v * 100)));
    return '<span class="conf' + (opt.lg ? " conf--lg" : "") + '" data-band="' + P.confBand(v) +
      '" title="Detector confidence ' + pct + '%">' +
      '<span class="conf__bar"><span class="conf__fill" style="width:' + pct + '%"></span></span>' +
      "<span>" + pct + "%</span></span>";
  };

  /* One state vocabulary for the whole product. Internal machine states
     map onto five words a person can act on. */
  const STATE_WORDS = {
    published: { label: "Published", kind: "published",
      say: "Approved and live on the public pages." },
    review: { label: "Needs review", kind: "review",
      say: "The detector finished — a person has to confirm it." },
    working: { label: "Processing", kind: "working",
      say: "Working through the video right now." },
    queued: { label: "Queued", kind: "queued",
      say: "Accepted and waiting its turn." },
    blocked: { label: "Blocked", kind: "blocked",
      say: "Stopped on something a person has to fix." },
    /* Indigo, the colour this system already reserves for "a machine said
       so". A found broadcast is exactly that: the scan located it on an
       official channel and nobody has looked at it yet. */
    found: { label: "Found automatically", kind: "detected",
      say: "The scan found this broadcast. Nothing has been read from it yet." },
    ignored: { label: "Not a match broadcast", kind: "queued",
      say: "Scored as something other than a match broadcast — kept so the "
        + "archive is complete, never offered as data." },
  };
  P.STATE_WORDS = STATE_WORDS;
  P.stateChip = (state, textOverride) => {
    const s = STATE_WORDS[state] || STATE_WORDS.queued;
    return '<span class="chip" data-state="' + esc(s.kind) + '" title="' + esc(s.say) + '">' +
      '<span class="dot' + (state === "working" ? " pulse" : "") + '"></span>' +
      esc(textOverride || s.label) + "</span>";
  };

  P.scorePlate = (a, b, winner) => {
    if (a == null && b == null)
      return '<span class="score score--none">No score recorded</span>';
    return '<span class="score" aria-label="Score ' + esc(a == null ? "unknown" : a) +
      " to " + esc(b == null ? "unknown" : b) + '">' +
      '<span class="' + (winner === "a" ? "win" : "") + '">' + esc(a == null ? "–" : a) + "</span>" +
      '<span class="sep">:</span>' +
      '<span class="' + (winner === "b" ? "win" : "") + '">' + esc(b == null ? "–" : b) + "</span></span>";
  };

  P.empty = (glyph, title, hint) =>
    '<div class="empty" role="status"><span class="empty__glyph" aria-hidden="true">' + glyph +
    '</span><span class="empty__title">' + esc(title) + "</span>" +
    (hint ? '<span class="empty__hint">' + hint + "</span>" : "") + "</div>";

  const ICONS = {
    info: '<path d="M12 8h.01M11 12h1v4h1" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2" fill="none"/>',
    warn: '<path d="M12 3 22 20H2Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M12 9v5M12 17h.01" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    error: '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M9 9l6 6M15 9l-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    ok: '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="m8 12.5 2.6 2.6L16 9.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
  };
  P.note = (kind, title, body, actions) =>
    '<div class="note" data-kind="' + esc(kind) + '" role="' +
    (kind === "error" ? "alert" : "note") + '">' +
    '<svg class="note__ic" viewBox="0 0 24 24" aria-hidden="true">' + (ICONS[kind] || ICONS.info) + "</svg>" +
    "<div>" + (title ? "<strong>" + esc(title) + "</strong>" : "") + (body || "") + "</div>" +
    (actions ? '<div class="note__actions">' + actions + "</div>" : "") + "</div>";

  P.breadcrumbs = (items) =>
    '<nav class="breadcrumbs" aria-label="Breadcrumb">' + items.map((it, i) => {
      const last = i === items.length - 1;
      const seg = it.href && !last
        ? '<a href="' + esc(it.href) + '">' + esc(it.label) + "</a>"
        : "<span" + (last ? ' aria-current="page"' : "") + ">" + esc(it.label) + "</span>";
      return seg + (last ? "" : '<span class="sep" aria-hidden="true">›</span>');
    }).join("") + "</nav>";

  P.diag = (summary, bodyHtml) =>
    '<details class="diag"><summary>' + esc(summary) + '</summary>' +
    '<div class="diag__body">' + bodyHtml + "</div></details>";

  P.dl = (pairs) =>
    "<dl>" + pairs.filter((p) => p && p[1] != null && p[1] !== "")
      .map((p) => "<dt>" + esc(p[0]) + "</dt><dd>" + esc(p[1]) + "</dd>").join("") + "</dl>";

  /* --------------------------------------------------------- URL + DOM */
  P.qs = (key) => new URL(location.href).searchParams.get(key);
  P.setQs = (kv) => {
    const u = new URL(location.href);
    Object.keys(kv).forEach((k) => {
      const v = kv[k];
      if (v == null || v === "" || v === "all") u.searchParams.delete(k);
      else u.searchParams.set(k, v);
    });
    history.replaceState(null, "", u);
  };
  P.$ = (sel, root) => (root || document).querySelector(sel);
  P.$$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));
  P.mount = (id, html) => {
    const el = document.getElementById(id);
    if (!el) return null;
    el.innerHTML = html;
    if (P.observeReveals) P.observeReveals(el);
    return el;
  };

  /* A broken image must never leave a hole. Capture-phase because `error`
     does not bubble. */
  document.addEventListener("error", (e) => {
    const img = e.target;
    if (!(img instanceof HTMLImageElement) || !img.dataset.fallback) return;
    const span = document.createElement("span");
    span.className = "hero-mono";
    span.textContent = img.dataset.fallback;
    img.replaceWith(span);
  }, true);

  /* -------------------------------------------------------------- tabs */
  P.initTabs = (root, opt) => {
    opt = opt || {};
    const tabs = P.$$('[role="tab"]', root);
    const panels = tabs.map((t) => document.getElementById(t.getAttribute("aria-controls")));
    const select = (tab, push) => {
      tabs.forEach((t, i) => {
        const on = t === tab;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
        if (panels[i]) panels[i].hidden = !on;
      });
      if (push !== false && opt.key) P.setQs({ [opt.key]: tab.dataset.tab });
      if (opt.onChange) opt.onChange(tab.dataset.tab);
    };
    tabs.forEach((t) => {
      t.addEventListener("click", () => select(t));
      t.addEventListener("keydown", (e) => {
        const i = tabs.indexOf(t);
        let next = null;
        if (e.key === "ArrowRight") next = tabs[(i + 1) % tabs.length];
        if (e.key === "ArrowLeft") next = tabs[(i - 1 + tabs.length) % tabs.length];
        if (e.key === "Home") next = tabs[0];
        if (e.key === "End") next = tabs[tabs.length - 1];
        if (next) { e.preventDefault(); next.focus(); select(next); }
      });
    });
    let initial = tabs[0];
    if (opt.key) {
      const want = P.qs(opt.key);
      const found = tabs.filter((t) => t.dataset.tab === want)[0];
      if (found) initial = found;
    }
    if (initial) select(initial, false);
    return { select: (name) => {
      const t = tabs.filter((x) => x.dataset.tab === name)[0];
      if (t) select(t);
    } };
  };

  /* ------------------------------------------------------------- toast */
  P.toast = (message, kind) => {
    let host = document.querySelector(".toasts");
    if (!host) {
      host = document.createElement("div");
      host.className = "toasts";
      host.setAttribute("role", "status");
      host.setAttribute("aria-live", "polite");
      document.body.appendChild(host);
    }
    const t = document.createElement("div");
    t.className = "toast";
    t.dataset.kind = kind || "info";
    t.textContent = message;
    host.appendChild(t);
    setTimeout(() => t.remove(), 5200);
  };

  P.copy = (text, okMessage) => {
    const done = () => P.toast(okMessage || "Copied to the clipboard", "ok");
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, () => P.toast("Could not copy — select and copy manually", "error"));
      return;
    }
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); done(); } catch (e) { P.toast("Could not copy", "error"); }
    ta.remove();
  };

  /* ---- optional platform APIs, guarded --------------------------------
     `window.matchMedia` is not universally present: it is missing in
     jsdom, in some embedded webviews, and in several headless renderers
     and screenshot tools. Calling it unguarded at the top of a module —
     which shell.js did — throws before a single line of that module
     runs, so the header, nav, search and footer silently never build and
     the page renders as bare unstyled sections. A progressive-enhancement
     query must never be able to do that, so every read goes through here
     and defaults to "no, the user did not ask for this". */
  P.media = (query) => {
    try { return !!(window.matchMedia && window.matchMedia(query).matches); }
    catch (e) { return false; }
  };
  P.onMedia = (query, handler) => {
    try {
      if (!window.matchMedia) return;
      const mq = window.matchMedia(query);
      if (mq.addEventListener) mq.addEventListener("change", handler);
      else if (mq.addListener) mq.addListener(handler);
    } catch (e) { /* optional API; never fatal */ }
  };

  /* ---- the guide layer ------------------------------------------------
     Product requirement: any technical step keeps a walkthrough within
     reach, and the walkthrough has to be unmistakable rather than a
     footnote. Two builders plus a delegated copy handler cover every
     case on the site.

     THE PAGE NEVER EXECUTES A COMMAND. On the hosted copy there is no
     server behind a static site; on a local copy the tracker's own API
     handles real actions. A guide builds the exact command and hands it
     over — pretending otherwise is the one thing this project refuses
     to do everywhere else, and it would be no more acceptable here. */
  P.cmd = (command, note) =>
    '<div class="cmdline"><code>' + esc(command) + "</code>" +
    '<button type="button" class="cmdline__copy" data-copy="' + esc(command) + '">Copy</button>' +
    (note ? '<p class="cmdline__note">' + note + "</p>" : "") + "</div>";

  P.guide = (title, intro, steps, opt) => {
    opt = opt || {};
    const body = (steps || []).map((s) => {
      const state = s.done ? ' data-done="1"' : (s.blocked ? ' data-blocked="1"' : "");
      const parts = [];
      if (s.body) parts.push('<p class="gs-body">' + s.body + "</p>");
      if (s.command) parts.push(P.cmd(s.command, s.commandNote));
      if (s.note) parts.push('<p class="gs-body dim">' + s.note + "</p>");
      return "<li" + state + '><div><p class="gs-title">' + esc(s.title) + "</p>" +
        parts.join("") + "</div></li>";
    }).join("");
    return '<details class="guide"' + (opt.open ? " open" : "") +
      (opt.id ? ' id="' + esc(opt.id) + '"' : "") + ">" +
      "<summary>" + esc(title) + "</summary>" +
      '<div class="guide__body">' +
        (intro ? "<p>" + intro + "</p>" : "") +
        '<ol class="guide-steps">' + body + "</ol>" +
      "</div></details>";
  };

  /* One delegated listener covers every command block on the page, now
     and after any re-render, without each page script wiring its own. */
  document.addEventListener("click", (e) => {
    const btn = e.target.closest && e.target.closest(".cmdline__copy[data-copy]");
    if (!btn) return;
    P.copy(btn.dataset.copy, "Command copied — run it in your terminal");
    btn.textContent = "Copied";
    btn.dataset.copied = "1";
    setTimeout(() => { btn.textContent = "Copy"; delete btn.dataset.copied; }, 2200);
  });

  P.download = (filename, text, mime) => {
    const blob = new Blob([text], { type: mime || "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
})();
