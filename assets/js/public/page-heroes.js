/* =====================================================================
   OWCS Comp Tracker — page-heroes.js  (the hero board)

   One dense, sortable, filterable table of the WHOLE roster. The format
   is the one every good stats site converges on, because it answers the
   question people actually arrive with — "who is being played, and is
   that number worth anything" — in a single view.

   Two rules make it honest here, and they are not negotiable:

     * every rate is rendered next to the sample it came from, and win
       rate is RANKED by the Wilson lower bound (S.wilsonLower), so a hero
       who won their only decided map cannot outrank one who won 8 of 12;
     * a hero with no verified appearance shows "—", never 0%. Zero is a
       claim about the hero; "not sighted" is the truth about the dataset.

   Everything is computed from OWCS_STATS, which reads only reviewed /
   auto-high comps — the same source as every other number on the site.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS_PUB, S = window.OWCS_STATS;
  if (!P || !P.data || !S) return;
  const D = P.data;
  const esc = P.esc;
  const $ = P.$, $$ = P.$$;

  const ROLES = ["Tank", "Damage", "Support"];
  const MAX_COMPARE = 3;

  const state = {
    role: "all", q: "", region: "all",
    tournamentId: "all", teamId: "all", mapId: "all",
    unseen: false, sort: "picks", dir: "desc",
    open: null, compare: [],
  };

  /* ---- deep-linkable state (share a filtered board, not a screenshot) -- */
  const q0 = P.qs();
  ["role", "region", "sort", "dir"].forEach((k) => { if (q0.get(k)) state[k] = q0.get(k); });
  if (q0.get("q")) state.q = q0.get("q");
  if (q0.get("tournament")) state.tournamentId = q0.get("tournament");
  if (q0.get("team")) state.teamId = q0.get("team");
  if (q0.get("map")) state.mapId = q0.get("map");
  if (q0.get("unseen") === "1") state.unseen = true;
  if (q0.get("hero")) state.open = q0.get("hero");
  if (q0.get("cmp")) state.compare = q0.get("cmp").split(",").filter(Boolean).slice(0, MAX_COMPARE);

  /* ---- controls ------------------------------------------------------- */
  const REGION_VARS = { all: "--rg-all", na: "--rg-na", emea: "--rg-emea",
    asia: "--rg-asia", china: "--rg-china", pacific: "--rg-pacific" };
  const seg = $("#region-seg");
  seg.innerHTML = (D.regions || []).map((r) =>
    `<button type="button" data-region="${esc(r.id)}" style="--rg:var(${
      REGION_VARS[r.id] || "--rg-all"})" aria-pressed="false">${
      esc(r.id === "all" ? "All regions" : r.short)}</button>`).join("");

  const selT = $("#sf-tournament"), selTeam = $("#sf-team"), selMap = $("#sf-map");
  selT.innerHTML += (D.tournaments || []).map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");
  selTeam.innerHTML += (D.teams || []).map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");
  selMap.innerHTML += (D.mapsCatalog || []).map((m) => `<option value="${esc(m.id)}">${esc(m.name)}</option>`).join("");
  const setSel = (el, v) => { el.value = Array.from(el.options).some((o) => o.value === v) ? v : "all"; };
  setSel(selT, state.tournamentId); setSel(selTeam, state.teamId); setSel(selMap, state.mapId);
  $("#hx-search").value = state.q;
  $("#hb-unseen").checked = state.unseen;

  /* ---- little renderers ----------------------------------------------- */
  const pct = (v) => (v == null ? null : Math.round(v * 100) + "%");
  const dash = `<span class="faint">—</span>`;

  /* A zero draws NO bar. The usual trick of flooring the width so a small
     value stays visible would render 0% as a sliver, which reads as "a
     little" — the one thing it must not say. */
  function bar(value, max, cls) {
    const raw = max > 0 && value ? (value / max) * 100 : 0;
    const w = raw > 0 ? Math.max(3, Math.round(raw)) : 0;
    return `<span class="hb-pick"><span class="hb-bar ${cls || ""}"><i style="width:${w}%"></i></span>
      <span class="hb-val">${pct(value)}</span></span>`;
  }

  /* Sample strength, stated rather than graded into a letter. The title is
     where the honesty lives: it says what the number is and is not. */
  function sample(r) {
    if (!r.picks) return dash;
    const why = {
      thin: `${r.picks} appearance${r.picks === 1 ? "" : "s"} — arithmetic over a handful of maps, not a measurement of the meta`,
      some: `${r.picks} appearances — enough to describe this dataset, still a small sample`,
      solid: `${r.picks} appearances — the largest samples this dataset has`,
    }[r.grade] || `${r.picks} appearances`;
    return `<span class="hb-sample" data-grade="${esc(r.grade)}" title="${esc(why)}">
      <span class="hb-pips"><i></i><i></i><i></i></span>n=${r.picks}</span>`;
  }

  function evidenceTicks(r) {
    const seen = new Set();
    const out = [];
    r.evidence.forEach((e) => {
      if (seen.has(e.matchId)) return;
      seen.add(e.matchId);
      const m = P.match(e.matchId);
      const label = m
        ? `${(P.team(m.teamA) || { code: "?" }).code} v ${(P.team(m.teamB) || { code: "?" }).code}`
        : e.matchId;
      out.push(`<a class="ev-tick" href="match.html?id=${esc(e.matchId)}&tab=evidence"
        title="Open the evidence chain for this match">${esc(label)}</a>`);
    });
    return out.join(" ") || dash;
  }

  /* ---- sorting --------------------------------------------------------
     Win rate sorts on the CONFIDENCE (Wilson lower bound), not the raw
     rate: that is the whole reason a small-sample board can be ranked at
     all without lying. Unseen heroes always sink, whichever way you sort,
     because "no data" is not a score. */
  const SORTS = {
    hero: (r) => r.name.toLowerCase(),
    role: (r) => ROLES.indexOf(r.role),
    picks: (r) => r.picks,
    pickRate: (r) => (r.pickRate == null ? -1 : r.pickRate),
    winRate: (r) => (r.confidence == null ? -1 : r.confidence),
    sample: (r) => r.picks,
    swaps: (r) => r.swapsIn + r.swapsOut,
    bans: (r) => r.bans,
  };

  function sortRows(rows) {
    const f = SORTS[state.sort] || SORTS.picks;
    const sign = state.dir === "asc" ? 1 : -1;
    return rows.slice().sort((a, b) => {
      if (a.seen !== b.seen) return a.seen ? -1 : 1;
      const va = f(a), vb = f(b);
      const c = typeof va === "string" ? va.localeCompare(vb) : va - vb;
      return c ? c * sign : a.name.localeCompare(b.name);
    });
  }

  /* ---- the expanded row ----------------------------------------------- */
  function drill(r) {
    const filters = current();
    const detail = S.heroDetail(r.heroId, filters);
    const maps = S.heroMaps(r.heroId, filters);
    const maxTeam = Math.max(...detail.teams.map((t) => t.picks), 1);
    const teams = detail.teams.length
      ? detail.teams.map((t) => `<div class="drill-team">
          ${P.teamPlate(t.teamId, { size: "sm", link: true })}
          <span class="rate-cell"><span class="rate-bar"><span class="rb-track"><span class="rb-fill"
            style="width:${Math.round((t.picks / maxTeam) * 100)}%"></span></span></span></span>
          <span class="mono" style="font-size:12px">${t.picks} map${t.picks === 1 ? "" : "s"} · ${t.wins}–${t.losses}</span>
        </div>`).join("")
      : `<p class="dim" style="margin:0;font-size:12.5px">No team has a verified pick of ${esc(r.name)} in this slice.</p>`;
    const mapRows = maps.length
      ? maps.map((m) => `<div>
          <span><a href="stats.html?map=${esc(m.mapId)}">${esc(m.name)}</a>
            <span class="hb-mode">${esc(m.mode || "")}</span></span>
          <span class="mono" style="font-size:12px">${m.picks} pick${m.picks === 1 ? "" : "s"}</span>
          <span class="mono" style="font-size:12px">${m.wins + m.losses ? `${m.wins}–${m.losses}` : "—"}</span>
        </div>`).join("")
      : `<p class="dim" style="margin:0;font-size:12.5px">No map data in this slice.</p>`;
    return `<tr class="hb-drill" data-drill="${esc(r.heroId)}" style="--role-c:var(--role-${esc((r.role || "").toLowerCase())})">
      <td colspan="10"><div class="hb-drill__in">
        <div class="hb-drill__grid">
          <div><h4>Teams that ran ${esc(r.name)}</h4>${teams}</div>
          <div><h4>Maps</h4><div class="hb-maps">${mapRows}</div></div>
          <div><h4>Evidence</h4>
            <p style="margin:0 0 8px">${evidenceTicks(r)}</p>
            <p class="dim" style="margin:0;font-size:12px">Every tick opens the frames the read
              came from.</p>
            <p style="margin:10px 0 0"><a href="hero.html?id=${esc(r.heroId)}">Full dossier for ${esc(r.name)} →</a></p>
          </div>
        </div>
      </div></td></tr>`;
  }

  /* ---- the table ------------------------------------------------------ */
  const COLS = [
    ["cmp", "", null],
    ["hero", "Hero", "hero"],
    ["role", "Role", "role"],
    ["picks", "Picks", "picks"],
    ["pickRate", "Pick rate", "pickRate"],
    ["winRate", "Win rate", "winRate"],
    ["record", "W–L", null],
    ["sample", "Sample", "sample"],
    ["swaps", "Swaps", "swaps"],
    ["bans", "Bans", "bans"],
  ];
  const COL_TITLE = {
    pickRate: "Share of tracked team-map appearances this hero was in",
    winRate: "Ranked by the win rate the evidence supports (Wilson lower bound), not the raw percentage",
    sample: "How many appearances the rates were computed from",
    swaps: "Confirmed mid-map swaps into / out of this hero",
    bans: "Imported match facts — never inferred from video",
  };

  function table(rows) {
    const maxPick = Math.max(...rows.map((r) => r.pickRate || 0), 0.0001);
    const head = COLS.map(([k, label, sortKey]) => {
      const aria = state.sort === sortKey && sortKey
        ? ` aria-sort="${state.dir === "asc" ? "ascending" : "descending"}"` : "";
      const t = COL_TITLE[k] ? ` title="${esc(COL_TITLE[k])}"` : "";
      return `<th scope="col" class="${k === "cmp" ? "hb-cmp" : ""}"${
        sortKey ? ` data-sort="${sortKey}" tabindex="0" role="button" aria-label="Sort by ${esc(label)}"${aria}` : ""}${t}>${esc(label)}</th>`;
    }).join("");

    const body = rows.map((r) => {
      const open = state.open === r.heroId;
      const picked = state.compare.indexOf(r.heroId) !== -1;
      const wr = r.winRate;
      const row = `<tr class="${r.seen ? "" : "hb-unseen "}${open ? "is-open" : ""}"
        data-hero="${esc(r.heroId)}" data-role="${esc(r.role)}" tabindex="0" role="button"
        aria-expanded="${open}" aria-label="Show teams and maps for ${esc(r.name)}">
        <td class="hb-cmp"><input type="checkbox" data-cmp="${esc(r.heroId)}" ${picked ? "checked" : ""}
          aria-label="Compare ${esc(r.name)}"${
          !picked && state.compare.length >= MAX_COMPARE ? " disabled" : ""}></td>
        <td><span class="hb-hero">${P.heroTile(r.heroId, { sm: true })}<span class="hb-name">${esc(r.name)}</span></span></td>
        <td><span class="hb-role">${esc(r.role)}</span></td>
        <td class="num">${r.picks || dash}</td>
        <td>${r.pickRate == null ? dash : bar(r.pickRate, maxPick)}</td>
        <td>${wr == null
          ? `<span class="faint" title="${r.seen ? "No map with a recorded winner yet" : "Not sighted"}">—</span>`
          : bar(wr, 1, "win" + (wr < 0.5 ? " low" : ""))}</td>
        <td class="num">${r.decided ? `${r.wins}–${r.losses}` : dash}</td>
        <td>${sample(r)}</td>
        <td class="num">${r.swapsIn || r.swapsOut
          ? `<span title="${r.swapsIn} in, ${r.swapsOut} out">${r.swapsIn}/${r.swapsOut}</span>` : dash}</td>
        <td class="num">${r.bans || dash}</td>
      </tr>`;
      return row + (open ? drill(r) : "");
    }).join("");

    return `<table class="hb"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
  }

  /* ---- comparison ------------------------------------------------------ */
  function comparePanel(all) {
    const picks = state.compare
      .map((id) => all.find((r) => r.heroId === id)).filter(Boolean);
    const sec = $("#hb-compare-section");
    if (picks.length < 2) { sec.hidden = true; return; }
    sec.hidden = false;
    /* "best" is only marked where a comparison is meaningful — marking a
       winner between two single-map samples would be exactly the false
       confidence this board exists to avoid. */
    const best = (key, higher) => {
      const vals = picks.map((p) => p[key]).filter((v) => v != null);
      if (vals.length < 2) return null;
      return higher ? Math.max.apply(null, vals) : Math.min.apply(null, vals);
    };
    const bestPick = best("pickRate", true);
    const bestConf = best("confidence", true);
    $("#hb-compare").innerHTML = picks.map((r) => {
      const line = (label, value, isBest, note) =>
        `<div class="hb-cmp-row${isBest ? " best" : ""}">
          <span>${esc(label)}${note ? ` <span class="faint" style="font-size:11px">${esc(note)}</span>` : ""}</span>
          <b>${value}</b></div>`;
      return `<div class="hb-cmp-card" style="--role-c:var(--role-${esc((r.role || "").toLowerCase())})">
        <div class="cluster" style="gap:10px;align-items:center">
          ${P.heroTile(r.heroId, { sm: true })}
          <div><h3>${esc(r.name)}</h3><span class="hb-cmp-role">${esc(r.role)}</span></div>
        </div>
        <div style="margin-top:10px">
          ${line("Picks", r.picks || "—")}
          ${line("Pick rate", pct(r.pickRate) || "—", bestPick != null && r.pickRate === bestPick)}
          ${line("Win rate", pct(r.winRate) || "—",
            bestConf != null && r.confidence === bestConf,
            r.decided ? `on ${r.decided} decided` : "no decided map")}
          ${line("Sample", r.picks ? `n=${r.picks}` : "—", false, r.grade === "thin" ? "thin" : "")}
          ${line("Swaps in / out", r.swapsIn + " / " + r.swapsOut)}
          ${line("Bans", r.bans || "—")}
        </div>
        <p style="margin:12px 0 0"><a href="hero.html?id=${esc(r.heroId)}">Dossier →</a></p>
      </div>`;
    }).join("");
  }

  function tray() {
    const el = $("#hb-tray");
    if (!state.compare.length) { el.hidden = true; return; }
    el.hidden = false;
    $("#hb-tray-label").textContent = state.compare.length < 2
      ? `Pick one more hero to compare (${state.compare.length}/${MAX_COMPARE})`
      : `Comparing ${state.compare.length} heroes`;
    $("#hb-tray-who").innerHTML = state.compare.map((id) => {
      const h = P.hero(id);
      return `<span class="chip">${esc(h ? h.name : id)}</span>`;
    }).join("");
  }

  /* ---- render ---------------------------------------------------------- */
  const current = () => ({ region: state.region, tournamentId: state.tournamentId,
    teamId: state.teamId, mapId: state.mapId });

  function render(push) {
    if (push !== false) P.setQs({
      role: state.role === "all" ? null : state.role,
      region: state.region === "all" ? null : state.region,
      tournament: state.tournamentId === "all" ? null : state.tournamentId,
      team: state.teamId === "all" ? null : state.teamId,
      map: state.mapId === "all" ? null : state.mapId,
      q: state.q || null,
      unseen: state.unseen ? "1" : null,
      sort: state.sort === "picks" ? null : state.sort,
      dir: state.dir === "desc" ? null : state.dir,
      hero: state.open, cmp: state.compare.length ? state.compare.join(",") : null,
    });
    $$("button", seg).forEach((b) =>
      b.setAttribute("aria-pressed", b.dataset.region === state.region ? "true" : "false"));

    const board = S.heroBoard(current());
    const all = board.rows;
    const seenAll = all.filter((r) => r.seen);
    const unseenAll = all.filter((r) => !r.seen);

    /* role chips carry their own counts, so the filter tells you what it
       will do before you click it */
    $("#hb-roles").innerHTML = [["all", "All roles"]]
      .concat(ROLES.map((r) => [r, r]))
      .map(([key, label]) => {
        const n = key === "all" ? seenAll.length
          : seenAll.filter((r) => r.role === key).length;
        return `<button type="button" data-role="${esc(key)}"
          aria-pressed="${state.role === key}">${esc(label)}<span class="hb-n">${n}</span></button>`;
      }).join("");

    const needle = state.q.trim().toLowerCase();
    const visible = all.filter((r) =>
      (state.role === "all" || r.role === state.role)
      && (!needle || r.name.toLowerCase().indexOf(needle) !== -1
        || r.heroId.indexOf(needle) !== -1)
      && (state.unseen || r.seen));

    const sum = S.summary(current());
    $("#hx-cards").innerHTML = [
      { n: board.seenCount, l: "heroes sighted", s: `of ${board.poolCount} in the pool` },
      { n: board.totalAppearances, l: "team-map appearances", s: "the unit every rate is built on" },
      { n: sum.comps, l: "verified comps", s: "reviewed or auto-high only" },
      { n: board.swapCount, l: "confirmed swaps", s: "temporal-consensus verdicts" },
    ].map((c) => `<div class="card stat-card"><span class="sc-num" data-count-to="${c.n}">${c.n}</span>
      <span class="sc-label">${esc(c.l)}</span><span class="sc-sub">${esc(c.s)}</span></div>`).join("");

    /* The coverage line is written from the data every time rather than
       kept as copy, so it can never overstate what the dataset holds. */
    const cov = $("#hb-coverage");
    cov.innerHTML = board.totalAppearances
      ? `<span aria-hidden="true">⛨</span><span><b>${board.seenCount} of ${board.poolCount}
          heroes</b> have a verified appearance, across <b>${board.totalAppearances}</b>
          tracked team-map appearance${board.totalAppearances === 1 ? "" : "s"} in
          <b>${sum.matches}</b> match${sum.matches === 1 ? "" : "es"}. Read every rate below as
          “what happened in these maps”, not “the state of the meta” —
          <a href="how-it-works.html#limits">what we don't claim →</a></span>`
      : `<span aria-hidden="true">◌</span><span>No verified compositions in this slice yet, so
          there is nothing to rate. The board fills in as captured maps clear review.</span>`;

    $("#hx-live-count").textContent = `(${visible.length})`;
    $("#hb-unseen-count").textContent = `(${unseenAll.length})`;
    $("#hx-summary").innerHTML = [
      `<span>${visible.length} shown</span>`,
      state.role === "all" ? "" : `<span class="fs-pill">role: ${esc(state.role)}</span>`,
      state.region === "all" ? "" : `<span class="fs-pill">region: ${esc(P.regionName(state.region))}</span>`,
      state.tournamentId === "all" ? "" : `<span class="fs-pill">tournament</span>`,
      state.teamId === "all" ? "" : `<span class="fs-pill">team</span>`,
      state.mapId === "all" ? "" : `<span class="fs-pill">map: ${esc(state.mapId)}</span>`,
      needle ? `<span class="fs-pill">“${esc(needle)}”</span>` : "",
    ].filter(Boolean).join("");

    const wrap = $("#hb-wrap"), empty = $("#hx-live-empty");
    if (!visible.length) {
      wrap.innerHTML = "";
      empty.hidden = false;
      empty.innerHTML = P.emptyState("◈", "No heroes match",
        seenAll.length
          ? "Nothing matches this combination. Widen a filter, or tick “Show heroes not yet sighted”."
          : "No verified comps in this slice yet. Every hero in the pool is one tick away — turn on “Show heroes not yet sighted” to see the roster.");
    } else {
      empty.hidden = true;
      wrap.innerHTML = table(sortRows(visible));
    }

    comparePanel(all);
    tray();
    P.observeReveals && P.observeReveals(document);
    if (window.OWCSMotion) window.OWCSMotion.observe(document);
    $$("#hx-cards [data-count-to]").forEach((el) => P.countUp && P.countUp(el));
    wire();
  }

  /* ---- events ---------------------------------------------------------- */
  function wire() {
    $$("#hb-wrap th[data-sort]").forEach((th) => {
      const act = () => {
        const k = th.dataset.sort;
        if (state.sort === k) state.dir = state.dir === "desc" ? "asc" : "desc";
        else { state.sort = k; state.dir = k === "hero" || k === "role" ? "asc" : "desc"; }
        render();
      };
      th.addEventListener("click", act);
      th.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); act(); }
      });
    });
    $$("#hb-wrap tbody tr[data-hero]").forEach((tr) => {
      const act = (e) => {
        if (e.target.closest("a, input")) return;
        state.open = state.open === tr.dataset.hero ? null : tr.dataset.hero;
        render();
      };
      tr.addEventListener("click", act);
      tr.addEventListener("keydown", (e) => {
        if ((e.key === "Enter" || e.key === " ") && !e.target.closest("a, input")) {
          e.preventDefault(); act(e);
        }
      });
    });
    $$("#hb-wrap input[data-cmp]").forEach((box) => {
      box.addEventListener("change", () => {
        const id = box.dataset.cmp;
        const i = state.compare.indexOf(id);
        if (i === -1) {
          if (state.compare.length >= MAX_COMPARE) { box.checked = false; return; }
          state.compare.push(id);
        } else {
          state.compare.splice(i, 1);
        }
        render();
      });
    });
  }

  $("#hb-roles").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-role]");
    if (!b) return;
    state.role = b.dataset.role;
    render();
  });
  seg.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-region]");
    if (!b) return;
    state.region = b.dataset.region;
    render();
  });
  $("#hx-search").addEventListener("input", (e) => { state.q = e.target.value; render(); });
  [["sf-tournament", "tournamentId"], ["sf-team", "teamId"], ["sf-map", "mapId"]]
    .forEach(([id, key]) => {
      $("#" + id).addEventListener("change", (e) => { state[key] = e.target.value; render(); });
    });
  $("#hb-unseen").addEventListener("change", (e) => { state.unseen = e.target.checked; render(); });
  $("#hb-tray-clear").addEventListener("click", () => { state.compare = []; render(); });
  $("#sf-reset").addEventListener("click", () => {
    state.role = "all"; state.region = "all"; state.q = "";
    state.tournamentId = state.teamId = state.mapId = "all";
    state.unseen = false; state.compare = []; state.open = null;
    $("#hx-search").value = ""; $("#hb-unseen").checked = false;
    selT.value = selTeam.value = selMap.value = "all";
    render();
  });
  $("#hx-filters").addEventListener("submit", (e) => e.preventDefault());

  const fresh = $("#freshness");
  if (fresh && D.meta) fresh.textContent = "as of " + P.fmtLocal(D.meta.generatedAt);
  render(false);
})();
