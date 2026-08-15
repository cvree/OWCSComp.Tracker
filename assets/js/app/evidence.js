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

  /* --------------------------------------------------------------- zoom
     The two things this viewer opens are wildly different sizes: a 1280x720
     broadcast frame, and a 35x35 hero crop cut out of one. Left at their
     natural size the first overflows and the second is a speck, so the
     image is fitted to the stage on load — scaled DOWN for the frame and
     UP for the crop — and that fitted size is what 100% means.

     Fitting first is also what makes the zoom work at all. Panzoom's
     `contain: "outside"` keeps the element covering its parent, which for
     a letterboxed image means forcing the scale up until it does: opening
     a 35px crop pinned it at the 800% ceiling, and "Fit" could not bring
     it back because 800% *was* the contained minimum. With the element
     already fitted there is nothing to contain, so scale 1 is a real
     resting state again. */
  function fitToStage() {
    if (!img || !img.naturalWidth || !stage) return 1;
    const box = stage.getBoundingClientRect();
    const pad = 24;
    const availW = Math.max(80, box.width - pad);
    const availH = Math.max(80, box.height - pad);
    const factor = Math.min(availW / img.naturalWidth, availH / img.naturalHeight);
    img.style.width = Math.round(img.naturalWidth * factor) + "px";
    img.style.height = Math.round(img.naturalHeight * factor) + "px";
    /* Past 2x there are no real pixels left to interpolate. A reviewer
       judging a portrait wants to see the pixels the detector saw, not a
       smoothed guess at them. */
    img.style.imageRendering = factor >= 2 ? "pixelated" : "";
    return factor;
  }

  function attachZoom() {
    detachZoom();
    if (!img) return;
    fitToStage();
    if (typeof window.Panzoom !== "function") { syncLevel(); return; }
    try {
      pz = window.Panzoom(img, {
        maxScale: ZOOM_MAX,
        minScale: ZOOM_MIN,
        contain: false,
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
    /* Keyed on the FILE, not on the button. Both teams' opening line-ups
       are read off the same broadcast frame, so the page carries two
       anchors pointing at one image — and deduplicating on path+caption
       kept both, which made "next" appear to do nothing: it stepped to a
       different caption for the same picture. One frame is one stop, and
       the captions of everything read from it are joined, which is also
       the more honest label for what that frame shows. */
    const byPath = new Map();
    P.$$("[data-evidence]").forEach((el) => {
      const p = el.dataset.evidence;
      if (!p) return;
      const cap = el.dataset.evidenceCap || "";
      if (!byPath.has(p)) byPath.set(p, { path: p, caps: [] });
      const entry = byPath.get(p);
      if (cap && entry.caps.indexOf(cap) < 0) entry.caps.push(cap);
    });
    group = Array.from(byPath.values()).map((e) => ({
      path: e.path, cap: e.caps.join(" · "),
    }));
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
    img.removeAttribute("style");  /* drop the previous image's fitted size */
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
    /* Whether the viewer currently owns the keyboard.

       Callers must ask through this rather than reading `#lightbox`'s
       `hidden`: the dialog is built on first use now, so before anyone has
       opened a frame there is no element to read, and a page that pokes at
       the id directly throws on every keypress. The review workspace's
       whole keyboard died that way. */
    isOpen: function () {
      return !!(box && !box.hidden);
    },
    /* `path` is repo-relative, exactly as the record stores it. */
    open: function (path, caption) {
      if (!ensure() || !path) return;
      lastFocus = document.activeElement;
      collect(path);
      /* Only fills a gap — a caller opening a path that is not on the page
         has no anchor to have been collected from. It never overwrites the
         merged caption, which names everything read off that frame rather
         than only the one button that was clicked. */
      if (caption && group[at] && !group[at].cap) group[at].cap = caption;
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
