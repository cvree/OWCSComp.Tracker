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
     1 · 2 · 3               the last 24 / 48 / 72 hours: the newest match
                             day, two days or three (dates are per day)
     custom                  any from / to, by date

   No "whole season" and no per-stage presets: what people want is what is
   winning now, so the choice is a recent window or their own dates.

   Region: a page can put a region menu in the bar (opt.regions), so the
   region sits beside the dates however far down the page someone is.

   State: ?t=14 | ?t=1 | ?t=custom&tf=…&tt=…
   The URL wins, so a shared link shows what its sender saw; otherwise the
   last range this browser chose is remembered (localStorage, a per-viewer
   convenience — a missing or blocked store simply means the default).
   Older links (?range= / ?asof= / ?from= on tiers, ?w= on home, ?cw= on
   comps) are read once and translated; an old season or stage link opens
   the default window.

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
  const HOURS = ["1", "2", "3"];
  const DAYS = HOURS.concat(["7", "14", "30", "60", "90"]);
  const QUICK = [["7", "7 days"], ["14", "14 days"], ["30", "30 days"], ["90", "90 days"]];
  const STORE = "owcs.range.v1";
  /* "Last 24 hours" for 1–3 days, "Last 30 days" otherwise. */
  const isHours = (t) => HOURS.indexOf(String(t)) >= 0;
  const longLabel = (t) => (isHours(t) ? "Last " + Number(t) * 24 + " hours" : "Last " + t + " days");
  const shortLabel = (t) => (isHours(t) ? Number(t) * 24 + " hours" : t + " days");

  const DAY = 86400000;
  const addDays = (iso, n) => new Date(Date.parse(iso + "T00:00:00Z") + n * DAY).toISOString().slice(0, 10);
  const span = (a, b) => Math.round((Date.parse(b + "T00:00:00Z") - Date.parse(a + "T00:00:00Z")) / DAY) + 1;
  const fmt = (iso) => (iso ? (P.fmtDate ? P.fmtDate(iso) : iso) : "—");
  const valid = (iso) => /^\d{4}-\d{2}-\d{2}$/.test(iso || "");

  /* ------------------------------------------------------------ data */
  const maxDate = () => ((T && T.dates) || {}).max || null;
  const minDate = () => ((T && T.dates) || {}).min || null;

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
      return f ? { t: "custom", tf: f, tt: to } : null;
    }
    if (DAYS.indexOf(range) >= 0) return { t: range };
    if (w === "30" || cw === "30" || cw === "60" || cw === "90") return { t: w === "30" ? "30" : cw };
    return null;
  }

  function clean(v) {
    if (!v || !v.t) return null;
    if (DAYS.indexOf(String(v.t)) >= 0) return { t: String(v.t) };
    if (v.t === "custom" && valid(v.tf) && valid(v.tt) && v.tf <= v.tt) return { t: "custom", tf: v.tf, tt: v.tt };
    return null;
  }

  let state = clean({ t: P.qs("t"), tf: P.qs("tf"), tt: P.qs("tt") }) || clean(fromLegacy()) || clean(readStore()) ||
    { t: DEFAULT };

  /* -------------------------------------------------------- resolving */
  /* opt.region: count relative windows back from that region's newest map.
     Returns { key, from, to, days, label, short, prev } — `from` null means
     "from the start"; `prev` is the window of equal length just before,
     for rising / falling arrows. */
  function resolve(opt) {
    opt = opt || {};
    const max = latest(opt.region) || maxDate();
    const t = state.t;
    if (DAYS.indexOf(t) >= 0) {
      const n = Number(t);
      const from = addDays(max, -(n - 1));
      return { key: t, from: from, to: max, days: n, label: longLabel(t), short: shortLabel(t),
        prev: { from: addDays(from, -n), to: addDays(from, -1), days: n } };
    }
    if (t === "custom") {
      const n = span(state.tf, state.tt);
      return { key: t, from: state.tf, to: state.tt, days: n, label: fmt(state.tf) + " – " + fmt(state.tt), short: "Custom",
        prev: { from: addDays(state.tf, -n), to: addDays(state.tf, -1), days: n } };
    }
    /* Unreachable (clean() only lets the presets above through), kept so
       a caller never gets undefined. */
    state = { t: DEFAULT };
    return resolve(opt);
  }

  /* The range as words that fit inside a sentence: "the last 14 days",
     "the last 24 hours", "Oct 1 – Oct 14". */
  const phrase = (opt) => {
    const r = resolve(opt);
    if (DAYS.indexOf(r.key) >= 0) return longLabel(r.key).replace(/^Last/, "the last");
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
  function controlHtml(host) {
    const r = resolve({ region: (host && host.dataset.region) || null });
    const sel = (v) => (state.t === v ? " selected" : "");
    const quick = '<div class="seg trange__seg" role="group" aria-label="Time range">' + QUICK.map((q) =>
      '<button type="button" data-t="' + q[0] + '" aria-pressed="' + (state.t === q[0] ? "true" : "false") + '">' +
      esc(q[1]) + "</button>").join("") + "</div>";
    /* "Custom…": the short windows (24 / 48 / 72 hours), 60 days, and
       your own dates. */
    const hours = HOURS.map((h) => '<option value="' + h + '"' + sel(h) + ">" + esc(longLabel(h)) + "</option>").join("");
    const more = '<label class="trange__more"><span class="visually-hidden">Custom range</span>' +
      '<select class="input" data-t-select>' +
      '<option value="">' + (QUICK.some((q) => q[0] === state.t) ? "Custom…" : esc(r.short)) + "</option>" +
      hours +
      '<option value="60"' + sel("60") + ">Last 60 days</option>" +
      '<option value="custom"' + sel("custom") + ">Pick dates…</option>" +
      "</select></label>";
    const custom = '<span class="trange__custom"' + (state.t === "custom" ? "" : " hidden") + ">" +
      '<label><span class="visually-hidden">From</span><input class="input" type="date" data-tf value="' +
        esc(state.tf || r.from || minDate() || "") + '" min="' + esc(minDate() || "") + '" max="' + esc(maxDate() || "") + '"></label>' +
      '<span class="dim">to</span>' +
      '<label><span class="visually-hidden">To</span><input class="input" type="date" data-tt value="' +
        esc(state.tt || r.to || maxDate() || "") + '" min="' + esc(minDate() || "") + '" max="' + esc(maxDate() || "") + '"></label></span>';
    /* On a phone the whole choice is one menu, so the pinned bar is one
       row high: every window and custom dates. */
    const all = '<label class="trange__all"><span class="visually-hidden">Time range</span>' +
      '<select class="input" data-t-select>' +
      DAYS.map((d) => '<option value="' + d + '"' + sel(d) + ">" + esc(longLabel(d)) + "</option>").join("") +
      '<option value="custom"' + sel("custom") + ">Pick dates…</option>" +
      "</select></label>";
    /* The region menu, when the page asked for one (opt.regions). */
    const regs = host && host._regions;
    const cur = (host && host.dataset.region) || "";
    const region = regs && regs.length ? '<label class="trange__region"><span class="visually-hidden">Region</span>' +
      '<select class="input" data-t-region aria-label="Region">' + regs.map((x) => '<option value="' + esc(x[0]) + '"' +
        (String(x[0]) === cur ? " selected" : "") + ">" + esc(x[1]) + "</option>").join("") + "</select></label>" : "";
    return '<div class="trange__in">' +
      '<span class="trange__k" aria-hidden="true">◷</span>' + region + quick + more + all + custom +
      '<span class="trange__res" data-tr-res aria-live="polite"></span></div>';
  }

  function paint(host) {
    const r = resolve({ region: host.dataset.region || null });
    host.innerHTML = controlHtml(host);
    const res = host.querySelector("[data-tr-res]");
    if (res) {
      /* The hour windows count back from the newest match, not from now
         (on a day with no matches "the last 24 hours" would be empty), so
         the bar says which day that is. */
      const latestNote = isHours(r.key) ? ' <span class="dim" title="Counted back from the newest match' +
        (host.dataset.region ? " in this region" : "") + ', not from right now.">(latest match day' +
        (r.days > 1 ? "s" : "") + ")</span>" : "";
      res.innerHTML = (r.from && r.from !== r.to ? esc(fmt(r.from)) + " – " : "") + esc(fmt(r.to)) + latestNote +
        (host._note ? ' · <span class="trange__note">' + host._note + "</span>" : "") +
        ' <span class="dim" title="The spreadsheet records no dates; each match is dated from the official schedule by its stage, week and day.">· est. dates</span>';
    }
  }

  /* Mount the bar into `host` (an element or id). opt.region scopes the
     relative windows; opt.sticky (default true) pins it under the header.
     opt.regions ([[id, label], …], "" = all) puts a region menu in the
     bar; opt.onRegion(id) hears when it changes (the bar rescopes itself). */
  function mount(host, opt) {
    opt = opt || {};
    const el = typeof host === "string" ? document.getElementById(host) : host;
    if (!el) return null;
    el.setAttribute("data-trange", "");
    el.classList.add("trange");
    if (opt.sticky !== false) el.classList.add("trange--sticky");
    if (opt.region) el.dataset.region = opt.region;
    if (opt.regions) el._regions = opt.regions;
    if (opt.onRegion) el._onRegion = opt.onRegion;
    paint(el);
    if (!el._wired) {
      el._wired = true;
      el.addEventListener("click", (e) => {
        const b = e.target.closest && e.target.closest("[data-t]");
        if (b) set({ t: b.dataset.t });
      });
      el.addEventListener("change", (e) => {
        const t = e.target;
        if (t.matches("[data-t-region]")) {
          setRegion(el, t.value);
          if (el._onRegion) el._onRegion(t.value);
          return;
        }
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
    const i = DAYS.indexOf(state.t);
    const next = i >= 0 && i < DAYS.length - 2 ? DAYS.slice(i + 1, i + 3) : i === DAYS.length - 2 ? ["90"] : ["30", "90"];
    return '<span class="row trange__widen">' + next.map((t) => '<button type="button" class="btn btn--sm btn--ghost" data-t-widen="' +
      t + '">' + esc(longLabel(t)) + "</button>").join("") + "</span>";
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("[data-t-widen]");
    if (b) set({ t: b.dataset.tWiden });
  });

  P.timeRange = { DEFAULT: DEFAULT, get: () => Object.assign({}, state), resolve: resolve, phrase: phrase, buildOpts: buildOpts, set: set,
    onChange: onChange, mount: mount, note: note, setRegion: setRegion, widenHtml: widenHtml, addDays: addDays, longLabel: longLabel };
})();
