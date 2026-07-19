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
        a = { heroes: new Map(), result: mapResultFor(c), matchId: c.matchId, mapId: c.mapId };
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
        r.evidence.push({ matchId: a.matchId, mapId: a.mapId, snapshotIds: snapIds });
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
