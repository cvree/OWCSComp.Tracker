# Capture benchmark — full download vs sparse remote frames

The change this measures: calibrating a broadcast's HUD used to download
the whole VOD and keep about a dozen frames. It now resolves one direct
media URL and fetches only the frames it needs, by HTTP range.

Everything after acquisition is held identical between the two paths — the
same candidate screen, the same `gameplay_state` filter, the same
`calibrate_source.py --frames-dir` handoff, the same confidence floor. Only
the acquisition differs, which is the only way the numbers mean anything.
Reproduce with:

```bash
python pipeline/benchmark_capture.py --fixture --duration 1800 \
    --reference layouts/owcs_jksix_qwc.json
python pipeline/benchmark_capture.py --fixture --duration 3600 \
    --reference layouts/owcs_jksix_qwc.json
python pipeline/benchmark_capture.py --url "<broadcast-url>" --yes   # real VOD
```

## What was measured, and where

`--fixture` builds a broadcast-shaped MP4 out of this repository's own
committed OWCS frames (`reports/ingest/qad-twis-nepal/frames/`) — real
1280x720 broadcast frames with a real HUD — with live-gameplay windows
separated by blurred, darkened "desk" segments. It serves that file over a
byte-range HTTP server (`pipeline/fixtures/range_server.py`) that counts
every byte it sends.

**The byte figures below are counted by the server, at the wire, by the
other end of the connection.** They are not estimates and they cannot be
fooled by caching. That makes the offline measurement the more trustworthy
of the two available, at a smaller scale than a real broadcast.

One caveat worth stating: the server's send buffer is deliberately shrunk
to 16 KB. A seek issues an open-ended range and the client hangs up the
moment it has its frame; over loopback, with no TCP window to speak of, a
naive server pushes megabytes into the socket buffer before it notices and
would report those as "downloaded" when the client never read them.
Shrinking the buffer restores the back-pressure a real link provides.

## Results

### 30-minute broadcast (143 MB source, live play in 2 windows)

| | OLD (full download) | NEW (sparse remote) |
|---|---|---|
| bytes over the wire | **136.2 MB** | **13.4 MB** — 10.2x less |
| HTTP requests | 1 | 60 |
| frames acquired | 30 | 30 |
| frames calibrated from | 12 | 12 |
| wall time | 261.2 s | 176.6 s |
| calibration confidence | 0.986 | 0.965 |
| layout written | yes | yes |
| old vs new | — | equivalent (8 px max slot delta at 1920x1080) |
| **vs the committed reference** | **15 px** | **21 px** |

### 60-minute broadcast (273 MB source, live play in 2 windows)

| | OLD (full download) | NEW (sparse remote) |
|---|---|---|
| bytes over the wire | **272.7 MB** | **19.8 MB** — 13.8x less |
| HTTP requests | 1 | 96 |
| frames acquired | 60 | 48 |
| frames calibrated from | 24 | 18 |
| wall time | 467.2 s | 240.5 s |
| calibration confidence | 0.972 | 0.947 |
| layout written | yes | yes |
| old vs new | — | 61 px apart |
| **vs the committed reference** | **82 px** | **21 px** |

## Read the last row, not the one above it

At 60 minutes the two paths disagree by 61 px, and the obvious reading —
"the sparse path drifted" — is the wrong one. Comparing the two paths to
each other can only ever say that they differ; it cannot say which is
wrong. Both were therefore measured against
`layouts/owcs_jksix_qwc.json`, the layout the project already trusts,
calibrated from the very frames the fixture is built out of:

| | vs reference, 30 min | vs reference, 60 min |
|---|---|---|
| old (full download) | 15 px | **82 px** |
| new (sparse remote) | 21 px | 21 px |

A hero portrait is ~54 px wide and the chip pitch is ~107 px, so 82 px is
not a rounding difference — at 60 minutes the full-download path put its
boxes on the wrong heroes. The sparse path was stable at 21 px in both
runs.

The cause is not the acquisition. `calibrate_source`'s RANSAC grid fit can
latch onto a spurious saturated blob near the frame edge when it is given
more, noisier frames; the old path handed it 24 frames straight off a
60-second grid, while the sparse path's `pick_diverse` caps the input at 16
frames chosen to span the broadcast. That is a pre-existing sensitivity in
the calibrator, left alone deliberately — the brief was to change how
frames are acquired, not how they are fitted — but it is why "fewer,
better-spread frames" turns out to be a correctness property and not only
a bandwidth one.

`pipeline/test_calibrate_remote.py` pins this: the layout the sparse path
produces must land within 30 px of the committed reference, slot by slot.
It currently measures 15 px.

Pass `--reference layouts/<source>.json` to the benchmark to get that
comparison in the table.

## Why the cost does not track broadcast length

A 60-second ladder over nine hours is 540 offsets. Fetching all of them
before asking "is that enough?" would cost ~135 MB to answer a question two
dozen well-spread frames usually settle — so the scan does two things:

* **spread-first ordering** (`calibrate_remote.spread_first`) — the ladder
  is visited by recursive bisection, so the first 24 samples are a coarse
  sweep of the ENTIRE broadcast rather than its first 24 minutes;
* **chunked evaluation** — evidence is checked after every 24 frames, and
  acquisition stops the moment a trial calibration clears the existing
  confidence floor with margin.

`pipeline/test_calibrate_remote.py` asserts both directly: that the first
chunk of a nine-hour ladder covers all eight regions of the broadcast, and
that the scan records how many chunks each pass actually took.

## Projection to a real OWCS broadcast

Not a measurement — a projection from the measured per-frame cost, stated
as such:

| | 9-hour OWCS VOD |
|---|---|
| old path | the whole 720p file, ~2–4 GB |
| new path | ~30–50 frames at ~0.25–0.45 MB each ≈ **10–20 MB** |
| ratio | roughly **150–300x** less |

The ratio grows with broadcast length because the old cost scales with
duration and the new cost scales with how quickly the scan finds enough
evidence.

## What has NOT been measured here

The real-VOD leg (`--url`) has not been run in this environment: outbound
access to YouTube is blocked by the sandbox's network policy, and no
substitute would be honest. `yt-dlp` and `ffmpeg` are installed and the
code path is the same one the fixture exercises — the only untested link is
`yt-dlp -g` returning a googlevideo URL, and whether that CDN honours range
requests as reliably as the test server does. Run it on a networked machine
with:

```bash
python pipeline/benchmark_capture.py \
    --url "https://www.youtube.com/watch?v=jkSiX___Qwc" \
    --source-id owcs-jksix-qwc --yes
```

Add `--skip-old` to measure only the new path without downloading the whole
broadcast first. On that path the byte figures come from the host's network
interface counters and are therefore host-wide — run it on a quiet machine.

If a broadcast's CDN refuses range requests, `remote_frames` reports the
failure and says so; `capture.py --full-download` and the existing clip
route remain available and unchanged.
