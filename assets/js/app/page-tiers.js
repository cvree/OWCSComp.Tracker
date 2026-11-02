/* =====================================================================
   OWCS Comp Tracker — app/page-tiers.js
   tiers.html: S–D hero and comp tiers per map, a team power ranking, for
   a chosen viewing date and range. All maths in app/tierstats.js; this
   file only reads the controls and draws.

   Data: window.OWCS_SHEET_TIERS (spreadsheet imported; dates ESTIMATED
   from the official schedule, config/external/sheet_schedule.json).
   State lives in the URL so a view can be linked:
     ?asof=YYYY-MM-DD&range=30|60|…|season|custom&from=…&region=…&map=…&role=…&tab=…
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const T = window.OWCS_SHEET_TIERS;
  const S = P && P.tierStats;
  if (!P || !S) return;
  const esc = P.esc;
  const W = (T && T.weights) || {};

  const ROLES = {};
  P.heroes().forEach((h) => { ROLES[h.id] = h.role; });
  const ROLE_ORDER = ["Tank", "Damage", "Support"];
  const REGIONS = [["", "All regions"], ["na", "North America"], ["emea", "EMEA"], ["korea", "Korea"],
    ["japan", "Japan"], ["china", "China"], ["pacific", "Pacific"], ["asia", "Asia championship"],
    ["international", "International LANs"]];
  const RANGES = [["14", "Last 14 days"], ["30", "Last 30 days"], ["60", "Last 60 days"], ["90", "Last 90 days"],
    ["180", "Last 180 days"], ["season", "Whole season"], ["custom", "Custom start date"]];
  const STAGE_NAMES = { "pre-season": "Pre-Season Bootcamp", "stage-1": "Stage 1", "stage-2": "Stage 2",
    "stage-3": "Stage 3", "champions-clash": "Champions Clash", "midseason-championship": "Midseason Championship",
    "world-finals": "World Finals" };
  const REGION_SHORT = { na: "NA", emea: "EMEA", korea: "Korea", japan: "Japan", china: "China",
    pacific: "Pacific", asia: "Asia", international: "International" };

  const state = {
    asof: P.qs("asof") || "", range: P.qs("range") || "60", from: P.qs("from") || "",
    region: P.qs("region") || "", map: P.qs("map") || "", role: P.qs("role") || "",
  };
  let R = null;
  let missingMap = "";

  /* --------------------------------------------------------- helpers */
  const pct = (v, d) => (v == null || !isFinite(v) ? "—" : (v * 100).toFixed(d == null ? 0 : d) + "%");
  const signed = (v, d) => (v == null || !isFinite(v) ? "—" : (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(d == null ? 1 : d));
  const teamName = (id) => {
    const t = id && P.team(id);
    if (t) return t.name;
    return (T.teams[id] || [])[0] || P.anyTeamName(id);
  };
  const mapName = (id) => (id === "*" || !id ? "All maps" : P.mapInfo(id).name || id);
  const fmtDate = (iso) => (iso ? P.fmtDate(iso) : "—");
  const matchLabel = (m) => {
    if (!m) return "";
    const stage = STAGE_NAMES[m.s] || m.s || "";
    const where = m.r === "international" || !m.r ? stage : (REGION_SHORT[m.r] || m.r) + " " + stage;
    return where + (m.f ? " · " + m.f : "");
  };
  const sampleClass = (n) => (n >= 20 ? "sample" : n >= 6 ? "sample sample--small" : "sample sample--tiny");

  /* --------------------------------------------------------- filters */
  function renderFilters() {
    const min = T.dates.min, max = T.dates.max;
    const sel = (key, label, opts) => '<label class="field"><span class="mx-label">' + esc(label) +
      '</span><select class="input" data-key="' + key + '">' + opts.map((o) => '<option value="' + esc(o[0]) + '"' +
        (String(state[key]) === String(o[0]) ? " selected" : "") + ">" + esc(o[1]) + "</option>").join("") +
      "</select></label>";
    const date = (key, label, val, hidden) => '<label class="field"' + (hidden ? " hidden" : "") +
      ' data-field="' + key + '"><span class="mx-label">' + esc(label) +
      '</span><input class="input" type="date" data-key="' + key + '" value="' + esc(val) + '" min="' + esc(min) +
      '" max="' + esc(max) + '"></label>';
    document.getElementById("filters").innerHTML =
      '<div class="mx-grid">' +
      date("asof", "Viewing date", state.asof || max) +
      sel("range", "Date range", RANGES) +
      date("from", "From", state.from || S.addDays(state.asof || max, -59), state.range !== "custom") +
      sel("region", "Region", REGIONS) +
      sel("role", "Role", [["", "All roles"]].concat(ROLE_ORDER.map((r) => [r, r]))) +
      "</div>" +
      '<div class="row u-mt-4 tr-quick"><span class="mx-label">Jump to</span>' +
      '<button type="button" class="btn btn--sm btn--ghost" data-jump="latest">Latest</button>' +
      quickStages().map((q) => '<button type="button" class="btn btn--sm btn--ghost" data-jump="' + esc(q[1]) +
        '" data-jump-from="' + esc(q[2]) + '">' + esc(q[0]) + "</button>").join("") + "</div>";
    document.getElementById("filters").addEventListener("change", (e) => {
      const k = e.target.dataset.key;
      if (!k) return;
      state[k] = e.target.value;
      if (k === "range") {
        document.querySelector('[data-field="from"]').hidden = state.range !== "custom";
        if (state.range === "custom" && !state.from) state.from = document.querySelector('[data-key="from"]').value;
      }
      if (k === "from") state.range = "custom";
      update();
    });
    document.getElementById("filters").addEventListener("click", (e) => {
      const b = e.target.closest("[data-jump]");
      if (!b) return;
      if (b.dataset.jump === "latest") { state.asof = ""; }
      else { state.asof = b.dataset.jump; state.from = b.dataset.jumpFrom; state.range = "custom"; }
      renderFilters();
      update();
    });
  }

  /* One button per stretch of the season: its first and last estimated date. */
  function quickStages() {
    const spans = new Map();
    T.matches.forEach((m) => {
      if (!m.d) return;
      const key = m.s;
      const s = spans.get(key) || { from: m.d, to: m.d };
      if (m.d < s.from) s.from = m.d;
      if (m.d > s.to) s.to = m.d;
      spans.set(key, s);
    });
    return Array.from(spans.entries()).sort((a, b) => a[1].to.localeCompare(b[1].to))
      .map((p) => [STAGE_NAMES[p[0]] || p[0], p[1].to, p[1].from]);
  }

  function opts() {
    const asOf = state.asof || T.dates.max;
    const o = { asOf: asOf, region: state.region || null };
    if (state.range === "custom") o.from = state.from || null;
    else if (state.range !== "season") o.days = Number(state.range) || 60;
    return o;
  }

  /* ---------------------------------------------------------- render */
  function update() {
    P.setQs({ asof: state.asof, range: state.range === "60" ? "" : state.range,
      from: state.range === "custom" ? state.from : "", region: state.region, map: state.map, role: state.role });
    R = S.compute(T, ROLES, opts());
    missingMap = state.map && !R.bucket(state.map) ? state.map : "";
    if (missingMap) state.map = "";
    renderSummary();
    renderMaps();
    renderHeroes();
    renderComps();
    renderBans();
    renderPower();
  }

  function renderProvenance() {
    P.mount("provenance", P.tierUI ? P.tierUI.sourceBadge() : "");
  }

  function renderSummary() {
    const B = R.bucket("*");
    const n = B ? B.maps : 0;
    const region = (REGIONS.find((r) => r[0] === (state.region || "")) || REGIONS[0])[1];
    P.mount("summary", '<p class="small">' +
      (n ? "<strong>" + n + "</strong> maps (" + B.decided + " with a recorded winner) between <strong>" +
        esc(fmtDate(B.from)) + "</strong> and <strong>" + esc(fmtDate(B.to)) + "</strong> · " + esc(region) +
        " · team power as of <strong>" + esc(fmtDate(R.asOf)) + "</strong>"
        : "No maps in this range. Widen the range or move the viewing date.") +
      ' <span class="dim">(estimated dates)</span></p>' +
      (missingMap ? '<p class="small u-mt-2" role="status">' + esc(mapName(missingMap)) +
        " wasn't played in this range (it may have been out of the map pool), so all maps are shown. " +
        "Widen the range or move the viewing date to see it.</p>" : ""));
  }

  function renderMaps() {
    const all = R.bucket("*");
    const btn = (id, label, n, mode) => '<button type="button" class="tr-map" data-map="' + esc(id) + '" aria-pressed="' +
      (state.map === id ? "true" : "false") + '"' + (mode ? ' title="' + esc(mode) + '"' : "") + ">" + esc(label) +
      ' <span class="count">' + n + "</span></button>";
    const el = P.mount("maps", btn("", "All maps", all ? all.maps : 0) +
      R.maps.map((b) => btn(b.map, mapName(b.map), b.maps, b.mode)).join(""));
    if (el && !el.dataset.bound) {
      el.dataset.bound = "1";
      el.addEventListener("click", (e) => {
        const b = e.target.closest("[data-map]");
        if (!b) return;
        state.map = b.dataset.map;
        update();
      });
    }
  }

  /* ---------------------------------------------------- hero blurbs */
  function heroBlurb(r, B) {
    const out = [];
    out.push("On " + pct(r.pickShare) + " of team-maps" + (state.map ? " here" : "") + " (" + r.apps + " listings).");
    if (r.decided >= 1) {
      out.push("Won " + pct(r.rawWin) + "; weighed for opponent power and stakes, " + pct(r.adjWin, 1) +
        " (" + signed(r.value) + " wins vs expected).");
    }
    if (r.best && r.best.e != null) {
      const m = T.matches[r.best.match];
      out.push("Biggest win: " + esc(teamName(r.best.team)) + " over " + esc(teamName(r.best.opp)) +
        (r.best.e < 0.5 ? " as a " + pct(r.best.e) + " underdog" : "") + " · " + esc(matchLabel(m)) +
        (state.map ? "" : " on " + esc(mapName(r.best.map))) + ".");
    }
    if (r.worst && r.worst.e != null && r.worst.e >= 0.6) {
      const m = T.matches[r.worst.match];
      out.push("Worst loss: " + esc(teamName(r.worst.team)) + " to " + esc(teamName(r.worst.opp)) + " at " +
        pct(r.worst.e) + " to win · " + esc(matchLabel(m)) + ".");
    }
    if (r.teams.length) out.push("Most by " + r.teams.slice(0, 3).map((t) => esc(teamName(t[0]))).join(", ") + ".");
    if (r.banShare >= 0.02) out.push("Banned on " + pct(r.banShare) + " of maps.");
    return out.join(" ");
  }

  function heroRow(r, B) {
    const comp = (r.comps || []).find((c) => c.n >= 2);
    return "<tr>" +
      '<td class="tr-hero">' + P.heroTile(r.id, { size: "sm", link: true }) + "</td>" +
      '<td class="small">' + esc(r.role || "—") + "</td>" +
      '<td class="num">' + pct(r.pickShare) + "</td>" +
      '<td class="num">' + pct(r.rawWin) + "</td>" +
      '<td class="num"><strong>' + pct(r.adjWin, 1) + "</strong></td>" +
      '<td class="num">' + signed(r.value) + "</td>" +
      '<td class="num"><span class="' + sampleClass(r.decided) + '">' + r.decided.toFixed(1) + "</span></td>" +
      '<td class="tr-desc small">' + heroBlurb(r, B) +
      (comp ? '<div class="tr-comp u-mt-2"><span class="dim">Most-run comp (' + comp.n + "):</span> " +
        P.compStrip(comp.heroes, { size: "xs", tight: true }) + "</div>" : "") + "</td></tr>";
  }

  const HERO_HEAD = '<thead><tr><th scope="col">Hero</th><th scope="col">Role</th>' +
    '<th scope="col" class="num" title="Order-weighted share of team-maps listing this hero">Pick</th>' +
    '<th scope="col" class="num" title="Raw share of decided maps won">Win</th>' +
    '<th scope="col" class="num" title="Win rate after weighing event, bracket and opponent power; pulled toward 50% on small samples">Adj. win</th>' +
    '<th scope="col" class="num" title="Importance-weighted wins above what the two teams\' power predicted">Value</th>' +
    '<th scope="col" class="num" title="Order-weighted decided maps behind the win numbers">Maps</th>' +
    '<th scope="col">What the numbers say</th></tr></thead>';

  function renderHeroes() {
    const B = R.bucket(state.map || "*");
    if (!B) { P.mount("p-heroes", P.empty("◇", "No maps in this range", "Widen the date range or pick another region.")); return; }
    let rows = R.heroTiers(state.map || "*");
    if (state.role) rows = rows.filter((r) => r.role === state.role);
    const roleRank = (r) => { const i = ROLE_ORDER.indexOf(r.role); return i < 0 ? 9 : i; };
    const html = (W.tiers || []).map((t) => {
      const list = rows.filter((r) => r.tier === t.id).sort((a, b) => roleRank(a) - roleRank(b) || b.score - a.score);
      if (!list.length) return "";
      return '<section class="card tr-tier u-mt-5" data-tier="' + esc(t.id) + '">' +
        '<header class="tr-tier__hd"><h2 class="tr-tier__name">Tier – ' + esc(t.id) + "</h2>" +
        '<p class="small dim">' + esc(t.blurb || "") + "</p></header>" +
        '<div class="table-wrap"><table class="tbl tbl--compact tr-tbl">' + HERO_HEAD + "<tbody>" +
        list.map((r) => heroRow(r, B)).join("") + "</tbody></table></div></section>";
    }).join("");
    const low = rows.filter((r) => r.lowSample);
    P.mount("p-heroes",
      '<h2 class="u-mt-5">' + esc(mapName(state.map)) + (B.mode ? ' <span class="dim small">' + esc(B.mode) + "</span>" : "") +
      ' <span class="sample">' + B.maps + " maps · " + B.teamMaps + " team-maps</span></h2>" +
      (html || P.empty("◇", "Not enough picks to tier", "Widen the range.")) +
      (low.length ? '<details class="card u-mt-5 tr-low"><summary>Low sample (' + low.length + " heroes on under " +
        pct(W.minPickShare) + " of team-maps, or under " + esc(String(W.minPicks)) + " weighted listings)</summary>" +
        '<div class="table-wrap u-mt-3"><table class="tbl tbl--compact tr-tbl">' + HERO_HEAD + "<tbody>" +
        low.map((r) => heroRow(r, B)).join("") + "</tbody></table></div></details>" : ""));
  }

  /* ------------------------------------------------------------ comps */
  function renderComps() {
    const B = R.bucket(state.map || "*");
    if (!B) { P.mount("p-comps", ""); return; }
    const rows = R.compTiers(state.map || "*");
    const ok = rows.filter((r) => !r.lowSample);
    /* A list, not a table: table wrappers clip, and each comp carries a
       hover card (record, swaps, teams). */
    const head = '<div class="tr-crow tr-crow--hd" aria-hidden="true"><span>Tier</span><span>Comp · hover for details</span>' +
      '<span class="num">Maps</span><span class="num">W–L</span><span class="num">Pick</span><span class="num">Adj. win</span>' +
      '<span class="num">Value</span><span>Run by</span></div>';
    const row = (r) => '<div class="tr-crow">' +
      '<span><span class="tr-badge" data-tier="' + esc(r.tier || "") + '">' + esc(r.tier || "–") + "</span></span>" +
      "<span>" + P.tierUI.comp(r, { px: 32 }) + "</span>" +
      '<span class="num">' + r.apps + '</span><span class="num">' + Math.round(r.wins) + "–" + Math.round(r.losses) + "</span>" +
      '<span class="num">' + pct(r.pickShare, 1) + '</span><span class="num"><strong>' + pct(r.adjWin, 1) + "</strong></span>" +
      '<span class="num">' + signed(r.value) + "</span>" +
      '<span class="small dim">' + r.teams.slice(0, 3).map((t) => esc(teamName(t[0])) + " (" + t[1] + ")").join(", ") + "</span></div>";
    P.mount("p-comps",
      '<h2 class="u-mt-5">' + esc(mapName(state.map)) + ' <span class="sample">' + B.teamMaps + " team-maps</span></h2>" +
      '<p class="small dim">The five heroes each team\'s players are listed with first, when all five are known. ' +
      "Comps run at least " + esc(String(W.compMinMaps)) + " times are tiered; tiers are cut across all comps on the map.</p>" +
      (ok.length ? '<div class="tr-clist u-mt-4">' + head + ok.slice(0, 60).map(row).join("") + "</div>"
        : P.empty("◇", "No comp was run twice in this range", "Widen the range or pick All maps.")));
  }

  /* ------------------------------------------------------------- bans */
  /* What pros take off the board here. Each team bans one hero a map and
     the two teams can't ban the same role, so bans read by role. */
  function renderBans() {
    const key = state.map || "*";
    const bp = R.banProfile(key);
    if (!bp || !bp.rows.length) { P.mount("p-bans", P.empty("⊘", "No bans recorded in this range", "Widen the range.")); return; }
    const max = bp.rows[0].rate || 1;
    const heroLine = (r) => {
      const h = P.hero(r.id);
      return '<div class="tr-banrow" title="' + esc(h.name + ": banned on " + pct(r.rate) + " of maps; the team that banned it won " +
        pct(r.afterWin) + " (" + pct(r.afterAdj, 1) + " weighed)") + '">' + P.heroFace(h, 32) +
        '<span class="tr-banrow__name">' + esc(h.name) + "</span>" +
        '<span class="hm-ban__bar"><span style="width:' + Math.round((r.rate / max) * 100) + '%"></span></span>' +
        '<b class="tr-banrow__rate">' + pct(r.rate) + "</b>" +
        '<span class="tr-banrow__after" title="How the team that banned it did">' + pct(r.afterWin) + " won</span></div>";
    };
    const cols = ROLE_ORDER.map((role) => {
      const x = bp.byRole[role];
      const list = bp.rows.filter((r) => r.role === role);
      return '<section class="card tr-bancol"><h3 class="tr-bancol__hd">' + esc(role) + " ban" +
        (x ? ' <span class="dim small">' + pct(x.share) + " of bans</span>" : "") + "</h3>" +
        (list.length ? list.slice(0, 8).map(heroLine).join("") : '<p class="dim small">None in this range.</p>') + "</section>";
    }).join("");
    let byMap = "";
    if (!state.map) {
      const cell = (B, role) => {
        const p2 = R.banProfile(B.map);
        const r = p2 && p2.byRole[role] && p2.byRole[role].top[0];
        return "<td>" + (r ? '<span class="tr-mban">' + P.heroFace(P.hero(r.id), 26) + esc(P.hero(r.id).name) +
          " <b>" + pct(r.rate) + "</b></span>" : '<span class="dim">—</span>') + "</td>";
      };
      byMap = '<h3 class="u-mt-6">The usual bans on every map</h3>' +
        '<p class="small dim">The most-banned hero in each role, and how often it goes. Click a map for its full ban list.</p>' +
        '<div class="table-wrap u-mt-3"><table class="tbl tbl--compact"><thead><tr><th scope="col">Map</th>' +
        '<th scope="col" class="num">Maps</th><th scope="col">Tank ban</th><th scope="col">Damage ban</th><th scope="col">Support ban</th>' +
        "</tr></thead><tbody>" + R.maps.map((B) => '<tr><th scope="row"><button type="button" class="link-btn" data-map="' + esc(B.map) +
          '">' + esc(mapName(B.map)) + '</button></th><td class="num">' + B.maps + "</td>" +
          ROLE_ORDER.map((role) => cell(B, role)).join("") + "</tr>").join("") + "</tbody></table></div>";
    }
    const pairs = bp.pairs.length ? '<h3 class="u-mt-6">Most common ban pairs</h3><div class="hm-pairs u-mt-3">' +
      bp.pairs.map((p2) => '<span class="hm-pair" title="' + esc(p2.heroes.map((h) => P.hero(h).name).join(" + ")) + '">' +
        P.tierUI.faces(P.tierUI.byRole(p2.heroes), 26) + "<b>" + p2.n + "× · " + pct(p2.rate) + "</b></span>").join("") + "</div>" : "";
    P.mount("p-bans",
      '<h2 class="u-mt-5">' + esc(mapName(state.map)) + ' <span class="sample">' + bp.maps + " maps · " + bp.total + " bans</span></h2>" +
      '<p class="small hm-rule"><b>How bans work:</b> each team bans one hero per map and the two teams can\'t ban the same role, ' +
      "so a map loses heroes from two different roles. Bars show how often each hero is banned; \"won\" is how the team that banned it did.</p>" +
      '<div class="tr-bancols u-mt-4">' + cols + "</div>" + byMap + pairs);
    const el = document.getElementById("p-bans");
    if (el && !el.dataset.bound) {
      el.dataset.bound = "1";
      el.addEventListener("click", (e) => {
        const b = e.target.closest("[data-map]");
        if (!b) return;
        state.map = b.dataset.map;
        update();
      });
    }
  }

  /* ------------------------------------------------------------ power */
  function renderPower() {
    let rows = R.power;
    if (state.region && state.region !== "international" && state.region !== "asia") rows = rows.filter((r) => r.region === state.region);
    const move = (r) => {
      if (!r.prevRank || r.prevRank === r.rank) return '<span class="dim">–</span>';
      const d = r.prevRank - r.rank;
      return '<span class="tr-move" data-dir="' + (d > 0 ? "up" : "down") + '">' + (d > 0 ? "▲" : "▼") + Math.abs(d) + "</span>";
    };
    P.mount("p-power",
      '<p class="small dim u-mt-5">Power as of <strong>' + esc(fmtDate(R.asOf)) + "</strong>: an Elo rating that starts " +
      "at the team's region tier and moves after every map (more at bigger events and deeper in the bracket), plus a " +
      "bonus or penalty for the team's record in its current regional stage. Movement is since the start of the range" +
      (R.from ? " (" + esc(fmtDate(R.from)) + ")" : "") + ". This is what decides how much each hero's win is worth.</p>" +
      '<div class="table-wrap u-mt-4"><table class="tbl tbl--compact"><thead><tr>' +
      '<th scope="col" class="num">#</th><th scope="col">Move</th><th scope="col">Team</th><th scope="col">Region</th>' +
      '<th scope="col" class="num">Power</th><th scope="col" class="num">Elo</th><th scope="col" class="num">Standings</th>' +
      '<th scope="col" class="num">Maps W–L</th><th scope="col">Current stage</th><th scope="col">Last played</th></tr></thead><tbody>' +
      rows.map((r) => "<tr>" +
        '<td class="num">' + r.rank + "</td><td>" + move(r) + "</td>" +
        "<td>" + P.anyTeamPlate(r.team, { size: "sm" }) + "</td>" +
        '<td class="small">' + esc(REGION_SHORT[r.region] || "—") + "</td>" +
        '<td class="num"><strong>' + Math.round(r.power) + "</strong></td>" +
        '<td class="num">' + Math.round(r.elo) + "</td>" +
        '<td class="num">' + signed(r.bonus, 0) + "</td>" +
        '<td class="num">' + r.w + "–" + r.l + "</td>" +
        '<td class="small">' + (r.stage ? esc((REGION_SHORT[r.stage.key.split("/")[0]] || "") + " " +
          (STAGE_NAMES[r.stage.key.split("/")[1]] || "")) + " · " + r.stage.w + "–" + r.stage.l : "—") + "</td>" +
        '<td class="small">' + esc(fmtDate(r.last)) + "</td></tr>").join("") +
      "</tbody></table></div>");
  }

  /* -------------------------------------------------------------- how */
  function renderHow() {
    const ev = W.eventWeight || {};
    const rt = W.regionTier || {};
    const li = (k, v) => "<tr><th scope=\"row\">" + esc(k) + '</th><td class="num">' + esc(String(v)) + "</td></tr>";
    P.mount("p-how",
      '<div class="lane-grid u-mt-5">' +
      '<section class="card"><h2>A win is not just a win</h2><p class="small">Every map result gets a <strong>value</strong>:</p>' +
      '<p class="mono small">importance × (result − expected)</p>' +
      '<ul class="small"><li><strong>importance</strong> = event weight × bracket weight (tables below).</li>' +
      "<li><strong>expected</strong> = the winner's chance from both teams' power just before that map. Beating a stronger team " +
      "earns a lot, beating a much weaker one very little, and losing to a weaker team costs a lot.</li>" +
      "<li><strong>Adjusted win</strong> = 50% + a hero's summed value ÷ (its summed importance + " + esc(String(W.shrink)) +
      "). The extra " + esc(String(W.shrink)) + " pulls small samples toward 50%, so a 3–0 hero can't top a 40–25 one.</li>" +
      "<li>Each listing counts by order: a player's only hero = 1; two heroes = " + esc((W.listWeights || {})["2"].join(" / ")) +
      "; three = " + esc((W.listWeights || {})["3"].join(" / ")) + ". The sheet lists heroes, not time played.</li></ul>" +
      '<h3 class="u-mt-4">Tiers</h3><p class="small">Cut within each role on each map. Score = ' +
      esc(String((W.pick || {}).weight)) + " × √(pick share ÷ most-picked in the role) + " + esc(String((W.win || {}).weight)) +
      " × adjusted win scaled over 50% ± " + pct((W.win || {}).span) + ", out of 100. " +
      (W.tiers || []).map((t) => "<strong>" + esc(t.id) + "</strong> ≥ " + (t.min < 0 ? "anything" : t.min)).join(" · ") +
      ". Heroes on under " + pct(W.minPickShare) + " of team-maps (or under " + esc(String(W.minPicks)) +
      " weighted listings) are shown as low sample.</p></section>" +
      '<section class="card"><h2>Power ranking</h2><p class="small">Elo starts at the region tier and moves by ' +
      esc(String((W.elo || {}).k)) + " × importance × (result − expected) after every map. International results are what " +
      "move regions against each other. A team's record in its current regional stage adds up to ±" +
      Math.round(((W.standings || {}).blend || 0) * ((W.standings || {}).points || 0)) + " points.</p>" +
      '<div class="table-wrap u-mt-3"><table class="tbl tbl--compact tbl--fit"><caption>Region tier (starting rating)</caption><tbody>' +
      Object.keys(rt).map((k) => li(k === "default" ? "Other / unknown" : (REGION_SHORT[k] || k), rt[k])).join("") + "</tbody></table></div></section>" +
      '<section class="card"><h2>Event weight</h2><div class="table-wrap u-mt-3"><table class="tbl tbl--compact tbl--fit"><tbody>' +
      Object.keys(ev).map((k) => li(STAGE_NAMES[k] || (k === "asia/stage-1" ? "Asia Stage 1 championship" : k), "× " + ev[k])).join("") +
      "</tbody></table></div></section>" +
      '<section class="card"><h2>Bracket weight</h2><div class="table-wrap u-mt-3"><table class="tbl tbl--compact tbl--fit"><tbody>' +
      (W.bracketWeight || []).map((p) => li(p[0], "× " + p[1])).join("") + li("anything else", "× " + W.bracketDefault) +
      li("“Group A/B …” rounds", "× " + W.groupPrefixFactor + " more") + "</tbody></table></div></section>" +
      "</div>" +
      '<p class="small dim u-mt-5">Every number above lives in <span class="mono">config/tier_weights.json</span>; stage dates in ' +
      '<span class="mono">config/external/sheet_schedule.json</span>. Edit either and run ' +
      '<span class="mono">python3 pipeline/sheet_sync.py rebuild</span>.</p>');
  }

  /* ------------------------------------------------------------ boot */
  document.addEventListener("DOMContentLoaded", () => {
    if (!T || !T.maps || !T.maps.length) {
      P.mount("p-heroes", P.empty("◇", "No spreadsheet import yet", "Run the sheet-sync workflow to build the tier data."));
      return;
    }
    renderProvenance();
    renderFilters();
    renderHow();
    update();
    P.initTabs(document, { key: "tab" });
  });
})();
