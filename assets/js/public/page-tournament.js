/* Tournament detail — event header, tabbed body, readable bracket. */
(function () {
  "use strict";
  const P = window.OWCS_PUB, D = P.data, esc = P.esc;
  const root = P.$("#t-root");
  const id = P.qs().get("id");
  const t = id && D ? P.tournament(id) : null;

  if (!t) {
    root.innerHTML = P.breadcrumbs([{ label: "Tournaments", href: "tournaments.html" }, { label: "Not found" }]) +
      P.emptyState("◈", "Tournament not found",
        `No event with id <code>${esc(id || "(none)")}</code> exists in the current dataset. <a href="tournaments.html">Back to all tournaments</a>.`);
    return;
  }

  const matches = P.matchesOf(t.id);
  const rounds = P.roundsOf(t.id).sort((a, b) => a.order - b.order);
  const nodes = P.bracketNodesOf(rounds.map((r) => r.id));
  const liveMatch = matches.find((m) => m.status === "live");
  document.title = t.name + " — OWCS Comp Tracker";

  /* ---------- event header ---------- */
  function head() {
    const facts = [
      { b: P.fmtRange(t.startsAt, t.endsAt), s: "Dates" },
      { b: t.teamIds.length ? t.teamIds.length + " teams" : "TBD", s: "Field" },
      t.prizePool ? { b: t.prizePool, s: "Prize pool" } : null,
      { b: t.stages.length ? t.stages.map((s) => s.name).join(" → ") : "Format TBA", s: "Format" },
    ].filter(Boolean);
    const srcBadges = (t.sources || []).map((s) =>
      s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${P.badgeSrc(s.type)}</a>` : P.badgeSrc(s.type)).join(" ");
    return `
      ${P.breadcrumbs([{ label: "Tournaments", href: "tournaments.html" }, { label: t.name }])}
      <div class="event-head rv" style="margin-top:12px">
        <div class="event-head__top">
          <span class="event-head__logo" aria-hidden="true">${esc(t.series.replace(/[^A-Z0-9]/gi, "").slice(0, 3).toUpperCase() || "OW")}</span>
          <div class="event-head__title">
            <h1>${esc(t.name)}</h1>
            <div class="event-head__badges">
              ${P.badgeRegion(t.region)} ${P.badgeTier(t.tier)} ${P.chipStatus(t.status)}
              ${t.winnerTeamId ? `<span class="badge" style="color:var(--gold);border-color:color-mix(in srgb,var(--gold) 50%,transparent)">🏆 ${esc(P.team(t.winnerTeamId).name)}</span>` : ""}
            </div>
            ${t.summary ? `<p class="dim" style="margin:0;max-width:64ch">${esc(t.summary)}</p>` : ""}
          </div>
        </div>
        <div class="event-head__facts">
          ${facts.map((f) => `<span class="f"><b>${esc(f.b)}</b><span>${esc(f.s)}</span></span>`).join("")}
        </div>
        <div class="cluster">
          ${srcBadges}
          <span class="freshness">as of ${esc(P.fmtLocal(D.meta.generatedAt))}</span>
        </div>
        ${liveMatch ? `<div class="callout-live">
            <span class="chip" data-st="live">Live</span>
            <span><b>${esc(teamName(liveMatch.teamA))}</b> vs <b>${esc(teamName(liveMatch.teamB))}</b> — ${esc(roundName(liveMatch.roundId))}</span>
            <a class="btn btn--gold" href="match.html?id=${esc(liveMatch.id)}">Open match</a>
            ${liveMatch.streamUrl ? `<a class="btn" href="${esc(liveMatch.streamUrl)}" target="_blank" rel="noopener">Watch stream ↗</a>` : ""}
          </div>` : ""}
      </div>`;
  }
  function teamName(tid) { return tid ? P.team(tid).name : "TBD"; }
  function roundName(rid) { const r = rounds.find((x) => x.id === rid); return r ? r.name : ""; }

  /* ---------- tabs scaffold ---------- */
  const TABS = [
    ["overview", "Overview"], ["bracket", "Bracket"], ["matches", "Matches", matches.length],
    ["standings", "Standings"], ["teams", "Teams", t.teamIds.length],
    ["maps", "Maps & bans"], ["vods", "VODs"], ["capture", "Capture status"],
  ];
  function tabsHtml() {
    return `<div class="section">
      <div class="tabs" role="tablist" aria-label="Tournament sections">
        ${TABS.map(([k, label, count], i) =>
          `<button role="tab" id="tab-${k}" data-tab="${k}" aria-controls="panel-${k}" aria-selected="${i === 0}">${esc(label)}${count ? `<span class="tab-count">${count}</span>` : ""}</button>`).join("")}
      </div>
      ${TABS.map(([k]) => `<div role="tabpanel" id="panel-${k}" aria-labelledby="tab-${k}" hidden></div>`).join("")}
    </div>`;
  }

  /* ---------- panels ---------- */
  function matchCard(m) {
    const winA = m.winner && m.winner === m.teamA, winB = m.winner && m.winner === m.teamB;
    return `<a class="card card--link card--spot m-card rv" href="match.html?id=${esc(m.id)}">
      <div class="m-card__meta">
        ${P.chipStatus(m.status)} ${P.chipCapture(m.captureStatus)}
        <span>${esc(roundName(m.roundId) || stageName(m.stageId))}</span>
        <span class="mono">${esc(P.fmtLocal(m.scheduledAt))}</span>
      </div>
      <div class="m-card__row">
        <div class="m-card__teams">
          ${P.teamPlate(m.teamA, { win: winA, tbd: m.tbdNote })}
          ${P.teamPlate(m.teamB, { win: winB, tbd: m.tbdNote })}
        </div>
        ${P.scorePlate(m.scoreA, m.scoreB, winA ? "a" : winB ? "b" : null)}
      </div>
    </a>`;
  }
  function stageName(sid) { const s = t.stages.find((x) => x.id === sid); return s ? s.name : ""; }

  function panelOverview() {
    const next = matches.filter((m) => m.status === "upcoming")
      .sort((a, b) => new Date(a.scheduledAt) - new Date(b.scheduledAt))[0];
    const recent = matches.filter((m) => m.status === "completed" || m.status === "forfeit")
      .sort((a, b) => new Date(b.scheduledAt) - new Date(a.scheduledAt)).slice(0, 3);
    const verified = matches.filter((m) => m.captureStatus === "verified").length;
    return `<div class="stack">
      <div class="stat-cards">
        <div class="card stat-card"><span class="sc-num" data-count-to="${matches.length}">${matches.length}</span><span class="sc-label">Matches</span></div>
        <div class="card stat-card"><span class="sc-num" data-count-to="${verified}">${verified}</span><span class="sc-label">Verified captures</span><span class="sc-sub">runs a human signed off</span></div>
        <div class="card stat-card"><span class="sc-num">${esc(t.stages.length || "—")}</span><span class="sc-label">Stages</span></div>
      </div>
      ${liveMatch ? `<h3>Live now</h3>${matchCard(liveMatch)}` : ""}
      ${next ? `<h3>Up next</h3>${matchCard(next)}` : ""}
      ${recent.length ? `<h3>Latest results</h3><div class="stack-sm">${recent.map(matchCard).join("")}</div>` : ""}
      ${!matches.length ? P.emptyState("◷", "Nothing scheduled yet",
        "Matches appear here once the schedule import runs for this event. Check back after registration closes.") : ""}
    </div>`;
  }

  /* bracket */
  function nodeHtml(bn, isLastCol) {
    const m = bn.matchId ? P.match(bn.matchId) : null;
    const round = rounds.find((r) => r.id === bn.roundId);
    const dropTo = bn.feedsLoserTo ? nodes.find((x) => x.id === bn.feedsLoserTo) : null;
    const dropRound = dropTo ? rounds.find((r) => r.id === dropTo.roundId) : null;
    const done = m && (m.status === "completed" || m.status === "forfeit");
    const onPath = done && !!m.winner;
    const cls = ["b-node", m && m.status === "live" ? "b-node--live" : "",
      done ? "b-node--done" : "", onPath && !isLastCol ? "on-path" : "", isLastCol ? "no-conn" : ""]
      .filter(Boolean).join(" ");
    const teamRow = (tid, score, win) => `
      <div class="b-node__team${win ? " win" : ""}">
        ${P.teamPlate(tid, { size: "sm", short: true, win, tbd: m && m.tbdNote })}
        <span class="sc">${score == null ? "–" : esc(score)}</span>
      </div>`;
    const inner = `
      <div class="b-node__meta">
        <span>${m ? P.chipStatus(m.status) : `<span class="chip" data-st="upcoming">TBD</span>`}</span>
        <span class="mono">${m ? esc(P.fmtLocal(m.scheduledAt, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })) : ""}</span>
      </div>
      ${teamRow(m && m.teamA, m && m.scoreA, m && m.winner && m.winner === m.teamA)}
      ${teamRow(m && m.teamB, m && m.scoreB, m && m.winner && m.winner === m.teamB)}
      ${dropRound ? `<span class="b-node__drop">loser → ${esc(dropRound.name)}</span>` : ""}
      ${m && m.status === "forfeit" ? `<span class="b-node__drop">walkover — see match page</span>` : ""}`;
    return m
      ? `<a class="${cls}" href="match.html?id=${esc(m.id)}" aria-label="${esc(round ? round.name : "")}: ${esc(teamName(m.teamA))} vs ${esc(teamName(m.teamB))}">${inner}</a>`
      : `<div class="${cls}">${inner}</div>`;
  }
  function bracketSection(title, sideRounds, lastColIsEnd) {
    if (!sideRounds.length) return "";
    const cols = sideRounds.map((r, ci) => {
      const rn = nodes.filter((n) => n.roundId === r.id).sort((a, b) => a.position - b.position);
      const last = ci === sideRounds.length - 1 && lastColIsEnd;
      return `<div class="b-col" data-side="${esc(r.side)}" data-col="${ci}">
        <div class="b-col__head"><span>${esc(r.name)}</span><span class="bo">Bo${r.bestOf}</span></div>
        <div class="b-col__body">${rn.map((n) => nodeHtml(n, last)).join("")}</div>
      </div>`;
    }).join("");
    return `<h3 style="margin:18px 0 8px">${esc(title)}</h3>
      <div class="bracket-scroll"><div class="bracket" data-b-section>${cols}</div></div>`;
  }
  function panelBracket() {
    if (!rounds.length)
      return P.emptyState("⑂", "Bracket not published",
        "The bracket appears once seeding is imported for this event.");
    const upper = rounds.filter((r) => r.side === "upper");
    const gf = rounds.filter((r) => r.side === "gf");
    const lower = rounds.filter((r) => r.side === "lower");
    const single = !upper.length && !lower.length;
    return `<div class="stack-sm">
      <div class="bracket-side-switch seg-region" role="group" aria-label="Bracket side">
        ${[["upper", "Upper"], ["lower", "Lower"], ["gf", "Grand Final"]]
          .filter(([s]) => rounds.some((r) => r.side === s))
          .map(([s, l], i) => `<button type="button" data-side-btn="${s}" aria-pressed="${i === 0}">${l}</button>`).join("")}
      </div>
      ${single
        ? bracketSection("Bracket", gf, true)
        : bracketSection("Upper bracket — winners stay, losers drop down", upper.concat(gf), true) +
          bracketSection("Lower bracket — lose here and you're out", lower, false)}
      <div class="bracket-legend">
        <span class="lg"><span class="lg-line"></span> winner's path</span>
        <span class="lg"><span class="lg-line lg-line--n"></span> advances to</span>
        <span class="lg">${P.chipStatus("live")} on air</span>
        <span class="lg"><span class="mono faint">Bo5/Bo7</span> series length</span>
      </div>
    </div>`;
  }

  function panelMatches() {
    if (!matches.length)
      return P.emptyState("◷", "No matches yet",
        "The schedule fills in when matches are imported for this event.");
    const byStage = t.stages.length ? t.stages : [{ id: null, name: "Matches" }];
    return `<div class="stack">` + byStage.map((s) => {
      const ms = matches.filter((m) => (s.id ? m.stageId === s.id : true))
        .sort((a, b) => new Date(a.scheduledAt) - new Date(b.scheduledAt));
      if (!ms.length) return "";
      return `<div><h3 style="margin-bottom:10px">${esc(s.name)}${s.format ? ` <span class="faint" style="text-transform:none;font-family:var(--font-mono);font-size:11px">${esc(s.format)}</span>` : ""}</h3>
        <div class="stack-sm">${ms.map(matchCard).join("")}</div></div>`;
    }).join("") + `</div>`;
  }

  function panelStandings() {
    if (!t.standings || !t.standings.length)
      return P.emptyState("≡", "No standings for this event",
        t.stages.some((s) => s.format === "round-robin")
          ? "Standings appear after group results are imported."
          : "This event has no group stage — see the bracket for progression.");
    return `<div class="grid-cards">` + t.standings.map((g) => `
      <div class="card" style="padding:0;overflow:hidden">
        <div style="padding:12px 16px;border-bottom:1px solid var(--line)"><h3>${esc(g.group)}</h3></div>
        <table class="stat-table" style="min-width:0">
          <thead><tr><th scope="col">Team</th><th scope="col">W–L</th><th scope="col">Maps</th></tr></thead>
          <tbody>${g.rows.map((r, i) => `
            <tr><td>${i < 2 ? `<span title="Advances to playoffs" style="color:var(--gold)">▸</span> ` : ""}${P.teamPlate(r.teamId, { size: "sm", link: true })}</td>
            <td class="num">${r.w}–${r.l}</td><td class="num">${esc(r.mapDiff)}</td></tr>`).join("")}
          </tbody>
        </table>
      </div>`).join("") + `</div>
      <p class="faint" style="font-size:12px;margin-top:10px">▸ marks playoff qualification. Standings come from imported match facts.</p>`;
  }

  function panelTeams() {
    if (!t.teamIds.length)
      return P.emptyState("⚑", "Field not announced",
        "Teams appear once registration or seeding is imported.");
    return `<div class="grid-cards">` + t.teamIds.map((tid) => {
      const tm = P.team(tid);
      const roster = (D.players || []).filter((p) => p.teamId === tid);
      return `<div class="card t-card rv">
        <div class="split">${P.teamPlate(tid, { link: true })}${P.badgeRegion(tm.region)}</div>
        ${roster.length
          ? `<div class="cluster" style="gap:6px">${roster.map((p) =>
              `<span class="chip" title="${esc(p.role)}">${esc(p.handle)}</span>`).join("")}</div>`
          : `<span class="faint" style="font-size:12.5px">Roster not on file yet.</span>`}
      </div>`;
    }).join("") + `</div>`;
  }

  function panelMaps() {
    const played = [];
    matches.forEach((m) => (m.maps || []).forEach((mp) => played.push({ m, mp })));
    const bans = (D.heroBans || []).filter((b) => matches.some((m) => m.id === b.matchId));
    const mapCounts = new Map();
    played.forEach(({ mp }) => mapCounts.set(mp.map, (mapCounts.get(mp.map) || 0) + 1));
    const banCounts = new Map();
    bans.forEach((b) => banCounts.set(b.hero, (banCounts.get(b.hero) || 0) + 1));
    const mapList = Array.from(mapCounts.entries()).sort((a, b) => b[1] - a[1]);
    const banList = Array.from(banCounts.entries()).sort((a, b) => b[1] - a[1]);
    if (!played.length && !bans.length)
      return P.emptyState("🗺", "No map data yet",
        "Maps and bans appear as match results are imported.");
    return `<div class="stack">
      ${mapList.length ? `<div><h3 style="margin-bottom:10px">Maps played</h3><div class="cluster">
        ${mapList.map(([mid, n]) => { const mi = P.mapInfo(mid); return `<span class="chip" title="${esc(mi.mode)}">${esc(mi.name)} · ${n}</span>`; }).join("")}
      </div></div>` : ""}
      ${banList.length ? `<div><h3 style="margin-bottom:10px">Hero bans <span class="badge badge--src" data-src="faceit" style="vertical-align:2px">match facts</span></h3>
        <div class="hero-strip">${banList.map(([h, n]) =>
          `<span style="display:grid;justify-items:center;gap:2px">${P.heroTile(h)}<span class="mono faint" style="font-size:10px">×${n}</span></span>`).join("")}</div>
        <p class="faint" style="font-size:12px;margin-top:8px">Bans are imported match facts (FACEIT / manual) — they are never inferred from video.</p>
      </div>` : ""}
    </div>`;
  }

  function panelVods() {
    const vods = (D.vodSources || []).filter((v) => (v.matchIds || []).some((mid) => matches.some((m) => m.id === mid)));
    if (!vods.length)
      return P.emptyState("▸", "No VODs linked",
        "Link a broadcast VOD in <a href='sources.html'>Sources</a> and it will show up here with the matches it covers.");
    return `<div class="stack-sm">` + vods.map((v) => `
      <div class="card m-card">
        <div class="split">
          <div>
            <b>${esc(v.title)}</b>
            <div class="m-card__meta"><span class="mono">${esc(v.provider)}</span>
              <span class="mono">${v.heightAvailable ? v.heightAvailable + "p available" : "resolution unknown"}</span></div>
          </div>
          <a class="btn" href="${esc(v.url)}" target="_blank" rel="noopener">Open VOD ↗</a>
        </div>
        <div class="cluster">${(v.matchIds || []).filter((mid) => matches.some((m) => m.id === mid))
          .map((mid) => { const m = P.match(mid); return `<a class="chip" href="match.html?id=${esc(mid)}">${esc(P.team(m.teamA) ? P.team(m.teamA).code : "TBD")} v ${esc(P.team(m.teamB) ? P.team(m.teamB).code : "TBD")}</a>`; }).join("")}</div>
      </div>`).join("") + `</div>`;
  }

  function panelCapture() {
    const LADDER = [
      ["needs-source", "No VOD is linked yet — nothing to process."],
      ["queued", "A VOD is linked; the capture run hasn't started."],
      ["capturing", "Frames are being extracted from the broadcast right now."],
      ["needs-review", "The pipeline produced detections that a human hasn't approved yet."],
      ["verified", "A reviewer signed off — comps from this run can appear on the site."],
      ["failed", "The run failed (missing VOD, resolution too low, tooling error) — see the run report."],
    ];
    return `<div class="stack">
      <div class="stat-note"><span aria-hidden="true">⛨</span>
        <span>Comps only reach the public site after their capture run is human-reviewed or clears the high-confidence gate. This table is the honest state of every match in the event.</span></div>
      <div class="stat-table-wrap"><table class="stat-table">
        <thead><tr><th scope="col">Match</th><th scope="col">Status</th><th scope="col">Capture</th><th scope="col">Resolution</th><th scope="col">Evidence</th></tr></thead>
        <tbody>${matches.map((m) => {
          const run = m.captureRunId ? P.run(m.captureRunId) : null;
          const res = run && run.actualWidth ? `${run.actualWidth}×${run.actualHeight}` +
            (run.requestedHeight && run.actualHeight !== run.requestedHeight
              ? ` <span style="color:var(--review)" title="Requested ${run.requestedHeight}p">⚠ asked ${run.requestedHeight}p</span>` : "") : "—";
          return `<tr>
            <td><a href="match.html?id=${esc(m.id)}">${esc(teamName(m.teamA))} vs ${esc(teamName(m.teamB))}</a></td>
            <td>${P.chipStatus(m.status)}</td>
            <td>${P.chipCapture(m.captureStatus)}</td>
            <td class="num">${res}</td>
            <td>${run && run.reportPath
              ? `<a class="ev-tick" href="${esc(run.reportPath)}">run report</a>`
              : `<span class="faint">—</span>`}</td></tr>`;
        }).join("")}</tbody></table></div>
      <details class="ev-more"><summary>What do the capture states mean?</summary>
        <dl class="ev-kv" style="margin:10px 0 4px">
          ${LADDER.map(([k, txt]) => `<dt>${P.chipCapture(k)}</dt><dd style="font-family:var(--font-body);font-size:12.5px">${esc(txt)}</dd>`).join("")}
        </dl></details>
    </div>`;
  }

  /* ---------- assemble ---------- */
  root.innerHTML = head() + tabsHtml();
  const panels = {
    overview: panelOverview, bracket: panelBracket, matches: panelMatches,
    standings: panelStandings, teams: panelTeams, maps: panelMaps,
    vods: panelVods, capture: panelCapture,
  };
  Object.entries(panels).forEach(([k, fn]) => { P.$("#panel-" + k).innerHTML = fn(); });
  P.initTabs(root.querySelector(".tabs").parentElement, { hashKey: "tab" });
  P.observeReveals(root);
  root.querySelectorAll("[data-count-to]").forEach((el) => P.countUp && P.countUp(el));

  /* mobile bracket side switcher */
  const bSections = P.$$("[data-b-section]", root);
  const sideBtns = P.$$("[data-side-btn]", root);
  function setSide(side) {
    sideBtns.forEach((b) => b.setAttribute("aria-pressed", b.dataset.sideBtn === side ? "true" : "false"));
    bSections.forEach((sec) => {
      sec.setAttribute("data-mobile", "");
      P.$$(".b-col", sec).forEach((col) => {
        col.classList.toggle("mob-show",
          col.dataset.side === side || (side === "upper" && col.dataset.side === "gf" && false));
      });
    });
  }
  if (sideBtns.length) {
    sideBtns.forEach((b) => b.addEventListener("click", () => setSide(b.dataset.sideBtn)));
    setSide(sideBtns[0].dataset.sideBtn);
  }
})();
