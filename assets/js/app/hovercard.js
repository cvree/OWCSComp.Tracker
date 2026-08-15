/* =====================================================================
   OWCS Comp Tracker — app/hovercard.js
   A hero card that follows the pointer.

   Composition strips are the densest thing on this site: ten 40px faces
   in a row, and a reader who does not know a hero by its face has to
   click through to find out — losing their place in the list to answer a
   one-second question. This answers it in place: name, role, and what
   this dataset has actually seen the hero do, over the wider crop of the
   official art.

   Deliberately not a tooltip library. The whole behaviour is: find the
   anchor, place a panel next to it inside the viewport, and never let it
   sit under the pointer. Positioning is one clamp in each axis.

   It is strictly an enhancement:
     * every anchor is already a link to the hero page, and stays one;
     * `title` still carries name and role for anyone who never hovers;
     * touch and keyboard never summon it — a card that opens on tap is a
       card that eats the tap that was meant for the link. Keyboard users
       get the hero page, which has all of this and more;
     * reduced motion drops the fade.
   ===================================================================== */
(function () {
  "use strict";
  const P = window.OWCS;
  const esc = P.esc;

  const OPEN_DELAY = 260;
  const CLOSE_DELAY = 120;
  const GAP = 10;

  let panel = null, timer = null, anchor = null;

  const canHover = () => {
    try {
      return !!(window.matchMedia && window.matchMedia("(hover: hover) and (pointer: fine)").matches);
    } catch (e) { return false; }
  };

  function ensure() {
    if (panel) return panel;
    panel = document.createElement("div");
    panel.className = "hcard";
    panel.hidden = true;
    panel.setAttribute("role", "presentation");
    document.body.appendChild(panel);
    /* The card must not become a hover target of its own: it sits beside
       the pointer, and a reader sweeping along a comp strip would
       otherwise re-trigger it from its own body. */
    panel.style.pointerEvents = "none";
    return panel;
  }

  function content(hero) {
    const rate = ((P.pub && P.pub.heroPickRates) || []).filter((r) => r.hero === hero.id)[0];
    const art = P.heroCard(hero.id);
    const pct = (v) => (v == null ? "—" : Math.round(v * 100) + "%");
    return (art
      ? '<span class="hcard__art"><img src="' + esc(art) + '" alt="" decoding="async"></span>'
      : "") +
      '<span class="hcard__body">' +
        '<span class="hcard__name">' + esc(hero.name) + "</span>" +
        '<span class="hcard__role" data-role="' + esc(hero.role || "") + '">' +
          esc(hero.role || "Role not recorded") + "</span>" +
        '<span class="hcard__stat">' +
          (rate
            ? esc(rate.picks + " approved line-up" + (rate.picks === 1 ? "" : "s") +
                " · " + pct(rate.pickRate) + " pick rate · " + pct(rate.winRate) + " won")
            : "Not yet seen in an approved line-up") +
        "</span>" +
      "</span>";
  }

  function place(el) {
    const r = el.getBoundingClientRect();
    const p = panel.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;

    /* Below the anchor by default; above it when there is no room below,
       which is what happens on the last row of a long list. */
    let top = r.bottom + GAP;
    if (top + p.height > vh - 8) top = Math.max(8, r.top - p.height - GAP);

    let left = r.left + r.width / 2 - p.width / 2;
    left = Math.max(8, Math.min(left, vw - p.width - 8));

    panel.style.top = Math.round(top + window.scrollY) + "px";
    panel.style.left = Math.round(left + window.scrollX) + "px";
  }

  function open(el) {
    const id = el.dataset.hero;
    const hero = id && P.hero(id);
    if (!hero || !hero.name) return;
    ensure();
    panel.innerHTML = content(hero);
    panel.hidden = false;
    anchor = el;
    place(el);
    requestAnimationFrame(() => panel.classList.add("is-in"));
  }

  function close() {
    clearTimeout(timer);
    timer = null;
    anchor = null;
    if (!panel) return;
    panel.classList.remove("is-in");
    panel.hidden = true;
  }

  function boot() {
    if (!canHover()) return;
    document.addEventListener("pointerover", (e) => {
      if (e.pointerType && e.pointerType !== "mouse") return;
      const el = e.target.closest && e.target.closest("[data-hero]");
      if (!el || el === anchor) return;
      clearTimeout(timer);
      timer = setTimeout(() => open(el), OPEN_DELAY);
    });
    document.addEventListener("pointerout", (e) => {
      const el = e.target.closest && e.target.closest("[data-hero]");
      if (!el) return;
      if (e.relatedTarget && el.contains(e.relatedTarget)) return;
      clearTimeout(timer);
      timer = setTimeout(close, CLOSE_DELAY);
    });
    /* Anything that moves the anchor invalidates the position, and a card
       stranded mid-page is worse than no card. */
    addEventListener("scroll", close, { passive: true });
    addEventListener("resize", close);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
