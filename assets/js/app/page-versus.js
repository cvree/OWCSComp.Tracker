/* =====================================================================
   OWCS Comp Tracker — app/page-versus.js
   Versus (versus.html): any two teams head to head. The maths is
   app/lab.js (versus) and app/compbook.js (what each runs).

   The odds come from the same team power the Hero tiers page uses (Elo
   seeded by region, moved by every map, plus current-stage standings),
   as of the end of the time range. A map's odds nudge that by the two
   teams' records on it, each pulled toward 50%. A best-of-n chance is
   the chance of reaching ceil(n/2) map wins first. These are a model's
   numbers, labelled as such — not a prediction anyone should bet on.

   State: ?a=<team>&b=<team>; the window is the site-wide time range.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const T = window.OWCS_SHEET_TIERS;
  const L = P && P.lab;
  const U = P && P.tierUI;
  const TR = P && P.timeRange;
  if (!P || !T || !L || !U || !TR) return;
  const esc = P.esc;
  const pct = U.pct;

  const REGION = { na: "North America", emea: "EMEA", korea: "Korea", japan: "Japan", china: "China", pacific: "Pacific" };
  const teams = Object.keys(T.teams || {}).map((id) => ({ id: id, name: U.teamName(id), region: (T.teams[id] || [])[1] || null }))
    .sort((a, b) => a.name.localeCompare(b.name));
  const known = (id) => teams.some((t) => t.id === id);

  /* Default: the two strongest teams right now. */
  function topTwo() {
    const w = TR.buildOpts();
    const table = P.tierStats.sweep(T, w.asOf, w.from).table;
    return [table[0] && table[0].team, table[1] && table[1].team];
  }
  const state = { a: known(P.qs("a")) ? P.qs("a") : "", b: known(P.qs("b")) ? P.qs("b") : "" };
  if (!state.a || !state.b) {
    const t = topTwo();
    state.a = state.a || t[0] || (teams[0] || {}).id;
    state.b = state.b || (t[0] === state.a ? t[1] : t[0]) || (teams[1] || {}).id;
  }

  const mark = (id, px) => {
    const t = P.team(id);
    return t ? '<span class="vs-mark">' + P.teamMark(t, px || 40) + "</span>"
      : '<span class="vs-mark vs-mark--none" aria-hidden="true">' + esc((U.teamName(id) || "?").slice(0, 2).toUpperCase()) + "</span>";
  };
  const wl = (t) => t.w + "–" + t.l;
  const rate = (t) => (t.w + t.l ? pct(t.w / (t.w + t.l)) : "—");
  const plural = (n, w) => n + " " + w + (n === 1 ? "" : "s");

  /* ---------------------------------------------------------- picker */
  function renderPick() {
    const byReg = new Map();
    teams.forEach((t) => { const k = REGION[t.region] || "Other"; if (!byReg.has(k)) byReg.set(k, []); byReg.get(k).push(t); });
    const sel = (k) => '<label class="field"><span class="mx-label">' + (k === "a" ? "Team" : "Against") + "</span>" +
      '<select class="input" data-k="' + k + '">' + Array.from(byReg.entries()).map((e) => '<optgroup label="' + esc(e[0]) + '">' +
        e[1].map((t) => '<option value="' + esc(t.id) + '"' + (state[k] === t.id ? " selected" : "") + ">" + esc(t.name) + "</option>").join("") +
        "</optgroup>").join("") + "</select></label>";
    document.getElementById("vs-pick").innerHTML = '<div class="vs-pick__row">' + sel("a") +
      '<button type="button" class="btn btn--ghost vs-swap" data-act="swap" title="Swap the two teams" aria-label="Swap the two teams">⇄</button>' +
      sel("b") + "</div>";
  }

  /* ----------------------------------------------------------- output */
  function oddsBar(p, labelA, labelB) {
    const a = Math.round(p * 100);
    return '<div class="vs-odds" role="img" aria-label="' + esc(labelA + " " + a + "%, " + labelB + " " + (100 - a) + "%") + '">' +
      '<span class="vs-odds__a" style="width:' + a + '%"><b>' + a + "%</b></span>" +
      '<span class="vs-odds__b" style="width:' + (100 - a) + '%"><b>' + (100 - a) + "%</b></span></div>";
  }

  function render() {
    if (!state.a || !state.b) return;
    if (state.a === state.b) {
      P.mount("vs-out", P.empty("⇄", "Pick two different teams", "A team against itself is always 50%."));
      return;
    }
    const w = TR.buildOpts();
    const v = L.versus(T, state.a, state.b, { asOf: w.asOf, from: w.from });
    const A = v.a, B = v.b;
    const nameA = U.teamName(state.a), nameB = U.teamName(state.b);
    const range = TR.phrase();
    TR.note("vs-range", "<b>" + (A.maps.n + B.maps.n) + "</b> maps between them");

    const side = (t, X) => '<div class="vs-side">' + mark(t, 56) + '<div><h2><a href="team.html?id=' + encodeURIComponent(t) + '">' +
      esc(U.teamName(t)) + "</a></h2>" + (X.power ? '<p class="dim small">Power #' + X.power.rank + " · " + Math.round(X.power.power) +
        (X.power.region ? " · " + esc(REGION[X.power.region] || X.power.region) : "") + "</p>" : '<p class="dim small">Not rated yet</p>') +
      '<p class="small">' + wl(X.maps) + " on maps in " + esc(range) + "</p></div></div>";

    const h2h = v.meetings.reduce((s, m) => { if (m.a > m.b) s.a += 1; else if (m.b > m.a) s.b += 1; return s; }, { a: 0, b: 0 });
    const fav = v.pMap == null ? "" : v.pMap >= 0.5 ? nameA : nameB;
    const head = '<section class="vs-head card">' +
      '<div class="vs-head__teams">' + side(state.a, A) + '<div class="vs-head__vs">VS</div>' + side(state.b, B) + "</div>" +
      (v.pMap == null ? '<p class="dim">One of these teams has no rated maps yet, so there are no odds.</p>'
        : '<div class="vs-head__odds"><p class="cb-sub">The model, per map</p>' + oddsBar(v.pMap, nameA, nameB) +
          '<div class="vs-series">' + [["bo3", "Bo3"], ["bo5", "Bo5"], ["bo7", "Bo7"]].map((x) =>
            '<div class="vs-series__one"><span class="dim small">' + x[1] + "</span><b>" + pct(v.series[x[0]]) + '</b><span class="dim small">' +
            esc(nameA) + "</span></div>").join("") + "</div>" +
          '<p class="vs-verdict"><b>' + esc(fav) + "</b> " + (Math.abs(v.pMap - 0.5) < 0.04 ? "by a hair — this one is close to a coin flip"
            : Math.abs(v.pMap - 0.5) < 0.12 ? "are favoured" : "are clear favourites") +
          (v.meetings.length ? ' · <span class="dim">past series ' + esc(nameA) + " " + h2h.a + "–" + h2h.b + " " + esc(nameB) + "</span>" : "") +
          '</p><p class="dim small">Team power as of ' + esc(U.fmtDate(v.asOf)) + ' (Elo, seeded by region and moved by every map) — ' +
          '<a href="tiers.html?tab=power">the power ranking →</a>. A model, not a promise.</p></div>') +
      "</section>";

    const maps = v.maps.filter((m) => m.a.n || m.b.n);
    const mapTable = '<section class="dl-box dl-box--wide"><h3>Map by map</h3><p class="dim small">Each team\'s record on the map in ' +
      esc(range) + ", and the model's chance there for " + esc(nameA) + ".</p>" +
      (maps.length ? '<div class="table-wrap vs-maps-wrap"><table class="tbl tbl--compact cb-tbl vs-maps"><thead><tr><th>Map</th><th class="num">' + esc(nameA) + "</th><th class=\"num\">" +
        esc(nameB) + '</th><th>Chance for ' + esc(nameA) + "</th></tr></thead><tbody>" + maps.map((m) =>
          "<tr><td>" + esc(P.mapInfo(m.map).name || m.map) + (m.mode ? ' <span class="dim small">' + esc(m.mode) + "</span>" : "") + "</td>" +
          '<td class="num">' + (m.a.n ? wl(m.a) + ' <span class="dim">' + rate(m.a) + "</span>" : "—") + '</td><td class="num">' +
          (m.b.n ? wl(m.b) + ' <span class="dim">' + rate(m.b) + "</span>" : "—") + "</td><td>" +
          (m.p == null ? "—" : '<span class="vs-mini"><span style="width:' + Math.round(m.p * 100) + '%"></span></span> <b>' + pct(m.p) + "</b>") +
          "</td></tr>").join("") + "</tbody></table></div>"
        : '<p class="dim small">Neither team played a map in this time range.</p>' + TR.widenHtml()) + "</section>";

    const pips = (X) => X.form.length ? '<ol class="vs-form">' + X.form.map((f) => '<li class="vs-pip ' + (f.res === 1 ? "is-w" : f.res === 0 ? "is-l" : "") +
      '" title="' + esc((f.res === 1 ? "Won" : f.res === 0 ? "Lost" : "No result") + " vs " + U.teamName(f.opp) + " on " +
        (P.mapInfo(f.map).name || f.map) + " · " + U.fmtDate(f.d)) + '">' + (f.res === 1 ? "W" : f.res === 0 ? "L" : "–") + "</li>").join("") + "</ol>"
      : '<p class="dim small">No maps in this range.</p>';
    const form = '<section class="dl-box"><h3>Form</h3><p class="dim small">Last ten maps, newest first.</p>' +
      '<p class="cb-sub">' + esc(nameA) + "</p>" + pips(A) + '<p class="cb-sub cb-sub--gap">' + esc(nameB) + "</p>" + pips(B) + "</section>";

    const meet = '<section class="dl-box"><h3>Past meetings</h3><p class="dim small">Every series between them this season, whatever the range.</p>' +
      (v.meetings.length ? '<ol class="vs-meet">' + v.meetings.map((m) => '<li><a href="series.html?id=' + encodeURIComponent(m.series) + '">' +
        '<b class="' + (m.a > m.b ? "vs-won" : "") + '">' + m.a + "</b>–<b class=\"" + (m.b > m.a ? "vs-won" : "") + '">' + m.b + "</b></a>" +
        '<span class="small">' + esc(U.matchLabel(m)) + '</span><span class="dim small">' + esc(U.fmtDate(m.d)) + "</span></li>").join("") + "</ol>"
        : '<p class="dim small">They have not met this season.</p>') + "</section>";

    /* rows are [hero, value]; `fmt` says what the value is (a share of
       maps, or a count of bans) */
    const heroList = (rows, fmt) => rows.length ? '<ul class="cb-hcounts">' + rows.map((r) => {
      const h = P.hero(r[0]);
      return "<li>" + P.heroFace(h, 24) + "<span>" + esc(h.name) + "</span><b>" + fmt(r[1]) + "</b><span></span></li>";
    }).join("") + "</ul>" : '<p class="dim small">None recorded.</p>';
    const share = (x) => pct(x), times = (n) => n + "×";
    const bansBox = (X, name) => '<section class="dl-box"><h3>' + esc(name) + "</h3>" +
      '<p class="cb-sub">Plays most <span class="dim">· share of maps</span></p>' + heroList(X.heroes.slice(0, 5), share) +
      '<p class="cb-sub cb-sub--gap">Bans most</p>' + heroList(X.bans, times) +
      '<p class="cb-sub cb-sub--gap">Gets banned</p>' + heroList(X.bannedAgainst, times) + "</section>";

    const comps = (t, name) => '<section class="dl-box"><h3>What ' + esc(name) + " runs</h3>" +
      (P.compBookSummary ? P.compBookSummary({ team: t }, { limit: 4 }) : "") + "</section>";

    P.mount("vs-out", head + '<div class="dl-outgrid u-mt-4">' + mapTable + form + meet +
      comps(state.a, nameA) + comps(state.b, nameB) + bansBox(A, nameA) + bansBox(B, nameB) + "</div>");
  }

  function update() {
    P.setQs({ a: state.a || null, b: state.b || null });
    renderPick();
    render();
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!T.maps || !T.maps.length || !teams.length) { P.mount("vs-out", P.empty("◇", "No pro data yet", "")); return; }
    TR.mount("vs-range");
    TR.onChange(render);
    update();
    document.getElementById("vs-pick").addEventListener("change", (e) => {
      const k = e.target.dataset.k;
      if (!k) return;
      state[k] = e.target.value;
      update();
    });
    document.getElementById("vs-pick").addEventListener("click", (e) => {
      const b = e.target.closest && e.target.closest('[data-act="swap"]');
      if (!b) return;
      const x = state.a; state.a = state.b; state.b = x;
      update();
    });
  });
})();
