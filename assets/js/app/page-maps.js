/* =====================================================================
   OWCS Comp Tracker — app/page-maps.js
   The home page: every OWCS map, map by map, and the comps behind them.

   Two questions, in the order a fan asks them:
     1. What is being played overall? — the most-run comps and the most-
        picked heroes by role, from every published line-up.
     2. What happened on each map? — every map the tracker has found,
        newest broadcast first, both teams with their marks, each team's
        comp where it has been read, and a link that opens the VOD at the
        second the map starts.

   Nothing here is operator furniture. Submitting, reviewing and naming
   heroes live on the admin pages (admin.html), which this page does not
   link to unless this browser is in admin mode.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const D = P.pub || {};
  const esc = P.esc;
  const F = P.mapFeed;
  const pct = (v) => (v == null ? "—" : Math.round(v * 100) + "%");

  const state = { team: P.qs("team") || "", map: P.qs("map") || "",
    mode: P.qs("mode") || "", comps: P.qs("comps") === "1" };

  document.addEventListener("DOMContentLoaded", () => {
    renderCounts();
    renderOverall();
    renderSheet();
    renderFilters();
    renderFeed();
  });

  /* ------------------------------------------------------------ counts */
  function renderCounts() {
    const maps = F.maps();
    const withComps = maps.filter(F.hasComp).length;
    const teams = new Set();
    maps.forEach((m) => [m.teamA, m.teamB].forEach((t) => { if (t && (t.id || t.name)) teams.add(t.id || t.name); }));
    const tile = (k, v, note) =>
      '<div class="stat"><span class="stat__k">' + esc(k) + '</span><span class="stat__v">' +
      esc(v) + '</span><span class="stat__note">' + esc(note) + "</span></div>";
    const tileB = (k, v, note, badge) =>
      '<div class="stat"><span class="stat__k">' + esc(k) + badge + '</span><span class="stat__v">' +
      esc(v) + '</span><span class="stat__note">' + esc(note) + "</span></div>";
    const S = P.extSummary;
    const t = S && S.snapshot && S.totals;
    const b = (k, text) => (P.srcBadge ? " " + P.srcBadge(k, text) : "");
    document.getElementById("counts").innerHTML = '<div class="grid grid--4">' +
      tile("Maps", maps.length, "found on official broadcasts") +
      tile("Maps with comps", withComps, "both line-ups read off the HUD") +
      tile("Teams", teams.size, "every one with its mark") +
      tile("Broadcasts", F.groups().length, "newest first below") +
      "</div>" +
      /* The community spreadsheet, as its own row: a separate source with
         its own badge, never added to the broadcast counts above. */
      (t ? '<div class="grid grid--4 u-mt-3">' +
        tileB("Spreadsheet series", t.matches.toLocaleString("en-US"), "documented by the community spreadsheet", b("sheet", "Sheet")) +
        tileB("Spreadsheet maps", t.maps.toLocaleString("en-US"), t.linkedMaps + " also read off a VOD, counted once", b("sheet", "Sheet")) +
        tileB("Teams in the sheet", t.teams, t.unmappedTeams + " not in the tracker’s registry", b("sheet", "Sheet")) +
        tileB("Hero bans", t.resolvedBans.toLocaleString("en-US"), "as the sheet lists them", b("sheet", "Sheet")) +
        "</div>" : "");
  }

  /* ----------------------------------------------------------- overall */
  function renderOverall() {
    const host = document.getElementById("overall");
    const freq = (D.compFrequency || []).slice().sort((a, b) => b.appearances - a.appearances);
    const rates = {};
    (D.compWinRate || []).forEach((c) => { rates[c.id] = c; });
    const picks = (D.heroPickRates || []).slice().sort((a, b) => b.picks - a.picks);

    if (!freq.length && !picks.length) {
      host.innerHTML = P.note("info", "No comps read yet",
        "<p>Every map below is already indexed with its teams and a watch link. Comps appear " +
        "beside each map as the tracker reads its heroes, and the overall numbers fill in here.</p>");
      return;
    }

    const comps = freq.slice(0, 4).map((c, i) => {
      const w = rates[c.id];
      return '<div class="oc-comp">' +
        '<span class="oc-comp__rank mono">#' + (i + 1) + "</span>" +
        P.compStrip(c.heroes, { link: true, size: "sm" }) +
        '<div class="oc-comp__meta">' +
          '<span class="oc-comp__n">' + c.appearances + " map" + (c.appearances === 1 ? "" : "s") + "</span>" +
          (w && w.decidedMaps ? '<span class="dim small">' + pct(w.winRate) + " won</span>" : "") +
          '<span class="oc-comp__teams">' + (c.teamIds || []).map((t) =>
            '<a href="team.html?id=' + encodeURIComponent(t) + '" title="' +
            esc((P.team(t) || {}).name || t) + '">' + P.teamMark(t, 22) + "</a>").join("") + "</span>" +
        "</div></div>";
    }).join("");

    const roles = ["Tank", "Damage", "Support"].map((role) => {
      const rows = picks.filter((r) => P.hero(r.hero).role === role).slice(0, 5);
      return '<div class="oc-role" data-role="' + role + '"><h3 class="oc-role__h">' + role + "</h3>" +
        (rows.length
          ? '<ol class="oc-role__list">' + rows.map((r) =>
            "<li>" + P.heroTile(r.hero, { size: "xs", link: true }) +
            '<a class="oc-role__name" href="hero.html?id=' + encodeURIComponent(r.hero) + '">' +
            esc(P.hero(r.hero).name) + "</a>" +
            '<span class="oc-role__n mono">' + r.picks + "</span></li>").join("") + "</ol>"
          : '<p class="dim small">None read yet.</p>') + "</div>";
    }).join("");

    const sample = P.publishedComps().length;
    host.innerHTML =
      '<div class="oc">' +
        '<div class="oc__col"><h3 class="oc__h">Most-run comps</h3>' +
          (comps || '<p class="dim small">No full five-hero comp yet.</p>') + "</div>" +
        '<div class="oc__col"><h3 class="oc__h">Most-picked heroes</h3>' +
          '<div class="oc-roles">' + roles + "</div></div>" +
      "</div>" +
      '<p class="dim small u-mt-3">From ' + sample + " published line-up" + (sample === 1 ? "" : "s") +
      " across " + F.maps().filter(F.hasComp).length + " map" +
      (F.maps().filter(F.hasComp).length === 1 ? "" : "s") + ". Grows with every map read.</p>";
  }

  /* ------------------------------------------------- the spreadsheet */
  /* What the community spreadsheet adds: its most-listed heroes per role
     (appearance rate, not time) and its coverage. Own section, own badge,
     own numbers — the broadcast comps above are untouched by it. */
  function renderSheet() {
    const host = document.getElementById("sheet");
    if (!host || !P.extSummary || !P.extSummary.snapshot || !P.extRoleTopHtml) {
      if (host) host.closest("section").hidden = true;
      return;
    }
    const t = P.extSummary.heroDenominators.all;
    host.innerHTML = '<div class="oc"><div class="oc__col" style="flex:1 1 100%"><h3 class="oc__h">Most-listed heroes ' +
      P.srcBadge("sheet", "Sheet") + '</h3><div class="oc-roles">' + P.extRoleTopHtml(5) + "</div>" +
      '<p class="dim small u-mt-3">Share of ' + t.maps.toLocaleString("en-US") + " spreadsheet maps on which any player " +
      "is listed on the hero. Appearances, not playtime; not comparable with the broadcast numbers above. " +
      '<a href="stats.html?tab=heroes#sheet-heroes">By region and stage →</a></p></div></div>' +
      '<div class="u-mt-5">' + P.extCoverageCard() + "</div>";
  }

  /* ----------------------------------------------------------- filters */
  function renderFilters() {
    const maps = F.maps();
    const teams = new Map();
    maps.forEach((m) => [m.teamA, m.teamB].forEach((t) => {
      if (t && t.id) teams.set(t.id, F.teamName(t));
    }));
    const mapIds = new Map();
    maps.forEach((m) => { if (m.map) mapIds.set(m.map, P.mapInfo(m.map).name); });
    const modes = new Set(maps.map((m) => m.mode).filter((x) => x && !/unknown/i.test(x)));

    const opt = (v, label, cur) => '<option value="' + esc(v) + '"' + (v === cur ? " selected" : "") +
      ">" + esc(label) + "</option>";
    const byName = (a, b) => a[1].localeCompare(b[1]);
    document.getElementById("filters").innerHTML =
      '<label class="visually-hidden" for="f-team">Team</label>' +
      '<select class="input mf-select" id="f-team">' + opt("", "All teams", state.team) +
        Array.from(teams).sort(byName).map((t) => opt(t[0], t[1], state.team)).join("") + "</select>" +
      '<label class="visually-hidden" for="f-map">Map</label>' +
      '<select class="input mf-select" id="f-map">' + opt("", "All maps", state.map) +
        Array.from(mapIds).sort(byName).map((m) => opt(m[0], m[1], state.map)).join("") + "</select>" +
      '<div class="seg" role="group" aria-label="Mode">' +
        ["", ...Array.from(modes).sort()].map((m) =>
          '<button type="button" data-mode="' + esc(m) + '" aria-pressed="' + (state.mode === m) + '">' +
          esc(m || "All modes") + "</button>").join("") + "</div>" +
      '<label class="mf-toggle"><input type="checkbox" id="f-comps"' + (state.comps ? " checked" : "") +
        "> Only maps with comps</label>";

    const root = document.getElementById("filters");
    root.addEventListener("change", (e) => {
      if (e.target.id === "f-team") state.team = e.target.value;
      if (e.target.id === "f-map") state.map = e.target.value;
      if (e.target.id === "f-comps") state.comps = e.target.checked;
      sync();
    });
    root.addEventListener("click", (e) => {
      const b = e.target.closest("[data-mode]");
      if (!b) return;
      state.mode = b.dataset.mode;
      root.querySelectorAll("[data-mode]").forEach((x) =>
        x.setAttribute("aria-pressed", x === b ? "true" : "false"));
      sync();
    });
  }

  function sync() {
    P.setQs({ team: state.team || null, map: state.map || null, mode: state.mode || null,
      comps: state.comps ? "1" : null });
    renderFeed();
  }

  /* -------------------------------------------------------------- feed */
  function renderFeed() {
    const groups = F.filter((m) =>
      (!state.team || F.plays(m, state.team)) &&
      (!state.map || m.map === state.map) &&
      (!state.mode || m.mode === state.mode) &&
      (!state.comps || F.hasComp(m)));
    const n = groups.reduce((s, g) => s + g.maps.length, 0);
    document.getElementById("feed-count").textContent = n + " map" + (n === 1 ? "" : "s");
    const host = document.getElementById("feed");
    if (!F.maps().length) {
      host.innerHTML = P.empty("◇", "No maps indexed yet",
        "Maps appear here as broadcasts are indexed.");
      return;
    }
    host.innerHTML = groups.length ? F.html(groups)
      : P.empty("◇", "No map matches those filters", "Clear a filter to see more.");
    if (P.observeReveals) P.observeReveals(host);
  }
})();
