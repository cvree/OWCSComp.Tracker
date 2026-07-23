/* =====================================================================
   OWCS Comp Tracker — page-hero.js
   Hero detail (?id=<heroId>): verified rates, per-team breakdown,
   confirmed swap activity, appearances with evidence links, and the
   portrait's provenance from the asset manifest. Honest empty states
   everywhere — a hero with no verified pick says exactly that.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS_PUB, S = window.OWCS_STATS;
  if (!P || !P.data || !S) return;
  const D = P.data;
  const esc = P.esc;
  const $ = P.$;

  const heroId = P.qs().get("id");
  const hero = heroId ? P.hero(heroId) : null;
  const known = hero && (D.heroes || []).some((h) => h.id === heroId);
  const main = $("#main");
  if (!known) {
    main.innerHTML = P.emptyState("◈", "Unknown hero",
      `No hero with id <code>${esc(heroId || "—")}</code> exists in the dataset. Browse the <a href="heroes.html">hero analytics</a> directory.`);
    return;
  }
  document.title = `${hero.name} — OWCS Comp Tracker`;
  const pct = (v) => (v == null ? "—" : Math.round(v * 100) + "%");
  const fmtT = (s) => P.fmtOffset(s);

  $("#hero-crumbs").innerHTML = P.breadcrumbs([
    { label: "Heroes", href: "heroes.html" },
    { label: hero.name },
  ]);

  /* ---- header ---- */
  const face = P.assets ? P.assets.heroFace(hero, { px: 120 }) : "";
  const roleIc = P.assets ? P.assets.roleIcon(hero.role) : "";
  $("#hero-head").dataset.role = hero.role;
  $("#hero-head").innerHTML = `
    <div class="hero-head__face">${face}</div>
    <div class="hero-head__body">
      <span class="ph-eyebrow" style="font-family:var(--font-serif);letter-spacing:.3em;text-transform:uppercase;color:var(--gold);font-size:12px">Hero dossier</span>
      <h1>${esc(hero.name)}</h1>
      <div class="cluster">
        <span class="hero-card__role" style="font-size:12px">${roleIc}${esc(hero.role)}</span>
        <span class="badge">${esc(hero.id)}</span>
      </div>
    </div>`;

  /* ---- stats ---- */
  const detail = S.heroDetail(heroId, {});
  const row = detail.row || null;
  const swaps = (D.heroSwaps || []).filter(
    (s) => s.status === "confirmed" && (s.fromHero === heroId || s.toHero === heroId));

  $("#hero-cards").innerHTML = [
    { n: row ? row.picks : 0, l: "verified picks", s: "one per team-map appearance" },
    { n: row ? pct(row.pickRate) : "—", l: "pick rate", s: "share of tracked team-maps", raw: true },
    { n: row ? pct(row.winRate) : "—", l: "win rate", s: row && row.wins + row.losses ? `${row.wins}W–${row.losses}L on decided maps` : "no decided maps yet", raw: true },
    { n: swaps.length, l: "confirmed swaps", s: "temporal-consensus verdicts" },
  ].map((c) => `<div class="card stat-card">
      <span class="sc-num"${c.raw ? "" : ` data-count-to="${c.n}"`}>${c.n}</span>
      <span class="sc-label">${esc(c.l)}</span><span class="sc-sub">${esc(c.s)}</span>
    </div>`).join("");

  /* ---- per-team breakdown ---- */
  const teamsEl = $("#hero-teams"), teamsEmpty = $("#hero-teams-empty");
  $("#hero-teams-count").textContent = `(${detail.teams.length})`;
  if (!detail.teams.length) {
    teamsEmpty.hidden = false;
    teamsEmpty.innerHTML = P.emptyState("⛨", "No verified appearances",
      `${esc(hero.name)} hasn't appeared in a verified composition yet. The moment the pipeline proves a pick, this page fills in — no guesses in the meantime.`);
  } else {
    teamsEl.innerHTML = detail.teams.map((t) => {
      const maxPicks = detail.teams[0].picks || 1;
      return `<div class="drill-team">
        ${P.teamPlate(t.teamId, { link: true })}
        <span class="rate-bar rate-cell"><span class="rb-track"><span class="rb-fill" style="width:${(t.picks / maxPicks) * 100}%"></span></span></span>
        <span class="mono">${t.picks} pick${t.picks === 1 ? "" : "s"} · ${t.wins}W–${t.losses}L</span>
        <span class="drill-team__evi">${t.evidence.slice(0, 3).map((e) =>
          `<a class="ev-tick" href="match.html?id=${esc(e.matchId)}&tab=evidence">${esc(e.mapId.split("-").pop())}</a>`).join("")}</span>
      </div>`;
    }).join("");
  }

  /* ---- swap activity ---- */
  const swEl = $("#hero-swaps"), swEmpty = $("#hero-swaps-empty");
  $("#hero-swaps-count").textContent = `(${swaps.length})`;
  if (!swaps.length) {
    swEmpty.hidden = false;
    swEmpty.innerHTML = P.emptyState("⇄", "No confirmed swaps",
      "No temporally-confirmed swap involves this hero yet. Rejected candidates never show here — see the <a href='swaps.html'>swap intelligence</a> page for the honesty ledger.");
  } else {
    swEl.innerHTML = swaps.map((s) => swapCard(s)).join("");
  }

  function swapCard(s) {
    const from = P.hero(s.fromHero), to = P.hero(s.toHero);
    const m = P.match(s.matchId);
    const crop = (p, lbl, hh) => `<div class="swap-flow__cell">
        <span class="swap-flow__crop">${p
          ? `<img src="${esc(p)}" alt="Broadcast portrait crop — ${esc(lbl)}" width="84" height="84">`
          : (P.assets ? P.assets.heroFace(hh, { px: 84 }) : "")}</span>
        <span class="swap-flow__label">${esc(lbl)}</span>
      </div>`;
    return `<div class="card card--trace swap-card">
      <div class="swap-card__head">
        ${P.teamPlate(s.teamId, { link: true })}
        <span class="chip" data-sw="confirmed">confirmed</span>
        <span class="mono dim">slot ${esc(s.slot)} · @${fmtT(s.offset)}${s.confidence != null ? ` · conf ${s.confidence}` : ""}</span>
        ${m ? `<a class="ev-tick" style="margin-left:auto" href="match.html?id=${esc(s.matchId)}&tab=evidence">open match evidence</a>` : ""}
      </div>
      <div class="swap-flow">
        ${crop(s.evidenceBefore, from.name, from)}
        <div class="swap-flow__arrow" aria-hidden="true">
          <span class="sf-t">${esc(from.name)} → ${esc(to.name)}</span>
          <span class="sf-line"></span>
        </div>
        ${crop(s.evidenceAfter, to.name, to)}
      </div>
      ${s.reason ? `<div class="swap-card__why">${esc(s.reason)}</div>` : ""}
    </div>`;
  }

  /* ---- appearances ---- */
  const apps = row ? row.evidence : [];
  const appsEl = $("#hero-apps"), appsEmpty = $("#hero-apps-empty");
  $("#hero-apps-count").textContent = `(${apps.length})`;
  if (!apps.length) {
    appsEmpty.hidden = false;
    appsEmpty.innerHTML = P.emptyState("▦", "Nothing on record yet",
      "Appearances list every verified (map, team) sighting with links to the frames they were read from.");
  } else {
    appsEl.innerHTML = apps.map((e) => {
      const m = P.match(e.matchId);
      const mapRow = m && (m.maps || []).find((x) => x.id === e.mapId);
      const mapInfo = mapRow ? P.mapInfo(mapRow.map) : null;
      return `<a class="card card--link card--spot m-card" href="match.html?id=${esc(e.matchId)}&tab=evidence">
        <div class="m-card__meta">
          ${P.chipStatus(m ? m.status : "completed")}
          <span class="map-mode">${esc(mapInfo ? mapInfo.name + " · " + mapInfo.mode : e.mapId)}</span>
          <span class="faint">${e.result ? (e.result === "win" ? "map won" : "map lost") : "map undecided"}</span>
        </div>
        <div class="m-card__row">
          <div class="m-card__teams">${P.teamPlate(e.teamId)}</div>
          <span class="ev-tick">${e.snapshotIds.length} snapshot${e.snapshotIds.length === 1 ? "" : "s"}</span>
        </div>
      </a>`;
    }).join("");
  }

  /* ---- provenance ---- */
  const prov = $("#hero-prov");
  const renderProv = (man) => {
    const e = man && man.heroes ? man.heroes[heroId] : null;
    if (!e) {
      prov.innerHTML = `<p class="dim" style="margin:0">Asset manifest unavailable on this host — the portrait above is either a verified broadcast crop or a designed monogram, never a guess.</p>`;
      return;
    }
    if (e.reviewStatus === "verified-broadcast-crop") {
      prov.innerHTML = `<dl class="ev-kv">
        <dt>Status</dt><dd><span class="ev-tick">verified broadcast crop</span></dd>
        <dt>Cropped from</dt><dd>${esc(e.source)}</dd>
        <dt>Dimensions</dt><dd>${e.width}×${e.height}</dd>
        <dt>Hash</dt><dd>${esc(e.hash)}</dd>
        <dt>Attribution</dt><dd style="font-family:var(--font-body)">${esc(e.attribution)}</dd>
      </dl>`;
    } else {
      prov.innerHTML = `<dl class="ev-kv">
        <dt>Status</dt><dd>intentional designed monogram</dd>
        <dt>Why</dt><dd style="font-family:var(--font-body)">${esc(e.attribution)}</dd>
      </dl>`;
    }
  };
  if (P.assets && P.assets.loadManifest) P.assets.loadManifest(renderProv);
  else renderProv(null);

  P.observeReveals && P.observeReveals(document);
})();
