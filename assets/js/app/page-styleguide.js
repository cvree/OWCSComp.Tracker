/* =====================================================================
   OWCS Comp Tracker — app/page-styleguide.js
   The design system reference.

   Everything on this page is rendered by the SAME helpers the product
   uses — P.stateChip, P.teamPlate, P.heroTile, P.compStrip, P.confMeter,
   P.scorePlate, P.empty, P.note, P.guide, P.cmd. That is the entire
   point: a styleguide built from duplicated markup drifts within a week
   and then lies about the product. This one cannot drift, because if a
   component changes shape it changes shape here too.

   Where a component needs sample data it uses REAL entities from the
   loaded export, and where the export has none it says so rather than
   inventing a fake team or hero — the same rule as every other screen.
   ===================================================================== */
(function () {
  "use strict";

  const P = window.OWCS;
  if (!P) return;
  const D = P.pub, esc = P.esc, $ = P.$;
  const set = (sel, html) => { const el = $(sel); if (el) el.innerHTML = html; };

  /* ------------------------------------------------------------ rules */
  set("#sg-rules", [
    ["Structure over decoration",
     "Borders, blocks and hard offset shadows carry the hierarchy. Nothing is rounded and nothing is blurred. If a panel needs a gradient to look like a panel, it is not a panel."],
    ["Status is never colour alone",
     "Every state carries a glyph, a word, or both. Colour is a second channel, never the only one. This is an accessibility rule, not a style preference."],
    ["Candy fills take ink text",
     "A saturated fill is always paired with near-black text. Neon against white is how a bold palette becomes an unreadable one."],
    ["Colour must encode something",
     "State, role, provenance, evidence. Gold is published, amber needs a human, cyan is working, emerald is evidence, red is blocked. Colour that encodes nothing is bone, ink or grey."],
    ["Motion is orientation and feedback",
     "Blocks snap into place, controls press under the cursor, and that is all. The ticker is the single perpetual movement in the system and it stops completely under reduced motion."],
    ["Uncertainty is never buried",
     "Confidence scores, requested versus actual resolution, expected versus actual crop counts and review status are surfaced by components, not hidden behind them."],
  ].map((r) =>
    '<div class="card rv" style="padding:var(--s-4)"><h3>' + esc(r[0]) + "</h3>" +
    '<p class="small dim u-mt-3" style="margin-bottom:0">' + esc(r[1]) + "</p></div>").join(""));

  /* ----------------------------------------------------------- colour */
  const sw = (v, note) =>
    '<div class="card" style="display:grid;grid-template-columns:44px minmax(0,1fr);' +
      'grid-template-rows:auto auto;gap:2px var(--s-3);align-items:center;padding:var(--s-2)">' +
      '<span style="grid-row:1/3;width:44px;height:44px;border:2px solid var(--ink);' +
        "background:var(" + v + ')"></span>' +
      '<span class="mono small" style="align-self:end;color:var(--tx)">' + esc(v) + "</span>" +
      '<span class="small dim" style="align-self:start">' + esc(note) + "</span></div>";
  set("#sg-colour",
    "<h3>Signal colours — meanings unchanged</h3>" +
    '<div class="grid grid--3 u-mt-3">' +
      sw("--gold", "published, verified, the product's spine") +
      sw("--amber", "needs a human") +
      sw("--cyan", "working right now") +
      sw("--emerald", "evidence, proven") +
      sw("--violet", "detected, machine-authored") +
      sw("--red", "blocked, failed") +
    "</div>" +
    '<h3 class="u-mt-5">Candy accents — added by this system</h3>' +
    '<div class="grid grid--3 u-mt-3">' +
      sw("--lime", "primary accent, current selection, live meta") +
      sw("--magenta", "movers, demo data, heat") +
      sw("--bone", "structural borders and ink-on-candy") +
    "</div>" +
    '<h3 class="u-mt-5">Roles</h3>' +
    '<div class="role-key u-mt-3">' +
      '<span><i style="background:var(--role-tank)"></i>Tank</span>' +
      '<span><i style="background:var(--role-damage)"></i>Damage</span>' +
      '<span><i style="background:var(--role-support)"></i>Support</span>' +
    "</div>" +
    '<p class="small dim u-mt-3">Role colour always appears next to the role word or a labelled ' +
    "tile. A colour-blind reader never has to tell blue from red to know what a hero does.</p>");

  /* ------------------------------------------------------------- type */
  set("#sg-type",
    '<div class="card" style="padding:var(--s-5);display:grid;gap:var(--s-4)">' +
      '<div><span class="stat__k">Display · Saira Condensed 700 · headlines</span>' +
        '<p style="font-family:var(--f-display);font-weight:700;font-size:clamp(34px,7vw,84px);' +
        'line-height:.86;letter-spacing:-.015em;text-transform:uppercase;margin:6px 0 0">' +
        "Reviewed, not guessed</p></div>" +
      '<div><span class="stat__k">Section · clamp(1.6rem, 3.2vw, 2.6rem)</span>' +
        '<h2 style="margin:6px 0 0">Where everything stands</h2></div>' +
      '<div><span class="stat__k">Body · Archivo 400 · max 68ch</span>' +
        '<p class="lede" style="margin:6px 0 0">Body copy is deliberately not condensed and not ' +
        "uppercase. The headlines are the poster; the data is the product, and the data has to " +
        "stay readable at length on a phone.</p></div>" +
      '<div><span class="stat__k">Mono · IBM Plex Mono · commands, evidence, timestamps</span>' +
        '<p class="mono" style="margin:6px 0 0">python3 pipeline/run_owcs_auto.py --url "…" --start 0:06:00</p></div>' +
      '<div><span class="stat__k">Numerals · outlined, section signage</span>' +
        '<span class="numeral" style="display:block;margin-top:6px">04</span></div>' +
    "</div>");

  /* ----------------------------------------------------------- status */
  const TRUST = ["published", "production", "reviewed", "auto-high", "detected", "manual",
    "needs-review", "rejected", "partial", "failed", "stale", "demo"];
  set("#sg-status",
    '<div class="card" style="padding:var(--s-4);display:grid;gap:var(--s-5)">' +
      "<div><h3>Game state — the five words the product uses</h3>" +
        '<div class="row u-mt-3">' +
        ["published", "review", "working", "queued", "blocked"].map((s) => P.stateChip(s)).join("") +
        "</div>" +
        '<p class="small dim u-mt-3" style="margin-bottom:0">Each one carries a dot, a word and a ' +
        "title explaining it. Only <b>published</b> reaches the public statistics.</p></div>" +
      "<div><h3>Trust labels</h3>" +
        '<div class="row u-mt-3">' + TRUST.map((t) =>
          '<span class="trust" data-trust="' + esc(t) + '">' + esc(t) + "</span>").join("") + "</div>" +
        '<p class="small dim u-mt-3" style="margin-bottom:0">One component, twelve honest states, ' +
        "used wherever provenance or confidence has to be stated rather than implied.</p></div>" +
      "<div><h3>Confidence</h3>" +
        '<div class="u-mt-3" style="max-width:340px;display:grid;gap:var(--s-3)">' +
          P.confMeter(0.97) + P.confMeter(0.81) + P.confMeter(0.42) + P.confMeter(null) +
        "</div>" +
        '<p class="small dim u-mt-3" style="margin-bottom:0">The detector’s number, shown so ' +
        "you can discount a marginal read — never a quality score for a team.</p></div>" +
      "<div><h3>Score</h3>" +
        '<div class="row u-mt-3">' + P.scorePlate(3, 1, "a") + P.scorePlate(null, null) + "</div>" +
        '<p class="small dim u-mt-3" style="margin-bottom:0">“Not recorded” is a real state. A ' +
        "blank scoreboard would read as a rendering fault; 0–0 would be a lie.</p></div>" +
    "</div>");

  /* ----------------------------------------------------------- blocks */
  const team = D && D.teams && D.teams[0] ? D.teams[0].id : null;
  const heroes = (D && D.heroes ? D.heroes : []).slice(0, 5).map((h) => h.id);
  set("#sg-blocks",
    '<div style="display:grid;gap:var(--s-4)">' +
      '<div class="slab"><p class="eyebrow" style="background:var(--ink);color:var(--lime);' +
        'justify-self:start;margin:0">Slab</p>' +
        "<h2>The loud section break.</h2>" +
        "<p>A saturated full-width block with ink text. Two or three per page, never adjacent. " +
        "It exists to stop a scroll, not to decorate one.</p></div>" +
      '<div class="grid grid--3">' +
        '<div class="stat stat--lime" style="padding:var(--s-4)">' +
          '<span class="stat__k">Stat tile</span>' +
          '<span class="stat__v">128</span>' +
          '<span class="stat__note small dim">Oversized tabular numeral, candy accent, mono label.</span></div>' +
        '<div class="card card--hoverable" style="padding:var(--s-4)"><h3>Pressable block</h3>' +
          '<p class="small dim u-mt-3" style="margin-bottom:0">Hover and keyboard focus both push ' +
          "the block up-left and grow its hard shadow; active presses it in. Reduced motion swaps " +
          "the transform for a border change.</p></div>" +
        '<div class="card spine spine--emerald" style="padding:var(--s-4)"><h3>Spine</h3>' +
          '<p class="small dim u-mt-3" style="margin-bottom:0">A 6px candy edge carrying state ' +
          "without adding another badge. The most reused signal in the system.</p></div>" +
      "</div>" +
      '<div class="grid grid--2">' +
        '<div class="card" style="padding:var(--s-4)"><h3>Team plate</h3><div class="u-mt-3">' +
          (team ? P.teamPlate(team, { size: "lg", link: true })
                : '<p class="small dim">No team in this export to render one from.</p>') + "</div></div>" +
        '<div class="card" style="padding:var(--s-4)"><h3>Composition strip</h3><div class="u-mt-3">' +
          (heroes.length ? P.compStrip(heroes, { link: true })
                         : '<p class="small dim">No hero in this export to render one from.</p>') + "</div></div>" +
      "</div>" +
      "<div><h3>Notices</h3><div class='u-mt-3' style='display:grid;gap:var(--s-3)'>" +
        P.note("ok", "Passed", "Something cleared a gate.") +
        P.note("warn", "Needs attention", "A person has to decide.") +
        P.note("error", "Blocked", "Always stated with the exact next command.") +
        P.note("info", "For context", "Neutral information that changes what you should expect.") +
      "</div></div>" +
      "<div><h3>Buttons</h3><div class='row u-mt-3'>" +
        '<button type="button" class="btn btn--primary">Primary</button>' +
        '<button type="button" class="btn btn--lime">Accent</button>' +
        '<button type="button" class="btn btn--cyan">Guide</button>' +
        '<button type="button" class="btn btn--good">Approve</button>' +
        '<button type="button" class="btn btn--danger">Reject</button>' +
        '<button type="button" class="btn">Default</button>' +
        '<button type="button" class="btn btn--ghost">Ghost</button>' +
      "</div></div>" +
    "</div>");

  /* ----------------------------------------------------------- guides */
  set("#sg-guides",
    '<div style="display:grid;gap:var(--s-4)">' +
      P.guide("A guide component, expanded",
        "Collapsible, numbered, and always reachable from the screen it explains. This is the " +
        "component behind every walkthrough on the site.",
        [
          { title: "A step with prose only", body: "Most steps are a sentence and a link." },
          { title: "A step with an exact command",
            body: "Commands name real scripts in this repository.",
            command: "python3 pipeline/export_data.py",
            commandNote: "The page never executes it — it builds it and hands it over." },
          { title: "A step already done", body: "Completed steps show a tick instead of a number.", done: true },
          { title: "A step that is blocked", body: "Blocked steps go amber and always carry the next command.", blocked: true },
        ], { open: true, id: "sg-guide-demo" }) +
      "<div><h3>Command block, standalone</h3><div class='u-mt-3'>" +
        P.cmd('python3 pipeline/run_owcs_auto.py --url "<broadcast-url>" --start 0:06:00 --end 0:06:30',
              "Selectable either way; the copy button is progressive enhancement and falls back to " +
              "a textarea where the clipboard API is unavailable.") + "</div></div>" +
      "<div><h3>Human gates</h3><div class='grid grid--3 u-mt-3'>" +
        '<div class="gate gate--passed"><div class="gate__head">' +
          '<span class="gate__badge">Passed</span><h4 class="gate__title">Source approval</h4></div>' +
          '<p class="gate__why">A person confirmed this video is the game it claims to be.</p></div>' +
        '<div class="gate"><div class="gate__head">' +
          '<span class="gate__badge">Waiting</span><h4 class="gate__title">Detection review</h4></div>' +
          '<p class="gate__why">Low-confidence reads are queued for a person. UNKNOWN is a valid answer.</p></div>' +
        '<div class="gate gate--blocked"><div class="gate__head">' +
          '<span class="gate__badge">Blocked</span><h4 class="gate__title">Promote gate</h4></div>' +
          '<p class="gate__why">Nothing is published until review passes. This gate is not optional.</p></div>' +
      "</div></div>" +
    "</div>");

  /* ----------------------------------------------------------- empty */
  set("#sg-empty",
    P.empty("▦", "No published compositions match",
      'Says what is missing, why, and what would fill it — with a way out. <a href="games.html">See every game →</a>') +
    P.empty("↕", "No trend yet",
      "A comparison needs data on both sides of a split. Until then this reports that it cannot " +
      "compare, rather than showing a flat zero.") +
    P.empty("○", "Nothing captured here yet",
      "Absence of data and absence of play are different facts, and this product never conflates them."));

  /* ---------------------------------------------------------- motion */
  set("#sg-motion",
    '<div class="card" style="padding:var(--s-4);display:grid;gap:var(--s-3)">' +
      '<p class="small" style="margin:0">Motion here is <b>structural</b>: blocks snap into place ' +
      "from a hard offset, matching the physics of the shadows. No parallax, no drift, and no " +
      "ambient movement except the ticker.</p>" +
      P.dl([
        ["Smooth scroll", "Lenis, one instance, owned by app/motion.js"],
        ["Entrances", "GSAP + ScrollTrigger, with a watchdog that force-reveals"],
        ["Hover pressure", "transform + hard shadow, 110ms, cubic-bezier(.16,1.2,.3,1)"],
        ["Ticker", "46s linear loop, pauses on hover and focus"],
        ["Reduced motion", "ticker stops and wraps, transforms drop, reveals go inert"],
        ["Save-Data", "the motion layer never boots at all"],
      ]) +
      '<p class="small dim" style="margin:0">Content is never withheld by an animation: reveals ' +
      "add their visible class <b>before</b> the tween runs, a MutationObserver arms anything " +
      "rendered later, and a watchdog force-reveals whatever is still pending — so print, " +
      "find-in-page and deep links can never land on an empty page.</p>" +
    "</div>");

  P.observeReveals(document);
})();
