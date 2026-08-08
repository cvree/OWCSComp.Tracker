/* =====================================================================
   OWCS Comp Tracker — app/page-review.js
   The review workspace.

   THE RULE THIS FILE EXISTS TO ENFORCE
   ------------------------------------
   A human decision never overwrites raw detection evidence. What the
   detector saw — the hero, the confidence, the frame it read, the
   detector version — stays exactly as recorded. A correction is an
   ADDITIONAL, attributed, reversible layer on top of it, and both are
   kept side by side so anyone can see what changed and why.

   That is why every decision here produces a changeset entry carrying:
     what the detector said · what the person said · the evidence path ·
     who decided · when · why
   and why the export path is `corrections/corrections.json`, which is
   committed to git — so git history is the audit trail.

   SPEED
   -----
   Reviewing one game is easy; reviewing forty is the actual job. So:
     · the whole keyboard works (j/k move, A approve, C correct, F flag,
       Shift+A approves every clean read on the map, N next map)
     · a clean map can be cleared in one keystroke
     · nothing blocks on the network — decisions are local first, and
       synced to a connected tracker in the background
     · work survives a reload (kept per game in this browser)
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const G = P.games;
  const esc = P.esc;
  const $ = (id) => document.getElementById(id);

  /* Below this, the pipeline itself refuses to auto-publish. Mirrored
     here so "clean" on this screen means the same thing it means there. */
  const CLEAN_FLOOR = 0.90;

  let current = null;          // the game being reviewed
  let decisions = {};          // key -> decision record
  let liveInbox = null;        // numeric ids from a connected tracker
  let focusRow = -1;

  /* ------------------------------------------------------------- boot */
  document.addEventListener("DOMContentLoaded", () => {
    P.evidence.wire(document);
    P.api.mountStatus("mode-status", {
      readonlyTitle: "Reviewing here produces a correction file, not a live write",
      readonlyBody: "<p>There is no tracker behind this page, so decisions cannot be written " +
        "straight to the record. They are kept in this browser and exported as " +
        "<code>corrections/corrections.json</code> — the same file the pipeline reads, and the " +
        "same one git keeps as the audit trail. Nothing is lost and nothing is faked.</p>",
    });

    $("reviewer").value = P.api.reviewer();
    $("reviewer").addEventListener("change", (e) => P.api.setReviewer(e.target.value));

    renderQueue();
    const want = P.qs("game");
    const queue = reviewable();
    /* Open on something there is actually work to do on. A game that is
       waiting but carries no detections yet is a dead end to land on —
       it goes to the bottom of the queue, not the front of the screen. */
    const withWork = queue.filter((g) => stintsOf(g).some(needsPerson));
    select(want && G.byId(want) ? G.byId(want) : withWork[0] || queue[0] || null);

    wireKeys();
    wirePicker();

    P.api.probe().then(() => {
      if (!P.api.isConnected()) return;
      P.api.reviewInbox().then((r) => {
        if (r && r.items) { liveInbox = r.items; renderWorkspace(); }
      });
    });
  });

  /* ------------------------------------------------------------ queue */
  function reviewable() {
    /* A game is reviewable if it is waiting, or if it has any detection
       that has not been through a person. Published games stay listed
       when they still carry unreviewed stints — re-review is legitimate
       and hiding it would make the record look cleaner than it is. */
    return G.all().filter((g) => g.state === "review" || stintsOf(g).some(needsPerson));
  }

  function renderQueue() {
    const items = reviewable();
    const host = $("queue");
    if (!items.length) {
      host.innerHTML = '<div style="padding:var(--s-5)">' +
        P.empty("✓", "Nothing waiting",
          "Every detection on record has been through a person.") + "</div>";
      return;
    }
    host.innerHTML = items.map((g) => {
      const pending = stintsOf(g).filter(needsPerson).length;
      const done = Object.keys(decisionsFor(g.id)).length;
      return '<button class="qitem" type="button" data-game="' + esc(g.id) + '"' +
        (current && current.id === g.id ? ' aria-current="true"' : "") + ">" +
        '<span class="qitem__t">' + esc(g.title) + "</span>" +
        '<span class="qitem__m">' + P.stateChip(g.state) +
        (pending ? "<span>" + pending + " unconfirmed</span>" : "<span>all confirmed</span>") +
        (done ? '<span style="color:var(--gold-hi)">' + done + " decided</span>" : "") +
        "</span></button>";
    }).join("");
    host.onclick = (e) => {
      const b = e.target.closest("[data-game]");
      if (b) select(G.byId(b.dataset.game));
    };
  }

  function select(game) {
    current = game;
    decisions = game ? decisionsFor(game.id) : {};
    focusRow = -1;
    if (game) P.setQs({ game: game.id });
    renderQueue();
    renderWorkspace();
  }

  /* ------------------------------------------------------------- data */
  function stintsOf(game) {
    if (!game) return [];
    return ((P.pub && P.pub.heroStints) || []).filter((s) => s.matchId === game.id);
  }
  function swapsOf(game) {
    if (!game) return [];
    return ((P.pub && P.pub.heroSwaps) || []).concat((P.pub && P.pub.rejectedSwaps) || [])
      .filter((s) => s.matchId === game.id)
      /* the export lists a rejected swap in both arrays */
      .filter((s, i, arr) => arr.findIndex((x) => x.id === s.id) === i);
  }
  const needsPerson = (s) => s.reviewStatus !== "reviewed" && s.reviewStatus !== "rejected";
  const isClean = (s) =>
    s.meanConfidence != null && s.meanConfidence >= CLEAN_FLOOR &&
    (s.minConfidence == null || s.minConfidence >= CLEAN_FLOOR * 0.9);

  /* ------------------------------------------------- decision storage */
  const storeKey = (gameId) => "owcs.review." + gameId;
  function decisionsFor(gameId) {
    try { return JSON.parse(localStorage.getItem(storeKey(gameId)) || "{}"); }
    catch (e) { return {}; }
  }
  function persist() {
    if (!current) return;
    try { localStorage.setItem(storeKey(current.id), JSON.stringify(decisions)); }
    catch (e) { P.toast("This browser will not store the review locally", "error"); }
  }

  /* The audit record. Both sides of every change, always. */
  function decide(stint, verdict, correctedHero, note) {
    decisions[stint.id] = {
      kind: "stint",
      stintId: stint.id,
      matchId: stint.matchId,
      mapId: stint.mapId,
      teamId: stint.teamId,
      slot: stint.slot,
      verdict: verdict,                       // approved | corrected | flagged
      detected: {
        hero: stint.hero,
        meanConfidence: stint.meanConfidence,
        minConfidence: stint.minConfidence,
        observations: stint.observations,
        detectorVersion: stint.detectorVersion,
        evidenceStart: stint.evidenceStart,
        evidenceEnd: stint.evidenceEnd,
      },
      corrected: correctedHero || null,
      note: note || null,
      reviewer: P.api.reviewer() || "anonymous",
      decidedAt: new Date().toISOString(),
    };
    persist();
    syncOne(stint, verdict);
  }

  function decideSwap(swap, verdict) {
    decisions["swap:" + swap.id] = {
      kind: "swap",
      swapId: swap.id,
      matchId: swap.matchId,
      mapId: swap.mapId,
      teamId: swap.teamId,
      slot: swap.slot,
      verdict: verdict,
      detected: {
        fromHero: swap.fromHero, toHero: swap.toHero,
        offset: swap.offset, confidence: swap.confidence,
        status: swap.status, reason: swap.reason,
        evidenceBefore: swap.evidenceBefore, evidenceAfter: swap.evidenceAfter,
      },
      reviewer: P.api.reviewer() || "anonymous",
      decidedAt: new Date().toISOString(),
    };
    persist();
    syncSwap(swap, verdict);
  }

  function decideBoundary(map, verdict, rounds) {
    decisions["map:" + map.id] = {
      kind: "mapBoundary",
      mapId: map.id,
      matchId: current.id,
      verdict: verdict,
      detected: { rounds: map.rounds || [] },
      corrected: rounds || null,
      reviewer: P.api.reviewer() || "anonymous",
      decidedAt: new Date().toISOString(),
    };
    persist();
  }

  /* ------------------------------------------------- live write-through
     Only approve/reject can be written straight to a connected tracker —
     that is exactly what the desktop API exposes, and it sets
     manual_override so a later detector pass cannot silently undo it.
     A hero CORRECTION has no such route by design: it goes through
     corrections.json so it lands in git with its justification. */
  function inboxMatch(stint) {
    if (!liveInbox) return null;
    return liveInbox.filter((i) =>
      i.kind === "stint" && i.matchId === stint.matchId &&
      i.teamId === stint.teamId && i.slot === stint.slot &&
      Number(i.startOffset) === Number(stint.start))[0] || null;
  }
  function syncOne(stint, verdict) {
    if (!P.api.isConnected() || verdict === "flagged") return;
    const hit = inboxMatch(stint);
    if (!hit) return;
    P.api.reviewDecide("stint", hit.id, verdict === "approved" ? "approve" : "reject",
      P.api.reviewer()).then((r) => {
      if (!r || r.ok === false) P.toast("Saved locally, but the tracker refused it: " +
        ((r && r.error) || "unknown reason"), "error");
    });
  }
  function syncSwap(swap, verdict) {
    if (!P.api.isConnected() || !liveInbox) return;
    const hit = liveInbox.filter((i) =>
      i.kind === "swap" && i.matchId === swap.matchId && i.teamId === swap.teamId &&
      i.slot === swap.slot && Number(i.offsetSeconds) === Number(swap.offset))[0];
    if (!hit) return;
    P.api.reviewDecide("swap", hit.id, verdict === "approved" ? "approve" : "reject",
      P.api.reviewer());
  }

  /* -------------------------------------------------------- workspace */
  function renderWorkspace() {
    const host = $("workspace");
    if (!current) {
      host.innerHTML = P.empty("✓", "Nothing to review",
        "Every detection on record has been confirmed by a person. " +
        '<a href="submit.html">Submit another game</a> to give the queue something to do.');
      return;
    }

    const stints = stintsOf(current);
    if (!stints.length) {
      host.innerHTML =
        '<div class="card"><h2>' + esc(current.title) + "</h2>" +
        '<p class="dim u-mt-3">This game is waiting for review, but the published export ' +
        "carries no per-slot detections for it yet — so there is nothing here to confirm. " +
        "That usually means detection has not finished, or the export has not been " +
        "regenerated since it did.</p>" +
        '<div class="row u-mt-5"><a class="btn" href="game.html?id=' +
        encodeURIComponent(current.id) + '">Open the game</a>' +
        '<a class="btn btn--ghost" href="tools.html">Diagnostics</a></div></div>';
      return;
    }

    const maps = mapsOf(current, stints);
    host.innerHTML =
      headerBlock(stints) +
      maps.map((m) => mapBlock(m, stints)).join("") +
      changesetBlock();

    wireWorkspace();
    /* Deliberately NOT calling observeReveals here. The workspace is a
       work surface, re-rendered after every single decision; replaying an
       entrance animation on each render slides the row you are aiming at
       out from under the pointer. Reveals belong on pages you read, not
       on the one you operate. */
  }

  function mapsOf(game, stints) {
    const byId = new Map();
    (game.maps || []).forEach((m) => byId.set(m.id, m));
    /* a stint can reference a map the match record does not list */
    stints.forEach((s) => {
      if (!byId.has(s.mapId)) byId.set(s.mapId, { id: s.mapId, map: null, order: 99, rounds: [] });
    });
    return Array.from(byId.values()).sort((a, b) => (a.order || 0) - (b.order || 0));
  }

  function headerBlock(stints) {
    const pending = stints.filter(needsPerson).length;
    const decided = Object.keys(decisions).filter((k) => k.indexOf(":") < 0).length;
    const clean = stints.filter((s) => needsPerson(s) && isClean(s) && !decisions[s.id]).length;
    return '<div class="card card--raised" style="position:sticky;top:calc(var(--header-h) + 8px);' +
      'z-index:20;margin-bottom:var(--s-5)">' +
      '<div class="u-flex u-between u-center u-gap-4 u-wrap">' +
      "<div><h2 style=\"font-size:1.25rem\">" + esc(current.title) + "</h2>" +
      '<p class="dim small u-mt-3">' + stints.length + " detected slot" +
      (stints.length === 1 ? "" : "s") + " · " + pending + " unconfirmed · " +
      decided + " decided by you</p></div>" +
      '<div class="row">' +
      (clean
        ? '<button class="btn btn--good" id="approve-clean">Approve ' + clean +
          " clean read" + (clean === 1 ? "" : "s") + ' <kbd>⇧A</kbd></button>'
        : "") +
      '<a class="btn btn--ghost" href="game.html?id=' + encodeURIComponent(current.id) +
      '">Open the game</a>' +
      "</div></div>" +
      '<p class="dim small u-mt-4">A read is “clean” when the detector agreed with itself ' +
      "across the whole stint at " + Math.round(CLEAN_FLOOR * 100) + "% or better — the same " +
      "threshold the pipeline uses before it will publish anything without asking. " +
      "Everything else is listed individually below.</p></div>";
  }

  /* ------------------------------------------------------------- a map */
  function mapBlock(m, allStints) {
    const info = m.map ? P.mapInfo(m.map) : { name: "Unidentified map", mode: "" };
    const stints = allStints.filter((s) => s.mapId === m.id);
    const teams = [];
    [current.teamA, current.teamB].forEach((t) => { if (t) teams.push(t); });
    stints.forEach((s) => { if (teams.indexOf(s.teamId) < 0) teams.push(s.teamId); });

    const swaps = swapsOf(current).filter((s) => s.mapId === m.id);

    return '<section class="card card--flush u-mt-5" data-map="' + esc(m.id) + '">' +
      '<div class="card-head">' +
        "<h3>" + esc(info.name) + "</h3>" +
        '<span class="chip chip--outline">' + esc(info.mode || m.mode || "mode unknown") + "</span>" +
        '<span class="spacer"></span>' +
        '<button class="btn btn--sm btn--good" data-approve-map="' + esc(m.id) + '">' +
        "Approve this map</button>" +
      "</div>" +
      boundaryBlock(m) +
      teams.map((t) => teamBlock(m, t, stints.filter((s) => s.teamId === t))).join("") +
      (swaps.length ? swapBlock(m, swaps) : "") +
      "</section>";
  }

  /* ------------------------------------------------- map boundaries */
  function boundaryBlock(m) {
    const rounds = m.rounds || [];
    const d = decisions["map:" + m.id];
    if (!rounds.length) {
      return '<div class="card-body" style="border-bottom:1px solid var(--line)">' +
        '<p class="label">Where the map starts and ends</p>' +
        '<p class="dim small u-mt-3">No round boundaries were recorded for this map, so ' +
        "there is nothing to confirm here. The line-ups below are still reviewable.</p></div>";
    }
    const total = rounds[rounds.length - 1].end - rounds[0].start || 1;
    return '<div class="card-body" style="border-bottom:1px solid var(--line)">' +
      '<div class="u-flex u-between u-center u-gap-3 u-wrap">' +
      '<p class="label" style="margin:0">Where the map starts and ends</p>' +
      (d
        ? '<span class="chip" data-state="evidence"><span class="dot"></span>' +
          esc(d.verdict === "approved" ? "Boundaries confirmed" : "Boundaries flagged") + "</span>"
        : '<div class="row"><button class="btn btn--sm btn--good" data-bound="approved" ' +
          'data-map="' + esc(m.id) + '">Boundaries look right</button>' +
          '<button class="btn btn--sm btn--ghost" data-bound="flagged" data-map="' +
          esc(m.id) + '">Flag them</button></div>') +
      "</div>" +
      '<div class="u-mt-4" style="display:grid;gap:6px">' + rounds.map((r) => {
        const left = ((r.start - rounds[0].start) / total) * 100;
        const width = Math.max(2, ((r.end - r.start) / total) * 100);
        return '<div class="u-flex u-center u-gap-3">' +
          '<span class="mono small dim" style="min-width:104px">' +
          esc(P.fmtClock(r.start)) + " → " + esc(P.fmtClock(r.end)) + "</span>" +
          '<span style="flex:1;position:relative;height:10px;background:rgba(150,165,205,.12);' +
          'border-radius:999px"><span style="position:absolute;left:' + left.toFixed(1) +
          "%;width:" + width.toFixed(1) + '%;top:0;bottom:0;background:var(--cyan);' +
          'border-radius:999px;opacity:.75"></span></span>' +
          '<span class="dim small u-nowrap">round ' + esc(r.index) + "</span>" +
          (r.confidence != null ? P.confMeter(r.confidence) : "") + "</div>";
      }).join("") + "</div></div>";
  }

  /* -------------------------------------------------- one team's slots */
  function teamBlock(m, teamId, stints) {
    if (!stints.length) {
      return '<div class="card-body" style="border-bottom:1px solid var(--line)">' +
        '<div class="u-flex u-center u-gap-3">' + P.teamPlate(teamId, { size: "sm" }) +
        '<span class="dim small">Nothing was detected for this team on this map.</span>' +
        "</div></div>";
    }
    stints.sort((a, b) => (a.slot - b.slot) || (a.start - b.start));

    const detectedComp = stints.filter((s, i, arr) =>
      arr.findIndex((x) => x.slot === s.slot) === i).map((s) => currentHero(s));

    return '<div style="border-bottom:1px solid var(--line)">' +
      '<div class="card-body" style="padding-bottom:0">' +
        '<div class="u-flex u-between u-center u-gap-4 u-wrap">' +
          '<div class="u-flex u-center u-gap-4 u-wrap">' +
            P.teamPlate(teamId, { size: "sm" }) +
            P.compStrip(detectedComp, { size: "sm" }) +
          "</div>" +
          '<button class="btn btn--sm btn--ghost" data-approve-team="' + esc(teamId) +
          '" data-map="' + esc(m.id) + '">Approve this line-up</button>' +
        "</div>" +
      "</div>" +
      stints.map((s) => slotRow(s)).join("") +
      "</div>";
  }

  function currentHero(s) {
    const d = decisions[s.id];
    return (d && d.corrected) || s.hero;
  }

  function slotRow(s) {
    const d = decisions[s.id];
    const shown = currentHero(s);
    const changed = d && d.corrected && d.corrected !== s.hero;
    const status = d ? d.verdict
      : s.reviewStatus === "reviewed" ? "already-reviewed" : null;

    const heroCell = changed
      ? '<span class="diff"><span class="diff__was">' + P.heroTile(s.hero, { size: "sm" }) +
        '</span><span class="diff__arrow" aria-label="corrected to">→</span>' +
        P.heroTile(shown, { size: "sm" }) + "</span>"
      : P.heroTile(shown, { size: "sm" });

    const why = [];
    why.push("slot " + s.slot);
    why.push(P.fmtClock(s.start) + "–" + P.fmtClock(s.end));
    if (s.observations != null) why.push(s.observations + " frames agreed");
    if (s.detectorVersion) why.push(s.detectorVersion);

    return '<div class="slot" data-slot="' + esc(s.id) + '"' +
      (status ? ' data-decision="' + esc(status === "already-reviewed" ? "approved" : status) + '"' : "") +
      ' tabindex="-1">' +
      P.evidence.thumb(s.evidenceStart,
        P.hero(s.hero).name + " · slot " + s.slot + " · " + P.fmtClock(s.start)) +
      heroCell +
      '<div class="slot__body">' +
        '<div class="slot__who">' + esc(P.hero(shown).name) +
        (changed ? ' <span class="dim small">(detector said ' + esc(P.hero(s.hero).name) + ")</span>" : "") +
        "</div>" +
        '<div class="slot__why">' + esc(why.join(" · ")) + "</div>" +
      "</div>" +
      '<div class="slot__actions">' +
        P.confMeter(s.meanConfidence) +
        (status
          ? '<span class="chip" data-state="' +
            (status === "flagged" ? "review" : status === "corrected" ? "detected" : "evidence") +
            '"><span class="dot"></span>' +
            esc(status === "already-reviewed" ? "Confirmed earlier"
              : status === "approved" ? "Approved"
              : status === "corrected" ? "Corrected" : "Flagged") + "</span>" +
            '<button class="btn btn--sm btn--quiet" data-undo="' + esc(s.id) + '">Undo</button>'
          : '<button class="btn btn--sm btn--good" data-act="approve" data-slot="' + esc(s.id) +
            '" title="Approve (A)">Approve</button>' +
            '<button class="btn btn--sm" data-act="correct" data-slot="' + esc(s.id) +
            '" title="Correct the hero (C)">Correct</button>' +
            '<button class="btn btn--sm btn--ghost" data-act="flag" data-slot="' + esc(s.id) +
            '" title="Flag as uncertain (F)">Flag</button>') +
      "</div></div>";
  }

  /* ------------------------------------------------------------ swaps */
  function swapBlock(m, swaps) {
    return '<div class="card-body">' +
      '<p class="label">Mid-map hero swaps</p>' +
      '<p class="dim small u-mt-3" style="max-width:70ch">A swap is only published once ' +
      "someone confirms it. The detector's own verdict is shown so you can see whether you " +
      "are agreeing with it or overruling it — both are recorded.</p>" +
      '<div class="stack u-mt-4">' + swaps.map((s) => {
        const d = decisions["swap:" + s.id];
        return '<div class="well"><div class="u-flex u-center u-gap-3 u-wrap">' +
          P.evidence.thumb(s.evidenceBefore, "before the swap") +
          '<span class="diff"><span class="diff__was">' + P.heroTile(s.fromHero, { size: "sm" }) +
          '</span><span class="diff__arrow">→</span>' + P.heroTile(s.toHero, { size: "sm" }) + "</span>" +
          P.evidence.thumb(s.evidenceAfter, "after the swap") +
          '<div class="slot__body"><div class="slot__who">' +
          esc(P.hero(s.fromHero).name + " → " + P.hero(s.toHero).name) + "</div>" +
          '<div class="slot__why">' + esc((P.team(s.teamId) || {}).code || s.teamId) +
          " · slot " + esc(s.slot) + " · " + esc(P.fmtClock(s.offset)) +
          " · detector said " + esc(s.status) +
          (s.reason ? " (" + esc(s.reason) + ")" : "") + "</div></div>" +
          '<span class="spacer"></span>' +
          (s.confidence != null ? P.confMeter(s.confidence) : "") +
          (d
            ? '<span class="chip" data-state="' + (d.verdict === "approved" ? "evidence" : "review") +
              '"><span class="dot"></span>' + esc(d.verdict === "approved" ? "Confirmed" : "Rejected") +
              '</span><button class="btn btn--sm btn--quiet" data-undo="swap:' + esc(s.id) +
              '">Undo</button>'
            : '<button class="btn btn--sm btn--good" data-swap="' + esc(s.id) +
              '" data-verdict="approved">Confirm</button>' +
              '<button class="btn btn--sm btn--ghost" data-swap="' + esc(s.id) +
              '" data-verdict="rejected">Not a swap</button>') +
          "</div></div>";
      }).join("") + "</div></div>";
  }

  /* ------------------------------------------------------- changeset */
  function changesetBlock() {
    const list = Object.keys(decisions).map((k) => decisions[k]);
    if (!list.length) {
      return '<div class="card u-mt-5"><p class="label">Your changes</p>' +
        '<p class="dim small u-mt-3">Nothing decided yet. Approve, correct or flag a read ' +
        "above and it appears here — with both what the detector said and what you said.</p></div>";
    }
    const corrections = list.filter((d) => d.kind === "stint" && d.verdict === "corrected");
    const flagged = list.filter((d) => d.verdict === "flagged");

    return '<div class="card card--raised u-mt-6" id="changeset">' +
      '<div class="u-flex u-between u-center u-gap-3 u-wrap">' +
      '<div><p class="label" style="margin:0">Your changes</p>' +
      '<p class="dim small u-mt-3">' + list.length + " decision" + (list.length === 1 ? "" : "s") +
      " · " + corrections.length + " hero correction" + (corrections.length === 1 ? "" : "s") +
      " · " + flagged.length + " flagged</p></div>" +
      '<div class="row">' +
      '<button class="btn btn--sm btn--primary" id="export-corrections">' +
      "Download corrections.json</button>" +
      '<button class="btn btn--sm btn--ghost" id="export-log">Download the review log</button>' +
      '<button class="btn btn--sm btn--quiet" id="clear-decisions">Discard my changes</button>' +
      "</div></div>" +
      (corrections.length
        ? '<div class="table-wrap u-mt-4"><table class="tbl">' +
          "<thead><tr><th>Map</th><th>Team</th><th>Slot</th><th>Detector said</th>" +
          "<th>You said</th><th>Confidence it had</th></tr></thead><tbody>" +
          corrections.map((d) => "<tr><td>" + esc(mapName(d.mapId)) + "</td><td>" +
            esc((P.team(d.teamId) || {}).code || d.teamId) + "</td><td>" + esc(d.slot) +
            '</td><td><span class="diff__was">' + esc(P.hero(d.detected.hero).name) +
            "</span></td><td><b>" + esc(P.hero(d.corrected).name) + "</b></td><td>" +
            (d.detected.meanConfidence != null
              ? Math.round(d.detected.meanConfidence * 100) + "%" : "not scored") +
            "</td></tr>").join("") + "</tbody></table></div>"
        : "") +
      '<p class="dim small u-mt-4" style="max-width:74ch">' +
      "Downloading writes <code>corrections/corrections.json</code>. Commit it and the next " +
      "pipeline run applies your corrections as manual-source data that overrides the " +
      "detector — <b>without deleting what the detector saw</b>. Remove the entry and re-run " +
      "to undo it. Git history is the audit trail." +
      (P.api.isConnected()
        ? " Approvals and rejections have already been written to this machine's record."
        : "") + "</p></div>";
  }

  const mapName = (mapId) => {
    const m = (current.maps || []).filter((x) => x.id === mapId)[0];
    return m && m.map ? P.mapInfo(m.map).name : mapId;
  };

  /* ---------------------------------------------------------- wiring */
  function wireWorkspace() {
    const host = $("workspace");

    host.onclick = (e) => {
      const act = e.target.closest("[data-act]");
      if (act) {
        const s = stintById(act.dataset.slot);
        if (!s) return;
        if (act.dataset.act === "approve") { decide(s, "approved"); refresh(); }
        if (act.dataset.act === "flag") { decide(s, "flagged", null, "flagged as uncertain by the reviewer"); refresh(); }
        if (act.dataset.act === "correct") openPicker(s);
        return;
      }
      const undo = e.target.closest("[data-undo]");
      if (undo) { delete decisions[undo.dataset.undo]; persist(); refresh(); return; }

      const sw = e.target.closest("[data-swap]");
      if (sw) {
        const swap = swapsOf(current).filter((x) => x.id === sw.dataset.swap)[0];
        if (swap) { decideSwap(swap, sw.dataset.verdict); refresh(); }
        return;
      }
      const bound = e.target.closest("[data-bound]");
      if (bound) {
        const m = (current.maps || []).filter((x) => x.id === bound.dataset.map)[0];
        if (m) { decideBoundary(m, bound.dataset.bound); refresh(); }
        return;
      }
      const team = e.target.closest("[data-approve-team]");
      if (team) { approveMany(team.dataset.map, team.dataset.approveTeam); return; }
      const map = e.target.closest("[data-approve-map]");
      if (map) { approveMany(map.dataset.approveMap, null); return; }

      if (e.target.closest("#approve-clean")) { approveClean(); return; }
      if (e.target.closest("#export-corrections")) { exportCorrections(); return; }
      if (e.target.closest("#export-log")) { exportLog(); return; }
      if (e.target.closest("#clear-decisions")) {
        if (!confirm("Discard every decision you have made on this game? " +
          "Approvals already written to a connected tracker are not undone by this.")) return;
        decisions = {};
        persist();
        refresh();
      }
    };
  }

  function stintById(id) {
    return stintsOf(current).filter((s) => s.id === id)[0] || null;
  }

  function approveMany(mapId, teamId) {
    let n = 0;
    stintsOf(current).forEach((s) => {
      if (mapId && s.mapId !== mapId) return;
      if (teamId && s.teamId !== teamId) return;
      if (decisions[s.id]) return;
      decide(s, "approved");
      n++;
    });
    P.toast(n ? "Approved " + n + " read" + (n === 1 ? "" : "s") : "Nothing left to approve here",
      n ? "ok" : "info");
    refresh();
  }

  function approveClean() {
    let n = 0;
    stintsOf(current).forEach((s) => {
      if (decisions[s.id] || !needsPerson(s) || !isClean(s)) return;
      decide(s, "approved");
      n++;
    });
    P.toast(n ? "Approved " + n + " clean read" + (n === 1 ? "" : "s")
      : "No clean reads left — the rest need looking at", n ? "ok" : "info");
    refresh();
  }

  /* Re-rendering must not move the reviewer. Two things fight that:
     the scroll position (Lenis owns the scroller, so window.scrollTo alone
     gets overridden by the smoothing on the next frame), and keyboard
     focus (a re-render destroys the focused row). Both are restored, and
     the row that was being worked on keeps the cursor — landing back at
     the top of a forty-row map after every single decision is the
     difference between reviewing one game and reviewing forty. */
  function refresh(keepSlotId) {
    const y = window.scrollY;
    const anchor = keepSlotId ||
      (focusRow >= 0 && P.$$(".slot[data-slot]")[focusRow]
        ? P.$$(".slot[data-slot]")[focusRow].dataset.slot : null);

    renderWorkspace();
    renderQueue();

    const lenis = window.OWCSMotion && window.OWCSMotion.lenis;
    if (lenis) lenis.scrollTo(y, { immediate: true, force: true });
    else window.scrollTo(0, y);

    if (!anchor) return;
    const rows = P.$$(".slot[data-slot]");
    const i = rows.findIndex((r) => r.dataset.slot === anchor);
    if (i >= 0) { focusRow = i; rows[i].focus({ preventScroll: true }); }
  }

  /* ----------------------------------------------------- hero picker */
  let pickerFor = null;
  let pickerActive = 0;
  let pickerList = [];

  function openPicker(stint) {
    pickerFor = stint;
    const t = P.team(stint.teamId);
    $("picker-context").innerHTML =
      "The detector read <b>" + esc(P.hero(stint.hero).name) + "</b> in slot " + esc(stint.slot) +
      " for " + esc(t ? t.name : stint.teamId) + " at " + esc(P.fmtClock(stint.start)) +
      ", with " + (stint.meanConfidence != null
        ? Math.round(stint.meanConfidence * 100) + "% confidence" : "no confidence score") +
      ". Picking a different hero records both, and never deletes what it saw.";
    $("picker-q").value = "";
    renderPickerGrid("");
    $("picker-modal").hidden = false;
    $("picker-q").focus();
  }
  function closePicker(keepId) {
    $("picker-modal").hidden = true;
    pickerFor = null;
    /* Back to the row you were correcting — never to the top of the map. */
    const row = keepId
      ? document.querySelector('.slot[data-slot="' + window.CSS.escape(keepId) + '"]')
      : null;
    if (row) row.focus({ preventScroll: true });
  }

  function renderPickerGrid(q) {
    const query = String(q || "").trim().toLowerCase();
    pickerList = P.heroes().filter((h) =>
      !query || h.name.toLowerCase().indexOf(query) >= 0 || h.id.indexOf(query) >= 0);
    pickerActive = 0;
    $("picker-grid").innerHTML = pickerList.length
      ? pickerList.map((h, i) =>
        '<button type="button" class="picker__opt" role="option" data-hero="' + esc(h.id) +
        '" data-active="' + (i === 0) + '" aria-selected="' + (i === 0) + '">' +
        P.heroTile(h.id, { size: "sm" }) +
        '<span class="hero__name">' + esc(h.name) + "</span></button>").join("")
      : '<p class="dim small">No hero matches “' + esc(q) + "”.</p>";
  }

  function wirePicker() {
    const modal = $("picker-modal");
    modal.addEventListener("click", (e) => {
      if (e.target.closest("[data-close]")) {
        closePicker(pickerFor && pickerFor.id);
        return;
      }
      const opt = e.target.closest("[data-hero]");
      if (opt && pickerFor) {
        const id = pickerFor.id;
        decide(pickerFor, "corrected", opt.dataset.hero, "corrected in the review workspace");
        closePicker(id);
        refresh(id);
      }
    });
    $("picker-q").addEventListener("input", (e) => renderPickerGrid(e.target.value));
    $("picker-q").addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        closePicker(pickerFor && pickerFor.id);
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        const h = pickerList[pickerActive];
        if (h && pickerFor) {
          const id = pickerFor.id;
          decide(pickerFor, "corrected", h.id, "corrected in the review workspace");
          closePicker(id);
          refresh(id);
        }
        return;
      }
      if (e.key === "ArrowRight" || e.key === "ArrowLeft" ||
          e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1
          : e.key === "ArrowDown" ? 6 : -6;
        pickerActive = Math.max(0, Math.min(pickerList.length - 1, pickerActive + step));
        P.$$("[data-hero]", $("picker-grid")).forEach((el, i) => {
          el.dataset.active = i === pickerActive;
          el.setAttribute("aria-selected", i === pickerActive);
          if (i === pickerActive) el.scrollIntoView({ block: "nearest" });
        });
      }
    });
  }

  /* -------------------------------------------------------- keyboard */
  function wireKeys() {
    document.addEventListener("keydown", (e) => {
      if (!$("picker-modal").hidden) return;
      if (!$("lightbox").hidden) return;
      const typing = /^(input|textarea|select)$/i.test(e.target.tagName || "") ||
        e.target.isContentEditable;
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;

      const rows = P.$$(".slot[data-slot]");
      if (!rows.length) return;

      const move = (d) => {
        focusRow = Math.max(0, Math.min(rows.length - 1, focusRow + d));
        rows[focusRow].focus();
        rows[focusRow].scrollIntoView({ block: "center" });
      };
      if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); move(1); return; }
      if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); move(-1); return; }
      if (e.key === "A" && e.shiftKey) { e.preventDefault(); approveClean(); return; }

      if (focusRow < 0) return;
      const s = stintById(rows[focusRow].dataset.slot);
      if (!s) return;
      if (e.key === "a" || e.key === "Enter") { e.preventDefault(); decide(s, "approved"); after(); }
      else if (e.key === "c") { e.preventDefault(); openPicker(s); }
      else if (e.key === "f") { e.preventDefault(); decide(s, "flagged", null, "flagged as uncertain by the reviewer"); after(); }
      else if (e.key === "n") {
        e.preventDefault();
        const next = reviewable().filter((g) => g.id !== current.id)[0];
        if (next) select(next);
      }

      function after() {
        const keep = focusRow;
        refresh();
        const fresh = P.$$(".slot[data-slot]");
        focusRow = Math.min(keep + 1, fresh.length - 1);
        if (fresh[focusRow]) { fresh[focusRow].focus(); fresh[focusRow].scrollIntoView({ block: "center" }); }
      }
    });

    /* clicking a row makes it the keyboard cursor */
    document.addEventListener("click", (e) => {
      const row = e.target.closest(".slot[data-slot]");
      if (!row) return;
      focusRow = P.$$(".slot[data-slot]").indexOf(row);
    });
  }

  /* --------------------------------------------------------- exports */
  function exportCorrections() {
    /* corrections.json is per (match, mapOrder, team): the opener comp is
       the five slots at the earliest start, plus any hero that appears
       later in the same map as a swap-in. That is exactly the shape
       pipeline/apply_corrections.py validates. */
    const stints = stintsOf(current);
    const byKey = {};
    stints.forEach((s) => {
      const mapRec = (current.maps || []).filter((m) => m.id === s.mapId)[0];
      const order = mapRec ? mapRec.order : null;
      if (order == null) return;
      const key = order + "|" + s.teamId;
      byKey[key] = byKey[key] || { order: order, team: s.teamId, stints: [] };
      byKey[key].stints.push(s);
    });

    const corrections = [];
    Object.keys(byKey).forEach((k) => {
      const g = byKey[k];
      const touched = g.stints.some((s) => {
        const d = decisions[s.id];
        return d && d.verdict === "corrected";
      });
      if (!touched) return;

      const earliest = Math.min.apply(null, g.stints.map((s) => s.start));
      const opener = [];
      const later = [];
      g.stints.slice().sort((a, b) => (a.slot - b.slot) || (a.start - b.start))
        .forEach((s) => {
          const hero = currentHero(s);
          if (s.start <= earliest) {
            if (opener.indexOf(hero) < 0) opener.push(hero);
          } else if (later.indexOf(hero) < 0) later.push(hero);
        });
      const swaps = later.filter((h) => opener.indexOf(h) < 0);
      const notes = g.stints.filter((s) => decisions[s.id] &&
        decisions[s.id].verdict === "corrected")
        .map((s) => "slot " + s.slot + ": detector read " + P.hero(s.hero).name +
          " (" + (s.meanConfidence != null ? Math.round(s.meanConfidence * 100) + "%" : "unscored") +
          "), reviewed to " + P.hero(currentHero(s)).name);

      corrections.push({
        match: current.id,
        mapOrder: g.order,
        team: g.team,
        openerComp: opener.slice(0, 5),
        swaps: swaps,
        note: notes.join("; "),
        author: P.api.reviewer() || "anonymous",
      });
    });

    if (!corrections.length) {
      P.toast("No hero corrections to export — approvals alone do not change the data", "info");
      return;
    }
    const bad = corrections.filter((c) => c.openerComp.length !== 5);
    if (bad.length) {
      P.toast("Warning: " + bad.length + " line-up(s) do not have exactly 5 heroes — " +
        "the pipeline will reject those entries", "error");
    }
    P.download("corrections.json", JSON.stringify({
      _readme: "Authored in the OWCS Comp Tracker review workspace. Commit as " +
        "corrections/corrections.json; pipeline/apply_corrections.py applies these as " +
        "manual-source comps that override the detector without deleting what it saw.",
      corrections: corrections,
    }, null, 2));
  }

  function exportLog() {
    P.download("review-log-" + current.id + ".json", JSON.stringify({
      schema: "review-log.v1",
      game: current.id,
      title: current.title,
      reviewer: P.api.reviewer() || "anonymous",
      exportedAt: new Date().toISOString(),
      note: "Every decision with the detector's original reading beside it. The detector's " +
        "record is never modified by this file — it exists so a correction can always be " +
        "traced back to what was corrected and why.",
      decisions: Object.keys(decisions).map((k) => decisions[k]),
    }, null, 2));
  }
})();
