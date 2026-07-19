/* =====================================================================
   OWCS Comp Tracker — app.js
   One shared script; each page sets <body data-page="..."> and this
   file renders it from window.OWCS_DATA. No frameworks, no build step.
   ===================================================================== */

(function () {
  const D = window.OWCS_DATA || { heroes: [], maps: [], teams: [], matches: [], teamPrepNotes: [] };
  D.heroes = D.heroes || [];
  D.maps = D.maps || [];
  D.teams = D.teams || [];
  D.matches = D.matches || [];
  D.teamPrepNotes = D.teamPrepNotes || [];

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const esc = value => String(value ?? "").replace(/[&<>'"]/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  }[ch]));

  // ---- Lookups ---------------------------------------------------------
  const heroById = Object.fromEntries(D.heroes.map(h => [h.id, h]));
  const mapById  = Object.fromEntries(D.maps.map(m => [m.id, m]));
  const teamById = Object.fromEntries(D.teams.map(t => [t.id, t]));
  const regions  = [...new Set(D.teams.map(t => t.region).filter(Boolean))].sort();
  const dates = D.matches.map(m => m.date).filter(Boolean).sort();
  const MIN_DATE = dates[0] || "";
  const MAX_DATE = dates[dates.length - 1] || "";

  // ---- Normalize provenance-separated data -----------------------------
  // data.js keeps FACEIT-sourced facts under .faceit and tracker-generated
  // comp data under .tracker. Hoist both into the flat fields the UI reads,
  // keeping the separation visible via g.faceit / g.tracker for badges.
  D.matches.forEach(m => {
    const F = m.faceit || {};
    m.faceitMatchId = m.faceitMatchId ?? F.matchId ?? null;
    m.faceitRoomUrl = m.faceitRoomUrl ?? F.roomUrl ?? null;
    m.replayCodes   = m.replayCodes ?? F.replayCodes ?? [];
    m.heroBans      = m.heroBans ?? F.heroBans ?? [];
    m.pickVeto      = m.pickVeto ?? F.pickVeto ?? [];
    m.rosters       = F.rosters || { a: [], b: [] };
    (m.maps || []).forEach(g => {
      const gf = g.faceit || {}, gt = g.tracker || {};
      g.mapOrder     = g.mapOrder ?? gf.mapOrder;
      g.order        = g.order ?? gf.mapOrder;
      g.scoreA       = g.scoreA ?? gf.mapScores?.a;
      g.scoreB       = g.scoreB ?? gf.mapScores?.b;
      g.score        = g.score ?? gf.mapScores;
      g.winnerTeam   = g.winnerTeam ?? gf.winnerTeam;
      g.pickedByTeam = g.pickedByTeam ?? gf.pickedByTeam;
      g.vetoAction   = g.vetoAction ?? gf.vetoAction;
      g.pickVeto     = g.pickVeto ?? gf.pickVeto;
      g.replayCode   = g.replayCode ?? gf.replayCode;
      g.replayCodes  = g.replayCodes ?? gf.replayCodes ?? [];
      g.replayExpiresNote = g.replayExpiresNote ?? gf.replayExpiresNote;
      g.heroBans     = g.heroBans ?? gf.heroBans ?? [];
      g.bans         = g.heroBans;
      g.faceitSource = gf.source;
      // tracker-generated (never assumed from FACEIT)
      g.detected     = gt.detected ?? Boolean((gt.playedHeroesA||[]).length || (gt.playedHeroesB||[]).length);
      g.openerCompA  = gt.openerCompA || [];
      g.openerCompB  = gt.openerCompB || [];
      g.playedHeroesA = gt.playedHeroesA || [];
      g.playedHeroesB = gt.playedHeroesB || [];
      g.swapsA       = gt.swapsA || [];
      g.swapsB       = gt.swapsB || [];
      g.compTimeline = gt.compTimeline || { a: [], b: [] };
      g.confidence   = gt.confidence ?? null;
      g.trackerSource = gt.source ?? null;
      g.trackerSourceA = gt.sourceA ?? null;
      g.trackerSourceB = gt.sourceB ?? null;
      g.confidenceA = gt.confidenceA ?? null;
      g.confidenceB = gt.confidenceB ?? null;
      g.detectedA = gt.detectedA ?? Boolean((gt.playedHeroesA || []).length);
      g.detectedB = gt.detectedB ?? Boolean((gt.playedHeroesB || []).length);
    });
  });

  // ---- Flatten matches into per-team map plays -------------------------
  // Stats (pick rate / win rate) derive ONLY from tracker-generated
  // playedHeroes — never from FACEIT metadata. Undetected maps contribute
  // nothing to hero stats but still appear in match/prep views.
  const plays = [];
  D.matches.forEach(m => {
    (m.maps || []).forEach(g => {
      if (g.playedHeroesA.length)
        plays.push({ date: m.date || "", region: m.region || "", matchId: m.id, mapId: g.map,
                     teamId: m.teamA, comp: g.playedHeroesA, won: g.winner === "a" || g.winnerTeam === m.teamA });
      if (g.playedHeroesB.length)
        plays.push({ date: m.date || "", region: m.region || "", matchId: m.id, mapId: g.map,
                     teamId: m.teamB, comp: g.playedHeroesB, won: g.winner === "b" || g.winnerTeam === m.teamB });
    });
  });

  // ---- Filtering -------------------------------------------------------
  function readFilters() {
    return {
      from:   $("#f-from") ? $("#f-from").value : "",
      to:     $("#f-to") ? $("#f-to").value : "",
      region: $("#f-region") ? $("#f-region").value : "",
      map:    $("#f-map") ? $("#f-map").value : "",
      role:   $("#f-role") ? $("#f-role").value : "",
      team:   $("#f-team") ? $("#f-team").value : "",
      banned: $("#f-banned") ? $("#f-banned").value : "",
      hasReplay: $("#f-replay") ? $("#f-replay").value : "",
    };
  }
  function playPasses(p, f) {
    if (f.from && p.date < f.from) return false;
    if (f.to && p.date > f.to) return false;
    if (f.region && p.region !== f.region) return false;
    if (f.map && p.mapId !== f.map) return false;
    if (f.team && p.teamId !== f.team) return false;
    return true;
  }

  // ---- Rendering helpers ----------------------------------------------
  const pct = v => v == null ? "—" : Math.round(v * 100) + "%";
  const fmtDate = iso => {
    if (!iso) return "Date unknown";
    return new Date(iso + "T00:00:00").toLocaleDateString(undefined, {
      weekday: "short", year: "numeric", month: "short", day: "numeric"
    });
  };
  const teamName = id => teamById[id]?.name || id || "Unknown team";
  const teamCode = id => teamById[id]?.code || teamName(id);
  const mapName = id => mapById[id]?.name || id || "Unknown map";
  const mapMode = id => mapById[id]?.mode || "Unknown mode";
  const sourceLabel = source => source ? `<span class="source-badge">${esc(source)}</span>` : "";

  function meter(rate, colorClass) {
    if (rate == null) return "<span class='muted small'>—</span>";
    const on = Math.round(rate * 10);
    let segs = "";
    for (let i = 0; i < 10; i++) segs += `<i class="${i < on ? "on" : ""}"></i>`;
    return `<span class="meter ${colorClass || ""}" aria-label="${pct(rate)}">${segs}</span>`;
  }

  function heroChip(hid) {
    const h = heroById[hid] || { name: hid || "Unknown", role: "Unknown" };
    const initials = h.name.replace(/[^A-Za-z0-9 ]/g, "").split(" ")
      .filter(Boolean).map(w => w[0]).join("").slice(0, 2).toUpperCase() || "?";
    return `<span class="chip" title="${esc(h.name)} · ${esc(h.role)}">
      <span class="dot ${esc(h.role)}">${esc(initials)}</span>${esc(h.name)}</span>`;
  }
  function compHtml(comp, emptyText) {
    const list = Array.isArray(comp) ? comp.filter(Boolean) : [];
    if (!list.length) return `<span class="muted small">${esc(emptyText || "Comp not recorded yet")}</span>`;
    return `<span class="comp">${list.map(heroChip).join("")}</span>`;
  }
  function banHtml(bans) {
    const list = Array.isArray(bans) ? bans : [];
    if (!list.length) return `<span class="muted small">No bans recorded</span>`;
    return `<span class="comp">${list.map(b => {
      const hid = b.hero || b.heroId;
      const h = heroById[hid] || { name: hid || "Unknown", role: "Unknown" };
      const team = b.teamId ? ` · ${teamCode(b.teamId)}` : "";
      return `<span class="chip ban-chip" title="${esc((b.source || "unknown") + team)}">${esc(h.name)}${team ? `<span class="muted tiny">${esc(team)}</span>` : ""}</span>`;
    }).join("")}</span>`;
  }
  function replayHtml(codes, note) {
    const list = Array.isArray(codes) ? codes.filter(Boolean) : [];
    if (!list.length) return `<span class="muted small">No replay code</span>`;
    return `<span class="replay-list">${list.map(c => `<button class="replay-code copy-code" data-code="${esc(c)}" title="Copy replay code">${esc(c)}</button>`).join("")}${note ? `<span class="muted tiny">${esc(note)}</span>` : ""}</span>`;
  }
  function mapScore(g) {
    if (g.scoreA == null && g.scoreB == null) return "Score unknown";
    return `${g.scoreA ?? "?"}–${g.scoreB ?? "?"}`;
  }
  function matchScore(m) {
    return `${m.scoreA ?? m.score?.a ?? 0}–${m.scoreB ?? m.score?.b ?? 0}`;
  }
  function faceitLink(m) {
    if (!m.faceitRoomUrl) return "";
    return `<a class="btn btn-ghost btn-sm" href="${esc(m.faceitRoomUrl)}" target="_blank" rel="noopener">Open FACEIT</a>`;
  }
  function empty(message) {
    return `<div class="empty">${esc(message)}</div>`;
  }

  function fillFilterOptions() {
    const reg = $("#f-region");
    if (reg && reg.options.length <= 1) regions.forEach(r => reg.insertAdjacentHTML("beforeend", `<option value="${esc(r)}">${esc(r)}</option>`));
    const prepReg = $("#prep-region");
    if (prepReg && prepReg.options.length <= 1) regions.forEach(r => prepReg.insertAdjacentHTML("beforeend", `<option value="${esc(r)}">${esc(r)}</option>`));

    const maps = D.maps.slice().sort((a, b) => a.name.localeCompare(b.name));
    ["#f-map"].forEach(sel => {
      const map = $(sel);
      if (map && map.options.length <= 1) maps.forEach(m => map.insertAdjacentHTML("beforeend", `<option value="${esc(m.id)}">${esc(m.name)} · ${esc(m.mode)}</option>`));
    });

    ["#f-team", "#prep-team", "#prep-opponent"].forEach(sel => {
      const el = $(sel);
      if (el && el.options.length <= 1) D.teams.slice().sort((a, b) => a.name.localeCompare(b.name))
        .forEach(t => el.insertAdjacentHTML("beforeend", `<option value="${esc(t.id)}">${esc(t.name)} · ${esc(t.region)}</option>`));
    });

    const banned = $("#f-banned");
    if (banned && banned.options.length <= 1) D.heroes.slice().sort((a, b) => a.name.localeCompare(b.name))
      .forEach(h => banned.insertAdjacentHTML("beforeend", `<option value="${esc(h.id)}">${esc(h.name)}</option>`));

    const from = $("#f-from"), to = $("#f-to");
    if (from && MIN_DATE) { from.value = MIN_DATE; from.min = MIN_DATE; from.max = MAX_DATE; }
    if (to && MAX_DATE)   { to.value = MAX_DATE;   to.min = MIN_DATE;   to.max = MAX_DATE; }
    const prepFrom = $("#prep-from"), prepTo = $("#prep-to");
    if (prepFrom && MIN_DATE) { prepFrom.value = MIN_DATE; prepFrom.min = MIN_DATE; prepFrom.max = MAX_DATE; }
    if (prepTo && MAX_DATE)   { prepTo.value = MAX_DATE;   prepTo.min = MIN_DATE;   prepTo.max = MAX_DATE; }
  }

  // =====================================================================
  // PAGE: stats (hero win/pick rates)
  // =====================================================================
  let sortKey = "pickRate", sortDir = -1;

  function heroStats(f) {
    const filtered = plays.filter(p => playPasses(p, f));
    const totalPlays = filtered.length;
    const tally = {};
    filtered.forEach(p => {
      (p.comp || []).forEach(hid => {
        (tally[hid] = tally[hid] || { picks: 0, wins: 0 });
        tally[hid].picks += 1;
        if (p.won) tally[hid].wins += 1;
      });
    });
    return D.heroes
      .filter(h => !f.role || h.role === f.role)
      .map(h => {
        const t = tally[h.id] || { picks: 0, wins: 0 };
        return {
          hero: h,
          picks: t.picks,
          pickRate: totalPlays ? t.picks / totalPlays : 0,
          winRate: t.picks ? t.wins / t.picks : null,
        };
      })
      .filter(r => r.picks > 0);
  }

  function renderStats() {
    const f = readFilters();
    const rows = heroStats(f).sort((a, b) => {
      const av = sortKey === "name" ? a.hero.name : a[sortKey] ?? -1;
      const bv = sortKey === "name" ? b.hero.name : b[sortKey] ?? -1;
      return (av > bv ? 1 : av < bv ? -1 : 0) * sortDir;
    });

    const body = $("#stats-body");
    if (!body) return;
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="5">${empty("No comps recorded in this filter range yet. Widen the dates or clear a filter.")}</td></tr>`;
      return;
    }
    body.innerHTML = rows.map(r => `
      <tr data-hero="${esc(r.hero.id)}">
        <td><strong>${esc(r.hero.name)}</strong></td>
        <td><span class="role-tag">${esc(r.hero.role)}</span></td>
        <td class="num">${pct(r.pickRate)} ${meter(r.pickRate)}</td>
        <td class="num">${pct(r.winRate)} ${meter(r.winRate, r.winRate >= .5 ? "good" : "bad")}</td>
        <td class="num muted">${r.picks}</td>
      </tr>`).join("");

    $$("#stats-body tr").forEach(tr =>
      tr.addEventListener("click", () => showHeroDetail(tr.dataset.hero, f)));
    $$("th[data-sort]").forEach(th =>
      th.classList.toggle("sorted", th.dataset.sort === sortKey));
  }

  function showHeroDetail(hid, f) {
    const h = heroById[hid];
    if (!h) return;
    const noMap = Object.assign({}, f, { map: "" });
    const byMap = {};
    plays.filter(p => playPasses(p, noMap)).forEach(p => {
      if (!(p.comp || []).includes(hid)) return;
      (byMap[p.mapId] = byMap[p.mapId] || { picks: 0, wins: 0 });
      byMap[p.mapId].picks++; if (p.won) byMap[p.mapId].wins++;
    });
    const rows = Object.entries(byMap)
      .map(([mid, t]) => ({ map: mapById[mid] || { name: mid, mode: "Unknown" }, picks: t.picks, wr: t.wins / t.picks }))
      .sort((a, b) => b.wr - a.wr);

    const panel = $("#hero-panel");
    panel.innerHTML = `
      <button class="btn btn-ghost btn-sm panel-close" id="panel-x">Close</button>
      <p class="eyebrow">${esc(h.role)}</p>
      <h2>${esc(h.name)} by map</h2>
      ${rows.length ? `<div class="table-wrap"><table class="stats"><thead>
        <tr><th>Map</th><th>Mode</th><th>Win rate</th><th>Maps played</th></tr></thead><tbody>
        ${rows.map(r => `<tr>
          <td><strong>${esc(r.map.name)}</strong></td>
          <td><span class="role-tag">${esc(r.map.mode)}</span></td>
          <td class="num">${pct(r.wr)} ${meter(r.wr, r.wr >= .5 ? "good" : "bad")}</td>
          <td class="num muted">${r.picks}</td></tr>`).join("")}
      </tbody></table></div>` : `<p class="muted">No recorded maps for ${esc(h.name)} in this range.</p>`}`;
    panel.hidden = false;
    $("#panel-x").addEventListener("click", () => { panel.hidden = true; });
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // =====================================================================
  // Shared analyst calculations
  // =====================================================================
  function teamRecord(tid) {
    let mw = 0, ml = 0, gw = 0, gl = 0;
    D.matches.forEach(m => {
      if (m.teamA !== tid && m.teamB !== tid) return;
      const isA = m.teamA === tid;
      const a = m.scoreA ?? m.score?.a ?? 0, b = m.scoreB ?? m.score?.b ?? 0;
      if (a !== b) ((isA && a > b) || (!isA && b > a)) ? mw++ : ml++;
      (m.maps || []).forEach(g => {
        const won = g.winnerTeam ? g.winnerTeam === tid : ((g.winner === "a") === isA);
        won ? gw++ : gl++;
      });
    });
    return { mw, ml, gw, gl };
  }

  function teamAnalytics(tid) {
    const matches = D.matches.filter(m => m.teamA === tid || m.teamB === tid)
      .sort((a, b) => (b.date || "").localeCompare(a.date || ""));
    const mapStats = {}, bansFor = {}, bansAgainst = {}, replayRows = [], pickRows = [];
    matches.forEach(m => {
      const isA = m.teamA === tid;
      const opp = isA ? m.teamB : m.teamA;
      (m.maps || []).forEach(g => {
        const mid = g.map;
        if (!mapStats[mid]) mapStats[mid] = { played: 0, wins: 0, picked: 0, banned: 0 };
        mapStats[mid].played++;
        const won = g.winnerTeam ? g.winnerTeam === tid : ((g.winner === "a") === isA);
        if (won) mapStats[mid].wins++;
        if (g.pickedByTeam === tid) mapStats[mid].picked++;
        if (g.vetoAction === "ban" && g.pickedByTeam === tid) mapStats[mid].banned++;
        if (g.pickVeto || g.pickedByTeam || g.vetoAction) {
          pickRows.push({ match: m, map: g, opponent: opp });
        }
        (g.heroBans || g.bans || []).forEach(b => {
          const hid = b.hero || b.heroId;
          if (!hid) return;
          if (b.teamId === tid) bansFor[hid] = (bansFor[hid] || 0) + 1;
          else bansAgainst[hid] = (bansAgainst[hid] || 0) + 1;
        });
        const codes = g.replayCodes?.length ? g.replayCodes : (g.replayCode ? [g.replayCode] : []);
        codes.forEach(code => replayRows.push({ code, match: m, map: g, opponent: opp }));
      });
    });
    const topMaps = Object.entries(mapStats).map(([map, s]) => ({ map, ...s, wr: s.played ? s.wins / s.played : null }))
      .sort((a, b) => b.played - a.played || (b.wr || 0) - (a.wr || 0));
    const topList = obj => Object.entries(obj).map(([hero, count]) => ({ hero, count })).sort((a, b) => b.count - a.count);
    return { matches, topMaps, bansFor: topList(bansFor), bansAgainst: topList(bansAgainst), replayRows, pickRows };
  }

  function notesForTeam(tid) {
    return [
      ...(teamById[tid]?.prepNotes ? [{ noteType: "team", note: teamById[tid].prepNotes, source: "team" }] : []),
      ...D.teamPrepNotes.filter(n => n.teamId === tid)
    ];
  }


  function scoreForTeam(m, tid) {
    const isA = m.teamA === tid;
    const us = isA ? (m.scoreA ?? m.score?.a ?? 0) : (m.scoreB ?? m.score?.b ?? 0);
    const them = isA ? (m.scoreB ?? m.score?.b ?? 0) : (m.scoreA ?? m.score?.a ?? 0);
    return { us, them, result: us > them ? "W" : us < them ? "L" : "T" };
  }

  function matchDatePasses(m, f) {
    if (f.from && (m.date || "") < f.from) return false;
    if (f.to && (m.date || "") > f.to) return false;
    if (f.region && m.region !== f.region) return false;
    return true;
  }

  function prepFilters() {
    return {
      focus: $("#prep-team") ? $("#prep-team").value : "",
      opponent: $("#prep-opponent") ? $("#prep-opponent").value : "",
      region: $("#prep-region") ? $("#prep-region").value : "",
      from: $("#prep-from") ? $("#prep-from").value : "",
      to: $("#prep-to") ? $("#prep-to").value : "",
    };
  }

  function filteredTeamMatches(tid, f) {
    return D.matches.filter(m => {
      if (tid && m.teamA !== tid && m.teamB !== tid) return false;
      if (f.opponent && m.teamA !== f.opponent && m.teamB !== f.opponent) return false;
      return matchDatePasses(m, f);
    }).sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  }

  function teamComps(tid) {
    const counts = {};
    D.matches.forEach(m => {
      const isA = m.teamA === tid;
      if (!isA && m.teamB !== tid) return;
      (m.maps || []).forEach(g => {
        const comp = (isA ? g.openerCompA : g.openerCompB) || [];
        if (!comp.length) return;
        const key = comp.slice(0, 5).join("|");
        counts[key] = (counts[key] || 0) + 1;
      });
    });
    return Object.entries(counts).map(([key, count]) => ({ heroes: key.split("|"), count })).sort((a, b) => b.count - a.count);
  }

  function prepMapBuckets(tid) {
    const maps = teamAnalytics(tid).topMaps;
    return {
      likely: maps.filter(r => r.picked || r.played).slice().sort((a, b) => b.picked - a.picked || b.played - a.played).slice(0, 4),
      target: maps.filter(r => r.played >= 1).slice().sort((a, b) => (b.wr || 0) - (a.wr || 0) || b.played - a.played).slice(0, 4),
      avoid: maps.filter(r => r.played >= 1).slice().sort((a, b) => (a.wr || 0) - (b.wr || 0) || b.played - a.played).slice(0, 4)
    };
  }

  function sampleWarning(n) {
    if (!n) return `<span class="quality warn">No sample</span>`;
    if (n < 3) return `<span class="quality warn">Low sample</span>`;
    return `<span class="quality good">${n} maps</span>`;
  }

  function mapBucketHtml(title, rows, mode) {
    if (!rows.length) return `<div class="prep-mini"><h3>${esc(title)}</h3><p class="muted small">Not enough map data yet.</p></div>`;
    return `<div class="prep-mini"><h3>${esc(title)}</h3><div class="rank-list">
      ${rows.map((r, i) => `<div class="rank-row"><span class="rank">${i + 1}</span><div><strong>${esc(mapName(r.map))}</strong><span class="muted tiny">${esc(mapMode(r.map))}</span></div><div class="rank-metric">${mode === "pick" ? `${r.picked} picks` : pct(r.wr)} ${sampleWarning(r.played)}</div></div>`).join("")}
    </div></div>`;
  }

  function replayRowsForTeam(tid, f) {
    return teamAnalytics(tid).replayRows
      .filter(r => matchDatePasses(r.match, f))
      .filter(r => !f.opponent || r.opponent === f.opponent)
      .sort((a, b) => (b.match.date || "").localeCompare(a.match.date || ""));
  }

  function topHeroesHtml(rows, emptyText) {
    if (!rows.length) return `<p class="muted small">${esc(emptyText)}</p>`;
    return `<div class="stacked-chips">${rows.slice(0, 8).map(r => `<span class="chip">${esc(heroById[r.hero]?.name || r.hero)} <strong>×${r.count}</strong></span>`).join("")}</div>`;
  }

  function scoutReportHtml(f) {
    if (!f.focus) return `<div class="empty compact-empty">Pick a focus team to generate a scout report. Leave opponent blank for general prep, or choose an opponent for head-to-head prep.</div>`;
    const focus = teamById[f.focus];
    if (!focus) return empty("Selected team could not be found in data.js.");
    const a = teamAnalytics(f.focus);
    const matches = filteredTeamMatches(f.focus, f);
    const buckets = prepMapBuckets(f.focus);
    const comps = teamComps(f.focus);
    const oppText = f.opponent ? `vs ${teamName(f.opponent)}` : "general prep";
    const latest = matches[0];
    return `<div class="scout-card">
      <div class="scout-head">
        <div><p class="eyebrow">Scout report</p><h2>${esc(focus.name)} <span class="muted">${esc(oppText)}</span></h2></div>
        <div class="quality-block"><span class="quality ${matches.length >= 3 ? "good" : "warn"}">${matches.length} relevant matches</span><span class="quality">${a.replayRows.length} codes</span></div>
      </div>
      <div class="grid-3 scout-grid">
        ${mapBucketHtml("Likely picks", buckets.likely, "pick")}
        ${mapBucketHtml("Maps to target", buckets.target, "rate")}
        ${mapBucketHtml("Maps to review carefully", buckets.avoid, "rate")}
      </div>
      <div class="grid-2 analyst-section">
        <div class="card inner-card"><h3>Ban expectations</h3><p class="muted small">What this team tends to remove first. Casual read: these are heroes they likely do not want to deal with.</p>${topHeroesHtml(a.bansFor, "No bans recorded for this team.")}</div>
        <div class="card inner-card"><h3>Common openers</h3>${comps.length ? comps.slice(0, 4).map(c => `<div class="metric-row"><span>${compHtml(c.heroes, "")}</span><strong>×${c.count}</strong></div>`).join("") : `<p class="muted small">No comp snapshots yet. FACEIT map/replay data can still guide VOD prep.</p>`}</div>
      </div>
      <div class="coach-note"><strong>Fast read:</strong> ${esc(latest ? `${focus.code || focus.name} last played ${teamName(latest.teamA === f.focus ? latest.teamB : latest.teamA)} on ${fmtDate(latest.date)}. Start with the replay archive, then check likely picks and bans above.` : "No recent matches in this filter. Broaden the dates or region.")}</div>
    </div>`;
  }

  // =====================================================================
  // PAGE: teams
  // =====================================================================
  function renderTeams() {
    const grid = $("#team-grid");
    if (!grid) return;
    grid.innerHTML = D.teams.map(t => {
      const r = teamRecord(t.id);
      const a = teamAnalytics(t.id);
      const topMap = a.topMaps[0] ? mapName(a.topMaps[0].map) : "No maps yet";
      return `<div class="team-card" data-team="${esc(t.id)}" role="button" tabindex="0"
                   aria-label="View ${esc(t.name)} analyst prep">
        <div class="team-code">${esc(t.code)}</div>
        <div><strong>${esc(t.name)}</strong></div>
        <div class="team-meta">${esc(t.region)} · top map: ${esc(topMap)}</div>
        <div class="team-record">Matches <span class="w">${r.mw}W</span>–<span class="l">${r.ml}L</span>
          &nbsp;·&nbsp; Maps <span class="w">${r.gw}W</span>–<span class="l">${r.gl}L</span></div>
      </div>`;
    }).join("");

    $$(".team-card").forEach(card => {
      const open = () => maybeAskDonation(() => showTeamDetail(card.dataset.team));
      card.addEventListener("click", open);
      card.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    });
  }

  function showTeamDetail(tid) {
    const t = teamById[tid];
    if (!t) return;
    const a = teamAnalytics(tid);
    const r = teamRecord(tid);
    const notes = notesForTeam(tid);
    const panel = $("#team-panel");
    panel.innerHTML = `
      <button class="btn btn-ghost btn-sm panel-close" id="team-x">Close</button>
      <p class="eyebrow">${esc(t.region)} · ${esc(t.code)}</p>
      <h2>${esc(t.name)} — analyst prep sheet</h2>
      <div class="analyst-grid compact">
        <div class="metric-card"><span>Match record</span><strong>${r.mw}–${r.ml}</strong></div>
        <div class="metric-card"><span>Map record</span><strong>${r.gw}–${r.gl}</strong></div>
        <div class="metric-card"><span>Replay codes</span><strong>${a.replayRows.length}</strong></div>
        <div class="metric-card"><span>Tracked bans</span><strong>${a.bansFor.reduce((n, b) => n + b.count, 0)}</strong></div>
      </div>
      <div class="grid-2 analyst-section">
        <div class="card"><h3>Map tendencies</h3>${mapTendencyTable(a.topMaps)}</div>
        <div class="card"><h3>Hero bans / banned against</h3>${banSummary(a.bansFor, a.bansAgainst)}</div>
      </div>
      <div class="grid-2 analyst-section">
        <div class="card"><h3>Common opener comps</h3>${commonCompsHtml(tid)}</div>
        <div class="card"><h3>Casual viewer quick read</h3>${teamQuickReadHtml(tid)}</div>
      </div>
      <div class="card analyst-section"><h3>Recent matches</h3>${recentMatchesHtml(a.matches, tid)}</div>
      <div class="card analyst-section"><h3>Replay archive</h3>${replayArchiveHtml(a.replayRows)}</div>
      <div class="card analyst-section"><h3>Prep notes</h3>${notesHtml(notes)}</div>`;
    panel.hidden = false;
    $("#team-x").addEventListener("click", () => { panel.hidden = true; });
    attachCopyHandlers(panel);
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function mapTendencyTable(rows) {
    if (!rows.length) return `<p class="muted">No map data recorded yet.</p>`;
    return `<div class="table-wrap"><table class="stats mini-table"><thead><tr><th>Map</th><th>Played</th><th>Win</th><th>Picked</th></tr></thead><tbody>
      ${rows.slice(0, 8).map(r => `<tr><td><strong>${esc(mapName(r.map))}</strong><span class="map-mode">${esc(mapMode(r.map))}</span></td>
        <td class="num">${r.played}</td><td class="num">${pct(r.wr)}</td><td class="num">${r.picked}</td></tr>`).join("")}
    </tbody></table></div>`;
  }

  function banSummary(forRows, againstRows) {
    const list = (rows, emptyText) => rows.length
      ? `<div class="stacked-chips">${rows.slice(0, 6).map(r => `<span class="chip">${esc(heroById[r.hero]?.name || r.hero)} <strong>×${r.count}</strong></span>`).join("")}</div>`
      : `<p class="muted small">${esc(emptyText)}</p>`;
    return `<div class="split-notes"><div><span class="side-label a">This team bans</span>${list(forRows, "No owned bans recorded.")}</div>
      <div><span class="side-label b">Banned against them</span>${list(againstRows, "No opponent bans recorded.")}</div></div>`;
  }

  function commonCompsHtml(tid) {
    const comps = teamComps(tid);
    if (!comps.length) return `<p class="muted">No comp snapshots yet. This section will fill after the vision pipeline runs.</p>`;
    return comps.slice(0, 5).map(c => `<div class="metric-row"><span>${compHtml(c.heroes, "")}</span><strong>×${c.count}</strong></div>`).join("");
  }

  function teamQuickReadHtml(tid) {
    const a = teamAnalytics(tid);
    const best = a.topMaps.slice().sort((x, y) => (y.wr || 0) - (x.wr || 0))[0];
    const worst = a.topMaps.slice().sort((x, y) => (x.wr || 0) - (y.wr || 0))[0];
    const ban = a.bansFor[0];
    return `<ul class="read-list">
      <li><strong>Best signal:</strong> ${best ? `${esc(mapName(best.map))} at ${pct(best.wr)} over ${best.played} map(s).` : "Need more map rows."}</li>
      <li><strong>Watch closely:</strong> ${worst ? `${esc(mapName(worst.map))} at ${pct(worst.wr)} over ${worst.played} map(s).` : "Need more losses/wins to compare."}</li>
      <li><strong>Ban habit:</strong> ${ban ? `${esc(heroById[ban.hero]?.name || ban.hero)} is their most repeated recorded ban.` : "No bans recorded yet."}</li>
      <li><strong>Start prep:</strong> Open the newest replay code first, then compare map order against their likely picks.</li>
    </ul>`;
  }

  function recentMatchesHtml(matches, tid) {
    if (!matches.length) return `<p class="muted">No matches recorded for this team yet.</p>`;
    return matches.slice(0, 6).map(m => {
      const isA = m.teamA === tid;
      const opp = isA ? m.teamB : m.teamA;
      const us = isA ? (m.scoreA ?? 0) : (m.scoreB ?? 0);
      const them = isA ? (m.scoreB ?? 0) : (m.scoreA ?? 0);
      return `<div class="prep-row">
        <div><strong>${esc(fmtDate(m.date))}</strong><span class="muted"> · ${esc(m.stage || m.round || "Stage unknown")} · vs ${esc(teamName(opp))}</span></div>
        <div class="match-score"><span class="${us > them ? "a" : "b"}">${us}</span>–${them}</div>
        <div class="row-actions">${faceitLink(m)}</div>
      </div>
      <div class="map-strip">${(m.maps || []).map(g => `<span class="pill">#${g.mapOrder || g.order || "?"} ${esc(mapName(g.map))} · ${esc(mapScore(g))}</span>`).join("") || `<span class="muted small">No map rows yet.</span>`}</div>`;
    }).join("");
  }

  // Comp status for a map, from the tracker block: pending / manual / cv / detected.
  function compStatusBadge(g) {
    if (!g.detected) return `<span class="status-badge pending">pending</span>`;
    const src = g.trackerSource || "detected";
    const cls = src === "manual" ? "manual" : src === "cv" ? "cv" : "detected";
    return `<span class="status-badge ${cls}">${esc(src)}</span>`;
  }

  function replayArchiveHtml(rows) {
    if (!rows.length) return `<p class="muted">No replay codes recorded yet.</p>`;
    return `<div class="table-wrap"><table class="stats mini-table"><thead><tr><th>Code</th><th>Date</th><th>Opponent</th><th>Map</th><th>Result</th><th>Comp</th></tr></thead><tbody>
      ${rows.slice(0, 30).map(r => `<tr><td>${replayHtml([r.code], r.map.replayExpiresNote)}</td><td>${esc(r.match.date || "")}</td>
        <td>${esc(teamName(r.opponent))}</td><td>${esc(mapName(r.map.map))}</td><td>${esc(mapScore(r.map))}</td>
        <td>${compStatusBadge(r.map)}</td></tr>`).join("")}
    </tbody></table></div>`;
  }

  function notesHtml(notes) {
    if (!notes.length) return `<p class="muted">No prep notes yet. Add rows to team_prep_notes or team prep_notes in sample data.</p>`;
    return `<div class="note-list">${notes.map(n => `<div class="note-item"><span class="role-tag">${esc(n.noteType || "note")}</span>
      <p>${esc(n.note)}</p><span class="muted tiny">${esc(n.source || "manual")}${n.map ? " · " + esc(mapName(n.map)) : ""}</span></div>`).join("")}</div>`;
  }

  function maybeAskDonation(proceed) {
    if (sessionStorage.getItem("owcs-donate-seen")) return proceed();
    const modal = $("#donate-modal");
    if (!modal) return proceed();
    modal.classList.add("show");
    const done = () => {
      sessionStorage.setItem("owcs-donate-seen", "1");
      modal.classList.remove("show");
      proceed();
    };
    $("#donate-later").onclick = done;
    $("#donate-go").onclick = () => { done(); };
    modal.onclick = e => { if (e.target === modal) done(); };
  }

  // =====================================================================
  // PAGE: matches
  // =====================================================================
  function matchPasses(m, f) {
    if (f.region && m.region !== f.region) return false;
    if (f.from && m.date < f.from) return false;
    if (f.to && m.date > f.to) return false;
    if (f.team && m.teamA !== f.team && m.teamB !== f.team) return false;
    const maps = m.maps || [];
    if (f.map && !maps.some(g => g.map === f.map)) return false;
    if (f.banned && !maps.some(g => (g.heroBans || g.bans || []).some(b => (b.hero || b.heroId) === f.banned))) return false;
    if (f.hasReplay === "yes" && !maps.some(g => g.replayCode || (g.replayCodes || []).length)) return false;
    if (f.hasReplay === "no" && maps.some(g => g.replayCode || (g.replayCodes || []).length)) return false;
    return true;
  }

  function mapsForMatch(m, f) {
    return (m.maps || []).filter(g => {
      if (f.map && g.map !== f.map) return false;
      if (f.banned && !(g.heroBans || g.bans || []).some(b => (b.hero || b.heroId) === f.banned)) return false;
      if (f.hasReplay === "yes" && !(g.replayCode || (g.replayCodes || []).length)) return false;
      if (f.hasReplay === "no" && (g.replayCode || (g.replayCodes || []).length)) return false;
      return true;
    });
  }

  function renderMatches() {
    const f = readFilters();
    const list = $("#match-list");
    if (!list) return;
    const filtered = D.matches
      .filter(m => matchPasses(m, f))
      .sort((a, b) => (b.date || "").localeCompare(a.date || ""));

    if (!filtered.length) {
      list.innerHTML = empty("No matches in this range. Widen the dates or clear filters.");
      return;
    }

    const byDate = {};
    filtered.forEach(m => (byDate[m.date || "unknown"] = byDate[m.date || "unknown"] || []).push(m));

    list.innerHTML = Object.entries(byDate).map(([date, ms]) => `
      <div class="day-block">
        <div class="day-label">${esc(date === "unknown" ? "Date unknown" : fmtDate(date))}</div>
        ${ms.map(m => matchCardHtml(m, f)).join("")}
      </div>`).join("");

    $$(".match-head").forEach(head => {
      if (head.classList.contains("no-toggle")) return;
      const toggle = () => {
        const card = head.parentElement;
        card.classList.toggle("open");
        head.setAttribute("aria-expanded", card.classList.contains("open"));
      };
      head.addEventListener("click", toggle);
      head.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
    });
    attachCopyHandlers(list);
  }

  function matchCardHtml(m, f) {
    const ta = teamById[m.teamA] || { name: m.teamA || "Team A", code: "A" };
    const tb = teamById[m.teamB] || { name: m.teamB || "Team B", code: "B" };
    const maps = mapsForMatch(m, f);
    return `<div class="match-card" id="card-${esc(m.id)}">
      <div class="match-head" role="button" tabindex="0" aria-expanded="false"
           aria-label="Toggle map details for ${esc(ta.name)} vs ${esc(tb.name)}">
        <span class="match-score"><span class="a">${esc(m.scoreA ?? m.score?.a ?? 0)}</span>–<span class="b">${esc(m.scoreB ?? m.score?.b ?? 0)}</span></span>
        <span class="match-teams">${esc(ta.name)} <span class="muted">vs</span> ${esc(tb.name)}</span>
        <span class="match-stage">${esc(m.region || "Unknown")} · ${esc(m.stage || m.round || "Stage unknown")} · ${(m.maps || []).length} maps · ${((m.replayCodes || []).length || (m.maps || []).filter(g => g.replayCode || (g.replayCodes || []).length).length)} codes</span>
      </div>
      <div class="match-maps">
        <div class="match-tools">
          <div>${sourceLabel(m.rawSource)} ${m.faceitMatchId ? `<span class="source-badge">FACEIT ${esc(m.faceitMatchId)}</span>` : ""}</div>
          <div class="row-actions">${faceitLink(m)}</div>
        </div>
        ${rosterHtml(m, ta, tb)}
        ${m.prepNotes ? `<p class="source-note">${esc(m.prepNotes)}</p>` : ""}
        ${maps.length ? maps.map(g => mapRowHtml(g, m, ta, tb)).join("") : empty("No map rows match these filters yet. FACEIT ingest can still show this match shell until map data is available.")}
      </div>
    </div>`;
  }

  function heroPicksMissingMsg(g) {
    const hasCode = Boolean(g.replayCode || (g.replayCodes || []).length);
    return hasCode ? "Hero picks not detected yet. Replay code available."
                   : "Hero picks not detected yet.";
  }

  function trackerCompCells(g, ta, tb) {
    if (!g.detected) {
      return `<div class="map-detail map-detail-wide">
        <span class="side-label">Team comps <span class="source-badge tracker">Tracker</span></span>
        <p class="muted small">${esc(heroPicksMissingMsg(g))}</p></div>`;
    }
    // Each side shows its OWN source + confidence (a map can have one team
    // corrected manually and the other still from CV, or one side pending).
    const cell = (label, cls, opener, swaps, detected, source, confidence) => {
      if (!detected) {
        return `<div class="map-detail">
          <span class="side-label ${cls}">${esc(label)} <span class="source-badge tracker">Tracker</span></span>
          <p class="muted small">${esc(heroPicksMissingMsg(g))}</p></div>`;
      }
      const conf = confidence != null ? ` · ${Math.round(confidence * 100)}%` : "";
      const src = source ? ` ${esc(source)}` : "";
      return `<div class="map-detail">
        <span class="side-label ${cls}">${esc(label)} opener
          <span class="source-badge tracker">Tracker${src}${conf}</span></span>
        ${compHtml(opener, "")}
        ${swaps.length ? `<span class="muted tiny">Swapped in: </span>${compHtml(swaps, "")}` : ""}
      </div>`;
    };
    return cell(ta.code, "a", g.openerCompA, g.swapsA, g.detectedA, g.trackerSourceA, g.confidenceA)
         + cell(tb.code, "b", g.openerCompB, g.swapsB, g.detectedB, g.trackerSourceB, g.confidenceB);
  }

  function rosterHtml(m, ta, tb) {
    const r = m.rosters || { a: [], b: [] };
    if (!r.a.length && !r.b.length) return "";
    const side = (t, list) => `<span class="roster-side"><strong>${esc(t.code)}:</strong> ${list.map(p => esc(p.nickname)).join(", ")}</span>`;
    return `<p class="source-note"><span class="source-badge">FACEIT roster</span> ${side(ta, r.a)} · ${side(tb, r.b)}</p>`;
  }

  function mapRowHtml(g, m, ta, tb) {
    const codes = g.replayCodes?.length ? g.replayCodes : (g.replayCode ? [g.replayCode] : []);
    const winCode = g.winnerTeam ? teamCode(g.winnerTeam) : (g.winner === "a" ? ta.code : g.winner === "b" ? tb.code : "Unknown");
    const pickedBy = g.pickedByTeam ? teamCode(g.pickedByTeam) : "Unknown/neutral";
    return `<div class="map-row map-row-rich">
      <div class="map-main">
        <span class="map-name">#${esc(g.mapOrder || g.order || "?")} ${esc(mapName(g.map))}</span>
        <span class="map-mode">${esc(mapMode(g.map))}</span>
        <span class="map-result ${g.winner === "a" || g.winnerTeam === m.teamA ? "win" : "loss"}">${esc(winCode)} won · ${esc(mapScore(g))}</span>
        <div class="kv-list">
          <span><strong>Pick/veto:</strong> ${esc(g.pickVeto || g.vetoAction || "Unknown")}</span>
          <span><strong>Picked by:</strong> ${esc(pickedBy)}</span>
          <span><strong>Map data:</strong> <span class="source-badge">FACEIT</span> ${esc(g.faceitSource || "matchroom")} · <strong>Comps:</strong> <span class="source-badge tracker">Tracker</span> ${g.detected ? esc(g.trackerSource || "detected") + (g.confidence != null ? ` · ${Math.round(g.confidence * 100)}% confidence` : "") : "pending"}</span>
        </div>
      </div>
      ${trackerCompCells(g, ta, tb)}
      <div class="map-extra"><span class="side-label">Hero bans</span>${banHtml(g.heroBans || g.bans)}</div>
      <div class="map-extra"><span class="side-label">Replay code</span>${replayHtml(codes, g.replayExpiresNote)}</div>
    </div>`;
  }

  function attachCopyHandlers(root) {
    $$(".copy-code", root || document).forEach(btn => {
      btn.addEventListener("click", e => {
        e.stopPropagation();
        const code = btn.dataset.code;
        if (navigator.clipboard && code) navigator.clipboard.writeText(code).catch(() => {});
        btn.classList.add("copied");
        btn.textContent = "Copied " + code;
        setTimeout(() => { btn.classList.remove("copied"); btn.textContent = code; }, 1100);
      });
    });
  }

  // =====================================================================
  // PAGE: prep
  // =====================================================================
  // ---- Missing Comps Queue --------------------------------------------
  // A map belongs in the queue when it has FACEIT map data, tracker.detected
  // is false, and there's a way to review it (replay code OR FACEIT room link).
  function missingCompsRows() {
    const rows = [];
    D.matches.forEach(m => {
      const roomUrl = m.faceitRoomUrl || (m.faceit && m.faceit.roomUrl);
      (m.maps || []).forEach(g => {
        const hasFaceitMap = Boolean(g.map || g.mapOrder != null
          || (g.faceit && g.faceit.mapOrder != null));
        const code = g.replayCode || (g.replayCodes || [])[0] || null;
        const reviewable = Boolean(code || roomUrl);
        if (hasFaceitMap && g.detected === false && reviewable) {
          rows.push({ match: m, map: g, code, roomUrl });
        }
      });
    });
    return rows.sort((a, b) => (b.match.date || "").localeCompare(a.match.date || ""));
  }

  function renderMissingComps() {
    const box = $("#missing-comps-list");
    if (!box) return;
    const region = $("#mc-region") ? $("#mc-region").value : "";
    const hasCode = $("#mc-hascode") ? $("#mc-hascode").value : "";
    let rows = missingCompsRows();
    if (region) rows = rows.filter(r => (r.match.region || "") === region);
    if (hasCode === "yes") rows = rows.filter(r => r.code);
    if (hasCode === "no") rows = rows.filter(r => !r.code);

    const countEl = $("#missing-count");
    if (countEl) countEl.textContent = rows.length ? `· ${rows.length} map${rows.length > 1 ? "s" : ""} awaiting review` : "· all caught up";

    if (!rows.length) {
      box.innerHTML = `<div class="empty">No maps awaiting comp review in this filter. Every FACEIT map here already has hero picks, or none match.</div>`;
      return;
    }
    box.innerHTML = rows.slice(0, 40).map(r => {
      const m = r.match, g = r.map;
      const ta = teamById[m.teamA], tb = teamById[m.teamB];
      const mp = D.maps.find(x => x.id === g.map);
      const order = g.mapOrder ?? (g.faceit && g.faceit.mapOrder) ?? "?";
      const score = (g.scoreA != null || g.scoreB != null)
        ? `${g.scoreA ?? "?"}–${g.scoreB ?? "?"}` : "score n/a";
      const msg = r.code
        ? "Hero picks not detected yet. Replay code available."
        : "Hero picks not detected yet.";
      const bans = (g.heroBans || []).map(b => heroById[b.hero || b.heroId]?.name || b.hero || b.heroId).filter(Boolean);
      // per-team tracker status
      const statA = g.detectedA ? (g.trackerSourceA || "detected") : "pending";
      const statB = g.detectedB ? (g.trackerSourceB || "detected") : "pending";
      const sb = (label, stat) => `<span class="status-badge ${stat}">${esc(label)}: ${esc(stat)}</span>`;
      const correctBtn = (side, teamId, code) => {
        const done = side === "A" ? g.detectedA : g.detectedB;
        const url = `admin.html?match=${encodeURIComponent(m.id)}&mapOrder=${encodeURIComponent(order)}&team=${encodeURIComponent(teamId)}`;
        return `<a class="btn ${done ? "btn-ghost" : "btn-primary"} btn-sm" href="${url}">${done ? "Re-correct" : "Correct"} ${esc(code)}</a>`;
      };
      const factsUrl = `fact-admin.html?match=${encodeURIComponent(m.id)}&mapOrder=${encodeURIComponent(order)}`;
      return `<div class="missing-row">
        <div class="missing-main">
          <div class="missing-title">
            <strong>${esc((ta && ta.code) || m.teamA)} vs ${esc((tb && tb.code) || m.teamB)}</strong>
            <span class="muted small">· Map ${esc(String(order))}: ${esc(mp ? mp.name : g.map)} · ${esc(score)}</span>
          </div>
          <div class="muted tiny">${esc(m.date || "")} · ${esc(m.region || "Unknown")} · ${esc(m.stage || m.round || "")}</div>
          <p class="missing-msg">${esc(msg)}</p>
          <div class="missing-status">${sb((ta&&ta.code)||"A", statA)} ${sb((tb&&tb.code)||"B", statB)}
            ${g.faceit && g.faceit.factSource ? `<span class="source-badge">facts: ${esc(g.faceit.factSource)}</span>` : ""}</div>
          ${bans.length ? `<p class="tiny muted">Bans: ${esc(bans.join(", "))}</p>` : ""}
        </div>
        <div class="missing-actions">
          ${r.code ? `<button class="replay-code copy-code" data-code="${esc(r.code)}" title="Copy replay code">${esc(r.code)} ⧉</button>` : `<span class="muted tiny">no code</span>`}
          ${r.roomUrl ? `<a class="btn btn-ghost btn-sm" href="${esc(r.roomUrl)}" target="_blank" rel="noopener">FACEIT room</a>` : ""}
          ${correctBtn("A", m.teamA, (ta&&ta.code)||"A")}
          ${correctBtn("B", m.teamB, (tb&&tb.code)||"B")}
          <a class="btn btn-ghost btn-sm" href="${factsUrl}">Map facts</a>
        </div>
      </div>`;
    }).join("");
    attachCopyHandlers(box);
  }

  // ---- Review Progress dashboard --------------------------------------
  function renderReviewProgress() {
    const box = $("#review-progress");
    if (!box) return;
    let totalMaps = 0, withFacts = 0, withReplay = 0, missBoth = 0, missOne = 0,
        fullReviewed = 0, faceitOnly = 0;
    const compSrc = { sample: 0, manual: 0, cv: 0, pending: 0 };
    const factSrc = { faceit: 0, manual_facts: 0, sample: 0, unknown: 0 };
    let manualCompMaps = 0, manualFactMaps = 0;

    D.matches.forEach(m => (m.maps || []).forEach(g => {
      totalMaps++;
      const f = g.faceit || {};
      const hasFacts = Boolean(g.map || f.mapOrder != null);
      if (hasFacts) withFacts++;
      if (f.replayCode || (f.replayCodes || []).length) withReplay++;
      const a = g.detectedA, b = g.detectedB;
      if (!a && !b) missBoth++;
      else if (!a || !b) missOne++;
      else fullReviewed++;
      if (!a && !b && hasFacts) faceitOnly++;
      // per-side comp source tally
      [[a, g.trackerSourceA], [b, g.trackerSourceB]].forEach(([det, src]) => {
        if (!det) compSrc.pending++;
        else if (src === "manual") compSrc.manual++;
        else if (src === "cv") compSrc.cv++;
        else compSrc.sample++;
      });
      const fs = f.factSource || "unknown";
      factSrc[fs] = (factSrc[fs] || 0) + 1;
      if (g.trackerSourceA === "manual" || g.trackerSourceB === "manual") manualCompMaps++;
      if (fs === "manual_facts") manualFactMaps++;
    }));

    const stat = (label, val, hint) =>
      `<div class="rp-stat"><div class="rp-num">${val}</div><div class="rp-label">${esc(label)}</div>${hint ? `<div class="rp-hint muted tiny">${esc(hint)}</div>` : ""}</div>`;
    const pctReviewed = totalMaps ? Math.round(fullReviewed / totalMaps * 100) : 0;

    box.innerHTML = `
      <div class="rp-bar" title="${fullReviewed}/${totalMaps} maps fully reviewed">
        <span style="width:${pctReviewed}%"></span>
        <em>${pctReviewed}% fully reviewed (${fullReviewed}/${totalMaps} maps)</em>
      </div>
      <div class="rp-grid">
        ${stat("Total maps", totalMaps)}
        ${stat("With FACEIT facts", withFacts)}
        ${stat("With replay code", withReplay)}
        ${stat("Missing both comps", missBoth, "in the queue")}
        ${stat("Missing one comp", missOne, "half done")}
        ${stat("Fully reviewed", fullReviewed)}
        ${stat("FACEIT-only maps", faceitOnly, "facts, no comps")}
        ${stat("Manual comp maps", manualCompMaps)}
        ${stat("Manual-fact maps", manualFactMaps)}
      </div>
      <div class="rp-breakdown">
        <div><span class="rp-key">Comp sides:</span>
          <span class="status-badge sample">sample ${compSrc.sample}</span>
          <span class="status-badge manual">manual ${compSrc.manual}</span>
          <span class="status-badge cv">cv ${compSrc.cv}</span>
          <span class="status-badge pending">pending ${compSrc.pending}</span></div>
        <div><span class="rp-key">Fact sources:</span>
          <span class="source-badge">FACEIT ${factSrc.faceit || 0}</span>
          <span class="source-badge">manual ${factSrc.manual_facts || 0}</span>
          <span class="source-badge">sample ${factSrc.sample || 0}</span></div>
      </div>`;
  }

  function renderPrep() {
    const f = prepFilters();
    const selected = f.focus;
    const teams = selected ? D.teams.filter(t => t.id === selected) : D.teams.filter(t => !f.region || t.region === f.region).slice(0, 6);
    const cards = $("#prep-team-cards");
    const matches = $("#prep-match-list");
    const replays = $("#prep-replay-list");
    const scout = $("#prep-scout-report");
    const mapNotes = $("#prep-map-notes");
    const banSummaryBox = $("#prep-ban-summary");

    if (scout) scout.innerHTML = scoutReportHtml(f);
    if (cards) cards.innerHTML = teams.length ? teams.map(t => prepTeamCard(t)).join("") : empty("No teams match this prep filter.");
    if (matches) {
      const relevant = D.matches
        .filter(m => (!selected || m.teamA === selected || m.teamB === selected))
        .filter(m => (!f.opponent || m.teamA === f.opponent || m.teamB === f.opponent))
        .filter(m => matchDatePasses(m, f))
        .sort((a, b) => (b.date || "").localeCompare(a.date || ""))
        .slice(0, 10);
      matches.innerHTML = relevant.length ? relevant.map(m => prepMatchCard(m, selected || m.teamA)).join("") : empty("No recent or upcoming matches for this selection.");
    }
    if (replays) {
      let rows = [];
      (selected ? [selected] : D.teams.map(t => t.id)).forEach(tid => rows = rows.concat(replayRowsForTeam(tid, f).map(r => ({ ...r, teamId: tid }))));
      rows.sort((a, b) => (b.match.date || "").localeCompare(a.match.date || ""));
      replays.innerHTML = rows.length ? replayArchiveHtml(rows.slice(0, 30)) : `<p class="muted">No replay codes recorded yet.</p>`;
      attachCopyHandlers(replays);
    }
    if (mapNotes) {
      if (!selected) mapNotes.innerHTML = `<p class="muted">Choose a focus team to see target, avoid, and likely-pick map buckets.</p>`;
      else {
        const buckets = prepMapBuckets(selected);
        mapNotes.innerHTML = `<div class="grid-3">${mapBucketHtml("Likely picks", buckets.likely, "pick")}${mapBucketHtml("Target maps", buckets.target, "rate")}${mapBucketHtml("Review/avoid", buckets.avoid, "rate")}</div>`;
      }
    }
    if (banSummaryBox) {
      if (!selected) banSummaryBox.innerHTML = `<p class="muted">Choose a team to summarize ban tendencies.</p>`;
      else {
        const a = teamAnalytics(selected);
        banSummaryBox.innerHTML = banSummary(a.bansFor, a.bansAgainst);
      }
    }
    renderMissingComps();
    renderReviewProgress();
  }

  function prepTeamCard(t) {
    const a = teamAnalytics(t.id);
    const topMap = a.topMaps[0];
    const topBan = a.bansFor[0];
    return `<div class="card prep-card">
      <p class="eyebrow">${esc(t.region)} · ${esc(t.code)}</p>
      <h3>${esc(t.name)}</h3>
      <div class="metric-row"><span>Most played map</span><strong>${topMap ? esc(mapName(topMap.map)) : "—"}</strong></div>
      <div class="metric-row"><span>Map win rate there</span><strong>${topMap ? pct(topMap.wr) : "—"}</strong></div>
      <div class="metric-row"><span>Most common ban</span><strong>${topBan ? esc(heroById[topBan.hero]?.name || topBan.hero) : "—"}</strong></div>
      <div class="metric-row"><span>Replay codes</span><strong>${a.replayRows.length}</strong></div>
      ${notesHtml(notesForTeam(t.id).slice(0, 2))}
    </div>`;
  }

  function prepMatchCard(m, selected) {
    const focus = selected || m.teamA;
    const opponent = m.teamA === focus ? m.teamB : m.teamA;
    const maps = (m.maps || []).slice(0, 5);
    return `<div class="match-card open prep-match">
      <div class="match-head no-toggle">
        <span class="match-score"><span class="a">${esc(m.scoreA ?? 0)}</span>–<span class="b">${esc(m.scoreB ?? 0)}</span></span>
        <span class="match-teams">${esc(teamName(m.teamA))} <span class="muted">vs</span> ${esc(teamName(m.teamB))}</span>
        <span class="match-stage">${esc(m.date || "Date unknown")} · ${esc(m.region || "Unknown")}</span>
      </div>
      <div class="match-maps">
        <div class="match-tools"><span class="source-badge">${esc(m.status || "unknown")}</span><div>${faceitLink(m)}</div></div>
        <div class="map-strip">${maps.length ? maps.map(g => `<span class="pill">${esc(mapName(g.map))} · ${esc(g.pickVeto || g.vetoAction || "pick unknown")} · ${esc(mapScore(g))}</span>`).join("") : `<span class="muted small">Map order not loaded yet.</span>`}</div>
        <div class="split-notes">
          <div><span class="side-label a">Focus team</span><strong>${esc(teamName(focus))}</strong></div>
          <div><span class="side-label b">Opponent</span><strong>${esc(teamName(opponent))}</strong></div>
        </div>
      </div>
    </div>`;
  }

  // =====================================================================
  // PAGE: landing — fill live-looking counters + mini scoreboard
  // =====================================================================
  function renderLanding() {
    const totalMaps = D.matches.reduce((n, m) => n + (m.maps || []).length, 0);
    const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
    set("#stat-matches", D.matches.length);
    set("#stat-maps", totalMaps);
    set("#stat-comps", totalMaps * 2);
    set("#stat-teams", D.teams.length);

    const board = $("#board-rows");
    if (board) {
      const latest = D.matches.slice().sort((a, b) => (b.date || "").localeCompare(a.date || "")).slice(0, 4);
      board.innerHTML = latest.length ? latest.map(m => {
        const ta = teamById[m.teamA] || { code: "A" }, tb = teamById[m.teamB] || { code: "B" };
        const last = (m.maps || [])[Math.max((m.maps || []).length - 1, 0)];
        const mapText = last ? `${mapName(last.map)} · ${m.region || "Unknown"}` : `${m.region || "Unknown"} · maps pending`;
        return `<div class="board-row">
          <div>${esc(ta.code)} <span class="muted">vs</span> ${esc(tb.code)}
            <span class="board-map">· ${esc(mapText)}</span></div>
          <div class="match-score"><span class="a">${esc(m.scoreA ?? 0)}</span>–<span class="b">${esc(m.scoreB ?? 0)}</span></div>
        </div>`;
      }).join("") : `<div class="empty">No tracked matches yet.</div>`;
    }
  }

  // ---- Boot ------------------------------------------------------------
  document.addEventListener("DOMContentLoaded", () => {
    fillFilterOptions();
    const page = document.body.dataset.page;

    if (page === "stats") {
      renderStats();
      $$(".filters select, .filters input").forEach(el => el.addEventListener("change", renderStats));
      $$("th[data-sort]").forEach(th => th.addEventListener("click", () => {
        const k = th.dataset.sort;
        if (sortKey === k) sortDir *= -1; else { sortKey = k === "name" ? "name" : k; sortDir = k === "name" ? 1 : -1; }
        renderStats();
      }));
    }
    if (page === "teams") renderTeams();
    if (page === "matches") {
      renderMatches();
      $$(".filters select, .filters input").forEach(el => el.addEventListener("change", renderMatches));
    }
    if (page === "prep") {
      // populate the missing-comps region filter from known regions
      const mcRegion = $("#mc-region");
      if (mcRegion) {
        [...new Set(D.teams.map(t => t.region).concat(D.matches.map(m => m.region)))]
          .filter(Boolean).sort()
          .forEach(r => mcRegion.insertAdjacentHTML("beforeend", `<option value="${esc(r)}">${esc(r)}</option>`));
      }
      renderPrep();
      $$("#prep-team, #prep-opponent, #prep-region, #prep-from, #prep-to").forEach(el => el.addEventListener("change", renderPrep));
      $$("#mc-region, #mc-hascode").forEach(el => el.addEventListener("change", renderMissingComps));
    }
    if (page === "landing") renderLanding();
  });
})();
