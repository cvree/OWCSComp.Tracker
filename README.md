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

## The product

There is one product and one workflow, and every page belongs to a step of
it:

**Submit a game → Automatic processing → Review what was detected →
Approve or correct → Published match stats**

The navigation has five entries, in that order. Everything else is reached
from the screen it belongs to.

| page | what it is for |
|---|---|
| `index.html` | **Dashboard** — one obvious "Submit a game", what the unattended scan filled in since anyone last looked, what needs a person, what is processing, what is blocked, what was recently published, and a small honest system-health strip. |
| `games.html` | **Games** — one row per game whatever state it is in, including every broadcast the scheduled scan found on its own (grouped separately and labelled as found, not read). This replaced the separate matches / runs / sources / tournaments lists, which were four views of the same thing. Also carries the official season schedule, collapsed, with how many broadcasts have already been found for each event. |
| `submit.html` | **Submit** — one required field. The link is classified as you type (offline; a connected tracker's own classifier takes over when there is one), known broadcasts autofill the rest, advanced options stay shut, and there is exactly one final button. |
| `review.html` | **Review** — the human gate, and the best screen in the product. Every detection beside the frame it was read from, confidence as a bar, a fast hero picker, whole-map and whole-line-up approval, swap and map-boundary confirmation, and a full keyboard (`j`/`k` move, `a` approve, `c` correct, `f` flag, `⇧A` approve every clean read). |
| `game.html` | **One game**, in whatever state it is in — including `?video=<id>`, a broadcast the scan found that nobody has submitted, which shows exactly what is known and what is not: the six-step progression with live output while it runs, the blocker and its exact fix when it is stuck, and the published maps and line-ups once it is approved. |
| `stats.html` | **Stats** — heroes, compositions, maps, teams and swaps as five tabs over the same approved dataset. |
| `teams.html` · `team.html` · `hero.html` | nested profiles, reached from a game or a stats table. |
| `how-it-works.html` | the explainer, in plain language, rendering the product's own step definitions so it cannot drift. |
| `tools.html` | everything technical, off the main path: automatic-discovery health (when the scan last ran, what it found, every source error), broadcast sources, processing history, calibration health, download authentication, storage, publishing, the evidence archive. |
| `calibrate.html` | the in-browser calibration wizard — the one operator tool that works fully on the published copy. |
| `404.html` | the legacy-URL map. Every page the redesign removed names where it went, and goes there. |

Three datasets, never merged: `assets/data/public_data.v1.js` is the
**published** record (approved, evidence-backed, safe to state as fact),
`assets/js/data.js` is the **working** record (runs, sources, what is
happening), and `assets/data/discovered.v1.js` is the **discovery** record
— what the unattended scan found and what each title says about itself,
never a fact a person confirmed. The demo fixture is a test asset under
`pipeline/fixtures/` and no page can load it — an empty export renders an
honest empty state, never invented games.

**The site fills itself** (`pipeline/automation/self_fill.py` →
`assets/data/discovered.v1.js`, rebuilt by the scheduled `match-finder`
workflow): the finder has always scanned every verified official channel
on free sources, but its snapshot was rendered only by the operator's own
localhost portal — so the live site kept showing whatever a human last
pushed while dozens of real broadcasts sat in a committed file. `self_fill`
is a pure, offline function of four committed files (the scan, the official
calendar, the published dataset, and the workflow's own cron) that produces
the layer the public pages render: every discovered broadcast with the
event / stage / day / region **its own title states** (and `null` where it
does not), the official calendar event whose window and region actually
contain it (with `matchedBy` naming the evidence, and a refusal plus its
reason when several events fit), its lifecycle state joined against the
published record, an event rollup, and the one action that moves it
forward. Discovered broadcasts appear on the dashboard ("Filling itself"),
in the games list as their own group, on `submit.html` as one-click
suggestions, in site search, and each on its own `game.html?video=<id>`
page. Finding is not reading: nothing here can publish a composition,
approve a source, or touch the DB — the human review gate is unchanged.
`python3 pipeline/automation/cli.py self-fill [--check]` rebuilds or gates
it; CI fails if the committed layer is not what a fresh build produces.

**Site search** (`assets/js/app/shell.js`): every page carries a search
button plus `/` and ⌘/Ctrl-K. It indexes games, teams, heroes, maps and the
product's own pages straight from the export — no service, no index file to
rebuild. Arrow keys move, Enter opens, Esc closes. Matching is fuzzy
(Fuse.js, vendored), because "qadsia" and "midseson" are what people
actually type; each row carries the thing it is — the hero's portrait, the
team's crest, the game's state chip — so a result is recognised rather than
read. Without the vendor file it falls back to substring matching.

**Hero art** (`pipeline/hero_crop.py`): Blizzard's official hero pages
serve one wide splash per hero, and it is a *scene* — the hero stands
off-centre in a depth-of-field-blurred map location and the bottom third is
flat colour for Blizzard's own page text. Cropping that down the middle,
which is what this repo did until 2026-08, produced portraits that were
mostly empty backdrop with the head clipped off the top edge: unreadable at
the 28–40 px the comp strips and stats tables actually render them at. The
cropper now finds the hero first — the flat band has no detail, and the
hero is the only thing in focus, so a block-wise sharpness map isolates the
figure, and the figure is separated from its props (a turret, a ship, a
raised gauntlet) by picking the heaviest run of columns. There is no
per-hero table: a hero added tomorrow frames itself. Four variants come out
of it — `artwork` (untouched), `card` (3:2, for banners and hover cards),
`portrait` (320², for tiles) and `icon` (96²). `python3
pipeline/build_hero_official_assets.py --recrop` re-derives all of them
from the committed artwork with no network; `pipeline/test_hero_crop.py`
asserts the framing rather than just the file sizes.

**Evidence viewer** (`assets/js/app/evidence.js`): a broadcast frame is
1280×720 and the thing being judged inside it is a 40 px hero portrait, so
the viewer zooms and pans (Panzoom, vendored) — wheel to zoom, double-click
for 3× under the pointer, `←`/`→` to walk every piece of evidence on the
page without closing it, and the file path always on screen. Without the
vendor file the frame still opens, fit to the window.

**No third-party requests.** Every byte a page loads comes from this
origin, including the three webfonts (`assets/fonts/`, SIL OFL, vendored
via @fontsource — see `assets/css/fonts.css`). They used to come from
fonts.googleapis.com, which meant a render-blocking stylesheet on someone
else's origin before any of this site's own CSS could apply, and a product
that did not look like itself wherever that CDN is unreachable.
`pipeline/test_site.py` gates it with no exceptions.

**Motion** (`assets/js/app/motion.js`): Lenis smooth scroll, one entrance
reveal per element, a scroll hairline, and nothing else. The WebGL ambience
layer (three.js + Vanta, 630 KB) and the cursor effects were removed in the
2026 redesign: they cost more than the rest of the page combined and made
targets move away from the pointer. `prefers-reduced-motion` and Save-Data
disable everything, and a reveal can never permanently withhold content.

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

## Optional: the language-model advisor

Two jobs here are language problems wearing computer-vision clothing: an
OCR'd nameplate reading `TW1STED M1NDS` that `difflib` cannot place, and a
calibration refusal like `b3 portrait box has almost no detail (texture 31)`
that is precise, correct and useless to a human. `pipeline/llm_advisor.py`
optionally points a model at those two, and **only** those two.

Add a Claude, OpenAI or Gemini key in the control room's **Credentials**
page (or export `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`);
it is stored in the same vault as the FACEIT and GitHub tokens, with the
same honest protection labels. Then:

```bash
python3 pipeline/llm_advisor.py --check          # which providers are set
python3 pipeline/calibrate_source.py ... --explain
```

**Where the boundary is, and why the code — not the prompt — enforces it:**

- **It never measures anything.** HUD geometry comes from the RANSAC grid
  fit in `calibrate_source.py`, which is deterministic and reproducible from
  the VOD. Models are bad at pixel coordinates; this one is never asked for
  any. `--explain` rewrites the *wording* of a result, never the result.
- **Suggestions are closed-vocabulary.** `suggest_team`/`suggest_player` may
  only return an id from the list they were handed. A model that answers
  with anything else — an invented team, a reformatted id — is refused and
  downgraded to an abstention. It is structurally unable to invent a team or
  a person, which is the guarantee `player_identify.py` exists to protect.
- **It only fills gaps.** Both suggesters take the deterministic result and
  refuse to run if it already resolved. The fuzzy matcher is never
  second-guessed.
- **Nothing it says is a fact.** Every return value carries `advisory:
  true`, `binding: false` and a `provenance` block naming the provider and
  model. `assert_never_binding()` raises if one reaches a persistence path.
  A suggestion is addressed to the same human gate that already confirms
  template labels — it is not written to the DB, a layout, or an export, and
  it never appears on the public site.
- **It is off by default and free to ignore.** With no key, calibration
  triage falls back to a rule table that ships to everyone, and name
  matching behaves exactly as it always has. There is no code path where a
  missing key is an error, and a dead or misconfigured provider degrades to
  those same offline rules rather than failing a run.

No provider SDK enters `requirements.txt` — transport is stdlib `urllib`,
injectable, so all 74 checks in `pipeline/test_llm_advisor.py` run offline
with no key. Most of them test the refusals.

The public site does not participate in any of this. It is static, has no
server to hold a key, and has no business asking a visitor for one.

---

## Quick start

Requirements: **Python 3.12+**, `pip install -r requirements.txt`, and
**ffmpeg** on PATH (system package). VOD download additionally needs the
`yt-dlp` binary.

### Preview the site locally

```bash
python pipeline/serve.py            # the dashboard at http://localhost:8000/
                                    # submit a game at /submit.html
                                    # review detections at /review.html
# or any static server (read-only: no video can be processed):
python -m http.server 8000          # then open game.html?id=m-qad-twis-s2po
```

Every page detects whether a tracker is running behind it and says so.
Served by `serve.py` the buttons really work; served statically they explain
that nothing is listening and hand you the exact command instead of
pretending.

### Run the tests (offline, no network, no VOD)

```bash
for t in pipeline/test_*.py; do python "$t"; done   # 101 suites
python pipeline/check_packaging.py                  # reproducibility gate
```

### Regenerate the public data from the DB

```bash
python pipeline/export_data.py --public   # writes assets/data/public_data.v1.js
```

The committed `data/owcs.sqlite` already contains the ingested Nepal
milestone, so this works on a fresh clone without re-processing any video.

### Acquisition: sparse first, download only what needs depth

The pipeline no longer fetches a broadcast to look at it. The shape is:

```
YouTube VOD
  └─ sparse remote frames  (pipeline/remote_frames.py — HTTP range, ~60s apart)
      └─ gameplay filtering (gameplay_state.py, unchanged)
          └─ automatic HUD calibration (calibrate_source.py, unchanged)
              └─ download ONLY the identified map window
                  └─ baseline hero detection      ┐  ingest_map.py,
                      └─ 1s dense recapture       ├─ the adaptive pass,
                          └─ temporal consensus   ┘  unchanged
                              └─ evidence → review → publish
```

Calibrating a HUD needs about a dozen frames. It used to cost the whole
broadcast; it now costs those frames:

```bash
# calibrate a broadcast the tracker has never seen — no VOD download
python pipeline/calibrate_remote.py --url "<youtube-url>" \
  --source-id owcs-jksix-qwc --out layouts/owcs_jksix_qwc.json \
  --windows-out work/owcs-jksix-qwc.windows.json
```

It samples at 60s, densifies to 30s and then only around offsets that
already showed HUD structure, and stops as soon as a trial calibration
clears `calibrate_source`'s own confidence floor with margin. Frames are
cached under `work/remote_frames/<video>/<height>p/` keyed by video +
timestamp + resolution, so a re-run costs nothing. `--windows-out` records
where the live play was, which is the window the deep pass should download.

`pipeline/capture.py` samples remotely by default too; `--full-download`
restores the old behaviour for a CDN that will not serve byte ranges.

Measure it yourself — the benchmark runs both paths and compares the
layouts they produce:

```bash
python pipeline/benchmark_capture.py --fixture          # offline, real HTTP
python pipeline/benchmark_capture.py --url "<youtube-url>" --yes
```

### Re-process the Nepal map from scratch

Requires the local clip (`work/clips/nepal_720p.mp4`; the 7 GB VOD and clips
are **not** shipped — see *Downloading a VOD* below). Clip `t=0` maps to
stream offset 1795 s. The calibration step below is the pre-2026 form, kept
because it is exactly reproducible from the committed clip; `calibrate_remote`
above is what a NEW broadcast should use.

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
quota): `python pipeline/automation/cli.py find-matches`, or the "broadcasts we
already know about" shortcuts on the submit form. `--queue-likely` is the agentic mode:
every likely broadcast is registered through the same intake gate as a
pasted URL, metadata only, nothing downloaded or approved.

**The whole pipeline runs from the browser**: `python pipeline/serve.py`,
open `submit.html`, paste the link or pick a found broadcast, watch the live
log on the game's own page — then drive every stage from the product itself (retry, autopilot, approve source, approve
layout, propose/accept identity, detect, publish, export). Audited
approvals require a typed name and a confirm; nothing is ever approved
automatically. `tools.html` carries the **download-authentication panel**
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

That is the DEEP pass, and it now only happens for a window the sparse scan
has already identified as live play. If even the range-based sparse scan is
refused on a given broadcast, `capture.py --full-download` and the clip
route above still work — `remote_frames` reports the failure and says so
rather than silently falling back to gigabytes.

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
- **The capture/FACEIT workflows are manual-only** (`workflow_dispatch`).
  Those auto-commit pipelines are intentionally off-cron so they can't race
  CI or mutate the committed milestone unattended. The two that DO run on a
  schedule are read-only by construction: `discovery` (registry/calendar
  sync) and `match-finder` (broadcast discovery + the site's discovery
  layer). Neither downloads a video, writes a composition, or approves a
  source.
- **Discovery is not detection.** The site now fills itself with broadcasts
  nobody submitted, and every one of them is labelled as found rather than
  read. A discovered row carries the video's own metadata and a reading of
  its title — never a composition, a score or a result. Turning one into
  match data still requires processing and a human review.
- **The LLM advisor is a convenience, not a measurement**, and is scoped to
  stay that way — closed-vocabulary suggestions behind a human gate, plus
  wording help on calibration failures. It has been exercised against a
  fake provider across 74 offline checks, but its *live* suggestion quality
  has not been measured against a labelled set of real OCR misreads. Until
  it has, treat a suggestion as a prompt to go look, not as evidence. It
  contributes nothing to any published number.

---

## Repo layout

```
owcs-comp-tracker/
├── *.html                       # public pages + control-room pages
├── assets/
│   ├── css/ js/                 # site logic (public/ = fan pages)
│   ├── fonts/                   # the three webfonts, self-hosted (SIL OFL)
│   ├── vendor/                  # vendored OSS builds — no CDN, no build step
│   │                            #   fuse.basic.min.js  fuzzy search
│   │                            #   panzoom.min.js     evidence zoom/pan
│   │                            #   gsap + ScrollTrigger, lenis  motion
│   └── data/
│       ├── public_data.v1.js    # PRODUCTION export (real Nepal data)
│       ├── discovered.v1.js     # what the unattended scan found (self-fill)
│       ├── matchfinder.v1.json  # the raw scan the layer above is built from
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
