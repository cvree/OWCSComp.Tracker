/* Match detail — the page where every comp shows its receipts. */
(function () {
  "use strict";
  const P = window.OWCS_PUB, D = P.data, esc = P.esc;
  const root = P.$("#m-root");
  const id = P.qs().get("id");
  const m = id && D ? P.match(id) : null;

  if (!m) {
    root.innerHTML = P.breadcrumbs([{ label: "Matches", href: "matches.html" }, { label: "Not found" }]) +
      P.emptyState("⚔", "Match not found",
        `No match with id <code>${esc(id || "(none)")}</code> exists in the current dataset. <a href="matches.html">Back to the schedule</a>.`);
    return;
  }

  const t = P.tournament(m.tournamentId);
  const stage = t && t.stages.find((s) => s.id === m.stageId);
  const round = P.roundsOf(m.tournamentId).find((r) => r.id === m.roundId);
  const run = m.captureRunId ? P.run(m.captureRunId) : null;
  const comps = P.publicComps((c) => c.matchId === m.id);
  const allSnapshotsOfMatch = (D.compSnapshots || []).filter((c) => c.matchId === m.id);
  const hiddenCount = allSnapshotsOfMatch.length -
    allSnapshotsOfMatch.filter((c) => P.APPROVED_REVIEW.includes(c.reviewStatus)).length;
  const teamName = (tid) => (tid ? P.team(tid).name : "TBD");
  document.title = `${teamName(m.teamA)} vs ${teamName(m.teamB)} — OWCS Comp Tracker`;

  /* ---------- header ---------- */
  function head() {
    const winA = m.winner && m.winner === m.teamA, winB = m.winner && m.winner === m.teamB;
    const crumbs = [
      { label: "Tournaments", href: "tournaments.html" },
      t ? { label: t.name, href: `tournament.html?id=${t.id}` } : null,
      stage ? { label: stage.name, href: `tournament.html?id=${t.id}&tab=matches` } : null,
      { label: round ? round.name : "Match" },
    ].filter(Boolean);
    return `
      ${P.breadcrumbs(crumbs)}
      <div class="event-head rv" style="margin-top:12px">
        <div class="cluster" style="justify-content:center">
          ${P.chipStatus(m.status)} ${P.chipCapture(m.captureStatus)}
          ${round ? `<span class="badge">Bo${round.bestOf || m.bestOf}</span>` : `<span class="badge">Bo${m.bestOf}</span>`}
          ${t ? P.badgeRegion(t.region) : ""}
        </div>
        <div class="vs-band">
          <div class="side-a">${P.teamPlate(m.teamA, { size: "lg", win: winA, tbd: m.tbdNote, link: true })}</div>
          <div class="vs-center">
            <div class="vs-score" aria-label="Series score">
              <span class="${winA ? "win" : ""}">${m.scoreA == null ? "–" : m.scoreA}</span>
              <span class="vs-dash">:</span>
              <span class="${winB ? "win" : ""}">${m.scoreB == null ? "–" : m.scoreB}</span>
            </div>
            <span class="mono dim" style="font-size:12px">${esc(P.fmtLocal(m.scheduledAt))} <span class="faint">(${esc(P.fmtRel(m.scheduledAt))})</span></span>
          </div>
          <div class="side-b">${P.teamPlate(m.teamB, { size: "lg", win: winB, tbd: m.tbdNote, link: true })}</div>
        </div>
        ${m.status === "forfeit" ? `<div class="stat-note" style="border-color:color-mix(in srgb,var(--review) 40%,transparent);background:color-mix(in srgb,var(--review) 7%,transparent)">
          <span aria-hidden="true">⚑</span><span>This series ended in a forfeit — the score was awarded, no maps were played, and there is no broadcast to capture.</span></div>` : ""}
        <div class="cluster" style="justify-content:center">
          ${m.streamUrl ? `<a class="btn ${m.status === "live" ? "btn--gold" : ""}" href="${esc(m.streamUrl)}" target="_blank" rel="noopener">${m.status === "live" ? "Watch live ↗" : "Watch VOD ↗"}</a>` : ""}
          ${m.faceitUrl ? `<a class="btn btn--ghost" href="${esc(m.faceitUrl)}" target="_blank" rel="noopener">FACEIT room ↗</a>` : ""}
          ${m.liquipediaUrl ? `<a class="btn btn--ghost" href="${esc(m.liquipediaUrl)}" target="_blank" rel="noopener">Liquipedia ↗</a>` : ""}
        </div>
      </div>`;
  }

  /* ---------- tabs ---------- */
  const TABS = [
    ["overview", "Overview"], ["maps", "Maps", (m.maps || []).length],
    ["bans", "Bans"], ["comps", "Comps", comps.length],
    ["vod", "VOD"], ["evidence", "Evidence"], ["review", "Review"],
  ];
  function tabsHtml() {
    return `<div class="section">
      <div class="tabs" role="tablist" aria-label="Match sections">
        ${TABS.map(([k, label, count], i) =>
          `<button role="tab" id="tab-${k}" data-tab="${k}" aria-controls="panel-${k}" aria-selected="${i === 0}">${esc(label)}${count ? `<span class="tab-count">${count}</span>` : ""}</button>`).join("")}
      </div>
      ${TABS.map(([k]) => `<div role="tabpanel" id="panel-${k}" aria-labelledby="tab-${k}" hidden></div>`).join("")}
    </div>`;
  }

  /* ---------- panels ---------- */
  function mapRow(mp) {
    const winA = mp.winner && mp.winner === m.teamA, winB = mp.winner && mp.winner === m.teamB;
    const mi = P.mapInfo(mp.map);
    return `<div class="map-row rv">
      <span class="map-row__order" aria-hidden="true"><span>${mp.order}</span></span>
      <div class="map-row__body">
        <div class="map-row__name">
          <b>${esc(mi.name)}</b><span class="map-mode">${esc(mp.mode)}</span>
          ${mp.pickNote ? `<span class="faint" style="font-size:11.5px">${esc(mp.pickNote)}</span>` : ""}
          ${mp.live ? `<span class="chip" data-st="live">Live</span>` : ""}
        </div>
        ${P.mapScoreDetail(mp, m.teamA, m.teamB)}
      </div>
      <span class="cluster">
        ${P.scorePlate(mp.scoreA, mp.scoreB, winA ? "a" : winB ? "b" : null)}
        ${mp.winner ? P.teamPlate(mp.winner, { size: "sm", short: true, win: true, link: true }) : ""}
      </span>
    </div>`;
  }
  function panelOverview() {
    const srcRows = (m.sources || []).map((s) =>
      `<dt>${P.badgeSrc(s.type)}</dt><dd>${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url)}</a>` : "manual entry"} <span class="faint">synced ${esc(P.fmtRel(s.lastSynced))}</span></dd>`).join("");
    return `<div class="stack">
      ${m.summary ? `<p class="dim" style="max-width:70ch;margin:0">${esc(m.summary)}</p>` : ""}
      <div class="stat-cards">
        <div class="card stat-card"><span class="sc-num">${(m.maps || []).length || "—"}</span><span class="sc-label">Maps played</span></div>
        <div class="card stat-card"><span class="sc-num">${comps.length}</span><span class="sc-label">Verified comps</span><span class="sc-sub">reviewed or auto-high only</span></div>
        <div class="card stat-card"><span class="sc-num" style="font-size:20px;padding-top:6px">${P.chipCapture(m.captureStatus)}</span><span class="sc-label">Capture pipeline</span></div>
      </div>
      ${(m.maps || []).length ? `<div class="stack-sm">${m.maps.map(mapRow).join("")}</div>`
        : m.status === "upcoming"
          ? P.emptyState("◷", "Not played yet", `Maps appear here after the series ${m.tbdNote ? "opponent is decided and the series " : ""}is played.`)
          : m.status === "forfeit"
            ? P.emptyState("⚑", "No maps — forfeit", "The series was awarded without play.")
            : P.emptyState("🗺", "Map results not imported", "Per-map detail appears after the results import for this match.")}
      ${m.casters && m.casters.length ? `<p class="faint" style="font-size:12.5px;margin:0">Casters: ${m.casters.map(esc).join(", ")}</p>` : ""}
      ${srcRows ? `<details class="ev-more"><summary>Where these match facts come from</summary><dl class="ev-kv" style="margin-top:10px">${srcRows}</dl>
        <p class="faint" style="font-size:12px">Match facts (teams, scores, schedule) come from FACEIT, official pages or manual entry. Hero compositions never come from these sources — only from reviewed video capture.</p></details>` : ""}
    </div>`;
  }

  function panelMaps() {
    if (!(m.maps || []).length)
      return m.status === "upcoming"
        ? P.emptyState("◷", "Maps will appear after the series is played", m.tbdNote ? `Waiting on: ${esc(m.tbdNote)}.` : "Check back after the scheduled start time.")
        : m.status === "forfeit"
          ? P.emptyState("⚑", "No maps — the series was forfeited", "The score was awarded administratively.")
          : P.emptyState("🗺", "Map results not imported yet", "Run the results import to fill this in.");
    return `<div class="stack-sm">${m.maps.map(mapRow).join("")}</div>
      <p class="faint" style="font-size:12px;margin-top:12px">Score widgets are typed per game mode — control shows round capture %, push shows distance, escort/hybrid show points and time bank.</p>`;
  }

  function panelBans() {
    const bans = (D.heroBans || []).filter((b) => b.matchId === m.id);
    if (!bans.length)
      return P.emptyState("🚫", "No bans recorded",
        m.status === "upcoming" ? "Bans are recorded per map once the series is played." : "No ban data was imported for this match.");
    const byMap = new Map();
    bans.forEach((b) => {
      const k = b.mapId || "match";
      if (!byMap.has(k)) byMap.set(k, []);
      byMap.get(k).push(b);
    });
    return `<div class="stack">` + Array.from(byMap.entries()).map(([mapId, list]) => {
      const mp = (m.maps || []).find((x) => x.id === mapId);
      const mi = mp ? P.mapInfo(mp.map) : null;
      return `<div class="card comp-block rv">
        <div class="comp-block__head"><h3>${mi ? esc(mi.name) : "Match-level"}</h3>
          <span class="badge badge--src" data-src="${esc(list[0].source)}">${esc(list[0].source)} fact</span></div>
        <div class="cluster" style="gap:18px">${list.sort((a, b) => a.order - b.order).map((b) =>
          `<span class="cluster" style="gap:8px">${P.teamPlate(b.teamId, { size: "sm", short: true, link: true })}<span class="faint">banned</span>${P.heroTile(b.hero)}</span>`).join("")}</div>
      </div>`;
    }).join("") + `</div>
    <p class="faint" style="font-size:12px;margin-top:12px">Bans are match facts from the import pipeline — never inferred from video.</p>`;
  }

  function panelComps() {
    if (!comps.length) {
      const why = {
        "verified": "The capture run is verified but no snapshots were promoted yet.",
        "needs-review": "The capture run finished, but its detections are still waiting for human review — unreviewed comps never appear here.",
        "capturing": "The broadcast is being captured right now. Comps appear after detection and review.",
        "queued": "The VOD is queued for capture. Comps appear after capture, detection and review.",
        "needs-source": "No VOD is linked for this match yet, so there is nothing to extract comps from.",
        "failed": "The capture run failed — see the Evidence tab for what went wrong.",
      }[m.captureStatus] || "No verified comps exist for this match.";
      return P.emptyState("⛨", "No verified comps to show", esc(why) +
        (hiddenCount ? `<br><span class="mono" style="font-size:11px">${hiddenCount} unreviewed snapshot${hiddenCount > 1 ? "s" : ""} held back</span>` : ""));
    }
    const byMap = new Map();
    comps.forEach((c) => {
      if (!byMap.has(c.mapId)) byMap.set(c.mapId, []);
      byMap.get(c.mapId).push(c);
    });
    const blocks = Array.from(byMap.entries()).map(([mapId, list]) => {
      const mp = (m.maps || []).find((x) => x.id === mapId);
      const mi = mp ? P.mapInfo(mp.map) : { name: mapId };
      const rows = list.sort((a, b) => a.timestamp - b.timestamp || (a.side < b.side ? -1 : 1)).map((c) => {
        const overridden = c.overridesId ? (D.compSnapshots || []).find((x) => x.id === c.overridesId) : null;
        return `<div class="comp-row rv">
          ${P.teamPlate(c.teamId, { size: "sm", link: true })}
          ${P.heroStrip(c.heroes)}
          <div class="stack-sm" style="gap:6px;justify-items:end">
            <span class="cluster" style="gap:6px">
              ${P.badgeSrc(c.source)}
              <span class="chip" data-cap="${c.reviewStatus === "reviewed" ? "verified" : "verified"}">${c.reviewStatus === "reviewed" ? "human reviewed" : "auto-high"}</span>
            </span>
            <span class="mono faint" style="font-size:10.5px">@ ${esc(P.fmtOffset(c.timestamp))} · conf ${(c.confidence * 100).toFixed(0)}%</span>
            <a class="ev-tick" href="?id=${esc(m.id)}&tab=evidence" data-goto-tab="evidence">view evidence</a>
          </div>
        </div>
        ${c.correction ? `<details class="ev-more"><summary>✎ Manual correction applied ${esc(P.fmtRel(c.correction.appliedAt))}</summary>
            <div class="stack-sm" style="margin-top:10px">
              <p style="margin:0;font-size:13px">${esc(c.correction.note)}</p>
              ${overridden ? `<div class="cluster"><span class="faint" style="font-size:12px">Original CV read (kept, never deleted):</span>${P.heroStrip(overridden.heroes, { sm: true })}<span class="mono faint" style="font-size:10.5px">conf ${(overridden.confidence * 100).toFixed(0)}%</span></div>` : ""}
            </div></details>` : ""}`;
      }).join("");
      return `<div class="card comp-block">
        <div class="comp-block__head"><h3>${esc(mi.name)}</h3>${mp ? `<span class="map-mode">${esc(mp.mode)}</span>` : ""}</div>
        ${rows}
      </div>`;
    }).join("");
    const uncoveredMaps = (m.maps || []).filter((mp) => !byMap.has(mp.id));
    return `<div class="stack">
      <div class="stat-note"><span aria-hidden="true">⛨</span>
        <span>Every comp below is either <b>human reviewed</b> or passed the <b>auto-high</b> confidence gate, and links to the frames it was read from. ${hiddenCount ? `${hiddenCount} lower-confidence snapshot${hiddenCount > 1 ? "s are" : " is"} held back pending review.` : ""}</span></div>
      ${blocks}
      ${swapsHtml()}
      ${uncoveredMaps.length ? `<div class="comp-why">No verified comps yet for ${uncoveredMaps.map((mp) => esc(P.mapInfo(mp.map).name)).join(", ")} — the capture run only covered a window of the broadcast, or detections there haven't cleared review.</div>` : ""}
    </div>`;
  }

  /* confirmed swaps for this match (from the DB's temporal-consensus
     verdicts, exported as D.heroSwaps) with before/after evidence crops.
     Rejected candidates are only summarized — they never render as swaps. */
  function swapsHtml() {
    const all = (D.heroSwaps || []).filter((s) => s.matchId === m.id);
    if (!all.length) return "";
    const confirmed = all.filter((s) => s.status === "confirmed")
      .sort((a, b) => (a.offset || 0) - (b.offset || 0));
    const rejected = all.length - confirmed.length;
    const crop = (p, hh) => `<span class="swap-flow__crop" style="width:56px;height:56px">${p
      ? `<img src="${esc(p)}" alt="Broadcast crop — ${esc(hh.name)}" width="56" height="56" loading="lazy">`
      : (P.assets ? P.assets.heroFace(hh, { px: 56 }) : "")}</span>`;
    if (!confirmed.length && !rejected) return "";
    return `<div class="card comp-block">
      <div class="comp-block__head"><h3>Confirmed swaps</h3>
        <span class="chip" data-sw="confirmed">${confirmed.length}</span>
        <a class="ev-tick" style="margin-left:auto" href="swaps.html">swap intelligence ↗</a></div>
      ${confirmed.map((s) => {
        const from = P.hero(s.fromHero), to = P.hero(s.toHero);
        return `<div class="comp-row rv" style="grid-template-columns:minmax(120px,180px) 1fr auto">
          ${P.teamPlate(s.teamId, { size: "sm", link: true })}
          <span class="cluster" style="gap:10px">${crop(s.evidenceBefore, from)}
            <span class="mono dim">${esc(from.name)} → ${esc(to.name)}</span>${crop(s.evidenceAfter, to)}</span>
          <span class="mono faint" style="font-size:10.5px">@ ${esc(P.fmtOffset(s.offset))}${s.confidence != null ? ` · conf ${s.confidence}` : ""}</span>
        </div>`;
      }).join("")}
      ${rejected ? `<div class="comp-why">${rejected} suspected swap${rejected === 1 ? "" : "s"} rejected by temporal consensus (dead-portrait lookalikes, one-frame flickers) — none became a public swap.</div>` : ""}
    </div>`;
  }

  function panelVod() {
    const vods = (D.vodSources || []).filter((v) => (v.matchIds || []).includes(m.id));
    if (!vods.length && !m.streamUrl)
      return P.emptyState("▸", "No VOD linked",
        "Link a broadcast VOD in <a href='sources.html'>Sources</a> to enable capture for this match.");
    return `<div class="stack-sm">
      ${vods.map((v) => `<div class="card m-card"><div class="split">
        <div><b>${esc(v.title)}</b>
          <div class="m-card__meta"><span class="mono">${esc(v.provider)}</span>
          <span class="mono">${v.heightAvailable ? v.heightAvailable + "p available" : "resolution unknown"}</span></div></div>
        <a class="btn" href="${esc(v.url)}" target="_blank" rel="noopener">Open VOD ↗</a></div></div>`).join("")}
      ${!vods.length && m.streamUrl ? `<div class="card m-card"><div class="split"><b>Broadcast stream</b>
        <a class="btn" href="${esc(m.streamUrl)}" target="_blank" rel="noopener">Open ↗</a></div></div>` : ""}
    </div>`;
  }

  function panelEvidence() {
    const chain = (steps) => `<div class="ev-chain" role="list">${steps.map((s, i) =>
      `<span class="ev-step" role="listitem" data-ok="${s.ok ? 1 : 0}">${esc(s.label)}</span>` +
      (i < steps.length - 1 ? `<span class="ev-arrow" aria-hidden="true">→</span>` : "")).join("")}</div>`;
    if (!run) {
      return `<div class="stack">
        ${chain([
          { label: "Match", ok: true },
          { label: "VOD source", ok: false },
          { label: "Capture run", ok: false },
          { label: "Frames + crops", ok: false },
          { label: "Review", ok: false },
        ])}
        ${P.emptyState("⛓", "Evidence chain not started",
          m.status === "forfeit"
            ? "A forfeited series has no broadcast, so no capture run can exist. The scoreline above is an imported match fact."
            : `No capture run exists for this match yet. Start one from the terminal:<br><code>python pipeline/run_owcs_auto.py --source &lt;vod-id&gt; --start H:MM:SS --end H:MM:SS --every 30</code>`)}
      </div>`;
    }
    const resHonesty = run.actualHeight
      ? (run.requestedHeight && run.actualHeight !== run.requestedHeight
        ? `<span style="color:var(--review)">⚠ requested ${run.requestedHeight}p, got ${run.actualHeight}p — flagged before any detection is trusted</span>`
        : `<span style="color:var(--verified)">requested ${run.requestedHeight}p, captured ${run.actualWidth}×${run.actualHeight} ✓</span>`)
      : `<span class="faint">resolution pending (${esc(P.CAPTURE_LABELS[run.status] || run.status)})</span>`;
    return `<div class="stack">
      ${chain([
        { label: "Match", ok: true },
        { label: "VOD " + (run.sourceId || "—"), ok: !!run.sourceId },
        { label: "Run " + run.id, ok: run.status === "verified" },
        { label: (run.frames || []).length + " frames", ok: (run.frames || []).length > 0 },
        { label: comps.length + " approved comps", ok: comps.length > 0 },
      ])}
      <div class="ev-card rv">
        <div class="split"><h3>Capture run ${esc(run.id)}</h3>${P.chipCapture(run.status)}</div>
        <dl class="ev-kv">
          <dt>Window</dt><dd>${run.window ? esc(run.window.start + " → " + (run.window.end || "live") + " · every " + run.window.every + "s") : "—"}</dd>
          <dt>Resolution</dt><dd>${resHonesty}</dd>
          <dt>Clip mode</dt><dd>${esc(run.clipMode || "—")}</dd>
          <dt>Started</dt><dd>${esc(P.fmtLocal(run.createdAt))}</dd>
          ${run.note ? `<dt>Note</dt><dd style="font-family:var(--font-body)">${esc(run.note)}</dd>` : ""}
        </dl>
        ${run.reportPath ? `<div><a class="btn" href="${esc(run.reportPath)}">Open full run report ↗</a></div>` : ""}
      </div>
      ${(run.frames || []).length ? `<div class="ev-card">
        <h3>Sampled frames</h3>
        <div class="ev-grid">${run.frames.map((f) => `
          <figure class="ev-frame" style="margin:0">
            <img src="${esc(f.file)}" alt="Broadcast frame at offset ${esc(P.fmtOffset(f.offset))}" loading="lazy">
            <figcaption>@ ${esc(P.fmtOffset(f.offset))}${f.layoutDebug ? ` · <a href="${esc(f.layoutDebug)}">layout debug</a>` : ""}</figcaption>
          </figure>`).join("")}</div>
      </div>` : run.status === "failed"
        ? P.emptyState("×", "Run failed — no frames", esc(run.note || "See the run report for the failing step and the fix."))
        : P.emptyState("▸", "No frames yet", "Frames appear here as the capture progresses.")}
      ${(run.crops || []).length ? `<details class="ev-more"><summary>Hero HUD crops the detector actually saw (${run.crops.length})</summary>
        <div class="ev-grid" style="margin-top:12px">${run.crops.map((c) => `
          <figure class="ev-frame" style="margin:0"><img src="${esc(c)}" alt="Hero HUD crop ${esc(c.split("/").pop())}" loading="lazy" style="aspect-ratio:1/1">
          <figcaption>${esc(c.split("/").pop())}</figcaption></figure>`).join("")}</div></details>` : ""}
      <p class="faint" style="font-size:12px;margin:0">This is the click-through rule in action: comp → frame → crop → review status. If any link in that chain is missing, the comp doesn't ship.</p>
    </div>`;
  }

  function panelReview() {
    const pending = allSnapshotsOfMatch.filter((c) => !P.APPROVED_REVIEW.includes(c.reviewStatus));
    return `<div class="stack">
      <div class="stat-note" style="border-color:var(--line);background:var(--surface-1)"><span aria-hidden="true">⌨</span>
        <span>This static site never executes anything. Review actions run in your terminal; the page just hands you the exact commands.</span></div>
      ${pending.length ? `<div class="card comp-block">
        <div class="comp-block__head"><h3>Held for review</h3><span class="chip" data-cap="needs-review">${pending.length} item${pending.length > 1 ? "s" : ""}</span></div>
        ${pending.map((c) => `<div class="comp-row">
          ${P.teamPlate(c.teamId, { size: "sm" })}
          ${P.heroStrip(c.heroes, { sm: true })}
          <span class="mono faint" style="font-size:10.5px">conf ${(c.confidence * 100).toFixed(0)}% · ${esc(c.reviewStatus)}</span>
        </div>${c.note ? `<p class="faint" style="font-size:12px;margin:0">${esc(c.note)}</p>` : ""}`).join("")}
      </div>` : `<p class="dim" style="margin:0">Nothing is waiting for review on this match.</p>`}
      <div class="card comp-block">
        <h3>Terminal actions</h3>
        <dl class="ev-kv">
          ${run ? `<dt>Re-run capture</dt><dd>python pipeline/run_owcs_auto.py --source ${esc(run.sourceId || "<vod-id>")} --start ${esc(run.window ? run.window.start : "H:MM:SS")} --end ${esc(run.window && run.window.end ? run.window.end : "H:MM:SS")} --every ${esc(run.window ? String(run.window.every) : "30")}</dd>` : ""}
          <dt>Review workbench</dt><dd><a href="admin.html">admin.html</a> — apply corrections (manual always overrides CV; CV rows are never deleted)</dd>
          <dt>Run reports</dt><dd><a href="runs.html">runs.html</a> — every run's step-by-step status and evidence pages</dd>
        </dl>
      </div>
    </div>`;
  }

  /* ---------- assemble ---------- */
  root.innerHTML = head() + tabsHtml();
  const panels = { overview: panelOverview, maps: panelMaps, bans: panelBans, comps: panelComps, vod: panelVod, evidence: panelEvidence, review: panelReview };
  Object.entries(panels).forEach(([k, fn]) => { P.$("#panel-" + k).innerHTML = fn(); });
  const tabApi = P.initTabs(root.querySelector(".tabs").parentElement, { hashKey: "tab" });
  root.addEventListener("click", (e) => {
    const goto = e.target.closest && e.target.closest("[data-goto-tab]");
    if (goto) { e.preventDefault(); tabApi.select(goto.dataset.gotoTab); }
  });
  P.observeReveals(root);
})();
