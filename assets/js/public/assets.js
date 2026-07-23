/* =====================================================================
   OWCS Comp Tracker — public/assets.js
   The asset registry: one honest resolver for every team mark and hero
   face on the public site.

   Rules (mirrors assets/data/asset_manifest.json + pipeline test):
     * A verified image renders as an <img> with intrinsic dimensions.
     * Anything unverified renders a DESIGNED fallback — an inline-SVG
       crest (teams) or a role-tinted monogram (heroes). Never a broken
       image, never a guessed logo, never hotlinked art.
     * Team accents are UI illumination (deterministic, curated), not a
       claim about an org's brand palette.
   ===================================================================== */
(function () {
  "use strict";
  const P = (window.OWCS_PUB = window.OWCS_PUB || {});
  const esc = P.esc || ((s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;"));

  const A = (P.assets = P.assets || {});

  /* ---- team accents: focused illumination, one hue per team ---------- */
  const TEAM_HUES = {
    /* curated so the teams that actually appear read distinctly; the
       formula below covers everyone else deterministically */
    qadsiah: 43,   /* burnished gold   */
    twis: 275,     /* electric violet  */
    cr: 203,       /* glacial cyan     */
    zeta: 152,     /* emerald          */
    falcons: 88,   /* venom green      */
    gen: 38, nrg: 12, ssg: 320, quick: 190,
  };
  const hashHue = (str) => {
    let h = 0;
    for (let i = 0; i < String(str).length; i++)
      h = (h * 31 + String(str).charCodeAt(i)) % 360;
    return h;
  };
  A.teamHue = (teamId) =>
    (teamId in TEAM_HUES ? TEAM_HUES[teamId] : hashHue(teamId || "tbd"));
  A.teamAccent = (teamId, l) =>
    `hsl(${A.teamHue(teamId)} 62% ${l == null ? 62 : l}%)`;

  /* ---- team crest: designed fallback mark (inline SVG, can't break) --- */
  const CREST_PATH = "M12 1 H36 L47 12 V30 L24 47 L1 30 V12 Z";
  A.teamCrest = (team, opt) => {
    opt = opt || {};
    const id = team ? team.id : "tbd";
    const code = team ? (team.code || "?") : (opt.tbd || "?");
    const hue = A.teamHue(id);
    const fs = code.length >= 4 ? 12 : code.length === 3 ? 14 : 17;
    /* unique gradient id per crest instance-family (id-scoped is enough:
       the same team's crest is identical everywhere) */
    const gid = "crest-" + String(id).replace(/[^a-z0-9-]/gi, "");
    return `<svg class="crest" viewBox="0 0 48 48" role="img" aria-hidden="true" focusable="false">
      <defs><radialGradient id="${gid}" cx="50%" cy="30%" r="75%">
        <stop offset="0%" stop-color="hsl(${hue} 48% 26%)"/>
        <stop offset="58%" stop-color="hsl(${hue} 42% 13%)"/>
        <stop offset="100%" stop-color="hsl(232 34% 7%)"/>
      </radialGradient></defs>
      <path d="${CREST_PATH}" fill="url(#${gid})" stroke="hsl(${hue} 55% 48%)" stroke-width="1.4"/>
      <path d="M12 4 H36 L44.4 12.4 V28.8 L24 43.6 L3.6 28.8 V12.4 Z" fill="none"
        stroke="hsl(${hue} 45% 34%)" stroke-width=".7" opacity=".8"/>
      <text x="24" y="24" text-anchor="middle" dominant-baseline="central"
        font-family="'Chakra Petch','Inter',sans-serif" font-weight="700"
        font-size="${fs}" fill="hsl(${hue} 72% 78%)" letter-spacing=".5">${esc(code)}</text>
    </svg>`;
  };

  /* teamMark: verified logo <img> when the export carries one, else the
     designed crest. Broken downloads still fall back via data-img-fallback
     (core.js global error hook). */
  A.teamMark = (team, opt) => {
    opt = opt || {};
    if (team && team.logoUrl) {
      return `<img class="team-logo-img" src="${esc(team.logoUrl)}" alt=""
        width="${opt.px || 34}" height="${opt.px || 34}" loading="lazy"
        data-img-fallback="${esc(team.code || "?")}">`;
    }
    return A.teamCrest(team, opt);
  };

  /* ---- role iconography (inline SVG, stroke inherits currentColor) ---- */
  const ROLE_ICONS = {
    Tank: '<path d="M12 2 L21 6 V12 C21 17.4 17.4 21 12 22 C6.6 21 3 17.4 3 12 V6 Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>',
    Damage: '<circle cx="12" cy="12" r="8.4" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 1.6 V7 M12 17 V22.4 M1.6 12 H7 M17 12 H22.4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    Support: '<path d="M9.4 3 H14.6 V9.4 H21 V14.6 H14.6 V21 H9.4 V14.6 H3 V9.4 H9.4 Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>',
  };
  A.roleIcon = (role, cls) =>
    `<svg class="role-ic${cls ? " " + cls : ""}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${ROLE_ICONS[role] || ROLE_ICONS.Damage}</svg>`;

  A.ROLE_HUES = { Tank: 210, Damage: 4, Support: 145 };
  A.roleHue = (role) => (role in A.ROLE_HUES ? A.ROLE_HUES[role] : 265);

  /* ---- hero faces ----------------------------------------------------- */
  A.heroInitials = (hero) =>
    String(hero && hero.name || "?").replace(/[^A-Za-z0-9. ]/g, "")
      .split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();

  /* heroFace: the inside of a portrait cell. Verified broadcast crop when
     the export carries portraitUrl; otherwise a role-tinted monogram —
     an INTENTIONAL designed fallback, never a guess. */
  A.heroFace = (hero, opt) => {
    opt = opt || {};
    if (hero && hero.portraitUrl) {
      return `<img src="${esc(hero.portraitUrl)}" alt="" loading="lazy"
        width="${opt.px || 96}" height="${opt.px || 96}"
        data-img-fallback="${esc(A.heroInitials(hero))}">`;
    }
    const hue = A.roleHue(hero && hero.role);
    return `<span class="hero-mono" style="--mono-h:${hue}" aria-hidden="true">${esc(A.heroInitials(hero))}</span>`;
  };

  /* provenance lookup (manifest is optional at runtime; pages that want
     to show "where did this image come from" call this) */
  A.manifest = null;
  A.loadManifest = function (cb) {
    if (A.manifest) { cb && cb(A.manifest); return; }
    fetch("assets/data/asset_manifest.json")
      .then((r) => (r.ok ? r.json() : null))
      .then((m) => { A.manifest = m; cb && cb(m); })
      .catch(() => { cb && cb(null); });
  };
  A.heroProvenance = (heroId) =>
    A.manifest && A.manifest.heroes ? A.manifest.heroes[heroId] || null : null;
})();
