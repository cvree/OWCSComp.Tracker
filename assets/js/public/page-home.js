/* =====================================================================
   OWCS Comp Tracker — public/page-home.js
   The front door. Its job is to answer, in this order:
     1. what is this site,
     2. how much has it actually proven so far (real numbers, never
        rounded up, never a "coming soon"),
     3. what one finished result looks like,
     4. where to go next.
   Every number here comes from window.OWCS_PUBLIC through the same
   credibility gate the rest of the site uses (P.publicComps).
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS_PUB, D = P.data, esc = P.esc;
  const S = window.OWCS_STATS || {};
  const $ = P.$;

  /* ---------- coverage: the honest size of the dataset --------------- */
  const comps = P.publicComps ? P.publicComps() : [];
  const summary = S.summary ? S.summary() : {
    comps: comps.length, verifiedMaps: 0, matches: 0, heroesSeen: 0,
  };
  const swaps = (D && D.heroSwaps ? D.heroSwaps : []).filter((s) => s.status === "confirmed");
  const rejected = (D && D.rejectedSwaps ? D.rejectedSwaps : []).length;
  const tracked = (D && D.matches ? D.matches : []).length;
  const teamsWithComps = new Set(comps.map((c) => c.teamId)).size;

  /* ---------- 1 · calls to action ------------------------------------ */
  const featured = pickFeatured();

  function pickFeatured() {
    if (!D || !D.matches) return null;
    const byMatch = new Map();
    comps.forEach((c) => byMatch.set(c.matchId, (byMatch.get(c.matchId) || 0) + 1));
    let best = null, bestN = 0;
    D.matches.forEach((m) => {
      const n = byMatch.get(m.id) || 0;
      if (n > bestN) { best = m; bestN = n; }
    });
    return best;
  }

  $("#home-cta").innerHTML = [
    featured
      ? `<a class="btn btn--gold" href="match.html?id=${esc(featured.id)}">See a verified match</a>`
      : `<a class="btn btn--gold" href="matches.html">Browse matches</a>`,
    `<a class="btn" href="stats.html">Hero pick rates</a>`,
    /* The portal is the only thing on this site a visitor can *use* rather
       than read, so it earns a place in the hero rather than a link near
       the footer. */
    `<a class="btn" href="portal.html">Run it on a broadcast</a>`,
    `<a class="btn btn--ghost" href="how-it-works.html">How it works →</a>`,
  ].join("");

  /* ---------- 2 · the coverage sentence -------------------------------
     Deliberately a sentence, not a dashboard: a first-time visitor needs
     to know how young this dataset is before they read a percentage. */
  const gen = D && D.meta ? D.meta.generatedAt : null;
  $("#home-coverage").innerHTML =
    `<b>Everything on this site right now:</b> ` +
    [
      `${summary.matches || 0} match${summary.matches === 1 ? "" : "es"} with verified compositions`,
      `${summary.verifiedMaps || 0} map${summary.verifiedMaps === 1 ? "" : "s"}`,
      `${comps.length} composition snapshot${comps.length === 1 ? "" : "s"}`,
      `${swaps.length} confirmed mid-map swap${swaps.length === 1 ? "" : "s"}`,
      `${summary.heroesSeen || 0} hero${summary.heroesSeen === 1 ? "" : "es"} sighted`,
    ].join(" · ") +
    `. ${tracked > (summary.matches || 0)
      ? `${tracked - (summary.matches || 0)} further match${tracked - (summary.matches || 0) === 1 ? " is" : "es are"} tracked as schedule facts only — no compositions read from them yet. `
      : ""}` +
    `We would rather show you a handful of proven maps than a season of guesses` +
    (gen ? ` · dataset built ${esc(P.fmtLocal(gen))}` : "") + `.`;

  /* ---------- 3 · the four doors -------------------------------------- */
  const doors = [
    {
      href: "matches.html", kicker: "Results",
      title: "Matches",
      body: "Every series the tracker follows, in your local time — and exactly how far the "
        + "capture pipeline got on each one. Open one to see both line-ups and the frames.",
      metric: `${tracked} tracked`,
    },
    {
      href: "stats.html", kicker: "The meta",
      title: "Hero stats",
      body: "Pick and win rates computed only from compositions a human reviewed or that "
        + "cleared the high-confidence gate. Every row drills down to the maps behind it.",
      metric: `${summary.heroesSeen || 0} heroes with data`,
    },
    {
      href: "teams.html", kicker: "Dossiers",
      title: "Teams",
      body: "Record, hero pool and match history per roster. Teams we have not captured yet "
        + "say so plainly instead of showing you an empty chart.",
      metric: `${teamsWithComps} with verified comps`,
    },
    {
      href: "swaps.html", kicker: "The hard part",
      title: "Swap evidence",
      body: "Mid-map hero switches, each with the before and after crop the detector saw — "
        + "plus the ledger of suspected swaps we threw out, and why.",
      metric: `${swaps.length} confirmed · ${rejected} rejected`,
    },
  ];
  $("#home-doors").innerHTML = doors.map((d) => `
    <a class="card card--link card--spot home-door rv" href="${esc(d.href)}">
      <span class="hd-kicker">${esc(d.kicker)}</span>
      <h3>${esc(d.title)}</h3>
      <p>${esc(d.body)}</p>
      <span class="hd-metric mono">${esc(d.metric)}</span>
    </a>`).join("") + `
    <div class="home-more">
      <span class="dim">Also:</span>
      <a href="calendar.html">Calendar</a>
      <a href="comps.html">Full comp list</a>
      <a href="heroes.html">Hero directory</a>
      <a href="maps.html">Maps</a>
      <a href="tournaments.html">Tournaments</a>
    </div>`;

  /* ---------- 4 · the featured verified map --------------------------- */
  function featuredHtml() {
    if (!featured) {
      return P.emptyState("⛨", "No verified compositions yet",
        `Nothing has cleared review in this dataset. When a map is captured and reviewed it
         appears here first. <a href="how-it-works.html">What review means →</a>`);
    }
    const mine = comps.filter((c) => c.matchId === featured.id);
    const mapId = mine.length ? mine[0].mapId : null;
    const mapRow = (featured.maps || []).find((x) => x.id === mapId) || (featured.maps || [])[0];
    const mi = mapRow ? P.mapInfo(mapRow.map) : null;
    /* one line-up per team: the first verified snapshot on that map */
    const sides = [featured.teamA, featured.teamB].map((tid) => {
      const c = mine.find((x) => x.teamId === tid && (!mapId || x.mapId === mapId));
      return { teamId: tid, comp: c };
    });
    const mySwaps = swaps.filter((s) => s.matchId === featured.id);
    const winner = mapRow && mapRow.winner ? P.team(mapRow.winner) : null;
    return `
      <div class="card home-featured rv">
        <div class="hf-head">
          <div class="cluster">
            ${P.chipStatus(featured.status)}
            ${mi ? `<span class="badge">${esc(mi.name)}</span><span class="map-mode">${esc(mapRow.mode || "")}</span>` : ""}
          </div>
          <a class="ev-tick" href="match.html?id=${esc(featured.id)}&tab=evidence">see the evidence →</a>
        </div>
        <div class="hf-sides">
          ${sides.map((s) => `
            <div class="hf-side${winner && winner.id === s.teamId ? " hf-side--win" : ""}">
              <div class="split">
                ${P.teamPlate(s.teamId, { link: true })}
                ${winner && winner.id === s.teamId
                  ? `<span class="chip" data-cap="verified">won this map</span>` : ""}
              </div>
              ${s.comp
                ? P.heroStrip(s.comp.heroes, { link: true })
                : `<p class="dim" style="font-size:13px;margin:0">No reviewed line-up for this side yet.</p>`}
            </div>`).join("")}
        </div>
        ${mySwaps.length ? `<div class="hf-swaps">
          <b>${mySwaps.length} confirmed swap${mySwaps.length === 1 ? "" : "s"} mid-map:</b>
          ${mySwaps.slice(0, 4).map((s) =>
            `<span class="hf-swap mono">${esc(P.hero(s.fromHero).name)} → ${esc(P.hero(s.toHero).name)}
             <span class="faint">@ ${esc(P.fmtOffset(s.offset))}</span></span>`).join("")}
          <a class="ev-tick" href="swaps.html">all swap evidence →</a>
        </div>` : ""}
        <p class="hf-foot dim">
          Read from the broadcast VOD, checked by a human, and stored with the crop behind every
          slot. Open the match to walk the chain: composition → capture run → sampled frames →
          hero crops → review status.</p>
        <div class="cluster">
          <a class="btn btn--gold" href="match.html?id=${esc(featured.id)}">Open the full match</a>
          <a class="btn" href="comps.html">Every verified composition</a>
        </div>
      </div>`;
  }
  $("#home-featured").innerHTML = featuredHtml();

  /* ---------- 5 · how it works, short ---------------------------------- */
  const steps = [
    ["Find the broadcast",
     "An OWCS VOD is registered — pasted in by hand, or found automatically on the official channels."],
    ["Learn the HUD",
     "The calibrator works out where the ten hero portraits sit in this broadcast's layout, and refuses to continue if it isn't sure."],
    ["Read every slot",
     "Frames are sampled across the map and each portrait is template-matched against real, harvested hero crops. A slot the detector can't prove returns UNKNOWN instead of a guess."],
    ["A human confirms",
     "Nothing reaches these pages on the machine's word alone: identities, maps and detections pass a human gate, and manual corrections always beat the detector."],
    ["Publish with receipts",
     "Only approved compositions are exported — each one carrying the run, frames and crops it came from, which is what every link on this site points back to."],
  ];
  $("#home-steps").innerHTML = steps.map(([t, b], i) => `
    <li class="rv"><span class="hs-num mono">${String(i + 1).padStart(2, "0")}</span>
      <div><h3>${esc(t)}</h3><p>${esc(b)}</p></div></li>`).join("");

  /* ---------- 6 · limits ----------------------------------------------- */
  const seriesScored = (D && D.matches ? D.matches : []).filter((m) => m.scoreA != null).length;
  const limits = [
    ["Coverage is small, on purpose",
     `${summary.verifiedMaps || 0} map${summary.verifiedMaps === 1 ? "" : "s"} of `
     + `${summary.matches || 0} match${summary.matches === 1 ? "" : "es"} carry verified `
     + `compositions today. Rates computed from that are real, but they are a sample, not a season.`],
    ["Series scores are not invented",
     seriesScored
       ? "Series scores are shown only where they were imported as facts; the rest stay blank."
       : "No series score in this dataset has been imported yet, so match scorelines show “–” rather than a number nobody verified."],
    ["Timings are sample-accurate",
     "Round boundaries and swap timestamps come from sampling the video every few seconds, so they can be off by about one sample. They are never presented as frame-exact."],
    ["Match facts and comps never mix",
     "Teams, schedules and scores come from official/imported facts. Hero compositions only ever come from reviewed video capture — one can never be used to fill in the other."],
  ];
  $("#home-limits").innerHTML = limits.map(([t, b]) => `
    <div class="card limit-card rv"><h3>${esc(t)}</h3><p>${esc(b)}</p></div>`).join("")
    + `<p class="dim" style="grid-column:1/-1;margin:0;font-size:13px">
        The complete list, including how a composition can be corrected after publication, is in
        <a href="how-it-works.html#limits">How it works</a>.</p>`;

  P.observeReveals(document);
  if (window.OWCSMotion) window.OWCSMotion.observe(document);
})();
