/* =====================================================================
   OWCS Comp Tracker — app/maplog.js
   Every spreadsheet map as one row, newest first, with everything a
   reader needs to place it: the day and start time, the event and round,
   both teams and their comps, the bans, who won, and the link to the
   map's own page (map.html?ext=…).

   Reads window.OWCS_SHEET_TIERS (assets/data/sheet_tiers.v1.js):
     matches[i]  one series — d (day), dc (how the day is known), t (start
                 time from Liquipedia, ISO with the event's offset), r, s, f,
                 bo, a, b, lbl
     maps[j]     one map — m (index into matches), id, map, mode, o (map
                 number), w ("a" / "b" / null), a / b: { c: first-listed comp,
                 h: [[hero, share]] every hero listed, bn: bans }

   The time shown is the SERIES start (the sheet does not time maps); a
   day with no source behind it says "est.". Used by the hero, team and
   map pages (page-profiles.js, page-map.js).
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  if (!P) return;
  const esc = P.esc;
  const T = () => window.OWCS_SHEET_TIERS;

  let cache = null;
  function build() {
    const t = T();
    if (!t || !t.maps) return { list: [], byId: new Map(), byMatch: new Map() };
    const matches = t.matches || [];
    const list = t.maps.map((mp) => {
      const m = matches[mp.m] || {};
      return { id: mp.id, map: mp.map, mode: mp.mode || null, n: mp.o || null, w: mp.w || null,
        mi: mp.m, match: m, series: m.id, d: m.d || null, t: m.t || null, dc: m.dc || null,
        teamA: m.a || null, teamB: m.b || null,
        a: mp.a || { c: null, h: [], bn: [] }, b: mp.b || { c: null, h: [], bn: [] } };
    });
    /* Newest first: the day, then the start time, then sheet order (a
       later card in the sheet was played later), then the map number. */
    list.sort((x, y) => cmp(y.d, x.d) || cmp(y.t, x.t) || (y.mi - x.mi) || ((y.n || 0) - (x.n || 0)));
    const byId = new Map(), byMatch = new Map();
    list.forEach((e) => {
      byId.set(e.id, e);
      if (!byMatch.has(e.mi)) byMatch.set(e.mi, []);
      byMatch.get(e.mi).push(e);
    });
    byMatch.forEach((arr) => arr.sort((x, y) => (x.n || 0) - (y.n || 0)));
    return { list: list, byId: byId, byMatch: byMatch };
  }
  const cmp = (a, b) => (a === b ? 0 : a == null ? -1 : b == null ? 1 : a < b ? -1 : 1);
  const db = () => cache || (cache = build());

  const L = {};
  L.all = () => db().list;
  L.byId = (id) => db().byId.get(id) || null;
  /* Every map of the series this map belongs to, in map order. */
  L.series = (e) => (e ? db().byMatch.get(e.mi) || [e] : []);
  L.href = (e) => "map.html?ext=" + encodeURIComponent(e.id);
  L.seriesHref = (e) => "series.html?id=" + encodeURIComponent(e.series);

  /* Who won, and each side as { team, comp, heroes, bans, won }. */
  L.side = (e, s) => {
    const x = e[s] || {};
    return { side: s, team: s === "a" ? e.teamA : e.teamB, comp: x.c || null,
      heroes: (x.h || []).map((p) => p[0]), shares: x.h || [], bans: x.bn || [],
      won: e.w === s ? true : e.w && e.w !== s && (e.w === "a" || e.w === "b") ? false : null };
  };
  L.winner = (e) => (e.w === "a" ? e.teamA : e.w === "b" ? e.teamB : null);

  /* Series score after every map of it the sheet records. */
  L.seriesScore = (e) => {
    let a = 0, b = 0;
    L.series(e).forEach((x) => { if (x.w === "a") a++; else if (x.w === "b") b++; });
    return [a, b];
  };

  /* Maps a hero was listed on, newest first, with the side it was on and
     how it was played: the only hero its player was listed with ("whole
     map"), listed first with others ("started, swapped off"), or listed
     after another ("swapped in"). The sheet lists heroes; it never times
     them, so these are the site's usual first-listed reading, not seconds. */
  L.forHero = (hero, opt) => {
    opt = opt || {};
    const out = [];
    L.all().forEach((e) => {
      if (opt.from && e.d && e.d < opt.from) return;
      if (opt.to && e.d && e.d > opt.to) return;
      /* One row per map; a mirror (both teams on the hero) is one row
         with `both`, the winner's side first. */
      const sides = ["a", "b"].map((s) => {
        const sh = (e[s].h || []).filter((p) => p[0] === hero)[0];
        if (!sh) return null;
        const inComp = (e[s].c || []).indexOf(hero) >= 0;
        return { side: s, share: sh[1], how: sh[1] >= 0.99 ? "whole" : inComp ? "started" : "swapped" };
      }).filter(Boolean);
      if (!sides.length) return;
      if (sides.length === 2 && e.w === "b") sides.reverse();
      out.push({ e: e, side: sides[0].side, share: sides[0].share, how: sides[0].how, both: sides.length === 2,
        hows: sides.map((x) => x.how) });
    });
    return out;
  };
  L.HOW = { whole: "Whole map", started: "Started, swapped off", swapped: "Swapped in" };

  /* Maps a team played, newest first. */
  L.forTeam = (team, opt) => {
    opt = opt || {};
    const out = [];
    L.all().forEach((e) => {
      if (opt.from && e.d && e.d < opt.from) return;
      if (opt.to && e.d && e.d > opt.to) return;
      if (e.teamA === team) out.push({ e: e, side: "a" });
      else if (e.teamB === team) out.push({ e: e, side: "b" });
    });
    return out;
  };

  /* ---------------------------------------------------------- when */
  /* { date, time, title }: the start time in the viewer's own zone when a
     source gave one, else the day (marked est. when it was estimated). */
  L.when = (e) => {
    const m = e.match || {};
    if (e.t) {
      const d = new Date(e.t);
      if (!isNaN(d)) {
        return { date: d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" }),
          time: d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", timeZoneName: "short" }),
          est: false, title: "Series start, from Liquipedia, in your time zone. The spreadsheet does not time each map." };
      }
    }
    if (!e.d) return { date: "Date unknown", time: "", est: true, title: "No source dates this series." };
    const d = new Date(e.d + "T12:00:00Z");
    const est = m.dc !== "liquipedia";
    return { date: d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }),
      time: "", est: est,
      title: est ? "Estimated from the official schedule (stage, week and day); no source lists this series' day."
        : "The day Liquipedia lists this series on; no start time was listed." };
  };
  L.whenHtml = (e, opt) => {
    const w = L.when(e);
    return '<span class="ml-when" title="' + esc(w.title) + '"><span class="ml-when__d">' + esc(w.date) + "</span>" +
      (w.time ? '<span class="ml-when__t">' + esc(w.time) + "</span>"
        : w.est ? '<span class="ml-when__t dim">est. day</span>' : (opt && opt.noTime ? "" : '<span class="ml-when__t dim">time not listed</span>')) +
      "</span>";
  };

  /* "Pacific Stage 3 · Third Place Match · Week 2 | Day 1" */
  L.eventLabel = (e) => {
    const U = P.tierUI;
    const m = e.match || {};
    const base = U ? U.matchLabel(m) : [m.s, m.f].filter(Boolean).join(" · ");
    return base + (m.bo ? " (Bo" + m.bo + ")" : "");
  };
  L.mapName = (e) => (e.map ? P.mapInfo(e.map).name || e.map : "Map not recorded");
  L.teamName = (id) => (P.tierUI ? P.tierUI.teamName(id) : P.anyTeamName ? P.anyTeamName(id) : id || "TBD");

  /* Hero faces, one optionally highlighted; never links (rows are links). */
  L.faces = (heroes, px, mark) => '<span class="ml-faces">' + (heroes || []).map((id) => {
    const h = P.hero(id);
    return '<span class="ml-face' + (id === mark ? " is-mark" : "") + '" title="' + esc(h.name) +
      (h.role ? " — " + esc(h.role) : "") + '" data-role="' + esc(h.role || "") + '">' + P.heroFace(h, px || 26) + "</span>";
  }).join("") + "</span>";
  const byRole = (hs) => (P.tierUI ? P.tierUI.byRole(hs) : hs);

  /* One side as: team · W/L · comp (first-listed five, else every hero
     listed) · bans. */
  L.sideHtml = (e, s, opt) => {
    opt = opt || {};
    const x = L.side(e, s);
    const heroes = byRole(x.comp || x.heroes);
    const res = x.won === true ? '<b class="cb-res cb-res--w" title="Won the map">W</b>'
      : x.won === false ? '<b class="cb-res cb-res--l" title="Lost the map">L</b>' : '<b class="cb-res" title="No winner recorded">–</b>';
    return '<span class="ml-side">' + res + '<span class="ml-side__team">' + esc(L.teamName(x.team)) + "</span>" +
      (heroes.length ? L.faces(heroes, opt.px || 26, opt.mark) : '<span class="dim small">no heroes listed</span>') +
      /* The marked hero was listed but is not in the first-listed five:
         it came on as a swap, so show it after the comp. */
      (opt.mark && x.comp && x.comp.indexOf(opt.mark) < 0 && x.heroes.indexOf(opt.mark) >= 0
        ? '<span class="ml-swap" title="Swapped in">⇄</span>' + L.faces([opt.mark], opt.px || 26, opt.mark) : "") +
      (x.bans.length ? '<span class="ml-bans" title="Banned by ' + esc(L.teamName(x.team)) + '"><span class="dim small">ban</span>' +
        L.faces(x.bans, 20) + "</span>" : "") + "</span>";
  };

  /* A whole row linking to the map page. opt.hero: the hero to mark and
     the side to put first; opt.team: the team to put first; opt.how. */
  L.rowHtml = (e, opt) => {
    opt = opt || {};
    const first = opt.side || "a", second = first === "a" ? "b" : "a";
    return '<li><a class="ml-row" href="' + esc(L.href(e)) + '">' +
      L.whenHtml(e) +
      '<span class="ml-map"><b>' + esc(L.mapName(e)) + "</b>" + (e.mode ? ' <span class="dim small">' + esc(e.mode) + "</span>" : "") +
        (e.n ? ' <span class="dim small">· map ' + e.n + "</span>" : "") +
        '<span class="ml-ev dim small">' + esc(L.eventLabel(e)) + "</span>" +
        (opt.how ? '<span class="ml-how" data-how="' + esc(opt.how) + '">' + esc(L.HOW[opt.how] || opt.how) +
          (opt.both ? " · both teams" : "") + "</span>" : "") +
      "</span>" +
      '<span class="ml-sides">' + L.sideHtml(e, first, { mark: opt.hero }) + L.sideHtml(e, second, { mark: opt.hero }) + "</span>" +
      '<span class="ml-go" aria-hidden="true">→</span></a></li>';
  };

  /* A paged list: `items` [{e, side, how}], `size` rows at a time. */
  L.listHtml = (id, items, opt) => {
    opt = opt || {};
    const size = opt.size || 25;
    const shown = Math.min(items.length, opt.shown || size);
    return '<ol class="ml-list" id="' + esc(id) + '" data-size="' + size + '">' +
      items.slice(0, shown).map((it) => L.rowHtml(it.e, { side: it.side, how: it.how, both: it.both, hero: opt.hero })).join("") + "</ol>" +
      (items.length > shown ? '<p class="u-mt-3"><button type="button" class="btn btn--sm btn--ghost" data-ml-more="' + esc(id) + '">Show ' +
        Math.min(size, items.length - shown) + " more · " + (items.length - shown) + " left</button></p>" : "");
  };
  /* The "show more" button re-renders the list it names from `get()`. */
  const pagers = {};
  L.page = (id, get, opt) => { pagers[id] = { get: get, opt: opt || {}, shown: (opt && opt.size) || 25 }; };
  L.repaint = (id) => {
    const pg = pagers[id];
    const host = document.getElementById(id + "-host");
    if (!pg || !host) return;
    host.innerHTML = L.listHtml(id, pg.get(), Object.assign({}, pg.opt, { shown: pg.shown }));
  };
  document.addEventListener("click", (ev) => {
    const b = ev.target.closest && ev.target.closest("[data-ml-more]");
    if (!b) return;
    const pg = pagers[b.dataset.mlMore];
    if (!pg) return;
    pg.shown += (pg.opt.size || 25);
    L.repaint(b.dataset.mlMore);
  });

  P.mapLog = L;
})();
