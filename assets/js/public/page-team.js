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

  /* ---- header ------------------------------------------------------- */
  P.$("#t-crumbs").innerHTML = P.breadcrumbs([
    { label: "Matches", href: "matches.html" },
    { label: team.name },
  ]);
  P.$("#t-head").innerHTML = `
    <div class="split" style="align-items:center;gap:18px;flex-wrap:wrap">
      <div class="cluster" style="gap:14px">
        ${P.teamPlate(id, { size: "lg" })}
        ${P.badgeRegion(team.region)}
      </div>
      <div class="cluster" style="gap:10px">
        ${tournaments.map((t) =>
          `<a class="chip" href="tournament.html?id=${esc(t.id)}">${esc(t.name)}</a>`).join("")}
      </div>
    </div>`;

  /* ---- summary cards ------------------------------------------------ */
  const hs = S.computeHeroStats({ teamId: id });
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
      return P.emptyState("◈", "No verified comps for this team yet",
        "The hero pool fills in as this team's maps are ingested and clear review.");
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

  P.observeReveals(document);
})();
