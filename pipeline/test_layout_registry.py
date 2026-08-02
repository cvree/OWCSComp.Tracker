#!/usr/bin/env python3
"""
test_layout_registry.py — a starter layout can never become the automatic
default again.

The bug this locks out: `run_owcs_auto.py` and `discover_owcs_vods.py` both
hardcoded `DEFAULT_LAYOUT = "layouts/owcs_youtube_2026.json"`. That file is a
documented STARTER — its own `_comments` open with "ALL RECTANGLES BELOW ARE
PLACEHOLDER GUESSES". Every automatic run that did not pass `--layout` and
every auto-discovered source therefore pointed at rectangles that do not
correspond to any real HUD.

It never crashed. Detection read whatever pixels sat under a guessed box,
reported UNKNOWN for most slots and occasionally something plausible and
wrong, and the run looked like it had worked. That is the worst shape a
failure can take in a system whose entire value is being trustworthy about
what it saw.

Run: python3 pipeline/test_layout_registry.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import proc_text  # noqa: E402  (UTF-8 subprocess decoding)

import layout_registry as lr  # noqa: E402

LAYOUT_DIR = os.path.join(REPO, "layouts")


class TestCalibrationDetection(unittest.TestCase):
    def test_the_verified_milestone_layout_is_calibrated(self):
        self.assertTrue(lr.is_calibrated(
            os.path.join(LAYOUT_DIR, "owcs_jksix_qwc.json")))

    def test_the_starter_layout_is_not_calibrated(self):
        self.assertFalse(lr.is_calibrated(
            os.path.join(LAYOUT_DIR, "owcs_youtube_2026.json")),
            "the documented placeholder layout is being treated as calibrated")

    def test_calibration_needs_measured_chip_geometry_not_just_a_key(self):
        """A layout can carry slots, an anchor and thresholds and still be
        entirely guesses. Only the calibrator produces chip rows."""
        self.assertFalse(lr.is_calibrated({}))
        self.assertFalse(lr.is_calibrated({"hud_probe": {}}))
        self.assertFalse(lr.is_calibrated(
            {"hud_probe": {"sat_min": 100}, "slots_a": [[1, 2, 3, 4]] * 5}))
        self.assertTrue(lr.is_calibrated(
            {"hud_probe": {"chips_a": [[1, 2, 3, 4]], "chips_b": [[5, 6, 7, 8]]}}))

    def test_a_layout_with_only_one_chip_row_is_not_calibrated(self):
        self.assertFalse(lr.is_calibrated({"hud_probe": {"chips_a": [[1, 2, 3, 4]]}}))


class TestDefaultResolution(unittest.TestCase):
    def test_the_default_is_calibrated(self):
        default = lr.default_layout()
        self.assertTrue(lr.is_calibrated(os.path.join(REPO, default)),
                        f"{default} is the automatic default but is not "
                        f"calibrated")

    def test_the_default_prefers_the_verified_package(self):
        self.assertEqual(lr.default_layout(), "layouts/owcs_jksix_qwc.json")

    def test_with_no_calibrated_layout_it_refuses_rather_than_guessing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "starter.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"frame_width": 1920, "slots_a": [[1, 2, 3, 4]]}, f)
            with self.assertRaises(lr.NoCalibratedLayout) as ctx:
                lr.default_layout(tmp)
            message = str(ctx.exception)
            # The refusal has to tell the user how to fix it.
            self.assertIn("calibrate_source.py", message)
            self.assertIn("Calibration", message)

    def test_an_explicit_choice_is_still_honoured(self):
        """An operator deliberately pointing at a starter while calibrating it
        is legitimate; only the automatic default refuses to be a guess."""
        self.assertEqual(lr.resolve("layouts/owcs_youtube_2026.json"),
                         "layouts/owcs_youtube_2026.json")

    def test_resolve_falls_back_to_the_calibrated_default(self):
        self.assertEqual(lr.resolve(None), lr.default_layout())

    def test_a_corrupt_layout_file_does_not_break_the_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "broken.json"), "w",
                      encoding="utf-8") as f:
                f.write("{ not json")
            entries = lr.all_layouts(tmp)
            self.assertEqual(len(entries), 1)
            self.assertFalse(entries[0]["calibrated"])
            self.assertIn("error", entries[0])


class TestTheCallersNoLongerHardcode(unittest.TestCase):
    """The regression guard. A future edit that reintroduces a hardcoded
    starter default fails here."""

    CALLERS = ("run_owcs_auto.py", "discover_owcs_vods.py")

    def test_no_caller_hardcodes_a_layout_path_as_its_default(self):
        for name in self.CALLERS:
            with self.subTest(module=name):
                with open(os.path.join(HERE, name), "r", encoding="utf-8") as f:
                    text = f.read()
                self.assertNotIn(
                    'DEFAULT_LAYOUT = "layouts/', text,
                    f"{name} hardcodes a layout as its automatic default. It "
                    f"must resolve one through layout_registry so a starter "
                    f"can never be used unattended.")

    def test_every_caller_imports_the_registry(self):
        for name in self.CALLERS:
            with self.subTest(module=name):
                with open(os.path.join(HERE, name), "r", encoding="utf-8") as f:
                    self.assertIn("layout_registry", f.read())

    def test_a_discovered_source_is_written_with_a_calibrated_layout(self):
        import discover_owcs_vods as dv
        with tempfile.TemporaryDirectory() as tmp:
            sources = os.path.join(tmp, "video_sources.json")
            with open(sources, "w", encoding="utf-8") as f:
                json.dump({"_readme": "x", "sources": []}, f)
            added, _slug = dv.write_source(
                {"slug": "owcs-test", "title": "T", "videoId": "AAAAAAAAAA1",
                 "url": "https://www.youtube.com/watch?v=AAAAAAAAAA1",
                 "date": "2026-01-01"}, sources)
            self.assertTrue(added)
            with open(sources, encoding="utf-8") as f:
                written = json.load(f)["sources"][0]
        self.assertTrue(
            lr.is_calibrated(os.path.join(REPO, written["layout"])),
            f"a discovered source was registered against {written['layout']}, "
            f"which is not calibrated — every run of it would read guessed "
            f"rectangles")


class TestPackagingGate(unittest.TestCase):
    def test_check_packaging_enforces_a_calibrated_default(self):
        import check_packaging
        self.assertTrue(hasattr(check_packaging, "check_calibrated_default"),
                        "the packaging gate no longer checks that the "
                        "automatic layout default is calibrated")

    def test_the_gate_passes_on_this_repository(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, os.path.join(HERE, "check_packaging.py")],
            capture_output=True, text=True, cwd=REPO, **proc_text.PIPE_TEXT)
        self.assertEqual(result.returncode, 0,
                         result.stdout[-3000:] + result.stderr[-2000:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
