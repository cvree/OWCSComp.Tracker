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
| find broadcasts | `match-finder.yml`, every 6 h | ✅ already | Twitch channel added as a source |
| acquire frames | operator's machine | ✅ **proven above** | Twitch accepted at intake |
| calibrate the HUD | operator runs `calibrate_source.py` | computational already; refuses below 0.55 | an auto-approve threshold |
| forge templates | operator runs `template_forge.py` | build → gate → held-out validation is already one command | promote only `VALIDATED` heroes |
| ingest the map | operator runs `ingest_map.py` | pure OpenCV, offline | nothing |
| review detections | **human gate, by design** | policy decision — see below | a confidence rule |
| export + publish | `export_data.py --public` | already gated by CI | a PR the workflow merges on green |
| deploy | `pages.yml` | ✅ already | nothing |

Only two rows are genuinely open: **Twitch as a source**, and **what
publishes without a human**.

---

## What to build, in order

### 1. Twitch as a first-class source — the actual blocker now

`link_intake.parse_link` rejects `twitch.tv` with `unsupported_host`, and
`pipeline/test_automation_link_intake.py` asserts that it does. The one
source a free runner can fetch is the one intake refuses. This is the
keystone change and it is not small: `videoId` identity, the 11-character
YouTube id regex, `canonical_url`, the worker's download path, discovery,
`self_fill` and the public export contract all assume YouTube spelling.

Do it as: a `platform` field beside `videoId` (defaulting to `youtube` so
every committed artifact stays byte-identical), a per-platform id shape,
per-platform canonical URLs, and the same verified-channel authority rule
applied to the Twitch channel registry. Tests first — the export contract
suite is the one that will catch drift.

### 2. `autopilot.yml` — the scheduled chain

`pipeline/automation/autopilot.py` already drives every automatic stage in
a row and stops honestly at the first human gate. Unattended operation is
that loop, on a cron, picking the oldest unprocessed discovered broadcast,
then committing through the same PR-and-green-CI path `discovery.yml`
already uses. Concurrency must join the existing shared data group so it
can never race the other committing workflows.

### 3. The four gates

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

## The decision that is not the pipeline's to make

Today the product states plainly that **nothing is ever approved
automatically**: hero compositions reach production only through a human
review. Fully unattended publishing changes that, and it is a product
decision, not a technical one.

The Windows app already has the shape of an answer — *"only repeated,
high-confidence readings publish on their own"* — and the detector already
produces exactly the evidence such a rule needs: a ranked candidate, its
runner-up, the margin, `UNKNOWN` instead of a guess, and temporal
consensus over hundreds of agreeing frames.

So the honest unattended rule is a **narrow** one: publish a slot read
only when a long run of frames agrees, the margin clears a stated floor,
and no neighbouring read is `UNKNOWN` — and send everything else to the
review inbox, where it waits as long as it needs to. That keeps the
guarantee that matters (nothing uncertain is ever stated as fact) while
letting the ordinary case flow through untouched.

What must NOT happen is lowering the detector's own floors to make more
things auto-publishable. An unattended pipeline that publishes a
plausible-but-wrong composition is worse than one that publishes nothing.

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
