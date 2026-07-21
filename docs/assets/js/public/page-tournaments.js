/* Tournaments index — filterable event cards, URL-persisted state. */
(function () {
  "use strict";
  const P = window.OWCS_PUB, D = P.data, esc = P.esc;
  const grid = P.$("#t-grid"), emptyEl = P.$("#t-empty"), summary = P.$("#f-summary");

  if (!D) {
    grid.innerHTML = "";
    emptyEl.hidden = false;
    emptyEl.innerHTML = P.emptyState("◈", "No dataset loaded",
      "The public data file failed to load. Regenerate it with <code>python pipeline/export_data.py</code> and reload.");
    return;
  }

  /* region + year selects built from data */
  const selRegion = P.$("#f-region"), selYear = P.$("#f-year");
  selRegion.innerHTML = D.regions.map((r) =>
    `<option value="${esc(r.id)}">${esc(r.id === "all" ? "All regions" : r.name)}</option>`).join("");
  const years = Array.from(new Set(D.tournaments.map((t) => t.year))).sort((a, b) => b - a);
  selYear.innerHTML = `<option value="all">All years</option>` +
    years.map((y) => `<option value="${y}">${y}</option>`).join("");

  const controls = { region: selRegion, year: selYear, tier: P.$("#f-tier"), status: P.$("#f-status") };
  /* hydrate from URL */
  const q = P.qs();
  Object.entries(controls).forEach(([k, el]) => {
    const v = q.get(k);
    if (v && Array.from(el.options).some((o) => o.value === v)) el.value = v;
  });

  const statusRank = { live: 0, upcoming: 1, completed: 2 };

  function tCard(t) {
    const matches = P.matchesOf(t.id);
    const done = matches.filter((m) => m.status === "completed" || m.status === "forfeit").length;
    const verified = matches.filter((m) => m.captureStatus === "verified").length;
    const winner = t.winnerTeamId
      ? `<span class="cluster">${P.teamPlate(t.winnerTeamId, { size: "sm", win: true })}<span class="faint">champion</span></span>`
      : (t.status === "live"
        ? `<span class="chip" data-st="live">Live now</span>`
        : `<span class="faint">${matches.length ? done + "/" + matches.length + " matches final" : "No matches scheduled yet"}</span>`);
    return `<a class="card card--link card--spot t-card rv" href="tournament.html?id=${esc(t.id)}" aria-label="${esc(t.name)}">
      <div class="t-card__top">
        <span class="t-card__logo" aria-hidden="true">${esc(t.series.replace(/[^A-Z0-9]/gi, "").slice(0, 3).toUpperCase() || "OW")}</span>
        <span>
          <div class="t-card__name">${esc(t.name)}</div>
          <div class="t-card__series">${esc(t.series)} · ${t.year}</div>
        </span>
      </div>
      <div class="t-card__badges">
        ${P.badgeRegion(t.region)} ${P.badgeTier(t.tier)} ${P.chipStatus(t.status)}
      </div>
      <div class="t-card__facts">
        <span><span class="mono">📅</span> ${esc(P.fmtRange(t.startsAt, t.endsAt))}</span>
        <span><span class="mono">⚔</span> ${t.teamIds.length ? t.teamIds.length + " teams" : "Teams TBD"}</span>
        ${t.prizePool ? `<span><span class="mono">🏆</span> ${esc(t.prizePool)}</span>` : ""}
        ${verified ? `<span class="ev-tick" title="Matches with a verified capture run">${verified} verified capture${verified > 1 ? "s" : ""}</span>` : ""}
      </div>
      <div class="t-card__foot">${winner}<span class="faint">View event →</span></div>
    </a>`;
  }

  function apply(push) {
    const f = {};
    Object.entries(controls).forEach(([k, el]) => (f[k] = el.value));
    if (push !== false) P.setQs(f);

    let list = D.tournaments.slice();
    if (f.region !== "all") list = list.filter((t) => t.region === f.region);
    if (f.year !== "all") list = list.filter((t) => String(t.year) === f.year);
    if (f.tier !== "all") list = list.filter((t) => t.tier === f.tier);
    if (f.status !== "all") list = list.filter((t) => t.status === f.status);
    list.sort((a, b) =>
      (statusRank[a.status] ?? 3) - (statusRank[b.status] ?? 3) ||
      new Date(b.startsAt) - new Date(a.startsAt));

    const active = Object.entries(f).filter(([, v]) => v !== "all");
    summary.innerHTML =
      `<span>${list.length} event${list.length === 1 ? "" : "s"}</span>` +
      (active.length
        ? active.map(([k, v]) => `<span class="fs-pill">${esc(k)}: ${esc(k === "region" ? P.regionName(v) : v)}</span>`).join("")
        : `<span class="faint">no filters</span>`);

    if (!list.length) {
      grid.innerHTML = "";
      emptyEl.hidden = false;
      emptyEl.innerHTML = P.emptyState("⌕", "No tournaments match these filters",
        "Try widening the region or year — or reset the filters. New events appear here after the discovery import runs.");
    } else {
      emptyEl.hidden = true;
      grid.innerHTML = list.map(tCard).join("");
      P.observeReveals(grid);
    }
  }

  Object.values(controls).forEach((el) => el.addEventListener("change", () => apply()));
  P.$("#f-reset").addEventListener("click", () => {
    Object.values(controls).forEach((el) => (el.value = "all"));
    apply();
  });
  const fresh = P.$("#freshness");
  if (fresh && D.meta) fresh.textContent = "as of " + P.fmtLocal(D.meta.generatedAt);
  apply(false);
})();
