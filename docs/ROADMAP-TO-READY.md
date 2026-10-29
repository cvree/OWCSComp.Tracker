# Roadmap to "ready for use" (audit of 2026-10-29)

A step back from the pass-by-pass history in `HANDOFF.md`: where the project
actually stands, what blocks it, and the shortest path to a site that fills
itself with real hero compositions at $0.

---

## Where it stands

**Built and working**

- Static site on GitHub Pages; auto-redeploys after every data commit.
- Discovery, unattended: `match-finder` (every 6 h) knows 407 broadcasts
  (333 YouTube, 74 Twitch); `discovery` syncs FACEIT hourly.
- Every gate before detection is open on free GitHub hardware for Twitch:
  source authorization (automatic), frame acquisition, gameplay filtering,
  and layout (`owcs_8c105lnzlam` reproduces the Twitch HUD at score 1.000).
- Publish-then-audit: `review.html` audits after publication; corrections are
  commits.
- Windows desktop app with a background service, built and verified on a
  clean runner by `windows-app.yml`.
- 108 offline test suites, packaging gate, export and self-fill
  reproducibility gates.

**The gap, in numbers**

| | |
|---|---|
| Maps with published compositions | **1** (Nepal, ingested 2026-07) |
| Public export last regenerated | 2026-08-26 |
| Last human change | 2026-08-27 |
| `autopilot` runs since | ~225, all in `report` mode — nothing processed |
| Twitch VODs expired before anyone processed them | 15 of 74 |
| GitHub Releases (installer downloads) | **0** — README links to an empty page |

The automation runs, stays green, and produces no match data. Green
workflows have hidden that.

---

## What blocks it, in order of weight

### 1. Hero templates: the real ceiling

A composition needs five named heroes. `hero_coverage.py` today:

| package | covered | validated | note |
|---|---|---|---|
| `owcs_8c105lnzlam` — **the layout that matches Twitch** | 7 / 52 | **0** | no provenance |
| `owcs_jksix_qwc` — best package | 8 / 52 | 8 | leave-one-out false-match **4.64 %** vs a 2.0 % ceiling |
| `owcs_youtube_2026` | 17 / 52 | 0 | no provenance |

So switching `autopilot` to `process` today would publish almost nothing,
because nearly every map fields an uncovered hero. No other fix moves the
output until this one is done.

### 2. Evidence storage does not scale

One map committed 23 MB and 3,186 evidence images under `reports/ingest/`.
GitHub Pages caps a published site at 1 GB, and `pages.yml` uploads the whole
repository root. That limit is hit at about 40 maps.

### 3. Several PCs publishing overwrite each other

The desktop app's `website.publish` uploads `public_data.v1.js` exported
from **that PC's local database**, and does not upload the database itself:

- a second PC publishing replaces the first PC's matches (last writer wins);
- the committed export no longer matches a fresh export of the committed DB,
  so the next CI run fails `export_data.py --check --public`;
- it needs a GitHub token with write access to this repo on every PC that
  publishes, so volunteers would need write access.

### 4. The unattended queue (two defects fixed in this pass)

- **Fixed:** with `YOUTUBE_API_KEY` set, the date backfill sent every
  dateless row, Twitch ids included, to the YouTube Data API. The API cannot
  know a Twitch id, so all 74 Twitch rows stayed dateless and each scan
  reported them as "deleted, private or region-blocked". YouTube rows now go
  to the API and Twitch rows go to the bounded per-video lookup, which Twitch
  serves from a runner.
- **Fixed:** with no dates, "oldest first" sorted Twitch rows by id, so the
  head of the queue was the oldest VOD, which Twitch had already deleted
  (`2642999697`, delisted 2026-08-29). `fetchable_queue` now skips a Twitch
  row that its channel's latest listing no longer carries.
- **Open:** the job store is wiped at the end of every run, so `process` mode
  has no record of what it already tried. A broadcast that fails would head
  the queue again on every run. It needs a small committed attempt ledger
  (video id, outcome, attempt count, last error) passed as `exclude`.
- **Open:** the autopilot picker accepts any title that is not refused
  (Collegiate, Calling All Heroes, FACEIT League). The calibration picker
  already prefers titles that name an allowed family, and the autopilot
  should do the same, so the committed OWCS layout is tried on OWCS first.
- **Open:** rebroadcasts are listed as separate VODs, so the same day can be
  processed twice.

### 5. Drift and hygiene

- The README's install section points at Releases, and no `v*` tag has ever
  been pushed, so the installer was never published.
- `GOING-LIVE.md` still describes manual-only pipelines with mandatory human
  review. That stopped being true with publish-then-audit.
- `HANDOFF.md` runs to 3,788 lines. The current state is hard to find in it.
- `config/owcs_calendar.json`: every event is unverified, Stage 3 is missing,
  and the Global Finals entry (2026-11-01 → 11-10) starts this week.
- There are 25 design screenshots (~11 MB) at the repo root, plus
  `.playwright-mcp/` debug logs. `pages.yml` publishes all of it, along with
  the SQLite DB, the pipeline source and the handoff docs.
- 81 `auto/discovery-*` branches and 46 `claude/*` branches remain on the
  remote.
- CI only runs on push and pull requests. Bot commits do not trigger it, so
  nothing has run the full suite since 2026-08-27. Dependency drift (yt-dlp,
  runner images) would go unnoticed.

---

## Steps to finished

### Phase 0 — housekeeping (about an hour, no risk)

1. Merge this branch (the queue and date-backfill fixes).
2. Push a `v0.1.0` tag. `windows-app.yml` builds, verifies on a clean
   machine, and attaches the installer to a Release. The README's install
   link then works.
3. Verify the 2026 Global Finals and Stage 3 in `config/owcs_calendar.json`
   (dates and regions from the official source). Mark them `verified`.
4. Delete the root `*.png` files and `.playwright-mcp/`. Prune merged or
   closed branches.
5. Add `schedule:` (weekly) to `ci.yml` so the suite runs even when only
   bots commit.

### Phase 1 — hero templates for the Twitch package (the unblocker)

**Status (2026-10-29): built and proven.** `label_batch.py`, `label.html`,
`label_forge.py`, `label-harvest.yml`, `label-forge.yml` — see
`HANDOFF.md` (fifteenth pass). What remains is the human step: naming the
cards.

This is the only phase that needs a person, and it needs one only once per
hero.

1. **Harvest on a runner.** Add a workflow that runs `template_bootstrap` /
   `harvest_templates.py --cluster` over the gameplay windows of 3–5 OWCS
   broadcasts that are still listed on Twitch (Midseason Championship days,
   Stage 2 Playoffs). The layout is already `owcs_8c105lnzlam`. Commit the
   cluster montages plus the `suggest_labels` proposals, ranked against the
   official hero icons.
2. **Label in the browser.** Build a labelling page in the style of
   `review.html`: one cluster per card, the suggested hero preselected, and
   `y`/`n`/pick to confirm. It exports `labels.json` as a commit, the same
   way corrections already work, so git stays the audit trail. A human is
   still the only writer of hero labels, which keeps the guarantee
   `template_bootstrap` states.
3. **Forge and validate on a runner.** `template_forge.py --promote-to` runs
   build → gate → held-out validation. Only `VALIDATED` heroes promote.
4. **Target coverage, not 52/52.** The OWCS meta uses roughly 25–30 heroes,
   so cover those first. Re-measure the leave-one-out false-match rate.
   Coverage of more heroes is also what pulls that rate under the 2 %
   ceiling.

Realistic human cost: one or two sittings of clicking "yes, that's Kiriko".

### Phase 2 — turn the cloud loop on

1. Add the attempt ledger and the OWCS-first preference to the autopilot
   picker (section 4, above).
2. Fix evidence storage before the first unattended publish. Pick one:
   - **Keep fewer, smaller crops** (recommended first step): WebP at
     quality 70, and only change points plus N samples per stint, not every
     slot read. Likely a 10–20× reduction.
   - **Move full evidence out of the Pages artifact**: per-map archives as
     GitHub Release assets (free, no repository growth) or Cloudflare R2's
     free tier (10 GB, no egress fee). The site links to them.
   - **Build a `_site/` directory in `pages.yml`** containing only the pages,
     `assets/` and the evidence the pages reference, instead of uploading the
     repository root.
3. Dispatch `autopilot` with `mode=process` on **one** broadcast. Read the
   result and audit it on `review.html`.
4. When that map is right, make `process` the scheduled default. Use a
   matrix (one job per map window) so a broadcast day finishes in parallel.
   Standard GitHub-hosted runners are free for public repositories.

From here, a new OWCS broadcast on Twitch reaches the site with nobody
touching it, within the six-hour cadence plus processing time.

### Phase 3 — the YouTube archive on volunteer PCs

GitHub's datacentre IPs are bot-checked by YouTube on every client, so the
2024/2025 archive (333 broadcasts, all dated) can only be fetched from a
residential connection. The desktop app already does the heavy lifting
locally. What it needs is a way to contribute results without write access.

1. **Contribution bundles instead of exports.** Each processed map produces
   one self-describing file, `contributions/<video>/<map>.json`: detector
   version, layout id, template-package hash, stints, swaps, and a compact
   evidence set. The bundle is data, never a regenerated export.
2. **A fork pull request as the transport.** The app uses the volunteer's
   own GitHub token (`public_repo` scope on their own account), forks,
   commits the bundle, and opens a PR. No volunteer needs write access to
   this repository.
3. **The merge happens in one place.** A workflow validates the bundle
   (schema, known detector version, evidence present, a known layout and
   template package), folds it into `data/owcs.sqlite`, re-exports, and
   commits. Two PCs can no longer overwrite each other, and the CI
   reproducibility gate stays true.
4. **Don't use a self-hosted Actions runner for this.** On a public
   repository, a self-hosted runner can be made to run code from fork pull
   requests. The desktop app plus pull requests gets the same volunteer
   compute without that exposure.
5. A shared work queue: the app reads `discovered.v1.js`, picks an
   unclaimed YouTube broadcast, and claims it by opening a draft PR. That
   lets several volunteers split the archive without coordinating.

### Phase 4 — depth

- Map winner and score from FACEIT or HUD OCR, instead of `--map-winner`.
- Rebroadcast deduplication in the finder.
- Optional: a small ONNX classifier trained on the labelled crops. It
  generalises across broadcast packages better than per-source templates,
  and runs on CPU with `onnxruntime` (about 10 MB, no torch). Only worth it
  once Phase 1 has produced the labels.

---

## Cost summary

| piece | where | cost |
|---|---|---|
| Site | GitHub Pages | $0 (1 GB site cap: see evidence storage) |
| Discovery, Twitch processing, merging | GitHub Actions, public repository | $0 (standard runners) |
| YouTube archive | volunteer PCs through the desktop app | $0 to the project |
| Dates for YouTube rows | YouTube Data API | about 5 quota units per scan of the 10,000/day free tier |
| Evidence overflow, if needed | Release assets or the R2 free tier | $0 |
| Installer signing | optional Authenticode certificate | the only paid item, and optional |
