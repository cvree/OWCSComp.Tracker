/* =====================================================================
   OWCS Comp Tracker — app/mapfeed.js
   Every map, one row each, with the comp each team ran on it.

   Two records describe maps and neither is enough alone:

     * the broadcast map index (broadcast_maps.v1.js) knows every map of
       an indexed broadcast — which map, which game, both teams, and the
       second it starts — but no heroes;
     * the published export (public_data.v1.js) knows the comps, but only
       for the maps that have been read hero by hero.

   This joins them into one list of maps, grouped broadcast → series →
   map, so a fan sees every map that was played and, where it has been
   read, both line-ups beside it. A published map and an indexed window
   of the same broadcast that overlap in time are the same map, shown
   once. Nothing is inferred: a map with no read comp says so.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  if (!P) return;
  const esc = P.esc;
  const D = P.pub || {};
  const INDEX = window.OWCS_BROADCAST_MAPS || { broadcasts: {} };

  const vidOf = (url) => {
    const m = String(url || "").match(/twitch\.tv\/videos\/(\d+)/);
    return m ? m[1] : P.videoId(url);
  };
  const vodAt = (url, t) => (P.mapIndex ? P.mapIndex.vodAt(url, t) : url);

  /* The distinct line-ups each team ran on one map, in the order they
     first appeared: [teamId] -> [{ heroes, at, tier }]. */
  function compsByTeam(mapId) {
    const out = {};
    P.publishedComps((c) => c.mapId === mapId)
      .slice().sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0))
      .forEach((c) => {
        const list = out[c.teamId] || (out[c.teamId] = []);
        const key = (c.heroes || []).join("+");
        if (!list.some((x) => x.key === key)) {
          list.push({ key: key, heroes: c.heroes || [], at: c.timestamp, tier: P.auditTier(c) });
        }
      });
    return out;
  }

  let cache = null;
  function build() {
    if (cache) return cache;
    const meta = {};
    (P.discovered ? P.discovered() : []).forEach((b) => { meta[b.videoId] = b; });
    const groups = new Map();
    const group = (vid, url, title, when) => {
      if (!groups.has(vid)) {
        const m = meta[vid] || {};
        groups.set(vid, {
          vid: vid, url: url || m.url || null,
          title: (m.parsed && m.parsed.cleanTitle) || m.title || title || "Broadcast",
          event: (m.parsed && m.parsed.eventName) || null,
          when: m.publishedAt || when || null,
          year: (m.parsed && m.parsed.year) || (when ? Number(String(when).slice(0, 4)) : null),
          maps: [],
        });
      }
      return groups.get(vid);
    };

    /* 1. Published maps — the ones with comps. */
    (D.matches || []).forEach((match) => {
      const url = match.streamUrl ||
        ((match.sources || []).filter((s) => s.url)[0] || {}).url || null;
      const vid = vidOf(url) || "match:" + match.id;
      const tour = P.tournament(match.tournamentId);
      const g = group(vid, url, tour ? tour.name : null, match.scheduledAt);
      (match.maps || []).forEach((mp) => {
        const rounds = mp.rounds || [];
        const comps = compsByTeam(mp.id);
        g.maps.push({
          key: mp.id, matchId: match.id, map: mp.map, mode: mp.mode || P.mapInfo(mp.map).mode,
          game: mp.order, series: "m:" + match.id,
          teamA: match.teamA ? { id: match.teamA } : null,
          teamB: match.teamB ? { id: match.teamB } : null,
          winner: mp.winner || null,
          start: rounds.length ? rounds[0].start : null,
          end: rounds.length ? rounds[rounds.length - 1].end : null,
          check: [],
          comps: { a: comps[match.teamA] || [], b: comps[match.teamB] || [] },
        });
      });
    });

    /* 2. Every indexed map window, unless a published map already is it. */
    Object.keys(INDEX.broadcasts || {}).forEach((vid) => {
      const e = INDEX.broadcasts[vid];
      const g = group(vid, e.url, null, null);
      (e.maps || []).forEach((m) => {
        const same = g.maps.filter((x) => x.matchId && x.start != null &&
          x.start < m.end && m.start < x.end)[0];
        if (same) return;
        g.maps.push({
          key: vid + ":" + m.index, matchId: null, map: m.map,
          mode: m.map ? P.mapInfo(m.map).mode : null, game: m.game, series: "s:" + m.series,
          teamA: m.team_a || null, teamB: m.team_b || null, winner: null,
          start: m.start, end: m.end, check: m.check || [], comps: null,
        });
      });
    });

    const list = Array.from(groups.values()).filter((g) => g.maps.length);
    list.forEach((g) => {
      g.maps.sort((a, b) => (a.start == null) - (b.start == null) || (a.start || 0) - (b.start || 0));
      g.maps.forEach((m) => { m.url = g.url; m.group = g; });
    });
    /* Newest first. A broadcast with no air date sorts by its season. */
    list.sort((a, b) => String(b.when || b.year || "").localeCompare(String(a.when || a.year || "")) ||
      a.title.localeCompare(b.title));
    cache = list;
    return list;
  }

  const allMaps = () => build().reduce((acc, g) => acc.concat(g.maps), []);
  const hasComp = (m) => !!(m.comps && (m.comps.a.length || m.comps.b.length));
  const teamId = (t) => (t && (t.id || null));
  const plays = (m, id) => teamId(m.teamA) === id || teamId(m.teamB) === id;

  /* Series inside one broadcast group, in broadcast order. */
  function series(maps) {
    const out = [];
    maps.forEach((m) => {
      const last = out[out.length - 1];
      if (last && last.key === m.series) last.maps.push(m);
      else out.push({ key: m.series, maps: [m] });
    });
    return out;
  }

  /* ------------------------------------------------------------ render */
  function teamName(t) {
    if (!t) return "Not read";
    const known = t.id && P.team(t.id);
    return known ? known.name : (t.name || t.id || "Not read");
  }

  function teamLine(t, comps, won, m) {
    const known = t && t.id && P.team(t.id);
    const name = '<span class="mf-team__name">' + esc(teamName(t)) + "</span>";
    const who = known
      ? '<a class="mf-team" href="team.html?id=' + encodeURIComponent(t.id) + '">' +
        '<span class="mf-team__logo">' + P.teamMark(known, 28) + "</span>" + name + "</a>"
      : '<span class="mf-team">' + '<span class="mf-team__logo">' +
        P.teamMark(t || { id: "tbd", name: "?" }, 28) + "</span>" + name + "</span>";
    let comp;
    if (comps && comps.length) {
      const first = comps[0];
      comp = P.compStrip(first.heroes, { size: "xs", link: true, tight: true }) +
        (comps.length > 1
          ? '<a class="mf-swaps" href="game.html?id=' + encodeURIComponent(m.matchId) +
            '" title="This team changed its line-up during the map">+' + (comps.length - 1) +
            " change" + (comps.length === 2 ? "" : "s") + "</a>"
          : "");
    } else {
      comp = '<span class="mf-nocomp">Comp not read yet</span>';
    }
    return '<div class="mf-side' + (won ? " mf-side--win" : "") + '">' + who +
      (won ? '<span class="mf-win" title="Won this map">W</span>' : "<span></span>") +
      '<div class="mf-comp">' + comp + "</div></div>";
  }

  function mapCard(m) {
    const info = m.map ? P.mapInfo(m.map) : null;
    const mode = m.mode && !/unknown/i.test(m.mode) ? m.mode : null;
    const winA = m.winner && m.winner === teamId(m.teamA);
    const winB = m.winner && m.winner === teamId(m.teamB);
    const len = m.start != null && m.end != null ? Math.round(Math.max(0, m.end - m.start) / 60) : null;
    return '<article class="mf-map' + (hasComp(m) ? " mf-map--comp" : "") + '" data-map="' +
      esc(m.map || "") + '">' +
      '<header class="mf-map__head">' +
        '<span class="mf-map__game mono">' + (m.game ? "G" + m.game : "—") + "</span>" +
        '<span class="mf-map__name">' + esc(info ? info.name : "Map not read") + "</span>" +
        (mode ? '<span class="mf-map__mode">' + esc(mode) + "</span>" : "") +
        (m.check && m.check.length
          ? '<span class="mi-check" title="Some of this was read with less than 90% agreement">check</span>' : "") +
        '<span class="spacer"></span>' +
        (m.matchId ? '<a class="btn btn--sm btn--quiet" href="map.html?id=' +
          encodeURIComponent(m.key) + '" title="Time on every hero, both sources, side by side">Scout</a>' +
          '<a class="btn btn--sm btn--quiet" href="game.html?id=' +
          encodeURIComponent(m.matchId) + '">Details</a>' : "") +
        (m.url && m.start != null
          ? '<a class="btn btn--sm btn--primary" href="' + esc(vodAt(m.url, m.start)) +
            '" target="_blank" rel="noopener" aria-label="Watch ' + esc(info ? info.name : "this map") +
            '">▶ Watch' + (len ? '<span class="mf-len"> · ' + len + "m</span>" : "") + "</a>"
          : "") +
      "</header>" +
      teamLine(m.teamA, m.comps && m.comps.a, winA, m) +
      teamLine(m.teamB, m.comps && m.comps.b, winB, m) +
      "</article>";
  }

  function seriesBlock(s) {
    const first = s.maps[0];
    const a = first.teamA, b = first.teamB;
    const winsA = s.maps.filter((m) => m.winner && m.winner === teamId(a)).length;
    const winsB = s.maps.filter((m) => m.winner && m.winner === teamId(b)).length;
    const mark = (t) => '<span class="mf-series__logo">' +
      P.teamMark((t && t.id && P.team(t.id)) || t || { id: "tbd", name: "?" }, 34) + "</span>";
    return '<section class="mf-series">' +
      '<header class="mf-series__head">' +
        mark(a) + '<span class="mf-series__name">' + esc(teamName(a)) + "</span>" +
        (winsA || winsB
          ? '<span class="mf-series__score mono">' + winsA + "–" + winsB + "</span>"
          : '<span class="mf-series__vs">vs</span>') +
        '<span class="mf-series__name">' + esc(teamName(b)) + "</span>" + mark(b) +
        '<span class="mf-series__n dim small">' + s.maps.length + " map" +
          (s.maps.length === 1 ? "" : "s") + "</span>" +
      "</header>" +
      '<div class="mf-maps">' + s.maps.map(mapCard).join("") + "</div></section>";
  }

  /* groups: build() output, optionally filtered map by map. */
  function html(groups) {
    return groups.map((g) =>
      '<section class="mf-bcast">' +
        '<header class="mf-bcast__head"><h3>' + esc(g.title) + "</h3>" +
          '<span class="dim small">' + [g.when ? esc(P.fmtDate(g.when)) : "",
            g.maps.length + " map" + (g.maps.length === 1 ? "" : "s"),
            g.maps.filter(hasComp).length ? g.maps.filter(hasComp).length + " with comps" : ""]
            .filter(Boolean).join(" · ") + "</span></header>" +
        series(g.maps).map(seriesBlock).join("") +
      "</section>").join("");
  }

  /* Filter the groups map by map; empty groups drop out. */
  function filter(pred) {
    return build().map((g) => Object.assign({}, g, { maps: g.maps.filter(pred) }))
      .filter((g) => g.maps.length);
  }

  P.mapFeed = {
    groups: build, maps: allMaps, filter: filter, html: html, mapCard: mapCard,
    hasComp: hasComp, plays: plays, teamName: teamName,
  };
})();
