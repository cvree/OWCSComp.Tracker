/* =====================================================================
   OWCS Comp Tracker — app/tierstats.js
   The maths behind tiers.html: team power ratings, the value of a win,
   and S–D hero / comp tiers, map by map, for any as-of date and range.

   Input is window.OWCS_SHEET_TIERS (pipeline/external_sheet/tiers.py):
   spreadsheet series with ESTIMATED dates, bracket rounds, map winners
   and order-weighted hero listings; and its `weights` block, a copy of
   config/tier_weights.json, where every constant below is explained.

     importance(map) = event weight x bracket weight
     power(team)     = Elo (seeded by region tier, moved by every map,
                       k x importance) + standings bonus (map-win share
                       in the team's current regional stage)
     expected(map)   = Elo expectation from both teams' power at the
                       moment the map was played (only results BEFORE it)
     value           = importance x (result - expected)

   Ratings always replay the whole history up to the as-of date, whatever
   the range or region filter, so a team's strength never depends on
   which slice is on screen. The range and region only choose which maps
   feed the hero and comp tables.

   Pure functions over plain objects: runs in the browser
   (window.OWCS.tierStats) and in Node for tests (module.exports).
   ===================================================================== */
(function (root) {
  "use strict";

  const HOME = ["na", "emea", "korea", "japan", "china", "pacific"];
  const DAY = 86400000;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  /* ------------------------------------------------------ importance */
  function bracketWeight(format, W) {
    const f = String(format || "").toLowerCase();
    let w = W.bracketDefault != null ? W.bracketDefault : 1;
    for (const pair of W.bracketWeight || []) {
      if (f.indexOf(pair[0]) >= 0) { w = pair[1]; break; }
    }
    if (/^group [a-z]\b/.test(f)) w *= (W.groupPrefixFactor || 1);
    return w;
  }

  function eventWeight(match, W) {
    const ev = W.eventWeight || {};
    if (ev[match.ev] != null) return ev[match.ev];
    if (ev[match.s] != null) return ev[match.s];
    return 1;
  }

  function importance(match, W) {
    return eventWeight(match, W) * bracketWeight(match.f, W);
  }

  /* --------------------------------------------------------- dates */
  const addDays = (iso, n) => new Date(Date.parse(iso + "T00:00:00Z") + n * DAY).toISOString().slice(0, 10);

  /* Maps in the order they were played: estimated date, then the sheet's
     own series order (already chronological within a stage), then map
     number. Cached on the dataset. */
  function ordered(T) {
    if (T._ordered) return T._ordered;
    const ms = T.matches || [];
    const list = (T.maps || []).filter((mp) => ms[mp.m] && ms[mp.m].d);
    list.sort((a, b) => {
      const da = ms[a.m].d, db = ms[b.m].d;
      return da < db ? -1 : da > db ? 1 : (a.m - b.m) || ((a.o || 0) - (b.o || 0));
    });
    Object.defineProperty(T, "_ordered", { value: list, enumerable: false });
    return list;
  }

  /* ---------------------------------------------------- power ratings */
  /* Replays every map up to `asOf`. Returns the per-map expectation (from
     ratings BEFORE that map), the end-of-day power table and, when `from`
     is given, each team's rank at the start of the range. */
  function sweep(T, asOf, from) {
    const W = T.weights || {};
    const teams = T.teams || {};
    const RT = W.regionTier || {};
    const K = (W.elo || {}).k || 32;
    const SCALE = (W.elo || {}).scale || 400;
    const ST = W.standings || {};
    const elo = new Map();
    const stand = new Map();
    const rec = new Map();
    const per = new Map();
    let before = null;

    const seed = (t) => {
      const r = (teams[t] || [])[1];
      return RT[r] != null ? RT[r] : (RT.default != null ? RT.default : 1500);
    };
    const E = (t) => { if (!elo.has(t)) elo.set(t, seed(t)); return elo.get(t); };
    const bonus = (t) => {
      const s = stand.get(t);
      if (!s) return 0;
      const pr = ST.priorMaps || 0;
      const n = s.w + s.l + pr;
      const pct = n ? (s.w + pr / 2) / n : 0.5;
      return (ST.blend || 0) * (ST.points || 0) * (pct - 0.5) * 2;
    };
    const power = (t) => E(t) + bonus(t);
    const table = () => {
      const rows = Array.from(rec.keys()).map((t) => ({
        team: t, power: power(t), elo: E(t), bonus: bonus(t), seed: seed(t),
        region: (teams[t] || [])[1] || null, w: rec.get(t).w, l: rec.get(t).l,
        last: rec.get(t).last, stage: stand.get(t) || null,
      }));
      rows.sort((a, b) => b.power - a.power || a.team.localeCompare(b.team));
      rows.forEach((r, i) => { r.rank = i + 1; });
      return rows;
    };

    for (const mp of ordered(T)) {
      const m = T.matches[mp.m];
      if (m.d > asOf) break;
      if (from && !before && m.d >= from) before = table();
      if (!m.a || !m.b) continue;
      const imp = importance(m, W);
      const pa = power(m.a), pb = power(m.b);
      const ea = 1 / (1 + Math.pow(10, (pb - pa) / SCALE));
      per.set(mp.id, { ea: ea, imp: imp, pa: pa, pb: pb });
      if (mp.w !== "a" && mp.w !== "b") continue;
      const ra = mp.w === "a" ? 1 : 0;
      const d = K * imp * (ra - ea);
      elo.set(m.a, E(m.a) + d);
      elo.set(m.b, E(m.b) - d);
      [[m.a, ra], [m.b, 1 - ra]].forEach((p) => {
        const r = rec.get(p[0]) || { w: 0, l: 0, last: null };
        if (p[1]) r.w += 1; else r.l += 1;
        r.last = m.d;
        rec.set(p[0], r);
        if (HOME.indexOf(m.r) >= 0) {
          const key = m.r + "/" + m.s;
          let s = stand.get(p[0]);
          if (!s || s.key !== key) { s = { key: key, w: 0, l: 0 }; stand.set(p[0], s); }
          if (p[1]) s.w += 1; else s.l += 1;
        }
      });
    }
    const now = table();
    const prevRank = new Map((before || now).map((r) => [r.team, r.rank]));
    now.forEach((r) => { r.prevRank = before ? (prevRank.get(r.team) || null) : r.rank; });
    return { per: per, table: now, rankOf: new Map(now.map((r) => [r.team, r])) };
  }

  /* ------------------------------------------------------- filtering */
  function inScope(m, f) {
    if (!m.d || m.d > f.asOf) return false;
    if (f.from && m.d < f.from) return false;
    if (f.region) {
      if (f.region === "international") return m.r === "international";
      if (m.r !== f.region) return false;
    }
    if (f.team && m.a !== f.team && m.b !== f.team) return false;
    return true;
  }

  /* ------------------------------------------------------ aggregation */
  const compKey = (heroes) => heroes.slice().sort().join("+");

  function newAcc(id) {
    return { id: id, picks: 0, apps: 0, n: 0, w: 0, val: 0, imp: 0, best: null, worst: null,
      teams: new Map(), opps: [] };
  }

  function addResult(acc, x, res, e, imp, ctx) {
    acc.n += x;
    acc.w += x * res;
    const v = x * imp * (res - e);
    acc.val += v;
    acc.imp += x * imp;
    if (res && (!acc.best || v > acc.best.v)) acc.best = Object.assign({ v: v }, ctx);
    if (!res && (!acc.worst || v < acc.worst.v)) acc.worst = Object.assign({ v: v }, ctx);
  }

  /* Every map in scope feeds its own bucket and the all-maps bucket "*". */
  function aggregate(T, f, per) {
    const buckets = new Map();
    const bucket = (k, mode) => {
      if (!buckets.has(k)) {
        buckets.set(k, { map: k, mode: mode, maps: 0, teamMaps: 0, decided: 0,
          heroes: new Map(), comps: new Map(), bans: new Map(), heroComps: new Map(), from: null, to: null });
      }
      return buckets.get(k);
    };
    for (const mp of T.maps || []) {
      const m = (T.matches || [])[mp.m];
      if (!m || !inScope(m, f)) continue;
      const r = per.get(mp.id);
      const keys = [mp.map, "*"];
      for (const key of keys) {
        const B = bucket(key, key === "*" ? null : mp.mode);
        B.maps += 1;
        if (mp.w) B.decided += 1;
        B.from = !B.from || m.d < B.from ? m.d : B.from;
        B.to = !B.to || m.d > B.to ? m.d : B.to;
        const banned = new Set((mp.a.bn || []).concat(mp.b.bn || []));
        banned.forEach((h) => B.bans.set(h, (B.bans.get(h) || 0) + 1));
        ["a", "b"].forEach((side) => {
          const sd = mp[side];
          const team = side === "a" ? m.a : m.b;
          const opp = side === "a" ? m.b : m.a;
          if (!sd || !(sd.h || []).length) return;
          B.teamMaps += 1;
          const res = mp.w ? (mp.w === side ? 1 : 0) : null;
          const e = r ? (side === "a" ? r.ea : 1 - r.ea) : null;
          const imp = r ? r.imp : importance(m, T.weights || {});
          const ctx = { mapId: mp.id, map: mp.map, match: mp.m, team: team, opp: opp, e: e, imp: imp };
          sd.h.forEach((pair) => {
            const h = pair[0], x = pair[1];
            let acc = B.heroes.get(h);
            if (!acc) { acc = newAcc(h); B.heroes.set(h, acc); }
            acc.picks += x;
            acc.apps += 1;
            acc.teams.set(team, (acc.teams.get(team) || 0) + x);
            if (res != null && e != null) addResult(acc, x, res, e, imp, ctx);
          });
          if (sd.c && sd.c.length) {
            const k = compKey(sd.c);
            let acc = B.comps.get(k);
            if (!acc) { acc = newAcc(k); acc.heroes = sd.c.slice(); B.comps.set(k, acc); }
            acc.picks += 1;
            acc.apps += 1;
            acc.teams.set(team, (acc.teams.get(team) || 0) + 1);
            if (res != null && e != null) addResult(acc, 1, res, e, imp, ctx);
            sd.c.forEach((h) => {
              let hc = B.heroComps.get(h);
              if (!hc) { hc = new Map(); B.heroComps.set(h, hc); }
              hc.set(k, (hc.get(k) || 0) + 1);
            });
          }
        });
      }
    }
    return buckets;
  }

  /* ------------------------------------------------------------ tiers */
  function finishRow(acc, B, W) {
    const shrink = W.shrink != null ? W.shrink : 6;
    return {
      id: acc.id, heroes: acc.heroes || null,
      picks: acc.picks, apps: acc.apps,
      pickShare: B.teamMaps ? acc.picks / B.teamMaps : 0,
      decided: acc.n,
      rawWin: acc.n ? acc.w / acc.n : null,
      value: acc.val,
      adjWin: 0.5 + acc.val / ((acc.imp || 0) + shrink),
      importance: acc.imp,
      best: acc.best, worst: acc.worst,
      banShare: B.maps ? (B.bans.get(acc.id) || 0) / B.maps : 0,
      teams: Array.from(acc.teams.entries()).sort((a, b) => b[1] - a[1]).slice(0, 5),
    };
  }

  function grade(rows, W, minPicks) {
    const pw = (W.pick || {}).weight != null ? W.pick.weight : 0.5;
    const ww = (W.win || {}).weight != null ? W.win.weight : 0.5;
    const span = (W.win || {}).span || 0.10;
    const bands = (W.tiers || []).slice().sort((a, b) => b.min - a.min);
    const eligible = rows.filter((r) => r.picks >= minPicks);
    const maxPick = eligible.reduce((m, r) => Math.max(m, r.pickShare), 0) || 1;
    rows.forEach((r) => {
      const winPart = (clamp((r.adjWin - 0.5) / span, -1, 1) + 1) / 2;
      r.score = 100 * (pw * Math.sqrt(Math.min(1, r.pickShare / maxPick)) + ww * winPart);
      r.lowSample = r.picks < minPicks;
      r.tier = r.lowSample ? null : ((bands.find((b) => r.score >= b.min) || bands[bands.length - 1] || {}).id || null);
    });
    rows.sort((a, b) => (b.lowSample ? -1 : 0) - (a.lowSample ? -1 : 0) || b.score - a.score || a.id.localeCompare(b.id));
    return rows;
  }

  /* Hero rows for one bucket, graded within each role. */
  function heroTiers(B, W, roles) {
    const byRole = new Map();
    B.heroes.forEach((acc) => {
      const row = finishRow(acc, B, W);
      row.role = (roles && roles[acc.id]) || "";
      const hc = B.heroComps.get(acc.id);
      row.comps = hc ? Array.from(hc.entries()).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 3)
        .map((p) => ({ key: p[0], heroes: (B.comps.get(p[0]) || {}).heroes || p[0].split("+"), n: p[1] })) : [];
      if (!byRole.has(row.role)) byRole.set(row.role, []);
      byRole.get(row.role).push(row);
    });
    const out = [];
    byRole.forEach((rows) => { grade(rows, W, Math.max(W.minPicks != null ? W.minPicks : 1.5, (W.minPickShare || 0) * B.teamMaps)).forEach((r) => out.push(r)); });
    return out;
  }

  function compTiers(B, W) {
    const rows = Array.from(B.comps.values()).map((acc) => finishRow(acc, B, W));
    return grade(rows, W, W.compMinMaps != null ? W.compMinMaps : 2);
  }

  /* ----------------------------------------------------------- driver */
  /* opts: { asOf, from | days, region, team }  roles: {heroId: role} */
  function compute(T, roles, opts) {
    opts = opts || {};
    const W = T.weights || {};
    const maxDate = (T.dates || {}).max || "9999-12-31";
    const asOf = opts.asOf && opts.asOf < maxDate ? opts.asOf : maxDate;
    const from = opts.from || (opts.days ? addDays(asOf, -(Number(opts.days) - 1)) : null);
    const R = sweep(T, asOf, from);
    const f = { asOf: asOf, from: from, region: opts.region || null, team: opts.team || null };
    const buckets = aggregate(T, f, R.per);
    return {
      asOf: asOf, from: from, region: f.region,
      power: R.table, rankOf: R.rankOf, per: R.per,
      maps: Array.from(buckets.values()).filter((b) => b.map !== "*")
        .sort((a, b) => b.maps - a.maps || a.map.localeCompare(b.map)),
      bucket: (key) => buckets.get(key || "*") || null,
      heroTiers: (key) => { const B = buckets.get(key || "*"); return B ? heroTiers(B, W, roles) : []; },
      compTiers: (key) => { const B = buckets.get(key || "*"); return B ? compTiers(B, W) : []; },
    };
  }

  const api = {
    HOME: HOME, bracketWeight: bracketWeight, eventWeight: eventWeight, importance: importance,
    addDays: addDays, ordered: ordered, sweep: sweep, inScope: inScope, aggregate: aggregate,
    heroTiers: heroTiers, compTiers: compTiers, grade: grade, compute: compute,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) { root.OWCS = root.OWCS || {}; root.OWCS.tierStats = api; }
})(typeof window !== "undefined" ? window : null);
