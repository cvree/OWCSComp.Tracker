/* =====================================================================
   OWCS Comp Tracker — app/page-guide.js
   The walkthrough surface.

   Three hard rules this file obeys:

   1. NOTHING HERE IS EXECUTED BY THE PAGE. On the hosted copy there is
      no server behind a static site; on a local copy the tracker's own
      API does real work through submit.html and review.html. A guide
      builds the exact command and hands it over. Pretending otherwise
      is the one thing this project refuses to do everywhere else, and
      it would be no more acceptable here.

   2. COMMANDS ARE REAL. Every command below names a script that exists
      in this repository, in the same `python3 pipeline/…` form the rest
      of the product uses. A test asserts each one resolves to a file.

   3. EVERY BLOCKED STATE HAS A NEXT STEP. A guide that ends at "it
      failed" is not a guide. Section 05 maps every state a game can
      stop in to what it means and what to do about it.

   The page also mounts the tracker-connection banner, because the
   honest answer to "can I do this right now" depends on whether a local
   tracker is reachable — and that is not something a guide should make
   the reader find out by failing.
   ===================================================================== */
(function () {
  "use strict";

  const P = window.OWCS;
  if (!P) return;
  const esc = P.esc, $ = P.$;

  /* Is a local tracker reachable? The submit/review pages already answer
     this; the guide should too rather than assuming. */
  if (P.api && P.api.mountStatus) P.api.mountStatus("gd-mode");

  /* ------------------------------------------------------------- index */
  const SECTIONS = [
    ["setup", "00", "Set up the project", "Clone, install, and prove it works before touching a broadcast."],
    ["submit", "01", "Submit a game", "Paste a link, get a queued job. The only step most people need."],
    ["calibrate", "02", "Calibrate a layout", "Teach the tracker where the portraits live in a new broadcast."],
    ["review", "03", "Review + human gates", "The points where a person has to say yes."],
    ["publish", "04", "Promote and publish", "Turn reviewed detections into the public dataset."],
    ["troubleshoot", "05", "When something is blocked", "Every stopped state and the exact next step."],
  ];
  $("#gd-index").innerHTML = SECTIONS.map((s) =>
    '<a class="card card--hoverable rv" href="#' + esc(s[0]) + '" ' +
      'style="padding:var(--s-4);display:grid;gap:var(--s-2);align-content:start">' +
      '<span class="eyebrow" style="margin:0;justify-self:start">' + esc(s[1]) + "</span>" +
      "<h3>" + esc(s[2]) + "</h3>" +
      '<p class="small dim" style="margin:0">' + esc(s[3]) + "</p>" +
      '<span class="mono small" style="color:var(--lime)">jump to guide →</span>' +
    "</a>").join("");

  const g = (host, title, intro, steps, opt) => {
    const el = $(host);
    if (el) el.innerHTML = P.guide(title, intro, steps, opt);
  };

  /* --------------------------------------------------------- 00 setup */
  g("#gd-setup", "Set the project up (do this once)",
    'You need Python 3.10 or newer and <b>ffmpeg</b> on your PATH. ffmpeg is what actually ' +
    'decodes the broadcast; without it processing fails immediately with a message about the ' +
    'decoder, which is the single most common first-run problem.',
    [
      { title: "Get the code and install the dependencies",
        body: "From wherever you keep projects:",
        command: 'git clone https://github.com/cvree/OWCSComp.Tracker.git; cd OWCSComp.Tracker; py -m pip install -r requirements.txt' },
      { title: "Check that ffmpeg is visible to Python, not just to you",
        body: "A PATH that works in one shell and not another is why this is a separate check. " +
              "If it prints a version, you are fine.",
        command: "ffmpeg -version" },
      { title: "Run the tests before you change anything",
        body: "This is your baseline. If it is green now, anything that breaks later is yours — " +
              "and you will know exactly when it broke.",
        command: "python3 pipeline/test_site.py; python3 pipeline/test_meta_hub.py" },
      { title: "Start the local tracker",
        body: 'Open <code>http://localhost:8000</code>. This is what turns the submit and review ' +
              'screens from read-only into working ones — the banner at the top of this page ' +
              'says which mode you are in right now.',
        command: "python3 pipeline/serve.py",
        note: "Leave this running in its own terminal window. Everything below runs in a second one." },
    ], { open: true, id: "guide-setup" });

  /* -------------------------------------------------------- 01 submit */
  g("#gd-submit", "Submit a broadcast, start to finish",
    'The tracker downloads a slice of the video, finds the live play inside it, and reads all ' +
    'ten hero portraits off the HUD. It stops — deliberately — at every point where it is not ' +
    'sure, instead of guessing. Expect to run it more than once on a broadcast it has not seen.',
    [
      { title: "Paste the link",
        body: '<a class="btn btn--sm btn--primary" href="submit.html">Open the submit form</a> ' +
              'and paste a broadcast URL. If a local tracker is running it queues the job; if not, ' +
              'the form builds the command for you to run yourself. Either way you see the same ' +
              'thing — it never pretends to have started work it cannot do.' },
      { title: "Pick a window, not the whole video",
        body: "A three-hour broadcast is a three-hour download. Start with about 30 seconds over " +
              "one map's opening — enough to prove the layout is right before you spend real time.",
        command: 'python3 pipeline/run_owcs_auto.py --url "<broadcast-url>" --start 0:06:00 --end 0:06:30 --every 10',
        commandNote: "<code>--start</code>/<code>--end</code> are offsets into the video, not wall-clock times." },
      { title: "Read the result, not the terminal",
        body: 'Open the game from <a href="games.html">Games</a>. It shows how far it got, what ' +
              'it read, and — where they exist — the frames and hero crops behind every slot.',
        note: "Requested versus actual capture resolution is recorded. If they differ, the source " +
              "only had the lower one available. That is stated, not hidden." },
      { title: "Check the crop count before you trust anything",
        body: "Ten slots per sampled frame. If it expected 120 crops and produced 74, either the " +
              "layout is wrong or the HUD was not on screen for part of the window — go to guide " +
              "02 before running anything longer." },
      { title: "Widen the window once the crops look right",
        body: "Now spend the download.",
        command: 'python3 pipeline/run_owcs_auto.py --url "<broadcast-url>" --start 0:06:00 --end 0:26:00 --every 10' },
    ], { id: "guide-submit" });

  /* ----------------------------------------------------- 02 calibrate */
  g("#gd-calibrate", "Calibrate a broadcast the tracker has never seen",
    'A layout file tells the tracker where the ten hero portraits sit in this production’s HUD. ' +
    'There are two ways to make one, and the remote path is almost always the right one because ' +
    'it never downloads the video.',
    [
      { title: "Calibrate from a handful of remote frames",
        body: "This pulls a few frames straight from the source and works the layout out from " +
              "them. No download, minutes not hours.",
        command: 'python3 pipeline/calibrate_remote.py --url "<broadcast-url>" --source-id <source> --out layouts/<source>.json' },
      { title: "Or do it visually, in your browser",
        body: '<a class="btn btn--sm btn--cyan" href="calibrate.html">Open the calibrator</a> and ' +
              'drop in a video or a few screenshots. The detection runs on your machine, in the ' +
              'tab — nothing is uploaded. Use frames from <b>live gameplay</b>, not a replay, ' +
              'killcam or hero-select screen, where the HUD is different or absent.' },
      { title: "Check the ten boxes it found",
        body: "If it cannot find ten it says so rather than placing boxes at a guess. That is " +
              "working as intended, and usually means the frames were not live play." },
      { title: "Build the hero reference images for this broadcast",
        body: "Every production draws the portraits slightly differently, so the tracker needs one " +
              "set of reference crops per broadcast before it can name heroes.",
        command: "python3 pipeline/build_hero_templates.py --layout layouts/<source>.json" },
      { title: "Prove it on a 30-second window",
        body: "Same short window as guide 01. Confirm ten clean portraits per frame before going " +
              "any further.",
        command: 'python3 pipeline/run_owcs_auto.py --url "<broadcast-url>" --start 0:06:00 --end 0:06:30 --every 10' },
    ], { id: "guide-calibrate" });

  /* -------------------------------------------------------- 03 review */
  const gates = $("#gd-review");
  if (gates) {
    const GATES = [
      ["Source approval",
       "A broadcast is not tracked until a person confirms this video is the game it claims to be. Wrong source, wrong everything.",
       "games.html", "See every game"],
      ["Layout approval",
       "The calibrated HUD layout has to be confirmed against real frames before any crop taken from it is trusted.",
       "calibrate.html", "Open the calibrator"],
      ["Segment approval",
       "The tracker proposes where each map starts and ends. A person confirms those boundaries — misplaced segments attribute compositions to the wrong map.",
       "review.html", "Open review"],
      ["Detection review",
       "Every read below the high-confidence threshold is queued for a person. UNKNOWN is a valid answer and stays UNKNOWN.",
       "review.html", "Open review"],
      ["Promote gate",
       "Nothing becomes a published composition without passing promote_detections.py, which refuses anything unreviewed. This gate is not optional and nothing in the interface can weaken it.",
       "stats.html", "See what is published"],
    ];
    gates.innerHTML =
      '<div style="margin-bottom:var(--s-4)">' + P.note("warn",
        "These gates are why “published” means something",
        "<p class='small' style='margin:6px 0 0'>They are human decisions on purpose. The tracker " +
        "can propose, sort and score — it can never publish on its own. If you find a way to make " +
        "it publish on its own, that is a bug worth reporting.</p>") + "</div>" +
      '<div class="grid grid--2">' + GATES.map((gt, i) =>
        '<div class="gate rv"><div class="gate__head">' +
          '<span class="gate__badge">Gate ' + (i + 1) + "</span>" +
          '<h3 class="gate__title">' + esc(gt[0]) + "</h3></div>" +
          '<p class="gate__why">' + esc(gt[1]) + "</p>" +
          '<div class="row"><a class="btn btn--sm" href="' + esc(gt[2]) + '">' + esc(gt[3]) + "</a></div>" +
        "</div>").join("") + "</div>" +
      '<div class="u-mt-5">' + P.guide(
        "How do I actually review a detection?",
        "Review happens against the crops the tracker cut out. You are not re-watching the " +
        "broadcast — you are answering one question per slot: is this portrait the hero the " +
        "machine said it was.",
        [
          { title: "Open the queue worst-confidence-first",
            body: '<a href="review.html">Review</a> orders items by how unsure the detector was, ' +
                  'because that is where your attention is worth the most.' },
          { title: "Compare the crop to the claim",
            body: "Each row shows the cropped portrait, the hero it was matched to, and the " +
                  "confidence. Accept it, correct it, or mark it unknown." },
          { title: "Correcting beats deleting",
            body: "A manual correction supersedes the machine read, but the original is kept and " +
                  "shown in the correction history. Nothing is quietly rewritten." },
          { title: "Leave genuinely unreadable slots UNKNOWN",
            body: "A portrait buried under a killfeed is not a hero pick. UNKNOWN keeps that " +
                  "composition out of published statistics, which is the correct outcome — an " +
                  "invented fifth hero is far worse than a missing map." },
        ], { id: "guide-review-how" }) + "</div>";
  }

  /* ------------------------------------------------------- 04 publish */
  g("#gd-publish", "Promote reviewed detections and publish the dataset",
    'Two steps, and the order matters. Promotion turns reviewed detections into compositions in ' +
    'the database. Export turns the database into the single file every page here reads. Running ' +
    'export without promoting first just republishes yesterday.',
    [
      { title: "Promote — dry run first",
        body: "Without <code>--write</code> this only reports what it <i>would</i> promote and " +
              "writes the review queue. Read that output before you write anything.",
        command: "python3 pipeline/promote_detections.py --run <run-id>" },
      { title: "Promote — write, with the pairing",
        body: "Writing requires you to say which match, which map and which team is on which " +
              "side. That is the gate: it will not guess a pairing, and it refuses any detection " +
              "that has not cleared review.",
        command: "python3 pipeline/promote_detections.py --run <run-id> --write --match <match-id> --map-order 1 --team-a <team-id> --team-b <team-id>",
        commandNote: "If it promotes zero rows, that is the gate doing its job — not a failure." },
      { title: "Export the public dataset",
        body: "Writes <code>assets/data/public_data.v1.js</code>, which is what every page on " +
              "this site reads.",
        command: "python3 pipeline/export_data.py" },
      { title: "Confirm the site is reading production, not a fixture",
        body: "Reload any page and look at the ticker under the header: it must say " +
              "<b>PRODUCTION</b>. If it says <b>DEMO FIXTURE</b> and a pink bar appears, the page " +
              "is still on demo data.",
        note: "It is deliberately impossible to browse demo data here without being told." },
      { title: "Re-run the tests, then commit",
        body: "The suite checks referential integrity, evidence paths and the demo/production split.",
        command: "python3 pipeline/test_site.py; python3 pipeline/test_meta_hub.py" },
    ], { id: "guide-publish" });

  /* -------------------------------------------------- 05 troubleshoot */
  const trouble = $("#gd-trouble");
  if (trouble) {
    /* Keyed to the same five states the rest of the product uses, so this
       section cannot describe a state the games list does not show. */
    const BLOCKS = [
      ["queued", "Accepted, waiting its turn. Nothing is wrong.",
       "If no local tracker is running, nothing will pick it up — start one, or run the command yourself.",
       "python3 pipeline/serve.py"],
      ["working", "Downloading or reading frames right now.",
       "Watch the game page. If it has not moved in a long time, the download is usually the cause.",
       null],
      ["review", "It finished reading and is waiting on a person.",
       "Open the queue and work the low-confidence rows first.",
       null],
      ["blocked", "It stopped on something a person has to fix — most often a missing layout or missing hero reference images.",
       "The game page names the specific cause and shows the exact command for it. The two most common are below.",
       "python3 pipeline/build_hero_templates.py --layout layouts/<source>.json"],
      ["published", "Approved and live on the public pages.",
       "Nothing to do. If the numbers look stale, re-run the export.",
       "python3 pipeline/export_data.py"],
    ];
    trouble.innerHTML = '<div class="grid grid--2">' + BLOCKS.map((b) =>
      '<div class="card rv" style="padding:var(--s-4);display:grid;gap:var(--s-3);align-content:start">' +
        '<div class="row">' + P.stateChip(b[0]) + "</div>" +
        '<p class="small" style="margin:0"><b>What it means.</b> ' + esc(b[1]) + "</p>" +
        '<p class="small" style="margin:0"><b>What to do.</b> ' + esc(b[2]) + "</p>" +
        (b[3] ? P.cmd(b[3])
              : '<p class="mono small dim" style="margin:0">No command — this one resolves on its own or in the review queue.</p>') +
      "</div>").join("") + "</div>" +
      '<div class="u-mt-4">' + P.note("info", "Still stuck?",
        "<p class='small' style='margin:6px 0 0'>Every game page prints the reason it stopped in " +
        "plain language, along with the command that addresses it. " +
        '<a href="games.html">Open the games list →</a></p>') + "</div>";
  }

  /* freshness echo — a guide page should still say how old the data is */
  const foot = $("#gd-foot");
  if (foot) {
    const gen = P.dataAge();
    foot.textContent = "These guides describe the pipeline in this checkout. " +
      (gen ? "Dataset built " + P.fmtDateTime(gen) + "." : "This build carries no dataset timestamp.");
  }

  /* A deep link should not land on a collapsed summary that looks empty. */
  function openTarget() {
    const id = (location.hash || "").slice(1);
    if (!id) return;
    const host = document.getElementById(id);
    if (!host) return;
    P.$$("details.guide", host).forEach((d) => { d.open = true; });
  }
  addEventListener("hashchange", openTarget);
  openTarget();

  P.observeReveals(document);
})();
