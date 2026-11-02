/* =====================================================================
   OWCS Comp Tracker — app/page-compbook.js
   The Comps page's comp book: every five-hero comp the community
   spreadsheet records, filterable, each with everything worth knowing
   about it (app/compbook.js does the maths). Also the spreadsheet half
   of the "By map" tab: what teams run on each map.

   Spreadsheet data only, and labelled as such: a comp here is the five
   heroes a team's players are listed with first — an appearance, not
   seconds played. The broadcast-read comps stay in their own section
   (page-stats.js); the two sources are never added together.

   State lives in the URL so a view can be linked:
     ?tab=comps&cr=region&cs=stage&cm=map&cmo=mode
       &ct=team&ch=hero&cmin=N&csort=played|win|tier|recent
   The time window is the site-wide range (app/timerange.js: ?t=…), shown
   in the bar pinned under the header, 14 days unless changed.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const T = window.OWCS_SHEET_TIERS;
  const CB = P && P.compBook;
  const U = P && P.tierUI;
  if (!P || !T || !CB || !U) return;
  const esc = P.esc;
  const pct = U.pct;

  const REGIONS = [["", "All regions"], ["na", "North America"], ["emea", "EMEA"], ["korea", "Korea"],
    ["japan", "Japan"], ["china", "China"], ["pacific", "Pacific"], ["asia", "Asia championship"],
    ["international", "International LANs"]];
  const TR = P.timeRange;
  const SORTS = [["played", "Most played"], ["win", "Best adjusted win"], ["tier", "Tier score"], ["recent", "Most recent"]];
  const MINS = [["1", "Every comp"], ["2", "Run 2+ times"], ["3", "Run 3+ times"], ["5", "Run 5+ times"], ["10", "Run 10+ times"]];
  const PAGE = 30;
  const DEFAULTS = { cr: "", cm: "", cmo: "", ct: "", ch: "", cmin: "2", csort: "played" };
  const MORE_KEYS = ["cmo", "ct", "cmin"];
  /* An older link's ?cs=<stage> becomes that stage's time range. */
  if (P.qs("cs") && TR) { TR.set({ t: "stage:" + P.qs("cs") }); P.setQs({ cs: null }); }

  const state = {
    cr: P.qs("cr") || "", cm: P.qs("cm") || "",
    cmo: P.qs("cmo") || "", ct: P.qs("ct") || "", ch: P.qs("ch") || "",
    cmin: P.qs("cmin") || "2", csort: P.qs("csort") || "played",
  };
  let result = null;
  let shown = PAGE;

  const opts = (over) => {
    const s = Object.assign({}, state, over || {});
    const w = TR ? TR.buildOpts({ region: s.cr || null }) : {};
    return { asOf: w.asOf || null, from: w.from || null, region: s.cr || null,
      map: s.cm || null, mode: s.cmo || null, team: s.ct || null, hero: s.ch || null,
      minMaps: Number(s.cmin || 1), sort: s.csort };
  };

  /* ---------------------------------------------------------- helpers */
  const n = (v) => Number(v).toLocaleString("en-US");
  const plural = (k, word) => k + " " + word + (k === 1 ? "" : "s");
  const heroName = (id) => P.hero(id).name;
  const face = (id, px) => {
    const h = P.hero(id);
    return '<a class="cb-face" href="hero.html?id=' + encodeURIComponent(h.id) + '" title="' + esc(h.name) +
      '" data-role="' + esc(h.role || "") + '">' + P.heroFace(h, px || 30) + "</a>";
  };
  const faces = (heroes, px) => '<span class="cb-faces">' + U.byRole(heroes).map((h) => face(h, px)).join("") + "</span>";
  /* The same portraits, not links: for inside an element that is itself a link. */
  const facesPlain = (heroes, px) => '<span class="cb-faces">' + U.byRole(heroes).map((id) => {
    const h = P.hero(id);
    return '<span class="cb-face" title="' + esc(h.name) + '" data-role="' + esc(h.role || "") + '">' + P.heroFace(h, px || 30) + "</span>";
  }).join("") + "</span>";
  const teamLink = (id) => {
    const t = P.team(id);
    return '<a class="cb-team" href="team.html?id=' + encodeURIComponent(id) + '">' +
      (t ? '<span class="cb-team__mark">' + P.teamMark(t, 18) + "</span>" : "") +
      "<span>" + esc(U.teamName(id)) + "</span></a>";
  };
  const wl = (w, l) => w + "–" + l;
  const longRange = () => { const r = TR ? TR.resolve() : null; return !r || r.days == null || r.days > 30; };
  const rate = (w, l) => (w + l ? pct(w / (w + l)) : "—");
  const mapLabel = (id) => U.mapName(id);
  const stageName = (s) => U.STAGE_NAMES[s] || s;
  const dateLabel = (iso) => (iso ? U.fmtDate(iso) : "—");
  const gameLine = (g) => {
    const m = (T.matches || [])[g.match] || {};
    const res = g.res === 1 ? '<b class="cb-res cb-res--w">W</b>' : g.res === 0 ? '<b class="cb-res cb-res--l">L</b>'
      : '<b class="cb-res">–</b>';
    return '<li class="cb-game">' + res +
      '<span class="cb-game__who">' + esc(U.teamName(g.team)) + ' <span class="dim">vs</span> ' + esc(U.teamName(g.opp)) + "</span>" +
      '<span class="cb-game__map">' + esc(mapLabel(g.map)) + "</span>" +
      '<span class="cb-game__when dim small">' + esc(U.matchLabel(m)) + " · " + esc(dateLabel(g.d)) + "</span>" +
      (g.oppComp ? '<span class="cb-game__opp" title="What the other team ran">' + faces(g.oppComp, 20) + "</span>" : '<span></span>') +
      '<a class="cb-game__go small" href="series.html?id=' + encodeURIComponent(g.series) + '">Series →</a></li>';
  };
  const momentLine = (g, word) => {
    if (!g) return "";
    const m = (T.matches || [])[g.match] || {};
    return '<p class="cb-moment"><span class="cb-moment__k">' + word + "</span> " +
      esc(U.teamName(g.team)) + (g.res === 1 ? " beat " : " lost to ") + esc(U.teamName(g.opp)) + " on " +
      esc(mapLabel(g.map)) + ' <span class="dim">— ' + esc(U.matchLabel(m)) + ", " + esc(dateLabel(g.d)) +
      (g.e != null ? ", " + pct(g.e) + " to win going in" : "") + "</span> " +
      '<a class="small" href="series.html?id=' + encodeURIComponent(g.series) + '">Series →</a></p>';
  };
  const heroCounts = (pairs, unit) => pairs.length
    ? '<ul class="cb-hcounts">' + pairs.map((p) => '<li>' + face(p[0], 26) + '<span>' + esc(heroName(p[0])) +
      '</span><b>' + p[1] + "</b>" + (unit ? '<span class="dim small">' + unit + "</span>" : "") + "</li>").join("") + "</ul>"
    : '<p class="dim small">None recorded.</p>';

  /* ------------------------------------------------------------ filters */
  function filterBar() {
    const F = CB.facets(T);
    const sel = (k, label, options) => '<label class="field"><span class="mx-label">' + esc(label) + "</span>" +
      '<select class="input" data-k="' + k + '">' + options.map((o) =>
        '<option value="' + esc(o[0]) + '"' + (String(state[k]) === String(o[0]) ? " selected" : "") + ">" + esc(o[1]) + "</option>").join("") +
      "</select></label>";
    const maps = Array.from(F.maps.keys()).sort((a, b) => mapLabel(a).localeCompare(mapLabel(b)))
      .map((id) => [id, mapLabel(id) + (F.maps.get(id) ? " · " + F.maps.get(id) : "")]);
    const teams = Array.from(F.teams.keys()).sort((a, b) => U.teamName(a).localeCompare(U.teamName(b)))
      .map((id) => [id, U.teamName(id)]);
    const heroes = F.heroes.slice().sort((a, b) => heroName(a).localeCompare(heroName(b))).map((id) => [id, heroName(id)]);
    /* The four questions people actually ask first — which map, which
       hero, which region, best or most played — stay in view. The rest
       fold away, and open by themselves when one of them is in use, so a
       filter is never hidden while it is changing the list. */
    const more = MORE_KEYS.filter((k) => String(state[k] || "") !== String(DEFAULTS[k] || "")).length;
    return '<form class="card mx-filters cb-filters" id="cb-filters" onsubmit="return false">' +
      '<div class="cb-fgrid">' +
      sel("cm", "Map", [["", "Every map"]].concat(maps)) +
      sel("ch", "Includes hero", [["", "Any hero"]].concat(heroes)) +
      sel("cr", "Region", REGIONS) +
      sel("csort", "Sort by", SORTS) +
      "</div>" +
      '<details class="cb-more-filters"' + (more ? " open" : "") + '><summary class="small">More filters' +
        (more ? ' <span class="cb-count">' + more + " on</span>" : "") + "</summary>" +
      '<div class="cb-fgrid u-mt-3">' +
      sel("cmo", "Mode", [["", "Every mode"]].concat(F.modes.slice().sort().map((m) => [m, m]))) +
      sel("ct", "Team", [["", "Every team"]].concat(teams)) +
      sel("cmin", "Show", MINS) +
      "</div>" +
      "</details>" +
      '<div class="row u-mt-3"><button type="button" class="btn btn--sm btn--quiet" id="cb-reset">Reset filters</button></div></form>';
  }

  function updateMore(form) {
    const sum = form.querySelector(".cb-more-filters > summary");
    if (!sum) return;
    const more = MORE_KEYS.filter((k) => String(state[k] || "") !== String(DEFAULTS[k] || "")).length;
    sum.innerHTML = "More filters" + (more ? ' <span class="cb-count">' + more + " on</span>" : "");
  }

  function wireFilters() {
    const form = document.getElementById("cb-filters");
    if (!form) return;
    form.addEventListener("change", (e) => {
      const k = e.target && e.target.dataset.k;
      if (!k) return;
      state[k] = e.target.value;
      P.setQs({ [k]: state[k] || null });
      shown = PAGE;
      updateMore(form);
      if (k === "cr" && TR) TR.setRegion("cb-range", state.cr);
      drawHeroes();
      draw();
      drawByMap();
    });
    document.getElementById("cb-reset").addEventListener("click", () => {
      Object.assign(state, DEFAULTS);
      P.setQs({ cr: null, cm: null, cmo: null, ct: null, ch: null, cmin: null, csort: null });
      form.querySelectorAll("select").forEach((s) => { s.value = state[s.dataset.k]; });
      updateMore(form);
      shown = PAGE;
      draw();
      drawByMap();
    });
  }

  /* -------------------------------------------------------------- rows */
  function row(c, i) {
    const om = c.offMirror;
    const topMaps = c.maps.slice(0, 2).map((m) => '<span class="cb-chip">' + esc(mapLabel(m.id)) + ' <b>' + m.n + "</b></span>").join("");
    const topTeams = c.teams.slice(0, 2).map((t) => '<span class="cb-chip">' + esc(U.teamName(t.id)) + " <b>" + t.n + "</b></span>").join("");
    return '<details class="cb-row" data-key="' + esc(c.key) + '">' +
      '<summary class="cb-sum">' +
      '<span class="cb-rank mono">' + (i + 1) + "</span>" +
      '<span class="cb-tier"><span class="tr-badge" data-tier="' + esc(c.tier || "") + '" title="' +
        esc(c.tier ? "Tier " + c.tier : "Not tiered: run fewer than " + ((T.weights || {}).compMinMaps || 2) + " times") + '">' +
        esc(c.tier || "–") + "</span></span>" +
      '<span class="cb-lineup">' + faces(c.heroes, 32) + "</span>" +
      '<span class="cb-stat"><b>' + c.n + "</b><small>maps · " + pct(c.share, 1) + "</small></span>" +
      '<span class="cb-stat"' + (c.und ? ' title="' + c.und + ' more with no winner recorded"' : "") + "><b>" +
        wl(c.wins, c.losses) + "</b><small>" + pct(c.rawWin) + " won</small></span>" +
      '<span class="cb-stat" title="Wins and losses against every other comp — mirrors left out, since a mirror is one win and one loss for the same comp">' +
        "<b>" + (om.w + om.l ? wl(om.w, om.l) : "—") + "</b><small>" + (om.w + om.l ? pct(om.rate) + " vs other comps" : "only mirrors") + "</small></span>" +
      '<span class="cb-stat" title="Weighted by event, bracket and the opponent\'s power going in, and pulled toward 50% on small samples — the Hero tiers page\'s scale">' +
        "<b>" + pct(c.adjWin, 1) + "</b><small>adj. win</small></span>" +
      '<span class="cb-where">' + topMaps + "</span>" +
      '<span class="cb-who">' + topTeams + "</span>" +
      '<span class="cb-when small"><span>' + esc(dateLabel(c.last)) + '</span><span class="dim">' +
        /* "N in the last 30 days" only says something when the range is
           longer than that; inside a 14-day range it would contradict it. */
        (c.recent && longRange() ? c.recent + " in last 30 days" : "last seen") + "</span></span>" +
      "</summary>" +
      '<div class="cb-detail" data-for="' + esc(c.key) + '"></div></details>';
  }

  function detail(c) {
    const om = c.offMirror;
    const record = '<section class="cb-box"><h3>Record</h3>' +
      '<dl class="cb-dl">' +
      "<dt>Played</dt><dd><b>" + plural(c.n, "map") + "</b>, " + pct(c.share, 1) + " of every line-up in this view</dd>" +
      "<dt>Result</dt><dd><b>" + wl(c.wins, c.losses) + "</b> (" + pct(c.rawWin) + ")" + (c.und ? ", " + c.und + " with no winner recorded" : "") + "</dd>" +
      "<dt>Vs other comps</dt><dd><b>" + (om.w + om.l ? wl(om.w, om.l) + "</b> (" + pct(om.rate) + ")" : "—</b>") +
        (c.mirrors ? ' · <span class="dim">' + plural(c.mirrors, "mirror map") + " left out</span>" : "") + "</dd>" +
      "<dt>Adjusted win</dt><dd><b>" + pct(c.adjWin, 1) + "</b> " + '<span class="dim">(' + U.signed(c.value) + " wins vs expected)</span></dd>" +
      "<dt>Tier</dt><dd>" + (c.tier ? '<span class="tr-badge" data-tier="' + esc(c.tier) + '">' + esc(c.tier) + "</span> " +
        '<span class="dim small">graded across every comp in this view, on how often it is run and its adjusted win</span>'
        : '<span class="dim">Not tiered: run fewer than ' + ((T.weights || {}).compMinMaps || 2) + " times</span>") + "</dd>" +
      "<dt>Seen</dt><dd>" + esc(dateLabel(c.first)) + " – " + esc(dateLabel(c.last)) +
        (c.recent && longRange() ? ' · <b>' + c.recent + "</b> in the last 30 days" : "") + ' <span class="dim small">(estimated dates)</span></dd>' +
      "<dt>Regions</dt><dd>" + c.regions.map((r) => esc(U.REGION_SHORT[r[0]] || stageName(r[0])) + " " + r[1]).join(" · ") + "</dd>" +
      "</dl></section>";

    const maps = '<section class="cb-box"><h3>Where it is played</h3><table class="tbl tbl--compact cb-tbl"><thead><tr>' +
      '<th>Map</th><th class="num">Maps</th><th class="num">W–L</th><th class="num">Won</th></tr></thead><tbody>' +
      c.maps.map((m) => '<tr><td><a href="tiers.html?map=' + encodeURIComponent(m.id) + '&tab=comps">' + esc(mapLabel(m.id)) + "</a>" +
        (m.mode ? ' <span class="dim small">' + esc(m.mode) + "</span>" : "") + '</td><td class="num">' + m.n +
        '</td><td class="num">' + wl(m.w, m.l) + '</td><td class="num">' + rate(m.w, m.l) + "</td></tr>").join("") +
      "</tbody></table></section>";

    const teams = '<section class="cb-box"><h3>Who runs it</h3><table class="tbl tbl--compact cb-tbl"><thead><tr>' +
      '<th>Team</th><th class="num">Maps</th><th class="num">W–L</th><th class="num">Won</th></tr></thead><tbody>' +
      c.teams.slice(0, 12).map((t) => "<tr><td>" + teamLink(t.id) + '</td><td class="num">' + t.n +
        '</td><td class="num">' + wl(t.w, t.l) + '</td><td class="num">' + rate(t.w, t.l) + "</td></tr>").join("") +
      "</tbody></table>" + (c.teams.length > 12 ? '<p class="dim small">and ' + (c.teams.length - 12) + " more</p>" : "") + "</section>";

    const vs = '<section class="cb-box"><h3>Against other comps</h3>' +
      (c.vs.length ? '<ul class="cb-vs">' + c.vs.map((v) => "<li>" + faces(v.heroes, 24) +
        '<span class="cb-vs__n">' + plural(v.n, "map") + "</span><b>" + wl(v.w, v.l) + "</b></li>").join("") + "</ul>"
        : '<p class="dim small">No opponent line-up recorded in full.</p>') +
      (c.mirrors ? '<p class="dim small u-mt-3">Plus ' + c.mirrors + " mirror match" + (c.mirrors === 1 ? "" : "es") + ", where both teams ran it.</p>" : "") +
      "</section>";

    const bans = '<section class="cb-box"><h3>Bans around it</h3>' +
      '<p class="cb-sub">The other team banned</p>' + heroCounts(c.bansFaced) +
      '<p class="cb-sub cb-sub--gap">Its own team banned</p>' + heroCounts(c.bansOwn) + "</section>";

    const swaps = '<section class="cb-box"><h3>Swaps</h3>' +
      '<p class="dim small">Heroes its players are also listed with, second or third — the swaps the line-up made. ' +
      "Weighted by list order.</p>" + heroCounts(c.swaps) + "</section>";

    const moments = (c.best || c.worst) ? '<section class="cb-box cb-box--wide"><h3>Moments</h3>' +
      momentLine(c.best, "Biggest win") + momentLine(c.worst, "Costliest loss") + "</section>" : "";

    const g = c.games;
    const games = '<section class="cb-box cb-box--wide"><h3>Every game <span class="sample">' + g.length + "</span></h3>" +
      '<ol class="cb-games">' + g.slice(0, 10).map(gameLine).join("") + "</ol>" +
      (g.length > 10 ? '<details class="cb-more"><summary class="small">Show the other ' + (g.length - 10) + "</summary>" +
        '<ol class="cb-games">' + g.slice(10).map(gameLine).join("") + "</ol></details>" : "") + "</section>";

    return '<div class="cb-grid">' + record + maps + teams + vs + bans + swaps + moments + games + "</div>";
  }

  /* -------------------------------------------------------------- draw */
  function summary(r) {
    const scope = [];
    if (state.cr) scope.push((REGIONS.find((x) => x[0] === state.cr) || [0, state.cr])[1]);
    if (state.cm) scope.push(mapLabel(state.cm));
    if (state.cmo) scope.push(state.cmo);
    if (state.ct) scope.push(U.teamName(state.ct));
    if (state.ch) scope.push("with " + heroName(state.ch));
    const when = TR ? TR.resolve({ region: state.cr || null }).label.toLowerCase().replace(/^last/, "the last") : "the whole season";
    return '<p class="cb-summary"><b>' + n(r.distinct) + "</b> distinct comps across <b>" + n(r.coveredMaps) +
      "</b> team line-ups on <b>" + n(r.maps) + "</b> maps" + (scope.length ? " · " + esc(scope.join(" · ")) : "") +
      " · " + esc(when) + '. <span class="dim">' + n(r.repeated) + " were run more than once; showing " +
      n(r.comps.length) + ".</span></p>";
  }

  function draw() {
    const host = document.getElementById("cb-list");
    if (!host) return;
    result = CB.build(T, opts());
    const rows = result.comps;
    const head = '<div class="cb-head" aria-hidden="true"><span>#</span><span>Tier</span><span>Comp</span>' +
      '<span>Played</span><span>Record</span><span>Vs others</span><span>Adj. win</span><span>Maps</span>' +
      "<span>Teams</span><span>Last seen</span></div>";
    host.innerHTML = summary(result) + (rows.length
      ? '<div class="cb-list u-mt-4">' + head + rows.slice(0, shown).map(row).join("") + "</div>" +
        (rows.length > shown ? '<div class="row u-mt-4"><button type="button" class="btn btn--ghost" id="cb-more">Show ' +
          Math.min(PAGE, rows.length - shown) + " more · " + (rows.length - shown) + " left</button></div>" : "")
      : P.empty("◇", "No comp matches these filters", "Widen the time range, lower the minimum, or reset the filters.") +
        (TR ? TR.widenHtml() : ""));
    if (TR) TR.note("cb-range", "<b>" + n(result.distinct) + "</b> comps · <b>" + n(result.maps) + "</b> maps");
    const more = document.getElementById("cb-more");
    if (more) more.addEventListener("click", () => { shown += PAGE; draw(); });
  }

  /* Detail panels are built when opened: 400 comps x 8 boxes is a lot
     of DOM nobody asked for. */
  function wireDetails(root) {
    root.addEventListener("toggle", (e) => {
      const d = e.target;
      if (!d.classList || !d.classList.contains("cb-row") || !d.open) return;
      const box = d.querySelector(".cb-detail");
      if (!box || box.dataset.built) return;
      const c = result && result.comps.find((x) => x.key === d.dataset.key);
      if (!c) return;
      box.innerHTML = detail(c);
      box.dataset.built = "1";
    }, true);
  }

  /* ------------------------------------------------------------ by map */
  /* The comp book for one map, keeping every other filter in view. */
  const compsLink = (map) => {
    const q = new URLSearchParams(location.search);
    q.set("tab", "comps");
    q.set("cm", map);
    return "?" + q.toString();
  };
  function drawByMap() {
    const host = document.getElementById("cb-bymap");
    if (!host) return;
    const F = CB.facets(T);
    const base = opts({ cm: "", cmin: "1", csort: "played" });
    const cards = Array.from(F.maps.keys()).map((id) => {
      const r = CB.build(T, Object.assign({}, base, { map: id }));
      return { id: id, r: r };
    }).filter((x) => x.r.comps.length).sort((a, b) => b.r.teamMaps - a.r.teamMaps || mapLabel(a.id).localeCompare(mapLabel(b.id)));
    if (!cards.length) { host.innerHTML = P.empty("◇", "No comp matches these filters", "Widen the time range or reset the filters on the Comps tab.") + (TR ? TR.widenHtml() : ""); return; }
    const line = (c) => '<li class="cb-mline">' + faces(c.heroes, 26) + '<span class="cb-mline__n">' + plural(c.n, "map") + "</span>" +
      "<b>" + wl(c.wins, c.losses) + '</b><span class="dim small">' + pct(c.adjWin) + " adj.</span></li>";
    host.innerHTML = '<div class="cb-mapgrid">' + cards.map((x) => {
      const top = x.r.comps.slice(0, 3);
      const topKeys = new Set(top.map((c) => c.key));
      const winner = x.r.comps.filter((c) => c.n >= 3 && c.decided >= 3 && !topKeys.has(c.key))
        .sort((a, b) => b.adjWin - a.adjWin)[0];
      return '<section class="card cb-mapcard"><div class="cb-mapcard__hd"><h3>' + esc(mapLabel(x.id)) + "</h3>" +
        '<span class="dim small">' + esc(F.maps.get(x.id) || "") + " · " + plural(x.r.teamMaps, "line-up") + "</span></div>" +
        '<p class="cb-sub">Most run</p><ol class="cb-mlist">' + top.map(line).join("") + "</ol>" +
        (winner ? '<p class="cb-sub cb-sub--gap">Winning most</p><ol class="cb-mlist">' + line(winner) + "</ol>" : "") +
        '<a class="sec-link small" href="' + esc(compsLink(x.id)) + '">All ' +
        plural(x.r.distinct, "comp") + " on " + esc(mapLabel(x.id)) + " →</a></section>";
    }).join("") + "</div>";
  }

  /* -------------------------------------------------------------- boot */
  function mount() {
    const host = document.getElementById("comp-book");
    if (host) {
      host.innerHTML = '<div class="cb-top u-mt-4"><span class="dim small">Every line-up the community spreadsheet records: ' +
        "who was listed, not seconds played. Tap a comp to open it.</span>" + U.sourceBadge() + "</div>" +
        filterBar() + '<div id="cb-list" class="u-mt-4"></div>';
      wireFilters();
      wireDetails(host);
      draw();
      openLinked();
      const heroes = document.getElementById("p-heroes");
      if (heroes && P.tierStats) {
        heroes.insertAdjacentHTML("afterbegin", '<div class="sec-head u-mt-5"><h2>Heroes in this time range</h2>' +
          U.sourceBadge() + '</div><div id="cb-heroes"></div>');
        drawHeroes();
      }
      if (TR) {
        TR.mount("cb-range", { region: state.cr || null });
        TR.onChange(() => { shown = PAGE; draw(); drawByMap(); drawHeroes(); });
      }
    }
    const bm = document.getElementById("cb-bymap-wrap");
    if (bm) {
      bm.innerHTML = '<div class="sec-head u-mt-5"><h2>What teams run on each map</h2>' + U.sourceBadge() + "</div>" +
        '<p class="dim small">From the community spreadsheet, using the Comps tab\'s window, stage, region, mode, team and hero ' +
        "filters. \"Winning most\" is the best adjusted win among comps run at least three times.</p>" +
        '<div id="cb-bymap" class="u-mt-4"></div>';
      drawByMap();
    }
  }

  /* ------------------------------------------------------ heroes tab
     Every hero in the time range, by role: tier, pick share, adjusted win,
     W–L, ban rate, and the arrow against the window of the same length
     just before. Same maths as the home page and Hero tiers (tierstats). */
  function drawHeroes() {
    const host = document.getElementById("cb-heroes");
    const S = P.tierStats;
    if (!host || !S || !TR) return;
    const roles = U.roles();
    const r = TR.resolve({ region: state.cr || null });
    const cur = S.compute(T, roles, { asOf: r.to, from: r.from || null, region: state.cr || null });
    const prev = r.prev && r.prev.from >= ((T.dates || {}).min || "")
      ? S.compute(T, roles, { asOf: r.prev.to, from: r.prev.from, region: state.cr || null }) : null;
    const before = new Map(prev ? prev.heroTiers("*").map((x) => [x.id, x.pickShare]) : []);
    const B = cur.bucket("*");
    if (!B || !B.maps) {
      host.innerHTML = P.empty("◇", "No pro maps in this time range", "Widen it to see heroes.") + TR.widenHtml();
      return;
    }
    const rows = cur.heroTiers("*");
    const arrow = (x) => {
      if (!prev) return "";
      const b = before.get(x.id);
      if (b == null) return '<span class="trend" data-dir="new" title="Not picked in the window before">NEW</span>';
      const d = Math.round((x.pickShare - b) * 100);
      if (Math.abs(d) < 2) return '<span class="trend" data-dir="flat">±' + Math.abs(d) + "</span>";
      return '<span class="trend" data-dir="' + (d > 0 ? "up" : "down") + '">' + (d > 0 ? "▲" : "▼") + " " + Math.abs(d) + "</span>";
    };
    const table = (role) => {
      const list = rows.filter((x) => x.role === role).sort((a, b) => (a.lowSample ? 1 : 0) - (b.lowSample ? 1 : 0) || (b.score || 0) - (a.score || 0));
      return '<section class="cb-box"><h3>' + esc(role) + "</h3>" +
        '<table class="tbl tbl--compact cb-tbl cb-htbl"><thead><tr><th>Hero</th><th>Tier</th><th class="num">Pick</th>' +
        '<th class="num">Adj. win</th><th class="num">Ban</th><th class="num" aria-label="Pick change">±</th></tr></thead><tbody>' +
        list.map((x) => {
          const h = P.hero(x.id);
          return '<tr><td><a class="cb-team" href="hero.html?id=' + encodeURIComponent(h.id) + '">' + P.heroFace(h, 24) +
            "<span>" + esc(h.name) + '</span></a></td><td><span class="tr-badge" data-tier="' + esc(x.tier || "") + '">' +
            esc(x.tier || "–") + '</span></td><td class="num">' + pct(x.pickShare) + '</td><td class="num">' +
            (x.decided ? pct(x.adjWin, 1) : "—") + '</td><td class="num">' + pct(x.banShare) + '</td><td class="num">' + arrow(x) + "</td></tr>";
        }).join("") + "</tbody></table></section>";
    };
    host.innerHTML = '<p class="cb-summary">Every hero in <b>' + n(B.maps) + "</b> pro maps · " +
      esc(r.label.toLowerCase().replace(/^last/, "the last")) + (state.cr ? " · " + esc((REGIONS.find((x) => x[0] === state.cr) || [0, state.cr])[1]) : "") +
      '. <span class="dim">Pick = share of team line-ups listing the hero; adj. win weighs each map by event, bracket and the ' +
      'opponent\'s power; ± is pick change against the window before. <a href="tiers.html">Map by map on Hero tiers →</a></span></p>' +
      (B.maps < 20 ? '<p class="dim small">Only ' + B.maps + " maps — a small sample moves a lot." + TR.widenHtml() + "</p>" : "") +
      '<div class="cb-grid cb-grid--heroes u-mt-4">' + ["Tank", "Damage", "Support"].map(table).join("") + "</div>";
  }

  /* --------------------------------------------- deep link to a comp
     ?cc=<comp key> opens that comp in the list (and pages far enough to
     show it), so a comp named on a team or hero page is one tap away
     from its full record. */
  function openLinked() {
    const want = P.qs("cc");
    if (!want || !result) return;
    const i = result.comps.findIndex((c) => c.key === want);
    if (i < 0) return;
    if (i >= shown) { shown = Math.ceil((i + 1) / PAGE) * PAGE; draw(); }
    const el = document.querySelector('.cb-row[data-key="' + (window.CSS && CSS.escape ? CSS.escape(want) : want) + '"]');
    if (!el) return;
    el.open = true;
    requestAnimationFrame(() => el.scrollIntoView({ block: "start" }));
  }

  /* ------------------------------------------- the short list, elsewhere
     A compact "what they run" list for team and hero pages: the most-run
     comps for a filter, each linking to its full record in the comp book.
     `filter` is compbook.build's options (team, hero, …). */
  const bookLink = (filter, key) => {
    const q = new URLSearchParams();
    q.set("tab", "comps");
    if (filter.team) q.set("ct", filter.team);
    if (filter.hero) q.set("ch", filter.hero);
    q.set("cmin", "1");
    if (key) q.set("cc", key);
    if (TR) { const t = TR.get(); if (t.t !== TR.DEFAULT) { q.set("t", t.t); if (t.tf) { q.set("tf", t.tf); q.set("tt", t.tt); } } }
    return "stats.html?" + q.toString();
  };
  P.compBookSummary = (filter, opt) => {
    opt = opt || {};
    const w = TR ? TR.buildOpts() : {};
    const r = CB.build(T, Object.assign({ minMaps: 1, sort: "played", asOf: w.asOf || null, from: w.from || null }, filter));
    if (!r.comps.length) {
      return P.empty("◇", "Nothing in this time range", "No full line-up is recorded " +
        (TR ? TR.resolve().label.toLowerCase().replace(/^last/, "in the last") : "here") + ".") + (TR ? TR.widenHtml() : "");
    }
    const top = r.comps.slice(0, opt.limit || 6);
    const topKeys = new Set(top.map((c) => c.key));
    const best = r.comps.filter((c) => c.n >= 3 && c.decided >= 3 && !topKeys.has(c.key))
      .sort((a, b) => b.adjWin - a.adjWin)[0];
    const line = (c) => '<li><a class="cb-pick" href="' + esc(bookLink(filter, c.key)) + '" aria-label="' +
      esc(U.byRole(c.heroes).map(heroName).join(", ") + ": " + plural(c.n, "map") + ", " + wl(c.wins, c.losses)) + '">' +
      facesPlain(c.heroes, 30) +
      '<span class="cb-pick__n">' + plural(c.n, "map") + "</span>" +
      "<b>" + wl(c.wins, c.losses) + '</b><span class="cb-pick__adj">' + pct(c.adjWin) + " adj.</span>" +
      '<span class="cb-pick__go" aria-hidden="true">→</span></a></li>';
    return '<div class="cb-summary-box">' +
      '<p class="cb-summary">' + plural(r.coveredMaps, "line-up") + " · <b>" + n(r.distinct) + "</b> different comps" +
      (r.repeated ? " · " + n(r.repeated) + " run more than once" : "") + "</p>" +
      '<ol class="cb-picks u-mt-3">' + top.map(line).join("") + "</ol>" +
      (best ? '<p class="cb-sub cb-sub--gap">Winning most</p><ol class="cb-picks">' + line(best) + "</ol>" : "") +
      '<p class="u-mt-4"><a class="btn btn--sm btn--ghost" href="' + esc(bookLink(filter)) + '">All ' +
        plural(r.distinct, "comp") + " in the comp book →</a></p></div>";
  };

  P.compBookPage = { mount: mount, state: state };
})();
