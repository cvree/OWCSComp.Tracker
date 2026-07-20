/* Stats page — region-first, verified-only, evidence-linked. */
(function () {
  "use strict";
  const P = window.OWCS_PUB, S = window.OWCS_STATS, D = P.data, esc = P.esc;
  const seg = P.$("#region-seg");

  if (!D) {
    P.$("#s-hero-table").innerHTML = P.emptyState("◈", "No dataset loaded",
      "Regenerate the public data file and reload.");
    return;
  }

  /* region segmented control */
  const REGION_VARS = { all: "--rg-all", na: "--rg-na", emea: "--rg-emea", asia: "--rg-asia", china: "--rg-china", pacific: "--rg-pacific" };
  seg.innerHTML = D.regions.map((r) =>
    `<button type="button" data-region="${esc(r.id)}" style="--rg:var(${REGION_VARS[r.id] || "--rg-all"})" aria-pressed="false">${esc(r.id === "all" ? "All regions" : r.short)}</button>`).join("");

  /* other filters */
  const selT = P.$("#sf-tournament"), selTeam = P.$("#sf-team"), selMap = P.$("#sf-map");
  selT.innerHTML += D.tournaments.map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");
  selTeam.innerHTML += D.teams.map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");
  selMap.innerHTML += D.mapsCatalog.map((mp) => `<option value="${esc(mp.id)}">${esc(mp.name)}</option>`).join("");

  const state = { region: "all", tournamentId: "all", teamId: "all", mapId: "all", sort: "picks", dir: "desc" };
  const q = P.qs();
  ["region", "sort", "dir"].forEach((k) => { if (q.get(k)) state[k] = q.get(k); });
  if (q.get("tournament")) state.tournamentId = q.get("tournament");
  if (q.get("team")) state.teamId = q.get("team");
  if (q.get("map")) state.mapId = q.get("map");
  selT.value = Array.from(selT.options).some((o) => o.value === state.tournamentId) ? state.tournamentId : "all";
  selTeam.value = Array.from(selTeam.options).some((o) => o.value === state.teamId) ? state.teamId : "all";
  selMap.value = Array.from(selMap.options).some((o) => o.value === state.mapId) ? state.mapId : "all";

  function evidenceLinks(evidence) {
    const seen = new Set();
    return evidence.filter((e) => {
      const k = e.matchId;
      if (seen.has(k)) return false;
      seen.add(k); return true;
    }).map((e) => {
      const m = P.match(e.matchId);
      const label = m ? `${(P.team(m.teamA) || { code: "TBD" }).code} v ${(P.team(m.teamB) || { code: "TBD" }).code}` : e.matchId;
      return `<a class="ev-tick" href="match.html?id=${esc(e.matchId)}&tab=evidence" title="Open the evidence chain for this match">${esc(label)}</a>`;
    }).join(" ");
  }

  const pct = (v) => v == null ? `<span class="faint">—</span>` : (v * 100).toFixed(0) + "%";
  const rateBar = (v, hot) => `<span class="rate-bar${hot ? " hot" : ""}"><span class="rb-track"><span class="rb-fill" style="width:${Math.round((v || 0) * 100)}%"></span></span><span class="mono" style="font-size:11px">${pct(v)}</span></span>`;

  function heroTable(rows) {
    if (!rows.length)
      return P.emptyState("⛨", "No verified comps match these filters",
        `Nothing has cleared review for this slice yet. Widen the filters, or process a match window:<br><code>python pipeline/run_owcs_auto.py --source &lt;vod-id&gt; --start H:MM:SS --end H:MM:SS --every 30</code>`);
    const cols = [
      ["hero", "Hero"], ["role", "Role"], ["picks", "Picks"],
      ["pickRate", "Pick rate"], ["winRate", "Win rate"], ["record", "W–L"], ["evidence", "Evidence"],
    ];
    const sortable = { hero: (r) => r.name, role: (r) => r.role, picks: (r) => r.picks, pickRate: (r) => r.pickRate, winRate: (r) => r.winRate == null ? -1 : r.winRate };
    const sorted = rows.slice().sort((a, b) => {
      const f = sortable[state.sort] || sortable.picks;
      const va = f(a), vb = f(b);
      const c = typeof va === "string" ? va.localeCompare(vb) : va - vb;
      return state.dir === "asc" ? c : -c;
    });
    const maxPick = Math.max(...rows.map((r) => r.pickRate), 0.0001);
    return `<div class="stat-table-wrap"><table class="stat-table">
      <thead><tr>${cols.map(([k, label]) => {
        const sortKey = sortable[k] ? k : null;
        const aria = state.sort === k ? ` aria-sort="${state.dir === "asc" ? "ascending" : "descending"}"` : "";
        return `<th scope="col"${sortKey ? ` data-sort="${k}"${aria} tabindex="0" role="button" aria-label="Sort by ${esc(label)}"` : ""}>${esc(label)}</th>`;
      }).join("")}</tr></thead>
      <tbody>${sorted.map((r) => `<tr>
        <td><span class="cluster" style="gap:8px">${P.heroTile(r.heroId, { sm: true })}<b>${esc(r.name)}</b></span></td>
        <td><span class="hero-tile" data-role="${esc(r.role)}" style="width:auto"><span style="color:var(--role-c);font-size:12px">${esc(r.role)}</span></span></td>
        <td class="num">${r.picks}</td>
        <td>${rateBar(r.pickRate, r.pickRate === maxPick)}</td>
        <td>${r.winRate == null ? `<span class="faint" title="No decided maps in this slice yet">—</span>` : rateBar(r.winRate, r.winRate >= 0.6)}</td>
        <td class="num">${r.wins}–${r.losses}</td>
        <td>${evidenceLinks(r.evidence)}</td>
      </tr>`).join("")}</tbody></table></div>`;
  }

  const ROLE_ORDER = ["Tank", "Damage", "Support"];
  const MAX_PER_ROLE = 6;

  function metaSnapshot(rows) {
    if (!rows.length)
      return `<div class="meta-snap-empty">${P.emptyState("◈", "No verified comps yet",
        "The meta snapshot fills in as ingested maps clear review.")}</div>`;
    const byRole = new Map();
    rows.forEach((r) => {
      const role = ROLE_ORDER.includes(r.role) ? r.role : "Other";
      if (!byRole.has(role)) byRole.set(role, []);
      byRole.get(role).push(r);
    });
    const roles = ROLE_ORDER.filter((r) => byRole.has(r))
      .concat(Array.from(byRole.keys()).filter((r) => !ROLE_ORDER.includes(r)));
    return roles.map((role) => {
      const list = byRole.get(role).slice().sort((a, b) => b.pickRate - a.pickRate || a.name.localeCompare(b.name));
      const shown = list.slice(0, MAX_PER_ROLE);
      const top = shown[0].pickRate || 0.0001;
      const cards = shown.map((r, i) => {
        const lead = i === 0;
        const fill = Math.round((r.pickRate / top) * 100);
        return `<div class="meta-card${lead ? " meta-card--lead" : ""}" style="--fill:${fill}%">
          <span class="meta-card__rank">${lead ? "★" : i + 1}</span>
          <span class="meta-card__body">
            <span class="meta-card__name">${esc(r.name)}</span><br>
            <span class="meta-card__sub">${r.picks} pick${r.picks === 1 ? "" : "s"}${r.winRate == null ? "" : " · " + r.wins + "–" + r.losses}</span>
          </span>
          <span class="meta-card__pct">${pct(r.pickRate)}</span>
        </div>`;
      }).join("");
      const more = list.length > MAX_PER_ROLE
        ? `<div class="faint" style="font-size:11px;padding:2px 2px 0">+${list.length - MAX_PER_ROLE} more</div>` : "";
      return `<div class="meta-col" data-role="${esc(role)}">
        <div class="meta-col__head">${esc(role)}<span class="mc-n">${list.length}</span></div>
        ${cards}${more}
      </div>`;
    }).join("");
  }

  function banTable(rows) {
    if (!rows.length)
      return P.emptyState("🚫", "No ban data for these filters", "Bans appear as match imports run.");
    return `<div class="stat-table-wrap"><table class="stat-table">
      <thead><tr><th scope="col">Hero</th><th scope="col">Times banned</th><th scope="col">Source</th><th scope="col">Matches</th></tr></thead>
      <tbody>${rows.map((r) => `<tr>
        <td><span class="cluster" style="gap:8px">${P.heroTile(r.heroId, { sm: true })}<b>${esc(r.name)}</b></span></td>
        <td class="num">${r.bans}</td>
        <td>${P.badgeSrc(r.source)}</td>
        <td>${Array.from(new Set(r.evidence.map((e) => e.matchId))).map((mid) => {
          const m = P.match(mid);
          return `<a class="chip" href="match.html?id=${esc(mid)}&tab=bans">${m ? esc((P.team(m.teamA) || {}).code + " v " + (P.team(m.teamB) || {}).code) : esc(mid)}</a>`;
        }).join(" ")}</td>
      </tr>`).join("")}</tbody></table></div>`;
  }

  function render(push) {
    if (push !== false) P.setQs({
      region: state.region, tournament: state.tournamentId, team: state.teamId,
      map: state.mapId, sort: state.sort === "picks" ? null : state.sort, dir: state.dir === "desc" ? null : state.dir,
    });
    P.$$("button", seg).forEach((b) => b.setAttribute("aria-pressed", b.dataset.region === state.region ? "true" : "false"));

    const filters = { region: state.region, tournamentId: state.tournamentId, teamId: state.teamId, mapId: state.mapId };
    const hs = S.computeHeroStats(filters);
    const bs = S.computeBanStats(filters);
    const sum = S.summary(filters);

    P.$("#s-cards").innerHTML = [
      [sum.comps, "Verified comps", "reviewed or auto-high"],
      [sum.verifiedMaps, "Maps with comps", "evidence-linked"],
      [sum.matches, "Matches covered", ""],
      [sum.heroesSeen, "Heroes seen", ""],
    ].map(([n, label, sub]) =>
      `<div class="card stat-card rv"><span class="sc-num" data-count-to="${n}">${n}</span><span class="sc-label">${esc(label)}</span>${sub ? `<span class="sc-sub">${esc(sub)}</span>` : ""}</div>`).join("");

    const active = Object.entries(filters).filter(([, v]) => v !== "all");
    P.$("#sf-summary").innerHTML =
      `<span>${hs.rows.length} hero${hs.rows.length === 1 ? "" : "es"} · ${sum.comps} comps</span>` +
      (active.length ? active.map(([k, v]) => `<span class="fs-pill">${esc(k.replace("Id", ""))}: ${esc(k === "region" ? P.regionName(v) : v)}</span>`).join("") : `<span class="faint">no filters</span>`);
    P.$("#s-hero-count").textContent = hs.rows.length ? `${hs.rows.length} heroes` : "";
    P.$("#s-meta").innerHTML = metaSnapshot(hs.rows);
    P.$("#s-hero-table").innerHTML = heroTable(hs.rows);
    P.$("#s-ban-table").innerHTML = banTable(bs.rows);
    P.observeReveals(document);
    document.querySelectorAll("#s-cards [data-count-to]").forEach((el) => P.countUp && P.countUp(el));

    P.$$("[data-sort]").forEach((th) => {
      const act = () => {
        const k = th.dataset.sort;
        if (state.sort === k) state.dir = state.dir === "desc" ? "asc" : "desc";
        else { state.sort = k; state.dir = "desc"; }
        render();
      };
      th.addEventListener("click", act);
      th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); act(); } });
    });
  }

  seg.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-region]");
    if (!b) return;
    state.region = b.dataset.region;
    render();
  });
  [["sf-tournament", "tournamentId"], ["sf-team", "teamId"], ["sf-map", "mapId"]].forEach(([sel, key]) => {
    P.$("#" + sel).addEventListener("change", (e) => { state[key] = e.target.value; render(); });
  });
  P.$("#sf-reset").addEventListener("click", () => {
    state.region = "all"; state.tournamentId = "all"; state.teamId = "all"; state.mapId = "all";
    selT.value = selTeam.value = selMap.value = "all";
    render();
  });
  const fresh = P.$("#freshness");
  if (fresh && D.meta) fresh.textContent = "as of " + P.fmtLocal(D.meta.generatedAt);
  render(false);
})();
