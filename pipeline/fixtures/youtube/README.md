# Offline YouTube fixtures (Phase C broadcast discovery)

These committed JSON files let `automation.youtube_api.fixture_transport` serve a
complete broadcast-discovery scenario with **no network and no API key**, so CI
and `cli.py discover-broadcasts --fixture-dir pipeline/fixtures/youtube` run the
whole matching pipeline deterministically.

File naming (matches `fixture_transport` in `youtube_api.py`):

| Request | File |
|---|---|
| `channels.list?forHandle=ow_esports` | `handle_ow_esports.json` |
| `channels.list?id=UC_OW_ESPORTS_OFFICIAL` | `channel_uc_ow_esports_official.json` |
| `playlistItems.list?playlistId=UU_OW_ESPORTS_OFFICIAL` | `playlist_uu_ow_esports_official.json` |
| `videos.list?id=<vid>` | `videos_<vid>.json` (merged per-id) |
| `search.list?...` | `search.json` |

The scenario is dated around **2026-06-01 … 2026-07-25** so most of it lands
inside a 14-day rolling window anchored at 2026-07-24. On the ONE official
channel `UC_OW_ESPORTS_OFFICIAL` it contains:

* `naday3broadcast` — a full-day "Day 3" broadcast (completed livestream),
* `nafinalsvod` — a team-specific VOD (Spacestation vs NTMR),
* `upcomingnaday4` — an upcoming scheduled livestream,
* `oldstage1vod` — a Stage 1 VOD **outside** the rolling window (must be
  filtered out by the window boundary).

Unofficial-mirror rejection and other edge cases are exercised directly in
`test_automation_broadcast.py` with an in-memory transport; these committed
files back the CLI dry-run and the fixture-path test.

None of these files contain a real API key — fixtures never do. They are match
FACTS about broadcasts only; there is not a single hero/composition field.
