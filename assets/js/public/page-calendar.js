/* =====================================================================
   OWCS Comp Tracker — page-calendar.js
   Month grid + agenda, built ONLY from D.matches (real scheduled dates).
   Nothing is invented: months with no tracked matches say so, and every
   event links to its match page. URL state: ?m=YYYY-MM.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS_PUB;
  if (!P || !P.data) return;
  const D = P.data;
  const esc = P.esc;
  const $ = P.$;

  const matches = (D.matches || []).filter((m) => m.scheduledAt);
  const byDay = new Map(); // "YYYY-MM-DD" -> [match]
  matches.forEach((m) => {
    const d = new Date(m.scheduledAt);
    if (isNaN(d)) return;
    const key = d.toISOString().slice(0, 10);
    if (!byDay.has(key)) byDay.set(key, []);
    byDay.get(key).push(m);
  });

  const latest = matches.length
    ? matches.map((m) => m.scheduledAt).sort().slice(-1)[0]
    : new Date().toISOString();
  const parseMonth = (s) => {
    const m = /^(\d{4})-(\d{2})$/.exec(s || "");
    return m ? { y: +m[1], mo: +m[2] - 1 } : null;
  };
  let view = parseMonth(P.qs().get("m")) ||
    { y: new Date(latest).getUTCFullYear(), mo: new Date(latest).getUTCMonth() };

  const MONTHS = ["January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"];
  const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  function regionOf(m) {
    const t = P.tournament(m.tournamentId);
    return t ? t.region : "all";
  }

  function evHtml(m) {
    const a = P.team(m.teamA), b = P.team(m.teamB);
    const label = `${a ? a.code : "TBD"} vs ${b ? b.code : "TBD"}`;
    const verified = (P.publicComps((c) => c.matchId === m.id) || []).length;
    const rg = regionOf(m);
    return `<a class="cal-ev" href="match.html?id=${esc(m.id)}"
        style="--rg:var(--rg-${esc(rg)})"
        title="${esc(label)} — open match page">
      <span class="ce-teams">${esc(label)}</span>
      <span class="ce-meta">${P.chipStatus(m.status)}${verified
        ? `<span class="ev-tick" aria-label="${verified} verified comps">${verified}</span>` : ""}</span>
    </a>`;
  }

  function render() {
    $("#cal-title").textContent = `${MONTHS[view.mo]} ${view.y}`;
    P.setQs({ m: `${view.y}-${String(view.mo + 1).padStart(2, "0")}` });

    const first = new Date(Date.UTC(view.y, view.mo, 1));
    const daysIn = new Date(Date.UTC(view.y, view.mo + 1, 0)).getUTCDate();
    const lead = (first.getUTCDay() + 6) % 7; // Monday-first
    const todayKey = new Date().toISOString().slice(0, 10);

    let html = DOW.map((d) => `<div class="cal-dow" role="columnheader">${d}</div>`).join("");
    const cells = [];
    const prevDays = new Date(Date.UTC(view.y, view.mo, 0)).getUTCDate();
    for (let i = lead - 1; i >= 0; i--)
      cells.push({ day: prevDays - i, out: true, key: null });
    for (let d = 1; d <= daysIn; d++) {
      const key = `${view.y}-${String(view.mo + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
      cells.push({ day: d, out: false, key });
    }
    while (cells.length % 7) cells.push({ day: cells.length, out: true, key: null, trail: true });
    let trail = 0;
    html += cells.map((c) => {
      if (c.out) {
        const label = c.trail ? ++trail : c.day;
        return `<div class="cal-cell cal-cell--out" role="gridcell" aria-disabled="true"><span class="cal-day">${label}</span></div>`;
      }
      const evs = byDay.get(c.key) || [];
      const today = c.key === todayKey ? " cal-cell--today" : "";
      return `<div class="cal-cell${today}" role="gridcell">
        <span class="cal-day">${c.day}</span>
        ${evs.map(evHtml).join("")}
      </div>`;
    }).join("");
    $("#cal-grid").innerHTML = html;

    const monthPrefix = `${view.y}-${String(view.mo + 1).padStart(2, "0")}`;
    const dayKeys = Array.from(byDay.keys()).filter((k) => k.startsWith(monthPrefix)).sort();
    const n = dayKeys.reduce((s, k) => s + byDay.get(k).length, 0);
    $("#cal-summary").textContent = n
      ? `${n} tracked match${n === 1 ? "" : "es"} this month`
      : "no tracked matches this month";
    $("#agenda-count").textContent = n ? `(${n})` : "";

    const agenda = $("#cal-agenda"), empty = $("#cal-empty");
    if (!dayKeys.length) {
      agenda.innerHTML = "";
      empty.hidden = false;
      empty.innerHTML = P.emptyState("◷", "No tracked matches this month",
        "Only matches that exist in the dataset appear here — nothing is invented. Browse another month or the full <a href='matches.html'>match list</a>.");
      P.observeReveals && P.observeReveals(agenda.parentElement);
      return;
    }
    empty.hidden = true;
    agenda.innerHTML = dayKeys.map((k) => {
      const d = new Date(k + "T00:00:00Z");
      const evs = byDay.get(k);
      return `<div class="cal-agenda__day">
        <div class="cal-agenda__date">${DOW[(d.getUTCDay() + 6) % 7]}<b>${d.getUTCDate()}</b></div>
        <div class="stack-sm">${evs.map((m) => {
          const verified = (P.publicComps((c) => c.matchId === m.id) || []).length;
          const t = P.tournament(m.tournamentId);
          return `<a class="card card--link card--spot m-card" href="match.html?id=${esc(m.id)}">
            <div class="m-card__meta">
              ${t ? P.badgeRegion(t.region) : ""}${P.chipStatus(m.status)}${P.chipCapture(m.captureStatus)}
              <span class="faint">${esc(t ? t.name : "")}</span>
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
    view = { y: new Date(latest).getUTCFullYear(), mo: new Date(latest).getUTCMonth() };
    render();
  });

  render();
})();
