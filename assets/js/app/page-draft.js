/* =====================================================================
   OWCS Comp Tracker — app/page-draft.js
   The Draft Lab (draft.html): pick a map, set the two bans, build a line-
   up hero by hero, and see — live — what OWCS teams ran with those
   heroes and how it went. The maths is app/lab.js; this file draws.

   The draft follows OWCS rules: each team bans one hero, and the two
   bans must be from different roles. A line-up is one tank, two damage,
   two support. Mirror picks are allowed (both teams can play a hero).

   State lives in the URL, so a draft is a link:
     ?map=<id>&us=a,b&them=c&b1=<our ban>&b2=<their ban>&opp=<team>
   The time window is the site-wide range (app/timerange.js).
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const T = window.OWCS_SHEET_TIERS;
  const L = P && P.lab;
  const U = P && P.tierUI;
  const TR = P && P.timeRange;
  if (!P || !T || !L || !U || !TR) return;
  const esc = P.esc;
  const pct = U.pct;

  const ROLE_ORDER = ["Tank", "Damage", "Support"];
  const SLOTS = ["Tank", "Damage", "Damage", "Support", "Support"];
  const ROLE_ICON = { Tank: "⛨", Damage: "✦", Support: "✚" };
  const roles = {};
  P.heroes().forEach((h) => { roles[h.id] = h.role; });

  const list = (v) => (v || "").split(",").map((x) => x.trim()).filter((x) => roles[x]);
  const state = {
    map: P.qs("map") || "", us: list(P.qs("us")).slice(0, 5), them: list(P.qs("them")).slice(0, 5),
    b1: roles[P.qs("b1")] ? P.qs("b1") : "", b2: roles[P.qs("b2")] ? P.qs("b2") : "",
    opp: P.qs("opp") || "", mode: "us", q: "",
  };

  /* --------------------------------------------------------- helpers */
  const heroName = (id) => P.hero(id).name;
  const face = (id, px) => {
    const h = P.hero(id);
    return '<span class="dl-face" data-role="' + esc(h.role || "") + '">' + P.heroFace(h, px || 36) + "</span>";
  };
  const wl = (r) => r.w + "–" + r.l;
  const plural = (n, w) => n + " " + w + (n === 1 ? "" : "s");
  const byRole = (ids) => ids.slice().sort((a, b) => ROLE_ORDER.indexOf(roles[a]) - ROLE_ORDER.indexOf(roles[b]));
  const roleCount = (ids, role) => ids.filter((h) => roles[h] === role).length;
  const NEED = L.NEED;

  const maps = (() => {
    const m = new Map();
    (T.maps || []).forEach((mp) => { if (mp.map) m.set(mp.map, mp.mode || m.get(mp.map) || ""); });
    return Array.from(m.entries()).map((e) => ({ id: e[0], mode: e[1], name: P.mapInfo(e[0]).name || e[0] }))
      .sort((a, b) => (a.mode || "").localeCompare(b.mode || "") || a.name.localeCompare(b.name));
  })();
  const teams = Object.keys(T.teams || {}).map((id) => ({ id: id, name: U.teamName(id) }))
    .sort((a, b) => a.name.localeCompare(b.name));

  function sync() {
    P.setQs({ map: state.map || null, us: state.us.join(",") || null, them: state.them.join(",") || null,
      b1: state.b1 || null, b2: state.b2 || null, opp: state.opp || null });
  }

  /* What a hero click does in the current mode, or why it can't. */
  function blocked(id) {
    const banned = id === state.b1 || id === state.b2;
    if (state.mode === "us" || state.mode === "them") {
      const team = state[state.mode];
      if (banned) return "Banned";
      if (team.indexOf(id) >= 0) return "Picked";
      if (roleCount(team, roles[id]) >= NEED[roles[id]]) return "Role full";
      if (team.length >= 5) return "Line-up full";
      return "";
    }
    if (state.us.indexOf(id) >= 0 || state.them.indexOf(id) >= 0) return "Picked";
    return "";
  }

  function choose(id) {
    if (!id || blocked(id)) return;
    if (state.mode === "us" || state.mode === "them") state[state.mode].push(id);
    else if (state.mode === "b1") state.b1 = id;
    else if (state.mode === "b2") state.b2 = id;
    /* after a ban, go back to picking for whichever side still has room;
       a full line-up hands over to the other side */
    if (state.mode === "b1" || state.mode === "b2") state.mode = state.us.length < 5 ? "us" : "them";
    else if (state[state.mode].length >= 5) state.mode = state.mode === "us" ? "them" : "us";
    update();
  }

  /* ----------------------------------------------------------- setup */
  function renderSetup() {
    const byMode = new Map();
    maps.forEach((m) => { if (!byMode.has(m.mode)) byMode.set(m.mode, []); byMode.get(m.mode).push(m); });
    const el = document.getElementById("dl-setup");
    el.innerHTML = '<div class="dl-setup__row">' +
      '<label class="field"><span class="mx-label">Map</span><select class="input" data-k="map">' +
        '<option value="">Any map</option>' + Array.from(byMode.entries()).map((e) => '<optgroup label="' + esc(e[0] || "Other") + '">' +
          e[1].map((m) => '<option value="' + esc(m.id) + '"' + (state.map === m.id ? " selected" : "") + ">" + esc(m.name) + "</option>").join("") +
          "</optgroup>").join("") + "</select></label>" +
      '<label class="field"><span class="mx-label">Against <span class="dim">(optional)</span></span><select class="input" data-k="opp">' +
        '<option value="">Any team</option>' + teams.map((t) => '<option value="' + esc(t.id) + '"' + (state.opp === t.id ? " selected" : "") + ">" +
          esc(t.name) + "</option>").join("") + "</select></label>" +
      '<div class="dl-actions">' +
        '<button type="button" class="btn btn--sm btn--ghost" data-act="surprise" title="Fill your line-up with one of the best pro comps here">🎲 Surprise me</button>' +
        '<button type="button" class="btn btn--sm btn--ghost" data-act="copy">Copy link</button>' +
        '<button type="button" class="btn btn--sm btn--quiet" data-act="reset">Reset</button>' +
      "</div></div>";
  }

  /* ----------------------------------------------------------- board */
  function teamPanel(key, title, banKey) {
    const team = state[key];
    const filled = {};
    const slots = SLOTS.map((role) => {
      const idx = filled[role] || 0;
      filled[role] = idx + 1;
      const id = team.filter((h) => roles[h] === role)[idx];
      return id
        ? '<button type="button" class="dl-slot is-filled" data-remove="' + key + ":" + esc(id) + '" title="Remove ' + esc(heroName(id)) + '">' +
            face(id, 44) + '<span class="dl-slot__name">' + esc(heroName(id)) + '</span><span class="dl-slot__x" aria-hidden="true">×</span></button>'
        : '<button type="button" class="dl-slot" data-mode="' + key + '" data-role="' + role + '">' +
            '<span class="dl-slot__role" aria-hidden="true">' + ROLE_ICON[role] + '</span><span class="dl-slot__name dim">' + role + "</span></button>";
    }).join("");
    const ban = state[banKey];
    const banEl = ban
      ? '<button type="button" class="dl-ban is-filled" data-remove="' + banKey + ":" + esc(ban) + '" title="Remove the ban">' + face(ban, 30) +
          "<span>" + esc(heroName(ban)) + '</span><span class="dl-slot__x" aria-hidden="true">×</span></button>'
      : '<button type="button" class="dl-ban" data-mode="' + banKey + '"><span aria-hidden="true">⊘</span><span class="dim">Ban a hero</span></button>';
    return '<section class="dl-team' + (state.mode === key || state.mode === banKey ? " is-active" : "") + '" data-side="' + key + '">' +
      '<div class="dl-team__hd"><h2>' + esc(title) + "</h2>" +
        '<button type="button" class="btn btn--sm ' + (state.mode === key ? "btn--ghost" : "btn--quiet") + '" data-mode="' + key + '" aria-pressed="' +
        (state.mode === key) + '">' + (state.mode === key ? "Picking" : "Pick here") + "</button></div>" +
      '<div class="dl-slots">' + slots + "</div>" +
      '<div class="dl-team__ban"><span class="cb-sub">Ban</span>' + banEl + "</div></section>";
  }

  function renderBoard() {
    const warn = state.b1 && state.b2 && roles[state.b1] === roles[state.b2]
      ? '<p class="dl-warn" role="status">⚠ In OWCS the two bans must be from different roles — both are ' + esc(roles[state.b1]) + ".</p>" : "";
    document.getElementById("dl-board").innerHTML =
      '<div class="dl-board__grid">' + teamPanel("us", "Your team", "b1") +
      '<div class="dl-vs" aria-hidden="true">VS</div>' + teamPanel("them", state.opp ? U.teamName(state.opp) : "Their team", "b2") + "</div>" + warn;
  }

  /* ---------------------------------------------------------- picker */
  const MODES = [["us", "Pick for you"], ["them", "Pick for them"], ["b1", "Your ban"], ["b2", "Their ban"]];
  function renderPicker() {
    const q = state.q.trim().toLowerCase();
    const heroes = P.heroes().filter((h) => !q || h.name.toLowerCase().indexOf(q) >= 0 || h.id.indexOf(q) >= 0);
    const col = (role) => '<div class="dl-pcol"><p class="cb-sub">' + ROLE_ICON[role] + " " + role + "</p>" +
      '<div class="dl-grid">' + heroes.filter((h) => h.role === role).sort((a, b) => a.name.localeCompare(b.name)).map((h) => {
        const why = blocked(h.id);
        return '<button type="button" class="dl-hero" data-hero="' + esc(h.id) + '"' + (why ? ' disabled title="' + esc(why) + '"' : ' title="' + esc(h.name) + '"') +
          ">" + face(h.id, 40) + '<span class="dl-hero__name">' + esc(h.name) + "</span></button>";
      }).join("") + "</div></div>";
    const el = document.getElementById("dl-picker");
    const focused = document.activeElement && document.activeElement.id === "dl-q";
    el.innerHTML = '<div class="dl-picker__hd"><div class="seg" role="group" aria-label="What a click does">' +
      MODES.map((m) => '<button type="button" data-mode="' + m[0] + '" aria-pressed="' + (state.mode === m[0]) + '">' + esc(m[1]) + "</button>").join("") +
      '</div><label class="dl-search"><span class="visually-hidden">Find a hero</span><input id="dl-q" class="input" type="search" ' +
      'placeholder="Find a hero… (Enter picks it)" autocomplete="off" value="' + esc(state.q) + '"></label></div>' +
      '<div class="dl-pcols">' + ROLE_ORDER.map(col).join("") + "</div>";
    if (focused) { const i = document.getElementById("dl-q"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }
  }

  /* ---------------------------------------------------------- output */
  function compCard(c, useLabel) {
    return '<li class="dl-comp">' + '<span class="dl-comp__faces">' + byRole(c.heroes).map((h) => face(h, 34)).join("") + "</span>" +
      '<span class="dl-comp__rec"><b>' + wl(c) + '</b><small>' + pct(c.raw == null ? 0.5 : c.raw) + " won · " + plural(c.n, "map") + "</small></span>" +
      (c.vs ? '<span class="dl-comp__vs" title="When the other team ran your opponent\'s heroes"><b>' + wl(c.vs) + "</b><small>vs their line-up</small></span>" : "<span></span>") +
      '<span class="dl-comp__teams small dim">' + (c.teams || []).map((t) => esc(U.teamName(t[0]))).join(", ") + "</span>" +
      '<button type="button" class="btn btn--sm btn--ghost" data-use="' + esc(c.heroes.join(",")) + '">' + esc(useLabel || "Use this") + "</button></li>";
  }

  function renderOut() {
    const w = TR.buildOpts();
    const res = L.draft(T, roles, { map: state.map || null, ours: state.us, theirs: state.them, bans: [state.b1, state.b2],
      opp: state.opp || null, asOf: w.asOf, from: w.from });
    const range = TR.phrase();
    const mapName = state.map ? P.mapInfo(state.map).name : "every map";
    const scope = res.where === "all-fallback"
      ? '<p class="dl-note">Only a few line-ups on ' + esc(mapName) + " in " + esc(range) + ", so these use <b>every map</b>. " +
        "Widen the time range to stay on " + esc(mapName) + "." + TR.widenHtml() + "</p>"
      : '<p class="dl-note">' + plural(res.pool, "pro line-up") + " on " + esc(mapName) + " in " + esc(range) + ".</p>";
    TR.note("dl-range", "<b>" + res.poolMaps + "</b> pro maps");

    const done = state.us.length >= 5;
    const finished = res.completions.length
      ? '<ol class="dl-comps">' + res.completions.map((c) => compCard(c)).join("") + "</ol>"
      : P.empty("◇", "No pro comp has every hero you picked", "Remove a hero, change a ban, or widen the time range.") + TR.widenHtml();

    const slots = res.open.length && !done ? res.open.map((role) => {
      const s = res.slots[role];
      return '<div class="dl-slotsugg"><p class="cb-sub">' + ROLE_ICON[role] + " " + role + (s.need > 1 ? " · " + s.need + " to pick" : "") + "</p>" +
        (s.picks.length ? '<div class="dl-chips">' + s.picks.map((r) => '<button type="button" class="dl-chip" data-pick="' + esc(r.id) + '" title="' +
          esc(heroName(r.id) + " with your picks: " + wl(r) + " over " + plural(r.n, "map") + (r.vs ? "; " + wl(r.vs) + " against their line-up" : "")) + '">' +
          face(r.id, 28) + "<span>" + esc(heroName(r.id)) + "</span><b>" + wl(r) + "</b></button>").join("") + "</div>"
          : '<p class="dim small">Nothing recorded with these picks.</p>') + "</div>";
    }).join("") : "";

    const banList = (rows, label, fmt) => rows.length ? '<div class="dl-slotsugg"><p class="cb-sub">' + label + '</p><div class="dl-chips">' +
      rows.map((r) => '<button type="button" class="dl-chip" data-ban="' + esc(r.id) + '" title="Set as your ban">' + face(r.id, 28) +
        "<span>" + esc(heroName(r.id)) + "</span><b>" + fmt(r) + "</b></button>").join("") + "</div></div>" : "";
    const bans = banList(res.bans, "Banned most here", (r) => pct(r.rate)) +
      (state.opp ? banList(res.comfort, "What " + esc(U.teamName(state.opp)) + " plays most", (r) => pct(r.share)) : "");

    const answers = state.them.length ? '<section class="dl-box dl-box--wide"><h3>What has beaten their line-up</h3>' +
      '<p class="dim small">Comps that faced a line-up with ' + esc(byRole(state.them).map(heroName).join(", ")) + ", best record first.</p>" +
      (res.answers.length ? '<ol class="dl-comps">' + res.answers.map((c) => compCard(c, "Use this")).join("") + "</ol>"
        : '<p class="dim small">No full line-up recorded against those heroes here.</p>') + "</section>" : "";

    document.getElementById("dl-out").innerHTML = scope +
      '<div class="dl-outgrid">' +
      (slots ? '<section class="dl-box"><h3>Fill the next slot</h3><p class="dim small">The best record with every hero you have so far. ' +
        "Tap one to add it.</p>" + slots + "</section>" : "") +
      '<section class="dl-box dl-box--wide"><h3>' + (state.us.length ? "Pro comps with your picks" : "The best pro comps here") + "</h3>" +
        '<p class="dim small">Finished five-hero comps' + (state.us.length ? " containing every hero you picked" : "") +
        (state.b1 || state.b2 ? ", without the banned heroes" : "") + ". Ranked by record, pulled toward 50% on small samples.</p>" +
        finished + "</section>" +
      answers +
      '<section class="dl-box"><h3>Bans to consider</h3><p class="dim small">Tap one to make it your ban.</p>' +
        (bans || '<p class="dim small">No bans recorded here.</p>') + "</section>" +
      "</div>";
    window.__dlLast = res;
  }

  function update() {
    sync();
    renderBoard();
    renderPicker();
    renderOut();
  }

  /* ----------------------------------------------------------- wiring */
  document.addEventListener("DOMContentLoaded", () => {
    if (!T.maps || !T.maps.length) { P.mount("dl-out", P.empty("◇", "No pro data yet", "")); return; }
    TR.mount("dl-range");
    TR.onChange(() => renderOut());
    renderSetup();
    update();

    document.getElementById("dl-setup").addEventListener("change", (e) => {
      const k = e.target.dataset.k;
      if (!k) return;
      state[k] = e.target.value;
      update();
    });
    document.getElementById("main").addEventListener("click", (e) => {
      const t = e.target.closest && e.target.closest("button");
      if (!t) return;
      const d = t.dataset;
      if (d.act === "reset") {
        Object.assign(state, { us: [], them: [], b1: "", b2: "", mode: "us", q: "" });
        update();
      } else if (d.act === "copy") {
        P.copy(location.href, "Draft link copied — paste it anywhere");
      } else if (d.act === "surprise") {
        const top = (window.__dlLast && window.__dlLast.completions) || [];
        const pick = top[Math.floor(Math.random() * Math.min(top.length, 5))];
        if (pick) { state.us = pick.heroes.slice(); state.mode = "them"; update(); }
        else P.toast("Nothing to draw from here — widen the time range", "error");
      } else if (d.remove) {
        const parts = d.remove.split(":");
        if (parts[0] === "b1" || parts[0] === "b2") state[parts[0]] = "";
        else state[parts[0]] = state[parts[0]].filter((h) => h !== parts[1]);
        state.mode = parts[0];
        update();
      } else if (d.mode) {
        state.mode = d.mode;
        update();
      } else if (d.hero) {
        choose(d.hero);
      } else if (d.pick) {
        state.mode = "us";
        choose(d.pick);
      } else if (d.ban) {
        state.mode = "b1";
        choose(d.ban);
      } else if (d.use) {
        state.us = d.use.split(",").filter((h) => roles[h] && h !== state.b1 && h !== state.b2).slice(0, 5);
        state.mode = "them";
        update();
      }
    });
    document.getElementById("dl-picker").addEventListener("input", (e) => {
      if (e.target.id !== "dl-q") return;
      state.q = e.target.value;
      renderPicker();
    });
    document.getElementById("dl-picker").addEventListener("keydown", (e) => {
      if (e.target.id !== "dl-q" || e.key !== "Enter") return;
      e.preventDefault();
      const first = document.querySelector(".dl-hero:not([disabled])");
      if (first) { state.q = ""; choose(first.dataset.hero); document.getElementById("dl-q").focus(); }
    });
  });
})();
