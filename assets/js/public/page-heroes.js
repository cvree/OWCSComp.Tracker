/* =====================================================================
   OWCS Comp Tracker — page-heroes.js
   Hero analytics directory. Rates come ONLY from OWCS_STATS
   (verified comps); heroes without a proven appearance are shown in an
   honest "not yet sighted" section instead of fake zeros.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS_PUB, S = window.OWCS_STATS;
  if (!P || !P.data || !S) return;
  const D = P.data;
  const esc = P.esc;
  const $ = P.$;

  const stats = S.computeHeroStats({});
  const summary = S.summary({});
  const statByHero = new Map(stats.rows.map((r) => [r.heroId, r]));
  const pct = (v) => (v == null ? "—" : Math.round(v * 100) + "%");

  $("#hx-cards").innerHTML = [
    { n: summary.heroesSeen, l: "heroes with verified picks", s: `of ${(D.heroes || []).length} in the pool` },
    { n: summary.comps, l: "verified comps", s: "reviewed or auto-high only" },
    { n: summary.teamMapAppearances, l: "team-map appearances", s: "the unit every rate is built on" },
    { n: summary.matches, l: "matches covered", s: "with click-through evidence" },
  ].map((c) => `<div class="card stat-card">
      <span class="sc-num" data-count-to="${c.n}">${c.n}</span>
      <span class="sc-label">${esc(c.l)}</span><span class="sc-sub">${esc(c.s)}</span>
    </div>`).join("");

  function heroCard(h) {
    const r = statByHero.get(h.id);
    const face = P.assets ? P.assets.heroFace(h, { px: 52 }) : "";
    const roleIc = P.assets ? P.assets.roleIcon(h.role) : "";
    const stat = r
      ? `<div class="hero-card__stats">
           <span class="hero-card__pr">${pct(r.pickRate)}</span>
           <span class="hero-card__sub">${r.picks} pick${r.picks === 1 ? "" : "s"} · WR ${pct(r.winRate)}</span>
         </div>`
      : `<div class="hero-card__stats hero-card--dormant">
           <span class="hero-card__pr">—</span>
           <span class="hero-card__sub">no verified pick</span>
         </div>`;
    return `<a class="card card--link card--spot hero-card${r ? "" : " hero-card--dormant"}"
        data-role="${esc(h.role)}" href="hero.html?id=${esc(h.id)}"
        title="Open hero page — ${esc(h.name)}">
      <span class="hero-card__face">${face}</span>
      <span class="hero-card__body">
        <span class="hero-card__name">${esc(h.name)}</span>
        <span class="hero-card__role">${roleIc}${esc(h.role)}</span>
      </span>
      ${stat}
    </a>`;
  }

  function render() {
    const role = $("#hx-role").value;
    const q = $("#hx-search").value.trim().toLowerCase();
    const match = (h) =>
      (role === "all" || h.role === role) &&
      (!q || h.name.toLowerCase().includes(q) || h.id.includes(q));
    const heroes = (D.heroes || []).filter(match);
    const live = heroes.filter((h) => statByHero.has(h.id))
      .sort((a, b) => statByHero.get(b.id).picks - statByHero.get(a.id).picks
        || a.name.localeCompare(b.name));
    const dormant = heroes.filter((h) => !statByHero.has(h.id))
      .sort((a, b) => a.name.localeCompare(b.name));

    $("#hx-live").innerHTML = live.map(heroCard).join("");
    $("#hx-live-count").textContent = `(${live.length})`;
    const le = $("#hx-live-empty");
    le.hidden = live.length > 0;
    if (!live.length)
      le.innerHTML = P.emptyState("◈", "No heroes match",
        q || role !== "all"
          ? "No hero with verified appearances matches this filter."
          : "No verified comps in the dataset yet — run the pipeline export.");
    $("#hx-dormant").innerHTML = dormant.map(heroCard).join("");
    $("#hx-dormant-count").textContent = `(${dormant.length})`;
    $("#hx-summary").textContent =
      `${live.length} in the meta · ${dormant.length} awaiting proof`;
    P.observeReveals && P.observeReveals(document);
    if (window.OWCSMotion) window.OWCSMotion.observe(document);
  }

  $("#hx-role").addEventListener("change", render);
  $("#hx-search").addEventListener("input", render);
  $("#hx-filters").addEventListener("submit", (e) => e.preventDefault());
  render();
})();
