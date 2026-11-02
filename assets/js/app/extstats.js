/* =====================================================================
   OWCS Comp Tracker — app/extstats.js
   Pure helpers over the two small spreadsheet files:

     window.OWCS_EXTERNAL_SUMMARY  (external_summary.v1.js)  totals, coverage,
                                    hero and team aggregates
     window.OWCS_EXTERNAL_SERIES   (external_series.v1.js)   one row per series

   Both are spreadsheet observations and stay that way here: nothing in
   this file reads the VOD dataset, so nothing can be blended. Pages put
   VOD numbers BESIDE these, each with its own badge.

     * a listed hero is an appearance: the rate is maps that list it ÷ maps
       with any listing — never seconds, never a share of playtime;
     * the sheet records no dates and no map picks. Order is the sheet's
       own: stage of the season, region, week, day, game;
     * a winner the VOD reading disputes is already excluded from the
       team records by the exporter and counted as `disputed`.

   Same file in the browser (window.OWCS.extStats) and under Node for the
   tests (module.exports), like metastats.js.
   ===================================================================== */
(function (root) {
  "use strict";

  /* Mirrors STAGE_ORDER in pipeline/external_sheet/summary.py (a test keeps them equal). */
  const STAGE_ORDER = ["pre-season", "stage-1", "champions-clash", "stage-2", "midseason-championship", "stage-3", "world-finals"];
  const REGION_ORDER = ["na", "emea", "korea", "japan", "china", "pacific", "asia", "international"];

  const stageRank = (s) => {
    const i = STAGE_ORDER.indexOf(s);
    return i >= 0 ? i : STAGE_ORDER.length;
  };
  const regionRank = (r) => {
    const i = REGION_ORDER.indexOf(r);
    return i >= 0 ? i : REGION_ORDER.length + (r ? 0 : 1);
  };
  const num = (v) => (typeof v === "number" ? v : 999);
  const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

  /* The sheet's own order for two series (same as summary.series_key). */
  function seriesCompare(a, b) {
    return cmp(a.year || 0, b.year || 0) || cmp(stageRank(a.stage), stageRank(b.stage)) ||
      cmp(a.stage || "", b.stage || "") || cmp(regionRank(a.region), regionRank(b.region)) ||
      cmp(num(a.week), num(b.week)) || cmp(num(a.day), num(b.day)) || cmp(num(a.game), num(b.game)) ||
      cmp(a.id || "", b.id || "");
  }

  /* --------------------------------------------------------- series */
  const LINKED = ["matched", "partial", "conflict"];
  const isLinked = (s) => (s.maps || []).some((m) => LINKED.indexOf(m.rec) >= 0);
  const needsReview = (s) => (s.maps || []).some((m) => m.rec === "needs_review");

  /* f = {region, stage, event, team, source: "linked" | "review" | "sheet-only", q, names}
     `names(teamId)` turns ids into searchable text; `q` matches teams, event,
     stage label, game label and the player of the match. Region "none" is
     the series the sheet files under no region (Pre-Season). */
  function filterSeries(rows, f) {
    f = f || {};
    const has = (v) => v != null && v !== "" && v !== "all";
    const q = has(f.q) ? String(f.q).toLowerCase() : "";
    const names = f.names || ((id) => id || "");
    return (rows || []).filter((s) => {
      if (has(f.region) && (f.region === "none" ? s.region != null : s.region !== f.region)) return false;
      if (has(f.stage) && s.stage !== f.stage) return false;
      if (has(f.event) && s.event !== f.event) return false;
      if (has(f.team) && s.teamA !== f.team && s.teamB !== f.team) return false;
      if (f.source === "linked" && !isLinked(s)) return false;
      if (f.source === "review" && !needsReview(s)) return false;
      if (f.source === "sheet-only" && (isLinked(s) || needsReview(s))) return false;
      if (q) {
        const hay = [names(s.teamA), names(s.teamB), s.teamA, s.teamB, s.event, s.stageRaw, s.label,
          s.format, s.potm].filter(Boolean).join(" ").toLowerCase();
        if (hay.indexOf(q) < 0) return false;
      }
      return true;
    }).sort(seriesCompare);
  }

  function seriesOptions(rows) {
    const uniq = (fn) => Array.from(new Set((rows || []).map(fn).filter((v) => v != null && v !== "")));
    return {
      regions: uniq((s) => s.region).sort((a, b) => regionRank(a) - regionRank(b)),
      hasNoRegion: (rows || []).some((s) => s.region == null),
      stages: uniq((s) => s.stage).sort((a, b) => stageRank(a) - stageRank(b) || cmp(a, b)),
      events: uniq((s) => s.event).sort(),
      teams: uniq((s) => s.teamA).concat(uniq((s) => s.teamB)).filter((v, i, a) => a.indexOf(v) === i).sort(),
    };
  }

  /* Consecutive rows of one stage + region become one group, in order. */
  function groupSeries(rows) {
    const out = [];
    (rows || []).forEach((s) => {
      const last = out[out.length - 1];
      if (last && last.stage === s.stage && last.region === s.region) last.rows.push(s);
      else out.push({ stage: s.stage, stageRaw: s.stageRaw, region: s.region, event: s.event, rows: [s] });
    });
    return out;
  }

  /* Map tallies for one series row: decided maps by side, draws, undecided. */
  function seriesTally(s) {
    const t = { a: 0, b: 0, draw: 0, undecided: 0, maps: (s.maps || []).length };
    (s.maps || []).forEach((m) => {
      if (m.w === "a" || m.w === "b") t[m.w] += 1;
      else if (m.w === "draw") t.draw += 1;
      else t.undecided += 1;
    });
    return t;
  }

  /* ------------------------------------------------------- the card */
  /* One series as the page draws it, from its index row and the detail
     shard rows of its maps (P.loadExtDetail). Players keep the sheet's
     position; each player's heroes stay in LIST order (`n`), which is the
     order the sheet lists them in — not the order they were played. */
  function seriesCard(row, details, records) {
    details = details || {};
    const recs = {};
    (records || []).forEach((r) => { recs[r.externalMapId] = r; });
    const maps = (row.maps || []).map((m) => {
      const d = details[m.id] || null;
      const sideBans = (side) => ((d && d.bans) || []).filter((b) => b.side === side)
        .map((b) => ({ hero: b.hero || null, raw: b.raw || null }));
      const players = (side) => (((d && d.teams && d.teams[side]) || {}).players || [])
        .slice().sort((x, y) => num(x.position) - num(y.position))
        .map((p) => ({ position: p.position, player: p.player || null, playerRaw: p.playerRaw || null,
          playerMapped: !!p.playerMapped, role: p.role || null,
          heroes: (p.heroes || []).slice().sort((x, y) => num(x.n) - num(y.n))
            .map((h) => ({ hero: h.hero || null, raw: h.raw || null, n: h.n })) }));
      return {
        id: m.id, order: m.n, map: m.map || null, mapRaw: m.raw || (d && d.mapRaw) || null,
        mode: (d && d.mode) || null, winnerSide: m.w || null,
        scoreA: d ? d.scoreA : null, scoreB: d ? d.scoreB : null, scoreRaw: d ? d.scoreRaw : null,
        flags: (d && d.flags) || [], loaded: !!d,
        bans: { a: sideBans("a"), b: sideBans("b") },
        players: { a: players("a"), b: players("b") },
        rec: recs[m.id] || null, recStatus: m.rec || "unmatched",
      };
    }).sort((x, y) => num(x.order) - num(y.order) || cmp(x.id, y.id));
    const tally = seriesTally(row);
    return {
      id: row.id, teamA: row.teamA, teamB: row.teamB, event: row.event, stage: row.stage,
      stageRaw: row.stageRaw, region: row.region, label: row.label, format: row.format,
      bestOf: row.bestOf, scoreA: row.scoreA, scoreB: row.scoreB, complete: row.complete,
      winner: row.winner, potm: row.potm, flags: row.flags || [], src: row.src || null,
      maps: maps, tally: tally,
      linkedVodMaps: maps.filter((m) => m.rec && m.rec.vodMapId).map((m) => m.rec.vodMapId),
      scoreAgreesWithMaps: row.scoreA == null || row.scoreB == null ? null
        : row.scoreA === tally.a && row.scoreB === tally.b,
    };
  }

  /* ---------------------------------------------------------- heroes */
  /* Appearance and ban table for a slice: all, one region, or one stage.
     rate = maps that list the hero ÷ maps with any listing in the slice;
     banRate = maps the hero is banned on ÷ maps with a ban recorded.
     The exporter stores each slice as [listedMaps, bans, banMaps]. */
  function heroTable(summary, f) {
    f = f || {};
    const H = (summary && summary.heroes) || {};
    const D = (summary && summary.heroDenominators) || {};
    let den, pick;
    if (f.region) {
      den = (D.region || {})[f.region] || { maps: 0, banMaps: 0 };
      pick = (h) => (h.region || {})[f.region] || [0, 0, 0];
    } else if (f.stage) {
      den = (D.stage || {})[f.stage] || { maps: 0, banMaps: 0 };
      pick = (h) => (h.stage || {})[f.stage] || [0, 0, 0];
    } else {
      den = D.all || { maps: 0, banMaps: 0 };
      pick = (h) => [h.listedMaps || 0, h.bans || 0, h.banMaps || 0];
    }
    const rows = Object.keys(H).map((id) => {
      const v = pick(H[id]);
      const banMaps = v[2] || 0;
      return { hero: id, listedMaps: v[0], maps: den.maps || 0,
        rate: den.maps ? v[0] / den.maps : null,
        bans: v[1], banMaps: banMaps, banDenominator: den.banMaps || 0,
        banRate: den.banMaps ? banMaps / den.banMaps : null };
    }).filter((r) => r.listedMaps || r.bans);
    rows.sort((a, b) => b.listedMaps - a.listedMaps || b.bans - a.bans || cmp(a.hero, b.hero));
    return { rows: rows, maps: den.maps || 0, banMaps: den.banMaps || 0 };
  }

  /* One hero across every region and every stage, in season order. */
  function heroSplit(summary, hero) {
    const h = ((summary && summary.heroes) || {})[hero] || null;
    const D = (summary && summary.heroDenominators) || {};
    const lane = (kind, rank) => Object.keys(D[kind] || {}).map((k) => {
      const den = D[kind][k];
      const v = (h && h[kind] && h[kind][k]) || [0, 0, 0];
      return { key: k, listedMaps: v[0], maps: den.maps, rate: den.maps ? v[0] / den.maps : null,
        bans: v[1], banMaps: v[2] || 0, banDenominator: den.banMaps,
        banRate: den.banMaps ? (v[2] || 0) / den.banMaps : null };
    }).sort((a, b) => rank(a.key) - rank(b.key) || cmp(a.key, b.key));
    const all = D.all || { maps: 0, banMaps: 0 };
    return {
      hero: hero, known: !!h,
      all: { listedMaps: h ? h.listedMaps : 0, listedSides: h ? h.listedSides : 0, maps: all.maps,
        rate: all.maps ? (h ? h.listedMaps : 0) / all.maps : null,
        bans: h ? h.bans : 0, banMaps: h ? h.banMaps : 0, banDenominator: all.banMaps,
        banRate: all.banMaps ? (h ? h.banMaps : 0) / all.banMaps : null },
      region: lane("region", (k) => (k === "none" ? 99 : regionRank(k))),
      stage: lane("stage", stageRank),
    };
  }

  /* ----------------------------------------------------------- teams */
  /* Map win share over DECIDED maps (won + lost + draw); undecided and
     disputed maps are reported beside it, never folded in. */
  function teamRecord(p) {
    if (!p) return null;
    const m = p.maps || {};
    const decided = (m.won || 0) + (m.lost || 0) + (m.draw || 0);
    const rows = (obj) => Object.keys(obj || {}).map((k) => {
      const v = obj[k];
      const d = v[1] + v[2] + v[3];
      return { key: k, played: v[0], won: v[1], lost: v[2], draw: v[3], decided: d,
        winShare: d ? v[1] / d : null };
    }).sort((a, b) => b.played - a.played || cmp(a.key, b.key));
    return {
      id: p.id, name: p.name, mapped: !!p.mapped, regions: p.regions || [], stages: p.stages || [],
      series: p.series || {}, maps: m, decided: decided,
      winShare: decided ? (m.won || 0) / decided : null,
      byMap: rows(p.byMap), byMode: rows(p.byMode),
      heroes: (p.heroes || []).map((x) => ({ hero: x[0], maps: x[1],
        rate: m.listed ? x[1] / m.listed : null })),
      bansBy: (p.bansBy || []).map((x) => ({ hero: x[0], count: x[1] })),
      bansAgainst: (p.bansAgainst || []).map((x) => ({ hero: x[0], count: x[1] })),
    };
  }

  /* Every team the sheet names, most maps first. */
  function teamList(summary) {
    const T = (summary && summary.teamProfiles) || {};
    return Object.keys(T).map((k) => T[k])
      .sort((a, b) => ((b.maps || {}).played || 0) - ((a.maps || {}).played || 0) || cmp(a.name || "", b.name || ""));
  }

  /* -------------------------------------------------------- coverage */
  function coverageMatrix(summary) {
    const rows = (summary && summary.coverage) || [];
    const stages = Array.from(new Set(rows.map((r) => r.stage)))
      .sort((a, b) => stageRank(a) - stageRank(b) || cmp(a || "", b || ""));
    const regions = Array.from(new Set(rows.map((r) => (r.region == null ? "none" : r.region))))
      .sort((a, b) => (a === "none" ? 99 : regionRank(a)) - (b === "none" ? 99 : regionRank(b)));
    const cells = {};
    const total = { matches: 0, maps: 0, bans: 0, listings: 0 };
    const byStage = {}, byRegion = {};
    rows.forEach((r) => {
      const reg = r.region == null ? "none" : r.region;
      cells[r.stage + "|" + reg] = r;
      ["matches", "maps", "bans", "listings"].forEach((k) => {
        total[k] += r[k] || 0;
        byStage[r.stage] = byStage[r.stage] || { matches: 0, maps: 0, bans: 0, listings: 0 };
        byRegion[reg] = byRegion[reg] || { matches: 0, maps: 0, bans: 0, listings: 0 };
        byStage[r.stage][k] += r[k] || 0;
        byRegion[reg][k] += r[k] || 0;
      });
    });
    return { stages: stages, regions: regions, cells: cells, total: total, byStage: byStage, byRegion: byRegion };
  }

  const api = { STAGE_ORDER: STAGE_ORDER, REGION_ORDER: REGION_ORDER, stageRank: stageRank,
    regionRank: regionRank, seriesCompare: seriesCompare, filterSeries: filterSeries,
    seriesOptions: seriesOptions, groupSeries: groupSeries, seriesTally: seriesTally,
    seriesCard: seriesCard, heroTable: heroTable, heroSplit: heroSplit, teamRecord: teamRecord,
    teamList: teamList, coverageMatrix: coverageMatrix, isLinked: isLinked, needsReview: needsReview };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root && root.OWCS) root.OWCS.extStats = api;
})(typeof window !== "undefined" ? window : null);
