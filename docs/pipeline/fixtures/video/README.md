# Video pipeline demo fixtures

Tiny synthetic assets that let the whole video CV layer run offline — no
real footage, no `yt-dlp`/`ffmpeg`, no network. Used by
`pipeline/test_video_pipeline.py` and by `video_pipeline.py --demo`.

- `demo_match/frames/*.png` — 8 synthetic 1280×720 "broadcast" frames:
  three gameplay frames on map 1 (with a genji→sojourn swap at `000600`),
  a break screen (`000900`), a replay frame (`001200`), a blank/bad frame
  (`001500`), and two gameplay frames on map 2 (`002100`, `002400`). The
  background is flat and a small `t=` tag keeps each frame byte-distinct so
  frame-hash dedup has real, different frames.
- `templates/<hero>.png` — the matching hero icon set, generated from the
  same deterministic icons the frames use, so template matching resolves
  exactly. Kept separate from the repo's real `templates/` folder.
- `anchor.png` / `replay.png` — the HUD-anchor and replay-marker crops the
  gameplay filter checks.
- `demo-layout.json` — layout pointing at the fixtures above.

Regenerate everything with:

```
python3 pipeline/fixtures/video/_gen_demo_fixtures.py
```

These are fixtures, not real broadcast crops. Calibrate real layouts and
templates from actual OWCS frames — see `docs/video-pipeline.md`.
