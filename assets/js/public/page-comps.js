/* =====================================================================
   OWCS Comp Tracker — page-comps.js
   Composition intelligence: verified comp snapshots grouped into
   distinct five-hero lineups per team, with play context, map results
   and evidence links. Built ONLY on P.publicComps — the credibility
   rule applies unchanged, and every card links to its receipts.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS_PUB, S = window.OWCS_STATS;
  if (!P || !P.data || !S) return;
  const esc = P.esc;
  const $ = P.$;

  const ROLE_ORDER = { Tank: 0, Damage: 1, Support: 2 };
  const roleSort = (a, b) => {
    const ha = P.hero(a), hb = P.hero(b);
    return (ROLE_ORDER[ha.role] ?? 3) - (ROLE_ORDER[hb.role] ?? 3)
      || ha.name.localeCompare(hb.name);
  };

  /* group verified snapshots into distinct lineups per team */
  function buildGroups() {
    const comps = P.publicComps();
    const groups = new Map(); // teamId|sortedHeroes -> group
    comps.forEach((c) => {
      const heroes = (c.heroes || []).slice().sort(roleSort);
      const key = c.teamId + "|" + heroes.join(",");
      let g = groups.get(key);
      if (!g) {
        g = { key, teamId: c.teamId, heroes, snapshots: [], maps: new Map() };
        groups.set(key, g);
      }
      g.snapshots.push(c);
      if (!g.maps.has(c.mapId)) {
        const m = P.match(c.matchId);
        const mapRow = m && (m.maps || []).find((x) => x.id === c.mapId);
        g.maps.set(c.mapId, {
          matchId: c.matchId,
          mapId: c.mapId,
          map: mapRow ? mapRow.map : null,
          result: mapRow && mapRow.winner
            ? (mapRow.winner === c.teamId ? "win" : "loss") : null,
        });
      }
    });
    return Array.from(groups.values())
      .sort((a, b) => b.snapshots.length - a.snapshots.length);
  }

  const all = buildGroups();

  /* filters */
  const teams = Array.from(new Set(all.map((g) => g.teamId)));
  $("#cp-team").innerHTML = `<option value="all">All teams</option>` +
    teams.map((t) => {
      const team = P.team(t);
      return `<option value="${esc(t)}">${esc(team ? team.name : t)}</option>`;
    }).join("");
  const mapIds = Array.from(new Set(all.flatMap((g) =>
    Array.from(g.maps.values()).map((m) => m.map).filter(Boolean))));
  $("#cp-map").innerHTML = `<option value="all">All maps</option>` +
    mapIds.map((m) => `<option value="${esc(m)}">${esc(P.mapInfo(m).name)}</option>`).join("");

  const qs = P.qs();
  if (qs.get("team")) $("#cp-team").value = qs.get("team");
  if (qs.get("map")) $("#cp-map").value = qs.get("map");

  function card(g) {
    const mapsArr = Array.from(g.maps.values());
    const wins = mapsArr.filter((m) => m.result === "win").length;
    const losses = mapsArr.filter((m) => m.result === "loss").length;
    const conf = g.snapshots.map((s) => s.confidence).filter((v) => v != null);
    const meanConf = conf.length
      ? (conf.reduce((a, b) => a + b, 0) / conf.length).toFixed(3) : null;
    return `<article class="card card--spot compi panel-cut">
      <div class="compi__head">
        ${P.teamPlate(g.teamId, { link: true })}
        <span class="chip" data-cap="verified">verified</span>
        ${meanConf ? `<span class="mono dim">mean conf ${meanConf}</span>` : ""}
        <span class="compi__count">${g.snapshots.length}<small> snapshot${g.snapshots.length === 1 ? "" : "s"}</small></span>
      </div>
      ${P.heroStrip(g.heroes)}
      <div class="compi__ctx">
        ${mapsArr.map((m) => {
          const mi = m.map ? P.mapInfo(m.map) : { name: m.mapId, mode: "" };
          const dot = m.result === "win" ? '<span class="win-dot" aria-hidden="true"></span>'
            : m.result === "loss" ? '<span class="loss-dot" aria-hidden="true"></span>' : "";
          const label = m.result === "win" ? "won" : m.result === "loss" ? "lost" : "undecided";
          return `<a class="ev-tick" href="match.html?id=${esc(m.matchId)}&tab=evidence"
            title="Open the match evidence for ${esc(mi.name)}">${dot}${esc(mi.name)} · ${label}</a>`;
        }).join("")}
        <span class="faint">${wins}W–${losses}L on decided maps</span>
      </div>
    </article>`;
  }

  function render() {
    const team = $("#cp-team").value, map = $("#cp-map").value;
    P.setQs({ team, map });
    const groups = all.filter((g) =>
      (team === "all" || g.teamId === team) &&
      (map === "all" || Array.from(g.maps.values()).some((m) => m.map === map)));
    $("#cp-list").innerHTML = groups.map(card).join("");
    const empty = $("#cp-empty");
    empty.hidden = groups.length > 0;
    if (!groups.length)
      empty.innerHTML = P.emptyState("▦", "No verified compositions match",
        all.length
          ? "Loosen the filters — only lineups with verified evidence are listed."
          : "No verified comps in the dataset yet. Run the pipeline export after an ingest.");
    $("#cp-summary").textContent =
      `${groups.length} distinct lineup${groups.length === 1 ? "" : "s"}`;
    P.observeReveals && P.observeReveals(document);
    if (window.OWCSMotion) window.OWCSMotion.observe(document);
  }

  const summaryStats = S.summary({});
  $("#cp-cards").innerHTML = [
    { n: all.length, l: "distinct lineups", s: "grouped by team + five heroes" },
    { n: summaryStats.comps, l: "verified snapshots", s: "reviewed or auto-high only" },
    { n: summaryStats.verifiedMaps, l: "maps with comps", s: "every one traceable to frames" },
    { n: teams.length, l: "teams on record", s: "with at least one proven lineup" },
  ].map((c) => `<div class="card stat-card">
      <span class="sc-num" data-count-to="${c.n}">${c.n}</span>
      <span class="sc-label">${esc(c.l)}</span><span class="sc-sub">${esc(c.s)}</span>
    </div>`).join("");

  $("#cp-team").addEventListener("change", render);
  $("#cp-map").addEventListener("change", render);
  $("#cp-filters").addEventListener("submit", (e) => e.preventDefault());
  render();
})();
