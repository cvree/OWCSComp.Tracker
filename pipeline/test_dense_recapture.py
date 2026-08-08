#!/usr/bin/env python3
"""
test_dense_recapture.py — the adaptive full-map pass must not have changed.

`ingest_map` reads a map with a coarse baseline sweep and then a DENSE
one-second recapture around every suspected hero change. That two-speed
design is the thing that makes the hero timeline accurate, and the sparse
acquisition work must not have touched it — the brief was explicit that
30/60-second sampling must never replace it.

Two things changed around it, and this suite pins both:

  1. **Batched prefetch.** The dense pass used to launch one ffmpeg per
     second of window. It now pulls each contiguous run in one call. The
     test that matters is not "it is faster" but "the frames are the same":
     this suite extracts the same offsets both ways from a real clip built
     from the repository's committed OWCS frames and requires the results
     to be BYTE-IDENTICAL. If they ever diverge, detection and swap
     accuracy could diverge with them, and the batching must go.

  2. **A remote backend.** `FrameServer` can now source those offsets by
     HTTP range instead of from a downloaded clip. The local path must be
     completely unaffected by that existing.

Run: python3 pipeline/test_dense_recapture.py
"""
from __future__ import annotations

import glob
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import ingest_map as im  # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
FRAMES = os.path.join(REPO, "reports", "ingest", "qad-twis-nepal", "frames")

_STATE: dict = {}


def _clip() -> str:
    """A short real-broadcast clip, built once."""
    if "clip" in _STATE:
        return _STATE["clip"]
    import cv2
    tmp = tempfile.mkdtemp(prefix="dense_")
    _STATE["tmp"] = tmp
    srcs = sorted(glob.glob(os.path.join(FRAMES, "*.jpg")))
    imgs = [cv2.imread(p) for p in srcs]
    imgs = [i for i in imgs if i is not None]
    stage = os.path.join(tmp, "stage")
    os.makedirs(stage)
    for i in range(120):                       # 60s at 2fps
        cv2.imwrite(os.path.join(stage, f"f{i:05d}.png"), imgs[i % len(imgs)])
    out = os.path.join(tmp, "clip.mp4")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-framerate", "2", "-i", os.path.join(stage, "f%05d.png"),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
         "-pix_fmt", "yuv420p", "-g", "2", "-keyint_min", "2",
         "-sc_threshold", "0", out], check=True)
    shutil.rmtree(stage, ignore_errors=True)
    _STATE["clip"] = out
    return out


def tearDownModule():                              # noqa: N802
    if _STATE.get("tmp"):
        shutil.rmtree(_STATE["tmp"], ignore_errors=True)


def _digest(directory: str) -> tuple[str, list[str]]:
    names = sorted(f for f in os.listdir(directory)
                   if f.startswith("t") and f.endswith(".jpg"))
    h = hashlib.sha256()
    for fn in names:
        h.update(fn.encode("utf-8"))
        with open(os.path.join(directory, fn), "rb") as f:
            h.update(f.read())
    return h.hexdigest(), names


class TestTheDenseDesignIsIntact(unittest.TestCase):
    """Static guards — the shape of the adaptive pass, without a video."""

    def test_the_dense_step_is_still_one_second(self):
        self.assertEqual(im.DENSE_STEP, 1.0,
                         "the dense recapture must stay at 1s; coarser "
                         "sampling is exactly what it exists to avoid")

    def test_the_baseline_is_still_a_separate_coarser_pass(self):
        with open(os.path.join(HERE, "ingest_map.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("extract_baseline", src)
        self.assertIn("dense_windows", src)
        self.assertIn("change_windows", src,
                      "dense windows must still be derived from suspected "
                      "slot changes, not from a fixed schedule")

    def test_dense_windows_still_come_from_disagreeing_reads(self):
        track = [
            {"t": 10.0, "hero": "tracer"}, {"t": 15.0, "hero": "tracer"},
            {"t": 20.0, "hero": "genji"}, {"t": 25.0, "hero": "genji"},
        ]
        windows = im.change_windows(track)
        self.assertEqual(windows, [(15.0, 20.0)],
                         "the window must bracket the change, and only it")

    def test_contiguous_runs_split_on_any_gap(self):
        runs = im._contiguous_runs([10.0, 11.0, 12.0, 20.0, 21.0], 1.0)
        self.assertEqual(runs, [[10.0, 11.0, 12.0], [20.0, 21.0]])


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg is not installed")
class TestBatchedPrefetchIsEquivalent(unittest.TestCase):
    """The claim that lets batching be the default."""

    @classmethod
    def setUpClass(cls):
        cls.clip = _clip()
        cls.work = tempfile.mkdtemp(prefix="densew_")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def test_batched_and_per_frame_extraction_are_byte_identical(self):
        ts = [round(10.0 + i, 1) for i in range(20)]

        one_at_a_time = os.path.join(self.work, "single")
        fs_a = im.FrameServer(self.clip, 0.0, one_at_a_time)
        for t in ts:
            fs_a.get(t)
        digest_a, names_a = _digest(one_at_a_time)

        batched = os.path.join(self.work, "batched")
        fs_b = im.FrameServer(self.clip, 0.0, batched)
        fs_b.prefetch(ts, step=im.DENSE_STEP)
        for t in ts:
            fs_b.get(t)
        digest_b, names_b = _digest(batched)

        self.assertEqual(names_a, names_b, "different frames were produced")
        self.assertEqual(len(names_a), len(ts), "some frames went missing")
        self.assertEqual(
            digest_a, digest_b,
            "batched dense extraction produced DIFFERENT pixels from the "
            "per-frame path — detection and swap accuracy could differ, so "
            "batching must not be the default until this holds")

    def test_batching_actually_collapses_the_processes(self):
        ts = [round(30.0 + i, 1) for i in range(15)]
        d = os.path.join(self.work, "count")
        fs = im.FrameServer(self.clip, 0.0, d)
        fs.prefetch(ts, step=im.DENSE_STEP)
        self.assertEqual(fs.stats["batchedReads"], 1)
        self.assertEqual(fs.stats["singleReads"], 0)
        self.assertEqual(fs.stats["framesFromBatch"], len(ts))

    def test_a_pair_of_frames_is_not_worth_a_filter_graph(self):
        d = os.path.join(self.work, "pair")
        fs = im.FrameServer(self.clip, 0.0, d)
        fs.prefetch([12.0, 13.0], step=1.0)
        self.assertEqual(fs.stats["batchedReads"], 0)
        self.assertEqual(fs.stats["singleReads"], 2)

    def test_prefetch_is_optional_and_get_still_works_alone(self):
        d = os.path.join(self.work, "noprefetch")
        fs = im.FrameServer(self.clip, 0.0, d)
        self.assertIsNotNone(fs.get(20.0))
        self.assertEqual(fs.stats["batchedReads"], 0)

    def test_prefetch_never_refetches_what_is_already_cached(self):
        d = os.path.join(self.work, "reuse")
        fs = im.FrameServer(self.clip, 0.0, d)
        ts = [round(40.0 + i, 1) for i in range(5)]
        fs.prefetch(ts, step=1.0)
        again = im.FrameServer(self.clip, 0.0, d)
        made = again.prefetch(ts, step=1.0)
        self.assertEqual(made, 0)
        self.assertEqual(again.stats["batchedReads"], 0)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg is not installed")
class TestRemoteBackedFrameServer(unittest.TestCase):
    """The dense pass can run without a downloaded clip at all."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(HERE, "fixtures"))
        import range_server
        cls.clip = _clip()
        cls.server_mod = range_server
        cls.httpd, port = range_server.serve(os.path.dirname(cls.clip))
        cls.url = f"http://127.0.0.1:{port}/{os.path.basename(cls.clip)}"
        cls.work = tempfile.mkdtemp(prefix="densr_")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.work, ignore_errors=True)

    def _remote(self, cache):
        import remote_frames as rf
        return rf.RemoteFrameSource(self.url, cache_root=cache,
                                    resolver=lambda u, h: (u, "test"))

    def test_a_dense_window_can_be_fetched_without_a_clip(self):
        cache = os.path.join(self.work, "cache1")
        fs = im.FrameServer(None, 0.0, os.path.join(self.work, "rframes"),
                            remote_source=self._remote(cache))
        ts = [round(10.0 + i, 1) for i in range(10)]
        made = fs.prefetch(ts, step=1.0)
        self.assertEqual(made, len(ts))
        for t in ts:
            self.assertTrue(os.path.exists(fs.path_for(t)))

    def test_a_frame_server_needs_a_clip_or_a_remote_source(self):
        with self.assertRaises(ValueError):
            im.FrameServer(None, 0.0, os.path.join(self.work, "nope"))

    def test_the_remote_path_produces_readable_frames_of_the_right_size(self):
        import cv2
        cache = os.path.join(self.work, "cache2")
        fs = im.FrameServer(None, 0.0, os.path.join(self.work, "rframes2"),
                            remote_source=self._remote(cache))
        path = fs.get(20.0)
        self.assertIsNotNone(path)
        img = cv2.imread(path)
        self.assertIsNotNone(img)
        self.assertEqual(img.shape[:2], (720, 1280))


if __name__ == "__main__":
    unittest.main(verbosity=2)
