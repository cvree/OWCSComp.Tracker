/* =====================================================================
   OWCS Comp Tracker — app/tierui.js
   Small drawing helpers shared by the home snapshot (page-home.js) and
   the tier list (page-tiers.js): names, labels, the hover card, a comp
   with its hover details, and the one-line source badge.
   Reads window.OWCS_SHEET_TIERS; maths stays in app/tierstats.js.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const T = window.OWCS_SHEET_TIERS;
  if (!P) return;
  const esc = P.esc;

  const STAGE_NAMES = { "pre-season": "Pre-Season Bootcamp", "stage-1": "Stage 1", "stage-2": "Stage 2",
    "stage-3": "Stage 3", "champions-clash": "Champions Clash", "midseason-championship": "Midseason Championship",
    "world-finals": "World Finals" };
  const REGION_SHORT = { na: "NA", emea: "EMEA", korea: "Korea", japan: "Japan", china: "China",
    pacific: "Pacific", asia: "Asia", international: "International" };
  const ROLE_ORDER = ["Tank", "Damage", "Support"];

  const U = {
    STAGE_NAMES: STAGE_NAMES, REGION_SHORT: REGION_SHORT, ROLE_ORDER: ROLE_ORDER,
    pct: (v, d) => (v == null || !isFinite(v) ? "—" : (v * 100).toFixed(d == null ? 0 : d) + "%"),
    signed: (v, d) => (v == null || !isFinite(v) ? "—" : (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(d == null ? 1 : d)),
    roles: () => { const r = {}; P.heroes().forEach((h) => { r[h.id] = h.role; }); return r; },
    teamName: (id) => {
      const t = id && P.team(id);
      if (t) return t.name;
      return ((T && T.teams) || {})[id] ? T.teams[id][0] : (P.anyTeamName ? P.anyTeamName(id) : id);
    },
    mapName: (id) => (id === "*" || !id ? "All maps" : P.mapInfo(id).name || id),
    fmtDate: (iso) => (iso ? P.fmtDate(iso) : "—"),
    stageLabel: (m) => {
      if (!m) return "";
      const stage = STAGE_NAMES[m.s] || m.s || "";
      return m.r === "international" || !m.r ? stage : (REGION_SHORT[m.r] || m.r) + " " + stage;
    },
  };
  U.matchLabel = (m) => (m ? U.stageLabel(m) + (m.f ? " · " + m.f : "") : "");

  /* A hover / focus card. `trigger` is HTML; `body` is the card's HTML. */
  U.tip = (trigger, body, opt) => {
    opt = opt || {};
    return '<span class="tip' + (opt.cls ? " " + opt.cls : "") + '" tabindex="0">' + trigger +
      '<span class="tip__card" role="tooltip">' + body + "</span></span>";
  };

  /* Icons only (no per-hero hover card, so the comp's own card wins). */
  U.faces = (heroes, px) => '<span class="faces">' + (heroes || []).map((id) => {
    const h = P.hero(id);
    return '<span class="faces__one" title="' + esc(h.name) + '" data-role="' + esc(h.role || "") +
      '" style="--px:' + (px || 32) + 'px">' +
      P.heroFace(h, px || 32) + "</span>";
  }).join("") + "</span>";

  const roleRank = (id) => { const i = ROLE_ORDER.indexOf(P.hero(id).role); return i < 0 ? 9 : i; };
  U.byRole = (heroes) => (heroes || []).slice().sort((a, b) => roleRank(a) - roleRank(b) || a.localeCompare(b));

  /* A comp row from tierstats (finishRow) as icons + its details on hover. */
  U.comp = (row, opt) => {
    opt = opt || {};
    const heroes = U.byRole(row.heroes || []);
    const names = heroes.map((h) => P.hero(h).name).join(" · ");
    const rec = (row.wins != null ? Math.round(row.wins) + "–" + Math.round(row.losses) : "");
    const body =
      '<strong class="tip__title">' + esc(names) + "</strong>" +
      '<span class="tip__line">Started on <b>' + row.apps + "</b> map" + (row.apps === 1 ? "" : "s") +
      (rec ? " · <b>" + rec + "</b>" : "") + (row.adjWin != null ? " · " + U.pct(row.adjWin, 1) + " adj. win" : "") + "</span>" +
      (row.swaps && row.swaps.length ? '<span class="tip__line">Swapped to: ' + U.faces(row.swaps.map((s) => s[0]), 22) + "</span>" : "") +
      (row.teams && row.teams.length ? '<span class="tip__line">Ran it: ' + row.teams.slice(0, 3)
        .map((t) => esc(U.teamName(t[0])) + " (" + Math.round(t[1]) + ")").join(", ") + "</span>" : "") +
      (row.last ? '<span class="tip__line dim">Last seen ' + esc(U.fmtDate(row.last)) +
        (row.regions && row.regions.length ? " · " + row.regions.map((r) => esc(REGION_SHORT[r[0]] || r[0])).join(", ") : "") +
        "</span>" : "") +
      '<span class="tip__line dim">The five heroes the players are listed with first. The sheet records line-ups, not seconds.</span>';
    return U.tip(U.faces(heroes, opt.px || 34), body, { cls: "tip--comp" });
  };

  /* The single, minimal source line: a badge, details on hover. */
  U.sourceBadge = () => {
    if (!T) return "";
    const c = (T.dates || {}).confidence || {};
    return U.tip('<span class="src-mini">ⓘ Pro data · ' + Number(T.counts.maps).toLocaleString("en-US") + " maps</span>",
      '<strong class="tip__title">Where this comes from</strong>' +
      '<span class="tip__line">Every OWCS 2026 series in the community-maintained match spreadsheet: ' +
      T.counts.series + " series, " + T.counts.maps + " maps, line-ups, results and bans.</span>" +
      '<span class="tip__line">The sheet has no dates. ' + (c.liquipedia || 0) + " played series take the day Liquipedia lists them on " +
      "(same teams, same score); the rest are placed from the official schedule by stage, week and day (" +
      (c.window || 0) + " inside a published window, " + (c.estimate || 0) + " estimated).</span>" +
      '<span class="tip__line">Line-ups are listed heroes, not time played: a player\'s first hero counts most.</span>' +
      '<a class="tip__line" href="how-it-works.html#sources">Credits and method →</a>', { cls: "tip--src" });
  };

  P.tierUI = U;
})();
