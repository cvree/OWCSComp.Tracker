/* =====================================================================
   OWCS Comp Tracker — app/schedule.js
   The season, for a fan: what is on next, when, and where it sits in the
   bracket. Renders window.OWCS_SCHEDULE (pipeline/schedule_build.py) into
   the Matches page's Upcoming, Schedule and Brackets views.

     P.sched.mountUpcoming(host)   next up, day by day, in your time zone
     P.sched.mountSchedule(host)   the season at a glance, event by event
     P.sched.mountBrackets(host)   any event's bracket, standings and lists

   Honesty rules, the same as the rest of the site:
     * a time is shown only when a source gave one (Liquipedia or a person,
       config/schedule.json); a published day reads "time TBA"; an
       estimated day says "estimated"; a stage with no published days
       reads "dates TBA" — nothing is invented;
     * a slot with no teams reads TBD; a past slot with no result reads
       "awaiting result", never a guess at who won;
     * every view says where its dates came from.

   Per-viewer conveniences only live in localStorage (followed teams), and
   every read and write is wrapped so a private window still works.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  if (!P) return;
  const esc = P.esc;
  const S = window.OWCS_SCHEDULE || null;
  const plural = (n, w) => n + " " + w + (n === 1 || /s$/.test(w) ? "" : "s");

  const M = new Map();
  const EV = new Map();
  if (S) {
    (S.matches || []).forEach((m) => M.set(m.id, m));
    (S.events || []).forEach((e) => EV.set(e.id, e));
  }

  /* ---------------------------------------------------------- time */
  const pad = (n) => (n < 10 ? "0" : "") + n;
  const isoLocal = (d) => d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  const dateOf = (iso) => { const p = iso.split("-").map(Number); return new Date(p[0], p[1] - 1, p[2]); };
  const now = () => new Date();
  const today = () => isoLocal(now());
  const dayDiff = (iso) => Math.round((dateOf(iso) - dateOf(today())) / 864e5);
  const ZONE = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { return ""; } })();
  const fmt = (opts) => { try { return new Intl.DateTimeFormat(undefined, opts); } catch (e) { return null; } };
  const F_DAY = fmt({ weekday: "long", month: "long", day: "numeric" });
  const F_SHORT = fmt({ weekday: "short", month: "short", day: "numeric" });
  const F_MON = fmt({ month: "short", day: "numeric" });
  const F_MONTH = fmt({ month: "long", year: "numeric" });
  const F_TIME = fmt({ hour: "numeric", minute: "2-digit" });
  const F_TZ = fmt({ timeZoneName: "short" });
  const fDay = (iso) => (F_DAY ? F_DAY.format(dateOf(iso)) : iso);
  const fShort = (iso) => (F_SHORT ? F_SHORT.format(dateOf(iso)) : iso);
  const fMon = (iso) => (F_MON ? F_MON.format(dateOf(iso)) : iso);
  const fTime = (d) => (F_TIME ? F_TIME.format(d) : d.toISOString().slice(11, 16));
  const tzShort = (d) => {
    if (!F_TZ || !F_TZ.formatToParts) return "";
    const p = F_TZ.formatToParts(d).filter((x) => x.type === "timeZoneName")[0];
    return p ? p.value : "";
  };
  const rel = (iso) => {
    const n = dayDiff(iso);
    if (n === 0) return "Today";
    if (n === 1) return "Tomorrow";
    if (n === -1) return "Yesterday";
    if (n > 1 && n < 7) return "in " + n + " days";
    if (n >= 7) return n < 14 ? "next week" : "in " + Math.round(n / 7) + " weeks";
    return n > -7 ? -n + " days ago" : Math.round(-n / 7) + " weeks ago";
  };
  const range = (a, b) => {
    if (!a) return "Dates TBA";
    if (!b || a === b) return fMon(a);
    const A = dateOf(a), B = dateOf(b);
    if (A.getMonth() === B.getMonth()) return fMon(a) + "–" + B.getDate();
    return fMon(a) + " – " + fMon(b);
  };
  const HOURS = { 3: 2, 5: 3, 7: 4 };
  const lengthMs = (m) => (HOURS[m.bestOf] || 3) * 36e5;

  /* A match's calendar day in the viewer's own zone. */
  const dayKey = (m) => (m.startsAt ? isoLocal(new Date(m.startsAt)) : m.date || null);

  /* done · live · today · upcoming · awaiting (its day passed, no result)
     · tba (no day published). */
  const status = (m) => {
    if (m.state === "done") return "done";
    if (m.startsAt) {
      const t0 = new Date(m.startsAt).getTime(), t = Date.now();
      if (t >= t0 && t < t0 + lengthMs(m)) return "live";
      if (t >= t0 + lengthMs(m)) return "awaiting";
      return dayKey(m) === today() ? "today" : "upcoming";
    }
    if (!m.date) return "tba";
    const n = dayDiff(m.date);
    return n === 0 ? "today" : n < 0 ? "awaiting" : "upcoming";
  };
  const isOpen = (m) => m.state !== "done";

  /* --------------------------------------------------------- teams */
  const teamName = (m, side) => {
    const id = m["team" + side];
    if (id) return P.anyTeamName(id);
    return m["name" + side] || null;
  };
  const teamKey = (m, side) => m["team" + side] || (m["name" + side] ? "raw:" + m["name" + side] : null);
  const mark = (m, side, px) => {
    const id = m["team" + side], nm = m["name" + side];
    if (id && P.team(id)) return P.teamMark(id, px);
    if (id || nm) return P.teamMark({ id: id || "raw-" + nm, name: id ? P.anyTeamName(id) : nm }, px);
    return '<span class="sx-tbd-mark" aria-hidden="true">?</span>';
  };

  /* Followed teams: a per-viewer convenience, nothing more. */
  const FOLLOW_KEY = "owcs.follow";
  const follows = (() => {
    try { return new Set(JSON.parse(localStorage.getItem(FOLLOW_KEY) || "[]")); } catch (e) { return new Set(); }
  })();
  const saveFollows = () => { try { localStorage.setItem(FOLLOW_KEY, JSON.stringify(Array.from(follows))); } catch (e) { /* private window */ } };
  const followed = (m) => !!(teamKey(m, "A") && follows.has(teamKey(m, "A")) || teamKey(m, "B") && follows.has(teamKey(m, "B")));

  /* --------------------------------------------------------- labels */
  const evName = (e) => (e ? e.name : "");
  const regionName = (r) => (r ? P.extRegionName(r) : "Pre-Season");
  const STAGE_NAME = { "pre-season": "Pre-Season", "stage-1": "Stage 1", "champions-clash": "Champions Clash",
    "stage-2": "Stage 2", "midseason-championship": "Midseason Championship", "stage-3": "Stage 3",
    "world-finals": "World Finals" };
  const roundLabel = (m) => {
    const ev = EV.get(m.event);
    const ph = ev && ev.phases.find((p) => p.id === m.phase);
    const r = m.round || "";
    if (m.kind === "bracket" && ph && ph.id !== "playoffs") return ph.name + " · " + r;
    if (m.kind === "bracket") return r;
    if (r === "Round robin" && m.week) return "Round robin · Week " + m.week;
    return r;
  };
  const boChip = (m) => (m.bestOf ? '<span class="sx-bo" title="Best of ' + m.bestOf + '">Bo' + m.bestOf + "</span>" : "");
  const regionChip = (r) => '<span class="sx-region" data-region="' + esc(r || "none") + '">' + esc(regionName(r)) + "</span>";

  const WHEN_SAY = {
    liquipedia: "Start time from Liquipedia",
    official: "Start time from the official schedule",
    "liquipedia-day": "Day from Liquipedia; time not listed yet",
    "published-day": "Day from the published schedule; time not announced yet",
    estimate: "Estimated from the stage's published weeks",
  };
  /* The time cell: a real time only when a source gave one. */
  const whenHtml = (m, opt) => {
    opt = opt || {};
    const st = status(m);
    if (m.startsAt) {
      const d = new Date(m.startsAt);
      return '<span class="sx-when" title="' + esc(WHEN_SAY[m.dateFrom] || "") + '"><b>' + esc(fTime(d)) + "</b>" +
        '<small>' + esc(tzShort(d)) + "</small>" + (opt.day ? "<small>" + esc(fShort(dayKey(m))) + "</small>" : "") + "</span>";
    }
    if (st === "done") return '<span class="sx-when sx-when--tba"><b>Final</b>' + (opt.day && m.date ? "<small>" + esc(fShort(m.date)) + "</small>" : "") + "</span>";
    if (!m.date) return '<span class="sx-when sx-when--tba" title="No day published for this series yet"><b>TBA</b><small>date</small></span>';
    const est = m.dateFrom === "estimate";
    return '<span class="sx-when sx-when--tba" title="' + esc(WHEN_SAY[m.dateFrom] || "") + '">' +
      (opt.day ? "<b>" + esc(fShort(m.date)) + "</b>" : "<b>" + (st === "done" ? "—" : "TBA") + "</b>") +
      "<small>" + (est ? "est. day" : "time") + "</small></span>";
  };
  const stateChip = (m) => {
    const st = status(m);
    if (st === "live") return '<span class="sx-live"><span class="dot pulse"></span>Live</span>';
    if (st === "awaiting") return '<span class="chip" data-state="review" title="Its day has passed and the result is not in the spreadsheet yet"><span class="dot"></span>Awaiting result</span>';
    return "";
  };

  /* ----------------------------------------------------- calendar */
  const icsEsc = (s) => String(s || "").replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/[,;]/g, (c) => "\\" + c);
  const icsStamp = (d) => d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  const pageUrl = (q) => location.href.split("?")[0].split("#")[0] + (q ? "?" + q : "");
  const vevent = (m) => {
    const ev = EV.get(m.event);
    const a = teamName(m, "A") || "TBD", b = teamName(m, "B") || "TBD";
    const sum = a + " vs " + b + " · " + evName(ev) + " " + roundLabel(m) + (m.bestOf ? " (Bo" + m.bestOf + ")" : "");
    const lines = ["BEGIN:VEVENT", "UID:" + m.id + "@owcs-comp-tracker", "DTSTAMP:" + icsStamp(new Date())];
    if (m.startsAt) {
      const d = new Date(m.startsAt);
      lines.push("DTSTART:" + icsStamp(d), "DTEND:" + icsStamp(new Date(d.getTime() + lengthMs(m))));
    } else {
      const d = dateOf(m.date), e = new Date(d.getTime() + 864e5);
      lines.push("DTSTART;VALUE=DATE:" + m.date.replace(/-/g, ""), "DTEND;VALUE=DATE:" + isoLocal(e).replace(/-/g, ""));
    }
    const desc = (m.startsAt ? WHEN_SAY[m.dateFrom] || "" : "The start time is not announced yet — this is an all-day placeholder.") +
      "\n" + pageUrl("view=brackets&event=" + encodeURIComponent(m.event));
    lines.push("SUMMARY:" + icsEsc(sum), "DESCRIPTION:" + icsEsc(desc));
    if (ev && ev.location) lines.push("LOCATION:" + icsEsc(ev.location));
    const stream = (m.streams || [])[0];
    if (stream) lines.push("URL:" + stream.url);
    lines.push("END:VEVENT");
    return lines.join("\r\n");
  };
  const downloadIcs = (ms, name) => {
    const dated = ms.filter((m) => m.startsAt || m.date);
    if (!dated.length) { P.toast("None of these series has a date yet.", "info"); return; }
    const text = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//OWCS Comp Tracker//Schedule//EN", "CALSCALE:GREGORIAN",
      "X-WR-CALNAME:" + icsEsc(name)].concat(dated.map(vevent)).concat(["END:VCALENDAR"]).join("\r\n") + "\r\n";
    P.download(name.replace(/[^\w.-]+/g, "-").toLowerCase() + ".ics", text, "text/calendar");
    P.toast("Calendar file saved: " + plural(dated.length, "series") + ". Open it to add them to your calendar.");
  };

  /* --------------------------------------------------------- pieces */
  const followBtn = (key, name) => key ? '<button type="button" class="sx-star" data-follow="' + esc(key) + '" aria-pressed="' +
    (follows.has(key) ? "true" : "false") + '" title="' + esc((follows.has(key) ? "Unfollow " : "Follow ") + name) +
    '" aria-label="' + esc((follows.has(key) ? "Unfollow " : "Follow ") + name) + '">' +
    '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path d="M12 3.2l2.6 5.6 6.1.7-4.5 4.2 1.2 6-5.4-3.1-5.4 3.1 1.2-6L3.3 9.5l6.1-.7z"/></svg></button>' : "";

  /* One series as a row: time · teams · context · actions. */
  const rowHtml = (m, opt) => {
    opt = opt || {};
    const ev = EV.get(m.event);
    const st = status(m);
    const done = st === "done";
    const winA = done && m.winner && m.winner === m.teamA, winB = done && m.winner && m.winner === m.teamB;
    const side = (s, win) => {
      const nm = teamName(m, s);
      const k = teamKey(m, s);
      return '<span class="sx-team' + (win ? " is-win" : "") + (nm ? "" : " is-tbd") + (done && !win && m.winner ? " is-loss" : "") +
        '"' + (k ? ' data-team="' + esc(k) + '"' : "") + ">" +
        '<span class="sx-team__mark">' + mark(m, s, 28) + "</span>" +
        '<span class="sx-team__name">' + esc(nm || "TBD") + "</span>" +
        (opt.follow !== false && nm ? followBtn(k, nm) : "") + "</span>";
    };
    const mid = done
      ? P.scorePlate(m.scoreA, m.scoreB, winA ? "a" : winB ? "b" : null)
      : '<span class="sx-vs">vs</span>';
    const tbdBoth = !done && !teamName(m, "A") && !teamName(m, "B");
    const actions = [];
    if (m.series) actions.push('<a class="btn btn--sm btn--ghost" href="series.html?id=' + encodeURIComponent(m.series) + '">Details</a>');
    (m.streams || []).slice(0, 1).forEach((s) => actions.push('<a class="btn btn--sm btn--ghost" href="' + esc(s.url) +
      '" target="_blank" rel="noopener">Watch ↗</a>'));
    if (!done && (m.startsAt || m.date)) actions.push('<button type="button" class="btn btn--sm btn--quiet" data-ics="' + esc(m.id) +
      '" title="Save this series to your calendar">+ Calendar</button>');
    return '<li class="sx-row' + (followed(m) ? " is-followed" : "") + '" data-status="' + st + '" data-id="' + esc(m.id) + '">' +
      '<div class="sx-row__when">' + whenHtml(m, opt) + "</div>" +
      (tbdBoth ? '<div class="sx-row__teams sx-row__teams--tbd"><span class="sx-tbd-mark" aria-hidden="true">?</span>' +
        '<span>Teams to be decided</span></div>'
        : '<div class="sx-row__teams">' + side("A", winA) + mid + side("B", winB) + "</div>") +
      '<div class="sx-row__ctx">' + (opt.event !== false ? regionChip(ev && ev.region) +
        '<a class="sx-evlink" href="series.html?view=brackets&amp;event=' + encodeURIComponent(m.event) + '">' + esc(evName(ev)) + "</a>" : "") +
      '<span class="sx-round">' + esc(roundLabel(m)) + "</span>" + boChip(m) + stateChip(m) +
      (m.note ? '<span class="dim small" title="' + esc(m.note) + '">ⓘ</span>' : "") + "</div>" +
      '<div class="sx-row__act">' + actions.join("") + "</div></li>";
  };

  const dayHead = (iso, n) => '<div class="sx-day__head"><h3>' + esc(fDay(iso)) + '</h3><span class="sx-rel" data-n="' +
    dayDiff(iso) + '">' + esc(rel(iso)) + "</span>" + (n ? '<span class="dim small">' + plural(n, "series") + "</span>" : "") + "</div>";

  /* Matches grouped by local day, in order. */
  const byDay = (ms) => {
    const g = new Map();
    ms.forEach((m) => {
      const k = dayKey(m) || "tba";
      if (!g.has(k)) g.set(k, []);
      g.get(k).push(m);
    });
    g.forEach((list) => list.sort(matchOrder));
    return g;
  };
  const matchOrder = (a, b) => (a.startsAt || "~").localeCompare(b.startsAt || "~") ||
    (a.event > b.event ? 1 : a.event < b.event ? -1 : 0) ||
    ((a.week || 0) - (b.week || 0)) || ((a.day || 0) - (b.day || 0)) || ((a.game || 0) - (b.game || 0)) ||
    ((a.nth || 0) - (b.nth || 0));

  /* ------------------------------------------------------- filters */
  const REGION_ORDER = ["na", "emea", "korea", "japan", "china", "pacific", "asia", "international"];
  const filterBar = (state, opt) => {
    const regions = REGION_ORDER.filter((r) => (S.events || []).some((e) => e.region === r));
    return '<div class="sx-filters">' +
      '<div class="seg" role="group" aria-label="Region">' +
      [["", "All regions"]].concat(regions.map((r) => [r, regionName(r)])).map((r) =>
        '<button type="button" data-region="' + r[0] + '" aria-pressed="' + (state.region === r[0] ? "true" : "false") + '">' +
        esc(r[1]) + "</button>").join("") + "</div>" +
      (opt && opt.follow ? '<label class="switch sx-follow-only"><input type="checkbox" data-mine' + (state.mine ? " checked" : "") +
        '><span class="switch__track"></span><span>Only teams I follow' +
        ' <span class="dim small" data-follow-count>(' + follows.size + ")</span></span></label>" : "") +
      "</div>";
  };
  const regionOf = (m) => { const e = EV.get(m.event); return e ? e.region || "" : ""; };
  const passes = (m, state) => (!state.region || regionOf(m) === state.region) && (!state.mine || followed(m));

  const bindCommon = (host, rerender, state) => {
    host.addEventListener("click", (e) => {
      const t = e.target.closest ? e.target : null;
      if (!t) return;
      const reg = t.closest("[data-region]");
      if (reg && reg.tagName === "BUTTON") {
        state.region = reg.dataset.region;
        P.setQs({ region: state.region || null });
        rerender();
        return;
      }
      const star = t.closest("[data-follow]");
      if (star) {
        const k = star.dataset.follow;
        if (follows.has(k)) follows.delete(k); else follows.add(k);
        saveFollows();
        const on = follows.has(k);
        P.$$('[data-follow="' + (window.CSS && CSS.escape ? CSS.escape(k) : k) + '"]').forEach((b) => {
          b.setAttribute("aria-pressed", on ? "true" : "false");
          b.title = b.getAttribute("aria-label").replace(/^(Un)?[Ff]ollow/, on ? "Unfollow" : "Follow");
          b.setAttribute("aria-label", b.title);
        });
        P.$$("[data-follow-count]").forEach((c) => { c.textContent = "(" + follows.size + ")"; });
        P.toast(on ? "Following — their series are marked, and “Only teams I follow” shows just them."
          : "No longer following.");
        if (state.mine) rerender();
        else P.$$(".sx-row").forEach((r) => { const m = M.get(r.dataset.id); if (m) r.classList.toggle("is-followed", followed(m)); });
        return;
      }
      const ics = t.closest("[data-ics]");
      if (ics) {
        const m = M.get(ics.dataset.ics);
        if (m) downloadIcs([m], (teamName(m, "A") || "TBD") + " vs " + (teamName(m, "B") || "TBD"));
        return;
      }
      const all = t.closest("[data-ics-all]");
      if (all && host._icsAll) { downloadIcs(host._icsAll(all.dataset.icsAll), all.dataset.name || "OWCS schedule"); }
    });
    host.addEventListener("change", (e) => {
      if (e.target.matches && e.target.matches("[data-mine]")) {
        state.mine = e.target.checked;
        P.setQs({ mine: state.mine ? "1" : null });
        rerender();
      }
    });
    /* Hover a team: light it up everywhere on screen (bracket and lists). */
    const lit = (k, on) => P.$$('[data-team="' + (window.CSS && CSS.escape ? CSS.escape(k) : k) + '"]', host)
      .forEach((x) => x.classList.toggle("is-lit", on));
    host.addEventListener("mouseover", (e) => {
      const t = e.target.closest && e.target.closest("[data-team]");
      if (t && t !== host._lit) { if (host._lit) lit(host._lit.dataset.team, false); host._lit = t; lit(t.dataset.team, true); }
    });
    host.addEventListener("mouseout", (e) => {
      const t = e.target.closest && e.target.closest("[data-team]");
      if (t && (!e.relatedTarget || !t.contains(e.relatedTarget))) { lit(t.dataset.team, false); host._lit = null; }
    });
  };

  /* ---------------------------------------------- event helpers */
  const evMatches = (e) => (S.matches || []).filter((m) => m.event === e.id);
  const evStatus = (e) => {
    const t = today();
    const open = e.counts.scheduled;
    const [a, b] = e.window || [];
    if (!open && e.counts.matches) return "done";
    if (!e.counts.matches && b && b < t) return "done";
    if (a && a <= t && (!b || b >= t)) return "live";
    if (a && a > t) return "next";
    if (open && b && b < t) return "awaiting";
    return a ? "next" : "tba";
  };
  const EV_STATUS = { done: ["Finished", "evidence"], live: ["On now", "working"], next: ["Coming up", "queued"],
    tba: ["Dates TBA", "queued"], awaiting: ["Awaiting results", "review"] };
  const evChip = (e) => {
    const s = EV_STATUS[evStatus(e)];
    return '<span class="chip sx-evchip" data-state="' + s[1] + '"><span class="dot' + (evStatus(e) === "live" ? " pulse" : "") +
      '"></span>' + esc(s[0]) + "</span>";
  };
  const champHtml = (e) => e.champion ? '<span class="sx-champ" title="Won the event">' +
    '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M7 4h10v3a5 5 0 0 1-10 0zM5 5H3v1a4 4 0 0 0 4 4M19 5h2v1a4 4 0 0 1-4 4M12 12v4m-4 4h8m-6 0l1-4h2l1 4"/></svg>' +
    P.anyTeamPlate(e.champion, { size: "sm" }) + "</span>" : "";
  const nextPhaseLine = (e) => {
    const ph = e.phases || [];
    const done = ph.filter((p) => p.done), open = ph.filter((p) => !p.done);
    if (!ph.length) return e.format || "Bracket to be drawn once the regional playoffs finish.";
    if (!open.length) return ph.map((p) => p.name).join(" · ") + " · finished";
    return (done.length ? done.map((p) => p.name).join(", ") + " done · " : "") + open[0].name + " next";
  };

  /* ================================================== UPCOMING */
  function mountUpcoming(host) {
    if (!host) return;
    if (!S) { host.innerHTML = P.note("info", "The schedule is not loaded", "<p>assets/data/schedule.v1.js is missing.</p>"); return; }
    const state = { region: P.qs("region") || "", mine: P.qs("mine") === "1" };
    let timer = null;
    const render = () => {
      const all = (S.matches || []).filter(isOpen);
      const open = all.filter((m) => passes(m, state));
      const dated = open.filter((m) => dayKey(m));
      const live = dated.filter((m) => status(m) === "live");
      const awaiting = dated.filter((m) => status(m) === "awaiting");
      const ahead = dated.filter((m) => ["today", "upcoming", "live"].indexOf(status(m)) >= 0);
      const undated = open.filter((m) => !dayKey(m));
      const days = byDay(ahead);
      const keys = Array.from(days.keys()).sort();
      const events = (S.events || []).filter((e) => (!state.region || e.region === state.region) &&
        ["live", "next", "tba", "awaiting"].indexOf(evStatus(e)) >= 0)
        .sort((a, b) => ((a.window || [])[0] || "9999").localeCompare((b.window || [])[0] || "9999"));
      host._icsAll = (which) => which === "ahead" ? ahead : which === "followed" ? ahead.filter(followed)
        : ahead.filter((m) => m.event === which);

      let html = filterBar(state, { follow: true });

      /* The spotlight: the next match day, as one glance. */
      /* Only a series that is still AHEAD (or on now) can be "next up". A
         series whose day has passed with no result recorded used to win this
         slot, and the page then read "Next up · 4 days ago · First series in
         starting". When nothing dated is ahead, the same card says what is
         true instead: the result is not in yet. */
      const isAhead = (m) => isOpen(m) && dayKey(m) && ["today", "upcoming", "live"].indexOf(status(m)) >= 0;
      const isPending = (m) => isOpen(m) && dayKey(m) && status(m) === "awaiting";
      const nextEv = events.find((e) => evStatus(e) !== "tba" && evMatches(e).some(isAhead));
      const pendingEv = nextEv ? null : events.find((e) => evMatches(e).some(isPending));
      if (nextEv) html += spotlight(nextEv, isAhead, false);
      else if (pendingEv) html += spotlight(pendingEv, isPending, true);

      if (events.length) {
        html += '<section class="u-mt-6" aria-labelledby="sx-on"><div class="sec-head"><h2 id="sx-on">Events still to play</h2>' +
          '<span class="dim small">' + plural(events.length, "event") + "</span></div>" +
          '<div class="sx-evgrid">' + events.map(eventCard).join("") + "</div></section>";
      }

      html += '<section class="u-mt-7" aria-labelledby="sx-agenda"><div class="sec-head"><h2 id="sx-agenda">Day by day</h2>' +
        '<span class="row">' + (ahead.length ? '<button type="button" class="btn btn--sm btn--ghost" data-ics-all="ahead" data-name="OWCS upcoming">' +
          "+ Add all " + ahead.length + " to calendar</button>" : "") + "</span></div>" +
        '<p class="dim small sx-tznote">Times are in your time zone' + (ZONE ? " (<b>" + esc(ZONE.replace(/_/g, " ")) + "</b>)" : "") +
        ". A series whose start time is not announced yet shows <b>TBA</b> — its day comes from the published schedule.</p>";
      if (live.length) {
        html += '<div class="sx-day sx-day--live"><div class="sx-day__head"><h3>Live now</h3></div><ul class="sx-list">' +
          live.map((m) => rowHtml(m)).join("") + "</ul></div>";
      }
      if (!keys.length && !live.length) {
        html += P.empty("◷", state.mine ? "None of the teams you follow has a dated series ahead"
          : "No dated series ahead" + (state.region ? " in " + regionName(state.region) : ""),
          state.mine ? "Follow teams with the ☆ beside their name, or turn off “Only teams I follow”."
            : "The next stage's days are not published yet. The Schedule tab has the season's remaining events.");
      }
      keys.forEach((k) => {
        const list = days.get(k).filter((m) => status(m) !== "live");
        if (!list.length) return;
        html += '<div class="sx-day">' + dayHead(k, list.length) + '<ul class="sx-list">' + list.map((m) => rowHtml(m)).join("") + "</ul></div>";
      });
      if (awaiting.length) {
        html += '<details class="sx-day sx-day--await card u-mt-5"><summary><b>Awaiting results</b> <span class="dim small">' +
          plural(awaiting.length, "series") + " whose day has passed; the spreadsheet has not recorded them yet</span></summary>" +
          '<ul class="sx-list">' + awaiting.sort(matchOrder).map((m) => rowHtml(m, { day: true })).join("") + "</ul></details>";
      }
      if (undated.length) html += undatedBlock(undated);
      html += recentBlock(state);
      html += sourcesNote();
      host.innerHTML = html;
      tick();
    };
    /* Countdown and live chips stay current without a reload. */
    const tick = () => {
      P.$$("[data-countdown]", host).forEach((el) => { el.textContent = countdown(el.dataset.countdown); });
      clearTimeout(timer);
      timer = setTimeout(tick, 30000);
    };
    bindCommon(host, render, state);
    render();
  }

  const countdown = (iso) => {
    const ms = new Date(iso).getTime() - Date.now();
    if (ms <= 0) return "under way";
    const d = Math.floor(ms / 864e5), h = Math.floor(ms / 36e5) % 24, mi = Math.floor(ms / 6e4) % 60;
    return "in " + (d ? d + "d " + h + "h" : h ? h + "h " + mi + "m" : mi + "m");
  };

  function spotlight(e, pick, pending) {
    const ms = evMatches(e).filter(pick).sort(matchOrder);
    const first = ms[0];
    const day = dayKey(first);
    const timed = ms.find((m) => m.startsAt);
    const n = dayDiff(day);
    const lead = pending ? "Was due " + rel(day).replace(/^(Today|Yesterday)$/, (w) => w.toLowerCase())
      : n === 0 ? "Today" : n === 1 ? "Tomorrow" : rel(day).replace(/^in /, "In ");
    const days = Array.from(new Set(ms.map(dayKey))).sort();
    const rounds = [];
    ms.forEach((m) => { const r = roundLabel(m); if (rounds.indexOf(r) < 0) rounds.push(r); });
    const known = Array.from(new Set(ms.reduce((a, m) => a.concat([teamKey(m, "A"), teamKey(m, "B")]), []).filter(Boolean)));
    /* Context while the slots read TBD: how the stage's league finished. */
    const league = (e.phases || []).filter((p) => p.kind === "league" && p.standings && p.standings.length).pop();
    const table = !known.length && league ? '<div class="sx-spot__table"><p class="label">' + esc(league.name) + " — final standings</p><ol>" +
      league.standings.slice(0, 6).map((r) => "<li>" + P.anyTeamPlate(r.team, { size: "sm" }) + '<span class="mono small">' +
        r.w + "–" + r.l + "</span></li>").join("") + "</ol></div>" : "";
    return '<section class="sx-spot card" aria-labelledby="sx-spot-h">' +
      '<div class="sx-spot__main"><p class="eyebrow">' + (pending ? "Result pending" : "Next up") + " · " + esc(regionName(e.region)) + "</p>" +
      '<h2 id="sx-spot-h">' + esc(evName(e)) + " · " + esc(ms.some((m) => m.kind === "bracket") ? "Playoffs" : rounds[0] || "") + "</h2>" +
      '<p class="sx-spot__when"><b>' + esc(lead) + "</b> · " + esc(fDay(day)) +
      (days.length > 1 ? " – " + esc(fDay(days[days.length - 1])) : "") + "</p>" +
      (pending ? '<p class="sx-spot__pending"><span class="chip" data-state="review"><span class="dot"></span>Awaiting result</span> ' +
        "The day has passed and the community spreadsheet has not recorded a score yet. Nothing is shown as a result until it does." +
        (timed ? " It was scheduled for " + esc(fTime(new Date(timed.startsAt))) + " " + esc(tzShort(new Date(timed.startsAt))) + "." : "") + "</p>"
        : timed ? '<p class="sx-spot__count">' + "First series " +
        '<b data-countdown="' + esc(timed.startsAt) + '">' + esc(countdown(timed.startsAt)) +
        "</b> · " + esc(fTime(new Date(timed.startsAt))) + " " + esc(tzShort(new Date(timed.startsAt))) + "</p>"
        : '<p class="dim small">Start times are not announced yet; the days are from the published schedule.</p>') +
      '<ul class="sx-spot__facts">' +
      (e.location ? "<li><b>Where</b>" + esc(e.location) + "</li>" : "") +
      "<li><b>Series</b>" + plural(ms.length, "series") + " · " + esc(rounds.slice(0, 4).join(", ")) + "</li>" +
      (e.format ? "<li><b>Format</b>" + esc(e.format) + "</li>" : "") +
      "<li><b>Teams</b>" + (known.length ? known.length + " decided" + (ms.some((m) => !teamKey(m, "A") || !teamKey(m, "B")) ? ", the rest decided by the bracket" : "")
        : "Set by the bracket — the slots fill in as the editors and Liquipedia list them") + "</li></ul>" +
      '<div class="row u-mt-4"><a class="btn btn--primary" href="series.html?view=brackets&amp;event=' + encodeURIComponent(e.id) +
      '">See the bracket</a>' + (pending ? "" : '<button type="button" class="btn btn--ghost" data-ics-all="' + esc(e.id) + '" data-name="' + esc(evName(e)) +
      '">+ Add to calendar</button>') + "</div></div>" + table + "</section>";
  }

  function eventCard(e) {
    const ms = evMatches(e);
    const open = ms.filter(isOpen);
    const [a, b] = e.window || [];
    const when = a ? range(a, b) : "Dates TBA";
    const n = a ? dayDiff(a) : null;
    const relTxt = a ? (n > 0 ? rel(a) : b && dayDiff(b) >= 0 ? "on now" : "") : "";
    return '<article class="sx-ev card card--hoverable">' +
      '<div class="sx-ev__top">' + regionChip(e.region) + evChip(e) + "</div>" +
      '<h3><a class="sx-ev__link" href="series.html?view=brackets&amp;event=' + encodeURIComponent(e.id) + '">' + esc(evName(e)) + "</a></h3>" +
      '<p class="sx-ev__when"><b>' + esc(when) + "</b>" + (relTxt ? ' <span class="dim">· ' + esc(relTxt) + "</span>" : "") + "</p>" +
      (e.location ? '<p class="dim small">' + esc(e.location) + "</p>" : "") +
      '<p class="small u-mt-3">' + esc(nextPhaseLine(e)) + "</p>" +
      '<p class="dim small">' + (ms.length ? plural(open.length, "series") + " to play" + (e.counts.tbd ? " · " + e.counts.tbd + " with teams TBD" : "")
        : "Bracket not drawn yet") + "</p></article>";
  }

  function undatedBlock(undated) {
    const byEv = new Map();
    undated.forEach((m) => { if (!byEv.has(m.event)) byEv.set(m.event, []); byEv.get(m.event).push(m); });
    return '<section class="u-mt-6"><div class="sec-head"><h2>Dates not announced yet</h2><span class="dim small">' +
      plural(undated.length, "series") + "</span></div>" +
      Array.from(byEv.entries()).map(([id, ms]) => {
        const e = EV.get(id);
        const groups = new Map();
        ms.forEach((m) => { const k = roundLabel(m).replace(/ · Week \d+$/, "") + (m.bestOf ? " (Bo" + m.bestOf + ")" : ""); groups.set(k, (groups.get(k) || 0) + 1); });
        return '<details class="card sx-undated"><summary><span class="row">' + regionChip(e && e.region) + "<b>" + esc(evName(e)) + "</b>" +
          '<span class="dim small">' + Array.from(groups.entries()).map(([k, n]) => (n > 1 ? n + "× " : "") + k).join(" · ") +
          "</span></span></summary>" + (e && e.source ? '<p class="dim small u-mt-3">' + esc(e.source) + "</p>" : "") +
          '<ul class="sx-list u-mt-3">' + ms.sort(matchOrder).map((m) => rowHtml(m, { event: false })).join("") + "</ul></details>";
      }).join("") + "</section>";
  }

  function recentBlock(state) {
    const done = (S.matches || []).filter((m) => m.state === "done" && m.date && passes(m, state) && dayDiff(m.date) <= 0)
      .sort((a, b) => b.date.localeCompare(a.date) || matchOrder(b, a));
    if (!done.length) return "";
    const days = Array.from(new Set(done.map((m) => m.date))).slice(0, 2);
    const list = done.filter((m) => days.indexOf(m.date) >= 0).slice(0, 10);
    days.splice(0, days.length, ...Array.from(new Set(list.map((m) => m.date))));
    return '<section class="u-mt-7" aria-labelledby="sx-recent"><div class="sec-head"><h2 id="sx-recent">Latest results</h2>' +
      '<a class="sec-link" href="series.html?view=results">Every result →</a></div>' +
      days.map((d) => '<div class="sx-day">' + dayHead(d) + '<ul class="sx-list">' +
        list.filter((m) => m.date === d).map((m) => rowHtml(m)).join("") + "</ul></div>").join("") +
      '<p class="dim small">Results from the community spreadsheet. Dates of played series are placed from the stage\'s ' +
      "published weeks, so a day can be off by one where a week's days are not published.</p></section>";
  }

  function sourcesNote() {
    const lp = (S.sources || []).find((s) => s.id === "liquipedia");
    return '<details class="about-data card u-mt-6"><summary><span class="about-data__title">Where the schedule comes from</span>' +
      '<span class="dim small">and how sure each date is</span></summary><div class="about-data__body">' +
      "<ul class=\"small\">" +
      "<li><b>Which series, in which order, and every result:</b> the community OWCS spreadsheet. A series its editors have " +
      "laid out but not played yet is a slot here — its round and format are known, its teams read TBD until they are decided.</li>" +
      "<li><b>Which day:</b> the published stage schedules (Blizzard's viewers' guides, Liquipedia, esports.gg, owtics.gg, " +
      "Strafe). Where only a stage's weeks are published, a day is <i>estimated</i> and says so.</li>" +
      "<li><b>What time:</b> " + (lp ? '<a href="' + esc(lp.url) + '" target="_blank" rel="noopener">Liquipedia</a> (' + esc(lp.license) +
        "), read " + esc(P.fmtRel ? P.fmtRel(lp.fetchedAt) : lp.fetchedAt) + ", and the official schedule where a person entered it."
        : "Liquipedia, read twice a day by a scheduled job, and the official schedule where a person entered it. Until a time is " +
          "listed, a series shows its day and <b>TBA</b>.") + "</li>" +
      "<li>Every time is converted to your own time zone. Calendar files carry the real start time when one is known, and an " +
      "all-day placeholder when it is not.</li></ul></div></details>";
  }

  /* ================================================== SCHEDULE */
  function mountSchedule(host) {
    if (!host) return;
    if (!S) { host.innerHTML = ""; return; }
    const state = { region: P.qs("region") || "" };
    const render = () => {
      const evs = (S.events || []).filter((e) => !state.region || e.region === state.region);
      let html = filterBar(state) + timeline(evs);
      const months = new Map();
      evs.forEach((e) => {
        const a = (e.window || [])[0];
        const k = a ? a.slice(0, 7) : "tba";
        if (!months.has(k)) months.set(k, []);
        months.get(k).push(e);
      });
      Array.from(months.keys()).sort().forEach((k) => {
        const label = k === "tba" ? "Dates to be announced" : (F_MONTH ? F_MONTH.format(dateOf(k + "-01")) : k);
        const cur = k !== "tba" && k === today().slice(0, 7);
        html += '<section class="sx-month' + (cur ? " is-current" : "") + '"><h2 class="sx-month__h">' + esc(label) +
          (cur ? ' <span class="dim small">· this month</span>' : "") + "</h2>" +
          '<div class="stack">' + months.get(k).map(eventRow).join("") + "</div></section>";
      });
      host.innerHTML = html;
      const now = host.querySelector(".sx-tl__scroll");
      const mark = host.querySelector(".sx-tl__today");
      if (now && mark) now.scrollLeft = Math.max(0, mark.offsetLeft - now.clientWidth * 0.6);
    };
    host.addEventListener("toggle", (e) => {
      const d = e.target;
      if (d.matches && d.matches("details[data-ev]") && d.open && !d.dataset.filled) {
        d.dataset.filled = "1";
        const ms = evMatches(EV.get(d.dataset.ev));
        const days = byDay(ms);
        const keys = Array.from(days.keys()).sort();
        d.querySelector(".sx-evrow__days").innerHTML = keys.length ? keys.map((k) =>
          '<div class="sx-day">' + (k === "tba" ? '<div class="sx-day__head"><h3>Date not announced</h3></div>' : dayHead(k, days.get(k).length)) +
          '<ul class="sx-list">' + days.get(k).map((m) => rowHtml(m, { event: false })).join("") + "</ul></div>").join("")
          : '<p class="dim small">No series listed for this event yet. Its bracket fills in as the regional playoffs finish.</p>';
      }
    }, true);
    bindCommon(host, render, state);
    render();
  }

  function eventRow(e) {
    const st = evStatus(e);
    const [a, b] = e.window || [];
    return '<details class="sx-evrow card" data-ev="' + esc(e.id) + '" data-status="' + st + '">' +
      '<summary><div class="sx-evrow__when"><b>' + esc(range(a, b)) + "</b>" + (a && st === "next" ? '<span class="dim small">' + esc(rel(a)) + "</span>"
        : st === "live" && b ? '<span class="dim small">' + (dayDiff(b) === 0 ? "ends today" : "ends " + esc(rel(b))) + "</span>" : "") + "</div>" +
      '<div class="sx-evrow__name">' + regionChip(e.region) + "<b>" + esc(evName(e)) + "</b>" +
      (e.location ? '<span class="dim small">' + esc(e.location) + "</span>" : "") + "</div>" +
      '<div class="sx-evrow__state">' + evChip(e) + champHtml(e) + "</div>" +
      '<div class="sx-evrow__count dim small">' + (e.counts.matches ? e.counts.done + "/" + e.counts.matches + " played" : "—") + "</div></summary>" +
      '<div class="sx-evrow__body"><p class="small">' + esc(nextPhaseLine(e)) + (e.format ? " — " + esc(e.format) : "") + "</p>" +
      '<div class="row u-mt-3"><a class="btn btn--sm btn--ghost" href="series.html?view=brackets&amp;event=' + encodeURIComponent(e.id) + '">Bracket &amp; standings</a>' +
      (e.counts.done ? '<a class="btn btn--sm btn--quiet" href="series.html?view=results' + (e.region ? "&amp;region=" + encodeURIComponent(e.region) : "") +
        (e.stage ? "&amp;stage=" + encodeURIComponent(e.stage) : "") + '">Results</a>' : "") +
      (e.counts.scheduled ? '<button type="button" class="btn btn--sm btn--quiet" data-ics-all="' + esc(e.id) + '" data-name="' + esc(evName(e)) + '">+ Calendar</button>' : "") +
      "</div>" + (e.source ? '<p class="dim small u-mt-3">Dates: ' + esc(e.source) + "</p>" : "") +
      '<div class="sx-evrow__days u-mt-4"></div></div></details>';
  }

  /* The season at a glance: one lane per region, one bar per event. */
  function timeline(evs) {
    const dated = evs.filter((e) => (e.window || [])[0]);
    if (!dated.length) return "";
    const y = Math.min.apply(null, dated.map((e) => +e.window[0].slice(0, 4)));
    const start = dated.map((e) => e.window[0]).sort()[0].slice(0, 7) + "-01";
    const endIso = dated.map((e) => e.window[1] || e.window[0]).sort().pop();
    const end = new Date(dateOf(endIso).getFullYear(), dateOf(endIso).getMonth() + 1, 1);
    const t0 = dateOf(start).getTime(), span = end.getTime() - t0;
    const x = (iso) => ((dateOf(iso).getTime() - t0) / span * 100).toFixed(3) + "%";
    const w = (a, b) => Math.max(0.9, ((dateOf(b || a).getTime() + 864e5 - dateOf(a).getTime()) / span * 100)).toFixed(3) + "%";
    const lanes = REGION_ORDER.concat([null]).filter((r) => dated.some((e) => (e.region || null) === r));
    const months = [];
    for (let d = dateOf(start); d < end; d = new Date(d.getFullYear(), d.getMonth() + 1, 1)) months.push(isoLocal(d));
    const t = today();
    const todayX = t >= start && dateOf(t) < end ? x(t) : null;
    return '<section class="sx-tl card" aria-label="The season at a glance"><div class="sx-tl__scroll" data-lenis-prevent>' +
      '<div class="sx-tl__inner"><div class="sx-tl__months">' + months.map((m) => '<span style="left:' + x(m) + '">' +
        esc(F_MON ? new Intl.DateTimeFormat(undefined, { month: "short" }).format(dateOf(m)) : m.slice(5, 7)) + "</span>").join("") + "</div>" +
      lanes.map((r) => '<div class="sx-tl__lane"><span class="sx-tl__lab">' + esc(regionName(r)) + "</span>" +
        '<div class="sx-tl__track">' + dated.filter((e) => (e.region || null) === r).map((e) =>
          '<a class="sx-tl__bar" data-status="' + evStatus(e) + '" style="left:' + x(e.window[0]) + ";width:" + w(e.window[0], e.window[1]) +
          '" href="series.html?view=brackets&amp;event=' + encodeURIComponent(e.id) + '" title="' + esc(evName(e) + " · " + range(e.window[0], e.window[1])) +
          '"><span>' + esc(STAGE_NAME[e.stage] || evName(e)) + "</span></a>").join("") + "</div></div>").join("") +
      (todayX ? '<span class="sx-tl__today" style="left:calc(var(--lab) + (100% - var(--lab)) * ' + parseFloat(todayX) / 100 + ')"><span>Today</span></span>' : "") +
      "</div></div>" +
      '<p class="sx-tl__key dim small"><span data-k="done"></span>Finished <span data-k="live"></span>On now <span data-k="next"></span>Coming up' +
      " · " + y + " season · tap an event for its bracket</p></section>";
  }

  /* ================================================== BRACKETS */
  function defaultEvent() {
    const evs = S.events || [];
    const want = P.qs("event");
    if (want && EV.has(want)) return want;
    const open = evs.filter((e) => ["live", "next", "awaiting"].indexOf(evStatus(e)) >= 0 && e.phases.some((p) => p.kind === "bracket"))
      .sort((a, b) => (a.window[0] || "9999").localeCompare(b.window[0] || "9999"));
    if (open.length) return open[0].id;
    const done = evs.filter((e) => evStatus(e) === "done" && e.phases.length);
    return (done.pop() || evs[0] || {}).id;
  }

  function mountBrackets(host) {
    if (!host) return;
    if (!S) { host.innerHTML = ""; return; }
    let cur = defaultEvent();
    let phase = P.qs("phase") || null;
    const groups = new Map();
    (S.events || []).forEach((e) => {
      const k = STAGE_NAME[e.stage] || "Other";
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(e);
    });
    const picker = () => '<div class="sx-pick"><label class="field"><span class="label">Event</span>' +
      '<select class="input" data-event>' + Array.from(groups.entries()).map(([k, evs]) => '<optgroup label="' + esc(k) + '">' +
        evs.map((e) => '<option value="' + esc(e.id) + '"' + (e.id === cur ? " selected" : "") + ">" + esc(evName(e)) +
          (evStatus(e) === "live" ? " — on now" : evStatus(e) === "next" ? " — coming up" : "") + "</option>").join("") + "</optgroup>").join("") +
      "</select></label>" +
      '<div class="sx-pick__quick" role="group" aria-label="Events coming up">' + (S.events || []).filter((e) => ["live", "next"].indexOf(evStatus(e)) >= 0)
        .map((e) => '<button type="button" class="chip chip--outline" data-goto="' + esc(e.id) + '" aria-pressed="' + (e.id === cur ? "true" : "false") + '">' +
          esc(evName(e)) + "</button>").join("") + "</div></div>";
    const render = () => {
      const e = EV.get(cur);
      if (!e) { host.innerHTML = picker() + P.empty("◇", "No event selected", ""); return; }
      const phases = e.phases || [];
      const pick = phases.find((p) => p.id === phase) || phases.slice().reverse().find((p) => p.kind === "bracket" && !p.done && p.matches.some((id) => dayKey(M.get(id))))
        || phases.find((p) => p.kind === "bracket" && p.id === "playoffs") || phases[phases.length - 1];
      let html = picker() + '<header class="sx-bhead card"><div><p class="eyebrow">' + esc(regionName(e.region)) + " · " +
        esc(range(e.window[0], e.window[1])) + "</p><h2>" + esc(evName(e)) + "</h2>" +
        (e.location ? '<p class="dim small">' + esc(e.location) + "</p>" : "") +
        (e.format ? '<p class="small u-mt-3">' + esc(e.format) + "</p>" : "") + "</div>" +
        '<div class="sx-bhead__side">' + evChip(e) + (e.champion ? '<div class="sx-bhead__champ"><span class="label">Champion</span>' +
          P.anyTeamPlate(e.champion, {}) + (e.runnerUp ? '<span class="dim small">beat ' + esc(P.anyTeamName(e.runnerUp)) + " in the final</span>" : "") + "</div>" : "") +
        (e.counts.scheduled ? '<button type="button" class="btn btn--sm btn--ghost" data-ics-all="' + esc(e.id) + '" data-name="' + esc(evName(e)) + '">+ Add to calendar</button>' : "") +
        "</div></header>";
      if (!phases.length) {
        html += P.empty("◇", "The bracket is not drawn yet", (e.source ? e.source + ". " : "") +
          "It fills in as the teams qualify — the regional playoffs decide who goes.");
        host.innerHTML = html;
        return;
      }
      host._icsAll = (id) => evMatches(EV.get(id)).filter(isOpen);
      if (phases.length > 1) {
        html += '<div class="seg sx-phases u-mt-5" role="group" aria-label="Phase">' + phases.map((p) =>
          '<button type="button" data-phase="' + esc(p.id) + '" aria-pressed="' + (p === pick ? "true" : "false") + '">' + esc(p.name) +
          (!p.done ? ' <span class="count">' + p.matches.filter((id) => isOpen(M.get(id))).length + " to play</span>" : "") + "</button>").join("") + "</div>";
      }
      html += '<div class="u-mt-5">' + phaseHtml(e, pick) + "</div>";
      html += '<p class="dim small u-mt-5">Hover a team to follow its path. Played series open their full match card; ' +
        "a slot reads TBD until its teams are decided.</p>";
      host.innerHTML = html;
    };
    host.addEventListener("change", (ev) => {
      if (ev.target.matches && ev.target.matches("[data-event]")) { cur = ev.target.value; phase = null; P.setQs({ event: cur, phase: null }); render(); }
    });
    host.addEventListener("click", (ev) => {
      const g = ev.target.closest && ev.target.closest("[data-goto]");
      if (g) { cur = g.dataset.goto; phase = null; P.setQs({ event: cur, phase: null }); render(); return; }
      const p = ev.target.closest && ev.target.closest("[data-phase]");
      if (p) { phase = p.dataset.phase; P.setQs({ phase: phase }); render(); }
    });
    bindCommon(host, render, {});
    render();
  }

  function phaseHtml(e, p) {
    if (!p) return "";
    if (p.kind === "bracket") return bracketHtml(p.bracket);
    if (p.kind === "league") return standingsHtml(p) + '<details class="card u-mt-5"><summary><b>Every ' + esc(p.name.toLowerCase()) +
      " series</b> <span class=\"dim small\">" + plural(p.matches.length, "series") + "</span></summary>" + listHtml(p) + "</details>";
    return listHtml(p);
  }

  const listHtml = (p) => {
    const ms = p.matches.map((id) => M.get(id)).sort(matchOrder);
    const days = byDay(ms);
    return Array.from(days.keys()).sort().map((k) => '<div class="sx-day">' +
      (k === "tba" ? '<div class="sx-day__head"><h3>Date not announced</h3></div>' : dayHead(k, days.get(k).length)) +
      '<ul class="sx-list">' + days.get(k).map((m) => rowHtml(m, { event: false, follow: false })).join("") + "</ul></div>").join("");
  };

  function standingsHtml(p) {
    const st = p.standings || [];
    if (!st.length) return '<p class="dim">No teams are listed for this phase yet.</p>';
    const open = p.matches.some((id) => isOpen(M.get(id)));
    return '<div class="table-wrap"><table class="tbl sx-stand"><caption>Ordered by series won, then map difference. ' +
      "The official tiebreakers (head-to-head, then a tiebreaker match) can order tied teams differently.</caption>" +
      '<thead><tr><th class="num">#</th><th>Team</th><th class="num">Series</th><th class="num">Maps</th><th class="num">Diff</th>' +
      (open ? '<th class="num">To play</th>' : "") + "</tr></thead><tbody>" +
      st.map((r) => '<tr data-team="' + esc(r.team) + '"><td class="num">' + r.rank + "</td><td>" + P.extTeamLink(r.team) + "</td>" +
        '<td class="num"><b>' + r.w + "–" + r.l + '</b></td><td class="num">' + r.mw + "–" + r.ml + '</td><td class="num">' +
        (r.diff > 0 ? "+" : "") + r.diff + "</td>" + (open ? '<td class="num">' + r.left + "</td>" : "") + "</tr>").join("") +
      "</tbody></table></div>";
  }

  /* A series box in the bracket. */
  function boxHtml(id, label) {
    const m = M.get(id);
    if (!m) return "";
    const st = status(m);
    const done = st === "done";
    const team = (s) => {
      const nm = teamName(m, s), k = teamKey(m, s);
      const win = done && m.winner && m.winner === m["team" + s];
      const sc = done ? m["score" + s] : null;
      return '<div class="bx-team' + (win ? " is-win" : "") + (nm ? "" : " is-tbd") + (done && m.winner && !win ? " is-loss" : "") + '"' +
        (k ? ' data-team="' + esc(k) + '"' : "") + '><span class="bx-mark">' + mark(m, s, 20) + "</span>" +
        '<span class="bx-name">' + esc(nm || "TBD") + "</span>" +
        '<span class="bx-score">' + (sc == null ? "" : esc(sc)) + "</span></div>";
    };
    const when = m.startsAt ? fShort(dayKey(m)) + " · " + fTime(new Date(m.startsAt)) : m.date ? fShort(m.date) + (done ? "" : " · TBA") : "Date TBA";
    const inner = '<div class="bx-meta"><span>' + esc(label || "") + (m.bestOf ? " · Bo" + m.bestOf : "") + "</span>" +
      (st === "live" ? '<span class="sx-live"><span class="dot pulse"></span>Live</span>' : '<span>' + esc(when) + "</span>") + "</div>" +
      team("A") + team("B");
    return m.series ? '<a class="bx" data-status="' + st + '" href="series.html?id=' + encodeURIComponent(m.series) + '">' + inner + "</a>"
      : '<div class="bx" data-status="' + st + '">' + inner + "</div>";
  }

  function bracketHtml(b) {
    if (!b) return "";
    const col = (c, i, all, side) => '<div class="bx-col"><h4 class="bx-col__h">' + esc(c.round) + "</h4>" +
      '<div class="bx-col__body">' + c.matches.map((id, j) => '<div class="bx-slot">' + boxHtml(id, short(c.round) + (c.matches.length > 1 ? " " + (j + 1) : "")) + "</div>").join("") +
      "</div></div>";
    const lane = (cols, side, title) => cols && cols.length ? '<div class="bx-lane" data-side="' + side + '">' +
      (title ? '<p class="label bx-lane__h">' + esc(title) + "</p>" : "") +
      '<div class="bx-cols">' + cols.map((c, i) => col(c, i, cols, side)).join("") + "</div></div>" : "";
    const fin = (b.final || []).length ? '<div class="bx-col bx-col--final"><h4 class="bx-col__h">Grand Final</h4><div class="bx-col__body">' +
      b.final.map((id) => '<div class="bx-slot">' + boxHtml(id, "GF") + "</div>").join("") + "</div>" +
      ((b.third || []).length ? '<h4 class="bx-col__h u-mt-5">Third place</h4>' + b.third.map((id) => '<div class="bx-slot">' + boxHtml(id, "3rd") + "</div>").join("") : "") +
      "</div>" : "";
    const lanes = b.type === "double"
      ? lane(b.upper || b.main, "upper", "Upper bracket") + lane(b.lower, "lower", "Lower bracket")
      : lane(b.main || b.upper, "main", "");
    return '<div class="bx-wrap" data-type="' + esc(b.type) + '" data-lenis-prevent><div class="bx-grid">' +
      '<div class="bx-lanes">' + lanes + "</div>" + fin + "</div></div>";
  }
  const short = (r) => r.replace(/^Upper /, "UB ").replace(/^Lower /, "LB ").replace("Quarterfinals", "QF").replace("Semifinals", "SF")
    .replace(/ Final$/, " Final").replace(/^Round (\d)/, "R$1");

  P.sched = { mountUpcoming: mountUpcoming, mountSchedule: mountSchedule, mountBrackets: mountBrackets,
    data: S, status: status, dayKey: dayKey, eventStatus: evStatus,
    /* for the series page: where a played series sits in its event */
    eventOfSeries: (seriesId) => { const m = (S && S.matches || []).find((x) => x.series === seriesId); return m ? EV.get(m.event) : null; },
    /* for the home page: the next dated series */
    next: () => (S ? (S.matches || []).filter((m) => isOpen(m) && dayKey(m) && ["today", "upcoming", "live"].indexOf(status(m)) >= 0).sort(matchOrder) : []),
    ics: downloadIcs };
})();
