# Prototype plan — one pasted YouTube link → every comp, every wanted datum

**Status report + full implementation plan. No code changed by this document.**

Scope of the target: a human pastes an official OWCS broadcast URL. The system
acquires it, works out *what it is* (event, match, teams, maps, sides, players),
detects every hero composition and swap on every map in that VOD, reads the
supporting facts (bans, scores, winners), writes them into the content DB with a
full evidence chain, and publishes them to the public site — with named human
gates instead of guesses.

---

## PART 1 — STATUS REPORT (2026-07-27)

### 1.1 What is real and working today

| Layer | State | Evidence |
|---|---|---|
| Content DB schema | ✅ complete for this goal | `pipeline/schema.sql`: `heroes`(52), `game_maps`(28), `teams`, `matches`, `map_results`, `hero_bans`, `map_veto_events`, `comp_snapshots`/`snapshot_heroes`, `ingest_runs`, `ingest_findings`, `slot_observations`, `map_rounds`, `hero_stints`, `hero_swaps`, `players`, `match_rosters` |
| HUD calibration | ✅ computational, refuses when unsure | `calibrate_source.py` (HSV chip rows + RANSAC grid fit + pixel verification, refuses < 0.55 confidence, writes resolution-independent layout + review sheet) |
| Frame classification | ✅ | `gameplay_state.py` + `capture.is_gameplay` + reject markers (HIGHLIGHTS/REPLAY/scoreboard) — replays/scoreboards can never become comps |
| Hero detection | ✅ | `detect.py` (ranked candidate + runner-up + margin, returns `UNKNOWN` rather than guessing) |
| Role-constrained resolution | ✅ | `comp_solver.py` (exact 1 Tank / 2 DPS / 2 Support, no duplicate heroes, role-inferred slots reported at their real lower score) |
| Full-map ingest + swaps | ✅ | `ingest_map.py` (adaptive sampling, per-slot temporal hysteresis, emblem-based rounds, side-swap tracking, evidence crops, staged idempotent writes) |
| Team identity from video | ✅ built, analysis-only | `team_identify.py` (OCR name zones → fuzzy match vs name **and** code → ≥N-frame consensus → cross-check operator claim) |
| Ban detection from video | ✅ built, analysis-only | `detect_bans.py` (pick/ban screen OCR → hero resolution → side by frame half → temporal consensus) |
| HUD OCR diagnostics | ✅ | `ocr_hud.py` (scene classification, team zones, hero-text candidates, layout sanity, pluggable easyocr/tesseract/paddle, fake reader in tests) |
| Automation spine | ✅ Phases A–D3 | job store, state machine, leases/heartbeats/crash-steal, deterministic job keys, dead-letter, `config/automation.yml` |
| Discovery | ✅ Phases B/C | FACEIT sync, OWCS calendar adapter, YouTube client with quota accounting, broadcast discovery + explainable scoring + broadcast-likeness pre-filter |
| Closed-loop worker | ✅ Phases E/F/G/I | `worker.py` (download via existing yt-dlp/ffmpeg, media metadata, 8 classified failure modes, resume-after-crash), `segmentation.py` (candidate map windows + review CRUD + ffmpeg extraction), `detection_runner.py` (drives `ingest_map.py` unchanged, two-phase dry-run→approve→write), `publish.py` (preconditions, export regen+validate, packaging/secret/media checks, scoped commit+push) |
| Public export + site | ✅ | `export_data.py --public` → `assets/data/public_data.v1.js` (meta, regions, teams, players, tournaments, brackets, matches, heroBans, heroSwaps, captureRuns, compSnapshots, vodSources, heroes, mapsCatalog, patches) rendered by 14 static pages |
| Assets | ✅ | 52/52 official hero portrait/artwork/icon sets; 5 verified team logos published, 2 held for a human, 2 unresolved |
| Proven end-to-end | ✅ once | Al Qadsiah vs Twisted Minds, Nepal — 3 rounds, 299 frames, 1,950 accepted slot reads, 2 confirmed swaps with before/after crops, 7 rejected noise swaps, live on the production dataset |

### 1.2 The eleven gaps between today and the goal

| # | Gap | Where it bites | Severity |
|---|---|---|---|
| G1 | **No URL-only intake.** `create-job` requires `--match` (must already exist), `--video-id`, `--channel-id`, `--team-a`, `--team-b`, `--layout-id` | `cli.py:1245` | blocker |
| G2 | **Pasted links have no authorization path.** `worker.validate_source` accepts a verified registry channel *or* `manual_approved_video_ids` — but **nothing anywhere populates that set** | `worker.py:140`, `ops.py:130` | blocker |
| G3 | **Whole-VOD acquisition is unmodelled.** `download_clip` takes start/end at a chosen height; there is no full-broadcast fetch, no disk sizing from duration, no cheap scan proxy | `download_vod_clip.py:44` | blocker |
| G4 | **Layout resolution is manual.** Nothing picks or creates a layout for an unseen broadcast package; `calibrate_source.py` is never called by the job loop; reject markers + `round_emblem` are hand-cut per package | `layouts/*.json`, `HANDOFF.md` "Repeatable workflow" | blocker |
| G5 | **Template coverage is the real ceiling on "all comps."** Committed sets: 17 generic + `owcs_jksix_qwc` (8 heroes × 5 variants) + `owcs_8c105lnzlam` (~4). The game has **52** heroes. Detection can only ever name a hero it has a template for | `templates/` | blocker |
| G6 | **Map identity is operator input.** `approve_segment` demands `map_name`/`map_mode`; no code reads the map name off the broadcast | `segmentation.py:257` | blocker |
| G7 | **Map winner + scores are operator input.** `--map-winner` is trusted; per-round control %, map score and series score are never read | `HANDOFF.md` "Honest limitations" | high |
| G8 | **Players are never linked to slots.** `players`/`match_rosters` come from FACEIT only; the nameplate row under the HUD is never OCR'd | `schema.sql:363` | high |
| G9 | **Series structure is not derived.** One VOD = many maps = one series; nothing assembles `map_results` ordering, `bestOf`, or the series score from a single link | `export_data.py` | high |
| G10 | **No segment-review UI.** Review is CLI-only (`segment-list`/`segment-approve`); a human cannot see the proposed map windows | `docs/AUTOMATION.md` "Not yet implemented" | medium |
| G11 | **No real VOD has traversed the loop.** This sandbox's egress policy denies `www.youtube.com` (policy 403, confirmed) and ships no ffmpeg/yt-dlp/opencv by default | `HANDOFF.md` "Honest gap" | process |

### 1.3 Prototype definition (what "successful" means)

One pasted official VOD URL → **every map in that broadcast** ingested, with:

* every five-hero composition per team per round, evidence-backed;
* every confirmed hero swap (and the rejected-noise ledger);
* map identity, team identity, side assignment, map order, map winner;
* hero bans where a pick/ban screen exists;
* players attached to slots where nameplates are readable;
* a published match page reachable from the calendar, both team pages, every
  hero page, `comps.html`, `swaps.html`, `stats.html` and `maps.html`;
* **zero hand-edited database rows**, and every single field either carrying an
  evidence chain or explicitly marked `needs-review` — never invented.

Four human gates are *in scope* for the prototype (source approval, layout
confirmation, segment confirmation, detection approval). Removing them is a
later phase, not this one.

---

## PART 2 — TARGET OPERATOR SURFACE

```bash
# The whole prototype, from a human's point of view:
python pipeline/automation/cli.py ingest-link --url "https://www.youtube.com/watch?v=<id>"
#   -> prints a link-job id, what it resolved, and the first gate that needs a human

python pipeline/automation/cli.py link-status --job <id>       # where it is, what it needs
python pipeline/automation/cli.py approve-source --video <id> --confirm --approved-by "<you>"
python pipeline/automation/cli.py approve-layout --job <id> --confirm
python pipeline/automation/cli.py segment-approve --segment <n> [--accept-proposed]
python pipeline/automation/cli.py detect-job --job <id>            # dry run + review report
python pipeline/automation/cli.py detect-job --job <id> --write    # after approval
python pipeline/automation/cli.py process-approved-job --job <id> --publish
```

Plus `worker-run --worker-id <name>` to advance every automatic step unattended
between gates, and a new read-only `intake.html` panel in the control room
showing each link's stage, proposals, and the exact next command.

**Design rules carried over from the existing codebase (non-negotiable):**
no guessed identity; every derived field records `source` + `confidence`;
`UNKNOWN` beats a wrong answer; nothing is deleted on failure; every stage is
idempotent under a deterministic key; dry-run writes nothing; a human approval
is the only path past each gate.

---

## PART 3 — THE FIFTEEN STAGES

### S0 · Intake and normalization  *(new: `automation/link_intake.py`)*
* **In:** a URL string typed by a user.
* **Do:** parse to `(host, video_id)`; reject non-`youtube.com`/`youtu.be`;
  canonicalize (`watch?v=`, `youtu.be/`, `/live/`, `&t=` all collapse to one id);
  dedupe on video id — a re-paste attaches to the existing job, never a second one.
* **Out:** `jobs` row, `kind='link-ingest'`, `job_key=link:<video_id>`,
  state `DISCOVERED`, payload `{sourceUrl, videoId, submittedBy, submittedAt}`.
* **Fails:** malformed URL, non-YouTube host, playlist/channel URL → refused with
  the reason, no job created.

### S1 · Provenance and authorization  *(new: `manual_source_approvals` table; reuse `youtube_api.py`)*
* **Do:** one `videos.list` call (1 quota unit) → `channelId`, title, description,
  `duration`, `publishedAt`, `liveStreamingDetails`, thumbnails, default language.
  Then classify the source:
  * channel id ∈ verified `config/broadcast_channels.json` → **auto-authorized**;
  * otherwise → **held**, requiring `approve-source --confirm`, which writes an
    audited row into the new `manual_source_approvals` table.
  * `broadcast_matching.broadcast_likeness(video)` runs as a sanity signal — an
    `unlikely` video (promo/guide/short) is flagged loudly, never silently accepted.
* **Wiring:** `worker.claim_and_lock`/`validate_source` already accept
  `manual_approved_video_ids`; this stage is what finally populates it (**closes G2**).
* **Out:** payload gains `{channelId, title, durationSeconds, publishedAt, isLive,
  authorization: registry|manual|held, likeness}`. State → `ARCHIVED` (ready).

### S2 · Event and match resolution  *(reuse `broadcast_matching.py`; new `title_parse.py`)*
* **Do:** score the video against every target pooled from `scheduled_matches`,
  `source_events`, and the committed `owcs_calendar.load_events()` seed — the
  existing three-source matcher. In parallel, parse the title/description with a
  conservative grammar (`<TEAM> vs <TEAM>`, `Day N`, `Week N`, stage/round words,
  region tokens), resolving names through the team registry's aliases + codes.
* **Decide:**
  * HIGH-confidence match to an existing `matches` row → link it, set `vod_url`.
  * A confident *event* but no match row → create a **provisional** match with
    `fixture_kind='production'`, `lifecycle_status='needs-review'`,
    `source_authority='link-intake'`. The export gate already refuses
    unevidenced stubs, so it stays invisible until the loop evidences it.
  * Nothing confident → identity deferred to S6, which reads it off the screen.
* **Never:** merge two teams on name similarity; overwrite a FACEIT-sourced fact
  with a title-parsed one (`reconcile.py`'s precedence rules apply).

### S3 · Acquisition  *(extend `worker.download_job`)*
* **Preflight:** `worker.check_dependencies` (yt-dlp/ffmpeg/opencv versions) and
  `check_disk_space`, now sized from the real duration
  (`duration × bitrate_estimate × 1.4` headroom, refuse before the first byte).
* **Do:** one full-VOD fetch at **720p** into gitignored `data/worker/<video_id>/source.mp4`
  (720p is the resolution the verified Nepal ingest and the committed reject
  markers are cut at; layouts themselves are resolution-independent). Record
  sha256, duration, resolution, codec, container, byte size, yt-dlp/ffmpeg
  versions. Then produce a local **360p scan proxy** with one ffmpeg pass — every
  scanning/OCR stage runs on the proxy, so the expensive file is fetched exactly once.
* **Resume:** `worker.resume_interrupted` + `download_clip`'s cache reuse make
  re-entry safe after a crash or a killed stall.
* **Fails:** the existing 8 classified codes (missing_dependency, corrupt_media,
  network_stall, invalid_source, insufficient_disk, download_failed, timeout,
  unknown) — never a bare crash.

### S4 · Broadcast-package fingerprint and layout resolution  *(new: `layout_resolver.py`, `marker_harvest.py`)*
* **Fingerprint:** sample ~40 frames spread across the proxy; for each committed
  `layouts/*.json`, score the anchor template + `hud_probe` pixel signature.
  A confident hit reuses that layout id (this is the common case for a stable
  season overlay — it is why the second VOD is cheap).
* **No hit → autocalibrate:** pick 6–8 spread frames that `gameplay_state`
  classifies as live, run `calibrate_source.py` unchanged, write
  `layouts/auto_<video_id>.json` + `reports/calibration/<id>/sheet.png`.
  Its existing refusal below 0.55 confidence is preserved — a refusal becomes a
  review task, never a downgrade to guessing.
* **Markers:** `marker_harvest.py` proposes REPLAY/HIGHLIGHTS/scoreboard crops
  and a `round_emblem` rect from frames the classifier already rejects as
  non-gameplay, writing `layouts/auto_<id>-{replay,highlight,scoreboard}.png`.
* **Gate 2:** `approve-layout --confirm` after a human looks at the sheet.
  Once approved for a package, every later VOD on that package skips this whole stage.

### S5 · Map segmentation  *(reuse `segmentation.py`)*
* **Do:** `generate_candidates` over the proxy — the existing HUD-anchor/reject-marker
  classifier, grouped with configurable pre/post-roll and flicker tolerance.
  Emit per candidate: start/end offsets, gameplay-frame count, confidence, and
  three thumbnails (start / mid / end).
* **Out:** `map_segments` rows, `review_status='candidate'`.
* **Expect:** a BO5 broadcast day yields ~3–5 real windows plus desk/interview
  stretches correctly excluded.

### S6 · Per-segment identity resolution — *the new intelligence layer*
Runs on each candidate window before any human sees it, so review is
*confirmation*, not data entry. Every field carries `{value, source, confidence,
frames_agreeing}` and lands in `ingest_findings`.

| Field | Module | Method |
|---|---|---|
| Map name + mode | **new `map_identify.py`** | OCR the map-intro / scoreboard / round-banner label; fuzzy-match against `game_maps`(28) with the same ambiguity-safe matcher `ocr_hud.match_hero` uses; temporal consensus ≥3 frames; mode read from the matched row, never guessed (**closes G6**) |
| Team A / Team B | `team_identify.py` *(exists)* | OCR left/right name zones → fuzzy match vs registry name **and** code → ≥N-frame consensus → cross-check S2's claim (agree / disagree→review / no-signal) |
| Side assignment + swaps | `ingest_map.py` *(exists)* | side-hue + chip-hue continuity across rounds |
| Map order | `link_intake` | chronological by segment start within the VOD |
| Players per slot | **new `player_identify.py`** | OCR the nameplate row beneath the ten slots; match against `match_rosters`/`players`; an unrecognized handle is recorded as a *candidate*, never auto-created (**closes G8**) |
| Layout id | S4 | carried through |

Anything without consensus stays `UNKNOWN` and is presented as an empty field
the reviewer must fill — `approve_segment` already refuses a half-described segment.

### S7 · Gate 3 — segment review  *(new `intake.html` panel; CLI already exists)*
* A thumbnail timeline per candidate with the S6 proposals pre-filled, an
  `--accept-proposed` fast path for the common case, and edit/split/merge/reject
  actions that already exist in `segmentation.py` (**closes G10**).
* Approval writes map identity, teams, sides, order, layout onto the segment,
  then `extract_segment_clip` cuts the **720p** window (not the proxy) via the
  existing `video_ingest._ffmpeg_cut_from_url` helper against the local file.

### S8 · Template readiness  *(new: `template_bootstrap.py`)* — **the "all comps" enabler**
Detection can only name heroes it has templates for; this is G5, the single
biggest limiter on completeness. Four-step ladder, cheapest first:

1. **Reuse** the per-source template dir if S4 matched a known package.
2. **Seed** a candidate reference set from the 52 committed official hero icons
   (`assets/img/heroes/official/<id>/icon.png`), rescaled to the layout's slot
   geometry. Icons are used **only for labeling clusters** — never as the
   matching templates themselves, because broadcast portraits are stylized and
   an icon-vs-portrait score is not honest evidence.
3. **Harvest** real crops with `harvest_templates.py --cluster` over the map,
   auto-label each cluster by matching its medoid against the seeded icons, and
   surface **only** clusters whose top-1/top-2 margin is below threshold for a
   human to name (this collapses the existing hour-long labeling pass to minutes).
4. **Emit** multi-variant per-hero templates (`--variants 5`: alive/dead/ult
   states) into `templates/<layout_id>/`, and record template provenance plus a
   coverage number (`heroes covered / 52`) in the run report.

Every VOD processed permanently widens the committed template library, so
coverage compounds toward 52/52 across real broadcasts. A hero with no template
still reads `UNKNOWN` — never a nearest-neighbour guess.

### S9 · Detection  *(reuse `detection_runner.py` → `ingest_map.py`, unchanged)*
Per approved segment, `run_detection(write=False)` builds the exact
`argparse.Namespace` `ingest_map.run()` expects and produces, staged:

* `ingest_runs` — the run, its window, layout, calibration version, status;
* `slot_observations` — every sampled read: hero, score, runner-up, margin, frame path;
* `map_rounds` — emblem-derived round boundaries and side state;
* `hero_stints` — the composition timeline (what the public comps are built from);
* `hero_swaps` — temporal-consensus verdicts, **confirmed with before/after
  evidence crops and rejected with the reason** (this contract is already what
  `swaps.html` renders);
* `ingest_findings` — every unresolved/ambiguous item for review;
* `reports/ingest/<id>/report.html` + `review.html` — the full-map report and a
  per-change-point review page.

`ingest_id_for(job, segment)` is stable, so a rerun replaces only its own rows.
Failure classes (layout mismatch, no valid frames, missing templates, …) are
already enumerated in `classify_detection_error`.

### S10 · Supporting facts  *(reuse `detect_bans.py`; new `score_read.py`)*
* **Bans:** `detect_bans.py` on the pre-map draft window → confirmed bans into
  `hero_bans` (`source='cv'`, with `evidence_path`), candidates into findings.
* **Scores (new `score_read.py`, closes G7):** OCR the layout's `score_map`
  region at round ends and at the post-map scoreboard → per-round outcome, map
  score, map winner, and the running series score, each under temporal consensus.
  Without consensus the field stays operator-supplied and is flagged — the
  current honest limitation is narrowed, not papered over.
* **Series assembly (closes G9):** map order + per-map winners → `map_results`
  rows, `matches.score_a/score_b/winner_team`, and an inferred `bestOf` from the
  map count and the score pattern, marked `needs-review` when ambiguous.

### S11 · Gate 4 — detection review  *(exists)*
Human reads `report.html` + `review.html`, approves → job `APPROVED` →
`detect-job --write` commits the identical run. Nothing reaches the public
dataset without traversing this state.

### S12 · Aggregation and export  *(reuse `export_data.py --public`)*
Regenerates `public_data.v1.js` from the DB — comps only from approved stints
with intact evidence chains — plus the derived rollups the pages consume:
comp frequency and win rate, hero pick/win rate sliced by map/mode, swap
intelligence including the rejected ledger, team hero pools and map records,
map meta, and calendar/tournament placement.

### S13 · Assets and completeness reporting  *(exists)*
`collect-team-assets` proposes candidate marks for any newly-seen team (human
approval still the only publish path); `coverage`, `team-coverage --save`,
`job-coverage --save`, `export-coverage` refresh the four dashboards so nothing
that failed can quietly disappear.

### S14 · Publication  *(reuse `publish.py`)*
`process-approved-job --job <id> --publish`: precondition checks (review
complete, match/map/team identity resolved, layout matched, evidence present),
export regeneration + validation, `check_packaging.py`, secret scan, staged-media
check, then a scoped commit on a fresh branch and a push. PR → CI → merge →
Pages stays the existing working flow. A `publication_runs` row records
`db_hash`/`export_hash`/`branch`/`source_commit`/`state`.

---

## PART 4 — DATA INVENTORY: everything one link should write

### 4.1 Written by the prototype

| Table | Fields populated | Source of truth |
|---|---|---|
| `matches` | id, event_name, season, stage, round, region, date, scheduled/started/finished, status, lifecycle_status, team_a/team_b, score_a/score_b, winner_team, vod_url, source_url, competition_id, fixture_kind | S2 match resolution + S10 scores |
| `map_results` | map_order, map_id, score_a/score_b, winner_team, vod_url, **vod_start_seconds**, source='cv', confidence | S6 map identity + S10 scores + segment offsets |
| `hero_stints` | match/map/team/side/slot, hero_id, start_offset, end_offset, confidence, ingest_id, evidence | S9 `ingest_map.py` |
| `hero_swaps` | side, slot, from_hero, to_hero, offset, confidence, status (confirmed/rejected), reason, evidence_before, evidence_after | S9 temporal consensus |
| `slot_observations` | every sampled read: hero, score, runner-up, margin, frame path | S9 |
| `map_rounds` | round index, start/end, side state | S9 emblem segmentation |
| `hero_bans` | team, hero, role, ban_order, source='cv', confidence, evidence_path | S10 `detect_bans.py` |
| `ingest_runs` | window, layout, calibration version/status, frame + crop counts, report path | S9 |
| `ingest_findings` | every unresolved identity/ambiguity, with its reason | S1–S10 |
| `teams` | any newly-seen team: name, code, region, aliases, source_authority, identity_verified_at, needs_review | S2/S6 + team registry rules |
| `players` / `match_rosters` | handle, role, team, per-map slot linkage (candidates flagged, never auto-created) | S6 `player_identify.py` |
| `map_veto_events` | pick/ban/decider order where a veto screen is readable | S10 (best-effort) |
| `map_segments` | window, review status, map identity, teams, sides, layout, extraction path | S5–S7 |
| `jobs` / `job_attempts` / `locks` | full lifecycle, every attempt, every error code, worker identity | spine |
| `manual_source_approvals` *(new)* | video id, approver, timestamp, reason | S1 |
| `publication_runs` | db_hash, export_hash, branch, source_commit, state | S14 |
| `quota_usage` | YouTube units spent per call | S1 |

### 4.2 Derived into the public payload
Comps per team/map/round; comp frequency + win rate; hero pick rate, win rate,
and swap activity, sliced by map and mode; team hero pools, map records, and
calibration provenance; swap ledger with rejected noise; capture-run provenance;
calendar/tournament/bracket placement; VOD deep-links per map.

### 4.3 Wanted but explicitly **out of prototype scope** (named, not silently dropped)
Per-round control percentages · ultimate economy and fight-level timing ·
player performance stats (elims/damage/healing) · patch-version attribution ·
caster credits · replay codes · in-map objective timeline · non-YouTube
platforms (bilibili/Twitch) · live (mid-stream) ingestion.

---

## PART 5 — NEW CODE INVENTORY

**New modules** (all stdlib/existing-deps, all offline-testable with injected
transports and fake OCR readers, matching every existing suite's pattern):

| File | Purpose | Est. |
|---|---|---|
| `automation/link_intake.py` | S0/S2 URL normalization, dedupe, job creation, series assembly | ~260 |
| `automation/title_parse.py` | conservative title/description grammar → event/team candidates | ~180 |
| `automation/layout_resolver.py` | S4 fingerprint match + autocalibration driver | ~300 |
| `marker_harvest.py` | S4 reject-marker + round-emblem proposals | ~200 |
| `map_identify.py` | S6 map-name OCR + consensus vs `game_maps` | ~220 |
| `player_identify.py` | S6 nameplate OCR → roster matching | ~220 |
| `template_bootstrap.py` | S8 icon-seeded cluster labeling + variant emission | ~340 |
| `score_read.py` | S10 round/map/series score OCR + consensus | ~260 |

**Changed:** `automation/cli.py` (+6 commands), `automation/worker.py`
(full-VOD download, proxy generation, duration-sized disk preflight, manual-approval
wiring), `automation/ops.py` (link-job dispatch), `automation/segmentation.py`
(carry S6 proposals onto candidates), `automation/schema.sql` (+`manual_source_approvals`,
+ proposal/confidence columns on `map_segments`), `export_data.py` (series +
player-slot surfacing), `intake.html` + `assets/js/public/page-intake.js` (new
read-only panel), `docs/AUTOMATION.md` + `HANDOFF.md`.

**New test suites** (mirroring the existing offline discipline):
`test_link_intake.py`, `test_title_parse.py`, `test_layout_resolver.py`,
`test_map_identify.py`, `test_player_identify.py`, `test_template_bootstrap.py`,
`test_score_read.py`, plus extensions to the worker/segmentation/detection-runner
/publish/state-machine suites. Target ≈120 new checks, zero network, zero keys.

---

## PART 6 — MILESTONES AND ACCEPTANCE GATES

| M | Deliverable | Accepted when |
|---|---|---|
| **M0** | Environment truth | A machine with real YouTube egress + ffmpeg/yt-dlp/opencv passes `worker-doctor` clean. (This sandbox cannot — policy 403 on `www.youtube.com`. The self-hosted Windows worker in `docs/WINDOWS_WORKFLOW.md` is the intended host.) |
| **M1** | Intake + authorization (S0–S2) | `ingest-link --url` creates one idempotent job from a bare URL; a registry channel auto-authorizes, a non-registry one is *held* until `approve-source --confirm`; re-pasting the same link creates nothing new; offline tests green |
| **M2** | Acquisition (S3) | A full real VOD downloads once at 720p with complete metadata + sha256, a 360p proxy is generated, disk preflight refuses an oversized fetch *before* the first byte, and a killed download resumes cleanly |
| **M3** | Layout + segmentation (S4–S5, S7) | An unseen broadcast package autocalibrates to a reviewable sheet or refuses with reasons; a known package matches by fingerprint with no calibration; a real broadcast day produces map candidates with no desk/interview window promoted |
| **M4** | Identity (S6) | Map name, both teams, sides, and map order are proposed automatically on a real VOD and agree with a human's reading; every disagreement surfaces as `needs-review`, never as a silent overwrite |
| **M5** | Templates + detection (S8–S9, S11) | Every map in the VOD produces comps and swaps at Nepal-milestone quality; template coverage for the VOD's heroes is reported as a number; heroes without templates read `UNKNOWN` rather than a guess |
| **M6** | Facts, export, publish (S10, S12–S14) | Bans, map scores, map winners and series score are read where evidence exists; `export_data.py --public` regenerates; `process-approved-job --publish` opens a green PR; the match renders on the calendar, both team pages, every involved hero page, `comps.html`, `swaps.html`, `stats.html`, `maps.html` — **with zero hand-edited DB rows** |

**Prototype-complete demo:** paste one link → run `worker-run` between four
confirmations → a live public match page with every map's comps, swaps, bans and
result, each click-through to its own evidence crop.

---

## PART 7 — RISKS, AND WHAT THIS PLAN REFUSES TO FAKE

1. **Template coverage is the honest ceiling.** With today's library, an
   arbitrary VOD yields comps only for the ~17–20 heroes covered; the rest read
   `UNKNOWN`. S8 makes coverage compound with each processed VOD instead of
   requiring a manual labeling session per broadcast — but the first few real
   VODs will legitimately show partial comps. That will be reported as a
   coverage number, never hidden by a nearest-neighbour guess.
2. **OCR is an optional dependency.** `ocr_hud.py` supports easyocr/tesseract/paddle
   and none. Every OCR-derived field (map, teams, players, bans, scores) must
   degrade to `needs-review` when no engine is installed — never to a guess.
   Tests keep using the injected fake reader.
3. **Layout drift.** A mid-season overlay change invalidates a fingerprint;
   S4 handles it as "no match → recalibrate → human confirms," which is a
   detour, not a failure.
4. **Score/winner OCR is unproven** in this codebase. It ships behind consensus
   and falls back to operator input; the current honest limitation narrows
   rather than disappears.
5. **VOD scale.** A full broadcast day is 6–8 h (~4–6 GB at 720p). One fetch,
   local proxy, per-segment cuts, duration-sized preflight, gitignored media.
6. **YouTube quota + throttling.** Metadata is 1 unit per link; downloads are
   yt-dlp's problem and the existing stall guards (`FAST_STALL_TIMEOUT`,
   fallback formats) already handle it.
7. **Provisional matches must never leak.** A link-created match stays out of
   the public export until it is evidenced — the existing export gate and
   `test_undiscovered_match_not_fabricated` are the guardrails, and stay.
8. **This sandbox cannot finish M2+.** Any claim that a real VOD was processed
   must come from the worker machine, with the report paths to prove it.
