#!/usr/bin/env python3
"""
test_automation_cli_import_isolation.py — regression coverage for the
discovery-workflow false-success bug (GitHub Actions run #28): cli.py used
to import `detection_runner` (-> `ingest_map` -> `capture` -> `cv2`) at
module level, so ANY invocation of cli.py — including the lightweight
discovery/registry/coverage commands the hourly `discovery.yml` workflow
actually runs — crashed with `ModuleNotFoundError: No module named 'cv2'`
on a runner with no OpenCV installed. The crash was masked by an
unguarded `python3 ... | tee run-output.txt` pipeline (no `pipefail`), so
the workflow still reported success with a near-empty artifact.

This suite proves the fix at the process level: every lightweight command
runs successfully with cv2 UNAVAILABLE (simulated via `sys.modules['cv2'] =
None`, which makes any subsequent `import cv2` raise exactly like a missing
package would — see https://docs.python.org/3/reference/import.html), heavy
modules are never even loaded for those commands, and a genuine CV command
still works normally and fails explicitly (never silently) when cv2 truly
isn't installed.

Run: python3 pipeline/test_automation_cli_import_isolation.py
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
CLI = os.path.join(HERE, "automation", "cli.py")

# Modules a lightweight discovery/registry/coverage command must NEVER pull
# in. `cv2`/`numpy` are the actual missing dependency on the lightweight
# runner; the rest are this repo's own CV/recording/segmentation/detection
# modules that transitively require it.
HEAVY_MODULES = (
    "cv2",
    "capture",
    "video_ingest",
    "download_vod_clip",
    "ingest_map",
    "automation.worker",
    "automation.segmentation",
    "automation.detection_runner",
    "automation.ops",
    "automation.publish",
)

_NO_CV2_PREAMBLE = "import sys; sys.modules['cv2'] = None\n"


def _run(args: list[str], *, block_cv2: bool = False, timeout: float = 30,
        env: dict | None = None) -> subprocess.CompletedProcess:
    """Run `cli.py <args>` as a real subprocess (never this test process —
    isolation must be verified at the interpreter level, not by mocking).
    block_cv2=True simulates a machine with no OpenCV installed: any
    subsequent `import cv2` (direct or transitive) raises ModuleNotFoundError,
    exactly as it would on a bare Python install."""
    full_env = dict(os.environ if env is None else env)
    if block_cv2:
        script = (_NO_CV2_PREAMBLE +
                  "import runpy\n"
                  f"sys.argv = ['cli.py'] + {args!r}\n"
                  f"runpy.run_path({CLI!r}, run_name='__main__')\n")
        cmd = [sys.executable, "-c", script]
    else:
        cmd = [sys.executable, CLI, *args]
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True,
                         timeout=timeout, env=full_env)


def _loaded_heavy_modules(args: list[str], *, block_cv2: bool = False) -> set[str]:
    """Run `cli.py <args>` then report which HEAVY_MODULES ended up in
    sys.modules — proof of what the command actually imported, not what it
    merely could import."""
    preamble = _NO_CV2_PREAMBLE if block_cv2 else ""
    marker = "OWCS_HEAVY_MODULES_LOADED::"
    script = (
        preamble +
        "import sys, runpy\n"
        f"sys.argv = ['cli.py'] + {args!r}\n"
        "try:\n"
        f"    runpy.run_path({CLI!r}, run_name='__main__')\n"
        "except SystemExit:\n"
        "    pass\n"
        f"loaded = [m for m in {list(HEAVY_MODULES)!r} if m in sys.modules]\n"
        f"print({marker!r} + ','.join(loaded))\n"
    )
    res = subprocess.run([sys.executable, "-c", script], cwd=REPO_ROOT,
                        capture_output=True, text=True, timeout=30)
    for line in res.stdout.splitlines():
        if line.startswith(marker):
            rest = line[len(marker):].strip()
            return set(rest.split(",")) if rest else set()
    raise AssertionError(f"marker line not found; stdout={res.stdout!r} stderr={res.stderr[-2000:]!r}")


class TestLightweightCommandsWorkWithoutCv2(unittest.TestCase):
    """Requirements 1-3: the exact commands discovery.yml runs must start
    successfully in a clean Python environment without OpenCV."""

    def test_help_works_without_cv2(self):
        res = _run(["--help"], block_cv2=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("usage:", res.stdout.lower())

    def test_broadcast_dryrun_help_works_without_cv2(self):
        res = _run(["broadcast-dryrun", "--help"], block_cv2=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("broadcast", res.stdout.lower())

    def test_verify_channels_help_works_without_cv2(self):
        res = _run(["verify-channels", "--help"], block_cv2=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_calendar_dryrun_help_works_without_cv2(self):
        res = _run(["calendar-dryrun", "--help"], block_cv2=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_discover_broadcasts_help_works_without_cv2(self):
        res = _run(["discover-broadcasts", "--help"], block_cv2=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_coverage_help_works_without_cv2(self):
        res = _run(["coverage", "--help"], block_cv2=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_sync_faceit_help_works_without_cv2(self):
        res = _run(["sync-faceit", "--help"], block_cv2=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_sync_calendar_help_works_without_cv2(self):
        res = _run(["sync-calendar", "--help"], block_cv2=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_sync_all_help_works_without_cv2(self):
        res = _run(["sync-all", "--help"], block_cv2=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_list_championships_help_works_without_cv2(self):
        res = _run(["list-championships", "--help"], block_cv2=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_verify_registry_help_works_without_cv2(self):
        res = _run(["verify-registry", "--help"], block_cv2=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_coverage_actually_runs_without_cv2(self):
        """Not just --help — a real (read-only) invocation of the exact
        command the discovery workflow's `mode=coverage` dispatch runs."""
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "automation.sqlite")
            res = _run(["--db", db_path, "coverage", "--window", "1"],
                      block_cv2=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("coverage", res.stdout.lower())

    def test_broadcast_dryrun_actually_runs_without_cv2(self):
        """The exact failing command from the incident report
        (`broadcast-dryrun --lookback-days 14`), run for real with cv2
        blocked. No YOUTUBE_API_KEY is set in this test environment either —
        that degrades to a per-channel API-error line (already-established,
        pre-existing behavior), never a crash; exit stays 0."""
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "automation.sqlite")
            res = _run(["--db", db_path, "broadcast-dryrun",
                       "--lookback-days", "14"], block_cv2=True)
        self.assertEqual(res.returncode, 0, res.stderr)


class TestNoHeavyImportsAtStartup(unittest.TestCase):
    """Requirement 4: lightweight commands do not import detection_runner,
    ingest_map, or any other CV/recording/segmentation module — checked by
    inspecting sys.modules in the subprocess that actually ran them, not by
    reasoning about the source."""

    def _assert_lightweight(self, args: list[str]):
        with tempfile.TemporaryDirectory() as d:
            full_args = ["--db", os.path.join(d, "automation.sqlite"), *args]
            loaded = _loaded_heavy_modules(full_args)
        self.assertEqual(loaded, set(),
                         f"cli.py {' '.join(args)} loaded heavy modules: {loaded}")

    def test_help_loads_nothing_heavy(self):
        self._assert_lightweight(["--help"])

    def test_coverage_loads_nothing_heavy(self):
        self._assert_lightweight(["coverage", "--window", "1"])

    def test_registries_loads_nothing_heavy(self):
        self._assert_lightweight(["registries"])

    def test_config_loads_nothing_heavy(self):
        self._assert_lightweight(["config"])

    def test_status_loads_nothing_heavy(self):
        self._assert_lightweight(["status"])

    def test_verify_channels_help_loads_nothing_heavy(self):
        self._assert_lightweight(["verify-channels", "--help"])

    def test_broadcast_dryrun_real_run_loads_nothing_heavy(self):
        self._assert_lightweight(["broadcast-dryrun", "--lookback-days", "14"])

    def test_list_jobs_loads_ops_but_not_cv2(self):
        """`list-jobs` DOES need `ops` (and, transitively, `worker`/
        `detection_runner` — both fixed to be cv2-safe themselves) — proves
        the isolation is precise about the actual dependency (cv2/capture/
        segmentation), not just "cli.py never imports anything new"."""
        with tempfile.TemporaryDirectory() as d:
            loaded = _loaded_heavy_modules(
                ["--db", os.path.join(d, "automation.sqlite"), "list-jobs"])
        self.assertIn("automation.ops", loaded)
        self.assertNotIn("cv2", loaded)
        self.assertNotIn("capture", loaded)
        self.assertNotIn("video_ingest", loaded)
        self.assertNotIn("download_vod_clip", loaded)
        self.assertNotIn("automation.segmentation", loaded)


class TestDetectionCommandsStillWork(unittest.TestCase):
    """Requirement 5: detection/segmentation commands still import and use
    the real modules when actually invoked (this fix must not have quietly
    broken the feature it's built around)."""

    def test_detect_job_imports_detection_runner_and_segmentation(self):
        with tempfile.TemporaryDirectory() as d:
            loaded = _loaded_heavy_modules(
                ["--db", os.path.join(d, "automation.sqlite"),
                 "detect-job", "record:does-not-exist:source"])
        self.assertIn("automation.detection_runner", loaded)
        self.assertIn("automation.segmentation", loaded)

    def test_segment_list_imports_segmentation(self):
        with tempfile.TemporaryDirectory() as d:
            loaded = _loaded_heavy_modules(
                ["--db", os.path.join(d, "automation.sqlite"), "segment-list"])
        self.assertIn("automation.segmentation", loaded)

    def test_worker_run_imports_worker_and_ops(self):
        with tempfile.TemporaryDirectory() as d:
            loaded = _loaded_heavy_modules(
                ["--db", os.path.join(d, "automation.sqlite"),
                 "worker-run", "--max-jobs", "0"])
        self.assertIn("automation.worker", loaded)
        self.assertIn("automation.ops", loaded)

    def test_detect_job_on_missing_job_fails_cleanly_with_cv2_present(self):
        with tempfile.TemporaryDirectory() as d:
            res = _run(["--db", os.path.join(d, "automation.sqlite"),
                       "detect-job", "record:does-not-exist:source"])
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("no such job", (res.stdout + res.stderr).lower())


class TestMissingCv2OnlyFailsCvCommands(unittest.TestCase):
    """Requirement 6: a missing cv2 produces an explicit error ONLY when a
    CV command is actually requested — never for a lightweight command,
    and never silently (no false-success) for a CV one."""

    def test_lightweight_command_unaffected_by_missing_cv2(self):
        with tempfile.TemporaryDirectory() as d:
            res = _run(["--db", os.path.join(d, "automation.sqlite"),
                       "coverage", "--window", "1"], block_cv2=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_cv_command_fails_explicitly_without_cv2(self):
        with tempfile.TemporaryDirectory() as d:
            res = _run(["--db", os.path.join(d, "automation.sqlite"),
                       "segment-list"], block_cv2=True)
        self.assertNotEqual(res.returncode, 0,
                            "a CV command must fail, not silently succeed, "
                            "when cv2 is unavailable")
        self.assertIn("cv2", (res.stdout + res.stderr).lower())

    def test_worker_doctor_still_runs_and_reports_honestly_without_cv2(self):
        # worker-doctor merely CHECKS for cv2 (never imports it) so it must
        # keep working; this is the contrast case proving the boundary is
        # precise, not "anything worker-related now breaks without cv2".
        # Its EXIT CODE is defined to report worker READINESS (see
        # cmd_worker_doctor: `0 if report["ok"] else 1`), so a machine that
        # is legitimately not ready — no ffmpeg/yt-dlp, unharvested layouts —
        # exits 1 without anything being broken. Asserting a bare 0 here made
        # this case pass only on a fully provisioned worker; the real
        # contract is that the command RUNS (no import crash), emits a
        # parseable report, tells the truth about the missing cv2, and ties
        # its exit code to that report rather than to the crash.
        with tempfile.TemporaryDirectory() as d:
            res = _run(["--db", os.path.join(d, "automation.sqlite"),
                       "worker-doctor", "--json"], block_cv2=True)
        self.assertNotIn("Traceback", res.stderr)
        self.assertNotIn("ModuleNotFoundError", res.stderr)
        report = json.loads(res.stdout)
        self.assertIsNone(report["repoDependencies"]["opencv-python-headless"])
        # Never a silent success: readiness is reported, not swallowed.
        self.assertEqual(res.returncode, 0 if report["ok"] else 1, res.stderr)

    def test_worker_doctor_pulls_in_no_media_pipeline_modules(self):
        """worker-doctor only PROBES for the media stack, so it must not drag
        in the recording/segmentation modules to answer. (cv2 itself is
        deliberately excluded from this check: check_repo_dependencies
        imports it on purpose to report its version, and under block_cv2 the
        sentinel makes `'cv2' in sys.modules` true regardless — so cv2's
        absence is proven by the report above, not by sys.modules.)"""
        with tempfile.TemporaryDirectory() as d:
            loaded = _loaded_heavy_modules(
                ["--db", os.path.join(d, "automation.sqlite"),
                 "worker-doctor", "--json"])
        self.assertNotIn("capture", loaded)
        self.assertNotIn("video_ingest", loaded)
        self.assertNotIn("download_vod_clip", loaded)
        self.assertNotIn("automation.segmentation", loaded)


if __name__ == "__main__":
    unittest.main(verbosity=2)
