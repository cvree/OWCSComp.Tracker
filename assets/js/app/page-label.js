/* =====================================================================
   OWCS Comp Tracker — app/page-label.js
   Name the heroes: the human half of teaching the tracker a hero.

   WHAT A CARD IS
   --------------
   pipeline/label_batch.py cut every hero portrait out of every gameplay
   frame of a broadcast and grouped the ones that look alike. A card is one
   group: the same hero, in the same look (alive, dead-grey, ult flash),
   seen hundreds of times. On the Nepal map, 3,070 portraits became 18
   cards. Naming those 18 teaches the tracker every hero on that map.

   WHY NOTHING HERE GUESSES
   ------------------------
   Official hero art and other broadcasts' reference images were both
   measured as suggestions against real cards, and both were wrong every
   time — a broadcast portrait is its own rendering. So the page offers no
   guesses. What it does offer is what the TIMELINE proves: a card that
   only ever appears in one slot while another card's hero is playing there
   is that hero's grey or ult portrait. That link is shown, and accepting
   it is one click — but it is the person's click.

   WHERE THE LABELS GO
   -------------------
   Nowhere live. A static page has no server and no business holding a
   key. The labels leave as `labelling/<batch>/labels.json` — committed on
   GitHub in two clicks from here, or downloaded — and the label-forge
   workflow builds reference images from them, tests them on frames they
   were not cut from, and keeps only the heroes that pass. Git is the
   audit trail, exactly as corrections/corrections.json is for review.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  if (!P) return;
  const esc = P.esc;
  const $ = (id) => document.getElementById(id);
  const DATA = window.OWCS_LABELLING || { batches: [] };
  const SKIP = "skip";
  const ROLES = ["Tank", "Damage", "Support"];

  /* ------------------------------------------------------ persistence */
  /* A draft survives a reload. Storage can be absent or throw (private
     windows, blocked site data), and the page works the same without it. */
  const store = {
    get(key) { try { return JSON.parse(window.localStorage.getItem(key) || "null"); } catch (e) { return null; } },
    set(key, v) { try { window.localStorage.setItem(key, JSON.stringify(v)); } catch (e) { /* not kept */ } },
    drop(key) { try { window.localStorage.removeItem(key); } catch (e) { /* nothing */ } },
  };

  /* Where "commit on GitHub" points. On a github.io copy the owner and repo
     are in the address; anywhere else, the project's own repository. */
  function repoSlug() {
    const host = location.hostname || "";
    const seg = (location.pathname || "").split("/").filter(Boolean)[0];
    if (/\.github\.io$/.test(host) && seg) return host.split(".")[0] + "/" + seg;
    return "cvree/OWCSComp.Tracker";
  }

  const batches = DATA.batches || [];
  const wanted = P.qs("batch");
  const batch = wanted ? batches.find((b) => b.batchId === wanted) : null;

  /* --------------------------------------------------------- the list */
  function renderList() {
    if (!batches.length) {
      P.mount("label-app", P.empty("◇", "Nothing to name yet",
        "A batch appears here when a broadcast has been cut into cards. " +
        "Run <code>python3 pipeline/label_batch.py --url &lt;broadcast&gt; --layout " +
        "&lt;layout&gt;</code>, or start the <b>label-harvest</b> workflow on GitHub."));
      return;
    }
    P.mount("label-app",
      '<div class="lb-batches">' + batches.map((b) => {
        const named = Object.keys(b.labels || {}).filter((k) => b.labels[k] && b.labels[k] !== SKIP).length;
        const total = (b.cards || []).length;
        const faces = (b.cards || []).slice(0, 8).map((c) =>
          '<img class="lb-batch__face" src="labelling/' + esc(b.batchId) + "/" + esc(c.image) +
          '" alt="" width="44" height="44" loading="lazy">').join("");
        const chip = b.state === "forged"
          ? P.stateChip("published", "Learned")
          : b.state === "labelled" ? P.stateChip("working", "Named — waiting to be learned")
          : P.stateChip("review", "Needs names");
        return '<a class="lb-batch card card--hoverable" href="label.html?batch=' +
          encodeURIComponent(b.batchId) + '">' +
          '<div class="lb-batch__faces">' + faces + "</div>" +
          '<div class="lb-batch__body">' +
            '<div class="u-flex u-between u-center u-gap-3 u-wrap">' + chip +
              '<span class="dim small mono">' + esc(P.fmtDate(b.createdAt)) + "</span></div>" +
            "<h2>" + esc(b.title || b.batchId) + "</h2>" +
            '<p class="dim small">' + total + " cards · " + named + " named" +
              ((b.promoted || []).length ? " · " + b.promoted.length + " heroes learned" : "") + "</p>" +
            progressBar(named, total) +
          "</div></a>";
      }).join("") + "</div>");
  }

  function progressBar(n, total) {
    const pct = total ? Math.round((n / total) * 100) : 0;
    return '<div class="lb-progress" role="progressbar" aria-valuemin="0" aria-valuemax="' + total +
      '" aria-valuenow="' + n + '" aria-label="' + n + " of " + total + ' cards named">' +
      '<span class="lb-progress__fill" style="width:' + pct + '%"></span></div>';
  }

  if (!batch) {
    if (wanted) P.toast("No batch called “" + wanted + "” — showing every batch.", "error");
    renderList();
    return;
  }

  /* ------------------------------------------------------ batch state */
  const KEY = "owcs.labels." + batch.batchId;
  const draft = store.get(KEY) || {};
  const labels = Object.assign({}, batch.labels || {}, draft.labels || {});
  let signedAs = draft.signedAs || "";
  /* The person's double-check of heroes the forge measured at or below 90%. */
  const approved = new Set(draft.approved || batch.approved || []);
  const rejected = new Set(draft.rejected || batch.rejected || []);
  const review = batch.review || {};
  /* Labels already committed for this batch: updating means editing that file. */
  const committed = Object.keys(batch.labels || {}).length > 0;
  const cards = batch.cards || [];
  const byId = {};
  cards.forEach((c, i) => { byId[c.id] = c; c.n = i + 1; });
  let focusIdx = 0;

  /* ------------------------------------------------------- role lock */
  /* OWCS broadcasts print each team in a fixed role order (on these
     packages: damage, damage, tank, support, support). A card's slot
     therefore says what role its hero plays — the picker leads with that
     role, and a name of another role is flagged before it is committed. */
  const ROLE_OF = { tank: "Tank", damage: "Damage", support: "Support" };
  const slotRoles = (batch.slotRoles || []).map((r) => ROLE_OF[r] || null);
  const roleOfSlot = (slot) => slotRoles.length ? slotRoles[Number(String(slot).slice(-1)) - 1] || null : null;
  function cardRole(c) {
    if (!c || !slotRoles.length) return null;
    const tally = {};
    Object.keys(c.slots || {}).forEach((k) => {
      const r = roleOfSlot(k);
      if (r) tally[r] = (tally[r] || 0) + ((c.slots[k] || {}).n || 0);
    });
    const best = Object.keys(tally).sort((a, b) => tally[b] - tally[a])[0];
    return best || null;
  }
  const roleClash = (c) => {
    const h = heroOf(c);
    const want = cardRole(c);
    return h && want && P.hero(h).role && P.hero(h).role !== want ? want : null;
  };

  function save() {
    store.set(KEY, { labels: labels, signedAs: signedAs,
      approved: Array.from(approved), rejected: Array.from(rejected) });
  }
  const heroOf = (c) => labels[c.id] && labels[c.id] !== SKIP ? labels[c.id] : null;
  const namedCount = () => cards.filter((c) => labels[c.id]).length;

  /* ----------------------------------------------- the line-up check */
  /* Which card holds each of the ten slots most of the time. With names
     filled in, this IS the line-up the tracker would read — so a person
     sees at once whether a team has come out with three tanks. */
  function lineup() {
    const rows = { a: [], b: [] };
    ["a", "b"].forEach((side) => {
      for (let i = 1; i <= 5; i++) {
        const slot = side + i;
        let best = null;
        cards.forEach((c) => {
          if (c.nestedIn) return;
          const s = (c.slots || {})[slot];
          if (s && (!best || s.n > best.s.n)) best = { c: c, s: s };
        });
        rows[side].push({ slot: slot, card: best && best.c });
      }
    });
    return rows;
  }

  function renderLineup() {
    const rows = lineup();
    const side = (key, label) => {
      const heroes = rows[key].map((r) => r.card && heroOf(r.card)).filter(Boolean);
      const roles = {};
      heroes.forEach((h) => { const r = P.hero(h).role; roles[r] = (roles[r] || 0) + 1; });
      const full = heroes.length === 5;
      const ok = full && roles.Tank === 1 && roles.Damage === 2 && roles.Support === 2;
      const verdict = !full ? '<span class="dim small">' + heroes.length + " of 5 named</span>"
        : ok ? '<span class="lb-verdict lb-verdict--ok">✓ 1 tank · 2 damage · 2 support</span>'
        : '<span class="lb-verdict lb-verdict--warn">⚠ ' + ROLES.map((r) => (roles[r] || 0) + " " + r.toLowerCase()).join(" · ") +
          " — check a name</span>";
      return '<div class="lb-lineup__side"><div class="u-flex u-between u-center u-gap-3">' +
        '<span class="tag">' + label + "</span>" + verdict + "</div>" +
        '<div class="lb-lineup__slots">' + rows[key].map((r) => {
          const h = r.card && heroOf(r.card);
          const want = roleOfSlot(r.slot);
          const clash = h && want && P.hero(h).role !== want;
          return '<button type="button" class="lb-slot' + (clash ? " lb-slot--warn" : "") + '" data-card="' +
            esc(r.card ? r.card.id : "") + '"' + (r.card ? "" : " disabled") +
            ' title="Slot ' + esc(r.slot) + (want ? " · the " + want.toLowerCase() + " slot" : "") +
            (clash ? " — " + esc(P.hero(h).name) + " is not a " + want.toLowerCase() : "") + '">' +
            (h ? P.heroTile(h, { size: "sm" })
              : '<span class="lb-slot__q">' + (r.card ? "#" + r.card.n : "—") + "</span>") +
            "</button>";
        }).join("") + "</div></div>";
    };
    return '<div class="lb-lineup card">' +
      '<div class="card-head"><h2>The line-up you are building</h2>' +
      '<span class="dim small">the card that holds each slot longest</span></div>' +
      '<div class="lb-lineup__body">' + side("a", "Left team") + side("b", "Right team") + "</div></div>";
  }

  /* -------------------------------------------------------- the cards */
  function cardHtml(c, i) {
    const h = heroOf(c);
    const skipped = labels[c.id] === SKIP;
    const parent = c.nestedIn ? byId[c.nestedIn] : null;
    const parentHero = parent && heroOf(parent);
    const slots = Object.keys(c.slots || {});
    const where = c.side === "both" ? "both teams" : (c.side === "a" ? "left team" : "right team");
    const samples = (c.samples || []).map((s) =>
      '<img src="labelling/' + esc(batch.batchId) + "/" + esc(s.image) + '" alt="" width="35" height="35"' +
      ' loading="lazy" title="' + esc(s.slot + " at " + P.fmtClock(s.t)) + '">').join("");
    const link = parent && !h && !skipped
      ? '<div class="lb-card__link">' +
          '<span>Same hero as <b>#' + parent.n + "</b>" + (parentHero ? " (" + esc(P.hero(parentHero).name) + ")" : "") +
          " — its grey or ult portrait, by the timeline.</span>" +
          (parentHero ? '<button type="button" class="btn btn--sm btn--good" data-accept="' + esc(c.id) +
            '">Yes, ' + esc(P.hero(parentHero).name) + "</button>" : "") +
        "</div>" : "";
    return '<article class="lb-card' + (h ? " is-named" : "") + (skipped ? " is-skipped" : "") +
      (i === focusIdx ? " is-focus" : "") + '" data-card="' + esc(c.id) + '" tabindex="-1"' +
      (h ? ' data-role="' + esc(P.hero(h).role || "") + '"' : "") + ">" +
      '<div class="lb-card__top">' +
        '<span class="lb-card__n mono">#' + c.n + "</span>" +
        '<img class="lb-card__face" src="labelling/' + esc(batch.batchId) + "/" + esc(c.image) +
          '" alt="Portrait card ' + c.n + '" width="112" height="112">' +
        '<div class="lb-card__answer">' +
          (h ? P.heroTile(h) : skipped ? '<span class="dim">Skipped — not a hero portrait</span>'
            : '<span class="lb-card__ask">Who is this?</span>') +
        "</div>" +
      "</div>" +
      '<div class="lb-card__samples" aria-label="The same portrait at other moments">' + samples + "</div>" +
      '<p class="lb-card__meta dim small">Seen <b>' + c.size + "</b>× · " + esc(where) +
        " · " + esc(slots.join(", ")) + " · " + esc(P.fmtClock(c.tFirst)) + "–" + esc(P.fmtClock(c.tLast)) + "</p>" +
      link +
      (roleClash(c) ? '<p class="lb-card__warn small">⚠ This portrait sits in the ' +
        esc(roleClash(c).toLowerCase()) + " slot, and " + esc(P.hero(heroOf(c)).name) + " is " +
        esc(String(P.hero(heroOf(c)).role || "").toLowerCase()) + ". Check the name.</p>" : "") +
      '<div class="lb-card__actions">' +
        '<button type="button" class="btn btn--sm ' + (h ? "btn--ghost" : "btn--primary") +
          '" data-name="' + esc(c.id) + '">' + (h ? "Change" : "Name it") + "</button>" +
        '<button type="button" class="btn btn--sm btn--quiet" data-skip="' + esc(c.id) + '">' +
          (skipped ? "Un-skip" : "Not a hero") + "</button>" +
      "</div></article>";
  }

  function verdictsHtml() {
    const v = batch.verdicts || {};
    const ids = Object.keys(v);
    if (!ids.length) return "";
    const loo = batch.leaveOneOut;
    return '<div class="card lb-learned"><div class="card-head"><h2>What the tracker learned</h2>' +
      (loo ? '<span class="dim small">' + esc(loo.note || "") + "</span>" : "") + "</div>" +
      '<div class="lb-learned__grid">' + ids.sort().map((id) => {
        const kept = v[id] === "VALIDATED";
        const ask = v[id] === "REVIEW";
        return '<div class="lb-learned__hero' + (kept ? " is-kept" : "") + (ask ? " is-ask" : "") + '">' +
          P.heroTile(id, { size: "sm" }) + '<span class="small">' +
          (kept ? "✓ learned" : ask ? (approved.has(id) ? "✓ you approved — next run" : "? double-check below")
            : "✗ not yet — " + esc(String(v[id]).toLowerCase())) + "</span></div>";
      }).join("") + "</div></div>";
  }

  /* ---------------------------------------------- the double-check */
  /* The owner's rule: above 90% the tracker learns a hero on its own; at
     or below, a person looks first. Each hero here shows how it measured
     on frames it was never taught from, and every card it was taught from —
     a wrong card is the usual reason, and it is visible at a glance. */
  function reviewHtml() {
    const ids = Object.keys(review).sort();
    if (!ids.length) return "";
    return '<div class="card lb-review"><div class="card-head"><h2>Double-check ' + ids.length +
      " hero" + (ids.length === 1 ? "" : "es") + "</h2>" +
      '<span class="dim small">measured at 90% or less — look, then approve or flag</span></div>' +
      '<div class="lb-review__list">' + ids.map((id) => {
        const r = review[id];
        const pct = r.trials ? Math.round((r.correct / r.trials) * 100) : 0;
        const wrong = Object.keys(r.confusedWith || {}).map((h) =>
          r.confusedWith[h] + "× as " + esc(P.hero(h).name)).join(", ");
        const thumbs = (r.cards || []).map((cid) => byId[cid]).filter(Boolean).map((c) =>
          '<button type="button" class="lb-review__card" data-card="' + esc(c.id) + '" title="Card #' + c.n + '">' +
          '<img src="labelling/' + esc(batch.batchId) + "/" + esc(c.image) + '" alt="Card ' + c.n +
          '" width="56" height="56" loading="lazy"></button>').join("");
        const state = approved.has(id) ? "approved" : rejected.has(id) ? "rejected" : "";
        return '<div class="lb-review__hero' + (state ? " is-" + state : "") + '">' +
          '<div class="lb-review__who">' + P.heroTile(id) +
            '<p class="small"><b>' + pct + "%</b> right on " + r.trials + " unseen frames · " +
              r.unknown + " unsure" + (wrong ? " · <span class=\"lb-review__wrong\">read " + wrong + "</span>" : "") + "</p></div>" +
          '<div class="lb-review__cards" aria-label="Cards named ' + esc(P.hero(id).name) + '">' + thumbs + "</div>" +
          '<div class="lb-review__act">' +
            '<button type="button" class="btn btn--sm ' + (state === "approved" ? "btn--good" : "btn--ghost") +
              '" data-approve="' + esc(id) + '" aria-pressed="' + (state === "approved") + '">✓ All ' +
              esc(P.hero(id).name) + "</button>" +
            '<button type="button" class="btn btn--sm ' + (state === "rejected" ? "btn--danger" : "btn--quiet") +
              '" data-reject="' + esc(id) + '" aria-pressed="' + (state === "rejected") + '">Something’s off</button>' +
          "</div></div>";
      }).join("") + "</div>" +
      '<p class="dim small">Approving tells the tracker these cards really are that hero; it still has to pass ' +
      "the safety checks before it is used. Something’s off? Open a card and fix its name.</p></div>";
  }

  function labelsJson() {
    const out = {};
    cards.forEach((c) => { if (labels[c.id]) out[c.id] = labels[c.id]; });
    const doc = {
      schema: "label-labels.v1",
      batchId: batch.batchId,
      labelledBy: signedAs || null,
      labelledAt: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
      labels: out,
    };
    if (approved.size) doc.approved = Array.from(approved).sort();
    if (rejected.size) doc.rejected = Array.from(rejected).sort();
    return JSON.stringify(doc, null, 1) + "\n";
  }

  function render() {
    const n = namedCount();
    const total = cards.length;
    const url = batch.url
      ? ' · <a href="' + esc(batch.url) + '" target="_blank" rel="noopener">the broadcast ↗</a>' : "";
    $("label-app").innerHTML =
      '<p class="dim small"><a href="label.html">Every batch</a> · ' + esc(batch.batchId) + url + "</p>" +
      '<h2 class="lb-title">' + esc(batch.title || batch.batchId) + "</h2>" +
      '<div class="lb-bar card">' +
        '<div class="lb-bar__count"><span class="lb-bar__big">' + n + '</span><span class="dim"> / ' + total +
          " cards named</span></div>" + progressBar(n, total) +
        '<div class="u-flex u-gap-2 u-wrap u-center">' +
          '<label class="visually-hidden" for="signed-as">Your name</label>' +
          '<input class="input lb-bar__who" id="signed-as" type="text" autocomplete="name" placeholder="your name" value="' + esc(signedAs) + '">' +
          '<a class="btn btn--primary" id="commit" target="_blank" rel="noopener">' +
            (committed ? "Copy &amp; update on GitHub ↗" : "Commit on GitHub ↗") + "</a>" +
          '<button type="button" class="btn btn--ghost" id="download">Download</button>' +
          '<button type="button" class="btn btn--quiet" id="copy">Copy</button>' +
        "</div>" +
        (committed ? '<p class="dim small lb-bar__how">This batch’s labels are already on GitHub, so the button ' +
          "copies yours and opens that file to edit. There: click in the text, press <kbd>Ctrl</kbd>+<kbd>A</kbd>, " +
          "then <kbd>Ctrl</kbd>+<kbd>V</kbd>, then <b>Commit changes</b>.</p>" : "") +
        "</div>" +
      verdictsHtml() +
      reviewHtml() +
      renderLineup() +
      '<p class="dim small u-mt-5">Keys: <kbd>j</kbd>/<kbd>k</kbd> move · ' +
        '<kbd>Enter</kbd> name · <kbd>y</kbd> accept the linked name · ' +
        '<kbd>x</kbd> not a hero</p>' +
      '<div class="lb-grid">' + cards.map(cardHtml).join("") + "</div>";
    updateCommitLink();
  }

  function updateCommitLink() {
    const a = $("commit");
    if (!a) return;
    const path = "labelling/" + batch.batchId + "/labels.json";
    /* GitHub will not create a file that already exists, so a batch whose
       labels were committed before (every one with a Double-check) is
       updated through the edit page — the labels go on the clipboard. */
    if (committed) {
      a.href = "https://github.com/" + repoSlug() + "/edit/main/" + path;
      a.title = "Copies your labels and opens " + path + " on GitHub — select all, paste, Commit changes.";
      return;
    }
    a.href = "https://github.com/" + repoSlug() + "/new/main?filename=" +
      encodeURIComponent(path) + "&value=" + encodeURIComponent(labelsJson());
    a.title = "Opens GitHub with " + path + " filled in — press “Commit changes” there.";
  }

  function refresh(focusId) {
    save();
    const y = window.scrollY;
    render();
    window.scrollTo(0, y);
    if (focusId) {
      const el = document.querySelector('.lb-card[data-card="' + focusId + '"]');
      if (el) el.focus({ preventScroll: true });
    }
  }

  function setLabel(id, hero) {
    labels[id] = hero;
    /* Name a card, and every card the timeline ties to it that is still
       unnamed now SHOWS the suggestion — it is not applied for you. */
    refresh(id);
  }

  /* ----------------------------------------------------- hero picker */
  let pickerFor = null;
  let pickerActive = 0;
  let pickerList = [];

  let pickerRole = null;
  function openPicker(id) {
    pickerFor = id;
    const c = byId[id];
    pickerRole = cardRole(c);
    $("picker-context").innerHTML =
      '<img class="lb-card__face lb-card__face--sm" src="labelling/' + esc(batch.batchId) + "/" + esc(c.image) +
      '" alt="" width="64" height="64">' +
      '<span class="dim small">Card #' + c.n + " · seen " + c.size + "× · " +
      esc(Object.keys(c.slots || {}).join(", ")) +
      (pickerRole ? " · <b>" + esc(pickerRole.toLowerCase()) + " slot</b> — " +
        esc(pickerRole.toLowerCase()) + " heroes first" : "") + "</span>";
    $("picker-q").value = "";
    renderPickerGrid("");
    $("picker-modal").hidden = false;
    $("picker-q").focus();
  }
  function closePicker() {
    const id = pickerFor;
    $("picker-modal").hidden = true;
    pickerFor = null;
    const el = id && document.querySelector('.lb-card[data-card="' + id + '"]');
    if (el) el.focus({ preventScroll: true });
  }
  function renderPickerGrid(q) {
    const query = String(q || "").trim().toLowerCase();
    /* The slot's role leads; the others follow, never hidden — a stray
       portrait in the wrong slot still has to be nameable. */
    const rank = (h) => (pickerRole && h.role === pickerRole ? -1 : ROLES.indexOf(h.role));
    pickerList = P.heroes().slice().sort((a, b) =>
      rank(a) - rank(b) || a.name.localeCompare(b.name))
      .filter((h) => !query || h.name.toLowerCase().indexOf(query) >= 0 || h.id.indexOf(query) >= 0);
    pickerActive = 0;
    const lead = pickerRole ? pickerList.filter((h) => h.role === pickerRole).length : 0;
    $("picker-grid").innerHTML = pickerList.length
      ? pickerList.map((h, i) =>
        (lead && i === lead ? '<p class="picker__divider dim small">Other roles</p>' : "") +
        '<button type="button" class="picker__opt" role="option" data-pick="' + esc(h.id) +
        '" data-active="' + (i === 0) + '" aria-selected="' + (i === 0) + '">' +
        P.heroTile(h.id, { size: "sm" }) + "</button>").join("")
      : '<p class="dim small">No hero matches “' + esc(q) + "”.</p>";
  }
  function pick(heroId) {
    const id = pickerFor;
    $("picker-modal").hidden = true;
    pickerFor = null;
    if (id) setLabel(id, heroId);
  }

  /* --------------------------------------------------------- wiring */
  function moveFocus(step) {
    focusIdx = Math.max(0, Math.min(cards.length - 1, focusIdx + step));
    P.$$(".lb-card").forEach((el, i) => el.classList.toggle("is-focus", i === focusIdx));
    const el = P.$$(".lb-card")[focusIdx];
    if (el) { el.focus({ preventScroll: true }); el.scrollIntoView({ block: "nearest" }); }
  }

  function wire() {
    $("label-app").addEventListener("click", (e) => {
      const t = e.target.closest("[data-name],[data-skip],[data-accept],[data-approve],[data-reject],[data-card],#download,#copy,#commit");
      if (!t) return;
      if (t.id === "commit") {
        /* the link still opens; on an existing file the labels must travel by clipboard */
        if (committed) P.copy(labelsJson(), "Labels copied — paste them over the file on GitHub");
        return;
      }
      if (t.dataset.approve || t.dataset.reject) {
        const id = t.dataset.approve || t.dataset.reject;
        const into = t.dataset.approve ? approved : rejected;
        const other = t.dataset.approve ? rejected : approved;
        if (into.has(id)) into.delete(id); else { into.add(id); other.delete(id); }
        refresh();
        return;
      }
      if (t.id === "download") {
        P.download("labels.json", labelsJson());
        return;
      }
      if (t.id === "copy") { P.copy(labelsJson(), "labels.json copied"); return; }
      if (t.dataset.name) { openPicker(t.dataset.name); return; }
      if (t.dataset.skip) {
        const id = t.dataset.skip;
        if (labels[id] === SKIP) delete labels[id]; else labels[id] = SKIP;
        refresh(id);
        return;
      }
      if (t.dataset.accept) {
        const c = byId[t.dataset.accept];
        const parentHero = c && c.nestedIn && heroOf(byId[c.nestedIn]);
        if (parentHero) setLabel(c.id, parentHero);
        return;
      }
      if ((t.classList.contains("lb-slot") || t.classList.contains("lb-review__card")) && t.dataset.card) {
        const el = document.querySelector('.lb-card[data-card="' + t.dataset.card + '"]');
        if (el) { el.scrollIntoView({ block: "center", behavior: "smooth" }); el.focus({ preventScroll: true }); }
      }
    });
    $("label-app").addEventListener("input", (e) => {
      if (e.target.id === "signed-as") { signedAs = e.target.value.trim(); save(); updateCommitLink(); }
    });

    const modal = $("picker-modal");
    modal.addEventListener("click", (e) => {
      if (e.target.closest("[data-close]")) { closePicker(); return; }
      const opt = e.target.closest("[data-pick]");
      if (opt) pick(opt.dataset.pick);
    });
    $("picker-q").addEventListener("input", (e) => renderPickerGrid(e.target.value));
    $("picker-q").addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); closePicker(); return; }
      if (e.key === "Enter") {
        e.preventDefault();
        const h = pickerList[pickerActive];
        if (h) pick(h.id);
        return;
      }
      const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 6, ArrowUp: -6 }[e.key];
      if (step) {
        e.preventDefault();
        pickerActive = Math.max(0, Math.min(pickerList.length - 1, pickerActive + step));
        P.$$("[data-pick]", $("picker-grid")).forEach((el, i) => {
          el.dataset.active = i === pickerActive;
          el.setAttribute("aria-selected", i === pickerActive);
          if (i === pickerActive) el.scrollIntoView({ block: "nearest" });
        });
      }
    });

    document.addEventListener("keydown", (e) => {
      if (!$("picker-modal").hidden) return;
      if (e.target.closest && e.target.closest("input, textarea, select")) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const c = cards[focusIdx];
      if (e.key === "j") { e.preventDefault(); moveFocus(1); }
      else if (e.key === "k") { e.preventDefault(); moveFocus(-1); }
      else if (e.key === "Enter" && c && e.target.closest && e.target.closest(".lb-card")) {
        e.preventDefault(); openPicker(c.id);
      } else if (e.key === "x" && c) {
        e.preventDefault();
        labels[c.id] = labels[c.id] === SKIP ? undefined : SKIP;
        if (!labels[c.id]) delete labels[c.id];
        refresh(c.id);
      } else if (e.key === "y" && c && c.nestedIn) {
        const ph = heroOf(byId[c.nestedIn]);
        if (ph) { e.preventDefault(); setLabel(c.id, ph); moveFocus(1); }
      }
    });
  }

  render();
  wire();
})();
