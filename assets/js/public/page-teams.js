/* Teams directory — every team with verified comps, region-filterable.
   Each card summarizes record + hero pool size and links to the full
   team page. Built from the same verified dataset as everything else. */
(function () {
  "use strict";
  const P = window.OWCS_PUB, S = window.OWCS_STATS, D = P.data, esc = P.esc;
  const seg = P.$("#region-seg");
  if (!D) {
    P.$("#tm-grid").innerHTML = P.emptyState("◈", "No dataset loaded",
      "Regenerate the public data file and reload.");
    return;
  }

  const REGION_VARS = { all: "--rg-all", na: "--rg-na", emea: "--rg-emea", asia: "--rg-asia", china: "--rg-china", pacific: "--rg-pacific" };
  seg.innerHTML = D.regions.map((r) =>
    `<button type="button" data-region="${esc(r.id)}" style="--rg:var(${REGION_VARS[r.id] || "--rg-all"})" aria-pressed="false">${esc(r.id === "all" ? "All regions" : r.short)}</button>`).join("");
  let region = P.qs().get("region") || "all";

  /* Precompute per-team facts once. */
  const teamCards = (D.teams || []).map((t) => {
    const matches = (D.matches || []).filter((m) => m.teamA === t.id || m.teamB === t.id);
    let mw = 0, ml = 0;
    matches.forEach((m) => (m.maps || []).forEach((mp) => {
      if (!mp.winner) return;
      if (mp.winner === t.id) mw += 1; else ml += 1;
    }));
    const hs = S.computeHeroStats({ teamId: t.id });
    const top = hs.rows.slice(0, 4);
    return { team: t, matches: matches.length, mw, ml, heroes: hs.rows.length, top };
  }).sort((a, b) => b.heroes - a.heroes || a.team.name.localeCompare(b.team.name));

  function card(c) {
    const t = c.team;
    return `<a class="card card--link card--spot t-card rv" href="team.html?id=${esc(t.id)}"
        aria-label="Open ${esc(t.name)}">
      <div class="split" style="align-items:center">
        ${P.teamPlate(t.id, { size: "lg" })}
        ${P.badgeRegion(t.region)}
      </div>
      <div class="cluster" style="gap:14px;margin-top:4px">
        <span class="mono" style="font-size:12px"><b style="font-size:15px">${c.mw}–${c.ml}</b> maps</span>
        <span class="mono" style="font-size:12px"><b style="font-size:15px">${c.heroes}</b> heroes</span>
        <span class="mono" style="font-size:12px"><b style="font-size:15px">${c.matches}</b> match${c.matches === 1 ? "" : "es"}</span>
      </div>
      ${c.top.length
        ? `<div class="hero-strip" style="margin-top:2px">${c.top.map((r) => P.heroTile(r.heroId, { sm: true })).join("")}${c.heroes > c.top.length ? `<span class="faint" style="align-self:center;font-size:11px;margin-left:4px">+${c.heroes - c.top.length}</span>` : ""}</div>`
        : `<span class="faint" style="font-size:12px">No verified comps yet.</span>`}
      <span class="pillar__go" style="margin-top:auto">Open team <span class="arw">→</span></span>
    </a>`;
  }

  function render(push) {
    if (push !== false) P.setQs({ region });
    P.$$("button", seg).forEach((b) => b.setAttribute("aria-pressed", b.dataset.region === region ? "true" : "false"));
    let list = teamCards;
    if (region !== "all") list = list.filter((c) => c.team.region === region);
    P.$("#tm-grid").innerHTML = list.length
      ? list.map(card).join("")
      : P.emptyState("⚑", "No teams in this region yet",
        "Teams appear here as their matches are ingested and clear review.");
    P.$("#tm-summary").innerHTML = `<span>${list.length} team${list.length === 1 ? "" : "s"}</span>` +
      (region !== "all" ? `<span class="fs-pill">region: ${esc(P.regionName(region))}</span>` : `<span class="faint">all regions</span>`);
    P.observeReveals(document);
  }

  seg.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-region]");
    if (!b) return;
    region = b.dataset.region;
    render();
  });
  const fresh = P.$("#freshness");
  if (fresh && D.meta) fresh.textContent = "as of " + P.fmtLocal(D.meta.generatedAt);
  render(false);
})();
