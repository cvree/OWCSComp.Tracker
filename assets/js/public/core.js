/* =====================================================================
   OWCS Comp Tracker — public/core.js
   Shared data access + formatting + component renderers for the public
   site. Vanilla JS, no framework. Everything renders from
   window.OWCS_PUBLIC — nothing fan-facing is hard-coded in page HTML.
   ===================================================================== */
(function () {
  "use strict";
  const D = window.OWCS_PUBLIC || null;
  const P = (window.OWCS_PUB = window.OWCS_PUB || {});
  P.data = D;

  /* -------- escaping ------------------------------------------------ */
  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
  P.esc = esc;

  /* -------- lookups ------------------------------------------------- */
  const byId = (arr) => {
    const m = new Map();
    (arr || []).forEach((x) => m.set(x.id, x));
    return m;
  };
  P.idx = {
    teams: byId(D && D.teams),
    heroes: byId(D && D.heroes),
    maps: byId(D && D.mapsCatalog),
    tournaments: byId(D && D.tournaments),
    matches: byId(D && D.matches),
    runs: byId(D && D.captureRuns),
    regions: byId(D && D.regions),
  };
  P.team = (id) => P.idx.teams.get(id) || null;
  P.hero = (id) => P.idx.heroes.get(id) || { id, name: id, role: "" };
  P.mapInfo = (id) => P.idx.maps.get(id) || { id, name: id, mode: "" };
  P.tournament = (id) => P.idx.tournaments.get(id) || null;
  P.match = (id) => P.idx.matches.get(id) || null;
  P.run = (id) => P.idx.runs.get(id) || null;
  P.regionName = (id) => (P.idx.regions.get(id) || { name: id }).name;
  P.matchesOf = (tid) => (D ? D.matches.filter((m) => m.tournamentId === tid) : []);
  P.roundsOf = (tid) => {
    if (!D) return [];
    const all = (D.bracketRounds || []).concat(D.extraRounds || []);
    return all.filter((r) => r.tournamentId === tid);
  };
  P.bracketNodesOf = (roundIds) =>
    (D ? (D.bracketMatches || []).filter((b) => roundIds.includes(b.roundId)) : []);

  /* Approved public comp review states — THE credibility rule.
     Anything else (needs-review, rejected, missing) never renders on a
     fan page. Manual snapshots override the cv rows they correct. */
  P.APPROVED_REVIEW = ["reviewed", "auto-high"];
  P.publicComps = (filter) => {
    if (!D) return [];
    const overridden = new Set(
      (D.compSnapshots || []).filter((c) => c.overridesId).map((c) => c.overridesId)
    );
    return (D.compSnapshots || []).filter((c) => {
      if (!P.APPROVED_REVIEW.includes(c.reviewStatus)) return false;
      if (c.source !== "cv" && c.source !== "manual") return false; // FACEIT can never supply comps
      if (overridden.has(c.id)) return false;                        // manual wins over cv
      if (filter && !filter(c)) return false;
      return true;
    });
  };

  /* -------- time ---------------------------------------------------- */
  P.fmtLocal = (iso, opts) => {
    if (!iso) return "TBD";
    const d = new Date(iso);
    if (isNaN(d)) return "TBD";
    return d.toLocaleString(undefined, opts || {
      weekday: "short", month: "short", day: "numeric",
      hour: "numeric", minute: "2-digit",
    });
  };
  P.fmtDate = (iso) => {
    if (!iso) return "TBD";
    const d = new Date(iso);
    return isNaN(d) ? "TBD" : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  };
  P.fmtRange = (a, b) => {
    if (!a) return "Dates TBD";
    const da = new Date(a), db = b ? new Date(b) : null;
    const o = { month: "short", day: "numeric" };
    if (!db || isNaN(db)) return da.toLocaleDateString(undefined, o) + ", " + da.getFullYear();
    const sameYear = da.getFullYear() === db.getFullYear();
    return da.toLocaleDateString(undefined, o) + " – " +
      db.toLocaleDateString(undefined, o) + (sameYear ? ", " + db.getFullYear() : "");
  };
  P.fmtRel = (iso) => {
    if (!iso) return "";
    const ms = new Date(iso).getTime();
    if (isNaN(ms)) return "";
    const diff = Date.now() - ms;
    const abs = Math.abs(diff);
    const units = [[86400000, "d"], [3600000, "h"], [60000, "m"]];
    for (const [ms1, label] of units) {
      if (abs >= ms1) {
        const n = Math.round(abs / ms1);
        return diff >= 0 ? `${n}${label} ago` : `in ${n}${label}`;
      }
    }
    return diff >= 0 ? "just now" : "moments away";
  };
  P.fmtOffset = (s) => {
    if (s == null) return "";
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return (h ? h + ":" : "") + String(m).padStart(h ? 2 : 1, "0") + ":" + String(sec).padStart(2, "0");
  };
  /* stale = generatedAt older than `hours` (default 24) */
  P.isStale = (hours) => {
    if (!D || !D.meta || !D.meta.generatedAt) return true;
    const age = Date.now() - new Date(D.meta.generatedAt).getTime();
    return age > (hours || 24) * 3600000;
  };

  /* -------- component renderers (return HTML strings) --------------- */
  P.badgeRegion = (id) =>
    `<span class="badge badge--region" data-region="${esc(id)}"><span class="b-dot" aria-hidden="true"></span>${esc(P.regionName(id))}</span>`;
  P.badgeTier = (t) =>
    t ? `<span class="badge badge--tier" data-tier="${esc(t)}">Tier ${esc(t)}</span>` : "";
  P.badgeSrc = (type) =>
    `<span class="badge badge--src" data-src="${esc(type)}">${esc(type)}</span>`;
  P.chipStatus = (st) => {
    const label = { upcoming: "Upcoming", live: "Live", completed: "Final", forfeit: "Forfeit" }[st] || st;
    return `<span class="chip" data-st="${esc(st)}">${esc(label)}</span>`;
  };
  P.CAPTURE_LABELS = {
    "needs-source": "Needs source", "queued": "Queued", "capturing": "Capturing",
    "needs-review": "Needs review", "verified": "Verified", "failed": "Failed",
  };
  P.chipCapture = (st) =>
    st ? `<span class="chip" data-cap="${esc(st)}" title="Capture pipeline: ${esc(P.CAPTURE_LABELS[st] || st)}">${esc(P.CAPTURE_LABELS[st] || st)}</span>` : "";

  const hue = (str) => {
    let h = 0;
    for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) % 360;
    return h;
  };
  P.teamPlate = (teamId, opt) => {
    opt = opt || {};
    const t = teamId ? P.team(teamId) : null;
    const size = opt.size ? ` team-plate--${opt.size}` : "";
    if (!t) {
      return `<span class="team-plate team-plate--tbd${size}">
        <span class="team-plate__logo" aria-hidden="true"><span>?</span></span>
        <span class="team-plate__name">${esc(opt.tbd || "TBD")}</span></span>`;
    }
    const win = opt.win ? " team-plate--win" : "";
    const logo = t.logoUrl
      ? `<img src="${esc(t.logoUrl)}" alt="" width="34" height="34" loading="lazy" data-img-fallback="${esc(t.code)}">`
      : `<span>${esc(t.code)}</span>`;
    const style = t.logoUrl ? "" : ` style="background:hsl(${hue(t.id)} 42% 72%)"`;
    return `<span class="team-plate${win}${size}" data-team="${esc(t.id)}">
      <span class="team-plate__logo"${style} aria-hidden="true">${logo}</span>
      <span class="team-plate__name">${esc(opt.short ? t.code : t.name)}</span></span>`;
  };
  P.heroTile = (heroId, opt) => {
    opt = opt || {};
    const h = P.hero(heroId);
    const initials = h.name.replace(/[^A-Za-z0-9. ]/g, "").split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
    const face = h.portraitUrl
      ? `<img src="${esc(h.portraitUrl)}" alt="" loading="lazy">`
      : esc(initials);
    return `<span class="hero-tile${opt.sm ? " hero-tile--sm" : ""}" data-role="${esc(h.role)}" title="${esc(h.name)} — ${esc(h.role)}">
      <span class="hero-tile__face">${face}<span class="hero-tile__role" aria-hidden="true"></span></span>
      <span class="hero-tile__name">${esc(h.name)}</span>
      <span class="visually-hidden">${esc(h.name)} (${esc(h.role)})</span></span>`;
  };
  P.heroStrip = (heroIds, opt) =>
    `<span class="hero-strip">${(heroIds || []).map((h) => P.heroTile(h, opt)).join("")}</span>`;
  P.scorePlate = (a, b, winner) => {
    const va = a == null ? "–" : a, vb = b == null ? "–" : b;
    return `<span class="score-plate" aria-label="score ${esc(va)} to ${esc(vb)}">
      <span class="s-a${winner === "a" ? " win" : ""}">${esc(va)}</span>
      <span class="s-sep">:</span>
      <span class="s-b${winner === "b" ? " win" : ""}">${esc(vb)}</span></span>`;
  };
  P.emptyState = (glyph, title, hint) =>
    `<div class="empty" role="status"><span class="e-glyph" aria-hidden="true">${glyph}</span>
      <span class="e-title">${esc(title)}</span>
      ${hint ? `<span class="e-hint">${hint}</span>` : ""}</div>`;
  P.breadcrumbs = (items) =>
    `<nav class="breadcrumbs" aria-label="Breadcrumb">${items.map((it, i) => {
      const last = i === items.length - 1;
      const seg = it.href && !last ? `<a href="${esc(it.href)}">${esc(it.label)}</a>` : `<span${last ? ' aria-current="page"' : ""}>${esc(it.label)}</span>`;
      return seg + (last ? "" : '<span class="bc-sep" aria-hidden="true">›</span>');
    }).join("")}</nav>`;

  /* map score detail, typed per mode */
  P.mapScoreDetail = (map, teamAId, teamBId) => {
    const d = map.scoreDetail;
    const tA = teamAId ? (P.team(teamAId) || {}).code || "A" : "A";
    const tB = teamBId ? (P.team(teamBId) || {}).code || "B" : "B";
    if (!d) {
      return map.live
        ? `<span class="map-detail"><span class="chip" data-st="live">Live</span><span class="dim">In progress — score pending.</span></span>`
        : `<span class="map-detail faint">Score detail unavailable.</span>`;
    }
    if (d.type === "control") {
      const rounds = (d.rounds || []).map((r, i) => {
        const aw = r.a >= 100 && r.a > r.b, bw = r.b >= 100 && r.b > r.a;
        return `<span class="ctrl-round"><span class="cr-bar">
          <span class="cr-a${aw ? " win" : ""}" style="width:${Math.min(50, r.a / 2)}%"></span>
          <span class="cr-b${bw ? " win" : ""}" style="width:${Math.min(50, r.b / 2)}%"></span></span>
          <span class="cr-nums"><span>${esc(tA)} ${r.a}%</span><span>${r.b}% ${esc(tB)}</span></span></span>`;
      }).join("");
      return `<span class="map-detail"><span class="md-k">Rounds</span></span><span class="ctrl-rounds">${rounds}</span>`;
    }
    if (d.type === "escort" || d.type === "hybrid") {
      const seg = (side, v) => v ? `<span><span class="md-k">${esc(side)}</span> ${v.points} pts · bank ${esc(v.timeBank)}</span>` : "";
      return `<span class="map-detail">${seg(tA, d.a)}${seg(tB, d.b)}${d.note ? `<span class="faint">${esc(d.note)}</span>` : ""}</span>`;
    }
    if (d.type === "push") {
      const pa = parseFloat(d.distanceA) || 0, pb = parseFloat(d.distanceB) || 0;
      const max = Math.max(pa, pb, 1);
      const bar = (code, v, n) =>
        `<span class="push-bar${n === Math.max(pa, pb) && pa !== pb ? " win" : ""}"><span>${esc(code)}</span>
         <span class="pb-track"><span class="pb-fill" style="width:${(n / max) * 100}%"></span></span>
         <span>${esc(v)}</span></span>`;
      return `<span class="push-bars">${bar(tA, d.distanceA, pa)}${bar(tB, d.distanceB, pb)}</span>`;
    }
    if (d.type === "flashpoint")
      return `<span class="map-detail"><span class="md-k">Points captured</span><span>${esc(tA)} ${d.capturesA} — ${d.capturesB} ${esc(tB)}</span></span>`;
    if (d.type === "clash")
      return `<span class="map-detail"><span class="md-k">Point progression</span><span>${esc(tA)} ${d.pointsA} — ${d.pointsB} ${esc(tB)}</span></span>`;
    return `<span class="map-detail faint">Score detail unavailable.</span>`;
  };

  /* -------- tabs (accessible, hash-synced) --------------------------- */
  P.initTabs = (root, opt) => {
    opt = opt || {};
    const tabs = Array.from(root.querySelectorAll('[role="tab"]'));
    const panels = tabs.map((t) => document.getElementById(t.getAttribute("aria-controls")));
    const select = (tab, push) => {
      tabs.forEach((t, i) => {
        const on = t === tab;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
        if (panels[i]) panels[i].hidden = !on;
      });
      if (push !== false && opt.hashKey) {
        const u = new URL(location.href);
        u.searchParams.set(opt.hashKey, tab.dataset.tab);
        history.replaceState(null, "", u);
      }
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
    if (opt.hashKey) {
      const want = new URL(location.href).searchParams.get(opt.hashKey);
      const found = tabs.find((t) => t.dataset.tab === want);
      if (found) initial = found;
    }
    if (initial) select(initial, false);
    return { select: (name) => { const t = tabs.find((x) => x.dataset.tab === name); if (t) select(t); } };
  };

  /* -------- URL state helpers --------------------------------------- */
  P.qs = () => new URL(location.href).searchParams;
  P.setQs = (kv) => {
    const u = new URL(location.href);
    Object.entries(kv).forEach(([k, v]) => {
      if (v == null || v === "" || v === "all") u.searchParams.delete(k);
      else u.searchParams.set(k, v);
    });
    history.replaceState(null, "", u);
  };

  /* -------- misc DOM ------------------------------------------------- */
  P.$ = (sel, root) => (root || document).querySelector(sel);
  P.$$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  /* safe image fallback: broken logo -> monogram */
  document.addEventListener("error", (e) => {
    const img = e.target;
    if (!(img instanceof HTMLImageElement) || !img.dataset.imgFallback) return;
    const span = document.createElement("span");
    span.textContent = img.dataset.imgFallback;
    img.replaceWith(span);
  }, true);
})();
