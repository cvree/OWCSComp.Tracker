#!/usr/bin/env python3
"""
test_remote_frames.py — does sparse acquisition actually avoid the download?

The claim this suite has to defend is a quantitative one: calibrating a
broadcast should cost a few frames' worth of bytes, not the whole VOD. That
cannot be checked by reading the code, so this runs the real thing:

  * a broadcast-shaped MP4 built from the repository's OWN committed OWCS
    frames (`pipeline/fixtures/make_broadcast.py`) — real HUD, real chips,
    real portraits, with real desk segments between the games;
  * served over HTTP by a server that implements byte ranges properly and
    COUNTS what it sends (`pipeline/fixtures/range_server.py`), so "bytes
    downloaded" is measured at the wire by the other end rather than
    estimated;
  * fetched by the real `remote_frames` code with real ffmpeg.

The fast tests (planning, cache keys, ladder logic) always run. The tests
that need ffmpeg skip loudly when it is absent — never silently pass.

Run: python3 pipeline/test_remote_frames.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "fixtures"))

import remote_frames as rf  # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None

#: Short but broadcast-shaped: two separated live windows in a sea of desk.
FIXTURE_DURATION = 600.0
FIXTURE_WINDOWS = [(120.0, 240.0), (400.0, 520.0)]

_STATE: dict = {}


def _build_fixture() -> str:
    """Build the served VOD once for the whole module (it costs ffmpeg)."""
    if "vod" in _STATE:
        return _STATE["vod"]
    import make_broadcast
    tmp = tempfile.mkdtemp(prefix="rfx_")
    _STATE["tmp"] = tmp
    out = os.path.join(tmp, "vod.mp4")
    make_broadcast.build(out, FIXTURE_DURATION, FIXTURE_WINDOWS)
    _STATE["vod"] = out
    return out


def _serve() -> tuple[str, object]:
    import range_server
    vod = _build_fixture()
    if "server" not in _STATE:
        httpd, port = range_server.serve(os.path.dirname(vod))
        _STATE["server"] = httpd
        _STATE["port"] = port
    return (f"http://127.0.0.1:{_STATE['port']}/{os.path.basename(vod)}",
            __import__("range_server"))


def tearDownModule():                              # noqa: N802
    srv = _STATE.get("server")
    if srv:
        srv.shutdown()
    if _STATE.get("tmp"):
        shutil.rmtree(_STATE["tmp"], ignore_errors=True)


def _local_resolver(url, height):
    """The URL IS the media URL in these tests — no yt-dlp anywhere.

    That is not a shortcut around the interesting part: `yt-dlp -g` exists
    solely to turn a watch page into a direct media URL, and everything
    this module is judged on happens after that."""
    return url, "test-direct"


# ------------------------------------------------------------ pure logic
class TestPlanning(unittest.TestCase):
    """The batching decision is the whole economic argument, so it is a
    pure function with its own tests rather than a shape inside a loop."""

    def test_a_sparse_scan_never_batches_across_the_gaps(self):
        # 60s apart: a range read between them would download the minute in
        # between, which is exactly the waste this replaces.
        plan = rf.plan_batches([0, 60, 120, 180])
        self.assertEqual([b["kind"] for b in plan], ["single"] * 4)

    def test_a_dense_burst_becomes_one_read(self):
        plan = rf.plan_batches([100 + i for i in range(20)])
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["kind"], "range")
        self.assertEqual(plan[0]["step"], 1.0)

    def test_bursts_far_apart_stay_separate(self):
        plan = rf.plan_batches([100, 101, 102, 900, 901, 902])
        self.assertEqual(len(plan), 2)
        self.assertTrue(all(b["kind"] == "range" for b in plan))

    def test_a_long_run_is_split_so_it_cannot_become_a_download(self):
        offsets = [i for i in range(0, 1000, 2)]      # 2s apart, 1000s span
        plan = rf.plan_batches(offsets, batch_span=180.0)
        self.assertGreater(len(plan), 1)
        for b in plan:
            self.assertLessEqual(b["end"] - b["start"], 180.0 + 1e-6)

    def test_every_requested_offset_survives_planning(self):
        offsets = [0, 1, 2, 3, 60, 61, 300, 900, 901, 902, 903]
        plan = rf.plan_batches(offsets)
        planned = sorted(t for b in plan for t in b["offsets"])
        self.assertEqual(planned, sorted(float(t) for t in offsets))


class TestCacheKeys(unittest.TestCase):
    def test_one_video_has_one_key_however_the_url_is_written(self):
        keys = {rf.video_key(u) for u in (
            "https://www.youtube.com/watch?v=jkSiX___Qwc",
            "https://youtu.be/jkSiX___Qwc",
            "https://www.youtube.com/live/jkSiX___Qwc",
            "https://m.youtube.com/watch?v=jkSiX___Qwc&t=42",
        )}
        self.assertEqual(keys, {"jkSiX___Qwc"})

    def test_different_videos_never_collide(self):
        self.assertNotEqual(rf.video_key("https://youtu.be/aaaaaaaaaaa"),
                            rf.video_key("https://youtu.be/bbbbbbbbbbb"))

    def test_a_non_youtube_url_still_gets_a_stable_key(self):
        a = rf.video_key("http://example.test/x.mp4")
        self.assertEqual(a, rf.video_key("http://example.test/x.mp4"))
        self.assertTrue(a.startswith("u"))

    def test_the_cache_path_is_keyed_by_video_timestamp_and_resolution(self):
        tmp = tempfile.mkdtemp(prefix="rfk_")
        try:
            hd = rf.RemoteFrameSource("https://youtu.be/jkSiX___Qwc",
                                      height=1080, cache_root=tmp,
                                      resolver=_local_resolver)
            sd = rf.RemoteFrameSource("https://youtu.be/jkSiX___Qwc",
                                      height=480, cache_root=tmp,
                                      resolver=_local_resolver)
            self.assertNotEqual(hd.path_for(60), sd.path_for(60),
                                "resolution must be part of the key")
            self.assertNotEqual(hd.path_for(60), hd.path_for(61),
                                "timestamp must be part of the key")
            self.assertEqual(hd.path_for(60), hd.path_for(60.0),
                             "the same request must resolve to one path")
            self.assertIn("jkSiX___Qwc", hd.path_for(60))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestFormatPolicy(unittest.TestCase):
    def test_it_asks_for_video_only_high_quality_first(self):
        ladder = rf.format_ladder(1080)
        self.assertTrue(ladder[0].startswith("bestvideo[height<=1080]"),
                        f"first rung should be 1080p video-only: {ladder[0]}")
        self.assertTrue(any("bestvideo" in f for f in ladder))

    def test_audio_is_never_requested(self):
        for f in rf.format_ladder(1080):
            self.assertNotIn("+ba", f)
            self.assertNotIn("bestaudio", f)


# ------------------------------------------------- the real thing, over HTTP
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg is not installed")
class TestSparseAcquisitionOverHttp(unittest.TestCase):
    """Real ffmpeg, real HTTP range requests, measured bytes."""

    @classmethod
    def setUpClass(cls):
        cls.url, cls.server = _serve()
        cls.vod = _STATE["vod"]
        cls.vod_bytes = os.path.getsize(cls.vod)

    def setUp(self):
        self.cache = tempfile.mkdtemp(prefix="rfc_")
        self.server.reset()

    def tearDown(self):
        shutil.rmtree(self.cache, ignore_errors=True)

    def source(self, **kw):
        return rf.RemoteFrameSource(self.url, height=1080,
                                    cache_root=self.cache,
                                    resolver=_local_resolver, **kw)

    def test_a_sparse_scan_does_not_download_the_whole_vod(self):
        """THE claim. Ten frames spread over the broadcast must cost a
        fraction of the file, measured at the server."""
        src = self.source()
        offsets = [float(t) for t in range(0, int(FIXTURE_DURATION), 60)]
        got = src.grab(offsets)
        self.assertEqual(sum(1 for p in got.values() if p), len(offsets),
                         "every sampled offset should produce a frame")
        served = self.server.total_served()
        self.assertLess(served, self.vod_bytes * 0.5,
                        f"sparse scan pulled {served / 1e6:.1f} MB of a "
                        f"{self.vod_bytes / 1e6:.1f} MB file — that is not "
                        f"sparse")
        # and it must be one yt-dlp resolve at most, never one per frame
        self.assertLessEqual(src.stats["ytdlpCalls"], 1)

    def test_the_cache_is_reused_and_costs_nothing(self):
        offsets = [60.0, 120.0, 180.0]
        first = self.source()
        first.grab(offsets)
        self.assertEqual(first.stats["framesFromCache"], 0)

        self.server.reset()
        second = self.source()
        got = second.grab(offsets)
        self.assertEqual(sum(1 for p in got.values() if p), len(offsets))
        self.assertEqual(second.stats["framesFromCache"], len(offsets))
        self.assertEqual(second.stats["ffmpegCalls"], 0,
                         "a cached frame must not launch ffmpeg")
        self.assertEqual(self.server.total_served(), 0,
                         "a cached frame must not touch the network")

    def test_a_dense_burst_is_one_read_not_twenty(self):
        src = self.source()
        dense = [round(140.0 + i, 1) for i in range(20)]
        got = src.grab(dense)
        self.assertEqual(sum(1 for p in got.values() if p), 20)
        self.assertEqual(src.stats["ffmpegCalls"], 1,
                         "a contiguous 1s burst must be batched into one read")
        self.assertEqual(src.stats["rangeBatches"], 1)

    def test_a_partial_cache_only_pays_for_what_is_missing(self):
        """Densification must not re-fetch what the coarse pass already got."""
        src = self.source()
        src.grab([120.0, 180.0])
        self.server.reset()
        src2 = self.source()
        src2.grab([120.0, 150.0, 180.0])
        self.assertEqual(src2.stats["framesFromCache"], 2)
        self.assertEqual(src2.stats["ffmpegCalls"], 1,
                         "only the new offset should be fetched")

    def test_the_manifest_records_provenance_but_never_the_signed_url(self):
        import json
        src = self.source(source_id="fixture")
        src.grab([60.0])
        with open(os.path.join(src.dir, "manifest.json"), encoding="utf-8") as f:
            man = json.load(f)
        self.assertEqual(man["schema"], "remote-frames.v1")
        self.assertEqual(man["requestedHeight"], 1080)
        self.assertEqual(man["sourceId"], "fixture")
        self.assertIn("frames", man)
        blob = json.dumps(man)
        for secret in ("signature=", "&sig=", "pot=", "expire="):
            self.assertNotIn(secret, blob)

    def test_a_frame_that_cannot_be_read_is_none_not_an_exception(self):
        """One unreachable instant must not lose the other nineteen."""
        src = self.source()
        got = src.grab([60.0, FIXTURE_DURATION + 500.0])
        self.assertIsNotNone(got[60.0])
        self.assertIsNone(got[FIXTURE_DURATION + 500.0])


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg is not installed")
class TestNoFullDownloadEverHappens(unittest.TestCase):
    """A guard with teeth: fail if any single HTTP response carries the
    whole file, however the code is refactored later."""

    @classmethod
    def setUpClass(cls):
        cls.url, cls.server = _serve()
        cls.vod_bytes = os.path.getsize(_STATE["vod"])

    def test_no_single_request_transfers_the_whole_file(self):
        cache = tempfile.mkdtemp(prefix="rfn_")
        try:
            self.server.reset()
            src = rf.RemoteFrameSource(self.url, cache_root=cache,
                                       resolver=_local_resolver)
            src.grab([30.0, 200.0, 450.0])
            served = self.server.total_served()
            self.assertLess(
                served, self.vod_bytes,
                f"three frames pulled {served} bytes of a "
                f"{self.vod_bytes}-byte file — something is downloading "
                f"the VOD")
        finally:
            shutil.rmtree(cache, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
