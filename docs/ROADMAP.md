# OWCS Comp Tracker — Future Plan / Roadmap

_Last updated: 2026-07-24. Living document — reorder as priorities shift._

This is the honest, prioritized plan for what comes next. It's grounded in
the code that exists today, not aspirational filler. Every item names the
files it touches and the gap it closes. Nothing here changes the two core
principles: **only verified data ships**, and **every public number links
to its evidence**.

---

## Where things stand today (2026-07-24)

**Live:** https://cvree.github.io/OWCSComp.Tracker/ — auto-deploys on merge
to `main` via `.github/workflows/pages.yml`.

| Area | State |
|---|---|
| Public site | Dark-gothic "nocturne" redesign, 13 pages, evidence-first |
| Real data | **1 map** fully ingested (Al Qadsiah vs Twisted Minds, Nepal) |
| Comps | 8 verified snapshots, 2 confirmed swaps with evidence crops |
| Hero portraits | 14 / 52 real broadcast crops (96px); rest = designed monograms |
| Team logos | **0 verified** — all designed crests (see P2) |
| Tests | 44 offline suites green, `check_packaging.py` clean, CI on every PR |
| Deploy | Fully automated (push → CI → merge → Pages) |

The engine is done. What's left is almost entirely **breadth** (more real
matches) and **fidelity** (better assets), not new architecture.

---

## P0 — Quick wins (hours, no video processing)

These need no VOD, no network to game servers, no calibration — just code.

1. **Enable hands-off auto-merge** _(you, 2 min)_
   Settings → General → Pull Requests → ✅ Allow auto-merge, then
   Settings → Branches → protect `main` → require the `test` check. After
   this, PRs merge themselves the instant CI is green — no babysitting.

2. **`test_export_data.py`** _(pipeline)_
   The exporter's multi-tournament path (`_tournament_id`, tournament
   accumulation) is only exercised by the single real match. Add a suite
   with a synthetic 2-tournament DB so a second real match can't silently
   mislabel. Flagged as a known untested path in `HANDOFF.md`.

3. **Higher-contrast hero monograms audit** _(assets.js)_
   The role-tinted fallback monograms are readable but could carry the
   role icon inline. Low effort, improves the 38 heroes without portraits.

4. **`sitemap.xml` + `robots.txt`** _(static)_
   13 public pages, zero SEO surface today. One static sitemap listing the
   canonical routes helps the site get found.

---

## P1 — Data breadth (the biggest lever)

The site is a beautiful frame around **one map**. Every stat, every meta
snapshot, every hero rate gets more trustworthy the moment there's more
real data. This is the highest-value work.

1. **Finish the CR-ZETA ingest** _(next in line — half-built already)_
   `m-cr-zeta-ccuf` has a calibrated layout (`owcs_8c105lnzlam.json`, with
   the `hud_probe` block) and a match row, but **`ingest_map.py --write`
   was never run**. The exact next commands are in `HANDOFF.md`'s
   "IN PROGRESS" section. Needs: re-download the clip, dry-run to confirm
   round 2's end, optionally harvest more templates (only 7 heroes covered
   so far), then `--write` + `export_data.py --public`. This proves the
   multi-match path end-to-end and roughly doubles the stat sample.

2. **Ingest a full series, not just map 1** _(schema already supports it)_
   `map_results` keys on `(match, map_order)`; the pipeline is idempotent
   per map. Capture maps 2–5 of an existing match to record a real
   series-level scoreline (currently `scoreA/scoreB` are null by honest
   design). This lights up the bracket/standings surfaces with real data.

3. **A second region** _(generalization test)_
   The whole pipeline is per-source: calibrate → review sheet → harvest +
   label templates → ingest. Pick one APAC or China broadcast and run the
   repeatable workflow in `HANDOFF.md` → "Repeatable workflow for a NEW
   VOD/map". Proves the region accent system and multi-region filters
   against real rows.

4. **Round-boundary + control-% OCR** _(detection depth)_
   Today `scoreDetail` is null (honest fallback) and the map winner is
   operator-supplied via `--map-winner`, not read. Reading the round
   score/control-% off the HUD would make the typed `control` score widget
   render real per-round bars instead of the fallback. Non-trivial (new
   OCR region) but high visual payoff on `match.html`.

---

## P2 — Asset fidelity

The redesign's asset registry is built and honest, but under-populated.
Everything here is "swap a fallback for a verified real asset," never
"invent."

1. **Verified team logos** _(needs an open-network machine)_
   All 9 teams currently render designed crests because **no official mark
   could be fetched from the build sandbox** (network policy blocks it).
   The whole pipeline is ready:
   - `assets/data/team_asset_sources.json` documents candidate official
     sources per team.
   - Drop a verified transparent PNG at `assets/img/teams/<id>/logo.png`,
     record its `sourceUrl` + attribution, then run
     `python pipeline/build_asset_manifest.py`.
   - The manifest flips that team to `verified-official`, `test_assets.py`
     confirms it, and every team plate site-wide upgrades automatically.
   - **Rules (non-negotiable):** official sources only, never hotlink,
     never scrape stat sites, never guess which mark belongs to a team,
     preserve transparency, record provenance.

2. **Higher-resolution hero portraits**
   Current portraits are 96px broadcast crops — crisp at tile size, soft
   at the 120px `hero.html` dossier header. Harvest larger crops (or an
   authoritative clean source, documented + licensed) and regenerate via
   `build_hero_portraits.py`. Also raises coverage past today's 14/52.

3. **Light/dark + wide/square logo variants**
   The vision doc calls for light/dark logo variants and wide vs square
   marks. The registry schema can carry them; add variant fields once real
   logos exist (depends on P2.1).

---

## P3 — Detection quality (deeper pipeline work)

1. **Unify the production detector on the honest-read path.**
   Debug tooling uses `detect.read_slot()` (top hero + runner-up + margin
   + explicit UNKNOWN), but the production accept/reject path still uses
   the older bare-score `match_slot()`. Noted as a deliberately-deferred
   gap in `HANDOFF.md`. Migrating it tightens accuracy on new broadcasts.

2. **Expand per-source template coverage.**
   Each broadcast needs its own harvested templates. Heroes outside the
   labeled set correctly read `UNKNOWN` (honest, not a bug) but leave slots
   uncovered. A faster harvest/label loop (or cross-source template reuse
   where art matches) widens `full_house_rate`.

3. **Measured accuracy gate (`eval_detection.py`).**
   Before trusting auto-promotion at scale, add a labeled eval set and a
   published slot-accuracy number, so the auto-high gate is backed by a
   measured figure, not just confidence thresholds.

---

## P4 — Product & polish

1. **Player pages** — `players` data already exports; a `player.html`
   dossier (hero pool, per-match comps) is a natural sibling to `team.html`
   and `hero.html`.
2. **Search** — a fast client-side command palette over teams / heroes /
   matches / maps. All the data is already in `window.OWCS_PUBLIC`.
3. **Head-to-head / matchup view** — two teams' comp tendencies side by
   side, built from existing verified comps.
4. **Shareable evidence cards** — a static OG-image per confirmed swap /
   comp for social sharing (pre-rendered, no runtime).
5. **Patch filtering** — `patches` is an empty array in the contract today;
   wire it once patch metadata is recorded so stats can be patch-scoped.

---

## Operating model (how updates ship)

Established and working:

```
push to a branch → open PR → CI (test suite + packaging) → merge → Pages deploy
```

- Every merge to `main` redeploys the live site automatically.
- Until the P0.1 auto-merge toggle is on, merges happen manually on green
  (fast, but a person/agent has to click). After it, fully hands-off.
- The committed `data/owcs.sqlite` + `public_data.v1.js` mean a fresh clone
  renders the real milestone with no video processing.

## Hard constraints to never break

- $0 / no-build / GitHub-Pages-safe. No server, no framework, no paid APIs.
- Comps only from CV detection (staged through review) or manual
  correction — **never** from FACEIT/match facts.
- Only `reviewed` / `auto-high` snapshots render publicly.
- Every comp links comp → run → frames → crops → review status.
- Assets: real verified imagery or an intentional fallback — never a
  broken image, never a guessed logo.
- Reduced-motion users get a complete, static, professional experience.

---

_Priorities are a suggestion, not a contract. If you only do one thing
next: **finish the CR-ZETA ingest (P1.1)** — it's half-built and unlocks
the most visible improvement per hour spent._
