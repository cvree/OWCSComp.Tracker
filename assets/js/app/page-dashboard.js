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
    renderTiles();
    renderAttention();
    renderRecent();
    renderPublished();
    renderHealth();
  });

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
      tile("review.html", "Needs review", c.review, "amber",
        c.review ? "Requires confirmation" : "Nothing waiting", "Review queue") +
      tile("games.html?state=working", "Processing now", c.working, "cyan",
        c.working ? "Reading video" : "Idle", "Watch progress") +
      tile("games.html?state=blocked", "Blocked", c.blocked, "red",
        c.blocked ? "A person must intervene" : "No blockers", "See what stopped") +
      tile("stats.html", "Published line-ups", comps, "gold",
        "Evidence-backed compositions", "Explore the data"));
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
