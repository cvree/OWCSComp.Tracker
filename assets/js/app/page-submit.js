/* =====================================================================
   OWCS Comp Tracker — app/page-submit.js

   The submission flow has exactly one required field and one button.
   Everything else is either autofilled from the link or optional, and
   the advanced block stays shut unless it is opened.

   The link is classified as it is typed. That classification is a
   client-side mirror of `owcs_desktop/intake.classify` — deliberately
   the same accept/reject rules — so the form can tell you what you
   pasted before anything is submitted, and can do it with no server at
   all. When a tracker IS connected, its own classifier is authoritative
   and overwrites the local guess.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const esc = P.esc;
  const $ = (id) => document.getElementById(id);

  const YT_HOSTS = ["youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
    "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com"];
  const FACEIT_HOSTS = ["faceit.com", "www.faceit.com"];
  const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;

  /* ---------------------------------------------------- classification */
  function classify(text) {
    const raw = String(text || "").trim().replace(/^["']|["']$/g, "");
    if (!raw) {
      return { kind: "unknown", accepted: false, label: "nothing pasted",
        reason: "Paste a link to the broadcast — YouTube or FACEIT." };
    }
    if (/^[A-Za-z]:\\/.test(raw) || raw.indexOf("file://") === 0 || raw.charAt(0) === "/") {
      return { kind: "local-file", accepted: true, label: "a video file on this computer",
        detail: "The tracker will read it straight from disk — nothing is downloaded.",
        localOnly: true, path: raw };
    }
    let url;
    try {
      url = new URL(/^https?:\/\//i.test(raw) ? raw : "https://" + raw);
    } catch (e) {
      return { kind: "unknown", accepted: false, label: "not a link",
        reason: "That does not look like a web address. A YouTube link looks like " +
          "https://www.youtube.com/watch?v=…" };
    }
    const host = url.hostname.toLowerCase();
    const parts = url.pathname.split("/").filter(Boolean);

    if (YT_HOSTS.indexOf(host) >= 0) {
      const list = url.searchParams.get("list");
      let vid = url.searchParams.get("v");
      if (!vid && host.indexOf("youtu.be") >= 0) vid = parts[0];
      if (!vid && parts.length >= 2 &&
          ["live", "embed", "shorts", "v"].indexOf(parts[0].toLowerCase()) >= 0) vid = parts[1];
      if (!vid && list) {
        return { kind: "youtube-playlist", accepted: true, label: "a YouTube playlist",
          detail: "Every broadcast in the playlist is queued through the same gate as a " +
            "single link.", playlistId: list };
      }
      if (!vid) {
        return { kind: "unknown", accepted: false, label: "a YouTube page, but not a video",
          reason: "That is a YouTube address without a video in it. Open the broadcast and " +
            "copy the link from the address bar." };
      }
      if (!VIDEO_ID.test(vid)) {
        return { kind: "unknown", accepted: false, label: "a broken YouTube link",
          reason: "“" + vid + "” is not a YouTube video id." };
      }
      return { kind: "youtube-video", accepted: true, label: "a YouTube broadcast",
        detail: "Video " + vid + ". The tracker will download only the part it needs.",
        videoId: vid, canonical: "https://www.youtube.com/watch?v=" + vid };
    }

    if (FACEIT_HOSTS.indexOf(host) >= 0) {
      const isRoom = parts.indexOf("room") >= 0;
      const isChamp = parts.indexOf("championship") >= 0;
      if (isRoom) {
        return { kind: "faceit-match", accepted: true, label: "a FACEIT match room",
          detail: "This supplies match FACTS — teams, maps, scores, bans. It can never " +
            "supply hero compositions; those only come from reading the video." };
      }
      if (isChamp) {
        return { kind: "faceit-championship", accepted: true, label: "a FACEIT championship",
          detail: "Its match list will be pulled in as facts." };
      }
      return { kind: "unknown", accepted: false, label: "a FACEIT page, but not a match",
        reason: "Open the specific match room and copy that link." };
    }

    return { kind: "unknown", accepted: false, label: "an unrecognised site",
      reason: "This tracker reads YouTube broadcasts and FACEIT matches, plus video files " +
        "already on your computer." };
  }

  /* ------------------------------------------------------------ state */
  let verdict = classify("");

  /* --------------------------------------------------------- autofill */
  function fillChoices() {
    const regions = (P.pub && P.pub.regions) || [];
    $("region").innerHTML = '<option value="">Not sure</option>' +
      regions.filter((r) => r.id !== "all")
        .map((r) => '<option value="' + esc(r.id) + '">' + esc(r.name) + "</option>").join("");
    $("team-list").innerHTML = P.teams()
      .map((t) => '<option value="' + esc(t.name) + '">' + esc(t.code || "") + "</option>").join("");
    $("tournament-list").innerHTML = ((P.pub && P.pub.tournaments) || [])
      .map((t) => '<option value="' + esc(t.name) + '"></option>').join("");
  }

  /* Smart autofill: a link we already know about carries its whole
     context with it, so filling the form from the record beats making
     someone retype it. Nothing is invented — only fields the record
     actually has are touched, and the user can edit every one. */
  function autofillFromLink(v) {
    if (!v.videoId) return null;
    const matches = (P.pub && P.pub.matches) || [];
    const hit = matches.filter((m) =>
      String(m.streamUrl || "").indexOf(v.videoId) >= 0)[0];
    if (!hit) return null;
    const t = P.tournament(hit.tournamentId);
    const a = P.team(hit.teamA), b = P.team(hit.teamB);
    if (t && !$("tournament").value) $("tournament").value = t.name;
    if (a && !$("teamA").value) $("teamA").value = a.name;
    if (b && !$("teamB").value) $("teamB").value = b.name;
    if (hit.faceitUrl && !$("faceit").value) $("faceit").value = hit.faceitUrl;
    return hit;
  }

  /* The same idea against the DISCOVERY layer: a link the unattended scan
     already found arrives with an event name and a day read off its own
     title. Only empty fields are filled, and only from what the title
     actually said — a parsed value that is null stays null here too. */
  function autofillFromDiscovery(v) {
    if (!v.videoId) return null;
    const hit = P.discovered().filter((b) => b.videoId === v.videoId)[0];
    if (!hit || hit.state === "published") return null;
    const p = hit.parsed || {};
    if (p.eventName && !$("tournament").value) $("tournament").value = p.eventName;
    if ((p.regions || []).length === 1 && !$("region").value) {
      $("region").value = p.regions[0];
    }
    if (p.fixture && !$("teamA").value && !$("teamB").value) {
      $("teamA").value = p.fixture[0];
      $("teamB").value = p.fixture[1];
    }
    return hit;
  }

  /* ---------------------------------------------------------- verdict */
  function renderVerdict() {
    const box = $("link-verdict");
    const input = $("link");
    if (!input.value.trim()) {
      box.innerHTML = "";
      input.removeAttribute("aria-invalid");
      renderReady();
      return;
    }
    if (verdict.accepted) {
      input.removeAttribute("aria-invalid");
      box.innerHTML = '<p class="ok-note">✓ Recognised ' + esc(verdict.label) + ". " +
        esc(verdict.detail || "") + "</p>";
      const found = autofillFromDiscovery(verdict);
      if (found) {
        box.innerHTML += P.note("info", "The tracker already found this broadcast",
          "<p>It was picked up automatically on " + esc(found.channelTitle ||
            "an official channel") + (found.publishedAt
            ? ", published " + esc(P.fmtDate(found.publishedAt)) : "") +
          ". Nothing has been read from it yet — that is what submitting does.</p>",
          '<a class="btn btn--sm btn--ghost" href="game.html?video=' +
          encodeURIComponent(found.videoId) + '">See what was found</a>');
      }
      const known = autofillFromLink(verdict);
      if (known) {
        box.innerHTML += P.note("info", "This broadcast is already in the record",
          "<p>We filled in what is already known about it. Submitting again re-reads the " +
          "video — useful after a calibration change, harmless otherwise.</p>",
          '<a class="btn btn--sm btn--ghost" href="game.html?id=' + encodeURIComponent(known.id) +
          '">Open the existing game</a>');
      }
    } else {
      input.setAttribute("aria-invalid", "true");
      box.innerHTML = '<p class="err"><span aria-hidden="true">✕</span><span>' +
        esc(verdict.reason) + "</span></p>";
    }
    renderReady();
  }

  function renderReady() {
    const box = $("ready-summary");
    const go = $("go");
    if (!verdict.accepted) {
      go.setAttribute("aria-disabled", "true");
      box.innerHTML = '<p class="dim small">Add a link above and this button turns on.</p>';
      return;
    }
    go.removeAttribute("aria-disabled");
    const bits = [];
    bits.push("Read " + esc(verdict.label));
    const s = $("start").value.trim(), e = $("end").value.trim();
    if (s || e) bits.push("from " + esc(s || "the start") + " to " + esc(e || "the end"));
    else bits.push("across the whole video");
    const teamA = $("teamA").value.trim(), teamB = $("teamB").value.trim();
    if (teamA && teamB) bits.push("labelled " + esc(teamA) + " vs " + esc(teamB));
    box.innerHTML = '<p class="dim">' + bits.join(", ") +
      ", then stop and wait for you to review it.</p>";
  }

  /* ----------------------------------------------------- suggestions
     Broadcasts the tracker already found but nobody has submitted. This
     is the fastest possible path: one click instead of hunting YouTube. */
  function renderSuggestions() {
    const host = $("suggestions");
    const known = new Set();
    ((P.pub && P.pub.matches) || []).forEach((m) => {
      if (m.streamUrl) known.add(m.streamUrl);
    });
    const sources = ((P.work && P.work.videoSources) || [])
      .filter((s) => s.url && s.platform === "youtube" && !known.has(s.url))
      .map((s) => ({ url: s.url, label: s.title || s.id, sub: null }));

    /* The published copy has no working record at all, so on the live
       site the list above is always empty and this box used to disappear
       — the one place a visitor could actually start from was a blank
       field. These come from the unattended scan instead, which means the
       published site can offer real, current OWCS broadcasts. */
    P.discovered().filter((b) => b.state === "found" && b.url &&
      !b.parsed.companion && !known.has(b.url)).forEach((b) => {
      if (sources.some((s) => s.url === b.url)) return;
      const p = b.parsed || {};
      sources.push({
        url: b.url,
        label: p.cleanTitle || b.title || b.videoId,
        sub: [p.eventName, p.day ? "Day " + p.day : null,
          b.publishedAt ? P.fmtDate(b.publishedAt) : null]
          .filter(Boolean).join(" · "),
      });
    });

    const shown = sources.slice(0, 6);
    if (!shown.length) { host.innerHTML = ""; return; }
    host.innerHTML =
      '<p class="label">Broadcasts the tracker already found</p>' +
      '<div class="row u-mt-3">' + shown.map((s) =>
        '<button type="button" class="btn btn--sm btn--ghost btn--trunc" data-url="' +
        esc(s.url) + '" title="' + esc(s.sub || s.label) + '"><span>' +
        esc(s.label) + "</span></button>").join("") + "</div>" +
      '<p class="dim small u-mt-3">Found automatically on verified official channels. ' +
      'Nothing has been read from them — picking one starts that.</p>';
    host.addEventListener("click", (e) => {
      const b = e.target.closest("[data-url]");
      if (!b) return;
      $("link").value = b.dataset.url;
      verdict = classify(b.dataset.url);
      renderVerdict();
      $("link").focus();
    });
  }

  /* ------------------------------------------------------------ submit */
  function payload() {
    return {
      url: verdict.canonical || $("link").value.trim(),
      kind: verdict.kind,
      tournament: $("tournament").value.trim() || null,
      region: $("region").value || null,
      teamA: $("teamA").value.trim() || null,
      teamB: $("teamB").value.trim() || null,
      faceitUrl: $("faceit").value.trim() || null,
      start: $("start").value.trim() || null,
      end: $("end").value.trim() || null,
      every: $("every").value ? Number($("every").value) : null,
      lowres: $("lowres").checked,
    };
  }

  async function onSubmit(e) {
    e.preventDefault();
    if (!verdict.accepted) { $("link").focus(); return; }
    const out = $("submit-result");
    const go = $("go");
    const body = payload();

    go.setAttribute("aria-disabled", "true");
    out.innerHTML = '<div class="row"><span class="spinner"></span>' +
      '<span class="dim">Sending it to the tracker…</span></div>';

    await P.api.probe();
    if (!P.api.isConnected()) {
      go.removeAttribute("aria-disabled");
      handoff(body);
      return;
    }

    const res = await P.api.submit(body.url, P.api.reviewer());
    if (!res || res.ok === false) {
      go.removeAttribute("aria-disabled");
      out.innerHTML = P.note("error", "The tracker refused this submission",
        "<p>" + esc((res && res.error) || "No reason was given.") + "</p>",
        '<button class="btn btn--sm" type="submit" form="submit-form">Try again</button>');
      return;
    }
    out.innerHTML = '<div class="row"><span class="spinner"></span>' +
      '<span class="dim">Accepted. Working out what it is…</span></div>';
    pollTask(out, body);
  }

  function pollTask(out, body) {
    let tries = 0;
    const tick = async () => {
      const snap = await P.api.task();
      tries++;
      if (!snap || snap.offline) {
        out.innerHTML = P.note("warn", "Lost contact with the tracker",
          "<p>The submission may still have gone through. Check the games list.</p>",
          '<a class="btn btn--sm" href="games.html">Open games</a>');
        return;
      }
      if (snap.running && tries < 120) { setTimeout(tick, 1200); return; }
      if (snap.error) {
        out.innerHTML = P.note("error", "Processing could not start",
          "<p>" + esc(snap.error) + "</p>",
          '<a class="btn btn--sm" href="tools.html">Open diagnostics</a>');
        return;
      }
      const r = snap.result || {};
      const n = r.accepted != null ? r.accepted : (r.queued != null ? r.queued : null);
      out.innerHTML = P.note("ok", "Submitted",
        "<p>" + (n != null ? esc(n) + " game(s) entered the pipeline. " : "") +
        "You can watch it work, and it will stop and ask you to review what it read.</p>",
        '<a class="btn btn--sm btn--primary" href="games.html?state=working">Watch progress</a>' +
        '<button class="btn btn--sm btn--ghost" id="submit-another">Submit another</button>');
      wireAnother();
      if (body) P.api.clearPending(body.url);
    };
    setTimeout(tick, 900);
  }

  /* The read-only path. A submission is never silently dropped: it is
     kept on this device and turned into the exact command that does the
     work on a machine that can. */
  function handoff(body) {
    const entry = Object.assign({ id: body.url }, body);
    P.api.savePending(entry);
    const cmd = P.api.handoffCommand(entry);
    $("submit-result").innerHTML = P.note("info", "Saved — but nothing here can process video",
      "<p>This page is the published copy of the tracker; there is no machine behind it to " +
      "download a video. Your submission is kept in this browser, and this is the exact " +
      "command that runs it on a machine that has the tracker installed:</p>" +
      '<pre class="console u-mt-3" style="max-height:none">' + esc(cmd) + "</pre>",
      '<button class="btn btn--sm btn--primary" data-copy="' + esc(cmd) + '">Copy the command</button>' +
      '<a class="btn btn--sm btn--ghost" href="how-it-works.html#run-it-yourself">How to install it</a>');
    renderPending();
    $("submit-result").addEventListener("click", (e) => {
      const b = e.target.closest("[data-copy]");
      if (b) P.copy(b.dataset.copy, "Command copied");
    });
  }

  function wireAnother() {
    const b = document.getElementById("submit-another");
    if (!b) return;
    b.addEventListener("click", () => {
      $("submit-form").reset();
      verdict = classify("");
      renderVerdict();
      $("submit-result").innerHTML = "";
      $("link").focus();
    });
  }

  function renderPending() {
    const band = $("pending-band");
    const list = P.api.pending();
    if (!list.length) { band.hidden = true; return; }
    band.hidden = false;
    $("pending").innerHTML = list.map((e) => {
      const cmd = P.api.handoffCommand(e);
      return '<div class="card"><div class="u-flex u-between u-center u-gap-3 u-wrap">' +
        '<div class="u-trunc"><strong>' + esc(e.teamA && e.teamB
          ? e.teamA + " vs " + e.teamB : e.url) + "</strong>" +
        '<div class="dim small">saved ' + esc(P.fmtRel(e.savedAt)) + "</div></div>" +
        '<div class="row"><button class="btn btn--sm" data-copy="' + esc(cmd) + '">Copy command</button>' +
        '<button class="btn btn--sm btn--quiet" data-drop="' + esc(e.id) + '">Remove</button></div>' +
        "</div></div>";
    }).join("");
  }

  /* -------------------------------------------------------------- boot */
  document.addEventListener("DOMContentLoaded", () => {
    P.api.mountStatus("mode-status", {
      readonlyTitle: "You can prepare a submission here, but not run it",
      readonlyBody: "<p>This is the published copy of the tracker — a static site with no " +
        "machine behind it, so it cannot download or read a video. Fill the form in anyway: " +
        "it will hand you the exact command to run on a machine that has the tracker " +
        "installed, with everything you entered already filled in.</p>",
    });

    fillChoices();
    renderSuggestions();
    renderVerdict();
    renderPending();

    /* Prefill from a link handed over by another page (a blocked game's
       "try a different window", the dashboard's empty state). */
    const pre = P.qs("url");
    if (pre) { $("link").value = pre; verdict = classify(pre); }

    let debounce = null;
    $("link").addEventListener("input", () => {
      verdict = classify($("link").value);
      clearTimeout(debounce);
      debounce = setTimeout(() => {
        renderVerdict();
        /* When a tracker is connected its classifier is the real one. */
        if (P.api.isConnected() && $("link").value.trim()) {
          P.api.classify($("link").value).then((r) => {
            if (!r || r.kind === undefined) return;
            if (r.input && r.input !== $("link").value.trim().replace(/^["']|["']$/g, "")) return;
            verdict = Object.assign({}, verdict, r);
            renderVerdict();
          });
        }
      }, 220);
    });
    ["start", "end", "teamA", "teamB"].forEach((id) =>
      $(id).addEventListener("input", renderReady));

    $("submit-form").addEventListener("submit", onSubmit);
    $("pending").addEventListener("click", (e) => {
      const c = e.target.closest("[data-copy]");
      if (c) { P.copy(c.dataset.copy, "Command copied"); return; }
      const d = e.target.closest("[data-drop]");
      if (d) { P.api.clearPending(d.dataset.drop); renderPending(); }
    });

    renderVerdict();
    $("link").focus({ preventScroll: true });
  });
})();
