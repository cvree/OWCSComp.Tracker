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

  const TR = P.timeRange;
  const REGIONS = [["", "All regions"], ["na", "NA"], ["emea", "EMEA"], ["korea", "Korea"], ["china", "China"],
    ["japan", "Japan"], ["pacific", "Pacific"], ["international", "LANs"]];
  const ROLE_ICON = { Tank: "⛨", Damage: "✦", Support: "✚" };

  const state = { region: P.qs("region") || "", mapQ: "", mode: "", allMaps: false, allBans: false };
  const MAPS_SHOWN = 8, BANS_SHOWN = 5;
  const store = {
    get: (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
  };
  let cur = null, prev = null, label = "", link = "";

  /* ----------------------------------------------------------- window
     The site-wide time range (app/timerange.js), 14 days unless the
     visitor picks another. Relative windows count back from the chosen
     region's own newest map. The arrows compare with the window of the
     same length just before — when that window is inside the season. */
  function run() {
    const region = state.region || null;
    const r = TR.resolve({ region: region });
    cur = S.compute(T, ROLES, { asOf: r.to, from: r.from || null, region: region });
    prev = r.prev && r.prev.from >= ((T.dates || {}).min || "")
      ? S.compute(T, ROLES, { asOf: r.prev.to, from: r.prev.from, region: region }) : null;
    label = r.label;
    link = "t=" + encodeURIComponent(r.key) + (r.key === "custom" ? "&tf=" + r.from + "&tt=" + r.to : "");
    if (state.region) link += "&region=" + state.region;
  }

  const tiersHref = (extra) => "tiers.html?" + link + (extra ? "&" + extra : "");

  /* -------------------------------------------------------- controls */
  function renderControls() {
    const seg = (key, opts) => '<div class="seg" role="group" aria-label="Region">' +
      opts.map((o) => '<button type="button" data-' + key + '="' + esc(o[0]) + '" aria-pressed="' +
        (state[key] === o[0] ? "true" : "false") + '">' + esc(o[1]) + "</button>").join("") + "</div>";
    P.mount("hm-controls", seg("region", REGIONS) + U.sourceBadge());
  }

  function renderWindowLine() {
    const B = cur.bucket("*");
    const n = B ? B.maps : 0;
    TR.note("hm-range", "<b>" + n + "</b> pro map" + (n === 1 ? "" : "s") +
      (state.region ? " · " + esc((REGIONS.find((r) => r[0] === state.region) || [])[1]) : ""));
    /* A short window in a quiet region can be thin; say so, and offer
       the wider window in one tap rather than leaving a sparse page. */
    P.mount("hm-window", !n ? "No pro maps in this window." + TR.widenHtml()
      : (prev && prev.bucket("*") ? '<span class="dim">▲▼ compare with the ' + esc(label.toLowerCase().replace("last ", "")) +
          " before.</span>" : "") +
        (n < 20 ? ' <span class="dim">Only ' + n + " maps in this window — tiers on a small sample move a lot.</span>" + TR.widenHtml() : ""));
  }

  /* --------------------------------------------------------- welcome
     A first visit gets three ways in, in plain words. Dismissed once,
     it stays gone (per browser). */
  function renderWelcome() {
    if (store.get("owcs.welcome") === "0") { P.mount("hm-welcome", ""); return; }
    P.mount("hm-welcome",
      '<aside class="welcome" aria-labelledby="welcome-h">' +
        '<div class="welcome__txt"><h2 id="welcome-h">New here? Start with one of these</h2>' +
        "<p>Everything comes from pro OWCS matches in the time range above (14 days unless you change it).</p></div>" +
        '<ol class="welcome__steps">' +
          '<li><a href="#h-stier"><b>See the top heroes</b><span>The S tier for each role, and what\'s rising</span></a></li>' +
          '<li><a href="#h-every"><b>Find your map</b><span>The comp pros run on it and what they ban</span></a></li>' +
          '<li><a href="tiers.html"><b>Check the tier list</b><span>Every hero ranked S to D, map by map</span></a></li>' +
        "</ol>" +
        '<div class="welcome__end"><button type="button" class="btn btn--ghost btn--sm" data-help>What do the numbers mean?</button>' +
        '<button type="button" class="btn btn--ghost btn--sm" data-dismiss aria-label="Hide this introduction">Got it</button></div>' +
      "</aside>");
  }

  function renderKey() {
    if (!P.term) return;
    P.mount("hm-key", '<span class="dim">How to read the cards:</span> ' +
      "<b>pick</b> = " + P.term("pick", "how often teams run it") + " · " +
      "<b>adj. win</b> = " + P.term("adj", "win rate weighed for opponent and stakes (50% is average)") + " · " +
      "<b>ban</b> = " + P.term("ban", "share of maps it was banned") + " · " +
      "<b>▲▼</b> = " + P.term("trend", "change since the period before"));
  }

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
    const all10 = bp.rows.slice(0, 10);
    const top = state.allBans ? all10 : all10.slice(0, BANS_SHOWN);
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
      '<div><ol class="hm-banlist" aria-label="Most banned heroes">' + top.map(row).join("") + "</ol>" +
      (all10.length > BANS_SHOWN ? '<button type="button" class="btn btn--ghost btn--sm more-btn" data-more-bans aria-expanded="' +
        state.allBans + '">' + (state.allBans ? "Show top " + BANS_SHOWN + " only" : "Show top " + all10.length) + "</button>" : "") +
      "</div>" +
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

  const fold = (x) => String(x || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

  function renderMapBar() {
    const modes = Array.from(new Set(cur.maps.map((B) => B.mode).filter(Boolean))).sort();
    P.mount("hm-mapbar",
      '<label class="hm-mapbar__q"><span class="visually-hidden">Find a map</span>' +
        '<svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
        '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 4.5 4.5"/></svg>' +
        '<input class="input" type="search" id="hm-mapq" placeholder="Find a map…" autocomplete="off" value="' + esc(state.mapQ) + '"></label>' +
      '<div class="seg" role="group" aria-label="Game mode">' +
        [["", "All modes"]].concat(modes.map((m) => [m, m])).map((o) =>
          '<button type="button" data-mode="' + esc(o[0]) + '" aria-pressed="' + (state.mode === o[0]) + '">' + esc(o[1]) + "</button>").join("") +
      "</div>");
  }

  function renderMaps() {
    longer = null;
    const q = fold(state.mapQ).trim();
    const shown = cur.maps.filter((B) => (!state.mode || B.mode === state.mode) &&
      (!q || fold(U.mapName(B.map)).indexOf(q) >= 0 || fold(B.mode).indexOf(q) >= 0));
    const filtered = q || state.mode;
    const list = filtered || state.allMaps ? shown : shown.slice(0, MAPS_SHOWN);
    const cards = list.map((B) => {
      let top = topComp(cur, B.map);
      let note = "";
      if ((!top || top.apps < 2) && (TR.resolve().days || 0) < LONG_DAYS) {
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
        '</a></h3><span class="hm-map__meta">' + esc(B.mode || "") + " · " + B.maps + " map" + (B.maps === 1 ? "" : "s") + "</span></header>" +
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
    const more = !filtered && shown.length > MAPS_SHOWN
      ? '<button type="button" class="btn btn--ghost more-btn" data-more-maps aria-expanded="' + state.allMaps + '">' +
        (state.allMaps ? "Show the " + MAPS_SHOWN + " most-played maps only" : "Show all " + shown.length + " maps") + "</button>" : "";
    P.mount("hm-maps", cards.length ? '<div class="hm-mapgrid">' + cards.join("") + "</div>" + more
      : cur.maps.length ? P.empty("◇", "No map matches \u201c" + (state.mapQ || state.mode) + "\u201d", "Clear the search to see every map.")
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
    P.setQs({ region: state.region });
    TR.setRegion("hm-range", state.region);
    run();
    renderControls();
    renderWindowLine();
    renderWelcome();
    renderKey();
    renderSTier();
    renderBans();
    renderMapBar();
    renderMaps();
    renderUpsets();
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!T || !T.maps || !T.maps.length) { P.mount("hm-stier", P.empty("◇", "No pro data yet", "")); return; }
    TR.mount("hm-range", { region: state.region || null });
    TR.onChange(update);
    update();
    document.getElementById("hm-welcome").addEventListener("click", (e) => {
      if (e.target.closest("[data-dismiss]")) { store.set("owcs.welcome", "0"); renderWelcome(); return; }
      if (e.target.closest("[data-help]") && P.openHelp) P.openHelp();
    });
    document.getElementById("hm-bans").addEventListener("click", (e) => {
      if (!e.target.closest("[data-more-bans]")) return;
      state.allBans = !state.allBans;
      renderBans();
    });
    const bar = document.getElementById("hm-mapbar");
    bar.addEventListener("input", (e) => {
      if (e.target.id !== "hm-mapq") return;
      state.mapQ = e.target.value;
      renderMaps();
    });
    bar.addEventListener("click", (e) => {
      const b = e.target.closest("[data-mode]");
      if (!b) return;
      state.mode = b.dataset.mode;
      bar.querySelectorAll("[data-mode]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      renderMaps();
    });
    document.getElementById("hm-maps").addEventListener("click", (e) => {
      if (!e.target.closest("[data-more-maps]")) return;
      state.allMaps = !state.allMaps;
      renderMaps();
      if (!state.allMaps) document.getElementById("h-every").scrollIntoView({ block: "start" });
    });
    document.getElementById("hm-controls").addEventListener("click", (e) => {
      const b = e.target.closest("[data-region]");
      if (!b) return;
      state.region = b.dataset.region;
      update();
    });
  });
})();
