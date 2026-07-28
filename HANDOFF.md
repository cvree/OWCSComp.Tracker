# OWCS Comp Tracker — Handoff (control room: no-terminal workflow)

## CURRENT STATUS (authoritative — 2026-07-27) — URL-only intake to audited public data (Phases 1-8)

> **This is the ONLY authoritative status section.** Every `## HISTORICAL
> (superseded — …)` section below is kept for context and is out of date
> wherever it conflicts with this one. When the code and any document
> disagree, the code wins.

### The headline: an operator now pastes ONE link

```powershell
python pipeline/automation/cli.py ingest-link --url "<youtube-url>"
```

That is the whole intake. No match id, channel id, video id, team names or
layout id from the operator — every one of those is either derived from the
URL and its public metadata, resolved automatically from evidence later, or
left honestly `UNKNOWN` for a human to fill in with a named reason.

### What was built this pass

**Phase 1 — URL intake + authorization** (`pipeline/automation/link_intake.py`)
Canonical parsing of every YouTube spelling (`watch?v=`, `youtu.be/`,
`/live/`, `/embed/`, `/shorts/`, `/v/`, `youtube-nocookie`, `?t=`/`#t=`
timestamps, playlist + tracking params) down to ONE video id, ONE canonical
URL and ONE deterministic job key. Pasting the same broadcast in five
spellings attaches to the same job and records each paste in an audit
history — it never creates a second job. YouTube metadata retrieval;
automatic approval only for a channel in the VERIFIED
`config/broadcast_channels.json` registry; everything else blocks on
`approve-source --confirm`, which records who approved it, when and why. A
metadata failure (no key, quota, network, deleted video) still records the
link and refuses auto-approval — evidence you don't have can't authorize
anything. Broadcast-likeness warnings reuse the existing tuned
`broadcast_matching.broadcast_likeness` gate, so a promo/guide/Shorts upload
is flagged even on the official channel.

**Phase 2 — full-VOD acquisition + 360p scan proxy** (`worker.py`,
`video_ingest.py`)
Duration is probed BEFORE any bytes are fetched, then a duration-aware disk
preflight refuses up front when the estimated 720p footprint (bitrate ×
duration × 1.5 safety, including the proxy) will not fit — with the numbers
behind the refusal recorded on the job. One resumable 720p download
(`--continue`, ONE pinned format so a resume stays valid; a killed download
resumes rather than restarts). Full media metadata: sha256, duration,
resolution, codec, container, source URL, format selector, yt-dlp/ffmpeg
versions, timestamps. Then ONE local 360p scan proxy: every scanning pass
(gameplay classification, OCR, layout fingerprinting, segmentation) reads the
proxy; detection and clip extraction read the full-resolution original and
`extract_segment_clip` REFUSES a proxy path outright. A proxy failure never
loses a good multi-hour download — it is recorded as a classified proxy error
and `scan_path_for` returns `None` rather than silently downgrading.

**Phase 3 — layout resolution + honest segmentation**
(`pipeline/automation/layout_resolver.py`)
Broadcast fingerprinting across representative frames, scored with the SAME
structural HUD probe (`gameplay_state.probe_hud`) production detection
trusts: a layout that belongs to this broadcast reproduces its own chip-row +
portrait geometry, a foreign one scores 0. Automatic reuse of a committed
layout above the score bar and with a clear margin; ambiguity between two
equally-good layouts goes to a human instead of a coin flip; no match at all
falls through to `calibrate_source.py`, whose OWN confidence floor is
enforced unchanged — below it, a HARD refusal, and `approve-layout` will not
publish a calibration the calibrator itself refused. Marker harvesting saves
a representative frame per broadcast state; `round_emblem`/`highlight` have
no automatic classifier and are always reported missing rather than
fabricated. **A real defect was fixed here**: segmentation required an
`anchor` template, and the one fully-proven layout (`owcs_jksix_qwc`) has
none — so it could not segment its own broadcast at all. It now prefers the
structural probe (matching `ingest_map.py`) and falls back to the anchor
template, strengthening rather than loosening the gameplay-state rules.
Candidates carry three spread thumbnails and a per-reason rejection tally.

**Phase 4 — automatic segment identity** (`map_identify.py`,
`player_identify.py`, `pipeline/automation/segment_identity.py`, `intake.html`)
Map name by OCR consensus against the catalog, with mode taken FROM the
catalog rather than OCR'd independently (a second weaker signal could only
disagree with itself); team consensus per side; side continuity tracked by
chip-row hue so a mid-window side swap is DETECTED and blocks the side
assignment; chronological map order; nameplate OCR against the match roster.
A nameplate matching nobody becomes a recorded CANDIDATE — there is no code
path that creates a `players` row. Ten slots hold ten distinct people, so a
duplicate makes BOTH slots `UNKNOWN`. Every signal carries
`{value, source, confidence, reason, evidence}`, is mirrored into
`ingest_findings`, and every disagreement becomes a named review task.
`accept-proposed` approves a segment from its proposal in one action, through
the SAME `approve_segment` gate, and refuses a blocked or incomplete
proposal. `intake.html` is the read-only operator panel: stage, exact next
command, segment timeline, thumbnails, proposed vs human-confirmed values,
blocking reasons, and the accept/edit/split/merge/reject commands.

**Phase 5 — template coverage, honestly** (`pipeline/template_bootstrap.py`)
Coverage is measured against the FULL roster, not against what happens to
exist. The real numbers today: `templates/owcs_jksix_qwc` covers **8 of 52
heroes (15.4%)**, `templates/owcs_8c105lnzlam` 7/52, the shared
`templates/` 17/52. Every uncovered hero is named; detection reports UNKNOWN
for them and never guesses. Existing sets are REUSED, never re-harvested.
Official hero art is used ONLY to propose a cluster label — never written
into a template set, because an official render is not a broadcast HUD
portrait. Low-margin clusters are surfaced for human review. Provenance is
recorded per template file. Packaging now HARD-FAILS a detection-capable
layout with no resolvable `templates_dir`, and hard-fails a template
filename matching no hero id (detection would load it as a phantom hero).

**Phase 6 — supporting match facts** (`pipeline/score_read.py`)
Map scores read as a PAIR under temporal consensus and a monotonicity rule
(a score never decreases; a read that would is discarded with its reason).
Winners DERIVED from the final score, never OCR'd separately; a level score
yields no winner. Series score read from the between-maps card and
cross-checked against established map winners — a contradiction is reported,
not resolved. Best-of inferred only when the evidence forces it. Map order
and VOD timestamps from segment windows. `operator_value` records a
human-supplied fact as `source='operator'` with their name, so it can never
be mistaken for a measurement.

**Phase 7 — production export completeness** (`pipeline/export_data.py`)
The production export now carries every contract the pages and docs require:
`mapResults`, `heroStints`, `rejectedSwaps`, `compFrequency`, `compWinRate`,
`heroPickRates` (pick/win/swap), `teamHeroPools`, `teamMapRecords`,
`mapStats`, `modeStats`. Every aggregate is computed from the approved comp
snapshots IN THAT FILE, after provisional blocking, so it can never include a
withheld match — and a test recomputes them from the file's own rows to catch
drift. `provisional_reasons` is the explicit gate: a match with an
incomplete ingest run publishes its calendar facts and NO maps,
compositions, swaps or bans, with the reason recorded in
`meta.withheldMatches`. Win rates are `null` (unknown) rather than `0` when
nothing is decided. Production/fixture switching was already correct and is
now test-enforced across every public page.

**Phase 8 — workflow + release reliability**
`discovery`, `pipeline` and `update-data` all write `data/owcs.sqlite`,
`assets/js/data.js` and `assets/data/public_data.v1.js` but used THREE
different concurrency groups, so they could race and clobber each other's
commits — they now share one `owcs-generated-data` group. `pipeline.yml`'s
layout default was the explicitly-labelled PLACEHOLDER starter profile
(guessed rectangles, no `hud_probe`), which produced confident-looking
garbage; it now defaults to the one calibrated package. The
`REPLACE_WITH_INTERNAL_MATCH_ID` placeholder video source is gone, and a test
stops another from being committed. `test_release_reproducibility.py` builds
a fresh `git archive`, extracts it to a different path, runs the packaging
gate + worker diagnostics + the CLI there, verifies no absolute path or drive
letter is baked into any committed artifact, verifies every layout's
templates and markers exist in the extraction, and re-runs a known detection
from the clean extraction.

### Two real bugs fixed along the way

* **Segmentation could not run on the only proven layout** (Phase 3, above).
* **Publication was permanently blocked by its own gate.** `publish.py` ran
  `validate_data.py --strict`, which treats WARNINGS as failures — and this
  project's warnings are its normal steady state ("10 map(s) have replay
  codes but no tracker comp yet (these are the manual-correction queue)").
  Publication was therefore impossible for as long as any map was
  un-processed, i.e. always, and a real error could not be distinguished from
  the backlog. Errors still refuse publication; warnings are surfaced in the
  result.

### Honest gap — no real VOD was downloaded this pass

The environment's egress policy denies `www.youtube.com` (a policy `403`, not
a flaky network). Everything above is implemented and offline-tested, and the
calibration→approval path, the detection regression, and the packaging gate
all run for real. **No real YouTube VOD was fetched and no new match went
through the loop end to end.** The exact Windows commands to finish that
validation on a real host are in `docs/AUTOMATION.md` under "Real-host
validation".

### Database migration

`ingest_findings` is rebuilt in place (SQLite cannot ALTER a CHECK
constraint) to widen its `kind` and `status` vocabularies for the Phase 4/6
findings. Additive, idempotent, no row rewritten — see
`db._widen_ingest_findings`.

### New CLI commands

`ingest-link`, `link-status`, `approve-source`, `resolve-layout`,
`approve-layout`, `propose-identity`, `accept-proposed`, `segment-split`,
`segment-merge`, `intake-export`.


## HISTORICAL (superseded — 2026-07-26) — Closed-loop beta: worker, segmentation, detection wiring, publication (Phases E/F/G/I)

Everything below this section down to the next `## CURRENT STATUS` marker is
historical context; when it conflicts with this section, this section wins.
Full detail in `docs/AUTOMATION.md`'s "Beta Sprint — Phases E/F/G/I" section.

Built the remaining pieces the prior Phase A-D3 passes explicitly deferred:
the self-hosted worker (download + metadata capture, reusing `video_ingest.py`/
`download_vod_clip.py` unchanged), assisted map segmentation (candidate
generation reusing `capture.py`'s existing HUD-anchor classifier, full
review-action CRUD, ffmpeg extraction), wiring an approved segment into the
existing `ingest_map.py` detector/swap system (unchanged — this pass only
drives it automatically), and human-gated publication (`process-approved-job
--publish`: promote, regenerate + validate the export, packaging/secret/
media checks, a scoped commit + push — PR/CI/merge/Pages stay the existing
flow). One job now travels the whole loop: `ARCHIVED` ("ready") ->
`DOWNLOADING` -> `DOWNLOADED` -> `SEGMENTING` -> `NEEDS_REVIEW` ->
`READY_FOR_DETECTION` -> `PROCESSING` -> `NEEDS_REVIEW` -> `APPROVED` ->
`PUBLISHED`, plus a new explicit `CANCELLED` terminal state. New CLI:
`create-job`, `list-jobs`, `show-job`, `claim-job`, `release-job`,
`retry-job [--force]`, `cancel-job`, `reset-stale-lock`, `resume-job`,
`run-job`, `job-coverage [--save]`, `worker-run`, `segment-list`,
`segment-approve`, `segment-reject`, `detect-job [--write]`,
`process-approved-job [--publish]`. New read-only ops dashboard:
`beta-ops.html` (fed by `assets/data/job_coverage.v1.json`). 94 new offline
checks across 5 new test suites + extensions to 5 existing ones — see
`docs/AUTOMATION.md` for the full list.

### Honest gap — no real match was processed end-to-end this pass

This session's sandboxed environment has an egress policy that explicitly
denies `www.youtube.com` (confirmed via the proxy's own diagnostics — a
policy `403`, not a flaky network) and ships no `ffmpeg`/`yt-dlp`/`opencv`
by default (installed for this session only, to run the tests). Every piece
above is built and offline-tested — `worker-run` was smoke-tested live
through the real CLI with a real `yt-dlp`/`ffmpeg`, hit the real network
boundary, and failed safely with a classified error — but **no real VOD was
downloaded and no real match went through the loop**. That needs the
self-hosted Windows worker machine this repo's docs already point at for
every prior real-VOD ingestion. Next session, on that machine:

```
python pipeline/automation/cli.py create-job --match <id> --video-id <id> \
    --source-url <official-vod-url> --channel-id UCiAInBL9kUzz1XRxk66v-gw \
    --team-a <id> --team-b <id> --layout-id <layout-id>
python pipeline/automation/cli.py worker-run --worker-id <name> --max-jobs 1
# repeat worker-run / run-job as the job advances through SEGMENTING,
# review with segment-list / segment-approve, detect-job, detect-job --write,
# then: python pipeline/automation/cli.py process-approved-job --job <id> --publish
```

---

## HISTORICAL (superseded — 2026-07-26) — Phase D3: official hero presentation assets

Everything below this section down to the next `## CURRENT STATUS` marker is
historical context; when it conflicts with this section, this section wins.
Full detail in `docs/AUTOMATION.md` ("Phase D3").

Every one of the 52 public hero ids now has a real, authoritative portrait,
full artwork, and icon — sourced from Blizzard's own official hero pages
(overwatch.blizzard.com/en-us/heroes/<slug>/), never a guess. Separate from
(and never blended with) the pre-existing evidence-only broadcast-crop
portraits: those still mean "this hero was actually detected being played
in a tracked match"; the new official art means "this is what the hero
looks like," used only on encyclopedia-style surfaces.

**52/52 resolved.** All 52 heroes turned out to be real, currently-live
Overwatch heroes (the real game kept shipping new heroes past training
cutoff, through mid-2026) — confirmed live via web search before writing
any fetch code. Three real naming quirks in Blizzard's own CMS were found
and handled explicitly (never patched with a blanket guess): CMS revision
suffixes (`Juno_v2`, `Mei_02` — direct match tried first so a hero whose
real name itself ends in a number, `Soldier: 76`, is never mistaken for a
revision-suffixed file), diacritics (`Torbjörn`/`Lúcio` normalize to match
Blizzard's ASCII filenames), and a pre-release dev codename (Wuyang's asset
is filed under `Aqua.png`, independently corroborated by outside reporting
at reveal time — recorded explicitly with full provenance, never silently
masked).

New: `pipeline/build_hero_official_assets.py` (fetch + portrait/artwork/icon
generation + WebP, idempotent, `--dry-run`/`--hero-id`/`--force`), a new
`heroOfficial` section in `asset_manifest.json` (role, curated aliases,
source, attribution, usage note, dimensions, hash), a new `A.heroOfficialFace`/
`applyHeroOfficial` resolver in `assets.js` (same monogram placeholder
`heroFace()` already uses — the intentional unknown-hero fallback — swapped
for the real portrait once resolved), a new "Official presentation" panel
on `hero.html`, and official portraits on `heroes.html`'s "not yet sighted"
cards. `pipeline/test_hero_official_assets.py` (23 checks, offline).

AVIF explicitly `null` (same documented limitation as the Phase D2 team
logo pipeline — no AVIF encoder in this stdlib/existing-deps environment).
~11MB of committed image assets (52 heroes × portrait/artwork/icon × PNG/
JPG/WebP).

### Honest gaps / next blockers
- No hero currently needs a human review pass (52/52 resolved) — the
  `KNOWN_CODENAMES` override table exists for the one case (Wuyang) that
  needed it; a future new hero release may need a similar one-line addition
  if Blizzard's CMS ever files an asset under an unrevealed codename again.
- `aliases` is a small, deliberately conservative curated list — most
  2026-era heroes have no separately-documented civilian name and correctly
  show an empty aliases list rather than a guessed one.

---

## HISTORICAL (superseded — 2026-07-25) — Phase D2.1: production team population, verified logos, match export repair

Everything below this section down to the next `## CURRENT STATUS` marker is
historical context; when it conflicts with this section, this section wins.
Full detail in `docs/AUTOMATION.md` ("Phase D2.1").

**Match export repair.** Root cause found and fixed: `export_data.py`'s
`build_public_payload` only surfaced a match via a completed CV ingest run
or `_discovered_window_matches` (required `competition_id`/`lifecycle_status`
set AND a spot inside the rolling discovery window) — a real, evidenced
legacy match with neither (predating the Phase B automation columns) fell
through and silently vanished from the calendar, match directory,
tournament pages, team history, search, and stats. New
`pipeline/automation/match_repair.py` classifies every match's
`fixture_kind` (production vs the 12 `sample:*` demo rows from
`pipeline/sample_data.json` — evidence-based, never guessed) and backfills
`lifecycle_status` only from the match's own pre-existing `status` field
(`final→finished`/`live→live`/`upcoming→scheduled`; `unknown` stays
unresolved for a human). The export gate itself is fixed so a concluded
(`status='final'`) real match is never excluded by the rolling window
(that window governs discovery recency, not export retention) while
synthetic rows stay correctly hidden. `m-cr-zeta-krgf` and `m-cr-zeta-ccuf`
now export; `m01`–`m12` correctly do not. New
`pipeline/automation/match_export_coverage.py` reports exactly why every
excluded match isn't public. New CLI: `match-audit`, `match-repair
[--write] [--coverage]`, `export-coverage`. New `discovery.yml` dispatch
modes: `match-audit`, `match-repair-dryrun`, `export-dryrun`; the `sync`
path now always runs `match-repair --write` (needs no API key) before
regenerating the export. 2 new test suites (`test_automation_match_repair.py`,
`test_match_export_coverage.py`), 34 new checks, all offline.

**Team population.** Ran live against this repo's real `FACEIT_API_KEY`/
`YOUTUBE_API_KEY` GitHub Actions secrets via `workflow_dispatch` on this
phase's branch: 262 real matches exist in the 2 enabled FACEIT competitions
but 0 fall inside the current 30-day window; the verified YouTube channel's
recent uploads are all short-form social clips, not match VODs. Honest
"nothing new today" — no team fabricated to force a different result. The
existing 9 teams (from earlier CV/manual ingestion) remain the complete,
evidenced registry.

**First verified logo batch.** Real primary-source candidates found via web
search (each org's own domain) for the 9 known teams, run through the
existing `team_assets.py` state machine unmodified. **Published** (official
site, ≥150px, all variants): Crazy Raccoon, ZETA DIVISION, Team Falcons,
Spacestation Gaming, Twisted Minds. **Held for a human** (validated but
below a 150px auto-approve bar): NRG (72px), Al Qadsiah (50px). **No
candidate found** (needs a human): Gen.G Esports (site only exposed a
sponsor banner, not their own mark), Quick Esports (rebranded to "Vanir
Quick", ambiguous current identity — never merged on name similarity).
Found and fixed two real bugs surfaced only by actually publishing for the
first time: `build_asset_manifest.py` never recognized a published
candidate (`team_assets.py` writes `assetCandidates[].state=="published"`,
the manifest builder read a flat `sourceUrl` field nothing ever wrote), and
`publish_candidate` wrote Windows backslash paths into `logo_url` — a
browser can never load `assets\img\teams\x\logo.png` — fixed with a
`_site_relpath()` helper, regression-tested.

**Team Coverage dashboard**: `team-coverage.html` gained a 16-filter bar
(needs-review, missing identity/region/FACEIT-id/roster, roster conflict,
missing/candidate/approved/fallback logo, missing broadcast, no captured
maps, historical, unsigned, academy, duplicate-name conflict) plus explicit
FACEIT-id/logo-state columns and a remediation hint per blocking issue.

**Shared asset resolver audit**: confirmed (no changes needed) — a single
`P.teamPlate()` in `core.js` already renders every team logo, used
consistently by all 11 public page scripts.

### Honest gaps / next blockers
- NRG and Al Qadsiah logos need a human to find a higher-resolution
  official source than their site favicons.
- Gen.G and Quick Esports need a human to locate/confirm an official mark
  (Gen.G: find the real brand asset, not a sponsor banner; Quick Esports:
  resolve the Vanir Quick rebrand before sourcing a logo at all).
- The team registry's coarse `region` values ("Asia") don't match
  `config/automation.yml`'s finer regions list (korea/japan/pacific/china) —
  a pre-existing mismatch (not introduced this pass) that surfaces as
  `unsupported` in the coverage report for 4 real, active teams. Needs a
  human to assign each team's correct fine-grained region; not guessed here.
- Real team/match discovery still finds nothing in-window as of this pass —
  rerun `match-repair`/discovery dry-runs periodically; the infrastructure
  is live and will pick up new activity automatically once it falls inside
  the 30-day window.

---

## HISTORICAL (superseded — 2026-07-24) — Phase C: YouTube broadcast discovery

Read-only official-schedule + YouTube broadcast discovery, built on the
Phase A/B spine. Never downloads video, never records, never writes hero
compositions, never enables unattended production linking. Full detail in
`docs/AUTOMATION.md` ("Phase C") and `docs/YOUTUBE-DISCOVERY.md`.

**Update (same day, follow-up branch `claude/verified-owcs-youtube-channel`):**
a live GitHub Actions `mode=verify-channels` dispatch confirmed
`ow_esports_global` — channelId `UCiAInBL9kUzz1XRxk66v-gw` ("Overwatch
Esports"), uploads playlist `UUiAInBL9kUzz1XRxk66v-gw`, 1 quota unit. It is
now the only `enabled: true` / `verifiedStatus: verified` registry entry;
regional channels and China remain disabled exactly as before (no
human-sourced URL / different platform, never guessed).

**Update 2 (same day, branch `claude/phase-c-real-dryrun-fix`):** the first
real `broadcast-dryrun` Actions run found 7 in-window videos but
`videos_scored` came back 0 — diagnosed and fixed. Root cause: dry-run
discovery never surfaced its discovered videos to the matching stage (only
the write-path did, and dry-run never writes), and matching only built
targets from `scheduled_matches` (empty, since `broadcast-dryrun` never runs
`sync_faceit`/`sync_calendar`). Fixed: discovered videos now always flow
into matching regardless of dry-run; matching pools targets from THREE
sources (FACEIT matches, automation-DB source_events, and the always-
available committed calendar seed) so a video is never required to match a
specific FACEIT match; every video now gets one of 7 explicit
classifications so `videosScored == sum(classifications)` always holds —
see `docs/AUTOMATION.md`'s "Phase C real dry-run fix" section. Also fixed
the reported "videos seen: 1000" inefficiency: playlist pagination now stops
at the lookback boundary (+2-day buffer) instead of walking a channel's full
history, and the YouTube client gained a TTL response cache so repeated
dry-runs don't re-spend quota. 8 new tests + 3 extended files, all offline.

**Update 3 (same day, branch `claude/phase-c-broadcast-classification-refinement`):**
the corrected dry-run scored all 7 real videos but classified all 7 as
`event-level-candidate`, including six plainly short-form promotional/
instructional uploads (a 6-second lootbox promo; several 1.5–2.5 minute
tips/perk/patch videos) — a flat +40 official-channel bonus outweighed the
lone −15 short-duration penalty. Fixed with a new `broadcast_likeness()`
pre-filter stage (livestream metadata, duration, tournament/match-format
terms vs. instructional keywords — no single duration cutoff, no hard-coded
title) that runs BEFORE target scoring: an "unlikely" video is classified
`unrelated-official-upload` immediately, never scored against any target.
Also fixed report clarity: candidate video×target PAIR counts were being
conflated with DISTINCT VIDEO counts (e.g. "21" meaning 7 videos × 3
targets, not 21 videos) — `match_broadcasts` now separates
`totalCandidatePairsEvaluated` from `distinctVideos`, and every per-video
result carries explicit `reasons` and `targetsFiltered` (which targets were
excluded and why). See `docs/AUTOMATION.md`'s "Phase C broadcast-
classification refinement" section and `docs/YOUTUBE-DISCOVERY.md`'s
"Broadcast-likeness pre-filter" section. 15 new tests, all offline,
including a sanitized end-to-end regression shaped exactly like the 7 real
videos.

* **C1** `config/broadcast_channels.json` gained sourceUrl/ownershipEvidence/
  uploadsPlaylistId/verificationMethod/verifiedDate/verifiedStatus/
  disabledReason/preferredLayout on every entry + `cli.py verify-channels`.
  `ow_esports_global` is now API-verified (see the Update above); regional
  channels (Korea/Japan/Pacific) and China remain disabled — no
  human-sourced official URL yet / different platform, never guessed.
* **C2** `pipeline/automation/youtube_api.py` — dependency-light Data API v3
  client (channels/playlistItems/videos/search.list), injectable transport,
  quota-unit accounting + exhaustion detection, sanitized errors (the API key
  can never reach a log, cache filename, or exception message), deterministic
  cache keys.
* **C3** `broadcast_discovery.py` — cheapest-path-first discovery (uploads
  playlist before search.list fallback), normalizes upcoming/live/completed/
  archived/VOD broadcasts, rolling 14-day + horizon window, idempotent
  `broadcast_videos` ledger + `broadcast:<video-id>` jobs.
* **C4** `broadcast_matching.py` — explainable additive scoring (channel
  authority, team/competition/title-pattern text match, region/language,
  time proximity/conflict, live status, duration plausibility) into
  HIGH/MEDIUM/LOW bands; HIGH proposes a link (never auto-applied), MEDIUM
  opens a review task, LOW is rejected. One video can link many matches and
  vice versa (`broadcast_candidates`).
* **C6** `coverage.py` gained `build_broadcast_coverage` — 11 explicit states
  (schedule-discovered … published) + an honest `cancelled` bucket, so no
  configured match/event can silently disappear from the report; also
  surfaces quota used, last successful refresh, last source error.
* **C7** `owcs_calendar.py` gained a live `http_fetcher` that resiliently
  parses the official schedule's `__NEXT_DATA__` (Next.js) blob — any parse
  failure degrades to "fetched nothing" rather than raising or fabricating
  match pairings/times; the committed seed stays authoritative.
* New CLI: `verify-channels`, `calendar-dryrun`, `broadcast-dryrun`,
  `discover-broadcasts`. New workflow_dispatch modes on `discovery.yml`
  (read-only, sanitized-artifact output, no video download/recording/comp
  writes ever).
* **4 new test suites** (`test_automation_youtube_api.py`,
  `test_automation_broadcast_discovery.py`,
  `test_automation_broadcast_matching.py`,
  `test_automation_owcs_calendar.py`) + extensions to
  `test_automation_{schema,config,coverage}.py`. All offline, no network/key.

### Honest gaps
- Regional channels (Korea/Japan/Pacific) still have no evidenced official
  URL — `verify-channels` can't resolve an id without one, and none is
  guessed. China stays explicitly out of scope (bilibili, not YouTube).
- No broadcast has actually been discovered/matched yet — that needs a live
  `discover-broadcasts`/`broadcast-dryrun` Actions run now that one channel
  is enabled; production linking stays off regardless (HIGH confidence only
  proposes a candidate, per the roadmap's Phase C scope boundary).
- Phase E (self-hosted recording), F (segmentation), G (detector/layout/
  template automation) and I (auto-publication PRs) are still not built —
  they plug into this spine when they land.

---

## PREVIOUS SESSION (2026-07-23) — "nocturne" public redesign, swap intelligence, asset registry

This session rebuilt the public site into a dark-gothic esports
intelligence surface and shipped the swap-intelligence layer. Everything
below is verified: **all 44 offline suites green** (incl. the new
`test_assets.py`), `check_packaging.py` OK, and every public page
screenshotted in headless Chromium at 1440/820/390 with **zero
non-font/pre-existing console errors** (`docs/screenshots/`).

### 1. Swap + round data exported (real DB rows, nothing derived)
`export_data.py` now writes `heroSwaps` (all temporal-consensus verdicts:
2 confirmed with before/after evidence crops, 8 rejected with reasons) and
per-map `rounds` windows into `public_data.v1.js`. Contract documented in
`docs/PUBLIC_DATA_CONTRACT.md`; the fixture gained matching demo rows.
Evidence paths are dropped to null if the file is missing — the UI can
never render a broken crop.

### 2. Asset registry (never guess, never break)
- `pipeline/build_asset_manifest.py` → `assets/data/asset_manifest.json`:
  audited registry of every team/hero image (source, attribution, dims,
  hash, review status). 14 heroes have verified broadcast-crop portraits;
  the rest are explicit `fallback-monogram`. All 9 teams are explicit
  `fallback-crest` — **no verified official logo could be fetched from
  this sandbox** (network policy blocks all non-registry hosts), so team
  marks render as designed inline-SVG crests. Candidate official sources
  are documented in `assets/data/team_asset_sources.json`; drop a verified
  `assets/img/teams/<id>/logo.png` + source URL and re-run the builder to
  flip a team to `verified-official`.
- `assets/js/public/assets.js`: client resolver — crest SVG (can't 404),
  hero face (real crop or role monogram), role icons, per-team accent
  hues. `core.js` team plates + hero tiles delegate to it.
- `pipeline/test_assets.py` proves every hero/team id in both datasets
  resolves to a real file or an intentional fallback.

### 3. "Nocturne" design system (public.css rewritten)
Near-black blue-black bases, bone-white type, gold/emerald/crimson/violet
accent system, clipped-corner broadcast panels, engraved dividers, noise
+ stained-glass atmosphere, energy-trace card hovers, verified seals.
Every pre-existing selector contract kept (all string-level tests pass
unchanged). Cinzel added as the serif accent face on public pages.

### 4. New public pages (all on the shared shell, all evidence-first)
`calendar.html` (month grid + agenda from real scheduledAt),
`heroes.html` (analytics directory; "not yet sighted" section is honest
absence), `hero.html?id=` (dossier: rates, teams, swap activity,
portrait provenance from the manifest), `comps.html` (distinct verified
lineups grouped by team+heroes), `swaps.html` (confirmed swaps with
crops + the rejected honesty ledger). Nav/footer updated; admin links
moved to mobile menu + footer so the desktop rail never overflows.

### 5. Homepage hero rebuilt (index.html)
The Tracer mascot scene is replaced by the real detection story: the
actual `frame_2394.jpg`, the autocalibrated slot rects drawn at their
true normalized positions, scanline → locate → identify → swap flare →
verdict panel with the real Juno/Lúcio crops → conf 0.817 count-up →
gold seal. GSAP timeline in `assets/js/landing-hero.js` (~6 s, plays
once); reduced-motion/mobile/no-GSAP get the complete static final
state. Landing-only gothic overrides appended to style.css; control-room
pages untouched.

### 6. Scrolling fixed at the root
`motion.js`: ONE Lenis instance guard + ONE GSAP ticker loop
(`gsap.ticker.add(t => lenis.raf(t*1000))`), ScrollTrigger.update from
Lenis' scroll event, ScrollTrigger.refresh on window load, anchors
routed through `lenis.scrollTo`, progress hairline re-measures page
length per frame. `shell.js` no longer spawns its own fallback Lenis
(the second racing instance was the root cause of broken scrolling).
Touch stays native (`syncTouch:false`); reduced-motion never boots the
engine.

### 7. Automation foundation installed (Roadmap Phase A + discovery scaffolding)
New standalone package `pipeline/automation/` — the persistent job/state spine
the *Complete Automation Roadmap* hangs off. Stdlib-only (sqlite3 + a tiny
dependency-free YAML parser), so it runs in the same offline env as CI; it
never writes hero comps. Delivered: the automation job DB
(`schema.sql` — persistent queue, not workflow artifacts), the explicit
`DISCOVERED…PUBLISHED/FAILED` state machine (illegal jumps rejected; failures
retain full context and dead-letter to `FAILED_PERMANENT`, never deleted),
deterministic idempotent job keys (`models.py`), lease locks with heartbeats +
crash-steal (`locks.py`), the operator config `config/automation.yml`, the
curated FACEIT competition + broadcast-channel registries
(`config/faceit_competitions.json`, `config/broadcast_channels.json` — all
placeholder IDs, all disabled by design), and the rolling 14-day completeness
report (`coverage.py`, `cli.py coverage`). Operator CLI:
`python pipeline/automation/cli.py {init-db,config,registries,coverage,status}`.
Six new offline suites (`test_automation_*.py`) all green. Full details +
go-live steps in **`docs/AUTOMATION.md`**. Recording/segmentation/detection/
publication automation are the roadmap's later passes and plug into this spine.

### 8. Phase B — automatic calendar ingestion (FACEIT + official OWCS)
Production discovery pipeline on the Phase-A spine, stdlib-only and fully
offline-tested. `faceit_api.py` (FACEIT Data API client + fact-only
normalizer, injectable transport, response caching, forfeit/cancel/live/
scheduled lifecycle mapping — never comps), `owcs_calendar.py` +
`config/owcs_calendar.json` (official event-level adapter), `reconcile.py`
(B4 warnings, never overwrites), `discovery.py` (idempotent upsert into the
content DB with stable ids `faceit-<matchId>` + alias-safe team resolution,
rolling 14-day + horizon window, per-match broadcast-discovery jobs +
`scheduled_matches` ledger in the automation DB, API-failure retry jobs,
dry-run purity). `export_data.py` now surfaces discovered scheduled matches
into `public_data.v1.js` so `calendar.html` populates (facts before CV, I3);
guarded so a pre-migration DB never crashes the export. New CLI:
`sync-faceit`, `sync-calendar`, `sync-all [--dry-run] [--export]
[--fixture-dir]`. Hourly `.github/workflows/discovery.yml` — safe by default
(dry-run only until registries enabled + `FACEIT_API_KEY` set; opens a
data-update PR only on validated calendar change). Four new suites
(`test_automation_{faceit_api,discovery,reconcile,calendar_export}.py`) green.
Content `matches` gained `lifecycle_status` / `capture_status` /
`competition_id` (additive migration). Details + go-live steps + required
secret in **`docs/AUTOMATION.md`**.

### Not done / honest gaps
- Team logos remain designed crests until a human fetches + verifies
  official marks (see §2 — the pipeline and manifest are ready).
- Phase B registries are placeholder IDs (disabled) and need real FACEIT
  championship ids + the `FACEIT_API_KEY` secret before the hourly workflow
  does anything but a dry-run health check. `config/owcs_calendar.json`
  dates are unverified placeholders.
- Phase C+ (YouTube broadcast discovery), the self-hosted recording worker
  (E), segmentation (F) and auto-publication (I) are not built yet — they
  enqueue onto the same spine when they land.
- CR-ZETA (`m-cr-zeta-ccuf`) still has no `ingest_map.py --write`; the
  new pages will gain breadth automatically when it lands.
- Hero portraits are 96px broadcast crops — sharp at tile size, soft at
  the 120px dossier size; harvesting higher-res crops would fix that.

---

## PREVIOUS SESSION (2026-07-20) — real portraits, clickable teams, Calibration Lab

This session built the "ultimate" autocalibration + stat-viewing layer on top
of the existing pipeline. Everything below is verified live in headless
Chromium (control-room server + all four pages, zero non-font console errors)
and by the offline suite (**36 suites green under Python 3.11**; the only
non-passing suite, `test_calibration_tools.py`, needs a real `ffmpeg` binary
this sandbox lacks — it was already failing before this session for the same
reason).

### 1. Real hero portraits from actual broadcast crops
`pipeline/build_hero_portraits.py` turns the harvested per-source template
crops (`templates/<source>/*.png`) into public portrait assets
(`assets/img/heroes/<id>.png`, 96px) + `manifest.json`. **Honesty rule
enforced in code and tests:** only real broadcast crops are used — the
root-level synthetic starter set is never touched. Each portrait picks the
best variant by a deterministic quality score (nameplate trimmed off, hue
diversity + sharpness floor kill dead/ult/damage-flash states) and records
which exact crop it came from. 14 heroes have real portraits today (the two
calibrated broadcasts' rosters). `export_data.py` attaches `portraitUrl` to
both `data.js` and `public_data.v1.js`; `core.js` hero tiles already render
it, so portraits now show on stats, match, team and calibration pages.
Heroes without a harvested crop still fall back to role-tinted monograms.

### 2. Clickable teams + a real team page
`team.html` + `assets/js/public/page-team.js`: team header (record, region,
tournaments), 4 summary cards (match/map record, heroes fielded, team-map
appearances), a **role-grouped hero pool with portraits and per-hero
evidence links**, and match history — all built from `OWCS_STATS`, so the
same verified-comps-only credibility rule applies unchanged. `teamPlate`
gained an `opt.link` that renders a real `<a>` to `team.html?id=…`; it's
enabled only at call sites NOT already nested inside another anchor (no
`<a>`-in-`<a>`) — match VS band, comps/bans rows, standings, tournament
team cards, and the stats drill-down.

### 3. Stats drill-down (click a hero → per-team breakdown)
Every hero row on `stats.html` is now expandable (click / Enter / Space,
`aria-expanded`, URL-persisted via `?hero=`): it opens a panel with the
hero's real portrait, portrait provenance, and a **per-team pick/W–L
breakdown** with evidence links, computed by the new `S.heroDetail()` in
`stats.js` (same appearances as the pick-rate table, so nothing new is
invented). Meta-snapshot cards became clickable buttons that jump to and
open the matching hero's drill-down, and now carry portraits.

### 4. Calibration Lab (the autocalibration dashboard)
`calibration.html` (control-room shell, in the nav on every control page) +
`pipeline/calibration_status.py` + `GET /api/calibration` & `/api/portraits`
on `serve.py`. For every real source it reports, **honestly, only what's on
disk and in the DB**: layout + native size, auto-calibrator confidence,
`hud_probe` present (without it the gameplay filter reads zero frames),
round-emblem rect, reject-marker count (+ whether the PNGs resolve),
per-source template coverage vs the 52-hero roster (with a bar), calibration
reports found, and ingest-run status counts. Each source is graded
ok / warn / fail with explicit "what's left" reasons and a copy-ready
`calibrate_source.py` command. It also shows the real harvested portraits
with provenance. Works live against the control-room server and degrades to
a `data.js`-derived static view on plain hosting.

### Also fixed (pre-existing, blocked 4 test suites)
`pipeline/ingest_map.py:1076` had a multiline-f-string that only parses on
Python 3.12+ (PEP 701); it made the core ingest module un-importable on
3.11 and broke `test_map_ingestion`, `test_calibration_health`,
`test_ingest_ocr_wiring`, `test_post_unlock_grace`. Rewritten to a 3.11-safe
form (110 checks unblocked).

### New/changed files
NEW: `calibration.html`, `team.html`, `assets/js/public/page-team.js`,
`assets/img/heroes/*` (14 portraits + manifest),
`pipeline/build_hero_portraits.py`, `pipeline/calibration_status.py`,
`pipeline/test_calibration_dashboard.py`.
MODIFIED: `export_data.py` (portraitUrl), `serve.py` (2 endpoints),
`ingest_map.py` (f-string fix), `core.js`/`stats.js`/`page-stats.js`/
`page-match.js`/`page-tournament.js` (links + drill-down),
`public.css` (team/drill/calibration styles), `run/runs/sources/admin.html`
(Calibration nav link), the three affected test suites, regenerated
`data.js` + `public_data.v1.js`.

### Not done / honest gaps (unchanged from before + notes)
- The CR-ZETA match (`m-cr-zeta-ccuf`) still has **no `ingest_map.py
  --write`** — so it does not appear in `public_data.v1.js` yet (only the
  Nepal `m-qad-twis-s2po` match does). Portraits, the Calibration Lab and
  the team/stats drill-downs all work now; they'll simply gain breadth the
  moment CR-ZETA is written. The next-command block below still applies.
- The Nepal broadcast isn't registered as a *source* row in
  `video_sources.json` (only its match/layout exist), so the Calibration
  Lab lists the two registered sources; add a source row if you want its
  card too.
- `ffmpeg` is required for `test_calibration_tools.py` and for any real
  clip work — not present in this sandbox.

---


## HISTORICAL (superseded — 2026-07-19)

**Repo is pushed and in sync with GitHub** (`origin/main` ==
local `main`, nothing uncommitted). A fresh clone gets everything below.
Everything under this section down to the next `## PREVIOUS SESSION`
marker is historical context; when it conflicts with this section, this
section wins.

### What shipped this session (on top of 07-18's Nepal milestone)

* **Evidence pages upgraded to the honest-read standard everywhere.**
  `build_crop_report.py`, `capture_hero_crops.py`, and
  `vision_dashboard.py` all now use `detect.read_slot()` (top hero,
  runner-up, margin, explicit `UNKNOWN` + reason) instead of the old
  bare-score `match_slot()`. Scope note: the *production* accept/reject
  path (`detect.py`'s `read_frame_comps()`/`validate()`, used by
  `hero_overlay_detect.py` → `detections.json` →
  `promote_detections.py` → real `comp_snapshots` writes) still uses the
  old bare-score matcher — user explicitly chose "debug tooling only"
  scope when asked, so that one's a known, deliberately-deferred gap.
* **Every report now includes a hero crop report.** Wired
  `build_crop_report.process()` into `ingest_map.py`'s own report step
  (previously only `run_owcs_auto.py` runs had one), with a
  `name_filter` fix for stale `base*.jpg` leftovers that accumulate in
  a reused `frames_dir` across re-runs, and correct footer nav links per
  caller (`run_report_href`/`layout_href` params on `process()`).
* **`vision_dashboard.py` is now generated automatically**, not just on
  manual invocation — it's step 8/9 in `run_owcs_auto.py`'s pipeline and
  in `serve.py`'s "regenerate evidence" job.
* **Pick-rate stats are real and now visually prominent.** Turned out
  `hero_stints` → derived comp snapshots → `public_data.v1.js` →
  `stats.js` was already correctly wired (not a gap); added a
  role-grouped "Meta snapshot" visual (Tank/Damage/Support columns,
  ranked within role, gold leader marker) to `stats.html` above the
  existing sortable table.
* **Found + fixed a real exporter bug**: `export_data.py`'s
  `build_payload()` used a single **hardcoded** tournament id
  (`"owcs-2026-naemea-s2po"`) and *reassigned* (not appended)
  `tournaments_out` inside the per-match loop — harmless with exactly
  one real match, but it would have silently mislabeled every match once
  a second real match existed. Fixed: `_tournament_id()` derives a slug
  from each match's real `event_name`/`season`/`stage`, and tournaments
  now accumulate in a dict keyed by that id. Verified with a synthetic
  2-tournament test (see session transcript) — no test file covers this
  yet, worth adding `test_export_data.py` if touching this again.

### IN PROGRESS — second real match (Crazy Raccoon vs ZETA DIVISION)

Started ingesting a second real match beyond Nepal, to prove the
multi-match path and give the pick-rate stats real breadth. **Not
finished — this is the actual goal to hand off.**

* **Source**: `owcs-8c105lnzlam` (`video_sources.json`), YouTube VOD
  `https://www.youtube.com/watch?v=8C105lNzLAM`, "Champions Clash Upper
  Finals | OWCS 2026 | Crazy Raccoon vs ZETA DIVISION" (official
  Overwatch Esports channel, 102 min, uploaded 2026-06-03). Confirmed
  from the broadcast overlay itself: **Map 1 is Control, "FIRST TO 3"**
  (best of 5 rounds), OWCS Champions Clash Day 3 Upper Final.
* **Layout is DONE and committed**: `layouts/owcs_8c105lnzlam.json` was
  already calibrated in an earlier session (slots_a/slots_b, real player
  names HEESANG/STALK3R/JUNBIN/CHORONG/VIGILANTE vs
  KNIFE/PROPER/MEALGARU/VIOL2T/SHU). This session added the missing
  **`hud_probe` block** (chip boxes derived from the same
  `chip_x = slot_x - 54` geometry already documented in the layout's
  own `_comments`, verified against a real t=365s frame: all 10 chips
  clear `sat_min=90/val_min=150` with 0.51-0.89 saturated-pixel
  fraction, comfortably above the `MIN_SAT_FRAC=0.25` gate). Without
  this the pipeline read **zero gameplay frames** across a 13-minute
  scan — `hud_probe` is required by `gameplay_state.classify_frame()`
  and this layout never had one.
* **Match record created and committed** (honest, no fabricated facts):
  `matches.id = 'm-cr-zeta-ccuf'`, teams `cr`/`zeta` (already existed),
  `event_name='OWCS 2026 Champions Clash'`, `round='Upper Finals'`,
  `date='2026-06-03'`, `score_a=0/score_b=0/winner_team=NULL` (unknown,
  never guessed). **No `map_results`/`hero_stints`/`ingest_runs` rows
  yet** — `ingest_map.py --write` was never run.
* **Template coverage is partial**: only 7 heroes harvested for this
  broadcast so far (`templates/owcs_8c105lnzlam/`: ball, cass, jetcat,
  lucio, mizuki, tracer, winston — 21 files incl. `.a`/`.b` variants).
  A dry-run scan showed `calibration_health: ok` but
  `full_house_rate: 0.588` — heroes outside these 7 correctly read
  `UNKNOWN` rather than guessing, which means some slots may end up with
  no established `hero_stint` at all until more templates are harvested.
  This is honest incomplete-coverage behavior, not a bug.
* **Round 1 is fully captured and clean**: dry-run over stream offsets
  0–780s found `rounds: [(1, 325, 694), (2, 725, 779‑cutoff)]`,
  `calibration_health: ok`, round 2 starts at 725s but the scan window
  ended at 780s before it finished — **need more footage**.
  `confirmed_swaps: 8`, `rejected_swaps: 58` (temporal consensus
  correctly filtering noise from the template gap, not a red flag).
* **What's NOT committed / won't transfer to a new machine** (all
  gitignored, local-only, and this machine hit real download
  bandwidth contention doing this — expect ~1.1 MB/s per stream, don't
  run concurrent downloads of the same VOD):
  - `work/clips/owcs-8c105lnzlam_map1_v2.mp4` — a full 1080p clip of
    stream offsets 0–1080s (18 min), just finished downloading.
  - `reports/ingest/cr-zeta-ccuf-m1-scan/` — the dry-run scan's
    report/crops/evidence (safe to delete; regenerate anytime).
  - Earlier abandoned attempts (`owcs-8c105lnzlam_map1_full.mp4`,
    `_map1_ext.mp4`) — safe to delete.

#### Exact next steps on the new machine

1. `git pull`, confirm `layouts/owcs_8c105lnzlam.json` has a
   `hud_probe` block and `matches` has `m-cr-zeta-ccuf` (it will, both
   are committed).
2. Re-download the clip (this is the part that doesn't transfer):
   `python pipeline/download_vod_clip.py --source owcs-8c105lnzlam
   --start 0:00 --end 18:00 --height 1080 --out
   work/clips/owcs-8c105lnzlam_map1.mp4` (~660 MB, ~10 min at this
   machine's observed ~1.1 MB/s — budget accordingly, and don't run it
   twice concurrently).
3. Dry-run first to confirm round 2's actual end (it wasn't captured
   before offset 780s in the 13-min version — the 18-min clip *should*
   cover it, but hasn't been dry-run yet):
   `python pipeline/ingest_map.py --clip
   work/clips/owcs-8c105lnzlam_map1.mp4 --clip-offset 0 --start 0
   --end 1080 --layout layouts/owcs_8c105lnzlam.json --source-id
   owcs-8c105lnzlam --ingest-id cr-zeta-ccuf-m1 --match m-cr-zeta-ccuf
   --map-order 1 --team-a cr --team-b zeta` — check the printed
   `rounds:` list. Since this is "first to 3," the map could legitimately
   run past 1080s if it goes 3-2; extend the window and re-download
   further if the last detected round still looks cut off.
4. Decide whether to harvest more hero templates first (via
   `capture_hero_crops.py`'s review workflow against this scan's crops)
   for fuller comp coverage, or accept the partial-coverage result as-is
   — both are legitimate, it's a judgment call on how complete this
   needs to be before writing.
5. `--write` once satisfied, then `python pipeline/export_data.py
   --public` to regenerate `assets/data/public_data.v1.js`, then check
   `stats.html`'s Meta Snapshot and `runs.html`'s Vision Lab card for
   the new ingest — this is what actually proves the multi-match path
   and enriches the pick-rate data.

## PREVIOUS SESSION — 2026-07-18 — GitHub packaging + real hero detection

**The pipeline now processes a COMPLETE map end-to-end and the real data
renders on the public site.** Everything below this section is historical
context; when it conflicts with this section, this section wins.

### Packaged for GitHub (latest)

The repo is now a clean git project ready to publish:

* **`.gitignore`** keeps source/docs/tests/layouts/per-source template
  dirs/schema/public pages/`data/owcs.sqlite`/`public_data.v1.js` and the
  **essential milestone evidence** (`reports/ingest/qad-twis-nepal/`,
  `reports/capture_trial/`, `reports/calibration/owcs-jksix-qwc/`); drops
  the 7 GB `work/` tree, all media, caches, DB sidecars, and the 4.7 MB
  template `_candidates/` scratch.
* **Workflows** (`.github/workflows/`): new **`ci.yml`** (offline test
  suite + `check_packaging.py` + public-export regeneration on push/PR)
  and **`pages.yml`** (static Pages deploy). The pre-existing
  `pipeline.yml` / `update-data.yml` are fixed — the stale
  `layouts/owcs-asia-2026.json` reference is gone and both are now
  **manual-only** (`workflow_dispatch`, no cron/push triggers) so they
  can't race CI or auto-mutate the committed milestone.
* **`pipeline/check_packaging.py`** is the reproducibility gate (template
  dirs, marker assets, DB milestone, evidence-path resolution, page load
  order). `requirements.txt` pins the deps.
* **Portable ZIP**: `owcs-comp-tracker-github-ready.zip`
  (`C:\Users\eppol\Downloads\`) extracts clean and passes
  `check_packaging.py` + the offline suite standalone.
* **GitHub push is the one remaining manual step**: `gh` CLI is not
  installed on this machine and no token is present, so the remote
  repo + push must be done after `gh auth login` (or with a PAT). Exact
  commands are in the final session response and below under
  "Publishing to GitHub".

#### Publishing to GitHub (once authenticated)

```powershell
winget install --id GitHub.cli -e     # if gh is missing
gh auth login                          # interactive: GitHub.com > HTTPS > browser
cd "C:\Users\eppol\Claude Code\owcs"
gh repo create owcs-comp-tracker --private --source=. --remote=origin --push
```

The repo is already `git init`-ed with `main` checked out and one
commit staged/made locally, so if you prefer plain git after auth:
`git remote add origin <url> && git push -u origin main`.

### What is true right now

* **Full-map production ingestion works.** The Al Qadsiah vs Twisted
  Minds **Nepal** map (OWCS 2026 NA/EMEA Stage 2 Playoffs Day 2,
  `https://www.youtube.com/live/jkSiX___Qwc`, stream offsets
  ~30:05–46:18) is fully ingested: 3 control rounds, 299 sampled frames
  (196 hero-readable, 103 skipped with reasons), 1950 accepted slot
  reads, **2 confirmed swaps** (ZOX Juno→Lúcio @39:54 in round 2 and
  @42:55 in round 3), 4 setup-phase comp toggles, 7 rejected suspected
  swaps — all with evidence crops. Map winner recorded: **Twisted
  Minds**. Comps: QAD = Shion/Sym/Mauga/Kiriko/Juno(→Lúcio); TM =
  Sojourn/Sym/D.Va/Lúcio/Kiriko (identities evidenced in
  `work/nepal_labels.json`).
* **Auto-calibration** (`pipeline/calibrate_source.py`): computational
  HUD calibration from multiple frames — HSV blob rows + RANSAC grid fit
  + pixel-evidence verification (temporal stability, hue diversity,
  texture) + cross-side pitch/y agreement; writes a reusable profile
  (native 1080p rects + normalized rects + `hud_probe` + confidence +
  warnings) and an annotated sheet under `reports/calibration/<source>/`.
  Refuses below confidence 0.55 with explicit reasons. Nepal profile:
  `layouts/owcs_jksix_qwc.json` (confidence 0.91).
* **Real gameplay-state filter** (`pipeline/gameplay_state.py`):
  structural chip-row probe (color-agnostic) + portrait-texture floor +
  layout reject markers. The Nepal layout carries three cut-from-broadcast
  markers: `HIGHLIGHTS` banner, `REPLAY` chyron, scoreboard header —
  replays/scoreboards can no longer fake comps.
* **Detector** (`pipeline/detect.py`): ranked candidates + runner-up +
  margin + per-hero scores; `read_slot()` returns UNKNOWN instead of
  guessing. Per-source template sets with any number of state variants
  (`<hero>.v1.png`…): `templates/owcs_jksix_qwc/` (8 heroes × 5
  variants, includes dead/ult states), `templates/owcs_8c105lnzlam/`
  (CR-ZETA regression set, 21 files).
* **Temporal consensus** (`pipeline/ingest_map.py`): per-slot hysteresis;
  a swap needs ≥3 consecutive reads (or 2 strong) spanning ≥3 s, margin,
  AND mean displacement of the old hero's own score ≥0.04 (kills
  dead-portrait lookalikes); a candidate run can't bridge >20 s gaps.
  Round boundaries come from clustering the center point-emblem (lock =
  setup, letter = combat round); side identity is tracked per round via
  chip hue continuity + crossover detection.
* **Staged, idempotent DB writes**: new tables `ingest_runs`,
  `slot_observations`, `map_rounds`, `hero_stints`, `hero_swaps`
  (see `pipeline/schema.sql`). Rerunning the same
  (match, map, detector_version) replaces its own CV rows and never
  touches `manual_override=1` or `status='reviewed'` rows (upserts carry
  WHERE guards).
* **Production public export**: `python pipeline/export_data.py --public`
  writes `assets/data/public_data.v1.js` (`meta.demo=false`) from the DB
  only — comp snapshots derived from approved hero stints with evidence
  chains. All five public pages load it BEFORE the fixture; the fixture
  is now a guarded fallback (`window.OWCS_PUBLIC = window.OWCS_PUBLIC ||
  …`). Static hosting unchanged. The real match renders on
  `match.html?id=m-qad-twis-s2po` (comps, swaps, confidence, evidence
  links) and `stats.html` (pick/win rates from verified comps).
* **Reports**: `reports/ingest/qad-twis-nepal/report.html` (full-map
  report) and `review.html` (every confirmed/rejected change point with
  crops). Calibration sheet: `reports/calibration/owcs-jksix-qwc/`.
* **Tests: 29 suites green** including the new
  `pipeline/test_map_ingestion.py` (calibration, gameplay filter,
  temporal consensus incl. displacement/gap guards, emblem rounds, side
  swaps, idempotent writes, manual-correction survival, public.v1 export
  shape, template/marker packaging, page wiring).
* **ROOT CAUSE of the vanished `templates/owcs_8c105lnzlam/`**:
  `test_pipeline_synthetic.py` used to `rmtree(templates/)` wholesale.
  Fixed — it now only replaces root-level synthetic PNGs, and
  `test_map_ingestion.py` fails loudly if any layout's `templates_dir`
  or marker asset is missing from the repo.

### Machine quirks (still true)

* `yt-dlp` needs `--js-runtimes node` on this machine.
* googlevideo throttles sustained plain-HTTP streams to ~0.7 MB/s and
  403s mid-download on direct URLs; `--download-sections` stalls.
  **What works**: full-file `yt-dlp` download (chunked, ~30 MB/s; loop
  to resume `.part` after 403s — only the map's byte prefix is needed),
  then cut locally with ffmpeg. Full 720p60 VOD kept at
  `work/vods/jksix_298_full.mp4`; Nepal cut at
  `work/clips/nepal_720p.mp4` (clip t=0 ⇔ stream offset 1795 s).

### Repeatable workflow for a NEW VOD/map

1. Download + cut the map window (see quirks above), e.g. to
   `work/clips/<map>.mp4`, noting the clip's stream-offset origin.
2. Calibrate: `python pipeline/calibrate_source.py --clip <clip>
   --times t1,t2,…(6-8 spread gameplay times) --source-id <slug>
   --out layouts/<slug>.json` → review
   `reports/calibration/<slug>/sheet.png`.
3. Harvest templates: `python pipeline/harvest_templates.py --clip
   <clip> --times start:end:10 --layout layouts/<slug>.json --out
   templates/<slug> --cluster` → identify clusters
   (montage + scoreboards/hex panels/killfeed), write
   `work/<slug>_labels.json`, then rerun with
   `--labels … --variants 5`.
4. (Once per broadcast package) cut reject markers
   (HIGHLIGHTS/REPLAY/scoreboard) into `layouts/<slug>-*.png` and add a
   `round_emblem` rect — see `layouts/owcs_jksix_qwc.json` for the
   shapes; recalibration preserves these keys.
5. Ingest: `python pipeline/ingest_map.py --clip <clip> --clip-offset
   <origin> --start <s> --end <e> --layout layouts/<slug>.json
   --source-id <slug> --ingest-id <id> --match <match-id> --map-order n
   --map-id <map> --map-winner <team> --team-a <left-team>
   --team-b <right-team> --every 5 [--write]` (dry-run first; teams/
   match rows must exist — see `m-qad-twis-s2po` insert pattern).
6. Review `reports/ingest/<id>/report.html` + `review.html`.
7. Export: `python pipeline/export_data.py --public` and reload the
   site (`python pipeline/serve.py` → e.g.
   `http://localhost:8000/match.html?id=<match-id>`).

### Honest limitations

* Per-round control percentages (scoreDetail) are not read; map winner
  comes from the operator (`--map-winner`), not OCR.
* Series-level match score isn't ingested (only map 1 of this match).
* Round-boundary times are ±1 emblem-sample (~5 s); round-1 start reads
  ~1940 s (first clean emblem read) vs ~1863 s actual unlock.
* Hero identification for template labeling is human-in-the-loop by
  design (evidence recorded in `work/nepal_labels.json`).
* Nepal capture is 720p; the layout profile is resolution-independent
  but the reject-marker templates are cut at 720p (re-cut if you capture
  at another resolution).

## PREVIOUS SESSION — REAL HERO DETECTION WORKS (P2 gate PASSED)

Mission: analyze the capture reports and make hero detection actually work.
**Result: all 3 captured frames of the CR-vs-ZETA window (6:00-6:30) now
detect all 10 slots correctly — confidences 0.90-1.0, zero quarantines,
crops.html shows 30/30 OK, and the promotion gate classifies 6/6 team
snapshots as HIGH while still writing ZERO comps.** 28 suites green.

### What the report analysis found (root cause)

Viewing the actual frames (the Read tool renders PNGs) showed the
placeholder layout was aimed at the WRONG part of the HUD: A-slot boxes sat
on the empty top banner and B-slots on the map pills, while the real
portraits live in a row of [ult-chip][portrait] cells BELOW the team name
bars (orange chips = team A, blue = team B). Every prior LOW/NO-MATCH score
was measuring wallpaper.

### Layout recalibration (layouts/owcs_8c105lnzlam.json)

Chips located by HSV blob detection + a measured uniform pitch of 47.5px at
854x480 (B side mirrors A: 854-18-46 = B5 chip). Portrait = chip_x+24, y=46,
24x24 @480p -> stored at 1080p native (x54px cells, pitch ~106.8). Verified
by re-rendering layout_debug: every box frames a portrait exactly.

### Hero identification (evidence chain, not guesswork)

The 2026 roster contains 8 heroes past the model's knowledge (anran,
domina, emre, jetpack-cat, mizuki, shion, sierra, vendetta — fetched from
the OverFast API with official portraits + hitpoint tables), so identities
were established from broadcast evidence:
- spectator hex panels (name + HP + portrait): STALK3R/KNIFE = Cassidy
  (250), JUNBIN = Wrecking Ball (725 exactly — unique), PROPER = Tracer
  (175 exactly — unique; portrait confirmed at 90px), MEALGARU = Winston
  (625 + primal 1126 + bubble interior), VIOL2T = Jetpack Cat (225 +
  first-person cockpit-paws POV)
- the mid-round TAB scoreboard (large portraits + role icons): CHORONG =
  Lucio, HEESANG = Tracer, VIGILANTE = Mizuki (dark hood + pale angular
  mask; the a5/b5 art matches no other support; SHU's 250 setup panel
  matches mizuki's 250 total)
- cross-slot correlation on the captured frames proves the mirrors
  (a2<->b1 cass 0.84, a1<->b2 tracer 0.65) and that the live-fight comp ==
  the captured comp (all slots 0.62-0.91 vs a live frame).

FINAL: CR = tracer cass ball lucio mizuki · ZETA = cass tracer winston
jetcat mizuki. (Liquipedia confirms the match: CR 3-1, May 23 2026.)

### Templates + detection changes

- NEW `templates/owcs_8c105lnzlam/` (layout's templates_dir points here;
  the shared synthetic starter set is untouched): 21 real 24x24 crops, up
  to 3 STATE VARIANTS per hero via the loader's .a/.b scheme picked by
  greedy min-correlation (Ball's portrait swaps hamster/mech art mid-game —
  one template per hero is brittle; this fixed 2 false quarantines).
- `detect.match_slot` now resizes templates to the slot size in BOTH
  directions (was: only downsize) — templates cut at one capture
  resolution match at any other; a no-op at equal sizes so the
  detection-regression lock still passes.
- `_ytdlp_dump_json` retries transient probe failures 2x (a live "Video
  unavailable" flake killed a run at the probe step).
- DB reference roster: +8 2026 heroes in sample_data.json (52 total;
  id `jetcat` for Jetpack Cat). Reference data only — still ZERO comps
  (verified: comp_snapshots source='cv' count = 0).

### Downloading around YouTube throttling (important for next session)

Repeated `--download-sections` calls get hard-throttled (stall forever)
after ~6-8 requests/hour. What KEPT working: `yt-dlp --extractor-args
"youtube:player_client=android" -g -f 135` for a direct googlevideo URL,
then `ffmpeg -ss <t> -i <url> -t <len> -c copy` — that's the same strategy
as the pipeline's built-in direct-url fallback. h264 DASH (135) and muxed
(18) flow; AV1 (397) and VP9 (302) stall; 1080p exists only as HLS (301-x)
and is impractical to section-cut.

### Verified end-to-end

`run_owcs_auto --source owcs-8c105lnzlam --start 0:06:00 --end 0:06:30
--every 10 --fast` -> PARTIAL (filter still honestly skipped — anchor
template remains a placeholder by design), detection step OK:
  t=360  A 1.0   B 1.0
  t=370  A 0.952 B 0.965
  t=380  A 0.897 B 0.928
crops.html: 3 frames x 10/10 slots, 30 OK / 0 LOW / 0 NO-MATCH / 0 BAD BOX.
promote_detections dry run: 6 high, 0 needs-review, nothing written.

### Next (unchanged rules)

- The promote gate can now genuinely pass; pairing + `--write` remains a
  HUMAN decision. Filter anchor template still to cut (same-resolution
  crop needed). Templates are per-source; other VODs need their own
  calibration+labeling pass (the workflow above is repeatable).

---

## PREVIOUS SESSION — "uplink" motion system (Lenis + GSAP + Vanta)

Site-wide UI upgrade: both shells now share ONE motion engine,
`assets/js/motion.js`, that makes every page come online like a broadcast
graphics package — without touching the visual identity (navy broadcast
desk, single amber accent) or either shell's architecture.

**Vendored (all local, no CDN at runtime):** `three.min.js` (r134, 601KB),
`vanta.net.min.js` (0.5.24), `ScrollTrigger.min.js` (3.12.5 — matches the
existing gsap 3.12.5) alongside the existing lenis 1.1.14 + gsap core.

**Engine layers** (each optional, each crash-isolated via `safely()`, all
disabled under prefers-reduced-motion or Save-Data):
- **flow** — Lenis smooth scroll on BOTH shells, synced to GSAP's ticker +
  ScrollTrigger; inner scroll regions (`.console-body`) auto-tagged
  `data-lenis-prevent` so the live log keeps native wheel.
- **entrance** — one fast page-load timeline (nav → headline → panels,
  power3, <1s, `clearProps` after). Skips elements owned by the public
  shell's `.rv` reveal system (no double animation).
- **reveals** — below-the-fold `.hud`/`[data-rv]` cards rise in once
  (ScrollTrigger.batch → IntersectionObserver → instant fallback chain).
- **decrypt** — `.eyebrow` / `.hud-kicker` labels resolve from scrambled
  glyphs, once, in view (broadcast-terminal feel).
- **physics** — magnetic primary buttons, ≤1.6° card tilt + spotlight
  tracking on `.hud.lift` / `.card--spot` (fine-pointer devices only).
- **progress** — 2px amber/gold scroll hairline on long pages.
- **ambience** — Vanta NET tactical grid (steel-blue #35507e points,
  transparent background, mouse-reactive) with a fallback chain: WebGL
  probe fails / <760px / reduced motion → the old 2D canvas net → static
  CSS gradient. Public pages get it fullscreen behind `#pub-atmosphere`;
  control room only `index.html` opts in via `<body data-vanta="net">` —
  work surfaces (run/runs/admin) stay calm on purpose.

**Wiring:** 8 control pages load lenis+gsap+ScrollTrigger+motion.js (index
also three+vanta); 5 public pages load the full stack. `shell.js` delegates
ambience/count-ups to the engine but keeps owning the `.rv` contract
(page scripts still call `P.observeReveals(root)`). `ui.js` untouched apart
from coexisting. CSS motion layers appended to both stylesheets
(`.scroll-progress`, ambience holders, hud spotlight, pill transitions,
live-log line entry).

**Verified live** (serve.py + Chromium): engine boots on both shells, Vanta
NET active on WebGL on index + all public pages, Lenis running with the
console excluded, a full fast capture streamed through the upgraded
run.html (PARTIAL in 4s, 8/8 timeline steps, report CTA) — zero console
errors on index/run/runs/tournaments/tournament-bracket/match-evidence/
stats. **28 suites all green** (static-page + public-site suites extended:
engine layers present, WebGL guard + fallback net, vendored files exist,
every page wired, index vanta opt-in, no-CDN rule still enforced).

Note: `three.min.js` adds ~601KB (deferred) to pages that use Vanta; pages
render fully before it loads and work without it.

---

## PREVIOUS SESSION — capture reliability on a real Windows machine

Mission: make browser-driven captures finish reliably. **Verified live on
Windows 11 against the real CR-vs-ZETA VOD (`owcs-8c105lnzlam`,
0:06:00–0:06:30): fresh forced download → full 8-step PARTIAL run in ~9s**,
report + layout.html + crops.html (30/30 slots) + runs.html + export all
green, zero console errors. **28 test suites, all passing on Windows.**

### Root causes found ON a real machine (and fixed)

1. **cp1252 UnicodeEncodeError killed EVERY terminal run** before the
   pipeline even started ('→' in log lines). Fix: `db.utf8_stdout()`
   reconfigures stdout/stderr to UTF-8/replace on import — output can never
   crash a run again. (serve.py runs were unaffected; terminal runs died.)
2. **yt-dlp ignores an installed Node.js** — modern yt-dlp only enables Deno
   by default. Fix: `video_ingest.detect_js_runtime()/js_runtime_args()`
   auto-pass `--js-runtimes node` to every yt-dlp call.
3. **YouTube DASH video-only section downloads stall** (format 397 prints
   'Destination' then zero bytes for the whole guard window) even WITH a JS
   runtime, while progressive/muxed formats flow instantly. Fix: `--fast`
   runs put `best[height<=H]` (muxed) FIRST in the ladder
   (`clip_format_ladder(prefer_muxed=True)`); normal runs keep
   quality-first + the stall guard.
4. **Transient "Video unavailable"** from YouTube killed a run at probe
   (observed once, succeeded seconds later). Fix: `_ytdlp_dump_json` retries
   2x with a 3s wait.
5. **Windows backslashes in generated HTML** (`src="candidates\x.png"`) —
   all relpath-into-HTML sites now emit forward slashes.

### New: preflight (setup problems can no longer fail a run late)

`pipeline/preflight.py` — checks python/ffmpeg/ffprobe/yt-dlp/JS-runtime/
opencv/**database tables**/source/layout/writable-folders, each with an
exact remedy. Wired in three places:
- **run_owcs_auto step 1 of 8** ("preflight"): logs warnings, FAILS the run
  immediately (with remedies) on hard problems, and **auto-initializes a
  missing/blank DB** (schema + heroes/maps/teams reference rows, idempotent,
  never comps) so "no such table: heroes" can't happen at the export step.
- **GET /api/preflight** on serve.py (read-only) → the new **Capture
  readiness panel** on run.html (per-check OK/WARN/FAIL chips).
- CLI: `python pipeline/preflight.py --source owcs-8c105lnzlam` (+`--json`,
  `--fix-db`).

### New: last-resort capture path + full attempt reporting

- `_download_youtube_clip` now walks the format ladder on **errors as well
  as stalls**, records every attempt (`strategy/format/outcome/seconds/
  note`), and after the ladder is exhausted tries **direct media URL
  (`yt-dlp -g`) + plain ffmpeg cut** (stream copy, then re-encode) under the
  same stall guard. Its failure never masks the original error; the original
  exception carries `.attempts`.
- `download_clip` returns `attempts` + actual `resolution` (new
  `probe_clip_resolution` via ffprobe), **deletes a corrupt fresh download**
  (cache can never be poisoned), and reports cached-clip resolution on
  REUSE. Verified live: an 8-byte stub cache is deleted + re-downloaded.
- The run record + report now show: requested vs **actual resolution** (with
  a ⚠ when lower), clip path + reused/fresh, **every capture attempt**,
  frames planned/extracted/kept, layout native + scaled size (factor),
  **crops expected vs actual (10/frame)**, skipped slots with exact reasons,
  detection/export status, preflight table, and the exact next action on
  failure.

### Capture success is now independent of detection

`run_status_of`: detection skipped/error, filter-not-ready, or an
evidence-page problem → **PARTIAL, never failed**. A capture succeeds when
clip + frames + layout debug + crops + export succeed.

### run.html (control room)

Capture readiness panel (preflight chips) · **Start fast capture** /
**Start normal capture** / **Force re-download** buttons · Latest-run box
with **Open latest report / Open latest crops / Rebuild evidence** (uses new
GET /api/latest-run) · 8-step timeline incl. Preflight. No comp-writing
buttons; promote_detections.py untouched.

### Tests

**28 suites all green on Windows** (+`test_capture_reliability.py`, 46
checks: JS-runtime opt-in, muxed-first ladder, preflight DB auto-init +
source/layout checks, ladder-on-error, direct-url fallback + never-masks,
corrupt-download deletion, resolution probe, report contents, status
independence, page markup). test_run_owcs_auto/test_clip_stall updated for
the 8-step contract.

### Exact commands (verified on this machine)

```
python pipeline/init_db.py --with-sample     (or let preflight auto-init)
python pipeline/serve.py                     ->  http://localhost:8000/run.html
   pick owcs-8c105lnzlam · 0:06:00–0:06:30 · every 10 · Start fast capture
python pipeline/preflight.py --source owcs-8c105lnzlam   (readiness in a terminal)
python pipeline/run_owcs_auto.py --source owcs-8c105lnzlam --start 0:06:00 --end 0:06:30 --every 10 --fast --force
python pipeline/run_owcs_auto.py --local work/clips/<clip>.mp4 --start 0 --end 0:00:30 --every 10 --fast
```

### Known limitations

- `--fast` muxed-first usually lands 360p–480p (YouTube's progressive
  formats); normal runs still request DASH quality first and may spend one
  stall-guard window (180s) before falling back on machines where DASH
  sections stall. Actual resolution is always reported.
- Layouts scale automatically for same-aspect sizes (1080p layout → 640x360
  frames, factor reported); aspect mismatches skip per-slot with reasons.
- Detection quality is untouched this session (synthetic starter templates →
  LOW/NO-MATCH on real frames is expected); comps still NEVER auto-written.

---

## PREVIOUS SESSION — primary real VOD + cache safety + comp gate

Set up the first real end-to-end calibration target and the safe path to comps.

**A. New primary real-VOD source** `owcs-8c105lnzlam` (Crazy Raccoon vs ZETA
DIVISION, youtube `8C105lNzLAM`). Reference frame t=0:06:06 (366s) has a clean
10-portrait HUD. Dedicated calibration layout `layouts/owcs_8c105lnzlam.json`
(placeholder rects, ready to nudge). Shows on sources.html, selectable in
run.html. Test command:
`python pipeline/run_owcs_auto.py --source owcs-8c105lnzlam --start 0:06:00 --end 0:06:40 --every 10 --fast --force`

**B. Clip cache safety** (`video_ingest.probe_clip_valid` + InvalidClip;
wired into `download_vod_clip.download_clip`):
- a cached clip is validated (4KB byte-floor + ffprobe video-stream check when
  available) BEFORE reuse; an invalid/corrupt cache (e.g. 8-byte stub) is
  auto-deleted and re-downloaded, never fed to ffmpeg.
- a freshly-downloaded corrupt clip raises `InvalidClip` with a clear message
  ("cached clip invalid/corrupt …"), NOT a misleading "ffmpeg not found".
- probe failures now surface yt-dlp's real stderr; new remedies for SSL/network
  ("could not read vod metadata", "certificate_verify_failed", "sign in to
  confirm") so a network problem no longer reads as "yt-dlp not found".

**C. Safe comp-promotion gate** — NEW `pipeline/promote_detections.py`:
- reads a run's `detections.json`, classifies each per-team snapshot into
  `high` (all 5 slots ≥ threshold AND overall ≥ promote floor AND ≥2 consistent
  consecutive snapshots agree) vs `needs-review`.
- DRY RUN by default: writes `review_queue.json`, ZERO comps.
- writes `source='cv'` comps ONLY with `--write` AND explicit pairing
  (`--match/--map-order/--team-a/--team-b`); idempotent per (frame_hash, team);
  never deletes/overrides manual rows. FACEIT supplies structure only.
- the run report now shows a "Comp promotion (gated — nothing written yet)"
  section with the exact command, shown only when detection actually ran.
- runs.html links `review queue` alongside detections.json.

**Verified end-to-end** (the real VOD download itself can't run in this sandbox
— its TLS proxy blocks YouTube with a self-signed-cert SSL error, which the new
probe remedy explains correctly). Proven with a synthetic 1080p CR-vs-ZETA HUD
clip through the real layout: frames → filter (honestly "not ready", no anchor
template) → detection → 10-slot crop report (20 cells / 2 frames, OK/LOW/
NO-MATCH labels) → layout debug → promote gate correctly writing ZERO comps
(all frames quarantined, honesty invariant holds).

**Tests: 18 suites, all green** (+`test_promote_and_cache.py`: 20 checks for
cache safety + the promotion gate incl. idempotency and manual-override
preservation). Fixture demo verified.

---

## HISTORICAL (superseded) — control-room redesign + YouTube stall fix

Two things landed on top of the previous control-room build:

**A. Full visual redesign** (see the design-system section further down):
one shared `assets/css/style.css` v2 layer + `assets/js/ui.js`; index is now
a real landing/dashboard; run/runs/sources are HUD-styled control surfaces;
generated reports (run report, layout.html, crops.html) share the dark theme.
New offline suite `pipeline/test_static_pages.py`. No pipeline logic changed
by the redesign.

**B. YouTube clip-download no longer hangs.** The real bug: `yt-dlp
--download-sections` prints `Destination: ...` then never sends another byte,
and the old runner heart-beat forever. Fixed in `pipeline/video_ingest.py`:

- `_run_live` gained a **stall guard**: it distinguishes *real byte/frame
  progress* (`_is_progress`) from metadata banners and our own heartbeats. If
  no real progress arrives within `stall_timeout` seconds it kills the whole
  process tree (`_kill_proc_tree`, POSIX process-group / Windows `taskkill
  /T`) and raises `StallTimeout`.
- `_download_youtube_clip` now walks a **format fallback ladder**
  (`clip_format_ladder`) on a stall — simpler/smaller formats that usually
  start flowing (…480p → 720p muxed → `worst`). Only if *every* rung stalls
  does it give up.
- The `No supported JavaScript runtime found` warning is detected
  (`_saw_js_runtime_warning`) and surfaced as concrete setup advice.
- Timeouts thread through: `download_vod_clip.download_clip(stall_timeout=…)`
  → `run_owcs_auto.run_auto(stall_timeout=…)`. `--fast` uses **75s**, normal
  **180s** (`FAST_STALL_TIMEOUT` / `DEFAULT_STALL_TIMEOUT`), tunable via
  `--stall-timeout`. A stalled run gets `runStatus="timeout"` (grouped under
  "Failed" in runs.html, red pill in the report) and a clear remedy: *"YouTube
  section download stalled. Try a local MP4 (--local), another VOD, browser
  cookies, or install a JS runtime for yt-dlp."*
- New offline suite `pipeline/test_clip_stall.py` (22 checks): stalled process
  is killed, heartbeats aren't progress, real progress resets the clock,
  fallback ladder is walked + can recover, `--fast` uses the shorter timeout,
  JS-runtime warning surfaced, run maps stall → timeout status + remedy.

Verified with a fake stalling `yt-dlp` on PATH end-to-end: a `--fast` run
kills each stalled format in ~3s (test) / 75s (default), walks the ladder,
recovers if a later format flows, and otherwise fails as `timeout` with the
remedy — no endless heartbeats. Server stays responsive throughout.

**Test status: 17 suites, all green** (was 16; +`test_clip_stall.py`, and
`test_static_pages.py` from part A). Fixture demo verified.

Docs: `docs/windows-setup.md` gained a JS-runtime section + a "if a clip
download stalls" playbook.

---

## START HERE (new session)

Unzip, then everything runs from the browser:

```
pip install yt-dlp opencv-python-headless   (once; ffmpeg on PATH)
python pipeline/serve.py                     ->  http://localhost:8000/run.html
```

Run page: pick source `owcs-afcxdimpsle`, keep the prefilled 1:30:00–1:30:30
`--fast` window, click **Start run**, watch the live log, then follow the
report link / Runs page. Terminal is no longer required for run / test /
calibrate loops (see docs/control-room.md).

## What this session delivered

1. **NEW `pipeline/serve.py`** — local control-room server replacing
   `python -m http.server`: same static site + local-only JSON API
   (127.0.0.1, stdlib only, argv-list subprocess, ONE job at a time,
   409 on concurrent start). Endpoints: ping / sources / status?since=N
   (incremental live log tail) / run / evidence / test. Jobs are command
   SEQUENCES that stop at the first failure with the exit code in the log.
   This is a local dev tool, not a hosted backend — the public site stays
   static; user explicitly requested browser-driven test/debug.
2. **NEW `run.html`** — the control room page: source dropdown (from the
   API), window/every/height fields, --fast / force / with-audio toggles,
   Start run + Run all tests buttons, dark live-log panel with colored
   heartbeat/error/success lines, status pill, report link extracted from
   the log when the run finishes. Falls back to instructions when the API
   is absent (static hosting still works).
3. **runs.html** — per-run **regenerate evidence** button (appears only when
   the API is up): re-runs build_layout_debug + build_crop_report from the
   run's existing frames_raw, no re-download — the calibration loop.
4. Nav "Run" links; run_owcs_auto's final hint now points at serve.py
   (matching test assertion updated).
5. Fix found by tests: Handler.log_message crashed on int args (404 path),
   killing the connection — now str()-safe and only logs POST /api/ calls.

## Test status

**15 suites, 355 checks, all passing.** New `test_serve.py` (offline: real
ThreadingHTTPServer on an ephemeral localhost port + injected fake process
runner): ping/sources/static serving, 7 invalid /api/run payloads all
rejected 400 with named reasons and ZERO launches, argv built exactly
(--fast / --with-audio opt-in), incremental since=N tail, 409 while running,
evidence 404/400-with-remedy/correct two-command chain, chain stops at first
failing step with exit code, test job includes every suite. Fixture demo OK.

**Live-verified in this sandbox:** real serve.py + real API-driven --fast
run on a synthetic MP4 end to end (7/7 steps, PARTIAL, report link in log),
then API-driven evidence regeneration (layout.html + crops.html rebuilt),
run.html/runs.html served 200.

## Where the project stands vs BLUEPRINT.md

- Phase 1 (runs.html + per-run reports): DONE.
- Download optimization (video-only default, --fast, heartbeat, cache
  states, ext-rename fix): DONE.
- Phase 2 (layout.html + crops.html evidence, validate_layout): DONE code-
  side. **P2→P3 GATE OPEN: needs a real run on a machine with network —
  verify >=3 real frames in crops.html with all 10 boxes on portraits
  (calibrate layouts/owcs_youtube_2026.json via the browser loop).**
- Phase 3 next after the gate: real templates cut from verified crops +
  eval_detection.py measured accuracy. Phase 4 (gated comp promotion)
  after >=95% slot accuracy. Still ZERO comps in the DB (hard rule).

## Environment notes

Author pipeline files via bash heredocs if Edit-tool sync issues appear;
run tests from a /tmp copy (outputs folder can be write-once).

---

# OWCS Comp Tracker — Handoff (Phase 2: calibration & evidence)

## What this session delivered (Blueprint Phase 2)

1. **`validate_layout()`** (build_layout_debug.py) — advisory schema checks:
   5+5 slots, [x,y,w,h] shape, positive sizes, boxes/anchor/replay/score_map
   inside the declared frame size. Surfaced in layout.html, never blocking.
2. **`layout.html` per run** — `write_layout_html()`: validation box
   (green ok / amber warnings), every annotated debug frame embedded, slot
   coordinates, and the exact NO-re-download re-run commands
   (build_layout_debug + build_crop_report --frames-dir <same frames>).
   Standalone `build_layout_debug.py` main() now also emits it.
3. **NEW `pipeline/build_crop_report.py` → `crops.html`** — all 10 slot crops
   per frame (first 8 frames), each cell showing the crop, the best-matching
   hero template image, the score, and an OK / LOW / NO-MATCH pill
   (OK >= layout match_threshold 0.6, LOW >= 0.35 floor). No templates →
   loud "crops only" banner. Out-of-bounds slots degrade PER-SLOT to a
   "BAD BOX [coords] outside WxH frame" cell — verified live: 720p smoke
   frames against the 1080p layout produce exactly 15 BAD BOX cells with
   reasons, and 15 valid crops.
4. **Wired into the auto run** — `_step_debug` now writes annotated images +
   layout.html + crops.html (evidence pages best-effort, non-fatal); step
   detail reports "N annotated, M crops, K layout warning(s)"; run report
   index and runs.html cards link both pages.

## Test status

**14 suites, 352 checks, all passing** (+22: new `test_layout_evidence.py`
covering validate_layout warnings, layout.html contents/commands/links,
crop report with+without templates, BAD BOX degradation, label thresholds,
real `_step_debug` integration, report links). Fixture demo OK. End-to-end
local smoke run verified: run folder now contains
index.html / layout.html / crops.html / crops/ / layout_debug/.

## Calibration loop now (browser-first, per blueprint Phase 2)

```
1. python pipeline/run_owcs_auto.py --source owcs-afcxdimpsle --start 1:30:00 --end 1:32:00 --every 30
2. http://localhost:8000/runs.html -> run -> "layout debug"
3. Boxes off? edit layouts/owcs_youtube_2026.json, then (no re-download):
   python pipeline/build_layout_debug.py --layout layouts/owcs_youtube_2026.json --frames-dir work/auto/<run>/frames_raw --out reports/auto/<run>/layout_debug
   python pipeline/build_crop_report.py  --layout layouts/owcs_youtube_2026.json --frames-dir work/auto/<run>/frames_raw --report-dir reports/auto/<run>
4. Refresh layout.html / crops.html; repeat until all 10 boxes hold one portrait.
```

P2→P3 gate (from BLUEPRINT): >=3 real frames (different maps/teams) with all
10 boxes verified correct in crops.html — needs your machine (real frames).

## Still true / not yet

- Zero comps in the DB; no template tuning; no accuracy chasing (Phase 3).
- Crop scores currently come from the 17 synthetic starter templates —
  informative only, expect LOW/NO-MATCH on real frames until Phase 3.

---

# OWCS Comp Tracker — Handoff (download speed session)

## What this session delivered (on top of Phase 1 below)

**Goal: YouTube window downloads feel fast, visible, and reliable.**

1. **Video-only downloads by default.** `clip_format()` in `video_ingest.py`:
   `bestvideo[height<=H]/best[height<=H]/best` — no audio stream, no merge
   step (the old `bv*+ba` format downloaded audio too; that merge is also
   what produced the `...mp4.webm` filename). `--with-audio` opts back in
   (run_owcs_auto.py + download_vod_clip.py; `--video-only` accepted for
   explicitness on the clip tool).
2. **Ext-append rename fix.** If yt-dlp writes `clip.mp4.webm`, the single
   candidate is renamed back to the expected path so caching + ffmpeg keep
   working (ffmpeg sniffs content, not extension).
3. **`--fast` smoke mode** on run_owcs_auto: caps window at 30s, 480p
   (unless --height explicit), sampling >=10s. Detection then skips with the
   explained resolution reason — expected; the mode proves capture->report.
4. **Heartbeat in `_run_live`:** reader thread + queue (Windows-safe, no
   select); if no output for 12s prints
   `[yt-dlp] still downloading... elapsed Xs (no output for Ys)`.
   `heartbeat_every`/`idle_msg` injectable for tests.
5. **Cache states announced:** `download_clip` logs REUSING (complete clip),
   RESUME (found `.part`), DELETING (--force removes clip + .part).
   `--force` added as alias of `--force-clip` on run_owcs_auto.
6. **Direct no-clip extraction:** intentionally NOT wired into run_owcs_auto —
   per-timestamp remote seeking proved unreliable deep into VODs (prev
   handoff); documented as the explicit fallback instead.
7. **`docs/fast-workflow.md`** — smoke / normal / calibration commands.

Test status: **13 suites, 330 checks, all passing** (+19 this session:
format selection, yt-dlp cmd audio assertions, run_auto with_audio threading,
ext-rename, --fast window/height/every/explicit-height/small-window,
heartbeat fires + no spam, REUSING/RESUME/DELETING cache messages).
Fixture demo OK; `--fast` local smoke run verified end to end incl. data.js.

Real-network commands to verify on your machine (sandbox has no yt-dlp):
```
python pipeline\run_owcs_auto.py --source owcs-afcxdimpsle --start 1:30:00 --end 1:30:30 --every 10 --fast
python pipeline\run_owcs_auto.py --source owcs-afcxdimpsle --start 1:30:00 --end 1:32:00 --every 30 --height 480
python pipeline\download_vod_clip.py --source owcs-afcxdimpsle --start 1:30:00 --end 1:32:00 --out work/clips/test.webm --video-only
```

---

# OWCS Comp Tracker — Handoff (Phase 1: runs.html + per-run reports)

## What this session delivered (Blueprint §4 — Phase 1 complete)

**1. Per-step status records.** `run_owcs_auto.py` now records every step as
`{name, status, detail, out}` in the run record (`steps` array, 7 steps:
probe / clip / frames / filter / detect / layout-debug / export). Statuses:
`ok` / `skipped` / `error` / `failed` / `not-run`. On failure the failing step
is named and all remaining steps are explicit `not-run` — never silently absent.
Run-level label `runStatus`: `ok` / `partial` / `failed` (blueprint §2 labels).
The record also stores `layout` and resolved `height`.

**2. Rich per-run report.** `reports/auto/<run_id>/index.html` is now a real
report: status pill, summary table, colored step table, layout-debug thumbnails
(first 8), error box with a **remedy line** (`remedy_for()` maps yt-dlp/ffmpeg/
seek/window errors to exact next actions), links to detections.json /
layout_debug/ / runs.html. Generated by one pure string-template helper
(`build_report_html`), HTML-escaped, wrapped best-effort (report failure never
kills a run). Reports are produced on success AND failure.

**3. New `runs.html`.** Expandable run cards from `OWCS_DATA.autoRuns`:
run/mode/status pills, window/frames/detection/layout meta, links to the full
report + debug images, `<details>` step table with per-step pills. Client-side
`runStatus` fallback for pre-upgrade records. Empty state shows the exact
commands to run. Nav links added on sources.html and admin.html.

**4. Automation bug fixes found while smoke-testing:**
- **Detection preflight** (`detect_preflight`): before detection runs, the frame
  resolution is checked against the layout's `frame_width/height` and every slot
  box is bounds-checked. Mismatches now yield
  `skipped — frame is 1280x720 but layout expects 1920x1080 — re-run with
  --height 1080 ...` instead of a raw `cv2.resize` assertion crash.
- **Self-consistent default height:** `--height` now defaults to the layout's
  `frame_height` (was hardcoded 720 while the default layout is 1080p — the
  defaults could never produce detectable frames). Explicit `--height` still wins.
- **Export self-reference fix:** the run record is completed (export step, final
  `runStatus`, `finishedAt`) and upserted BEFORE `export_data` runs, so `data.js`
  is never one status behind for the current run (previously the site showed
  6/7 steps and stale status until the *next* run exported). If export then
  throws, the export step flips `ok → failed` in place (no duplicate row) and
  the run downgrades to `failed`.

## Test status

**13 suites, 311 checks, all passing** (was 278; +33 this session:
step records, success+failure reports, remedy mapping, HTML escaping,
report non-fatality, preflight, layout-height default, export self-reference,
export-failure downgrade). Fixture demo verified. End-to-end smoke run
(synthetic MP4, local mode) verified: PARTIAL run, complete 7-step table in
exported `data.js`, confirmed via node against the real `runs.html` contract.

## Files changed

- `pipeline/run_owcs_auto.py` — steps tracking, `remedy_for`, `run_status_of`,
  `detect_preflight`, `_layout_frame_height`, `build_report_html`, safe
  `write_report_index`, height default, export ordering fix.
- `runs.html` — NEW.
- `sources.html`, `admin.html` — nav gained a Runs link (one line each).
- `pipeline/test_run_owcs_auto.py` — +33 checks (see above).

## Verified commands (Windows, real network optional)

```
python pipeline\run_owcs_auto.py --local <clip.mp4> --start 0 --end 0:30 --every 10
python pipeline\run_owcs_auto.py --source owcs-afcxdimpsle --start 1:30:00 --end 1:32:00 --every 30
python -m http.server 8000    ->  http://localhost:8000/runs.html
```
Note: with the height default fix, `--source` runs against the 1080p layout now
download 1080p clips by default. Pass `--height 720` to keep smaller clips
(detection will then be skipped with the explained resolution reason).

## Environment notes (unchanged from previous handoff)

Run tests from a `/tmp` copy; author pipeline files via bash heredocs if the
Read/Edit sync quirk reappears; `outputs` files may be immutable once written.

## Next (Blueprint Phase 2 — do NOT start Phase 3/4 work)

1. Real-machine verification of the two commands above (this sandbox has no
   yt-dlp/network).
2. Phase 2: `layout.html` per-run (extend `build_layout_debug.py`) + new
   `pipeline/build_crop_report.py` → `crops.html`, then calibrate
   `layouts/owcs_youtube_2026.json` against real frames.
   Gate to pass first (P1→P2): both failed and successful runs produce complete
   linked reports — DONE in fixtures; confirm once on a real run.
3. Still zero comps written to the DB; no full-VOD passes (hard rules).

---

## Session update — highlight rejection + detection-regression lock

**Delivered, all offline/deterministic, full suite = 21 suites / 0 fail.**

### 1. HIGHLIGHT / reject-marker frame rejection (installed, feature OFF)
Additive, backward-compatible. New optional layout key `reject` (a LIST of
marker dicts) → banners (HIGHLIGHT/HIGHLIGHTS/POTG) reject a frame the same
way `replay` does. Absent key = feature OFF = unchanged behavior.
- `pipeline/capture.py`: `_load_reject_markers(layout)` + `reject_reason(gray,
  markers)` (the single OCR-extension point — a future `kind:"text"` branch
  plugs in here, no filter rewrite); `is_gameplay(...,rejects=None)` checks
  rejects FIRST (so a banner is caught even without the HUD anchor);
  `scale_layout_to_frame` scales reject rects; `process_video` loads+passes them.
- `pipeline/frame_filter.py`: loads + passes `rejects`.
- `layouts/owcs_youtube_2026.json`: inert `_reject_example` (rename → `reject`
  to activate; still needs a real `-highlight.png` crop cut).
- Tests: `pipeline/test_frame_filter_highlight.py` (24 checks).
- NOT done on purpose: no real highlight template cut, no OCR, feature not active.

### 2. Detection-regression lock (tests only, froze current behavior)
`pipeline/test_detection_regression.py` (43 checks) locks TODAY's detector
before any masked-template work. Reuses `test_pipeline_synthetic` fixtures;
isolated temp templates (real `templates/` untouched); no DB/network.
Freezes: golden read (exact heroes both sides + confidence ≥ threshold),
side orientation (slots_a→a/left, slots_b→b/right; fails on swap), low-confidence
quarantine (blank frame → clear `low-confidence` reason), duplicate-hero
quarantine (behavior lives in `detect.validate`), and a tint-cast invariance
lock (grayscale + TM_CCOEFF_NORMED is tint-invariant on synthetic icons →
scores stay ≥0.9).

**Important nuance for the NEXT session:** the tint test locks the *invariance
property on synthetic icons only*. It is NOT proof that real broadcast team-tint
is handled. The real red/blue broadcast-tint acceptance test needs actual tinted
crops and belongs to the future **masked-template matching** patch (add an alpha
mask to `detect.match_slot` via `cv2.matchTemplate(..., mask=...)`, design ref:
overtrack-cv's tab processor — concept only, AGPL, never copied). When that
patch lands, add a real-crop tint acceptance test alongside these locks.

### Still not done (unchanged hard rules)
No comps written to DB, no full-VOD run, no detection/template/promote/FACEIT/
team-side logic changes this pass.

---

# SESSION: Public site milestone — Tournament Hub, Match Detail, Stats foundation

## What shipped

A complete public fan-facing layer next to (not instead of) the control-room
pages. **5 public pages · 16 new source files · 1 new test suite ·
27/27 suites green · 0 console errors** verified in headless Chromium at
1280px and 380px.

### New public pages
| Page | URL | What it does |
|---|---|---|
| Tournaments | `tournaments.html` | Filterable event index (region/year/tier/status, URL-persisted, reset, count, empty state) |
| Tournament detail | `tournament.html?id=kyoto-inv-2026` | Event header + 8 tabs: Overview, **Bracket**, Matches, Standings, Teams, Maps & bans, VODs, **Capture status** |
| Match detail | `match.html?id=pm-ubsf1` | VS band + 7 tabs: Overview, Maps (typed score widgets), Bans, **Comps**, VOD, **Evidence**, Review |
| Stats | `stats.html` (rebuilt as public) | Region-first segmented control + tournament/team/map filters, sortable hero pick/win table, ban table — verified comps only, every row evidence-linked |
| Matches | `matches.html` (rebuilt as public) | Live / Upcoming / Recent schedule, region filter, capture chips, local-time rendering |

### Architecture decisions
- **Two shells, zero collisions.** Public pages use `assets/css/public.css` +
  `assets/js/public/*`; all control-room pages keep `style.css`/`ui.js`
  untouched (asserted by tests). Nav cross-links both worlds.
- **One data contract.** Everything renders from `window.OWCS_PUBLIC`
  (`assets/data/public_fixture.v1.js`, `meta.demo:true` → visible ribbon).
  Full spec in `docs/PUBLIC_DATA_CONTRACT.md`. `export_data.py` never writes
  the fixture path (tested); a future production export writes
  `public_data.v1.js` with the same shape.
- **Credibility rules enforced in one place.** `OWCS_PUB.publicComps()`
  (core.js): review status ∈ {reviewed, auto-high} only, source ∈ {cv,
  manual} only, manual `overridesId` hides (never deletes) the CV row. The
  fixture deliberately contains a `needs-review` snapshot and the tests +
  a node functional check prove it never renders.
- **Vendored motion, no CDN at runtime:** `assets/vendor/lenis.min.js`
  (v1.1.14) + `gsap.min.js` (v3.12.5). **Vanta was skipped on purpose** —
  it requires three.js (~600 KB); replaced with a ~40-line canvas particle
  ambience in shell.js (20 fps, pauses when hidden, off under
  reduced-motion). React Bits patterns ported to vanilla: spotlight cards,
  count-up numbers, animated tab underline.
- **Design system ("arena"):** navy-charcoal surfaces, Saira Condensed /
  Archivo / IBM Plex Mono, region+source+role color tokens. Signature
  elements: skewed broadcast plates, gold winner path in brackets, teal
  evidence tick on every verified fact. Status chips always glyph+word,
  never color alone. 44px targets, keyboard tabs (arrows/Home/End),
  focus-visible rings, prefers-reduced-motion kill switch.
- **Bracket readability:** upper row = UB rounds + GF with gold winner
  path and "loser → Lower Bracket X" labels; lower row beneath; ≤720px a
  side switcher (Upper/Lower/GF) swaps full-width stacked columns instead
  of squeezing columns. Legend explains every mark.
- **Evidence chain UX:** Match → VOD → Run → frames → approved comps as a
  step chain; run card shows requested vs actual resolution (mismatch ⚠ —
  the 720p fixture run demonstrates it); frames + crops link to the real
  `reports/capture_trial/` assets; Review tab shows exact terminal
  commands and states the site never executes anything.

### Files added
```
assets/css/public.css                assets/js/public/core.js
assets/data/public_fixture.v1.js     assets/js/public/shell.js
assets/vendor/lenis.min.js           assets/js/public/stats.js
assets/vendor/gsap.min.js            assets/js/public/page-tournaments.js
tournaments.html                     assets/js/public/page-tournament.js
tournament.html                      assets/js/public/page-match.js
match.html                           assets/js/public/page-stats.js
docs/PUBLIC_DATA_CONTRACT.md         assets/js/public/page-matches.js
pipeline/test_public_site.py
```
Files modified: `stats.html`, `matches.html` (rebuilt as public pages),
`pipeline/test_static_pages.py` (those two moved to a public-shell
assertion; old-shell coverage otherwise unchanged).

### Tests
- `python3 pipeline/test_public_site.py` — 89 checks: fixture schema +
  referential integrity (bracket feeds, run ids, hero/map ids), typed
  scoreDetail per mode (all 6 modes exercised), comp credibility rules,
  evidence paths resolve to real files, capture ladder honesty
  (incl. resolution-mismatch fixture), page shell/a11y markup,
  demo/production separation, control-room pages untouched.
- All 27 suites pass: `for f in pipeline/test_*.py; do python3 "$f"; done`
- Node functional smoke (manual): publicComps excludes needs-review +
  overridden CV, includes manual override; Tracer 2 picks / 50% WR correct.

### Windows verification
```
cd owcs
for %f in (pipeline\test_*.py) do python %f
python -m http.server 8000
```
Then open:
```
http://localhost:8000/tournaments.html
http://localhost:8000/tournament.html?id=kyoto-inv-2026&tab=bracket
http://localhost:8000/tournament.html?id=na-open-q4-2026        (empty states)
http://localhost:8000/match.html?id=pm-ubsf1&tab=comps           (verified comps + correction)
http://localhost:8000/match.html?id=pm-ubsf1&tab=evidence        (evidence chain + real frames)
http://localhost:8000/match.html?id=pm-groups-forfeit            (forfeit + failed capture)
http://localhost:8000/stats.html?region=asia
http://localhost:8000/matches.html
```

### Known limitations / next milestone
- Hero portraits are role-colored monogram tiles (no hero art in repo);
  `heroes[].portraitUrl` is already supported when assets exist.
- Team logos are deterministic monogram plates; `teams[].logoUrl` supported.
- Teams/Players public pages not built (control-room `teams.html` remains);
  nav intentionally lists only built destinations.
- Production export (`public_data.v1.js` writer in `export_data.py`) is the
  natural next chunk — the contract doc specifies exactly what to emit,
  and the page script-tag swap is one line per page.
- Google Fonts load from CDN with full system fallbacks; fully offline
  environments render with Arial Narrow/system-ui automatically.
