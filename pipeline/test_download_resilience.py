#!/usr/bin/env python3
"""
test_download_resilience.py — the YouTube ingestion fallback system.

Covers, deterministically and with NO network, every behaviour the
match-day download path has to get right:

  * a 403 on rung 1 followed by success on the force-IPv4 rung;
  * a 403 followed by success on the configured browser-cookie rung;
  * every rung failing -> one classified `youtube_media_forbidden`;
  * a stale signed URL being refreshed rather than re-requested forever;
  * corrupt/foreign `.part` cleanup before a clean retry, and a VALID
    complete download never being deleted;
  * source approval surviving a retry (no second approval demanded);
  * sensitive arguments and signed URLs redacted everywhere;
  * no cookies file ever written to disk;
  * a missing template set / placeholder anchor failing BEFORE the
    download rather than after it.

Every yt-dlp invocation is a scripted fake: the tests assert on the exact
argv the pipeline builds, which is what makes "the cookie rung was used"
and "the value was redacted" checkable without a browser or a network.
Run: python3 pipeline/test_download_resilience.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import detection_assets as da  # noqa: E402
import video_ingest as vi  # noqa: E402
import ytdlp_opts as yo  # noqa: E402
from automation import job_store as js  # noqa: E402
from automation import locks as lk  # noqa: E402
from automation import ops  # noqa: E402
from automation import state_machine as sm  # noqa: E402
from automation import worker  # noqa: E402

FORBIDDEN = ("ERROR: unable to download video data: HTTP Error 403: "
             "Forbidden")


class FakeProc:
    def __init__(self, lines, rc):
        self.stdout = iter(l + "\n" for l in lines)
        self.returncode = rc
        self.pid = None

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        pass


class ScriptedRunner:
    """Records every argv; a `script` decides each call's (lines, rc) and
    may create the output file to simulate a real download."""

    def __init__(self, script):
        self.calls: list[list[str]] = []
        self.script = script

    def Popen(self, cmd, **kw):  # noqa: N802
        self.calls.append(list(cmd))
        lines, rc = self.script(len(self.calls) - 1, list(cmd))
        return FakeProc(lines, rc)

    def run(self, cmd, **kw):
        # ffprobe validity/resolution checks go through .run()
        self.calls.append(list(cmd))
        exe = os.path.basename(cmd[0])
        if exe.startswith("ffprobe"):
            if "codec_type" in " ".join(cmd):
                return subprocess.CompletedProcess(cmd, 0, "video\n", "")
            return subprocess.CompletedProcess(
                cmd, 0,
                json.dumps({"streams": [{"width": 1280, "height": 720,
                                         "codec_name": "h264"}],
                            "format": {"duration": "10.0"}}), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def argv_for(self, needle: str) -> list[list[str]]:
        return [c for c in self.calls if any(needle in t for t in c)]

    @property
    def ytdlp_calls(self) -> list[list[str]]:
        return [c for c in self.calls if os.path.basename(c[0]) == "yt-dlp"]


def _write_out(cmd: list[str], size: int = 200_000) -> None:
    """Create the file the -o argument names (simulates a real download)."""
    if "-o" not in cmd:
        return
    out = cmd[cmd.index("-o") + 1]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "wb") as f:
        f.write(b"\x00" * size)


class LadderTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "media", "vid.mp4")
        self.url = "https://www.youtube.com/watch?v=AAAAAAAAAAA"

    def tearDown(self):
        self.tmp.cleanup()

    def rungs_used(self, runner) -> list[str]:
        """Which ladder rung each yt-dlp call corresponds to, by argv."""
        out = []
        for cmd in runner.ytdlp_calls:
            if "--impersonate" in cmd:
                out.append("browser-cookies+impersonate")
            elif "--cookies-from-browser" in cmd:
                out.append("browser-cookies")
            elif "--force-ipv4" in cmd:
                out.append("force-ipv4")
            elif "--no-cache-dir" in cmd:
                out.append("refresh-signed-url")
            else:
                out.append("normal")
        return out


class TestForbiddenThenIpv4(LadderTestBase):
    def test_403_then_ipv4_succeeds(self):
        """Rungs 1 and 2 are refused; forcing IPv4 works. The download
        result must report the rung that actually worked."""
        def script(i, cmd):
            if "--force-ipv4" in cmd:
                _write_out(cmd)
                return (["[download] 100% of 200.00KiB"], 0)
            return ([FORBIDDEN], 1)

        runner = ScriptedRunner(script)
        auth = yo.AuthConfig()
        res = vi.download_full_video(self.url, self.out, runner=runner,
                                     auth=auth, stall_timeout=None)
        self.assertTrue(os.path.exists(self.out))
        self.assertEqual(res["rung"], yo.RUNG_IPV4)
        self.assertEqual(self.rungs_used(runner)[:3],
                         ["normal", "refresh-signed-url", "force-ipv4"])
        # the two failures are recorded with the precise code
        failed = [a for a in res["attempts"] if not a["ok"]]
        self.assertTrue(failed)
        self.assertTrue(all(a["errorCode"] == yo.ERR_FORBIDDEN
                            for a in failed if a.get("errorCode")))


class TestForbiddenThenCookies(LadderTestBase):
    def test_403_then_configured_browser_cookies_succeed(self):
        """Only the configured-cookie rung works. Cookies must be read
        FROM THE BROWSER (--cookies-from-browser) and no cookie file may
        ever be created."""
        def script(i, cmd):
            if "--cookies-from-browser" in cmd:
                _write_out(cmd)
                return (["[download] 100% of 200.00KiB"], 0)
            return ([FORBIDDEN], 1)

        runner = ScriptedRunner(script)
        auth = yo.load_auth_config({
            yo.ENV_COOKIES_FROM_BROWSER: "chrome",
            yo.ENV_BROWSER_PROFILE: "Profile 1"})
        self.assertEqual(auth.problems, [])
        res = vi.download_full_video(self.url, self.out, runner=runner,
                                     auth=auth, stall_timeout=None)
        self.assertEqual(res["rung"], yo.RUNG_COOKIES)
        cookie_calls = runner.argv_for("--cookies-from-browser")
        self.assertTrue(cookie_calls)
        # browser:profile spelling, assembled in exactly one place
        spec = cookie_calls[0][cookie_calls[0].index(
            "--cookies-from-browser") + 1]
        self.assertEqual(spec, "chrome:Profile 1")
        # ...and NOTHING was written to disk as a cookies file
        for root, _dirs, files in os.walk(self.tmp.name):
            for name in files:
                self.assertNotIn("cookie", name.lower(),
                                 f"a cookie file was created: {root}/{name}")
        self.assertEqual(runner.argv_for("--cookies"), cookie_calls,
                         "the file-based --cookies flag must never be used")

    def test_cookie_rungs_are_skipped_when_not_configured(self):
        """Default state: no browser access at all. The rungs still appear
        in the record — as explicit skips, never silent absences."""
        ladder = yo.build_ladder(yo.load_auth_config({}), have_curl_cffi=True)
        by_name = {r.name: r for r in ladder}
        self.assertFalse(by_name[yo.RUNG_COOKIES].runnable)
        self.assertFalse(by_name[yo.RUNG_COOKIES_IMPERSONATE].runnable)
        self.assertIn(yo.ENV_COOKIES_FROM_BROWSER,
                      by_name[yo.RUNG_COOKIES].skip_reason)
        for rung in ladder:
            self.assertNotIn("--cookies-from-browser", rung.args)

    def test_impersonation_rung_needs_curl_cffi(self):
        auth = yo.load_auth_config({yo.ENV_COOKIES_FROM_BROWSER: "edge",
                                    yo.ENV_IMPERSONATE: "chrome"})
        without = {r.name: r for r in yo.build_ladder(auth,
                                                      have_curl_cffi=False)}
        self.assertFalse(without[yo.RUNG_COOKIES_IMPERSONATE].runnable)
        self.assertIn("curl_cffi",
                      without[yo.RUNG_COOKIES_IMPERSONATE].skip_reason)
        with_cffi = {r.name: r for r in yo.build_ladder(auth,
                                                        have_curl_cffi=True)}
        self.assertTrue(with_cffi[yo.RUNG_COOKIES_IMPERSONATE].runnable)
        self.assertIn("--impersonate",
                      with_cffi[yo.RUNG_COOKIES_IMPERSONATE].args)


class TestEveryRungFails(LadderTestBase):
    def test_all_attempts_fail_gives_one_classified_error(self):
        runner = ScriptedRunner(lambda i, cmd: ([FORBIDDEN], 1))
        with self.assertRaises(vi.MediaDownloadError) as ctx:
            vi.download_full_video(self.url, self.out, runner=runner,
                                   auth=yo.AuthConfig(), stall_timeout=None)
        exc = ctx.exception
        self.assertEqual(exc.code, yo.ERR_FORBIDDEN)
        self.assertIn("cookies", exc.remedy.lower())
        # bounded: one attempt per rung, never a loop
        self.assertLessEqual(len(runner.ytdlp_calls), len(yo.LADDER_ORDER))
        self.assertEqual(len(set(map(tuple, runner.ytdlp_calls))),
                         len(runner.ytdlp_calls),
                         "the same command must not be retried verbatim")
        # every rung is accounted for in the record
        self.assertEqual(len(exc.attempts), len(yo.LADDER_ORDER))

    def test_unavailable_video_stops_the_ladder_early(self):
        """A permanently unavailable video is not worth walking: every
        remaining rung would fail identically."""
        runner = ScriptedRunner(
            lambda i, cmd: (["ERROR: Video unavailable"], 1))
        with self.assertRaises(vi.MediaDownloadError) as ctx:
            vi.download_full_video(self.url, self.out, runner=runner,
                                   auth=yo.AuthConfig(), stall_timeout=None)
        self.assertEqual(ctx.exception.code, yo.ERR_UNAVAILABLE)
        self.assertEqual(len(runner.ytdlp_calls), 1)


class TestStaleSignedUrl(LadderTestBase):
    def test_stale_url_is_refreshed_not_re_requested(self):
        """Rung 2 exists to re-extract the player. It must pass
        --no-cache-dir (a fresh extraction), and must not simply repeat
        rung 1's identical command."""
        def script(i, cmd):
            if "--no-cache-dir" in cmd:
                _write_out(cmd)
                return (["[download] 100%"], 0)
            return (["ERROR: unable to download video data: HTTP Error 403: "
                     "Forbidden (signed URL expired)"], 1)

        runner = ScriptedRunner(script)
        res = vi.download_full_video(self.url, self.out, runner=runner,
                                     auth=yo.AuthConfig(), stall_timeout=None)
        self.assertEqual(res["rung"], yo.RUNG_REFRESH)
        first, second = runner.ytdlp_calls[0], runner.ytdlp_calls[1]
        self.assertNotEqual(first, second)
        self.assertIn("--no-cache-dir", second)
        self.assertNotIn("--no-cache-dir", first)
        # every ladder rung after the first re-extracts
        for rung in yo.build_ladder(yo.AuthConfig())[1:]:
            self.assertTrue(rung.fresh_url)


class TestPartialCleanup(LadderTestBase):
    def _partial(self, size=50_000, fmt=None):
        os.makedirs(os.path.dirname(self.out), exist_ok=True)
        with open(self.out + ".part", "wb") as f:
            f.write(b"\x01" * size)
        if fmt:
            with open(self.out + ".fmt", "w", encoding="utf-8") as f:
                f.write(fmt)

    def test_partial_from_a_different_format_is_discarded(self):
        """Resuming across formats silently corrupts the output — the
        partial must be deleted, not `--continue`d."""
        selector = vi.full_vod_format(720)
        self._partial(fmt="some-other-format-398")

        seen = {}

        def script(i, cmd):
            seen["part_exists"] = os.path.exists(self.out + ".part")
            _write_out(cmd)
            return (["[download] 100%"], 0)

        runner = ScriptedRunner(script)
        vi.download_full_video(self.url, self.out, runner=runner,
                               auth=yo.AuthConfig(), stall_timeout=None)
        self.assertFalse(seen["part_exists"],
                         "a foreign-format partial must be discarded first")
        self.assertNotEqual(selector, "some-other-format-398")

    def test_partial_from_the_same_format_is_resumed(self):
        selector = vi.full_vod_format(720)
        self._partial(fmt=selector)
        seen = {}

        def script(i, cmd):
            seen["part_exists"] = os.path.exists(self.out + ".part")
            seen["continue"] = "--continue" in cmd
            _write_out(cmd)
            return (["[download] 100%"], 0)

        runner = ScriptedRunner(script)
        res = vi.download_full_video(self.url, self.out, runner=runner,
                                     auth=yo.AuthConfig(), stall_timeout=None)
        self.assertTrue(seen["part_exists"],
                        "a same-format partial must be kept and resumed")
        self.assertTrue(seen["continue"])
        self.assertTrue(res["resumed"])

    def test_sidecarless_partial_resumes_on_the_same_pinned_selector(self):
        """A partial with no sidecar (pre-upgrade, or a killed run) is
        still OURS when the rung uses the same pinned selector a previous
        run would have used — resuming a multi-hour download rather than
        restarting it is a core promise of this pipeline."""
        self._partial(fmt=None)
        seen = {}

        def script(i, cmd):
            seen["part_exists"] = os.path.exists(self.out + ".part")
            seen["continue"] = "--continue" in cmd
            _write_out(cmd)
            return (["[download] 100%"], 0)

        runner = ScriptedRunner(script)
        res = vi.download_full_video(self.url, self.out, runner=runner,
                                     auth=yo.AuthConfig(), stall_timeout=None)
        self.assertTrue(seen["part_exists"])
        self.assertTrue(seen["continue"])
        self.assertTrue(res["resumed"])

    def test_sidecarless_partial_is_discarded_when_the_rung_changes_format(self):
        """...but the moment a rung overrides the selector, unprovable
        bytes must go: splicing two formats fails only at detection."""
        self._partial(fmt=None)
        seen = {}

        def script(i, cmd):
            seen.setdefault("part_at_rung", []).append(
                os.path.exists(self.out + ".part"))
            # Only the alt-format rung succeeds. Match its selector
            # exactly — the default selector CONTAINS "best[height<=720]"
            # as a substring, so a loose check would match rung 1 too.
            selector = cmd[cmd.index("-f") + 1] if "-f" in cmd else ""
            if selector.startswith("best[height<=720]/"):
                _write_out(cmd)
                return (["[download] 100%"], 0)
            return ([FORBIDDEN], 1)

        runner = ScriptedRunner(script)
        res = vi.download_full_video(self.url, self.out, runner=runner,
                                     auth=yo.AuthConfig(), stall_timeout=None)
        self.assertEqual(res["rung"], yo.RUNG_ALT_FORMAT)
        self.assertFalse(res["resumed"],
                         "the alt-format rung must not resume foreign bytes")

    def test_a_valid_complete_download_is_never_deleted(self):
        os.makedirs(os.path.dirname(self.out), exist_ok=True)
        with open(self.out, "wb") as f:
            f.write(b"\x00" * 300_000)
        runner = ScriptedRunner(
            lambda i, cmd: (_ for _ in ()).throw(
                AssertionError("must not re-download a valid file")))
        res = vi.download_full_video(self.url, self.out, runner=runner,
                                     auth=yo.AuthConfig(), stall_timeout=None)
        self.assertTrue(res["reused"])
        self.assertTrue(os.path.exists(self.out))
        self.assertEqual(runner.ytdlp_calls, [])

    def test_only_temporary_artifacts_are_removed(self):
        os.makedirs(os.path.dirname(self.out), exist_ok=True)
        keep = os.path.join(os.path.dirname(self.out), "keep-me.mp4")
        for path in (self.out + ".part", self.out + ".ytdl", keep):
            with open(path, "wb") as f:
                f.write(b"x" * 100)
        vi._clear_partial(self.out, "test")
        self.assertFalse(os.path.exists(self.out + ".part"))
        self.assertFalse(os.path.exists(self.out + ".ytdl"))
        self.assertTrue(os.path.exists(keep),
                        "unrelated files must never be touched")


class TestMediaProbe(LadderTestBase):
    def test_probe_downloads_real_bytes_not_metadata(self):
        def script(i, cmd):
            self.assertIn("--download-sections", cmd,
                          "the probe must fetch real media, not metadata")
            self.assertNotIn("--skip-download", cmd)
            _write_out(cmd, size=64_000)
            return (["[download] 100%"], 0)

        runner = ScriptedRunner(script)
        res = vi.media_download_probe(self.url, runner=runner,
                                      auth=yo.AuthConfig(),
                                      out_dir=self.tmp.name,
                                      stall_timeout=None)
        self.assertTrue(res["ok"])
        self.assertEqual(res["rung"], yo.RUNG_NORMAL)
        self.assertGreaterEqual(res["bytes"], vi.PROBE_MIN_BYTES)

    def test_probe_rejects_a_zero_byte_success(self):
        """yt-dlp exiting 0 having written nothing real is exactly the
        failure mode metadata-only checks miss."""
        def script(i, cmd):
            _write_out(cmd, size=10)      # below PROBE_MIN_BYTES
            return (["[download] 100%"], 0)

        runner = ScriptedRunner(script)
        with self.assertRaises(vi.MediaDownloadError):
            vi.media_download_probe(self.url, runner=runner,
                                    auth=yo.AuthConfig(),
                                    out_dir=self.tmp.name,
                                    stall_timeout=None)

    def test_probe_result_lets_the_download_skip_proven_rungs(self):
        def script(i, cmd):
            _write_out(cmd)
            return (["[download] 100%"], 0)

        runner = ScriptedRunner(script)
        res = vi.download_full_video(self.url, self.out, runner=runner,
                                     auth=yo.AuthConfig(), stall_timeout=None,
                                     start_rung=yo.RUNG_IPV4)
        self.assertEqual(res["rung"], yo.RUNG_IPV4)
        self.assertIn("--force-ipv4", runner.ytdlp_calls[0])


class TestRedaction(unittest.TestCase):
    def test_signed_urls_are_redacted(self):
        raw = ("[download] Destination: https://rr5---sn-abc.googlevideo.com/"
               "videoplayback?expire=1&ei=X&ip=1.2.3.4&sig=SUPERSECRET&"
               "pot=TOKENVALUE&itag=398")
        out = yo.redact_text(raw)
        for secret in ("SUPERSECRET", "TOKENVALUE", "1.2.3.4"):
            self.assertNotIn(secret, out)
        self.assertIn("itag=398", out, "identifying detail should survive")
        self.assertIn("googlevideo.com", out)

    def test_cookie_source_and_profile_are_redacted(self):
        argv = ["yt-dlp", "--cookies-from-browser", "chrome:Profile 17",
                "--impersonate", "chrome", "-f", "best"]
        red = yo.redact_argv(argv)
        self.assertIn("--cookies-from-browser", red)
        self.assertNotIn("chrome:Profile 17", red)
        self.assertIn(yo.REDACTED, red)
        # a non-sensitive flag is untouched
        self.assertIn("--impersonate", red)
        self.assertIn("best", red)

    def test_profile_paths_are_redacted(self):
        text = (r"reading cookies from C:\Users\connor\AppData\Local\Google\
Chrome\User Data\Default\Cookies")
        self.assertNotIn("connor", yo.redact_text(text.replace("\n", "")))

    def test_credentials_never_survive_a_ladder_record(self):
        auth = yo.load_auth_config({yo.ENV_COOKIES_FROM_BROWSER: "chrome",
                                    yo.ENV_BROWSER_PROFILE: "SecretProfile"})
        blob = json.dumps([r.describe() for r in
                           yo.build_ladder(auth, have_curl_cffi=True)])
        self.assertNotIn("SecretProfile", blob)
        self.assertIn("--cookies-from-browser", blob)
        self.assertNotIn("SecretProfile", json.dumps(auth.describe()))

    def test_downloader_output_is_redacted_before_logging(self):
        """_run_live prints every child line; it must sanitize first."""
        import io
        from contextlib import redirect_stdout

        runner = ScriptedRunner(lambda i, cmd: ([
            "[download] https://rr1---sn-x.googlevideo.com/videoplayback?"
            "sig=LEAKME&itag=136"], 0))
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                vi._run_live(["yt-dlp", "x"], "[t]", runner)
            except Exception:
                pass
        self.assertNotIn("LEAKME", buf.getvalue())


class TestErrorClassification(unittest.TestCase):
    def test_media_403_is_youtube_media_forbidden(self):
        code, remedy = yo.classify_ytdlp_error(FORBIDDEN)
        self.assertEqual(code, yo.ERR_FORBIDDEN)
        self.assertTrue(remedy)
        self.assertTrue(yo.is_retryable(code))

    def test_bot_check_beats_a_bare_403(self):
        code, _ = yo.classify_ytdlp_error(
            "ERROR: Sign in to confirm you're not a bot. HTTP Error 403")
        self.assertEqual(code, yo.ERR_BOT_CHECK)

    def test_unavailable_is_not_retryable(self):
        code, _ = yo.classify_ytdlp_error("ERROR: Video unavailable")
        self.assertEqual(code, yo.ERR_UNAVAILABLE)
        self.assertFalse(yo.is_retryable(code))

    def test_worker_maps_the_ladder_code_through(self):
        exc = vi.MediaDownloadError(yo.ERR_FORBIDDEN, "every rung failed")
        code, message = worker.classify_download_error(exc)
        self.assertEqual(code, yo.ERR_FORBIDDEN)
        self.assertIn("every rung failed", message)

    def test_worker_classifies_a_raw_403_not_as_generic(self):
        exc = subprocess.CalledProcessError(1, ["yt-dlp"], output=FORBIDDEN)
        code, _ = worker.classify_download_error(exc)
        self.assertEqual(code, yo.ERR_FORBIDDEN)
        self.assertNotEqual(code, "download_failed")


class TestExtraArgPolicy(unittest.TestCase):
    def test_safe_args_are_accepted(self):
        args, problems = yo.parse_extra_args(
            "--sleep-requests 2 --extractor-args youtube:player_client=android")
        self.assertEqual(problems, [])
        self.assertIn("--sleep-requests", args)
        self.assertIn("youtube:player_client=android", args)

    def test_credential_and_output_flags_are_refused(self):
        for bad in ("--cookies /tmp/c.txt", "--username me --password x",
                    "--exec rm -rf /", "-o /tmp/pwn.mp4"):
            args, problems = yo.parse_extra_args(bad)
            self.assertTrue(problems, f"{bad!r} should have been refused")
            for token in args:
                self.assertNotIn("--cookies", token)
                self.assertNotIn("--exec", token)
                self.assertNotIn("--password", token)

    def test_unknown_browser_is_refused_with_the_supported_list(self):
        cfg = yo.load_auth_config({yo.ENV_COOKIES_FROM_BROWSER: "netscape"})
        self.assertIsNone(cfg.cookies_from_browser)
        self.assertTrue(cfg.problems)
        self.assertIn("chrome", cfg.problems[0])

    def test_profile_without_a_browser_grants_nothing(self):
        cfg = yo.load_auth_config({yo.ENV_BROWSER_PROFILE: "Default"})
        self.assertFalse(cfg.cookies_configured)
        self.assertEqual(cfg.cookie_args(), [])
        self.assertTrue(cfg.problems)


class TestDetectionAssetGate(unittest.TestCase):
    """The check that must happen BEFORE a multi-hour download."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = js.JobStore(os.path.join(self.tmp.name, "a.sqlite"))
        self.locks = lk.LockManager(self.store.con)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _job(self, layout_id):
        job = ops.create_job_from_broadcast(
            self.store, match_id="m", video_id="AAAAAAAAAAA",
            source_url="https://www.youtube.com/watch?v=AAAAAAAAAAA",
            channel_id="UCofficial", team_a="a", team_b="b",
            expected_layout_id=layout_id)
        self.store.update_payload(job.job_key, {
            "source": {"state": "approved", "autoApproved": True}})
        return self.store.get(job.job_key)

    def test_missing_template_dir_fails_before_download(self):
        job = self._job("owcs_nd5lllwdky0")   # real: templates never harvested
        with self.assertRaises(worker.DetectionAssetsMissing) as ctx:
            worker.assert_detection_assets(job)
        msg = str(ctx.exception)
        self.assertIn("templates", msg)
        self.assertIn("harvest_templates.py", msg)
        self.assertIn("--for-harvest", msg)

    def test_placeholder_anchor_fails(self):
        job = self._job("owcs_8c105lnzlam")   # real: anchor is a PLACEHOLDER
        with self.assertRaises(worker.DetectionAssetsMissing):
            worker.assert_detection_assets(job)

    def test_the_proven_layout_passes(self):
        job = self._job("owcs_jksix_qwc")
        report = worker.assert_detection_assets(job)
        self.assertTrue(report["ok"])

    def test_for_harvest_is_an_explicit_opt_out(self):
        job = self._job("owcs_nd5lllwdky0")
        report = worker.assert_detection_assets(job, allow_missing=True)
        self.assertTrue(report["ok"])
        self.assertFalse(report["hardOk"])

    def test_no_layout_yet_is_reported_honestly(self):
        job = self._job(None)
        report = worker.check_detection_assets(job)
        self.assertFalse(report["checked"])
        self.assertIn("after the download", report["reason"])

    def test_gate_runs_before_any_download_call(self):
        """The whole point: no bytes are fetched when the gate fails."""
        job = self._job("owcs_nd5lllwdky0")
        calls = []

        def boom(*a, **kw):
            calls.append(a)
            raise AssertionError("must not download")

        result = worker.download_job(
            self.store, self.locks, job, worker_id="w1",
            media_root=os.path.join(self.tmp.name, "media"),
            official_channel_ids={"UCofficial"},
            probe_fn=boom, download_full_fn=boom, probe_media_fn=boom,
            which=lambda t: f"/usr/bin/{t}")
        self.assertFalse(result["ok"])
        self.assertEqual(result["errorCode"], "detection_assets_missing")
        self.assertEqual(calls, [], "nothing may be fetched")

    def test_anchor_detection_is_honest_about_the_real_repo(self):
        """Guards the actual committed state this pass diagnosed."""
        ready = [a["layoutId"] for a in da.audit_all_layouts() if a["ok"]]
        self.assertIn("owcs_jksix_qwc", ready)
        nd5 = da.check_layout_assets("owcs_nd5lllwdky0")
        self.assertIn("templates", nd5["failed"])


class TestRetryPreservesApproval(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = js.JobStore(os.path.join(self.tmp.name, "a.sqlite"))
        self.locks = lk.LockManager(self.store.con)
        job = ops.create_job_from_broadcast(
            self.store, match_id="m", video_id="AAAAAAAAAAA",
            source_url="https://www.youtube.com/watch?v=AAAAAAAAAAA",
            channel_id="UCofficial", team_a="a", team_b="b",
            expected_layout_id="owcs_jksix_qwc")
        self.key = job.job_key
        self.store.update_payload(self.key, {
            "source": {"state": "approved", "autoApproved": False,
                       "decidedBy": "connor", "reasonCode": "manual_approval"},
            "resumeState": sm.ARCHIVED})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_retry_restores_a_downloadable_state_without_reapproval(self):
        self.store.record_attempt(self.key, ok=False,
                                  error_code=yo.ERR_FORBIDDEN,
                                  error_message="403")
        self.assertEqual(self.store.get(self.key).state, sm.RETRY_SCHEDULED)
        job = ops.retry_job(self.store, self.key)
        self.assertEqual(job.state, sm.ARCHIVED,
                         "retry must restore the downloadable stage")
        source = job.payload["source"]
        self.assertEqual(source["state"], "approved")
        self.assertEqual(source["decidedBy"], "connor",
                         "the audited approval must survive untouched")

    def test_retry_never_revives_an_unapproved_source(self):
        self.store.update_payload(self.key, {
            "source": {"state": "pending-approval"}})
        self.store.record_attempt(self.key, ok=False, error_code="x")
        job = ops.retry_job(self.store, self.key)
        self.assertEqual(job.state, sm.RETRY_SCHEDULED,
                         "an unapproved source must not become downloadable")

    def test_autopilot_continues_after_a_retry(self):
        from automation import autopilot as ap
        self.store.record_attempt(self.key, ok=False,
                                  error_code=yo.ERR_FORBIDDEN,
                                  error_message="403")
        ops.retry_job(self.store, self.key)   # -> ARCHIVED
        seen = []

        def fake_run_one(store, lock_mgr, con, job_key, **kw):
            seen.append(store.get(job_key).state)
            return {"ok": False, "reason": "stop here for the test"}

        res = ap.run_autopilot(self.store, self.locks, self.key,
                               worker_id="w1", run_one=fake_run_one)
        self.assertEqual(seen, [sm.ARCHIVED],
                         "autopilot must resume at the download stage")
        self.assertEqual(res["stop"], ap.STOP_BLOCKED)

    def test_autopilot_takes_a_due_retry_itself(self):
        """A RETRY_SCHEDULED job whose backoff has elapsed used to dead-end
        with 'no automatic action'."""
        from automation import autopilot as ap
        self.store.record_attempt(self.key, ok=False,
                                  error_code=yo.ERR_FORBIDDEN,
                                  error_message="403")
        self.store.con.execute(
            "UPDATE jobs SET next_retry_at='2000-01-01T00:00:00+00:00' "
            "WHERE job_key=?", (self.key,))
        self.store.con.commit()
        seen = []

        def fake_run_one(store, lock_mgr, con, job_key, **kw):
            seen.append(store.get(job_key).state)
            return {"ok": False, "reason": "stop"}

        res = ap.run_autopilot(self.store, self.locks, self.key,
                               worker_id="w1", run_one=fake_run_one)
        self.assertEqual(seen, [sm.ARCHIVED])
        self.assertIn("resume-after-retry",
                      [s["action"] for s in res["steps"]])

    def test_state_is_never_mislabelled_archived_while_retry_scheduled(self):
        self.store.record_attempt(self.key, ok=False, error_code="x")
        job = self.store.get(self.key)
        self.assertEqual(job.state, sm.RETRY_SCHEDULED)
        from automation import link_intake as li
        [row] = li.link_status(self.store, job_key=self.key)
        self.assertEqual(row["state"], sm.RETRY_SCHEDULED)
        self.assertNotEqual(row["state"], sm.ARCHIVED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
