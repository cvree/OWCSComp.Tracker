# Going Live — complete implementation plan

How to turn this repository into a real, working public website, and how to
keep its data flowing automatically. Written to be followed top-to-bottom;
every step is either a one-time click or a command you can copy.

The site is split into two independent halves, and that split is the whole
reason going live is cheap and safe:

| Half | What it is | Where it runs | Cost |
|---|---|---|---|
| **Public site** | Static HTML/CSS/JS + a generated `assets/data/public_data.v1.js` | GitHub Pages (or any static host) | $0 |
| **Data pipeline** | Python CV + FACEIT ingestion that regenerates the data file | GitHub Actions, on demand | $0 |

The public site never talks to a server. It renders entirely from committed
JSON. So "make it live" = *host static files*; "keep it fresh" = *re-run the
pipeline and commit the new data file*. Nothing else is required, ever.

```
 broadcast VOD ─┐
 FACEIT room  ──┤   pipeline (GitHub Actions, manual/opt-in)
                │     init_db → run_batch → corrections → validate
                ▼
        data/owcs.sqlite  ──  export_data.py --public
                                     │
                                     ▼
                  assets/data/public_data.v1.js   (committed)
                                     │  push to main
                                     ▼
                        pages.yml  →  GitHub Pages CDN
                                     │
                                     ▼
        https://cvree.github.io/OWCSComp.Tracker/   (public, static, fast)
```

---

## Part 1 — Go live now (static site, ~5 minutes)

Everything needed is already in the repo: `pages.yml` (the deploy workflow),
`.nojekyll` (so Pages serves the files verbatim), `404.html`, and a committed
`public_data.v1.js` with the real Nepal milestone. You only flip one switch.

### 1.1 Merge this branch to `main`
The `pages` and `ci` workflows are wired to `main`. Merge PR #1 (or push the
branch to `main`). CI runs the full offline suite + packaging gate on the way
in — a red build blocks the merge, which is what you want.

### 1.2 Enable GitHub Pages (one-time)
On GitHub: **Settings → Pages → Build and deployment → Source: “GitHub
Actions.”** That's it — do **not** pick "Deploy from a branch"; the
`pages.yml` workflow owns the deploy.

### 1.3 Watch the deploy
**Actions → pages → (latest run).** When it goes green, the site is live at:

> **https://cvree.github.io/OWCSComp.Tracker/**

The URL is also printed on the workflow run's summary (the `github-pages`
environment). First deploy can take 2–3 minutes to propagate on the CDN.

### 1.4 Confirm it works
Open these and click around — all are static and load from the committed data:

- `/` — landing (headline numbers are computed live from the verified dataset)
- `/stats.html` — hero pick/win rates, click any row for the per-team drill-down
- `/teams.html` → `/team.html?id=qadsiah` — team directory → team page
- `/matches.html`, `/tournaments.html`, `/match.html?id=m-qad-twis-s2po`
- `/calibration.html` — shows the static (no-server) calibration view

> **Why a project-site subpath is safe here:** every asset/link in the repo is
> relative (`assets/…`, `stats.html`), so the site works unchanged under
> `/OWCSComp.Tracker/`. Verified: zero root-absolute references.

### 1.5 (Optional) custom domain
Settings → Pages → Custom domain → e.g. `owcstracker.gg`. Add the DNS records
GitHub shows (an `A`/`ALIAS` or `CNAME` to `cvree.github.io`), tick **Enforce
HTTPS**, and commit a `CNAME` file (GitHub offers to create it). On a custom
*apex* domain the site serves from `/`, so the subpath caveat disappears
entirely.

---

## Part 2 — Keep the data fresh (the automation)

The public data file is a build artifact of `data/owcs.sqlite`. Three
workflows already exist; all are **manual on purpose** (`workflow_dispatch`)
so an unattended job can never silently mutate the milestone or push a bad
frame. Pick the cadence you actually want.

### 2.1 The three workflows

| Workflow | Does | Needs | When to run |
|---|---|---|---|
| `update-data.yml` | Ingest FACEIT rooms + apply corrections → regenerate `data.js` → commit | nothing (default `GITHUB_TOKEN`) | after adding FACEIT rooms / corrections |
| `pipeline.yml` | Full CV batch (`init_db → run_batch`: capture → detect → map_sync) | `yt-dlp`, `ffmpeg`, `opencv` (installed in-job) | after adding a VOD source |
| `pages.yml` | Deploy the static site | — | automatically, on push to `main` |

Run any of them from **Actions → (workflow) → Run workflow**.

### 2.2 The one gap to close for a *fully* automated public dataset
`update-data.yml` currently regenerates `assets/js/data.js` (the control-room
dataset). To also refresh the **public** dataset, add one line so it emits
`public_data.v1.js` and commits it too:

```yaml
      - name: Run pipeline
        run: |
          ARGS="--no-sample"
          if [ -n "${{ github.event.inputs.limit }}" ]; then ARGS="$ARGS --limit ${{ github.event.inputs.limit }}"; fi
          python3 pipeline/run_batch.py $ARGS
          python3 pipeline/export_data.py --public          # <-- add this

      - name: Commit updated data (and DB) if changed
        run: |
          ...
          git add assets/js/data.js assets/data/public_data.v1.js data/owcs.sqlite
          ...
```

CI already regenerates `public_data.v1.js` on every push and fails if it drifts
from the DB, so this stays honest.

### 2.3 Turning a real match into live stats (the human-in-the-loop path)
This is the workflow the pipeline is built around; it is deliberately *not*
fully unattended, because comps only count once a human has signed off.

1. **Add the VOD** to `data/sources/video_sources.json` (id, url, layout).
2. **Calibrate** it (Calibration Lab shows exactly what's missing):
   `python pipeline/calibrate_source.py --clip <clip> --times t1,…,t6 --source-id <id> --out layouts/<id>.json`
3. **Harvest hero templates** for that broadcast, then rebuild portraits:
   `python pipeline/build_hero_portraits.py`
4. **Ingest the map** (dry-run first, then `--write`):
   `python pipeline/ingest_map.py --clip … --layout layouts/<id>.json --match <id> … --write`
5. **Review** `reports/ingest/<id>/review.html`; correct anything wrong.
6. **Export + commit**: `python pipeline/export_data.py --public` → push to `main`.
   Pages redeploys; the new match, its comps, teams and pick-rates appear.

The full command block for the in-progress CR-ZETA match is in
`HANDOFF.md` under the current-status section.

### 2.4 (Optional) schedule it
If you want the FACEIT half to self-update (schedules, results, bans — no CV),
add a cron to `update-data.yml`:

```yaml
on:
  workflow_dispatch:
  schedule:
    - cron: "17 6 * * *"   # 06:17 UTC daily; avoid :00 (busy on Actions)
```

Leave `pipeline.yml` (the CV/VOD half) manual — unattended video downloads +
detection should always land in front of a human before they count.

---

## Part 3 — Operations runbook

- **Deploy is green but the page is stale:** the CDN caches ~10 min; hard-reload.
  Confirm the `pages` run actually ran after your data commit.
- **A page 404s:** check the link is relative and the target file is committed.
  The custom `404.html` catches typos and routes users home.
- **CI fails on "public export drift":** the committed `public_data.v1.js`
  doesn't match a fresh export — run `python pipeline/export_data.py --public`
  and commit the result.
- **Rolling back:** the site is just files at a commit. Revert the commit (or
  re-run `pages.yml` from an older SHA) to restore the previous site instantly.
- **Local preview exactly as it deploys:** `python pipeline/serve.py` →
  http://localhost:8000/ (also powers the live Calibration Lab + Run pages).

---

## Part 4 — What "works as intended" means here, and where it stands

| Capability | Status |
|---|---|
| Public site hosts with zero backend | ✅ ready (`pages.yml`, `.nojekyll`, `404.html`) |
| Front door routes to every stat surface | ✅ Home → Stats / Teams / Matches / Tournaments |
| All statistics reachable + drill-downable | ✅ hero→team drill-down, team pages, teams directory |
| Real hero portraits from broadcast | ✅ 14 today; grows per calibrated broadcast |
| Headline numbers stay honest as data grows | ✅ computed from verified comps at load |
| Autocalibration visibility | ✅ Calibration Lab (live + static) |
| Data refresh is automatable | ✅ manual workflows; one-line change for public auto-export (2.2) |
| Fully unattended CV ingestion | ⛔ by design — comps require human review before counting |

The only thing between the current repo and a live site is **Part 1** (merge +
flip the Pages switch). Everything else is about how often you feed it.
