/* =====================================================================
   OWCS Comp Tracker — app/page-game.js
   One page for one game, in whatever state it happens to be.

   Splitting "the processing view" and "the published match page" into
   separate destinations was the single biggest source of the old site's
   disorientation: the same game had two addresses and neither one told
   you the other existed. Here the address is the game, and the page
   answers the four questions in order:

     Where am I?          the header — teams, event, state
     What is happening?   the progression, with live output when it is running
     Do I need to do anything?  the blocker or the review call-to-action
     What next?           exactly one primary button, always

   The published view is what a fan sees, and it is the same page.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const G = P.games;
  const esc = P.esc;

  let game = null;
  let logCursor = 0;
  let poller = null;

  /* ------------------------------------------------------------- boot */
  document.addEventListener("DOMContentLoaded", () => {
    const id = P.qs("id");
    const run = P.qs("run");
    game = id ? G.byId(id) : run ? G.byId(run) : null;

    if (!game) {
      document.getElementById("head").innerHTML =
        '<div class="page-head"><h1>That game is not here</h1>' +
        '<p class="lede">The link points at a game this copy of the tracker has no record ' +
        "of. It may not have been published yet, or the address may be wrong.</p>" +
        '<div class="row u-mt-5"><a class="btn btn--primary" href="games.html">See all games</a>' +
        '<a class="btn btn--ghost" href="submit.html">Submit a game</a></div></div>';
      return;
    }

    document.title = game.title + " — OWCS Comp Tracker";
    P.evidence.wire(document);
    renderCrumbs();
    renderHead();
    renderBody();

    if (game.state === "working") startLivePolling();
  });

  /* ------------------------------------------------------------ header */
  function renderCrumbs() {
    document.getElementById("crumbs").innerHTML = P.breadcrumbs([
      { label: "Games", href: "games.html" },
      { label: game.title },
    ]);
  }

  function renderHead() {
    const a = P.team(game.teamA), b = P.team(game.teamB);
    const meta = [];
    if (game.tournamentName) meta.push(esc(game.tournamentName));
    if (game.scheduledAt) meta.push(esc(P.fmtDate(game.scheduledAt)));
    if (game.mapCount) meta.push(game.mapCount + (game.mapCount === 1 ? " map" : " maps"));

    const primary = {
      published: ["Explore the data", "stats.html", "btn--primary"],
      review: ["Review this game", "review.html?game=" + encodeURIComponent(game.id), "btn--primary"],
      working: ["Watch the live output", "#live", "btn--ghost"],
      queued: ["See all games", "games.html", "btn--ghost"],
      blocked: ["Fix the blocker", "#blockers", "btn--primary"],
    }[game.state];

    document.getElementById("head").innerHTML =
      '<div class="page-head page-head--tight">' +
        '<div class="row">' + P.stateChip(game.state) +
        (game.state === "published" && game.compCount
          ? '<span class="chip" data-state="evidence"><span class="dot"></span>' +
            game.compCount + " verified line-up" + (game.compCount === 1 ? "" : "s") + "</span>"
          : "") + "</div>" +
        '<h1 class="u-mt-4" style="font-size:clamp(1.8rem,3.6vw,2.8rem)">' +
          (a || b
            ? esc((a ? a.name : "Team A")) + ' <span class="dim" style="font-size:.6em">vs</span> ' +
              esc((b ? b.name : "Team B"))
            : esc(game.title)) + "</h1>" +
        (meta.length ? '<p class="dim u-mt-3">' + meta.join(" · ") + "</p>" : "") +
        '<p class="lede u-mt-4">' + esc(P.STATE_WORDS[game.state].say) +
        (game.summary ? " " + esc(game.summary) : "") + "</p>" +
        '<div class="row u-mt-5">' +
          '<a class="btn ' + primary[2] + '" href="' + esc(primary[1]) + '">' + esc(primary[0]) + "</a>" +
          (game.sourceUrl
            ? '<a class="btn btn--ghost" href="' + esc(game.sourceUrl) +
              '" target="_blank" rel="noopener">Open the broadcast ↗</a>' : "") +
        "</div>" +
      "</div>";
  }

  /* -------------------------------------------------------------- body */
  function renderBody() {
    const parts = [];

    if (game.blockers.length) parts.push(blockerSection());
    parts.push(flowSection());
    if (game.state === "working") parts.push(liveSection());
    if (game.state === "review") parts.push(reviewCallSection());
    if (game.state === "published" || game.compCount) parts.push(publishedSection());
    parts.push(diagnosticsSection());

    document.getElementById("body").innerHTML = parts.join("");

    document.getElementById("body").addEventListener("click", (e) => {
      const c = e.target.closest("[data-copy]");
      if (c) P.copy(c.dataset.copy, "Copied — paste it in a terminal");
    });
  }

  /* ---------------------------------------------------------- blockers */
  function blockerSection() {
    return '<section class="band" id="blockers" aria-labelledby="h-block">' +
      '<div class="sec-head"><h2 id="h-block">What is stopping this</h2></div>' +
      '<div class="stack">' + game.blockers.map((b) =>
        P.note("error", b.title,
          "<p>" + esc(b.why) + "</p>" +
          (b.command ? '<pre class="console u-mt-3" style="max-height:none">' +
            esc(b.command) + "</pre>" : ""),
          (b.command
            ? '<button class="btn btn--sm btn--primary" data-copy="' + esc(b.command) + '">' +
              esc(b.fixLabel) + "</button>"
            : "") +
          (b.link ? '<a class="btn btn--sm' + (b.command ? " btn--ghost" : " btn--primary") +
            '" href="' + esc(b.link) + '">' + esc(b.fixLabel) + "</a>" : ""))
      ).join("") + "</div></section>";
  }

  /* -------------------------------------------------------------- flow */
  function flowSection() {
    return '<section class="band" aria-labelledby="h-flow">' +
      '<div class="sec-head"><h2 id="h-flow">How far it has got</h2></div>' +
      '<div class="card"><div class="flow">' + game.steps.map((s, i) => {
        const glyph = s.state === "done" ? "✓" : s.state === "blocked" ? "!" : String(i + 1);
        return '<div class="flow__step" data-state="' + esc(s.state) + '">' +
          '<span class="flow__dot" aria-hidden="true">' + glyph + "</span>" +
          "<div><div class=\"flow__label\">" + esc(s.label) + "</div>" +
          '<div class="flow__detail">' + esc(s.detail || s.say) + "</div></div>" +
          '<span class="flow__aside">' + esc(
            s.state === "done" ? "done" : s.state === "active" ? "working"
              : s.state === "blocked" ? "stopped" : "not yet") + "</span></div>";
      }).join("") + "</div></div></section>";
  }

  /* -------------------------------------------------------- live output */
  function liveSection() {
    return '<section class="band" id="live" aria-labelledby="h-live">' +
      '<div class="sec-head"><h2 id="h-live">Live output</h2>' +
      '<button class="btn btn--sm btn--danger" id="cancel-run">Stop processing</button></div>' +
      '<div class="card card--flush"><div class="card-head">' +
      '<span class="spinner" id="live-spin"></span>' +
      '<span class="dim small" id="live-status">connecting…</span></div>' +
      '<pre class="console" id="live-log" data-scroll-region ' +
      'aria-live="polite" aria-label="Processing output"></pre></div>' +
      '<p class="dim small u-mt-3">This is the raw output of the job. You do not need to read ' +
      "it — the steps above are the same story in plain language — but it is here when " +
      "something goes wrong.</p></section>";
  }

  function startLivePolling() {
    const log = document.getElementById("live-log");
    const status = document.getElementById("live-status");
    const cancel = document.getElementById("cancel-run");
    if (!log) return;

    if (cancel) {
      cancel.addEventListener("click", async () => {
        cancel.setAttribute("aria-disabled", "true");
        const r = await P.api.cancelRun();
        P.toast(r && r.ok ? "Stopping…" : ((r && r.error) || "Could not stop it"),
          r && r.ok ? "ok" : "error");
      });
    }

    const tick = async () => {
      const snap = await P.api.runStatus(logCursor);
      if (!snap || snap.offline) {
        status.textContent = "No tracker is running behind this page — this is the last " +
          "recorded state, not a live one.";
        const spin = document.getElementById("live-spin");
        if (spin) spin.remove();
        clearInterval(poller);
        return;
      }
      (snap.lines || []).forEach((line) => {
        const el = document.createElement("span");
        el.textContent = line + "\n";
        if (/error|failed|traceback/i.test(line)) el.className = "ln-err";
        else if (/warn/i.test(line)) el.className = "ln-warn";
        else if (/\bok\b|done|complete/i.test(line)) el.className = "ln-ok";
        log.appendChild(el);
      });
      if (snap.next != null) logCursor = snap.next;
      if (snap.lines && snap.lines.length) log.scrollTop = log.scrollHeight;
      status.textContent = snap.running
        ? (snap.label || snap.job || "working") + " · " + (snap.elapsed || 0) + "s"
        : "Finished — " + (snap.status || "no status recorded");
      if (!snap.running) {
        const spin = document.getElementById("live-spin");
        if (spin) spin.remove();
        clearInterval(poller);
        status.innerHTML += ' · <a href="' + esc(location.href) + '">reload for the result</a>';
      }
    };
    tick();
    poller = setInterval(tick, 1800);
    addEventListener("beforeunload", () => clearInterval(poller));
  }

  /* ------------------------------------------------------- review call */
  function reviewCallSection() {
    return '<section class="band" aria-labelledby="h-rev">' +
      '<div class="sec-head"><h2 id="h-rev">Your turn</h2></div>' +
      P.note("warn", "This game is waiting for a person",
        "<p>The detector has read every hero it could find and scored how sure it is. " +
        "Nothing here reaches the public pages until someone confirms it. The review " +
        "workspace shows each read next to the actual frame it came from, so confirming " +
        "a clean map takes seconds.</p>",
        '<a class="btn btn--sm btn--primary" href="review.html?game=' +
        encodeURIComponent(game.id) + '">Open the review workspace</a>') +
      "</section>";
  }

  /* ---------------------------------------------------------- published
     The fan-facing view. Everything here is approved data only — the
     same gate the exporter enforces, applied again at render time. */
  function publishedSection() {
    const maps = game.maps || [];
    if (!maps.length) {
      return '<section class="band"><div class="sec-head"><h2>Maps</h2></div>' +
        P.empty("◇", "No maps were recorded for this game",
          "The tracker read the video but did not resolve it into individual maps.") +
        "</section>";
    }
    return '<section class="band" aria-labelledby="h-maps">' +
      '<div class="sec-head"><h2 id="h-maps">Maps and line-ups</h2>' +
      '<span class="dim small">Every hero shown was confirmed by a person</span></div>' +
      '<div class="stack-lg">' + maps.map(mapCard).join("") + "</div></section>";
  }

  function mapCard(m) {
    const info = P.mapInfo(m.map);
    const comps = P.publishedComps((c) => c.mapId === m.id);
    const byTeam = {};
    comps.forEach((c) => {
      if (!byTeam[c.teamId] || c.timestamp < byTeam[c.teamId].timestamp) byTeam[c.teamId] = c;
    });
    const swaps = ((P.pub && P.pub.heroSwaps) || [])
      .filter((s) => s.mapId === m.id && s.status === "confirmed");
    const rejected = ((P.pub && P.pub.rejectedSwaps) || []).filter((s) => s.mapId === m.id);

    const side = (teamId) => {
      const c = byTeam[teamId];
      const t = P.team(teamId);
      return '<div class="stack">' +
        '<div class="u-flex u-between u-center u-gap-3 u-wrap">' +
        P.teamPlate(teamId, { link: true, win: m.winner === teamId }) +
        (c ? P.confMeter(c.confidence) : "") + "</div>" +
        (c
          ? P.compStrip(c.heroes, { link: true }) +
            '<p class="dim small">Read at ' + esc(P.fmtClock(c.timestamp)) +
            " into the broadcast" +
            (c.source === "manual" ? " · corrected by a person" : "") +
            (c.evidenceFrame
              ? ' · <button class="btn btn--sm btn--quiet" data-evidence="' +
                esc(c.evidenceFrame) + '" data-evidence-cap="' +
                esc((t ? t.name : "") + " opening line-up") + '">See the frame</button>'
              : "") + "</p>"
          : '<p class="dim small">No approved line-up for ' +
            esc(t ? t.name : "this team") + " on this map yet.</p>") +
        "</div>";
    };

    return '<article class="card rv">' +
      '<div class="u-flex u-between u-center u-gap-4 u-wrap">' +
        "<div><h3>" + esc(info.name) + '</h3><p class="dim small u-mt-3">' +
        esc(info.mode || m.mode || "Mode not recorded") +
        (m.roundCount ? " · " + m.roundCount + " rounds" : "") + "</p></div>" +
        P.scorePlate(m.scoreA, m.scoreB,
          m.winner === game.teamA ? "a" : m.winner === game.teamB ? "b" : null) +
      "</div>" +
      '<div class="grid grid--2 u-mt-5">' + side(game.teamA) + side(game.teamB) + "</div>" +
      (swaps.length
        ? '<div class="u-mt-5"><p class="label">Confirmed mid-map swaps</p>' +
          '<div class="stack u-mt-3">' + swaps.map(swapRow).join("") + "</div></div>"
        : "") +
      (rejected.length
        ? P.diag(rejected.length + " swap candidate(s) the tracker rejected",
          '<p class="dim small" style="margin-bottom:var(--s-3)">Kept on the record on ' +
          "purpose: a rejected read is evidence about the detector, and hiding it would make " +
          "the published data look more certain than it is.</p>" +
          '<div class="stack">' + rejected.map((s) =>
            '<div class="well"><div class="u-flex u-center u-gap-3 u-wrap">' +
            P.heroTile(s.fromHero, { size: "xs" }) +
            '<span class="diff__arrow">→</span>' + P.heroTile(s.toHero, { size: "xs" }) +
            '<span class="dim small">at ' + esc(P.fmtClock(s.offset)) + "</span></div>" +
            '<p class="dim small u-mt-3">' + esc(s.reason || "no reason recorded") + "</p></div>"
          ).join("") + "</div>")
        : "") +
      "</article>";
  }

  function swapRow(s) {
    return '<div class="well"><div class="u-flex u-center u-gap-3 u-wrap">' +
      P.teamPlate(s.teamId, { size: "sm", short: true }) +
      '<span class="dim small">slot ' + esc(s.slot) + "</span>" +
      '<span class="diff"><span class="diff__was">' + P.heroTile(s.fromHero, { size: "sm" }) +
      '</span><span class="diff__arrow">→</span>' + P.heroTile(s.toHero, { size: "sm" }) + "</span>" +
      '<span class="dim small">at ' + esc(P.fmtClock(s.offset)) + "</span>" +
      (s.confidence != null ? P.confMeter(s.confidence) : "") +
      '<span class="spacer"></span>' +
      (s.evidenceBefore
        ? '<button class="btn btn--sm btn--quiet" data-evidence="' + esc(s.evidenceBefore) +
          '" data-evidence-cap="before the swap">Before</button>' : "") +
      (s.evidenceAfter
        ? '<button class="btn btn--sm btn--quiet" data-evidence="' + esc(s.evidenceAfter) +
          '" data-evidence-cap="after the swap">After</button>' : "") +
      "</div></div>";
  }

  /* ------------------------------------------------------- diagnostics
     Everything an engineer wants and a first-time visitor should never
     have to see. One collapsed block, at the bottom, off by default. */
  function diagnosticsSection() {
    const r = game.run || {};
    const pairs = [
      ["Game id", game.id],
      ["Kind", game.kind === "run" ? "processing run (no match record yet)" : "match record"],
      ["Capture status", game.captureStatus],
      ["Capture run", game.captureRunId],
      ["Source", r.source || (r.sourceId || null)],
      ["Layout", r.layout],
      ["Window", game.window || (r.window && (r.window.start + "–" + r.window.end)) || null],
      ["Detector", (r.detectorVersion) || null],
      ["Clip", r.clipMode || (r.clipResolution &&
        (r.clipResolution.width + "×" + r.clipResolution.height)) || null],
      ["Report", r.reportPath || r.reportDir || null],
      ["Broadcast URL", game.sourceUrl],
      ["FACEIT", game.faceitUrl],
    ];
    let extra = "";
    if (r.steps && r.steps.length) {
      extra += '<p class="label u-mt-4">Pipeline steps</p><div class="table-wrap u-mt-3">' +
        '<table class="tbl"><thead><tr><th>Step</th><th>Status</th><th>Detail</th></tr></thead><tbody>' +
        r.steps.map((s) => "<tr><td>" + esc(s.name) + "</td><td>" + esc(s.status) +
          "</td><td>" + esc(s.detail || "") + "</td></tr>").join("") +
        "</tbody></table></div>";
    }
    if (r.preflight && r.preflight.checks) {
      extra += '<p class="label u-mt-4">Readiness checks at run time</p>' +
        '<div class="table-wrap u-mt-3"><table class="tbl"><thead><tr><th>Check</th>' +
        "<th>Status</th><th>Detail</th></tr></thead><tbody>" +
        r.preflight.checks.map((c) => "<tr><td>" + esc(c.name) + "</td><td>" +
          esc(c.status) + "</td><td>" + esc(c.detail || "") + "</td></tr>").join("") +
        "</tbody></table></div>";
    }
    return '<section class="band">' +
      P.diag("Technical details for this game", P.dl(pairs) + extra) + "</section>";
  }
})();
