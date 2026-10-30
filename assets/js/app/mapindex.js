/* =====================================================================
   OWCS Comp Tracker — app/mapindex.js
   Every map of a broadcast, one click from playing.

   An OWCS broadcast runs six to twelve hours. pipeline/live_ingest.py
   finds each map's window by sampling the broadcast remotely, and
   pipeline/live_identity.py reads the map, the game number and both team
   names off the broadcast's own HUD — nobody involved. This draws that
   index: series by series, map by map, each with a link that opens the
   VOD at the second the map starts.

   What is shown as certain is only what was read clearly. A team the
   dataset does not know yet keeps the name the broadcast printed, marked
   with "?"; a value fewer than nine in ten frames agreed on carries a
   "check" mark (the owner's rule: 90% or less, a person looks).
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  if (!P) return;
  const esc = P.esc;
  const DATA = window.OWCS_BROADCAST_MAPS || { broadcasts: {} };

  const entry = (videoId) => (DATA.broadcasts || {})[videoId] || null;
  const ids = () => Object.keys(DATA.broadcasts || {});

  /* A link that opens the VOD at `t` seconds, on Twitch or YouTube. */
  function vodAt(url, t) {
    const s = Math.max(0, Math.round(t));
    if (/twitch\.tv\/videos\//.test(url || "")) {
      const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
      return url.split("?")[0] + "?t=" + h + "h" + m + "m" + sec + "s";
    }
    if (/youtu/.test(url || "")) return url + (url.indexOf("?") >= 0 ? "&" : "?") + "t=" + s + "s";
    return url;
  }

  function teamHtml(t, cls) {
    if (!t) return '<span class="mi-team mi-team--none ' + (cls || "") + '">Not read</span>';
    const known = t.id && P.team(t.id);
    if (known) {
      return '<a class="mi-team ' + (cls || "") + '" href="team.html?id=' + encodeURIComponent(t.id) + '">' +
        '<span class="mi-team__logo">' + (known.logoUrl
          ? '<img src="' + esc(known.logoUrl) + '" alt="" width="20" height="20" loading="lazy">'
          : P.teamCrest(known)) + "</span>" +
        '<span class="mi-team__name">' + esc(known.name) + "</span></a>";
    }
    return '<span class="mi-team mi-team--new ' + (cls || "") + '" title="Read from the broadcast. ' +
      'This team is not in the dataset yet, so a person confirms the name once.">' +
      '<span class="mi-team__name">' + esc(t.name) + '</span><span class="mi-team__q" aria-label="not confirmed">?</span></span>';
  }

  const CHECK_WORDS = { map: "the map", game: "the game number", team_a: "the left team", team_b: "the right team" };

  function series(maps) {
    const out = [];
    maps.forEach((m) => {
      const last = out[out.length - 1];
      if (last && m.series != null && last.key === m.series) last.maps.push(m);
      else out.push({ key: m.series, maps: [m] });
    });
    return out;
  }

  /* The two teams of a series, in the order they were first shown. */
  function seriesTeams(s) {
    const first = s.maps[0];
    return [first.team_a, first.team_b];
  }

  function mapRow(m, url) {
    const info = m.map ? P.mapInfo(m.map) : null;
    const len = Math.max(0, m.end - m.start);
    const check = (m.check || []).map((k) => CHECK_WORDS[k] || k);
    return '<li class="mi-map">' +
      '<span class="mi-map__game mono">' + (m.game ? "G" + m.game : "—") + "</span>" +
      '<span class="mi-map__name">' + esc(info ? info.name : "Map not read") +
        (info && info.mode && !/unknown/i.test(info.mode)
          ? '<span class="mi-map__mode">' + esc(info.mode) + "</span>" : "") + "</span>" +
      '<span class="mi-map__vs small">' + teamHtml(m.team_a) + '<span class="dim">vs</span>' + teamHtml(m.team_b) + "</span>" +
      '<span class="mi-map__when mono small dim" title="Starts at ' + esc(P.fmtClock(m.start)) + " into the broadcast; about " +
        Math.round(len / 60) + ' minutes of play">' + esc(P.fmtClock(m.start)) + " · " + Math.round(len / 60) + "m</span>" +
      (check.length ? '<span class="mi-check" title="Fewer than nine in ten frames agreed on ' +
        esc(check.join(" and ")) + ' — worth a look">check</span>' : "<span></span>") +
      (url ? '<a class="btn btn--sm btn--primary mi-map__watch" href="' + esc(vodAt(url, m.start)) +
        '" target="_blank" rel="noopener" aria-label="Watch ' + esc(info ? info.name : "this map") +
        " from " + esc(P.fmtClock(m.start)) + '">▶ Watch</a>' : "") +
      "</li>";
  }

  /* opts.limit: show only the first N series (the dashboard's teaser). */
  function html(videoId, opts) {
    opts = opts || {};
    const e = entry(videoId);
    if (!e || !(e.maps || []).length) return "";
    let groups = series(e.maps);
    const more = opts.limit && groups.length > opts.limit ? groups.length - opts.limit : 0;
    if (opts.limit) groups = groups.slice(0, opts.limit);
    return '<div class="mi">' + groups.map((s) => {
      const [a, b] = seriesTeams(s);
      return '<section class="mi-series">' +
        '<header class="mi-series__head">' + teamHtml(a, "mi-team--lg") + '<span class="dim">vs</span>' +
          teamHtml(b, "mi-team--lg") + '<span class="dim small">' + s.maps.length + " map" +
          (s.maps.length === 1 ? "" : "s") + "</span></header>" +
        '<ol class="mi-maps">' + s.maps.map((m) => mapRow(m, e.url)).join("") + "</ol></section>";
    }).join("") +
    (more ? '<p class="dim small mi-more">+ ' + more + " more series in this broadcast</p>" : "") + "</div>";
  }

  /* One line for a list row: "19 maps · 8 series". */
  function summary(videoId) {
    const e = entry(videoId);
    if (!e || !(e.maps || []).length) return "";
    return e.maps.length + " maps · " + series(e.maps).length + " series indexed";
  }

  /* Every indexed map a team played, newest broadcast first:
     [{ videoId, url, title, m, opponent }]. */
  function forTeam(teamId) {
    const titles = {};
    (P.discovered ? P.discovered() : []).forEach((b) => { titles[b.videoId] = b; });
    const out = [];
    ids().forEach((vid) => {
      const e = entry(vid);
      (e.maps || []).forEach((m) => {
        const a = m.team_a && m.team_a.id === teamId, b = m.team_b && m.team_b.id === teamId;
        if (!a && !b) return;
        const meta = titles[vid] || {};
        out.push({ videoId: vid, url: e.url, m: m, opponent: a ? m.team_b : m.team_a,
          title: (meta.parsed && meta.parsed.cleanTitle) || meta.title || "Broadcast " + vid,
          when: meta.publishedAt || "" });
      });
    });
    return out.sort((x, y) => String(y.when).localeCompare(String(x.when)) || x.m.start - y.m.start);
  }

  /* A team's maps as rows, grouped by broadcast. */
  function teamHtml_(teamId) {
    const rows = forTeam(teamId);
    if (!rows.length) return "";
    const groups = [];
    rows.forEach((r) => {
      const g = groups[groups.length - 1];
      if (g && g.videoId === r.videoId) g.rows.push(r);
      else groups.push({ videoId: r.videoId, title: r.title, rows: [r] });
    });
    return '<div class="mi">' + groups.map((g) =>
      '<section class="mi-series"><header class="mi-series__head">' +
        '<a class="mi-series__title" href="game.html?video=' + encodeURIComponent(g.videoId) + '#maps">' +
        esc(g.title) + '</a><span class="dim small">' + g.rows.length + " map" +
        (g.rows.length === 1 ? "" : "s") + "</span></header>" +
      '<ol class="mi-maps">' + g.rows.map((r) => {
        const info = r.m.map ? P.mapInfo(r.m.map) : null;
        return '<li class="mi-map mi-map--team">' +
          '<span class="mi-map__game mono">' + (r.m.game ? "G" + r.m.game : "—") + "</span>" +
          '<span class="mi-map__name">' + esc(info ? info.name : "Map not read") +
            (info && info.mode && !/unknown/i.test(info.mode)
              ? '<span class="mi-map__mode">' + esc(info.mode) + "</span>" : "") + "</span>" +
          '<span class="mi-map__vs small"><span class="dim">vs</span>' + teamHtml(r.opponent) + "</span>" +
          '<span class="mi-map__when mono small dim">' + esc(P.fmtClock(r.m.start)) + " · " +
            Math.round(Math.max(0, r.m.end - r.m.start) / 60) + "m</span>" +
          '<span></span>' +
          '<a class="btn btn--sm btn--primary mi-map__watch" href="' + esc(vodAt(r.url, r.m.start)) +
            '" target="_blank" rel="noopener">▶ Watch</a></li>';
      }).join("") + "</ol></section>").join("") + "</div>";
  }

  P.mapIndex = { entry: entry, ids: ids, html: html, summary: summary, vodAt: vodAt,
    forTeam: forTeam, teamHtml: teamHtml_ };
})();
