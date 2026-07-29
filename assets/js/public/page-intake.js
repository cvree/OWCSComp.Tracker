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

  function jobBlock(job) {
    const blocking = (job.blocking || []).map((b) =>
      `<p class="ik-block">BLOCKED: ${esc(b)}</p>`).join("");
    const warnings = (job.warnings || []).map((w) =>
      `<p class="ik-warn">WARNING: ${esc(w)}</p>`).join("");
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
      ${warnings}${blocking}
      <p class="ik-warn"><b>Next command</b></p>
      <code class="ik-cmd">${esc(job.nextCommand || "—")}</code>
      ${layoutBlock(job.layout)}
      ${timeline(job)}
      ${segs || `<p class="ik-warn">No segment candidates yet.</p>`}
    </section>`;
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
        document.getElementById("ik-root").innerHTML =
          `<p class="muted">No intake snapshot available (${esc(err.message)}).
           Generate one with:</p>
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

  function tailJob(sinceStart) {
    /* Follow the running convert via /api/status until it ends, then
       re-render the panel from the live report. */
    let since = sinceStart || 0;
    if (liveLog) { liveLog.hidden = false; liveLog.textContent = ""; }
    const tick = () => fetch(`/api/status?since=${since}`, { cache: "no-store" })
      .then((r) => r.json())
      .then((st) => {
        since = st.next;
        if (liveLog && st.lines && st.lines.length) {
          liveLog.textContent += st.lines.join("\n") + "\n";
          liveLog.scrollTop = liveLog.scrollHeight;
        }
        if (st.running) { setTimeout(tick, 800); return; }
        const ok = st.status === "ok";
        setNote(ok
          ? "convert finished — review the updated stages below."
          : `convert ended: ${st.status} — the log above says why; the job is resumable.`,
          ok ? "ok" : "bad");
        if (goBtn) goBtn.disabled = false;
        reload();
      })
      .catch(() => { setTimeout(tick, 2000); });
    tick();
  }

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
          tailJob(0);
        } else {
          setNote(j.error || `refused (HTTP ${status})`, "bad");
          if (goBtn) goBtn.disabled = false;
        }
      })
      .catch((err) => {
        setNote(`control room unreachable: ${err.message}`, "bad");
        if (goBtn) goBtn.disabled = false;
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
    })
    .then(reload);
})();
