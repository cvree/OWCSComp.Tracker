# Running the tracker locally

The published site is a static file: it can show everything that has been
reviewed and published, but it has no machine behind it, so it cannot
download or read a video. To actually process a broadcast, run the tracker
on your own machine.

```
python pipeline/serve.py
```

It prints three addresses. They are the same pages as the published site —
the difference is that here the buttons really do the work.

| Where | What it does |
|---|---|
| `/submit.html` | Paste a broadcast link. It is classified as you type, known broadcasts autofill the rest, and **Start processing** really starts it. |
| `/game.html?id=…` | The game's own page. While it is running this is where the live log streams, with a **Stop processing** control. When it is stuck, the blocker is named in plain language with the exact command that fixes it. |
| `/review.html` | The human gate. Approve, correct or flag each detection; with a tracker connected, approvals and rejections are written straight to the record with `manual_override` set, so a later detector pass cannot silently undo them. |
| `/tools.html` | Everything technical: broadcast sources (each with its run command), the processing-run archive with its report/layout/crop links, calibration health, download authentication, storage, and publishing. |

Every page detects whether a tracker is listening and says which mode it is
in. Served statically, the same pages explain that nothing is behind them
and hand you the command to run instead of pretending to work.

The calibration loop without a terminal:

1. `/submit.html` — start a short window first (`Advanced options` → a
   couple of minutes) to prove the pipe works.
2. `/tools.html#runs` — open that run's **report** for its layout-debug and
   crop pages.
3. Boxes off? Either edit `layouts/<source>.json` in a text editor, or use
   `/calibrate.html`, which measures the slots from your own frames in the
   browser.
4. Re-run and refresh. Repeat.

## What serve.py is (and is not)

- Python **stdlib only**, one file, binds **127.0.0.1** by default.
- It only executes this repo's own pipeline scripts (`sys.executable`,
  argv lists, no shell), **one job at a time** (a second start → 409).
- It is a **local tool**, not a hosted backend. The published site stays
  100% static; every page degrades honestly without the API.
- `--host 0.0.0.0 --port N` only if you deliberately want LAN access.

## API (for reference)

```
GET  /api/ping                  {ok, running}
GET  /api/sources               saved YouTube sources
GET  /api/preflight             read-only readiness snapshot
GET  /api/status?since=N        job state + incremental log lines
POST /api/run                   {source|local, start, end, every, fast,
                                 force, withAudio, height}
POST /api/cancel                {}
POST /api/evidence              {run}
POST /api/test                  {}
```

The desktop application adds a second surface under `/api/desktop/*`
(overview, health, queue, review inbox, review decisions, intake, publish)
— see `desktop/owcs_desktop/webapi.py`. The product's own API client is
`assets/js/app/api.js`, which is the only place either surface is called
from.
