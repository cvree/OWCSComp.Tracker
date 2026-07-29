/* =====================================================================
   OWCS Comp Tracker — page-calendar.js

   The season by day. Two independent layers, deliberately kept apart:

     * OFFICIAL EVENTS (D.calendarEvents) — the stage windows from
       config/owcs_calendar.json. These exist whether or not a single match
       has been ingested, and are what makes a month say "EMEA Stage 2 is
       running" instead of "nothing here". Their `verified` flag is carried
       through and shown: the committed seed is unverified, and presenting
       placeholder dates as fact would be a lie.
     * TRACKED MATCHES (D.matches) — real rows, each linking to its match
       page. Nothing is invented; a month with no matches says so.

   Time honesty: a match whose `timeKnown` is false only has a DATE. The
   exporter derives midnight so the match can be placed on a grid, but this
   page must never render 00:00 UTC as though it were a kickoff time — it
   shows "time TBA" instead.

   URL state: ?m=YYYY-MM.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS_PUB;
  if (!P || !P.data) return;
  const D = P.data;
  const esc = P.esc;
  const $ = P.$;

  const NOW = new Date();
  const todayKey = NOW.toISOString().slice(0, 10);

  /* ---- matches ------------------------------------------------------ */
  const matches = (D.matches || []).filter((m) => m.scheduledAt);
  const byDay = new Map();                 // "YYYY-MM-DD" -> [match]
  matches.forEach((m) => {
    const d = new Date(m.scheduledAt);
    if (isNaN(d)) return;
    const key = d.toISOString().slice(0, 10);
    if (!byDay.has(key)) byDay.set(key, []);
    byDay.get(key).push(m);
  });

  const dayKeyOf = (m) => new Date(m.scheduledAt).toISOString().slice(0, 10);

  /* A match's calendar state. `status` from the dataset wins when it is
     decisive (completed/live); otherwise the date decides. */
  function stateOf(m) {
    const st = String(m.status || "").toLowerCase();
    if (st === "live") return "live";
    if (st === "completed" || st === "final" || m.winner) return "past";
    const key = dayKeyOf(m);
    if (key > todayKey) return "upcoming";
    if (key === todayKey) return "today";
    return "past";
  }

  /* ---- official events ---------------------------------------------- */
  const events = (D.calendarEvents || []).filter((e) => e.startDate);
  const covers = (e, key) =>
    key >= e.startDate && key <= (e.endDate || e.startDate);
  const eventsOn = (key) => events.filter((e) => covers(e, key));
  const eventsInMonth = (prefix) => events.filter((e) => {
    const end = e.endDate || e.startDate;
    return e.startDate.slice(0, 7) <= prefix && end.slice(0, 7) >= prefix;
  });

  /* ---- month view state --------------------------------------------- */
  const latest = matches.length
    ? matches.map((m) => m.scheduledAt).sort().slice(-1)[0]
    : NOW.toISOString();
  const parseMonth = (s) => {
    const m = /^(\d{4})-(\d{2})$/.exec(s || "");
    return m ? { y: +m[1], mo: +m[2] - 1 } : null;
  };
  const monthOf = (iso) =>
    ({ y: new Date(iso).getUTCFullYear(), mo: new Date(iso).getUTCMonth() });

  /* Default to the CURRENT month when the season is live around now
     (that is what a schedule is for), else the month of the latest match. */
  function defaultView() {
    const nowPrefix = todayKey.slice(0, 7);
    const hasNow = Array.from(byDay.keys()).some((k) => k.startsWith(nowPrefix))
      || eventsInMonth(nowPrefix).length;
    return hasNow ? monthOf(NOW.toISOString()) : monthOf(latest);
  }
  let view = parseMonth(P.qs().get("m")) || defaultView();

  const MONTHS = ["January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"];
  const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const pad = (n) => String(n).padStart(2, "0");
  const monthPrefix = () => `${view.y}-${pad(view.mo + 1)}`;

  function regionOf(m) {
    const t = P.tournament(m.tournamentId);
    return t ? t.region : "all";
  }

  /* "time TBA" rather than a fabricated 00:00. */
  function timeLabel(m) {
    if (!m.timeKnown) return "time TBA";
    return P.fmtLocal
      ? P.fmtLocal(m.scheduledAt, { hour: "2-digit", minute: "2-digit" })
      : new Date(m.scheduledAt).toISOString().slice(11, 16) + " UTC";
  }

  function evHtml(m) {
    const a = P.team(m.teamA), b = P.team(m.teamB);
    const label = `${a ? a.code : "TBD"} vs ${b ? b.code : "TBD"}`;
    const verified = (P.publicComps((c) => c.matchId === m.id) || []).length;
    const state = stateOf(m);
    return `<a class="cal-ev cal-ev--${esc(state)}" href="match.html?id=${esc(m.id)}"
        style="--rg:var(--rg-${esc(regionOf(m))}, var(--rg-all))"
        title="${esc(label)} — ${esc(timeLabel(m))} — open match page">
      <span class="ce-teams">${esc(label)}</span>
      <span class="ce-meta">${P.chipStatus(m.status)}${verified
        ? `<span class="ev-tick" aria-label="${verified} verified comps">${verified}</span>` : ""}</span>
    </a>`;
  }

  /* ---- official-event bands for the viewed month --------------------- */
  function renderEventBands() {
    const host = $("#cal-events");
    if (!host) return;
    const inMonth = eventsInMonth(monthPrefix());
    if (!inMonth.length) { host.innerHTML = ""; return; }
    host.innerHTML = inMonth.map((e) => {
      const end = e.endDate || e.startDate;
      const running = todayKey >= e.startDate && todayKey <= end;
      const window_ = P.fmtRange ? P.fmtRange(e.startDate, end)
        : `${e.startDate} → ${end}`;
      return `<div class="cal-eband" style="--rg:var(--rg-${esc(e.region)}, var(--rg-all))">
        ${P.badgeRegion ? P.badgeRegion(e.region) : ""}
        <span class="cal-eband__name">${esc(e.name)}</span>
        ${e.stage ? `<span class="cal-eband__win">${esc(e.stage)}</span>` : ""}
        <span class="cal-eband__win">${esc(window_)}</span>
        ${running ? `<span class="ev-tick">running now</span>` : ""}
        ${e.verified
          ? ""
          : `<span class="cal-eband__note" title="These dates come from the committed season seed, not a confirmed official source.">unverified dates — season seed, not an official confirmation</span>`}
      </div>`;
    }).join("");
  }

  /* ---- next up ------------------------------------------------------- */
  function renderNextUp() {
    const wrap = $("#cal-next-up-wrap");
    const host = $("#cal-next-up");
    if (!wrap || !host) return;
    const soon = matches
      .filter((m) => ["upcoming", "today", "live"].includes(stateOf(m)))
      .sort((x, y) => String(x.scheduledAt).localeCompare(String(y.scheduledAt)))
      .slice(0, 5);
    if (!soon.length) { wrap.hidden = true; return; }
    wrap.hidden = false;
    $("#cal-next-count").textContent = `(${soon.length})`;
    host.innerHTML = soon.map((m) => {
      const t = P.tournament(m.tournamentId);
      const d = new Date(m.scheduledAt);
      return `<a class="card card--link card--spot m-card" href="match.html?id=${esc(m.id)}">
        <div class="m-card__meta">
          ${t ? P.badgeRegion(t.region) : ""}${P.chipStatus(m.status)}
          <span class="faint">${esc(P.fmtDate ? P.fmtDate(m.scheduledAt)
            : d.toISOString().slice(0, 10))} · ${esc(timeLabel(m))}</span>
        </div>
        <div class="m-card__row">
          <div class="m-card__teams">
            ${P.teamPlate(m.teamA, { tbd: m.tbdNote })}
            ${P.teamPlate(m.teamB, { tbd: m.tbdNote })}
          </div>
        </div>
      </a>`;
    }).join("");
  }

  /* ---- the grid ------------------------------------------------------ */
  function render() {
    $("#cal-title").textContent = `${MONTHS[view.mo]} ${view.y}`;
    P.setQs({ m: monthPrefix() });

    const first = new Date(Date.UTC(view.y, view.mo, 1));
    const daysIn = new Date(Date.UTC(view.y, view.mo + 1, 0)).getUTCDate();
    const lead = (first.getUTCDay() + 6) % 7;   // Monday-first

    let html = DOW.map((d) =>
      `<div class="cal-dow" role="columnheader">${d}</div>`).join("");
    const cells = [];
    const prevDays = new Date(Date.UTC(view.y, view.mo, 0)).getUTCDate();
    for (let i = lead - 1; i >= 0; i--)
      cells.push({ day: prevDays - i, out: true, key: null });
    for (let d = 1; d <= daysIn; d++)
      cells.push({ day: d, out: false, key: `${monthPrefix()}-${pad(d)}` });
    while (cells.length % 7)
      cells.push({ day: cells.length, out: true, key: null, trail: true });

    let trail = 0;
    html += cells.map((c) => {
      if (c.out) {
        const label = c.trail ? ++trail : c.day;
        return `<div class="cal-cell cal-cell--out" role="gridcell" aria-disabled="true">
          <span class="cal-day">${label}</span></div>`;
      }
      const evs = byDay.get(c.key) || [];
      const on = eventsOn(c.key);
      const cls = [
        c.key === todayKey ? "cal-cell--today" : "",
        on.length ? "cal-cell--inevent" : "",
        c.key < todayKey ? "cal-cell--past" : "",
      ].filter(Boolean).join(" ");
      const rg = on.length ? on[0].region : null;
      const style = rg ? ` style="--rg:var(--rg-${esc(rg)}, var(--rg-all))"` : "";
      const title = on.length
        ? ` title="${esc(on.map((e) => e.name).join(" · "))}"` : "";
      return `<div class="cal-cell ${cls}" role="gridcell"${style}${title}>
        <span class="cal-day">${c.day}</span>
        ${evs.map(evHtml).join("")}
      </div>`;
    }).join("");
    $("#cal-grid").innerHTML = html;

    renderEventBands();

    const prefix = monthPrefix();
    const dayKeys = Array.from(byDay.keys())
      .filter((k) => k.startsWith(prefix)).sort();
    const n = dayKeys.reduce((s, k) => s + byDay.get(k).length, 0);
    const monthEvents = eventsInMonth(prefix);
    $("#cal-summary").textContent = n
      ? `${n} tracked match${n === 1 ? "" : "es"} this month`
      : (monthEvents.length
        ? `${monthEvents.length} official event window${monthEvents.length === 1 ? "" : "s"}, no tracked matches yet`
        : "no tracked matches this month");
    $("#agenda-count").textContent = n ? `(${n})` : "";

    const agenda = $("#cal-agenda"), empty = $("#cal-empty");
    if (!dayKeys.length) {
      agenda.innerHTML = "";
      empty.hidden = false;
      // An empty month INSIDE a stage window means something different
      // from an empty month outside one — say which.
      empty.innerHTML = monthEvents.length
        ? P.emptyState("◷", "Stage running, nothing tracked yet",
          `${esc(monthEvents.map((e) => e.name).join(" · "))} covers this month, `
          + "but no match from it has been ingested yet. Matches appear here "
          + "once a broadcast has been converted — paste a link in the "
          + "control room to start one.")
        : P.emptyState("◷", "No tracked matches this month",
          "Only matches that exist in the dataset appear here — nothing is "
          + "invented. Browse another month or the full "
          + "<a href='matches.html'>match list</a>.");
      P.observeReveals && P.observeReveals(agenda.parentElement);
      return;
    }
    empty.hidden = true;
    agenda.innerHTML = dayKeys.map((k) => {
      const d = new Date(k + "T00:00:00Z");
      const evs = byDay.get(k);
      return `<div class="cal-agenda__day">
        <div class="cal-agenda__date">${DOW[(d.getUTCDay() + 6) % 7]}<b>${d.getUTCDate()}</b>
          ${k === todayKey ? `<span class="cal-day--tba">today</span>` : ""}</div>
        <div class="stack-sm">${evs.map((m) => {
          const verified = (P.publicComps((c) => c.matchId === m.id) || []).length;
          const t = P.tournament(m.tournamentId);
          return `<a class="card card--link card--spot m-card" href="match.html?id=${esc(m.id)}">
            <div class="m-card__meta">
              ${t ? P.badgeRegion(t.region) : ""}${P.chipStatus(m.status)}${P.chipCapture(m.captureStatus)}
              <span class="faint">${esc(t ? t.name : "")}</span>
              <span class="cal-day--tba">${esc(timeLabel(m))}</span>
            </div>
            <div class="m-card__row">
              <div class="m-card__teams">
                ${P.teamPlate(m.teamA, { tbd: m.tbdNote })}
                ${P.teamPlate(m.teamB, { tbd: m.tbdNote })}
              </div>
              <div class="cluster">
                ${P.scorePlate(m.scoreA, m.scoreB,
                  m.winner === m.teamA ? "a" : m.winner === m.teamB ? "b" : null)}
                ${verified ? `<span class="ev-tick">${verified} verified comps</span>` : ""}
              </div>
            </div>
          </a>`;
        }).join("")}</div>
      </div>`;
    }).join("");
    P.observeReveals && P.observeReveals(agenda);
    if (window.OWCSMotion) window.OWCSMotion.observe(agenda);
  }

  function shift(dm) {
    view.mo += dm;
    while (view.mo < 0) { view.mo += 12; view.y -= 1; }
    while (view.mo > 11) { view.mo -= 12; view.y += 1; }
    render();
  }
  $("#cal-prev").addEventListener("click", () => shift(-1));
  $("#cal-next").addEventListener("click", () => shift(1));
  $("#cal-today").addEventListener("click", () => {
    view = monthOf(latest);
    render();
  });
  const nowBtn = $("#cal-now");
  if (nowBtn) nowBtn.addEventListener("click", () => {
    view = monthOf(NOW.toISOString());
    render();
  });

  renderNextUp();
  render();
})();
