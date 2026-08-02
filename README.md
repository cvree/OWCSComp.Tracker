# OWCS Comp Tracker

A $0, no-build system that turns an **OWCS broadcast VOD** into an auditable
hero-composition timeline — automatic HUD calibration, live/replay frame
classification, template-matched hero detection with a temporal swap model,
a staged SQLite database, and a static fan-facing website that renders the
result with click-through evidence.

Everything downstream of the video is plain **Python + OpenCV** (detection)
and plain **HTML/CSS/JS** (site) — no frameworks, no server, no paid
services. The site deploys free on GitHub Pages.

---

## ✅ Verified milestone — Al Qadsiah vs Twisted Minds, Nepal

The full pipeline has processed one complete real map end to end:

- **Match:** Al Qadsiah vs Twisted Minds — OWCS 2026 NA/EMEA Stage 2
  Playoffs Day 2 ([VOD](https://www.youtube.com/live/jkSiX___Qwc), Nepal,
  ~30:43–46:14).
- **Result:** **Twisted Minds win** (recorded as the map winner).
- **Coverage:** 3 control rounds, 299 frames sampled (196 hero-readable,
  103 skipped with reasons), 1,950 accepted slot reads.
- **Compositions:**
  - Al Qadsiah — Shion, Symmetra, Mauga, Kiriko, Juno
  - Twisted Minds — Sojourn, Symmetra, D.Va, Lúcio, Kiriko
- **Hero swaps (temporally confirmed, with before/after evidence crops):**
  ZOX **Juno → Lúcio @ ~39:54** (round 2), reverts to Juno during the
  round-3 setup, then **Juno → Lúcio @ ~42:55** (round 3).
- **Rejected noise:** 7 suspected swaps rejected (dead-portrait
  lookalikes, killcam artifacts) — none became false swaps.

This is live on the public pages against the **production** dataset
(`assets/data/public_data.v1.js`, `meta.demo=false`), not the demo fixture.
Full evidence: `reports/ingest/qad-twis-nepal/report.html` (full-map report)
and `review.html` (every confirmed/rejected change point with crops).

---

## The public site ("nocturne" redesign)

The fan-facing site is a dark-gothic esports intelligence surface — still
plain HTML/CSS/JS, still GitHub-Pages-safe, still evidence-first. Pages:

| page | what it shows |
|---|---|
| `index.html` | **the front door** — what the site is in one screen, the honest size of the dataset (computed, never rounded up), the featured verified map with both line-ups and its confirmed swaps, the five-step explanation, the published limits, and the way in to every surface. Reading the data never touches the pipeline. |
| `how-it-works.html` | **the manual** — what "verified" means, the five pipeline stages, the evidence chain, the swap rejection ledger, a glossary of every badge on the site (rendered by the site's own chip helpers so it can't drift), how the stats are counted, and what the tracker refuses to claim |
| `portal.html` | **the operator portal** — the paste-a-link box, the auto match finder (every OWCS broadcast, one click to ingest), the live pipeline with every human gate as a button. Static hosting keeps it read-only and builds the command instead. (`intake.html` redirects here.) |
| `tournaments.html` / `tournament.html` | events, brackets, standings |
| `calendar.html` | the season by day: official stage windows (from `config/owcs_calendar.json`, with their unverified-dates status shown honestly), the month grid of tracked matches, a "next up" list, and "time TBA" wherever only a date is known |
| `matches.html` / `match.html` | schedule and the match page with comps, **confirmed swaps with before/after crops**, bans, evidence chain, review queue |
| `teams.html` / `team.html` | directory + team dossier (record, hero pool, calibration provenance) |
| `heroes.html` / `hero.html` | hero analytics directory + per-hero dossier (rates, teams, swap activity, portrait provenance) |
| `comps.html` | every verified five-hero lineup, grouped and counted, with map results and evidence links |
| `swaps.html` | swap intelligence — confirmed swaps with evidence, plus the rejected-noise honesty ledger |
| `maps.html` / `stats.html` | map meta + the sortable pick/win-rate table with drill-downs |

**Site search** (`assets/js/public/shell.js`): every public page carries a
search button plus `/` and ⌘/Ctrl-K shortcuts. It indexes matches, teams,
heroes, maps, tournaments and the site's own pages straight from
`public_data.v1.js` — no service, no index file to rebuild — and jumps to
the page. Arrow keys move, Enter opens, Esc closes.

Screenshots live in [`docs/screenshots/`](docs/screenshots/).

**Asset honesty** (`assets/data/asset_manifest.json`, built by
`pipeline/build_asset_manifest.py`, validated by `pipeline/test_assets.py`):
hero portraits are real broadcast crops harvested by the pipeline; heroes
without one get a designed role monogram, teams without a **verified**
official mark get a designed crest — never a guessed logo, never a broken
image, never hotlinked art. Candidate official sources for team marks are
documented in `assets/data/team_asset_sources.json` for a network-enabled
fetch + review pass.

**Official hero presentation art** (Phase D3, `pipeline/build_hero_official_assets.py`,
`assets/img/heroes/official/<id>/`): separate from the evidence-only
broadcast-crop portraits above. Every one of the 52 public hero ids resolves
to real portrait/artwork/icon assets fetched from Blizzard's own official
hero pages (overwatch.blizzard.com) — the one hero-specific image is matched
by its own embedded name against the hero's real name, never a guess from a
generic shared page image — or an intentional "no official source resolved"
fallback otherwise. WebP variants ship alongside; AVIF is explicitly `null`
(no encoder in this stdlib/existing-deps environment) rather than a
fabricated claim. Used only on encyclopedia-style surfaces (the hero
dossier's "Official presentation" panel, the hero directory's "not yet
sighted" cards) — never blended with the evidence-only portrait/provenance
used anywhere a verified comp pick is being shown. Non-commercial fan use
per Blizzard's Fan Content Policy; validated by
`pipeline/test_hero_official_assets.py`.

**Motion**: one Lenis instance driven by one GSAP ticker loop
(`assets/js/motion.js`), ScrollTrigger synced from Lenis' scroll event,
native touch scrolling, and a complete static experience under
`prefers-reduced-motion`. No scroll-jacking anywhere.

**Swap data contract**: `heroSwaps` in `public_data.v1.js` is exported
verbatim from the DB's temporal-consensus verdicts — confirmed rows carry
before/after evidence crops, rejected rows carry the reason they were
thrown out (see `docs/PUBLIC_DATA_CONTRACT.md`).

---

## The pipeline (source → site)

```
VOD ──► calibrate_source.py ──► layouts/<src>.json  (+ calibration sheet)
        (finds the 10 portrait boxes computationally, refuses if unsure)

VOD ──► harvest_templates.py ──► templates/<src>/   (real portraits, variants)
        (--cluster, human-label once, --labels emits per-hero templates)

                     ── the whole loop, one command: ──
        python3 pipeline/template_forge.py --from-report reports/ingest/<id> \
            --layout layouts/<id>.json --promote-to templates/<id>

reports/ingest/<id>/ ──► template_evidence.py ──► labelled crops + timestamps
        (stint consensus over hundreds of agreeing frames, marked as such —
         never presented as human labelling)
                     ──► template_forge.py ──► staging templates + provenance
        (build slice / dead zone / holdout, quality gate, no hero promoted
         until it survives held-out validation)
                     ──► template_validate.py ──► reports/validation/<id>.json
        (per hero: VALIDATED / WEAK / FAILED / UNVERIFIABLE, plus the
         leave-one-hero-out test that an UNCOVERED hero reads UNKNOWN)

VOD ──► ingest_map.py ──► SQLite (staged, idempotent) + reports/ingest/<id>/
        gameplay_state.py  : live vs replay/scoreboard/desk/transition
        detect.py          : ranked template match, runner-up, margin, UNKNOWN
        temporal consensus : hysteresis swaps (persistence + displacement)

SQLite ──► export_data.py --public ──► assets/data/public_data.v1.js
        ──► match.html / stats.html / … render the real data
```

Key modules (all under `pipeline/`):

| file | role |
|---|---|
| `calibrate_source.py` | computational HUD calibration (HSV chip rows + RANSAC grid fit + pixel-evidence verification); writes a reusable, resolution-independent layout profile + annotated sheet; **refuses** below confidence 0.55 with reasons |
| `gameplay_state.py` | structural gameplay filter + layout reject markers (HIGHLIGHTS / REPLAY / scoreboard) so replays and scoreboards never create comps |
| `harvest_templates.py` | clusters real slot crops across a map, human labels once, emits multi-variant per-hero templates (alive/dead/ult states); quality-gated before the diversity pick, and a second harvest replaces only the heroes it labels |
| `template_quality.py` | the gate a crop must pass to become a template: size, sharpness, contrast, flatness, overlay bands, confusion with another hero's template, and traceable provenance. ACCEPT / REVIEW / REJECT |
| `template_evidence.py` | labelled crops from a real ingest run, by stint consensus. Every crop records `labelSource`, so nothing downstream can present detector consensus as human ground truth |
| `template_forge.py` | build → gate → provenance → held-out validation → gated promotion, in the one order that produces a trustworthy result. Templates are cut only from a build slice; a dead zone separates it from the holdout |
| `template_validate.py` | scores a set on frames it has never seen. `UNVERIFIABLE` when provenance cannot prove separation — never rounded up to a pass. Includes the leave-one-hero-out false-match test |
| `hero_coverage.py` | 52-hero readiness per package: covered / sound / traceable / proven, never collapsed into one number, with the blocker and next action per hero |
| `hero_gap_finder.py` | where the missing heroes have already been recorded (from `hero_stints`), and what is in footage that a package cannot name (persistent UNKNOWN clusters → unlabelled review candidates) |
| `portrait_roi.py` | finds the portrait sub-rectangle of a calibrated slot from the footage itself, so a template describes the hero rather than the player currently on that hero |
| `detect.py` | ranked candidate + runner-up + margin per slot; returns `UNKNOWN` instead of guessing. Per-layout detector profile (`portrait_roi`, `unknown_floor`, `min_margin`) |
| `ingest_map.py` | full-map driver: adaptive sampling, per-slot temporal hysteresis, emblem-based rounds, side-swap tracking, evidence crops, staged idempotent DB writes |
| `build_ingest_report.py` | the full-map report + change-point review pages |
| `export_data.py` | `--public` writes the production `public_data.v1.js` from the DB (comps only from approved stints with evidence chains) |
| `check_packaging.py` | reproducibility gate: template dirs, marker assets, DB milestone, evidence paths, page wiring |

---

## Install it as a Windows application

Everything below this section is the developer path. If you just want to
process broadcasts, there is a one-file installer instead — no Python, no
ffmpeg, no terminal, no configuration files.

1. Download `OWCSCompTracker-<version>-Setup.exe` from
   [Releases](https://github.com/cvree/owcscomp.tracker/releases) and run it.
   It installs per-user, so it needs no administrator rights.
2. The graphical wizard checks the machine, sets a storage budget, takes any
   optional API keys, offers to start with Windows, and runs a **real**
   end-to-end test that builds a broadcast on your PC and drives the whole
   pipeline over it.
3. Paste a link in the control room: a YouTube VOD or playlist, a FACEIT
   matchroom or tournament, or a video file on your PC.

Processing runs in a background service, so closing the control room window
changes nothing and a reboot resumes where it left off. Uncertain detections
go to a graphical review inbox with their evidence crops attached; only
repeated, high-confidence readings publish on their own.

Architecture, guarantees and build instructions:
[`docs/WINDOWS-APP.md`](docs/WINDOWS-APP.md).

---

## Quick start

Requirements: **Python 3.12+**, `pip install -r requirements.txt`, and
**ffmpeg** on PATH (system package). VOD download additionally needs the
`yt-dlp` binary.

### Preview the site locally

```bash
python pipeline/serve.py            # public site at http://localhost:8000/
                                    # operator portal at /portal.html
# or any static server:
python -m http.server 8000          # then open match.html?id=m-qad-twis-s2po
```

The public pages load `assets/data/public_data.v1.js` first and fall back to
the demo fixture only if it is absent — so a fresh clone shows the real
Nepal match immediately.

### Run the tests (offline, no network, no VOD)

```bash
for t in pipeline/test_*.py; do python "$t"; done   # 29 suites
python pipeline/check_packaging.py                  # reproducibility gate
```

### Regenerate the public data from the DB

```bash
python pipeline/export_data.py --public   # writes assets/data/public_data.v1.js
```

The committed `data/owcs.sqlite` already contains the ingested Nepal
milestone, so this works on a fresh clone without re-processing any video.

### Re-process the Nepal map from scratch

Requires the local clip (`work/clips/nepal_720p.mp4`; the 7 GB VOD and clips
are **not** shipped — see *Downloading a VOD* below). Clip `t=0` maps to
stream offset 1795 s.

```bash
python pipeline/calibrate_source.py --clip work/clips/nepal_720p.mp4 \
  --times 100,150,250,350,500,650,800,900 \
  --source-id owcs-jksix-qwc --out layouts/owcs_jksix_qwc.json

python pipeline/harvest_templates.py --clip work/clips/nepal_720p.mp4 \
  --times 60:980:10 --layout layouts/owcs_jksix_qwc.json \
  --out templates/owcs_jksix_qwc --cluster
python pipeline/harvest_templates.py --layout layouts/owcs_jksix_qwc.json \
  --out templates/owcs_jksix_qwc --labels work/nepal_labels.json --variants 5

python pipeline/ingest_map.py --clip work/clips/nepal_720p.mp4 \
  --clip-offset 1795 --start 1805 --end 2778 \
  --layout layouts/owcs_jksix_qwc.json --source-id owcs-jksix-qwc \
  --ingest-id qad-twis-nepal --match m-qad-twis-s2po --map-order 1 \
  --map-id nepal --map-winner twis --team-a qadsiah --team-b twis \
  --every 5 --write

python pipeline/export_data.py --public
```

Reruns are idempotent — the same `(match, map, detector_version)` replaces
its own CV rows and never touches manual/reviewed rows.

### Process a NEW VOD/map — paste one link

```bash
python pipeline/automation/cli.py convert-link --url "<youtube-url>" --requested-by "<you>"
```

The free-agent autopilot runs every automatic stage in a row — metadata +
registry authorization, full-VOD download + 360p scan proxy, layout
resolution, segmentation, identity proposals, segment-clip extraction —
and stops honestly at the first gate that belongs to a human (source /
layout / segment review / detection review / publication), printing the
exact next command. After clearing a gate, `autopilot --job <key>`
re-enters the loop; `--auto-accept` additionally accepts clean machine
identity proposals through the same `accept-proposed` gate a human uses.

Don't have a link? The **auto match finder** finds one for you, on
permanently free sources (channel RSS + the streams tab — no API key, no
quota): `python pipeline/automation/cli.py find-matches`, or the "Scan for
new matches" button on the portal. `--queue-likely` is the agentic mode:
every likely broadcast is registered through the same intake gate as a
pasted URL, metadata only, nothing downloaded or approved.

**The whole pipeline runs from the browser**: `python pipeline/serve.py`,
open `portal.html` (the operator portal — `index.html` is the public site),
paste the link or click **Ingest** on a found match, watch the live log — then drive every
stage from the page itself (retry, autopilot, approve source, approve
layout, propose/accept identity, detect, publish, export). Audited
approvals require a typed name and a confirm; nothing is ever approved
automatically. The page also carries a **download-authentication panel**
(yt-dlp version, cookie mode, JS runtime, `curl_cffi`, API-key presence,
last probe result, live fallback rung, per-layout detection readiness) —
without exposing a single secret value. On static hosting it stays
read-only and only builds the command for you to copy.

### When YouTube refuses the download

```bash
python pipeline/automation/cli.py download-status   # stack + auth + ladder
python pipeline/automation/cli.py media-probe --url "<youtube-url>"
```

Downloads walk a bounded six-rung fallback ladder (normal → refresh signed
URL → force IPv4 → browser cookies → cookies+impersonation → alternate
≤720p format). **Browser-cookie access is off by default**; enable rungs
4–5 with `OWCS_YTDLP_COOKIES_FROM_BROWSER=chrome|edge|firefox`. Cookies are
read from the browser at request time — this project never writes, copies,
prints or commits a cookie file, and redacts signed media URLs, cookie
sources and profile paths from every log and export. Full details in
[`docs/AUTOMATION.md`](docs/AUTOMATION.md). See the
**Match-day runbook** in [`docs/AUTOMATION.md`](docs/AUTOMATION.md) and
the **CURRENT STATUS** section of [`HANDOFF.md`](HANDOFF.md) for the
step-by-step and the exact download strategy that works around googlevideo
throttling.

#### Downloading a VOD (this machine's quirks)

`yt-dlp` needs `--js-runtimes node` here, and googlevideo 403s/throttles
direct-URL and `--download-sections` fetches. What works: download the full
720p60 file (chunked, fast; loop to resume the `.part` after 403s — only the
map's byte prefix is needed), then cut the window locally with ffmpeg.

---

## Data model — facts vs tracker comps (unchanged core principle)

Two kinds of data are kept strictly separate everywhere:

- **FACEIT / match facts** — teams, score, map order, bans, replay codes,
  rosters. Never produce hero picks.
- **Tracker comps** — openers, played heroes, swaps, timelines, and the
  pick/win rates derived from them. Come only from CV detection (staged
  through review) or manual correction.

Public pages render a comp **only** if it is human-reviewed or cleared the
auto-high confidence gate, and every comp links comp → run → frames → crops
→ review status. If the evidence chain is missing, it is not shown.

New CV tables (`pipeline/schema.sql`): `ingest_runs`, `slot_observations`,
`map_rounds`, `hero_stints`, `hero_swaps`.

---

## What is NOT production-ready (honest limits)

- **One map, one match.** Only the Nepal map of this match is ingested.
  Series-level scores are not recorded.
- **Operator-supplied facts.** The map winner comes from `--map-winner`, not
  OCR. Per-round control percentages are not read (the map's `scoreDetail`
  renders its honest fallback).
- **Round times ±1 sample.** Round boundaries come from clustering the
  center point-emblem at the 5 s sample rate.
- **Hero coverage is 8 of 52 on the best package, not 52 of 52.** Those
  eight (`templates/owcs_jksix_qwc`) are fully provenanced and validated on
  held-out frames from the Nepal broadcast — 1,861 trials, zero confident
  wrong answers — and the control room's **Hero coverage** page shows the
  real number per package. The other 44 heroes have never appeared in a
  broadcast this pipeline has processed, so no amount of reprocessing
  covers them; they need footage that contains them.
  `pipeline/hero_gap_finder.py` says which is which.
- **`juno` and `kiriko` are confusable in that package.** With `juno`
  removed from the set, 38% of real `juno` portraits are read as `kiriko`
  rather than UNKNOWN — they are both a dark-haired figure on a light field
  at 35x35 in grayscale. Named in the validation report rather than averaged
  into a pass. A colour-based veto was built, measured against the real
  crops, and **removed** because it blocked none of the actual leaks while
  vetoing legitimate reads; the honest fix is a better `juno` template from
  a second broadcast.
- **The detector thresholds are fitted to one broadcast.** `unknown_floor`
  0.60 on `owcs_jksix_qwc` was chosen from 2,008 held-out crops of a single
  source. Re-measure when a second one is harvested.
- **Template labeling is human-in-the-loop** by design (evidence recorded in
  `work/nepal_labels.json`); the pipeline quarantines what it can't prove.
  Evidence labels produced by `template_evidence.py` are **detector
  consensus over a temporal stint**, not a human's eyes — strong (hundreds
  of agreeing frames decide each label, so no single template can influence
  the crop it is tested on) but it cannot catch a systematically mislabelled
  set. Every crop records which it is.
- **720p capture.** The layout profile is resolution-independent, but the
  reject-marker template crops are cut at 720p (re-cut for other
  resolutions).
- **Scheduled GitHub workflows are manual-only** (`workflow_dispatch`). The
  capture/FACEIT auto-commit pipelines are intentionally off-cron so they
  can't race CI or mutate the committed milestone unattended.

---

## Repo layout

```
owcs-comp-tracker/
├── *.html                       # public pages + control-room pages
├── assets/
│   ├── css/ js/                 # site logic (public/ = fan pages)
│   └── data/
│       ├── public_data.v1.js    # PRODUCTION export (real Nepal data)
│       └── public_fixture.v1.js # demo fixture (guarded fallback)
├── pipeline/                    # the whole Python pipeline + tests
├── layouts/                     # calibrated HUD profiles + marker crops
├── templates/                   # per-source hero portrait template sets
├── reports/                     # milestone evidence (ingest + calibration)
├── data/owcs.sqlite             # the staged database (committed)
├── docs/PUBLIC_DATA_CONTRACT.md # the public.v1 data contract
├── requirements.txt
└── HANDOFF.md                   # authoritative session log + workflow
```

Independent fan project — not affiliated with or endorsed by Blizzard,
Overwatch, OWCS, or FACEIT.
