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
- `config/broadcast_channels.json` — every channel still has `channelId: null`
  and `enabled: false`; `ow_esports_global` now carries a Liquipedia-evidenced
  `sourceUrl` (`youtube.com/OW_Esports`) ready for `verify-channels` to resolve
  in Actions. Full Phase C implementation (discovery + matching + coverage)
  landed this pass — see the "Phase C" section above and
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
| C1 verified broadcast-channel registry | `config/broadcast_channels.json` (sourceUrl/ownershipEvidence/verifiedDate/verifiedStatus/disabledReason/preferredLayout) + `cli.py verify-channels` | ✅ (no channelId confirmed yet — no live API access from this pass; see `docs/YOUTUBE-DISCOVERY.md`) |
| C2 YouTube Data API client (dependency-light, injectable transport) | `pipeline/automation/youtube_api.py` | ✅ |
| C2 quota-unit accounting + exhaustion detection | `youtube_api.QUOTA_COSTS`, `YouTubeQuotaExceeded`, `quota_usage` table | ✅ |
| C3 broadcast discovery (upcoming/live/completed/archive/VOD, cheapest-path-first) | `broadcast_discovery.py` (`discover_channel_videos`, `sync_broadcasts`) | ✅ |
| C3 rolling 14-day window + future horizon | `broadcast_discovery.in_window` | ✅ |
| C4 explainable scoring + HIGH/MEDIUM/LOW bands | `broadcast_matching.py` (`score_candidate`, `confidence_band`) | ✅ — see `docs/YOUTUBE-DISCOVERY.md` for the full weight table |
| C4 one-video-to-many-matches / many-videos-to-one-match | `broadcast_candidates` (match_id, platform, video_id) unique key | ✅ |
| C5 deterministic job keys + idempotency | `models.broadcast_discovery_key`, `models.broadcast_key`, `models.broadcast_match_link_key` | ✅ |
| C6 explicit coverage states (nothing disappears silently) | `coverage.build_broadcast_coverage` / `derive_coverage_label` | ✅ |
| C7 official-calendar adapter improvements (season/format/sourceUrl/retrievedAt/sourceHash/verificationStatus, live Next.js extraction) | `owcs_calendar.py` (`http_fetcher`, `extract_events_from_next_data`) | ✅ |

### CLI

```bash
python pipeline/automation/cli.py verify-channels [--json]
python pipeline/automation/cli.py calendar-dryrun --lookback-days 14
python pipeline/automation/cli.py broadcast-dryrun --lookback-days 14 [--allow-search-fallback]
python pipeline/automation/cli.py discover-broadcasts [--dry-run] --lookback-days 14
python pipeline/automation/cli.py coverage --window 14   # now includes Phase C6 broadcast coverage
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
mirror rejection), `test_automation_owcs_calendar.py` (C7 fields, resilient
`__NEXT_DATA__` extraction, live-fetch failure modes), plus extensions to
`test_automation_schema.py` (new tables) and `test_automation_config.py`
(registry field completeness) and `test_automation_coverage.py` (C6 label
derivation).

## Not yet implemented (later roadmap passes)

The self-hosted recording daemon (Phase E), broadcast segmentation
(Phase F), the detector/layout/template automation (Phase G), and automated
publication PRs (Phase I). Each plugs into this foundation: they enqueue jobs
with the deterministic keys above, take a lease before touching a shared
resource, and transition through the state machine.

## Operator CLI

All commands are offline and read-mostly (`init-db` and `coverage --save` are
the only writers, and they only touch the automation DB):

```bash
python pipeline/automation/cli.py init-db          # create/upgrade the job DB
python pipeline/automation/cli.py config           # resolved operator config
python pipeline/automation/cli.py registries       # competition/channel registries
python pipeline/automation/cli.py coverage         # rolling 14-day report (Phase D4)
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
