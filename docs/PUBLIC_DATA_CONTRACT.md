# Public data contract (`public.v1`)

Every fan-facing page reads exactly one dataset: `window.OWCS_PUBLIC`, loaded
from a single script file before `assets/js/public/core.js`. Pages never
compute stats server-side and never fetch anything at runtime — this is the
whole "backend".

## Fixture vs production boundary

| | file | `meta.demo` | who writes it |
|---|---|---|---|
| Demo / design fixture | `assets/data/public_fixture.v1.js` | `true` | hand-authored, versioned |
| Production (future) | `assets/data/public_data.v1.js` | `false` | `pipeline/export_data.py` |

Rules:
- `export_data.py` must **never** write to `public_fixture.v1.js`
  (guarded by `pipeline/test_public_site.py`).
- `meta.demo: true` renders a visible "Demo data" ribbon on every page and a
  "demo dataset" pill in the header. Production builds swap the script tag to
  the exported file; the fixture stays in the repo forever for tests.
- All timestamps are stored **UTC ISO-8601** and rendered in the viewer's
  local timezone by JS. `meta.generatedAt` drives the "as of …" freshness
  label and the >24h stale warning.

## Top-level keys

| key | shape | notes |
|---|---|---|
| `meta` | `{schema, demo, generatedAt, note}` | `schema` = `"public.v1"` |
| `regions` | `[{id, name, short}]` | ids: `all, na, emea, asia, china, pacific` |
| `teams` | `[{id, name, code, region, logoUrl}]` | `logoUrl:null` → monogram plate fallback |
| `players` | `[{id, teamId, handle, role}]` | optional per team; empty roster → empty state |
| `tournaments` | see below | |
| `bracketRounds` / `extraRounds` | `[{id, tournamentId, stageId, side, order, name, bestOf}]` | `side`: `upper` / `lower` / `gf` |
| `bracketMatches` | `[{id, roundId, position, matchId, feedsWinnerTo, feedsLoserTo}]` | feeds reference **bracket node ids** (validated by tests) |
| `matches` | see below | |
| `heroBans` | `[{id, matchId, mapId, teamId, hero, order, phase, source}]` | **match facts** (faceit/manual) — never comps |
| `heroSwaps` | see below | temporal-consensus swap verdicts (confirmed + rejected) |
| `captureRuns` | see below | the evidence spine |
| `compSnapshots` | see below | the moat |
| `vodSources` | `[{id, provider, url, title, matchIds, heightAvailable}]` | |
| `heroes` | `[{id, name, role}]` | optional `portraitUrl` supported by hero tiles |
| `mapsCatalog` | `[{id, name, mode}]` | |
| `patches` | `[{id, name, from}]` | stats filter foundation |

### tournaments
`{id, name, series, region, tier(S/A/B), year, startsAt, endsAt,
status(upcoming/live/completed), prizePool, teamIds[], winnerTeamId?,
summary, logoUrl, sources[{type,url,lastSynced}], stages[{id,name,order,
format,status}], standings[{stageId,group,rows[{teamId,w,l,mapDiff}]}]}`

### matches
`{id, tournamentId, stageId, roundId, teamA, teamB (null = TBD, with
tbdNote), bestOf, scheduledAt(UTC), status(upcoming/live/completed/forfeit),
scoreA, scoreB, winner, streamUrl, faceitUrl, liquipediaUrl, casters[],
sources[], captureStatus, captureRunId, summary, maps[]}`

`captureStatus` ladder (exact strings, used site-wide):
`needs-source → queued → capturing → needs-review → verified → failed`

### maps + typed `scoreDetail`
Each map: `{id, order, map(catalog id), mode, winner, scoreA, scoreB,
scoreDetail, pickedBy, pickNote, live?}`. `scoreDetail.type` decides the
widget:

| type | payload |
|---|---|
| `control` | `rounds: [{a: %, b: %}]` |
| `escort` / `hybrid` | `a/b: {points, timeBank}`, optional `note` |
| `push` | `distanceA`, `distanceB` (strings with unit) |
| `flashpoint` | `capturesA`, `capturesB` |
| `clash` | `pointsA`, `pointsB` |
| `null` | live / unavailable → honest fallback text |

### maps `rounds`
Each map additionally carries `rounds:
[{index, start, end, confidence, source}]` — the real round windows found
by the round-emblem detector (broadcast offsets in seconds, ±1 sample).
Empty when the map was not ingested by the CV pipeline.

### heroSwaps — the swap intelligence layer
`{id, matchId, mapId, teamId, side, slot, fromHero, toHero, offset(s),
confidence, status("confirmed"/"rejected"), reason, evidenceBefore,
evidenceAfter, ingestId}`

1. Rows come only from the DB's `hero_swaps` table (temporal-consensus
   verdicts) — never derived client-side, never invented.
2. `status:"confirmed"` rows carry before/after evidence crops (paths are
   dropped to `null` if the file is missing so the UI can never render a
   broken image).
3. `status:"rejected"` rows are exported too, with the honest `reason`
   they were thrown out (dead-portrait lookalikes, killcam artifacts…).
   Fan pages show confirmed swaps as the timeline and surface rejected
   counts/reasons as credibility, never as swaps.

### captureRuns
`{id, matchId, sourceId, window{start,end,every}, requestedHeight,
actualWidth, actualHeight, clipMode, status(ladder above), reportPath,
createdAt, frames[{offset, file, layoutDebug}], crops[], note?}`

`reportPath` / `frames[].file` / `crops[]` must resolve to real files in the
repo (validated by tests) — the click-through evidence rule.
Requested vs actual resolution is always rendered; a mismatch shows a ⚠.

### compSnapshots — credibility rules (enforced in `core.js`)
`{id, matchId, mapId, teamId, side, timestamp(s), heroes[5], source,
confidence, reviewStatus, evidenceRunId, evidenceFrame,
overridesId? / overriddenBy?, correction?{note, author, appliedAt}}`

1. `source` is only ever `"cv"` or `"manual"`. FACEIT can never supply a comp.
2. Public pages render only `reviewStatus` `"reviewed"` or `"auto-high"`
   (`OWCS_PUB.APPROVED_REVIEW`, applied by `OWCS_PUB.publicComps`).
   The fixture deliberately contains a `needs-review` row to prove the
   filter works.
3. A manual snapshot with `overridesId` hides the CV row it corrects; the
   CV row is kept (never deleted) and shown inside the correction history.
4. Every snapshot carries `evidenceRunId` (+ `evidenceFrame`) — the UI links
   comp → run → frames → crops → review status. Missing chain ⇒ not shown.

### Stats (`assets/js/public/stats.js`)
`OWCS_STATS.computeHeroStats(filters)` counts one **(map, team)
appearance** per hero (multiple snapshots collapse — long maps don't
multiply-count). Win rate only counts maps with a decided winner. Every row
carries `evidence: [{matchId, mapId, snapshotIds}]`. Ban stats come from
`heroBans` and are labeled as match facts. Filters: `region`, `teamId`,
`tournamentId`, `mapId` (all default `"all"`).
