/* =====================================================================
   OWCS Comp Tracker — app/scouting.js
   A recent-match scouting report for one team, from both sources.

   The last N maps the team played (newest first, whichever source recorded
   them), then what those maps say: map comfort, bans listed for and against
   them, heroes (VOD seconds beside spreadsheet listings, never added
   together) and comps. The window is always stated — "last 10 maps,
   2026-06-02 → 2026-07-12" — and so is how many of those maps each source
   actually covers, because a report built on 2 VOD maps and 8 spreadsheet
   rows is a different thing from one built on 10 VOD timelines.

   Used by team.html and the "Scout a team" tab of meta.html.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  if (!P || !P.metaStats) return;
  const esc = P.esc;
  const M = P.metaStats;
  const pct = (v) => P.pctHonest(v);

  function heroCount(list, label) {
    if (!list.length) return '<p class="dim small">' + esc(label) + ": none recorded.</p>";
    return '<p class="label">' + esc(label) + '</p><div class="row">' + list.slice(0, 8).map((b) =>
      '<span class="scout-chip">' + P.heroTile(b.hero, { size: "xs" }) + '<span class="mono small">×' + b.count +
      "</span></span>").join("") + "</div>";
  }

  P.scoutingHtml = (team, n, entries) => {
    entries = entries || P.metaEntries();
    const r = M.scouting(entries, team, n || 10);
    if (!r.available) {
      return P.empty("◇", "No maps on record for this team",
        "Neither the VOD readings nor the imported spreadsheet record a map for it yet.");
    }
    /* "Last N" needs an order. VOD maps carry a date; the spreadsheet
       records none, so its maps are ordered by the sheet's own stage /
       week / day / game — and the report says which order it used. */
    const order = r.ordering === "sheet"
      ? " · ordered by the spreadsheet’s stage, week, day and game (it records no dates)"
      : r.ordering === "mixed"
        ? " · dated VOD maps first, then undated spreadsheet maps by stage, week, day and game"
        : "";
    const span = r.fromLabel ? " · " + esc(r.fromLabel) + " → " + esc(r.toLabel) : "";
    const head = '<p class="dim small">Last <b>' + r.window + "</b> of " + r.available + " recorded map" +
      (r.available === 1 ? "" : "s") + span + order + " · " +
      r.sources.vod + " with a VOD timeline " + P.srcBadge("vod") + " · " + r.sources.sheet +
      " in the spreadsheet " + P.srcBadge("sheet") + "</p>";
    const maps = '<div class="table-wrap"><table class="tbl tbl--compact"><thead><tr><th>When</th><th>Map</th>' +
      "<th>Opponent</th><th>Result</th><th>Pick</th><th>Sources</th></tr></thead><tbody>" +
      r.maps.map((e) => {
        const opp = e.teamA === team ? e.teamB : e.teamA;
        const res = e.winnerConflict ? '<span class="trust" data-trust="failed">sources disagree</span>'
          : !e.winner ? '<span class="dim">—</span>' : e.winner === team ? "<b>W</b>" : "L";
        const when = e.date ? '<span class="mono">' + esc(e.date) + "</span>"
          : e.label ? '<span title="The spreadsheet records no date; this is its own game label">' +
            esc(e.label) + "</span>" : "—";
        return '<tr><td class="small">' + when + '</td><td><a href="' + P.mapPageHref(e) + '">' +
          esc(e.map ? P.mapInfo(e.map).name : (e.sheet && e.sheet.mapRaw) || "?") + "</a></td><td>" +
          P.anyTeamPlate(opp, { size: "sm" }) + "</td><td>" + res + "</td><td>" +
          (e.pickedBy ? (e.pickedBy === team ? "own pick" : "their pick") : '<span class="dim">—</span>') +
          "</td><td>" + (e.vod ? P.srcBadge("vod", "VOD") : "") + (e.sheet ? P.srcBadge("sheet", "Sheet") : "") +
          "</td></tr>";
      }).join("") + "</tbody></table></div>";
    const comfort = '<div class="table-wrap"><table class="tbl tbl--compact"><thead><tr><th>Map</th>' +
      '<th class="num">Played</th><th class="num">Own picks</th><th>Observed map-win share</th></tr></thead><tbody>' +
      r.comfort.map((c) => "<tr><td>" + esc(P.mapInfo(c.map).name) + '</td><td class="num">' + c.played +
        '</td><td class="num">' + c.picks + "</td><td>" + (c.decided ? pct(c.wins / c.decided) + " " : "— ") +
        P.sampleNote(c.decided, "decided map") + "</td></tr>").join("") + "</tbody></table></div>";
    const heroes = '<div class="table-wrap"><table class="tbl tbl--compact"><thead><tr><th>Hero</th>' +
      '<th class="num">' + P.srcBadge("vod", "VOD time") + '</th><th class="num">' + P.srcBadge("sheet", "Sheet maps listed") +
      "</th></tr></thead><tbody>" + r.heroes.slice(0, 15).map((h) => "<tr><td><span class=\"u-flex u-center u-gap-3\">" + P.heroTile(h.hero, { size: "xs", link: true }) +
        esc(P.hero(h.hero).name) + '</span></td><td class="num mono">' + (h.vodSeconds ? P.fmtClock(h.vodSeconds) : "—") + '</td><td class="num">' +
        (h.sheetMaps || "—") + "</td></tr>").join("") + "</tbody></table></div>" +
      '<p class="dim small">VOD time is seconds on the field across the window’s VOD timelines (' +
      P.fmtClock(r.vodKnownSeconds) + " known player-time). Sheet maps listed counts maps where the spreadsheet " +
      "lists the hero for this team — an observation, not time.</p>";
    const comps = r.comps.length ? r.comps.slice(0, 6).map((c) => '<div class="lu">' +
      P.compStrip(c.heroes, { size: "xs", tight: true }) +
      (c.vodSeconds ? '<span class="small">' + P.srcBadge("vod", "VOD") + " " + P.fmtClock(c.vodSeconds) + "</span>" : "") +
      (c.sheet ? '<span class="small">' + P.srcBadge("sheet", "Sheet") + " listed on " + c.sheet + " map" +
        (c.sheet === 1 ? "" : "s") + "</span>" : "") + "</div>").join("")
      : '<p class="dim small">No complete five-hero line-up recorded in this window.</p>';
    return head + '<div class="stack u-mt-4">' +
      '<div class="card"><h3>Recent maps</h3>' + maps + "</div>" +
      '<div class="scout-grid">' +
        '<div class="card"><h3>Map comfort</h3>' + comfort + "</div>" +
        '<div class="card"><h3>Bans <span class="dim small">as the spreadsheet lists them</span></h3>' +
          heroCount(r.bansFor, "Listed under this team") + heroCount(r.bansAgainst, "Listed under its opponents") + "</div>" +
        '<div class="card"><h3>Hero tendencies</h3>' + heroes + "</div>" +
        '<div class="card"><h3>Comp tendencies</h3>' + comps + "</div>" +
      "</div></div>";
  };
})();
