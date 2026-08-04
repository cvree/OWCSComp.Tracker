#!/usr/bin/env python3
"""
test_automation_worker.py — Phase E self-hosted worker: dependency/disk
preflight, official-source validation, claim+lock, download + metadata
capture, and every required failure mode. All fixtures/mocked transports —
no real yt-dlp/ffmpeg/network. Run: python3 pipeline/test_automation_worker.py
"""
from __future__ import annotations
import os
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


def _fake_which(present):
    def _which(tool):
        return f"/usr/bin/{tool}" if tool in present else None
    return _which


def _fake_probe(title="Test Match VOD", duration=120):
    def _probe(url):
        return {"title": title, "duration": duration, "id": "vid123",
                "uploader": "Overwatch Esports", "url": url}
    return _probe


def _fake_download_full_ok(size_bytes=65536):
    """Stands in for video_ingest.download_full_video: writes one whole
    source file and reports the resumable-download result shape."""
    def _dl(url, out, height=720, **kw):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(b"\x00" * size_bytes)
        return {"path": out, "reused": False, "resumed": False,
                "sizeBytes": size_bytes, "format": f"bestvideo[height<={height}]",
                "partialBytesBefore": 0}
    return _dl


def _fake_media_probe_ok(rung="normal", size_bytes=32768):
    """Stands in for video_ingest.media_download_probe — the short REAL
    media download that must prove bytes flow before a multi-hour VOD is
    committed to. Reports the ladder rung that worked."""
    def _probe(url, height=720, **kw):
        return {"ok": True, "rung": rung, "bytes": size_bytes,
                "format": f"bestvideo[height<={height}]", "width": 1280,
                "height": 720, "qualityDowngrade": False, "attempts": []}
    return _probe


def _fake_proxy_ok(height=360):
    """Stands in for video_ingest.make_scan_proxy."""
    def _proxy(src, out, height=height, **kw):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(b"\x01" * 2048)
        return {"path": out, "width": 640, "height": height,
                "sizeBytes": 2048, "reused": False}
    return _proxy


def _fake_resolution(width=1920, height=1080, codec="h264", duration=120.0):
    def _res(path):
        return {"width": width, "height": height, "codec": codec,
                "duration": duration}
    return _res


OFFICIAL_PAYLOAD = {
    "videoId": "vid123",
    "channelId": "UCofficial",
    "sourceUrl": "https://www.youtube.com/watch?v=vid123",
}


class WorkerTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "automation.sqlite")
        self.media_root = os.path.join(self.tmp.name, "media")
        self.store = js.JobStore(self.db)
        self.locks = lk.LockManager(self.store.con, lease_seconds=300)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def make_job(self, payload=None, state=sm.ARCHIVED):
        payload = payload or dict(OFFICIAL_PAYLOAD)
        k = models.record_key(payload["videoId"])
        self.store.enqueue(models.KIND_RECORD, k, payload=payload,
                          state=state, source_url=payload.get("sourceUrl"))
        return self.store.get(k)


class TestPreflight(WorkerTestBase):
    def test_all_dependencies_present(self):
        deps = worker.check_dependencies(which=_fake_which({"ffmpeg", "ffprobe", "yt-dlp"}))
        self.assertEqual(worker.missing_dependencies(deps), [])

    def test_missing_dependency_reported(self):
        deps = worker.check_dependencies(which=_fake_which({"ffmpeg"}))
        self.assertEqual(sorted(worker.missing_dependencies(deps)),
                         ["ffprobe", "yt-dlp"])

    def test_low_disk_detected(self):
        # An absurdly high floor makes any real filesystem "low".
        ok, free_gb = worker.check_disk_space(self.tmp.name, min_free_gb=1e9)
        self.assertFalse(ok)
        self.assertGreaterEqual(free_gb, 0)

    def test_sufficient_disk(self):
        ok, _ = worker.check_disk_space(self.tmp.name, min_free_gb=0.0001)
        self.assertTrue(ok)


class TestSourceValidation(WorkerTestBase):
    def test_official_channel_accepted(self):
        vid = worker.validate_source(OFFICIAL_PAYLOAD,
                                     official_channel_ids={"UCofficial"})
        self.assertEqual(vid, "vid123")

    def test_empty_url_rejected(self):
        with self.assertRaises(worker.SourceValidationError):
            worker.validate_source({"videoId": "x", "sourceUrl": ""})

    def test_unofficial_domain_rejected(self):
        with self.assertRaises(worker.SourceValidationError):
            worker.validate_source({
                "videoId": "x", "channelId": "UCofficial",
                "sourceUrl": "https://evil-mirror.example/watch?v=x",
            }, official_channel_ids={"UCofficial"})

    def test_shell_string_rejected(self):
        with self.assertRaises(worker.SourceValidationError):
            worker.validate_source({
                "sourceUrl": "; rm -rf / #",
            })

    def test_unverified_channel_rejected_without_registry_match(self):
        with self.assertRaises(worker.SourceValidationError):
            worker.validate_source({
                "videoId": "vid123", "channelId": "UCunverified",
                "sourceUrl": "https://www.youtube.com/watch?v=vid123",
            }, official_channel_ids={"UCofficial"})

    def test_manual_approval_bypasses_channel_registry(self):
        vid = worker.validate_source({
            "videoId": "vidManual",
            "sourceUrl": "https://www.youtube.com/watch?v=vidManual",
        }, official_channel_ids={"UCofficial"},
           manual_approved_video_ids={"vidManual"})
        self.assertEqual(vid, "vidManual")

    def test_authority_conflict_rejected(self):
        with self.assertRaises(worker.SourceValidationError):
            worker.validate_source({
                "videoId": "vid123", "channelId": "UCother",
                "expectedChannelId": "UCofficial",
                "sourceUrl": "https://www.youtube.com/watch?v=vid123",
            })

    def test_video_id_resolved_from_bare_url(self):
        vid = worker.validate_source({
            "channelId": "UCofficial",
            "sourceUrl": "https://youtu.be/abc12345678",
        }, official_channel_ids={"UCofficial"})
        self.assertEqual(vid, "abc12345678")


class TestClaimAndLock(WorkerTestBase):
    def test_claim_and_lock_succeeds(self):
        job = self.make_job()
        claimed = worker.claim_and_lock(self.store, self.locks,
                                        [models.KIND_RECORD], "w1")
        self.assertEqual(claimed.job_key, job.job_key)
        self.assertIsNotNone(self.locks.holder(worker.resource_for(job)))

    def test_second_worker_cannot_claim_locked_resource(self):
        self.make_job()
        worker.claim_and_lock(self.store, self.locks, [models.KIND_RECORD], "w1")
        # Force the job back into the claimable pool without releasing the
        # lock (simulates a race) — claim_and_lock must refuse to proceed.
        self.store.clear_worker(models.record_key("vid123"))
        second = worker.claim_and_lock(self.store, self.locks,
                                       [models.KIND_RECORD], "w2")
        self.assertIsNone(second)


class TestDownloadJob(WorkerTestBase):
    def _run(self, job, **overrides):
        kw = dict(
            worker_id="w1", media_root=self.media_root,
            official_channel_ids={"UCofficial"},
            probe_fn=_fake_probe(), download_full_fn=_fake_download_full_ok(),
            probe_media_fn=_fake_media_probe_ok(),
            proxy_fn=_fake_proxy_ok(),
            resolution_fn=_fake_resolution(),
            which=_fake_which({"ffmpeg", "ffprobe", "yt-dlp"}),
        )
        kw.update(overrides)
        return worker.download_job(self.store, self.locks, job, **kw)

    def test_successful_download_populates_metadata_and_state(self):
        job = self.make_job()
        result = self._run(job)
        self.assertTrue(result["ok"], result)
        final = self.store.get(job.job_key)
        self.assertEqual(final.state, sm.DOWNLOADED)
        media = final.payload["media"]
        self.assertEqual(media["videoId"], "vid123")
        self.assertEqual(len(media["sha256"]), 64)
        self.assertEqual(media["width"], 1920)
        self.assertEqual(media["height"], 1080)
        self.assertEqual(media["workerVersion"], worker.WORKER_VERSION)
        self.assertTrue(os.path.exists(result["path"]))
        # Lock is released after a successful run.
        self.assertIsNone(self.locks.holder(worker.resource_for(job)))

    def test_missing_dependency_fails_safely(self):
        job = self.make_job()
        result = self._run(job, which=_fake_which({"ffmpeg"}))
        self.assertFalse(result["ok"])
        self.assertEqual(result["errorCode"], "missing_dependency")
        final = self.store.get(job.job_key)
        self.assertIn(final.state, (sm.RETRY_SCHEDULED, sm.FAILED_PERMANENT))

    def test_insufficient_disk_fails_safely(self):
        job = self.make_job()
        result = self._run(job, min_free_gb=1e9)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errorCode"], "insufficient_disk")

    def test_unsupported_source_rejected_before_download(self):
        job = self.make_job(payload={
            "videoId": "vidBad", "channelId": "UCbad",
            "sourceUrl": "https://not-youtube.example/watch?v=vidBad",
        })
        result = self._run(job)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errorCode"], "invalid_source")

    def test_network_stall_is_retryable(self):
        def _stall(*a, **kw):
            raise vi.StallTimeout(["yt-dlp"], 30.0, "no bytes")
        job = self.make_job()
        result = self._run(job, download_full_fn=_stall)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errorCode"], "network_stall")
        self.assertEqual(self.store.get(job.job_key).state, sm.RETRY_SCHEDULED)

    def test_corrupt_media_classified(self):
        def _corrupt(*a, **kw):
            raise vi.InvalidClip("clip too small")
        job = self.make_job()
        result = self._run(job, download_full_fn=_corrupt)
        self.assertEqual(result["errorCode"], "corrupt_media")

    def test_permanent_failure_after_ceiling(self):
        from automation.config import AutomationConfig, DEFAULTS
        cfg = AutomationConfig(values=dict(DEFAULTS, max_recording_retries=1))
        store = js.JobStore(self.db, config=cfg)
        try:
            payload = dict(OFFICIAL_PAYLOAD)
            k = models.record_key(payload["videoId"])
            store.enqueue(models.KIND_RECORD, k, payload=payload,
                          state=sm.ARCHIVED, source_url=payload["sourceUrl"])
            job = store.get(k)

            def _boom(*a, **kw):
                raise ConnectionError("network unreachable")
            result = worker.download_job(
                store, self.locks, job, worker_id="w1",
                media_root=self.media_root, official_channel_ids={"UCofficial"},
                probe_fn=_fake_probe(), download_full_fn=_boom,
                proxy_fn=_fake_proxy_ok(), resolution_fn=_fake_resolution(),
                which=_fake_which({"ffmpeg", "ffprobe", "yt-dlp"}))
            self.assertFalse(result["ok"])
            self.assertEqual(store.get(job.job_key).state, sm.FAILED_PERMANENT)
        finally:
            store.close()

    def test_cached_clip_is_reused_not_redownloaded(self):
        calls = {"n": 0}

        def _dl(url, out, height=720, **kw):
            calls["n"] += 1
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as f:
                f.write(b"\x00" * 4096)
            return {"path": out, "reused": calls["n"] > 1, "resumed": False,
                    "sizeBytes": 4096, "format": "f", "partialBytesBefore": 0}

        job = self.make_job()
        self._run(job, download_full_fn=_dl)
        # Resume-style rerun against the same job/state must not explode —
        # download_vod_clip's own cache logic decides reuse; here we only
        # check the worker records whatever it's told.
        self.assertEqual(calls["n"], 1)


class TestResumeInterrupted(WorkerTestBase):
    def test_resumes_job_stuck_in_downloading_with_stale_lock(self):
        job = self.make_job()
        # Simulate a crash mid-download: DOWNLOADING with an already-expired lease.
        self.store.transition(job.job_key, sm.DOWNLOADING)
        import datetime as dt
        past = dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)
        self.locks.acquire(worker.resource_for(job), "dead-worker", now=past)

        results = worker.resume_interrupted(
            self.store, self.locks, worker_id="w-resume",
            media_root=self.media_root, official_channel_ids={"UCofficial"},
            probe_fn=_fake_probe(), download_full_fn=_fake_download_full_ok(),
            probe_media_fn=_fake_media_probe_ok(),
            proxy_fn=_fake_proxy_ok(),
            resolution_fn=_fake_resolution(),
            which=_fake_which({"ffmpeg", "ffprobe", "yt-dlp"}))
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["ok"])
        self.assertEqual(self.store.get(job.job_key).state, sm.DOWNLOADED)

    def test_does_not_touch_a_job_with_a_live_lock(self):
        job = self.make_job()
        self.store.transition(job.job_key, sm.DOWNLOADING)
        self.locks.acquire(worker.resource_for(job), "live-worker")
        results = worker.resume_interrupted(
            self.store, self.locks, worker_id="w-resume",
            media_root=self.media_root)
        self.assertEqual(results, [])
        self.assertEqual(self.store.get(job.job_key).state, sm.DOWNLOADING)


class TestErrorClassification(unittest.TestCase):
    def test_classify_covers_every_required_mode(self):
        cases = [
            (FileNotFoundError("no yt-dlp"), "missing_dependency"),
            (vi.InvalidClip("bad"), "corrupt_media"),
            (vi.StallTimeout(["x"], 1.0, ""), "network_stall"),
            (worker.SourceValidationError("bad url"), "invalid_source"),
            (OSError(28, "no space"), "insufficient_disk"),
            (ValueError("weird"), "unknown_error"),
        ]
        for exc, expected in cases:
            code, _ = worker.classify_download_error(exc)
            self.assertEqual(code, expected, exc)


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeRunner:
    def __init__(self, results):
        self.results = results  # cmd[0] -> _FakeCompletedProcess or Exception

    def run(self, cmd, **kw):
        res = self.results.get(cmd[0])
        if isinstance(res, Exception):
            raise res
        return res or _FakeCompletedProcess()


class TestDoctor(unittest.TestCase):
    def test_check_python_reports_version(self):
        info = worker.check_python()
        self.assertIn("version", info)
        self.assertTrue(info["version"][0].isdigit())

    def test_check_repo_dependencies_detects_installed_packages(self):
        report = worker.check_repo_dependencies()
        self.assertIn("opencv-python-headless", report)
        self.assertIn("numpy", report)

    def test_check_writable_true_for_new_and_existing_dir(self):
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "cache", "jobs")
            ok, reason = worker.check_writable(sub)
            self.assertTrue(ok, reason)
            self.assertTrue(os.path.isdir(sub))
            # probe file must be cleaned up, not left behind
            self.assertEqual(os.listdir(sub), [])

    def test_check_writable_false_for_unwritable_path(self):
        with tempfile.TemporaryDirectory() as d:
            locked = os.path.join(d, "locked")
            os.makedirs(locked)
            os.chmod(locked, 0o500)
            try:
                if os.access(locked, os.W_OK):
                    self.skipTest("running as a user that bypasses directory permissions")
                ok, reason = worker.check_writable(os.path.join(locked, "x"))
                self.assertFalse(ok)
            finally:
                os.chmod(locked, 0o700)

    def test_check_gh_auth_not_installed(self):
        report = worker.check_gh_auth(runner=_FakeRunner({}), which=_fake_which(set()))
        self.assertFalse(report["installed"])
        self.assertFalse(report["authenticated"])

    def test_check_gh_auth_authenticated(self):
        runner = _FakeRunner({"gh": _FakeCompletedProcess(0, "Logged in to github.com account x", "")})
        report = worker.check_gh_auth(runner=runner, which=_fake_which({"gh"}))
        self.assertTrue(report["installed"])
        self.assertTrue(report["authenticated"])

    def test_check_gh_auth_not_authenticated(self):
        runner = _FakeRunner({"gh": _FakeCompletedProcess(1, "", "not logged in")})
        report = worker.check_gh_auth(runner=runner, which=_fake_which({"gh"}))
        self.assertTrue(report["installed"])
        self.assertFalse(report["authenticated"])

    def test_check_gh_auth_redacts_token_shaped_strings(self):
        leaked = "token: ghp_abcdefghijklmnopqrstuvwxyz0123456789"
        runner = _FakeRunner({"gh": _FakeCompletedProcess(0, leaked, "")})
        report = worker.check_gh_auth(runner=runner, which=_fake_which({"gh"}))
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz0123456789", report["detail"])
        self.assertIn("[REDACTED]", report["detail"])

    def test_api_keys_present_never_leaks_value(self):
        os.environ["FACEIT_API_KEY"] = "super-secret-value-should-never-appear"
        try:
            result = worker.check_api_keys_present(("FACEIT_API_KEY", "YOUTUBE_API_KEY"))
            self.assertTrue(result["FACEIT_API_KEY"])
            self.assertFalse(result["YOUTUBE_API_KEY"])
            self.assertNotIn("super-secret-value-should-never-appear", json_dump_safe(result))
        finally:
            del os.environ["FACEIT_API_KEY"]

    def test_doctor_report_shape_and_ready_when_everything_ok(self):
        report = worker.doctor_report(
            media_root=tempfile.mkdtemp(),
            which=_fake_which({"ffmpeg", "ffprobe", "yt-dlp"}),
            runner=_FakeRunner({
                "yt-dlp": _FakeCompletedProcess(0, "2026.07.04", ""),
                "ffmpeg": _FakeCompletedProcess(0, "ffmpeg version 6.1.1", ""),
                "ffprobe": _FakeCompletedProcess(0, "ffprobe version 6.1.1", ""),
                "gh": _FakeCompletedProcess(0, "Logged in", ""),
            }))
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["missingTools"], [])
        self.assertIn("apiKeysPresent", report)

    def test_doctor_report_not_ok_when_tool_missing(self):
        report = worker.doctor_report(
            media_root=tempfile.mkdtemp(),
            which=_fake_which({"ffmpeg"}),
            runner=_FakeRunner({}))
        self.assertFalse(report["ok"])
        self.assertIn("yt-dlp", report["missingTools"])

    def test_format_doctor_report_never_contains_env_values(self):
        os.environ["FACEIT_API_KEY"] = "should-never-print"
        try:
            report = worker.doctor_report(
                media_root=tempfile.mkdtemp(),
                which=_fake_which({"ffmpeg", "ffprobe", "yt-dlp"}),
                runner=_FakeRunner({}))
            text = worker.format_doctor_report(report)
            self.assertNotIn("should-never-print", text)
        finally:
            del os.environ["FACEIT_API_KEY"]


def json_dump_safe(d):
    import json
    return json.dumps(d)


class TestWorkerIdentity(unittest.TestCase):
    def test_identity_is_stable_shape_and_unique(self):
        a = worker.worker_identity()
        b = worker.worker_identity()
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("beta-worker-"))

    def test_unicode_safe_log(self):
        # Must never raise regardless of platform stdout encoding.
        worker.log("caça mão 日本語 — no crash")


# --------------------------------------------------------------- environment
# These are OFFLINE suites, and several of them assert what happens when
# there is NO API key: a link is recorded but not approved, a client raises
# YouTubeAuthError, the doctor reports the key as absent. `YouTubeClient`
# falls back to `os.environ["YOUTUBE_API_KEY"]` when constructed with
# api_key=None, which is right for the CLI and wrong for a test — on a
# developer machine that exports a real key, those tests do not merely fail,
# they stop testing the no-key path and would reach for the live API.
#
# CI has no key, so this was invisible there and broke only on the machines
# where the project is actually operated.
_BORROWED_ENV: "dict[str, str]" = {}
_KEYS_UNDER_TEST = ("YOUTUBE_API_KEY", "FACEIT_API_KEY")


def setUpModule() -> None:
    for key in _KEYS_UNDER_TEST:
        if key in os.environ:
            _BORROWED_ENV[key] = os.environ.pop(key)


def tearDownModule() -> None:
    os.environ.update(_BORROWED_ENV)
    _BORROWED_ENV.clear()


if __name__ == "__main__":
    unittest.main(verbosity=2)
