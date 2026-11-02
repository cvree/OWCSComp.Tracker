/* =====================================================================
   OWCS Comp Tracker — app/extui.js
   The community spreadsheet on every public page, drawn the same way
   everywhere and never mixed into a VOD number.

     P.extCoverageCard()      matches / maps per region and stage, last
                              sync, link to the sheet, credit
     P.extHeadline()          the spreadsheet's headline tiles
     P.mountSeriesBrowser()   filterable series list (series.html, games.html)
     P.extHeroPanel(id)       hero.html: listing + ban counts by region/stage
     P.extTeamPanel(id)       team.html: record, maps, heroes, bans
     P.extTeamsTable()        teams.html: every team the sheet names

   Reads only window.OWCS_EXTERNAL_SUMMARY / _SERIES (through sources.js
   and the pure helpers in extstats.js). Every value carries the
   "Spreadsheet imported" badge; every rate states its sample; a listed
   hero is an appearance, never time; nothing here has a date.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  if (!P || !P.extStats) return;
  const E = P.extStats;
  const esc = P.esc;
  const S = () => P.extSummary || null;
  const R = () => P.extSeries || null;
  const pct = (v) => P.pctHonest(v);
  const plural = (n, w) => n + " " + w + (n === 1 ? "" : "s");
  const fmtN = (n) => (n == null ? "—" : Number(n).toLocaleString("en-US"));

  /* ---------------------------------------------------------- labels */
  const REGION = { na: "North America", emea: "EMEA", korea: "Korea", japan: "Japan", china: "China",
    pacific: "Pacific", asia: "Asia", international: "International", none: "No region (Pre-Season)" };
  const STAGE = { "pre-season": "Pre-Season Bootcamp", "stage-1": "Stage 1", "champions-clash": "Champions Clash",
    "stage-2": "Stage 2", "midseason-championship": "Midseason Championship", "stage-3": "Stage 3",
    "world-finals": "World Finals" };
  P.extRegionName = (r) => REGION[r == null ? "none" : r] || P.regionName(r);
  P.extStageName = (s) => STAGE[s] || s || "Stage not recorded";
  P.extMapName = (id, raw) => (id ? P.mapInfo(id).name : (raw || "Map not recorded"));

  /* A team the sheet names, linked to its profile whether or not the
     tracker's registry knows it (team.html handles both). */
  P.extTeamLink = (id, opt) => {
    const plate = P.anyTeamPlate(id, Object.assign({ size: "sm" }, opt || {}));
    return id ? '<a class="plain-link" href="team.html?id=' + encodeURIComponent(id) + '">' + plate + "</a>" : plate;
  };

  /* Load a script once (for the per-map detail shards and friends). */
  const loading = {};
  P.loadScriptOnce = (src) => loading[src] || (loading[src] = new Promise((resolve) => {
    const el = document.createElement("script");
    el.src = src;
    el.onload = () => resolve(true);
    el.onerror = () => resolve(false);
    document.head.appendChild(el);
  }));

  const stat = (k, v, note, badge) => '<div class="stat"><span class="stat__k">' + esc(k) +
    (badge ? " " + badge : "") + '</span><span class="stat__v">' + v + '</span><span class="stat__note">' +
    esc(note) + "</span></div>";
  P.extStat = stat;

  /* ------------------------------------------------------- headline */
  P.extHeadline = () => {
    const s = S();
    if (!s || !s.totals) return "";
    const t = s.totals;
    const b = P.srcBadge("sheet", "Sheet");
    return '<div class="grid grid--4">' +
      stat("Series", fmtN(t.matches), "documented by the community spreadsheet", b) +
      stat("Maps", fmtN(t.maps), (t.linkedMaps ? t.linkedMaps + " also read off a VOD, counted once" : "none linked to a VOD yet"), b) +
      stat("Hero bans", fmtN(t.resolvedBans), (t.bans - t.resolvedBans) + " more not matched to a hero", b) +
      stat("Listed heroes", fmtN(t.listings), "player-hero listings — appearances, not playtime", b) +
      "</div>";
  };

  /* -------------------------------------------------------- coverage */
  function creditHtml(s) {
    const src = s.source || {};
    const url = P.sheetLink();
    const credits = (src.credits || []).map((c) => esc(c.text.replace(/\s+/g, " ")) +
      ' <span class="dim">(' + esc(c.sheet + "!" + c.cell) + ")</span>");
    return '<p class="dim small u-mt-3">' + esc(src.creditNote || "") +
      (src.maintainer ? " Maintained by " + esc(src.maintainer) + "." : "") +
      (credits.length ? " The workbook credits: " + credits.join("; ") + "." : "") +
      (url ? ' <a href="' + esc(url) + '" target="_blank" rel="noopener">Open the original spreadsheet →</a>' : "") +
      ' <a href="how-it-works.html#sources">How the two sources differ</a></p>';
  }

  function syncLine(s) {
    const snap = s.snapshot;
    const la = s.lastAttempt || {};
    if (!snap) {
      return '<p class="small">' + P.srcBadge("sheet") + " Not imported yet: no spreadsheet numbers are shown " +
        "anywhere on the site until the first successful sync.</p>";
    }
    return '<p class="small">' + P.srcBadge("sheet") + " Last successful import " +
      '<b title="' + esc(snap.importedAt) + '">' + esc(P.fmtDate(snap.importedAt)) + "</b>" +
      (P.fmtRel ? ' <span class="dim">(' + esc(P.fmtRel(snap.importedAt)) + ")</span>" : "") +
      " · snapshot <code>" + esc(String(snap.sha256 || "").slice(0, 12)) + "</code>" +
      (la.status === "failed" ? ' · <span class="trust" data-trust="stale">the latest sync attempt failed — ' +
        "showing the last good import</span>" : "") + "</p>";
  }

  P.extCoverageCard = (opt) => {
    opt = opt || {};
    const s = S();
    if (!s) return "";
    if (!s.snapshot) return '<div class="card">' + syncLine(s) + creditHtml(s) + "</div>";
    const m = E.coverageMatrix(s);
    const link = (stage, region) => "series.html?" + [stage ? "stage=" + encodeURIComponent(stage) : "",
      region ? "region=" + encodeURIComponent(region) : ""].filter(Boolean).join("&");
    const cell = (stage, region) => {
      const r = m.cells[stage + "|" + region];
      if (!r) return '<td class="dim">—</td>';
      return '<td><a href="' + link(stage, region) + '" title="' + esc(plural(r.matches, "series") + ", " +
        plural(r.maps, "map") + ", " + plural(r.bans, "ban") + " — " + (r.stageRaw || "")) + '">' +
        '<b>' + r.matches + '</b><span class="dim"> / ' + r.maps + "</span></a></td>";
    };
    const table = '<div class="table-wrap u-mt-3"><table class="tbl tbl--compact matrix"><thead><tr><th>Stage</th>' +
      m.regions.map((r) => "<th>" + esc(P.extRegionName(r)) + "</th>").join("") +
      '<th>All</th></tr></thead><tbody>' +
      m.stages.map((st) => '<tr><th scope="row"><a href="' + link(st) + '">' + esc(P.extStageName(st)) + "</a></th>" +
        m.regions.map((r) => cell(st, r)).join("") +
        "<td><b>" + m.byStage[st].matches + '</b><span class="dim"> / ' + m.byStage[st].maps + "</span></td></tr>").join("") +
      '<tr><th scope="row">All stages</th>' + m.regions.map((r) => '<td><a href="' + link(null, r) + '"><b>' +
        m.byRegion[r].matches + '</b><span class="dim"> / ' + m.byRegion[r].maps + "</span></a></td>").join("") +
      "<td><b>" + m.total.matches + '</b><span class="dim"> / ' + m.total.maps + "</span></td></tr>" +
      "</tbody><caption>Series / maps the spreadsheet documents, per stage and region. Each cell opens " +
      "those series. The spreadsheet records no dates, so stages stand in for time.</caption></table></div>";
    return '<div class="card' + (opt.compact ? "" : "") + '">' +
      '<div class="sec-head" style="margin:0"><h3 style="margin:0">Spreadsheet coverage</h3>' +
      '<a class="sec-link" href="series.html">Every series →</a></div>' +
      syncLine(s) + table + creditHtml(s) + "</div>";
  };

  /* --------------------------------------------------- series list */
  function mapChip(m, row) {
    const name = P.extMapName(m.map, m.raw);
    const won = m.w === "a" ? row.teamA : m.w === "b" ? row.teamB : null;
    const res = m.w === "draw" ? "draw" : won ? P.anyTeamName(won) + " won" : "no winner recorded";
    const rec = m.rec ? " · " + ({ matched: "both sources agree", partial: "partly matched",
      conflict: "sources conflict", needs_review: "needs review" }[m.rec] || m.rec) : "";
    return '<a class="chip chip--outline xs-map" data-w="' + esc(m.w || "none") + '" href="map.html?ext=' +
      encodeURIComponent(m.id) + '" title="' + esc("Map " + m.n + ": " + name + " — " + res + rec) + '">' +
      '<span class="mono">' + esc(m.n) + "</span> " + esc(name) +
      (m.w === "a" ? " ◂" : m.w === "b" ? " ▸" : m.w === "draw" ? " =" : "") +
      (m.rec ? ' <span class="dot" data-rec="' + esc(m.rec) + '"></span>' : "") + "</a>";
  }

  P.extSeriesRow = (row) => {
    const t = E.seriesTally(row);
    const winA = row.winner && row.winner === row.teamA, winB = row.winner && row.winner === row.teamB;
    const linked = E.isLinked(row), review = E.needsReview(row);
    return "<tr>" +
      '<td class="small"><a href="series.html?id=' + encodeURIComponent(row.id) + '">' + esc(row.label || "Game") +
      "</a>" + (row.format ? '<div class="dim">' + esc(row.format) + "</div>" : "") + "</td>" +
      '<td><div class="xs-teams">' + P.extTeamLink(row.teamA, { win: winA }) +
      '<span class="xs-score mono">' + P.scorePlate(row.scoreA, row.scoreB, winA ? "a" : winB ? "b" : null) + "</span>" +
      P.extTeamLink(row.teamB, { win: winB }) + "</div>" +
      (row.complete === false ? '<div class="dim small">series not complete in the sheet</div>' : "") + "</td>" +
      '<td><div class="xs-maps">' + (row.maps || []).map((m) => mapChip(m, row)).join("") + "</div>" +
      (t.draw ? '<span class="dim small">' + plural(t.draw, "draw") + "</span>" : "") + "</td>" +
      "<td>" + P.srcBadge("sheet") + (linked ? " " + P.reconBadge((row.maps.find((m) => E.isLinked({ maps: [m] })) || {}).rec) : "") +
      (review ? " " + P.reconBadge("needs_review") : "") + "</td></tr>";
  };

  P.extSeriesListHtml = (rows, opt) => {
    opt = opt || {};
    if (!rows.length) return P.empty("◇", "No series match these filters", "Clear a filter to see more.");
    const limit = opt.limit || 0;
    let shown = 0;
    const groups = E.groupSeries(rows);
    return groups.map((g) => {
      if (limit && shown >= limit) return "";
      const list = limit ? g.rows.slice(0, Math.max(0, limit - shown)) : g.rows;
      shown += list.length;
      return '<section class="u-mt-5"><h3 class="u-flex u-center u-gap-3" style="font-size:1rem;flex-wrap:wrap">' +
        "<span>" + esc(g.stageRaw || (P.extStageName(g.stage) + " · " + P.extRegionName(g.region))) + "</span>" +
        '<span class="dim small" style="font-weight:400">' + esc(g.event || "") + " · " + plural(g.rows.length, "series") +
        "</span></h3>" +
        '<div class="table-wrap u-mt-3"><table class="tbl tbl--compact xs-table"><thead><tr><th>Game</th><th>Teams</th>' +
        "<th>Maps <span class=\"dim\">(◂ left team won · ▸ right team won · = draw)</span></th><th>Source</th></tr></thead><tbody>" +
        list.map(P.extSeriesRow).join("") + "</tbody></table></div></section>";
    }).join("") + (limit && rows.length > shown
      ? '<p class="dim small u-mt-4">Showing the first ' + shown + " of " + rows.length + " in the sheet’s order. " +
        "Narrow the filters to see the rest.</p>" : "");
  };

  /* A filterable list, its state in the URL (prefix keeps two lists on one
     page apart). */
  P.mountSeriesBrowser = (hostId, opt) => {
    opt = opt || {};
    const host = document.getElementById(hostId);
    if (!host) return;
    const rs = R();
    if (!rs || !rs.series) {
      host.innerHTML = P.note("info", "Spreadsheet series not loaded", "<p>This page could not load the series index.</p>");
      return;
    }
    const pre = opt.prefix || "";
    const KEYS = ["region", "stage", "event", "source", "team", "q"];
    const st = {};
    KEYS.forEach((k) => { st[k] = P.qs(pre + k) || (opt.fixed && opt.fixed[k]) || ""; });
    const o = E.seriesOptions(rs.series);
    const sel = (k, label, vals) => '<label class="field"><span class="mx-label">' + esc(label) + "</span>" +
      '<select class="input mf-select" data-k="' + k + '"><option value="">All</option>' +
      vals.map((v) => '<option value="' + esc(v[0]) + '"' + (st[k] === v[0] ? " selected" : "") + ">" + esc(v[1]) +
        "</option>").join("") + "</select></label>";
    const regions = o.regions.map((r) => [r, P.extRegionName(r)]).concat(o.hasNoRegion ? [["none", P.extRegionName(null)]] : []);
    host.innerHTML = '<div class="card mx-filters"><div class="mx-grid">' +
      sel("region", "Region", regions) +
      sel("stage", "Stage", o.stages.map((x) => [x, P.extStageName(x)])) +
      sel("event", "Event", o.events.map((x) => [x, x])) +
      sel("source", "Source", [["sheet-only", "Spreadsheet only"], ["linked", "Also read off a VOD"],
        ["review", "VOD candidate needs review"]]) +
      sel("team", "Team", o.teams.map((x) => [x, P.anyTeamName(x)]).sort((a, b) => a[1].localeCompare(b[1]))) +
      '<label class="field"><span class="mx-label">Search</span><input class="input" type="search" data-k="q" value="' +
      esc(st.q) + '" placeholder="Team, player of the match…" autocomplete="off"></label>' +
      '</div><p class="dim small u-mt-3">' + P.srcBadge("sheet") + " Ordered as the spreadsheet orders them: stage of " +
      "the season, region, then week, day and game. The spreadsheet records no dates and no map picks, so " +
      "neither is shown.</p></div>" +
      '<p class="dim small u-mt-4" data-role="count" aria-live="polite"></p><div data-role="list"></div>';
    const render = () => {
      const rows = E.filterSeries(rs.series, Object.assign({}, st, { names: P.anyTeamName }));
      const maps = rows.reduce((n, r) => n + (r.maps || []).length, 0);
      host.querySelector('[data-role="count"]').textContent = plural(rows.length, "series") + " · " +
        plural(maps, "map") + (rows.length !== rs.series.length ? " (of " + rs.series.length + " series)" : "");
      host.querySelector('[data-role="list"]').innerHTML = P.extSeriesListHtml(rows, { limit: opt.limit || 0 });
    };
    let t = null;
    const update = (k, v) => {
      st[k] = v;
      const qs = {};
      qs[pre + k] = v;
      P.setQs(qs);
      render();
    };
    host.addEventListener("change", (e) => { const k = e.target.dataset.k; if (k && k !== "q") update(k, e.target.value); });
    host.addEventListener("input", (e) => {
      if (e.target.dataset.k !== "q") return;
      clearTimeout(t);
      t = setTimeout(() => update("q", e.target.value.trim()), 160);
    });
    render();
  };

  /* ------------------------------------------------------------ hero */
  P.extHeroPanel = (heroId) => {
    const s = S();
    if (!s || !s.snapshot) return "";
    const h = E.heroSplit(s, heroId);
    const a = h.all;
    const badge = P.srcBadge("sheet");
    if (!h.known) {
      return '<div class="card"><h3>' + badge + " In the community spreadsheet</h3>" +
        '<p class="dim small u-mt-3">The spreadsheet does not list or ban this hero on any of its ' + fmtN(a.maps) +
        " maps with listings.</p></div>";
    }
    const lane = (rows, name) => '<div class="table-wrap u-mt-3"><table class="tbl tbl--compact tbl--fit"><thead><tr><th>' + esc(name) +
      '</th><th class="num">Listed on</th><th class="num">Appearance rate</th><th class="num">Banned on</th>' +
      '<th class="num">Ban rate</th></tr></thead><tbody>' +
      rows.filter((r) => r.maps || r.banDenominator).map((r) => "<tr><td>" +
        esc(name === "Region" ? P.extRegionName(r.key === "none" ? null : r.key) : P.extStageName(r.key === "none" ? null : r.key)) +
        '</td><td class="num">' + r.listedMaps + " / " + r.maps + '</td><td class="num">' + pct(r.rate) + " " +
        P.sampleNote(r.maps) + '</td><td class="num">' + r.banMaps + " / " + r.banDenominator + '</td><td class="num">' +
        pct(r.banRate) + "</td></tr>").join("") + "</tbody></table></div>";
    return '<div class="card"><h3>' + badge + " In the community spreadsheet</h3>" +
      '<div class="grid grid--4 u-mt-4">' +
      stat("Listed on", fmtN(a.listedMaps), "of " + fmtN(a.maps) + " maps with hero listings") +
      stat("Appearance rate", pct(a.rate), "maps where any player is listed on it") +
      stat("Banned on", fmtN(a.banMaps), "of " + fmtN(a.banDenominator) + " maps with a ban recorded") +
      stat("Ban rate", pct(a.banRate), fmtN(a.bans) + " ban" + (a.bans === 1 ? "" : "s") + " in all") +
      "</div>" +
      '<p class="dim small u-mt-3">Counts of what the spreadsheet’s editors list for each player (up to three heroes ' +
      "per player per map). A listing is an <b>appearance</b>: it says the hero was played on that map, not for how " +
      "long, and the order the sheet lists heroes in is not the order they were played. Rates are observational.</p>" +
      '<div class="scout-grid u-mt-4"><div>' + lane(h.region, "Region") + "</div><div>" + lane(h.stage, "Stage") + "</div></div>" +
      "</div>";
  };

  /* ------------------------------------------------------------ team */
  P.extTeamPanel = (teamId) => {
    const s = S();
    const p = s && s.teamProfiles && s.teamProfiles[teamId];
    if (!p) return "";
    const r = E.teamRecord(p);
    const badge = P.srcBadge("sheet");
    const m = r.maps;
    const rec = (x) => x.won + "–" + x.lost + (x.draw ? "–" + x.draw : "");
    const mapRows = r.byMap.map((x) => "<tr><td>" + (x.key.charAt(0) === "?"
      ? '<span class="unresolved" title="Not matched to a map in the catalog">“' + esc(x.key.slice(1)) + "”</span>"
      : esc(P.mapInfo(x.key).name)) + '</td><td class="num">' + x.played + '</td><td class="num">' + rec(x) +
      '</td><td>' + (x.decided ? pct(x.winShare) + " " : "— ") + P.sampleNote(x.decided, "decided map") + "</td></tr>").join("");
    const modeRows = r.byMode.map((x) => "<tr><td>" + esc(x.key === "?" ? "Mode not in the catalog" : x.key) +
      '</td><td class="num">' + x.played + '</td><td class="num">' + rec(x) + "</td><td>" +
      (x.decided ? pct(x.winShare) + " " : "— ") + P.sampleNote(x.decided, "decided map") + "</td></tr>").join("");
    const chips = (list, key) => list.length ? '<div class="row">' + list.slice(0, 10).map((x) =>
      '<span class="scout-chip">' + P.heroTile(x.hero, { size: "xs", link: true }) + '<span class="mono small">×' +
      x[key] + "</span></span>").join("") + "</div>" : '<p class="dim small">None recorded.</p>';
    return '<div class="stack">' +
      '<div class="grid grid--4">' +
      stat("Series", rec({ won: r.series.won || 0, lost: r.series.lost || 0, draw: 0 }),
        r.series.played + " documented" + (r.series.undecided ? ", " + r.series.undecided + " without a result" : ""), badge) +
      stat("Maps", rec(m), m.played + " played" + (m.undecided ? ", " + m.undecided + " without a winner" : "") +
        (m.disputed ? ", " + m.disputed + " disputed by the VOD reading (excluded)" : ""), badge) +
      stat("Map win share", pct(r.winShare), "of " + plural(r.decided, "decided map") + " (draws count as not won)", badge) +
      stat("Regions · stages", esc(r.regions.map((x) => P.extRegionName(x)).join(", ") || "—"),
        r.stages.map((x) => P.extStageName(x)).join(", ") || "—", badge) +
      "</div>" +
        '<div class="card"><h3>Maps <span class="dim small">won–lost(–drawn)</span></h3><div class="table-wrap">' +
          '<table class="tbl tbl--compact tbl--fit"><thead><tr><th>Map</th><th class="num">Played</th><th class="num">Record</th>' +
          "<th>Observed win share</th></tr></thead><tbody>" + mapRows + "</tbody></table></div></div>" +
      '<div class="lane-grid">' +
        '<div class="card"><h3>Modes</h3><div class="table-wrap"><table class="tbl tbl--compact tbl--fit"><thead><tr><th>Mode</th>' +
          '<th class="num">Played</th><th class="num">Record</th><th>Observed win share</th></tr></thead><tbody>' + modeRows +
          "</tbody></table></div></div>" +
        '<div class="card"><h3>Most-listed heroes</h3><p class="dim small">Maps on which the sheet lists the hero for this ' +
          "team, out of " + plural(m.listed, "map") + " with listings. Appearances, not playtime.</p>" +
          '<div class="table-wrap"><table class="tbl tbl--compact tbl--fit"><thead><tr><th>Hero</th><th class="num">Maps listed</th>' +
          '<th class="num">Share of its maps</th></tr></thead><tbody>' + r.heroes.map((x) =>
            '<tr><td><span class="u-flex u-center u-gap-3">' + P.heroTile(x.hero, { size: "xs", link: true }) +
            esc(P.hero(x.hero).name) + '</span></td><td class="num">' + x.maps + '</td><td class="num">' + pct(x.rate) +
            "</td></tr>").join("") + "</tbody></table></div></div>" +
        '<div class="card"><h3>Bans <span class="dim small">as the spreadsheet lists them</span></h3>' +
          '<p class="label">Listed under this team</p>' + chips(r.bansBy, "count") +
          '<p class="label u-mt-4">Listed under its opponents</p>' + chips(r.bansAgainst, "count") +
          '<p class="dim small u-mt-3">Whether a team column means “banned by” or “banned against” is the sheet’s own ' +
          "convention; it is reported as listed.</p></div>" +
      "</div></div>";
  };

  /* --------------------------------------------------- teams table */
  P.extTeamsTable = () => {
    const s = S();
    if (!s || !s.teamProfiles) return "";
    const rows = E.teamList(s).map((p) => E.teamRecord(p));
    const unmapped = rows.filter((r) => !r.mapped).length;
    return '<p class="dim small">' + P.srcBadge("sheet") + " " + plural(rows.length, "team") + " named by the spreadsheet; " +
      unmapped + " of them are not in the tracker’s team registry and keep the sheet’s spelling, marked " +
      "<i>unmapped</i>. Records are the sheet’s; a winner the VOD reading disputes is left out.</p>" +
      '<div class="table-wrap u-mt-3"><table class="tbl tbl--compact"><thead><tr><th>Team</th><th>Region</th>' +
      '<th class="num">Series</th><th class="num">Maps</th><th class="num">Map record</th><th>Map win share</th>' +
      "<th>Most-listed heroes</th></tr></thead><tbody>" + rows.map((r) =>
        "<tr><td>" + P.extTeamLink(r.id) + '</td><td class="small">' + esc(r.regions.map((x) => P.extRegionName(x)).join(", ") || "—") +
        '</td><td class="num">' + (r.series.won || 0) + "–" + (r.series.lost || 0) + '</td><td class="num">' + r.maps.played +
        '</td><td class="num">' + r.maps.won + "–" + r.maps.lost + (r.maps.draw ? "–" + r.maps.draw : "") + "</td><td>" +
        pct(r.winShare) + " " + P.sampleNote(r.decided, "decided map") + '</td><td><span class="comp comp--tight">' +
        r.heroes.slice(0, 5).map((x) => P.heroTile(x.hero, { size: "xs" })).join("") + "</span></td></tr>").join("") +
      "</tbody></table></div>";
  };

  /* ------------------------------------------ hero chart + table */
  const sliceOf = (v) => (!v ? {} : v.indexOf("region:") === 0 ? { region: v.slice(7) } : { stage: v.slice(6) });
  const sliceName = (v) => (!v ? "every stage and region" : v.indexOf("region:") === 0
    ? P.extRegionName(v.slice(7) === "none" ? null : v.slice(7)) : P.extStageName(v.slice(6)));

  P.extHeroesBlockHtml = (slice, metric) => {
    const s = S();
    if (!s || !s.snapshot) return "";
    const t = E.heroTable(s, sliceOf(slice));
    const ban = metric === "ban";
    const rows = t.rows.slice().filter((r) => (ban ? r.banMaps : r.listedMaps))
      .sort((a, b) => (ban ? b.banMaps - a.banMaps : b.listedMaps - a.listedMaps) || a.hero.localeCompare(b.hero));
    const top = rows.slice(0, 14);
    const chart = P.chart && top.length ? P.chart.bars(top.map((r) => {
      const h = P.hero(r.hero);
      return { label: h.name, value: ban ? r.banMaps : r.listedMaps, role: h.role,
        note: pct(ban ? r.banRate : r.rate) + " of maps", href: "hero.html?id=" + encodeURIComponent(h.id),
        mark: '<span class="hero__face">' + P.heroFace(h, 26) + "</span>" };
    }), { byRole: true,
      title: (ban ? "Maps banned on" : "Maps listed on") + " — spreadsheet, " + sliceName(slice) +
        (rows.length > top.length ? " (top " + top.length + " of " + rows.length + ")" : ""),
      caption: ban
        ? "Out of " + fmtN(t.banMaps) + " maps with a ban recorded. Spreadsheet imported; counts, not time."
        : "Out of " + fmtN(t.maps) + " maps with hero listings. A listing is an appearance (the hero was played on the " +
          "map), not playtime. Spreadsheet imported." }) : "";
    const table = '<div class="table-wrap u-mt-4"><table class="tbl tbl--compact"><thead><tr><th>Hero</th><th>Role</th>' +
      '<th class="num">Listed on</th><th class="num">Appearance rate</th><th class="num">Banned on</th>' +
      '<th class="num">Ban rate</th></tr></thead><tbody>' + rows.map((r) => {
        const h = P.hero(r.hero);
        return '<tr><td><span class="u-flex u-center u-gap-3">' + P.heroTile(h.id, { size: "xs", link: true }) +
          '<a href="hero.html?id=' + encodeURIComponent(h.id) + '">' + esc(h.name) + "</a></span></td><td>" +
          esc(h.role || "—") + '</td><td class="num">' + r.listedMaps + '</td><td class="num">' + pct(r.rate) +
          '</td><td class="num">' + r.banMaps + '</td><td class="num">' + pct(r.banRate) + "</td></tr>";
      }).join("") + "</tbody><caption>" + P.sampleNote(t.maps) + " with listings · " + P.sampleNote(t.banMaps) +
      " with a ban recorded · " + esc(sliceName(slice)) + "</caption></table></div>";
    return (chart ? '<div class="u-mt-4">' + chart + "</div>" : P.empty("◇", "Nothing listed in this slice", "")) +
      P.diag("Every hero in this slice (" + rows.length + ")", table);
  };

  /* A card with its own slice / metric pickers. */
  P.mountExtHeroesBlock = (hostId) => {
    const host = document.getElementById(hostId);
    const s = S();
    if (!host || !s || !s.snapshot) return;
    const D = s.heroDenominators || {};
    const regions = Object.keys(D.region || {}).sort((a, b) => (a === "none" ? 99 : E.regionRank(a)) - (b === "none" ? 99 : E.regionRank(b)));
    const stages = Object.keys(D.stage || {}).sort((a, b) => E.stageRank(a) - E.stageRank(b));
    const st = { slice: "", metric: "listed" };
    host.innerHTML = '<div class="card"><div class="sec-head" style="margin:0"><h3 style="margin:0">' + P.srcBadge("sheet") +
      " In the community spreadsheet</h3></div>" +
      '<div class="mf-filters u-mt-3"><label class="visually-hidden" for="' + hostId + '-slice">Slice</label>' +
      '<select class="input mf-select" id="' + hostId + '-slice"><option value="">All stages and regions</option>' +
      '<optgroup label="Region">' + regions.map((r) => '<option value="region:' + esc(r) + '">' +
        esc(P.extRegionName(r === "none" ? null : r)) + "</option>").join("") + "</optgroup>" +
      '<optgroup label="Stage">' + stages.map((x) => '<option value="stage:' + esc(x) + '">' + esc(P.extStageName(x)) +
        "</option>").join("") + "</optgroup></select>" +
      '<div class="seg" role="group" aria-label="Measure"><button type="button" data-m="listed" aria-pressed="true">' +
      'Appearances</button><button type="button" data-m="ban" aria-pressed="false">Bans</button></div></div>' +
      '<div data-role="out"></div>' + P.extUnmappedHeroesNote() + "</div>";
    const out = host.querySelector('[data-role="out"]');
    const draw = () => { out.innerHTML = P.extHeroesBlockHtml(st.slice, st.metric); };
    host.addEventListener("change", (e) => { if (e.target.tagName === "SELECT") { st.slice = e.target.value; draw(); } });
    host.addEventListener("click", (e) => {
      const b = e.target.closest("[data-m]");
      if (!b) return;
      st.metric = b.dataset.m;
      host.querySelectorAll("[data-m]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      draw();
    });
    draw();
  };

  /* Home page: the most-listed heroes per role, in the overall layout. */
  P.extRoleTopHtml = (n) => {
    const s = S();
    if (!s || !s.snapshot) return "";
    const t = E.heroTable(s, {});
    return ["Tank", "Damage", "Support"].map((role) => {
      const rows = t.rows.filter((r) => P.hero(r.hero).role === role).slice(0, n || 5);
      return '<div class="oc-role" data-role="' + role + '"><h3 class="oc-role__h">' + role + "</h3>" +
        (rows.length ? '<ol class="oc-role__list">' + rows.map((r) => "<li>" + P.heroTile(r.hero, { size: "xs", link: true }) +
          '<a class="oc-role__name" href="hero.html?id=' + encodeURIComponent(r.hero) + '">' + esc(P.hero(r.hero).name) + "</a>" +
          '<span class="oc-role__n mono" title="' + esc(r.listedMaps + " of " + t.maps + " maps") + '">' + pct(r.rate) +
          "</span></li>").join("") + "</ol>" : '<p class="dim small">None listed.</p>') + "</div>";
    }).join("");
  };

  /* --------------------------------------------- sources compared */
  const FIELD_LABEL = { date: "Date", map: "Map", order: "Map number", mode: "Mode", winner: "Winner",
    pickedBy: "Map pick", bans: "Hero bans" };
  const fmtVal = (k, v) => {
    if (v == null || (Array.isArray(v) && !v.length)) return '<span class="dim">not recorded</span>';
    if (k === "winner" || k === "pickedBy") return esc(P.anyTeamName(v));
    if (k === "map") return esc(P.mapInfo(v).name);
    if (k === "bans" || k.indexOf("heroes:") === 0) return v.map((h) => P.heroTile(h, { size: "xs" })).join("");
    return esc(v);
  };
  /* Field by field, VOD beside sheet, with the verdict. Never a winner. */
  P.extReconTable = (rec) => {
    const fields = (rec && rec.fields) || {};
    const verdict = (a) => a === true ? P.srcBadge("both", "agree") : a === false ? P.srcBadge("conflict", "disagree")
      : P.srcBadge("partial", "one source only");
    const rows = Object.keys(fields).map((k) => {
      const f = fields[k];
      const label = k.indexOf("heroes:") === 0 ? "Heroes — " + P.anyTeamName(k.slice(7)) : (FIELD_LABEL[k] || k);
      const extra = k.indexOf("heroes:") === 0
        ? ((f.sheetOnly || []).length ? '<div class="small">listed by the sheet, never seen on the VOD: ' +
            f.sheetOnly.map((h) => esc(P.hero(h).name)).join(", ") + "</div>" : "") +
          ((f.vodOnly || []).length ? '<div class="small">on the VOD for 30 s or more, not listed: ' +
            f.vodOnly.map((h) => esc(P.hero(h).name)).join(", ") + "</div>" : "")
        : (f.note ? '<div class="small dim">' + esc(f.note) + "</div>" : "");
      return '<tr><th scope="row">' + esc(label) + "</th><td>" + fmtVal(k, f.vod) + "</td><td>" +
        fmtVal(k, f.sheet) + "</td><td>" + verdict(f.agree) + extra + "</td></tr>";
    }).join("");
    if (!rows) return "";
    return '<p class="dim small">' + esc(rec.reason || "") + (rec.method ? " · linked " + esc(rec.method === "manual"
      ? "by a reviewer" : "automatically (score " + rec.score + ")") : "") + ". Where they disagree both claims stay; " +
      "a disputed winner is left out of every win count.</p>" +
      '<div class="table-wrap"><table class="tbl tbl--compact tbl--facts"><thead><tr><th>Field</th><th>' + P.srcBadge("vod") +
      "</th><th>" + P.srcBadge("sheet") + "</th><th>Verdict</th></tr></thead><tbody>" + rows + "</tbody></table></div>";
  };

  /* ------------------------------------------- unmapped catalog note */
  P.extUnmappedHeroesNote = () => {
    const s = S();
    const list = (s && s.unmapped && s.unmapped.hero) || [];
    const named = list.filter((x) => !/^picture:/.test(x.raw || ""));
    if (!named.length) return "";
    return '<p class="dim small u-mt-3">' + P.srcBadge("sheet") + " The spreadsheet also lists heroes the tracker’s " +
      "catalog does not have yet — " + named.map((x) => "<b>" + esc(x.raw) + "</b> (" + fmtN(x.count) + " listings)").join(", ") +
      ". They stay unmapped: shown as the sheet wrote them on each map, never counted in these numbers.</p>";
  };
})();
