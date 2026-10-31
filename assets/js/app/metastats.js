/* =====================================================================
   OWCS Comp Tracker — app/metastats.js
   Every filterable meta number, computed from the two sources WITHOUT
   blending them.

   One list of maps is built from both datasets. A spreadsheet map that the
   pipeline's reconciliation linked to a VOD map is ONE map with two
   records, never two maps:

     entry = { vod: <timeWeighting map row | null>,
               sheet: <external map | null>,
               winner / pickedBy: the value both sources agree on, the sole
                 source's value, or null + a conflict flag when they differ,
               … context: date, event, stage, region, patch, map, mode }

   From there every number keeps its lane:

     time-weighted (seconds)  -> only entries with a VOD timeline, only
                                 KNOWN seconds; coverage is a filter
     appearance counts        -> only spreadsheet hero listings, labelled
                                 "spreadsheet observation", never seconds
     results / picks / bans   -> one per real map; a field the sources
                                 disagree on is EXCLUDED and counted as such

   Pure functions over plain objects: the same file runs in the browser
   (window.OWCS.metaStats) and in Node for tests (module.exports).
   ===================================================================== */
(function (root) {
  "use strict";

  const DAY = 86400000;

  /* --------------------------------------------------------- entries */
  function buildEntries(pub, ext) {
    pub = pub || {};
    ext = ext || {};
    const tw = new Map(((pub.timeWeighting || {}).maps || []).map((m) => [m.mapId, m]));
    const tours = new Map((pub.tournaments || []).map((t) => [t.id, t]));
    const recs = ((ext.reconciliation || {}).records) || [];
    const recByExt = new Map(recs.map((r) => [r.externalMapId, r]));
    const recByVod = new Map(recs.filter((r) => r.vodMapId).map((r) => [r.vodMapId, r]));
    const extMatches = new Map((ext.matches || []).map((m) => [m.id, m]));
    const extById = new Map((ext.maps || []).map((m) => [m.id, m]));
    const out = [];
    const agreed = (a, b) => (a == null ? { v: b, conflict: false }
      : b == null ? { v: a, conflict: false }
        : a === b ? { v: a, conflict: false } : { v: null, conflict: true });

    (pub.matches || []).forEach((match) => {
      const tour = tours.get(match.tournamentId) || {};
      (match.maps || []).forEach((mp) => {
        const rec = recByVod.get(mp.id);
        const sheet = rec ? extById.get(rec.externalMapId) || null : null;
        const sm = sheet ? extMatches.get(sheet.matchId) || {} : {};
        const w = agreed(mp.winner || null, sheet ? sheet.winner : null);
        const p = agreed(mp.pickedBy || null, sheet ? sheet.pickedBy : null);
        out.push({
          id: mp.id, vodId: mp.id, extId: sheet ? sheet.id : null,
          status: rec ? rec.status : "vod-only",
          date: (match.scheduledAt || "").slice(0, 10) || (sheet && sheet.date) || null,
          event: tour.name || sm.event || null,
          tournamentId: match.tournamentId || null,
          stage: ((tour.stages || [])[0] || {}).name || sm.stageRaw || null,
          region: tour.region && tour.region !== "all" ? tour.region : (sm.region || null),
          patch: sm.patch || null,
          map: mp.map, mode: mp.mode || (sheet && sheet.mode) || null,
          teamA: match.teamA, teamB: match.teamB,
          winner: w.v, winnerConflict: w.conflict,
          pickedBy: p.v, pickConflict: p.conflict,
          bans: sheet ? (sheet.bans || []) : [],
          vod: tw.get(mp.id) || null, sheet: sheet,
          vodUrl: match.streamUrl || (sheet && sheet.vodUrl) || null,
        });
      });
    });
    (ext.maps || []).forEach((m) => {
      const rec = recByExt.get(m.id);
      if (rec && (rec.vodMapId || rec.ignored)) return;   // already one entry / ignored
      const sm = extMatches.get(m.matchId) || {};
      out.push({
        id: m.id, vodId: null, extId: m.id, status: rec ? rec.status : "unmatched",
        date: m.date || sm.date || null, event: sm.event || null,
        tournamentId: sm.tournamentId || null, stage: sm.stageRaw || sm.stage || null,
        region: sm.region || null, patch: sm.patch || null, map: m.map, mode: m.mode,
        teamA: m.teamA, teamB: m.teamB, winner: m.winner || null, winnerConflict: false,
        pickedBy: m.pickedBy || null, pickConflict: false, bans: m.bans || [],
        vod: null, sheet: m, vodUrl: m.vodUrl || null,
      });
    });
    out.sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")) ||
      String(a.id).localeCompare(String(b.id)));
    return out;
  }

  /* --------------------------------------------------------- filters */
  function filterEntries(entries, f) {
    f = f || {};
    const has = (v) => v != null && v !== "" && v !== "all";
    return entries.filter((e) => {
      if (f.source === "vod" && !e.vod) return false;
      if (f.source === "sheet" && !e.sheet) return false;
      if (f.source === "both" && !(e.vod && e.sheet)) return false;
      if (has(f.event) && e.event !== f.event) return false;
      if (has(f.stage) && e.stage !== f.stage) return false;
      if (has(f.region) && e.region !== f.region) return false;
      if (has(f.patch) && e.patch !== f.patch) return false;
      if (has(f.map) && e.map !== f.map) return false;
      if (has(f.mode) && e.mode !== f.mode) return false;
      if (has(f.from) && (!e.date || e.date < f.from)) return false;
      if (has(f.to) && (!e.date || e.date > f.to)) return false;
      if (has(f.team) && e.teamA !== f.team && e.teamB !== f.team) return false;
      if (has(f.opponent)) {
        if (!has(f.team)) { if (e.teamA !== f.opponent && e.teamB !== f.opponent) return false; }
        else if (!((e.teamA === f.team && e.teamB === f.opponent) ||
                   (e.teamB === f.team && e.teamA === f.opponent))) return false;
      }
      if (f.minCoverage && e.vod && (e.vod.coverage || 0) < f.minCoverage) {
        if (f.source === "vod" || f.source === "both") return false;
      }
      return true;
    });
  }

  /* The team views a filter selects inside a map: both teams, or only the
     filtered team. */
  function sidesOf(e, f) {
    const sides = [{ side: "a", team: e.teamA, opp: e.teamB }, { side: "b", team: e.teamB, opp: e.teamA }];
    if (f && f.team && f.team !== "all") return sides.filter((s) => s.team === f.team);
    return sides;
  }

  /* Recency weight: relative to the newest date IN THE DATA (not the
     wall clock), so the same data always gives the same numbers. */
  function weigher(entries, halfLifeDays) {
    if (!halfLifeDays) return () => 1;
    const newest = entries.reduce((m, e) => (e.date && e.date > m ? e.date : m), "");
    const t0 = newest ? Date.parse(newest) : 0;
    return (e) => {
      if (!e.date || !t0) return 0.5;
      const age = Math.max(0, (t0 - Date.parse(e.date)) / DAY);
      return Math.pow(0.5, age / halfLifeDays);
    };
  }

  function windowed(entries, days) {
    if (!days) return entries;
    const newest = entries.reduce((m, e) => (e.date && e.date > m ? e.date : m), "");
    if (!newest) return entries;
    const cut = new Date(Date.parse(newest) - days * DAY).toISOString().slice(0, 10);
    return entries.filter((e) => e.date && e.date >= cut);
  }

  /* A VOD timeline below the requested coverage is left out of every
     time-weighted number (its map still counts for results). */
  const vodOk = (e, f) => !!e.vod && !(f && f.minCoverage && (e.vod.coverage || 0) < f.minCoverage);

  /* ------------------------------------------------------ hero usage */
  /* The export's per-map summary carries, per side, every hero the sheet
     lists (`heroes`) and its complete five-hero listing (`comp`). */
  function sheetHeroesFor(e, side) {
    const t = e.sheet && e.sheet.teams && e.sheet.teams[side];
    return t && t.heroes ? t.heroes.slice() : [];
  }

  function heroUsage(entries, f, roles, opt) {
    opt = opt || {};
    const w = weigher(entries, opt.halfLife);
    const vod = {}, sheet = {};
    let vodKnown = 0, vodTimelines = 0, sheetUnits = 0, sheetUnitsW = 0;
    const roleKnown = {};
    entries.forEach((e) => {
      const we = w(e);
      if (vodOk(e, f)) {
        sidesOf(e, f).forEach((s) => {
          const t = (e.vod.teams || []).filter((x) => x.side === s.side)[0];
          if (!t || !t.knownPlayerSeconds) return;
          vodTimelines += 1;
          vodKnown += t.knownPlayerSeconds * we;
          Object.keys(t.roleKnownSeconds || {}).forEach((r) => {
            roleKnown[r] = (roleKnown[r] || 0) + t.roleKnownSeconds[r] * we;
          });
          (t.heroes || []).forEach((h) => {
            const r = vod[h.hero] || (vod[h.hero] = { seconds: 0, rawSeconds: 0, timelines: 0, winSeconds: 0 });
            r.seconds += h.seconds * we;
            r.rawSeconds += h.seconds;
            r.timelines += 1;
            if (t.result === "win") r.winSeconds += h.seconds;
          });
        });
      }
      if (e.sheet) {
        sidesOf(e, f).forEach((s) => {
          const heroes = sheetHeroesFor(e, s.side);
          if (!heroes.length) return;
          sheetUnits += 1;
          sheetUnitsW += we;
          const won = e.winner ? e.winner === s.team : null;
          heroes.forEach((h) => {
            const r = sheet[h] || (sheet[h] = { count: 0, weighted: 0, wins: 0, decided: 0 });
            r.count += 1;
            r.weighted += we;
            if (won != null) { r.decided += 1; if (won) r.wins += 1; }
          });
        });
      }
    });
    const ids = new Set(Object.keys(vod).concat(Object.keys(sheet)));
    const rows = Array.from(ids).map((h) => {
      const v = vod[h], s = sheet[h];
      const role = (roles && roles[h]) || "";
      return {
        hero: h, role: role,
        vodSeconds: v ? Math.round(v.rawSeconds) : 0,
        vodShare: v && vodKnown ? v.seconds / vodKnown : null,
        vodRoleShare: v && roleKnown[role] ? v.seconds / roleKnown[role] : null,
        vodPresence: v && vodTimelines ? v.timelines / vodTimelines : null,
        vodWinnerShareOfOwnTime: v && v.rawSeconds ? v.winSeconds / v.rawSeconds : null,
        sheetCount: s ? s.count : 0,
        sheetRate: s && sheetUnitsW ? s.weighted / sheetUnitsW : null,
        sheetDecided: s ? s.decided : 0,
        sheetWinShare: s && s.decided ? s.wins / s.decided : null,
      };
    });
    rows.sort((a, b) => (b.vodSeconds - a.vodSeconds) || (b.sheetCount - a.sheetCount) ||
      a.hero.localeCompare(b.hero));
    return { rows: rows, vodTimelines: vodTimelines, vodKnownSeconds: Math.round(vodKnown),
      sheetUnits: sheetUnits };
  }

  /* ----------------------------------------------------------- comps */
  function vodLineups(entries, f) {
    const rows = {};
    entries.forEach((e) => {
      if (!vodOk(e, f)) return;
      sidesOf(e, f).forEach((s) => {
        const t = (e.vod.teams || []).filter((x) => x.side === s.side)[0];
        if (!t) return;
        (t.lineups || []).forEach((lu) => {
          const r = rows[lu.key] || (rows[lu.key] = { key: lu.key, heroes: lu.heroes,
            archetype: lu.archetype, seconds: 0, timelines: 0, winSeconds: 0, teams: {} });
          r.seconds += lu.seconds;
          r.timelines += 1;
          if (t.result === "win") r.winSeconds += lu.seconds;
          if (s.team) r.teams[s.team] = 1;
        });
      });
    });
    return Object.values(rows).map((r) => Object.assign(r, { teams: Object.keys(r.teams) }))
      .sort((a, b) => b.seconds - a.seconds || a.key.localeCompare(b.key));
  }

  function classify(heroes, roles, rules) {
    const tanks = heroes.filter((h) => roles[h] === "Tank");
    for (let i = 0; i < (rules || []).length; i++) {
      const r = rules[i];
      if (!tanks.some((t) => (r.tanks || []).indexOf(t) >= 0)) continue;
      if ((r.requiresAny || []).length && !heroes.some((h) => r.requiresAny.indexOf(h) >= 0)) continue;
      return r.id;
    }
    return "unclassified";
  }

  /* A spreadsheet "composition" is the five heroes it lists first for a
     team (or a comp cell it wrote) — a listed snapshot, never "the comp
     they ran for N seconds". Only complete five-hero listings count. */
  function sheetCompOf(e, side) {
    const t = e.sheet && e.sheet.teams && e.sheet.teams[side];
    return t && t.comp ? t.comp.slice().sort() : null;
  }

  function sheetComps(entries, f) {
    const rows = {};
    entries.forEach((e) => {
      if (!e.sheet) return;
      sidesOf(e, f).forEach((s) => {
        const heroes = sheetCompOf(e, s.side);
        if (!heroes) return;
        const key = heroes.join("+");
        const r = rows[key] || (rows[key] = { key: key, heroes: heroes, count: 0, wins: 0, decided: 0,
          teams: {}, maps: {} });
        r.count += 1;
        if (e.winner) { r.decided += 1; if (e.winner === s.team) r.wins += 1; }
        if (s.team) r.teams[s.team] = 1;
        if (e.map) r.maps[e.map] = (r.maps[e.map] || 0) + 1;
      });
    });
    return Object.values(rows).map((r) => Object.assign(r, { teams: Object.keys(r.teams) }))
      .sort((a, b) => b.count - a.count || a.key.localeCompare(b.key));
  }

  function coOccurrence(entries, f, limit) {
    const pairs = {};
    let units = 0;
    entries.forEach((e) => {
      if (!e.sheet) return;
      sidesOf(e, f).forEach((s) => {
        const hs = sheetHeroesFor(e, s.side).sort();
        if (hs.length < 2) return;
        units += 1;
        for (let i = 0; i < hs.length; i++) {
          for (let j = i + 1; j < hs.length; j++) {
            const k = hs[i] + "+" + hs[j];
            pairs[k] = (pairs[k] || 0) + 1;
          }
        }
      });
    });
    return { units: units, rows: Object.keys(pairs).map((k) => ({ pair: k.split("+"), count: pairs[k],
      rate: units ? pairs[k] / units : null }))
      .sort((a, b) => b.count - a.count || a.pair.join().localeCompare(b.pair.join()))
      .slice(0, limit || 20) };
  }

  /* ----------------------------------------------------------- bans */
  function bans(entries, f) {
    const hero = {}, byMap = {}, byTeam = {};
    let units = 0;
    entries.forEach((e) => {
      const list = (e.bans || []).filter((b) => b.hero);
      if (!list.length) return;
      units += 1;
      list.forEach((b) => {
        if (f && f.team && f.team !== "all" && b.teamId && b.teamId !== f.team) return;
        hero[b.hero] = (hero[b.hero] || 0) + 1;
        const m = byMap[e.map || "?"] || (byMap[e.map || "?"] = {});
        m[b.hero] = (m[b.hero] || 0) + 1;
        if (b.teamId) {
          const t = byTeam[b.teamId] || (byTeam[b.teamId] = {});
          t[b.hero] = (t[b.hero] || 0) + 1;
        }
      });
    });
    const top = (o) => Object.keys(o).map((k) => ({ hero: k, count: o[k] }))
      .sort((a, b) => b.count - a.count || a.hero.localeCompare(b.hero));
    return {
      units: units,
      heroes: top(hero).map((r) => Object.assign(r, { rate: units ? r.count / units : null })),
      byMap: Object.keys(byMap).sort().map((m) => ({ map: m, heroes: top(byMap[m]) })),
      byTeam: Object.keys(byTeam).sort().map((t) => ({ team: t, heroes: top(byTeam[t]) })),
    };
  }

  /* ------------------------------------------------------ map stats */
  function mapRecords(entries, f) {
    const teams = {};
    let excluded = 0;
    entries.forEach((e) => {
      if (e.winnerConflict) excluded += 1;
      sidesOf(e, f).forEach((s) => {
        if (!s.team || !e.map) return;
        const t = teams[s.team] || (teams[s.team] = {});
        const r = t[e.map] || (t[e.map] = { map: e.map, mode: e.mode, played: 0, wins: 0, losses: 0,
          undecided: 0, conflicts: 0, picks: 0, pickedAgainst: 0 });
        r.played += 1;
        if (e.winnerConflict) r.conflicts += 1;
        else if (!e.winner) r.undecided += 1;
        else if (e.winner === s.team) r.wins += 1;
        else r.losses += 1;
        if (e.pickedBy === s.team) r.picks += 1;
        else if (e.pickedBy && e.pickedBy === s.opp) r.pickedAgainst += 1;
      });
    });
    return {
      excludedConflicts: excluded,
      teams: Object.keys(teams).sort().map((id) => ({
        team: id,
        maps: Object.values(teams[id]).map((r) => Object.assign(r, {
          decided: r.wins + r.losses,
          winShare: r.wins + r.losses ? r.wins / (r.wins + r.losses) : null,
        })).sort((a, b) => b.played - a.played || a.map.localeCompare(b.map)),
      })),
    };
  }

  function mapPicks(entries) {
    const byMap = {};
    let known = 0;
    entries.forEach((e) => {
      if (!e.map) return;
      const r = byMap[e.map] || (byMap[e.map] = { map: e.map, mode: e.mode, played: 0, picked: 0,
        pickerWon: 0, pickerDecided: 0 });
      r.played += 1;
      if (e.pickedBy && !e.pickConflict) {
        known += 1;
        r.picked += 1;
        if (e.winner && !e.winnerConflict) {
          r.pickerDecided += 1;
          if (e.winner === e.pickedBy) r.pickerWon += 1;
        }
      }
    });
    return { known: known, rows: Object.values(byMap).sort((a, b) => b.played - a.played ||
      a.map.localeCompare(b.map)) };
  }

  /* ------------------------------------------------- matchup matrix */
  function dominantArchetype(t) {
    const a = (t && t.archetypes) || [];
    return a.length ? a[0].archetype : null;
  }

  function matchups(entries, roles, rules) {
    const cells = {};
    const add = (src, a, b, won) => {
      const k = src + "|" + a + "|" + b;
      const c = cells[k] || (cells[k] = { source: src, archetype: a, opponent: b, maps: 0, wins: 0, decided: 0 });
      c.maps += 1;
      if (won != null) { c.decided += 1; if (won) c.wins += 1; }
    };
    entries.forEach((e) => {
      if (e.vod) {
        const ts = e.vod.teams || [];
        if (ts.length === 2) {
          const a = dominantArchetype(ts[0]), b = dominantArchetype(ts[1]);
          if (a && b) {
            const won = e.winner && !e.winnerConflict ? e.winner === ts[0].teamId : null;
            add("vod", a, b, won);
            add("vod", b, a, won == null ? null : !won);
          }
        }
      }
      if (e.sheet) {
        const ca = sheetCompOf(e, "a"), cb = sheetCompOf(e, "b");
        if (ca && cb) {
          const a = classify(ca, roles, rules), b = classify(cb, roles, rules);
          const won = e.winner && !e.winnerConflict ? e.winner === e.teamA : null;
          add("sheet", a, b, won);
          add("sheet", b, a, won == null ? null : !won);
        }
      }
    });
    return Object.values(cells);
  }

  /* --------------------------------------------- team comp preferences */
  function teamMapComps(entries, team) {
    const maps = {};
    entries.forEach((e) => {
      const side = e.teamA === team ? "a" : e.teamB === team ? "b" : null;
      if (!side || !e.map) return;
      const m = maps[e.map] || (maps[e.map] = { map: e.map, sheet: {}, vod: {}, played: 0 });
      m.played += 1;
      const c = sheetCompOf(e, side);
      if (c) {
        const k = c.join("+");
        m.sheet[k] = m.sheet[k] || { heroes: c, count: 0 };
        m.sheet[k].count += 1;
      }
      const t = e.vod && (e.vod.teams || []).filter((x) => x.side === side)[0];
      (t ? t.lineups || [] : []).forEach((lu) => {
        m.vod[lu.key] = m.vod[lu.key] || { heroes: lu.heroes, seconds: 0 };
        m.vod[lu.key].seconds += lu.seconds;
      });
    });
    return Object.values(maps).map((m) => ({
      map: m.map, played: m.played,
      sheet: Object.values(m.sheet).sort((a, b) => b.count - a.count),
      vod: Object.values(m.vod).sort((a, b) => b.seconds - a.seconds),
    })).sort((a, b) => b.played - a.played || a.map.localeCompare(b.map));
  }

  /* ------------------------------------------------- scouting report */
  function scouting(entries, team, n) {
    const mine = entries.filter((e) => e.teamA === team || e.teamB === team);
    const recent = mine.slice(0, n || 10);
    const sideOf = (e) => (e.teamA === team ? "a" : "b");
    const comfort = {};
    const bansFor = {}, bansAgainst = {};
    const heroes = {};
    const comps = {};
    let vodSeconds = 0;
    recent.forEach((e) => {
      const side = sideOf(e);
      const opp = side === "a" ? e.teamB : e.teamA;
      if (e.map) {
        const c = comfort[e.map] || (comfort[e.map] = { map: e.map, played: 0, wins: 0, decided: 0, picks: 0 });
        c.played += 1;
        if (e.winner && !e.winnerConflict) { c.decided += 1; if (e.winner === team) c.wins += 1; }
        if (e.pickedBy === team) c.picks += 1;
      }
      (e.bans || []).forEach((b) => {
        if (!b.hero) return;
        if (b.teamId === team) bansFor[b.hero] = (bansFor[b.hero] || 0) + 1;
        else if (b.teamId === opp) bansAgainst[b.hero] = (bansAgainst[b.hero] || 0) + 1;
      });
      sheetHeroesFor(e, side).forEach((h) => {
        const r = heroes[h] || (heroes[h] = { hero: h, sheetMaps: 0, vodSeconds: 0 });
        r.sheetMaps += 1;
      });
      const t = e.vod && (e.vod.teams || []).filter((x) => x.side === side)[0];
      if (t) {
        vodSeconds += t.knownPlayerSeconds || 0;
        (t.heroes || []).forEach((h) => {
          const r = heroes[h.hero] || (heroes[h.hero] = { hero: h.hero, sheetMaps: 0, vodSeconds: 0 });
          r.vodSeconds += h.seconds;
        });
        (t.lineups || []).forEach((lu) => {
          const c = comps[lu.key] || (comps[lu.key] = { heroes: lu.heroes, sheet: 0, vodSeconds: 0 });
          c.vodSeconds += lu.seconds;
        });
      }
      const sc = sheetCompOf(e, side);
      if (sc) {
        const k = sc.join("+");
        const c = comps[k] || (comps[k] = { heroes: sc, sheet: 0, vodSeconds: 0 });
        c.sheet += 1;
      }
    });
    const top = (o) => Object.keys(o).map((k) => ({ hero: k, count: o[k] }))
      .sort((a, b) => b.count - a.count || a.hero.localeCompare(b.hero));
    return {
      team: team, available: mine.length, window: recent.length,
      from: recent.length ? recent[recent.length - 1].date : null,
      to: recent.length ? recent[0].date : null,
      maps: recent,
      sources: { vod: recent.filter((e) => e.vod).length, sheet: recent.filter((e) => e.sheet).length },
      comfort: Object.values(comfort).sort((a, b) => b.played - a.played || a.map.localeCompare(b.map)),
      bansFor: top(bansFor), bansAgainst: top(bansAgainst),
      heroes: Object.values(heroes).sort((a, b) => b.vodSeconds - a.vodSeconds || b.sheetMaps - a.sheetMaps ||
        a.hero.localeCompare(b.hero)),
      comps: Object.values(comps).sort((a, b) => b.vodSeconds - a.vodSeconds || b.sheet - a.sheet),
      vodKnownSeconds: vodSeconds,
    };
  }

  function options(entries) {
    const uniq = (fn) => Array.from(new Set(entries.map(fn).filter((v) => v != null && v !== "")))
      .sort();
    return {
      events: uniq((e) => e.event), stages: uniq((e) => e.stage), regions: uniq((e) => e.region),
      patches: uniq((e) => e.patch), maps: uniq((e) => e.map), modes: uniq((e) => e.mode),
      teams: uniq((e) => e.teamA).concat(uniq((e) => e.teamB))
        .filter((v, i, a) => a.indexOf(v) === i).sort(),
      minDate: entries.reduce((m, e) => (e.date && (!m || e.date < m) ? e.date : m), null),
      maxDate: entries.reduce((m, e) => (e.date && (!m || e.date > m) ? e.date : m), null),
    };
  }

  const api = { buildEntries: buildEntries, filterEntries: filterEntries, heroUsage: heroUsage,
    vodLineups: vodLineups, sheetComps: sheetComps, coOccurrence: coOccurrence, bans: bans,
    mapRecords: mapRecords, mapPicks: mapPicks, matchups: matchups, teamMapComps: teamMapComps,
    scouting: scouting, options: options, windowed: windowed, weigher: weigher,
    classify: classify, sheetCompOf: sheetCompOf, sheetHeroesFor: sheetHeroesFor };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root && root.OWCS) {
    root.OWCS.metaStats = api;
    let cached = null;
    root.OWCS.metaEntries = () => cached || (cached = buildEntries(root.OWCS_PUBLIC, root.OWCS_EXTERNAL));
  }
})(typeof window !== "undefined" ? window : null);
