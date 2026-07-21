# Video → comps: the computer-vision layer

This is the part of the tracker that produces the one primitive nobody else
exposes: the actual 5-hero compositions each pro team ran, per map, over
time. FACEIT gives us the *structure* of a match; the video pipeline reads
the *comps* off the broadcast and pairs them back onto that structure.

Everything here is deterministic, local, and free: `yt-dlp`, `ffmpeg`,
OpenCV template matching, SQLite, and a static export. No paid vision API,
no always-on server, no backend. A large language model may help you build
templates or eyeball failures during development, but it is never the
production frame classifier.

## The four stages

The layer is split into four small modules so each stage is independently
runnable and testable. They live in `pipeline/` and are driven by
`data/sources/video_sources.json`.

1. `video_ingest.py` — resolves a source to raw sampled frames in
   `work/{match}/frames_raw/`. A source can be a committed fixture folder
   (offline/demo/CI), a local `.mp4` (dev), or a `vodUrl` fetched with
   `yt-dlp` (production). Only the last two touch `yt-dlp`/`ffmpeg` or the
   network. Frames are named by stream offset, e.g. `000600.png` = ten
   minutes in.
2. `frame_filter.py` — keeps only *live gameplay* frames (HUD anchor
   present, replay marker absent) using `capture.is_gameplay`. Kept frames
   go to `work/{match}/frames/`; rejected ones (break screens, caster cams,
   replays) are set aside in `work/{match}/frames_rejected/` with a reason,
   never silently dropped.
3. `hero_overlay_detect.py` — crops the ten hero-portrait slots (five per
   team) from the layout and template-matches each against the hero
   template set. It returns a structured per-frame reading with per-slot and
   per-team confidence, and routes any frame it cannot read cleanly to a
   quarantine folder with a JSON sidecar. It does not write the database.
4. `video_to_snapshots.py` — persists accepted readings into
   `comp_snapshots` (always `source='cv'`) plus `snapshot_heroes`, one
   snapshot per team per frame, deduplicated by frame hash so reruns add
   nothing.

`map_sync.py` then assigns each snapshot to the correct `map_result`, and
`export_data.py` publishes `assets/js/data.js`.

`video_pipeline.py` chains all of the above from `video_sources.json`.
`python3 pipeline/video_pipeline.py --demo` runs the whole thing offline
against committed fixtures in an isolated DB — the fastest way to see the
layer work.

## How FACEIT facts pair with VOD-detected comps

The two data sources are kept strictly separate and meet only at the map
level.

FACEIT (via the FACEIT ingest, `source='faceit'`) is the source of truth for
match structure: the match itself, its teams and rosters, the map order, map
names, map scores, winners, and — where the room exposes them — pick/veto and
bans. FACEIT is **never** used to infer a single hero played. A
`video_sources.json` entry never creates a match either; if the match it
names is not already in the DB, the source is skipped.

The video pipeline is the source of comps (`source='cv''`): opener comps,
played heroes, swaps, timelines, confidence, and stream offsets.

Pairing happens in two steps and refuses to guess:

- Frame → match. Each source entry carries the internal `match` id (and
  optionally `faceitMatchId`), so every snapshot is written against a known
  FACEIT match from the start.
- Snapshot → map. `map_sync` groups snapshot offsets into gameplay *blocks*
  (a gap longer than `gap_factor × sample_interval` starts a new block,
  because inter-map breaks are minutes long while in-map gaps are one
  interval) and matches those blocks **in order** to the match's known
  `map_results`. If the number of detected blocks does not equal the number
  of maps played, the match is flagged and nothing is assigned — you re-run
  capture with a denser interval or check quarantined frames rather than
  letting the tool invent an alignment. An optional `streamOffsetSeconds`
  lets you record a known offset between the VOD clock and the first map.

The result: FACEIT owns `faceit.*` on each exported map, the video pipeline
owns `tracker.*`, and the two are joined by `map_order`.

## YouTube VOD ingestion (long streams)

OWCS broadcasts are single multi-hour YouTube VODs, so they are sampled by
timestamp rather than downloaded whole. Add one to `video_sources.json` with
`platform: "youtube"` and an `id`, `title`, `url`, `date`, `region`, `notes`,
`enabled`, `sampleIntervalSeconds`, and `layout`. This step is ingestion
only — it produces frames, not comps, and does not touch FACEIT.

`video_ingest.py --source <id>` reads metadata with `yt-dlp` and, for each
sample offset, downloads only a ~2-second section (`yt-dlp
--download-sections`) and extracts one frame. A six-hour VOD is therefore
never fully downloaded — at a 300s interval it fetches ~72 short clips
totalling a couple of minutes of video. Frames land in
`work/vods/{id}/frames_raw/`, named by offset.

```
# preview: title, duration, and how many frames the plan will produce
python3 pipeline/video_ingest.py --source owcs-afcxdimpsle --dry-run

# sample a 15-minute window at one frame per minute
python3 pipeline/video_ingest.py --source owcs-afcxdimpsle \
      --start 1:30:00 --end 1:45:00 --sample-interval 60

# whole VOD at the source's default interval, capped for a first pass
python3 pipeline/video_ingest.py --source owcs-afcxdimpsle --max-frames 60
```

`--start`/`--end` accept seconds or `H:MM:SS`; `--end` defaults to (and is
clamped to) the VOD duration; `--sample-interval` overrides the source value;
`--max-frames` caps a run; `--height` bounds the fetched resolution. The real
CLI needs `yt-dlp` and `ffmpeg` on PATH (`pip install yt-dlp`); the dry-run
and the tests run offline by reading a saved metadata blob via `--probe-file`
(see `pipeline/fixtures/video/vod_meta_sample.json`).

These frames later feed the same `frame_filter` → `hero_overlay_detect` →
`video_to_snapshots` chain once the VOD is paired to a FACEIT match and a
broadcast layout is calibrated. That pairing is a separate, later step.

## Provenance and the manual override

Every comp row carries a `source`, and export honors a strict precedence:

- `manual` — hand corrections from `corrections/corrections.json`.
- `cv` — written by this layer.
- `replay` — reserved for future replay/VOD review tooling.
- `sample` — demo/seed data.

Manual always wins. `export_data.comp_info_for_map` prefers `manual`
snapshots over everything else, so a single correction fixes a bad read. The
correction path is additive and reversible: it never deletes `cv` rows, so
removing the correction and re-exporting brings the `cv` comp straight back.
The corrections file is committed, so git history is the audit trail.

## Calibrating a broadcast

Each broadcast package needs a layout (`layouts/*.json`) describing, in the
broadcast's own pixel coordinates: the HUD `anchor` region + template used to
recognize live gameplay, an optional `replay` marker, the ten hero slot
rectangles (`slots_a` / `slots_b`), a `match_threshold` for per-slot
confidence, and optionally `min_overall_confidence` and a `templates_dir`.

Build the hero template set once from a few clean frames:

```
python3 pipeline/detect.py --layout layouts/your.json --build-templates frame.png
# rename the clean crops in templates/_candidates/ to templates/<hero_id>.png
```

Roughly eight clean frames cover a whole roster. Matching runs on luminance,
which is robust to the red/blue team tint; if a season tints too hard, add
`templates/<hero>.a.png` / `.b.png` variants and they are used automatically.

## Sampling strategy

Start coarse and tighten: every 2–5 minutes for an MVP, 30–60 seconds during
live gameplay once the layout is trusted, and eventually adaptive sampling
around map starts, fights, and scoreboard changes. Opener comps come from the
earliest snapshot on a map; played heroes are the union across the map; swaps
are heroes seen after the opener that were not in it.

## Cost and hosting

Nothing here changes the `$0` posture: the whole layer is batch scripts over
free, open-source tools, writing a static `data.js` that GitHub Pages or
Cloudflare Pages serves for free. The heavy `yt-dlp` download is deleted
right after frames are extracted, and only derived stats — never raw
screenshots — are stored and published.
