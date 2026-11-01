/* =====================================================================
   OWCS Comp Tracker — app/page-series.js
   series.html: the community spreadsheet's series, as its own record.

     series.html              every series, filterable by region, stage,
                              event, source and team, in the sheet's order
     series.html?id=xm-…      one series, the way the sheet's match card
                              records it: teams, format, series score, and
                              per map the winner, map score, bans and each
                              player's listed heroes

   What is NOT here, because the sheet does not record it: a date, who
   picked each map, any time or duration, the order heroes were played in.
   A listed hero is an appearance; #1/#2/#3 is the sheet's list order.

   Where a map was also read off the broadcast, the VOD reading sits
   beside the sheet's, each with its own badge and the reconciliation
   verdict. Disagreements are shown, never resolved here.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const E = P.extStats;
  const esc = P.esc;
  const set = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };
  const plural = (n, w) => n + " " + w + (n === 1 ? "" : "s");

  document.addEventListener("DOMContentLoaded", () => {
    const id = P.qs("id");
    if (id) renderSeries(id);
    else renderList();
  });

  /* ------------------------------------------------------------ list */
  function renderList() {
    set("crumbs", P.breadcrumbs([{ label: "Maps", href: "index.html" }, { label: "Series" }]));
    set("head", '<div class="page-head page-head--tight"><p class="eyebrow eyebrow--gold">From the community spreadsheet</p>' +
      "<h1>Series</h1><p class=\"lede\">Every series the community-maintained OWCS spreadsheet documents: teams, " +
      "format, series score and each map's result. Open one for its bans and the heroes listed for every player. " +
      "This is the spreadsheet's record, labelled as such — the maps read off a broadcast are on " +
      '<a href="index.html">the home page</a>.</p></div>' +
      '<div class="u-mt-4">' + P.extProvenanceLine() + "</div>");
    set("body", '<div id="browser"></div><div class="u-mt-6">' + P.extCoverageCard() + "</div>");
    P.mountSeriesBrowser("browser", { limit: 400 });
  }

  /* ---------------------------------------------------------- series */
  const FLAG = {
    "series-score-disagrees-with-map-winners": "The series score on the card does not match the map winners listed under it.",
    "card-score-disagrees-with-map-list": "The map score in the map panel differs from the map list.",
    "winner-disagrees-with-map-score": "The winner text and the map score disagree.",
    "card-winner-disagrees-with-map-list": "The panel's winner text differs from the map list's arrow; the arrow is used.",
    "card-no-map-panel": "The map list has this map but the card has no panel for it (a tiebreak), so no heroes or bans.",
    "winner-unresolved": "The winner text could not be matched to either team.",
    "card-map-list-disagrees-with-panel": "The map list and the map panel name different maps.",
    "map-unmapped": "The map name is not in the tracker's catalog.",
  };
  const flagText = (f) => FLAG[f] || (/^no-ban:/.test(f) ? "No ban recorded for side " + f.split(":")[1].toUpperCase() +
    (f.split(":")[2] ? " (“" + f.split(":")[2] + "”)" : "") : /^ban-text-disagrees-with-picture/.test(f)
    ? "The ban text and the ban picture disagree; the text is used." : f);

  function renderSeries(id) {
    const rs = P.extSeries;
    const row = rs && (rs.series || []).find((s) => s.id === id);
    if (!row) {
      set("crumbs", P.breadcrumbs([{ label: "Maps", href: "index.html" }, { label: "Series", href: "series.html" },
        { label: "Not found" }]));
      set("head", '<div class="page-head"><h1>That series is not in the spreadsheet import</h1>' +
        '<p class="lede">The link points at a series id the current import does not have. Ids are keyed on ' +
        "the sheet's own content, so a corrected team name in the sheet gives a series a new id.</p>" +
        '<div class="row u-mt-5"><a class="btn btn--primary" href="series.html">Every series</a></div></div>');
      return;
    }
    const nameA = P.anyTeamName(row.teamA), nameB = P.anyTeamName(row.teamB);
    document.title = nameA + " vs " + nameB + " · " + (row.stageRaw || "") + " — OWCS Comp Tracker";
    set("crumbs", P.breadcrumbs([{ label: "Maps", href: "index.html" }, { label: "Series", href: "series.html" },
      { label: row.stageRaw || P.extStageName(row.stage), href: "series.html?stage=" + encodeURIComponent(row.stage || "") +
        (row.region ? "&region=" + encodeURIComponent(row.region) : "") },
      { label: nameA + " vs " + nameB }]));
    const winA = row.winner && row.winner === row.teamA, winB = row.winner && row.winner === row.teamB;
    const side = (tid, win) => '<a class="map-hd__team' + (win ? " is-win" : "") + '" href="team.html?id=' +
      encodeURIComponent(tid || "") + '" style="text-decoration:none;color:inherit">' + P.anyTeamPlate(tid, { size: "lg" }) +
      (win ? '<span class="chip" data-state="evidence"><span class="dot"></span>won the series</span>' : "") + "</a>";
    const meta = [row.event, row.stageRaw, row.label, row.format].filter(Boolean)
      .filter((v, i, a) => a.indexOf(v) === i).map(esc).join(" · ");
    set("head", '<div class="page-head page-head--tight"><p class="eyebrow eyebrow--gold">' + P.srcBadge("sheet") +
      " Series from the community spreadsheet</p>" +
      '<div class="map-hd__teams u-mt-4">' + side(row.teamA, winA) +
      '<span class="map-hd__vs">' + P.scorePlate(row.scoreA, row.scoreB, winA ? "a" : winB ? "b" : null) + "</span>" +
      side(row.teamB, winB) + "</div>" +
      '<p class="dim u-mt-4">' + meta + "</p>" +
      (row.complete === false ? P.note("info", "Series not complete in the sheet",
        "<p>The card's series score does not reach a winner for its format yet. Maps listed so far are shown.</p>") : "") +
      '<p class="dim small u-mt-3">The spreadsheet records no date and no map picks for this series, so neither is ' +
      "shown. Player of the match, as the sheet lists it: <b>" + esc(row.potm || "not recorded") + "</b>." +
      (row.src ? " Source: <code>" + esc(row.src.sheet + (row.src.row ? " row " + row.src.row : "")) + "</code>." : "") +
      "</p></div>");

    set("body", '<div class="u-mt-3">' + P.extProvenanceLine() + "</div>" +
      '<div class="stack u-mt-4">' + (row.maps || []).map((m) =>
        '<div class="card" id="map-' + esc(m.id) + '"><p class="dim small">Map ' + esc(m.n) + " · loading the sheet’s listing…</p></div>").join("") +
      "</div>");

    Promise.all((row.maps || []).map((m) => P.loadExtDetail(m.id).then((d) => [m.id, d]))).then((pairs) => {
      const details = {};
      pairs.forEach((p) => { if (p[1]) details[p[0]] = p[1]; });
      const card = E.seriesCard(row, details, rs.records);
      const warn = [];
      if (card.scoreAgreesWithMaps === false) {
        warn.push("The series score on the card (" + row.scoreA + "–" + row.scoreB + ") does not match the map winners " +
          "listed under it (" + card.tally.a + "–" + card.tally.b + (card.tally.draw ? ", " + plural(card.tally.draw, "draw") : "") +
          "). Both are shown as the sheet has them.");
      }
      (row.flags || []).forEach((f) => { if (f !== "series-score-disagrees-with-map-winners") warn.push(flagText(f)); });
      set("body", '<div class="u-mt-3">' + P.extProvenanceLine() + "</div>" +
        (warn.length ? P.note("warn", "The spreadsheet disagrees with itself here", "<ul>" +
          warn.map((w) => "<li>" + esc(w) + "</li>").join("") + "</ul>") : "") +
        '<div class="stack u-mt-4">' + card.maps.map((m) => mapCard(m, card)).join("") + "</div>" +
        '<p class="dim small u-mt-5">Heroes are listed as the spreadsheet’s editors recorded them, up to three per player ' +
        "per map. <b>#1, #2, #3 is the sheet’s list order, not the order they were played</b>, and a listing says nothing " +
        "about how long a hero was on the field. Unresolved spellings are shown struck through and never counted.</p>");
    });
  }

  function banList(list) {
    if (!list.length) return '<span class="dim small">none recorded</span>';
    return list.map((b) => '<span class="scout-chip">' + P.extHero(b, { size: "xs", link: !!b.hero }) +
      (b.hero ? '<span class="small">' + esc(P.hero(b.hero).name) + "</span>" : "") + "</span>").join(" ");
  }

  function playerTable(players, teamId) {
    if (!players.length) return '<p class="dim small">The sheet lists no players for this team on this map.</p>';
    return '<div class="table-wrap"><table class="tbl tbl--compact"><thead><tr><th>Player</th><th>Role</th>' +
      "<th>Listed heroes <span class=\"dim\">(sheet’s list order)</span></th></tr></thead><tbody>" +
      players.map((p) => "<tr><td>" + esc(p.playerRaw || p.player || "—") +
        (p.playerMapped ? "" : '<small class="unmapped-mark" title="Not in the tracker’s player registry"> · unmapped</small>') +
        '</td><td class="small">' + esc(p.role || "—") + '</td><td><span class="u-flex u-center u-gap-2" style="flex-wrap:wrap">' +
        (p.heroes.length ? p.heroes.map((h) => '<span class="scout-chip" title="Listed #' + esc(h.n) + ' by the sheet">' +
          '<span class="mono small dim">#' + esc(h.n) + "</span>" + P.extHero(h, { size: "xs", link: !!h.hero }) +
          (h.hero ? '<span class="small">' + esc(P.hero(h.hero).name) + "</span>" : "") + "</span>").join("")
          : '<span class="dim small">no hero listed</span>') + "</span></td></tr>").join("") + "</tbody></table></div>" +
      '<span class="visually-hidden">' + esc(P.anyTeamName(teamId)) + "</span>";
  }

  function result(m, card) {
    const a = card.teamA, b = card.teamB;
    const score = m.scoreA != null && m.scoreB != null ? m.scoreA + "–" + m.scoreB : (m.scoreRaw || "");
    if (m.winnerSide === "draw") return "<b>Draw</b>" + (score ? " · " + esc(score) : "");
    if (m.winnerSide === "a" || m.winnerSide === "b") {
      return "<b>" + esc(P.anyTeamName(m.winnerSide === "a" ? a : b)) + "</b> won" +
        (score ? ' · map score <span class="mono">' + esc(score) + "</span>" : "");
    }
    return '<span class="dim">No winner recorded</span>' + (score ? " · " + esc(score) : "");
  }

  /* The VOD reading of a linked map, timed, beside the sheet's listing. */
  function vodSide(rec) {
    const tw = ((P.pub && P.pub.timeWeighting && P.pub.timeWeighting.maps) || []).find((x) => x.mapId === rec.vodMapId);
    const vm = P.match && P.pub ? (P.pub.matches || []).find((mm) => (mm.maps || []).some((x) => x.id === rec.vodMapId)) : null;
    const teams = tw ? (tw.teams || []).map((t) => '<div class="u-mt-3"><p class="label">' + esc(P.anyTeamName(t.teamId)) +
      ' <span class="dim small">' + esc(t.result || "") + "</span></p>" +
      '<div class="row">' + (t.heroes || []).map((h) => '<span class="scout-chip">' + P.heroTile(h.hero, { size: "xs", link: true }) +
        '<span class="mono small">' + P.fmtClock(h.seconds) + "</span></span>").join("") + "</div></div>").join("")
      : '<p class="dim small">No timed hero data published for this VOD map.</p>';
    return '<div class="card"><h4>' + P.srcBadge("vod") + " Read off the broadcast</h4>" +
      (tw ? '<p class="dim small">Seconds on the field inside the detected rounds; coverage ' + P.pctHonest(tw.coverage) + ".</p>" : "") +
      teams + '<p class="small u-mt-3"><a href="map.html?id=' + encodeURIComponent(rec.vodMapId) + '">Open the VOD map →</a>' +
      (vm ? ' · <a href="game.html?id=' + encodeURIComponent(vm.id) + '">the VOD game</a>' : "") + "</p></div>";
  }

  function mapCard(m, card) {
    const rec = m.rec;
    const name = P.extMapName(m.map, m.mapRaw);
    const flags = (m.flags || []).filter((f) => f !== "map-unmapped");
    const scout = "map.html?" + (rec && rec.vodMapId ? "id=" + encodeURIComponent(rec.vodMapId) : "ext=" + encodeURIComponent(m.id));
    const sheetSide = '<div class="scout-grid u-mt-4">' +
      '<div><p class="label">' + P.extTeamLink(card.teamA) + "</p>" + playerTable(m.players.a, card.teamA) + "</div>" +
      '<div><p class="label">' + P.extTeamLink(card.teamB) + "</p>" + playerTable(m.players.b, card.teamB) + "</div></div>";
    let recon = "";
    if (rec && rec.vodMapId) {
      recon = '<div class="u-mt-5"><div class="sec-head" style="margin:0"><h4 style="margin:0">Both sources</h4>' +
        P.reconBadge(rec.status) + "</div>" + P.extReconTable(rec) +
        '<div class="scout-grid u-mt-4"><div class="card"><h4>' + P.srcBadge("sheet") + " Listed by the spreadsheet</h4>" +
        ["a", "b"].map((s) => '<p class="label u-mt-3">' + esc(P.anyTeamName(s === "a" ? card.teamA : card.teamB)) + "</p>" +
          '<div class="row">' + Array.from(new Set(m.players[s].reduce((acc, p) => acc.concat(p.heroes.filter((h) => h.hero)
            .map((h) => h.hero)), []))).map((h) => P.heroTile(h, { size: "xs", link: true })).join("") + "</div>").join("") +
        '<p class="dim small u-mt-3">Appearances only, no time.</p></div>' + vodSide(rec) + "</div></div>";
    } else if (rec && rec.status === "needs_review") {
      recon = P.note("info", "A VOD map might be this one — not linked", "<p>The importer found a candidate but could not " +
        "link it safely. A person decides on the Source health page; until then the two stay separate.</p><ul class=\"small\">" +
        (rec.candidates || []).map((c) => '<li><a href="map.html?id=' + encodeURIComponent(c.vodMapId) + '">' + esc(c.vodMapId) +
          "</a> — score " + esc(c.score) + ": " + esc((c.reasons || []).join("; ")) + "</li>").join("") + "</ul>");
    }
    return '<article class="card" id="map-' + esc(m.id) + '">' +
      '<div class="u-flex u-between u-center u-gap-3 u-wrap"><h3 style="margin:0">Map ' + esc(m.order) + " · " +
      (m.map ? esc(name) : '<span class="unresolved" title="Not in the map catalog">“' + esc(name) + "”</span>") +
      (m.mode || (m.map && P.mapInfo(m.map).mode) ? ' <span class="dim small">' + esc(m.mode || P.mapInfo(m.map).mode) + "</span>" : "") +
      "</h3><span class=\"row\">" + P.srcBadge("sheet") + (m.recStatus !== "unmatched" ? " " + P.reconBadge(m.recStatus) : "") +
      '<a class="btn btn--sm btn--ghost" href="' + scout + '">Scout this map →</a></span></div>' +
      '<p class="u-mt-3">' + result(m, card) + "</p>" +
      (flags.length ? '<ul class="dim small">' + flags.map((f) => "<li>" + esc(flagText(f)) + "</li>").join("") + "</ul>" : "") +
      (m.loaded ? '<div class="ban-row u-mt-4"><div class="ban-row__team"><span class="label">Ban listed under ' +
        esc(P.anyTeamName(card.teamA)) + "</span>" + banList(m.bans.a) + '</div><div class="ban-row__team"><span class="label">' +
        "Ban listed under " + esc(P.anyTeamName(card.teamB)) + "</span>" + banList(m.bans.b) + "</div></div>" + sheetSide
        : '<p class="dim small u-mt-3">The per-player listing for this map could not be loaded.</p>') +
      recon + "</article>";
  }
})();
