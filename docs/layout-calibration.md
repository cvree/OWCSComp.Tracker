# Calibrating the OWCS broadcast overlay

Goal: get the CV pipeline to read hero comps off the OWCS YouTube broadcast.
That needs two hand-tuned assets per broadcast package — a **layout** (where
the HUD elements sit) and a **hero template set** (one clean crop per hero).
This is a one-time, human-in-the-loop step; nothing here classifies the full
VOD or touches FACEIT.

Everything runs on a handful of frames, never the whole 6-hour stream.

## The five steps

### 1. Sample 10–20 frames from a gameplay window

Pick a stretch of clean live gameplay (not menus, replays, or casters) and
pull a few frames a few seconds apart.

```
python3 pipeline/extract_calibration_frames.py --source owcs-afcxdimpsle \
      --start 1:30:00 --end 1:33:00 --every 10 --format png
# -> reports/calibration_frames/owcs-afcxdimpsle/{offset}.png
```

`--start`/`--end` take seconds or `H:MM:SS`. You can also point at a URL
(`--url ...`) or a local file (`--local match.mp4`). For youtube, only a ~2s
section per frame is downloaded. Preview first with `video_ingest.py --source
owcs-afcxdimpsle --dry-run` to see the VOD's duration.

### 2. Adjust the layout rectangles

Start from `layouts/owcs_youtube_2026.json` — every rectangle in it is a
placeholder guess with a `_comments` block explaining what to change. A
rectangle is `[x, y, w, h]` in the frame's pixels. Extract your frames at the
same resolution the layout's `frame_width`/`frame_height` assume (OWCS VODs
are usually 1920×1080).

You are aligning: the five team-A hero slots (`slots_a`), the five team-B
slots (`slots_b`), the gameplay `anchor` (a HUD element on-screen only during
play), the `replay` marker, and the optional `score_map` plate.

### 3. Generate debug images and look

```
python3 pipeline/build_layout_debug.py \
      --layout layouts/owcs_youtube_2026.json \
      --frames-dir reports/calibration_frames/owcs-afcxdimpsle
# -> reports/layout_debug/{offset}_debug.png
```

Open the annotated frames. Green = A slots, blue = B slots, yellow = anchor,
red = replay, magenta = score/map. Nudge the numbers in the layout JSON,
re-run this command, and repeat until each box hugs its element. Tighten the
slot boxes to the portrait art so template matching stays clean.

### 4. Crop hero candidates

Once the boxes line up, crop the slots from every calibration frame:

```
python3 pipeline/build_hero_templates.py \
      --layout layouts/owcs_youtube_2026.json \
      --frames-dir reports/calibration_frames/owcs-afcxdimpsle
# -> templates/candidates/{offset}_{A|B}{slot}.png
# -> reports/template_candidates.html   (contact sheet: crop, time, side, slot)
```

Open `reports/template_candidates.html` to see every crop with its timestamp,
side (A/B), and slot (1–5).

### 5. Rename the good crops into the template set

From the contact sheet, pick the cleanest crop of each hero and copy it to
`templates/<hero_id>.png`:

```
cp templates/candidates/005400_A1.png templates/tracer.png
cp templates/candidates/005400_A2.png templates/winston.png
# ...one clean crop per hero on the map
```

Hero ids are the lowercase slugs already in the DB (`tracer`, `winston`,
`kiriko`, `dva`, ...). Only files directly in `templates/` are used by the
detector; the `candidates/` subfolder is ignored, so it never pollutes the
set. Matching runs on luminance, so one clean crop per hero usually suffices;
if a season tints team icons hard, add `templates/<hero>.a.png` /
`.b.png` variants and both are used automatically.

While you're here, build the anchor and replay templates the layout points
at, by cropping those regions from a clean frame (any image editor, or
`detect.py`'s crop helper), and save them to the paths in the layout
(`layouts/owcs_youtube_2026-anchor.png`, `-replay.png`). Tune `min_score` if
gameplay frames are wrongly rejected or replays wrongly accepted.

## When calibration is done

With the layout aligned and `templates/*.png` populated, the same VOD frames
flow through the existing chain — `frame_filter` → `hero_overlay_detect` →
`video_to_snapshots` (source='cv') → `map_sync` — and comps appear on the
site. Pairing a VOD to its FACEIT match/map structure is the next step after
that. Until then this stays calibration only: no full-VOD classification, no
comps inferred from FACEIT.

## Tips

- Sample frames from a mid-round moment when all ten portraits are visible.
- If the anchor also shows on replays, add the `replay` marker so replays are
  filtered out; if there's no persistent replay marker, delete the `replay`
  block and rely on the anchor alone.
- Re-check the layout whenever the broadcast package changes (new season,
  new overlay) — rectangles drift between production designs.
