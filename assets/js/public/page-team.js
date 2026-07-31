/* Team page — profile built ONLY from verified data: hero pool with pick/
   win rates (via OWCS_STATS, so the credibility rules apply unchanged),
   match history, and evidence links on every row. */
(function () {
  "use strict";
  const P = window.OWCS_PUB, S = window.OWCS_STATS, D = P.data, esc = P.esc;

  const id = P.qs().get("id");
  const team = id ? P.team(id) : null;
  if (!D || !team) {
    P.$("#t-head").innerHTML = P.emptyState("⚑", "Team not found",
      `No team with id <code>${esc(id || "(none)")}</code> exists in the current dataset. <a href="matches.html">Back to matches</a>.`);
    return;
  }
  document.title = `${team.name} — OWCS Comp Tracker`;

  /* ---- matches involving this team ---------------------------------- */
  const matches = (D.matches || []).filter(
    (m) => m.teamA === id || m.teamB === id);

  const record = { matchW: 0, matchL: 0, mapW: 0, mapL: 0 };
  matches.forEach((m) => {
    if (m.winner === id) record.matchW += 1;
    else if (m.winner && m.winner !== id) record.matchL += 1;
    (m.maps || []).forEach((mp) => {
      if (!mp.winner) return;
      if (mp.winner === id) record.mapW += 1;
      else record.mapL += 1;
    });
  });

  const tournaments = Array.from(new Set(matches.map((m) => m.tournamentId)))
    .map((tid) => P.tournament(tid)).filter(Boolean);

  /* ---- recency ------------------------------------------------------ */
  const dated = matches.filter((m) => m.scheduledAt)
    .sort((a, b) => new Date(b.scheduledAt) - new Date(a.scheduledAt));
  const lastPlayed = dated[0] ? dated[0].scheduledAt : null;
  const recencyHtml = lastPlayed
    ? `<span class="recency-badge" title="Most recent tracked match">
         <span class="rb-dot" aria-hidden="true"></span>
         Last played ${esc(P.fmtDate(lastPlayed))} · <b>${esc(P.fmtRel(lastPlayed))}</b></span>`
    : "";

  /* ---- header ------------------------------------------------------- */
  P.$("#t-crumbs").innerHTML = P.breadcrumbs([
    { label: "Teams", href: "teams.html" },
    { label: team.name },
  ]);
  // Optional FACEIT-sourced facts (Phase D team enrichment) — description,
  // website, socials. Absent (null) until a team has been enriched; never a
  // substitute for the human-verified logo/plate above.
  const socials = team.socials || {};
  const profileLinks = [
    team.website ? { label: "Website", href: team.website } : null,
    socials.twitter ? { label: "Twitter/X", href: `https://twitter.com/${socials.twitter}` } : null,
    socials.facebook ? { label: "Facebook", href: socials.facebook } : null,
  ].filter(Boolean);

  // Canonical registry facts (Phase D2) — status/organization/aliases are
  // identity facts, never a substitute for capture evidence below.
  const statusNote = team.status && team.status !== "active"
    ? `<span class="chip" data-cap="${team.status === "unsigned" ? "needs-review" : "failed"}">${esc(team.status)}</span>` : "";
  const aliasNote = (team.previousNames || []).length
    ? `<span class="faint" title="Previous name(s), preserved on rename">formerly ${esc(team.previousNames.join(", "))}</span>` : "";

  P.$("#t-head").innerHTML = `
    <div class="split" style="align-items:center;gap:18px;flex-wrap:wrap">
      <div class="cluster" style="gap:14px">
        ${P.teamPlate(id, { size: "lg" })}
        ${P.badgeRegion(team.region)}
        ${statusNote}
        ${recencyHtml}
        ${team.memberCount ? `<span class="faint">${esc(String(team.memberCount))} roster spots (FACEIT)</span>` : ""}
      </div>
      <div class="cluster" style="gap:10px">
        ${tournaments.map((t) =>
          `<a class="chip" href="tournament.html?id=${esc(t.id)}">${esc(t.name)}</a>`).join("")}
      </div>
    </div>
    ${aliasNote ? `<p class="faint" style="margin-top:6px">${aliasNote}</p>` : ""}
    ${team.organization && team.organization !== team.name ? `<p class="faint">Organization: ${esc(team.organization)}</p>` : ""}
    ${team.description ? `<p class="lede" style="margin-top:10px">${esc(team.description)}</p>` : ""}
    ${profileLinks.length ? `<div class="cluster" style="gap:8px;margin-top:8px">
        ${profileLinks.map((l) =>
          `<a class="chip" href="${esc(l.href)}" target="_blank" rel="noopener noreferrer">${esc(l.label)}</a>`).join("")}
      </div>` : ""}
    ${team.needsReview ? `<div class="stat-note" style="margin-top:10px"><span aria-hidden="true">⚑</span>
        <span>Needs review: ${esc(team.reviewReason || "a fact from a second source conflicts with what's on record")}</span></div>` : ""}`;

  /* ---- coverage (Phase D2) ------------------------------------------- */
  const coverage = [
    { label: "Identity", ok: !!team.identityVerifiedAt, note: team.identityVerifiedAt ? "verified" : "not yet verified by an automated source" },
    { label: "Roster", ok: !!(team.rosterVerifiedAt && team.roster.length), note: team.rosterVerifiedAt ? "verified" : "no roster on record" },
    { label: "Logo", ok: !!team.logoUrl, note: team.logoUrl ? "verified official mark" : (team.hasLogoCandidate ? "candidate awaiting human approval" : "no candidate source yet") },
    { label: "Broadcast", ok: matches.some((m) => m.streamUrl), note: matches.some((m) => m.streamUrl) ? "located" : "no official broadcast located yet" },
    { label: "Compositions", ok: !team.compositionTrackingPending, note: team.compositionTrackingPending ? "tracking pending — no maps captured yet" : "captured" },
  ];
  /* Coverage used to be a row of red "NO" chips whose meaning lived in a
     tooltip — unreadable on touch and alarming for what is usually just
     "we haven't captured this team yet". Each line now states the fact. */
  P.$("#t-coverage-sec").hidden = false;
  const covOk = coverage.filter((c) => c.ok).length;
  P.$("#t-coverage").innerHTML = `
    <p class="dim" style="margin:0 0 var(--sp-3);font-size:13px;max-width:74ch">
      What the tracker has confirmed about this team — ${covOk} of ${coverage.length} items.
      “Not yet” means nobody has verified it, never that it is untrue.
      <a href="how-it-works.html#verified">How verification works →</a></p>
    <ul class="cov-list">
      ${coverage.map((c) => `<li>
        <span class="chip" data-cap="${c.ok ? "verified" : "needs-review"}">${c.ok ? "confirmed" : "not yet"}</span>
        <b>${esc(c.label)}</b>
        <span class="cov-note">${esc(c.note)}</span></li>`).join("")}
    </ul>`;

  /* ---- roster (Phase D2) ---------------------------------------------- */
  if (team.roster && team.roster.length) {
    P.$("#t-roster-sec").hidden = false;
    P.$("#t-roster-count").textContent = team.roster.length;
    if (team.rosterSource === "faceit") P.$("#t-roster-src").hidden = false;
    P.$("#t-roster").innerHTML = `<div class="cluster" style="gap:8px;flex-wrap:wrap">
      ${team.roster.map((p) => `<span class="chip">${esc(p.handle)}${p.role ? ` <span class="faint">(${esc(p.role)})</span>` : ""}</span>`).join("")}
    </div>`;
  }

  /* ---- summary cards ------------------------------------------------ */
  const hs = S.computeHeroStats({ teamId: id });
  /* Say up front whether there is anything behind the numbers, so four
     zeroes never read as "this team lost everything". */
  P.$("#t-head").insertAdjacentHTML("beforeend", hs.totalAppearances
    ? `<div class="stat-note" style="margin-top:12px"><span aria-hidden="true">⛨</span><span>
        Verified compositions on record for <b>${hs.totalAppearances}</b>
        team-map ${hs.totalAppearances === 1 ? "appearance" : "appearances"} —
        every hero and rate below is read from those maps only.</span></div>`
    : `<div class="stat-note" style="margin-top:12px"><span aria-hidden="true">◌</span><span>
        <b>No map of this team has been captured and reviewed yet</b>, so its record and hero
        pool are empty rather than estimated. Match facts (schedule, opponents) are still shown.
        <a href="how-it-works.html#limits">Why coverage is small →</a></span></div>`);
  P.$("#t-cards").innerHTML = [
    [`${record.matchW}–${record.matchL}`, "Match record", "tracked series"],
    [`${record.mapW}–${record.mapL}`, "Map record", "decided maps"],
    [hs.rows.length, "Heroes fielded", "verified comps only"],
    [hs.totalAppearances, "Team-map appearances", "unit of pick rates"],
  ].map(([n, label, sub]) =>
    `<div class="card stat-card rv"><span class="sc-num">${esc(n)}</span><span class="sc-label">${esc(label)}</span>${sub ? `<span class="sc-sub">${esc(sub)}</span>` : ""}</div>`).join("");

  /* ---- hero pool (role-grouped, portrait-led) ----------------------- */
  const pct = (v) => v == null ? "—" : (v * 100).toFixed(0) + "%";
  const matchLabel = (mid) => {
    const m = P.match(mid);
    if (!m) return mid;
    return ((P.team(m.teamA) || { code: "?" }).code) + " v " +
      ((P.team(m.teamB) || { code: "?" }).code);
  };
  const ROLE_ORDER = ["Tank", "Damage", "Support"];
  function pool(rows) {
    if (!rows.length)
      return P.emptyState("◈", team.compositionTrackingPending
          ? "Composition tracking pending" : "No verified comps for this team yet",
        "The hero pool fills in as this team's maps are ingested and clear review. "
        + "Identity, roster, and schedule facts above are tracked independently of capture.");
    const byRole = new Map();
    rows.forEach((r) => {
      const role = ROLE_ORDER.includes(r.role) ? r.role : "Other";
      if (!byRole.has(role)) byRole.set(role, []);
      byRole.get(role).push(r);
    });
    const roles = ROLE_ORDER.filter((r) => byRole.has(r))
      .concat(Array.from(byRole.keys()).filter((r) => !ROLE_ORDER.includes(r)));
    return `<div class="meta-snap">` + roles.map((role) => {
      const list = byRole.get(role).slice()
        .sort((a, b) => b.picks - a.picks || a.name.localeCompare(b.name));
      const top = list[0].pickRate || 0.0001;
      return `<div class="meta-col" data-role="${esc(role)}">
        <div class="meta-col__head">${esc(role)}<span class="mc-n">${list.length}</span></div>
        ${list.map((r, i) => {
          const evid = r.evidence.filter((e, j, a) =>
            a.findIndex((x) => x.matchId === e.matchId) === j);
          return `<div class="meta-card${i === 0 ? " meta-card--lead" : ""}" style="--fill:${Math.round((r.pickRate / top) * 100)}%">
            ${P.heroTile(r.heroId, { sm: true })}
            <span class="meta-card__body">
              <span class="meta-card__name">${esc(r.name)}</span><br>
              <span class="meta-card__sub">${r.picks} map${r.picks === 1 ? "" : "s"} · ${r.wins}–${r.losses}
                ${evid.map((e) => `<a class="ev-tick" href="match.html?id=${esc(e.matchId)}&tab=evidence" title="Evidence chain">${esc(matchLabel(e.matchId))}</a>`).join(" ")}</span>
            </span>
            <span class="meta-card__pct">${pct(r.pickRate)}</span>
          </div>`;
        }).join("")}
      </div>`;
    }).join("") + `</div>`;
  }
  P.$("#t-pool-count").textContent = hs.rows.length ? `${hs.rows.length} heroes` : "";
  P.$("#t-pool").innerHTML = pool(hs.rows);

  /* ---- match history ------------------------------------------------ */
  function matchCard(m) {
    const t = P.tournament(m.tournamentId);
    const winA = m.winner && m.winner === m.teamA, winB = m.winner && m.winner === m.teamB;
    const won = m.winner === id;
    return `<a class="card card--link card--spot m-card rv" href="match.html?id=${esc(m.id)}">
      <div class="m-card__meta">
        ${P.chipStatus(m.status)} ${P.chipCapture(m.captureStatus)}
        ${t ? P.badgeRegion(t.region) : ""}
        <span>${t ? esc(t.name) : ""}</span>
        <span class="mono">${esc(P.fmtLocal(m.scheduledAt))}</span>
        ${m.winner ? `<span class="chip" data-cap="${won ? "verified" : "failed"}">${won ? "won" : "lost"}</span>` : ""}
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
  const sorted = matches.slice().sort(
    (a, b) => new Date(b.scheduledAt) - new Date(a.scheduledAt));
  P.$("#t-match-count").textContent = matches.length || "";
  P.$("#t-matches").innerHTML = sorted.length
    ? `<div class="stack-sm">${sorted.map(matchCard).join("")}</div>`
    : P.emptyState("◷", "No tracked matches yet",
      "Matches appear once a VOD for this team is captured and ingested.");

  /* ---- footage & calibration (the autocalibration story) ------------ */
  const runs = [];
  const seenRun = new Set();
  matches.forEach((m) => {
    if (m.captureRunId && !seenRun.has(m.captureRunId)) {
      seenRun.add(m.captureRunId);
      const r = P.run(m.captureRunId);
      if (r && r.calibration) runs.push({ run: r, match: m });
    }
  });
  function calibCard({ run, match }) {
    const c = run.calibration;
    const conf = c.confidence;
    const confPct = conf == null ? "—" : Math.round(conf * 100) + "%";
    const cov = c.roster ? Math.round((c.templateHeroes / c.roster) * 100) : 0;
    const cell = (k, v, ok) =>
      `<div class="cal-cell"><span class="cal-k">${esc(k)}</span><span class="cal-v${ok === false ? " bad" : ok ? " good" : ""}">${v}</span></div>`;
    return `<div class="card calib-card rv">
      <div class="cluster" style="justify-content:space-between;gap:10px;flex-wrap:wrap">
        <span class="cluster" style="gap:8px">
          <span class="chip" data-cap="verified">auto-calibrated</span>
          <span class="mono faint" style="font-size:11.5px">${esc(c.sourceId || run.sourceId || "")}</span>
        </span>
        <a class="ev-tick" href="match.html?id=${esc(match.id)}&tab=evidence">${esc(matchLabel(match.id))} · evidence</a>
      </div>
      <div class="calib-grid">
        ${cell("Calibrator confidence", confPct, conf != null && conf >= 0.55)}
        ${cell("HUD probe", c.hudProbe ? "verified" : "missing", c.hudProbe)}
        ${cell("Heroes templated", `${c.templateHeroes}<small> / ${c.roster}</small>`, c.templateHeroes >= 10)}
        ${cell("Reject markers", c.rejectMarkers || 0, (c.rejectMarkers || 0) > 0)}
        ${cell("Capture resolution", c.frameSize && c.frameSize[0] ? c.frameSize.join("×") : "—")}
        ${cell("Status", run.calibrationStatus || "ok", (run.calibrationStatus || "ok") === "ok")}
      </div>
      <div class="cal-cov"><span class="cal-k">Roster templated</span>
        <span class="cov-track"><span class="cov-fill" style="width:${cov}%"></span></span>
        <span class="mono" style="font-size:11px">${cov}%</span></div>
    </div>`;
  }
  if (runs.length) {
    P.$("#t-calib-sec").hidden = false;
    P.$("#t-calib").innerHTML = `<div class="stack-sm">${runs.map(calibCard).join("")}</div>`;
  }

  /* ---- maps played (with round/submap counts) ----------------------- */
  const mapRows = [];
  matches.forEach((m) => (m.maps || []).forEach((mp) => {
    if (!mp.map) return;
    mapRows.push({ mp, match: m,
      won: mp.winner ? mp.winner === id : null });
  }));
  function mapCard({ mp, match, won }) {
    const info = P.mapInfo(mp.map);
    return `<a class="card card--link map-story rv" href="match.html?id=${esc(match.id)}&tab=maps">
      <div class="cluster" style="justify-content:space-between;gap:10px;flex-wrap:wrap">
        <span class="cluster" style="gap:10px">
          <b style="font-size:15px">${esc(info.name || mp.map)}</b>
          <span class="map-mode">${esc(mp.mode || info.mode || "")}</span>
          ${mp.roundCount ? `<span class="chip" title="Rounds / sub-maps detected">${mp.roundCount} round${mp.roundCount === 1 ? "" : "s"}</span>` : ""}
        </span>
        ${won == null ? `<span class="faint">result pending</span>`
          : `<span class="chip" data-cap="${won ? "verified" : "failed"}">${won ? "won" : "lost"}</span>`}
      </div>
    </a>`;
  }
  if (mapRows.length) {
    P.$("#t-maps-sec").hidden = false;
    P.$("#t-maps-count").textContent = mapRows.length;
    P.$("#t-maps").innerHTML = `<div class="stack-sm">${mapRows.map(mapCard).join("")}</div>`;
  }

  /* ---- hero bans in this team's matches ----------------------------- */
  const myMatchIds = new Set(matches.map((m) => m.id));
  const bans = (D.heroBans || []).filter((b) => myMatchIds.has(b.matchId));
  P.$("#t-bans").innerHTML = bans.length
    ? `<div class="cluster" style="gap:10px;flex-wrap:wrap">${bans.map((b) => {
        const forThis = b.teamId === id;
        return `<span class="cluster" style="gap:6px">${P.heroTile(b.hero, { sm: true })}
          <span class="faint" style="font-size:11.5px">${forThis ? "banned by " + esc(team.code) : "banned vs " + esc(team.code)}</span></span>`;
      }).join("")}</div>`
    : P.emptyState("🚫", "No bans recorded in this team's tracked matches",
      "OWCS hero bans appear here once a match with bans is imported. The Nepal milestone match had none recorded.");

  P.observeReveals(document);
})();
