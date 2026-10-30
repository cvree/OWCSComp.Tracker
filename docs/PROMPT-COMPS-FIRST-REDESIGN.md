# Prompt — The Comps-First Revolution

> Hand this whole file to a fresh Claude Code session on this repository.
> It is written to be executed, not discussed. Every number in it was
> measured from the repo on 2026-10-30. Re-measure before you start, because
> the data moves.

---

## 0. The one-sentence brief

**Turn OWCS Comp Tracker from a tool for the people who feed it into a site for
the people who read it: a fast, beautiful, map-by-map and overall view of what
OWCS teams actually play. Every org gets a real icon. Submitting, reviewing,
labelling and operating move to an admin area that ordinary visitors never see.**

## 1. Why: what is wrong today

The site was built around the operator workflow:

> Submit a game → Automatic processing → Review → Approve → Published stats

That is the right model for **the pipeline** and the wrong one for **the site**.
Almost every visitor is a player, coach, analyst or fan. They want to know:

- *What do teams run on Nepal? On every Control map? On this map this stage?*
- *What is the meta overall? Which comps win?*
- *What did Twisted Minds play against Al Qadsiah, map by map?*
- *What does this team play on its best maps?*

None of them will submit a broadcast, review a detection or label a portrait.
Today they land on this:

| Symptom | Where | Fact |
|---|---|---|
| The homepage pitches **submitting** | `index.html` | H1 is "Turn broadcasts into reviewed match data", the first CTA is "Submit a broadcast", and the page shows "Where everything stands", "Filling itself" and a system-health strip. |
| Half the primary nav is admin | `assets/js/app/shell.js` `NAV` | Dashboard · Games · **Submit** · **Review** · Stats. `pipeline/test_site.py::test_navigation` locks exactly these five. |
| Comps are hidden in a tab | `stats.html` | "Compositions" is tab 2 of 5, behind Heroes. There is no page per map. |
| Orgs have no icons | `assets/img/teams/` | Only **5** orgs have a logo on disk: `cr`, `falcons`, `ssg`, `twis`, `zeta`. |
| Homepage weight | `index.html` | It parse-blocks on about **1.6 MB** of JS: `discovered.v1.js` (802 KB, admin discovery layer), `data.js` (292 KB, working/run record), vendor animation libraries (GSAP + ScrollTrigger + Lenis, about 128 KB), `public_data.v1.js` (65 KB), plus about 200 KB of app JS and 108 KB of CSS. A fan needs about 80 KB of that. |

## 2. Who the site is for (design against these, in this order)

1. **The competitive player / coach / analyst (most visitors).** Opens the site
   before scrims or after a match day. Wants a map, sees the comps, leaves in
   under 30 seconds. Often on a phone, often on Discord's in-app browser.
2. **The fan.** Follows a team. Wants the team's crest, its maps and its comps,
   and the series it just played, map by map.
3. **The admin (a handful of people).** Submits broadcasts, audits detections,
   labels portraits, runs tools. This user is **fully served, but out of the
   way**, behind `/admin/`.

If a feature serves only #3, it does not appear in the public chrome.

## 3. Non-negotiables (carry these over unchanged)

These are why the data is worth reading. The redesign must not weaken them.

- **Nothing invented.** An unread slot is *unknown*, never a guess. Do not show
  a scoreline that was not recorded (`scoreA/scoreB` may be `null`). Do not show
  a comp for a map that has only been *located* and not *read*.
- **Evidence one click away.** Every published comp still links to the frame it
  was read from (`compSnapshots[].evidenceFrame`, `evidence.js`). Keep the link
  quiet, but keep it.
- **Audit tier is visible but calm.** `auditTier` is `confirmed`, `strong` or
  `provisional`. Show it as a small mark, for example a dot or a dashed outline
  for provisional. Do not use banners.
- **Honest sample sizes.** Every rate carries its *n* ("1 map", "3 rounds").
  Small samples are labelled, not hidden. A win rate over 1 map is shown as
  "1–0", not "100%".
- **Three datasets, never merged:** `public_data.v1.js` (published facts),
  `discovered.v1.js` (what the scan found, not facts), `data.js` (working
  record). The public pages read the published record, plus the map index for
  "located, not yet read" placeholders.
- **No build step, no framework, $0 hosting.** Plain HTML/CSS/JS on GitHub
  Pages, vendored libraries only. Keep it that way.
- **Logo provenance policy** (`assets/data/team_asset_sources.json` `_note`):
  official sources only, never hotlink, never scrape third-party stat sites,
  never guess which mark belongs to which team, and record `sourceUrl` and
  `retrievedAt`.

## 4. The new information architecture

### 4.1 Public nav (replaces the five-entry workflow nav)

```
[logo] OWCS Comps     Maps   Comps   Teams   Matches   Heroes        [search /]
```

- **Maps** is the default way into the data, and the home page leads with it.
- **Comps** is the overall meta.
- **Teams** shows every org, with a crest.
- **Matches** shows series, map by map.
- **Heroes** shows pick and swap data. It is secondary but expected.
- Search (`/`, ⌘K) stays. Re-scope it to maps, comps, teams, heroes and matches.
  Admin pages are not indexed for the public.
- The footer carries **How it works** (a short honesty explainer), the data
  freshness line ("Updated 2 h ago · 1 map read · 36 located"), and a small
  **Admin** link.

### 4.2 Pages

| Page | URL | Job |
|---|---|---|
| **Home** | `index.html` | **The meta at a glance.** Top: global filters (Region · Event/Stage · Mode). Then **the map grid**: one card per map in the pool, grouped by mode (Control, Hybrid, Escort, Push, Flashpoint, Clash), each showing the most-played comp as five hero icons, its record (e.g. "2–1"), the number of maps read, and the two teams that played it most (crests). Then **Top comps overall**: 5–8 rows, hero icons, record, map spread. Then **Latest series**: crest vs crest, the maps as small chips, and a link. No pitch, no submit CTA, no system health. |
| **Map** | `map.html?id=nepal` | **The map-by-map view, the heart of the product.** Header: map name, mode, art if available, times played, sample size. Section 1, *What gets played here*: comps ranked by appearances, with W–L, teams that ran each (crests), and audit-tier marks. Section 2, *Round by round*: for Control, Flashpoint and Clash, sub-maps and rounds; for attack/defence modes, comps split by side when the data has it. Section 3, *Every time it was played*: chronological list of maps (crest vs crest, winner, both comps, swaps with timestamps, a "watch" link to the VOD timestamp and an evidence link). Section 4, *Located, not yet read*: maps from `broadcast_maps.v1.js` on this map with crests and a VOD link, clearly labelled as not read. |
| **Comps** | `comps.html` | **Overall comps.** Filterable table/grid (region, event, mode, map, team, and patch when `patches[]` is populated). Each comp shows 5 hero icons in a fixed role order (Tank · Damage · Damage · Support · Support), appearances, maps, W–L, teams and the maps it appeared on. Clicking a comp expands every instance (match, map, team, evidence). Also offer a **hero-core** view: the 2–3 hero cores that define comps (for example Mauga+Kiriko), because full 5-hero comps fragment fast. |
| **Teams** | `teams.html` | Grid of **every org that appears anywhere in public data**, each with its crest, region and maps read. No org without an icon (see §5). |
| **Team** | `team.html?id=twis` | Crest hero header in the team's accent colour (`accentColorRgb` already exists). **Map pool**: each map with W–L and the comp they ran there. **Signature comps.** Recent series, map by map. Roster when verified. |
| **Matches** | `matches.html` | Series list: crest vs crest, event, date, and the map chips in order (map icon, winner tint). Filter by event, region and team. It replaces the public face of `games.html`. |
| **Match** | `match.html?id=…` | **Series, map by map.** One panel per map: map name, mode, winner, both comps side by side (hero icons), swaps on a thin timeline, a "watch this map" VOD link at the right second, and evidence. Maps not read stay as honest placeholders. Reuse `mapstory.js` and `mapindex.js` where they fit. |
| **Heroes** / **Hero** | `heroes.html`, `hero.html?id=` | Pick rate by map and mode, comps the hero appears in, and swaps into and out of the hero. |

### 4.3 Admin area: moved, not deleted

Everything operator-facing moves under **`/admin/`**, with its own simple
chrome (a dense, utilitarian nav and an "Admin" badge):

```
admin/index.html      ← today's dashboard (tiles, Filling itself, system health)
admin/games.html      ← today's games.html (all states, discovered broadcasts, archive)
admin/game.html       ← today's game.html (pipeline progress, blockers)
admin/submit.html     ← submit.html
admin/review.html     ← review.html (the audit screen, unchanged in behaviour)
admin/label.html      ← label.html
admin/tools.html      ← tools.html, calibrate.html, control-room.html, setup.html, guide.html
```

Rules:

- **Do not break behaviour.** The review keyboard, corrections export, label
  flow, connected-tracker mode (`api.js` `A.isConnected()`), the desktop app
  (`desktop/owcs_desktop/website.py` serves these pages) and the workflows that
  reference paths (`.github/workflows/*.yml`, `pipeline/site_paths.py`) must all
  keep working. Grep for every old path and update every reference.
- **Old URLs keep working.** Leave a tiny redirect stub at each old path, for
  example `submit.html` → `admin/submit.html`, and extend the legacy map in
  `404.html`. Bookmarks and the desktop app must not 404.
- **Be honest about security.** GitHub Pages is static, so `/admin/` is
  *hidden*, not *protected*. That is acceptable because nothing on the site can
  write: corrections leave as a file and land through git, and processing runs
  on the operator's machine or in Actions. Say so in a comment and in the README.
  Do not add fake password gates.
- **Admin is discoverable for admins.** Show a footer link, a small persistent
  "Admin" pill in the public header **only** when a local tracker is connected
  (`api.js` already detects this), and `?admin=1` to set a localStorage flag
  that shows the pill (wrap storage in try/catch).
- **Only admin pages load admin data.** `discovered.v1.js` (802 KB),
  `data.js` (292 KB), `labelling.v1.js` (216 KB) and `matchfinder.v1.json`
  (447 KB) must never load on a public page.

## 5. ALL ORGS NEED ICONS

This is a hard requirement, not polish. A comp tracker is read by crest.

### 5.1 The gap, measured

**Registered teams** (`public_data.teams`, `team_coverage.v1.json`):

| id | name | logo today |
|---|---|---|
| `cr` | Crazy Raccoon | ✅ verified |
| `falcons` | Team Falcons | ✅ verified |
| `ssg` | Spacestation Gaming | ✅ verified |
| `twis` | Twisted Minds | ✅ verified |
| `zeta` | ZETA Division | ✅ verified |
| `qadsiah` | Al Qadsiah | ⚠️ candidate only (50×50 favicon, `logoUrl: null`) |
| `nrg` | NRG Shock | ⚠️ candidate only |
| `gen` | Gen.G Esports | ❌ none |
| `quick` | Quick Esports | ❌ none |

**Orgs on the public map index with no team record at all**
(`broadcast_maps.v1.js`, `team_*.id === null`): VARREL, TEAM SECRET, AG.AL,
GEEKAY ESPORTS, JD GAMING, T1, TEAM LIQUID, WEIBO GAMING, VIRTUS PRO, GLOBANT,
DALLAS FUEL.

These numbers will grow as `discovered.v1.js` (288 broadcasts) is read, so the
fix must be a **system**, not a one-off.

### 5.2 What to build

1. **One org registry.** Every org name that can reach a public page resolves to
   one team id: add the 11 missing orgs as team records, with `aliases` for
   broadcast spellings ("VIRTUS PRO" / "Virtus.pro" / "VP", "AG.AL" /
   "Al Ahli" if that is what it is). **Verify each mapping. Do not guess.** If
   you cannot confirm an alias, leave it unmapped and list it in the report.
   Wire the map-index builder (`pipeline/broadcast_maps.py`) to resolve names
   through this registry so `team_*.id` stops being `null`.
2. **Acquire official marks** for every registered org through the existing
   pipeline (`team_asset_sources.json` → stage → approve → variants in
   `assets/img/teams/<id>/` with `logo.png`, `logo-square.png`,
   `logo-wide.png`, `logo-dark-safe.webp`, `logo-light-safe.webp` →
   `pipeline/build_asset_manifest.py`). Prefer the org's own site or press kit,
   and record `sourceUrl`, `retrievedAt` and `accentColorRgb`. Replace the
   Al Qadsiah 50×50 favicon with a proper mark. If the network blocks a source,
   stop on that org, record the exact URL you need, and move on.
3. **A designed fallback that still counts as a gap.** Until a real mark lands,
   render the existing `P.teamCrest` monogram (`core.js`), upgraded to show the
   team **code** (QAD, VP, T1) on the org's accent colour. It must never be a
   broken image or a grey square. The monogram is a placeholder, so it shows up
   in coverage.
4. **A gate.** Add a test (extend `pipeline/test_automation_team_assets.py` or
   `test_public_export_contracts.py`) that walks **every team id and every org
   name** in `public_data.v1.js` and `broadcast_maps.v1.js` and fails if any is
   unresolved to a team or has no `logoUrl`. Ship it as a **reported warning
   with the exact list** while orgs are still being sourced, then flip it to a
   hard failure once the list is empty. Put the coverage line ("16 / 20 orgs
   have official marks") on `admin/tools.html`, not on public pages.
5. **Render rules.** Crests always have explicit `width`/`height` (no layout
   shift), use the dark-safe or light-safe variant by theme, `loading="lazy"`
   below the fold, and `alt=""` when the name is printed beside the crest.
   Sizes: 16 px in chips, 24 px in rows, 40 px on cards, 96 px in team headers.

### 5.3 Map art and hero icons (same spirit)

- Hero icons exist (`assets/img/heroes/official/<id>/icon.webp`), so use them
  everywhere a comp is shown. A comp is five icons, never five words.
- There is **no map imagery** (`assets/img/` has no `maps/`). Add a mode glyph
  set (6 small inline SVGs, one per mode) now. Add map thumbnails only from
  official sources with provenance, through a manifest like the team one. If
  you cannot source them, ship the mode glyph plus a typographic card. Do not
  ship placeholder photos.

## 6. Data: what the public pages need that the export does not give yet

Today's aggregates (`compFrequency`, `compWinRate`, `mapStats`,
`teamMapRecords`, `teamHeroPools`, `heroPickRates`, `modeStats`) are global.
Comps by map are not pre-computed. Add these to `pipeline/export_data.py`
(published only, withheld matches excluded, every count carrying its sample):

- `mapCompStats[]`: per map → comps seen `{heroes, appearances, wins, losses,
  undecided, teamIds, side?, auditTiers, evidence[]}`.
- `heroCores[]`: 2- and 3-hero cores with appearances and W–L, overall and per
  map.
- `seriesIndex[]`: per match → ordered maps with winner, both comps and the VOD
  offset. This is what `matches.html` and `match.html` render.
- `orgs[]` (or an enriched `teams[]`): id, name, code, region, aliases,
  logo variants, accent colour, `logoState`.

Document every new field in `docs/PUBLIC_DATA_CONTRACT.md`, add contract tests
in `pipeline/test_public_export_contracts.py`, and keep the role order for comps
fixed and documented (Tank → Damage → Support, then alphabetical within a role).

Consider splitting the public export so a page loads only what it renders, for
example `public_core.v1.js` (teams, maps, heroes, series index) and
`public_comps.v1.js`. Only split if it measurably helps. One 65 KB file is fine
today, and the 1.6 MB problem is the admin files, not this one.

## 7. "Utterly optimized": performance budget

Measure before and after, and put both in the PR description.

| Budget (public pages, mobile, cold cache) | Target |
|---|---|
| Total JS parsed before first render | **≤ 120 KB** (today about 1.6 MB on `index.html`) |
| CSS | **≤ 40 KB** for public pages. Split `owcs.css` (108 KB) into a public sheet and an admin sheet. |
| Largest Contentful Paint (Lighthouse mobile) | **< 1.5 s** |
| Cumulative Layout Shift | **< 0.02** (crests and hero icons sized) |
| Lighthouse Performance / Accessibility | **≥ 95 / ≥ 95** |

Tactics:

- Public pages load `public_data.v1.js`, `hero_art.v1.js` (if still needed),
  `broadcast_maps.v1.js` only on Map and Match pages, `core.js`, `shell.js`, and
  their own page script. Everything with `defer`.
- **Drop GSAP, ScrollTrigger and Lenis from public pages.** Motion must be CSS,
  must respect `prefers-reduced-motion`, and must stay subtle. Load Fuse.js
  lazily on first search open.
- Hero icons: one sized `icon.webp` per hero (verify each is ≤ 6 KB at 64 px).
  Never load `artwork.jpg` or `card.webp` in a list.
- Render the map grid from data in one pass. No per-card fetches.
- Test on a 390 px viewport first. The map grid is 2 columns on phone, 3–4 on
  tablet and 5–6 on desktop. There is no horizontal page scroll. Comp rows wrap
  without breaking role order.

## 8. Design direction

- **Data first, chrome last.** The first screen of every public page shows real
  comps, not a headline about comps.
- **Read by picture.** Crests, hero icons and mode glyphs carry the page. Text
  confirms them.
- **Comp row anatomy (reuse everywhere):**
  `[5 hero icons in role order] [W–L] [n maps] [team crests ×≤3] [tier dot]`.
  On hover or tap it expands to show instances. Build it once as a component in
  `core.js` (`P.compRow`, `P.heroStack`, `P.crest`, `P.mapCard`).
- **Map card anatomy:** mode glyph, map name, the top comp's hero icons,
  record, "n read · m located", and up to 2 crests.
- Keep the existing fonts (Archivo, Saira Condensed, IBM Plex Mono) and theme
  tokens. Light and dark both have to work. Use the team accent colour only on
  team-scoped surfaces.
- **Empty and sparse states are first-class.** Today one map has been read.
  The site must look intentional at n = 1 and still work at n = 2,000. A map
  with zero reads shows "Not read yet" plus located broadcasts with crests and
  VOD links, not a blank card.

## 9. Execution plan (in order, commit per phase, keep CI green at each step)

1. **Measure.** Record page weights, the script list per page, a Lighthouse
   mobile run (Playwright plus Chromium are preinstalled, at
   `/opt/pw-browsers/chromium`), and screenshots of today's home, stats, team
   and game pages at 390 px and 1440 px. Save them under
   `reports/redesign/before/`.
2. **Move admin.** Create `admin/`, move the operator pages, add redirect stubs,
   update `404.html`, `site_paths.py`, the desktop server and the workflows.
   Rewrite `pipeline/test_site.py`'s navigation test **deliberately**: it should
   now assert the public nav (Maps, Comps, Teams, Matches, Heroes), that no
   public page links Submit or Review in its nav, and that no public page loads
   `discovered.v1.js`, `data.js` or `labelling.v1.js`. Do not delete the
   intent; restate it.
3. **Org registry and icons** (§5). Registry, alias resolution in the map
   index, logo acquisition, monogram fallback and the coverage gate.
4. **Export.** `mapCompStats`, `heroCores`, `seriesIndex` and `orgs`, with
   contract docs and tests.
5. **Components.** `P.crest`, `P.heroStack`, `P.compRow`, `P.mapCard` and
   `P.seriesRow`, each shown on `styleguide.html`.
6. **Pages.** Home (map grid plus top comps plus latest series), `map.html`,
   `comps.html`, `matches.html`, `match.html`, then Teams, Team, Heroes and
   Hero.
7. **Optimize.** Split the CSS, remove animation libraries from public pages,
   lazy-load search, and audit images. Hit the §7 budget.
8. **Verify and document.** Run every test in `pipeline/test_*.py` that CI runs
   (`.github/workflows/ci.yml`), plus `node pipeline/render_check.js` if
   applicable. Take after-screenshots in `reports/redesign/after/`, run
   Lighthouse after, and compare. Rewrite the README "The product" section
   around the reader, with the admin workflow as its own subsection. Update
   `how-it-works.html`.

## 10. Acceptance criteria (all must be true)

- [ ] A first-time visitor on a phone reaches "what comps are played on Nepal"
      in **one tap** from the home page.
- [ ] The public nav is Maps · Comps · Teams · Matches · Heroes. Submit, Review,
      Label and Tools appear nowhere in public chrome, and are reachable via the
      footer Admin link, the connected-tracker pill and `/admin/`.
- [ ] Every old URL still resolves (redirect stub or 404 legacy map), and the
      desktop app still opens its pages.
- [ ] **Every org rendered on any public page shows an icon.** The gate test
      lists any org still on the monogram fallback, and the final report names
      each one with the exact official source needed.
- [ ] Every org name in `broadcast_maps.v1.js` resolves to a team id, or is
      explicitly listed as unverified.
- [ ] Every comp shown is five hero icons in fixed role order, with W–L, sample
      size and audit tier, and one click from its evidence frame.
- [ ] No public page loads `discovered.v1.js`, `data.js`, `labelling.v1.js`,
      `matchfinder.v1.json`, GSAP, ScrollTrigger or Lenis.
- [ ] The §7 budget is met and the before/after numbers are in the PR.
- [ ] No invented data: no null scorelines rendered as numbers, no comps for
      located-only maps, no percentages on n < 3 without the raw record beside
      them.
- [ ] Light and dark themes, 390 px with no horizontal scroll, keyboard
      navigable, and `prefers-reduced-motion` respected.
- [ ] All CI tests pass. Tests that encoded the old IA were rewritten to encode
      the new one, not deleted.

## 11. Do not

- Do not delete the review, label, submit or tools functionality. Move it.
- Do not add a framework, bundler, server or paid service.
- Do not fabricate logos, scrape Liquipedia or stat sites, or hotlink.
- Do not compute public aggregates from `discovered.v1.js`. Found is not read.
- Do not put a "Submit a broadcast" call to action on any public page.
- Do not let a sparse dataset push you into filler. Show the real small
  number, beautifully.
