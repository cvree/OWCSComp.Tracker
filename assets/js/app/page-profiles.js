/* =====================================================================
   OWCS Comp Tracker — app/page-profiles.js
   Three small nested surfaces that share one shape: a header, a body,
   and an honest statement of what is not known.

     teams.html   the directory
     team.html    one team
     hero.html    one hero

   None of these is in the primary navigation. They are where you land
   from a game or a stats table, which is the only reason anyone opens
   them.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const D = P.pub;
  const esc = P.esc;
  const pct = (v) => (v == null ? "—" : Math.round(v * 100) + "%");
  const set = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };

  document.addEventListener("DOMContentLoaded", () => {
    P.evidence.wire(document);
    const page = document.body.dataset.page;
    if (page === "teams") return renderDirectory();
    if (page === "team") return renderTeam();
    if (page === "hero") return renderHero();
  });

  /* -------------------------------------------------------- directory */
  function renderDirectory() {
    document.title = "Teams — OWCS Comp Tracker";
    set("crumbs", P.breadcrumbs([{ label: "Stats", href: "stats.html" }, { label: "Teams" }]));
    set("head",
      '<div class="page-head page-head--tight"><p class="eyebrow">Directory</p>' +
      "<h1>Teams</h1><p class=\"lede\">Every team the tracker has a record of. A team with " +
      "no approved data yet is still listed — absence of coverage is information too.</p></div>");

    const teams = P.teams().slice().sort((a, b) => a.name.localeCompare(b.name));
    if (!teams.length) {
      set("body", P.empty("◇", "No teams on record",
        "Teams appear as games are submitted and matched."));
      return;
    }
    const pools = {};
    ((D && D.teamHeroPools) || []).forEach((p) => { pools[p.teamId] = p; });

    set("body", '<div class="grid grid--3">' + teams.map((t) => {
      const pool = pools[t.id];
      return '<a class="card card--hoverable rv" href="team.html?id=' + encodeURIComponent(t.id) +
        '" style="text-decoration:none;color:inherit">' +
        P.teamPlate(t.id, { size: "lg" }) +
        '<p class="dim small u-mt-4">' + esc(P.regionName(t.region)) +
        (t.roster && t.roster.length ? " · " + t.roster.length + " players on record" : "") +
        "</p>" +
        (pool
          ? '<div class="comp comp--tight u-mt-4">' +
            pool.heroes.slice(0, 8).map((h) => P.heroTile(h.hero, { size: "xs" })).join("") + "</div>"
          : '<p class="dim small u-mt-3">No approved composition data yet.</p>') +
        "</a>";
    }).join("") + "</div>");
  }

  /* ------------------------------------------------------------- team */
  function renderTeam() {
    const t = P.team(P.qs("id"));
    if (!t) {
      set("head", notFound("team", "teams.html", "See every team"));
      return;
    }
    document.title = t.name + " — OWCS Comp Tracker";
    set("crumbs", P.breadcrumbs([
      { label: "Stats", href: "stats.html" },
      { label: "Teams", href: "teams.html" },
      { label: t.name }]));

    const games = P.games.all().filter((g) => g.teamA === t.id || g.teamB === t.id);
    const pool = ((D && D.teamHeroPools) || []).filter((p) => p.teamId === t.id)[0];
    const rec = ((D && D.teamMapRecords) || []).filter((r) => r.teamId === t.id)[0];

    set("head",
      '<div class="page-head page-head--tight">' +
      '<div class="u-flex u-center u-gap-4 u-wrap">' +
      '<span class="team team--lg"><span class="team__logo">' +
      (t.logoUrl
        ? '<img src="' + esc(t.logoUrl) + '" alt="" loading="lazy" data-fallback="' +
          esc(t.code || "?") + '">'
        : P.teamCrest(t)) +
      '</span><h1 style="font-size:clamp(1.7rem,3.4vw,2.6rem)">' + esc(t.name) + "</h1></span>" +
      '<span class="chip chip--outline">' + esc(P.regionName(t.region)) + "</span>" +
      (t.status ? '<span class="chip chip--outline">' + esc(t.status) + "</span>" : "") +
      "</div>" +
      '<p class="lede u-mt-4">' +
      (pool
        ? "Seen playing " + pool.poolSize + " different hero(es) across " + pool.appearances +
          " approved map(s)."
        : "No approved composition data for this team yet — nothing it has played has been " +
          "through review.") + "</p></div>");

    const parts = [];

    if (pool) {
      parts.push('<section class="band"><div class="sec-head"><h2>Hero pool</h2>' +
        '<span class="dim small">from approved line-ups only</span></div>' +
        '<div class="table-wrap"><table class="tbl"><thead><tr><th>Hero</th>' +
        '<th class="num">Picks</th><th class="num">Win rate</th><th>Maps</th>' +
        "</tr></thead><tbody>" +
        pool.heroes.slice().sort((a, b) => b.picks - a.picks).map((h) =>
          '<tr><td><div class="u-flex u-center u-gap-3">' + P.heroTile(h.hero, { size: "xs", link: true }) +
          '<a href="hero.html?id=' + encodeURIComponent(h.hero) + '" style="text-decoration:none;' +
          'font-weight:600;color:var(--tx)">' + esc(P.hero(h.hero).name) + "</a></div></td>" +
          '<td class="num">' + h.picks + '</td><td class="num">' + pct(h.winRate) + "</td><td>" +
          h.maps.map((m) => esc(P.mapInfo(m).name)).join(", ") + "</td></tr>").join("") +
        "</tbody></table></div></section>");
    }

    if (rec && rec.maps.length) {
      parts.push('<section class="band"><div class="sec-head"><h2>Map record</h2></div>' +
        '<div class="grid grid--4">' + rec.maps.map((m) =>
          '<div class="stat"><span class="stat__k">' + esc(P.mapInfo(m.map).name) + "</span>" +
          '<span class="stat__v">' + m.wins + "–" + m.losses + "</span>" +
          '<span class="stat__note">' + esc(m.mode || "") + "</span></div>").join("") + "</div></section>");
    }

    if (t.roster && t.roster.length) {
      parts.push('<section class="band"><div class="sec-head"><h2>Roster on record</h2>' +
        '<span class="dim small">' + esc(t.rosterSource || "source not recorded") + "</span></div>" +
        '<div class="grid grid--4">' + t.roster.map((r) =>
          '<div class="card"><div style="font:600 15px/1.2 var(--f-display)">' + esc(r.handle) +
          '</div><p class="dim small u-mt-3">' + esc(r.role || "role not recorded") + "</p></div>"
        ).join("") + "</div></section>");
    }

    parts.push('<section class="band"><div class="sec-head"><h2>Games</h2></div>' +
      (games.length
        ? '<div class="grid grid--2">' + games.map((g) => P.games.card(g)).join("") + "</div>"
        : P.empty("◇", "No games on record for this team",
          '<a href="submit.html">Submit one</a> and it will appear here.')));

    set("body", parts.join(""));
  }

  /* ------------------------------------------------------------- hero */
  function renderHero() {
    const h = P.hero(P.qs("id"));
    const known = P.idx.heroes.has(h.id);
    if (!known) {
      set("head", notFound("hero", "stats.html?tab=heroes", "See every hero"));
      return;
    }
    document.title = h.name + " — OWCS Comp Tracker";
    set("crumbs", P.breadcrumbs([
      { label: "Stats", href: "stats.html" },
      { label: "Heroes", href: "stats.html?tab=heroes" },
      { label: h.name }]));

    const rate = ((D && D.heroPickRates) || []).filter((r) => r.hero === h.id)[0];
    const art = P.heroArtwork(h.id);

    set("head",
      '<div class="page-head page-head--tight">' +
      '<div class="u-flex u-center u-gap-5 u-wrap">' +
      '<span class="hero hero--lg" data-role="' + esc(h.role || "") + '">' +
      '<span class="hero__face">' + P.heroFace(h, 84) + "</span></span>" +
      "<div><h1 style=\"font-size:clamp(1.8rem,3.6vw,2.8rem)\">" + esc(h.name) + "</h1>" +
      '<p class="dim u-mt-3">' + esc(h.role || "Role not recorded") + "</p></div></div>" +
      '<p class="lede u-mt-4">' +
      (rate
        ? "Seen in " + rate.picks + " approved line-up(s) — a " + pct(rate.pickRate) +
          " pick rate across everything reviewed so far, winning " + pct(rate.winRate) + " of them."
        : "This hero has not appeared in an approved line-up yet. That means nobody has " +
          "confirmed a frame showing it — not that it was never played.") + "</p></div>");

    const parts = [];
    if (art) {
      parts.push('<section class="band"><img src="' + esc(art) + '" alt="" loading="lazy" ' +
        'style="width:100%;max-height:280px;object-fit:cover;border-radius:var(--r-3);' +
        'border:1px solid var(--line)">' +
        '<p class="dim small u-mt-3">Official hero art from Blizzard’s own hero pages, ' +
        "used for recognition only. Compositions on this site are never illustrated with " +
        "official art — those show the actual broadcast crop.</p></section>");
    }

    if (rate) {
      parts.push('<section class="band"><div class="sec-head"><h2>Where it has been seen</h2></div>' +
        '<div class="grid grid--4">' +
        stat("Picks", rate.picks, "in approved line-ups") +
        stat("Pick rate", pct(rate.pickRate), "share of approved line-ups") +
        stat("Win rate", pct(rate.winRate), rate.wins + " won, " + rate.losses + " lost") +
        stat("Swaps", rate.swappedTo + rate.swappedFrom, "swapped in or out mid-map") +
        "</div>" +
        (rate.evidence && rate.evidence.length
          ? '<div class="stack u-mt-5">' + rate.evidence.map((e) =>
            '<div class="well"><div class="u-flex u-between u-center u-gap-3 u-wrap">' +
            '<div class="u-flex u-center u-gap-3">' + P.teamPlate(e.teamId, { size: "sm", link: true }) +
            '<span class="dim small">on ' + esc(P.mapInfo(mapKey(e.mapId)).name) + "</span></div>" +
            '<div class="row"><span class="chip" data-state="' +
            (e.result === "win" ? "evidence" : "queued") + '"><span class="dot"></span>' +
            esc(e.result) + "</span>" +
            '<a class="btn btn--sm btn--quiet" href="game.html?id=' + encodeURIComponent(e.matchId) +
            '">Open game</a></div></div>' +
            '<p class="dim small u-mt-3">' + e.snapshotIds.length +
            " approved snapshot(s) behind this.</p></div>").join("") + "</div>"
          : "") + "</section>");
    }

    const teams = ((D && D.teamHeroPools) || [])
      .filter((p) => p.heroes.some((x) => x.hero === h.id));
    if (teams.length) {
      parts.push('<section class="band"><div class="sec-head"><h2>Teams that play it</h2></div>' +
        '<div class="grid grid--3">' + teams.map((p) =>
          '<a class="card card--hoverable" href="team.html?id=' + encodeURIComponent(p.teamId) +
          '" style="text-decoration:none;color:inherit">' + P.teamPlate(p.teamId) + "</a>"
        ).join("") + "</div></section>");
    }

    if (!parts.length) {
      parts.push(P.empty("◇", "Nothing to show for " + h.name + " yet",
        "This page fills in as games featuring this hero are reviewed and published."));
    }
    set("body", parts.join(""));
  }

  const mapKey = (mapId) => {
    const m = ((D && D.mapResults) || []).filter((x) => x.id === mapId)[0];
    return m ? m.map : mapId;
  };
  const stat = (k, v, note) =>
    '<div class="stat"><span class="stat__k">' + esc(k) + '</span><span class="stat__v">' +
    esc(v) + '</span><span class="stat__note">' + esc(note) + "</span></div>";

  function notFound(what, href, label) {
    return '<div class="page-head"><h1>That ' + esc(what) + " is not on record</h1>" +
      '<p class="lede">The link points at a ' + esc(what) + " this copy of the tracker has " +
      "no data for. That is usually one of two things: the address is wrong, or nothing " +
      "featuring it has been submitted and reviewed yet. Everything on this site comes " +
      "from games somebody put through the pipeline, so coverage is deliberately narrow.</p>" +
      '<div class="row u-mt-5"><a class="btn btn--primary" href="' + esc(href) + '">' +
      esc(label) + '</a><a class="btn btn--ghost" href="submit.html">Submit a game</a>' +
      '<a class="btn btn--ghost" href="index.html">Dashboard</a></div></div>';
  }
})();
