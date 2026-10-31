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
    set("crumbs", P.breadcrumbs([{ label: "Maps", href: "index.html" }, { label: "Teams" }]));
    set("head",
      '<div class="page-head page-head--tight"><p class="eyebrow">Directory</p>' +
      "<h1>Teams</h1><p class=\"lede\">Every org seen on an OWCS broadcast, with its mark, " +
      "how many maps of it there are to watch, and the heroes it has been seen playing.</p></div>");

    const teams = P.teams().slice().sort((a, b) => a.name.localeCompare(b.name));
    if (!teams.length) {
      set("body", P.empty("◇", "No teams on record",
        "Teams appear as games are submitted and matched."));
      return;
    }
    const pools = {};
    ((D && D.teamHeroPools) || []).forEach((p) => { pools[p.teamId] = p; });

    const known = (r) => r && !/^(unknown|all)$/i.test(r);
    /* Orgs with maps to show lead, most maps first. An org the dataset
       knows of but has no map for yet is listed once, collapsed — it is
       on record, it just has nothing to watch. */
    const mapsOf = (t) => (P.mapFeed ? P.mapFeed.maps().filter((m) => P.mapFeed.plays(m, t.id)) : []);
    const rows = teams.map((t) => ({ t: t, maps: mapsOf(t) }))
      .sort((a, b) => b.maps.length - a.maps.length || a.t.name.localeCompare(b.t.name));
    const active = rows.filter((r) => r.maps.length);
    const quiet = rows.filter((r) => !r.maps.length);
    const card = (r) => {
      const t = r.t;
      const pool = pools[t.id];
      const read = r.maps.filter(P.mapFeed.hasComp).length;
      return '<a class="card card--hoverable rv" href="team.html?id=' + encodeURIComponent(t.id) +
        '" style="text-decoration:none;color:inherit">' +
        P.teamPlate(t.id, { size: "lg" }) +
        '<p class="dim small u-mt-4">' + [known(t.region) ? esc(P.regionName(t.region)) : "",
          r.maps.length ? "▶ " + r.maps.length + " map" + (r.maps.length === 1 ? "" : "s") : "",
          read ? read + " with comps" : ""]
          .filter(Boolean).join(" · ") + "</p>" +
        (pool
          ? '<div class="comp comp--tight u-mt-4">' +
            pool.heroes.slice(0, 8).map((h) => P.heroTile(h.hero, { size: "xs" })).join("") + "</div>"
          : "") +
        "</a>";
    };
    set("body", '<div class="grid grid--3">' + active.map(card).join("") + "</div>" +
      (quiet.length
        ? '<div class="u-mt-6">' + P.diag("Also on record, no maps yet (" + quiet.length + ")",
          '<div class="row">' + quiet.map((r) =>
            P.teamPlate(r.t.id, { link: true, size: "sm" })).join("") + "</div>") + "</div>"
        : ""));
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
      { label: "Maps", href: "index.html" },
      { label: "Teams", href: "teams.html" },
      { label: t.name }]));

    const games = P.games.all().filter((g) => g.teamA === t.id || g.teamB === t.id);
    const seen = P.mapIndex ? P.mapIndex.forTeam(t.id) : [];
    const pool = ((D && D.teamHeroPools) || []).filter((p) => p.teamId === t.id)[0];
    const rec = ((D && D.teamMapRecords) || []).filter((r) => r.teamId === t.id)[0];

    set("head",
      '<div class="page-head page-head--tight">' +
      '<div class="u-flex u-center u-gap-4 u-wrap">' +
      '<span class="team team--lg"><span class="team__logo">' +
      P.teamMark(t) +
      '</span><h1 style="font-size:clamp(1.7rem,3.4vw,2.6rem)">' + esc(t.name) + "</h1></span>" +
      (t.region && !/^(unknown|all)$/i.test(t.region)
        ? '<span class="chip chip--outline">' + esc(P.regionName(t.region)) + "</span>" : "") +
      (t.status ? '<span class="chip chip--outline">' + esc(t.status) + "</span>" : "") +
      "</div>" +
      '<p class="lede u-mt-4">' +
      (pool
        ? "Seen playing " + pool.poolSize + " different hero(es) across " + pool.appearances +
          " map(s) with comps read."
        : seen.length
          ? "On " + seen.length + " indexed map" + (seen.length === 1 ? "" : "s") +
            " — every one a click from playing. Compositions follow once the tracker reads them."
          : "No map of this team has been indexed or read yet.") + "</p></div>");

    const parts = [];
    const feed = P.mapFeed ? P.mapFeed.filter((m) => P.mapFeed.plays(m, t.id)) : [];
    if (feed.length) {
      const n = feed.reduce((a, g) => a + g.maps.length, 0);
      parts.push('<section class="band" id="maps"><div class="sec-head"><h2>Map by map</h2>' +
        '<span class="dim small">' + n + " map" + (n === 1 ? "" : "s") +
        " · both comps where read · ▶ opens the VOD at the map</span></div>" +
        P.mapFeed.html(feed) + "</section>");
    } else if (seen.length) {
      parts.push('<section class="band" id="maps"><div class="sec-head"><h2>Every map, ready to watch</h2>' +
        '<span class="dim small">read off the broadcast, nobody involved</span></div>' +
        P.mapIndex.teamHtml(t.id) + "</section>");
    }

    /* Recent-match scouting report — both sources, each labelled, the
       window and each source's share of it always stated. */
    if (P.scoutingHtml && P.metaEntries) {
      const n = Number(P.qs("scout") || 10) === 5 ? 5 : 10;
      const link = (k) => '<a class="btn btn--sm ' + (k === n ? "btn--primary" : "btn--ghost") +
        '" href="team.html?id=' + encodeURIComponent(t.id) + "&scout=" + k + '#scout"' +
        (k === n ? ' aria-current="true"' : "") + ">Last " + k + " maps</a>";
      parts.push('<section class="band" id="scout"><div class="sec-head"><h2>Scouting report</h2>' +
        '<span class="row">' + link(5) + link(10) +
        '<a class="sec-link" href="meta.html?tab=scout&team=' + encodeURIComponent(t.id) + '">Filter it →</a></span></div>' +
        P.scoutingHtml(t.id, n) + "</section>");
    }

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

    if (P.isAdmin()) {
      parts.push('<section class="band"><div class="sec-head"><h2>Games</h2></div>' +
        (games.length
          ? '<div class="grid grid--2">' + games.map((g) => P.games.card(g)).join("") + "</div>"
          : P.empty("◇", "No games on record for this team",
            '<a href="submit.html">Submit one</a> and it will appear here.')));
    } else if (!parts.length) {
      parts.push(P.empty("◇", "Nothing indexed for this team yet",
        "Its maps appear here as soon as a broadcast it played on is indexed."));
    }

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
      { label: "Comps", href: "stats.html" },
      { label: "Heroes", href: "stats.html?tab=heroes" },
      { label: h.name }]));

    const rate = ((D && D.heroPickRates) || []).filter((r) => r.hero === h.id)[0];
    const art = P.heroCard(h.id);

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
        'width="640" height="427" ' +
        'style="width:100%;aspect-ratio:3/2;max-height:340px;object-fit:cover;' +
        'object-position:50% 30%;border-radius:var(--r-3);border:1px solid var(--line)">' +
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
      esc(label) + '</a><a class="btn btn--ghost" href="index.html">Every map</a></div></div>';
  }
})();
