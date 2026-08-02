#!/usr/bin/env python3
"""
test_calibrate_engine.py — does the browser calibrator actually find the
hero portraits, on a real broadcast?

Everything else about the wizard can be checked statically. This cannot: the
only question that matters is whether the ten boxes land on the ten heroes,
and the only honest way to answer it is to run the real engine, in a real
browser, over real broadcast frames, and compare against a layout the
pipeline calibrated independently.

The reference is `layouts/owcs_jksix_qwc.json` — the verified Nepal milestone,
calibrated at 1920x1080 by `pipeline/calibrate_source.py` from real frames.
The committed evidence frames are 1280x720, so the reference is scaled down
and the engine's output is compared slot by slot.

This suite SKIPS (loudly, never silently passing) when Playwright or a
Chromium build is unavailable, because most environments will not have one.
CI installs both.

Run: python3 pipeline/test_calibrate_engine.py
"""
from __future__ import annotations

import functools
import glob
import http.server
import json
import os
import socketserver
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
FRAME_DIR = os.path.join(REPO, "reports", "ingest", "qad-twis-nepal", "frames")
REFERENCE = os.path.join(REPO, "layouts", "owcs_jksix_qwc.json")

#: Every slot must land within this many pixels of the pipeline's own answer,
#: at 1280x720. A hero portrait is ~35px wide, so 8px is comfortably inside
#: "the box is on the right hero" while still catching a real regression.
TOLERANCE_PX = 8
SIZE_TOLERANCE_PX = 4

_CHROME_CANDIDATES = (
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/opt/pw-browsers/chromium/chrome-linux/chrome",
)


def find_chromium() -> str | None:
    for path in _CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    for base in ("/opt/pw-browsers",):
        if os.path.isdir(base):
            hits = glob.glob(os.path.join(base, "chromium-*", "chrome-linux",
                                          "chrome"))
            if hits:
                return sorted(hits)[-1]
    return None


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # keep the test output readable
        pass


class TestBrowserCalibration(unittest.TestCase):
    """The engine, in a browser, on real frames."""

    server = None
    thread = None
    port = None

    @classmethod
    def setUpClass(cls):
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            raise unittest.SkipTest(
                "playwright is not installed — install it to verify the "
                "browser calibration engine (pip install playwright)")
        cls.chrome = find_chromium()
        if not cls.chrome:
            raise unittest.SkipTest(
                "no Chromium build found under /opt/pw-browsers — the browser "
                "calibration engine could not be exercised")
        cls.frames = sorted(glob.glob(os.path.join(FRAME_DIR, "*.jpg")))
        if len(cls.frames) < 3:
            raise unittest.SkipTest(
                f"need at least 3 committed evidence frames in {FRAME_DIR}")

        handler = functools.partial(QuietHandler, directory=REPO)
        cls.server = socketserver.TCPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()
        cls.result = cls._calibrate()

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.shutdown()
            cls.server.server_close()

    @classmethod
    def _calibrate(cls):
        from playwright.sync_api import sync_playwright

        base = f"http://127.0.0.1:{cls.port}"
        urls = [f"{base}/reports/ingest/qad-twis-nepal/frames/"
                f"{os.path.basename(f)}" for f in cls.frames]
        errors = []
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=cls.chrome)
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base}/404.html", wait_until="domcontentloaded")
            page.add_script_tag(url=f"{base}/assets/js/calibrate/engine.js")
            page.wait_for_function("window.OWCSCalibrate !== undefined",
                                   timeout=15000)
            result = page.evaluate("""async (files) => {
              const datas = [];
              for (const url of files) {
                const img = new Image();
                img.crossOrigin = 'anonymous';
                img.src = url;
                await img.decode();
                const c = document.createElement('canvas');
                c.width = img.naturalWidth; c.height = img.naturalHeight;
                c.getContext('2d').drawImage(img, 0, 0);
                datas.push(c.getContext('2d')
                  .getImageData(0, 0, c.width, c.height));
              }
              const r = window.OWCSCalibrate.calibrate(datas);
              delete r.blobs;                    // large, and not asserted on
              return r;
            }""", urls)
            browser.close()
        if errors:
            raise AssertionError(f"the engine threw in the browser: {errors}")
        return result

    # ------------------------------------------------------------ reference
    def reference_slots(self):
        with open(REFERENCE, encoding="utf-8") as f:
            doc = json.load(f)
        scale = self.result["frameW"] / doc["frame_width"]
        return ([[round(v * scale) for v in b] for b in doc["slots_a"]],
                [[round(v * scale) for v in b] for b in doc["slots_b"]])

    # --------------------------------------------------------------- tests
    def test_it_found_a_result_at_all(self):
        self.assertIsNotNone(self.result.get("boxesA"),
                             f"no slots found: {self.result.get('reasons')}")
        self.assertEqual(len(self.result["boxesA"]), 5)
        self.assertEqual(len(self.result["boxesB"]), 5)

    def test_it_clears_its_own_confidence_floor(self):
        self.assertGreaterEqual(
            self.result["confidence"], self.result["floor"],
            f"confidence {self.result['confidence']} is below the floor on a "
            f"broadcast the pipeline calibrates cleanly. Reasons: "
            f"{self.result.get('reasons')}")
        self.assertTrue(self.result["ok"])

    def test_every_slot_matches_the_pipelines_own_calibration(self):
        """The decisive test. Ten boxes, compared to a layout produced
        independently by calibrate_source.py from the same broadcast."""
        expected_a, expected_b = self.reference_slots()
        problems = []
        for label, detected, expected in (("A", self.result["boxesA"], expected_a),
                                          ("B", self.result["boxesB"], expected_b)):
            for i, (got, want) in enumerate(zip(detected, expected), 1):
                dx, dy = abs(got[0] - want[0]), abs(got[1] - want[1])
                ds = abs(got[2] - want[2])
                if dx > TOLERANCE_PX or dy > TOLERANCE_PX:
                    problems.append(
                        f"{label}{i}: at {got[:2]}, pipeline says {want[:2]} "
                        f"(off by {dx},{dy}px)")
                if ds > SIZE_TOLERANCE_PX:
                    problems.append(
                        f"{label}{i}: {got[2]}px box, pipeline says {want[2]}px")
        self.assertEqual(problems, [],
                         "the browser calibrator disagrees with the pipeline:\n  "
                         + "\n  ".join(problems))

    def test_the_two_rows_share_a_size_and_height(self):
        """One HUD package means one portrait row. Sides drifting apart is
        the failure this used to have."""
        a, b = self.result["boxesA"], self.result["boxesB"]
        self.assertEqual(a[0][2], b[0][2],
                         "the two teams' portrait boxes are different sizes")
        self.assertLessEqual(abs(a[0][1] - b[0][1]), 2,
                             "the two teams' rows are at different heights")

    def test_slots_are_evenly_spaced(self):
        for label, boxes in (("A", self.result["boxesA"]),
                             ("B", self.result["boxesB"])):
            gaps = [boxes[i + 1][0] - boxes[i][0] for i in range(4)]
            spread = max(gaps) - min(gaps)
            self.assertLessEqual(spread, 3,
                                 f"team {label} slots are not evenly spaced: "
                                 f"{gaps}")

    def test_it_produces_a_layout_the_registry_can_read(self):
        sys.path.insert(0, HERE)
        import layout_registry as lr
        from playwright.sync_api import sync_playwright

        base = f"http://127.0.0.1:{self.port}"
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=self.chrome)
            page = browser.new_page()
            page.goto(f"{base}/404.html", wait_until="domcontentloaded")
            page.add_script_tag(url=f"{base}/assets/js/calibrate/engine.js")
            page.wait_for_function("window.OWCSCalibrate !== undefined",
                                   timeout=15000)
            layout = page.evaluate(
                "(r) => window.OWCSCalibrate.toLayout(r, {name: 'unit-test'})",
                self.result)
            browser.close()

        for key in ("frame_width", "frame_height", "slots_a", "slots_b",
                    "match_threshold", "templates_dir"):
            self.assertIn(key, layout, f"the emitted layout has no {key}")
        self.assertEqual(len(layout["slots_a"]), 5)
        self.assertEqual(len(layout["slots_b"]), 5)
        # And it must NOT claim production calibration.
        self.assertNotIn("hud_probe", layout)
        self.assertFalse(lr.is_calibrated(layout))
        self.assertEqual(layout["calibration_source"], "browser")

    def test_it_refuses_when_given_nothing_to_work_with(self):
        """A blank frame must produce a refusal with a reason, not a guess."""
        from playwright.sync_api import sync_playwright

        base = f"http://127.0.0.1:{self.port}"
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=self.chrome)
            page = browser.new_page()
            page.goto(f"{base}/404.html", wait_until="domcontentloaded")
            page.add_script_tag(url=f"{base}/assets/js/calibrate/engine.js")
            page.wait_for_function("window.OWCSCalibrate !== undefined",
                                   timeout=15000)
            out = page.evaluate("""() => {
              const blanks = [];
              for (let i = 0; i < 3; i++) {
                const c = document.createElement('canvas');
                c.width = 1280; c.height = 720;
                const ctx = c.getContext('2d');
                ctx.fillStyle = '#101418';
                ctx.fillRect(0, 0, 1280, 720);
                blanks.push(ctx.getImageData(0, 0, 1280, 720));
              }
              const r = window.OWCSCalibrate.calibrate(blanks);
              delete r.blobs;
              return r;
            }""")
            browser.close()
        self.assertFalse(out["ok"], "a blank frame produced a confident result")
        self.assertTrue(out.get("reasons"),
                        "the engine refused without saying why")


if __name__ == "__main__":
    unittest.main(verbosity=2)
