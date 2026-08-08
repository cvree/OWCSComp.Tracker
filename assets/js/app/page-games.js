/* =====================================================================
   OWCS Comp Tracker — app/page-games.js
   One list, five filters, a text box. That is the whole page.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const G = P.games;
  const esc = P.esc;

  let state = P.qs("state") || "all";
  let query = P.qs("q") || "";

  const STATES = [
    ["all", "All"],
    ["review", "Needs review"],
    ["working", "Processing"],
    ["blocked", "Blocked"],
    ["queued", "Queued"],
    ["published", "Published"],
  ];

  function renderFilter() {
    const counts = G.counts();
    document.getElementById("state-filter").innerHTML = STATES.map((s) => {
      const n = s[0] === "all" ? counts.total : counts[s[0]] || 0;
      return '<button type="button" data-state="' + s[0] + '" aria-pressed="' +
        (state === s[0]) + '">' + esc(s[1]) +
        '<span class="count">' + n + "</span></button>";
    }).join("");
  }

  function matches(g) {
    if (state !== "all" && g.state !== state) return false;
    if (!query) return true;
    const q = query.toLowerCase();
    const a = P.team(g.teamA), b = P.team(g.teamB);
    return [g.title, g.tournamentName, a && a.code, b && b.code, g.id]
      .filter(Boolean).join(" ").toLowerCase().indexOf(q) >= 0;
  }

  function render() {
    const rows = G.all().filter(matches);
    const host = document.getElementById("games");
    if (!rows.length) {
      host.innerHTML = G.all().length
        ? P.empty("◇", "No games match that",
          "Try a different state, or clear the text filter.")
        : P.empty("◇", "No games yet",
          'Nothing has been submitted. <a href="submit.html">Submit the first one</a> — ' +
          "one broadcast link is all it needs.");
      return;
    }
    /* Anything needing a person gets the full card treatment; settled
       games are a dense table, because scanning beats browsing there. */
    const attention = rows.filter((g) => g.state === "blocked" || g.state === "review");
    const rest = rows.filter((g) => g.state !== "blocked" && g.state !== "review");

    host.innerHTML =
      (attention.length
        ? '<h2 class="visually-hidden">Games needing a person</h2>' +
          '<div class="grid grid--2">' +
          attention.map((g) => '<div class="rv">' + G.card(g) + "</div>").join("") + "</div>"
        : "") +
      (rest.length
        ? (attention.length ? '<h2 class="u-mt-6" style="font-size:1.1rem">Everything else</h2>' : "") +
          '<div class="table-wrap u-mt-4"><table class="tbl">' +
          "<thead><tr><th>Game</th><th>State</th><th>Progress</th><th>Event</th>" +
          "<th>Updated</th><th><span class=\"visually-hidden\">Open</span></th></tr></thead>" +
          "<tbody>" + rest.map(G.row).join("") + "</tbody>" +
          '<caption>' + rows.length + " game" + (rows.length === 1 ? "" : "s") +
          " shown. Progress bars read left to right: source, video, gameplay, heroes, " +
          "match link, review.</caption></table></div>"
        : "");
    if (P.observeReveals) P.observeReveals(host);
  }

  /* The official season schedule. It is not a page — nobody needs a
     calendar as a destination — but "what is coming that nobody has
     submitted yet" is the most useful thing to see under a games list,
     so it sits at the bottom of this one, collapsed. */
  function renderUpcoming() {
    const events = ((P.pub && P.pub.calendarEvents) || []).slice()
      .filter((e) => e.startsAt && new Date(e.startsAt).getTime() > Date.now() - 86400000)
      .sort((a, b) => String(a.startsAt).localeCompare(String(b.startsAt)));
    const host = document.getElementById("upcoming");
    if (!host) return;
    if (!events.length) { host.innerHTML = ""; return; }
    host.innerHTML = P.diag(
      "On the official schedule (" + events.length + " event" +
      (events.length === 1 ? "" : "s") + " nobody has submitted yet)",
      '<p class="dim small" style="margin-bottom:var(--s-3)">Straight from the published ' +
      "OWCS calendar. An event here has no data until someone submits its broadcast — " +
      "which is the whole point of listing it.</p>" +
      '<div class="table-wrap"><table class="tbl"><thead><tr><th>Event</th><th>Region</th>' +
      "<th>Starts</th><th>Confirmed</th></tr></thead><tbody>" +
      events.map((e) => "<tr><td><b>" + esc(e.name) + "</b></td><td>" +
        esc(P.regionName(e.region)) + "</td><td>" +
        esc(e.timeKnown === false ? P.fmtDate(e.startsAt) + " · time TBA"
          : P.fmtDateTime(e.startsAt)) + "</td><td>" +
        (e.verified
          ? '<span class="chip" data-state="evidence"><span class="dot"></span>confirmed</span>'
          : '<span class="chip" data-state="queued">provisional</span>') +
        "</td></tr>").join("") + "</tbody></table></div>");
  }

  document.addEventListener("DOMContentLoaded", () => {
    renderFilter();
    document.getElementById("q").value = query;
    render();
    renderUpcoming();

    document.getElementById("state-filter").addEventListener("click", (e) => {
      const b = e.target.closest("[data-state]");
      if (!b) return;
      state = b.dataset.state;
      P.setQs({ state: state });
      renderFilter();
      render();
    });
    let t = null;
    document.getElementById("q").addEventListener("input", (e) => {
      query = e.target.value.trim();
      clearTimeout(t);
      t = setTimeout(() => { P.setQs({ q: query }); render(); }, 160);
    });
  });
})();
