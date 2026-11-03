/* =====================================================================
   OWCS Comp Tracker — app/lab.js
   The maths behind the tools: the Draft Lab, Versus, and a hero's best
   teammates and hardest opponents.

   Input is window.OWCS_SHEET_TIERS (the community spreadsheet, with
   ESTIMATED dates) and app/tierstats.js for team power. Every function
   takes the same scope as the rest of the site — { asOf, from, region,
   map, mode } — so a tool always answers for the time range on screen.

   A side's line-up is its complete first-listed comp when the sheet has
   one, otherwise the heroes its players are listed with first (weight
   >= 0.5). Rates on small samples are pulled toward 50%:

     shrunk win = (wins + K/2) / (maps + K)      K = 6 unless given

   so a 2–0 duo does not outrank a 30–15 one. Every row still carries its
   raw W–L, so nothing is hidden behind the adjustment.

   Pure functions over plain objects: runs in the browser
   (window.OWCS.lab) and in Node for tests (module.exports).
   ===================================================================== */
(function (root) {
  "use strict";

  const S = (root && root.OWCS && root.OWCS.tierStats) ||
    (typeof require === "function" ? require("./tierstats.js") : null);

  const NEED = { Tank: 1, Damage: 2, Support: 2 };
  const K = 6;
  const shrunk = (w, n, k) => (w + (k == null ? K : k) / 2) / (n + (k == null ? K : k));
  const pct = (x) => Math.round(x * 1000) / 10;

  /* ------------------------------------------------------------ scope */
  function expectations(T, asOf, from) {
    if (!T._labSweep) Object.defineProperty(T, "_labSweep", { value: new Map(), enumerable: false });
    const k = asOf + "|" + (from || "");
    if (!T._labSweep.has(k)) T._labSweep.set(k, S.sweep(T, asOf, from));
    return T._labSweep.get(k);
  }

  const lineup = (sd) => {
    if (!sd) return [];
    if (sd.c && sd.c.length === 5) return sd.c;
    return (sd.h || []).filter((p) => p[1] >= 0.5).map((p) => p[0]);
  };

  /* Every side of every map in scope, with its result and opponent. */
  function sides(T, opts) {
    opts = opts || {};
    const maxDate = (T.dates || {}).max || "9999-12-31";
    const asOf = opts.asOf && opts.asOf < maxDate ? opts.asOf : maxDate;
    const scope = { asOf: asOf, from: opts.from || null, region: opts.region || null, team: null };
    const sw = expectations(T, asOf, scope.from);
    const out = [];
    for (const mp of T.maps || []) {
      const m = (T.matches || [])[mp.m];
      if (!m || !S.inScope(m, scope)) continue;
      if (opts.map && mp.map !== opts.map) continue;
      if (opts.mode && mp.mode !== opts.mode) continue;
      const r = sw.per.get(mp.id);
      ["a", "b"].forEach((side) => {
        const os = side === "a" ? "b" : "a";
        const mine = lineup(mp[side]), theirs = lineup(mp[os]);
        if (!mine.length) return;
        out.push({ map: mp.map, mode: mp.mode || null, mapId: mp.id, match: mp.m, series: m.id, d: m.d,
          team: m[side], opp: m[os], heroes: mine, oppHeroes: theirs,
          complete: !!(mp[side] && mp[side].c && mp[side].c.length === 5),
          comp: mp[side] && mp[side].c && mp[side].c.length === 5 ? mp[side].c : null,
          oppComp: mp[os] && mp[os].c && mp[os].c.length === 5 ? mp[os].c : null,
          bans: (mp[side] && mp[side].bn) || [], oppBans: (mp[os] && mp[os].bn) || [],
          res: mp.w === side ? 1 : mp.w === os ? 0 : null,
          e: r ? (side === "a" ? r.ea : 1 - r.ea) : null });
      });
    }
    return out;
  }

  const has = (list, need) => need.every((h) => list.indexOf(h) >= 0);
  const tally = () => ({ n: 0, w: 0, l: 0 });
  const add = (t, res) => { t.n += 1; if (res === 1) t.w += 1; else if (res === 0) t.l += 1; };
  const finish = (id, t, k) => Object.assign({ id: id }, t, { win: shrunk(t.w, t.w + t.l, k), raw: t.w + t.l ? t.w / (t.w + t.l) : null });

  /* ------------------------------------------------- hero pair tables */
  /* With: heroes on the SAME side as `hero`. Against: heroes on the
     OTHER side. Each row is the record of `hero`'s side. */
  function heroTables(T, hero, opts) {
    opts = opts || {};
    const min = opts.min || 4;
    const all = sides(T, opts).filter((s) => s.heroes.indexOf(hero) >= 0);
    const base = tally();
    const withM = new Map(), vsM = new Map();
    all.forEach((s) => {
      add(base, s.res);
      s.heroes.forEach((h) => { if (h !== hero) { if (!withM.has(h)) withM.set(h, tally()); add(withM.get(h), s.res); } });
      s.oppHeroes.forEach((h) => { if (!vsM.has(h)) vsM.set(h, tally()); add(vsM.get(h), s.res); });
    });
    const rows = (m) => Array.from(m.entries()).map((e) => finish(e[0], e[1])).filter((r) => r.w + r.l >= min);
    const w = rows(withM), v = rows(vsM);
    const b = finish(hero, base);
    return {
      hero: hero, maps: base.n, base: b,
      bestWith: w.slice().sort((x, y) => y.win - x.win || y.n - x.n).slice(0, opts.top || 8),
      mostWith: w.slice().sort((x, y) => y.n - x.n || y.win - x.win).slice(0, opts.top || 8),
      hardestAgainst: v.slice().sort((x, y) => x.win - y.win || y.n - x.n).slice(0, opts.top || 8),
      bestAgainst: v.slice().sort((x, y) => y.win - x.win || y.n - x.n).slice(0, opts.top || 8),
    };
  }

  /* ------------------------------------------------------------ draft */
  /* d: { map, ours[], theirs[], bans[], opp (team id), asOf, from, region }
     roles: { heroId: "Tank" | "Damage" | "Support" } */
  function draft(T, roles, d) {
    d = d || {};
    const ours = (d.ours || []).filter(Boolean), theirs = (d.theirs || []).filter(Boolean), bans = (d.bans || []).filter(Boolean);
    const scope = { asOf: d.asOf, from: d.from, region: d.region };
    let pool = sides(T, Object.assign({ map: d.map || null }, scope));
    let where = d.map ? "map" : "all";
    if (d.map && pool.length < 30) { pool = sides(T, scope); where = "all-fallback"; }
    const legal = (heroes) => !heroes.some((h) => bans.indexOf(h) >= 0);

    /* finished comps that contain every hero we have picked */
    const comps = new Map();
    pool.forEach((s) => {
      if (!s.comp || !legal(s.comp) || !has(s.comp, ours)) return;
      const k = s.comp.slice().sort().join("+");
      if (!comps.has(k)) comps.set(k, Object.assign(tally(), { heroes: s.comp.slice(), teams: new Map(), vs: tally() }));
      const c = comps.get(k);
      add(c, s.res);
      c.teams.set(s.team, (c.teams.get(s.team) || 0) + 1);
      if (theirs.length && has(s.oppHeroes, theirs)) add(c.vs, s.res);
    });
    const completions = Array.from(comps.entries()).map((e) => {
      const r = finish(e[0], e[1]);
      r.heroes = e[1].heroes;
      r.teams = Array.from(e[1].teams.entries()).sort((a, b) => b[1] - a[1]).slice(0, 3);
      r.vs = e[1].vs.n ? finish("vs", e[1].vs) : null;
      r.score = r.win * Math.min(1, Math.sqrt(r.n / 4));
      return r;
    }).sort((a, b) => b.score - a.score || b.n - a.n).slice(0, d.top || 8);

    /* what each open slot could be */
    const count = { Tank: 0, Damage: 0, Support: 0 };
    ours.forEach((h) => { if (count[roles[h]] != null) count[roles[h]] += 1; });
    const open = Object.keys(NEED).filter((r) => count[r] < NEED[r]);
    const heroIds = new Set();
    pool.forEach((s) => s.heroes.forEach((h) => heroIds.add(h)));
    const slots = {};
    open.forEach((role) => {
      const rows = [];
      heroIds.forEach((h) => {
        if (roles[h] !== role || ours.indexOf(h) >= 0 || bans.indexOf(h) >= 0) return;
        const want = ours.concat([h]);
        const t = tally(), vs = tally();
        pool.forEach((s) => {
          if (!has(s.heroes, want)) return;
          add(t, s.res);
          if (theirs.length && has(s.oppHeroes, theirs)) add(vs, s.res);
        });
        if (!t.n) return;
        const r = finish(h, t);
        r.vs = vs.n ? finish("vs", vs) : null;
        r.score = r.win * Math.min(1, Math.sqrt(r.n / 6));
        rows.push(r);
      });
      slots[role] = { need: NEED[role] - count[role],
        picks: rows.sort((a, b) => b.score - a.score || b.n - a.n).slice(0, d.slotTop || 6) };
    });

    /* bans worth considering here, and against this opponent */
    const banT = new Map();
    let banMaps = 0;
    pool.forEach((s) => {
      if (s.bans.length) banMaps += 1;
      s.bans.forEach((h) => { if (!banT.has(h)) banT.set(h, tally()); add(banT.get(h), s.res); });
    });
    const banRows = Array.from(banT.entries()).map((e) => finish(e[0], e[1]))
      .filter((r) => bans.indexOf(r.id) < 0)
      .sort((a, b) => b.n - a.n).slice(0, 6).map((r) => Object.assign(r, { rate: banMaps ? r.n / banMaps : 0 }));
    let comfort = [];
    if (d.opp) {
      const ct = new Map();
      let n = 0;
      sides(T, Object.assign({ map: where === "map" ? d.map : null }, scope)).forEach((s) => {
        if (s.team !== d.opp) return;
        n += 1;
        s.heroes.forEach((h) => { if (!ct.has(h)) ct.set(h, tally()); add(ct.get(h), s.res); });
      });
      comfort = Array.from(ct.entries()).map((e) => Object.assign(finish(e[0], e[1]), { share: n ? e[1].n / n : 0 }))
        .filter((r) => bans.indexOf(r.id) < 0).sort((a, b) => b.n - a.n).slice(0, 6);
    }

    /* what has beaten their line-up */
    let answers = [];
    if (theirs.length) {
      const am = new Map();
      pool.forEach((s) => {
        if (!s.comp || !has(s.oppHeroes, theirs) || !legal(s.comp)) return;
        const k = s.comp.slice().sort().join("+");
        if (!am.has(k)) am.set(k, Object.assign(tally(), { heroes: s.comp.slice() }));
        add(am.get(k), s.res);
      });
      answers = Array.from(am.entries()).map((e) => Object.assign(finish(e[0], e[1]), { heroes: e[1].heroes }))
        .sort((a, b) => b.win - a.win || b.n - a.n).slice(0, 6);
    }

    return { where: where, pool: pool.length, poolMaps: new Set(pool.map((s) => s.mapId)).size,
      open: open, completions: completions, slots: slots, bans: banRows, comfort: comfort, answers: answers };
  }

  /* ----------------------------------------------------------- versus */
  /* P(win a best-of-n) from a per-map chance p: first to k = ceil(n/2). */
  function seriesWin(p, n) {
    const k = Math.ceil(n / 2);
    let total = 0;
    const choose = (a, b) => { let r = 1; for (let i = 1; i <= b; i++) r = r * (a - b + i) / i; return r; };
    for (let j = 0; j < k; j++) total += choose(k - 1 + j, j) * Math.pow(p, k) * Math.pow(1 - p, j);
    return total;
  }

  function versus(T, a, b, opts) {
    opts = opts || {};
    const maxDate = (T.dates || {}).max || "9999-12-31";
    const asOf = opts.asOf && opts.asOf < maxDate ? opts.asOf : maxDate;
    const sw = expectations(T, asOf, opts.from || null);
    const ra = sw.rankOf.get(a) || null, rb = sw.rankOf.get(b) || null;
    const W = T.weights || {};
    const scale = (W.elo || {}).scale || 400;
    const pMap = ra && rb ? 1 / (1 + Math.pow(10, (rb.power - ra.power) / scale)) : null;

    const scoped = sides(T, { asOf: asOf, from: opts.from || null });
    const mine = (t) => scoped.filter((s) => s.team === t);
    const A = mine(a), B = mine(b);

    const mapRec = (list) => {
      const m = new Map();
      list.forEach((s) => { if (!m.has(s.map)) m.set(s.map, Object.assign(tally(), { mode: s.mode })); add(m.get(s.map), s.res); });
      return m;
    };
    const ma = mapRec(A), mb = mapRec(B);
    const maps = Array.from(new Set(Array.from(ma.keys()).concat(Array.from(mb.keys())))).map((id) => {
      const x = ma.get(id) || tally(), y = mb.get(id) || tally();
      const sa = shrunk(x.w, x.w + x.l), sb = shrunk(y.w, y.w + y.l);
      /* the map nudges the overall chance by the two teams' records on it,
         each pulled toward 50% so a 1–0 map barely moves it */
      let p = null;
      if (pMap != null) {
        const lg = Math.log(pMap / (1 - pMap)) + 1.2 * (sa - sb);
        p = 1 / (1 + Math.exp(-lg));
      }
      return { map: id, mode: (ma.get(id) || mb.get(id) || {}).mode || null, a: x, b: y, p: p };
    }).sort((p, q) => (q.a.n + q.b.n) - (p.a.n + p.b.n));

    const form = (list) => list.slice().sort((x, y) => (y.d < x.d ? -1 : y.d > x.d ? 1 : y.match - x.match))
      .slice(0, 10).map((s) => ({ d: s.d, map: s.map, opp: s.opp, res: s.res, series: s.series }));

    /* meetings: every series between the two, whatever the range */
    const meetings = [];
    (T.matches || []).forEach((m, i) => {
      if (!((m.a === a && m.b === b) || (m.a === b && m.b === a))) return;
      let wa = 0, wb = 0;
      (T.maps || []).forEach((mp) => {
        if (mp.m !== i || !mp.w) return;
        const winner = mp.w === "a" ? m.a : m.b;
        if (winner === a) wa += 1; else if (winner === b) wb += 1;
      });
      meetings.push({ series: m.id, d: m.d, s: m.s, r: m.r, f: m.f, a: wa, b: wb });
    });
    meetings.sort((x, y) => (y.d < x.d ? -1 : y.d > x.d ? 1 : 0));

    const banCount = (list, key) => {
      const m = new Map();
      list.forEach((s) => s[key].forEach((h) => m.set(h, (m.get(h) || 0) + 1)));
      return Array.from(m.entries()).sort((x, y) => y[1] - x[1]).slice(0, 5);
    };
    const heroCount = (list) => {
      const m = new Map();
      list.forEach((s) => s.heroes.forEach((h) => m.set(h, (m.get(h) || 0) + 1)));
      return Array.from(m.entries()).sort((x, y) => y[1] - x[1]).slice(0, 10).map((e) => [e[0], list.length ? e[1] / list.length : 0]);
    };
    const record = (list) => { const t = tally(); list.forEach((s) => add(t, s.res)); return t; };

    return {
      a: { id: a, power: ra, maps: record(A), form: form(A), bans: banCount(A, "bans"), bannedAgainst: banCount(A, "oppBans"), heroes: heroCount(A) },
      b: { id: b, power: rb, maps: record(B), form: form(B), bans: banCount(B, "bans"), bannedAgainst: banCount(B, "oppBans"), heroes: heroCount(B) },
      pMap: pMap,
      series: pMap == null ? null : { bo3: seriesWin(pMap, 3), bo5: seriesWin(pMap, 5), bo7: seriesWin(pMap, 7) },
      maps: maps, meetings: meetings, asOf: asOf,
    };
  }

  const api = { NEED: NEED, shrunk: shrunk, lineup: lineup, sides: sides, heroTables: heroTables,
    draft: draft, versus: versus, seriesWin: seriesWin, pct: pct };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) { root.OWCS = root.OWCS || {}; root.OWCS.lab = api; }
})(typeof window !== "undefined" ? window : null);
