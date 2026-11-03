/* =====================================================================
   OWCS Comp Tracker — app/timerange.js
   One time range for the whole site.

   Every page that counts comps or heroes reads the same range, shows the
   same control, and redraws when it changes. The control sits in a slim
   bar that sticks under the header, so the range is never more than a
   glance and a tap away however far down a page someone has scrolled.

   Default: the last 14 days. People open this site for what is winning
   now; a season average buries this week's meta under February's.

   Presets
     7 · 14 · 30 · 60 · 90   the last N days, counted back from the newest
                             map in view (the region's own newest map when
                             a region is chosen, so a region that has not
                             played this week is not simply empty)
     stage:<id>              one stretch of the season, first to last day
     season                  everything
     custom                  any from / to, by date

   State: ?t=14 | ?t=stage:stage-3 | ?t=season | ?t=custom&tf=…&tt=…
   The URL wins, so a shared link shows what its sender saw; otherwise the
   last range this browser chose is remembered (localStorage, a per-viewer
   convenience — a missing or blocked store simply means the default).
   Older links (?range= / ?asof= / ?from= on tiers, ?w= on home, ?cw= on
   comps) are read once and translated.

   Dates in the spreadsheet are ESTIMATED from the official schedule
   (config/external/sheet_schedule.json); the control says so.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const T = window.OWCS_SHEET_TIERS;
  if (!P) return;
  const esc = P.esc;

  const DEFAULT = "14";
  const DAYS = ["7", "14", "30", "60", "90"];
  const QUICK = [["7", "7 days"], ["14", "14 days"], ["30", "30 days"], ["90", "90 days"], ["season", "Season"]];
  const STORE = "owcs.range.v1";
  const STAGE_NAMES = { "pre-season": "Pre-Season Bootcamp", "stage-1": "Stage 1", "champions-clash": "Champions Clash",
    "stage-2": "Stage 2", "midseason-championship": "Midseason Championship", "stage-3": "Stage 3",
    "world-finals": "World Finals" };

  const DAY = 86400000;
  const addDays = (iso, n) => new Date(Date.parse(iso + "T00:00:00Z") + n * DAY).toISOString().slice(0, 10);
  const span = (a, b) => Math.round((Date.parse(b + "T00:00:00Z") - Date.parse(a + "T00:00:00Z")) / DAY) + 1;
  const fmt = (iso) => (iso ? (P.fmtDate ? P.fmtDate(iso) : iso) : "—");
  const valid = (iso) => /^\d{4}-\d{2}-\d{2}$/.test(iso || "");

  /* ------------------------------------------------------------ data */
  const maxDate = () => ((T && T.dates) || {}).max || null;
  const minDate = () => ((T && T.dates) || {}).min || null;

  /* Each stage's first and last estimated day, in season order. */
  function stages() {
    if (!T) return [];
    if (T._trStages) return T._trStages;
    const m = new Map();
    (T.matches || []).forEach((x) => {
      if (!x.d || !x.s) return;
      const s = m.get(x.s) || { id: x.s, from: x.d, to: x.d };
      if (x.d < s.from) s.from = x.d;
      if (x.d > s.to) s.to = x.d;
      m.set(x.s, s);
    });
    const list = Array.from(m.values()).sort((a, b) => a.to.localeCompare(b.to))
      .map((s) => Object.assign(s, { name: STAGE_NAMES[s.id] || s.id }));
    Object.defineProperty(T, "_trStages", { value: list, enumerable: false });
    return list;
  }

  /* The newest map in a region (or overall). */
  function latest(region) {
    if (!T || !region) return maxDate();
    let d = null;
    (T.matches || []).forEach((m) => {
      if (!m.d) return;
      const inR = region === "international" ? m.r === "international" : m.r === region;
      if (inR && (!d || m.d > d)) d = m.d;
    });
    return d || maxDate();
  }

  /* ----------------------------------------------------------- state */
  function readStore() {
    try { return JSON.parse(localStorage.getItem(STORE) || "null"); } catch (e) { return null; }
  }
  function writeStore(v) {
    try { localStorage.setItem(STORE, JSON.stringify(v)); } catch (e) { /* a blocked store only loses the memory */ }
  }

  function fromLegacy() {
    const range = P.qs("range"), asof = P.qs("asof"), from = P.qs("from"), w = P.qs("w"), cw = P.qs("cw");
    if (range === "custom" || (asof && valid(asof))) {
      const to = valid(asof) ? asof : maxDate();
      const f = range === "custom" && valid(from) ? from : (DAYS.indexOf(range) >= 0 ? addDays(to, -(Number(range) - 1)) : null);
      if (range === "season" && !valid(asof)) return { t: "season" };
      return f ? { t: "custom", tf: f, tt: to } : { t: "custom", tf: minDate(), tt: to };
    }
    if (range === "season" || w === "season" || cw === "season") return { t: "season" };
    if (DAYS.indexOf(range) >= 0) return { t: range };
    if (w === "30" || cw === "30" || cw === "60" || cw === "90") return { t: w === "30" ? "30" : cw };
    return null;
  }

  function clean(v) {
    if (!v || !v.t) return null;
    if (DAYS.indexOf(String(v.t)) >= 0 || v.t === "season") return { t: String(v.t) };
    if (/^stage:/.test(v.t) && stages().some((s) => "stage:" + s.id === v.t)) return { t: v.t };
    if (v.t === "custom" && valid(v.tf) && valid(v.tt) && v.tf <= v.tt) return { t: "custom", tf: v.tf, tt: v.tt };
    return null;
  }

  let state = clean({ t: P.qs("t"), tf: P.qs("tf"), tt: P.qs("tt") }) || clean(fromLegacy()) || clean(readStore()) ||
    { t: DEFAULT };

  /* -------------------------------------------------------- resolving */
  /* opt.region: count relative windows back from that region's newest map.
     Returns { key, from, to, days, label, short, prev } — `from` null means
     "from the start"; `prev` is the window of equal length just before,
     for rising / falling arrows (null for a stage or the whole season). */
  function resolve(opt) {
    opt = opt || {};
    const max = latest(opt.region) || maxDate();
    const t = state.t;
    if (DAYS.indexOf(t) >= 0) {
      const n = Number(t);
      const from = addDays(max, -(n - 1));
      return { key: t, from: from, to: max, days: n, label: "Last " + n + " days", short: n + " days",
        prev: { from: addDays(from, -n), to: addDays(from, -1), days: n } };
    }
    if (/^stage:/.test(t)) {
      const s = stages().find((x) => "stage:" + x.id === t);
      if (s) return { key: t, from: s.from, to: s.to, days: span(s.from, s.to), label: s.name, short: s.name, prev: null };
    }
    if (t === "custom") {
      const n = span(state.tf, state.tt);
      return { key: t, from: state.tf, to: state.tt, days: n, label: fmt(state.tf) + " – " + fmt(state.tt), short: "Custom",
        prev: { from: addDays(state.tf, -n), to: addDays(state.tf, -1), days: n } };
    }
    return { key: "season", from: null, to: maxDate(), days: null, label: "Whole season", short: "Season", prev: null };
  }

  /* The range as words that fit inside a sentence: "the last 14 days",
     "the whole season", "Stage 3", "Oct 1 – Oct 14". */
  const phrase = (opt) => {
    const r = resolve(opt);
    if (r.key === "season") return "the whole season";
    if (DAYS.indexOf(r.key) >= 0) return "the last " + r.days + " days";
    return r.label;
  };

  /* Options for app/compbook.js and app/tierstats.js. */
  const buildOpts = (opt) => { const r = resolve(opt); return { asOf: r.to, from: r.from || null }; };

  /* ---------------------------------------------------------- changes */
  const listeners = [];
  function set(v) {
    const next = clean(v);
    if (!next) return;
    state = next;
    writeStore(state);
    P.setQs({ t: state.t === DEFAULT ? null : state.t, tf: state.tf || null, tt: state.tt || null,
      range: null, asof: null, from: null, w: null, cw: null });
    document.querySelectorAll("[data-trange]").forEach(paint);
    listeners.forEach((fn) => { try { fn(resolve()); } catch (e) { if (window.console) console.warn("[timerange]", e); } });
  }
  const onChange = (fn) => { listeners.push(fn); };

  /* ---------------------------------------------------------- control */
  function controlHtml() {
    const r = resolve();
    const st = stages();
    const quick = '<div class="seg trange__seg" role="group" aria-label="Time range">' + QUICK.map((q) =>
      '<button type="button" data-t="' + q[0] + '" aria-pressed="' + (state.t === q[0] ? "true" : "false") + '">' +
      esc(q[1]) + "</button>").join("") + "</div>";
    const more = '<label class="trange__more"><span class="visually-hidden">More ranges</span>' +
      '<select class="input" data-t-select>' +
      '<option value="">' + (QUICK.some((q) => q[0] === state.t) ? "Stage or dates…" : esc(r.short)) + "</option>" +
      '<option value="60"' + (state.t === "60" ? " selected" : "") + ">Last 60 days</option>" +
      '<optgroup label="A stage">' + st.map((s) => '<option value="stage:' + esc(s.id) + '"' +
        (state.t === "stage:" + s.id ? " selected" : "") + ">" + esc(s.name) + "</option>").join("") + "</optgroup>" +
      '<option value="custom"' + (state.t === "custom" ? " selected" : "") + ">Custom dates…</option>" +
      "</select></label>";
    const custom = '<span class="trange__custom"' + (state.t === "custom" ? "" : " hidden") + ">" +
      '<label><span class="visually-hidden">From</span><input class="input" type="date" data-tf value="' +
        esc(state.tf || r.from || minDate() || "") + '" min="' + esc(minDate() || "") + '" max="' + esc(maxDate() || "") + '"></label>' +
      '<span class="dim">to</span>' +
      '<label><span class="visually-hidden">To</span><input class="input" type="date" data-tt value="' +
        esc(state.tt || r.to || maxDate() || "") + '" min="' + esc(minDate() || "") + '" max="' + esc(maxDate() || "") + '"></label></span>';
    /* On a phone the whole choice is one menu, so the pinned bar is one
       row high: every preset, every stage, and custom dates. */
    const all = '<label class="trange__all"><span class="visually-hidden">Time range</span>' +
      '<select class="input" data-t-select>' +
      ["7", "14", "30", "60", "90"].map((d) => '<option value="' + d + '"' + (state.t === d ? " selected" : "") + ">Last " + d + " days</option>").join("") +
      '<option value="season"' + (state.t === "season" ? " selected" : "") + ">Whole season</option>" +
      '<optgroup label="A stage">' + st.map((x) => '<option value="stage:' + esc(x.id) + '"' +
        (state.t === "stage:" + x.id ? " selected" : "") + ">" + esc(x.name) + "</option>").join("") + "</optgroup>" +
      '<option value="custom"' + (state.t === "custom" ? " selected" : "") + ">Custom dates…</option>" +
      "</select></label>";
    return '<div class="trange__in">' +
      '<span class="trange__k" aria-hidden="true">◷</span>' + quick + more + all + custom +
      '<span class="trange__res" data-tr-res aria-live="polite"></span></div>';
  }

  function paint(host) {
    const r = resolve({ region: host.dataset.region || null });
    host.innerHTML = controlHtml();
    const res = host.querySelector("[data-tr-res]");
    if (res) {
      res.innerHTML = (r.from ? esc(fmt(r.from)) + " – " : "Season to ") + esc(fmt(r.to)) +
        (host._note ? ' · <span class="trange__note">' + host._note + "</span>" : "") +
        ' <span class="dim" title="The spreadsheet records no dates; each match is dated from the official schedule by its stage, week and day.">· est. dates</span>';
    }
  }

  /* Mount the bar into `host` (an element or id). opt.region scopes the
     relative windows; opt.sticky (default true) pins it under the header. */
  function mount(host, opt) {
    opt = opt || {};
    const el = typeof host === "string" ? document.getElementById(host) : host;
    if (!el) return null;
    el.setAttribute("data-trange", "");
    el.classList.add("trange");
    if (opt.sticky !== false) el.classList.add("trange--sticky");
    if (opt.region) el.dataset.region = opt.region;
    paint(el);
    if (!el._wired) {
      el._wired = true;
      el.addEventListener("click", (e) => {
        const b = e.target.closest && e.target.closest("[data-t]");
        if (b) set({ t: b.dataset.t });
      });
      el.addEventListener("change", (e) => {
        const t = e.target;
        if (t.matches("[data-t-select]")) {
          if (!t.value) return;
          if (t.value === "custom" && state.t === "custom") return;
          if (t.value === "custom") {
            const r = resolve({ region: el.dataset.region || null });
            set({ t: "custom", tf: r.from || minDate(), tt: r.to || maxDate() });
          } else set({ t: t.value });
        } else if (t.matches("[data-tf],[data-tt]")) {
          let f = el.querySelector("[data-tf]").value, to = el.querySelector("[data-tt]").value;
          if (!valid(f) || !valid(to)) return;
          /* Moving one end past the other carries the other end along:
             a "from" after "to" means the window moves forward, so "to"
             goes to the newest map; a "to" before "from" moves back. */
          if (f > to) {
            if (t.matches("[data-tf]")) to = maxDate() && maxDate() >= f ? maxDate() : f;
            else f = addDays(to, -13) < (minDate() || to) ? (minDate() || to) : addDays(to, -13);
          }
          set({ t: "custom", tf: f, tt: to });
        }
      });
    }
    return el;
  }

  /* A page can put a short note on the bar ("204 maps"). */
  function note(host, html) {
    const el = typeof host === "string" ? document.getElementById(host) : host;
    if (!el) return;
    el._note = html || "";
    paint(el);
  }
  /* Keep a region-scoped bar in step with the page's region filter. */
  function setRegion(host, region) {
    const el = typeof host === "string" ? document.getElementById(host) : host;
    if (!el) return;
    if (region) el.dataset.region = region; else delete el.dataset.region;
    paint(el);
  }

  /* For an empty or thin result: one tap to a wider window. */
  function widenHtml() {
    const order = ["7", "14", "30", "60", "90", "season"];
    const i = order.indexOf(state.t);
    const next = i >= 0 && i < order.length - 1 ? order.slice(i + 1, i + 3) : ["30", "season"];
    return '<span class="row trange__widen">' + next.map((t) => '<button type="button" class="btn btn--sm btn--ghost" data-t-widen="' +
      t + '">' + (t === "season" ? "Whole season" : "Last " + t + " days") + "</button>").join("") + "</span>";
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("[data-t-widen]");
    if (b) set({ t: b.dataset.tWiden });
  });

  P.timeRange = { DEFAULT: DEFAULT, get: () => Object.assign({}, state), resolve: resolve, phrase: phrase, buildOpts: buildOpts, set: set,
    onChange: onChange, mount: mount, note: note, setRegion: setRegion, widenHtml: widenHtml, stages: stages, addDays: addDays };
})();
