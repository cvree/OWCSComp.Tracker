/* OWCS Comp Tracker — shared control-room interactions.
   No frameworks. Safe on every page; each feature is opt-in via markup. */
(function () {
  "use strict";

  /* ---- Favicon (idempotent; stops the /favicon.ico 404 on tools) ----- */
  if (!document.querySelector('link[rel="icon"]')) {
    const l = document.createElement("link");
    l.rel = "icon"; l.type = "image/svg+xml"; l.href = "assets/img/favicon.svg";
    document.head.appendChild(l);
  }

  /* ---- Copy-to-clipboard for .cmd-copy buttons (inside .cmd-chip) ---- */
  document.addEventListener("click", function (e) {
    const btn = e.target.closest(".cmd-copy");
    if (!btn) return;
    const chip = btn.closest(".cmd-chip");
    const code = chip && chip.querySelector("code");
    const text = btn.dataset.copy || (code ? code.textContent : "");
    if (!text) return;
    const done = () => {
      const old = btn.textContent;
      btn.textContent = "copied";
      btn.classList.add("copied");
      setTimeout(() => { btn.textContent = old; btn.classList.remove("copied"); }, 1400);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(done);
    } else { /* http://localhost fallback */
      const ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } catch (_) {}
      document.body.removeChild(ta); done();
    }
  });

  /* ---- Scroll reveals (.reveal -> .in) ------------------------------- */
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const targets = document.querySelectorAll(".reveal");
  if (targets.length) {
    if (reduced || !("IntersectionObserver" in window)) {
      targets.forEach(el => el.classList.add("in"));
    } else {
      const io = new IntersectionObserver((entries) => {
        for (const en of entries) {
          if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
        }
      }, { rootMargin: "0px 0px -8% 0px" });
      targets.forEach(el => io.observe(el));
    }
  }

  /* ---- Nav system-status dot (#nav-status) ---------------------------
     online  = control-room API answering (serve.py)
     running = API answering AND a job is in flight
     static  = plain file server / GitHub Pages — terminal workflow only  */
  const st = document.getElementById("nav-status");
  if (st) {
    const set = (state, label) => {
      st.dataset.state = state;
      st.querySelector("span:last-child").textContent = label;
    };
    const ping = () => fetch("/api/ping").then(r => r.json()).then(j => {
      set(j && j.running ? "running" : "online",
          j && j.running ? "job running" : "control room online");
    }).catch(() => set("static", "static mode"));
    ping();
    setInterval(ping, 5000);
  }
})();
