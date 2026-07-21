# Plan — ingesting ObsSojourn (player-POV) match videos

ObsSojourn-style POV uploads are a **better** ingestion source than
broadcasts, and they change the model in three specific ways this plan
handles: map segmentation is *given*, there's almost no filler, and one match
often has *several* POV videos that must not double-count.

Reference: *Crazy Raccoon vs ZETA Division | OWCS Korea Stage 2 Grand Final*
(youtube `is7eHd0nf84`).

## Why the existing engine already fits
Both the observer overhead shot and the first-person shots keep the **same
in-client top scoreboard bar** — Crazy Raccoon's five hero chips on the left,
ZETA's five on the right. That is the exact element the tracker already reads;
the ten-slot `slots_a` / `slots_b` layout + `detect.read_slot` approach
applies unchanged. What differs is *packaging*, and that's what this plan
adapts.

## The four differences, and how each is handled

### 1. Map boundaries come from the description (no detection needed)
The description lists chapter timestamps:
`Antarctic Peninsula 0:00 · New Junk City 13:20 · King's Row 29:00 · Circuit
Royal 41:15 · Colosseo 58:50 · Neon Junction 1:09:20`.

`pipeline/obssojourn_source.py::parse_description` turns a pasted description
into the match record and one window per map (`start` → next `start`), plus
the teams, event, date, and the POV player's heroes. Map names resolve to
`game_maps` ids; a new season map (here **Neon Junction**) is **flagged in
`unknownMaps`**, never silently dropped — add it to `game_maps`, re-parse.
Emblem round-segmentation still runs *within* each map window as today.

### 2. One calibration serves every video from the channel
The POV recording format is consistent across the channel, so the top-bar
layout is calibrated **once** (same `calibrate_source.py` pass as a
broadcast: HUD probe, slot rects, reject markers for the scoreboard-TAB and
hero-select screens) and reused for all their uploads. This is the standard
per-format calibration — it is the one step that still needs real frames.

### 3. Almost no filler — the phase gate does more of the work
No desk, no caster cuts, no highlight package. The `recapture_planner` phases
(setup, post-start settle, post-round grace, no-hud) plus the scoreboard-TAB /
hero-select reject markers cover what non-combat there is. Net: a higher
share of frames are countable than a broadcast.

### 4. Multiple POVs of one match — dedup + cross-confirm (the key rule)
A tank POV and a support POV of the same game each cover all six maps and show
the same comps. The rule: **one match, several video sources.**

- `match_signature(parsed)` = sorted(teamA, teamB) + date — **POV-independent**
  and order-independent, so both videos resolve to the same match id. Ingest
  each POV as its own `ingest_run` under that one match.
- `comp_merge_key(match, map, team, round, heroes)` collapses identical comp
  reads across POVs (slot-order-independent). Two POVs agreeing on a comp
  becomes **one** comp with provenance from both runs and *higher* confidence
  — two independent cameras confirming the same HUD, not a duplicate.
- The POV player's own hero (from `heroesPlayed`) is a free ground-truth slot
  and tells us which team's side the POV is on (`pov_role`).

## The per-video workflow (what runs locally, in order)
1. **Parse**: `python pipeline/obssojourn_source.py --file desc.txt` → match,
   maps, windows, unknown-map flags. Add any unknown map to `game_maps`.
2. **Create/upsert the match** by `match_signature` (idempotent — a second
   POV of the same match attaches, never duplicates).
3. **Calibrate once** for the POV format (first video only), then reuse.
4. **Per map window**: `ingest_map.py --clip <video> --start <ts> --end <ts>`
   with the ObsSojourn layout. The role solver, phase gate and match-confirm
   (teams/bans) from the vision upgrade all run here.
5. **Merge across POVs** by `comp_merge_key`; cross-agreeing comps gain
   confidence, disagreements go to review.
6. **Export + push** → the live site picks up the new match automatically.

## What's built now vs. next
- **Built + tested (offline):** the description parser, map-name resolution
  with new-map flagging, the POV-independent match signature, the cross-POV
  comp-merge key, and the POV-role hint (`test_obssojourn_source.py`).
- **Next (needs the video, runs on your machine):** the one-time POV-format
  calibration + template harvest, then per-map ingest through the upgraded
  engine, and the cross-POV merge into the DB. owtv.gg (linked in the
  description) is a second confirmation source alongside FACEIT.
