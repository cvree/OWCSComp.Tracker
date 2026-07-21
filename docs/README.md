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

## The pipeline (source → site)

```
VOD ──► calibrate_source.py ──► layouts/<src>.json  (+ calibration sheet)
        (finds the 10 portrait boxes computationally, refuses if unsure)

VOD ──► harvest_templates.py ──► templates/<src>/   (real portraits, variants)
        (--cluster, human-label once, --labels emits per-hero templates)

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
| `harvest_templates.py` | clusters real slot crops across a map, human labels once, emits multi-variant per-hero templates (alive/dead/ult states) |
| `detect.py` | ranked candidate + runner-up + margin per slot; returns `UNKNOWN` instead of guessing |
| `ingest_map.py` | full-map driver: adaptive sampling, per-slot temporal hysteresis, emblem-based rounds, side-swap tracking, evidence crops, staged idempotent DB writes |
| `build_ingest_report.py` | the full-map report + change-point review pages |
| `export_data.py` | `--public` writes the production `public_data.v1.js` from the DB (comps only from approved stints with evidence chains) |
| `check_packaging.py` | reproducibility gate: template dirs, marker assets, DB milestone, evidence paths, page wiring |

---

## Quick start

Requirements: **Python 3.12+**, `pip install -r requirements.txt`, and
**ffmpeg** on PATH (system package). VOD download additionally needs the
`yt-dlp` binary.

### Preview the site locally

```bash
python pipeline/serve.py            # control room at http://localhost:8000/run.html
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

### Process a NEW VOD/map

The workflow generalizes: calibrate → review the sheet → harvest + label
templates → (once per broadcast package) cut reject markers + a
`round_emblem` rect → ingest → review `report.html`/`review.html` →
`export_data.py --public`. See the **CURRENT STATUS** section of
[`HANDOFF.md`](HANDOFF.md) for the step-by-step and the exact download
strategy that works around googlevideo throttling.

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
- **Template labeling is human-in-the-loop** by design (evidence recorded in
  `work/nepal_labels.json`); the pipeline quarantines what it can't prove.
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
