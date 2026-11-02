/* =====================================================================
   OWCS Comp Tracker — app/compbook.js
   The comp book: every five-hero comp the community spreadsheet records,
   with everything worth knowing about it, for any filter.

   Input is window.OWCS_SHEET_TIERS (pipeline/external_sheet/tiers.py):
   per map, each side's `c` (the five heroes its players are listed with
   first, when all five are known), `h` (every listed hero, order-
   weighted), `bn` (its ban), the winner, and the series it belongs to
   with an ESTIMATED date. A comp here is a listed line-up — the sheet
   records appearances, never seconds.

   For each comp:
     n, share            team-maps it was run on, and share of all
                         team-maps with a full line-up in scope
     wins, losses, und   decided results, and maps with no winner
     rawWin, adjWin      plain win rate, and the tiers page's weighted
                         one (event x bracket x opponent power, pulled
                         toward 50% on small samples) — the same maths,
                         via app/tierstats.js, so the two pages agree
     tier, score         S–D, graded across the comps in scope
     maps, modes         where it is run, each with its own W–L
     teams, regions      who runs it, each team with its own W–L
     first, last, recent first / last seen, and maps in the last 30 days
     vs, mirrors         the opponent comps it met, with W–L; and the
                         maps where both teams ran it
     offMirror           W–L against every OTHER comp. A mirror is one
                         win and one loss for the same comp, so a comp
                         that mostly meets itself sits near 50% whatever
                         it is worth; this is the record that says
     bansFaced, bansOwn  what the other team banned against it, and
                         what its own team banned
     swaps               heroes its players are also listed with
                         (2nd / 3rd), so the swaps the line-up made
     best, worst         its most valuable win and costliest loss
     games               every appearance, newest first

   Pure functions over plain objects: runs in the browser
   (window.OWCS.compBook) and in Node for tests (module.exports).
   ===================================================================== */
(function (root) {
  "use strict";

  const S = (root && root.OWCS && root.OWCS.tierStats) ||
    (typeof require === "function" ? require("./tierstats.js") : null);

  const key = (heroes) => heroes.slice().sort().join("+");
  const bump = (map, k, by) => map.set(k, (map.get(k) || 0) + (by == null ? 1 : by));
  const rec = (map, k, res, extra) => {
    let r = map.get(k);
    if (!r) { r = Object.assign({ n: 0, w: 0, l: 0 }, extra || {}); map.set(k, r); }
    r.n += 1;
    if (res === 1) r.w += 1; else if (res === 0) r.l += 1;
    return r;
  };
  const sorted = (map, top) => Array.from(map.entries()).map((e) => Object.assign({ id: e[0] }, e[1]))
    .sort((a, b) => b.n - a.n || (b.w - b.l) - (a.w - a.l) || String(a.id).localeCompare(String(b.id)))
    .slice(0, top || 1e9);
  const counts = (map, top) => Array.from(map.entries()).sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])))
    .slice(0, top || 1e9);

  /* Ratings replay the whole history up to the as-of date (tierstats'
     rule), so they do not depend on the filter. Cached per window. */
  function expectations(T, asOf, from) {
    if (!T._cbSweep) Object.defineProperty(T, "_cbSweep", { value: new Map(), enumerable: false });
    const k = asOf + "|" + (from || "");
    if (!T._cbSweep.has(k)) T._cbSweep.set(k, S.sweep(T, asOf, from).per);
    return T._cbSweep.get(k);
  }

  /* opts: { asOf, from | days, region, stage, map, mode, team, hero,
             minMaps, sort: "played" | "win" | "tier" | "recent" } */
  function build(T, opts) {
    opts = opts || {};
    const W = T.weights || {};
    const shrink = W.shrink != null ? W.shrink : 6;
    const maxDate = (T.dates || {}).max || "9999-12-31";
    const asOf = opts.asOf && opts.asOf < maxDate ? opts.asOf : maxDate;
    const from = opts.from || (opts.days ? S.addDays(asOf, -(Number(opts.days) - 1)) : null);
    const recentFrom = S.addDays(asOf, -29);
    const per = expectations(T, asOf, from);
    const heroes = [].concat(opts.hero || []).filter(Boolean);
    const scope = { asOf: asOf, from: from, region: opts.region || null, team: null };

    const comps = new Map();
    const out = { asOf: asOf, from: from, maps: 0, teamMaps: 0, listedSides: 0, first: null, last: null };

    for (const mp of T.maps || []) {
      const m = (T.matches || [])[mp.m];
      if (!m || !S.inScope(m, scope)) continue;
      if (opts.stage && m.s !== opts.stage) continue;
      if (opts.map && mp.map !== opts.map) continue;
      if (opts.mode && mp.mode !== opts.mode) continue;
      if (opts.team && m.a !== opts.team && m.b !== opts.team) continue;
      out.maps += 1;
      out.first = !out.first || m.d < out.first ? m.d : out.first;
      out.last = !out.last || m.d > out.last ? m.d : out.last;
      const r = per.get(mp.id);
      const imp = r ? r.imp : S.importance(m, W);

      ["a", "b"].forEach((side) => {
        const sd = mp[side];
        const os = side === "a" ? "b" : "a";
        const od = mp[os] || {};
        const team = m[side], opp = m[os];
        if (!sd || !(sd.h || []).length) return;
        out.listedSides += 1;
        if (!sd.c || sd.c.length !== 5) return;
        if (opts.team && team !== opts.team) return;
        out.teamMaps += 1;
        if (heroes.length && !heroes.every((h) => sd.c.indexOf(h) >= 0)) return;

        const k = key(sd.c);
        let acc = comps.get(k);
        if (!acc) {
          acc = { key: k, heroes: sd.c.slice(), n: 0, wins: 0, losses: 0, und: 0, val: 0, imp: 0,
            maps: new Map(), modes: new Map(), teams: new Map(), regions: new Map(), vs: new Map(),
            mirrors: 0, ow: 0, ol: 0, bansFaced: new Map(), bansOwn: new Map(), swaps: new Map(),
            first: null, last: null, recent: 0, best: null, worst: null, games: [] };
          comps.set(k, acc);
        }
        const res = mp.w === side ? 1 : mp.w === os ? 0 : null;
        const e = r ? (side === "a" ? r.ea : 1 - r.ea) : null;
        acc.n += 1;
        if (res === 1) acc.wins += 1; else if (res === 0) acc.losses += 1; else acc.und += 1;
        let v = null;
        if (res != null && e != null) {
          v = imp * (res - e);
          acc.val += v;
          acc.imp += imp;
        }
        acc.first = !acc.first || m.d < acc.first ? m.d : acc.first;
        acc.last = !acc.last || m.d > acc.last ? m.d : acc.last;
        if (m.d >= recentFrom) acc.recent += 1;
        rec(acc.maps, mp.map, res, { mode: mp.mode || null });
        rec(acc.modes, mp.mode || "?", res);
        rec(acc.teams, team, res);
        bump(acc.regions, m.r || "pre-season");
        const oc = od.c && od.c.length === 5 ? od.c : null;
        if (oc) {
          const ok = key(oc);
          /* A mirror map is visited once per side; count the map once
             (on side a, or on the one side a team filter keeps). */
          if (ok === k) { if (side === "a" || opts.team) acc.mirrors += 1; }
          else rec(acc.vs, ok, res, { heroes: oc.slice() });
        }
        if (!oc || key(oc) !== k) { if (res === 1) acc.ow += 1; else if (res === 0) acc.ol += 1; }
        (od.bn || []).forEach((h) => bump(acc.bansFaced, h));
        (sd.bn || []).forEach((h) => bump(acc.bansOwn, h));
        (sd.h || []).forEach((pair) => { if (sd.c.indexOf(pair[0]) < 0) bump(acc.swaps, pair[0], pair[1]); });
        const game = { mapId: mp.id, map: mp.map, mode: mp.mode || null, match: mp.m, series: m.id, d: m.d,
          dc: m.dc || null, team: team, opp: opp, res: res, e: e, imp: imp, v: v, oppComp: oc };
        acc.games.push(game);
        if (res === 1 && v != null && (!acc.best || v > acc.best.v)) acc.best = game;
        if (res === 0 && v != null && (!acc.worst || v < acc.worst.v)) acc.worst = game;
      });
    }

    const rows = Array.from(comps.values()).map((acc) => {
      const decided = acc.wins + acc.losses;
      return {
        id: acc.key, key: acc.key, heroes: acc.heroes,
        n: acc.n, picks: acc.n, apps: acc.n,
        share: out.teamMaps ? acc.n / out.teamMaps : 0,
        pickShare: out.teamMaps ? acc.n / out.teamMaps : 0,
        wins: acc.wins, losses: acc.losses, und: acc.und, decided: decided,
        rawWin: decided ? acc.wins / decided : null,
        adjWin: 0.5 + acc.val / ((acc.imp || 0) + shrink),
        value: acc.val,
        first: acc.first, last: acc.last, recent: acc.recent,
        maps: sorted(acc.maps), modes: sorted(acc.modes), teams: sorted(acc.teams),
        regions: counts(acc.regions),
        vs: sorted(acc.vs, 8), mirrors: acc.mirrors,
        offMirror: { w: acc.ow, l: acc.ol, rate: acc.ow + acc.ol ? acc.ow / (acc.ow + acc.ol) : null },
        bansFaced: counts(acc.bansFaced, 6), bansOwn: counts(acc.bansOwn, 6),
        swaps: counts(acc.swaps, 6).map((p) => [p[0], Math.round(p[1] * 10) / 10]),
        best: acc.best, worst: acc.worst,
        games: acc.games.sort((a, b) => (b.d < a.d ? -1 : b.d > a.d ? 1 : 0) || (b.match - a.match)),
      };
    });

    /* Tiers are cut across every comp in scope, not only the ones shown,
       so raising the minimum hides rows without regrading the rest. */
    const tierMin = W.compMinMaps != null ? W.compMinMaps : 2;
    S.grade(rows, W, tierMin);

    const minMaps = Math.max(1, Number(opts.minMaps || 1));
    const shown = rows.filter((r) => r.n >= minMaps);
    const by = {
      played: (a, b) => b.n - a.n || (b.adjWin - a.adjWin),
      win: (a, b) => b.adjWin - a.adjWin || b.n - a.n,
      tier: (a, b) => (b.score || 0) - (a.score || 0) || b.n - a.n,
      recent: (a, b) => (b.last < a.last ? -1 : b.last > a.last ? 1 : 0) || b.n - a.n,
    }[opts.sort || "played"] || ((a, b) => b.n - a.n);
    shown.sort((a, b) => by(a, b) || a.key.localeCompare(b.key));

    out.distinct = rows.length;
    out.repeated = rows.filter((r) => r.n >= 2).length;
    out.coveredMaps = rows.reduce((s, r) => s + r.n, 0);
    out.comps = shown;
    out.all = rows;
    return out;
  }

  /* The options a filter bar can offer: what the data actually holds. */
  function facets(T) {
    if (T._cbFacets) return T._cbFacets;
    const maps = new Map(), modes = new Set(), teams = new Map(), heroes = new Set(), stages = new Set(), regions = new Set();
    for (const mp of T.maps || []) {
      const m = (T.matches || [])[mp.m];
      if (!m) continue;
      maps.set(mp.map, mp.mode || maps.get(mp.map) || null);
      if (mp.mode) modes.add(mp.mode);
      if (m.s) stages.add(m.s);
      if (m.r) regions.add(m.r);
      ["a", "b"].forEach((s) => {
        const sd = mp[s];
        if (sd && sd.c && sd.c.length === 5) {
          teams.set(m[s], (teams.get(m[s]) || 0) + 1);
          sd.c.forEach((h) => heroes.add(h));
        }
      });
    }
    const f = { maps: maps, modes: Array.from(modes), teams: teams, heroes: Array.from(heroes),
      stages: Array.from(stages), regions: Array.from(regions) };
    Object.defineProperty(T, "_cbFacets", { value: f, enumerable: false });
    return f;
  }

  const api = { build: build, facets: facets, key: key };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) { root.OWCS = root.OWCS || {}; root.OWCS.compBook = api; }
})(typeof window !== "undefined" ? window : null);
