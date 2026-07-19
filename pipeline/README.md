# Pipeline

Turns finished OWCS broadcast VODs into the comp data the website serves.
Pure Python + SQLite + OpenCV + ffmpeg + yt-dlp — all free, no server.

## Flow

```
init_db.py ──► ingest_faceit.py ──► capture.py ──► detect.py ──► map_sync.py ──► export_data.py
 (schema/seed)  (VOD→frames,   (frames→comp   (snapshots→     (SQLite→
                 gameplay       snapshots,     correct map,    assets/js/data.js,
                 filter)        quarantine)    verify report)  site redeploys)
```

`ingest_faceit.py` is the Milestone 1 results ingest. It caches public FACEIT matchrooms and can create placeholder match shells now; future API/parser work should enrich those rows with real teams, map order, bans, replay codes, and scores. Until live data exists, `init_db.py --with-sample` loads demo FACEIT-style matches.

## Setup

```bash
pip install opencv-python-headless yt-dlp    # ffmpeg from your OS packages
python3 pipeline/init_db.py                  # schema + heroes/maps/teams
python3 pipeline/init_db.py --with-sample    # optional: demo matches for testing
```

DB lives at `data/owcs.sqlite` (override with env `OWCS_DB`).

## One-time calibration per broadcast layout

1. Grab one clean gameplay frame from a VOD (e.g. via `capture.py --dry-run`,
   frames are left on disk for inspection).
2. Copy `layouts/owcs-demo.json` (created by the test) to e.g.
   `layouts/owcs-asia-2026.json` and set the pixel rects for: the 10 hero
   slots (`slots_a`/`slots_b`), the HUD `anchor` region, and a `replay`
   marker region. Crop small grayscale reference images for anchor/replay.
3. Build hero templates: `python3 pipeline/detect.py --layout L.json
   --build-templates frame.png` dumps the 10 slot crops; rename clean ones to
   `templates/<hero_id>.png`. ~8 varied frames covers the roster. If team
   color tinting hurts matching, add `<hero_id>.a.png` / `.b.png` variants —
   they're picked up automatically.

## FACEIT matchroom ingest

```bash
python3 pipeline/ingest_faceit.py --room-url "https://www.faceit.com/en/ow2/room/1-c55d6822-7ae7-4c53-b86c-015daa712dd3" --dry-run
python3 pipeline/ingest_faceit.py --room-url "https://www.faceit.com/en/ow2/room/1-c55d6822-7ae7-4c53-b86c-015daa712dd3"
python3 pipeline/export_data.py
```

No FACEIT API key is required for the current skeleton. If a public fetch fails, the script exits safely and still explains what it could not parse.

## Per-match run

```bash
python3 pipeline/capture.py  --layout layouts/owcs-asia-2026.json --match m01
python3 pipeline/detect.py   --layout layouts/owcs-asia-2026.json --match m01
python3 pipeline/map_sync.py --match m01            # prints a verify report
python3 pipeline/export_data.py                     # publishes to the site
```

`capture.py` with no `--match` processes every final match that has a
`vod_url` and no snapshots yet (capped by `--max`, default 2 per run — the
free-CI budget). Wire these four commands into a scheduled GitHub Actions
job for the fully automated loop (Section 5 of the build plan).

## Safety rails (why the data can be trusted)

- Frames are only used if the HUD anchor matches and the replay marker
  doesn't — breaks, casters, and replays never enter the data.
- A frame is only written if all 10 slots beat the confidence threshold
  and no hero repeats within a team; anything else lands in
  `work/<match>/quarantine/` with a JSON of scores for review.
- Map sync refuses to guess: if detected gameplay blocks ≠ maps played,
  the match is flagged and nothing is assigned.
- Export publishes partial FACEIT metadata and uses empty states for maps whose comps are not recorded yet.
- Snapshots are deduped by frame hash; every stage is safe to re-run.

## Testing

```bash
python3 pipeline/test_pipeline_synthetic.py
```

Builds synthetic 720p broadcast frames (unique icon per hero at the layout's
slot positions, HUD anchor, a replay frame, a corrupt frame) and runs the
real code end-to-end: classifier accept/reject → detection accuracy →
quarantine → map-block assignment → export shape the site validates against.

**Tested here:** everything above, plus the demo path
(`init_db.py --with-sample` → `export_data.py` → site-valid `data.js`).
**Not yet tested (needs internet):** actual `yt-dlp` downloads and real
broadcast frames — run `capture.py --dry-run <local.mp4>` on one downloaded
VOD first and tune `min_score`/`match_threshold` from the report.

## Frontend analyst prep notes

`export_data.py` still writes the same `window.OWCS_DATA` object. The Milestone 2 frontend reads the existing fields and computes prep-only summaries in `assets/js/app.js`, so no new pipeline requirement is introduced.

Useful fields for future ingest work:

- `matches[].faceitRoomUrl`
- `matches[].maps[].pickedByTeam`
- `matches[].maps[].pickVeto`
- `matches[].maps[].heroBans`
- `matches[].maps[].replayCode`
- `teamPrepNotes[]`

The prep page is intentionally defensive: missing comps, bans, maps, or replay codes produce readable empty states instead of crashes.

## Manual comp corrections (admin path)

When CV is wrong or hasn't run, comps can be set by hand:
1. Open `admin.html` locally, click together the opener (exactly 5) and any
   swap-ins per match/map/team, and download the generated JSON.
2. Commit it as `corrections/corrections.json`.
3. The pipeline (workflow step or `python3 pipeline/apply_corrections.py`)
   writes them as `source='manual'` snapshots with confidence 1.0, which
   **override** CV data for that map+team at export. CV rows are kept, so
   removing an entry and re-running reverts cleanly. Git history of the
   corrections file is the audit trail. `--dry-run` validates only.

## Milestone 3: FACEIT ingest + validation + missing-comps workflow

### The strict split (never violated)
**FACEIT provides facts.** The tracker provides comps. FACEIT populates match
id, room URL, teams, final score, map order/names/scores, hero bans, replay
codes, pick/veto, and rosters. FACEIT **never** produces hero picks, opener
comps, played heroes, swaps, comp timelines, or pick/win rates. Those come only
from manual corrections (today) or CV / replay review (later). This is why
ingest writes to `teams / players / match_rosters / matches / map_results /
hero_bans / map_veto_events` but never touches `comp_snapshots`.

### Full local workflow
```bash
python3 pipeline/init_db.py --with-sample            # schema + demo data
python3 pipeline/ingest_faceit.py \                  # pull FACEIT facts
    --room-url "https://www.faceit.com/en/ow2/room/<id>"
python3 pipeline/apply_corrections.py                # apply manual comps
python3 pipeline/validate_data.py                    # data-quality report
python3 pipeline/export_data.py                      # write assets/js/data.js
python3 -m http.server 8000                          # view the static site
```

### FACEIT ingest modes
```bash
# From a live room (fetches + caches the raw body):
python3 pipeline/ingest_faceit.py --room-url "https://faceit.com/en/ow2/room/<id>"

# From an explicit id:
python3 pipeline/ingest_faceit.py --match-id 1-<uuid>

# From a local cached body / fixture (no network) — JSON or HTML auto-detected:
python3 pipeline/ingest_faceit.py --from-cache pipeline/fixtures/faceit/room_full.json

# Parse + validate + print, write nothing:
python3 pipeline/ingest_faceit.py --room-url "..." --dry-run
```
Raw responses are cached under `data/raw/faceit/` and recorded in the
`faceit_raw_cache` table, so re-runs and parser work don't re-hit FACEIT.
Ingest is idempotent per match and reuses existing `map_result` ids, so any
tracker comps already attached to a map survive re-ingest.

### Why FACEIT doesn't create hero picks
FACEIT matchrooms are client-rendered and, even via the Data API, report match
facts — not which five heroes each team fielded moment to moment. Inferring
comps from FACEIT would fabricate data. So a freshly-ingested map is
`tracker.detected = false`: fully useful for prep (map, score, bans, replay
code) but honest that the comp is not yet known.

### Finding and filling missing comps
1. Open `prep.html` → **Missing comps** queue. It lists every map that has
   FACEIT facts, no comp yet, and a replay code or room link.
2. Copy the replay code and watch that map in Overwatch
   (Main Menu → Watch → Replays → enter code).
3. Open `admin.html`, pick the match/map/team, click the 5 opener heroes
   (and any swaps), export `corrections.json`, commit it.
4. `apply_corrections.py` writes them as `source='manual'` snapshots that
   override any CV data without deleting it; `export_data.py` republishes.

### corrections/corrections.json
A committed JSON list; git history is the audit trail. Each entry:
`{match, mapOrder, team, openerComp[5], swaps[], note?}`. Validation rejects
wrong-length openers, unknown heroes, and unknown match/map/team while still
applying the valid entries. `apply_corrections.py --dry-run` validates only.

### Reading validation warnings
`validate_data.py` warns (non-fatal) vs errors (fatal). Warnings are expected —
e.g. `replay_available_no_comp` is literally the missing-comps queue, and
`score_vs_map_winners` flags a match whose FACEIT final score doesn't match its
per-map winners. Errors mean broken references (unknown hero/map/team in a
comp, roster, or ban) and make the script exit non-zero. Run it before export.

### Fixtures & tests
FACEIT-like fixtures live in `pipeline/fixtures/faceit/` (source assets, safe
from `data/` resets). Run everything:
```bash
python3 pipeline/test_faceit_parser.py      # parser: extraction + malformed
python3 pipeline/test_ingest_faceit.py      # ingest: idempotent, preserves comps
python3 pipeline/test_corrections.py        # manual override without deleting CV
python3 pipeline/test_pipeline_synthetic.py # CV pipeline end-to-end
python3 pipeline/validate_data.py           # data quality
```

## Automation workflow (the $0 pipeline)

The whole pipeline runs from one command, locally or in GitHub Actions:
```bash
python3 pipeline/run_batch.py
```
Steps, in order: `init_db` → `ingest_faceit_batch` → `apply_corrections` →
`validate_data` → `export_data`. Each is skippable; one failing step is logged
but doesn't corrupt the others. Useful flags:
`--no-sample` (CI: real data only), `--limit N`, `--offline` (cached FACEIT
bodies only), `--skip-ingest`, `--strict-validate`, `--allow-empty`.

### Where to add FACEIT room URLs
Edit `data/sources/faceit_rooms.json`:
```json
{ "rooms": [
    { "url": "https://www.faceit.com/en/ow2/room/<id>",
      "region": "NA", "stage": "Stage 2", "notes": "OWCS matchroom" }
] }
```
Only `url` is required. Duplicate URLs and already-ingested match ids are
skipped. Run `python3 pipeline/ingest_faceit_batch.py` to ingest all rooms
(`--dry-run`, `--limit N`, `--offline` supported). This writes FACEIT facts
only — hero comps still come from corrections/CV.

### Where to add manual corrections
Build them in `admin.html`, save as `corrections/corrections.json`, commit.
`apply_corrections.py` writes them as `source='manual'` snapshots that override
CV without deleting it. See the "Manual comp corrections" section above.

### How GitHub Actions updates data.js
`.github/workflows/update-data.yml` runs on: manual dispatch, push to `main`
that touches `data/sources/faceit_rooms.json` / `corrections/` / `pipeline/`,
and a schedule every 6 hours. It checks out the repo, installs Python +
opencv, runs `pipeline/run_batch.py --no-sample`, sanity-checks `app.js` if
node is present, and commits `assets/js/data.js` (and the DB) only if changed —
which redeploys the static site. No secrets, no API key, no backend, no paid
service (public-repo Actions minutes only). An empty pipeline result never
overwrites a populated `data.js` (export is guarded; use `--allow-empty` to
force).

### Reading validation warnings
`validate_data.py` exits **0 for warnings, nonzero only for hard errors**, so
expected warnings never block deployment. Warnings are informational:
`replay_available_no_comp` literally *is* the missing-comps queue;
`replay_code_no_score` and `score_vs_map_winners` flag imperfect FACEIT data.
Errors mean broken references (unknown hero/map/team in a comp, roster, ban)
and should be fixed before shipping.

### Run the full pipeline locally
```bash
python3 pipeline/run_batch.py                 # sample data + configured rooms
python3 -m http.server 8000                   # view at localhost:8000
# or step by step:
python3 pipeline/init_db.py --with-sample
python3 pipeline/ingest_faceit_batch.py
python3 pipeline/apply_corrections.py
python3 pipeline/validate_data.py
python3 pipeline/export_data.py
```

### Note on the CV loop
The camera-vision capture→detect→map_sync loop now lives in
`pipeline/run_cv_batch.py` (unchanged), kept separate because it needs
calibrated layout/template assets. `run_batch.py` is the data orchestrator and
does not run CV; wire CV in once templates exist.

## Real FACEIT room validation — what public FACEIT actually exposes

We validated the parser against the FACEIT Data API v4 match shape (the JSON
FACEIT's own front-end fetches). Fixtures live at
`pipeline/fixtures/faceit/real_room_c55d6822.json` (API shape) and
`real_room_c55d6822_shell.html` (the public webpage shell). Player names in the
fixture are placeholders — real facts must come from a real cached response.

### What the public matchroom URL returns
`https://www.faceit.com/.../room/<id>` is a **client-rendered React shell**.
The HTML contains no match data — the app fetches it from the FACEIT API after
load. So the HTML path yields only the match id parsed from the URL; it never
fabricates teams/maps. (In this sandbox the fetch is blocked entirely —
`x-deny-reason: host_not_allowed` — so live validation must be run on a
networked machine; see below.)

### What the FACEIT API JSON exposes (and the parser extracts)
From the Data API match object the parser reads:
- **match id** (`match_id`)
- **teams** — names + faction ids (`teams.faction1/2.name`, `.faction_id`)
- **final score** (`results.score.faction1/2`)
- **rosters** — 5v5 nicknames + player ids (`teams.*.roster[]`)
- **map order, names, per-map scores, per-map winners** — from
  `detailed_results[]` merged with `voting.map.entities`/`pick` for the names
  (names are NOT in `detailed_results`; they live in the voting block)
- **map modes** — labeled locally from the map name

### What public FACEIT does NOT expose for OW2 (stays tracker-generated)
- **replay codes** — not present in the public API. Parsed as `None`.
- **hero bans** — not present. Parsed as `[]`.
- **pick/veto per map** — `voting` gives the picked map order but not a clean
  per-map "picked by team A" attribution, so `pickedBy`/`vetoAction` stay null
  unless a payload provides them explicitly.
- **hero picks / comps** — never provided, never inferred. Every ingested map
  is `tracker.detected = false` until a comp is added via corrections or CV.

### Are replay codes visible in public cached data?
**No.** Neither the public HTML shell nor the public Data API match object
contains OW2 replay codes. Replay codes therefore remain a tracker/manual field
(entered via `admin.html`), not a FACEIT-sourced one, until a source that
carries them is found.

### How to run the real validation (networked machine)
```bash
python3 pipeline/ingest_faceit.py \
  --room-url "https://www.faceit.com/en/ow2/room/1-c55d6822-7ae7-4c53-b86c-015daa712dd3"
ls data/raw/faceit/            # inspect the cached *.body
```
If the cached body is a JS shell (expected), fetch the API object instead:
```bash
# no key required for public matches:
curl -s "https://open.faceit.com/data/v4/matches/1-c55d6822-7ae7-4c53-b86c-015daa712dd3" \
  > data/raw/faceit/<id>.json     # (the open Data API needs a free app token;
                                  #  the internal api.faceit.com match endpoint
                                  #  is used by the site and is unauthenticated)
```
Then sanitize player names and save as
`pipeline/fixtures/faceit/real_room_<shortid>.json` and re-run the parser tests.

### Exact limitations
- No network in the build sandbox, so the fixture matches the **documented**
  Data API shape, not a byte-for-byte capture. Field names align with FACEIT
  v4; a real capture may add keys (harmless — the parser ignores unknowns) but
  is unlikely to move the ones we read.
- Replay codes and hero bans need a non-public source (client match JSON with
  `detailed_results` extensions, or manual entry).

## Milestone 4: review workbench + manual facts

### Two manual paths — keep them straight
- **Manual comp corrections** → `corrections/corrections.json`, applied by
  `apply_corrections.py`, written as `source='manual'` **comp snapshots**.
  This is the only manual way to set hero picks. Authored in `admin.html`.
- **Manual match facts** → `corrections/match_facts.json`, applied by
  `apply_match_facts.py`, written as `source='manual_facts'` on
  `map_results` + `hero_bans`. Fills replay codes / bans / pick-veto that the
  public FACEIT API omits. **Never creates comps.** Authored in `fact-admin.html`.

### Source-list management
```bash
python3 pipeline/manage_sources.py list
python3 pipeline/manage_sources.py add --url "<room url>" --region NA --stage "Stage 2"
python3 pipeline/manage_sources.py remove --match-id "1-..."
python3 pipeline/manage_sources.py dedupe
python3 pipeline/manage_sources.py validate
```
Safe edits to `data/sources/faceit_rooms.json` — validates URL shape, dedupes
by resolved match id, preserves formatting.

### Full pipeline order (run_batch.py)
init → ingest_faceit_batch → **apply_match_facts** → apply_corrections →
validate_data → export_data. Manual facts apply *after* FACEIT ingest so they
fill missing fields, *before* corrections/validate/export.

### See it working right now (offline demo)
```bash
python3 pipeline/seed_demo.py          # ingests bundled fixtures as FACEIT rooms,
                                       # adds demo facts + one comp correction
python3 -m http.server 8000            # open prep.html
```
This populates the Review Progress dashboard and the Missing Comps workbench
with automated FACEIT matches — one map half-reviewed — so every M4 surface has
something to show without live FACEIT.

### Admin tools (static, never write to disk)
- `admin.html` — comp corrections. Prefills from
  `admin.html?match=<id>&mapOrder=<n>&team=<teamId>`.
- `fact-admin.html` — manual facts (distinct blue theme). Prefills from
  `fact-admin.html?match=<id>&mapOrder=<n>`.
Both generate JSON you paste/commit into `corrections/`. The Missing Comps
workbench links straight to them with the right params.

### What still needs live FACEIT confirmation later
Field names for replay codes / bans / pick-veto in a real match payload (the
public API omits them, so manual facts cover that gap today). Everything else
(ids, teams, scores, map order/names, rosters) is validated against the
documented Data API shape.
