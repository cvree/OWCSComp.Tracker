/* =====================================================================
   OWCS Comp Tracker — app/page-sheet-sync.js
   Source health: the spreadsheet import, for the people who maintain it.

   Everything here is read from the committed external dataset — the page
   never contacts Google and never writes anything. A decision made here
   (link a spreadsheet map to a VOD map, reject a candidate, ignore a junk
   row, approve an alias for an unmapped value) becomes a JSON entry for
   corrections/external_reconciliation.json: copy it, or download the whole
   merged file, and commit it. Git is the audit trail, exactly as it is for
   corrections/corrections.json; the next `sheet_sync.py rebuild` applies it
   and records it in the export's provenance.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const esc = P.esc;
  const X = P.ext || {};
  const H = X.health || {};
  const R = (X.reconciliation || {});
  const KEY = "owcs.sheetDecisions.v1";
  const today = () => new Date().toISOString().slice(0, 10);

  let pending = { decisions: [], aliasApprovals: [] };
  try { pending = JSON.parse(localStorage.getItem(KEY) || "") || pending; } catch (err) { /* fresh */ }
  const persist = () => { try { localStorage.setItem(KEY, JSON.stringify(pending)); } catch (err) { /* private mode */ } };

  document.addEventListener("DOMContentLoaded", () => {
    renderStatus();
    renderCounts();
    renderQueue();
    renderUnmapped();
    renderDecisions();
    renderTabs();
    renderIssues();
    document.getElementById("main").addEventListener("click", onClick);
  });

  /* ----------------------------------------------------------- status */
  function renderStatus() {
    const st = (X.meta || {}).status || "missing";
    const snap = X.snapshot;
    const att = X.lastAttempt;
    const url = P.sheetLink();
    const word = {
      ok: ["ok", "Synced"], "never-synced": ["stale", "Never synced"],
      "failed-kept-last-good": ["failed", "Latest sync failed — last good import kept"],
      "failed-never-good": ["failed", "Every sync so far has failed"], missing: ["failed", "Dataset missing"],
    }[st] || ["stale", st];
    P.mount("status",
      (P.extIsFixture() ? P.note("warn", "Fixture data", "<p>" + esc(X.meta.fixtureNote) + "</p>") : "") +
      '<div class="card"><div class="u-flex u-between u-center u-gap-3 u-wrap"><h2>' +
      '<span class="trust" data-trust="' + (word[0] === "ok" ? "production" : word[0]) + '">' + esc(word[1]) + "</span></h2>" +
      (url ? '<a class="btn btn--sm btn--ghost" href="' + esc(url) + '" target="_blank" rel="noopener">Open the spreadsheet</a>' : "") +
      "</div>" + P.dl([
        ["Last successful sync", snap ? P.fmtDateTime(snap.importedAt) : "never"],
        ["Workbook SHA-256", snap ? snap.sha256 : "—"],
        ["Workbook size", snap ? (snap.bytes / 1048576).toFixed(1) + " MB" +
          (H.package ? " · " + H.package.mediaFiles + " embedded media files (" +
            (H.package.mediaBytes / 1048576).toFixed(1) + " MB)" : "") : "—"],
        ["Tabs", snap ? snap.sheetNames.length + " (" + snap.sheetNames.join(", ") + ")" : "—"],
        ["Source rows", snap ? snap.sourceRowCount : "—"],
        ["Parser / layout / aliases", [snap && snap.parserVersion, "layout v" + (H.layoutVersion || "?"),
          "aliases v" + (H.aliasesVersion || "?")].join(" · ")],
        ["Spreadsheet id", (X.source || {}).spreadsheetId],
        ["Last attempt", att ? P.fmtDateTime(att.at) + " — " + att.status +
          (att.status === "failed" ? " at " + att.stage + ": " + att.reason : "") : "—"],
      ]) +
      '<p class="small u-mt-4">Reports: <a href="reports/external/gsheet/inventory.md">source inventory</a> · ' +
      '<a href="reports/external/gsheet/reconciliation.md">reconciliation</a> · ' +
      (att && att.status === "failed" ? '<a href="reports/external/gsheet/last_failure.md">last failure</a> · ' : "") +
      '<a href="data/external/gsheet/raw/index.json">raw cell snapshot (index; one gzip file per tab)</a> · ' +
      '<a href="data/external/gsheet/manifest.json">snapshot manifest</a></p>' +
      P.cmd("python3 pipeline/sheet_sync.py sync", "Run a sync locally (the scheduled sheet-sync workflow does this daily and opens a PR)") +
      "</div>");
  }

  /* ----------------------------------------------------------- counts */
  function renderCounts() {
    const c = H.counts || {};
    const s = R.summary || {};
    const ch = (X.snapshot && X.snapshot.changes) || {};
    const tile = (k, v, note, accent) => '<div class="stat"' + (accent ? ' data-accent="' + accent + '"' : "") +
      '><span class="stat__k">' + esc(k) + '</span><span class="stat__v">' + esc(v == null ? "—" : v) +
      '</span><span class="stat__note">' + esc(note) + "</span></div>";
    const vodMaps = s.vodMaps || 0;
    P.mount("counts",
      '<div class="grid grid--4">' +
      tile("Tabs parsed", (H.dataTabs || 0) + " / " + (H.tabCount || 0), "recognised as data / all tabs") +
      tile("Matches", c.matches, "series") + tile("Maps", c.maps, "spreadsheet maps") +
      tile("Hero bans", c.bans, "ban records") +
      tile("Hero observations", c.appearances, "player-hero listings (untimed)") +
      tile("Composition observations", c.compositions, "listed snapshots and hero pools") +
      tile("Ignored rows", c.ignoredRows, "each with its reason below", c.ignoredRows ? "amber" : null) +
      tile("Unmapped values", c.unmappedValues, "texts no alias recognises", c.unmappedValues ? "amber" : null) +
      tile("New", ch.new, "maps since the previous import", "cyan") +
      tile("Changed", ch.changed, "maps whose record changed") +
      tile("Removed", ch.removed, "maps no longer in the sheet", ch.removed ? "amber" : null) +
      tile("Duplicates", (c.duplicates || 0) + " / " + (c.duplicateConflicts || 0), "identical / conflicting") +
      tile("Matched", s.matched, "linked, every shared field agrees", "emerald") +
      tile("Partial", s.partial, "linked, some field only one source records") +
      tile("Conflicts", s.conflict, "linked, a field disagrees", s.conflict ? "red" : null) +
      tile("Needs review", s.needs_review, "candidate exists, not safe to auto-link", s.needs_review ? "amber" : null) +
      "</div>" +
      '<div class="card u-mt-5"><h3>Coverage</h3>' + P.dl([
        ["VOD-verified maps", vodMaps],
        ["…with a spreadsheet record", s.linked || 0],
        ["…VOD only", s.vodOnly || 0],
        ["Spreadsheet maps with no VOD reading", s.unmatched || 0],
        ["Ignored by a reviewer", s.ignored || 0],
      ]) + '<p class="dim small">Spreadsheet-only maps are the long historical tail: results, bans and listed heroes, ' +
      "no timing. VOD maps are the timed, evidence-backed core.</p></div>");
  }

  /* ------------------------------------------------------------ queue */
  function extLabel(m) {
    if (!m) return "?";
    return (m.date || "undated") + " · " + P.anyTeamName(m.teamA) + " vs " + P.anyTeamName(m.teamB) + " · map " +
      (m.order || "?") + " " + (m.map ? P.mapInfo(m.map).name : "“" + (m.mapRaw || "?") + "”");
  }

  function renderQueue() {
    const recs = (R.records || []).filter((r) => (r.status === "needs_review" || r.status === "conflict") && !r.ignored);
    if (!recs.length) {
      P.mount("queue-body", P.empty("✓", "Nothing waiting", "No spreadsheet map needs a link decision and no linked map conflicts."));
      return;
    }
    P.mount("queue-body", '<div class="stack">' + recs.map((r) => {
      const m = P.extMap(r.externalMapId);
      return '<div class="card"><div class="u-flex u-between u-center u-gap-3 u-wrap"><b>' + esc(extLabel(m)) + "</b>" +
        P.reconBadge(r.status) + "</div>" +
        '<p class="dim small">' + esc(r.reason || "") + ' · <a href="map.html?ext=' + encodeURIComponent(r.externalMapId) +
        '">open the map</a> · ' + esc(P.cellRef(m && m.src)) + "</p>" +
        (r.vodMapId ? '<p class="small">Linked to <a href="map.html?id=' + encodeURIComponent(r.vodMapId) + '">' +
          esc(r.vodMapId) + "</a>. Disagreeing fields: " + esc(Object.keys(r.fields || {}).filter((k) =>
            r.fields[k].agree === false).join(", ") || "none") + "</p>" +
          '<div class="row"><button type="button" class="btn btn--sm btn--ghost" data-decide="reject" data-ext="' +
          esc(r.externalMapId) + '" data-vod="' + esc(r.vodMapId) + '">Not the same map — unlink</button></div>' : "") +
        ((r.candidates || []).length && !r.vodMapId ? '<ul class="small">' + r.candidates.map((c) =>
          '<li><a href="map.html?id=' + encodeURIComponent(c.vodMapId) + '">' + esc(c.vodMapId) + "</a> — score " +
          esc(c.score) + ": " + esc(c.reasons.join("; ")) + ' <button type="button" class="btn btn--sm btn--good" data-decide="link" data-ext="' +
          esc(r.externalMapId) + '" data-vod="' + esc(c.vodMapId) + '">Same map — link</button> ' +
          '<button type="button" class="btn btn--sm btn--ghost" data-decide="reject" data-ext="' + esc(r.externalMapId) +
          '" data-vod="' + esc(c.vodMapId) + '">Not this one</button></li>').join("") + "</ul>" : "") +
        '<div class="row"><button type="button" class="btn btn--sm btn--quiet" data-decide="ignore" data-ext="' +
        esc(r.externalMapId) + '">Ignore this row (not an OWCS map)</button></div></div>';
    }).join("") + "</div>");
  }

  /* --------------------------------------------------------- unmapped */
  function targets(kind) {
    if (kind === "hero") return P.heroes().map((h) => [h.id, h.name]);
    if (kind === "map") return P.maps().map((m) => [m.id, m.name]);
    if (kind === "team") return P.teams().map((t) => [t.id, t.name]);
    if (kind === "player") return ((P.pub || {}).players || []).map((p) => [p.id, p.handle]);
    if (kind === "mode") return ["Control", "Escort", "Hybrid", "Push", "Flashpoint", "Clash"].map((m) => [m, m]);
    return [];
  }

  function renderUnmapped() {
    const u = H.unmapped || {};
    const kinds = Object.keys(u);
    if (!kinds.length) { P.mount("unmapped", P.empty("✓", "Every value mapped", "")); return; }
    P.mount("unmapped", '<p class="dim small">Unmapped heroes and maps are never counted. Unmapped teams and players ' +
      "are grouped by their own spelling and marked “unmapped” on the public pages. Approving an alias here adds it to " +
      "your pending decisions.</p>" + kinds.map((k) => {
        const opts = targets(k);
        return '<details class="card"' + (k === "hero" || k === "map" ? " open" : "") + "><summary><b>" + esc(k) +
          "</b> · " + u[k].length + " value" + (u[k].length === 1 ? "" : "s") + "</summary>" +
          '<div class="table-wrap"><table class="tbl tbl--compact"><thead><tr><th>Written as</th><th class="num">Times</th>' +
          "<th>Where</th><th>Approve as</th></tr></thead><tbody>" + u[k].map((v, i) =>
            "<tr><td>“" + esc(v.raw) + '”</td><td class="num">' + v.count + '</td><td class="small">' + esc(v.refs.join(", ")) +
            "</td><td>" + (opts.length ? '<select class="input mf-select" aria-label="Canonical ' + esc(k) + " for " + esc(v.raw) +
              '" id="al-' + esc(k) + "-" + i + '"><option value="">—</option>' + opts.map((o) =>
                '<option value="' + esc(o[0]) + '">' + esc(o[1]) + "</option>").join("") + "</select> " +
              '<button type="button" class="btn btn--sm btn--ghost" data-alias="' + esc(k) + '" data-raw="' + esc(v.raw) +
              '" data-select="al-' + esc(k) + "-" + i + '">Approve</button>'
              : '<span class="dim small">edit config/external/aliases.json</span>') + "</td></tr>").join("") +
          "</tbody></table></div></details>";
      }).join(""));
  }

  /* -------------------------------------------------------- decisions */
  function who() {
    const el = document.getElementById("decider");
    return (el && el.value.trim()) || "";
  }

  function onClick(e) {
    const d = e.target.closest("[data-decide]");
    if (d) {
      if (!who()) { P.toast("Type your name under “Your pending decisions” first", "error"); return; }
      pending.decisions.push({ externalMapId: d.dataset.ext, action: d.dataset.decide,
        vodMapId: d.dataset.vod || undefined, by: who(), at: today(), note: "" });
      persist(); renderDecisions();
      P.toast("Added to your pending decisions");
      return;
    }
    const a = e.target.closest("[data-alias]");
    if (a) {
      const to = document.getElementById(a.dataset.select).value;
      if (!to) { P.toast("Pick the canonical value first", "error"); return; }
      if (!who()) { P.toast("Type your name under “Your pending decisions” first", "error"); return; }
      pending.aliasApprovals.push({ kind: a.dataset.alias, raw: a.dataset.raw, to: to, by: who(), at: today() });
      persist(); renderDecisions();
      P.toast("Alias added to your pending decisions");
      return;
    }
    if (e.target.closest("[data-clear]")) { pending = { decisions: [], aliasApprovals: [] }; persist(); renderDecisions(); }
    if (e.target.closest("[data-download]")) {
      const merged = Object.assign({}, X.decisions || {}, {
        decisions: ((X.decisions || {}).decisions || []).concat(pending.decisions),
        aliasApprovals: ((X.decisions || {}).aliasApprovals || []).concat(pending.aliasApprovals),
      });
      P.download("external_reconciliation.json", JSON.stringify(merged, null, 2) + "\n", "application/json");
    }
    if (e.target.closest("[data-copy-pending]")) P.copy(JSON.stringify(pending, null, 2), "Copied your pending decisions");
  }

  function renderDecisions() {
    const n = pending.decisions.length + pending.aliasApprovals.length;
    const prev = document.getElementById("decider");
    const name = prev ? prev.value : "";
    P.mount("decisions",
      '<label class="field"><span class="mx-label">Your name (recorded with every decision)</span>' +
      '<input class="input" id="decider" autocomplete="name" value="' + esc(name) + '"></label>' +
      (n ? '<pre class="console u-mt-4">' + esc(JSON.stringify(pending, null, 2)) + "</pre>" +
        '<div class="row u-mt-4"><button type="button" class="btn btn--sm btn--primary" data-download>Download the merged ' +
        "corrections/external_reconciliation.json</button>" +
        '<button type="button" class="btn btn--sm btn--ghost" data-copy-pending>Copy as JSON</button>' +
        '<button type="button" class="btn btn--sm btn--quiet" data-clear>Clear</button></div>' +
        '<p class="dim small u-mt-3">Commit the file, then run <code>python3 pipeline/sheet_sync.py rebuild</code>. ' +
        "Every decision is carried into the export’s provenance.</p>"
        : '<p class="dim small u-mt-3">No pending decisions. Already committed: ' +
          ((X.decisions || {}).decisions || []).length + " link decision(s), " +
          ((X.decisions || {}).aliasApprovals || []).length + " alias approval(s).</p>"));
  }

  /* ------------------------------------------------------------- tabs */
  function renderTabs() {
    const tabs = H.tabs || [];
    if (!tabs.length) { P.mount("tabs", P.empty("◇", "No workbook imported yet", "")); return; }
    P.mount("tabs", '<div class="table-wrap"><table class="tbl"><thead><tr><th>#</th><th>Tab</th><th class="num">Rows</th>' +
      '<th>Read as</th><th class="num">Header row</th><th class="num">Maps</th><th class="num">Bans</th>' +
      '<th class="num">Hero obs.</th><th class="num">Ignored</th></tr></thead><tbody>' + tabs.map((t) =>
        "<tr><td>" + (t.index + 1) + "</td><td><b>" + esc(t.name) + "</b>" + (t.state && t.state !== "visible"
          ? ' <span class="dim small">' + esc(t.state) + "</span>" : "") + '</td><td class="num">' + t.rows + "</td><td>" +
          esc(t.purposeLabel) + '</td><td class="num">' + (t.headerRow || "—") + '</td><td class="num">' + t.parsed.maps +
          '</td><td class="num">' + t.parsed.bans + '</td><td class="num">' + t.parsed.appearances + '</td><td class="num">' +
          t.ignoredCount + "</td></tr>").join("") + "</tbody></table></div>" +
      tabs.map((t) => (t.mappedColumns.length || t.unmappedColumns.length || t.ignoredRows.length)
        ? '<details class="diag"><summary>' + esc(t.name) + " — columns and ignored rows</summary><div class=\"diag__body\">" +
          (t.mappedColumns.length ? '<p class="small"><b>Mapped:</b> ' + t.mappedColumns.map((c) =>
            esc(c.column + " “" + c.header + "” → " + c.as)).join("; ") + "</p>" : "") +
          (t.unmappedColumns.length ? '<p class="small"><b>Kept in the raw layer only:</b> ' + t.unmappedColumns.map((c) =>
            esc(c.column + " “" + c.header + "”")).join("; ") + "</p>" : "") +
          (t.ignoredRows.length ? '<ul class="small">' + t.ignoredRows.map((r) => "<li>row " + r.row + ": " + esc(r.reason) +
            "</li>").join("") + "</ul>" : "") + "</div></details>" : "").join("") + renderCards());
  }

  /* Match cards + the sheet's own Self-Check (the real sheet's layout). */
  function renderCards() {
    const mc = H.matchCards, sc = H.selfCheck;
    let html = "";
    if (mc) {
      html += '<p class="small u-mt-4"><b>Match cards:</b> ' + mc.blocks + " found · " + mc.played + " imported · " +
        mc.scheduled + " scheduled · " + (mc.incomplete || 0) + " incomplete · " + (mc.forfeited || 0) +
        " forfeited · " + mc.failed + " unreadable. Season " + esc(mc.year || "not stated") +
        ". Heroes are pictures named by the sheet\u2019s " + esc(mc.heroIndexTab || "Hero Index") +
        "; the sheet records no dates and no map picks.</p>";
    }
    if (sc && (sc.rows || []).length) {
      html += '<h3 class="u-mt-4">Self-Check — the sheet\u2019s own counts vs this import</h3>' +
        '<div class="table-wrap"><table class="tbl tbl--compact"><thead><tr><th>Stage</th><th>Region</th><th>Tab</th>' +
        '<th class="num">Matches (sheet / parsed)</th><th class="num">Maps (sheet / parsed)</th><th></th></tr></thead><tbody>' +
        sc.rows.map((r) => {
          const p = r.parsed || {};
          const mark = r.agree === true ? "agree" : r.agree === false ? "<b>disagree</b>" : '<span class="dim">not paired</span>';
          return "<tr><td>" + esc(r.stage || "—") + "</td><td>" + esc(r.region) + "</td><td>" +
            esc((r.tabs || []).join(", ") || "—") + '</td><td class="num">' + esc(r.sheet.matches) + " / " +
            esc(p.matches != null ? p.matches : "—") + '</td><td class="num">' + esc(r.sheet.maps) + " / " +
            esc(p.maps != null ? p.maps : "—") + "</td><td>" + mark + "</td></tr>";
        }).join("") + "</tbody></table></div>";
    }
    return html;
  }

  function renderIssues() {
    const issues = (H.errors || []).concat(H.issues || []);
    const hist = X.history || [];
    P.mount("issues",
      (issues.length ? '<ul class="small">' + issues.map((i) => "<li><b>" + esc(i.code) + "</b> " +
        esc(i.message) + (i.accepted ? " (accepted by the operator)" : "") + "</li>").join("") + "</ul>"
        : '<p class="dim small">No parser issues in the last good import.</p>') +
      (hist.length ? '<div class="table-wrap u-mt-4"><table class="tbl tbl--compact"><thead><tr><th>Imported</th><th>SHA-256</th>' +
        '<th class="num">Maps</th><th class="num">New</th><th class="num">Changed</th><th class="num">Removed</th></tr></thead><tbody>' +
        hist.slice().reverse().map((h) => "<tr><td>" + esc(P.fmtDateTime(h.importedAt)) + '</td><td class="mono small">' +
          esc(String(h.sha256).slice(0, 16)) + '…</td><td class="num">' + ((h.counts || {}).maps || 0) + '</td><td class="num">' +
          ((h.changes || {}).new || 0) + '</td><td class="num">' + ((h.changes || {}).changed || 0) + '</td><td class="num">' +
          ((h.changes || {}).removed || 0) + "</td></tr>").join("") + "</tbody></table></div>" : ""));
  }
})();
