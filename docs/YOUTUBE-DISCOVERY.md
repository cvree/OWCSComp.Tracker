# YouTube broadcast discovery — channel verification, quota, scoring

This document records how `config/broadcast_channels.json` is verified against
the **live YouTube Data API v3**, the quota-cost assumptions the discovery
layer bakes in, the C4 matching-score rationale, and the result of the
2026-07-24 registry pass (implementation-only — no live channel could be
verified from this environment; see below).

## Why verification runs in GitHub Actions, not locally

`YOUTUBE_API_KEY` is a **GitHub Actions repository secret**. It is never
present in a developer sandbox. All live channel verification and broadcast
discovery therefore runs through the `discovery` workflow's read-only
`workflow_dispatch` modes (`verify-channels`, `calendar-dryrun`,
`broadcast-dryrun`, `coverage`), where the secret and network access exist.
Channel ids are **never guessed** — an entry in `config/broadcast_channels.json`
stays `channelId: null` and `enabled: false` until `verify-channels` resolves a
real id from the API and a human applies it (the command itself never edits
the registry file, exactly like the FACEIT registry pass in
`docs/FACEIT-REGISTRY.md`).

## Read-only tooling

CLI (offline-testable with `--fixture-dir <dir>` — serves committed/local
JSON instead of the network):

```bash
# verify every configured channel (enabled or not) against the live API
python pipeline/automation/cli.py verify-channels [--json]
# official-calendar dry-run + reconciliation (event-level, no rolling window)
python pipeline/automation/cli.py calendar-dryrun --lookback-days 14
# YouTube broadcast discovery + matching, read-only (always --dry-run)
python pipeline/automation/cli.py broadcast-dryrun --lookback-days 14 [--allow-search-fallback]
# the same, but writes when channels are enabled+verified and --dry-run is omitted
python pipeline/automation/cli.py discover-broadcasts [--dry-run] --lookback-days 14
# rolling completeness report, now including Phase C6 broadcast coverage
python pipeline/automation/cli.py coverage --window 14
```

Workflow (`discovery.yml`) read-only modes, dispatched with the secret:

```bash
gh workflow run discovery.yml -r <branch> -f mode=verify-channels
gh workflow run discovery.yml -r <branch> -f mode=calendar-dryrun -f lookback_days=14
gh workflow run discovery.yml -r <branch> -f mode=broadcast-dryrun -f lookback_days=14
gh workflow run discovery.yml -r <branch> -f mode=coverage -f lookback_days=14
```

Each run's stdout (never containing the API key — see "no secret leakage"
below) is uploaded as a workflow artifact (`discovery-<mode>-<run-id>`,
30-day retention); nothing is committed to the repo by these modes.

## Registry state (this pass, 2026-07-24)

**No channelId has been confirmed** — this pass ran with no YouTube API
network access (the key only exists as a GitHub Actions secret). What was
established from public, non-API sources:

| Registry id | sourceUrl (evidence) | Status |
|---|---|---|
| `ow_esports_global` | `https://www.youtube.com/OW_Esports` — listed as the official YouTube channel on Liquipedia's Overwatch Champions Series page, alongside the official Twitch/Discord/social accounts, credited to Blizzard Entertainment | `unverified`, disabled — needs `verify-channels` in Actions |
| `ow_esports_korea` | none found | disabled — no regional-specific channel evidenced |
| `ow_esports_japan` | none found | disabled — no regional-specific channel evidenced |
| `owcs_pacific` | none found | disabled — no regional-specific channel evidenced |
| `owcs_china` | n/a (bilibili) | disabled — out of scope for the YouTube API client by design |

**Next step to go live**: dispatch `mode=verify-channels` on this branch (or
after merge) to resolve `ow_esports_global`'s real `channelId` via
`channels.list(forHandle=@OW_Esports)`, then a human edits
`config/broadcast_channels.json` to set the id, `verifiedStatus: verified`,
`verifiedDate`, and `enabled: true`. Regional channels need a human-sourced
official URL first (see each entry's `disabledReason`) — never guessed.

## Quota cost assumptions (Data API v3)

| Endpoint | Cost | Used for |
|---|---|---|
| `channels.list` | 1 unit | Resolve a channel's uploads-playlist id (C1/C2) |
| `playlistItems.list` | 1 unit / page (up to 50 items) | Enumerate a channel's uploads (C3) |
| `videos.list` | 1 unit / call (up to 50 ids batched) | Hydrate status/liveStreamingDetails (C3) |
| `search.list` | 100 units / call | LAST-RESORT fallback only — opt-in via `--allow-search-fallback` |

The default YouTube Data API v3 project quota is 10,000 units/day. Preferred
path per channel per run: 1 (`channels.list`) + ceil(uploads/50)
(`playlistItems.list`) + ceil(videos/50) (`videos.list`) — a channel with
~200 recent uploads costs ~6 units/run, vs. 100+ units for one `search.list`
call. Spend is tracked in the automation DB's `quota_usage` table
(`pipeline/automation/broadcast_discovery._record_quota`) and surfaced by
`cli.py coverage` / `broadcast-dryrun`.

## C4 matching score — weights and thresholds

`pipeline/automation/broadcast_matching.py` scores every (video, nearby
scheduled-match) pair additively; every fired signal is recorded in
`reasons` and persisted as JSON in `broadcast_candidates.signals` so a human
reviewing a MEDIUM candidate can see exactly why it scored the way it did.

| Signal | Weight | Rationale |
|---|---:|---|
| Official channel | **+40** | The single strongest authority signal — an official upload is the ground truth source |
| Unofficial/unverified channel | **−30** | Actively distrust unregistered mirrors; combined with a typical +10..+20 of title/team signals, an unofficial mirror still lands LOW |
| Team A / Team B name in title or description | **+15 each** (up to +30) | Team names are highly specific; both matching is strong evidence |
| Competition/stage name matched | **+15** | e.g. "OWCS 2026 NA" |
| Known OWCS title pattern (OWCS, Champions Clash, Open Qualifier, Grand Final, Playoffs, Stage N) | **+10** | Catches official-style titling even before team names are confirmed |
| Region match | **+10** | Channel/video region agrees with the match's region |
| Language match | **+5** | Secondary signal — a channel can be official but cover multiple languages |
| FACEIT room reference in description | **+10** | Direct linkage to the FACEIT match room |
| Start time within 30 minutes | **+20** | Strong temporal agreement |
| Start time within 12 hours (not "close") | **+8** | Same broadcast day, weaker precision |
| Start time conflicts by > 48 hours | **−25** | Actively penalize — this is very unlikely to be the same event |
| Both match and video currently live | **+15** | Real-time confirmation |
| Duration ≥ 20 minutes | **+5** | Plausible for a full broadcast |
| Duration < 20 minutes | **−15** | Very likely a clip/highlight reel, not the broadcast itself |

Bands (`confidence_band`, `HIGH_THRESHOLD=70`, `MEDIUM_THRESHOLD=35`):

- **HIGH** (≥70): official channel + strong agreement (e.g. official +40,
  both team names +30, an OWCS pattern +10, region +10 already clears 70) —
  proposed as an automatic link in dry-run output, but Phase C never
  auto-applies it to production; a later phase/human confirms.
- **MEDIUM** (35–69): likely official but incomplete/ambiguous (e.g. official
  channel + one team name + OWCS pattern = 40+15+10 = 65, or lower) — opens a
  `review_tasks` row (`kind='broadcast_link'`).
- **LOW** (<35): weak or conflicting signals (unofficial channel nets −30
  before anything else) — rejected by default, not stored (storing a
  rejected pairing on every rerun would just accumulate noise with no
  operator value; the rejection is still visible in a dry-run's summary).

These constants are the single source of truth for the numbers above — see
`test_automation_broadcast_matching.py` for the pinned boundary behavior
(exact HIGH/MEDIUM/LOW transitions, every signal in isolation).

## No secret leakage, by construction

`pipeline/automation/youtube_api.py` strips the `key` query param from every
URL (`_sanitize_url`) before it is used as a cache-file key, recorded in
`client.calls` (the audit trail), or embedded in any exception message —
verified by `test_automation_youtube_api.py`'s `TestCaching` cases (key never
appears in cache files, call records, or error strings). The CLI never prints
`os.environ["YOUTUBE_API_KEY"]` directly; the workflow only ever uploads
stdout, which carries the same guarantee.
