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
    ["Hierarchy from surface, not ornament",
     "Depth is four surface steps and a hairline border. Most panels cast no shadow at all. If a block needs a heavy frame to read as a block, the spacing around it is wrong."],
    ["Status is never colour alone",
     "Every state carries a glyph, a word, or both. Colour is a second channel, never the only one. This is an accessibility rule, not a style preference."],
    ["Restraint is the product's credibility",
     "This tool claims its data has been checked by a person. A screen that shouts reads like a fan site; a screen that is quiet reads like an instrument."],
    ["Colour must encode something",
     "State, role, provenance, evidence. Gold is published, amber needs a human, cyan is working, emerald is evidence, indigo is machine-detected, red is blocked. Colour that encodes nothing is grey."],
    ["Motion is orientation and feedback",
     "A short fade-and-rise on arrival, a one-pixel lift on hover, and that is all. There is no perpetual movement anywhere in the system, and every transition stops under reduced motion."],
    ["The data supplies the personality",
     "Team marks, hero portraits, scores and maps are the colour on a page. The interface around them stays neutral so they are the first thing the eye lands on."],
    ["Uncertainty is never buried",
     "Confidence scores, requested versus actual resolution, expected versus actual crop counts and review status are surfaced by components, not hidden behind them."],
  ].map((r) =>
    '<div class="card rv" style="padding:var(--s-4)"><h3>' + esc(r[0]) + "</h3>" +
    '<p class="small dim u-mt-3" style="margin-bottom:0">' + esc(r[1]) + "</p></div>").join(""));

  /* ----------------------------------------------------------- colour */
  const sw = (v, note) =>
    '<div class="card" style="display:grid;grid-template-columns:44px minmax(0,1fr);' +
      'grid-template-rows:auto auto;gap:2px var(--s-3);align-items:center;padding:var(--s-2)">' +
      '<span style="grid-row:1/3;width:44px;height:44px;border:1px solid var(--line-2);' +
        'border-radius:var(--r-2);' +
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
      sw("--indigo", "detected, machine-authored, not yet confirmed") +
      sw("--red", "blocked, failed") +
    "</div>" +
    '<p class="small dim u-mt-3">There is no seventh, decorative colour. Anything that encodes ' +
    "nothing is one of the neutrals below — which is why a saturated colour on this site can be " +
    "trusted to mean something.</p>" +
    '<h3 class="u-mt-5">Neutrals — everything that is not a signal</h3>' +
    '<div class="grid grid--3 u-mt-3">' +
      sw("--bg", "page") +
      sw("--s1", "card") +
      sw("--s2", "raised, hover") +
      sw("--s3", "active, selected") +
      sw("--line", "hairline border — one weight, everywhere") +
      sw("--tx-2", "secondary text") +
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
        '<p style="font-family:var(--f-display);font-weight:600;font-size:clamp(34px,5vw,68px);' +
        'line-height:1;letter-spacing:-.025em;margin:6px 0 0">' +
        "Reviewed, not guessed</p></div>" +
      '<div><span class="stat__k">Section · clamp(1.35rem, 2.2vw, 1.85rem)</span>' +
        '<h2 style="margin:6px 0 0">Where everything stands</h2></div>' +
      '<div><span class="stat__k">Body · Archivo 400 · max 68ch</span>' +
        '<p class="lede" style="margin:6px 0 0">Saira Condensed is reserved for titles, section ' +
        "headings, scores and large numbers. Everything read at length — body copy, navigation, " +
        "forms, labels — is Archivo, in mixed case.</p></div>" +
      '<div><span class="stat__k">Mono · IBM Plex Mono · commands, evidence, timestamps</span>' +
        '<p class="mono" style="margin:6px 0 0">python3 pipeline/run_owcs_auto.py --url "…" --start 0:06:00</p></div>' +
      '<div><span class="stat__k">Numerals · tabular, section signage</span>' +
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
      '<div class="slab"><p class="eyebrow" style="justify-self:start;margin:0">Panel</p>' +
        "<h2>The section panel</h2>" +
        "<p>A raised surface with one accent edge, for a callout or an explainer that has to read " +
        "as a single block. It stops a scroll by being a different surface, not by shouting.</p></div>" +
      '<div class="grid grid--3">' +
        '<div class="stat" data-accent="gold" data-alert="1">' +
          '<span class="stat__k">Stat card</span>' +
          '<span class="stat__v">128</span>' +
          '<span class="stat__note">Neutral surface; semantic colour on the number only.</span>' +
          '<span class="stat__cta">Where this leads <span aria-hidden="true">&#8594;</span></span></div>' +
        '<div class="card card--hoverable" style="padding:var(--s-4)"><h3>Hoverable card</h3>' +
          '<p class="small dim u-mt-3" style="margin-bottom:0">Hover and keyboard focus lift the ' +
          "card one pixel and lighten its surface and border. Reduced motion keeps the colour " +
          "change and drops the movement.</p></div>" +
        '<div class="card spine spine--emerald" style="padding:var(--s-4)"><h3>Spine</h3>' +
          '<p class="small dim u-mt-3" style="margin-bottom:0">A 3px accent edge carrying state ' +
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

  /* ----------------------------------------------------------- data
     Rendered by the same P.chart helpers the stats page calls, on a fixed
     sample, so this section cannot drift from the real charts either. */
  set("#sg-charts",
    '<div class="grid grid--2">' +
      '<div class="card">' + P.chart.bars([
        { label: "Kiriko", value: 9, note: "82% of line-ups", role: "Support" },
        { label: "D.Va", value: 7, note: "64% of line-ups", role: "Tank" },
        { label: "Sojourn", value: 4, note: "36% of line-ups", role: "Damage" },
        { label: "Juno", value: 2, note: "18% of line-ups", role: "Support" },
      ], {
        byRole: true,
        title: "Magnitude — horizontal bars",
        caption: "Illustrative sample. Bars cap at 22px, square at the baseline and rounded " +
          "at the data end, on a track that shows what the value is out of.",
      }) + "</div>" +
      '<div class="card">' + P.chart.stack([
        { label: "Tank", value: 11, fill: P.chart.roleFill("Tank") },
        { label: "Damage", value: 22, fill: P.chart.roleFill("Damage") },
        { label: "Support", value: 22, fill: P.chart.roleFill("Support") },
      ], {
        title: "Part-to-whole — one stacked bar",
        caption: "Segments are separated by a 2px surface gap, never a stroke. The legend is " +
          "always present: identity is never colour alone.",
      }) + "</div>" +
    "</div>" +
    '<div class="card u-mt-4">' +
      '<p class="small" style="margin:0 0 var(--s-3)">Chart colour is chosen by the job it ' +
      "does. Magnitude is one hue on a track — sequential, no palette needed. Role is " +
      "<i>identity</i>, so it gets a categorical palette, and that palette is deliberately " +
      "<b>not</b> the interface's role tokens: those are tuned to read as a 3px underline on a " +
      "portrait, and side by side as fills they fail deuteranopia separation. The chart steps " +
      "are the same hues re-stepped until the lightness band, chroma floor, CVD separation and " +
      "contrast checks all pass on this surface.</p>" +
      P.dl([
        ["Interface roles", "--role-tank / --role-damage / --role-support — underlines, dots, text"],
        ["Chart roles", "#4a8ded / #b4303a / #1da875 — adjacent fills, worst pair ΔE 12.8 deutan"],
        ["Magnitude", "--gold on an --s2 track; one hue, more is longer"],
        ["Never", "a value on every mark, a second y-axis, or a generated hue for a 9th series"],
      ]) +
    "</div>");

  /* ---------------------------------------------------------- motion */
  set("#sg-motion",
    '<div class="card" style="padding:var(--s-4);display:grid;gap:var(--s-3)">' +
      '<p class="small" style="margin:0">Motion here is <b>orientation and feedback only</b>: a ' +
      "short rise on arrival and a one-pixel lift under the cursor. No parallax, no drift, and " +
      "no ambient movement anywhere in the system.</p>" +
      P.dl([
        ["Smooth scroll", "Lenis, one instance, owned by app/motion.js"],
        ["Entrances", "GSAP + ScrollTrigger, with a watchdog that force-reveals"],
        ["Hover", "1px lift + surface and border shift, 130ms, cubic-bezier(.2,.6,.3,1)"],
        ["Dataset status", "a static provenance line — there is no marquee"],
        ["Reduced motion", "transforms drop, transitions collapse, reveals go inert"],
        ["Save-Data", "the motion layer never boots at all"],
      ]) +
      '<p class="small dim" style="margin:0">Content is never withheld by an animation: reveals ' +
      "add their visible class <b>before</b> the tween runs, a MutationObserver arms anything " +
      "rendered later, and a watchdog force-reveals whatever is still pending — so print, " +
      "find-in-page and deep links can never land on an empty page.</p>" +
    "</div>");

  /* ----------------------------------------------------- dependencies
     A short, honest bill of materials. Each line says what the thing is
     for, so a future maintainer can tell whether removing it would cost
     anything. Every one of them is optional at runtime: the page works
     with the file missing, just with less. */
  set("#sg-deps",
    '<div class="card">' +
      P.dl([
        ["Fuse.js · Apache-2.0",
          "Fuzzy matching behind ⌘K/“/” search and the games filter. Without it both fall " +
          "back to substring matching."],
        ["Panzoom · MIT",
          "Zoom and pan in the evidence viewer, so a 40px portrait inside a 1280px frame can " +
          "actually be judged. Without it the frame still opens, fit to the window."],
        ["GSAP + ScrollTrigger · standard licence",
          "Entrance tweens. Reveals add their visible class before any tween, so a missing " +
          "GSAP cannot withhold content."],
        ["Lenis · MIT", "One smooth-scroll instance, driven by GSAP's ticker."],
        ["Archivo, Saira Condensed, IBM Plex Mono · SIL OFL 1.1",
          "Self-hosted Latin subsets. These used to load from fonts.googleapis.com, which " +
          "meant a render-blocking third-party request and a product that did not look like " +
          "itself wherever that CDN is unreachable."],
      ]) +
      '<p class="small dim" style="margin:var(--s-3) 0 0">Hero presentation art is derived ' +
      "locally by <code>pipeline/hero_crop.py</code>, which finds the hero inside Blizzard's " +
      "official splash before cropping — a centre crop of those puts the head off the top " +
      "edge and fills the tile with backdrop.</p>" +
    "</div>");

  P.observeReveals(document);
})();
