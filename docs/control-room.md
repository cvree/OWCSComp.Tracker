# Control room — run, test, and debug from the website

Start the local control-room server instead of `python -m http.server`:

```
python pipeline/serve.py
```

Then open **http://localhost:8000/run.html**. Everything the terminal did is
now clickable:

| In the browser | What it does |
|---|---|
| Run page → **Start run** | Runs `run_owcs_auto.py` with your source/local file, window, `--fast`, height, force, audio — with the full live log streaming on the page (heartbeat included) |
| Run page → **Run all tests** | Runs every `pipeline/test_*.py` suite in order; stops at the first failing suite and shows its output |
| Runs page → **regenerate evidence** | Re-runs `build_layout_debug.py` + `build_crop_report.py` for that run from its already-extracted frames — the layout-calibration loop with **no re-download** |
| Finished-job links | Jump straight to the run report / runs.html |

The calibrate loop without a terminal:
1. Run page → start a real window (or `--fast` first to prove the pipe).
2. Runs page → the run → **layout debug** / **crop report**.
3. Boxes off? Edit `layouts/owcs_youtube_2026.json` in any text editor.
4. Runs page → **regenerate evidence** → refresh the pages. Repeat.

## What serve.py is (and is not)

- Python **stdlib only**, ~1 file, binds **127.0.0.1** by default.
- It only executes this repo's own pipeline scripts (`sys.executable`,
  argv lists, no shell), **one job at a time** (second start → 409).
- It is a **local dev tool**, not a hosted backend: the published site is
  still 100% static. Every page detects the API and falls back to showing
  the copy-pasteable terminal commands when served statically (GitHub/
  Cloudflare Pages, or plain `python -m http.server`).
- `--host 0.0.0.0 --port N` only if you deliberately want LAN access.

## API (for reference)

```
GET  /api/ping                  {ok, running}
GET  /api/sources               saved YouTube sources
GET  /api/status?since=N        job state + incremental log lines
POST /api/run                   {source|local, start, end, every, fast,
                                 force, withAudio, height}
POST /api/evidence              {run}
POST /api/test                  {}
```
