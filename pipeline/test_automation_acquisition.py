#!/usr/bin/env python3
"""
test_automation_acquisition.py — Phase 2 full-VOD acquisition + scan proxy.

Two kinds of test here, both offline:

  * Pure-logic tests (bitrate estimation, disk preflight, resume bookkeeping,
    proxy wiring) driven with fake runners/injected functions — no ffmpeg.
  * Real-media tests that GENERATE a tiny synthetic video with ffmpeg and
    transcode a real proxy from it. Those skip automatically when ffmpeg or
    ffprobe is unavailable, so CI on a bare runner still passes.

Run: python3 pipeline/test_automation_acquisition.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import video_ingest as vi  # noqa: E402
from automation import job_store as js  # noqa: E402
from automation import locks as lk  # noqa: E402
from automation import models  # noqa: E402
from automation import state_machine as sm  # noqa: E402
from automation import worker  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_test_video(path: str, *, seconds: int = 2, width: int = 640,
                    height: int = 360, fps: int = 10) -> str:
    """A real, decodable H.264 file — the honest input for a proxy test."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i",
         f"testsrc=size={width}x{height}:rate={fps}:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         path],
        check=True, capture_output=True)
    return path


class TestBitrateEstimation(unittest.TestCase):
    def test_known_heights_have_estimates(self):
        for h in (360, 480, 720, 1080):
            self.assertGreater(worker.estimated_bitrate_bps(h), 0)

    def test_estimate_scales_with_duration_and_height(self):
        one_hour_720 = worker.estimate_download_bytes(3600, 720)
        two_hour_720 = worker.estimate_download_bytes(7200, 720)
        one_hour_1080 = worker.estimate_download_bytes(3600, 1080)
        self.assertAlmostEqual(two_hour_720 / one_hour_720, 2.0, places=3)
        self.assertGreater(one_hour_1080, one_hour_720)

    def test_a_four_hour_720p_broadcast_is_a_plausible_number(self):
        # 4h12m at 5 Mbit/s video-only ~= 9.5GB, x1.5 safety ~= 14GB. The
        # point is the order of magnitude: a real OWCS day-long VOD must not
        # be estimated at "a few hundred MB" and sail through the check.
        est_gb = worker.estimate_download_bytes(4 * 3600 + 720, 720) / (1024 ** 3)
        self.assertGreater(est_gb, 8)
        self.assertLess(est_gb, 30)

    def test_unknown_duration_returns_none_not_a_guess(self):
        self.assertIsNone(worker.estimate_download_bytes(None, 720))
        self.assertIsNone(worker.estimate_download_bytes(0, 720))

    def test_floor_prevents_an_absurdly_small_estimate(self):
        self.assertGreaterEqual(worker.estimate_download_bytes(1, 720),
                                worker.MIN_ESTIMATED_BYTES)


class TestDiskPreflight(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_short_video_fits(self):
        report = worker.disk_preflight(self.tmp.name, duration_seconds=60,
                                       height=720, min_free_gb=0.0001)
        self.assertTrue(report["ok"], report)
        self.assertIsNotNone(report["neededGb"])
        self.assertIn("estimated", report["reason"])

    def test_absurdly_long_video_is_refused_before_download(self):
        # 1000 hours at 720p cannot fit on any dev machine.
        report = worker.disk_preflight(self.tmp.name,
                                       duration_seconds=1000 * 3600,
                                       height=720, min_free_gb=0.0001)
        self.assertFalse(report["ok"])
        self.assertIn("REFUSING before download", report["reason"])

    def test_unknown_duration_falls_back_to_the_flat_floor_and_says_so(self):
        report = worker.disk_preflight(self.tmp.name, duration_seconds=None,
                                       min_free_gb=0.0001)
        self.assertIsNone(report["neededGb"])
        self.assertIn("duration unknown", report["reason"])
        self.assertTrue(report["ok"])
        # ... and an unknown duration on a full disk still refuses.
        strict = worker.disk_preflight(self.tmp.name, duration_seconds=None,
                                       min_free_gb=1e9)
        self.assertFalse(strict["ok"])

    def test_flat_floor_still_applies_to_a_tiny_video(self):
        report = worker.disk_preflight(self.tmp.name, duration_seconds=5,
                                       height=720, min_free_gb=1e9)
        self.assertFalse(report["ok"])


class AcquisitionJobBase(unittest.TestCase):
    PAYLOAD = {
        "videoId": "vid123",
        "channelId": "UCofficial",
        "sourceUrl": "https://www.youtube.com/watch?v=vid123",
    }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = js.JobStore(os.path.join(self.tmp.name, "automation.sqlite"))
        self.locks = lk.LockManager(self.store.con, lease_seconds=300)
        self.media_root = os.path.join(self.tmp.name, "media")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def make_job(self, state=sm.ARCHIVED):
        key = models.record_key(self.PAYLOAD["videoId"])
        self.store.enqueue(models.KIND_RECORD, key, payload=dict(self.PAYLOAD),
                           state=state, source_url=self.PAYLOAD["sourceUrl"])
        return self.store.get(key)

    def run_download(self, job, **overrides):
        kw = dict(worker_id="w1", media_root=self.media_root,
                  official_channel_ids={"UCofficial"},
                  which=lambda t: f"/usr/bin/{t}",
                  min_free_gb=0.0001)
        kw.update(overrides)
        return worker.download_job(self.store, self.locks, job, **kw)


class TestDurationBeforeDownload(AcquisitionJobBase):
    def test_duration_is_probed_before_any_bytes_are_fetched(self):
        order: list[str] = []

        def probe(url):
            order.append("probe")
            return {"title": "t", "duration": 120}

        def download(url, out, height=720, **kw):
            order.append("download")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            open(out, "wb").write(b"\x00" * 8192)
            return {"path": out, "sizeBytes": 8192, "resumed": False,
                    "reused": False, "format": "f", "partialBytesBefore": 0}

        res = self.run_download(self.make_job(), probe_fn=probe,
                                download_full_fn=download,
                                proxy_fn=lambda s, o, **kw: (
                                    open(o, "wb").write(b"x"),
                                    {"path": o, "width": 640, "height": 360,
                                     "sizeBytes": 1, "reused": False})[1],
                                resolution_fn=lambda p: {
                                    "width": 1280, "height": 720,
                                    "codec": "h264", "duration": 120.0})
        self.assertTrue(res["ok"], res)
        self.assertEqual(order, ["probe", "download"])

    def test_download_is_refused_when_the_estimate_does_not_fit(self):
        calls = {"download": 0}

        def download(url, out, height=720, **kw):
            calls["download"] += 1
            raise AssertionError("must never be reached")

        res = self.run_download(
            self.make_job(),
            probe_fn=lambda url: {"title": "t", "duration": 1000 * 3600},
            download_full_fn=download)
        self.assertFalse(res["ok"])
        self.assertEqual(res["errorCode"], "insufficient_disk")
        self.assertEqual(calls["download"], 0)
        # The refusal is explainable, and recorded on the job.
        job = self.store.get(self.make_job().job_key)
        self.assertIn("neededGb", job.payload["diskPreflight"])
        self.assertFalse(job.payload["diskPreflight"]["ok"])
        # A refused job never entered DOWNLOADING.
        self.assertIn(job.state, (sm.RETRY_SCHEDULED, sm.FAILED_PERMANENT))


class TestFullDownloadMetadata(AcquisitionJobBase):
    def _ok_download(self, size=16384):
        def download(url, out, height=720, **kw):
            os.makedirs(os.path.dirname(out), exist_ok=True)
            open(out, "wb").write(b"\x00" * size)
            return {"path": out, "sizeBytes": size, "resumed": False,
                    "reused": False, "format": f"bestvideo[height<={height}]",
                    "partialBytesBefore": 0}
        return download

    def _ok_proxy(self):
        def proxy(src, out, height=360, **kw):
            os.makedirs(os.path.dirname(out), exist_ok=True)
            open(out, "wb").write(b"\x01" * 512)
            return {"path": out, "width": 640, "height": height,
                    "sizeBytes": 512, "reused": False}
        return proxy

    def test_every_required_metadata_field_is_recorded(self):
        res = self.run_download(
            self.make_job(),
            probe_fn=lambda url: {"title": "OWCS Day 1", "duration": 15120},
            download_full_fn=self._ok_download(),
            proxy_fn=self._ok_proxy(),
            resolution_fn=lambda p: {"width": 1280, "height": 720,
                                     "codec": "h264", "duration": 15120.0},
            runner=_VersionRunner())
        self.assertTrue(res["ok"], res)
        media = self.store.get(res["metadata"]["videoId"] and
                               models.record_key("vid123")).payload["media"]
        for field in ("sha256", "durationSeconds", "width", "height", "codec",
                      "sourceUrl", "ytDlpVersion", "ffmpegVersion",
                      "downloadedAt", "localPath", "container",
                      "requestedHeight", "formatSelector"):
            self.assertIsNotNone(media.get(field), field)
        self.assertEqual(len(media["sha256"]), 64)
        self.assertEqual(media["requestedHeight"], 720)
        self.assertEqual(media["durationSeconds"], 15120)
        self.assertEqual(media["sourceUrl"],
                         "https://www.youtube.com/watch?v=vid123")
        # Forward slashes only — a stored Windows backslash breaks every
        # place a path is joined with '/'.
        self.assertNotIn("\\", media["localPath"])

    def test_proxy_is_generated_and_recorded_separately(self):
        res = self.run_download(
            self.make_job(),
            probe_fn=lambda url: {"title": "t", "duration": 300},
            download_full_fn=self._ok_download(),
            proxy_fn=self._ok_proxy(),
            resolution_fn=lambda p: {"width": 1280, "height": 720,
                                     "codec": "h264", "duration": 300.0})
        job = self.store.get(models.record_key("vid123"))
        proxy = job.payload["media"]["proxy"]
        self.assertEqual(proxy["height"], 360)
        self.assertEqual(len(proxy["sha256"]), 64)
        # scan_path_for -> proxy; source_path_for -> full-resolution source.
        scan = worker.scan_path_for(job, repo_root=os.path.dirname(HERE))
        source = worker.source_path_for(job, repo_root=os.path.dirname(HERE))
        self.assertIn("proxy360p", scan)
        self.assertNotIn("proxy", os.path.basename(source))

    def test_proxy_failure_does_not_lose_the_download(self):
        def bad_proxy(src, out, **kw):
            raise vi.InvalidClip("proxy transcode produced garbage")
        res = self.run_download(
            self.make_job(),
            probe_fn=lambda url: {"title": "t", "duration": 300},
            download_full_fn=self._ok_download(),
            proxy_fn=bad_proxy,
            resolution_fn=lambda p: {"width": 1280, "height": 720,
                                     "codec": "h264", "duration": 300.0})
        self.assertTrue(res["ok"], res)          # the download still succeeded
        job = self.store.get(models.record_key("vid123"))
        self.assertEqual(job.state, sm.DOWNLOADED)
        self.assertEqual(job.payload["media"]["proxy"]["errorCode"],
                         "corrupt_media")
        # And a missing proxy is honestly None, never a silent fallback to
        # the full-resolution file.
        self.assertIsNone(worker.scan_path_for(job))

    def test_resume_is_recorded_as_a_resume_not_a_fresh_download(self):
        def resumed_download(url, out, height=720, **kw):
            os.makedirs(os.path.dirname(out), exist_ok=True)
            open(out, "wb").write(b"\x00" * 32768)
            return {"path": out, "sizeBytes": 32768, "resumed": True,
                    "reused": False, "format": "f",
                    "partialBytesBefore": 12345}
        self.run_download(
            self.make_job(),
            probe_fn=lambda url: {"title": "t", "duration": 300},
            download_full_fn=resumed_download, proxy_fn=self._ok_proxy(),
            resolution_fn=lambda p: {"width": 1280, "height": 720,
                                     "codec": "h264", "duration": 300.0})
        media = self.store.get(models.record_key("vid123")).payload["media"]
        self.assertTrue(media["resumed"])
        self.assertEqual(media["partialBytesBefore"], 12345)


class _VersionRunner:
    """Minimal runner that answers --version for the tool-version record."""
    def run(self, cmd, **kw):
        class R:
            returncode = 0
            stdout = f"{cmd[0]} version 1.2.3"
            stderr = ""
        return R()


@unittest.skipUnless(HAVE_FFMPEG, "needs ffmpeg + ffprobe")
class TestRealDownloadResumeAndProxy(unittest.TestCase):
    """The parts that must be proven against real media, not a fake."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_complete_existing_download_is_reused_not_refetched(self):
        out = os.path.join(self.tmp.name, "vod.mp4")
        make_test_video(out, seconds=1)

        class NeverRunner:
            def run(self, cmd, **kw):
                if cmd[0] == "yt-dlp":
                    raise AssertionError("must not re-download a complete file")
                return subprocess.run(cmd, capture_output=True, text=True,
                                      check=kw.get("check", False))
        res = vi.download_full_video("https://youtu.be/x", out,
                                     runner=NeverRunner())
        self.assertTrue(res["reused"])
        self.assertGreater(res["sizeBytes"], 1000)

    def test_a_partial_download_resumes_instead_of_restarting(self):
        out = os.path.join(self.tmp.name, "vod.mp4")
        part = out + ".part"
        os.makedirs(self.tmp.name, exist_ok=True)
        open(part, "wb").write(b"\x00" * 4096)   # a killed download's leftover
        seen = {"cmd": None}

        class FakeYtDlp:
            """Answers the yt-dlp call by finishing the file, and records the
            exact command so the resume flags can be asserted."""
            def run(self, cmd, **kw):
                if cmd[0] == "yt-dlp":
                    seen["cmd"] = list(cmd)
                    make_test_video(out, seconds=1)
                    class R:
                        returncode = 0
                        stdout = ""
                        stderr = ""
                    return R()
                return subprocess.run(cmd, capture_output=True, text=True,
                                      check=kw.get("check", False))
        res = vi.download_full_video("https://youtu.be/x", out,
                                     runner=FakeYtDlp())
        self.assertTrue(res["resumed"])
        self.assertEqual(res["partialBytesBefore"], 4096)
        self.assertIn("--continue", seen["cmd"])
        # The partial was NOT deleted before the resume attempt.
        self.assertNotIn("--force-overwrites", seen["cmd"])

    def test_one_pinned_format_so_a_resume_stays_valid(self):
        # The clip path walks a ladder and deletes partials between rungs;
        # the full-VOD path must not, or a resume would splice two formats.
        self.assertNotIn("worst", vi.full_vod_format(720))
        self.assertIn("height<=720", vi.full_vod_format(720))

    def test_real_proxy_generation_downscales_and_stays_decodable(self):
        src = make_test_video(os.path.join(self.tmp.name, "src.mp4"),
                              seconds=2, width=1280, height=720)
        out = os.path.join(self.tmp.name, "proxy.mp4")
        res = vi.make_scan_proxy(src, out, height=360)
        self.assertEqual(res["height"], 360)
        self.assertEqual(res["width"], 640)          # 16:9 preserved
        ok, reason = vi.probe_clip_valid(out)
        self.assertTrue(ok, reason)
        # A proxy is meaningfully smaller than its source.
        self.assertLess(os.path.getsize(out), os.path.getsize(src))

    def test_proxy_is_reused_on_a_rerun(self):
        src = make_test_video(os.path.join(self.tmp.name, "src.mp4"), seconds=1)
        out = os.path.join(self.tmp.name, "proxy.mp4")
        vi.make_scan_proxy(src, out, height=360)
        again = vi.make_scan_proxy(src, out, height=360)
        self.assertTrue(again["reused"])

    def test_proxy_refuses_a_missing_source(self):
        with self.assertRaises(FileNotFoundError):
            vi.make_scan_proxy(os.path.join(self.tmp.name, "nope.mp4"),
                               os.path.join(self.tmp.name, "p.mp4"))

    def test_frames_extracted_from_a_proxy_are_readable(self):
        """The proxy has to be usable by the scanning path, not just valid."""
        import capture
        src = make_test_video(os.path.join(self.tmp.name, "src.mp4"),
                              seconds=3, width=1280, height=720)
        proxy = os.path.join(self.tmp.name, "proxy.mp4")
        vi.make_scan_proxy(src, proxy, height=360)
        frames_dir = os.path.join(self.tmp.name, "frames")
        frames = capture.extract_frames(proxy, frames_dir, interval=1)
        self.assertGreaterEqual(len(frames), 2)
        import cv2
        img = cv2.imread(frames[0])
        self.assertIsNotNone(img)
        self.assertEqual(img.shape[0], 360)


if __name__ == "__main__":
    unittest.main(verbosity=2)
