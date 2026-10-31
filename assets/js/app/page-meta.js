/* =====================================================================
   OWCS Comp Tracker — app/page-meta.js
   The meta explorer: one filter bar, seven views, two sources kept apart.

   Every view is computed by app/metastats.js from the same filtered list
   of maps. Three rules hold on every table:

     * a time-weighted number is VOD verified and says so; a count from the
       spreadsheet is a "spreadsheet observation" and never says "time";
     * every rate carries its sample size, and a small one says it is small;
     * wording is observational — "observed map-win share", "usage among
       eventual map winners" — never "this comp wins".

   Filters live in the URL, so a view can be linked.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const esc = P.esc;
  const M = P.metaStats;
  const pct = (v) => P.pctHonest(v);
  const ROLES = {};
  P.heroes().forEach((h) => { ROLES[h.id] = h.role; });
  const RULES = (((P.pub || {}).timeWeighting || {}).method || {}).archetypes || [];

  const KEYS = ["source", "event", "stage", "region", "patch", "map", "mode", "team", "opponent",
    "hero", "role", "from", "to", "cov", "min", "days", "half"];
  const state = {};
  KEYS.forEach((k) => { state[k] = P.qs(k) || ""; });

  let all = [];

  document.addEventListener("DOMContentLoaded", () => {
    all = P.metaEntries();
    P.mount("provenance", P.extProvenanceLine());
    renderFilters();
    renderAll();
    P.initTabs(document, { key: "tab" });
    document.addEventListener("click", (e) => {
      const b = e.target.closest("[data-scout-n]");
      if (b) { state.scoutN = b.dataset.scoutN; renderScout(current()); }
    });
  });

  /* ---------------------------------------------------------- filters */
  function filters() {
    return { source: state.source || "all", event: state.event, stage: state.stage, region: state.region,
      patch: state.patch, map: state.map, mode: state.mode, team: state.team, opponent: state.opponent,
      from: state.from, to: state.to, minCoverage: state.cov ? Number(state.cov) / 100 : 0 };
  }

  function current() {
    let list = M.filterEntries(all, filters());
    if (state.days) list = M.windowed(list, Number(state.days));
    if (state.hero) {
      list = list.filter((e) => {
        const v = e.vod && (e.vod.teams || []).some((t) => (t.heroes || []).some((h) => h.hero === state.hero));
        const s = e.sheet && (M.sheetHeroesFor(e, "a").concat(M.sheetHeroesFor(e, "b"))).indexOf(state.hero) >= 0;
        return v || s;
      });
    }
    return list;
  }

  function renderFilters() {
    const o = M.options(all);
    const sel = (key, label, values, fmt) => '<label class="field"><span class="mx-label">' + esc(label) +
      '</span><select class="input mf-select" data-key="' + key + '">' +
      '<option value="">All</option>' + values.map((v) => '<option value="' + esc(v[0] != null ? v[0] : v) + '"' +
        (String(state[key]) === String(v[0] != null ? v[0] : v) ? " selected" : "") + ">" +
        esc(fmt ? fmt(v) : (v[1] != null ? v[1] : v)) + "</option>").join("") + "</select></label>";
    const date = (key, label) => '<label class="field"><span class="mx-label">' + esc(label) +
      '</span><input class="input" type="date" data-key="' + key + '" value="' + esc(state[key]) + '"' +
      (o.minDate ? ' min="' + o.minDate + '"' : "") + (o.maxDate ? ' max="' + o.maxDate + '"' : "") + "></label>";
    const heroes = P.heroes().slice().sort((a, b) => a.name.localeCompare(b.name)).map((h) => [h.id, h.name]);
    const teams = o.teams.map((t) => [t, P.anyTeamName(t)]).sort((a, b) => a[1].localeCompare(b[1]));
    const maps = o.maps.map((m) => [m, P.mapInfo(m).name]).sort((a, b) => a[1].localeCompare(b[1]));
    document.getElementById("filters").innerHTML =
      '<div class="mx-grid">' +
      sel("source", "Source", [["vod", "VOD verified"], ["sheet", "Spreadsheet imported"], ["both", "Recorded by both"]]) +
      sel("event", "Event", o.events) +
      sel("stage", "Stage", o.stages) +
      sel("region", "Region", o.regions.map((r) => [r, P.regionName(r)])) +
      (o.patches.length ? sel("patch", "Patch", o.patches) : "") +
      sel("map", "Map", maps) +
      sel("mode", "Mode", o.modes) +
      sel("team", "Team", teams) +
      sel("opponent", "Opponent", teams) +
      sel("hero", "Hero", heroes) +
      sel("role", "Role", ["Tank", "Damage", "Support"]) +
      date("from", "From") + date("to", "To") +
      sel("days", "Window", [["30", "Last 30 days of data"], ["90", "Last 90 days"], ["180", "Last 180 days"], ["365", "Last 365 days"]]) +
      sel("half", "Recency weighting", [["30", "Half-life 30 days"], ["90", "Half-life 90 days"]]) +
      sel("cov", "Min VOD coverage", [["50", "50%"], ["80", "80%"], ["95", "95%"]]) +
      sel("min", "Min sample", [["2", "2 maps"], ["5", "5 maps"], ["10", "10 maps"], ["20", "20 maps"]]) +
      "</div>" +
      '<div class="row u-mt-4"><button type="button" class="btn btn--sm btn--ghost" id="reset">Reset filters</button>' +
      '<span class="dim small">Windows and recency weighting count back from the newest map in the data, ' +
      "not from today, so the same data always gives the same numbers.</span></div>";
    document.getElementById("filters").addEventListener("change", (e) => {
      const k = e.target.dataset.key;
      if (!k) return;
      state[k] = e.target.value;
      const qs = {};
      qs[k] = e.target.value;
      P.setQs(qs);
      renderAll();
    });
    document.getElementById("reset").addEventListener("click", () => {
      const qs = {};
      KEYS.forEach((k) => { state[k] = ""; qs[k] = ""; });
      P.setQs(qs);
      renderFilters();
      renderAll();
    });
  }

  /* ---------------------------------------------------------- summary */
  function renderAll() {
    const list = current();
    renderSummary(list);
    renderHeroes(list);
    renderComps(list);
    renderBans(list);
    renderMaps(list);
    renderMatchups(list);
    renderScout(list);
    renderList(list);
  }

  function renderSummary(list) {
    const vod = list.filter((e) => e.vod).length, sheet = list.filter((e) => e.sheet).length;
    const both = list.filter((e) => e.vod && e.sheet).length;
    const patches = new Set(list.map((e) => e.patch).filter(Boolean));
    const tile = (k, v, note, badge) => '<div class="stat"><span class="stat__k">' + esc(k) + " " + (badge || "") +
      '</span><span class="stat__v">' + v + '</span><span class="stat__note">' + esc(note) + "</span></div>";
    P.mount("summary", '<div class="grid grid--4">' +
      tile("Maps in view", list.length, "one per real map; a linked pair counts once") +
      tile("VOD timelines", vod, "maps with timed hero data", P.srcBadge("vod", "VOD")) +
      tile("Spreadsheet maps", sheet, "maps the sheet documents", P.srcBadge("sheet", "Sheet")) +
      tile("In both", both, "linked by reconciliation") + "</div>" +
      (patches.size > 1 && !state.patch ? P.note("warn", "Several patches are mixed in this view",
        "<p>" + esc(Array.from(patches).join(", ")) + ". Pick one in the Patch filter to avoid blending metas.</p>") : "") +
      (!patches.size ? '<p class="dim small u-mt-3">No patch is recorded for these maps, so patches cannot be separated — ' +
        "use the date filters to compare periods.</p>" : "") +
      (state.half ? '<p class="dim small">Recency weighting is on (half-life ' + esc(state.half) +
        " days): rates weight newer maps more. Raw counts are still shown beside them.</p>" : ""));
  }

  const minN = () => Number(state.min || 0);

  /* ----------------------------------------------------------- heroes */
  function renderHeroes(list) {
    const f = filters();
    const u = M.heroUsage(list, f, ROLES, { halfLife: Number(state.half || 0) });
    let rows = u.rows.filter((r) => (!state.role || r.role === state.role) && (!state.hero || r.hero === state.hero));
    rows = rows.filter((r) => Math.max(r.sheetCount, r.vodSeconds ? 1 : 0) >= 1 &&
      (minN() <= 1 || r.sheetCount >= minN() || (r.vodPresence != null && Math.round(r.vodPresence * u.vodTimelines) >= minN())));
    const host = document.getElementById("p-heroes");
    if (!rows.length) { host.innerHTML = P.empty("◇", "No hero data in this view", "Loosen the filters."); return; }
    host.innerHTML =
      '<div class="grid grid--2 u-mt-5">' +
      '<p class="small">' + P.srcBadge("vod") + " <b>Time-weighted</b> over " + u.vodTimelines +
        " team timeline" + (u.vodTimelines === 1 ? "" : "s") + " (" + P.fmtClock(u.vodKnownSeconds) +
        " known player-time). Share = hero seconds ÷ KNOWN seconds; unread time is excluded.</p>" +
      '<p class="small">' + P.srcBadge("sheet") + " <b>Spreadsheet observation</b> over " + u.sheetUnits +
        " team-maps with heroes listed. Appearance rate = team-maps listing the hero ÷ team-maps with any hero listed. " +
        "Not time.</p></div>" +
      '<div class="table-wrap u-mt-4"><table class="tbl"><thead><tr><th>Hero</th><th>Role</th>' +
      '<th class="num" title="Seconds on the field across VOD timelines">VOD time</th>' +
      '<th class="num" title="Hero seconds ÷ all known player-seconds in view">Share of known time</th>' +
      '<th class="num" title="Hero seconds ÷ known seconds of the same role">Share of role time</th>' +
      '<th class="num" title="Team timelines the hero appeared in ÷ all team timelines">VOD presence</th>' +
      '<th class="num" title="Team-maps the sheet lists the hero for">Sheet appearances</th>' +
      '<th class="num">Sheet appearance rate</th>' +
      '<th title="Of team-maps listing the hero with a recorded result, the share the team won. Observational.">Observed map-win share (sheet)</th>' +
      "</tr></thead><tbody>" + rows.map((r) => "<tr><td><span class=\"u-flex u-center u-gap-3\">" + P.heroTile(r.hero, { size: "xs", link: true }) +
        esc(P.hero(r.hero).name) + "</span></td><td>" + esc(r.role || "—") + '</td><td class="num mono">' + (r.vodSeconds ? P.fmtClock(r.vodSeconds) : "—") +
        '</td><td class="num">' + pct(r.vodShare) + '</td><td class="num">' + pct(r.vodRoleShare) +
        '</td><td class="num">' + pct(r.vodPresence) + '</td><td class="num">' + (r.sheetCount || "—") +
        '</td><td class="num">' + pct(r.sheetRate) + "</td><td>" + (r.sheetDecided ? pct(r.sheetWinShare) + " " : "") +
        P.sampleNote(r.sheetDecided, "decided team-map") + "</td></tr>").join("") +
      "</tbody></table></div>" +
      '<p class="dim small u-mt-3">Win shares describe what happened on the maps in view; they do not say a hero causes wins.</p>';
  }

  /* ------------------------------------------------------------ comps */
  function renderComps(list) {
    const f = filters();
    const vod = M.vodLineups(list, f).filter((r) => r.timelines >= (minN() || 1));
    const sheet = M.sheetComps(list, f).filter((r) => r.count >= (minN() || 1));
    const co = M.coOccurrence(list, f, 15);
    const host = document.getElementById("p-comps");
    host.innerHTML =
      '<section class="band"><div class="sec-head"><h2>Line-ups by verified duration</h2>' + P.srcBadge("vod") + "</div>" +
      (vod.length ? '<div class="stack">' + vod.slice(0, 12).map((r) => '<div class="card lu-card">' +
        P.compStrip(r.heroes, { size: "sm", link: true }) + '<div class="row u-mt-3">' +
        '<span class="chip chip--outline mono">' + P.fmtClock(r.seconds) + " fielded</span>" +
        '<span class="chip chip--outline">' + r.timelines + " team timeline" + (r.timelines === 1 ? "" : "s") + "</span>" +
        '<span class="chip chip--outline" title="Editorial label">' + esc(r.archetype) + "</span>" +
        '<span class="small">usage among eventual map winners: ' + pct(r.seconds ? r.winSeconds / r.seconds : null) + "</span>" +
        '<span class="row">' + r.teams.map((t) => P.anyTeamPlate(t, { size: "sm" })).join("") + "</span>" +
        "</div></div>").join("") + "</div>"
        : P.empty("◇", "No VOD timeline in this view", "Line-up durations exist only for maps read off the broadcast.")) +
      "</section>" +
      '<section class="band"><div class="sec-head"><h2>Most-listed composition snapshots</h2>' + P.srcBadge("sheet") + "</div>" +
      '<p class="dim small">The five heroes the spreadsheet lists first for a team (or a comp it writes in one cell). ' +
      "A snapshot of what was documented — not a starting comp, not a duration.</p>" +
      (sheet.length ? '<div class="table-wrap"><table class="tbl"><thead><tr><th>Composition</th><th class="num">Team-maps</th>' +
        "<th>Observed map-win share</th><th>Teams</th></tr></thead><tbody>" + sheet.slice(0, 20).map((r) =>
          "<tr><td>" + P.compStrip(r.heroes, { size: "xs", tight: true }) + '</td><td class="num">' + r.count + "</td><td>" +
          (r.decided ? pct(r.wins / r.decided) + " " : "") + P.sampleNote(r.decided, "decided map") + "</td><td>" +
          r.teams.slice(0, 4).map((t) => esc(P.anyTeamName(t))).join(", ") + (r.teams.length > 4 ? " +" + (r.teams.length - 4) : "") +
          "</td></tr>").join("") + "</tbody></table></div>"
        : P.empty("◇", "No complete five-hero listing in this view", "")) +
      "</section>" +
      '<section class="band"><div class="sec-head"><h2>Heroes listed together</h2>' + P.srcBadge("sheet") + "</div>" +
      (co.rows.length ? '<div class="table-wrap"><table class="tbl tbl--compact"><thead><tr><th>Pair</th><th class="num">Team-maps</th>' +
        '<th class="num">Rate</th></tr></thead><tbody>' + co.rows.map((r) => "<tr><td>" +
          P.compStrip(r.pair, { size: "xs", tight: true }) + '</td><td class="num">' + r.count + '</td><td class="num">' +
          pct(r.rate) + "</td></tr>").join("") + "</tbody></table></div>" +
        '<p class="dim small">Co-occurrence over ' + co.units + " team-maps with two or more heroes listed.</p>"
        : P.empty("◇", "No hero listings in this view", "")) + "</section>";
  }

  /* ------------------------------------------------------------- bans */
  function renderBans(list) {
    const b = M.bans(list, filters());
    const host = document.getElementById("p-bans");
    if (!b.units) {
      host.innerHTML = P.empty("◇", "No hero bans recorded in this view",
        "Bans come from the spreadsheet (and from FACEIT/broadcast facts once captured).");
      return;
    }
    const heroRows = (rows) => rows.slice(0, 10).map((r) => '<span class="scout-chip">' +
      P.heroTile(r.hero, { size: "xs" }) + '<span class="mono small">×' + r.count + "</span></span>").join("");
    host.innerHTML = '<p class="small u-mt-5">' + P.srcBadge("sheet") + " Over " + b.units +
      " map" + (b.units === 1 ? "" : "s") + " with bans recorded. Bans are listed under a team exactly as the sheet records them.</p>" +
      '<div class="table-wrap"><table class="tbl"><thead><tr><th>Hero</th><th class="num">Bans</th>' +
      '<th class="num">Share of maps</th></tr></thead><tbody>' + b.heroes.filter((r) => r.count >= (minN() || 1)).map((r) =>
        "<tr><td>" + P.heroTile(r.hero, { size: "xs", link: true }) + '</td><td class="num">' + r.count +
        '</td><td class="num">' + pct(r.rate) + "</td></tr>").join("") + "</tbody></table></div>" +
      '<div class="grid grid--2 u-mt-5"><div class="card"><h3>By map</h3>' + b.byMap.map((m) =>
        '<p class="label">' + esc(P.mapInfo(m.map).name) + '</p><div class="row">' + heroRows(m.heroes) + "</div>").join("") +
      '</div><div class="card"><h3>By team</h3>' + b.byTeam.map((t) =>
        '<p class="label">' + esc(P.anyTeamName(t.team)) + '</p><div class="row">' + heroRows(t.heroes) + "</div>").join("") +
      "</div></div>";
  }

  /* -------------------------------------------------------- maps */
  function renderMaps(list) {
    const rec = M.mapRecords(list, filters());
    const picks = M.mapPicks(list);
    const host = document.getElementById("p-maps");
    host.innerHTML =
      '<section class="band"><div class="sec-head"><h2>Map pool in view</h2></div>' +
      '<div class="table-wrap"><table class="tbl"><thead><tr><th>Map</th><th>Mode</th><th class="num">Played</th>' +
      '<th class="num">Picks recorded</th><th>Picker’s observed map-win share</th></tr></thead><tbody>' +
      picks.rows.map((r) => "<tr><td>" + esc(P.mapInfo(r.map).name) + "</td><td>" + esc(r.mode || "—") +
        '</td><td class="num">' + r.played + '</td><td class="num">' + r.picked + "</td><td>" +
        (r.pickerDecided ? pct(r.pickerWon / r.pickerDecided) + " " : "") + P.sampleNote(r.pickerDecided, "decided pick") +
        "</td></tr>").join("") + "</tbody></table></div></section>" +
      '<section class="band"><div class="sec-head"><h2>Team map records &amp; pick tendencies</h2></div>' +
      (rec.excludedConflicts ? P.note("info", rec.excludedConflicts + " map" + (rec.excludedConflicts === 1 ? "" : "s") +
        " left out of win counts", "<p>The VOD record and the spreadsheet disagree on who won, so neither result is counted.</p>") : "") +
      '<p class="dim small">Observed map-win share with its sample. Fewer than 5 decided maps is flagged ⚠ — that is an anecdote, not a rate.</p>' +
      '<div class="stack">' + rec.teams.filter((t) => !state.team || t.team === state.team).map((t) => {
        const rows = t.maps.filter((m) => m.played >= (minN() || 1));
        if (!rows.length) return "";
        return '<details class="card"><summary>' + P.anyTeamPlate(t.team, { size: "sm" }) + ' <span class="dim small">' +
          t.maps.reduce((n, m) => n + m.played, 0) + " maps</span></summary>" +
          '<div class="table-wrap"><table class="tbl tbl--compact"><thead><tr><th>Map</th><th class="num">Played</th>' +
          '<th class="num">W–L</th><th>Observed map-win share</th><th class="num">Own picks</th><th class="num">Opponent picks</th>' +
          "</tr></thead><tbody>" + rows.map((m) => "<tr><td>" + esc(P.mapInfo(m.map).name) + '</td><td class="num">' + m.played +
            '</td><td class="num">' + m.wins + "–" + m.losses + "</td><td>" + (m.decided ? pct(m.winShare) + " " : "") +
            P.sampleNote(m.decided, "decided map") + '</td><td class="num">' + m.picks + '</td><td class="num">' +
            m.pickedAgainst + "</td></tr>").join("") + "</tbody></table></div>" +
          (state.team ? teamCompPrefs(list, t.team) : "") + "</details>";
      }).join("") + "</div></section>";
  }

  function teamCompPrefs(list, team) {
    const rows = M.teamMapComps(list, team);
    return '<p class="label u-mt-4">Map-specific comp preferences</p>' + rows.map((m) =>
      '<div class="lu"><b class="small">' + esc(P.mapInfo(m.map).name) + "</b>" +
      (m.vod[0] ? P.srcBadge("vod", "VOD") + P.compStrip(m.vod[0].heroes, { size: "xs", tight: true }) +
        '<span class="mono small">' + P.fmtClock(m.vod[0].seconds) + "</span>" : "") +
      (m.sheet[0] ? P.srcBadge("sheet", "Sheet") + P.compStrip(m.sheet[0].heroes, { size: "xs", tight: true }) +
        '<span class="small">listed ×' + m.sheet[0].count + "</span>" : "") +
      (!m.vod[0] && !m.sheet[0] ? '<span class="dim small">no complete line-up recorded</span>' : "") + "</div>").join("");
  }

  /* --------------------------------------------------------- matchups */
  function renderMatchups(list) {
    const cells = M.matchups(list, ROLES, RULES);
    const host = document.getElementById("p-matchups");
    const grid = (src) => {
      const cs = cells.filter((c) => c.source === src);
      if (!cs.length) return P.empty("◇", "No map in view has both teams’ line-ups", "");
      const arch = Array.from(new Set(cs.map((c) => c.archetype).concat(cs.map((c) => c.opponent)))).sort();
      return '<div class="table-wrap"><table class="tbl tbl--compact matrix"><thead><tr><th>Archetype ↓ vs →</th>' +
        arch.map((a) => "<th>" + esc(a) + "</th>").join("") + "</tr></thead><tbody>" + arch.map((a) => "<tr><th scope=\"row\">" +
          esc(a) + "</th>" + arch.map((b) => {
            const c = cs.filter((x) => x.archetype === a && x.opponent === b)[0];
            return "<td>" + (c ? (c.decided ? pct(c.wins / c.decided) : "—") + ' <span class="dim small">n=' + c.decided + "</span>" : "") + "</td>";
          }).join("") + "</tr>").join("") + "</tbody></table></div>";
    };
    host.innerHTML = '<p class="dim small u-mt-5">Observed map-win share of the row archetype against the column archetype, ' +
      "with the number of decided maps. Archetypes are editorial labels from <code>config/comp_archetypes.json</code>, " +
      "applied to the tank hero — a grouping for reading, not a detection and not a cause.</p>" +
      '<section class="band"><div class="sec-head"><h2>From VOD timelines</h2>' + P.srcBadge("vod") + "</div>" +
      '<p class="dim small">Each team’s archetype is the one it fielded longest on the map.</p>' + grid("vod") + "</section>" +
      '<section class="band"><div class="sec-head"><h2>From spreadsheet listings</h2>' + P.srcBadge("sheet") + "</div>" +
      '<p class="dim small">Each team’s archetype is that of its first-listed five heroes.</p>' + grid("sheet") + "</section>";
  }

  /* ------------------------------------------------------------ scout */
  function renderScout(list) {
    const host = document.getElementById("p-scout");
    if (!state.team) {
      host.innerHTML = P.empty("◇", "Pick a team in the filters to scout it",
        "The report covers its last 5 or 10 maps from both sources, with map comfort, bans, hero and comp tendencies.");
      return;
    }
    const n = Number(state.scoutN || 10);
    host.innerHTML = '<div class="row u-mt-5"><span class="dim small">Window:</span>' +
      [5, 10, 20].map((k) => '<button type="button" class="btn btn--sm ' + (k === n ? "btn--primary" : "btn--ghost") +
        '" data-scout-n="' + k + '" aria-pressed="' + (k === n) + '">Last ' + k + " maps</button>").join("") + "</div>" +
      '<div class="u-mt-4">' + P.scoutingHtml(state.team, n, M.filterEntries(all, Object.assign({}, filters(),
        { team: "", opponent: state.opponent }))) + "</div>" +
      '<p class="u-mt-4"><a href="team.html?id=' + encodeURIComponent(state.team) + '">Open the team page →</a></p>';
  }

  /* ------------------------------------------------------------- list */
  function renderList(list) {
    const host = document.getElementById("p-list");
    if (!list.length) { host.innerHTML = P.empty("◇", "No map matches these filters", ""); return; }
    const shown = list.slice(0, 300);
    host.innerHTML = '<p class="dim small u-mt-5">' + list.length + " map" + (list.length === 1 ? "" : "s") +
      (list.length > shown.length ? " — showing the newest " + shown.length : "") + ".</p>" +
      '<div class="table-wrap"><table class="tbl tbl--compact"><thead><tr><th>Date</th><th>Event</th><th>Map</th>' +
      "<th>Teams</th><th>Winner</th><th>Sources</th></tr></thead><tbody>" + shown.map((e) =>
        '<tr><td class="mono small">' + esc(e.date || "—") + '</td><td class="small">' + esc(e.event || "—") +
        '</td><td><a href="' + P.mapPageHref(e) + '">' + esc(e.map ? P.mapInfo(e.map).name : (e.sheet && e.sheet.mapRaw) || "?") +
        "</a></td><td>" + esc(P.anyTeamName(e.teamA)) + " vs " + esc(P.anyTeamName(e.teamB)) + "</td><td>" +
        (e.winnerConflict ? P.srcBadge("conflict", "disagree") : esc(e.winner ? P.anyTeamName(e.winner) : "—")) +
        "</td><td>" + (e.vod ? P.srcBadge("vod", "VOD") : "") + (e.sheet ? P.srcBadge("sheet", "Sheet") : "") +
        (e.vod && e.sheet ? P.reconBadge(e.status) : "") + "</td></tr>").join("") + "</tbody></table></div>";
  }
})();
