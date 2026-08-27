# Running the whole loop unattended, on free services

The goal this document answers: **a broadcast airs, and the published site
updates itself — with nobody running a command, on nobody's hardware, for
$0.**

Everything downstream of the video already meets that bar. Detection,
calibration, template forging, export and the site are plain Python +
OpenCV + static files: deterministic, offline, no secrets, and already
running on GitHub-hosted runners in `ci.yml`. Discovery already runs
unattended every six hours in `match-finder.yml`.

One step was never unattended, and it is the one everything else waits on:
**getting the pixels**.

---

## The blocker, measured rather than assumed

`.github/workflows/acquire-probe.yml` asks that question on the same free
hardware the pipeline would use, and commits nothing. Two runs settled it.

### YouTube: refused, on every rung

One `yt-dlp -g` per player client, in fallback-ladder order:

| client | result |
|---|---|
| `default (web)` | `Sign in to confirm you're not a bot` |
| `android` | same |
| `ios` | same |
| `tv` | same |
| `mweb` | same |
| `web_safari` | same |
| `web_embedded` | same |

Seven for seven, in ten seconds. This is not a rate limit to back off
from and not a client to work around: YouTube bot-checks GitHub's
datacentre ranges, and the refusal arrives before any media is offered.
The repo already knew the per-video date lookup was refused here (which is
why the archive is dated through the Data API instead) — this establishes
that the *media* request is refused too, for every client yt-dlp has.

**A GitHub-hosted runner cannot fetch a YouTube VOD.** No workflow design
changes that.

### Twitch: served, and fast

`config/broadcast_channels.json` already records `twitch.tv/OW_Esports`
as an official OWCS destination. The same probe listed that channel's
recent VODs and resolved the newest one — no cookies, no key, no secret —
then ran a real sparse acquisition through `pipeline/remote_frames.py`
against `https://www.twitch.tv/videos/2854348714`:

```json
{"ytdlpCalls": 4, "ffmpegCalls": 6, "framesRequested": 6,
 "framesFetched": 6, "framesMissing": 0,
 "seconds": 20.57, "bytesDownloaded": 22863514, "bytesMeasured": true}
```

Six 720p frames, a minute apart, at offsets 31–37 minutes into the
broadcast: **6/6 fetched, 22.9 MB over the wire, 20.6 seconds**, on a free
ubuntu-latest runner. The frames landing at t=2200s also prove the VOD is
a full broadcast rather than a highlight clip.

**So the unattended loop is possible on free GitHub hardware — through
Twitch, not YouTube.**

### And the finder's Twitch source, run for real

The same probe runs the finder's Twitch source against the real registry
row. It returned **15 candidates from 15 VODs**, every one scored `likely`
by the same broadcast-likeness gate production intake trusts, and every one
round-tripping through `parse_link` back to the id it came from:

```
  8.5h  likely  [REBROADCAST] [DROPS] OWWC 2026 | Group Stage Day 4
 12.1h  likely  [DROPS] OWWC 2026 | Group Stage Day 1
  6.8h  likely  OWCS x FACEIT League | Stage 3 Promotion/Relegation Matches
  8.9h  likely  [DROPS] OWCS 2026 | Midseason Championship Day 5
```

Runtimes of 6–12 hours: real broadcast days, not clips.

Two things that run corrected, which is what a live check is for:

* **The flat dump carries no air date** — all fifteen came back dateless,
  exactly like the YouTube listing. The difference is what happens next:
  the bounded per-video lookup that fills the gap is refused on YouTube
  from this hardware and served on Twitch, so the path this repo already
  wrote (and had to route around with the Data API for YouTube) is the one
  that works here. `fetch_video_metadata` now takes the row's platform.
* **Rebroadcasts are listed as their own VODs** and score `likely`,
  because they are the same content aired again. Deduplicating them
  against their original is a real piece of work the finder does not do
  yet — worth doing before this queue drives anything unattended, or the
  same day gets processed twice.

### What that costs at full-map scale

The measured rate extrapolates honestly. The verified Nepal map sampled
299 frames:

| | measured (6 frames) | extrapolated (299 frames) |
|---|---|---|
| wire bytes | 22.9 MB | ~1.1 GB |
| wall clock | 20.6 s | ~17 min |

Both sit well inside a runner's limits (6 h per job, ~14 GB free disk).
Note `rangeBatches: 0` — Twitch serves HLS segments, so a frame costs a
whole segment (~3.8 MB) rather than the fine byte-range read an MP4
allows. `remote_frames.plan_batches` already batches nearby offsets, which
is what makes a dense burst around a suspected swap cheap; the number
above is the worst case of six deliberately-scattered reads.

---

## Where each step runs

| step | runs today | unattended | needs |
|---|---|---|---|
| find broadcasts | `match-finder.yml`, every 6 h | ✅ **done** | — |
| acquire frames | operator's machine | ✅ **proven above** | the worker's host allowlist |
| calibrate the HUD | operator runs `calibrate_source.py` | computational already; refuses below 0.55 | an auto-approve threshold |
| forge templates | operator runs `template_forge.py` | build → gate → held-out validation is already one command | promote only `VALIDATED` heroes |
| ingest the map | operator runs `ingest_map.py` | pure OpenCV, offline | nothing |
| review detections | **audit, after publication** | ✅ **done** | — |
| export + publish | `export_data.py --public` | ✅ **done** (`--publish`) | — |
| deploy | `pages.yml` | ✅ already | nothing |

Only two rows are genuinely open: **Twitch as a source**, and **what
publishes without a human**.

---

## What to build, in order

### 1. Twitch as a first-class source — DONE

`platform` rides beside `videoId`, defaulting to `youtube` so every
committed artifact stays byte-identical and every key already in a job
store keeps resolving. Intake parses and authorizes Twitch VODs with no
key at all, the finder scans the channel's `/videos` tab, and `self_fill`
joins a published Twitch match against its own record.

### 2. The worker's host allowlist — DONE

`worker.py` keeps its own `ALLOWED_HOSTS` and it stays its own gate:
intake decides what may be *recorded*, the worker decides what may be
*fetched*, and one must not be able to widen the other by accident. Both
now accept Twitch, and a test asserts they cannot drift apart — a host the
worker will download must be one intake will admit.

Closing that gate surfaced an older hole worth naming. `validate_source`
took `payload["videoId"]` on faith over the id in the URL beside it, which
was survivable while every id came from one namespace. With two, a Twitch
id beside a YouTube URL would authorize one broadcast and download
another. A disagreement is now refused rather than resolved in either
direction.

Still YouTube-only, and deliberately left alone: the operator-facing clip
tools (`download_vod_clip.py`, `run_owcs_auto.py`,
`extract_calibration_frames.py`) refuse a non-YouTube source through
`video_ingest.is_youtube_source`. They are the local-operator path, not
the one the autopilot drives, and widening them is a separate job with its
own tests.

### 3. `autopilot.yml` — the scheduled chain

The workflow exists and runs every six hours, offset from the scan. It
picks the next fetchable broadcast (`match_finder.next_fetchable` — oldest
first, dateless last, Twitch only because this hardware cannot fetch
YouTube, and `likely` only because "unlikely" is a verdict with reasons
that a scheduled job has no business overriding), drives the stages, and
prints the gate it reached.

**It writes nothing by default, and that is not timidity.** No Twitch
broadcast has ever been calibrated here: the HUD layout and the per-source
hero templates detection needs do not exist for this package yet. A chain
that cannot yet detect must not be able to publish, so the schedule runs
in `report` mode and `process` is a deliberate dispatch. Read where it
stops before letting it write — that is what the report is for.

It already joins the shared `owcs-generated-data` concurrency group, so it
is serialised against the other data workflows from the moment it starts
writing rather than at the moment someone remembers.

In `process` mode the chain now runs to publication — that step is wired
up (`--publish`), and the workflow owns the single push.

Still to wire up, in this order:

1. **Calibrate the Twitch package.** `calibrate_source.py` is
   computational and runs fine on a runner; it refuses below confidence
   0.55. This is the first thing `process` mode will stop at, and it is
   the reason the schedule still defaults to `report`.
2. **Forge its templates.** `template_forge.py` is already one command:
   build → gate → provenance → held-out validation → gated promotion. Only
   `VALIDATED` heroes promote; the rest read `UNKNOWN`, which is safe.

### 4. The four gates

`autopilot.py` stops at: source authorization, layout approval, segment
identity, detection review. Unattended means each needs a written rule
rather than a person:

* **source** — auto-approve only channels already `enabled` + confirmed in
  the verified registry. That authority already exists; this just applies
  it without a typed name.
* **layout** — auto-approve only above a threshold clearly higher than the
  0.55 refusal floor (0.75 is a reasonable start), with the calibration
  sheet committed as evidence either way.
* **segments** — `--auto-accept` already exists and runs through the same
  `accept-proposed` gate a human uses, refusing incomplete proposals.
* **detections** — the open question below.

---

## The publication rule — decided and wired up

The product used to state plainly that **nothing is ever approved
automatically**: hero compositions reached production only through a human
review. Fully unattended publishing changes that, and it was a product
decision rather than a technical one. It has been made: **publish what the
detector accepts.**

That is a narrower rule than it sounds, and the narrowness is the whole
reason it is defensible. It moves the human out of the loop; it does NOT
move the bar. Everything the detector already refuses, it still refuses:

* a slot whose best match does not clear `unknown_floor` reads `UNKNOWN`,
  and `UNKNOWN` is never a hero;
* a slot whose best and runner-up are too close to separate fails
  `min_margin` and reads `UNKNOWN` too;
* a hero with no template in the package is reported `UNKNOWN`, never
  guessed from a near neighbour;
* a change point survives only if temporal consensus confirms it — the
  verified Nepal map rejected 7 of 10 suspected swaps as dead-portrait
  lookalikes and killcam artifacts, and those rejections are exported with
  the reason they were thrown out;
* replays, scoreboards and desk segments never create comps at all.

So what publishes unattended is what survived all of that, and what did
not survive is still published as a stated `UNKNOWN` rather than a guess.

**What must never happen is lowering those floors to make more things
publishable.** They are the entire remaining guarantee, and an unattended
pipeline that states a plausible-but-wrong composition as fact is worse
than one that publishes nothing. Any future change that moves
`unknown_floor`, `min_margin`, or the consensus thresholds is a change to
this decision, not an implementation detail of it.

### Where the human went

The review did not disappear; it moved to the other side of publication,
and it moved onto the published site.

* **`review.html` is the audit surface**, not a gate. It reads the
  published dataset, on the published site, and lists everything published
  — not a queue of blocked work. Games carrying `provisional` reads sort
  first, because that is where a person's attention is worth most;
  confirmed ones stay listed, because hiding them would make the record
  look more checked than it is.
* **A correction is a commit.** With no tracker behind the page, decisions
  export as `corrections/corrections.json` — the same file the pipeline
  reads, which git keeps as the audit trail. An audit produces an
  attributed, reversible, public change rather than an invisible edit to a
  database.
* **A correction never deletes the detection it corrects.** That was
  already true and still is: what the detector saw stays exactly as
  recorded, with the correction as an additional layer beside it.

`how-it-works.html` was rewritten in the same change, not after it: the
six steps now end at "Published, open for audit", and the page says
plainly that a person comes after publication rather than before it. A
site that describes a review gate it no longer has would be lying about
the provenance of its own data, which is the one thing this project
sells.

### Measured: where the chain actually stops today

`.github/workflows/readiness-probe.yml` runs the real decision path against
a real Twitch broadcast and reports each gate's own verdict. Run against
`[REBROADCAST] [DROPS] OWWC 2026 | Group Stage Day 4` (8.5 h,
`twitch.tv/videos/2854348714`):

| step | result |
|---|---|
| find a broadcast | ✅ the finder returned it from the official Twitch channel |
| **source authorization** | ✅ **AUTOMATIC** — "channel `ow_esports` is the verified official registry entry `ow_esports_twitch`" |
| acquire frames | ✅ 12/12 sparse frames fetched and decoded |
| **layout** | ❌ **NO_MATCH** — all three committed layouts scored **0.000** |

So the first gate is already open with nothing to do. The layout gate is
not, and the numbers say why in a way worth reading carefully:

```
owcs_8c105lnzlam   score 0.000  gameplay 0/12
owcs_jksix_qwc     score 0.000  gameplay 0/12
owcs_nd5lllwdky0   score 0.000  gameplay 0/12
```

**`gameplay 0/12`.** Not one sampled frame was classified as live play at
all. That is a different finding from "the Twitch HUD differs from the
YouTube HUD", and the two must not be confused: a layout cannot reproduce
its own HUD structure on a frame that has no HUD in it, so on this evidence
the fingerprint says nothing about whether the committed layout would match.
Twelve frames spread over the middle half of an eight-and-a-half-hour
rebroadcast is simply a bad way to find live play — most of that runtime is
desk, breaks and waiting screens.

Which is exactly the problem `calibrate_remote.py` already exists to solve:
it walks a ladder (60 s → 30 s → 15 s, then densifies) specifically to find
gameplay windows in a long VOD, instead of sampling blind.

### Running the real ladder overturned that first reading

`.github/workflows/calibrate-twitch.yml` ran it against
`[DROPS] OWWC 2026 | Group Stage Day 2` (10.5 h, an original airing rather
than a rebroadcast):

```
pass 1: 60s over whole broadcast — 48 new sample(s), 2 chunk(s) of up to 24
pass 1 chunk 1: 11 clean so far — 11/12 clean frames
pass 1 chunk 2: 25 clean so far — 25 clean frames but only 1/4 regions of
                the broadcast — too clustered to calibrate from
provisional calibration from 16 screened frame(s)
  frames acquired: 0 · yt-dlp calls: 4 · ffmpeg reads: 48
  bytes over the wire: 822.0 MB · sample budget reached (48)
REFUSED — the screened frames did not yield a chip grid — only one side
produced grid candidates; left chip row not found (32 candidate blobs
pooled from 16 frames) — are these live-gameplay frames?; right chip row
not found (61 candidate blobs pooled from 16 frames)
```

**Gameplay is found on Twitch.** 11 of 12 frames in the first chunk passed
the gameplay filter, 25 clean overall. So the earlier `gameplay 0/12` was
purely an artifact of blind sampling, exactly as suspected — that question
is now settled, and settled the good way.

What refused is the next thing along: the **chip grid**. The HUD's two
ult-charge chip rows were not found in frames the gameplay filter was happy
with. Three candidate explanations, and they are not equally likely:

1. **This is OWWC, not OWCS.** The Overwatch World Cup is a different
   production from the Champions Series and may well carry a different
   overlay. Every committed layout here is an OWCS package. Note that the
   channel registry row restricts this channel to
   `allowedEventTypes: ["owcs"]` — but the Twitch finder path does not
   enforce that field, so an OWWC broadcast was queued and calibrated
   against as though it were OWCS. That is a real gap, and it is probably
   this result's cause.
2. **The sample was too clustered.** The ladder itself says so: 25 clean
   frames but only 1/4 of the broadcast covered, because a 48-sample budget
   over 10.5 h in chunks of 24 ran out before it spread. Calibrating from
   one region of one broadcast is exactly what it refuses to do.
3. The Twitch encode differs enough to defeat chip detection — least
   likely, since the gameplay filter (which reads the same HUD structurally)
   was satisfied.

So the honest state is: **acquisition works, gameplay detection works,
calibration has not yet been given a fair test.** The next run should target
an actual OWCS broadcast with a sample budget spread across the whole VOD,
and `allowedEventTypes` should be enforced on the Twitch path so the queue
stops offering OWWC in the first place.

Note also the cost: 822 MB for 48 frames — ~17 MB each, well above the
3.8 MB measured earlier, because seeking deep into a 10.5 h HLS stream pulls
more per read. A full calibration pass is not free, though it is still far
below downloading a 10.5-hour broadcast.

### What still stops for a human

Two gates remain, and neither is about whether a reading is right:

* **Source authorization** — whether a link may be downloaded at all. A
  channel in the verified registry auto-approves; anything else waits.
* **Layout approval** — whether a calibration profile really describes
  this broadcast's HUD. A detection cannot settle this; a wrong layout
  produces confident nonsense rather than an honest `UNKNOWN`.

Publication itself is opt-in per run (`--publish`), not because it needs
judgement but because it **writes**: it regenerates the export, runs the
validation and packaging checks, and commits. A local dry run should not
push commits by surprise. The unattended workflow asks for it explicitly,
and pushes in exactly one place.

---

## If Twitch is not enough

Twitch VOD retention is limited (typically weeks, not years), so the
unattended path covers **broadcasts from now on**. The 2024/2025 back
catalogue exists on YouTube only, and a GitHub runner cannot fetch it.

Two ways to reach that archive, neither of them free-and-unattended in the
way the Twitch path is:

* **A self-hosted runner** on any machine YouTube already serves. Installed
  once as a service, it needs no attention afterwards, and only the
  acquisition job targets it — everything else stays on GitHub hardware.
* **Cookies in a repository secret.** This works today and needs no
  hardware, but it contradicts a standing principle of this repo (an
  unattended public scan holds no cookies), the cookies expire and need
  re-exporting, and they carry the account they came from.

Neither is required for the loop to run. They only decide whether the
*archive* is ever read.
