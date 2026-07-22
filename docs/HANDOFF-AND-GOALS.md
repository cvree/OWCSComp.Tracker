# OWCS Comp Tracker — Handoff & Goals (START HERE for a new session)

Self-contained brief for a fresh chat to continue toward the **completely
automated website**. Read this top to bottom; it is current as of the merge
of PR #7 (`daac1e1`). When it conflicts with the older root `HANDOFF.md`,
this file wins.

---

## 0. What this project is

A fan site that records **what pro Overwatch teams actually play** — every
hero, every swap, per map — read from broadcast/POV video by a computer-
vision pipeline, with a hard rule: **nothing is shown unless it traces to a
frame.** No fabricated comps, ever.

**It is already LIVE:** https://cvree.github.io/OWCSComp.Tracker/
(GitHub Pages, free, auto-deploys on every push to `main`).

## 1. Architecture in one picture

```
 video ─▶ pipeline (Python CV, runs LOCALLY on a machine with the VOD)
            calibrate → harvest templates → ingest_map (read HUD) → review
            → data/owcs.sqlite → export_data.py --public
                          │  commit + push to main
                          ▼
      assets/data/public_data.v1.js  ─▶  pages.yml (GitHub Actions)
                          ▼
        https://cvree.github.io/OWCSComp.Tracker/  (static, no backend)
```

Two independent halves:
- **Public site** — pure static HTML/CSS/JS in the repo root, renders only
  from the committed `assets/data/public_data.v1.js`. No server. Changing it
  = editing files. It is DONE and live.
- **Pipeline** — Python in `pipeline/`, run locally (needs the video +
  ffmpeg + opencv). Produces the data file. This is where the automation
  work remains.

## 2. What is DONE and live (the site)

- Landing with live verified headline numbers; **Matches, Teams (directory +
  per-team story), Maps showcase, Stats, Tournaments** — all on the public
  "arena" shell (`assets/css/public.css`, `assets/js/public/*`).
- **Real hero portraits** upscaled from actual broadcast crops
  (`assets/img/heroes/`, `build_hero_portraits.py`), with provenance.
- **Clickable teams** → per-team page telling the story: recency,
  **autocalibration provenance** (confidence, HUD probe, template coverage),
  maps played with round counts, bans, portrait hero pool.
- **Stats drill-down**: click a hero → per-team pick/win breakdown.
- **Maps showcase** (`maps.html`): per-map pick-rate meta + bans, auto-built.
- **Calibration Lab** (`calibration.html`): per-source health dashboard.
- Deploy is fully automatic: **push to `main` → live in ~2 min.**
  `.nojekyll`, `404.html`, favicon all in place. Repo is public + Pages
  source = "GitHub Actions" (`pages.yml`, `enablement:true`).

## 3. What is DONE in the engine (pipeline)

- **Honest detector** (`detect.py`): `read_slot` returns UNKNOWN with a
  reason rather than guessing; carries a full `scores` map of every hero.
- **Role-aware 1/2/2 solver** (`comp_solver.py`): completes UNKNOWN/ambiguous
  slots from the 1-Tank/2-Damage/2-Support constraint; flags impossible
  comps as anomalies. Wired into `ingest_map.observe` as `obs["resolved"]`.
- **Phase gate** (`recapture_planner.py`, wired into `ingest_map.py`): only
  reads settled combat — skips setup, first ~8 s after a round unlock, the
  "finish + 10 s" swap window, highlights/replays/no-HUD; writes
  `phases.json` bookmarks + recapture windows. `--no-phase-gate` opts out.
- **Match confirmation** (`match_confirm.py`): team-name plates + bans vs the
  FACEIT-expected match (True/False/None; never blocks).
- **POV sources** (`obssojourn_source.py`): parse a video description →
  match + per-map windows + heroes; **POV-independent match signature** +
  **cross-POV comp de-dup** so multiple POVs of one game merge, not
  duplicate; flags brand-new maps.
- **One-command match ingest** (`ingest_obssojourn.py`): runs every seeded
  map window in order (dry-run default, `--write`, `--maps`, `--plan-only`).
- **Prep tools**: `prep_obssojourn_match.py` (seed a match skeleton from a
  description), `set_map_mode.py`.
- **Staged, idempotent DB** + gated promotion; `export_data.py --public`.
- **42 offline test suites**; CI (`ci.yml`, Python 3.12 + ffmpeg) runs them
  all + regenerates the public export and fails on drift. Only
  `test_calibration_tools.py` needs a real ffmpeg (present in CI).

## 4. THE NORTH STAR — "completely automated website"

Today the site auto-deploys, but **getting data into it is human-in-the-loop**:
someone downloads a video, calibrates once per format, labels templates,
runs the ingest, reviews, and pushes. Full automation means driving that
loop with as little human touch as the credibility rule allows.

**The invariant that shapes everything:** a comp only counts when it traces
to a frame AND clears review. "Automated" therefore means *auto-high
comps flow through untouched; only genuinely uncertain ones wait for a
human* — never "trust everything."

### The remaining gap, as concrete next goals (roughly in order)

1. **Auto-label templates (kills the one hands-on step).** `harvest_templates.py`
   stage 2 needs a human to map montage clusters → hero ids. Automate it:
   match each cluster against **reference portraits** (OverFast API hero art,
   already used once for the roster) by template/embedding similarity;
   `heroesPlayed` from the description constrains the POV player's own slot;
   the 1/2/2 solver + cross-frame consistency disambiguate the rest. Keep a
   review montage for the low-confidence few. *Biggest automation win.*

2. **Auto-calibrate end to end.** `calibrate_source.py` is already
   computational and refuses below confidence 0.55. Wire a fully-unattended
   path: pick spread gameplay timestamps automatically (from the phase gate),
   calibrate, and only surface the calibration sheet when confidence is low.
   Cut the ObsSojourn ban/team-name rects once (the one real-frame TODO from
   `docs/VISION-verification.md`) so `match_confirm` runs.

3. **Auto-promote by confidence.** Extend the promotion gate so comps that
   are role-resolved, multi-frame-consistent, and (ideally) cross-POV
   agreeing are written as `auto-high` (already a public-visible status);
   everything else lands in a **review queue**. This is what makes new
   matches appear with zero human touch when the reads are strong.

4. **A hosted/scheduled ingest (the real "automated pipeline").** The CV
   step needs ffmpeg + the VOD, so it can't run on Pages. Options:
   a **scheduled GitHub Action** (`pipeline.yml` exists, manual today) on a
   runner with ffmpeg that pulls new ObsSojourn uploads (a channel-scan step
   like `discover_owcs_vods.py`), ingests, auto-promotes high-confidence,
   commits the data, and opens a review PR for the rest. Cost/runtime and
   YouTube throttling are the constraints — see `HANDOFF.md` machine quirks.

5. **owtv.gg / FACEIT as authoritative fact sources.** Cross-confirm map
   winners, scores, and bans (the pipeline reads comps; results should come
   from a fact source). `ingest_faceit*.py` exists; owtv.gg is linked in
   ObsSojourn descriptions.

6. **Backfill breadth.** Right now only the Nepal match is public. Ingest the
   prepped **CR vs ZETA Korea Grand Final** (`m-cr-zeta-krgf`, all six map
   windows seeded — see §6) and more, so Stats/Maps/Teams have real depth.

## 5. How to work here (do this every time)

- **Branch → commit → PR → CI green → merge.** Never push straight to
  `main`. Merging to `main` auto-deploys the site.
- Before committing: run the suite —
  `for f in pipeline/test_*.py; do python3 "$f"; done` — expect all green
  except `test_calibration_tools.py` (needs ffmpeg). Then
  `python3 pipeline/export_data.py --public` and
  `python3 pipeline/check_packaging.py`.
- **Test side-effect gotcha:** running the full suite rewrites
  `templates/*.png` and `reports/capture_trial/index.html` (synthetic
  regen). Revert them before committing:
  `git checkout -- templates/*.png reports/capture_trial/index.html`.
- **Python 3.11 vs 3.12:** avoid multiline expressions inside f-strings
  (PEP 701) — they break on 3.11. This bit us twice; keep f-string
  expressions on one line.
- Every new module ships with a `test_*.py`; keep CI green.
- Engine changes DON'T change the live site until a match is re-ingested +
  re-exported — say so honestly.

## 6. The immediate next task (already teed up)

Ingest the **CR vs ZETA — OWCS Korea Stage 2 Grand Final** POV video
(`https://www.youtube.com/watch?v=is7eHd0nf84`). The match skeleton is
already seeded: match `m-cr-zeta-krgf`, teams `cr`/`zeta`, all six map
windows in `map_results`, source `owcs-is7ehd0nf84`, and the new map
`neonjunction` (mode still `Unknown` — set it). **Zero comps yet.**

Full copy-paste steps for a human: `docs/INGEST-CR-ZETA-KRGF.md`.
Design for POV sources: `docs/OBSSOJOURN-SOURCES.md`.
Vision/verification design: `docs/VISION-verification.md`.
Go-live/ops: `docs/GOING-LIVE.md`.

This needs a machine with the video + network (the CV sandbox has neither),
so it's the natural first thing to either hand to the user OR automate per
§4. **Automating steps 1–3 above is the highest-leverage path to the fully
automated website.**

## 7. Key files map

```
Public site (done):   index/tournaments/tournament/matches/match/teams/team/
                      maps/stats/calibration/404.html
                      assets/css/public.css  assets/js/public/*  assets/img/heroes/*
Data contract + export: docs/PUBLIC_DATA_CONTRACT.md  pipeline/export_data.py
Detection:            pipeline/detect.py  comp_solver.py  gameplay_state.py
Phase/verify:         pipeline/recapture_planner.py  match_confirm.py
Calibration:          pipeline/calibrate_source.py  calibration_status.py  calibration.html
Sources/ingest:       pipeline/obssojourn_source.py  prep_obssojourn_match.py
                      ingest_obssojourn.py  ingest_map.py  harvest_templates.py
DB + facts:           data/owcs.sqlite  pipeline/schema.sql  ingest_faceit*.py
CI/deploy:            .github/workflows/{ci,pages,pipeline,update-data}.yml
Docs:                 docs/HANDOFF-AND-GOALS.md (this)  + the four above
```
```
```
