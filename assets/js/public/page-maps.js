/* Maps showcase — the map meta, auto-built from verified data. For every
   map that's been played it shows how the pick rates and bans look ON that
   map (hero rates are map-scoped via OWCS_STATS, so the credibility rules
   apply unchanged). Nothing is hard-coded — maps appear as they're
   ingested. */
(function () {
  "use strict";
  const P = window.OWCS_PUB, S = window.OWCS_STATS, D = P.data, esc = P.esc;
  const seg = P.$("#region-seg");
  if (!D) {
    P.$("#mp-grid").innerHTML = P.emptyState("◈", "No dataset loaded",
      "Regenerate the public data file and reload.");
    return;
  }

  const REGION_VARS = { all: "--rg-all", na: "--rg-na", emea: "--rg-emea", asia: "--rg-asia", china: "--rg-china", pacific: "--rg-pacific" };
  seg.innerHTML = D.regions.map((r) =>
    `<button type="button" data-region="${esc(r.id)}" style="--rg:var(${REGION_VARS[r.id] || "--rg-all"})" aria-pressed="false">${esc(r.id === "all" ? "All regions" : r.short)}</button>`).join("");
  let region = P.qs().get("region") || "all";

  /* Aggregate every played map (by catalog id) once. */
  const played = new Map();
  (D.matches || []).forEach((m) => {
    const t = P.tournament(m.tournamentId);
    (m.maps || []).forEach((mp) => {
      if (!mp.map) return;
      let e = played.get(mp.map);
      if (!e) {
        e = { catalogId: mp.map, mode: mp.mode, pubIds: new Set(),
              matchIds: new Set(), rounds: 0, count: 0, regions: new Set() };
        played.set(mp.map, e);
      }
      e.pubIds.add(mp.id);
      e.matchIds.add(m.id);
      e.rounds += mp.roundCount || 0;
      e.count += 1;
      if (!e.mode && mp.mode) e.mode = mp.mode;
      if (t && t.region) e.regions.add(t.region);
    });
  });

  const pct = (v) => v == null ? "—" : Math.round(v * 100) + "%";
  const MODE_GLYPH = { Control: "◉", Escort: "⇥", Hybrid: "⧉", Push: "⇄",
    Flashpoint: "✦", Clash: "⚔" };

  function mapCard(e) {
    const info = P.mapInfo(e.catalogId);
    const hs = S.computeHeroStats({ region, mapId: e.catalogId });
    const top = hs.rows.slice(0, 6);
    const topRate = top.length ? (top[0].pickRate || 0.0001) : 1;
    const bans = (D.heroBans || []).filter((b) => e.pubIds.has(b.mapId));

    const heroRows = top.length
      ? top.map((r, i) => `<div class="mm-row">
          <span class="mm-rank">${i === 0 ? "★" : i + 1}</span>
          ${P.heroTile(r.heroId, { sm: true })}
          <span class="mm-name">${esc(r.name)}</span>
          <span class="mm-bar"><span class="mm-fill" style="width:${Math.round((r.pickRate / topRate) * 100)}%"></span></span>
          <span class="mm-pct">${pct(r.pickRate)}</span>
          <span class="mm-wl mono">${r.wins}–${r.losses}</span>
        </div>`).join("")
      : `<div class="mm-empty faint">No verified comps on this map yet.</div>`;

    const banRow = bans.length
      ? `<div class="mm-bans"><span class="mm-lbl">Bans</span><span class="hero-strip">${
          bans.map((b) => P.heroTile(b.hero, { sm: true })).join("")}</span></div>`
      : `<div class="mm-bans"><span class="mm-lbl">Bans</span><span class="faint" style="font-size:12px">none recorded on this map yet</span></div>`;

    return `<div class="card map-showcase rv">
      <div class="ms-head">
        <span class="ms-glyph" aria-hidden="true">${MODE_GLYPH[e.mode] || "◆"}</span>
        <div>
          <b class="ms-name">${esc(info.name || e.catalogId)}</b>
          <span class="ms-meta">${esc(e.mode || info.mode || "")} · ${e.count} map${e.count === 1 ? "" : "s"} played${e.rounds ? " · " + e.rounds + " rounds" : ""}</span>
        </div>
        <a class="ev-tick" href="stats.html?map=${esc(e.catalogId)}" title="Full pick/win table for this map">full stats →</a>
      </div>
      <div class="mm-list">${heroRows}</div>
      ${banRow}
    </div>`;
  }

  function render(push) {
    if (push !== false) P.setQs({ region });
    P.$$("button", seg).forEach((b) => b.setAttribute("aria-pressed", b.dataset.region === region ? "true" : "false"));
    let list = Array.from(played.values());
    if (region !== "all") list = list.filter((e) => e.regions.has(region));
    list.sort((a, b) => b.count - a.count ||
      (P.mapInfo(a.catalogId).name || a.catalogId).localeCompare(P.mapInfo(b.catalogId).name || b.catalogId));
    P.$("#mp-grid").innerHTML = list.length
      ? `<div class="map-grid">${list.map(mapCard).join("")}</div>`
      : P.emptyState("◈", "No maps in this slice yet",
        "Maps appear here as their matches are ingested and clear review.");
    P.$("#mp-summary").innerHTML = `<span>${list.length} map${list.length === 1 ? "" : "s"}</span>` +
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
