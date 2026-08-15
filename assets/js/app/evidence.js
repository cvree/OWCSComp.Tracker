/* =====================================================================
   OWCS Comp Tracker — app/evidence.js
   The evidence viewer, shared by the game page and the review workspace.

   Evidence is the reason this project exists: every published fact can
   show the frame it was read from. So the viewer is a first-class shared
   component rather than something each page reinvents — and it always
   shows the file path, because "trust me" is not evidence.

   What the old viewer could not do, and why that mattered: a broadcast
   frame is 1280x720 and the thing being judged is a 40px hero portrait
   inside it. Shown fit-to-window, the one detail the viewer exists to
   settle was smaller on screen than the thumbnail that opened it. So the
   image now zooms and pans (Panzoom, MIT, github.com/timmywil/panzoom),
   double-click snaps to 3x under the pointer, and the whole set of
   evidence on the page is navigable with ← → so an operator comparing a
   before/after swap never has to close and reopen.

   Everything degrades. Without Panzoom the viewer still opens, still
   shows the frame, still shows the path; only the zoom is missing.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const esc = P.esc;

  let box, img, cap, stage, lastFocus = null, pz = null;
  let group = [], at = -1;

  const ZOOM_MIN = 1;
  const ZOOM_MAX = 8;

  /* ------------------------------------------------------------ chrome
     The dialog is built here rather than being repeated in the markup of
     every page that can show evidence. Pages that ship the old inline
     `#lightbox` markup keep working: it is adopted and upgraded in place.
  */
  function ensure() {
    if (box) return true;
    box = document.getElementById("lightbox");
    if (!box) {
      box = document.createElement("div");
      box.className = "lightbox";
      box.id = "lightbox";
      box.hidden = true;
      document.body.appendChild(box);
    }
    box.innerHTML =
      '<div class="lightbox__scrim" data-close></div>' +
      '<figure class="lightbox__box" role="dialog" aria-modal="true" aria-label="Evidence frame">' +
        '<div class="lightbox__bar">' +
          '<figcaption id="lightbox-cap" class="lightbox__cap"></figcaption>' +
          '<div class="lightbox__tools">' +
            '<span class="lightbox__count" id="lightbox-count" aria-live="polite"></span>' +
            '<button type="button" class="icon-btn" data-zoom="out" aria-label="Zoom out">&minus;</button>' +
            '<span class="lightbox__level" id="lightbox-level">100%</span>' +
            '<button type="button" class="icon-btn" data-zoom="in" aria-label="Zoom in">+</button>' +
            '<button type="button" class="icon-btn" data-zoom="reset" aria-label="Fit to window">Fit</button>' +
            '<button type="button" class="icon-btn" data-close aria-label="Close">&times;</button>' +
          "</div>" +
        "</div>" +
        '<div class="lightbox__stage" id="lightbox-stage">' +
          '<img id="lightbox-img" src="" alt="" draggable="false">' +
        "</div>" +
        '<div class="lightbox__foot">' +
          '<button type="button" class="btn btn--sm btn--quiet" data-step="-1">' +
            '<span aria-hidden="true">←</span> Previous</button>' +
          '<code class="lightbox__path" id="lightbox-path"></code>' +
          '<button type="button" class="btn btn--sm btn--quiet" data-step="1">' +
            'Next <span aria-hidden="true">→</span></button>' +
        "</div>" +
      "</figure>";

    img = box.querySelector("#lightbox-img");
    cap = box.querySelector("#lightbox-cap");
    stage = box.querySelector("#lightbox-stage");

    box.addEventListener("click", (e) => {
      const t = e.target;
      if (t.closest("[data-close]")) return P.evidence.close();
      const z = t.closest("[data-zoom]");
      if (z) return zoomBy(z.dataset.zoom);
      const s = t.closest("[data-step]");
      if (s) return step(Number(s.dataset.step));
    });
    stage.addEventListener("dblclick", (e) => {
      if (!pz) return;
      const cur = pz.getScale();
      if (cur > 1.2) pz.reset();
      else pz.zoomToPoint(3, e);
      syncLevel();
    });
    document.addEventListener("keydown", (e) => {
      if (!box || box.hidden) return;
      if (e.key === "Escape") { e.preventDefault(); P.evidence.close(); }
      else if (e.key === "ArrowRight") { e.preventDefault(); step(1); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); step(-1); }
      else if (e.key === "+" || e.key === "=") { e.preventDefault(); zoomBy("in"); }
      else if (e.key === "-") { e.preventDefault(); zoomBy("out"); }
      else if (e.key === "0") { e.preventDefault(); zoomBy("reset"); }
    });
    return true;
  }

  /* --------------------------------------------------------------- zoom */
  function attachZoom() {
    detachZoom();
    if (typeof window.Panzoom !== "function" || !img) return;
    try {
      pz = window.Panzoom(img, {
        maxScale: ZOOM_MAX,
        minScale: ZOOM_MIN,
        contain: "outside",
        cursor: "grab",
        step: 0.4,
      });
      stage.addEventListener("wheel", onWheel, { passive: false });
      img.addEventListener("panzoomchange", syncLevel);
    } catch (err) {
      pz = null;
    }
    syncLevel();
  }
  function detachZoom() {
    if (!pz) return;
    try {
      stage.removeEventListener("wheel", onWheel);
      img.removeEventListener("panzoomchange", syncLevel);
      pz.destroy();
    } catch (err) { /* already gone */ }
    pz = null;
  }
  function onWheel(e) {
    if (!pz) return;
    e.preventDefault();
    pz.zoomWithWheel(e);
    syncLevel();
  }
  function zoomBy(kind) {
    if (!pz) return;
    if (kind === "in") pz.zoomIn();
    else if (kind === "out") pz.zoomOut();
    else pz.reset();
    syncLevel();
  }
  function syncLevel() {
    const el = box && box.querySelector("#lightbox-level");
    if (!el) return;
    const scale = pz ? pz.getScale() : 1;
    el.textContent = Math.round(scale * 100) + "%";
    el.hidden = !pz;
    box.querySelectorAll("[data-zoom]").forEach((b) => { b.disabled = !pz; });
  }

  /* --------------------------------------------------------- the group
     Every piece of evidence currently on the page, in document order, so
     ← → walk the same sequence the reader sees. Rebuilt on open: pages
     re-render, and a stale list would step to something that is gone. */
  function collect(path) {
    group = P.$$("[data-evidence]").map((el) => ({
      path: el.dataset.evidence,
      cap: el.dataset.evidenceCap || "",
    })).filter((e, i, all) =>
      e.path && all.findIndex((o) => o.path === e.path && o.cap === e.cap) === i);
    at = group.findIndex((e) => e.path === path);
    if (at < 0) {
      group.unshift({ path: path, cap: "" });
      at = 0;
    }
  }

  function show(i) {
    if (!group.length) return;
    at = (i + group.length) % group.length;
    const e = group[at];
    detachZoom();
    img.removeAttribute("style");
    img.src = e.path;
    img.alt = e.cap || "Evidence frame";
    cap.textContent = e.cap || "Evidence frame";
    box.querySelector("#lightbox-path").textContent = e.path;
    const count = box.querySelector("#lightbox-count");
    count.textContent = group.length > 1 ? at + 1 + " of " + group.length : "";
    box.querySelectorAll("[data-step]").forEach((b) => { b.hidden = group.length < 2; });
    if (img.complete) attachZoom();
    else img.addEventListener("load", attachZoom, { once: true });
  }
  function step(d) {
    if (group.length > 1) show(at + d);
  }

  P.evidence = {
    /* `path` is repo-relative, exactly as the record stores it. */
    open: function (path, caption) {
      if (!ensure() || !path) return;
      lastFocus = document.activeElement;
      collect(path);
      if (caption && group[at]) group[at].cap = caption;
      show(at);
      box.hidden = false;
      document.documentElement.classList.add("lightbox-open");
      const btn = box.querySelector('[data-close].icon-btn');
      if (btn) btn.focus();
    },
    close: function () {
      if (!box || box.hidden) return;
      detachZoom();
      box.hidden = true;
      document.documentElement.classList.remove("lightbox-open");
      img.src = "";
      if (lastFocus && lastFocus.focus) lastFocus.focus();
    },
    /* A clickable evidence thumbnail. Missing evidence renders as a
       labelled gap, never as a broken image or a silent blank. */
    thumb: function (path, caption) {
      if (!path) {
        return '<span class="slot__evidence" aria-hidden="true">' +
          '<span class="none">no crop<br>saved</span></span>';
      }
      return '<button type="button" class="slot__evidence" data-evidence="' + esc(path) +
        '" data-evidence-cap="' + esc(caption || "") + '" ' +
        'title="Open the frame this was read from — zoom with the wheel">' +
        '<img src="' + esc(path) + '" alt="' + esc(caption || "Evidence crop") +
        '" loading="lazy" decoding="async"></button>';
    },
    /* One delegated listener per container. */
    wire: function (root) {
      const host = root || document;
      if (host.dataset && host.dataset.evidenceWired) return;
      if (host.dataset) host.dataset.evidenceWired = "1";
      host.addEventListener("click", (e) => {
        const b = e.target.closest("[data-evidence]");
        if (!b) return;
        P.evidence.open(b.dataset.evidence, b.dataset.evidenceCap);
      });
    },
  };
})();
