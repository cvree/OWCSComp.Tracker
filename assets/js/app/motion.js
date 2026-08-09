/* =====================================================================
   OWCS Comp Tracker — app/motion.js
   A deliberately small motion engine.

   What survived the redesign, and why:
     flow      Lenis smooth scroll — the single biggest feel improvement
               on a long page, and the one effect users notice by its
               absence. ONE instance, driven by GSAP's ticker so there is
               never a second rAF loop fighting it.
     reveals   Content rises in once as it enters view. Orientation, not
               decoration — and it can NEVER withhold content (see below).
     progress  A 2px hairline showing position on long pages.
     countUp   Numbers on the dashboard settle rather than snap.

   What was removed, and why:
     Vanta/three.js  616 KB of WebGL for a background texture. It cost
                     more than everything else on the page combined and
                     added nothing a static gradient does not.
     decrypt text    Headings that scramble themselves are movement where
                     a reader is trying to read.
     magnetic / tilt / spotlight
                     Cursor gimmicks that make targets move away from the
                     pointer. Actively worse for aiming at a button.

   Hard rules: prefers-reduced-motion or Save-Data disables everything;
   no layer may throw; the page is complete with this file absent.
   ===================================================================== */
(function () {
  "use strict";

  /* matchMedia is optional in the platform — absent in jsdom, in some
     embedded webviews and in several headless renderers. Calling it
     unguarded at module top level throws before anything below runs,
     which takes the whole file down over a progressive-enhancement
     query. This file loads with `defer` and may run before core.js has
     defined P.media, so it carries its own copy of the guard. */
  const mq = (q) => {
    try { return !!(window.matchMedia && window.matchMedia(q).matches); }
    catch (e) { return false; }
  };
  const reduced =
    mq("(prefers-reduced-motion: reduce)") ||
    (navigator.connection && navigator.connection.saveData === true);
  const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));

  const M = (window.OWCSMotion = { reduced: reduced, booted: false, lenis: null });

  function safely(name, fn) {
    try { return fn(); } catch (err) {
      if (window.console) console.warn("[motion] " + name + " skipped:", err);
      return undefined;
    }
  }

  /* ---------------------------------------------------- flow (Lenis) */
  function initFlow() {
    if (reduced || typeof window.Lenis !== "function" || M.lenis) return;
    const lenis = new window.Lenis({
      lerp: 0.13,
      wheelMultiplier: 1,
      touchMultiplier: 1.4,
      smoothWheel: true,
      syncTouch: false,        // native touch scrolling already feels best
    });
    M.lenis = lenis;

    /* Anything that scrolls inside itself keeps native wheel behaviour —
       a console, a review queue, a hero picker. Smooth-scrolling those
       from the page scroller is what makes inner regions feel broken. */
    $$(".console, .rw__queue, .picker__grid, .table-wrap, [data-scroll-region]")
      .forEach((el) => el.setAttribute("data-lenis-prevent", ""));

    if (window.gsap && window.ScrollTrigger) {
      lenis.on("scroll", window.ScrollTrigger.update);
      window.gsap.ticker.add((t) => lenis.raf(t * 1000));
      window.gsap.ticker.lagSmoothing(0);
      window.addEventListener("load", () => window.ScrollTrigger.refresh());
    } else {
      const loop = (t) => { lenis.raf(t); requestAnimationFrame(loop); };
      requestAnimationFrame(loop);
    }

    /* In-page anchors go through Lenis, offset by whatever sticky chrome
       this page actually has, measured at click time. */
    const stickyOffset = () => {
      let h = 0;
      $$(".hdr").forEach((el) => {
        if (getComputedStyle(el).position !== "sticky") return;
        h += el.getBoundingClientRect().height;
      });
      return (h || 56) + 16;
    };
    document.addEventListener("click", (e) => {
      const a = e.target.closest && e.target.closest('a[href^="#"]');
      if (!a || a.getAttribute("href") === "#") return;
      const target = document.getElementById(a.getAttribute("href").slice(1));
      if (!target) return;
      e.preventDefault();
      lenis.scrollTo(target, { offset: -stickyOffset() });
      history.replaceState(null, "", a.getAttribute("href"));
    });

    /* A keyboard focus that lands off-screen must scroll into view: Lenis
       owns the scroller, so the browser's own scroll-into-view is bypassed
       and a tabbed-to control would otherwise be focused but invisible.

       `:focus-visible` is the discriminator, and it is load-bearing. A
       PROGRAMMATIC `.focus({preventScroll:true})` — which is how a screen
       that re-renders puts the cursor back where the operator left it —
       does not match it, so this no longer yanks the page somewhere the
       caller explicitly asked it not to go. Without the guard, the review
       workspace jumped a thousand pixels after every decision. */
    document.addEventListener("focusin", (e) => {
      const el = e.target;
      if (!el || !el.getBoundingClientRect) return;
      try { if (!el.matches(":focus-visible")) return; } catch (err) { return; }
      const r = el.getBoundingClientRect();
      if (r.top >= stickyOffset() && r.bottom <= window.innerHeight) return;
      lenis.scrollTo(el, { offset: -stickyOffset() - 24, immediate: true });
    });
  }

  /* ------------------------------------------------------- progress */
  function initProgress() {
    if (reduced) return;
    if (document.body.scrollHeight < window.innerHeight * 1.8) return;
    const bar = document.createElement("div");
    bar.className = "scroll-progress";
    bar.setAttribute("aria-hidden", "true");
    document.body.appendChild(bar);
    let ticking = false;
    const update = () => {
      ticking = false;
      const max = document.documentElement.scrollHeight - window.innerHeight;
      bar.style.width = max > 0 ? ((window.scrollY / max) * 100).toFixed(2) + "%" : "0";
    };
    addEventListener("scroll", () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(update);
    }, { passive: true });
    update();
  }

  /* -------------------------------------------------------- countUp */
  M.countUp = function (el) {
    const target = parseFloat(el.dataset.countTo || el.textContent) || 0;
    const suffix = el.dataset.countSuffix || "";
    const settle = () => { el.textContent = (target % 1 ? target.toFixed(1) : target) + suffix; };
    if (reduced || !window.gsap) { settle(); return; }
    const obj = { v: 0 };
    window.gsap.to(obj, {
      v: target, duration: 0.8, ease: "power2.out",
      onUpdate: () => {
        el.textContent = (target % 1 ? obj.v.toFixed(1) : Math.round(obj.v)) + suffix;
      },
      onComplete: settle,
    });
  };

  /* ---------------------------------------------------------- boot */
  M.boot = function () {
    if (M.booted) return;
    M.booted = true;
    safely("flow", initFlow);
    safely("progress", initProgress);
    safely("counts", () => $$("[data-count-to]").forEach(M.countUp));
  };
})();
