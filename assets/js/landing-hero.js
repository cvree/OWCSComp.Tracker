/* =====================================================================
   OWCS Comp Tracker — landing-hero.js
   The homepage detection story, animated. Six beats, all real data:
     1. the broadcast frame enters          (frame_2394.jpg, Nepal 39:54)
     2. ten portrait slots are located      (the autocalibrated rects)
     3. heroes are identified               (brackets lock emerald)
     4. a swap is suspected                 (slot a5 flares amber)
     5. evidence is verified                (Juno→Lúcio crops, conf 0.817)
     6. the composition goes public         (verdict seal resolves)

   Rules: GSAP only (no CSS transitions racing it), plays once, ~6s,
   never blocks clicks. Reduced motion, small screens, or a missing GSAP
   all get the complete static final state — the CSS default.
   ===================================================================== */
(function () {
  "use strict";
  const reduced =
    window.matchMedia("(prefers-reduced-motion: reduce)").matches ||
    (navigator.connection && navigator.connection.saveData === true);

  const stage = document.querySelector("[data-det]");
  if (!stage) return;
  const $ = (s) => stage.querySelector(s);
  const $$ = (s) => Array.from(stage.querySelectorAll(s));

  const frame = $("[data-det-frame]");
  const scan = $("[data-det-scan]");
  const slots = $$("[data-det-slot]");
  const steps = $$("[data-det-step]");
  const verdict = $("[data-det-verdict]");
  const before = $("[data-det-before]");
  const after = $("[data-det-after]");
  const arrow = $("[data-det-arrow]");
  const seal = $("[data-det-seal]");
  const conf = $("[data-det-conf]");
  const ring = $("[data-det-ring]");

  /* static final state (also the no-JS/no-GSAP baseline) */
  const finishStatic = () => {
    slots.forEach((s) => s.classList.add("is-locked"));
    steps.forEach((s) => s.classList.add("is-done"));
  };

  if (reduced || !window.gsap || innerWidth < 760) { finishStatic(); return; }
  const gsap = window.gsap;

  /* idle ambience: the arcane ring turns very slowly, forever */
  if (ring) gsap.to(ring, { rotation: 360, duration: 90, repeat: -1, ease: "none", transformOrigin: "50% 50%" });

  const stepOn = (i) => () => {
    steps.forEach((s, j) => {
      s.classList.toggle("is-done", j < i);
      s.classList.toggle("is-on", j === i);
    });
  };
  const allDone = () => steps.forEach((s) => { s.classList.remove("is-on"); s.classList.add("is-done"); });

  gsap.set([verdict], { autoAlpha: 0, y: 14 });
  gsap.set(slots, { autoAlpha: 0, scale: 0.55, transformOrigin: "50% 50%" });
  gsap.set([before, after], { autoAlpha: 0, scale: 0.8 });
  gsap.set(arrow, { scaleX: 0, transformOrigin: "0 50%" });
  gsap.set(seal, { autoAlpha: 0, scale: 0.4 });

  const tl = gsap.timeline({ defaults: { ease: "power3.out" }, delay: 0.35 });

  /* 1 — the frame enters */
  tl.call(stepOn(0))
    .from(frame, { autoAlpha: 0, scale: 1.045, duration: 0.9, ease: "power2.out" })
    .to(scan, { opacity: 1, duration: 0.18 }, "-=0.3")
    .fromTo(scan, { top: "-34%" }, { top: "100%", duration: 0.9, ease: "power1.inOut" }, "<")
    .to(scan, { opacity: 0, duration: 0.25 }, "-=0.2");

  /* 2 — ten slots located */
  tl.call(stepOn(1), null, "-=0.55")
    .to(slots, { autoAlpha: 1, scale: 1, duration: 0.4, stagger: 0.07 }, "-=0.45");

  /* 3 — heroes identified: brackets lock */
  tl.call(stepOn(2))
    .call(() => slots.forEach((s) => s.classList.add("is-locked")), null, "+=0.25");

  /* 4 — a swap is suspected in slot a5 */
  tl.call(stepOn(3), null, "+=0.3")
    .to(slots[4], { scale: 1.5, duration: 0.22, ease: "power2.out" })
    .to(slots[4], { scale: 1, duration: 0.35, ease: "power2.inOut" });

  /* 5 — evidence verified: the before/after crops + consensus */
  tl.call(stepOn(4))
    .to(verdict, { autoAlpha: 1, y: 0, duration: 0.5 })
    .to(before, { autoAlpha: 1, scale: 1, duration: 0.32 }, "-=0.2")
    .to(arrow, { scaleX: 1, duration: 0.4, ease: "power2.inOut" })
    .to(after, { autoAlpha: 1, scale: 1, duration: 0.32 });
  if (conf) {
    const target = parseFloat(conf.textContent) || 0.817;
    const obj = { v: 0 };
    tl.to(obj, {
      v: target, duration: 0.7, ease: "power2.out",
      onUpdate: () => { conf.textContent = obj.v.toFixed(3); },
      onComplete: () => { conf.textContent = target.toFixed(3); },
    }, "-=0.5");
  }

  /* 6 — published: the seal resolves with a precision pulse */
  tl.call(stepOn(5))
    .to(seal, { autoAlpha: 1, scale: 1.12, duration: 0.3, ease: "back.out(2.5)" })
    .to(seal, { scale: 1, duration: 0.25, ease: "power2.inOut" })
    .call(allDone);

  /* pointer parallax: restrained depth on fine pointers only */
  if (window.matchMedia("(hover: hover) and (pointer: fine)").matches) {
    const fx = gsap.quickTo(frame, "x", { duration: 0.6, ease: "power3" });
    const fy = gsap.quickTo(frame, "y", { duration: 0.6, ease: "power3" });
    stage.addEventListener("pointermove", (e) => {
      const r = stage.getBoundingClientRect();
      fx(((e.clientX - r.left) / r.width - 0.5) * 8);
      fy(((e.clientY - r.top) / r.height - 0.5) * 5);
    }, { passive: true });
    stage.addEventListener("pointerleave", () => { fx(0); fy(0); });
  }
})();
