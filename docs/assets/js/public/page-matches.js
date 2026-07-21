/* Matches schedule — live / upcoming / recent, region-filterable. */
(function () {
  "use strict";
  const P = window.OWCS_PUB, D = P.data, esc = P.esc;
  const seg = P.$("#region-seg");
  if (!D) {
    P.$("#m-upcoming").innerHTML = P.emptyState("◈", "No dataset loaded", "Regenerate the public data file and reload.");
    return;
  }

  const REGION_VARS = { all: "--rg-all", na: "--rg-na", emea: "--rg-emea", asia: "--rg-asia", china: "--rg-china", pacific: "--rg-pacific" };
  seg.innerHTML = D.regions.map((r) =>
    `<button type="button" data-region="${esc(r.id)}" style="--rg:var(${REGION_VARS[r.id] || "--rg-all"})" aria-pressed="false">${esc(r.id === "all" ? "All regions" : r.short)}</button>`).join("");

  let region = P.qs().get("region") || "all";

  function card(m) {
    const t = P.tournament(m.tournamentId);
    const winA = m.winner && m.winner === m.teamA, winB = m.winner && m.winner === m.teamB;
    return `<a class="card card--link card--spot m-card rv" href="match.html?id=${esc(m.id)}">
      <div class="m-card__meta">
        ${P.chipStatus(m.status)} ${P.chipCapture(m.captureStatus)}
        ${t ? P.badgeRegion(t.region) : ""}
        <span>${t ? esc(t.name) : ""}</span>
        <span class="mono">${esc(P.fmtLocal(m.scheduledAt))}</span>
      </div>
      <div class="m-card__row">
        <div class="m-card__teams">
          ${P.teamPlate(m.teamA, { win: winA, tbd: m.tbdNote })}
          ${P.teamPlate(m.teamB, { win: winB, tbd: m.tbdNote })}
        </div>
        <span class="cluster">
          ${P.scorePlate(m.scoreA, m.scoreB, winA ? "a" : winB ? "b" : null)}
          ${m.status === "live" && m.streamUrl ? `<span class="btn btn--gold" style="pointer-events:none">Watch</span>` : ""}
        </span>
      </div>
    </a>`;
  }

  function section(el, title, list, emptyMsg) {
    el.innerHTML = `<h2>${esc(title)} <span class="h-count">${list.length || ""}</span></h2>` +
      (list.length ? `<div class="stack-sm">${list.map(card).join("")}</div>` : P.emptyState("◷", emptyMsg[0], emptyMsg[1]));
  }

  function render(push) {
    if (push !== false) P.setQs({ region });
    P.$$("button", seg).forEach((b) => b.setAttribute("aria-pressed", b.dataset.region === region ? "true" : "false"));
    let ms = D.matches.slice();
    if (region !== "all") ms = ms.filter((m) => {
      const t = P.tournament(m.tournamentId);
      return t && t.region === region;
    });
    const live = ms.filter((m) => m.status === "live");
    const upcoming = ms.filter((m) => m.status === "upcoming").sort((a, b) => new Date(a.scheduledAt) - new Date(b.scheduledAt));
    const recent = ms.filter((m) => m.status === "completed" || m.status === "forfeit").sort((a, b) => new Date(b.scheduledAt) - new Date(a.scheduledAt));
    section(P.$("#m-live"), "Live now", live, ["Nothing on air", "When a tracked match goes live it appears here with a watch link."]);
    section(P.$("#m-upcoming"), "Upcoming", upcoming, ["No matches scheduled", "New matches appear after the discovery import runs for this region."]);
    section(P.$("#m-recent"), "Recent results", recent, ["No results yet", "Finished series show up here with their capture status."]);
    P.$("#m-summary").innerHTML = `<span>${ms.length} match${ms.length === 1 ? "" : "es"}</span>` +
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
