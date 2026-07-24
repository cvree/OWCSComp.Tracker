/* =====================================================================
   OWCS Comp Tracker — page-swaps.js
   Swap intelligence. Data comes ONLY from D.heroSwaps (the DB's
   temporal-consensus verdicts, exported verbatim):
     * confirmed swaps render as evidence cards with before/after crops
     * rejected candidates render in an honesty ledger with the reason
       the detector threw them out — they are never shown as swaps.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS_PUB;
  if (!P || !P.data) return;
  const D = P.data;
  const esc = P.esc;
  const $ = P.$;

  const swaps = D.heroSwaps || [];
  const confirmed = swaps.filter((s) => s.status === "confirmed")
    .sort((a, b) => (a.offset || 0) - (b.offset || 0));
  const rejected = swaps.filter((s) => s.status === "rejected");

  $("#sw-cards").innerHTML = [
    { n: confirmed.length, l: "confirmed swaps", s: "persistence + margin + displacement" },
    { n: rejected.length, l: "rejected candidates", s: "noise that never went public" },
    { n: confirmed.filter((s) => s.evidenceBefore && s.evidenceAfter).length,
      l: "with before/after crops", s: "click-through broadcast evidence" },
    { n: new Set(confirmed.map((s) => s.matchId)).size, l: "matches with swap coverage", s: "grows with every ingest" },
  ].map((c) => `<div class="card stat-card">
      <span class="sc-num" data-count-to="${c.n}">${c.n}</span>
      <span class="sc-label">${esc(c.l)}</span><span class="sc-sub">${esc(c.s)}</span>
    </div>`).join("");

  function swapCard(s) {
    const from = P.hero(s.fromHero), to = P.hero(s.toHero);
    const m = P.match(s.matchId);
    const mapRow = m && (m.maps || []).find((x) => x.id === s.mapId);
    const mapInfo = mapRow ? P.mapInfo(mapRow.map) : null;
    const crop = (p, lbl, hh) => `<div class="swap-flow__cell">
        <span class="swap-flow__crop">${p
          ? `<img src="${esc(p)}" alt="Broadcast portrait crop — ${esc(lbl)}" width="84" height="84" loading="lazy">`
          : (P.assets ? P.assets.heroFace(hh, { px: 84 }) : "")}</span>
        <span class="swap-flow__label">${esc(lbl)}</span>
      </div>`;
    return `<article class="card card--trace swap-card">
      <div class="swap-card__head">
        ${P.teamPlate(s.teamId, { link: true })}
        <span class="chip" data-sw="confirmed">confirmed</span>
        <span class="mono dim">${mapInfo ? esc(mapInfo.name) + " · " : ""}slot ${esc(s.slot)} · @${P.fmtOffset(s.offset)}${s.confidence != null ? ` · conf ${s.confidence}` : ""}</span>
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
      ${s.reason ? `<div class="swap-card__why"><b class="mono" style="color:var(--verified)">why it counts:</b> ${esc(s.reason)}</div>` : ""}
    </article>`;
  }

  $("#sw-count").textContent = `(${confirmed.length})`;
  const list = $("#sw-list"), empty = $("#sw-empty");
  if (!confirmed.length) {
    empty.hidden = false;
    empty.innerHTML = P.emptyState("⇄", "No confirmed swaps yet",
      "Swaps appear only after the temporal-consensus model confirms them against the broadcast. Nothing is invented while the ledger is empty.");
  } else {
    list.innerHTML = confirmed.map(swapCard).join("");
  }

  $("#sw-rej-count").textContent = `(${rejected.length})`;
  const rej = $("#sw-rejects"), rejEmpty = $("#sw-rej-empty");
  if (!rejected.length) {
    rej.hidden = true;
    rejEmpty.hidden = false;
    rejEmpty.innerHTML = P.emptyState("×", "No rejected candidates recorded",
      "When the detector rejects a suspected swap, it lands here with its reason.");
  } else {
    rej.innerHTML = `<details>
      <summary><span class="chip" data-sw="rejected">rejected</span> ${rejected.length} candidate${rejected.length === 1 ? "" : "s"} — expand the honesty ledger</summary>
      ${rejected.map((s) => {
        const from = P.hero(s.fromHero), to = P.hero(s.toHero);
        const team = P.team(s.teamId);
        return `<div class="rj-row">
          <span class="rj-what">${esc(team ? team.code : s.teamId)} · slot ${esc(s.slot)} · ${esc(from.name)} → ${esc(to.name)} @${P.fmtOffset(s.offset)}</span>
          <span class="rj-why">${esc(s.reason || "rejected by temporal consensus")}</span>
        </div>`;
      }).join("")}
    </details>`;
  }

  P.observeReveals && P.observeReveals(document);
  if (window.OWCSMotion) window.OWCSMotion.observe(document);
})();
