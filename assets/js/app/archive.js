/* =====================================================================
   OWCS Comp Tracker — app/archive.js
   Every official broadcast the scan found, as a calendar.

   The discovery layer knows hundreds of broadcasts and a couple of
   thousand hours of footage. As a list that is a wall; as a year of days
   it is a shape — the stages, the weekends, the off-season gaps, the
   championship weeks — and it shows at a glance how little of it has
   been read yet, which is the honest headline.

   Indigo is this system's colour for "a machine found this, nobody has
   read it", and that is exactly what every shaded day is. A day with a
   PUBLISHED game takes gold. A broadcast the scan scored as not-a-match
   (a clip, a promo) is left out, and so is one with no air date — the
   calendar says how many of those there are rather than placing them on
   a guessed day.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  if (!P) return;
  const esc = P.esc;
  const DAY_MS = 86400000;
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  const ymd = (d) => d.toISOString().slice(0, 10);

  /* Hours bucketed so a full championship day and a short qualifier read
     differently without a legend of exact numbers. */
  function level(hours) {
    if (hours <= 0) return 0;
    if (hours < 3) return 1;
    if (hours < 6) return 2;
    if (hours < 9) return 3;
    return 4;
  }

  function collect() {
    const rows = P.discovered().filter((b) => b.state !== "ignored");
    const byDay = new Map();
    let undated = 0;
    rows.forEach((b) => {
      if (!b.publishedAt) { undated++; return; }
      const day = String(b.publishedAt).slice(0, 10);
      if (!byDay.has(day)) byDay.set(day, { list: [], seconds: 0, published: false });
      const e = byDay.get(day);
      e.list.push(b);
      e.seconds += b.durationSeconds || 0;
      if (b.state === "published") e.published = true;
    });
    /* A game published from a broadcast the scan never listed still lights
       its day: the published record is the stronger fact. */
    (P.pub && P.pub.matches || []).forEach((m) => {
      const pubComps = P.publishedComps((c) => c.matchId === m.id);
      if (!pubComps.length || !m.scheduledAt) return;
      const day = String(m.scheduledAt).slice(0, 10);
      if (!byDay.has(day)) byDay.set(day, { list: [], seconds: 0, published: true, matchId: m.id });
      else byDay.get(day).published = true;
      byDay.get(day).matchId = byDay.get(day).matchId || m.id;
    });
    return { byDay: byDay, undated: undated, total: rows.length };
  }

  function yearGrid(year, byDay) {
    const jan1 = new Date(Date.UTC(year, 0, 1));
    const lead = (jan1.getUTCDay() + 6) % 7;            // Monday-first
    const days = (Date.UTC(year + 1, 0, 1) - jan1.getTime()) / DAY_MS;
    const weeks = Math.ceil((lead + days) / 7);
    let cells = "", monthHeads = "";
    let count = 0, hours = 0, published = 0;
    for (let i = 0; i < weeks * 7; i++) {
      const dayIdx = i - lead;
      if (dayIdx < 0 || dayIdx >= days) { cells += '<span class="arc-cell arc-cell--pad"></span>'; continue; }
      const d = new Date(jan1.getTime() + dayIdx * DAY_MS);
      const key = ymd(d);
      const e = byDay.get(key);
      if (d.getUTCDate() === 1) {
        const col = Math.floor(i / 7) + 1;
        monthHeads += '<span class="arc-month" style="grid-column:' + col + '">' + MONTHS[d.getUTCMonth()] + "</span>";
      }
      if (!e) { cells += '<span class="arc-cell" title="' + key + '"></span>'; continue; }
      const h = e.seconds / 3600;
      count += e.list.length; hours += h; if (e.published) published++;
      const titles = e.list.slice(0, 4).map((b) => (b.parsed && b.parsed.cleanTitle) || b.title).join(" · ");
      const href = e.published && e.matchId ? "game.html?id=" + encodeURIComponent(e.matchId)
        : e.list[0] ? "game.html?video=" + encodeURIComponent(e.list[0].videoId) : "#";
      const label = key + " · " + e.list.length + " broadcast" + (e.list.length === 1 ? "" : "s") +
        (h ? " · " + h.toFixed(1) + " h" : "") + (e.published ? " · published" : "") +
        (titles ? " — " + titles : "");
      cells += '<a class="arc-cell" data-level="' + level(h || (e.list.length ? 1 : 0)) + '"' +
        (e.published ? ' data-published="true"' : "") + ' href="' + esc(href) + '" title="' + esc(label) +
        '" aria-label="' + esc(label) + '"></a>';
    }
    if (!count && !published) return "";
    return '<div class="arc-year">' +
      '<div class="arc-year__head"><span class="arc-year__n">' + year + "</span>" +
        '<span class="dim small">' + count + " broadcast" + (count === 1 ? "" : "s") +
        (hours ? " · " + Math.round(hours).toLocaleString() + " h" : "") +
        " · " + (published ? '<b class="arc-gold">' + published + " published</b>" : "none read yet") + "</span></div>" +
      '<div class="arc-scroll"><div class="arc-grid" style="--weeks:' + weeks + '">' +
        '<div class="arc-months">' + monthHeads + "</div>" +
        '<div class="arc-days">' + cells + "</div>" +
      "</div></div></div>";
  }

  P.archiveCalendar = function () {
    const data = collect();
    if (!data.byDay.size) return "";
    const years = Array.from(new Set(Array.from(data.byDay.keys()).map((k) => +k.slice(0, 4))))
      .sort((a, b) => b - a);
    return '<section class="arc card" aria-labelledby="arc-h">' +
      '<div class="card-head"><h2 id="arc-h">Every official broadcast, by day</h2>' +
        '<span class="dim small">found by the scan · darker = more hours</span></div>' +
      '<div class="arc-body">' + years.map((y) => yearGrid(y, data.byDay)).join("") +
        '<div class="arc-key small dim">' +
          '<span>Less</span>' + [1, 2, 3, 4].map((l) => '<span class="arc-cell" data-level="' + l + '"></span>').join("") +
          "<span>More</span>" +
          '<span class="arc-cell" data-level="2" data-published="true"></span><span>has published line-ups</span>' +
          (data.undated ? "<span>· " + data.undated + " broadcast" + (data.undated === 1 ? "" : "s") +
            " without an air date yet, so not placed</span>" : "") +
        "</div>" +
      "</div></section>";
  };
})();
