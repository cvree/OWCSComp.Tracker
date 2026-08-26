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
    ["found", "Found automatically"],
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

  /* Fuse over the same haystack the substring filter used, so a typo or a
     half-remembered team name still finds the game. Built once and reused;
     the list does not change while the page is open. */
  let fuse = null;
  function searchIndex() {
    if (fuse !== null) return fuse;
    const rows = G.all().map((g) => {
      const a = P.team(g.teamA), b = P.team(g.teamB);
      return { id: g.id, title: g.title, event: g.tournamentName || "",
        teams: [a && a.name, a && a.code, b && b.name, b && b.code].filter(Boolean).join(" ") };
    });
    if (typeof window.Fuse !== "function") { fuse = false; return fuse; }
    try {
      fuse = new window.Fuse(rows, {
        ignoreLocation: true, threshold: 0.36,
        keys: [{ name: "title", weight: 0.5 }, { name: "teams", weight: 0.3 },
               { name: "event", weight: 0.2 }],
      });
    } catch (err) { fuse = false; }
    return fuse;
  }

  function matching() {
    let rows = G.all();
    if (state !== "all") rows = rows.filter((g) => g.state === state);
    if (!query) return rows;
    const f = searchIndex();
    if (f) {
      const hits = new Set(f.search(query).map((r) => r.item.id));
      return rows.filter((g) => hits.has(g.id));
    }
    const q = query.toLowerCase();
    return rows.filter((g) => {
      const a = P.team(g.teamA), b = P.team(g.teamB);
      return [g.title, g.tournamentName, a && a.code, b && b.code, g.id]
        .filter(Boolean).join(" ").toLowerCase().indexOf(q) >= 0;
    });
  }

  /* The archive's real size, in the two numbers that are actually
     measured: broadcasts found, and runtime the source reported for
     them. Printed once above the list so a visitor knows what they are
     looking at before they open anything. */
  function archiveScale() {
    const sum = (P.disc && P.disc.summary) || null;
    if (!sum || !sum.broadcastsKnown) return "";
    const bits = [];
    const hours = P.fmtHours(sum.runtimeSeconds);
    if (hours) {
      bits.push(sum.runtimeKnown < sum.broadcastsKnown
        ? hours + " of broadcast, over the " + sum.runtimeKnown +
          " the source reported a length for"
        : hours + " of broadcast");
    }
    if (sum.events) bits.push(sum.events + " events");
    if (sum.seasons) bits.push(sum.seasons + " seasons");
    if (!bits.length) return "";
    return '<p class="small u-mt-3" style="max-width:75ch"><strong>' +
      esc(bits.join(" · ")) + "</strong></p>";
  }

  /* Why almost none of these rows have a date. The archive is otherwise
     unreadable — "Aired: not stated" 246 times looks like a broken page
     rather than a source that will not answer. */
  function dateNote() {
    const cov = P.dateCoverage();
    if (!cov || !cov.unknown || !cov.reason) return "";
    return '<p class="dim small u-mt-3" style="max-width:75ch">' +
      "Only <strong>" + esc(cov.known) + "</strong> of these " +
      esc(cov.considered) + " carry a confirmed air date. " + esc(cov.reason) +
      (cov.blocked
        ? ' <a href="tools.html#discovery">See the scan\u2019s own errors \u2192</a>'
        : "") +
      "</p>";
  }

  function render() {
    const rows = matching();
    const host = document.getElementById("games");
    if (!rows.length) {
      host.innerHTML = G.all().length
        ? P.empty("◇", "No games match that",
          "Try a different state, or clear the text filter.")
        : P.empty("◇", "No games yet",
          "Nothing has been submitted and the automatic scan has not found a " +
          'broadcast yet. <a href="submit.html">Submit the first one</a> — ' +
          "one broadcast link is all it needs.");
      return;
    }
    /* Three groups, in the order a person cares about them: what needs a
       human, what the tracker is already working on, and what the scan
       found by itself. The third group is separated because it is a
       different KIND of row — nobody submitted it and nothing has been
       read from it — and burying sixty of those in one table would hide
       the one game that actually needs attention. */
    const attention = rows.filter((g) => g.state === "blocked" || g.state === "review");
    const found = rows.filter((g) => g.state === "found");
    const rest = rows.filter((g) => g.state !== "blocked" && g.state !== "review" &&
      g.state !== "found");

    const table = (list, caption) =>
      '<div class="table-wrap u-mt-4"><table class="tbl">' +
      "<thead><tr><th>Game</th><th>State</th><th>Progress</th><th>Event</th>" +
      "<th>Updated</th><th><span class=\"visually-hidden\">Open</span></th></tr></thead>" +
      "<tbody>" + list.map(G.row).join("") + "</tbody>" +
      "<caption>" + caption + "</caption></table></div>";

    /* 248 rows that differ only in a day number is not a list, it is a
       wall — and the wall was the site. The archive already carries the
       three things that turn it into something worth reading: the season
       the titles state, the event they belong to, and each broadcast's
       place inside that event. So it renders as season → event → day,
       and a row says the one thing its neighbours do not.

       Nothing here is a lookup. Every number comes from the discovery
       layer's own fold over broadcast titles and source-reported
       runtimes, and an event whose titles never named a season sits in
       its own group rather than being guessed into one. */
    const evByKey = new Map();
    P.discoveredEvents().forEach((e) => evByKey.set(e.key, e));

    const foundTable = (list, caption) =>
      '<div class="table-wrap u-mt-3"><table class="tbl">' +
      "<thead><tr><th>Broadcast</th><th>Length</th><th>Aired</th>" +
      "<th><span class=\"visually-hidden\">Open</span></th></tr></thead>" +
      "<tbody>" + list.map(G.foundRow).join("") + "</tbody>" +
      "<caption>" + caption + "</caption></table></div>";

    /* Days before weeks, ascending, so an event reads in the order it was
       played. A broadcast whose title places it nowhere sorts last rather
       than to the top on a zero. */
    function playOrder(a, b) {
      const pa = (a.broadcast && a.broadcast.parsed) || {};
      const pb = (b.broadcast && b.broadcast.parsed) || {};
      const wa = pa.week == null ? 99 : pa.week, wb = pb.week == null ? 99 : pb.week;
      if (wa !== wb) return wa - wb;
      const da = pa.day == null ? 99 : pa.day, db = pb.day == null ? 99 : pb.day;
      if (da !== db) return da - db;
      return String(a.title).localeCompare(String(b.title));
    }

    function eventKeyOf(g) {
      const p = (g.broadcast && g.broadcast.parsed) || {};
      return p.eventKey || null;
    }

    /* One door is left open, always: a section that is nothing but shut
       <details> reads as an empty page. The first event of the newest
       season opens, and every event opens while a text filter is on
       (the point of filtering is to see the matches). */
    function eventBlock(key, items, open) {
      const ev = key ? evByKey.get(key) : null;
      const name = (ev && ev.name) || items[0].tournamentName || "Event not identified";
      const meta = G.eventMeta(ev);
      items = items.slice().sort(playOrder);
      return '<details class="evt"' + (open || query ? " open" : "") + ">" +
        '<summary><span class="evt__name">' + esc(name) + "</span>" +
        '<span class="evt__count">' + items.length + " broadcast" +
        (items.length === 1 ? "" : "s") + "</span>" +
        (meta.length
          ? '<span class="evt__when dim small">' + esc(meta.join(" · ")) + "</span>"
          : "") +
        "</summary>" +
        foundTable(items, items.length + " broadcast" +
          (items.length === 1 ? "" : "s") + " on this event, none read yet.") +
        "</details>";
    }

    function grouped(list) {
      /* Which season each event belongs to, from the discovery layer's
         own rollup — never re-derived here, so the page and the archive
         can never disagree about what year something is. */
      const seasonOfEvent = new Map();
      P.discoveredSeasons().forEach((s) => {
        (s.eventKeys || []).forEach((k) => seasonOfEvent.set(k, s.season));
      });

      const bySeason = new Map();
      list.forEach((g) => {
        const ek = eventKeyOf(g);
        const season = ek != null && seasonOfEvent.has(ek)
          ? seasonOfEvent.get(ek) : null;
        if (!bySeason.has(season)) bySeason.set(season, new Map());
        const byEvent = bySeason.get(season);
        const key = ek || ("_" + (g.tournamentName || "unidentified"));
        if (!byEvent.has(key)) byEvent.set(key, []);
        byEvent.get(key).push(g);
      });

      /* Newest season first; the events whose titles never stated one go
         last, under a heading that says exactly that. */
      const seasons = Array.from(bySeason.keys()).sort((a, b) => {
        if (a === null) return 1;
        if (b === null) return -1;
        return b - a;
      });

      /* A filtered view can collapse to one season with one event — at
         that point the scaffolding is louder than the content. */
      const totalEvents = seasons.reduce((n, s) => n + bySeason.get(s).size, 0);
      if (totalEvents < 2) {
        return foundTable(list.slice().sort(playOrder),
          list.length + " broadcast" + (list.length === 1 ? "" : "s") +
          " found automatically, none read yet.");
      }

      let firstOpen = true;
      return '<div class="stack u-mt-4">' + seasons.map((season) => {
        const byEvent = bySeason.get(season);
        /* Newest first. Size was the ordering while the scan could not
           date anything and every `lastAt` was null; now that they all
           carry one, an archive orders by when it happened. Size stays
           as the tie-break for the events that still have no date. */
        const keys = Array.from(byEvent.keys()).sort((a, b) => {
          const ea = evByKey.get(a), eb = evByKey.get(b);
          const la = (ea && ea.lastAt) || "", lb = (eb && eb.lastAt) || "";
          if (la !== lb) return lb.localeCompare(la);
          return byEvent.get(b).length - byEvent.get(a).length;
        });
        const n = keys.reduce((t, k) => t + byEvent.get(k).length, 0);
        const runtime = P.fmtHours(keys.reduce((t, k) => {
          const ev = evByKey.get(k);
          return t + ((ev && ev.runtimeSeconds) || 0);
        }, 0));
        const heading = season == null
          ? "Season not stated in the titles"
          : season + " season";
        const sub = [n + " broadcast" + (n === 1 ? "" : "s"),
          keys.length + " event" + (keys.length === 1 ? "" : "s")];
        if (runtime) sub.push(runtime);
        /* The heading and its tally sit on one line where there is room
           and stack where there is not. Without the wrap the tally is a
           single unbreakable flex item — "100 broadcasts · 36 events ·
           680 hours" — and it pushed <main> to 485px inside a 358px
           column, which scrolls the whole page sideways on a phone. */
        return '<section class="u-mt-4">' +
          '<h3 class="u-flex u-center u-gap-3" ' +
          'style="font-size:1rem;flex-wrap:wrap">' +
          "<span>" + esc(heading) + "</span>" +
          '<span class="dim small" style="font-weight:400;min-width:0">' +
          esc(sub.join(" · ")) + "</span></h3>" +
          keys.map((k) => {
            const items = byEvent.get(k);
            const open = firstOpen;
            firstOpen = false;
            return eventBlock(items[0] && eventKeyOf(items[0]), items, open);
          }).join("") +
          "</section>";
      }).join("") + "</div>";
    }

    host.innerHTML =
      (attention.length
        ? '<h2 class="visually-hidden">Games needing a person</h2>' +
          '<div class="grid grid--2">' +
          attention.map((g) => '<div class="rv">' + G.card(g) + "</div>").join("") + "</div>"
        : "") +
      (rest.length
        ? (attention.length ? '<h2 class="u-mt-6" style="font-size:1.1rem">Everything else</h2>' : "") +
          table(rest, rest.length + " game" + (rest.length === 1 ? "" : "s") +
            " shown. Progress bars read left to right: source, video, gameplay, " +
            "heroes, match link, review.")
        : "") +
      (found.length
        ? '<h2 class="u-mt-6" style="font-size:1.1rem">Found automatically ' +
          '<span class="chip" data-state="detected"><span class="dot"></span>' +
          found.length + "</span></h2>" +
          '<p class="dim small u-mt-3" style="max-width:75ch">Broadcasts the tracker ' +
          "found by itself on verified official channels. Nothing has been read from " +
          "them yet — the title is all we know, and the title is all this list claims. " +
          "Opening one shows the evidence and the single step that starts processing.</p>" +
          archiveScale() + dateNote() +
          grouped(found)
        : "");
    if (P.observeReveals) P.observeReveals(host);
  }

  /* The official season schedule. It is not a page — nobody needs a
     calendar as a destination — but "what is coming that nobody has
     submitted yet" is the most useful thing to see under a games list,
     so it sits at the bottom of this one, collapsed. */
  function renderUpcoming() {
    const host = document.getElementById("upcoming");
    if (!host) return;
    /* `startDate`/`endDate` are what the exporter writes (and what the
       public data contract specifies). This read used to look for a
       `startsAt` field that has never existed in the export, so the
       filter dropped every row and the official schedule silently
       rendered as nothing on every load. */
    const cutoff = Date.now() - 86400000;
    const events = ((P.pub && P.pub.calendarEvents) || []).slice()
      .filter((e) => {
        const ends = e.endDate || e.startDate;
        return ends && new Date(ends).getTime() > cutoff;
      })
      .sort((a, b) => String(a.startDate || "").localeCompare(String(b.startDate || "")));
    if (!events.length) { host.innerHTML = ""; return; }

    /* How much of each event the scan has already found for itself, so
       the schedule stops saying "nobody has submitted this" about an
       event whose broadcasts are sitting in the list above. */
    const foundPerEvent = {};
    P.discovered().forEach((b) => {
      ((b.calendar && b.calendar.eventIds) || []).forEach((id) => {
        foundPerEvent[id] = (foundPerEvent[id] || 0) + 1;
      });
    });
    const covered = events.filter((e) => foundPerEvent[e.id]).length;

    host.innerHTML = P.diag(
      "On the official schedule (" + events.length + " event" +
      (events.length === 1 ? "" : "s") +
      (covered ? ", " + covered + " with broadcasts already found" : "") + ")",
      '<p class="dim small" style="margin-bottom:var(--s-3)">Straight from the published ' +
      "OWCS calendar. “Broadcasts found” counts what the automatic scan has already " +
      "located for that event; a count of none means nothing has turned up for it yet.</p>" +
      '<div class="table-wrap"><table class="tbl"><thead><tr><th>Event</th><th>Region</th>' +
      "<th>Dates</th><th>Broadcasts found</th><th>Confirmed</th></tr></thead><tbody>" +
      events.map((e) => {
        const n = foundPerEvent[e.id] || 0;
        const span = e.endDate && e.endDate !== e.startDate
          ? P.fmtDate(e.startDate) + " – " + P.fmtDate(e.endDate)
          : P.fmtDate(e.startDate);
        return "<tr><td><b>" + esc(e.name) + "</b></td><td>" +
          esc(P.regionName(e.region)) + "</td><td>" + esc(span) + "</td><td>" +
          (n
            ? '<a href="games.html?state=found&q=' + encodeURIComponent(e.name) + '">' +
              n + "</a>"
            : '<span class="dim">none yet</span>') + "</td><td>" +
          (e.verified
            ? '<span class="chip" data-state="evidence"><span class="dot"></span>confirmed</span>'
            : '<span class="chip" data-state="queued">provisional</span>') +
          "</td></tr>";
      }).join("") + "</tbody></table></div>");
  }

  /* The honest header for a self-filling list: when the tracker last
     looked, what it found, and when it will look again. It renders the
     staleness as a word, not only as a colour. */
  function renderScanStatus() {
    const host = document.getElementById("scan-status");
    if (!host) return;
    const d = P.disc;
    if (!d || !d.scan || !d.scan.generatedAt) {
      host.innerHTML = P.note("info", "No automatic scan has run yet",
        "<p>Every game in this list was put here by a person. Once the scheduled " +
        "broadcast scan runs, new OWCS broadcasts appear here on their own.</p>");
      return;
    }
    const s = d.summary || {};
    const stale = P.scanStale();
    const next = d.scan.nextExpectedAt;
    const errs = (d.scan.sourceErrors || []).length;
    const body = "<p>Last scan " + esc(P.fmtRel(d.scan.generatedAt)) +
      (stale ? " — <b>stale</b>" : "") + ". " +
      esc(s.broadcastsKnown || 0) + " broadcast" + ((s.broadcastsKnown === 1) ? "" : "s") +
      " known across " + esc(s.events || 0) + " event" + ((s.events === 1) ? "" : "s") +
      ", " + esc(s.awaitingProcessing || 0) + " waiting to be processed." +
      (next && !stale ? " Next scan due " + esc(P.fmtRel(next)) + "." : "") +
      (errs ? " " + errs + " source error" + (errs === 1 ? "" : "s") +
        ' — see <a href="tools.html">tools</a>.' : "") + "</p>";
    host.innerHTML = P.note(stale || errs ? "warn" : "ok",
      stale ? "The automatic scan has not run recently"
        : "This list fills itself", body);
  }

  document.addEventListener("DOMContentLoaded", () => {
    renderScanStatus();
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
