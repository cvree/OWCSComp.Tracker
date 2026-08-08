/* =====================================================================
   OWCS Comp Tracker — app/games.js
   One "game" model for the entire product.

   The system underneath has several vocabularies: matches, capture runs,
   ingest runs, video sources, automation jobs. A person submitting a
   broadcast link has exactly one: "my game". This module collapses all
   of them into a single list of games, each carrying the same six-step
   progression, the same five-word state, and — when it is stuck — a
   blocker written as an instruction rather than a stack trace.

   Nothing here invents data. A field the record does not have comes back
   as null and the UI says so.
   ===================================================================== */
(function () {
  "use strict";

  const P = window.OWCS;
  const D = P.pub;
  const W = P.work;
  const G = (P.games = {});

  /* The progression, in the words a person would use. Each step names
     what the machine achieved, not which script ran. */
  const STEPS = [
    { key: "source", label: "Source found",
      say: "We know which broadcast this game is on." },
    { key: "video", label: "Video captured",
      say: "The relevant part of the VOD has been downloaded." },
    { key: "gameplay", label: "Gameplay detected",
      say: "Live play separated from replays, the desk and highlights." },
    { key: "heroes", label: "Heroes detected",
      say: "All ten hero portraits read off the HUD, swaps tracked." },
    { key: "linked", label: "Match linked",
      say: "Teams, maps and scores attached to the detections." },
    { key: "ready", label: "Ready for review",
      say: "Waiting for a person to confirm or correct it." },
  ];
  G.STEPS = STEPS;

  const step = (key, state, detail) => {
    const def = STEPS.filter((s) => s.key === key)[0];
    return { key: key, label: def.label, say: def.say, state: state, detail: detail || "" };
  };

  /* ------------------------------------------------------------------ *
     Published games — a match that has approved comps behind it.
   * ------------------------------------------------------------------ */
  function publishedIds() {
    const ids = new Set();
    P.publishedComps().forEach((c) => ids.add(c.matchId));
    return ids;
  }

  /* ------------------------------------------------------------------ *
     Blockers. Every one has a plain-language "why" and an exact fix.
   * ------------------------------------------------------------------ */
  function blockersForRun(run) {
    const out = [];
    if (!run) return out;
    const det = run.detection || {};
    const reason = String(det.reason || "");

    if (det.status === "skipped" && /no hero templates/i.test(reason)) {
      out.push({
        title: "This broadcast has no hero reference images yet",
        why: "Every production draws the hero portraits slightly differently, so the " +
          "tracker needs one set of reference crops per broadcast before it can name heroes.",
        fixLabel: "Build the reference images",
        command: "python3 pipeline/build_hero_templates.py --layout " + (run.layout || "<layout>"),
        link: null,
      });
    } else if (det.status === "skipped" && /layout|calibrat/i.test(reason)) {
      out.push({
        title: "This broadcast has not been calibrated yet",
        why: "The tracker needs to learn where this production puts the hero portraits " +
          "before it can read them. It works that out from a handful of frames — it does " +
          "not need the video.",
        fixLabel: "Calibrate it (no download)",
        command: "python3 pipeline/calibrate_remote.py --url \"" +
          (run.url || "<broadcast-url>") + "\" --source-id " + (run.source || "<source>") +
          " --out layouts/" + String(run.source || "source").replace(/-/g, "_") + ".json",
        link: "calibrate.html",
      });
    } else if (det.status === "skipped") {
      out.push({
        title: "Hero detection did not run",
        why: reason || "The detection step was skipped and did not say why.",
        fixLabel: "Re-run this game",
        command: "python3 pipeline/run_owcs_auto.py --source " + (run.source || "<source>"),
        link: null,
      });
    }
    if (run.framesKept === 0 && run.framesRaw > 0) {
      out.push({
        title: "No live gameplay was found in this window",
        why: "Every sampled frame looked like a replay, the analyst desk or a highlight, " +
          "so there was nothing to read heroes from. Usually the time window is wrong.",
        fixLabel: "Pick a window that contains live play",
        command: null,
        link: "submit.html",
      });
    }
    (run.captureAttempts || []).forEach((a) => {
      if (a.outcome === "error" && /403|forbidden|sign in|cookie/i.test(String(a.note || ""))) {
        out.push({
          title: "YouTube refused the download",
          why: "The video needs a signed-in session before it will hand over the stream.",
          fixLabel: "Connect a browser session",
          command: null,
          link: "tools.html#downloads",
        });
      }
    });
    if (run.evidenceError) {
      out.push({
        title: "Evidence images could not be written",
        why: String(run.evidenceError),
        fixLabel: "Check free disk space, then re-run",
        command: null, link: "tools.html#storage",
      });
    }
    /* de-duplicate by title — a run can fail the same way twice */
    const seen = new Set();
    return out.filter((b) => (seen.has(b.title) ? false : (seen.add(b.title), true)));
  }

  /* ------------------------------------------------------------------ *
     Build one game from a published/working match.
   * ------------------------------------------------------------------ */
  function fromMatch(m, published) {
    const run = m.captureRunId ? P.captureRun(m.captureRunId) : null;
    const comps = P.publishedComps((c) => c.matchId === m.id);
    const isPublished = published.has(m.id);
    const cap = m.captureStatus || (run && run.status) || null;

    let state, steps;
    if (isPublished) {
      state = "published";
      steps = STEPS.map((s) => step(s.key, "done"));
    } else if (cap === "needs-review") {
      state = "review";
      steps = STEPS.map((s) => step(s.key, s.key === "ready" ? "active" : "done"));
    } else if (cap === "capturing" || cap === "queued") {
      state = cap === "queued" ? "queued" : "working";
      steps = STEPS.map((s, i) => step(s.key, i === 0 ? "done" : i === 1 ? "active" : "todo"));
    } else if (cap === "failed") {
      state = "blocked";
      steps = STEPS.map((s, i) => step(s.key, i === 0 ? "done" : i === 1 ? "blocked" : "todo"));
    } else if (cap === "verified") {
      state = "published";
      steps = STEPS.map((s) => step(s.key, "done"));
    } else {
      state = "queued";
      steps = STEPS.map((s, i) => step(s.key, i === 0 && m.streamUrl ? "done" : "todo"));
    }

    const a = P.team(m.teamA), b = P.team(m.teamB);
    return {
      id: m.id,
      kind: "match",
      href: "game.html?id=" + encodeURIComponent(m.id),
      title: (a ? a.name : "Team A") + " vs " + (b ? b.name : "Team B"),
      teamA: m.teamA, teamB: m.teamB,
      tournamentId: m.tournamentId,
      tournamentName: (P.tournament(m.tournamentId) || {}).name || null,
      scheduledAt: m.scheduledAt || null,
      updatedAt: (run && run.createdAt) || m.scheduledAt || null,
      state: state,
      steps: steps,
      maps: m.maps || [],
      mapCount: (m.maps || []).length,
      compCount: comps.length,
      sourceUrl: m.streamUrl || null,
      faceitUrl: m.faceitUrl || null,
      captureRunId: m.captureRunId || null,
      captureStatus: cap,
      scoreA: m.scoreA, scoreB: m.scoreB, winner: m.winner,
      summary: m.summary || null,
      blockers: [],
      match: m,
      run: run,
    };
  }

  /* ------------------------------------------------------------------ *
     Build one game from a processing run that has no match yet.
     These are the "submitted, still figuring it out" rows — the most
     important thing a dashboard can show and the old site hid on a
     separate Vision Lab page.
   * ------------------------------------------------------------------ */
  function fromRun(r) {
    const steps = [];
    const stepOf = (name) => (r.steps || []).filter((s) => s.name === name)[0] || null;
    const st = (s) => (!s ? "todo" : s.status === "ok" ? "done"
      : s.status === "skipped" ? "blocked" : s.status === "error" ? "blocked" : "active");

    const probe = stepOf("probe");
    const clip = stepOf("clip");
    const filter = stepOf("filter");
    const detect = stepOf("detect");

    steps.push(step("source", st(probe), probe && probe.detail));
    steps.push(step("video", st(clip), clip && clip.detail));
    steps.push(step("gameplay", st(filter),
      filter ? "kept " + (r.framesKept || 0) + " live frame(s) of " + (r.framesRaw || 0) : ""));
    steps.push(step("heroes", st(detect), detect && detect.detail));
    steps.push(step("linked", "todo", "No match record has been attached yet"));
    steps.push(step("ready", "todo", ""));

    const blockers = blockersForRun(r);
    const running = r.finishedAt == null;
    const state = running ? "working" : blockers.length ? "blocked"
      : r.runStatus === "ok" ? "review" : "blocked";

    const src = (W && (W.videoSources || []).filter((s) => s.id === r.source)[0]) || null;
    return {
      id: r.run,
      kind: "run",
      href: "game.html?run=" + encodeURIComponent(r.run),
      title: (src && src.title) || r.source || r.run,
      teamA: null, teamB: null,
      tournamentId: null, tournamentName: null,
      scheduledAt: r.startedAt || null,
      updatedAt: r.finishedAt || r.startedAt || null,
      state: state,
      steps: steps,
      maps: [], mapCount: 0, compCount: 0,
      sourceUrl: r.url || null,
      faceitUrl: null,
      captureRunId: null,
      captureStatus: r.runStatus || null,
      scoreA: null, scoreB: null, winner: null,
      summary: null,
      window: r.window || null,
      blockers: blockers,
      match: null,
      run: r,
      autoRun: r,
    };
  }

  /* ------------------------------------------------------------------ *
     The list.
   * ------------------------------------------------------------------ */
  let CACHE = null;
  G.all = function () {
    if (CACHE) return CACHE;
    const published = publishedIds();
    const out = [];
    ((D && D.matches) || []).forEach((m) => out.push(fromMatch(m, published)));

    /* Processing runs that never produced a match record. Runs against a
       source we already turned into a match are noise on a game list —
       they belong in that game's own diagnostics. */
    const coveredSources = new Set();
    out.forEach((g) => {
      if (g.run && g.run.sourceId) coveredSources.add(g.run.sourceId);
    });
    const seenRun = new Set();
    ((W && W.autoRuns) || []).forEach((r) => {
      if (!r || !r.run) return;
      if (coveredSources.has(r.source)) return;
      /* one row per source: the newest run is the state of that game */
      if (seenRun.has(r.source)) return;
      seenRun.add(r.source);
      out.push(fromRun(r));
    });

    out.sort((x, y) => {
      const rank = { blocked: 0, review: 1, working: 2, queued: 3, published: 4 };
      const dr = rank[x.state] - rank[y.state];
      if (dr) return dr;
      return String(y.updatedAt || "").localeCompare(String(x.updatedAt || ""));
    });
    CACHE = out;
    return out;
  };

  G.byId = (id) => G.all().filter((g) => g.id === id)[0] || null;
  G.byState = (state) => G.all().filter((g) => g.state === state);
  G.counts = function () {
    const c = { published: 0, review: 0, working: 0, queued: 0, blocked: 0, total: 0 };
    G.all().forEach((g) => { c[g.state] = (c[g.state] || 0) + 1; c.total++; });
    return c;
  };

  /* recency-first, for the dashboard */
  G.recent = (n) => G.all().slice().sort((a, b) =>
    String(b.updatedAt || "").localeCompare(String(a.updatedAt || ""))).slice(0, n || 5);

  /* ------------------------------------------------------------------ *
     Renderers shared by the dashboard, the games list and search.
   * ------------------------------------------------------------------ */
  G.railflow = (g) =>
    '<span class="railflow" role="img" aria-label="' +
    P.esc(g.steps.filter((s) => s.state === "done").length + " of " + g.steps.length +
      " steps complete") + '">' +
    g.steps.map((s) => '<span class="railflow__seg" data-state="' + s.state + '"></span>').join("") +
    "</span>";

  G.card = function (g) {
    const teams = g.teamA || g.teamB
      ? '<div class="game-card__teams">' +
        P.teamPlate(g.teamA, { win: g.winner === g.teamA }) +
        '<span class="game-card__vs">vs</span>' +
        P.teamPlate(g.teamB, { win: g.winner === g.teamB }) + "</div>"
      : '<div class="game-card__title u-trunc">' + P.esc(g.title) + "</div>";

    const meta = [];
    if (g.tournamentName) meta.push(P.esc(g.tournamentName));
    if (g.scheduledAt) meta.push(P.esc(P.fmtDate(g.scheduledAt)));
    if (g.mapCount) meta.push(g.mapCount + (g.mapCount === 1 ? " map" : " maps"));
    if (g.window) meta.push("window " + P.esc(g.window));

    const cta = { published: "View match stats", review: "Review this game",
      working: "Watch progress", queued: "See status", blocked: "Fix the blocker" }[g.state];

    return '<a class="game-card" href="' + P.esc(g.href) + '">' +
      '<div class="game-card__top">' + P.stateChip(g.state) +
      (g.state === "published" && g.compCount
        ? '<span class="chip" data-state="evidence"><span class="dot"></span>' + g.compCount +
          " verified line-up" + (g.compCount === 1 ? "" : "s") + "</span>"
        : "") +
      '<span class="spacer"></span>' +
      (g.scoreA != null || g.scoreB != null ? P.scorePlate(g.scoreA, g.scoreB,
        g.winner === g.teamA ? "a" : g.winner === g.teamB ? "b" : null) : "") +
      "</div>" +
      teams +
      (meta.length ? '<div class="game-card__meta">' + meta.join(" · ") + "</div>" : "") +
      G.railflow(g) +
      '<div class="game-card__cta">' + P.esc(cta) + " <span aria-hidden=\"true\">→</span></div>" +
      "</a>";
  };

  G.row = function (g) {
    const label = g.teamA || g.teamB
      ? P.teamPlate(g.teamA, { size: "sm", short: true }) +
        '<span class="game-card__vs">vs</span>' +
        P.teamPlate(g.teamB, { size: "sm", short: true })
      : '<span class="u-trunc">' + P.esc(g.title) + "</span>";
    return "<tr>" +
      "<td><div class=\"u-flex u-center u-gap-3\">" + label + "</div></td>" +
      "<td>" + P.stateChip(g.state) + "</td>" +
      '<td style="min-width:120px">' + G.railflow(g) + "</td>" +
      '<td class="dim small u-nowrap">' + P.esc(g.tournamentName || "—") + "</td>" +
      '<td class="dim small u-nowrap">' + P.esc(g.updatedAt ? P.fmtRel(g.updatedAt) : "—") + "</td>" +
      '<td class="u-nowrap"><a class="btn btn--sm" href="' + P.esc(g.href) + '">Open</a></td>' +
      "</tr>";
  };
})();
