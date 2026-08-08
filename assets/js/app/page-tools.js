/* =====================================================================
   OWCS Comp Tracker — app/page-tools.js
   The advanced surface. Five pages became seven sections of one page:
   sources, runs, calibration, downloads, storage, publishing, evidence.

   Two rules keep it from turning back into a control room:
     · nothing here is required to use the product;
     · every panel says what it can and cannot do from where you are
       standing, rather than showing a dead button.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const W = P.work;
  const esc = P.esc;
  const set = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };

  document.addEventListener("DOMContentLoaded", () => {
    P.api.mountStatus("mode-status", {
      readonlyTitle: "Read-only diagnostics",
      readonlyBody: "<p>This page is showing the last state that was exported into the site. " +
        "The live panels — download authentication, storage, publishing — need a tracker " +
        "running behind the page, so they say so instead of showing stale numbers as if " +
        "they were current.</p>",
    });

    renderSources();
    renderRuns();
    renderCalibration();
    renderEvidence();
    renderLivePanels();

    document.addEventListener("click", (e) => {
      const c = e.target.closest("[data-copy]");
      if (c) P.copy(c.dataset.copy, "Copied");
    });
  });

  /* ---------------------------------------------------------- sources */
  function renderSources() {
    const sources = (W && W.videoSources) || [];
    if (!sources.length) {
      set("sources-body", P.empty("◇", "No broadcast sources registered",
        "A source is a broadcast the tracker has been taught to read. Submitting a link " +
        "creates one automatically."));
      return;
    }
    set("sources-body",
      '<p class="dim small" style="margin-bottom:var(--s-4);max-width:74ch">A source pairs a ' +
      "broadcast with the HUD layout that tells the detector where the hero portraits are. " +
      "One layout per production, calibrated once.</p>" +
      '<div class="table-wrap"><table class="tbl"><thead><tr><th>Source</th><th>Platform</th>' +
      "<th>Layout</th><th>Enabled</th><th>Run it</th></tr></thead><tbody>" +
      sources.map((s) => {
        const cmd = "python3 pipeline/run_owcs_auto.py --source " + s.id;
        return "<tr><td><b>" + esc(s.title || s.id) + "</b>" +
          (s.url ? '<div class="dim small"><a href="' + esc(s.url) +
            '" target="_blank" rel="noopener">' + esc(s.url) + "</a></div>" : "") +
          '<div class="dim small mono">' + esc(s.id) + "</div></td>" +
          "<td>" + esc(s.platform || "—") + "</td>" +
          '<td class="mono small">' + esc(s.layout || "none") + "</td>" +
          '<td>' + (s.enabled ? '<span class="chip" data-state="evidence"><span class="dot">' +
            "</span>yes</span>" : '<span class="chip" data-state="queued">no</span>') + "</td>" +
          '<td><button class="btn btn--sm btn--quiet" data-copy="' + esc(cmd) +
          '">Copy command</button></td></tr>';
      }).join("") + "</tbody></table></div>");
  }

  /* ------------------------------------------------------------- runs */
  function renderRuns() {
    const runs = (W && W.autoRuns) || [];
    if (!runs.length) {
      set("runs-body", P.empty("◇", "No processing runs recorded",
        "Every submitted game leaves a run here with its full step table."));
      return;
    }
    const badge = (s) => {
      const kind = s === "ok" ? "evidence" : s === "partial" ? "review" : "blocked";
      const label = s === "ok" ? "completed" : s === "partial" ? "finished with gaps" : (s || "failed");
      return '<span class="chip" data-state="' + kind + '"><span class="dot"></span>' +
        esc(label) + "</span>";
    };
    set("runs-body",
      '<div class="table-wrap"><table class="tbl"><thead><tr><th>Run</th><th>Outcome</th>' +
      "<th>Window</th><th>Frames kept</th><th>Started</th><th>Report</th></tr></thead><tbody>" +
      runs.slice(0, 30).map((r) => "<tr>" +
        '<td class="mono small">' + esc(r.run) + '<div class="dim">' + esc(r.source || "") +
        "</div></td>" +
        "<td>" + badge(r.runStatus) + "</td>" +
        '<td class="mono small">' + esc(r.window || "—") + "</td>" +
        '<td class="num">' + (r.framesKept != null ? r.framesKept : "—") +
        (r.framesRaw ? '<span class="dim"> / ' + r.framesRaw + "</span>" : "") + "</td>" +
        '<td class="dim small u-nowrap">' + esc(P.fmtRel(r.startedAt)) + "</td>" +
        "<td>" + (r.reportDir
          ? '<a href="' + esc(r.reportDir) + '">open</a>' : '<span class="dim">—</span>') +
        "</td></tr>").join("") +
      "</tbody><caption>The newest 30 runs. Each report directory holds the annotated frames, " +
      "crops and layout debug images for that run.</caption></table></div>");
  }

  /* ------------------------------------------------------ calibration */
  function renderCalibration() {
    const sources = (W && W.videoSources) || [];
    const withLayout = sources.filter((s) => s.layout);
    const runs = (W && W.autoRuns) || [];
    const missingTemplates = runs.filter((r) =>
      r.detection && /no hero templates/i.test(String(r.detection.reason || "")));

    set("calibration-body",
      '<p class="dim small" style="max-width:74ch;margin-bottom:var(--s-4)">Every production ' +
      "draws the hero portraits in a slightly different place, so each broadcast is " +
      "calibrated once. The wizard runs entirely in your browser — nothing is uploaded and " +
      "nothing is installed — and a calibration for a broadcast nobody has covered is the " +
      "single most useful thing anyone can contribute.</p>" +
      '<div class="grid grid--3">' +
      '<div class="stat"><span class="stat__k">Calibrated broadcasts</span>' +
      '<span class="stat__v">' + withLayout.length + "</span>" +
      '<span class="stat__note">of ' + sources.length + " registered source(s)</span></div>" +
      '<div class="stat" data-accent="' + (missingTemplates.length ? "amber" : "emerald") + '">' +
      '<span class="stat__k">Missing hero references</span>' +
      '<span class="stat__v">' + missingTemplates.length + "</span>" +
      '<span class="stat__note">run(s) that could not name heroes</span></div>' +
      '<a class="stat" href="calibrate.html"><span class="stat__k">Calibration wizard</span>' +
      '<span class="stat__v" style="font-size:1.3rem">Open →</span>' +
      '<span class="stat__note">teach the tracker a new broadcast</span></a>' +
      "</div>" +
      /* The command a new broadcast actually needs. It samples the
         broadcast about sixty seconds apart over a range request, keeps
         only the frames the gameplay filter accepts, and stops as soon as
         the calibration is confident — so it costs a few megabytes rather
         than the whole VOD. */
      '<div class="u-mt-5">' + P.note("info",
        "Calibrating a new broadcast does not need the video",
        "<p>Point this at any broadcast URL. It samples it remotely, throws away the " +
        "desk and replay frames, and calibrates from what is left — then tells you " +
        "which windows contain live play, so the deep pass only downloads one of " +
        "those instead of the whole stream.</p>" +
        '<pre class="console u-mt-3" style="max-height:none">' +
        esc('python3 pipeline/calibrate_remote.py --url "<broadcast-url>" \\\n' +
            "  --source-id <source> --out layouts/<source>.json \\\n" +
            "  --windows-out work/<source>.windows.json") + "</pre>",
        '<button class="btn btn--sm" data-copy="' +
        esc('python3 pipeline/calibrate_remote.py --url "<broadcast-url>" ' +
            "--source-id <source> --out layouts/<source>.json " +
            "--windows-out work/<source>.windows.json") +
        '">Copy the command</button>' +
        '<a class="btn btn--sm btn--ghost" href="calibrate.html">Use the browser wizard instead</a>'
      ) + "</div>" +
      (missingTemplates.length
        ? '<div class="u-mt-5">' + P.note("warn", "Some broadcasts have no hero reference images",
          "<p>Detection cannot name a hero it has never seen a reference crop of. Build them " +
          "once per layout:</p>" +
          '<pre class="console u-mt-3" style="max-height:none">' +
          esc("python3 pipeline/build_hero_templates.py --layout " +
            (missingTemplates[0].layout || "<layout>")) + "</pre>",
          '<button class="btn btn--sm" data-copy="' +
          esc("python3 pipeline/build_hero_templates.py --layout " +
            (missingTemplates[0].layout || "<layout>")) + '">Copy the command</button>') + "</div>"
        : ""));
  }

  /* --------------------------------------------------------- evidence */
  function renderEvidence() {
    const comps = P.publishedComps();
    const stints = (P.pub && P.pub.heroStints) || [];
    const withEvidence = stints.filter((s) => s.evidenceStart).length;
    set("evidence-body",
      '<p class="dim small" style="max-width:74ch;margin-bottom:var(--s-4)">Every published ' +
      "fact points at the image it was read from. These files are committed alongside the " +
      "data, which is what makes a claim on this site checkable rather than merely stated.</p>" +
      '<div class="grid grid--3">' +
      '<div class="stat"><span class="stat__k">Approved line-ups</span><span class="stat__v">' +
      comps.length + '</span><span class="stat__note">with an evidence frame each</span></div>' +
      '<div class="stat"><span class="stat__k">Detected slots</span><span class="stat__v">' +
      stints.length + '</span><span class="stat__note">' + withEvidence +
      " carry a saved crop</span></div>" +
      '<a class="stat" href="review.html"><span class="stat__k">Inspect them</span>' +
      '<span class="stat__v" style="font-size:1.3rem">Review →</span>' +
      '<span class="stat__note">every prediction beside its frame</span></a></div>' +
      P.diag("Where the files live", P.dl([
        ["Evidence crops", "reports/ingest/<run>/evidence/"],
        ["Sampled frames", "reports/ingest/<run>/frames/"],
        ["Run reports", "reports/auto/<run>/"],
        ["Corrections", "corrections/corrections.json"],
        ["Published dataset", "assets/data/public_data.v1.js"],
      ])));
  }

  /* ------------------------------------------------------ live panels */
  function renderLivePanels() {
    const offline = (title) => P.note("info", title,
      "<p>This needs a tracker running behind the page. On the published copy there is " +
      "nothing to ask.</p>",
      '<a class="btn btn--sm btn--ghost" href="how-it-works.html#run-it-yourself">' +
      "Run your own copy</a>");

    P.api.probe().then(() => {
      if (!P.api.isConnected()) {
        set("downloads-body", offline("Download authentication is not readable from here"));
        set("storage-body", offline("Storage and health are not readable from here"));
        set("publish-body", offline("Publishing is not available from here"));
        return;
      }
      loadDownloads();
      loadStorage();
      loadPublish();
    });
  }

  function loadDownloads() {
    set("downloads-body", '<div class="row"><span class="spinner"></span>' +
      '<span class="dim">reading…</span></div>');
    P.api.get("/api/download-status").then((d) => {
      if (!d || d.ok === false && d.offline) {
        set("downloads-body", P.note("error", "Could not read the download status",
          "<p>" + esc((d && d.error) || "no reason given") + "</p>"));
        return;
      }
      const deps = d.dependencies || [];
      set("downloads-body",
        '<div class="grid grid--2">' +
        '<div class="card"><p class="label">Required programs</p>' +
        '<div class="stack u-mt-3">' + deps.map((x) => {
          const ok = x.present === true;
          return '<div><div class="u-flex u-between u-center u-gap-3"><span>' +
            esc(x.name || x.id || "?") +
            (x.required === false ? ' <span class="dim small">optional</span>' : "") +
            '</span><span class="chip" data-state="' + (ok ? "evidence" : "blocked") +
            '"><span class="dot"></span>' + esc(ok ? (x.version || "present") : "missing") +
            "</span></div>" +
            (!ok && x.remedy
              ? '<p class="dim small u-mt-3">Install it: <code>' + esc(x.remedy) + "</code></p>"
              : "") + "</div>";
        }).join("") + "</div></div>" +
        '<div class="card"><p class="label">Signed-in session</p>' +
        '<p class="dim small u-mt-3">Some broadcasts will not hand over a stream without a ' +
        "signed-in browser session. Only whether one is configured is ever reported — never " +
        "its value.</p>" + P.dl(Object.keys(d.auth || {}).map((k) =>
          [k, typeof d.auth[k] === "boolean" ? (d.auth[k] ? "configured" : "not configured")
            : String(d.auth[k])])) + "</div></div>");
    });
  }

  function loadStorage() {
    set("storage-body", '<div class="row"><span class="spinner"></span>' +
      '<span class="dim">reading…</span></div>');
    P.api.overview().then((o) => {
      if (!o || o.ok === false) {
        set("storage-body", P.note("error", "Could not read the health snapshot",
          "<p>" + esc((o && o.error) || "no reason given") + "</p>"));
        return;
      }
      const s = o.storage || {};
      const blocking = (o.health && o.health.blocking) || [];
      set("storage-body",
        '<div class="grid grid--3">' +
        '<div class="stat"><span class="stat__k">Used</span><span class="stat__v">' +
        esc(s.totalGb != null ? s.totalGb + " GB" : "—") + '</span>' +
        '<span class="stat__note">budget ' + esc(s.budgetGb != null ? s.budgetGb + " GB" : "—") +
        "</span></div>" +
        '<div class="stat"><span class="stat__k">Free on disk</span><span class="stat__v">' +
        esc(s.freeGb != null ? s.freeGb + " GB" : "—") + "</span></div>" +
        '<div class="stat" data-accent="' + (blocking.length ? "red" : "emerald") + '">' +
        '<span class="stat__k">Blocking problems</span><span class="stat__v">' +
        blocking.length + "</span></div></div>" +
        (blocking.length
          ? '<div class="stack u-mt-4" id="blocking-detail"><div class="row">' +
            '<span class="spinner"></span><span class="dim">reading the details…</span>' +
            "</div></div>"
          : ""));
      /* `overview` reports blocking checks as bare ids ("bin.ffmpeg"), which
         is useless on its own. The full health route carries the detail and
         the remedy, so a blocker is shown as something a person can fix. */
      if (blocking.length) loadBlockingDetail(blocking);
    });
  }

  function loadBlockingDetail(blocking) {
    P.api.health().then((h) => {
      const host = document.getElementById("blocking-detail");
      if (!host) return;
      const byId = {};
      ((h && h.checks) || []).forEach((c) => { byId[c.id || c.name] = c; });
      host.innerHTML = blocking.map((id) => {
        const c = byId[id] || {};
        return P.note("error", c.label || c.name || String(id),
          "<p>" + esc(c.detail || "This check failed and reported no detail.") + "</p>" +
          (c.remedy ? '<p class="dim small u-mt-3">Fix: ' + esc(c.remedy) + "</p>" : ""));
      }).join("");
    });
  }

  function loadPublish() {
    set("publish-body", '<div class="row"><span class="spinner"></span>' +
      '<span class="dim">reading…</span></div>');
    P.api.publishStatus().then((p) => {
      if (!p || p.ok === false) {
        set("publish-body", P.note("error", "Could not read the publishing status",
          "<p>" + esc((p && p.error) || "no reason given") + "</p>"));
        return;
      }
      set("publish-body",
        '<div class="card">' + P.dl([
          ["Export file", p.path],
          ["Present", p.exists ? "yes" : "no"],
          ["Size", p.bytes != null ? p.bytes + " bytes" : null],
          ["Modified", p.modified],
          ["Dataset", p.demo === true ? "DEMO" : p.demo === false ? "production" : "unknown"],
          ["Generated by", p.generatedBy],
        ]) +
        '<p class="dim small u-mt-4" style="max-width:74ch">Regenerating takes a backup first, ' +
        "writes atomically, and verifies the result parses before leaving it in place. It " +
        "only ever includes compositions a person has approved.</p>" +
        '<div class="row u-mt-4"><button class="btn btn--sm btn--primary" id="do-export">' +
        "Regenerate the published dataset</button></div>" +
        '<div id="export-result" class="u-mt-4"></div></div>');
      const btn = document.getElementById("do-export");
      if (btn) btn.addEventListener("click", async () => {
        btn.setAttribute("aria-disabled", "true");
        set("export-result", '<div class="row"><span class="spinner"></span>' +
          '<span class="dim">exporting…</span></div>');
        const r = await P.api.exportPublic();
        btn.removeAttribute("aria-disabled");
        set("export-result", r && r.ok
          ? P.note("ok", "Published dataset regenerated",
            "<p>" + esc(r.detail || "") + "</p><p class=\"dim small\">Backup " +
            esc(r.backup || "—") + " was taken first.</p>")
          : P.note("error", "The export did not complete",
            "<p>" + esc((r && r.error) || "no reason given") + "</p>"));
      });
    });
  }
})();
