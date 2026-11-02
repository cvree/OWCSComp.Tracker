/* =====================================================================
   OWCS Comp Tracker — app/page-home.js
   The home snapshot, for a high-rank / FACEIT player who wants the best,
   highest-value picks now:

     1. S tier right now      — per role, recent window, rising / falling
     2. Most banned           — ban rate, the usual ban per role, pairs
     3. Every map             — most-run comp (details on hover), S-tier
                                heroes, the usual tank / damage / support ban
     4. Comps that made noise — the biggest wins, upsets accented
   (the broadcast VOD feed, page-maps.js, stays below)

   Window + region live in the URL (?w=recent|30|stage|season&region=…).
   Maths: app/tierstats.js. Shared drawing: app/tierui.js.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const T = window.OWCS_SHEET_TIERS;
  const S = P && P.tierStats;
  const U = P && P.tierUI;
  if (!P || !S || !U) return;
  const esc = P.esc;
  const ROLES = U.roles();
  const pct = U.pct;

  const ROLE_ICON = { Tank: "⛨", Damage: "✦", Support: "✚" };

  const state = { w: P.qs("w") || "recent", region: P.qs("region") || "" };
  let cur = null, prev = null, label = "", link = "";

  /* ----------------------------------------------------------- window */
  function run() {
    const win = U.runWindow(state, ROLES);
    cur = win.cur; prev = win.prev; label = win.label; link = win.link;
  }

  const tiersHref = (extra) => "tiers.html?" + link + (extra ? "&" + extra : "");

  /* -------------------------------------------------------- controls */
  function renderControls() { P.mount("hm-controls", U.controlsHtml(state)); }
  function renderWindowLine() { P.mount("hm-window", U.windowLineHtml({ cur: cur, prev: prev, label: label }, state)); }

  /* ---------------------------------------------------------- S tier */
  function trendChip(now, before) {
    if (before == null) return '<span class="trend" data-dir="new" title="Not picked in the previous window">NEW</span>';
    const d = Math.round((now - before) * 100);
    if (Math.abs(d) < 2) return '<span class="trend" data-dir="flat" title="Pick share about the same as the previous window">±' + Math.abs(d) + "</span>";
    return '<span class="trend" data-dir="' + (d > 0 ? "up" : "down") + '" title="Pick share vs the previous window, in points">' +
      (d > 0 ? "▲ " : "▼ ") + Math.abs(d) + "</span>";
  }

  function renderSTier() {
    const rows = cur.heroTiers("*");
    const before = new Map(prev ? prev.heroTiers("*").map((r) => [r.id, r.pickShare]) : []);
    const card = (r) => {
      const art = P.heroCard(r.id) || P.heroArtwork(r.id);
      const h = P.hero(r.id);
      return '<a class="hm-hero" data-tier="' + esc(r.tier) + '" style="--role-h:' + P.roleHue(h.role) + '" href="' +
        esc(tiersHref("role=" + encodeURIComponent(h.role))) + '" data-hero="' + esc(r.id) + '">' +
        (art ? '<img class="hm-hero__art" src="' + esc(art) + '" alt="" loading="lazy" decoding="async">'
          : '<span class="hm-hero__mono" aria-hidden="true">' + esc(P.heroInitials(h)) + "</span>") +
        '<span class="hm-hero__shade"></span>' +
        '<span class="hm-hero__tier">' + esc(r.tier) + "</span>" +
        (prev ? '<span class="hm-hero__trend">' + trendChip(r.pickShare, before.get(r.id)) + "</span>" : "") +
        '<span class="hm-hero__body"><span class="hm-hero__name">' + esc(h.name) + "</span>" +
        '<span class="hm-hero__stats"><span><b>' + pct(r.pickShare) + "</b> pick</span><span><b>" + pct(r.adjWin, 1) +
        "</b> adj. win</span>" + (r.banShare >= 0.03 ? "<span><b>" + pct(r.banShare) + "</b> ban</span>" : "") + "</span></span></a>";
    };
    const mini = (r) => '<a class="hm-mini" href="' + esc(tiersHref("role=" + encodeURIComponent(r.role))) + '" title="' +
      esc(P.hero(r.id).name + " — " + pct(r.pickShare) + " pick, " + pct(r.adjWin, 1) + " adj. win") + '">' +
      P.heroFace(P.hero(r.id), 32) + '<span class="hm-mini__name">' + esc(P.hero(r.id).name) + "</span>" +
      '<span class="hm-mini__v">' + pct(r.pickShare) + "</span>" + (prev ? trendChip(r.pickShare, before.get(r.id)) : "") + "</a>";
    const cols = U.ROLE_ORDER.map((role) => {
      const list = rows.filter((r) => r.role === role && !r.lowSample).sort((a, b) => b.score - a.score);
      let top = list.filter((r) => r.tier === "S");
      if (!top.length) top = list.slice(0, 1);
      const next = list.filter((r) => top.indexOf(r) < 0 && (r.tier === "A" || r.tier === "S")).slice(0, 5);
      return '<div class="hm-role"><h3 class="hm-role__hd"><span aria-hidden="true">' + ROLE_ICON[role] + "</span> " + esc(role) + "</h3>" +
        '<div class="hm-role__s">' + top.slice(0, 3).map(card).join("") + "</div>" +
        (next.length ? '<p class="hm-role__k">Also strong</p><div class="hm-role__a">' + next.map(mini).join("") + "</div>" : "") +
        "</div>";
    }).join("");
    P.mount("hm-stier", '<div class="hm-roles">' + cols + "</div>");
  }

  /* ------------------------------------------------------------ bans */
  function renderBans() {
    const bp = cur.banProfile("*");
    if (!bp || !bp.rows.length) { P.mount("hm-bans", P.empty("⊘", "No bans recorded in this window", "")); return; }
    const before = new Map(prev && prev.banProfile("*") ? prev.banProfile("*").rows.map((r) => [r.id, r.rate]) : []);
    const top = bp.rows.slice(0, 10);
    const max = top[0].rate || 1;
    const trend = (r) => {
      if (!prev) return "";
      const b = before.get(r.id);
      if (b == null) return '<span class="trend" data-dir="new">NEW</span>';
      const d = Math.round((r.rate - b) * 100);
      return Math.abs(d) < 2 ? '<span class="trend" data-dir="flat">±' + Math.abs(d) + "</span>"
        : '<span class="trend" data-dir="' + (d > 0 ? "up" : "down") + '">' + (d > 0 ? "▲ " : "▼ ") + Math.abs(d) + "</span>";
    };
    const row = (r, i) => {
      const h = P.hero(r.id);
      const tipBody = '<strong class="tip__title">' + esc(h.name) + " — banned on " + pct(r.rate) + " of maps</strong>" +
        '<span class="tip__line">Teams that banned it won <b>' + pct(r.afterWin) + "</b> of those maps (" +
        pct(r.afterAdj, 1) + " weighed for opponent strength and stakes).</span>" +
        (r.teams.length ? '<span class="tip__line">Banned most by ' + r.teams.slice(0, 3).map((t) => esc(U.teamName(t[0])) + " (" + t[1] + ")").join(", ") + "</span>" : "");
      return '<li class="hm-ban">' + U.tip(
        '<span class="hm-ban__rank">' + (i + 1) + "</span>" +
        '<span class="hm-ban__face" style="--role-h:' + P.roleHue(h.role) + '">' + P.heroFace(h, 44) + "</span>" +
        '<span class="hm-ban__who"><span class="hm-ban__name">' + esc(h.name) + '</span><span class="hm-ban__role">' + esc(h.role) + "</span></span>" +
        '<span class="hm-ban__bar"><span style="width:' + Math.round((r.rate / max) * 100) + '%"></span></span>' +
        '<span class="hm-ban__rate">' + pct(r.rate) + "</span>" + trend(r), tipBody, { cls: "tip--row" }) + "</li>";
    };
    const roleCard = (role) => {
      const x = bp.byRole[role];
      if (!x) return "";
      return '<div class="hm-banrole"><p class="hm-banrole__hd"><span aria-hidden="true">' + ROLE_ICON[role] + "</span> " + esc(role) +
        ' ban <span class="dim">· ' + pct(x.share) + " of bans</span></p>" +
        x.top.map((r) => '<p class="hm-banrole__row">' + P.heroFace(P.hero(r.id), 28) + "<span>" + esc(P.hero(r.id).name) +
          "</span><b>" + pct(r.rate) + "</b></p>").join("") + "</div>";
    };
    P.mount("hm-bans",
      '<div class="hm-bans">' +
      '<ol class="hm-banlist" aria-label="Most banned heroes">' + top.map(row).join("") + "</ol>" +
      '<div class="hm-banside">' +
      '<p class="small hm-rule"><b>How bans work:</b> each team bans one hero per map, and the two teams can\'t ban the same role. ' +
      "So every map loses heroes from two different roles. Here is the usual pick in each:</p>" +
      '<div class="hm-banroles">' + U.ROLE_ORDER.map(roleCard).join("") + "</div>" +
      (bp.pairs.length ? '<p class="hm-role__k u-mt-4">Most common ban pairs</p><div class="hm-pairs">' +
        bp.pairs.slice(0, 4).map((p) => '<span class="hm-pair">' + U.faces(U.byRole(p.heroes), 26) + "<b>" + p.n + "×</b></span>").join("") + "</div>" : "") +
      '<p class="u-mt-4"><a class="sec-link" href="' + esc(tiersHref("tab=bans")) + '">Bans for every map →</a></p>' +
      "</div></div>");
  }

  /* ------------------------------------------------------------ maps */
  /* A short window often has no line-up run twice on a map. Then the
     card shows the most-run comp over a longer stretch, and says so. */
  let longer = null;
  const LONG_DAYS = 60;
  const topComp = (res, map) => res.compTiers(map).slice()
    .sort((a, b) => b.apps - a.apps || (b.adjWin - a.adjWin) || a.id.localeCompare(b.id))[0] || null;

  function renderMaps() {
    longer = null;
    const cards = cur.maps.map((B) => {
      let top = topComp(cur, B.map);
      let note = "";
      if ((!top || top.apps < 2) && state.w !== "season") {
        if (!longer) longer = S.compute(T, ROLES, { asOf: cur.asOf, days: LONG_DAYS, region: state.region || null });
        const alt = topComp(longer, B.map);
        if (alt && alt.apps >= 2 && (!top || alt.apps > top.apps)) { top = alt; note = LONG_DAYS + "d"; }
      }
      /* What's winning, when it isn't what's run most: the best adjusted
         win among comps run at least twice (same window as `top`). */
      const pool = (note ? longer : cur).compTiers(B.map).filter((c) => c.apps >= 2 && c.decided >= 2);
      const best = pool.slice().sort((a, b) => b.adjWin - a.adjWin || b.apps - a.apps)[0] || null;
      const showBest = best && top && best.id !== top.id && best.adjWin > top.adjWin + 0.02;
      const heroes = cur.heroTiers(B.map).filter((r) => r.tier === "S").sort((a, b) => b.score - a.score);
      const bp = cur.banProfile(B.map);
      const banCell = (role) => {
        const r = bp && bp.byRole[role] && bp.byRole[role].top[0];
        return r ? '<span class="hm-mban" title="' + esc(P.hero(r.id).name + ": banned on " + pct(r.rate) + " of maps here") + '">' +
          P.heroFace(P.hero(r.id), 26) + "<b>" + pct(r.rate) + "</b></span>" : '<span class="hm-mban dim">—</span>';
      };
      return '<article class="hm-map">' +
        '<header class="hm-map__hd"><h3><a href="' + esc(tiersHref("map=" + encodeURIComponent(B.map))) + '">' + esc(U.mapName(B.map)) +
        '</a></h3><span class="hm-map__meta">' + (B.mode ? esc(B.mode) + " · " : "") + B.maps + " map" + (B.maps === 1 ? "" : "s") + "</span></header>" +
        '<div class="hm-map__row"><span class="hm-map__k">Most-run comp</span>' +
        (top ? U.comp(top, { px: 32 }) + '<span class="hm-map__n"' + (note ? ' title="Most-run over the last ' + LONG_DAYS +
          ' days: no line-up was run twice on this map in the current window"' : "") + ">" + top.apps + "× · " +
          Math.round(top.wins) + "–" + Math.round(top.losses) + (note ? ' <span class="dim">(' + note + ")</span>" : "") + "</span>"
          : '<span class="dim small">No full line-up recorded</span>') + "</div>" +
        (showBest ? '<div class="hm-map__row"><span class="hm-map__k hm-map__k--win">Winning most</span>' + U.comp(best, { px: 32 }) +
          '<span class="hm-map__n">' + best.apps + "× · " + Math.round(best.wins) + "–" + Math.round(best.losses) +
          (note ? ' <span class="dim">(' + note + ")</span>" : "") + "</span></div>" : "") +
        '<div class="hm-map__row"><span class="hm-map__k">S tier</span>' +
        (heroes.length ? U.faces(U.byRole(heroes.map((r) => r.id)).slice(0, 6), 30) : '<span class="dim small">Too few maps to tier</span>') + "</div>" +
        '<div class="hm-map__row"><span class="hm-map__k">Usual bans</span><span class="hm-mbans">' +
        U.ROLE_ORDER.map(banCell).join("") + "</span></div>" +
        '<a class="hm-map__go" href="' + esc(tiersHref("map=" + encodeURIComponent(B.map))) + '">Full tier list →</a></article>';
    });
    P.mount("hm-maps", cards.length ? '<div class="hm-mapgrid">' + cards.join("") + "</div>"
      : P.empty("◇", "No maps in this window", "Pick a longer window."));
  }

  /* ------------------------------------------------------- highlights */
  function renderUpsets() {
    const list = cur.highlights(8);
    const card = (w) => {
      const m = T.matches[w.match];
      const upset = w.e < 0.4;
      return '<article class="hm-play"' + (upset ? " data-upset" : "") + ">" +
        '<div class="hm-play__tag">' + (upset ? '<span class="hm-upset">Upset</span> <span>won as a ' + pct(w.e) + " underdog</span>"
          : "<span>" + pct(w.e) + " to win</span>") + '<span class="hm-play__ev">' + esc(U.matchLabel(m)) + "</span></div>" +
        '<p class="hm-play__who"><b>' + esc(U.teamName(w.team)) + "</b> beat " + esc(U.teamName(w.opp)) + " on " +
        esc(U.mapName(w.map)) + ' <span class="dim">· ' + esc(U.fmtDate(m && m.d)) + "</span></p>" +
        '<div class="hm-play__comps"><span class="hm-play__k">Ran</span>' + U.faces(U.byRole(w.heroes), 34) +
        (w.oppComp ? '<span class="hm-play__k">vs</span>' + U.faces(U.byRole(w.oppComp), 26) : "") + "</div>" +
        '<p class="hm-play__v" title="Event weight × bracket weight × (1 − chance to win)">Win value <b>' + U.signed(w.v, 2) + "</b></p></article>";
    };
    P.mount("hm-upsets", list.length ? '<div class="hm-plays">' + list.map(card).join("") + "</div>"
      : P.empty("◇", "No full line-ups in this window", ""));
  }

  /* ------------------------------------------------------------ boot */
  function update() {
    P.setQs({ w: state.w === "recent" ? "" : state.w, region: state.region });
    run();
    renderControls();
    renderWindowLine();
    renderSTier();
    renderBans();
    renderMaps();
    renderUpsets();
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!T || !T.maps || !T.maps.length) { P.mount("hm-stier", P.empty("◇", "No pro data yet", "")); return; }
    update();
    document.getElementById("hm-controls").addEventListener("click", (e) => {
      const b = e.target.closest("[data-w],[data-region]");
      if (!b) return;
      if (b.dataset.w != null) state.w = b.dataset.w;
      if (b.dataset.region != null) state.region = b.dataset.region;
      update();
    });
  });
})();
