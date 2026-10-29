/* =====================================================================
   OWCS Comp Tracker — app/mapstory.js
   The map, told as a story: who played what, when, and every swap.

   A map's hero data is a set of stints — slot 5 of Al Qadsiah was Juno
   from 30:05 to 39:54, then Lúcio — plus the swaps between them and the
   round windows around them. A table of those is a puzzle. Drawn as ten
   lanes against one clock, it is a picture you can read at a glance and
   then scrub through: drag the playhead (or press play) and both line-ups
   above it change as the map did.

   Built only from what was recorded. A lane with no stint at some moment
   shows a gap, not a guess; the clock spans exactly the recorded stints
   and rounds, never a nominal map length; swaps the tracker REJECTED are
   available behind a toggle, because hiding them would make the record
   look more certain than it is. Every swap marker opens the frames it was
   read from.

   Motion is feedback only: a portrait that changes pops once. Under
   prefers-reduced-motion the playhead still moves (it is the data), but
   nothing animates, and nothing ever plays on its own.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  if (!P) return;
  const esc = P.esc;
  const PLAY_SECONDS = 14;          // a whole map replays in this long

  /* ------------------------------------------------------------- model */
  function model(m, game) {
    const pub = P.pub || {};
    const stints = (pub.heroStints || []).filter((s) => s.mapId === m.id &&
      s.start != null && s.end != null && s.end >= s.start);
    if (!stints.length) return null;
    const swaps = (pub.heroSwaps || []).filter((s) => s.mapId === m.id && s.offset != null);
    const rounds = (m.rounds || []).filter((r) => r.start != null && r.end != null);
    const times = stints.map((s) => s.start).concat(stints.map((s) => s.end),
      rounds.map((r) => r.start), rounds.map((r) => r.end));
    const lo = Math.min.apply(null, times);
    const hi = Math.max.apply(null, times);
    if (!(hi > lo)) return null;

    const teams = { a: game.teamA, b: game.teamB };
    stints.forEach((s) => { if (s.teamId) teams[s.side] = teams[s.side] || s.teamId; });
    const lanes = [];
    ["a", "b"].forEach((side) => {
      for (let slot = 1; slot <= 5; slot++) {
        lanes.push({ side: side, slot: slot,
          stints: stints.filter((s) => s.side === side && s.slot === slot)
            .sort((x, y) => x.start - y.start) });
      }
    });
    return { m: m, lo: lo, hi: hi, span: hi - lo, lanes: lanes, rounds: rounds,
      confirmed: swaps.filter((s) => s.status === "confirmed"),
      rejected: swaps.filter((s) => s.status !== "confirmed"),
      teams: teams };
  }

  const at = (md, t) => ((t - md.lo) / md.span) * 100;

  /* The hero in one lane at one moment, or null for a recorded gap. */
  function heroAt(lane, t) {
    for (let i = 0; i < lane.stints.length; i++) {
      const s = lane.stints[i];
      if (t >= s.start && t <= s.end) return s.hero;
    }
    return null;
  }

  /* Contiguous stretches where a team's five heroes did not change — the
     compositions it actually ran, and for how long. */
  function eras(md, side) {
    const lanes = md.lanes.filter((l) => l.side === side);
    const cuts = new Set([md.lo, md.hi]);
    lanes.forEach((l) => l.stints.forEach((s) => { cuts.add(s.start); cuts.add(s.end); }));
    const ts = Array.from(cuts).sort((a, b) => a - b);
    const out = [];
    for (let i = 0; i < ts.length - 1; i++) {
      const mid = (ts[i] + ts[i + 1]) / 2;
      const heroes = lanes.map((l) => heroAt(l, mid));
      if (heroes.some((h) => !h)) continue;
      const key = heroes.join(",");
      const prev = out[out.length - 1];
      if (prev && prev.key === key && Math.abs(prev.end - ts[i]) < 1) prev.end = ts[i + 1];
      else out.push({ key: key, heroes: heroes, start: ts[i], end: ts[i + 1] });
    }
    const total = out.reduce((n, e) => n + (e.end - e.start), 0) || 1;
    out.forEach((e) => { e.share = (e.end - e.start) / total; });
    return out;
  }

  /* -------------------------------------------------------------- html */
  function lineupHtml(md, side, t) {
    const lanes = md.lanes.filter((l) => l.side === side);
    return '<div class="ms-now__team" data-side="' + side + '">' +
      P.teamPlate(md.teams[side], { short: false }) +
      '<div class="ms-now__heroes">' + lanes.map((l) => {
        const h = heroAt(l, t);
        return '<span class="ms-now__slot" data-lane="' + side + l.slot + '" data-hero-now="' + esc(h || "") + '">' +
          (h ? P.heroTile(h, { size: "lg" }) : '<span class="ms-now__gap" title="Not read at this moment">—</span>') +
          "</span>";
      }).join("") + "</div></div>";
  }

  function laneHtml(md, lane) {
    const segs = lane.stints.map((s) => {
      const h = P.hero(s.hero);
      const left = at(md, s.start), width = Math.max(0.6, at(md, s.end) - left);
      return '<span class="ms-seg" data-role="' + esc(h.role || "") + '" style="left:' + left.toFixed(2) +
        "%;width:" + width.toFixed(2) + '%" title="' + esc(h.name + " · " + P.fmtClock(s.start) + "–" +
        P.fmtClock(s.end) + (s.meanConfidence != null ? " · " + Math.round(s.meanConfidence * 100) + "% sure" : "")) + '">' +
        '<span class="ms-seg__face">' + P.heroFace(h, 28) + "</span>" +
        '<span class="ms-seg__name">' + esc(h.name) + "</span></span>";
    }).join("");
    const marks = (list, kind) => list.filter((s) => s.side === lane.side && s.slot === lane.slot)
      .map((s) => {
        const from = P.hero(s.fromHero).name, to = P.hero(s.toHero).name;
        const title = (kind === "swap" ? "Swap: " : "Rejected: ") + from + " → " + to + " at " +
          P.fmtClock(s.offset) + (kind === "swap" ? "" : " — " + (s.reason || ""));
        const ev = s.evidenceAfter || s.evidenceBefore;
        return '<button type="button" class="ms-mark ms-mark--' + kind + '" style="left:' +
          at(md, s.offset).toFixed(2) + '%" title="' + esc(title) + '" aria-label="' + esc(title) + '"' +
          (ev ? ' data-evidence="' + esc(ev) + '" data-evidence-cap="' + esc(from + " → " + to + " at " +
            P.fmtClock(s.offset)) + '"' : "") + "></button>";
      }).join("");
    return '<div class="ms-lane" data-side="' + lane.side + '">' +
      '<span class="ms-lane__label mono">' + (lane.side === "a" ? "L" : "R") + lane.slot + "</span>" +
      '<div class="ms-lane__track">' + segs + marks(md.confirmed, "swap") + marks(md.rejected, "rejected") +
      "</div></div>";
  }

  /* A composition held for a few seconds is the edge of a stint — a
     portrait read a sample early or late — not something a team chose to
     run. It is still counted, as "brief", but it does not get a row. */
  const ERA_MIN_SECONDS = 30;

  function erasHtml(md, side) {
    const all = eras(md, side);
    const list = all.filter((e) => e.end - e.start >= ERA_MIN_SECONDS);
    const brief = all.length - list.length;
    if (!list.length) return "";
    return '<div class="ms-eras__team"><div class="u-flex u-between u-center u-gap-3">' +
      P.teamPlate(md.teams[side], { size: "sm", short: true }) +
      '<span class="dim small">' + list.length + (list.length === 1 ? " composition" : " compositions") +
      (brief ? " · " + brief + " brief change" + (brief === 1 ? "" : "s") + " under " + ERA_MIN_SECONDS + "s" : "") +
      "</span></div>" +
      list.map((e) => '<div class="ms-era">' + P.compStrip(e.heroes, { size: "xs", tight: true }) +
        '<span class="ms-era__bar"><span style="width:' + Math.max(2, Math.round(e.share * 100)) + '%"></span></span>' +
        '<span class="ms-era__time mono">' + esc(P.fmtClock(e.end - e.start)) + " · " +
          Math.round(e.share * 100) + "%</span></div>").join("") + "</div>";
  }

  function html(md) {
    const id = "ms-" + md.m.id.replace(/[^a-z0-9-]/gi, "");
    return '<section class="ms" id="' + esc(id) + '" data-map="' + esc(md.m.id) + '">' +
      '<div class="ms-now" aria-live="off">' +
        lineupHtml(md, "a", md.lo) +
        '<div class="ms-now__clock"><span class="ms-now__time mono" data-clock>' + esc(P.fmtClock(md.lo)) +
          '</span><span class="ms-now__round dim small" data-round></span></div>' +
        lineupHtml(md, "b", md.lo) +
      "</div>" +
      '<div class="ms-river" tabindex="0" role="slider" aria-label="Scrub through the map"' +
        ' aria-valuemin="' + md.lo + '" aria-valuemax="' + md.hi + '" aria-valuenow="' + md.lo +
        '" aria-valuetext="' + esc(P.fmtClock(md.lo)) + '">' +
        '<div class="ms-rounds">' + md.rounds.map((r) =>
          '<span class="ms-round" style="left:' + at(md, r.start).toFixed(2) + "%;width:" +
          (at(md, r.end) - at(md, r.start)).toFixed(2) + '%"><i>Round ' + esc(r.index) + "</i></span>").join("") +
        /* The playhead shares the rounds' box, which is offset exactly as
           the lane tracks are, so a percentage means the same moment in
           both. */
        '<span class="ms-head" data-head style="left:0%"><span class="ms-head__tip mono" data-head-tip>' +
          esc(P.fmtClock(md.lo)) + "</span></span>" +
        "</div>" +
        md.lanes.filter((l) => l.side === "a").map((l) => laneHtml(md, l)).join("") +
        '<div class="ms-divider"></div>' +
        md.lanes.filter((l) => l.side === "b").map((l) => laneHtml(md, l)).join("") +
      "</div>" +
      '<div class="ms-scale mono dim small"><span>' + esc(P.fmtClock(md.lo)) + "</span><span>" +
        esc(P.fmtClock(md.hi)) + "</span></div>" +
      '<div class="ms-controls">' +
        '<button type="button" class="btn btn--sm btn--primary" data-play>▶ Play the map</button>' +
        '<span class="dim small">Drag across the lanes, or use ← → · ' + md.confirmed.length +
          (md.confirmed.length === 1 ? " confirmed swap" : " confirmed swaps") + "</span>" +
        (md.rejected.length
          ? '<label class="ms-toggle small"><input type="checkbox" data-show-rejected> Show the ' +
            md.rejected.length + " swap" + (md.rejected.length === 1 ? "" : "s") + " the tracker rejected</label>"
          : "") +
      "</div>" +
      '<div class="ms-eras"><p class="label">What each team ran</p>' +
        '<div class="grid grid--2 u-mt-3">' + erasHtml(md, "a") + erasHtml(md, "b") + "</div></div>" +
      "</section>";
  }

  /* ------------------------------------------------------- behaviour */
  const reduced = P.media ? P.media("(prefers-reduced-motion: reduce)") : null;

  function wire(root, md) {
    const river = root.querySelector(".ms-river");
    const head = root.querySelector("[data-head]");
    const tip = root.querySelector("[data-head-tip]");
    const clock = root.querySelector("[data-clock]");
    const roundEl = root.querySelector("[data-round]");
    const playBtn = root.querySelector("[data-play]");
    let t = md.lo, raf = null, dragging = false;

    function set(next, fromPlay) {
      t = Math.max(md.lo, Math.min(md.hi, next));
      const pct = at(md, t).toFixed(3) + "%";
      head.style.left = pct;
      const label = P.fmtClock(t);
      tip.textContent = label;
      clock.textContent = label;
      river.setAttribute("aria-valuenow", Math.round(t));
      river.setAttribute("aria-valuetext", label);
      const r = md.rounds.find((x) => t >= x.start && t <= x.end);
      roundEl.textContent = r ? "Round " + r.index : "between rounds";
      md.lanes.forEach((l) => {
        const cell = root.querySelector('[data-lane="' + l.side + l.slot + '"]');
        const h = heroAt(l, t) || "";
        if (!cell || cell.dataset.heroNow === h) return;
        cell.dataset.heroNow = h;
        cell.innerHTML = h ? P.heroTile(h, { size: "lg" })
          : '<span class="ms-now__gap" title="Not read at this moment">—</span>';
        if (!(reduced && reduced.matches)) {
          cell.classList.remove("is-new");
          void cell.offsetWidth;            // restart the one-shot pop
          cell.classList.add("is-new");
        }
      });
      if (!fromPlay && raf) stop();
    }

    function timeFromEvent(e) {
      const track = river.querySelector(".ms-lane__track") || river;
      const box = track.getBoundingClientRect();
      const x = (e.touches ? e.touches[0].clientX : e.clientX) - box.left;
      return md.lo + Math.max(0, Math.min(1, x / (box.width || 1))) * md.span;
    }

    function stop() {
      if (raf) cancelAnimationFrame(raf);
      raf = null;
      playBtn.textContent = "▶ Play the map";
      root.classList.remove("is-playing");
    }
    function play() {
      if (raf) { stop(); return; }
      if (t >= md.hi - 1) set(md.lo, true);
      playBtn.textContent = "❚❚ Pause";
      root.classList.add("is-playing");
      let last = null;
      const rate = md.span / PLAY_SECONDS;
      const step = (now) => {
        if (last == null) last = now;
        const dt = Math.min(0.1, (now - last) / 1000);
        last = now;
        set(t + dt * rate, true);
        if (t >= md.hi) { stop(); return; }
        raf = requestAnimationFrame(step);
      };
      raf = requestAnimationFrame(step);
    }

    river.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".ms-mark")) return;
      dragging = true;
      if (river.setPointerCapture && e.pointerId != null) river.setPointerCapture(e.pointerId);
      set(timeFromEvent(e));
    });
    river.addEventListener("pointermove", (e) => {
      if (dragging || e.pointerType === "mouse") set(timeFromEvent(e));
    });
    river.addEventListener("pointerup", () => { dragging = false; });
    river.addEventListener("pointercancel", () => { dragging = false; });
    river.addEventListener("keydown", (e) => {
      const big = e.shiftKey ? 30 : 5;
      const k = { ArrowRight: big, ArrowLeft: -big, ArrowUp: big, ArrowDown: -big }[e.key];
      if (k) { e.preventDefault(); set(t + k); }
      else if (e.key === "Home") { e.preventDefault(); set(md.lo); }
      else if (e.key === "End") { e.preventDefault(); set(md.hi); }
      else if (e.key === " ") { e.preventDefault(); play(); }
    });
    playBtn.addEventListener("click", play);
    const toggle = root.querySelector("[data-show-rejected]");
    if (toggle) toggle.addEventListener("change", () =>
      root.classList.toggle("show-rejected", toggle.checked));

    /* Open on the first confirmed swap, if there is one: the most
       interesting moment of the map is the first thing a visitor sees. */
    set(md.confirmed.length ? md.confirmed[0].offset + 1 : md.lo);
  }

  P.mapStory = {
    /* HTML for one map, or "" when nothing per-slot was recorded. */
    html(m, game) {
      const md = model(m, game);
      return md ? html(md) : "";
    },
    /* Wire every rendered story under `root`. Safe to call repeatedly. */
    wire(root, game) {
      P.$$(".ms", root).forEach((el) => {
        if (el.dataset.wired) return;
        const m = (game.maps || []).find((x) => x.id === el.dataset.map);
        const md = m && model(m, game);
        if (!md) return;
        el.dataset.wired = "1";
        wire(el, md);
      });
    },
    eras: (m, game, side) => { const md = model(m, game); return md ? eras(md, side) : []; },
  };
})();
