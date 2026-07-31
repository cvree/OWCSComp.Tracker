/* =====================================================================
   OWCS Comp Tracker — public/page-how.js
   The glossary on how-it-works.html renders its chips with the SAME
   helpers the rest of the site uses (P.chipStatus / P.chipCapture /
   P.badgeSrc / …). If a label ever changes in core.js, this page changes
   with it — a glossary that can drift out of date is worse than none.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS_PUB, esc = P.esc;
  const root = P.$("#gloss-root");
  if (!root) return;

  const rows = [
    [P.chipStatus("live"),
     "The series is on air right now. Match pages show a watch link; compositions still only appear after review, so a live match usually has none yet."],
    [P.chipStatus("upcoming"),
     "Scheduled, not played. Times are shown in your own timezone; where only a date is known the site says “time TBA” rather than inventing an hour."],
    [P.chipStatus("completed"),
     "The series was played. Whether its compositions are published depends entirely on the capture status below."],
    [P.chipStatus("forfeit"),
     "Awarded without play. There is no broadcast, so there can be no capture and no compositions — the page says so instead of showing an empty chart."],
    [P.chipCapture("needs-source"),
     "No broadcast VOD is linked to this match yet, so there is nothing to read compositions from."],
    [P.chipCapture("queued"),
     "A VOD is linked and waiting for capture. Nothing has been read yet."],
    [P.chipCapture("capturing"),
     "Frames are being sampled from the broadcast right now."],
    [P.chipCapture("needs-review"),
     "The machine finished and produced detections that a human has not confirmed. Those detections are held back — this is the most common reason a played match shows no compositions."],
    [P.chipCapture("verified"),
     "The capture run cleared review. Its approved compositions are published with their evidence."],
    [P.chipCapture("failed"),
     "The run stopped on an error. The Evidence tab records what broke; no partial guesswork is published."],
    [`<span class="chip" data-cap="verified">human reviewed</span>`,
     "A person confirmed this exact read against the crops the detector used."],
    [`<span class="chip" data-cap="verified">auto-high</span>`,
     "No human confirmed this one individually, but the detection cleared the high-confidence gate: a strong match, stable across consecutive samples."],
    [P.badgeSrc("cv"),
     "Came from computer vision — reading the broadcast video. Only cv and manual rows can ever supply a composition."],
    [P.badgeSrc("manual"),
     "Entered or corrected by a human. A manual correction always overrides the machine read it replaces, and the original read is kept, never deleted."],
    [P.badgeSrc("faceit"),
     "A match fact imported from FACEIT (teams, schedule, score, bans). These can never become hero compositions."],
    [P.badgeSrc("official"),
     "A fact taken from an official OWCS source — usually schedules, stage windows or team identity."],
    [`<span class="ev-tick">view evidence</span>`,
     "A link into the receipts: the frames, crops and run behind the number you are looking at."],
    [`<span class="chip" data-sw="confirmed">confirmed swap</span>`,
     "A mid-map hero change that held across several consecutive samples, published with the before and after crop."],
    [`<span class="chip" data-cap="needs-review">rejected swap</span>`,
     "A suspected change the temporal test threw out — a dead portrait, a killcam, a one-frame flicker. Listed publicly with its reason so the ledger can be audited."],
    [P.badgeRegion("emea"),
     "The region of the tournament a match belongs to. Region filters on the schedule and stat pages use this, not the teams' nationalities."],
  ];

  root.innerHTML = rows.map(([chip, text]) =>
    `<div class="gloss__row"><div>${chip}</div><div><p>${esc(text)}</p></div></div>`).join("");

  P.observeReveals && P.observeReveals(document);
})();
