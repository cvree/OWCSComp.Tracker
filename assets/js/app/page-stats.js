/* =====================================================================
   OWCS Comp Tracker — app/page-stats.js
   Five views of one approved dataset. The old site gave heroes, comps,
   maps, teams and swaps a top-level page each; they are five questions
   about the same rows, so they are five tabs.

   Every table states its sample size. A pick rate computed from one map
   is not wrong, it is just small — saying so is the difference between
   a tracker and a rumour.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const D = P.pub;
  const esc = P.esc;
  const pct = (v) => (v == null ? "—" : Math.round(v * 100) + "%");

  document.addEventListener("DOMContentLoaded", () => {
    P.evidence.wire(document);
    renderCoverage();
    renderHeroes();
    renderComps();
    renderMaps();
    renderTeams();
    renderSwaps();
    P.initTabs(document, { key: "tab" });
  });

  /* --------------------------------------------------------- coverage */
  function renderCoverage() {
    const comps = P.publishedComps();
    const maps = new Set(comps.map((c) => c.mapId));
    const games = new Set(comps.map((c) => c.matchId));
    const teams = new Set(comps.map((c) => c.teamId));
    const el = document.getElementById("coverage");
    if (!comps.length) {
      el.innerHTML = P.note("info", "Nothing has been approved yet",
        "<p>These pages count only compositions a person has confirmed. None have been " +
        "yet, so every table below is empty — deliberately. It will fill as games are " +
        "submitted and reviewed.</p>",
        '<a class="btn btn--sm btn--primary" href="submit.html">Submit a game</a>' +
        '<a class="btn btn--sm btn--ghost" href="review.html">Open the review queue</a>');
      return;
    }
    const tile = (k, v, note) =>
      '<div class="stat"><span class="stat__k">' + esc(k) + '</span>' +
      '<span class="stat__v">' + v + "</span>" +
      '<span class="stat__note">' + esc(note) + "</span></div>";
    el.innerHTML = '<div class="grid grid--4">' +
      tile("Approved line-ups", comps.length, "each one confirmed against a frame") +
      tile("Maps covered", maps.size, "with at least one approved line-up") +
      tile("Games covered", games.size, "series with published data") +
      tile("Teams seen", teams.size, "appearing in approved line-ups") +
      "</div>";
  }

  /* ----------------------------------------------------------- heroes */
  function renderHeroes() {
    const rows = ((D && D.heroPickRates) || []).slice()
      .sort((a, b) => b.picks - a.picks || b.pickRate - a.pickRate);
    const host = document.getElementById("p-heroes");
    if (!rows.length) {
      host.innerHTML = P.empty("◇", "No hero has been seen in an approved line-up yet",
        "Hero numbers appear the moment the first game is reviewed.");
      return;
    }
    /* The table below carries every column; the chart answers the one
       question people open this tab for — who is being played — without
       making them rank twelve numbers by eye. Both, not either: the chart
       is the shape, the table is the record. */
    const picked = rows.slice(0, 14);
    const sample = P.publishedComps().length;
    const chart = P.chart ? P.chart.bars(picked.map((r) => {
      const h = P.hero(r.hero);
      return {
        label: h.name, value: r.picks, role: h.role,
        note: r.pickRate != null ? pct(r.pickRate) + " of line-ups" : "",
        href: "hero.html?id=" + encodeURIComponent(h.id),
        mark: '<span class="hero__face">' + P.heroFace(h, 26) + "</span>",
      };
    }), {
      byRole: true,
      title: "Times picked" + (rows.length > picked.length
        ? " — top " + picked.length + " of " + rows.length : ""),
      caption: "Out of " + sample + " approved line-up" + (sample === 1 ? "" : "s") +
        ". Bars are coloured by role; every value is also in the table below.",
    }) : "";

    const roleSplit = P.chart
      ? P.chart.roleSplit(
        P.publishedComps().reduce((all, c) => all.concat(c.heroes || []), []),
        { title: "Role split across every approved line-up",
          caption: "One count per hero slot, so five per line-up." })
      : "";

    host.innerHTML =
      '<p class="dim small u-mt-5">Counted across ' + rows.length +
      " hero(es) that appear in approved line-ups. Pick rate is the share of approved " +
      "line-ups the hero appears in; swap rate is how often they were swapped in or out " +
      "mid-map." + "</p>" +
      (chart ? '<div class="card u-mt-5">' + chart + "</div>" : "") +
      (roleSplit ? '<div class="card u-mt-4">' + roleSplit + "</div>" : "") +
      '<div class="table-wrap u-mt-5"><table class="tbl">' +
      "<thead><tr><th>Hero</th><th>Role</th><th class=\"num\">Picks</th>" +
      "<th class=\"num\">Pick rate</th><th class=\"num\">Win rate</th>" +
      "<th class=\"num\">Swapped</th></tr></thead><tbody>" +
      rows.map((r) => {
        const h = P.hero(r.hero);
        return "<tr>" +
          '<td><a class="hero hero--xs" href="hero.html?id=' + encodeURIComponent(h.id) +
          '" data-role="' + esc(h.role || "") + '" style="width:auto;grid-auto-flow:column;' +
          'align-items:center;gap:10px;text-decoration:none">' +
          '<span class="hero__face">' + P.heroFace(h, 28) + "</span>" +
          '<span style="font-weight:600;font-size:13.5px;color:var(--tx)">' + esc(h.name) +
          "</span></a></td>" +
          "<td>" + esc(h.role || "—") + "</td>" +
          '<td class="num">' + r.picks + "</td>" +
          '<td class="num">' + pct(r.pickRate) + "</td>" +
          '<td class="num">' + pct(r.winRate) + '<span class="dim small"> (' +
          r.wins + "–" + r.losses + ")</span></td>" +
          '<td class="num">' + (r.swappedTo + r.swappedFrom) + "</td>" +
          "</tr>";
      }).join("") +
      "</tbody><caption>Counted from every published composition — each one " +
      "carries the tier it earned (confirmed, strong or provisional) and the " +
      "frames it was read from. A hero absent from this table has not been " +
      "seen in a published line-up — not that it was never played." +
      "</caption></table></div>";
  }

  /* ------------------------------------------------------------ comps */
  function renderComps() {
    const freq = (D && D.compFrequency) || [];
    const winRates = {};
    ((D && D.compWinRate) || []).forEach((c) => { winRates[c.id] = c; });
    const host = document.getElementById("p-comps");
    if (!freq.length) {
      host.innerHTML = P.empty("◇", "No full composition has been approved yet",
        "A composition is five confirmed heroes on one team on one map.");
      return;
    }
    host.innerHTML =
      '<p class="dim small u-mt-5">Every distinct five-hero line-up that has been ' +
      "confirmed, most-played first.</p>" +
      '<div class="stack u-mt-4">' + freq.slice()
        .sort((a, b) => b.appearances - a.appearances).map((c) => {
          const w = winRates[c.id];
          return '<div class="card rv"><div class="u-flex u-between u-center u-gap-4 u-wrap">' +
            P.compStrip(c.heroes, { link: true, size: "sm" }) +
            '<div class="row">' +
            '<span class="chip chip--outline">' + c.appearances + " appearance" +
            (c.appearances === 1 ? "" : "s") + "</span>" +
            (w ? '<span class="chip" data-state="' + (w.winRate >= 0.5 ? "evidence" : "queued") +
              '"><span class="dot"></span>' + pct(w.winRate) + " on " + w.decidedMaps +
              " decided map" + (w.decidedMaps === 1 ? "" : "s") + "</span>" : "") +
            "</div></div>" +
            '<p class="dim small u-mt-4">Played by ' +
            c.teamIds.map((t) => esc((P.team(t) || {}).name || t)).join(", ") + ".</p>" +
            evidenceLine(c.evidence) + "</div>";
        }).join("") + "</div>";
  }

  function evidenceLine(evidence) {
    if (!evidence || !evidence.length) return "";
    return '<p class="dim small u-mt-3">Seen in ' + evidence.map((e) =>
      '<a href="game.html?id=' + encodeURIComponent(e.matchId) + '">' +
      esc((P.match(e.matchId) ? gameTitle(e.matchId) : e.matchId)) + "</a>").join(", ") + ".</p>";
  }
  function gameTitle(matchId) {
    const m = P.match(matchId);
    if (!m) return matchId;
    const a = P.team(m.teamA), b = P.team(m.teamB);
    return (a ? a.code || a.name : "?") + " vs " + (b ? b.code || b.name : "?");
  }

  /* ------------------------------------------------------------- maps */
  function renderMaps() {
    const stats = ((D && D.mapStats) || []).slice()
      .sort((a, b) => b.played - a.played || a.name.localeCompare(b.name));
    const modes = (D && D.modeStats) || [];
    const played = stats.filter((m) => m.played > 0);
    const host = document.getElementById("p-maps");

    host.innerHTML =
      (modes.length
        ? '<div class="grid grid--4 u-mt-5">' + modes.map((m) =>
          '<div class="stat"><span class="stat__k">' + esc(m.mode) + "</span>" +
          '<span class="stat__v">' + m.played + "</span>" +
          '<span class="stat__note">played of ' + m.maps + " map(s) in the pool</span></div>"
        ).join("") + "</div>"
        : "") +
      (played.length > 1 && P.chart
        ? '<div class="card u-mt-5">' + P.chart.bars(played.map((m) => ({
            label: m.name, value: m.played, note: m.mode,
          })), {
            title: "Maps by times played",
            caption: "Counted from approved line-ups only, so a map played on a " +
              "broadcast nobody has reviewed yet shows as zero.",
          }) + "</div>"
        : "") +
      (played.length
        ? '<div class="table-wrap u-mt-5"><table class="tbl">' +
          "<thead><tr><th>Map</th><th>Mode</th><th class=\"num\">Played</th>" +
          "<th class=\"num\">Decided</th><th>Games</th></tr></thead><tbody>" +
          played.map((m) => "<tr><td><b>" + esc(m.name) + "</b></td><td>" + esc(m.mode) +
            '</td><td class="num">' + m.played + '</td><td class="num">' + m.decided +
            "</td><td>" + m.matchIds.map((id) =>
              '<a href="game.html?id=' + encodeURIComponent(id) + '">' + esc(gameTitle(id)) +
              "</a>").join(", ") + "</td></tr>").join("") +
          "</tbody></table></div>"
        : P.empty("◇", "No map has approved data yet",
          "The full map pool is listed below so you can see what is not covered.")) +
      P.diag("The rest of the map pool (" + (stats.length - played.length) + " with no data yet)",
        '<div class="row">' + stats.filter((m) => !m.played).map((m) =>
          '<span class="chip chip--outline">' + esc(m.name) + '<span class="dim"> · ' +
          esc(m.mode) + "</span></span>").join("") + "</div>");
  }

  /* ------------------------------------------------------------ teams */
  function renderTeams() {
    const pools = (D && D.teamHeroPools) || [];
    const records = {};
    ((D && D.teamMapRecords) || []).forEach((r) => { records[r.teamId] = r; });
    const host = document.getElementById("p-teams");
    if (!pools.length) {
      host.innerHTML = P.empty("◇", "No team has approved data yet",
        'The <a href="teams.html">team directory</a> lists everyone the tracker knows about.');
      return;
    }
    host.innerHTML =
      '<p class="dim small u-mt-5">What each team has actually been seen playing. ' +
      '<a href="teams.html">Full team directory →</a></p>' +
      '<div class="stack u-mt-4">' + pools.map((p) => {
        const rec = records[p.teamId];
        return '<div class="card rv"><div class="u-flex u-between u-center u-gap-4 u-wrap">' +
          P.teamPlate(p.teamId, { link: true, size: "lg" }) +
          '<span class="chip chip--outline">' + p.poolSize + " hero(es) across " +
          p.appearances + " approved map(s)</span></div>" +
          '<div class="comp u-mt-4">' + p.heroes.map((h) =>
            P.heroTile(h.hero, { size: "sm", link: true })).join("") + "</div>" +
          (rec && rec.maps.length
            ? '<p class="dim small u-mt-4">Map record: ' + rec.maps.map((m) =>
              esc(P.mapInfo(m.map).name) + " " + m.wins + "–" + m.losses).join(" · ") + "</p>"
            : "") + "</div>";
      }).join("") + "</div>";
  }

  /* ------------------------------------------------------------ swaps */
  function renderSwaps() {
    const confirmed = ((D && D.heroSwaps) || []).filter((s) => s.status === "confirmed");
    const rejected = (D && D.rejectedSwaps) || [];
    const host = document.getElementById("p-swaps");
    if (!confirmed.length && !rejected.length) {
      host.innerHTML = P.empty("◇", "No swap evidence yet",
        "Mid-map hero swaps appear here once a game has been reviewed.");
      return;
    }
    const row = (s, ok) =>
      '<div class="well"><div class="u-flex u-center u-gap-3 u-wrap">' +
      P.teamPlate(s.teamId, { size: "sm", short: true }) +
      '<span class="diff"><span class="diff__was">' + P.heroTile(s.fromHero, { size: "sm" }) +
      '</span><span class="diff__arrow">→</span>' + P.heroTile(s.toHero, { size: "sm" }) + "</span>" +
      '<span class="dim small">' + esc(P.fmtClock(s.offset)) + " · slot " + esc(s.slot) + "</span>" +
      '<span class="spacer"></span>' +
      (s.confidence != null ? P.confMeter(s.confidence) : "") +
      '<a class="btn btn--sm btn--quiet" href="game.html?id=' + encodeURIComponent(s.matchId) +
      '">Open game</a></div>' +
      (!ok && s.reason ? '<p class="dim small u-mt-3">' + esc(s.reason) + "</p>" : "") +
      "</div>";

    host.innerHTML =
      '<p class="dim small u-mt-5">Both halves of the record. A swap only counts once a ' +
      "person has confirmed it; the rejected candidates stay visible because hiding them " +
      "would make the detector look more certain than it is.</p>" +
      (confirmed.length
        ? '<h2 class="u-mt-6" style="font-size:1.1rem">Confirmed swaps (' + confirmed.length +
          ')</h2><div class="stack u-mt-4">' + confirmed.map((s) => row(s, true)).join("") + "</div>"
        : "") +
      (rejected.length
        ? '<h2 class="u-mt-6" style="font-size:1.1rem">Rejected candidates (' + rejected.length +
          ')</h2><div class="stack u-mt-4">' + rejected.map((s) => row(s, false)).join("") + "</div>"
        : "");
  }
})();
