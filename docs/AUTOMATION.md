# Automation foundation (Roadmap Phase A + discovery scaffolding)

This document covers the automation layer added under `pipeline/automation/`.
It implements the **foundation** of the *OWCS Comp Tracker — Complete Automation
Roadmap*: the persistent job/state spine plus the discovery-side registries and
the rolling coverage report. It does **not** record video or download VODs —
that belongs to the self-hosted worker described in the roadmap's later passes.
This layer is what makes such work trackable, resumable, idempotent and safe to
run twice.

Everything here is **stdlib-only** (sqlite3 + a tiny dependency-free YAML
parser), so it runs in exactly the offline environment CI and the site build
already use — no new dependencies, no secrets.

## The calendar system (2026-07-29)

The calendar has two independent layers, and they are deliberately kept
apart because they have different provenance:

| layer | source | reaches the site via |
|---|---|---|
| **Official events** — regional stage windows, major events, their broadcast destinations | `config/owcs_calendar.json` (committed seed) | `export_data.py --public` → `calendarEvents` |
| **Tracked matches** — real rows with teams, scores, comps | the content DB | `export_data.py --public` → `matches` |

`sync-calendar` also loads the events into the automation DB's
`source_events` ledger, where reconciliation (B4) compares them against
FACEIT match facts. That ledger is an *operator* surface; the public site
reads the committed config directly so a public build never needs the
operator's private job database.

**Every event carries `verified` unchanged.** The committed seed is
entirely `verified: false` (the official schedule site was unreachable when
it was written), and `calendar.html` says so on every band — placeholder
dates are never presented as confirmed. To verify a window, edit
`config/owcs_calendar.json`, set `verified: true` against an official
source, and re-export.

**Time honesty.** Most matches carry only a DATE (`matches.date`), not a
start instant (`matches.scheduled_at` is NULL for every current row). The
exporter derives midnight so the match can be placed on a grid, and sets
`timeKnown: false`; the calendar renders "time TBA" rather than a
fabricated `00:00`. Populate `scheduled_at` and `timeKnown` becomes true
automatically.

```bash
python pipeline/automation/cli.py sync-calendar   # events -> operator ledger
python pipeline/export_data.py --public           # events -> public site
```

## YouTube download authentication + the fallback ladder (2026-07-29)

YouTube refuses media URLs for several unrelated reasons that need
different fixes. `pipeline/ytdlp_opts.py` owns the policy; the downloader
walks a **bounded six-rung ladder**, each rung tried at most once:

| # | rung | what it changes | needs config? |
|---|---|---|---|
| 1 | `normal` | the configured baseline | no |
| 2 | `refresh-signed-url` | `--no-cache-dir`: fresh player extraction, fresh signed URL | no |
| 3 | `force-ipv4` | `--force-ipv4` (YouTube 403s some IPv6 ranges) | no |
| 4 | `browser-cookies` | `--cookies-from-browser` | **yes** |
| 5 | `browser-cookies+impersonate` | + `--impersonate` (needs `curl_cffi`) | **yes** |
| 6 | `alternate-format` | plainer ≤720p progressive stream; marked a quality downgrade | no |

**Browser-cookie access is off by default.** Rungs 4 and 5 stay inert —
and are recorded as explicit skips — until an operator opts in:

```powershell
# PowerShell, current session only (nothing is written to disk):
$env:OWCS_YTDLP_COOKIES_FROM_BROWSER = "chrome"   # or edge / firefox
$env:OWCS_YTDLP_BROWSER_PROFILE      = "Default"  # optional
$env:OWCS_YTDLP_FORCE_IPV4           = "1"        # optional
$env:OWCS_YTDLP_IMPERSONATE          = "chrome"   # optional, needs curl_cffi
$env:OWCS_YTDLP_EXTRA_ARGS           = "--sleep-requests 2"   # allowlisted flags only
```

This project **never creates a cookies.txt** and never copies, prints or
commits cookies: yt-dlp reads them from the browser at request time. Cookie
sources, profile paths, signed googlevideo URLs and token-shaped values are
redacted from every log line, job payload, export and API response.

Check the resolved configuration any time — it never prints a value:

```powershell
python pipeline\automation\cli.py download-status
python pipeline\automation\cli.py worker-doctor
```

**Prove bytes actually download before committing to a multi-hour VOD.**
Metadata extraction succeeding does not mean the media URL will serve
anything (that is exactly the 403 this project hit):

```powershell
python pipeline\automation\cli.py media-probe --url "<youtube-url>"
```

It pulls a few real seconds through the same signed-URL path, validates it
with ffprobe, and reports which rung worked — the full download then starts
there. `worker-run`/`autopilot` run this probe automatically.

**Detection assets are checked BEFORE the download.** A layout with no
hero templates, or a declared-but-absent / placeholder HUD anchor, can
never produce a composition, so the download is refused with the exact
harvest command instead of failing hours later. Because templates can only
be cut from the broadcast's own frames, `--for-harvest` is the one explicit
opt-out (recorded on the job, never the default).

```powershell
python pipeline\detection_assets.py            # per-layout verdict + remedies
```

## The auto match finder (2026-07-29)

Finding the next broadcast no longer requires opening YouTube. The match
finder scans every VERIFIED broadcast channel on two **permanently free,
no-key** sources and keeps an idempotent ledger of every OWCS broadcast it
has ever seen:

* **The channel's public RSS feed**
  (`https://www.youtube.com/feeds/videos.xml?channel_id=…`) — stdlib HTTP +
  XML, no API key, no quota, free forever. Carries the ~15 newest videos.
* **The channel's `/streams` tab via `yt-dlp --flat-playlist`** — no key,
  no login, reaches the full livestream archive (bounded by `--limit`) and
  carries the duration + live status the RSS feed lacks.

Every video is scored with the SAME tuned broadcast-likeness gate the
intake path trusts (`broadcast_matching.broadcast_likeness`) — a
promo/guide/Shorts upload shows up labeled `unlikely` *with its reasons*,
never silently dropped, never silently ingested.

```powershell
python pipeline/automation/cli.py find-matches              # scan + save
python pipeline/automation/cli.py find-matches --dry-run    # scan, write nothing
python pipeline/automation/cli.py find-matches --queue-likely  # agentic mode:
#   every likely broadcast not yet in the pipeline is registered through the
#   SAME ingest_link gate as a hand-pasted URL. Metadata only — nothing is
#   downloaded and nothing is approved; source authorization rules unchanged.
```

Artifacts: `data/match_finder.json` (the accumulating ledger — a broadcast
that leaves the feed window is kept) and `assets/data/matchfinder.v1.json`
(the static snapshot the portal renders on GitHub Pages). In the control
room the portal front page (`index.html`) shows the live report
(`GET /api/matchfinder`) with an **Ingest** button per candidate that feeds
the exact paste-link flow, and a **Scan for new matches** button that runs
this command (`POST /api/action {action: "find-matches"}`).

Only channels from `config/broadcast_channels.json` that are enabled,
verified and carry a confirmed `channelId` are scanned — the finder never
guesses a handle. `--channel-url <url>` scans one extra channel ad hoc.

## Match-day runbook — the free-agent loop (2026-07-29)

The 12-step checklist below ("Real-host validation") is still the
authoritative stage-by-stage reference, but on a match day you no longer
drive it by hand. The steps that are *automatic* are now chained by the
autopilot, and the intake page can drive the whole thing from a browser.

**One command per finished broadcast:**

```powershell
python pipeline\automation\cli.py convert-link --url "<youtube-url>" --requested-by "<you>"
```

That ingests the link and runs every automatic stage in a row — metadata +
registry authorization, full-VOD download + 360p proxy, layout resolution,
segmentation, and (new) segment-clip extraction — stopping honestly at the
FIRST gate that belongs to a human, with the exact next command printed.
`assets/data/intake.v1.json` is refreshed automatically, so `intake.html`
always shows the live stage.

**Or from the browser:** run `python pipeline\serve.py`, open
`http://localhost:8000/intake.html`, paste the link, watch the live log.
The page POSTs `/api/intake/link`, which launches the same `convert-link`
command locally (static hosting keeps the read-only behavior and only
prints the command for you to copy).

**The human gates, unchanged and non-negotiable** — the autopilot stops at
each and names it:

1. `approve-source --confirm` — a link not on a verified official channel.
2. `approve-layout --confirm` — a freshly-calibrated layout (review the sheet).
3. Segment identity review — approve in `intake.html`/CLI as before, **or**
   re-run with `--auto-accept`, which accepts machine proposals through the
   SAME `accept-proposed` gate (a blocking review task or an UNKNOWN field
   still refuses and waits for you; recorded with your `--accepted-by` name).
4. Detection review — hero comps NEVER reach production automatically;
   review `reports/ingest/<id>/report.html`, then `detect-job <job> --write`.
5. Publication — `process-approved-job --job <job> --publish` stays
   supervised.

**Resuming after a gate:** once you've cleared a gate, one command re-enters
the loop and runs to the next gate:

```powershell
python pipeline\automation\cli.py autopilot --job <job-key>          # or --url "<same link>"
python pipeline\automation\cli.py autopilot --job <job-key> --auto-accept --accepted-by "<you>"
```

`link-status --job <job-key>` still prints stage/blockers/next command at
any moment, and every autopilot run ends with the same summary.

## Real-host validation — the exact Windows commands (2026-07-27)

Everything in the URL-only pipeline is implemented and offline-tested, but a
sandbox that denies `www.youtube.com` cannot prove a real download. Run these
on the Windows worker, in order. Each one stops on its own gate rather than
guessing, so it is safe to run them one at a time and read the output.

```powershell
# 0. One-time: confirm the machine can do the work at all.
python pipeline\automation\cli.py worker-doctor
#    Needs: python, opencv-python-headless, numpy, ffmpeg, ffprobe, yt-dlp,
#    free disk, a writable worker cache + reports dir. Fix anything MISSING
#    before continuing — every later step depends on it.

# 1. Paste the broadcast link. This is the whole intake.
$env:YOUTUBE_API_KEY = "<your key>"   # needed to read the source's channel
python pipeline\automation\cli.py ingest-link --url "<youtube-url>" --requested-by "<your name>"
#    -> prints the video id, canonical URL, deterministic job key, job state,
#       whether the source was auto-approved (verified official channel) or
#       needs you, any broadcast-likeness warning, and the next command.
#    Re-run it with the same link (any spelling) to prove idempotency: it must
#    say "duplicate link - attached to the existing job", never create a second.

# 2. Only if the source was NOT auto-approved (not a registry channel, or
#    metadata was unavailable). This is audited: your name is recorded.
python pipeline\automation\cli.py approve-source --job <job-key> --approved-by "<your name>" --confirm

# 3. Download the whole VOD once + build the 360p scan proxy.
#    Refuses BEFORE downloading if the estimated footprint will not fit.
python pipeline\automation\cli.py worker-run --max-jobs 1
#    A killed download RESUMES: interrupt this with Ctrl-C mid-download, then
#    re-run it and confirm the log says "RESUMING partial download".

# 4. Resolve the broadcast layout from evidence.
python pipeline\automation\cli.py resolve-layout --job <job-key>
#    Either reuses a committed layout (prints per-candidate scores) or
#    calibrates a NEW one and stops for approval. If it calibrated one, review
#    reports\layout\<video-id>\calibration\sheet.png, then:
python pipeline\automation\cli.py approve-layout --job <job-key> --approved-by "<your name>" --confirm

# 5. Propose every gameplay/map window (reads the proxy) and review it.
python pipeline\automation\cli.py worker-run --max-jobs 1
python pipeline\automation\cli.py segment-list --video-id <video-id>

# 6. Check hero-template coverage for this broadcast package BEFORE detecting.
#    Uncovered heroes will read UNKNOWN — this tells you the real ceiling.
python pipeline\template_bootstrap.py --layout layouts\<layout-id>.json

# 7. Propose map / mode / teams / sides / order / players, with evidence.
python pipeline\automation\cli.py propose-identity --job <job-key>

# 8. Render the operator review panel, then open intake.html in a browser.
python pipeline\automation\cli.py intake-export --save

# 9. Approve each real map segment. Either accept the proposal wholesale:
python pipeline\automation\cli.py accept-proposed --segment <segment-id>
#    ...or correct it by hand, or fix the window first:
python pipeline\automation\cli.py segment-split <segment-id> --at <seconds>
python pipeline\automation\cli.py segment-merge <segment-id> <other-id>
python pipeline\automation\cli.py segment-reject <segment-id> --reason "desk segment"

# 10. Detect compositions + swaps. DRY RUN FIRST — writes nothing.
python pipeline\automation\cli.py detect-job <job-key>
#     Read reports\ingest\<ingest-id>\report.html and review.html, then commit:
python pipeline\automation\cli.py detect-job <job-key> --write

# 11. Promote, export, validate, publish. Dry run first (zero git writes).
python pipeline\automation\cli.py process-approved-job --job <job-key>
python pipeline\automation\cli.py process-approved-job --job <job-key> --publish
#     Then the existing flow, unchanged: open the PR, wait for CI, merge,
#     confirm the Pages deploy and the live match page.

# 12. Repeat steps 1-11 with a SECOND broadcast to prove layout reuse: step 4
#     should now REUSE the layout automatically instead of calibrating.
```

**What to capture as evidence for each step:** the command's stdout, the job
payload (`show-job <job-key>`), and the generated paths it names —
`reports\layout\<video-id>\`, `reports\identity\<video-id>\`,
`reports\ingest\<ingest-id>\report.html` + `review.html` + `evidence\`,
`assets\data\intake.v1.json`, `assets\data\public_data.v1.js`.

**If a step refuses, that is the system working.** Every refusal names its
reason and the recorded blocking condition; `link-status --job <job-key>`
always prints the current stage, every blocker, and the exact next command.


## What's implemented

| Roadmap item | Where | Status |
|---|---|---|
| A1 automation database (persistent, not workflow artifacts) | `pipeline/automation/schema.sql` | ✅ |
| A1 job store / state machine / locks / models | `job_store.py`, `state_machine.py`, `locks.py`, `models.py` | ✅ |
| A2 global idempotency (deterministic job keys) | `models.py` (`match_key`, `record_key`, …) + `jobs.job_key` PK | ✅ |
| A3 distributed locking (leases + heartbeats + crash steal) | `locks.py` | ✅ |
| A4 operator config file | `config/automation.yml` + `config.py` | ✅ |
| B1 curated FACEIT competition registry | `config/faceit_competitions.json` | ✅ (placeholder IDs; fill + enable) |
| C1 verified broadcast-channel registry | `config/broadcast_channels.json` | ✅ (placeholder IDs; see the "Phase C" section below for the full implementation) |
| D4 rolling 14-day completeness report | `coverage.py` + `cli.py coverage` | ✅ |
| State-retention on failure (dead-letter, J1/J2) | `job_store.record_attempt` → `RETRY_SCHEDULED` / `FAILED_PERMANENT` | ✅ |

The state machine (the roadmap's `DISCOVERED … PUBLISHED / FAILED / IGNORED`
graph) is enforced on every transition, so a bug can never skip review and jump
straight to `PUBLISHED`, and **no record is ever deleted on failure** — a failed
job keeps its error code, message, attempt count, timestamps, worker id, source
URL and diagnostic path, then moves to `FAILED_PERMANENT` once its per-kind
retry ceiling is hit (still visible, still actionable).

## Phase B — automatic calendar ingestion (implemented)

The production FACEIT + official-calendar discovery pipeline is built on the
Phase A spine. It is stdlib-only and fully offline-testable (injectable HTTP
transport + fixtures).

| Roadmap item | Where | Status |
|---|---|---|
| B1 curated FACEIT competition registry (no broad search) | `config/faceit_competitions.json` + `config.load_competitions()` | ✅ (placeholder ids; enable to go live) |
| B2 poll FACEIT (championships → matches → teams/players/status/result) | `faceit_api.py` + `discovery.sync_faceit` | ✅ |
| B3 official OWCS calendar adapter | `config/owcs_calendar.json` + `owcs_calendar.py` | ✅ |
| B4 source reconciliation (never silently overwrites) | `reconcile.py` | ✅ |
| B5 generate the public calendar | `export_data.py` (discovered-window matches) → `public_data.v1.js` → `calendar.html` | ✅ |
| Rolling 14-day window + future horizon | `discovery.in_window` (config `lookback_days` / `schedule_horizon_days`) | ✅ |
| Delayed / rescheduled / cancelled / forfeited / completed / duplicate handling | `faceit_api.map_status` + `discovery.upsert_match` | ✅ |
| Idempotent upsert with stable public ids (`faceit-<matchId>`) | `discovery.upsert_match` (alias-safe team resolution) | ✅ |
| Deterministic discovery + broadcast-discovery jobs | `discovery` → `jobs` / `scheduled_matches` in `data/automation.sqlite` | ✅ |
| Dry-run (fetch + reconcile, zero writes) | `--dry-run` on every sync command | ✅ |
| Response caching + raw-metadata retention | `FaceitClient(cache_dir=…)`, `raw` kept on every normalized match | ✅ |
| Never writes/infers compositions | normalized shape has no comp field; discovery never touches comp tables | ✅ |
| API failures → retry jobs | `discovery` enqueues a `KIND_DISCOVERY` retry via `record_attempt` (backoff/dead-letter) | ✅ |

Discovery status distinctions on the public site: `upcoming`, `live`,
`completed`, `forfeit`, `cancelled` (match status) and `needs-source` /
`queued` / `needs-review` (capture status), mapped from the precise FACEIT
lifecycle in `export_data._public_match_status` / `_public_capture_status`.

### Sync CLI

```bash
python pipeline/automation/cli.py sync-faceit   --dry-run
python pipeline/automation/cli.py sync-calendar --dry-run
python pipeline/automation/cli.py sync-all      --lookback-days 14 [--export]
python pipeline/automation/cli.py coverage
# offline demo against local fixtures (no key, no network):
python pipeline/automation/cli.py sync-all --dry-run --fixture-dir pipeline/fixtures/automation
```

`--export` regenerates `public_data.v1.js` after a live sync so `calendar.html`
updates. Dry-run performs all API retrieval + reconciliation but writes nothing.

### Hourly workflow

`.github/workflows/discovery.yml` runs every hour. It is **safe by default**:
with the registries disabled or no `FACEIT_API_KEY` secret it only runs a
`--dry-run` health check (writes nothing, opens nothing). Once real ids are
enabled AND the secret is set, it runs a live sync, validates the result
(`check_packaging.py` + calendar/public-site tests) and opens a data-update PR
**only when the validated calendar data actually changes**.

### Required secrets

| Secret | Where to set it | Used by |
|---|---|---|
| `FACEIT_API_KEY` | GitHub → repo **Settings → Secrets and variables → Actions**; locally via an untracked `.env` / shell env | `faceit_api.urllib_transport` (live FACEIT Data API calls) |

No key is committed; `.env`, `credentials*.json`, `secrets*.json` and
`data/raw/` (cached API responses) are gitignored. `data/automation.sqlite`
(the runtime job queue) is gitignored too.

### Registry state (verified 2026-07-24 against the live FACEIT API)

- `config/faceit_competitions.json` — **2 enabled, API-verified** competitions:
  the OWCS 2026 NA + EMEA Open Qualifiers (Tier 2). All other regions/stages
  stay `enabled: false` with `championshipId: null` because no official FACEIT
  championship exists for them (see `docs/FACEIT-REGISTRY.md`). IDs are never
  guessed. Verify enabled entries any time with `cli.py verify-registry` (or
  the `discovery.yml` `mode=verify` dispatch).
- `config/broadcast_channels.json` — **`ow_esports_global` is API-verified**
  (live `mode=verify-channels` dispatch, 2026-07-24): channelId
  `UCiAInBL9kUzz1XRxk66v-gw`, uploads playlist `UUiAInBL9kUzz1XRxk66v-gw`,
  1 quota unit spent, now the only `enabled: true` entry. Regional channels
  (Korea/Japan/Pacific) stay `channelId: null` / disabled — no human-sourced
  official URL yet, never guessed. China stays disabled/bilibili, explicitly
  out of the YouTube client's scope. Full detail + procedure in
  `docs/YOUTUBE-DISCOVERY.md`.
- `config/owcs_calendar.json` — event dates remain `verified: false`; the
  official Overwatch Esports schedule site is not reachable from the config
  environment, so no date could be confirmed against an official source.

The full verification procedure, verified organizer/championship ids, coverage
finding, and the real dry-run output are documented in **`docs/FACEIT-REGISTRY.md`**.

## Phase C — official OWCS schedule + YouTube broadcast discovery (implemented)

Read-only discovery of official broadcasts, built on the same Phase A/B spine.
Never downloads video, never records, never writes hero compositions, and
never enables unattended production linking — every candidate this phase
produces still requires a human or a later phase (E/F/G/I) to confirm.

| Roadmap item | Where | Status |
|---|---|---|
| C1 verified broadcast-channel registry | `config/broadcast_channels.json` (sourceUrl/ownershipEvidence/uploadsPlaylistId/verificationMethod/verifiedDate/verifiedStatus/disabledReason/preferredLayout) + `cli.py verify-channels` | ✅ **`ow_esports_global` API-verified 2026-07-24** (channelId `UCiAInBL9kUzz1XRxk66v-gw`, 1 quota unit); see `docs/YOUTUBE-DISCOVERY.md` |
| C2 YouTube Data API client (dependency-light, injectable transport) | `pipeline/automation/youtube_api.py` | ✅ |
| C2 quota-unit accounting + exhaustion detection | `youtube_api.QUOTA_COSTS`, `YouTubeQuotaExceeded`, `quota_usage` table | ✅ |
| C3 broadcast discovery (upcoming/live/completed/archive/VOD, cheapest-path-first) | `broadcast_discovery.py` (`discover_channel_videos`, `sync_broadcasts`) | ✅ |
| C3 rolling 14-day window + future horizon | `broadcast_discovery.in_window` | ✅ |
| C4 explainable scoring + HIGH/MEDIUM/LOW bands | `broadcast_matching.py` (`score_candidate`, `confidence_band`) | ✅ — see `docs/YOUTUBE-DISCOVERY.md` for the full weight table |
| C4 one-video-to-many-matches / many-videos-to-one-match | `broadcast_candidates` (match_id, platform, video_id) unique key | ✅ |
| C5 deterministic job keys + idempotency | `models.broadcast_discovery_key`, `models.broadcast_key`, `models.broadcast_match_link_key` | ✅ |
| C6 explicit coverage states (nothing disappears silently) | `coverage.build_broadcast_coverage` / `derive_coverage_label` | ✅ |
| C7 official-calendar adapter improvements (season/format/sourceUrl/retrievedAt/sourceHash/verificationStatus, live Next.js extraction) | `owcs_calendar.py` (`http_fetcher`, `extract_events_from_next_data`) | ✅ |

### Phase C real dry-run fix (2026-07-24)

The first real `broadcast-dryrun` Actions run found 7 in-window videos but
`videos_scored` came back **0** — this section documents the root causes and
the fix, since the bug wasn't in scoring itself but in what fed it.

**Root causes (two, compounding):**
1. `sync_broadcasts` only ever collected `discover_channel_videos`'s
   in-window video list inside its `not dry_run` write branch. A dry-run —
   which by definition writes nothing to `broadcast_videos` — therefore
   discarded every discovered video before matching ever ran.
2. `cli.py`'s `_run_broadcast_discovery` called `match_broadcasts(store, …)`
   with no `videos=` argument, so matching fell back to reading the (empty,
   because of #1) `broadcast_videos` table — and separately, matching only
   ever built targets from `scheduled_matches`, which was also empty because
   `broadcast-dryrun` never runs `sync_faceit`/`sync_calendar`.

**Fix:**
- `sync_broadcasts` now always populates `summary["videos"]` with every
  in-window video, dry-run or not (writes remain gated on `not dry_run` —
  dry-run purity is unaffected).
- `cli.py` passes that list straight into `match_broadcasts(videos=…)`
  instead of relying on the DB table.
- `match_broadcasts` now pools matching targets from **three** sources — a
  video is never required to match a specific FACEIT match:
  1. `scheduled_matches` (verified FACEIT matches)
  2. `source_events` (automation DB — mirrors the calendar once
     `sync_calendar` has run)
  3. `owcs_calendar.load_events()` (the **committed seed file** — always
     available, independent of any prior sync; this is what makes matching
     work against a completely empty automation DB)
- Every video now gets exactly one top-level classification (see
  `broadcast_matching.ALL_CLASSIFICATIONS`) in addition to its per-target
  candidate rows, so `videosScored == sum(classifications.values())`
  always holds — nothing can silently vanish from the report again:
  `high`, `needs-review`, `event-level-candidate`, `unmatched-official-video`,
  `unrelated-official-upload`, `unsupported-event`, `outside-configured-coverage`.

**Efficiency (the "videos seen: 1000" complaint):** an uploads playlist is
newest-first, so `youtube_api.list_playlist_items` now stops paginating once
a fetched page's oldest item predates `lookback_days + 2` days (pass
`--full-history` to disable). `YouTubeClient` also gained a TTL-based
response cache (default 1h) that skips the network call AND the quota spend
entirely on a repeated identical request within the TTL — `cli.py` enables
this cache even in dry-run (caching a read is not a production write).

See `pipeline/automation/broadcast_matching.py`'s module docstring and
`test_automation_broadcast_matching.py` / `test_automation_broadcast_discovery.py`
/ `test_automation_youtube_api.py` for the full pinned behavior.

### Phase C broadcast-classification refinement (2026-07-24)

The corrected dry-run above found 7 real in-window videos and scored all 7
— but classified **all seven as `event-level-candidate`**, including six
that were plainly short-form promotional/instructional uploads (a 6-second
lootbox promo; several 1.5–2.5 minute tips/perk/patch videos). Root cause:
`score_candidate`'s flat **+40 official-channel** bonus plus a region-match
bonus outweighed the lone **−15 short-duration** penalty, so almost any
upload from the verified channel cleared `MEDIUM_THRESHOLD`.

**Fix:** `broadcast_matching.broadcast_likeness(video)` — a new pre-filter
stage that runs BEFORE any target/event scoring, using several independent
generic signals (livestream metadata, duration, tournament/broadcast
terminology, match/series formatting vs. instructional keywords — never a
single duration cutoff, never a hard-coded title). A video scored
`"unlikely"` is classified `unrelated-official-upload` immediately and is
never scored against any target at all (zero candidate pairs spent on it);
a `"likely"` video proceeds through matching exactly as before. See
`docs/YOUTUBE-DISCOVERY.md`'s "Broadcast-likeness pre-filter" section for
the full signal table and the worked example against the real 7 videos.

Also fixed, from the same real run: the report conflated candidate PAIR
counts with distinct VIDEO counts (e.g. printing "21" when that was 7 videos
× 3 targets, not 21 videos). `match_broadcasts` now returns
`totalCandidatePairsEvaluated` (pairs) separately from
`distinctVideos: {high, mediumOrReview, rejectedOrUnrelated}` (videos), and
every per-video result carries `reasons` (likeness signals + the
best-scoring target's signals, always populated) and `targetsFiltered`
(which loaded targets were excluded for THIS video and why — outside its
time window, or never evaluated because it failed the likeness gate). The
CLI report labels every number so a pair count can never be misread as a
video count.

### CLI

```bash
python pipeline/automation/cli.py verify-channels [--json]
python pipeline/automation/cli.py calendar-dryrun --lookback-days 30
python pipeline/automation/cli.py broadcast-dryrun --lookback-days 30 [--allow-search-fallback]
python pipeline/automation/cli.py discover-broadcasts [--dry-run] --lookback-days 30
python pipeline/automation/cli.py coverage --window 30   # now includes Phase C6 broadcast coverage
```

### Required secrets (updated)

| Secret | Where to set it | Used by |
|---|---|---|
| `FACEIT_API_KEY` | GitHub → repo **Settings → Secrets and variables → Actions** | `faceit_api.urllib_transport` |
| `YOUTUBE_API_KEY` | GitHub → repo **Settings → Secrets and variables → Actions** | `youtube_api.urllib_transport` (channel verification + broadcast discovery) |

Neither key is committed, logged, or ever appears in a cache filename or
exception message (`youtube_api._sanitize_url` strips it from every URL
before it's used for anything) — see `docs/YOUTUBE-DISCOVERY.md`.

### Workflow modes

`.github/workflows/discovery.yml` gained four read-only `workflow_dispatch`
modes: `verify-channels`, `calendar-dryrun`, `broadcast-dryrun`, `coverage`.
Each is safe by default (no key -> `::error::` and exit, never a silent
no-op that looks successful), writes nothing to the repo, and uploads its
sanitized stdout as a workflow artifact. Full dispatch instructions in
`docs/YOUTUBE-DISCOVERY.md`.

### Tests

`test_automation_youtube_api.py` (client: pagination, quota accounting, error
classification, quota exhaustion, cache determinism, no secret leakage),
`test_automation_broadcast_discovery.py` (C1 channel verification, C3
normalization/window/discovery, idempotent reruns, renamed/delayed
broadcasts, multi-language feeds, API-failure retry jobs),
`test_automation_broadcast_matching.py` (every C4 signal in isolation,
HIGH/MEDIUM/LOW boundaries, linking, one-to-many/many-to-one, unofficial
mirror rejection, the three-target-source matching + classification fix,
full-day multi-match, the accounting invariant), `test_automation_owcs_calendar.py`
(C7 fields, resilient `__NEXT_DATA__` extraction, live-fetch failure modes),
plus extensions to `test_automation_schema.py` (new tables),
`test_automation_config.py` (registry field completeness), and
`test_automation_coverage.py` (C6 label derivation).

Phase D — `test_automation_team_enrichment.py` (facts-only normalization,
COALESCE never blanks a known fact, candidate-source auto-population never
writes a logo, idempotent reruns, one team's API failure never blocks the
rest, dry-run purity, `--team-id` filtering, teams with no `faceit_team_id`
never touched, fixture-transport `/teams/{id}` routing).

## Phase D — team profile enrichment (implemented)

Match discovery only ever wrote a team row the minimum it needs to exist
(name, region, a crude code, `faceit_team_id`). This pass populates the rest
from FACEIT's own `/teams/{id}` resource — the same authority match facts
already come from — bio, website, socials, and roster size.

| Roadmap item | Where | Status |
|---|---|---|
| Team facts from FACEIT (bio/website/socials/roster) — never a search, only teams discovery already resolved an id for | `faceit_api.get_team` / `normalize_team` + `team_enrichment.enrich_teams` | ✅ |
| Never writes a logo directly (candidate-source registry only) | `team_enrichment._add_candidate_source` → `assets/data/team_asset_sources.json` (human still verifies + downloads before `logo_url` is ever set) | ✅ |
| Idempotent (no duplicate candidate-source lines, stable upserts) | `team_enrichment.enrich_teams` | ✅ |
| A thin/partial API response never blanks a known fact | `_upsert_team_facts` (`COALESCE`) | ✅ |
| One team's API failure never blocks the rest | per-team try/except in `enrich_teams` | ✅ |
| Runs live on the hourly schedule (not just a dry-run demo) | `discovery.yml` "Live team enrichment" step, gated on `FACEIT_API_KEY` only (works even with 0 enabled competitions — it enriches teams already known) | ✅ |
| Read-only dry-run mode | `cli.py enrich-teams --dry-run` / workflow `mode=teams-dryrun` | ✅ |
| Surfaced on the public site | `export_data.py` teams export + `page-team.js` (description/website/socials/roster count, guarded — absent until enriched) | ✅ |

```bash
python pipeline/automation/cli.py enrich-teams --dry-run
python pipeline/automation/cli.py enrich-teams --dry-run --team-id ssg --team-id nrg
# offline demo against a local fixture (no key, no network):
python pipeline/automation/cli.py enrich-teams --dry-run --fixture-dir pipeline/fixtures/automation
```

## Phase D2 — canonical team registry + verified logo pipeline (implemented)

Teams must be a first-class public entity as soon as they are discovered,
independent of whether the vision pipeline has captured any of their maps.
The single biggest gap this phase closes: the public export previously only
included teams reachable from a CV-ingested or in-window match (`teams_needed`
in `export_data.py`) — orphaning any team whose only match predates the
rolling window or has no capture yet. It now exports **every** team in the
registry, with an explicit `compositionTrackingPending` flag instead of
silent omission.

| Roadmap item | Where | Status |
|---|---|---|
| Canonical identity fields (aliases/previous names/organization/status/effective dates/source authority/verification timestamps) | `pipeline/schema.sql` teams columns + `pipeline/db.py` migration | ✅ |
| Rename-safe identity (never drops history) | `team_registry.upsert_identity` — old name moves to `previous_names`, never overwritten silently | ✅ |
| Duplicate names across regions never merged | `team_registry.resolve_identity_slug` — region-scoped id when a name collides across regions with no `faceit_team_id` link | ✅ |
| Conflicting source facts → needs_review, never a silent pick | `upsert_identity`'s region-conflict branch sets `needs_review`/`review_reason`, keeps the original value | ✅ |
| Unsigned/mix/academy rosters | `team_registry.looks_unsigned` heuristic → `status='unsigned'` | ✅ |
| Roster provenance | `team_registry.record_roster` — stamps `roster_source`/`roster_verified_at` only when real rows exist | ✅ |
| Per-team coverage ledger (identity/roster/logo/broadcast/capture, explicit blocking issue) | `team_coverage.py` (`build_report`, `format_report`) | ✅ |
| Verified logo pipeline: candidate → downloaded → validated → human-approved → published | `team_assets.py` | ✅ (mechanics fully built + tested; **zero real logos published this pass** — no verified official URL was available to approve, see below) |
| Source-authority ranking (website > social > official OWCS/FACEIT > other) | `team_assets.AUTHORITY_RANK` | ✅ |
| Reject invalid/tiny/broken/duplicate images | `team_assets.validate_candidate` | ✅ |
| Transparency preserved | square/wide variants written as PNG (see limitation below) | ✅ |
| Square/wide + dark-safe/light-safe variants | `team_assets.publish_candidate` (`_square_crop`, `_wide_pad`, `_safe_variant`) | ✅ |
| Restrained accent color extraction | `team_assets.accent_color` (mean of non-transparent pixels) | ✅ |
| Historical logo preservation (never overwritten, moved to `history/`) | `publish_candidate` — only when the incoming hash actually differs | ✅ |
| Human approval is the ONLY non-automatic step | `approve_candidate(..., confirm=True)` — no code path anywhere promotes past `validated` without it | ✅ |
| Never hotlinked, never guessed | candidates are fetched once into gitignored `data/asset_staging/`; only a published, human-approved asset reaches the committed `assets/img/teams/<id>/` tree | ✅ |
| Public export independent of composition capture | `export_data.build_public_payload` — teams list is every row in `teams`, not `teams_needed` | ✅ |
| Team Coverage control-room dashboard | `team-coverage.html` + `assets/js/public/page-team-coverage.js`, reading the small committed `assets/data/team_coverage.v1.json` | ✅ |
| Idempotent CLI/workflow modes | `cli.py team-coverage` / `collect-team-assets` / `approve-team-asset` / `publish-team-assets`; `discovery.yml` `mode=team-coverage` / `mode=team-assets-dryrun` | ✅ |

### Known, documented limitations

- **AVIF is not generated.** No AVIF encoder is available without adding a new project dependency, which is out of scope for this stdlib/existing-deps-only automation layer. `publish_candidate`'s output always sets `"avif": null` rather than fabricating a claim.
- **WebP alpha**: this environment's OpenCV/libwebp binding does not round-trip a WebP's alpha channel (verified — a written RGBA WebP reads back 3-channel). Since transparency is a hard requirement, the square/wide variants are written as PNG; WebP is used only for the dark-safe/light-safe variants, which are always composited onto an opaque backing plate and have no transparency to lose.
- **`comp_snapshots` vs `hero_stints`**: the content DB has two generations of capture tables. `hero_stints` is what the real, current full-map CV pipeline (and the public export's actual comp data) is built from; `comp_snapshots` is an older, separate path some other tools still write to. `compositionTrackingPending` and the coverage ledger's `composition-captured` state both key off `hero_stints` — using `comp_snapshots` here would have reported the wrong teams as "done."

### CLI

```bash
python pipeline/automation/cli.py team-coverage --window 30 [--json] [--save]
python pipeline/automation/cli.py collect-team-assets [--team-id ssg] [--save]
python pipeline/automation/cli.py approve-team-asset --team-id ssg --url <verified-url> --approved-by "<you>" --confirm
python pipeline/automation/cli.py publish-team-assets [--publish]
```

`team-coverage --save` writes `assets/data/team_coverage.v1.json` (also
regenerated automatically as part of `--export`/the hourly live-sync step) —
the same small, non-sensitive JSON `team-coverage.html` reads. `approve-team-asset`
is the one command a human must run deliberately; nothing else in this pass
ever calls it.

### Tests

`test_automation_team_registry.py` (renamed/historical teams, duplicate
names across regions, unsigned rosters, roster-change provenance,
conflicting source facts → needs_review, idempotent reruns, no
hero-composition writes, the same scenarios through the real
`discovery.upsert_match` entry point), `test_automation_team_coverage.py`
(every coverage state's derivation, blocking-issue priority, `hero_stints`
vs `comp_snapshots`), `test_automation_team_assets.py` (authority ranking,
image validation/rejection, approval gate, variant generation, historical
preservation, idempotent republish, FACEIT-candidate promotion),
`test_team_export.py` (teams export before composition capture, roster
reflects the most recent match, needs-review/aliases surface honestly,
GitHub-Pages-safe relative logo paths).

## Phase D2.1 — production team population, verified logos, match export repair

Turns the Phase D2 architecture into real production coverage. Three parts.

### 1. Match export repair

**Root cause.** `export_data.build_public_payload` only ever surfaced a match
through two paths: a completed `ingest_runs` CV run, or
`_discovered_window_matches` (which required `competition_id` OR
`lifecycle_status` to be set, AND the match to fall inside the rolling
discovery window). A match entered before the Phase B automation columns
existed — real, evidenced, but with neither field set — had no path in and
silently vanished from the calendar, match directory, tournament pages, team
history, search and stats. Legacy real matches `m-cr-zeta-krgf` and
`m-cr-zeta-ccuf` hit exactly this gap; the 12 `sample:*`-sourced demo rows
(`m01`–`m12`, from `pipeline/sample_data.json`) correctly do NOT — they are
synthetic fixtures, not real matches, and must stay hidden.

| Roadmap item | Where | Status |
|---|---|---|
| Evidence-based `fixture_kind` classification (production vs synthetic, never guessed) | `pipeline/automation/match_repair.py` (`classify_fixture_kind` — evidence is the row's own `source_ref`/`faceit_match_id` sample-prefix, never match content) | ✅ |
| Evidence-based `lifecycle_status` backfill (only from the match's own pre-existing `status`) | `match_repair.infer_lifecycle_status` (`final→finished`, `live→live`, `upcoming→scheduled`; `unknown` has no safe mapping and is left for a human) | ✅ |
| Idempotent dry-run/write repair, provenance recorded | `match_repair.repair_matches` (+ `lifecycle_source`/`lifecycle_repaired_at` columns) | ✅ |
| Export-gate fix: a concluded (`status='final'`) real match is never excluded by the rolling discovery window | `export_data._discovered_window_matches` (bypasses the window for `status='final'`, still excludes `fixture_kind='synthetic'`) | ✅ |
| Match export coverage report (why every excluded match isn't public) | `pipeline/automation/match_export_coverage.py` — computed directly from `export_data.build_public_payload`, never a second opinion | ✅ |

A bare *unconfirmed upcoming* stub (no evidence at all — no `competition_id`,
no `lifecycle_status`, not concluded) still never appears; only a match with
real evidence (a completed result, or FACEIT/calendar discovery) gets
surfaced. `test_automation_calendar_export.py`'s
`test_undiscovered_match_not_fabricated` pins this down.

```bash
python pipeline/automation/cli.py match-audit [--json]
python pipeline/automation/cli.py match-repair [--write] [--coverage] [--json]
python pipeline/automation/cli.py export-coverage [--json]
```

New `discovery.yml` `workflow_dispatch` modes: `match-audit`,
`match-repair-dryrun`, `export-dryrun` (all read-only). The `sync` path now
always runs `match-repair --write` + regenerates the public export before
the FACEIT/team-enrichment steps — this needs no API key, so a legacy
match's export gap never lingers on a schedule with registries disabled.

Tests: `test_automation_match_repair.py`, `test_match_export_coverage.py`.

### 2. Team population from real activity

Ran live against this repo's actual GitHub Actions secrets (workflow_dispatch
`dryrun`/`broadcast-dryrun`/`calendar-dryrun`/`teams-dryrun` on this phase's
branch): both `FACEIT_API_KEY` and `YOUTUBE_API_KEY` are live and connected —
262 real matches exist in the 2 enabled FACEIT competitions, but **0 fall
inside the current 30-day lookback/horizon window**; the verified YouTube
channel's recent uploads are all short-form social clips, not match VODs (0
high-confidence broadcast links). This is an honest "nothing new today"
result, not a defect — no team was fabricated to produce a different
outcome. The 9 teams already in the registry (from earlier CV/manual
ingestion) remain the complete, evidenced set; they already export
independent of composition capture (Phase D2).

### 3. First verified logo batch

Real primary-source candidates researched (web search → each org's own
domain) for the 9 known teams, then run through the existing, unmodified
`team_assets.py` state machine (`candidate → downloaded → validated →
human-approved → published`) — explicitly user-authorized for this batch to
self-approve candidates from a team's own primary domain that pass
validation and clear a 150px minimum-dimension bar (stricter than the
pipeline's own 48px validity floor).

| Team | Result |
|---|---|
| Crazy Raccoon, ZETA DIVISION, Team Falcons, Spacestation Gaming, Twisted Minds | **Published** — official-website source, ≥150px, all variants generated |
| NRG, Al Qadsiah | Validated, held for a human — official favicon only 72px/50px, below the auto-approve bar |
| Gen.G Esports | No candidate — site's exposed images were a Shopify sponsor banner, not the team's own mark; needs a human to find the real brand asset |
| Quick Esports | No candidate — rebranded to "Vanir Quick" per Liquipedia with an ambiguous current roster; identity needs a human decision before sourcing a mark, per the never-merge-on-name-similarity rule |

Two real bugs surfaced and fixed by actually publishing for the first time
(HANDOFF's Phase D2 pass had never exercised this path end-to-end):

1. **`build_asset_manifest.py` never recognized a published candidate.** It
   read a flat `sourceUrl` field from `team_asset_sources.json` that nothing
   in `team_assets.py` ever wrote — the real shape is
   `assetCandidates[].state == "published"`. Fixed to check for a published
   candidate first.
2. **`team_assets.publish_candidate` wrote Windows-style backslash paths**
   (`os.path.relpath` on Windows) into `variants`/`teams.logo_url` — a
   browser can never resolve `assets\img\teams\x\logo.png` as a URL, so
   every published logo silently failed to load. Fixed with a
   `_site_relpath()` helper that always normalizes to forward slashes;
   regression-tested (`test_variant_paths_are_github_pages_safe`).



The hourly `sync` path's data-update PR (calendar + team facts) now merges
itself once its OWN CI run (`ci.yml`, triggered by the push) goes green —
removing the need for a human to click merge on a validated, auto-generated
data PR. Concretely: `gh pr checks "$BR" --watch --fail-fast` blocks until
the branch's checks report a result, and the PR is squash-merged only on
success; a failing or timed-out check leaves it open for a human, never
force-merged. (GitHub's native `gh pr merge --auto` needs branch protection
with a required status check configured on `main` to have anything to wait
on — this repo has none configured, so `--auto` silently no-ops; polling the
check directly works regardless.) The rest of Phase I (confidence-gated
staged publication once hero-composition processing exists) is still future
work.

## Phase D3 — official hero presentation assets (implemented)

Every one of the 52 public hero ids gets a real, authoritative portrait,
full artwork, and icon — separate from (and never blended with) the
evidence-only broadcast-crop portraits Phase A/B/C above are built from.

| Roadmap item | Where | Status |
|---|---|---|
| Authoritative per-hero source resolution (Blizzard's own hero pages, never a guess) | `pipeline/build_hero_official_assets.py` (`HERO_SLUGS`, `find_splash_url`) | ✅ 52/52 resolved |
| Portrait / full artwork / icon generation | `build_hero_official_assets.py` (`_square_crop` + resize; artwork kept as-fetched) | ✅ |
| Role metadata | content DB `heroes.role` (unchanged; no schema/data migration needed) | ✅ |
| Aliases (curated, never fabricated) | `pipeline/build_asset_manifest.py`'s `HERO_ALIASES` — this repo's own existing hero-id nicknames + web-search-confirmed civilian names only | ✅ |
| Authoritative source + attribution + usage notes | `asset_manifest.json`'s new `heroOfficial` section | ✅ |
| WebP variants; AVIF explicitly null (documented, same limitation as team_assets.py) | `build_hero_official_assets.py` | ✅ |
| Dimensions + hash recorded | `asset_manifest.json`'s `heroOfficial[<id>].{portrait,artwork,icon}` | ✅ |
| Intentional unknown-hero fallback | `assets.js`'s `A.heroOfficialFace`/`applyHeroOfficial` (same monogram placeholder `heroFace()` uses, swapped for the real portrait once resolved — stays a monogram forever for an unresolved hero) | ✅ |
| Validation: all 52 public hero ids resolve correctly | `pipeline/test_hero_official_assets.py` | ✅ 23 checks |

### Root-cause work done to get from "should be easy" to "actually works"

Every hero page on overwatch.blizzard.com carries several GENERIC images
shared across the whole site (an "Outro" banner, "Origin_Story", "Perks",
"Related_Heroes" panels — identical content-ids across different heroes'
pages). The one hero-specific image is found by matching the URL's own
embedded name against the hero's real name (diacritics stripped, alnum-only)
— never a positional/first-match guess. Three real naming quirks surfaced
and were handled explicitly, never patched with a blanket guess:

* **CMS revision suffixes** — Juno's asset is `960_Juno_v2.jpg`, Mei's is
  `960_Mei_02.jpg`. A direct match is tried first, and only falls back to
  stripping a trailing `_v2`/`_02`-style suffix if that fails — so a hero
  whose own real name ends in a number (`Soldier: 76` → `960_Soldier_76.jpg`)
  is never mistaken for a revision-suffixed file of a different name.
* **Diacritics** — `Torbjörn`/`Lúcio` normalize (NFKD-strip) to match
  Blizzard's ASCII filenames (`Torbjorn.jpg`).
* **Pre-release dev codenames** — Wuyang's asset is filed under `Aqua.png`,
  his documented pre-reveal codename (independently corroborated by outside
  reporting at reveal time, not inferred from the page itself). Recorded
  explicitly in `KNOWN_CODENAMES` with its provenance, and the manifest
  records `matchedAsCodename` for full auditability — never silently masked.

### Where it's used

`hero.html`'s new "Official presentation" panel (full artwork + source link
+ attribution + aliases) and `heroes.html`'s "not yet sighted" directory
cards (official portrait instead of a bare monogram — these cards carry no
comp/evidence claim, so real art is a strict readability upgrade). The
"in the meta" (verified-pick) cards and the hero dossier's header/portrait-
provenance section are **completely untouched** — they still show only the
broadcast-crop-or-monogram evidence face, exactly as before Phase D3.

### CLI

```bash
python pipeline/build_hero_official_assets.py             # fetch + build all 52
python pipeline/build_hero_official_assets.py --dry-run   # report only, no writes
python pipeline/build_hero_official_assets.py --hero-id zarya --force
python pipeline/build_asset_manifest.py                    # regenerate asset_manifest.json (heroes + heroOfficial + teams)
```

### Tests

`pipeline/test_hero_official_assets.py` — offline unit tests for the
matching/normalization logic (synthetic HTML fixtures, no network) plus
validation of the real, committed manifest: all 52 hero ids present,
GitHub-Pages-safe paths, files exist on disk, WebP present + AVIF explicitly
null, dimensions/hash recorded, source/attribution present, aliases match
the curated (never-fabricated) table.

## Beta Sprint — Phases E/F/G/I: the closed-loop worker, segmentation,
## detection wiring, and publication (implemented)

The "one real match, one official VOD, one approved map, closed loop to a
live page" beta. **One job travels the whole loop** (not a separate job per
phase): `ARCHIVED` ("ready" — broadcast linked, download not started) ->
`DOWNLOADING` -> `DOWNLOADED` -> `SEGMENTING` -> `NEEDS_REVIEW` (segment
review) -> `READY_FOR_DETECTION` -> `PROCESSING` -> `NEEDS_REVIEW`
(detection/swap review) -> `APPROVED` -> `PUBLISHED`. `CANCELLED` is a new
terminal state (an explicit operator stop, distinct from the system's own
`IGNORED` verdict); `RETRY_SCHEDULED`/`FAILED`/`FAILED_PERMANENT` are the
existing Phase A failure lifecycle, unchanged. Only `DOWNLOADING` and
`READY_FOR_DETECTION` are genuinely new states — everything else reuses the
Phase A graph's established vocabulary (`ARCHIVED` = "ready", `RETRY_
SCHEDULED` = "retryable"), because an equivalent already existed.

| Roadmap item | Where | Status |
|---|---|---|
| Job creation from a matched broadcast (idempotent) | `ops.create_job_from_broadcast` (`models.record_key` dedup) | ✅ |
| List/show/claim/release/retry/cancel/reset-stale-lock/resume | `ops.py` (thin, safe wrappers over `job_store.py`/`locks.py`) | ✅ |
| Dead-letter re-open is explicit, never silent | `job_store.retry_job(force=True)` — re-enters via `RETRY_SCHEDULED`, keeps full attempt history | ✅ |
| Same-state publish refusals never strand an APPROVED job | `job_store.record_error` (records the error, does NOT transition state — distinct from `record_attempt`'s retry/backoff lifecycle) | ✅ |
| Worker identity, dependency + disk preflight | `worker.py` (`worker_identity`, `check_dependencies`, `check_disk_space`) | ✅ |
| Official-source-only validation (domain allowlist, channel registry, manual-approval override, authority-conflict rejection) | `worker.validate_source` | ✅ |
| Claim + lease in one step (never two workers on one resource) | `worker.claim_and_lock` | ✅ |
| Download reuses the EXISTING yt-dlp/ffmpeg machinery, never reimplemented | `worker.download_job` calls `download_vod_clip.download_clip` (built on `video_ingest.py`) | ✅ |
| Full metadata capture (hash/duration/resolution/codec/sizes/tool versions) | `worker.download_job` -> `job_store.update_payload(..., {"media": {...}})` | ✅ |
| Every required failure mode classified (never a bare crash) | `worker.classify_download_error` (missing_dependency/corrupt_media/network_stall/invalid_source/insufficient_disk/download_failed/timeout/unknown) | ✅ |
| Resume after a crash mid-download | `worker.resume_interrupted` (stale-lease detection + `download_vod_clip`'s own cache-reuse makes re-entry safe) | ✅ |
| Media/caches/thumbnails never enter git | `data/worker/` gitignored; `.gitignore` extended with `.mov/.avi/.ts/.m3u8` | ✅ |
| Assisted segmentation: candidate generation reuses the EXISTING HUD-anchor/reject-marker classifier | `segmentation.generate_candidates` (calls `capture.py`'s `is_gameplay`/`_load_template`/`_load_reject_markers` — no new CV code) | ✅ |
| Candidate grouping + configurable pre/post-roll + flicker tolerance | `segmentation._group_candidates` (pure, independently tested) | ✅ |
| Segment storage + review actions (approve/reject/split/merge/adjust/mark-invalid) | `segmentation.py` against `map_segments` (Phase F table — previously schema-only, zero writers; `job_store._migrate` added the review/team/extraction columns it actually needs) | ✅ |
| Segment extraction reuses the EXISTING ffmpeg cut helper (local file, not just URL) | `segmentation.extract_segment_clip` calls `video_ingest._ffmpeg_cut_from_url` (works identically against a local path) | ✅ |
| Detection wiring reuses `ingest_map.py` UNCHANGED — no CV reimplementation | `detection_runner.build_ingest_args`/`run_detection` build the exact `argparse.Namespace` `ingest_map.run()` expects from an approved+extracted segment | ✅ |
| Two-phase detection: automatic dry-run review pass, then an explicit human-approved write pass | `detection_runner.run_detection(write=False)` -> `NEEDS_REVIEW`; `commit_approved_detection` only runs when `job.state == APPROVED` | ✅ |
| Idempotent detection reruns | `detection_runner.ingest_id_for` — stable per (job, segment), so `ingest_map.write_db` only ever replaces its own rows | ✅ |
| Every Phase 4 failure state classified (layout mismatch, no valid frames, missing templates, ...) | `detection_runner.classify_detection_error` | ✅ |
| Composition stints + swap proposals | **Already built** — `ingest_map.py`'s existing temporal-consensus/hysteresis swap detector, unchanged; this phase only wires an approved segment into it automatically | ✅ (reused, not rebuilt) |
| Human-gated promotion, export regeneration + validation, packaging check, secret scan, no-media-staged check | `publish.py` (`check_preconditions`, `regenerate_and_validate_export`, `run_packaging_check`, `scan_for_secrets`, `staged_media_files`) | ✅ |
| Refuses publication for every named reason (review incomplete, match/map/team identity unresolved, layout mismatch, evidence missing, export/test failure) | `publish.PublishRefusal` + `check_preconditions` | ✅ |
| Scoped publication commit + push, never touching main directly | `publish.create_publication_commit` (fresh branch only; PR open/CI wait/merge/Pages stay the existing, already-working human/CI flow — not reimplemented) | ✅ |
| `publication_runs` recorded (Phase I table — previously schema-only) | `publish.publish_job` inserts `db_hash`/`export_hash`/`branch`/`source_commit`/`state` | ✅ |
| Operator command: `process-approved-job --job <id> [--publish]` | `cli.py cmd_process_approved_job` — dry-run by default, `--publish` to actually commit + push | ✅ |
| Beta ops dashboard (Phase 7) | `beta-ops.html` + `assets/js/public/page-beta-ops.js`, fed by `cli.py job-coverage --save` -> `assets/data/job_coverage.v1.json` — read-only (GitHub Pages has no server); every row names the exact CLI command to run next | ✅ |
| New CLI surface | `create-job`, `list-jobs`, `show-job`, `claim-job`, `release-job`, `retry-job`, `cancel-job`, `reset-stale-lock`, `resume-job`, `run-job`, `job-coverage [--save]`, `worker-run`, `worker-doctor`, `segment-list`, `segment-approve`, `segment-reject`, `detect-job [--write]`, `process-approved-job [--publish]` | ✅ |
| Windows-worker preflight checklist (Python, repo deps, tool versions, disk, cache/artifact-dir writability, `gh` auth, API-key presence — value NEVER read into the report) | `worker.doctor_report` / `cli.py worker-doctor` | ✅ |

### Honest environment limitation (this pass)

This pass was built and fully tested in a sandboxed remote session whose
egress policy explicitly denies `www.youtube.com` (confirmed via the agent
proxy's own diagnostics: `403` "policy denial" on the CONNECT, not a
transient failure) and ships no `ffmpeg`/`yt-dlp`/`opencv` pre-installed
(all three were pip/apt-installed for this session only, to run the offline
test suite — `ffmpeg` in particular made `segmentation.py`'s real-cut test
pass against a real synthetic clip). **No real YouTube VOD was downloaded
in this session** — that requires the self-hosted Windows worker machine
this repo's own docs already point at for every prior real-VOD ingestion
(`docs/WINDOWS_WORKFLOW.md`, `HANDOFF.md`'s "machine quirks" sections). The
closed-loop code above is complete and offline-tested end-to-end (`worker-
run` was smoke-tested live through the real CLI against a real `yt-dlp`
binary, real `ffmpeg`, and a real automation DB — it correctly reached the
network boundary, failed safely, and recorded a classified error); the
`--publish` git/PR/CI/Pages leg was exercised against an isolated throwaway
git repo (never this session's own working tree). Running one real match
through the loop end-to-end is the next action on a machine with YouTube
access — see the completion report for exact commands.

### Tests

`test_automation_worker.py` (27 checks: preflight, source validation incl.
unofficial/shell-string/authority-conflict rejection, claim+lock, every
required download failure mode, resume-after-crash, unicode-safe logs),
`test_automation_segmentation.py` (21 checks: candidate grouping incl. gap
tolerance/pre-post-roll/confidence, full review-action CRUD, extraction incl.
one real-ffmpeg end-to-end cut), `test_automation_detection_runner.py` (12
checks: arg construction from an approved segment, idempotent ingest ids,
dry-run-then-commit two-phase flow, every Phase 4 failure classification),
`test_automation_publish.py` (14 checks: every named refusal reason, secret
scan, real-git-repo commit/push/no-media-staged/no-empty-commit/no-
republish-without-reapproval), `test_automation_ops.py` (20 checks:
idempotent job creation, claim/release/retry/cancel/reset-lock, `run_one_job`
dispatch for every state, coverage-report shape). Plus extensions to
`test_automation_state_machine.py` (the new states/edges), `test_automation_
job_store.py` (`update_payload`/`cancel`/`retry_job`/`record_error`),
`test_automation_locks.py` (`reset_stale`), and `test_public_site.py`
(`beta-ops.html` stays on the control-room shell). All offline, no network/
key required — fixtures/mocked transports throughout, exactly like every
other automation suite.

## Not yet implemented (later roadmap passes)

A graphical segment-review timeline (scrubbing/thumbnail scrubbing UI) —
this pass's segment review is CLI-driven (`segment-list`/`segment-approve`/
`segment-reject`), which already satisfies "operate without editing the
database," but a browser timeline is a real usability upgrade for a human
reviewer. Multi-worker/multi-job-at-once, live-stream recording, and
automatic map/team identification remain explicitly out of this sprint's
scope per the roadmap.

## Operator CLI

All commands are offline and read-mostly (`init-db` and `coverage --save` are
the only writers, and they only touch the automation DB):

```bash
python pipeline/automation/cli.py init-db          # create/upgrade the job DB
python pipeline/automation/cli.py config           # resolved operator config
python pipeline/automation/cli.py registries       # competition/channel registries
python pipeline/automation/cli.py coverage         # rolling completeness report (Phase D4, default 30-day window)
python pipeline/automation/cli.py coverage --save  # + persist a coverage snapshot
python pipeline/automation/cli.py status           # job counts by state + locks
```

`coverage` reads the content DB (`data/owcs.sqlite`) as the universe of tracked
matches and prints the roadmap's D4 block, listing **every** match missing an
official broadcast individually.

## Going live (filling the registries)

Both registries ship with placeholder IDs and every entry **disabled**, so the
discovery layer never ingests on a guess:

1. `config/faceit_competitions.json` — set each competition's real
   `championshipId` from the FACEIT Data API and flip `enabled: true`. Only
   enabled entries with a real ID are returned by `config.load_competitions()`.
2. `config/broadcast_channels.json` — set each channel's real `channelId` and
   flip `enabled: true`. Prefer channel upload playlists over broad search
   (quota: `videos.list` = 1 unit, `search.list` ≈ 100).
3. Tunables live in `config/automation.yml` (lookback window, retry ceilings,
   backoff schedule, lease TTL, publish mode, regions).

## Data locations

- Automation job queue: `data/automation.sqlite` (gitignored — runtime state,
  regenerable via `init-db`; override with `OWCS_AUTOMATION_DB`).
- Content DB: `data/owcs.sqlite` (committed, unchanged by this layer).

## Tests

Ten offline suites, run the same way as the rest of the pipeline
(`python pipeline/test_*.py`):

Phase A — `test_automation_state_machine.py`, `test_automation_config.py`,
`test_automation_schema.py`, `test_automation_job_store.py`,
`test_automation_locks.py`, `test_automation_coverage.py`.

Phase B — `test_automation_faceit_api.py`, `test_automation_discovery.py`
(idempotent repeat sync, multi-tournament/region, changed start times,
cancellation/forfeit, duplicate teams/aliases, 14-day boundary, partial
responses, API-failure retry jobs, stable ids, dry-run purity, no comp
leakage, no fixture contamination), `test_automation_reconcile.py`
(FACEIT↔calendar conflicts), `test_automation_calendar_export.py`
(public calendar export, end-to-end discovery→export).
