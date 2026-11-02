/* =====================================================================
   OWCS Comp Tracker — app/page-comps.js
   The Comps page (stats.html), led by the pro record: every first-listed
   five-hero line-up from the community spreadsheet, for a time window and
   region (shared control: app/tierui.js).

     Top comps  — most run, and winning most (3+ maps), with the maps each
                  comp is run on; hover a comp for record, swaps, teams
     By map     — the most-run comps on every map, and what wins there
     Heroes     — pick, adjusted win and ban rate by role, with trends

   The broadcast (VOD) views stay below, drawn by page-stats.js.
   Maths: app/tierstats.js.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const T = window.OWCS_SHEET_TIERS;
  const U = P && P.tierUI;
  if (!P || !U || !P.tierStats) return;
  const esc = P.esc;
  const pct = U.pct;
  const ROLES = U.roles();
  const ROLE_ICON = { Tank: "⛨", Damage: "✦", Support: "✚" };
  const MIN_WIN_MAPS = 3;

  const state = { w: P.qs("w") || "recent", region: P.qs("region") || "" };
  let win = null;

  const tiersHref = (extra) => "tiers.html?" + win.link + (extra ? "&" + extra : "");
  const rec = (r) => Math.round(r.wins) + "–" + Math.round(r.losses);

  /* comp id -> [[map, apps], …] across the window's maps */
  function compMaps() {
    const idx = new Map();
    win.cur.maps.forEach((B) => {
      win.cur.compTiers(B.map).forEach((c) => {
        if (!idx.has(c.id)) idx.set(c.id, []);
        idx.get(c.id).push([B.map, c.apps]);
      });
    });
    idx.forEach((v) => v.sort((a, b) => b[1] - a[1]));
    return idx;
  }

  /* --------------------------------------------------------- tiles */
  function renderTiles() {
    const B = win.cur.bucket("*");
    const comps = win.cur.compTiers("*");
    const bp = win.cur.banProfile("*");
    const tile = (k, v, note) => '<div class="stat"><span class="stat__k">' + esc(k) + '</span><span class="stat__v">' +
      esc(v) + '</span><span class="stat__note">' + esc(note) + "</span></div>";
    P.mount("cx-tiles", B ? '<div class="grid grid--4">' +
      tile("Pro maps", B.maps.toLocaleString("en-US"), win.label.toLowerCase() + (state.region ? "" : ", every region")) +
      tile("Team line-ups", B.teamMaps.toLocaleString("en-US"), "every player's heroes, per map") +
      tile("Distinct comps", comps.length.toLocaleString("en-US"), "five heroes started together") +
      tile("Bans", ((bp && bp.total) || 0).toLocaleString("en-US"), "one per team per map") +
      "</div>" : "");
  }

  /* ------------------------------------------------------ top comps */
  function compRow(r, i, maps, before) {
    const where = (maps.get(r.id) || []).slice(0, 3);
    return '<div class="cx-row">' +
      '<span class="cx-row__rank">' + (i + 1) + "</span>" +
      "<span>" + U.comp(r, { px: 34 }) + "</span>" +
      '<span class="num"><b>' + r.apps + "</b></span>" +
      '<span class="num">' + rec(r) + "</span>" +
      '<span class="num"><strong>' + pct(r.adjWin, 1) + "</strong></span>" +
      '<span class="cx-row__maps">' + where.map((m) => '<a class="chip chip--outline" href="' +
        esc(tiersHref("tab=comps&map=" + encodeURIComponent(m[0]))) + '">' + esc(U.mapName(m[0])) + " " + m[1] + "</a>").join("") + "</span>" +
      "<span>" + (before ? U.trendChip(r.apps / Math.max(1, before.total), before.map.get(r.id) != null ? before.map.get(r.id) / Math.max(1, before.total) : null, "Share of line-ups") : "") + "</span>" +
      "</div>";
  }
  const HEAD = '<div class="cx-row cx-row--hd" aria-hidden="true"><span>#</span><span>Comp · hover for details</span>' +
    '<span class="num">Maps</span><span class="num">W–L</span><span class="num">Adj. win</span><span>Run on</span><span></span></div>';

  function renderTop() {
    const all = win.cur.compTiers("*");
    if (!all.length) { P.mount("p-top", P.empty("◇", "No full line-ups in this window", "Pick a longer window.")); return; }
    const maps = compMaps();
    let before = null;
    if (win.prev && win.prev.bucket("*")) {
      const pc = win.prev.compTiers("*");
      before = { map: new Map(pc.map((c) => [c.id, c.apps])), total: win.prev.bucket("*").teamMaps };
    }
    const mostRun = all.slice().sort((a, b) => b.apps - a.apps || b.adjWin - a.adjWin || a.id.localeCompare(b.id)).slice(0, 25);
    const winning = all.filter((c) => c.apps >= MIN_WIN_MAPS && c.decided >= MIN_WIN_MAPS && c.adjWin > 0.5)
      .sort((a, b) => b.adjWin - a.adjWin || b.apps - a.apps).slice(0, 12);
    P.mount("p-top",
      '<div class="cx-top u-mt-5">' +
      '<section><h2 class="cx-h">Most run</h2><p class="small dim">The line-ups teams start with most often, ' +
      "with their record and adjusted win (weighed for opponent strength and stakes).</p>" +
      '<div class="tr-clist cx-list u-mt-3">' + HEAD + mostRun.map((r, i) => compRow(r, i, maps, before)).join("") + "</div></section>" +
      '<section><h2 class="cx-h cx-h--win">Winning most</h2><p class="small dim">Comps run on ' + MIN_WIN_MAPS +
      "+ maps that win more than expected, best first.</p>" +
      (winning.length ? '<div class="cx-wins u-mt-3">' + winning.map((r) => '<div class="cx-win">' + U.comp(r, { px: 30 }) +
        '<span class="cx-win__v"><strong>' + pct(r.adjWin, 1) + '</strong><span class="dim"> · ' + rec(r) + " · " + r.apps + " maps</span></span></div>").join("") + "</div>"
        : '<p class="dim small u-mt-3">No comp was run ' + MIN_WIN_MAPS + "+ times in this window. Try Season.</p>") +
      "</section></div>");
  }

  /* --------------------------------------------------------- by map */
  function renderByMap() {
    const cards = win.cur.maps.map((B) => {
      const comps = win.cur.compTiers(B.map);
      const top = comps.slice().sort((a, b) => b.apps - a.apps || b.adjWin - a.adjWin || a.id.localeCompare(b.id)).slice(0, 3);
      const best = comps.filter((c) => c.apps >= 2 && c.decided >= 2).sort((a, b) => b.adjWin - a.adjWin || b.apps - a.apps)[0];
      const line = (r, cls) => '<div class="cx-mline' + (cls ? " " + cls : "") + '">' + U.comp(r, { px: 30 }) +
        '<span class="hm-map__n">' + r.apps + "× · " + rec(r) + "</span></div>";
      return '<article class="hm-map">' +
        '<header class="hm-map__hd"><h3><a href="' + esc(tiersHref("tab=comps&map=" + encodeURIComponent(B.map))) + '">' +
        esc(U.mapName(B.map)) + '</a></h3><span class="hm-map__meta">' + (B.mode ? esc(B.mode) + " · " : "") + B.maps + " maps</span></header>" +
        '<p class="hm-map__k">Most run</p>' +
        (top.length ? top.map((r) => line(r)).join("") : '<p class="dim small">No full line-up recorded</p>') +
        (best && (!top.length || best.id !== top[0].id) ? '<p class="hm-map__k hm-map__k--win">Winning most</p>' + line(best, "cx-mline--win") : "") +
        '<a class="hm-map__go" href="' + esc(tiersHref("tab=comps&map=" + encodeURIComponent(B.map))) + '">Every comp on this map →</a></article>';
    });
    P.mount("p-cxmap", cards.length ? '<div class="hm-mapgrid u-mt-5">' + cards.join("") + "</div>"
      : P.empty("◇", "No maps in this window", ""));
  }

  /* --------------------------------------------------------- heroes */
  function renderHeroes() {
    const rows = win.cur.heroTiers("*");
    const before = new Map(win.prev ? win.prev.heroTiers("*").map((r) => [r.id, r.pickShare]) : []);
    const maxPick = rows.reduce((m, r) => Math.max(m, r.pickShare), 0) || 1;
    const col = (role) => {
      const list = rows.filter((r) => r.role === role && r.pickShare >= 0.005).sort((a, b) => b.pickShare - a.pickShare);
      return '<section class="card cx-hcol"><h3 class="tr-bancol__hd"><span aria-hidden="true">' + ROLE_ICON[role] + "</span> " + esc(role) + "</h3>" +
        '<div class="cx-hrow cx-hrow--hd" aria-hidden="true"><span></span><span>Hero</span><span>Pick</span><span class="num">Adj. win</span><span class="num">Ban</span><span></span></div>' +
        list.map((r) => {
          const h = P.hero(r.id);
          return '<a class="cx-hrow' + (r.lowSample ? " is-low" : "") + '" href="hero.html?id=' + encodeURIComponent(r.id) + '" title="' +
            esc(h.name + ": " + pct(r.pickShare) + " of team-maps, " + pct(r.adjWin, 1) + " adjusted win, banned on " + pct(r.banShare) + " of maps" +
              (r.lowSample ? " (small sample)" : "")) + '">' +
            P.heroFace(h, 30) + '<span class="cx-hrow__name">' + esc(h.name) +
            (r.tier ? ' <span class="tr-badge" data-tier="' + esc(r.tier) + '">' + esc(r.tier) + "</span>" : "") + "</span>" +
            '<span class="cx-hrow__bar"><span style="width:' + Math.round((r.pickShare / maxPick) * 100) + '%"></span><b>' + pct(r.pickShare) + "</b></span>" +
            '<span class="num">' + pct(r.adjWin, 1) + '</span><span class="num">' + (r.banShare >= 0.005 ? pct(r.banShare) : "—") + "</span>" +
            "<span>" + (win.prev ? U.trendChip(r.pickShare, before.has(r.id) ? before.get(r.id) : null, "Pick share") : "") + "</span></a>";
        }).join("") + "</section>";
    };
    P.mount("p-cxheroes", '<p class="small dim u-mt-5">Pick = share of team-maps a hero is listed on (a player\'s first hero counts most). ' +
      'Tier letters are from the <a href="' + esc(tiersHref("")) + '">tier list</a>. Click a hero for its page.</p>' +
      '<div class="cx-hcols u-mt-4">' + U.ROLE_ORDER.map(col).join("") + "</div>");
  }

  /* ----------------------------------------------------------- boot */
  function update() {
    P.setQs({ w: state.w === "recent" ? "" : state.w, region: state.region });
    win = U.runWindow(state, ROLES);
    P.mount("cx-controls", U.controlsHtml(state));
    P.mount("cx-window", U.windowLineHtml(win, state));
    renderTiles();
    renderTop();
    renderByMap();
    renderHeroes();
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!T || !T.maps || !T.maps.length) { P.mount("p-top", P.empty("◇", "No pro data yet", "")); return; }
    update();
    P.initTabs(document.getElementById("cx"), { key: "tab" });
    document.getElementById("cx-controls").addEventListener("click", (e) => {
      const b = e.target.closest("[data-w],[data-region]");
      if (!b) return;
      if (b.dataset.w != null) state.w = b.dataset.w;
      if (b.dataset.region != null) state.region = b.dataset.region;
      update();
    });
  });
})();
