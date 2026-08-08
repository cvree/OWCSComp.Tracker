#!/usr/bin/env python3
"""
test_preflight_windows.py — the promises a new Windows operator relies on.

Everyone using this is on Windows, and `preflight.py` is the one thing they
run when something is wrong. That makes three properties load-bearing, and
all three have already been broken once:

  1. IT MUST RUN ON A BROKEN INSTALL. It is only ever run because something
     is missing, so it cannot import its way through the very packages it
     is checking for. `check_js_runtime` used to reach cv2 through
     `video_ingest` -> `capture`, so the readiness check died of exactly the
     fault it exists to report, printing a traceback instead of the ffmpeg
     and yt-dlp lines the operator needed.
  2. EVERY REMEDY MUST BE PASTEABLE. Remedies are rendered as click-to-copy
     commands in the portal. A remedy carrying a parenthetical aside gets
     copied into PowerShell along with the command and fails.
  3. ITS OUTPUT MUST SURVIVE REDIRECTION. `python pipeline\\preflight.py >
     out.txt` on an English Windows install encodes to cp1252, which has no
     '→'. Without an explicit UTF-8 stdout that redirect raises
     UnicodeEncodeError instead of writing the file.

Offline, no subprocesses of its own, no cv2 required.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import preflight as pf          # noqa: E402
import proc_text                # noqa: E402


class FakeRunner:
    """subprocess stand-in: every tool is missing, as on a fresh machine."""

    class CalledProcessError(Exception):
        pass

    @staticmethod
    def run(*_a, **_kw):
        raise FileNotFoundError("not on PATH")


class TestItRunsOnABrokenInstall(unittest.TestCase):
    def test_the_js_check_survives_an_unimportable_video_ingest(self):
        """The cv2 regression: this check must not need the CV stack."""
        saved = sys.modules.get("video_ingest")
        sys.modules["video_ingest"] = None      # import raises ImportError
        try:
            res = pf.check_js_runtime(which=lambda _n: None)
        finally:
            if saved is None:
                sys.modules.pop("video_ingest", None)
            else:
                sys.modules["video_ingest"] = saved
        self.assertEqual(res["status"], "warn")
        self.assertIn("Deno", res["detail"])

    def test_the_js_check_still_finds_a_runtime_without_video_ingest(self):
        saved = sys.modules.get("video_ingest")
        sys.modules["video_ingest"] = None
        try:
            res = pf.check_js_runtime(
                which=lambda n: r"C:\Program Files\nodejs\node.exe"
                if n == "node" else None)
        finally:
            if saved is None:
                sys.modules.pop("video_ingest", None)
            else:
                sys.modules["video_ingest"] = saved
        self.assertEqual(res["status"], "ok")
        self.assertIn("node", res["detail"])

    def test_one_exploding_check_does_not_hide_the_others(self):
        boom = pf.check_opencv

        def explode():
            raise RuntimeError("DLL load failed while importing cv2")

        pf.check_opencv = explode
        try:
            res = pf.run_checks()
        finally:
            pf.check_opencv = boom
        names = [c["name"] for c in res["checks"]]
        self.assertEqual(len(names), 10, names)
        self.assertIn("ffmpeg", names)
        opencv = next(c for c in res["checks"] if c["name"] == "opencv")
        self.assertEqual(opencv["status"], "fail")
        self.assertIn("DLL load failed", opencv["detail"])

    def test_a_machine_with_nothing_installed_still_gets_a_full_report(self):
        res = pf.run_checks()
        self.assertEqual(len(res["checks"]), 10)
        self.assertTrue(all(c["status"] in ("ok", "warn", "fail")
                            for c in res["checks"]))


class TestEveryRemedyIsPasteable(unittest.TestCase):
    """The portal renders `remedy` as a copy button. Anything in it that is
    not part of the command ends up pasted into PowerShell."""

    def _remedies(self):
        checks = [pf.check_python(), pf.check_ffmpeg(runner=FakeRunner),
                  pf.check_ffprobe(runner=FakeRunner),
                  pf.check_ytdlp(runner=FakeRunner),
                  pf.check_js_runtime(which=lambda _n: None),
                  pf.check_opencv()]
        return [(c["name"], c["remedy"]) for c in checks if c["remedy"]]

    def test_no_remedy_carries_prose(self):
        for name, remedy in self._remedies():
            with self.subTest(check=name):
                self.assertNotIn("(", remedy, f"{name}: prose in the command")
                self.assertNotIn("\n", remedy)
                self.assertEqual(remedy, remedy.strip())

    def test_prose_lives_in_note_instead_of_being_lost(self):
        ffmpeg = pf.check_ffmpeg(runner=FakeRunner)
        self.assertTrue(ffmpeg["note"])
        self.assertIn("open a new one", ffmpeg["note"])

    def test_pip_remedies_name_the_interpreter(self):
        """`pip` alone installs into whichever Python is first on PATH, which
        on a machine with two of them is routinely the wrong one."""
        for name, remedy in self._remedies():
            if "pip" in remedy:
                with self.subTest(check=name):
                    self.assertTrue(remedy.startswith("python -m pip"), remedy)

    def test_winget_remedies_cannot_stall_on_a_prompt(self):
        for name, remedy in self._remedies():
            if remedy.startswith("winget"):
                with self.subTest(check=name):
                    self.assertIn("--accept-source-agreements", remedy)
                    self.assertIn("--accept-package-agreements", remedy)

    def test_every_non_ok_check_says_something_actionable(self):
        for name, c in [(c["name"], c) for c in pf.run_checks()["checks"]]:
            if c["status"] == "ok":
                continue
            with self.subTest(check=name):
                self.assertTrue(c["remedy"] or c["note"],
                                f"{name} is not ok and offers no way forward")


class TestOutputSurvivesTheWindowsCodePage(unittest.TestCase):
    def test_reconfigure_makes_a_cp1252_stream_accept_an_arrow(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252")
        with self.assertRaises(UnicodeEncodeError):
            stream.write("→")
            stream.flush()
        stream.reconfigure(encoding="utf-8", errors="replace")
        stream.write("→ ≥ ■ ✓")
        stream.flush()
        self.assertIn("→", raw.getvalue().decode("utf-8"))

    def test_enable_utf8_stdio_never_raises_on_a_hostile_stream(self):
        class NotAStream:
            pass

        saved_out, saved_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = NotAStream()
        try:
            proc_text.enable_utf8_stdio()   # must be a no-op, not a crash
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err

    def test_the_cli_writes_utf8_to_a_pipe_under_a_legacy_code_page(self):
        """The end-to-end version: run the real CLI with the environment an
        English Windows install gives a redirected stdout."""
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        res = subprocess.run(
            [sys.executable, os.path.join("pipeline", "automation", "cli.py"),
             "--help"],
            cwd=repo, env=env, capture_output=True, timeout=120,
            **proc_text.PIPE_TEXT)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_preflight_itself_writes_utf8_to_a_pipe(self):
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        res = subprocess.run(
            [sys.executable, os.path.join("pipeline", "preflight.py")],
            cwd=repo, env=env, capture_output=True, timeout=180,
            **proc_text.PIPE_TEXT)
        # exit 1 just means something is missing on THIS machine; a crash
        # would be a traceback on stderr.
        self.assertIn(res.returncode, (0, 1), res.stderr)
        self.assertNotIn("UnicodeEncodeError", res.stderr)
        self.assertIn("python", res.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
