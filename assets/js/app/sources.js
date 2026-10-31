/* =====================================================================
   OWCS Comp Tracker — app/sources.js
   Where a number came from, said every time.

   The site now reads FOUR datasets and still never merges them:

     window.OWCS_PUBLIC    (public_data.v1.js)     VOD verified: read off the
                                                   broadcast, timed, with evidence
     window.OWCS_DATA      (data.js)               working record (admin)
     window.OWCS_DISCOVERED(discovered.v1.js)      what the scan found
     window.OWCS_EXTERNAL  (external_sheet.v1.js)  IMPORTED from a community-
                                                   maintained Google Sheet: what
                                                   its editors documented, with
                                                   no timing and no evidence frames

   Every fact rendered from the last one carries a provenance badge, and a
   fact that both sources record carries the reconciliation verdict instead:

     VOD verified · Spreadsheet imported · Both sources agree · Conflicting sources

   Nothing in this file contacts anything: the external dataset was
   imported by the pipeline and committed. Visitors never talk to Google.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  if (!P) return;
  const esc = P.esc;
  const X = window.OWCS_EXTERNAL || null;
  P.ext = X;

  const LABELS = (X && X.meta && X.meta.provenanceLabels) || {
    vod: "VOD verified", sheet: "Spreadsheet imported",
    both: "Both sources agree", conflict: "Conflicting sources",
  };
  const SAY = {
    vod: "Read off the broadcast HUD by the tracker: timed to the second, with evidence frames.",
    sheet: "Imported from the community-maintained spreadsheet: what its editors documented. " +
      "Not timed and not checked against the broadcast.",
    both: "The VOD reading and the spreadsheet record this the same way.",
    conflict: "The VOD reading and the spreadsheet disagree. Both claims are shown; neither is chosen.",
    partial: "Only one source records this field.",
  };
  const TRUST = { vod: "production", sheet: "detected", both: "reviewed", conflict: "failed",
    partial: "partial" };

  /* <span class="trust" data-trust=…>VOD verified</span> — the one badge. */
  P.srcBadge = (kind, text) =>
    '<span class="trust src-badge" data-src="' + esc(kind) + '" data-trust="' +
    esc(TRUST[kind] || "detected") + '" title="' + esc(SAY[kind] || "") + '">' +
    esc(text || LABELS[kind] || kind) + "</span>";
  P.srcSay = (kind) => SAY[kind] || "";

  /* Reconciliation status -> badge. The vocabulary is the exporter's. */
  const STATUS = {
    matched: { kind: "both", label: "Both sources agree" },
    partial: { kind: "partial", label: "Partly matched" },
    conflict: { kind: "conflict", label: "Conflicting sources" },
    unmatched: { kind: "sheet", label: "Spreadsheet only" },
    needs_review: { kind: "partial", label: "Needs review" },
  };
  P.reconBadge = (status) => {
    const s = STATUS[status] || STATUS.unmatched;
    return P.srcBadge(s.kind, s.label);
  };

  /* ----------------------------------------------------- accessors */
  const maps = (X && X.maps) || [];
  const matches = (X && X.matches) || [];
  const recs = (X && X.reconciliation && X.reconciliation.records) || [];
  const byId = new Map(maps.map((m) => [m.id, m]));
  const matchById = new Map(matches.map((m) => [m.id, m]));
  const recByExt = new Map(recs.map((r) => [r.externalMapId, r]));
  const recByVod = new Map();
  recs.forEach((r) => { if (r.vodMapId) recByVod.set(r.vodMapId, r); });
  const tours = new Map(((X && X.tournaments) || []).map((t) => [t.id, t]));

  /* Maps only the spreadsheet plays (not in the published catalog) get
     their proper name and mode instead of a raw id. */
  Object.keys((X && X.mapsExtra) || {}).forEach((id) => {
    if (P.idx && P.idx.maps && !P.idx.maps.has(id)) P.idx.maps.set(id, X.mapsExtra[id]);
  });

  P.extMaps = () => maps;
  P.extMatches = () => matches;
  P.extMap = (id) => byId.get(id) || null;
  P.extMatch = (id) => matchById.get(id) || null;
  P.extTournament = (id) => tours.get(id) || null;
  P.recForExt = (id) => recByExt.get(id) || null;
  P.recForVod = (id) => recByVod.get(id) || null;
  P.extForVod = (vodMapId) => {
    const r = recByVod.get(vodMapId);
    return r ? byId.get(r.externalMapId) || null : null;
  };
  P.extStatus = () => (X && X.meta && X.meta.status) || "missing";
  P.extIsFixture = () => !!(X && X.meta && X.meta.fixture);

  /* A team the registry knows renders as itself. A team only the sheet
     names renders with the sheet's spelling and an explicit marker — never
     dressed up as a verified organisation. */
  const extTeams = (X && X.teams) || {};
  P.anyTeamName = (id) => {
    const t = id && P.team(id);
    if (t) return t.name;
    const e = id && extTeams[id];
    return e ? e.name : (id || "Unknown team");
  };
  P.anyTeamPlate = (id, opt) => {
    opt = opt || {};
    if (!id) return P.teamPlate(null, { tbd: "Not recorded", size: opt.size });
    if (P.team(id)) return P.teamPlate(id, opt);
    const e = extTeams[id] || { name: id };
    return '<span class="team' + (opt.size ? " team--" + opt.size : "") + '" title="' +
      esc(e.name + " — named by the spreadsheet; not in the tracker's team registry") + '">' +
      '<span class="team__logo">' + P.teamCrest({ id: id, name: e.name }) + "</span>" +
      '<span class="team__name">' + esc(e.name) + '<small class="unmapped-mark"> · unmapped</small>' +
      "</span></span>";
  };

  /* A hero entry from the sheet: canonical tile when it resolved, the
     original text (struck through as unresolved) when it did not. */
  P.extHero = (h, opt) => {
    if (h && h.hero) return P.heroTile(h.hero, opt || { size: "xs" });
    return '<span class="chip chip--outline unresolved" title="The spreadsheet wrote this; ' +
      'no hero name matches it, so it is never counted">“' + esc((h && h.raw) || "?") + "”</span>";
  };

  /* Per-map spreadsheet detail (players, listed heroes, cell references)
     is sharded into assets/data/external_sheet/detail-<xx>.js by two hex
     characters of the map id — the same rule as export.detail_bucket in
     the pipeline. Same origin, loaded on demand, never from Google. */
  P.extDetailBucket = (id) => (/^xmap-/.test(id || "") ? id.slice(5, 7) : "00").toLowerCase();
  const detailLoads = {};
  P.loadExtDetail = (id) => {
    const bucket = P.extDetailBucket(id);
    const have = () => ((window.OWCS_EXTERNAL_DETAIL || {})[id]) || null;
    if (have()) return Promise.resolve(have());
    if (!detailLoads[bucket]) {
      detailLoads[bucket] = new Promise((resolve) => {
        const known = (X && X.detailShards) || [];
        if (known.indexOf(bucket) < 0) { resolve(); return; }
        const el = document.createElement("script");
        el.src = "assets/data/external_sheet/detail-" + bucket + ".js";
        el.onload = () => resolve();
        el.onerror = () => resolve();
        document.head.appendChild(el);
      });
    }
    return detailLoads[bucket].then(have);
  };

  /* The scouting page for one map entry (metastats.buildEntries). */
  P.mapPageHref = (e) => e.vodId ? "map.html?id=" + encodeURIComponent(e.vodId)
    : "map.html?ext=" + encodeURIComponent(e.extId);

  P.sheetLink = () => (X && X.source && X.source.viewUrl) || null;
  P.cellRef = (src) => (src && src.sheet ? src.sheet + (src.row ? " row " + src.row : "") +
    (src.cell ? " · " + src.cell : "") : "");

  /* A percentage that never rounds a gap away: 99.8% is not "100%", and
     0.3% is not "0%". */
  P.pctHonest = (v) => {
    if (v == null) return "—";
    const p = v * 100;
    if (p > 0 && p < 1) return p.toFixed(1) + "%";
    if (p > 99 && p < 100) return (Math.floor(p * 10) / 10).toFixed(1) + "%";
    return Math.round(p) + "%";
  };

  /* Honest small-sample wording, used everywhere a rate is shown. */
  P.sampleNote = (n, unit) => {
    unit = unit || "map";
    if (!n) return '<span class="sample sample--none">no sample</span>';
    const word = n + " " + unit + (n === 1 ? "" : "s");
    return n < 5
      ? '<span class="sample sample--tiny" title="Fewer than 5 — treat as anecdote, not a rate">n=' +
        esc(word) + " ⚠</span>"
      : n < 20
        ? '<span class="sample sample--small" title="Small sample">n=' + esc(word) + "</span>"
        : '<span class="sample">n=' + esc(word) + "</span>";
  };

  /* One line that says what the external dataset is and how fresh it is. */
  P.extProvenanceLine = () => {
    if (!X) return P.note("info", "Spreadsheet data not loaded", "<p>This page could not load " +
      "the imported spreadsheet dataset.</p>");
    const snap = X.snapshot;
    const url = P.sheetLink();
    const fixture = P.extIsFixture()
      ? P.note("warn", "Fixture data", "<p>" + esc(X.meta.fixtureNote || "") + "</p>") : "";
    if (!snap) {
      return fixture + P.note("info", "The community spreadsheet has not been imported yet",
        "<p>Spreadsheet observations appear here after the first successful sync " +
        "(<code>python3 pipeline/sheet_sync.py sync</code>, or the scheduled " +
        "<code>sheet-sync</code> workflow). Until then every number on this page is " +
        "VOD verified only." + (url ? ' <a href="' + esc(url) + '" rel="noopener" ' +
        'target="_blank">Open the spreadsheet →</a>' : "") + "</p>");
    }
    return fixture + '<p class="dim small ext-line">' + P.srcBadge("sheet") + " " +
      "Imported " + esc(P.fmtDate(snap.importedAt)) + " · snapshot <code>" +
      esc(String(snap.sha256 || "").slice(0, 12)) + "</code>" +
      (url ? ' · <a href="' + esc(url) + '" target="_blank" rel="noopener">original sheet</a>' : "") +
      ' · <a href="how-it-works.html#sources">how the two sources differ</a>' +
      (P.extStatus() === "failed-kept-last-good"
        ? ' · <span class="trust" data-trust="stale">latest sync failed — showing last good import</span>' : "") +
      "</p>";
  };
})();
