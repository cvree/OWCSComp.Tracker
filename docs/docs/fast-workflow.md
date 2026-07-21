# Fast VOD workflow — smoke test, normal run, calibration run

Downloads are **video-only by default** (no audio stream, no merge step) —
frame extraction never needs audio, and skipping it makes the yt-dlp step
much smaller and faster. Pass `--with-audio` only if you want a clip you can
watch with sound.

While yt-dlp/ffmpeg run you get live progress lines, and if nothing is
printed for ~12s a heartbeat appears
(`[yt-dlp] still downloading... elapsed 25s (no output for 13s)`) — the
pipeline is never silently "stuck".

## 1. Fastest smoke test (~seconds, proves the pipeline end to end)

```
python pipeline/run_owcs_auto.py --source owcs-afcxdimpsle --start 1:30:00 --end 1:30:30 --every 10 --fast
```

`--fast` caps the window at 30s, drops the clip to 480p, and samples every
10s (3 frames). Detection will be **skipped with an explained reason**
(480p frames vs a 1080p layout) — that is expected: this mode only proves
capture → frames → report → website work. Open
`http://localhost:8000/runs.html` afterwards.

## 2. Normal recommended run (calibration-quality frames, still small)

```
python pipeline/run_owcs_auto.py --source owcs-afcxdimpsle --start 1:30:00 --end 1:32:00 --every 30
```

Height defaults to the layout's `frame_height` (1080p for
`owcs_youtube_2026.json`) so frames match the layout. For a smaller/faster
download when you don't need detection, add `--height 480` or `--height 720`
— the detect step will tell you exactly why it skipped.

## 3. Longer calibration window (more varied frames, one cached clip)

```
python pipeline/run_owcs_auto.py --source owcs-afcxdimpsle --start 1:30:00 --end 1:35:00 --every 30
```

The clip lands in `work/clips/<id>_<start>_<end>.mp4` and is **reused** on
every re-run of the same window (layout tweaking never re-downloads).
Cache states are always announced: `REUSING` a complete clip, `RESUME` for a
partial `.part` file, `DELETING` with `--force` / `--force-clip`.

## Standalone clip download

```
python pipeline/download_vod_clip.py --source owcs-afcxdimpsle --start 1:30:00 --end 1:32:00 --out work/clips/test.webm --video-only
python pipeline/download_vod_clip.py --source owcs-afcxdimpsle --start 1:30:00 --end 1:32:00 --with-audio   # only if you need sound
```

`--video-only` is the default; the flag exists for explicitness. The clip
container may be webm or mp4 depending on what YouTube serves at that height
— ffmpeg sniffs content, not extension, so either works for frame extraction
(if yt-dlp appends the real extension, the pipeline renames the file back to
the expected path automatically).

## Why a cached clip instead of direct per-frame stream extraction

A "no saved clip" mode exists (`--clip-mode per-timestamp` in
`extract_calibration_frames.py` / `run_capture_trial.py`): it asks
yt-dlp/ffmpeg to remote-seek to each sample offset directly. In practice it
proved **unreliable deep into long VODs** ("could not seek to position
5400.0") and re-downloads on every re-run, so the cached local-window clip
stays the safe default for `run_owcs_auto.py`. Use per-timestamp only for
one-off grabs near the start of a VOD.
