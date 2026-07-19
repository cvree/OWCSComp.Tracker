/* =====================================================================
   OWCS Comp Tracker — motion.js
   One motion engine for both shells (control room + public site).

   Layers (each independent, each optional, each with a fallback):
     flow       Lenis smooth scroll, synced to GSAP's ticker + ScrollTrigger
     entrance   one fast page-load timeline: header -> headline -> panels
     reveals    below-the-fold cards rise in once (ScrollTrigger.batch ->
                IntersectionObserver -> instant, whichever is available)
     decrypt    eyebrow/kicker labels resolve from scrambled glyphs
                (broadcast-terminal feel; fires once per label, in view)
     physics    magnetic primary buttons, ~1.6deg card tilt, spotlight
                tracking (fine-pointer devices only)
     progress   2px scroll-progress hairline on long pages
     ambience   Vanta NET tactical grid (three.js/WebGL) behind the page,
                falling back to the lightweight 2D canvas net, falling
                back to the static CSS gradient

   Hard rules: prefers-reduced-motion or Save-Data disables everything
   except static styling; no layer may throw (every boot step is wrapped);
   pages must remain fully readable with this file absent.
   ===================================================================== */
(function () {
  "use strict";

  const reduced =
    window.matchMedia("(prefers-reduced-motion: reduce)").matches ||
    (navigator.connection && navigator.connection.saveData === true);
  const finePointer =
    window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  const M = (window.OWCSMotion = {
    reduced,
    booted: false,
    lenis: null,
    vantaFx: null,
  });

  function safely(name, fn) {
    try { return fn(); } catch (err) {
      if (window.console) console.warn("[motion] " + name + " skipped:", err);
      return undefined;
    }
  }

  /* ---- flow: Lenis + GSAP ticker + ScrollTrigger sync ----------------
     `lerp` (continuous exponential smoothing, frame-rate independent)
     instead of `duration` (a fixed-length eased tween per wheel gesture).
     Duration-mode replays a full tween on every wheel tick, so rapid or
     repeated scrolling queues/overlaps tweens and feels laggy and
     rubber-bandy; lerp mode just keeps chasing the latest target every
     frame, which reads as both snappier (higher lerp = catches up faster)
     and smoother (no per-gesture animation to restart or collide with). */
  function initFlow() {
    if (reduced || typeof window.Lenis !== "function") return;
    const lenis = new window.Lenis({
      lerp: 0.13,
      wheelMultiplier: 1,
      touchMultiplier: 1.4,
      smoothWheel: true,
      syncTouch: false,      // native touch scroll already feels best
    });
    M.lenis = lenis;
    // inner scroll regions keep native wheel behavior
    $$(".console-body, [data-scroll-region]").forEach((el) =>
      el.setAttribute("data-lenis-prevent", ""));
    if (window.gsap && window.ScrollTrigger) {
      lenis.on("scroll", window.ScrollTrigger.update);
      window.gsap.ticker.add((t) => lenis.raf(t * 1000));
      window.gsap.ticker.lagSmoothing(0);
    } else {
      const loop = (t) => { lenis.raf(t); requestAnimationFrame(loop); };
      requestAnimationFrame(loop);
    }
  }

  /* ---- entrance: one page-load timeline ------------------------------ */
  function initEntrance() {
    if (reduced || !window.gsap) return;
    const header = $$(".nav, .pub-header, .demo-ribbon");
    // elements inside a .rv block belong to the shell's reveal system —
    // animating them twice would fight it
    const hero = $$(".page-head > *, .pub-hero > *, [data-hero] > *")
      .filter((el) => !el.closest(".rv"));
    if (!header.length && !hero.length) return;
    const tl = window.gsap.timeline({
      defaults: { ease: "power3.out", clearProps: "transform,opacity,visibility" },
    });
    if (header.length)
      tl.from(header, { y: -14, autoAlpha: 0, duration: 0.4, stagger: 0.05 });
    if (hero.length)
      tl.from(hero, { y: 16, autoAlpha: 0, duration: 0.5, stagger: 0.06 }, "-=0.15");
  }

  /* ---- reveals: cards rise in once ----------------------------------- */
  function revealBatch(els) {
    if (window.gsap) {
      window.gsap.to(els, {
        y: 0, autoAlpha: 1, duration: 0.5, ease: "power3.out",
        stagger: 0.06, overwrite: true,
        onComplete: () => els.forEach((el) => el.classList.add("m-in")),
      });
    } else {
      els.forEach((el) => { el.classList.add("m-in"); el.style.cssText = ""; });
    }
  }

  let revealIO = null;
  function watchReveal(els) {
    const fresh = els.filter((el) => !el.dataset.mRv);
    if (!fresh.length) return;
    fresh.forEach((el) => { el.dataset.mRv = "1"; });
    if (reduced || !window.gsap) return; // visible as-is
    // only elements below the first viewport animate on scroll; the rest
    // are part of the entrance and stay put
    const below = fresh.filter(
      (el) => el.getBoundingClientRect().top > innerHeight * 0.92);
    below.forEach((el) => window.gsap.set(el, { y: 16, autoAlpha: 0 }));
    if (!below.length) return;
    if (window.ScrollTrigger) {
      window.ScrollTrigger.batch(below, {
        start: "top 92%", once: true,
        onEnter: (batch) => revealBatch(batch),
      });
      return;
    }
    if (!revealIO && "IntersectionObserver" in window) {
      revealIO = new IntersectionObserver((entries) => {
        const batch = [];
        for (const en of entries)
          if (en.isIntersecting) { batch.push(en.target); revealIO.unobserve(en.target); }
        if (batch.length) revealBatch(batch);
      }, { rootMargin: "0px 0px -8% 0px" });
    }
    if (revealIO) below.forEach((el) => revealIO.observe(el));
    else revealBatch(below);
  }

  /* observe(root): re-scan after dynamic renders (page scripts call it) */
  M.observe = function (root) {
    safely("observe", () => watchReveal($$(".hud, [data-rv]", root)));
  };

  /* ---- decrypt: labels resolve from scrambled glyphs ----------------- */
  const GLYPHS = "ABCDEFGHJKLMNPQRSTUVWXYZ023456789#/·";
  function decrypt(el) {
    const orig = el.textContent;
    if (!orig || el.children.length || el.dataset.mDec) return;
    el.dataset.mDec = "1";
    if (reduced) return;
    const total = Math.max(14, Math.min(30, orig.length * 2));
    let frame = 0;
    const step = () => {
      frame++;
      const prog = frame / total;
      el.textContent = orig.split("").map((ch, i) => {
        if (ch === " " || i / orig.length < prog) return ch;
        return GLYPHS[(Math.random() * GLYPHS.length) | 0];
      }).join("");
      if (prog < 1) requestAnimationFrame(step);
      else el.textContent = orig;
    };
    requestAnimationFrame(step);
  }

  function initDecrypt() {
    const targets = $$(".eyebrow, .hud-kicker, [data-decrypt]");
    if (!targets.length || reduced) return;
    if (!("IntersectionObserver" in window)) return;
    const io = new IntersectionObserver((entries) => {
      for (const en of entries)
        if (en.isIntersecting) { decrypt(en.target); io.unobserve(en.target); }
    }, { rootMargin: "0px 0px -4% 0px" });
    targets.forEach((el) => io.observe(el));
  }

  /* ---- physics: magnetic buttons, tilt, spotlight -------------------- */
  function initMagnetic() {
    if (reduced || !finePointer || !window.gsap) return;
    $$(".btn-primary, .pub-btn--gold, [data-magnetic]").forEach((btn) => {
      const label = btn.querySelector("span") || btn;
      const xTo = window.gsap.quickTo(btn, "x", { duration: 0.3, ease: "power3" });
      const yTo = window.gsap.quickTo(btn, "y", { duration: 0.3, ease: "power3" });
      // the label trails a little further behind the plate -> depth
      const lx = label === btn ? null
        : window.gsap.quickTo(label, "x", { duration: 0.45, ease: "power3" });
      const ly = label === btn ? null
        : window.gsap.quickTo(label, "y", { duration: 0.45, ease: "power3" });
      btn.addEventListener("pointermove", (e) => {
        const r = btn.getBoundingClientRect();
        const dx = e.clientX - r.left - r.width / 2;
        const dy = e.clientY - r.top - r.height / 2;
        xTo(dx * 0.32); yTo(dy * 0.36);
        if (lx) { lx(dx * 0.13); ly(dy * 0.15); }
      }, { passive: true });
      btn.addEventListener("pointerleave", () => {
        xTo(0); yTo(0); if (lx) { lx(0); ly(0); }
      });
    });
  }

  /* ---- hero figure: parallax depth + pointer float + blink-dash burst -
     Everything here is written by GSAP directly onto the elements'
     `transform`/`opacity` every tick (quickTo, ScrollTrigger scrub) —
     never through a CSS transition. A transition racing a high-frequency
     listener is what produced the old "shake": each new value restarted
     the transition mid-flight, so the element chased a constantly moving
     target instead of settling. GSAP's own ticker is the only smoothing
     layer here, exactly like the (already smooth) magnetic buttons.

     Depth comes from real layers on a shared preserve-3d stack (rings and
     glow sit at negative Z, the figure at Z0) plus a SMALL rotateY on the
     whole stack — small enough that the flat cutout doesn't visibly warp,
     but enough that the layers visibly shift against each other. Rotating
     the flat image itself by a large angle (the old approach) just skews
     a photo, which is what read as cheap. */
  function initHeroFigure() {
    if (reduced) return;
    const stage = document.querySelector(".tracer-stage");
    const stack = document.querySelector("[data-tracer]");
    if (!stage || !stack) return;
    const fig = stack.querySelector("[data-tracer-fig]");
    const rim = stack.querySelector("[data-tracer-rim]");
    const streaks = $$("[data-tracer-streak]", stack);
    if (!window.gsap) return;   // no animation engine -> she's just static, fine
    const gsap = window.gsap;

    // ---- entrance: soft arrival, not a hard pop-in ---------------------
    gsap.from(stage, {
      opacity: 0, scale: 0.95, y: 14, duration: 0.9, delay: 0.25,
      ease: "power3.out", clearProps: "transform,opacity",
    });

    // ---- scroll: gentle recede as the hero scrolls past (translate +
    //      scale + fade only -- never rotation, so she never warps) ----
    if (window.ScrollTrigger) {
      gsap.to(stack, {
        y: -46, scale: 0.95, opacity: 0.85, ease: "none",
        scrollTrigger: {
          trigger: stage, start: "top top", end: "bottom top", scrub: 0.6,
        },
      });
    }

    // ---- pointer: restrained parallax float, not a warp ----------------
    // rim opacity is a quickTo too -- one owner (GSAP) for that property,
    // so its hover fade can never race the burst's flash of the same prop.
    const rimFade = rim ? gsap.quickTo(rim, "opacity", { duration: 0.35, ease: "power2" }) : null;
    if (finePointer) {
      const stackRotY = gsap.quickTo(stack, "rotationY", { duration: 0.7, ease: "power3" });
      const figX = gsap.quickTo(fig, "x", { duration: 0.6, ease: "power3" });
      const figY = gsap.quickTo(fig, "y", { duration: 0.6, ease: "power3" });
      const figRot = gsap.quickTo(fig, "rotation", { duration: 0.7, ease: "power3" });
      let queued = false, lastX = 0, lastY = 0;
      stage.addEventListener("pointermove", (e) => {
        const r = stage.getBoundingClientRect();
        lastX = (e.clientX - r.left) / r.width - 0.5;
        lastY = (e.clientY - r.top) / r.height - 0.5;
        if (queued) return;
        queued = true;
        requestAnimationFrame(() => {
          queued = false;
          stackRotY(lastX * 5);           // whole scene: rings/glow parallax
          figX(lastX * 16); figY(lastY * 10);
          figRot(lastX * 2);              // tiny in-plane tilt, never warps
          if (rim) {
            rim.style.setProperty("--rx", (lastX * 100 + 50).toFixed(1) + "%");
            rim.style.setProperty("--ry", (lastY * 100 + 50).toFixed(1) + "%");
          }
        });
      }, { passive: true });
      stage.addEventListener("pointerenter", () => {
        stage.classList.add("is-active");
        if (rimFade) rimFade(1);
      });
      stage.addEventListener("pointerleave", () => {
        stage.classList.remove("is-active");
        stackRotY(0); figX(0); figY(0); figRot(0);
        if (rimFade) rimFade(0);
      });
    }

    // ---- blink-dash burst: a short, designed moment (not a continuous
    //      mouse-follow smear) -- she hops, two energy streaks flash
    //      through and fade, done in under half a second -------------
    if (streaks.length && fig) {
      let busy = false;
      const burst = () => {
        if (busy) return;
        busy = true;
        const tl = gsap.timeline({ onComplete: () => { busy = false; } })
          .to(streaks, { opacity: 1, scaleX: 1.15, duration: 0.13, ease: "power2.out", stagger: 0.045 })
          .to(fig, { x: "+=11", duration: 0.12, ease: "power1.inOut" }, "<")
          .to(fig, { x: "-=11", duration: 0.24, ease: "power2.out" })
          .to(streaks, { opacity: 0, scaleX: 0.4, duration: 0.32, ease: "power2.in" }, "<")
          .to(fig, { scale: 1.02, duration: 0.12, ease: "power1.out" }, 0)
          .to(fig, { scale: 1, duration: 0.3, ease: "power2.out" });
        // rim flash rides the SAME quickTo instance as the hover fade
        // (rimFade) -- one owner for that property, never two tweens
        // racing each other for it.
        if (rimFade) {
          const restTo = stage.classList.contains("is-active") ? 1 : 0;
          tl.call(rimFade, [0.9], 0)
            .call(rimFade, [restTo], 0.18);
        }
        return tl;
      };
      let visible = true;
      if ("IntersectionObserver" in window) {
        new IntersectionObserver((es) => { visible = es[0].isIntersecting; },
          { threshold: 0.2 }).observe(stage);
      }
      const schedule = () => {
        setTimeout(() => {
          if (visible && !document.hidden) burst();
          schedule();
        }, 6400 + Math.random() * 2600);
      };
      gsap.delayedCall(1.8, () => { if (visible) burst(); });
      schedule();
      stage.addEventListener("pointerenter", burst);
    }
  }

  function initTiltSpot() {
    if (reduced || !finePointer) return;
    // spotlight: CSS vars the stylesheets already read (--spot-x/--spot-y)
    document.addEventListener("pointermove", (e) => {
      const card = e.target.closest && e.target.closest(".hud.lift, .card--spot, .pillar");
      if (!card) return;
      const r = card.getBoundingClientRect();
      card.style.setProperty("--spot-x", (e.clientX - r.left) + "px");
      card.style.setProperty("--spot-y", (e.clientY - r.top) + "px");
      if (window.gsap && !card.dataset.mTiltOff) {
        const rx = ((e.clientY - r.top) / r.height - 0.5) * -1.6;
        const ry = ((e.clientX - r.left) / r.width - 0.5) * 1.6;
        window.gsap.to(card, {
          rotationX: rx, rotationY: ry, transformPerspective: 900,
          duration: 0.35, ease: "power2.out",
        });
      }
    }, { passive: true });
    document.addEventListener("pointerout", (e) => {
      const card = e.target.closest && e.target.closest(".hud.lift, .card--spot, .pillar");
      if (!card || card.contains(e.relatedTarget)) return;
      if (window.gsap)
        window.gsap.to(card, { rotationX: 0, rotationY: 0, duration: 0.45, ease: "power3.out" });
    }, { passive: true });
  }

  /* ---- progress hairline --------------------------------------------- */
  function initProgress() {
    if (reduced) return;
    if (document.documentElement.scrollHeight < innerHeight * 1.8) return;
    const bar = document.createElement("div");
    bar.className = "scroll-progress";
    bar.setAttribute("aria-hidden", "true");
    document.body.appendChild(bar);
    const update = () => {
      const max = document.documentElement.scrollHeight - innerHeight;
      bar.style.transform =
        "scaleX(" + (max > 0 ? Math.min(1, scrollY / max) : 0) + ")";
    };
    addEventListener("scroll", update, { passive: true });
    addEventListener("resize", update, { passive: true });
    update();
  }

  /* ---- count-up numbers ---------------------------------------------- */
  M.countUp = function (el) {
    const target = parseFloat(el.dataset.countTo || el.textContent) || 0;
    const suffix = el.dataset.countSuffix || "";
    if (reduced || !window.gsap) {
      el.textContent = el.dataset.countText || (target + suffix);
      return;
    }
    const obj = { v: 0 };
    window.gsap.to(obj, {
      v: target, duration: 0.9, ease: "power2.out",
      onUpdate: () => {
        el.textContent =
          (target % 1 ? obj.v.toFixed(1) : Math.round(obj.v)) + suffix;
      },
    });
  };

  /* ---- ambience: Vanta NET -> 2D canvas net -> static gradient ------- */
  function webglOK() {
    try {
      const c = document.createElement("canvas");
      return !!(c.getContext("webgl") || c.getContext("experimental-webgl"));
    } catch (_) { return false; }
  }

  function vanta(holder) {
    if (reduced || innerWidth < 760) return false;
    if (!window.VANTA || !window.VANTA.NET || !window.THREE || !webglOK())
      return false;
    try {
      M.vantaFx = window.VANTA.NET({
        el: holder,
        mouseControls: finePointer, touchControls: false, gyroControls: false,
        minHeight: 200, minWidth: 200, scale: 1, scaleMobile: 1,
        color: 0x35507e,            /* steel-blue grid — amber stays the accent */
        backgroundAlpha: 0,         /* page gradients show through */
        points: 9, maxDistance: 21, spacing: 17, showDots: true,
      });
      holder.classList.add("m-vanta");
      return true;
    } catch (_) { return false; }
  }

  function canvasNet(holder) {
    if (reduced) return false;
    const c = document.createElement("canvas");
    c.style.cssText = "position:absolute;inset:0;width:100%;height:100%;opacity:.5";
    holder.appendChild(c);
    const ctx = c.getContext("2d");
    if (!ctx) return false;
    let w, h, pts = [], raf = null, last = 0;
    const resize = () => {
      w = c.width = Math.floor(innerWidth / 2);
      h = c.height = Math.floor(innerHeight / 2);
      pts = Array.from({ length: Math.min(26, Math.floor(w / 34)) }, () => ({
        x: Math.random() * w, y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.12, vy: (Math.random() - 0.5) * 0.12,
        r: 0.6 + Math.random() * 1.2,
      }));
    };
    const tick = (t) => {
      raf = requestAnimationFrame(tick);
      if (t - last < 50) return; /* ~20fps ambience */
      last = t;
      ctx.clearRect(0, 0, w, h);
      for (const p of pts) {
        p.x = (p.x + p.vx + w) % w; p.y = (p.y + p.vy + h) % h;
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 7);
        ctx.fillStyle = "rgba(148,168,205,0.35)"; ctx.fill();
      }
      ctx.strokeStyle = "rgba(148,168,205,0.06)";
      for (let i = 0; i < pts.length; i++)
        for (let j = i + 1; j < pts.length; j++) {
          const dx = pts[i].x - pts[j].x, dy = pts[i].y - pts[j].y;
          if (dx * dx + dy * dy < 8000) {
            ctx.beginPath(); ctx.moveTo(pts[i].x, pts[i].y);
            ctx.lineTo(pts[j].x, pts[j].y); ctx.stroke();
          }
        }
    };
    resize();
    addEventListener("resize", resize);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) { cancelAnimationFrame(raf); raf = null; }
      else if (!raf) raf = requestAnimationFrame(tick);
    });
    raf = requestAnimationFrame(tick);
    return true;
  }

  /* atmosphere(holder): best available ambience into an existing layer */
  M.atmosphere = function (holder) {
    return safely("atmosphere", () => vanta(holder) || canvasNet(holder)) || false;
  };

  /* ---- boot ----------------------------------------------------------- */
  M.boot = function (opts) {
    if (M.booted) return;
    M.booted = true;
    opts = opts || {};
    if (!reduced) document.documentElement.classList.add("motion-on");
    safely("flow", initFlow);
    safely("entrance", initEntrance);
    safely("reveals", () => watchReveal($$(".hud, [data-rv]")));
    safely("decrypt", initDecrypt);
    safely("magnetic", initMagnetic);
    safely("hero-figure", initHeroFigure);
    safely("tilt", initTiltSpot);
    safely("progress", initProgress);
    safely("counts", () => $$("[data-count-to]").forEach(M.countUp));
    if (opts.ambience !== false && document.body.dataset.vanta) {
      safely("ambience", () => {
        let holder = document.getElementById("cr-atmosphere");
        if (!holder) {
          holder = document.createElement("div");
          holder.id = "cr-atmosphere";
          holder.setAttribute("aria-hidden", "true");
          document.body.prepend(holder);
        }
        M.atmosphere(holder);
      });
    }
  };

  /* control-room pages self-boot; the public shell boots after it builds
     its header (shell.js calls OWCSMotion.boot()) */
  document.addEventListener("DOMContentLoaded", () => {
    if (document.body && document.body.dataset.shell === "control") M.boot();
  });
})();
