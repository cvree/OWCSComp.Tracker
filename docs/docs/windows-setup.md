# Windows setup — Python, ffmpeg, yt-dlp

Everything here is free. You need three tools on PATH.

## 1. Python (3.10+)

Install from https://www.python.org/downloads/ and check
"Add python.exe to PATH" in the installer. Verify:

```powershell
python --version
```

## 2. ffmpeg

Download a Windows build (e.g. from https://www.gyan.dev/ffmpeg/builds/),
extract it to `C:\ffmpeg`, and add `C:\ffmpeg\bin` to your PATH
(Settings → System → About → Advanced system settings → Environment
Variables → Path → New → `C:\ffmpeg\bin`). Open a NEW terminal, then verify:

```powershell
ffmpeg -version
```

## 3. yt-dlp (simple method)

Download `yt-dlp.exe` from the GitHub releases page:

https://github.com/yt-dlp/yt-dlp/releases/latest

Put the file in `C:\ffmpeg\bin` — since that folder is already on PATH from
step 2, no extra PATH changes are needed. Verify in a new terminal:

```powershell
yt-dlp --version
```

No YouTube API key, cookies, or login is required for OWCS public VODs.

## Full workflow (discovery → frames → site)

```powershell
# 1. List recent OWCS VODs (metadata only, downloads nothing)
python pipeline/discover_owcs_vods.py --provider youtube --channel-url "https://www.youtube.com/@ow_esports/streams" --limit 20

# 2. Save entry #1 as a source (prints its slug)
python pipeline/discover_owcs_vods.py --provider youtube --limit 20 --select 1 --write

# 3. Capture trial on that source
python pipeline/run_capture_trial.py --source <slug> --start 1:30:00 --end 1:32:00 --every 30 --clip-mode local-window

# 4. Calibration frames
python pipeline/extract_calibration_frames.py --source <slug> --start 1:30:00 --end 1:35:00 --every 30 --out work/calib_real

# 5. Layout debug images
python pipeline/build_layout_debug.py --layout layouts/owcs_youtube_2026.json --frames-dir work/calib_real --out work/layout_debug

# 6. Export site data, serve, open http://localhost:8000
python pipeline/export_data.py
python -m http.server 8000
```

Saved sources are visible on the site at `sources.html`.

Note: the channel /streams listing is metadata-only, so `date` may show as
`TBD` for some entries — you can edit it later in
`data/sources/video_sources.json`.

## One-command auto pipeline (Phase 1)

```powershell
# Reusable clip (kept in work/clips, skipped if it already exists)
python pipeline/download_vod_clip.py --source owcs-afcxdimpsle --start 1:30:00 --end 1:35:00 --out work/clips/day1_0130_0135.mp4

# Everything in one go (clip is cached + reused across runs)
python pipeline/run_owcs_auto.py --source owcs-afcxdimpsle --start 1:30:00 --end 1:35:00 --every 30

# Same pipeline from your own local MP4 (no network)
python pipeline/run_owcs_auto.py --local work/clips/day1_0130_0135.mp4 --start 0 --end 5:00 --every 30

python -m http.server 8000   # open http://localhost:8000/sources.html
```

Run status + report links appear under "Automatic runs" on sources.html.
Detection runs only when the layout's hero templates are calibrated; until
then it reports skipped/error and the run still finishes.

## 4. (Recommended) a JavaScript runtime for yt-dlp

Newer YouTube formats are scrambled and yt-dlp needs a JS runtime to
unscramble them. Without one you may see:

```
WARNING: No supported JavaScript runtime found
```

and section downloads can stall (no bytes after "Destination: ..."). Install
**Deno** (smallest) or **Node.js**:

```powershell
winget install DenoLand.Deno    # or: winget install OpenJS.NodeJS.LTS
```

Open a new terminal so it's on PATH, then re-run. This is optional but fixes
the most common cause of a hanging clip download.

## If a YouTube clip download stalls

`run_owcs_auto.py` now guards every clip download: if no real byte progress
arrives within the stall window (75s in `--fast`, 180s otherwise) it **kills
yt-dlp and tries a simpler format** automatically, and if every format stalls
the run ends as **timeout** (not a silent hang) with a remedy line. Options:

- add a JS runtime (above) — the usual fix;
- use a **local MP4**: `--local work/clips/yourclip.mp4`;
- pass browser cookies to yt-dlp: `yt-dlp --cookies-from-browser chrome ...`;
- try another VOD/source;
- tune the guard: `--stall-timeout 120`.
