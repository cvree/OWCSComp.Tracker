# Broadcast discovery (Roadmap Phase C)

Read-only discovery that locates the **official** broadcast for each scheduled
OWCS match on YouTube — upcoming livestreams, currently-live broadcasts,
completed livestreams and uploaded VOD archives — scores each candidate, and
records an explicit per-match coverage state. It runs over the rolling previous
14 days plus the configured future horizon.

**It never downloads a byte of video and never writes a hero composition.** The
only content-DB write it can make is a discovered official `vod_url` on a match
row, and only when the master `broadcast_auto_link` switch is on AND the
candidate is high-confidence.

## Design rules (enforced in code + tests)

1. **Prefer official upload playlists over broad search.** `channels.list`,
   `playlistItems.list` and `videos.list` each cost **1** YouTube quota unit;
   `search.list` costs **~100**. Discovery scans each official channel's uploads
   playlist and only falls back to a channel-scoped search when a channel
   exposes no uploads playlist. The client tracks every unit
   (`YoutubeClient.quota_used`) and stops cleanly with a `YoutubeQuotaError`
   before exceeding the configured budget — it never invents a broadcast to
   fill a quota gap.
2. **Reject unofficial mirrors by default.** A candidate whose `channelId` is
   not in the verified-official set is rejected outright, no matter how well its
   title text matches.
3. **Auto-link only high-confidence official broadcasts.** Everything uncertain
   goes to review. Auto-linking is additionally gated by `broadcast_auto_link`
   (default `false`), so scheduled runs stay review-only until you approve the
   first real dry-run.
4. **No silent gaps.** Every in-window match gets exactly one
   `broadcast_coverage` row: `LOCATED`, `NEEDS_REVIEW`, `MISSING`, or
   `UNSUPPORTED` (no enabled official channel for the region).
5. **Idempotent.** Reruns upsert the same `broadcast_candidates` /
   `broadcast_coverage` rows and never duplicate a `broadcast:<videoId>` job.
6. **The key is a secret.** `YOUTUBE_API_KEY` is read only from the environment
   (GitHub Actions secret), and is redacted from every audit record, cache file,
   log line and exception. It is never printed, committed, or written to a
   report.

## The registry — `config/broadcast_channels.json`

Each channel stores exactly the fields the roadmap asks for:

| field | meaning |
|---|---|
| `id` | stable registry id (e.g. `ow_esports_global`) |
| `channelId` | **API-verified** YouTube channel id (`null` until confirmed) |
| `uploadsPlaylistId` | the channel's uploads playlist (`null` until confirmed) |
| `name` | exact channel name |
| `region` / `language` | feed selection + region compatibility |
| `officialSourceUrl` | public handle URL — the human-checkable source |
| `priority` | tie-breaker when several official feeds cover one match |
| `verificationDate` | date the id was API-verified (`null` until then) |
| `enabled` | only enabled + `channelId`-carrying channels drive discovery |

Only **API-verified** channel ids may be committed. Ids are never guessed: a
`null`-id channel stays `enabled: false` so it can never drive discovery on a
guess (identical to the FACEIT registry rule).

### Verifying channels

The `YOUTUBE_API_KEY` secret lives only in GitHub Actions, so verification runs
there:

```
Actions → discovery → Run workflow → mode = verify-channels
```

`verify-channels` resolves each official handle to its channel id + exact name +
uploads playlist via `channels.list` (1 unit each), prints them, and writes
nothing. Paste the confirmed `channelId` + `uploadsPlaylistId` +
`verificationDate` into the registry and flip `enabled: true`.

## Scoring model (`broadcast.score_candidate`)

Official-channel authority is a boost, not a shortcut: a candidate must carry a
real **content** signal (team / event / FACEIT id) or it is not recorded at all,
so an unrelated official upload (a hero trailer) never links to a match.

| signal | points |
|---|---|
| official channel (verified id) | +30 |
| both team names in title/description | +40 |
| one team name | +18 |
| event / competition name | +20 |
| region (in title, else channel region) | +10 |
| language feed match | +5 |
| FACEIT match id / room slug in description | +25 |
| broadcast time within `broadcast_time_window_hours` of match | +20 |
| same calendar day (full-day broadcast) | +12 |
| unofficial channel | reject |

`score ≥ broadcast_high_score` (default 90) → **high**;
`≥ broadcast_medium_score` (45) → **medium** (review); below → not recorded.

A **full-day broadcast** (event + region + day, no specific teams) is a medium
candidate for *each* match that day, so one video links to several matches. A
team-specific VOD outscores it and wins the per-match "best" slot.

## Coverage states (`broadcast_coverage`)

| state | meaning |
|---|---|
| `LOCATED` | high-confidence official broadcast, auto-linked (switch on) |
| `NEEDS_REVIEW` | a candidate found, but not auto-linked (default posture) |
| `MISSING` | no candidate scored at/above the review threshold |
| `UNSUPPORTED` | no enabled official channel exists for the match's region |

## Config knobs (`config/automation.yml`)

```yaml
youtube_daily_quota: 10000       # per-run quota ceiling (client stops before exceeding)
broadcast_auto_link: false       # master switch — review-only until you flip it on
broadcast_high_score: 90         # >= this AND official  -> high (auto-linkable)
broadcast_medium_score: 45       # >= this               -> review
broadcast_time_window_hours: 6   # same-window time signal
broadcast_playlist_pages: 6      # uploads pages scanned per channel per run
```

## CLI

```bash
# resolve/verify official channels (read-only; needs YOUTUBE_API_KEY)
python pipeline/automation/cli.py verify-channels

# rolling broadcast discovery dry-run (writes nothing; never downloads video)
python pipeline/automation/cli.py discover-broadcasts --dry-run

# fully offline demo against committed fixtures (no key, no network)
python pipeline/automation/cli.py discover-broadcasts --dry-run \
    --fixture-dir pipeline/fixtures/youtube
```

## First real dry-run — exact workflow inputs

Run these in order from **Actions → discovery → Run workflow**:

1. `mode = verify-channels` — confirm the official channel ids, then commit them
   to `config/broadcast_channels.json` with `enabled: true`.
2. `mode = broadcast-dryrun`, `lookback_days = 14` — rolling broadcast discovery
   with **no writes and no downloads**. Review the located/review/missing split.
3. Only after that review: set `broadcast_auto_link: true` for a supervised run.

Until step 3, discovery is strictly read-only and auto-linking is disabled.

## Quota / caching / data locations

- Quota per run is tracked on `YoutubeClient.quota_used` and appended to the
  `youtube_quota` ledger table each live run.
- Raw API responses are cached under `data/raw/youtube_api/` (gitignored, via
  `data/raw/`), with the key redacted from every cached URL.
- Broadcast state lives in the automation DB (`data/automation.sqlite`,
  gitignored runtime state): `broadcast_candidates`, `broadcast_coverage`,
  `review_tasks`, `youtube_quota`, plus `broadcast:<videoId>` jobs in `jobs`.
