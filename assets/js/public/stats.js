/* =====================================================================
   OWCS Comp Tracker — public/stats.js
   Stat computation for the public site. THE RULE: every pick/win number
   is computed ONLY from comp snapshots whose review status is
   "reviewed" or "auto-high" (via OWCS_PUB.publicComps, which also
   enforces cv/manual-only sources and manual-overrides-cv). Ban rates
   come from heroBans — those are match facts (FACEIT/manual), clearly
   labeled as such, and never treated as comps.
   Every stat row carries an `evidence` list of {matchId, mapId,
   snapshotIds} so the UI can link straight to the receipts.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS_PUB || {};
  const D = P.data;
  const S = (window.OWCS_STATS = window.OWCS_STATS || {});

  /* Region of a comp = region of the tournament its match belongs to. */
  function matchRegion(matchId) {
    const m = P.match(matchId);
    if (!m) return null;
    const t = P.tournament(m.tournamentId);
    return t ? t.region : null;
  }

  function passFilters(c, f) {
    if (!f) return true;
    if (f.region && f.region !== "all" && matchRegion(c.matchId) !== f.region) return false;
    if (f.teamId && f.teamId !== "all" && c.teamId !== f.teamId) return false;
    if (f.tournamentId && f.tournamentId !== "all") {
      const m = P.match(c.matchId);
      if (!m || m.tournamentId !== f.tournamentId) return false;
    }
    if (f.mapId && f.mapId !== "all") {
      const m = P.match(c.matchId);
      const mapRow = m && (m.maps || []).find((x) => x.id === c.mapId);
      if (!mapRow || mapRow.map !== f.mapId) return false;
    }
    return true;
  }

  /* Did the comp's team win the map the snapshot belongs to? */
  function mapResultFor(c) {
    const m = P.match(c.matchId);
    if (!m) return null;
    const mapRow = (m.maps || []).find((x) => x.id === c.mapId);
    if (!mapRow || !mapRow.winner) return null; // live / unscored maps count picks, not wins
    return mapRow.winner === c.teamId ? "win" : "loss";
  }

  /* Hero pick + win rates from verified comps.
     Unit of counting: one (map, team) appearance — multiple snapshots of
     the same hero on the same map/team collapse into one appearance, so
     long maps don't multiply-count. */
  S.computeHeroStats = function (filters) {
    const comps = (P.publicComps ? P.publicComps() : []).filter((c) => passFilters(c, filters));
    // key: mapId|teamId -> {heroes:Set, result, matchId, mapId, snapshotIds}
    const appearances = new Map();
    comps.forEach((c) => {
      const key = c.mapId + "|" + c.teamId;
      let a = appearances.get(key);
      if (!a) {
        a = { heroes: new Map(), result: mapResultFor(c), matchId: c.matchId, mapId: c.mapId, teamId: c.teamId };
        appearances.set(key, a);
      }
      (c.heroes || []).forEach((h) => {
        if (!a.heroes.has(h)) a.heroes.set(h, []);
        a.heroes.get(h).push(c.id);
      });
    });
    const totalAppearances = appearances.size;
    const rows = new Map(); // heroId -> row
    appearances.forEach((a) => {
      a.heroes.forEach((snapIds, heroId) => {
        let r = rows.get(heroId);
        if (!r) {
          const h = P.hero(heroId);
          r = { heroId, name: h.name, role: h.role, picks: 0, wins: 0, losses: 0, evidence: [] };
          rows.set(heroId, r);
        }
        r.picks += 1;
        if (a.result === "win") r.wins += 1;
        else if (a.result === "loss") r.losses += 1;
        r.evidence.push({ matchId: a.matchId, mapId: a.mapId, teamId: a.teamId, result: a.result, snapshotIds: snapIds });
      });
    });
    const out = Array.from(rows.values()).map((r) => {
      const decided = r.wins + r.losses;
      return Object.assign(r, {
        pickRate: totalAppearances ? r.picks / totalAppearances : 0,
        winRate: decided ? r.wins / decided : null, // null = no decided maps yet
      });
    });
    out.sort((a, b) => b.picks - a.picks || a.name.localeCompare(b.name));
    return { rows: out, totalAppearances, compCount: comps.length };
  };

  /* Per-team breakdown for ONE hero — the drill-down behind a stat row.
     Derived from the same verified appearances as computeHeroStats, so
     the credibility rules apply unchanged. */
  S.heroDetail = function (heroId, filters) {
    const hs = S.computeHeroStats(filters);
    const row = hs.rows.find((r) => r.heroId === heroId);
    if (!row) return { heroId, teams: [], picks: 0 };
    const teams = new Map();
    row.evidence.forEach((e) => {
      let t = teams.get(e.teamId);
      if (!t) {
        t = { teamId: e.teamId, picks: 0, wins: 0, losses: 0, evidence: [] };
        teams.set(e.teamId, t);
      }
      t.picks += 1;
      if (e.result === "win") t.wins += 1;
      else if (e.result === "loss") t.losses += 1;
      t.evidence.push(e);
    });
    const out = Array.from(teams.values())
      .sort((a, b) => b.picks - a.picks || String(a.teamId).localeCompare(String(b.teamId)));
    return { heroId, teams: out, picks: row.picks, row };
  };

  /* Ban counts — labeled match facts, never comps. */
  S.computeBanStats = function (filters) {
    const bans = (D && D.heroBans ? D.heroBans : []).filter((b) => {
      if (!filters) return true;
      if (filters.region && filters.region !== "all" && matchRegion(b.matchId) !== filters.region) return false;
      if (filters.tournamentId && filters.tournamentId !== "all") {
        const m = P.match(b.matchId);
        if (!m || m.tournamentId !== filters.tournamentId) return false;
      }
      return true;
    });
    const rows = new Map();
    bans.forEach((b) => {
      let r = rows.get(b.hero);
      if (!r) {
        const h = P.hero(b.hero);
        r = { heroId: b.hero, name: h.name, role: h.role, bans: 0, source: b.source, evidence: [] };
        rows.set(b.hero, r);
      }
      r.bans += 1;
      r.evidence.push({ matchId: b.matchId, mapId: b.mapId, banId: b.id });
    });
    const out = Array.from(rows.values()).sort((a, b) => b.bans - a.bans || a.name.localeCompare(b.name));
    return { rows: out, banCount: bans.length };
  };

  /* Confirmed swap activity per hero — swapped INTO a slot vs OUT of one.
     Rejected swap candidates are deliberately excluded: they are recorded
     evidence that something did NOT happen (see swaps.html), and counting
     them here would turn the rejection ledger into a stat. */
  S.computeSwapStats = function (filters) {
    const swaps = (D && D.heroSwaps ? D.heroSwaps : []).filter((s) => {
      if (s.status !== "confirmed") return false;
      if (!filters) return true;
      if (filters.region && filters.region !== "all"
        && matchRegion(s.matchId) !== filters.region) return false;
      if (filters.teamId && filters.teamId !== "all"
        && s.teamId !== filters.teamId) return false;
      if (filters.tournamentId && filters.tournamentId !== "all") {
        const m = P.match(s.matchId);
        if (!m || m.tournamentId !== filters.tournamentId) return false;
      }
      if (filters.mapId && filters.mapId !== "all") {
        const m = P.match(s.matchId);
        const row = m && (m.maps || []).find((x) => x.id === s.mapId);
        if (!row || row.map !== filters.mapId) return false;
      }
      return true;
    });
    const rows = new Map();
    const touch = (heroId) => {
      if (!rows.has(heroId)) rows.set(heroId, { heroId, in: 0, out: 0, evidence: [] });
      return rows.get(heroId);
    };
    swaps.forEach((s) => {
      if (s.toHero) { const r = touch(s.toHero); r.in += 1; r.evidence.push(s); }
      if (s.fromHero) { const r = touch(s.fromHero); r.out += 1; r.evidence.push(s); }
    });
    return { rows, swapCount: swaps.length };
  };

  /* ---- honest ranking ------------------------------------------------
     A raw win rate over two decided maps is not a measurement, it is an
     accident: "100%" from 1–0 must never outrank 8–4. The Wilson score
     interval's LOWER bound is the standard fix — it asks "what win rate is
     this sample actually evidence for", so a small sample is pulled toward
     the middle and a large one is trusted. It is what makes a ranked hero
     board possible here at all without inventing a tier list: rank by what
     the evidence supports, and show the sample size next to it so the
     reader can see exactly how much that is.

     z = 1.96 (95%). Returns null when there is nothing decided to score. */
  S.wilsonLower = function (wins, total, z) {
    const n = Number(total) || 0;
    if (n <= 0) return null;
    const zz = z == null ? 1.96 : z;
    const p = wins / n;
    const d = 1 + (zz * zz) / n;
    const centre = p + (zz * zz) / (2 * n);
    const margin = zz * Math.sqrt((p * (1 - p) + (zz * zz) / (4 * n)) / n);
    return Math.max(0, (centre - margin) / d);
  };

  /* How much a rate on this many appearances is worth saying out loud.
     The thresholds are deliberately blunt and stated in the UI rather than
     tuned: with a dataset this small the honest message is "this is a
     handful of maps", not a false precision. */
  S.SAMPLE_FLOOR = 3;          // below this, a rate is shown but never ranked
  S.SAMPLE_SOLID = 10;

  S.sampleGrade = function (n) {
    if (!n) return "none";
    if (n < S.SAMPLE_FLOOR) return "thin";
    if (n < S.SAMPLE_SOLID) return "some";
    return "solid";
  };

  /* ONE row per hero in the pool — including heroes with no verified pick.
     Every owtics-style board shows the whole roster; showing only the
     heroes that happen to have data hides the actual shape of the coverage,
     which for this project is the most important thing on the page. Rows
     for unseen heroes carry explicit nulls, never zeros: 0% pick rate is a
     claim, "not sighted" is the truth. */
  S.heroBoard = function (filters) {
    const hs = S.computeHeroStats(filters);
    const bs = S.computeBanStats(filters);
    const sw = S.computeSwapStats(filters);
    const statBy = new Map(hs.rows.map((r) => [r.heroId, r]));
    const banBy = new Map(bs.rows.map((r) => [r.heroId, r]));
    const rows = (D && D.heroes ? D.heroes : []).map((h) => {
      const r = statBy.get(h.id);
      const ban = banBy.get(h.id);
      const s = sw.rows.get(h.id);
      const decided = r ? r.wins + r.losses : 0;
      return {
        heroId: h.id, name: h.name, role: h.role,
        seen: !!r,
        picks: r ? r.picks : 0,
        pickRate: r ? r.pickRate : null,
        winRate: r && decided ? r.wins / decided : null,
        wins: r ? r.wins : 0,
        losses: r ? r.losses : 0,
        decided,
        confidence: r && decided ? S.wilsonLower(r.wins, decided) : null,
        grade: S.sampleGrade(r ? r.picks : 0),
        swapsIn: s ? s.in : 0,
        swapsOut: s ? s.out : 0,
        bans: ban ? ban.bans : 0,
        banSource: ban ? ban.source : null,
        evidence: r ? r.evidence : [],
      };
    });
    return {
      rows,
      totalAppearances: hs.totalAppearances,
      seenCount: hs.rows.length,
      poolCount: rows.length,
      compCount: hs.compCount,
      swapCount: sw.swapCount,
      banCount: bs.banCount,
    };
  };

  /* Which maps a hero was actually picked on, most-played first — the
     honest, evidence-backed version of an owtics "hero × map" table. */
  S.heroMaps = function (heroId, filters) {
    const hs = S.computeHeroStats(filters);
    const row = hs.rows.find((r) => r.heroId === heroId);
    if (!row) return [];
    const byMap = new Map();
    row.evidence.forEach((e) => {
      const m = P.match(e.matchId);
      const mapRow = m && (m.maps || []).find((x) => x.id === e.mapId);
      const key = (mapRow && mapRow.map) || "unknown";
      let g = byMap.get(key);
      if (!g) {
        const cat = (D.mapsCatalog || []).find((x) => x.id === key);
        g = { mapId: key, name: (cat && cat.name) || key,
              mode: cat && cat.mode, picks: 0, wins: 0, losses: 0,
              evidence: [] };
        byMap.set(key, g);
      }
      g.picks += 1;
      if (e.result === "win") g.wins += 1;
      else if (e.result === "loss") g.losses += 1;
      g.evidence.push(e);
    });
    return Array.from(byMap.values())
      .sort((a, b) => b.picks - a.picks || a.name.localeCompare(b.name));
  };

  /* Headline numbers for the stat cards. */
  S.summary = function (filters) {
    const hs = S.computeHeroStats(filters);
    const verifiedMaps = new Set();
    hs.rows.forEach((r) => r.evidence.forEach((e) => verifiedMaps.add(e.mapId)));
    const matches = new Set();
    hs.rows.forEach((r) => r.evidence.forEach((e) => matches.add(e.matchId)));
    return {
      comps: hs.compCount,
      teamMapAppearances: hs.totalAppearances,
      verifiedMaps: verifiedMaps.size,
      matches: matches.size,
      heroesSeen: hs.rows.length,
    };
  };
})();
