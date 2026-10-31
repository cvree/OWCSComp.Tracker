/* =====================================================================
   OWCS Comp Tracker — app/page-map.js
   One map, scouted — from whichever sources recorded it, side by side.

     map.html?id=<published map id>     a VOD-read map (and its sheet twin)
     map.html?ext=<spreadsheet map id>  a map only the spreadsheet records

   The page is two lanes that never blend:

     VOD verified        the timeline read off the broadcast — who was on
                         which hero, from when to when, every swap with its
                         before/after frames, seconds per hero, coverage
     Spreadsheet record  what the community sheet's editors wrote down —
                         the heroes listed per player, bans, pick, result;
                         never timed, never "the comp they ran for N s"

   and one panel that compares them field by field (reconciliation), so a
   visitor sees exactly where the two agree, where only one speaks, and
   where they conflict. Nothing here chooses between conflicting claims.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const esc = P.esc;
  const pct = (v) => P.pctHonest(v);
  const secs = (n) => (n == null ? "—" : P.fmtClock(n));

  let entry = null;

  document.addEventListener("DOMContentLoaded", () => {
    const id = P.qs("id"), ext = P.qs("ext");
    const all = P.metaEntries();
    entry = all.filter((e) => (id && (e.vodId === id || e.id === id)) ||
      (ext && (e.extId === ext || e.id === ext)))[0] || null;
    if (!entry) {
      P.mount("crumbs", P.breadcrumbs([{ label: "Maps", href: "index.html" }, { label: "Map" }]));
      P.mount("head", '<div class="page-head page-head--tight"><h1>Map not found</h1></div>');
      P.mount("body", P.empty("◇", "No map with that id",
        'It may have been renamed by a newer import. <a href="meta.html?tab=maps">Browse every map →</a>'));
      return;
    }
    render();
    if (P.evidence) P.evidence.wire(document);
    const match = entry.vodId ? P.match(entry.vodId.replace(/-m\d+$/, "")) : null;
    if (P.mapStory && match) P.mapStory.wire(document.getElementById("body"), match);
  });

  /* ----------------------------------------------------------- shell */
  function title() {
    const info = P.mapInfo(entry.map);
    return (entry.map ? info.name : (entry.sheet && entry.sheet.mapRaw) || "Unknown map");
  }

  function render() {
    const info = P.mapInfo(entry.map);
    document.title = title() + " — " + P.anyTeamName(entry.teamA) + " vs " +
      P.anyTeamName(entry.teamB) + " — OWCS Comp Tracker";
    P.mount("crumbs", P.breadcrumbs([
      { label: "Maps", href: "index.html" },
      entry.vodId ? { label: "Game", href: "game.html?id=" + encodeURIComponent(entry.vodId.replace(/-m\d+$/, "")) }
        : { label: "Meta", href: "meta.html?tab=maps" },
      { label: title() },
    ]));
    const order = (entry.sheet && entry.sheet.order) || (entry.vodId && Number((entry.vodId.match(/-m(\d+)$/) || [])[1])) || null;
    const badges = [entry.vod ? P.srcBadge("vod") : "", entry.sheet ? P.srcBadge("sheet") : "",
      entry.vod && entry.sheet ? P.reconBadge(entry.status) : ""].join(" ");
    const start = entry.vod && entry.vod.liveWindows && entry.vod.liveWindows[0]
      ? entry.vod.liveWindows[0][0] : null;
    const watch = entry.vodUrl
      ? '<a class="btn btn--primary btn--sm" href="' + esc(start != null && P.mapIndex
          ? P.mapIndex.vodAt(entry.vodUrl, start) : entry.vodUrl) +
        '" target="_blank" rel="noopener">▶ Watch' + (start != null ? " from " + P.fmtClock(start) : "") +
        "</a>" + (entry.vod ? "" : ' <span class="dim small">(link written in the spreadsheet)</span>')
      : "";
    const side = (team, won) => '<div class="map-hd__team' + (won ? " is-win" : "") + '">' +
      P.anyTeamPlate(team, { size: "lg", link: true }) + (won ? '<span class="mf-win" title="Won this map">W</span>' : "") +
      "</div>";
    P.mount("head",
      '<div class="page-head page-head--tight map-hd">' +
        '<p class="eyebrow eyebrow--gold">' + esc([entry.event, entry.stage, entry.date ? P.fmtDate(entry.date) : null]
          .filter(Boolean).join(" · ")) + "</p>" +
        "<h1>" + esc(title()) + (order ? ' <span class="dim">· map ' + esc(order) + "</span>" : "") + "</h1>" +
        '<p class="row">' + (entry.mode || info.mode ? '<span class="chip chip--outline">' + esc(entry.mode || info.mode) + "</span>" : "") +
          (entry.region ? '<span class="chip chip--outline">' + esc(P.regionName(entry.region)) + "</span>" : "") +
          (entry.patch ? '<span class="chip chip--outline">Patch ' + esc(entry.patch) + "</span>" : "") +
          badges + "</p>" +
        '<div class="map-hd__teams">' + side(entry.teamA, entry.winner && entry.winner === entry.teamA) +
          '<span class="map-hd__vs">vs</span>' + side(entry.teamB, entry.winner && entry.winner === entry.teamB) +
        "</div>" +
        (entry.winnerConflict ? P.note("warn", "The sources disagree on who won this map",
          "<p>Both claims are listed under <a href=\"#recon\">Sources compared</a>. The map is left out of every win count.</p>") : "") +
        '<div class="row u-mt-4">' + watch + "</div>" +
      "</div>");

    const parts = [P.extIsFixture() ? P.extProvenanceLine() : "", factsSection(), vodSection(), sheetSection()];
    if (entry.sheet) parts.push(reconSection());
    document.getElementById("body").innerHTML = parts.filter(Boolean).join("");
  }

  /* ---------------------------------------------------- result/draft */
  function factsSection() {
    const s = entry.sheet;
    const rows = [];
    const who = (id, raw) => id ? P.anyTeamPlate(id, { size: "sm" }) : raw ? "“" + esc(raw) + "”" : '<span class="dim">not recorded</span>';
    const vodWinner = entry.vod ? entry.vod.winner : null;
    rows.push(["Map winner",
      (entry.vod ? '<div class="fact">' + P.srcBadge("vod") + " " + who(vodWinner) +
        (vodWinner ? ' <span class="dim small">recorded with the VOD reading</span>' : "") + "</div>" : "") +
      (s ? '<div class="fact">' + P.srcBadge("sheet") + " " + who(s.winner, s.winnerRaw) +
        (s.winnerMethod === "derived-from-map-score" ? ' <span class="dim small">derived from the sheet’s map score</span>' : "") +
        "</div>" : "")]);
    rows.push(["Map pick", s ? '<div class="fact">' + P.srcBadge("sheet") + " " +
      (s.pickedBy ? who(s.pickedBy) : s.pickNote ? "“" + esc(s.pickNote) + "”" : '<span class="dim">not recorded</span>') + "</div>"
      : '<span class="dim">not recorded by either source</span>']);
    if (s && s.scoreRaw) rows.push(["Map score", P.srcBadge("sheet") + " " + esc(s.scoreRaw)]);
    const bans = (entry.bans || []);
    rows.push(["Hero bans", bans.length
      ? '<div class="ban-row">' + ["a", "b", null].map((side) => {
          const list = bans.filter((b) => (b.side || null) === side);
          if (!list.length) return "";
          const team = side === "a" ? entry.sheet.teamA : side === "b" ? entry.sheet.teamB : null;
          return '<div class="ban-row__team"><span class="dim small">' +
            (team ? "listed under " + esc(P.anyTeamName(team)) : "team not recorded") + "</span> " +
            list.map((b) => P.extHero(b, { size: "xs" })).join("") + "</div>";
        }).join("") + ' <span class="dim small">' + P.srcBadge("sheet") + "</span></div>"
      : '<span class="dim">no bans recorded' + (entry.vod ? " by either source" : "") + "</span>"]);
    return '<section class="band" aria-labelledby="h-facts"><div class="sec-head"><h2 id="h-facts">Result &amp; draft</h2></div>' +
      '<div class="card"><div class="table-wrap"><table class="tbl tbl--facts"><tbody>' + rows.map((r) =>
        '<tr><th scope="row">' + esc(r[0]) + "</th><td>" + r[1] + "</td></tr>").join("") +
      "</tbody></table></div></div></section>";
  }

  /* -------------------------------------------------- VOD lane */
  function vodSection() {
    if (!entry.vod) {
      return '<section class="band" aria-labelledby="h-vod"><div class="sec-head"><h2 id="h-vod">VOD-verified timeline</h2>' +
        P.srcBadge("vod") + "</div>" + P.note("info", "Not read from the broadcast",
          "<p>The tracker has not read this map off its VOD, so there are no timed hero stints, " +
          "no swap timings and no seconds-per-hero for it. Everything below comes from the " +
          "spreadsheet and is labelled as such.</p>") + "</section>";
    }
    const v = entry.vod;
    const match = P.match(entry.vodId.replace(/-m\d+$/, ""));
    const mp = match && (match.maps || []).filter((x) => x.id === entry.vodId)[0];
    const story = mp && P.mapStory ? P.mapStory.html(mp, match) : "";
    const tile = (k, val, note) => '<div class="stat"><span class="stat__k">' + esc(k) +
      '</span><span class="stat__v">' + val + '</span><span class="stat__note">' + esc(note) + "</span></div>";
    return '<section class="band" aria-labelledby="h-vod"><div class="sec-head"><h2 id="h-vod">VOD-verified timeline</h2>' +
      P.srcBadge("vod") + "</div>" +
      '<div class="grid grid--4">' +
        tile("Live seconds", secs(v.liveSeconds), v.liveWindowSource === "rounds"
          ? "inside the " + v.liveWindows.length + " detected round window" + (v.liveWindows.length === 1 ? "" : "s")
          : "stint span — no rounds were detected, so this includes non-live time") +
        tile("Coverage", pct(v.coverage), "known player-seconds ÷ (live seconds × " + (v.teamSize * 2) + " HUD slots)") +
        tile("Unknown", secs(v.unknownPlayerSeconds), "player-seconds with no reading — excluded from every share") +
        tile("Confirmed swaps", v.confirmedSwaps, "temporal-consensus verdicts; rejected ones are on the timeline") +
      "</div>" +
      (story ? '<div class="card u-mt-5">' + story + "</div>" : "") +
      '<div class="grid grid--2 u-mt-5">' + (v.teams || []).map(teamTime).join("") + "</div>" +
      '<p class="dim small u-mt-4">' + esc(((P.pub.timeWeighting || {}).method || {}).note || "") + "</p>" +
      "</section>";
  }

  function teamTime(t) {
    const bar = (n, total) => '<span class="dur"><span class="dur__fill" style="width:' +
      (total ? Math.max(1, Math.round(n / total * 100)) : 0) + '%"></span></span>';
    return '<div class="card tt">' +
      '<div class="u-flex u-between u-center u-gap-3">' + P.anyTeamPlate(t.teamId, { size: "sm" }) +
        '<span class="dim small">' + (t.result ? (t.result === "win" ? "won" : "lost") + " · " : "") +
        pct(t.coverage) + " coverage</span></div>" +
      '<p class="label u-mt-4">Time on each hero</p>' +
      '<div class="table-wrap"><table class="tbl tbl--compact"><thead><tr><th>Hero</th><th class="num">Time</th>' +
        '<th class="num" title="Seconds on this hero ÷ the team’s KNOWN player-seconds">Share of known</th>' +
        '<th class="num" title="Seconds on this hero ÷ the team’s known seconds on heroes of the same role">Share of role</th>' +
        '<th title="Bar = seconds on the hero ÷ the map’s live seconds">Of live time</th></tr></thead><tbody>' +
      (t.heroes || []).map((h) => "<tr><td>" + P.heroTile(h.hero, { size: "xs", link: true }) + "</td>" +
        '<td class="num mono">' + secs(h.seconds) + "</td>" +
        '<td class="num">' + pct(h.shareOfTeamKnownSeconds) + "</td>" +
        '<td class="num">' + pct(h.shareOfRoleKnownSeconds) + "</td>" +
        "<td>" + bar(h.seconds, entry.vod.liveSeconds) + "</td></tr>").join("") +
      "</tbody></table></div>" +
      '<p class="label u-mt-4">Per HUD slot <span class="dim small">(slot, not player: the HUD does not name players)</span></p>' +
      (t.slots || []).map((s) => '<div class="slotbar"><span class="mono dim small">' + (t.side === "a" ? "L" : "R") + s.slot +
        '</span><span class="slotbar__track">' + s.heroes.map((h) =>
          '<span class="slotbar__seg" data-role="' + esc(P.hero(h.hero).role || "") + '" style="flex:' + h.seconds +
          '" title="' + esc(P.hero(h.hero).name + " · " + secs(h.seconds)) + '">' + esc(P.hero(h.hero).name) + "</span>").join("") +
        (s.knownSeconds < entry.vod.liveSeconds ? '<span class="slotbar__seg slotbar__seg--unknown" style="flex:' +
          (entry.vod.liveSeconds - s.knownSeconds) + '" title="Not read: ' + secs(entry.vod.liveSeconds - s.knownSeconds) + '">?</span>' : "") +
        "</span></div>").join("") +
      '<p class="label u-mt-4">Line-ups, by time fielded</p>' +
      ((t.lineups || []).length ? (t.lineups || []).map((lu) => '<div class="lu">' + P.compStrip(lu.heroes, { size: "xs", tight: true }) +
        '<span class="mono small">' + secs(lu.seconds) + " · " + pct(lu.share) + "</span>" +
        '<span class="chip chip--outline" title="Editorial label from config/comp_archetypes.json">' + esc(lu.archetype) + "</span></div>").join("")
        : '<p class="dim small">No moment with all five slots read.</p>') +
      "</div>";
  }

  /* ------------------------------------------------ spreadsheet lane */
  function sheetSection() {
    const s = entry.sheet;
    if (!s) {
      return P.extMaps().length ? '<section class="band"><div class="sec-head"><h2>Spreadsheet record</h2>' +
        P.srcBadge("sheet") + "</div>" + P.note("info", "Not in the spreadsheet",
          "<p>No row of the imported community spreadsheet was matched to this map.</p>") + "</section>" : "";
    }
    const teamCard = (side) => {
      const t = (s.teams || {})[side] || { players: [], comps: [] };
      const team = side === "a" ? s.teamA : s.teamB;
      const first = (t.comps || []).filter((c) => c.kind === "first-listed")[0];
      const listed = (t.comps || []).filter((c) => c.kind === "listed-comp")[0];
      return '<div class="card">' + P.anyTeamPlate(team, { size: "sm" }) +
        ((t.players || []).length
          ? '<div class="table-wrap u-mt-4"><table class="tbl tbl--compact"><thead><tr><th>Player</th><th>Role</th>' +
            '<th title="In the order the sheet lists them — not play order, not time">Heroes listed</th></tr></thead><tbody>' +
            t.players.map((p) => "<tr><td>" + (p.playerRaw ? esc(p.playerRaw) +
              (p.playerMapped ? "" : ' <small class="unmapped-mark">unmapped</small>') : '<span class="dim">position ' + p.position + "</span>") +
              "</td><td>" + esc(p.role || "—") + "</td><td>" + p.heroes.map((h) => P.extHero(h, { size: "xs" })).join("") +
              "</td></tr>").join("") + "</tbody></table></div>"
          : '<p class="dim small u-mt-4">No heroes listed for this team.</p>') +
        (listed ? '<p class="label u-mt-4">Composition written in the sheet</p>' + compLine(listed) : "") +
        (first ? '<p class="label u-mt-4">First-listed heroes <span class="dim small">a snapshot of the sheet, not the starting comp</span></p>' +
          compLine(first) : "") +
        "</div>";
    };
    const flags = (s.flags || []).filter((f) => f !== "created-from-long-format-rows");
    return '<section class="band" aria-labelledby="h-sheet"><div class="sec-head"><h2 id="h-sheet">Spreadsheet record</h2>' +
      P.srcBadge("sheet") + "</div>" +
      '<p class="dim small">Spreadsheet observation: what the sheet’s editors documented. It does not say when a hero was ' +
      "played or for how long, and nothing on this page turns it into seconds.</p>" +
      '<div class="grid grid--2 u-mt-4">' + teamCard("a") + teamCard("b") + "</div>" +
      (flags.length || (s.duplicateConflicts || []).length
        ? P.note("warn", "The spreadsheet disagrees with itself here",
          "<ul>" + flags.map((f) => "<li>" + esc(f.replace(/-/g, " ")) + "</li>").join("") +
          (s.duplicateConflicts || []).map((d) => "<li>listed again at " + esc(P.cellRef(d.src)) +
            " with a different " + esc(d.fields.join(", ")) + "</li>").join("") + "</ul>")
        : "") +
      '<p class="dim small u-mt-4">Source: ' + esc(P.cellRef(s.src)) +
      ((s.alsoListedAt || []).length ? " · also listed at " + s.alsoListedAt.map(P.cellRef).map(esc).join(", ") : "") +
      (P.sheetLink() ? ' · <a href="' + esc(P.sheetLink()) + '" target="_blank" rel="noopener">open the spreadsheet</a>' : "") +
      "</p></section>";
  }

  function compLine(c) {
    return '<div class="row">' + P.compStrip(c.heroes, { size: "xs", tight: true }) +
      (c.unresolved || []).map((r) => P.extHero({ raw: r })).join("") +
      (c.complete ? "" : '<span class="dim small">incomplete — not counted as a comp</span>') + "</div>";
  }

  /* --------------------------------------------- reconciliation panel */
  const FIELD_LABEL = { date: "Date", map: "Map", order: "Map number", mode: "Mode", winner: "Winner",
    pickedBy: "Map pick", bans: "Hero bans" };

  function fmtVal(k, v) {
    if (v == null || (Array.isArray(v) && !v.length)) return '<span class="dim">not recorded</span>';
    if (k === "winner" || k === "pickedBy") return esc(P.anyTeamName(v));
    if (k === "map") return esc(P.mapInfo(v).name);
    if (k === "bans" || k.indexOf("heroes:") === 0) return v.map((h) => P.heroTile(h, { size: "xs" })).join("");
    return esc(v);
  }

  function reconSection() {
    const rec = entry.extId ? P.recForExt(entry.extId) : null;
    if (!rec) return "";
    const verdict = (a) => a === true ? P.srcBadge("both", "agree") : a === false ? P.srcBadge("conflict", "disagree")
      : P.srcBadge("partial", "one source only");
    const fields = rec.fields || {};
    const rows = Object.keys(fields).map((k) => {
      const f = fields[k];
      const label = k.indexOf("heroes:") === 0 ? "Heroes — " + P.anyTeamName(k.slice(7)) : (FIELD_LABEL[k] || k);
      const extra = k.indexOf("heroes:") === 0
        ? ((f.sheetOnly || []).length ? '<div class="small">listed by the sheet, never seen on the VOD: ' +
            f.sheetOnly.map((h) => esc(P.hero(h).name)).join(", ") + "</div>" : "") +
          ((f.vodOnly || []).length ? '<div class="small">on the VOD for 30 s or more, not listed: ' +
            f.vodOnly.map((h) => esc(P.hero(h).name)).join(", ") + "</div>" : "") +
          ((f.vodOnlyBrief || []).length ? '<div class="small dim">briefly on the VOD, not listed: ' +
            f.vodOnlyBrief.map((h) => esc(P.hero(h).name)).join(", ") + "</div>" : "")
        : (f.note ? '<div class="small dim">' + esc(f.note) + "</div>" : "");
      return '<tr><th scope="row">' + esc(label) + "</th><td>" + fmtVal(k, f.vod) + "</td><td>" +
        fmtVal(k, f.sheet) + "</td><td>" + verdict(f.agree) + extra + "</td></tr>";
    }).join("");
    const cands = (rec.candidates || []).map((c) => "<li><a href=\"map.html?id=" + encodeURIComponent(c.vodMapId) + '">' +
      esc(c.vodMapId) + "</a> — score " + esc(c.score) + ": " + esc(c.reasons.join("; ")) + "</li>").join("");
    return '<section class="band" id="recon" aria-labelledby="h-recon"><div class="sec-head"><h2 id="h-recon">Sources compared</h2>' +
      P.reconBadge(rec.status) + "</div>" +
      '<p class="dim small">' + esc(rec.reason || "") + (rec.method ? " · linked " + esc(rec.method === "manual"
        ? "by a reviewer" : "automatically (score " + rec.score + ")") : "") + "</p>" +
      (rows ? '<div class="table-wrap"><table class="tbl"><thead><tr><th>Field</th><th>' + P.srcBadge("vod") +
        "</th><th>" + P.srcBadge("sheet") + "</th><th>Verdict</th></tr></thead><tbody>" + rows + "</tbody></table></div>" : "") +
      (cands && !rec.vodMapId ? '<p class="label u-mt-4">Candidate VOD maps (not linked automatically)</p><ul class="small">' + cands + "</ul>" : "") +
      ((rec.decisions || []).length ? '<p class="label u-mt-4">Reviewer decisions</p><ul class="small">' + rec.decisions.map((d) =>
        "<li>" + esc(d.action) + (d.vodMapId ? " " + esc(d.vodMapId) : "") + " — " + esc(d.by || "?") + ", " + esc(d.at || "") +
        (d.note ? ": " + esc(d.note) : "") + "</li>").join("") + "</ul>" : "") +
      (P.isAdmin() ? '<p class="small u-mt-4"><a href="sheet-sync.html#queue">Review this in Source health →</a></p>' : "") +
      "</section>";
  }

})();
