/* =====================================================================
   OWCS Comp Tracker — app/evidence.js
   The evidence lightbox, shared by the game page and the review
   workspace.

   Evidence is the reason this project exists: every published fact can
   show the crop it was read from. So the viewer is a first-class shared
   component rather than something each page reinvents — and it always
   shows the file path, because "trust me" is not evidence.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;

  let box, img, cap, lastFocus = null;

  function ensure() {
    box = document.getElementById("lightbox");
    if (!box) return false;
    img = document.getElementById("lightbox-img");
    cap = document.getElementById("lightbox-cap");
    if (box.dataset.wired) return true;
    box.dataset.wired = "1";
    box.addEventListener("click", (e) => {
      if (e.target.closest("[data-close]")) P.evidence.close();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !box.hidden) P.evidence.close();
    });
    return true;
  }

  P.evidence = {
    /* `path` is repo-relative, exactly as the record stores it. */
    open: function (path, caption) {
      if (!ensure() || !path) return;
      lastFocus = document.activeElement;
      img.src = path;
      img.alt = caption || "Evidence crop";
      cap.textContent = caption ? caption + " — " + path : path;
      box.hidden = false;
      const btn = box.querySelector("[data-close].btn");
      if (btn) btn.focus();
    },
    close: function () {
      if (!box) return;
      box.hidden = true;
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
      return '<button type="button" class="slot__evidence" data-evidence="' + P.esc(path) +
        '" data-evidence-cap="' + P.esc(caption || "") + '" ' +
        'title="Open the frame this was read from">' +
        '<img src="' + P.esc(path) + '" alt="' + P.esc(caption || "Evidence crop") +
        '" loading="lazy" decoding="async"></button>';
    },
    /* One delegated listener per container. */
    wire: function (root) {
      (root || document).addEventListener("click", (e) => {
        const b = e.target.closest("[data-evidence]");
        if (!b) return;
        P.evidence.open(b.dataset.evidence, b.dataset.evidenceCap);
      });
    },
  };
})();
