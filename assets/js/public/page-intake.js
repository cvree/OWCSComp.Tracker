/* Intake Review panel.

   Two modes, decided at load time by probing /api/ping:

   * STATIC HOSTING (GitHub Pages) — read-only by construction: renders the
     committed assets/data/intake.v1.json snapshot, every action is printed
     as a copyable CLI command, and the paste form only PREVIEWS the exact
     `convert-link` command (nothing can execute without a server).
   * LOCAL CONTROL ROOM (python pipeline/serve.py) — the paste form POSTs
     /api/intake/link, which launches `cli.py convert-link` locally (the
     same trust model as run.html starting a capture); the log is tailed
     live from /api/status and the panel re-renders from the LIVE
     /api/intake report when the job ends.

   In BOTH modes nothing here approves, edits, or publishes anything —
   source, layout and detection review stay human CLI commands, and the
   rendered data carries only public broadcast metadata (video ids, titles,
   channel ids, public URLs), internal match/team/player ids, and local
   evidence paths — no credentials, no API keys, no raw API responses. */
(function () {
  "use strict";

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const OK_STATES = new Set(["PUBLISHED", "APPROVED", "DOWNLOADED", "ARCHIVED"]);
  const WARN_STATES = new Set(["NEEDS_REVIEW", "NEEDS_LAYOUT", "NEEDS_TEMPLATES",
    "RETRY_SCHEDULED", "SEGMENTING", "PROCESSING", "DISCOVERED", "SCHEDULED"]);
  const BAD_STATES = new Set(["FAILED", "FAILED_PERMANENT", "CANCELLED", "IGNORED"]);

  function chip(text, kind) {
    return `<span class="ik-chip ${kind || ""}">${esc(text)}</span>`;
  }

  function stateChip(s) {
    const cls = OK_STATES.has(s) ? "ok" : BAD_STATES.has(s) ? "bad"
      : WARN_STATES.has(s) ? "warn" : "";
    return chip(s, cls);
  }

  function sourceChip(state) {
    const cls = state === "approved" ? "ok"
      : state === "rejected" ? "bad" : "warn";
    return chip(`source: ${state || "unknown"}`, cls);
  }

  const clock = (s) => {
    if (s == null) return "—";
    const t = Math.max(0, Math.round(s));
    const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60;
    return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) +
      `:${String(sec).padStart(2, "0")}`;
  };

  /* ---- segment timeline ------------------------------------------------
     Spans are positioned as percentages of the VOD duration when known, so
     an operator can see at a glance where in a multi-hour broadcast the
     proposed maps sit — and how much of it produced nothing. */
  function timeline(job) {
    const segs = job.segments || [];
    if (!segs.length) return "";
    const total = job.durationSeconds
      || Math.max(...segs.map((s) => s.end || 0), 1);
    const spans = segs.map((s) => {
      const left = Math.max(0, Math.min(100, ((s.start || 0) / total) * 100));
      const width = Math.max(0.6, Math.min(100 - left,
        (((s.end || 0) - (s.start || 0)) / total) * 100));
      const title = `#${s.id} ${clock(s.start)}–${clock(s.end)} ${s.reviewStatus}`;
      return `<span class="ik-span ${esc(s.reviewStatus)}" title="${esc(title)}"
        style="left:${left}%;width:${width}%"></span>`;
    }).join("");
    return `<div class="ik-timeline">${spans}</div>
      <div class="ik-scale"><span>0:00</span><span>${esc(clock(total))}</span></div>`;
  }

  function proposedField(key, field) {
    if (!field) return "";
    const val = field.value;
    const unknown = val === null || val === undefined || val === "";
    return `<div class="ik-field">
      <span class="k">${esc(key)}</span>
      <span class="v ${unknown ? "unknown" : ""}">${esc(unknown ? "UNKNOWN" : val)}</span>
      <span class="why">${esc(field.source || "")}${
        field.confidence != null ? ` · conf ${field.confidence}` : ""}<br>${
        esc(field.reason || "")}</span>
    </div>`;
  }

  function playerSlots(field) {
    const slots = (field && field.value) || [];
    if (!slots.length) return "";
    const cells = slots.map((s) => {
      const who = s.player || "UNKNOWN";
      const cls = s.player ? "" : "unknown";
      return `<div><b>${esc(s.side + s.index)}</b>
        <span class="v ${cls}">${esc(who)}</span>
        <span class="why">${esc(s.reason || "")}</span></div>`;
    }).join("");
    return `<div class="ik-field"><span class="k">players</span>
      <div class="ik-slots">${cells}</div></div>`;
  }

  function confirmedRow(c) {
    const any = c && (c.map || c.teamA || c.mapOrder);
    if (!any) return `<p class="ik-warn">No human-confirmed values yet — nothing
      from this segment can reach production until a person approves it.</p>`;
    return `<p class="ik-warn"><b>Confirmed by a human:</b>
      map ${esc(c.map || "—")} (${esc(c.mode || "—")}) ·
      order ${esc(c.mapOrder != null ? c.mapOrder : "—")} ·
      ${esc(c.teamA || "—")} vs ${esc(c.teamB || "—")} ·
      side ${esc(c.side || "—")} · layout ${esc(c.layoutId || "—")}
      ${c.note ? `<br>note: ${esc(c.note)}` : ""}</p>`;
  }

  function rejections(rej) {
    const keys = Object.keys(rej || {});
    if (!keys.length) return "";
    return `<p class="ik-rej"><b>Rejected samples inside this window:</b> ` +
      keys.map((k) => `${esc(k)} ×${rej[k]}`).join(" · ") + `</p>`;
  }

  function segmentBlock(seg) {
    const thumbs = (seg.thumbnails || []).map((t) =>
      `<figure><img src="${esc(t.path)}" alt="segment ${esc(seg.id)} frame at ${esc(clock(t.offset))}"
        loading="lazy" />
        <figcaption>t=${esc(clock(t.offset))} · score ${esc(t.score)}</figcaption></figure>`
    ).join("");
    const p = seg.proposed;
    const proposed = p ? `<div class="ik-grid">
        ${proposedField("map", p.map)}
        ${proposedField("mode", p.mode)}
        ${proposedField("team A", p.teamA)}
        ${proposedField("team B", p.teamB)}
        ${proposedField("side", p.sideAssignment)}
        ${proposedField("map order", p.mapOrder)}
      </div>${playerSlots(p.players)}`
      : `<p class="ik-warn">No identity proposal yet — run
         <code>propose-identity</code> to read map, teams and nameplates off
         the broadcast.</p>`;
    const tasks = (seg.reviewTasks || []).map((t) =>
      `<p class="${t.severity === "blocking" ? "ik-block" : "ik-warn"}">
        ${esc(t.severity.toUpperCase())} · ${esc(t.kind)}: ${esc(t.reason)}</p>`).join("");
    const actions = (seg.actions || []).map((a) =>
      `<div><span class="lbl">${esc(a.label)}</span>
        <span class="note">${esc(a.note || "")}</span>
        ${a.command
          ? `<code class="ik-cmd">python pipeline/automation/cli.py ${esc(a.command)}</code>`
          : ""}</div>`).join("");
    return `<div class="ik-seg">
      <div class="ik-seg-head">
        <h3>Segment #${esc(seg.id)} · ${esc(clock(seg.start))}–${esc(clock(seg.end))}
          (${esc(seg.durationSeconds)}s)</h3>
        ${chip(seg.reviewStatus, seg.reviewStatus === "approved" ? "ok"
          : /reject|invalid/.test(seg.reviewStatus) ? "bad" : "warn")}
        ${seg.identityStatus ? chip(`identity: ${seg.identityStatus}`,
          seg.identityStatus === "blocked" ? "bad" : "warn") : ""}
        ${seg.confidence != null ? chip(`conf ${seg.confidence}`) : ""}
        ${seg.gameplaySamples != null
          ? chip(`${seg.gameplaySamples} gameplay frames`) : ""}
        ${seg.method ? chip(seg.method) : ""}
      </div>
      ${thumbs ? `<div class="ik-thumbs">${thumbs}</div>` : ""}
      ${rejections(seg.rejections)}
      ${proposed}
      ${confirmedRow(seg.confirmed)}
      ${tasks}
      <div class="ik-actions">${actions}</div>
    </div>`;
  }

  function layoutBlock(l) {
    if (!l || (!l.layoutId && !l.decision)) {
      return `<p class="ik-warn">No layout resolved yet — run
        <code>resolve-layout</code> after the download completes.</p>`;
    }
    const rows = (l.candidates || []).slice(0, 6).map((c) =>
      `<tr><td>${esc(c.layoutId)}</td><td>${esc(c.score)}</td>
        <td>${esc(c.gameplayFrames)}</td></tr>`).join("");
    const cal = l.calibration || {};
    return `<p class="ik-warn"><b>Layout:</b> ${esc(l.layoutId || "unresolved")}
        ${l.source ? `(${esc(l.source)})` : ""} — ${esc(l.reason || l.decision || "")}</p>
      ${rows ? `<table class="ik-lay"><tr><th>candidate</th><th>score</th>
        <th>gameplay frames</th></tr>${rows}</table>` : ""}
      ${cal.confidence != null
        ? `<p class="ik-warn">Calibration confidence ${esc(cal.confidence)}
            (floor ${esc(cal.floor)})${cal.reviewSheet
              ? ` · <a href="${esc(cal.reviewSheet)}">review sheet</a>` : ""}</p>` : ""}
      ${cal.refusal ? `<p class="ik-block">REFUSED: ${esc(cal.refusal)}</p>` : ""}
      ${l.approvalRequired
        ? `<code class="ik-cmd">python pipeline/automation/cli.py approve-layout --job &lt;job&gt; --confirm</code>`
        : ""}`;
  }

  /* ---- per-job action buttons (control-room mode only) -----------------
     Every button maps to ONE allowlisted server action, which runs the
     same CLI command an operator would type. The audited approvals
     (source, layout, publish) require a typed name and a confirm — the
     page never approves anything on its own, and on static hosting these
     are not rendered at all. */
  function actionButtons(job) {
    if (!apiMode) return "";
    const j = esc(job.jobKey);
    const st = job.state;
    const src = job.sourceState;
    const b = (act, label, cls, extra) =>
      `<button data-act="${esc(act)}" data-job="${j}"${extra || ""} ${
        cls ? `class="${cls}"` : ""}>${esc(label)}</button>`;
    const out = [];
    if (src !== "approved" && src !== "rejected") {
      out.push(b("approve-source", "Approve source", "go", ' data-confirm="1"'));
      out.push(b("reject-source", "Reject source", "danger", ' data-confirm="1"'));
    }
    if (src === "approved") {
      out.push(b("media-probe", "Media probe"));
      if (st === "RETRY_SCHEDULED" || st === "FAILED" || st === "FAILED_PERMANENT") {
        out.push(b("retry", "Retry", "go"));
      }
      out.push(b("autopilot", "Autopilot", "go"));
      if (st === "NEEDS_LAYOUT") {
        out.push(b("approve-layout", "Approve layout", "go", ' data-confirm="1"'));
      }
      if (st === "DOWNLOADED" || st === "NEEDS_LAYOUT") {
        out.push(b("resolve-layout", "Resolve layout"));
      }
      if (st === "NEEDS_REVIEW") out.push(b("propose-identity", "Propose identity"));
      if (st === "READY_FOR_DETECTION" || st === "PROCESSING") {
        out.push(b("detect", "Detect (dry run)"));
      }
      if (st === "APPROVED") {
        out.push(b("detect-write", "Commit detection", "go", ' data-confirm="1"'));
        out.push(b("publish-dry", "Publish (dry run)"));
        out.push(b("publish", "PUBLISH", "danger", ' data-confirm="1"'));
      }
    }
    const harvest = `<label class="ik-form-opt" style="margin:6px 0 0">
      <input type="checkbox" class="ik-harvest" data-job="${j}" />
      <span>--for-harvest (download even though this layout has no hero
      templates yet — the explicit escape hatch for cutting them from this
      VOD)</span></label>`;
    return `<div class="ik-btns">${out.join("")}</div>${harvest}`;
  }

  function probeBlock(job) {
    const p = job.mediaProbe;
    if (!p) return "";
    if (p.ok) {
      return `<p class="ik-warn"><b>Media probe:</b> real bytes downloaded via
        rung <b>${esc(p.rung)}</b> (${esc(p.bytes)} bytes, ${esc(p.width)}×${esc(p.height)})
        ${p.qualityDowngrade ? " — <b>quality downgrade</b>" : ""}</p>`;
    }
    return `<p class="ik-block">Media probe FAILED [${esc(p.errorCode)}]:
      ${esc(p.errorMessage || "")}</p>`;
  }

  function attemptsBlock(job) {
    const attempts = job.downloadAttempts || [];
    if (!attempts.length) return "";
    const rows = attempts.map((a) => {
      const mark = a.ok ? "ok" : (a.note === "skipped" ? "skip" : "FAIL");
      const cls = a.ok ? "ok" : (a.note === "skipped" ? "" : "bad");
      return `<tr><td>${chip(mark, cls)}</td><td>${esc(a.rung)}</td>
        <td>${esc(a.errorCode || "")}</td>
        <td>${esc(a.skipReason || a.errorMessage || a.note || "")}</td></tr>`;
    }).join("");
    return `<p class="ik-warn"><b>Download fallback attempts</b> (sanitized)</p>
      <table class="ik-lay"><tr><th></th><th>rung</th><th>code</th>
      <th>detail</th></tr>${rows}</table>`;
  }

  function assetsBlock(job) {
    const a = job.detectionAssets;
    if (!a || !a.checked) return "";
    if (a.hardOk) {
      return `<p class="ik-warn"><b>Detection assets:</b> ready
        (${esc(a.layoutId)})</p>`;
    }
    return `<p class="ik-block"><b>Detection assets MISSING</b>
      (${esc(a.layoutId)}: ${esc((a.failed || []).join(", "))}) —
      ${esc(a.reason || "")}${a.forHarvest
        ? " <em>(downloaded anyway with --for-harvest)</em>" : ""}</p>`;
  }

  function jobBlock(job) {
    const blocking = (job.blocking || []).map((b) =>
      `<p class="ik-block">BLOCKED: ${esc(b)}</p>`).join("");
    const warnings = (job.warnings || []).map((w) =>
      `<p class="ik-warn">WARNING: ${esc(w)}</p>`).join("");
    const remedy = job.lastFailure && job.lastFailure.remedy
      ? `<p class="ik-remedy"><b>Remedy:</b> ${esc(job.lastFailure.remedy)}</p>`
      : "";
    const segs = (job.segments || []).map(segmentBlock).join("");
    return `<section class="ik-job">
      <h2>${esc(job.title || job.videoId || job.jobKey)}</h2>
      <div class="ik-meta">
        ${stateChip(job.state)} ${sourceChip(job.sourceState)}
        <span>job <b>${esc(job.jobKey)}</b></span>
        <span>video <b>${esc(job.videoId)}</b></span>
        ${job.channelTitle ? `<span>channel <b>${esc(job.channelTitle)}</b></span>` : ""}
        ${job.durationSeconds ? `<span>duration <b>${esc(clock(job.durationSeconds))}</b></span>` : ""}
        <span>pastes <b>${esc(job.pastes)}</b></span>
        ${job.downloaded ? chip("downloaded", "ok") : chip("not downloaded", "warn")}
        ${job.proxyPath ? chip("360p proxy", "ok") : chip("no scan proxy", "warn")}
      </div>
      ${job.canonicalUrl
        ? `<p class="ik-warn"><a href="${esc(job.canonicalUrl)}" rel="noopener noreferrer"
            target="_blank">${esc(job.canonicalUrl)}</a></p>` : ""}
      ${job.sourceReason ? `<p class="ik-warn">Source: ${esc(job.sourceReason)}${
        job.sourceDecidedBy ? ` — decided by ${esc(job.sourceDecidedBy)}` : ""}</p>` : ""}
      ${warnings}${blocking}${remedy}
      ${assetsBlock(job)}${probeBlock(job)}${attemptsBlock(job)}
      <p class="ik-warn"><b>Next command</b></p>
      <code class="ik-cmd">${esc(job.nextCommand || "—")}</code>
      ${actionButtons(job)}
      ${layoutBlock(job.layout)}
      ${timeline(job)}
      ${segs || `<p class="ik-warn">No segment candidates yet.</p>`}
    </section>`;
  }

  /* ---- download-authentication panel ---------------------------------- */
  function renderDownloadStatus(d) {
    const stat = document.getElementById("ik-dl-stat");
    const ladderEl = document.getElementById("ik-dl-ladder");
    const assetsEl = document.getElementById("ik-dl-assets");
    if (!stat) return;
    if (!d || d.error) {
      stat.innerHTML = `<div><span class="k">status</span>
        <span class="v bad">unavailable${d && d.error ? `: ${esc(d.error)}` : ""}</span></div>`;
      return;
    }
    const dep = (name) => (d.dependencies || []).find((e) => e.name === name) || {};
    const cell = (k, v, cls, why) => `<div><span class="k">${esc(k)}</span>
      <span class="v ${cls || ""}">${esc(v)}</span>
      ${why ? `<span class="why" style="color:var(--muted);font-size:.7rem">${esc(why)}</span>` : ""}</div>`;
    const yt = dep("yt-dlp"), match = dep("yt-dlp-install-match");
    const js = dep("js-runtime"), ejs = dep("yt-dlp-ejs"), cffi = dep("curl_cffi");
    const auth = d.auth || {};
    const keys = d.apiKeys || {};
    const probe = lastProbe;
    stat.innerHTML = [
      cell("yt-dlp", yt.version || "MISSING", yt.present ? "good" : "bad",
        yt.present ? yt.detail : yt.remedy),
      cell("install match", match.present ? "PATH = python" : "MISMATCH",
        match.present ? "good" : "warn", match.present ? "" : match.detail),
      cell("browser cookies",
        auth.cookiesConfigured ? `${auth.cookiesFromBrowser} (configured)`
          : "not configured",
        auth.cookiesConfigured ? "good" : "warn",
        auth.cookiesConfigured
          ? (auth.browserProfileConfigured ? "profile set (value never shown)"
            : "default profile")
          : "set OWCS_YTDLP_COOKIES_FROM_BROWSER=chrome|edge|firefox"),
      cell("JS runtime", js.version || "MISSING", js.present ? "good" : "bad",
        js.present ? js.detail : js.remedy),
      cell("yt-dlp-ejs", ejs.present ? ejs.version : "not installed",
        ejs.present ? "good" : "", ejs.present ? "" : ejs.remedy),
      cell("curl_cffi (impersonate)", cffi.present ? cffi.version : "not installed",
        cffi.present ? "good" : "", cffi.present ? "" : cffi.remedy),
      cell("force IPv4", auth.forceIpv4 ? "on" : "off", auth.forceIpv4 ? "good" : ""),
      cell("impersonate", auth.impersonate || "not set", auth.impersonate ? "good" : ""),
      cell("YOUTUBE_API_KEY (metadata)", keys.YOUTUBE_API_KEY ? "present" : "absent",
        keys.YOUTUBE_API_KEY ? "good" : "warn",
        keys.YOUTUBE_API_KEY ? "value never shown"
          : "without it a source cannot auto-approve; media download is unaffected"),
      cell("last probe",
        probe ? (probe.ok ? `OK via ${probe.rung}` : `FAILED ${probe.code || ""}`)
          : "not run this session",
        probe ? (probe.ok ? "good" : "bad") : "",
        probe && probe.detail ? probe.detail : ""),
      cell("current attempt", currentAttempt || "idle", currentAttempt ? "warn" : ""),
    ].join("");
    if (ladderEl) {
      ladderEl.innerHTML = (d.ladder || []).map((r, i) => {
        const cls = currentAttempt === r.rung ? "active" : (r.runnable ? "on" : "");
        return `<span class="ik-rung ${cls}" title="${esc(r.skipReason || r.why || "")}">${
          i + 1}. ${esc(r.rung)}${r.runnable ? "" : " (skipped)"}</span>`;
      }).join("");
    }
    if (assetsEl) {
      const ready = (d.detectionAssets || []).filter((a) => a.ok);
      const bad = (d.detectionAssets || []).filter((a) => !a.ok);
      assetsEl.innerHTML = `<p class="ik-warn" style="margin-top:12px">
        <b>Detection-ready layouts:</b> ${ready.length
          ? ready.map((a) => esc(a.layoutId)).join(", ")
          : "<span style='color:var(--loss,#e15b5b)'>NONE</span>"}</p>` +
        bad.map((a) => `<p class="ik-block">${esc(a.layoutId)}:
          ${esc((a.failed || []).join(", "))}</p>` +
          (a.checks || []).filter((c) => c.status === "fail" && c.remedy)
            .map((c) => `<code class="ik-cmd">${esc(c.remedy)}</code>`).join("")
        ).join("");
    }
  }

  /* ---- auto match finder ----------------------------------------------
     Renders /api/matchfinder (control room) or the committed
     assets/data/matchfinder.v1.json snapshot (static hosting): every
     broadcast discovered on the free sources (channel RSS + streams tab),
     its likeness verdict WITH reasons, and where it already is in the
     pipeline. "Ingest" feeds the exact same paste-link flow — no separate
     code path, no separate trust model. */
  function fmtDur(s) {
    if (s == null) return "duration ?";
    const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
    return h ? `${h}h${String(m).padStart(2, "0")}m` : `${m}m`;
  }

  function mfCandidate(c) {
    const lk = c.likeness || {};
    const likely = lk.confidence === "likely";
    const why = (lk.reasons || []).join("\n");
    const job = c.job;
    /* A static-hosting visitor has no server to ingest through — so the
       ONE thing that must always work here is a link to actually watch the
       broadcast. Ingest/command stays a secondary, smaller action next to
       it rather than the only thing offered. */
    const watch = `<a class="mf-watch" href="${esc(c.url)}" target="_blank"
      rel="noopener noreferrer">Watch ↗</a>`;
    let action;
    if (job) {
      action = `${watch} ${stateChip(job.state)}`;
    } else if (apiMode) {
      action = `<div class="ik-btns" style="margin:0">
        ${watch}<button class="go mf-ingest" data-url="${esc(c.url)}">Ingest</button></div>`;
    } else {
      action = `<div class="ik-btns" style="margin:0">
        ${watch}<button class="mf-cmd" data-url="${esc(c.url)}">Copy command</button></div>`;
    }
    return `<div class="mf-cand">
      <span class="ik-chip likeness-why ${likely ? "ok" : "warn"}"
        title="${esc(why)}">${likely ? "likely broadcast" : "unlikely"}</span>
      <div class="t">
        <b>${esc(c.title || c.videoId)}</b>
        <span>${esc((c.publishedAt || "").slice(0, 10) || "date ?")} ·
          ${esc(fmtDur(c.durationSeconds))} ·
          ${esc(c.channelTitle || "")} ·
          via ${esc((c.sources || []).join("+") || "?")}</span>
      </div>
      ${action}
    </div>`;
  }

  function renderMatchFinder(report) {
    const root = document.getElementById("mf-root");
    if (!root) return;
    const cands = (report && report.candidates) || [];
    const errs = (report && report.sourceErrors) || [];
    const s = (report && report.summary) || {};
    const head = report && report.generatedAt
      ? `<p class="ik-warn">last scan ${esc(report.generatedAt)} —
          <b>${esc(s.total || 0)}</b> found ·
          <b>${esc(s.likely || 0)}</b> likely broadcasts ·
          <b>${esc(s.tracked || 0)}</b> already in the pipeline</p>`
      : "";
    const errHtml = errs.map((e) => `<p class="mf-err">source error: ${esc(e)}</p>`).join("");
    if (!cands.length) {
      root.innerHTML = head + errHtml
        + `<p class="muted" style="font-size:.82rem">No broadcasts discovered yet`
        + (apiMode
          ? " — click “Scan for new matches”.</p>"
          : (" — this runs automatically every ~6 hours "
             + "(<a href=\"https://github.com/cvree/OWCSComp.Tracker/actions/workflows/match-finder.yml\" "
             + "target=\"_blank\" rel=\"noopener noreferrer\">see the schedule</a>). "
             + "Click “Check for updates”, or if you have the repo cloned:</p>"
             + `<code class="ik-cmd">python pipeline/automation/cli.py find-matches</code>`));
      return;
    }
    /* likely + untracked first, then likely tracked, then the rest */
    const rank = (c) => (c.likeness && c.likeness.confidence === "likely" ? 0 : 2)
      + (c.job ? 1 : 0);
    const rows = cands.slice().sort((a, b) => rank(a) - rank(b));
    /* The discovery feed is ~90 rows and grows with every scan. Rendered
       flat it made portal.html a 7,600px page whose last two sections
       (the pipeline explainer and the results links) were effectively
       unreachable. The list keeps every row and every link — it just
       scrolls in its own well instead of scrolling the document. */
    root.innerHTML = head + errHtml
      + `<div class="mf-well" data-scroll-region tabindex="0" role="group"
              aria-label="Discovered broadcasts (${rows.length})">`
      + rows.map(mfCandidate).join("")
      + `</div>`;
  }

  function loadMatchFinder() {
    const root = document.getElementById("mf-root");
    if (!root) return Promise.resolve();
    const src = apiMode ? "/api/matchfinder" : "assets/data/matchfinder.v1.json";
    return fetch(src, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(renderMatchFinder)
      .catch(() => renderMatchFinder(null));
  }

  function mfSetNote(text, cls) {
    const n = document.getElementById("mf-note");
    if (n) { n.textContent = text; n.className = `ik-form-note ${cls || ""}`; }
  }

  document.addEventListener("click", (ev) => {
    const scan = ev.target.closest && ev.target.closest("#mf-scan");
    if (scan) {
      ev.preventDefault();
      if (!apiMode) {
        /* Static hosting has no server to run a live scan on — but the
           GitHub Actions workflow (match-finder.yml) already refreshes the
           committed snapshot every ~6 hours, so the useful, honest action
           here is to re-fetch it rather than dead-end on a CLI command. */
        mfSetNote("checking for a newer automated scan…");
        loadMatchFinder().then(() => {
          mfSetNote("showing the latest automated scan (runs every ~6h via "
            + "GitHub Actions — nothing to run yourself).", "ok");
        });
        return;
      }
      setBusy(true);
      mfSetNote("scanning verified channels (RSS + streams tab)…");
      fetch("/api/action", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "find-matches" }),
      })
        .then((r) => r.json().then((j) => ({ status: r.status, j })))
        .then(({ status, j }) => {
          if (status === 200 && j.started) { tailJob(0, "match scan"); }
          else { mfSetNote(j.error || `refused (HTTP ${status})`, "bad"); setBusy(false); }
        })
        .catch((err) => { mfSetNote(`control room unreachable: ${err.message}`, "bad"); setBusy(false); });
      return;
    }
    const ingest = ev.target.closest && ev.target.closest(".mf-ingest, .mf-cmd");
    if (ingest) {
      ev.preventDefault();
      const url = ingest.getAttribute("data-url");
      if (urlBox) {
        urlBox.value = url;
        urlBox.scrollIntoView({ behavior: "smooth", block: "center" });
      }
      if (ingest.classList.contains("mf-ingest")) {
        submitLink(new Event("submit"));
      } else {
        previewCommand();
      }
    }
  });

  let lastProbe = null;
  let currentAttempt = null;

  function loadDownloadStatus() {
    if (!apiMode) {
      const stat = document.getElementById("ik-dl-stat");
      if (stat) {
        stat.innerHTML = `<div><span class="k">status</span>
          <span class="v warn">static hosting — run
          <code>python pipeline/serve.py</code> to see live download status</span></div>`;
      }
      return Promise.resolve();
    }
    return fetch("/api/download-status", { cache: "no-store" })
      .then((r) => r.json()).then(renderDownloadStatus)
      .catch((e) => renderDownloadStatus({ error: e.message }));
  }

  function render(data) {
    const root = document.getElementById("ik-root");
    const summary = document.getElementById("ik-summary");
    const jobs = (data && data.jobs) || [];
    if (summary) {
      const segTotal = jobs.reduce((n, j) => n + (j.segments || []).length, 0);
      const approved = jobs.reduce((n, j) => n +
        (j.segments || []).filter((s) => s.reviewStatus === "approved").length, 0);
      const blocked = jobs.filter((j) => (j.blocking || []).length).length;
      summary.innerHTML = `
        <span>jobs <b>${jobs.length}</b></span>
        <span>segments <b>${segTotal}</b></span>
        <span>approved segments <b>${approved}</b></span>
        <span>jobs blocked <b>${blocked}</b></span>
        <span>generated <b>${esc(data.generatedAt || "—")}</b></span>`;
    }
    if (!jobs.length) {
      root.innerHTML = `<p class="muted">No intake jobs yet. Paste a broadcast
        link:</p><code class="ik-cmd">python pipeline/automation/cli.py ingest-link --url "&lt;youtube-url&gt;"</code>
        <p class="muted">Then regenerate this page:</p>
        <code class="ik-cmd">python pipeline/automation/cli.py intake-export --save</code>`;
      return;
    }
    root.innerHTML = jobs.map(jobBlock).join("");
  }

  /* ---- data loading: live control-room report, else static snapshot ---- */
  let apiMode = false;

  function loadStatic() {
    return fetch("assets/data/intake.v1.json", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(render)
      .catch((err) => {
        /* A missing snapshot on static hosting is the normal state, not a
           failure — say so, and keep the real error for anything else. */
        const missing = /HTTP 404/.test(err.message);
        document.getElementById("ik-root").innerHTML = missing
          ? `<p class="muted">No broadcast has been put through the pipeline on this deployment,
             so there is nothing to review here. Jobs appear once you run the intake locally:</p>
             <code class="ik-cmd">python pipeline/automation/cli.py convert-link --url "&lt;youtube-url&gt;"</code>
             <code class="ik-cmd">python pipeline/automation/cli.py intake-export --save</code>
             <p class="muted">Published results are on the
             <a href="index.html">public site</a>.</p>`
          : `<p class="muted">Could not read the intake snapshot (${esc(err.message)}).
             Regenerate it with:</p>
             <code class="ik-cmd">python pipeline/automation/cli.py intake-export --save</code>`;
      });
  }

  function loadLive() {
    return fetch("/api/intake", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data) => {
        if (data && data.error) throw new Error(data.error);
        render(data);
      })
      .catch(loadStatic);
  }

  const reload = () => (apiMode ? loadLive() : loadStatic());

  /* ---- the paste form ------------------------------------------------- */
  const form = document.getElementById("ik-form");
  const urlBox = document.getElementById("ik-url");
  const goBtn = document.getElementById("ik-go");
  const autoBox = document.getElementById("ik-autoaccept");
  const note = document.getElementById("ik-note");
  const cmdPrev = document.getElementById("ik-cmd-preview");
  const liveLog = document.getElementById("ik-live-log");

  function setNote(text, cls) {
    if (!note) return;
    note.textContent = text;
    note.className = `ik-form-note ${cls || ""}`;
  }

  function previewCommand() {
    if (!cmdPrev) return;
    const url = (urlBox && urlBox.value || "").trim();
    if (apiMode || !url) { cmdPrev.hidden = true; return; }
    cmdPrev.hidden = false;
    cmdPrev.textContent =
      `python pipeline/automation/cli.py convert-link --url "${url}"` +
      (autoBox && autoBox.checked ? " --auto-accept --accepted-by \"<your name>\"" : "");
  }

  /* Which ladder rung a running job is on right now, read from the
     sanitized log lines the downloader already prints. Nothing secret can
     appear here — the server redacts every line before it is streamed. */
  const RUNGS = ["normal", "refresh-signed-url", "force-ipv4",
    "browser-cookies", "browser-cookies+impersonate", "alternate-format"];

  function noteAttempt(line) {
    for (const r of RUNGS) {
      if (line.indexOf(r) !== -1 && /\[\d+\/\d+\]/.test(line)) {
        currentAttempt = r;
        return;
      }
    }
    if (/SUCCEEDED|job finished/.test(line)) currentAttempt = null;
    const probeOk = line.match(/probe: OK via rung (\S+)/);
    if (probeOk) lastProbe = { ok: true, rung: probeOk[1], detail: line.slice(0, 120) };
    const probeBad = line.match(/probe: FAILED \[([^\]]+)\]/) ||
      line.match(/probe.*\[(youtube_[a-z_]+)\]/);
    if (probeBad) lastProbe = { ok: false, code: probeBad[1], detail: line.slice(0, 120) };
  }

  function tailJob(sinceStart, what) {
    /* Follow the running job via /api/status until it ends, then re-render
       the panel + the download-auth status from the live report. */
    let since = sinceStart || 0;
    const label = what || "convert";
    if (liveLog) { liveLog.hidden = false; liveLog.textContent = ""; }
    const tick = () => fetch(`/api/status?since=${since}`, { cache: "no-store" })
      .then((r) => r.json())
      .then((st) => {
        since = st.next;
        if (st.lines && st.lines.length) {
          st.lines.forEach(noteAttempt);
          if (liveLog) {
            liveLog.textContent += st.lines.join("\n") + "\n";
            liveLog.scrollTop = liveLog.scrollHeight;
          }
          loadDownloadStatus();
        }
        if (st.running) { setTimeout(tick, 800); return; }
        currentAttempt = null;
        const ok = st.status === "ok";
        setNote(ok
          ? `${label} finished — review the updated stages below.`
          : `${label} ended: ${st.status} — the log above says why; the job is resumable.`,
          ok ? "ok" : "bad");
        setBusy(false);
        reload();
        loadDownloadStatus();
        loadMatchFinder();
      })
      .catch(() => { setTimeout(tick, 2000); });
    tick();
  }

  function setBusy(busy) {
    if (goBtn) goBtn.disabled = busy;
    document.querySelectorAll(".ik-btns button")
      .forEach((b) => { b.disabled = busy; });
  }

  /* One delegated handler for every per-job action button. */
  function runAction(btn) {
    const action = btn.getAttribute("data-act");
    const job = btn.getAttribute("data-job");
    const who = (document.getElementById("ik-who") || {}).value || "";
    const needsName = ["approve-source", "reject-source", "approve-layout",
      "publish"].indexOf(action) !== -1;
    if (needsName && !who.trim()) {
      setNote("type your name first — an approval with no accountable human "
        + "is not an approval.", "bad");
      return;
    }
    if (btn.getAttribute("data-confirm")) {
      const what = btn.textContent.trim();
      if (!window.confirm(`${what} for ${job}?\n\nThis is an audited human `
        + `decision and is recorded with your name.`)) return;
    }
    const harvestBox = document.querySelector(`.ik-harvest[data-job="${job}"]`);
    setBusy(true);
    setNote(`running ${action}…`);
    fetch("/api/action", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action, job, who: who.trim(),
        autoAccept: !!(autoBox && autoBox.checked),
        forHarvest: !!(harvestBox && harvestBox.checked),
      }),
    })
      .then((r) => r.json().then((j) => ({ status: r.status, j })))
      .then(({ status, j }) => {
        if (status === 200 && j.started) { tailJob(0, action); }
        else { setNote(j.error || `refused (HTTP ${status})`, "bad"); setBusy(false); }
      })
      .catch((err) => { setNote(`control room unreachable: ${err.message}`, "bad"); setBusy(false); });
  }

  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest ? ev.target.closest(".ik-btns button") : null;
    if (btn && btn.getAttribute("data-act")) { ev.preventDefault(); runAction(btn); }
  });

  function submitLink(ev) {
    ev.preventDefault();
    const url = (urlBox && urlBox.value || "").trim();
    if (!url) return;
    if (!apiMode) { previewCommand(); return; }
    if (goBtn) goBtn.disabled = true;
    setNote("submitting link…");
    fetch("/api/intake/link", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, autoAccept: !!(autoBox && autoBox.checked) }),
    })
      .then((r) => r.json().then((j) => ({ status: r.status, j })))
      .then(({ status, j }) => {
        if (status === 200 && j.started) {
          setNote(`converting ${j.videoId} (job ${j.jobKey}) — live log below.`);
          tailJob(0, "convert");
        } else {
          setNote(j.error || `refused (HTTP ${status})`, "bad");
          setBusy(false);
        }
      })
      .catch((err) => {
        setNote(`control room unreachable: ${err.message}`, "bad");
        setBusy(false);
      });
  }

  if (form) {
    form.addEventListener("submit", submitLink);
    if (urlBox) urlBox.addEventListener("input", previewCommand);
    if (autoBox) autoBox.addEventListener("change", previewCommand);
  }

  /* ---- boot: probe the control room, then load ------------------------- */
  fetch("/api/ping", { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error("no api"))))
    .then((ping) => {
      apiMode = !!(ping && ping.ok);
      if (apiMode) {
        if (goBtn) goBtn.disabled = !!ping.running;
        setNote(ping.running
          ? "control room detected — a job is already running (one at a time); wait or cancel it on the Run page."
          : "control room detected — pasting a link will run convert-link locally, to the first human gate.",
          "ok");
      }
    })
    .catch(() => {
      apiMode = false;
      setNote("static hosting — no server here, so this form only builds the exact "
        + "command to copy into your own terminal.");
      if (goBtn) { goBtn.disabled = false; goBtn.textContent = "Show command"; }
      /* No server here to launch a live scan either — but GitHub Actions
         already refreshes the committed snapshot on a schedule, so relabel
         honestly rather than leaving a button that implies it'll scan now. */
      const scanBtn = document.getElementById("mf-scan");
      if (scanBtn) scanBtn.textContent = "Check for updates";
    })
    .then(reload)
    .then(loadDownloadStatus)
    .then(loadMatchFinder);
})();
