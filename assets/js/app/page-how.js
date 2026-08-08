/* =====================================================================
   OWCS Comp Tracker — app/page-how.js
   The explainer renders the SAME step definitions the product uses, so
   the page can never drift out of date with the thing it explains.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const esc = P.esc;

  const LIMITS = [
    ["Coverage is small, and stated", "Only games that have been submitted, processed and " +
      "reviewed are here. A team or hero missing from a table has not been seen in approved " +
      "data — it does not mean it was never played."],
    ["Nothing is inferred", "A score the tracker did not read is shown as “not recorded”, " +
      "never as 0. An empty field is an empty field."],
    ["Detection is not perfect", "Portraits get obscured, productions change their HUD, and " +
      "the detector guesses wrong. That is precisely why a person confirms every read, and " +
      "why the frame stays attached to the claim."],
    ["This is a fan project", "Independent, unofficial, not affiliated with or endorsed by " +
      "Blizzard Entertainment or the Overwatch Champions Series. Hero art is used under " +
      "Blizzard's Fan Content Policy."],
  ];

  const GLOSSARY = [
    ["Game", "One series between two teams, from one broadcast. What everything else hangs off."],
    ["Line-up", "The five heroes one team played at one moment on one map."],
    ["Swap", "A hero change during a map. Only published once confirmed."],
    ["Stint", "One player slot holding one hero for a stretch of time. What the review " +
      "screen shows you, one row at a time."],
    ["Confidence", "How sure the detector was, as a percentage. Above 90% across a whole " +
      "stint counts as clean; below that a person must look."],
    ["Evidence", "The image file the reading came from. Kept forever, linked from the claim."],
    ["Reviewed", "A person confirmed it. Publishable."],
    ["Auto-high", "The detector was confident enough across enough frames that the gate " +
      "allowed it without asking. Publishable, and still fully evidenced."],
    ["Needs review", "Waiting for a person. Never published."],
    ["Calibration", "Teaching the tracker where a particular production puts the hero " +
      "portraits on screen. Done once per broadcast."],
  ];

  document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("steps").innerHTML = P.games.STEPS.map((s, i) =>
      '<div class="flow__step" data-state="done">' +
      '<span class="flow__dot" aria-hidden="true">' + (i + 1) + "</span>" +
      '<div><div class="flow__label">' + esc(s.label) + "</div>" +
      '<div class="flow__detail">' + esc(s.say) + "</div></div>" +
      '<span class="flow__aside">' + (i === 5 ? "you" : "machine") + "</span></div>").join("");

    document.getElementById("limits").innerHTML = LIMITS.map((l) =>
      '<div class="card rv"><h3>' + esc(l[0]) + '</h3><p class="dim u-mt-3">' +
      esc(l[1]) + "</p></div>").join("");

    document.getElementById("glossary").innerHTML = GLOSSARY.map((g) =>
      "<dt style=\"font:600 13px/1.5 var(--f-body);color:var(--gold-hi)\">" + esc(g[0]) + "</dt>" +
      '<dd style="margin:0;color:var(--tx-2);font-size:14px">' + esc(g[1]) + "</dd>").join("");

    document.addEventListener("click", (e) => {
      const c = e.target.closest("[data-copy]");
      if (c) P.copy(c.dataset.copy, "Copied");
    });
  });
})();
