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

## Registry state (verified 2026-07-24 against the live YouTube API)

**`ow_esports_global` is confirmed** via a real GitHub Actions
`mode=verify-channels` dispatch:

| Field | Value |
|---|---|
| Internal id | `ow_esports_global` |
| Channel name | Overwatch Esports |
| channelId | `UCiAInBL9kUzz1XRxk66v-gw` |
| uploadsPlaylistId | `UUiAInBL9kUzz1XRxk66v-gw` |
| Official source URL | `https://www.youtube.com/OW_Esports` |
| Region / language | global / en |
| Verification method | YouTube Data API v3 `channels.list` (resolved via the channel's public handle) |
| Verification date | 2026-07-24 |
| Quota cost | **1 unit** |

It is now the **only** `enabled: true` / `verifiedStatus: verified` entry —
`cfg.load_channels()` (what `sync_broadcasts`/`discover_channel_videos`
actually iterate) returns exactly this one channel
(`test_exactly_the_verified_global_channel_is_enabled`,
`test_only_enabled_verified_channel_drives_discovery`).

| Registry id | sourceUrl (evidence) | Status |
|---|---|---|
| `ow_esports_global` | `https://www.youtube.com/OW_Esports` | **verified, enabled** (channelId `UCiAInBL9kUzz1XRxk66v-gw`) |
| `ow_esports_korea` | none found | disabled — no regional-specific channel evidenced |
| `ow_esports_japan` | none found | disabled — no regional-specific channel evidenced |
| `owcs_pacific` | none found | disabled — no regional-specific channel evidenced |
| `owcs_china` | n/a (bilibili) | disabled — out of scope for the YouTube API client by design; surfaces as `coverage_state=unsupported-source` rather than a silent gap |

**Next step**: the regional channels (Korea/Japan/Pacific) still need a
human-sourced official URL before `verify-channels` can resolve anything for
them — never guessed. `owcs_china` stays permanently out of the YouTube
client's scope (different platform), by design, not as a gap to fill.

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

**Pagination is bounded by the lookback window, not the channel's full
history.** An uploads playlist is newest-first; `youtube_api.
list_playlist_items` stops fetching pages once a page's oldest item predates
`lookback_days + 2` days (`broadcast_discovery.PAGINATION_SAFETY_BUFFER_DAYS`).
A normal 14-day run against a channel with 1,000+ total uploads fetches only
the handful of pages that actually fall in-window — pass `--full-history` to
`broadcast-dryrun`/`discover-broadcasts` to walk the entire history instead
(e.g. a one-off backfill).

**Repeated runs are cache-aware.** `YouTubeClient` caches every response
under `data/raw/youtube_api/` (gitignored) keyed by its sanitized URL, and a
request within the cache TTL (default 1 hour) is served from disk with
**zero quota spent** — `client.cache_hits` / the CLI's "client cache hits"
line report how many. This is enabled even during `--dry-run` (reading a
local cache is not a production write), so re-running a dry-run minutes
apart doesn't re-spend quota on identical requests.

## Broadcast-likeness pre-filter (2026-07-24 refinement)

A live `broadcast-dryrun` correctly fixed the "0 scored" bug (see above) and
found 7 in-window videos on the verified channel — but scoring alone
classified **all seven** as `event-level-candidate`, including six that were
plainly short-form promotional/instructional uploads (a 6-second lootbox
promo, several 1.5–2.5 minute tips/perk/patch videos). The root cause: a flat
**+40 official-channel bonus** plus a region-match bonus outweighed the lone
**−15 short-duration penalty**, so almost anything from the verified channel
cleared `MEDIUM_THRESHOLD=35` regardless of what it actually was.

`broadcast_matching.broadcast_likeness(video)` now runs BEFORE any
target/event scoring and asks a narrower question: does this even look like
a broadcast? It uses several independent, generic signals — never a single
duration cutoff, never a hard-coded title:

| Signal | Weight | Notes |
|---|---:|---|
| Livestream metadata present (live/upcoming/completed) | **+25** | A real broadcast has real live-streaming timestamps |
| No livestream metadata (`liveBroadcastContent`-derived status = none) | **−10** | An ordinary upload — most promos/guides are |
| Substantial duration (≥ 20 min) | **+20** | |
| Short duration (60s–20min) | **−20** | |
| Shorts-length duration (< 60s) | **−25** | |
| Tournament/broadcast terminology (tournament, stage, match, day N, group, playoffs, finals, bracket, qualifier, round, …) | **+15** | |
| Match/series formatting (`vs`, `BoN`, `Game N`, `Map N`, a `3-1`-style score) | **+15** | Generic regex, not any specific team/event name |
| Neither of the above two | **−10** | "no team/competition relationship signal" |
| Instructional/promotional keyword (tips, guide, perk(s), patch, clip(s), trailer, giveaway, lootbox, highlight(s), how to, tutorial, tier list, rework, breakdown, update, shorts) | **−30** | The strongest negative signal — these words essentially never appear in an official match broadcast title |

`confidence = "likely"` when the total is ≥ 0, else `"unlikely"`. A video
scored `"unlikely"` is classified `unrelated-official-upload` immediately and
is **never** scored against any match/event target — it costs zero candidate
pairs. A video scored `"likely"` proceeds to the existing target-scoring
stage exactly as before (a completed multi-hour "Group Draw"-style show
still reaches event-level-candidate/unsupported-event, but can never reach
`CLASS_HIGH` without a real team-level FACEIT match — see `classify_video`).

Verified against sanitized shapes of the real 7 videos
(`test_automation_broadcast_matching.py`'s `TestSevenRealShapedVideos`): the
six short uploads all land `unrelated-official-upload`; the one genuine
completed livestream stays a broadcast candidate and is the only one ever
scored against a target, so `totalCandidatePairsEvaluated` reflects 1 video's
comparisons, not 7×N.

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

### A video is never required to match a specific FACEIT match

A video is scored against THREE pooled target sources — `scheduled_matches`
(verified FACEIT matches), `source_events` (automation DB, mirrors the
calendar once `sync_calendar` has run), and `owcs_calendar.load_events()`
(the committed seed file, always available). Every in-window video reduces
to exactly one top-level classification, so nothing is ever silently
omitted from a report:

| Classification | Meaning |
|---|---|
| `high` | Confirmed against a specific team-level FACEIT match, high confidence |
| `needs-review` | Ambiguous against a specific FACEIT match — opens a review task |
| `event-level-candidate` | Matches an event/calendar target (no team pairing available) at any confidence above LOW — the "full-day broadcast" case |
| `unmatched-official-video` | Official, in-window, YouTube-supported region — but no eligible target existed at all |
| `unrelated-official-upload` | Failed the broadcast-likeness gate (never scored against any target), OR eligible targets existed but every one scored LOW |
| `unsupported-event` | A target references this region, but no YouTube channel covers it (e.g. China/bilibili) |
| `outside-configured-coverage` | Nothing anywhere (FACEIT, calendar, or channel registry) tracks this region |

`match_broadcasts` guarantees `videosScored == sum(classifications.values())`
— see `test_automation_broadcast_matching.py`'s `TestClassifyVideo` and
`TestMatchBroadcastsThreeTargetSources` for the pinned behavior, including
the regression test for the exact "7 videos, 0 scored" bug this fixed.

**Distinct videos vs. candidate pairs — don't confuse the two.**
`summary["linked"]`/`["reviewed"]`/`["rejected"]` and
`summary["totalCandidatePairsEvaluated"]` count video×target PAIRS (one
video can be compared against several targets); `summary["classifications"]`
and `summary["distinctVideos"]` (`{high, mediumOrReview,
rejectedOrUnrelated}`) count DISTINCT VIDEOS. A run with 7 videos and 3
targets can produce up to 21 pairs — the CLI report labels each number
explicitly so "21" is never misread as "21 videos". Each per-video result
also carries `targetsFiltered`: which loaded targets were excluded for that
video and why (outside its time/date window, or never evaluated because the
video failed the likeness gate) — this is what answers "4 calendar events
were loaded, why were only 3 actually considered for this video?".

## No secret leakage, by construction

`pipeline/automation/youtube_api.py` strips the `key` query param from every
URL (`_sanitize_url`) before it is used as a cache-file key, recorded in
`client.calls` (the audit trail), or embedded in any exception message —
verified by `test_automation_youtube_api.py`'s `TestCaching` cases (key never
appears in cache files, call records, or error strings). The CLI never prints
`os.environ["YOUTUBE_API_KEY"]` directly; the workflow only ever uploads
stdout, which carries the same guarantee.
