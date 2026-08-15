/* =====================================================================
   OWCS Comp Tracker — app/page-dashboard.js
   The home screen answers four questions and nothing else:
     Where am I?   → the headline and the one primary action
     What is happening?  → the four tiles
     Do I need to do anything?  → "Waiting on you", which hides when empty
     What next?    → every card is a link to the next step
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const G = P.games;
  const esc = P.esc;

  document.addEventListener("DOMContentLoaded", () => {
    P.api.mountStatus("mode-status");
    renderShowcase();
    renderTiles();
    renderAutofill();
    renderAttention();
    renderRecent();
    renderPublished();
    renderHealth();
  });

  /* --------------------------------------------------------- showcase
     The headline claims the product turns a broadcast into reviewed data.
     This is the last time it did, in full: the map, both line-ups, the
     confidence the detector had, and the frame it was read from. It is the
     claim and the receipt side by side.

     It renders nothing at all when nothing has been published — an empty
     promise-shaped box would be worse than the space it saves. */
  function renderShowcase() {
    const host = document.getElementById("showcase");
    if (!host) return;
    const comps = P.publishedComps();
    if (!comps.length) { host.remove(); return; }

    /* Newest first by the clock the exporter writes, falling back to the
       read offset so a dataset without timestamps still picks the latest
       thing on the latest map rather than an arbitrary row. */
    const latest = comps.slice().sort((a, b) =>
      String(b.matchId).localeCompare(String(a.matchId)) ||
      (b.timestamp || 0) - (a.timestamp || 0))[0];
    const mapId = latest.mapId;
    const onMap = comps.filter((c) => c.mapId === mapId);
    const sides = {};
    onMap.forEach((c) => {
      if (!sides[c.teamId] || c.timestamp < sides[c.teamId].timestamp) sides[c.teamId] = c;
    });
    const teamIds = Object.keys(sides);
    if (!teamIds.length) { host.remove(); return; }

    const match = P.match(latest.matchId);
    const mapRec = match && (match.maps || []).find((m) => m.id === mapId);
    const info = P.mapInfo(mapRec ? mapRec.map : null);

    const side = (teamId) => {
      const c = sides[teamId];
      return '<div class="showcase__side">' +
        '<div class="showcase__team">' + P.teamPlate(teamId, { size: "sm", link: true }) +
        P.confMeter(c.confidence) + "</div>" +
        P.compStrip(c.heroes, { size: "sm", link: true }) + "</div>";
    };

    host.innerHTML =
      '<article class="showcase">' +
        '<div class="showcase__head">' +
          '<span class="chip" data-state="published"><span class="dot"></span>Published</span>' +
          '<span class="showcase__map">' + esc(info.name) +
          (info.mode ? '<span class="dim"> · ' + esc(info.mode) + "</span>" : "") + "</span>" +
        "</div>" +
        '<p class="showcase__kicker">The most recent line-up this read off a broadcast ' +
          "and a person confirmed.</p>" +
        teamIds.map(side).join('<span class="showcase__rule" aria-hidden="true"></span>') +
        '<div class="showcase__foot">' +
          (latest.evidenceFrame
            ? '<button class="btn btn--sm btn--quiet" data-evidence="' +
              esc(latest.evidenceFrame) + '" data-evidence-cap="' +
              esc(info.name + " — the frame this was read from") + '">See the frame</button>'
            : "") +
          '<a class="btn btn--sm btn--ghost" href="game.html?id=' +
            encodeURIComponent(latest.matchId) + '">Open the game</a>' +
        "</div>" +
      "</article>";
    if (P.evidence) P.evidence.wire(document);
  }

  /* ------------------------------------------------------------ tiles
     Analytics cards, not alert blocks. The number carries the semantic
     colour; the surface stays neutral. A card only takes an accent edge
     when its count is non-zero, so a clear queue reads as calm rather
     than as four emergencies. */
  function renderTiles() {
    const c = G.counts();
    const comps = P.publishedComps().length;
    const tile = (href, key, value, accent, note, cta) =>
      '<a class="stat rv" href="' + href + '" data-accent="' + accent + '"' +
      (value ? ' data-alert="1"' : "") + ">" +
      '<span class="stat__k">' + esc(key) + "</span>" +
      '<span class="stat__v" data-count-to="' + value + '">' + value + "</span>" +
      '<span class="stat__note">' + note + "</span>" +
      '<span class="stat__cta">' + esc(cta) + " <span aria-hidden=\"true\">→</span></span></a>";

    P.mount("tiles",
      tile("games.html?state=found", "Found automatically", c.found, "indigo",
        c.found ? "Waiting to be processed" : "Nothing new found", "See what was found") +
      tile("review.html", "Needs review", c.review, "amber",
        c.review ? "Requires confirmation" : "Nothing waiting", "Review queue") +
      tile("games.html?state=working", "Processing now", c.working, "cyan",
        c.working ? "Reading video" : "Idle", "Watch progress") +
      tile("games.html?state=blocked", "Blocked", c.blocked, "red",
        c.blocked ? "A person must intervene" : "No blockers", "See what stopped") +
      tile("stats.html", "Published line-ups", comps, "gold",
        "Evidence-backed compositions", "Explore the data"));
  }

  /* -------------------------------------------------- filling itself
     The one band on this page that is about the SYSTEM rather than a
     game: what the unattended scan did without anyone asking, when it
     last ran, and what it turned up. It hides itself entirely when no
     scan has ever run rather than printing an empty promise. */
  function renderAutofill() {
    const band = document.getElementById("autofill-band");
    const host = document.getElementById("autofill");
    if (!band || !host) return;
    const d = P.disc;
    if (!d || !d.scan || !d.scan.generatedAt) { band.hidden = true; return; }
    band.hidden = false;

    const s = d.summary || {};
    const stale = P.scanStale();
    const errs = (d.scan.sourceErrors || []).length;
    const channels = (d.scan.channels || []).length;

    const fact = (k, v, note) =>
      '<div class="card"><span class="stat__k">' + esc(k) + "</span>" +
      '<span class="stat__v">' + esc(v) + "</span>" +
      '<span class="stat__note">' + note + "</span></div>";

    const events = (d.events || []).filter((e) => e.found > 0).slice(0, 5);

    host.innerHTML =
      P.note(stale || errs ? "warn" : "ok",
        stale ? "The scan has not run recently" : "The tracker is filling itself",
        "<p>The scheduled scan reads every verified official broadcast channel and adds " +
        "what it finds to the games list without anyone asking. Last run " +
        esc(P.fmtRel(d.scan.generatedAt)) + (stale ? " — <b>stale</b>" : "") +
        (d.scan.nextExpectedAt && !stale
          ? ", next due " + esc(P.fmtRel(d.scan.nextExpectedAt)) : "") + "." +
        (errs ? " <b>" + errs + " source error" + (errs === 1 ? "" : "s") +
          "</b> on the last run." : "") +
        " Finding a broadcast is not reading it: nothing here becomes match data until " +
        "it has been processed and a person has confirmed what the detector saw.</p>") +
      '<div class="grid grid--4 u-mt-4">' +
      fact("Broadcasts known", s.broadcastsKnown || 0,
        esc(channels) + " official channel" + (channels === 1 ? "" : "s") + " scanned") +
      fact("Waiting to be processed", s.awaitingProcessing || 0,
        '<a href="games.html?state=found">Open the list</a>') +
      fact("Events recognised", s.events || 0,
        esc(s.calendarLinked || 0) + " broadcast" + (s.calendarLinked === 1 ? "" : "s") +
        " placed on the official calendar") +
      fact("Published from them", s.published || 0,
        "Reviewed, approved and live") +
      "</div>" +
      (events.length
        ? '<div class="table-wrap u-mt-4"><table class="tbl">' +
          "<thead><tr><th>Event the scan recognised</th><th>Broadcasts found</th>" +
          "<th>Published</th><th>Most recent</th></tr></thead><tbody>" +
          events.map((e) => "<tr><td><b>" + esc(e.name) + "</b>" +
            (e.days && e.days.length
              ? ' <span class="dim small">day ' + esc(e.days.join(", ")) + "</span>" : "") +
            "</td><td>" + esc(e.broadcasts) + "</td><td>" + esc(e.published) +
            '</td><td class="dim small u-nowrap">' +
            esc(e.lastAt ? P.fmtDate(e.lastAt) : "date unknown") + "</td></tr>").join("") +
          "</tbody><caption>Events are read from the broadcast titles themselves — " +
          "never from a source that could be wrong about them.</caption>" +
          "</table></div>"
        : "");
  }

  /* ------------------------------------------------- waiting on you */
  function renderAttention() {
    const band = document.getElementById("attention-band");
    const items = G.byState("blocked").concat(G.byState("review")).slice(0, 4);
    if (!items.length) { band.hidden = true; return; }
    band.hidden = false;

    P.mount("attention", items.map((g) => {
      if (g.state === "blocked" && g.blockers.length) {
        const b = g.blockers[0];
        return P.note("error", g.title + " — " + b.title,
          "<p>" + esc(b.why) + "</p>",
          '<a class="btn btn--sm" href="' + esc(g.href) + '">Open the game</a>' +
          (b.command
            ? '<button class="btn btn--sm btn--ghost" data-copy="' + esc(b.command) + '">Copy the fix</button>'
            : b.link ? '<a class="btn btn--sm btn--ghost" href="' + esc(b.link) + '">' + esc(b.fixLabel) + "</a>" : ""));
      }
      if (g.state === "blocked") {
        return P.note("error", g.title + " — stopped",
          "<p>Processing stopped and the record does not say why. Open the game to see " +
          "the full log.</p>",
          '<a class="btn btn--sm" href="' + esc(g.href) + '">Open the game</a>');
      }
      return P.note("warn", g.title + " — ready for review",
        "<p>The detector finished reading this game. " +
        (g.mapCount ? esc(g.mapCount) + (g.mapCount === 1 ? " map is" : " maps are") + " waiting"
          : "It is waiting") + " for someone to confirm the heroes it read.</p>",
        '<a class="btn btn--sm btn--primary" href="review.html?game=' + encodeURIComponent(g.id) +
        '">Review it</a><a class="btn btn--sm btn--ghost" href="' + esc(g.href) + '">See details</a>');
    }).join(""));

    document.getElementById("attention").addEventListener("click", (e) => {
      const b = e.target.closest("[data-copy]");
      if (b) P.copy(b.dataset.copy, "Command copied — paste it in a terminal");
    });
  }

  /* ----------------------------------------------------------- recent */
  function renderRecent() {
    const games = G.recent(4);
    if (!games.length) {
      P.mount("recent", P.empty("◇", "No games yet",
        'Submitting a broadcast link is the whole first step. ' +
        '<a href="submit.html">Submit your first game</a>.'));
      return;
    }
    P.mount("recent", games.map((g) => '<div class="rv">' + G.card(g) + "</div>").join(""));
  }

  /* -------------------------------------------------------- published */
  function renderPublished() {
    const games = G.byState("published").slice(0, 6);
    if (!games.length) {
      P.mount("published", P.empty("◈", "Nothing published yet",
        "A game appears here once someone has reviewed it and approved the detections. " +
        "That gate is deliberate — unreviewed data never reaches these pages."));
      return;
    }
    P.mount("published",
      '<div class="table-wrap"><table class="tbl">' +
      "<thead><tr><th>Game</th><th>State</th><th>Progress</th><th>Event</th>" +
      "<th>Updated</th><th><span class=\"visually-hidden\">Open</span></th></tr></thead>" +
      "<tbody>" + games.map(G.row).join("") + "</tbody></table></div>");
  }

  /* ----------------------------------------------------------- health
     Small and useful only. Anything an operator would need a terminal to
     act on lives in Tools, not here. */
  function renderHealth() {
    const el = document.getElementById("health");
    const gen = P.dataAge();
    const stale = P.isStale(24);
    const runs = (P.work && P.work.autoRuns) || [];
    const lastRun = runs[0] || null;

    const item = (label, value, kind, note) =>
      '<div class="card"><span class="stat__k">' + esc(label) + "</span>" +
      '<div class="u-flex u-center u-gap-3 u-mt-3">' +
      '<span class="chip" data-state="' + kind + '"><span class="dot"></span>' + esc(value) + "</span>" +
      "</div>" + (note ? '<p class="stat__note">' + note + "</p>" : "") + "</div>";

    const parts = [
      item("Published dataset", stale ? "Older than 24h" : "Current",
        stale ? "review" : "evidence",
        gen ? "Generated " + esc(P.fmtDateTime(gen)) : "Never generated"),
      item("Last processing run",
        lastRun ? (lastRun.runStatus === "ok" ? "Completed" : lastRun.runStatus === "partial"
          ? "Finished with gaps" : "Failed") : "None recorded",
        lastRun ? (lastRun.runStatus === "ok" ? "evidence" : lastRun.runStatus === "partial"
          ? "review" : "blocked") : "queued",
        lastRun ? esc(lastRun.run) + " · " + esc(P.fmtRel(lastRun.finishedAt || lastRun.startedAt)) : ""),
      '<div class="card" id="health-live"><span class="stat__k">This machine</span>' +
      '<div class="u-flex u-center u-gap-3 u-mt-3"><span class="spinner"></span>' +
      '<span class="dim small">checking…</span></div></div>',
    ];
    el.innerHTML = '<div class="grid grid--3">' + parts.join("") + "</div>";

    P.api.probe().then(() => {
      const live = document.getElementById("health-live");
      if (!live) return;
      if (!P.api.isConnected()) {
        live.innerHTML = '<span class="stat__k">This machine</span>' +
          '<div class="u-flex u-center u-gap-3 u-mt-3"><span class="chip" data-state="queued">' +
          '<span class="dot"></span>Read-only</span></div>' +
          '<p class="stat__note">Nothing is running behind this page, so it can show published ' +
          'data but not process video. <a href="how-it-works.html#run-it-yourself">Run your own copy</a>.</p>';
        return;
      }
      live.innerHTML = '<span class="stat__k">This machine</span>' +
        '<div class="u-flex u-center u-gap-3 u-mt-3"><span class="chip" data-state="evidence">' +
        '<span class="dot"></span>Connected</span></div>' +
        '<p class="stat__note">Reading live status…</p>';
      P.api.overview().then((o) => {
        if (!o || o.ok === false) return;
        const blocking = (o.health && o.health.blocking) || [];
        const worker = o.worker && o.worker.running;
        live.innerHTML = '<span class="stat__k">This machine</span>' +
          '<div class="u-flex u-center u-gap-3 u-mt-3">' +
          '<span class="chip" data-state="' + (blocking.length ? "blocked" : "evidence") + '">' +
          '<span class="dot"></span>' + (blocking.length ? blocking.length + " problem" +
            (blocking.length === 1 ? "" : "s") : "Healthy") + "</span>" +
          '<span class="chip" data-state="' + (worker ? "working" : "queued") + '">' +
          '<span class="dot' + (worker ? " pulse" : "") + '"></span>' +
          (worker ? "Worker running" : "Worker stopped") + "</span></div>" +
          '<p class="stat__note"><a href="tools.html">Open diagnostics</a></p>';
      });
    });
  }
})();
