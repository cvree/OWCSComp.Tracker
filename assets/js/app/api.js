/* =====================================================================
   OWCS Comp Tracker — app/api.js
   The connection layer, and the honesty layer.

   This product runs in two places and they can do different things:

     CONNECTED — the page is being served by the tracker itself
                 (pipeline/serve.py, or the desktop app). Downloading a
                 VOD, running detection and recording a review decision
                 all really happen.

     READ-ONLY — the page is the published copy on a static host. There
                 is no machine behind it. It can show everything that has
                 already been reviewed and published, and it can prepare
                 work for a connected machine to pick up. It cannot
                 process video, and it will say so rather than pretending.

   Everything below returns a plain object — a network failure becomes
   {ok:false, error:"…"} rather than a rejected promise, because every
   caller wants to render the failure, not crash on it.
   ===================================================================== */
(function () {
  "use strict";

  const P = window.OWCS;
  const A = (P.api = {});

  const TIMEOUT = 8000;
  let mode = "unknown";               // unknown | connected | readonly
  let probing = null;
  const listeners = [];

  A.mode = () => mode;
  A.isConnected = () => mode === "connected";
  A.onMode = (fn) => { listeners.push(fn); if (mode !== "unknown") fn(mode); };
  const setMode = (m) => {
    if (m === mode) return;
    mode = m;
    document.documentElement.dataset.owcsMode = m;
    listeners.forEach((fn) => { try { fn(m); } catch (e) { /* a listener must not break the probe */ } });
  };

  async function request(path, options) {
    const ctrl = typeof AbortController === "function" ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), (options && options.timeout) || TIMEOUT) : null;
    try {
      const res = await fetch(path, Object.assign({
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        signal: ctrl ? ctrl.signal : undefined,
      }, options || {}));
      /* A static host answers every unknown path with an HTML 404 page.
         Parsing that as JSON is the signal that nothing is listening. */
      const ct = res.headers.get("content-type") || "";
      if (ct.indexOf("json") < 0) {
        return { ok: false, offline: true,
          error: "No tracker is running behind this page." };
      }
      let body = null;
      try { body = await res.json(); } catch (e) { body = null; }
      if (body === null)
        return { ok: false, error: "The tracker returned an unreadable response (HTTP " + res.status + ")." };
      if (!res.ok && body.error === undefined) body.error = "HTTP " + res.status;
      if (!res.ok && body.ok === undefined) body.ok = false;
      return body;
    } catch (err) {
      return { ok: false, offline: true,
        error: (err && err.name === "AbortError")
          ? "The tracker did not answer in time."
          : "No tracker is running behind this page." };
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  A.get = (path) => request(path);
  A.post = (path, payload) =>
    request(path, { method: "POST", body: JSON.stringify(payload || {}) });

  /* ---------------------------------------------------------- probing */
  A.probe = function () {
    if (probing) return probing;
    probing = request("/api/ping", { timeout: 3500 }).then((r) => {
      setMode(r && r.ok ? "connected" : "readonly");
      return mode;
    });
    return probing;
  };

  /* ------------------------------------------------------ the verbs
     Each one is named for what a person is doing, not for the route. */
  A.overview = () => A.get("/api/desktop/overview");
  A.health = () => A.get("/api/desktop/health");
  A.queue = () => A.get("/api/desktop/queue");
  A.reviewInbox = () => A.get("/api/desktop/review");
  A.reviewDecide = (kind, id, decision, reviewer) =>
    A.post("/api/desktop/review/decide",
      { kind: kind, id: id, decision: decision, reviewer: reviewer });
  A.classify = (text) =>
    A.get("/api/desktop/intake/classify?q=" + encodeURIComponent(text || ""));
  A.submit = (input, requestedBy) =>
    A.post("/api/desktop/intake/submit", { input: input, requestedBy: requestedBy });
  A.task = () => A.get("/api/desktop/task");
  A.publishStatus = () => A.get("/api/desktop/publish");
  A.exportPublic = () => A.post("/api/desktop/publish/export", {});

  /* the pipeline's own run API (serve.py), used by the processing view */
  A.sources = () => A.get("/api/sources");
  A.preflight = (source) => A.get("/api/preflight" + (source ? "?source=" + encodeURIComponent(source) : ""));
  A.runStatus = (since) => A.get("/api/status?since=" + (since || 0));
  A.startRun = (payload) => A.post("/api/run", payload || {});
  A.cancelRun = () => A.post("/api/cancel", {});
  A.matchfinder = () => A.get("/api/matchfinder");

  /* -------------------------------------------------------- reviewer id
     Not authentication. The record wants a name against every decision;
     an unsigned decision is still recorded, as "anonymous". */
  const KEY = "owcs.reviewer";
  A.reviewer = () => {
    try { return localStorage.getItem(KEY) || ""; } catch (e) { return ""; }
  };
  A.setReviewer = (name) => {
    try { localStorage.setItem(KEY, String(name || "").slice(0, 120)); } catch (e) { /* private mode */ }
  };

  /* ---------------------------------------------- the read-only handoff
     A submission made on the published copy is not thrown away. It is
     kept in this browser and handed to a connected machine as an exact
     command, so nothing is lost and nothing is faked. */
  const QKEY = "owcs.pending";
  A.pending = function () {
    try { return JSON.parse(localStorage.getItem(QKEY) || "[]"); } catch (e) { return []; }
  };
  A.savePending = function (entry) {
    const list = A.pending();
    list.unshift(Object.assign({ savedAt: new Date().toISOString() }, entry));
    try { localStorage.setItem(QKEY, JSON.stringify(list.slice(0, 40))); } catch (e) { /* full/blocked */ }
    return list;
  };
  A.clearPending = function (id) {
    const list = A.pending().filter((e) => e.id !== id);
    try { localStorage.setItem(QKEY, JSON.stringify(list)); } catch (e) { /* ignore */ }
    return list;
  };

  /* The exact thing to paste on a machine that can do the work. */
  A.handoffCommand = function (entry) {
    if (!entry || !entry.url) return "python3 pipeline/run_owcs_auto.py --help";
    const parts = ["python3", "pipeline/run_owcs_auto.py", "--url", '"' + entry.url + '"'];
    if (entry.start) parts.push("--start", entry.start);
    if (entry.end) parts.push("--end", entry.end);
    if (entry.every) parts.push("--every", String(entry.every));
    return parts.join(" ");
  };

  /* ------------------------------------------------- connection banner
     One component, mounted by any page that offers to DO something. It
     never blocks the page: the page renders, then this fills in. */
  A.mountStatus = function (elId, opts) {
    opts = opts || {};
    const el = document.getElementById(elId);
    if (!el) return;
    el.innerHTML = '<div class="note" data-kind="info"><svg class="note__ic" viewBox="0 0 24 24" ' +
      'aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" ' +
      'stroke-width="2"/></svg><div><strong>Checking for a tracker…</strong></div></div>';
    A.probe().then(() => {
      if (A.isConnected()) {
        el.innerHTML = P.note("ok", "Connected to your tracker",
          "<p>Submitting a game here really downloads and processes it, and review " +
          "decisions are written to the record straight away.</p>");
        return;
      }
      el.innerHTML = P.note("info", opts.readonlyTitle || "You are on the published copy",
        opts.readonlyBody ||
        "<p>This page is a static publication of games that have already been reviewed. " +
        "There is no machine behind it, so it cannot download or process video. " +
        "You can still browse everything published, and prepare a submission for a " +
        "machine that can run it.</p>",
        '<a class="btn btn--sm" href="how-it-works.html#run-it-yourself">How to run your own</a>');
    });
  };
})();
