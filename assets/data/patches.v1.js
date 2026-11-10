/* =====================================================================
   OWCS Comp Tracker — patches.v1.js  (hand-maintained)
   Overwatch balance patches, newest first. The site's time range offers
   "Since patch day" (assets/js/app/timerange.js): every map from the
   newest patch here on, with the rising / falling arrows comparing it
   against the patch before it — how the meta moved since the patch.

   When a balance patch drops, add one line at the top:
     date      the day it went live (YYYY-MM-DD, the live servers' day)
     owcsFrom  optional: the first OWCS match day played on it, when the
               tournament client stayed on the old patch for a while
               (the window then starts here instead of `date`)
     name      what people call it
     url       the patch notes

   Hotfixes that change no hero numbers do not get a line: they would
   reset the window without changing the meta.
   ===================================================================== */
window.OWCS_PATCHES = {
  patches: [
    { id: "2026-11-03", date: "2026-11-03", name: "Season 5 midseason",
      url: "https://overwatch.blizzard.com/en-us/news/patch-notes/live/2026/11/" },
    { id: "2026-10-06", date: "2026-10-06", name: "Season 5 launch",
      url: "https://overwatch.blizzard.com/en-us/news/patch-notes/live/2026/10/" },
  ],
};
