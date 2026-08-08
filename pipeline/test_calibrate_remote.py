#!/usr/bin/env python3
"""
test_calibrate_remote.py — can a broadcast be calibrated without downloading it?

This is the end-to-end proof of the sparse workflow. It runs the real
driver against a broadcast-shaped MP4 built from the repository's own
committed OWCS frames and served over a byte-range HTTP server that counts
what it sends, so every claim below is measured rather than asserted:

  * calibration completes from sparse remote samples alone,
  * the whole VOD is never downloaded,
  * a re-run reuses the cache and pays nothing,
  * a broadcast whose first pass finds too little evidence densifies —
    and densifies TOWARDS the gameplay rather than uniformly,
  * the existing gameplay filter is what decides which frames calibrate,
  * the existing confidence floor and refusal behaviour are untouched.

The fixture's live-gameplay windows are known, so the scan can be checked
against ground truth: the frames it keeps must come from inside them.

Run: python3 pipeline/test_calibrate_remote.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "fixtures"))

import calibrate_remote as cr  # noqa: E402
import calibrate_source as cs  # noqa: E402
import remote_frames as rf  # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None

FIXTURE_DURATION = 900.0
FIXTURE_WINDOWS = [(180.0, 360.0), (600.0, 780.0)]

_STATE: dict = {}


def _serve() -> tuple[str, object]:
    if "url" not in _STATE:
        import make_broadcast
        import range_server
        tmp = tempfile.mkdtemp(prefix="crx_")
        _STATE["tmp"] = tmp
        vod = os.path.join(tmp, "vod.mp4")
        make_broadcast.build(vod, FIXTURE_DURATION, FIXTURE_WINDOWS)
        httpd, port = range_server.serve(tmp)
        _STATE.update(server=httpd, vod=vod,
                      url=f"http://127.0.0.1:{port}/vod.mp4")
    return _STATE["url"], __import__("range_server")


def tearDownModule():                              # noqa: N802
    if _STATE.get("server"):
        _STATE["server"].shutdown()
    if _STATE.get("tmp"):
        shutil.rmtree(_STATE["tmp"], ignore_errors=True)


def _resolver(url, height):
    return url, "test-direct"


def in_a_window(t: float) -> bool:
    return any(a <= t < b for a, b in FIXTURE_WINDOWS)


# --------------------------------------------------------------- pure logic
class TestLadderAndDensification(unittest.TestCase):
    """The densification policy, without touching a video."""

    def test_the_scan_starts_at_sixty_seconds(self):
        self.assertEqual(cr.DEFAULT_LADDER[0], 60.0,
                         "the documented starting rate is 60s")
        self.assertEqual(cr.DEFAULT_LADDER[1], 30.0,
                         "the first densification is to 30s")

    def test_it_never_asks_for_the_instant_past_the_end(self):
        offsets = cr.next_offsets(60.0, 0.0, 300.0, set())
        self.assertNotIn(300.0, offsets)
        self.assertEqual(offsets[-1], 240.0)

    def test_densification_skips_what_is_already_sampled(self):
        already = {0.0, 60.0, 120.0}
        offsets = cr.next_offsets(30.0, 0.0, 180.0, already)
        self.assertEqual(offsets, [30.0, 90.0, 150.0])

    def test_the_second_densification_only_looks_where_it_can_pay_off(self):
        """A broadcast that is mostly desk must not be swept twice as hard
        everywhere — only near offsets that already showed HUD structure."""
        promising = [600.0]
        offsets = cr.targeted_offsets(15.0, promising, 0.0, 3600.0, set())
        self.assertTrue(offsets, "should propose something near the hit")
        self.assertTrue(all(abs(t - 600.0) <= cr.NEIGHBOURHOOD for t in offsets),
                        "targeted densification must stay near the evidence")
        self.assertLess(len(offsets), 3600 / 15,
                        "targeted densification must be cheaper than a "
                        "global sweep")

    def test_evidence_must_be_spread_out_not_just_plentiful(self):
        """Twelve frames from one teamfight measure one lighting condition."""
        clustered = {float(t): "x" for t in range(100, 100 + 12)}
        ok, why = cr.enough_evidence(clustered, 3600.0)
        self.assertFalse(ok)
        self.assertIn("region", why)

        spread = {float(t): "x" for t in range(0, 3600, 300)}
        ok, why = cr.enough_evidence(spread, 3600.0)
        self.assertTrue(ok, why)

    def test_the_chosen_frames_are_spread_across_the_broadcast(self):
        clean = {float(t): f"f{t}" for t in range(0, 3600, 60)}
        picked = cr.pick_diverse(clean, 3600.0, limit=8)
        self.assertEqual(len(picked), 8)
        regions = {cr.region_of(float(p[1:]), 3600.0) for p in picked}
        self.assertGreaterEqual(len(regions), 4,
                                "picking must not take the first eight")

    def test_the_scan_cost_does_not_grow_with_broadcast_length(self):
        """The property that makes this work on a nine-hour VOD.

        A 60-second ladder over nine hours is 540 offsets. Fetching all of
        them before asking "is that enough?" would cost more than a hundred
        megabytes to answer a question two dozen frames usually settle — so
        the ladder is visited spread-first and evaluated in chunks.
        """
        nine_hours = 9 * 3600.0
        ladder = cr.next_offsets(60.0, 0.0, nine_hours, set())
        self.assertEqual(len(ladder), 540)

        order = cr.spread_first(ladder)
        self.assertEqual(sorted(order), sorted(ladder),
                         "re-ordering must not drop or invent an offset")
        self.assertEqual(len(set(order)), len(order), "duplicated offsets")

        first_chunk = order[:cr.CHUNK]
        regions = {cr.region_of(t, nine_hours) for t in first_chunk}
        self.assertEqual(
            len(regions), cr.REGION_COUNT,
            f"the first {cr.CHUNK} samples must already cover the whole "
            f"broadcast, not its first {cr.CHUNK} minutes (covered "
            f"{sorted(regions)})")

    def test_spread_first_is_stable_for_tiny_ladders(self):
        self.assertEqual(cr.spread_first([]), [])
        self.assertEqual(cr.spread_first([5.0]), [5.0])
        self.assertEqual(cr.spread_first([1.0, 2.0]), [1.0, 2.0])
        self.assertEqual(sorted(cr.spread_first([1.0, 2.0, 3.0])),
                         [1.0, 2.0, 3.0])

    def test_gameplay_windows_are_derived_from_the_clean_samples(self):
        clean = [180.0, 240.0, 300.0, 600.0, 660.0, 720.0]
        windows = cr.gameplay_windows(clean, gap=180.0)
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0]["start"], 180.0)
        self.assertEqual(windows[1]["end"], 720.0)


class TestFloorIsNotRelaxed(unittest.TestCase):
    """The sparse path chooses frames. It does not get a vote on whether
    the resulting calibration is good enough."""

    def test_the_confidence_floor_is_calibrate_sources_own(self):
        with open(os.path.join(HERE, "calibrate_remote.py"),
                  encoding="utf-8") as f:
            src = f.read()
        self.assertIn("cs.CONFIDENCE_FLOOR", src)
        self.assertNotIn("CONFIDENCE_FLOOR =", src,
                         "calibrate_remote must not define its own floor")

    def test_it_hands_off_to_the_real_cli(self):
        with open(os.path.join(HERE, "calibrate_remote.py"),
                  encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"--frames-dir"', src)
        self.assertIn("cs.main(", src,
                      "the surviving frames must go through the existing "
                      "calibrate_source CLI, not a reimplementation")


# ------------------------------------------------------- the real workflow
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg is not installed")
class TestCalibrationFromSparseRemoteFrames(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.url, cls.server = _serve()
        cls.vod_bytes = os.path.getsize(_STATE["vod"])
        cls.work = tempfile.mkdtemp(prefix="crw_")
        cls.cache = os.path.join(cls.work, "cache")
        cls.server.reset()
        cls.result = cr.run(
            cls.url, "fixture-bcast",
            os.path.join(cls.work, "layout.json"),
            duration=FIXTURE_DURATION, cache_root=cls.cache,
            frames_dir=os.path.join(cls.work, "frames"),
            sheet=os.path.join(cls.work, "sheet.png"),
            windows_out=os.path.join(cls.work, "windows.json"),
            resolver=_resolver)
        cls.served_first_run = cls.server.total_served()

        # The SECOND run, immediately, against the same cache. Done here
        # rather than inside a test because two tests examine it and
        # unittest orders tests alphabetically, not by intent.
        cls.server.reset()
        cls.layout2 = os.path.join(cls.work, "layout2.json")
        cls.result2 = cr.run(
            cls.url, "fixture-bcast", cls.layout2,
            duration=FIXTURE_DURATION, cache_root=cls.cache,
            frames_dir=os.path.join(cls.work, "frames2"),
            resolver=_resolver)
        cls.served_second_run = cls.server.total_served()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def test_it_produced_a_layout(self):
        self.assertTrue(self.result["ok"],
                        f"calibration failed: {self.result.get('reason')}")
        self.assertTrue(os.path.exists(self.result["layoutPath"]))

    def test_the_layout_is_a_real_calibration_with_ten_slots(self):
        with open(self.result["layoutPath"], encoding="utf-8") as f:
            layout = json.load(f)
        self.assertEqual(len(layout["slots_a"]), 5)
        self.assertEqual(len(layout["slots_b"]), 5)
        self.assertEqual(len(layout["hud_probe"]["chips_a"]), 5)
        self.assertEqual(len(layout["hud_probe"]["chips_b"]), 5)
        self.assertGreaterEqual(layout["calibration"]["confidence"],
                                cs.CONFIDENCE_FLOOR)
        self.assertEqual(layout["calibration"]["version"], cs.CALIB_VERSION)

    def test_the_whole_vod_was_never_downloaded(self):
        """The headline claim, measured at the server."""
        self.assertLess(
            self.served_first_run, self.vod_bytes,
            f"calibration pulled {self.served_first_run / 1e6:.1f} MB of a "
            f"{self.vod_bytes / 1e6:.1f} MB broadcast — that is a download")

    def test_only_one_media_url_resolution_was_needed(self):
        self.assertLessEqual(self.result["acquisition"]["ytdlpCalls"], 1)

    def test_the_existing_gameplay_filter_rejected_the_desk_segments(self):
        """The fixture is mostly desk. Those frames must be thrown out by
        gameplay_state, not merely by the candidate screen."""
        acquired = self.result["framesAcquired"]
        verified = self.result["framesVerifiedGameplay"]
        self.assertGreater(acquired, verified,
                           "some acquired frames should have been rejected")
        self.assertGreaterEqual(verified, cs.MIN_GOOD_FRAMES)

    def test_every_frame_it_calibrated_from_came_from_live_gameplay(self):
        """Ground truth: the fixture's live windows are known."""
        frames_dir = os.path.join(self.work, "frames")
        offsets = [float(fn[len("calib_"):-len(".png")])
                   for fn in os.listdir(frames_dir) if fn.startswith("calib_")]
        self.assertTrue(offsets)
        stray = [t for t in offsets if not in_a_window(t)]
        self.assertEqual(stray, [],
                         f"calibrated from frames outside the live windows: "
                         f"{stray}")

    def test_it_found_the_gameplay_windows(self):
        windows = self.result["gameplayWindows"]
        self.assertEqual(len(windows), len(FIXTURE_WINDOWS),
                         f"expected {len(FIXTURE_WINDOWS)} windows, got "
                         f"{windows}")
        for found, (a, b) in zip(windows, FIXTURE_WINDOWS):
            self.assertGreaterEqual(found["start"], a)
            self.assertLessEqual(found["end"], b)
        with open(os.path.join(self.work, "windows.json"),
                  encoding="utf-8") as f:
            self.assertEqual(json.load(f)["schema"], "gameplay-windows.v1")

    def test_it_stopped_early_instead_of_scanning_the_whole_broadcast(self):
        stop = self.result["scan"]["stopReason"]
        self.assertNotEqual(stop, "ladder exhausted",
                            "the scan should have stopped on evidence")
        full_sweep_at_finest = FIXTURE_DURATION / cr.DEFAULT_LADDER[-1]
        self.assertLess(self.result["framesAcquired"], full_sweep_at_finest,
                        "acquiring more than a full fine sweep is not sparse")

    def test_it_densified_because_sixty_seconds_was_not_enough(self):
        """The fixture has 6 live minutes in 15, so a 60s pass finds ~6
        clean frames — under the target. The scan must respond by
        densifying rather than calibrating on too little."""
        passes = self.result["scan"]["passes"]
        self.assertGreaterEqual(len(passes), 2,
                                f"expected a densification pass: {passes}")
        self.assertEqual(passes[0]["interval"], 60.0)
        self.assertLess(passes[1]["interval"], passes[0]["interval"])
        self.assertGreater(passes[1]["newClean"], 0,
                           "densifying should have found more gameplay")

    def test_it_evaluated_its_evidence_before_finishing_a_pass(self):
        """Chunked evaluation is what keeps the cost off the broadcast's
        length. Every pass must record how many chunks it took, and the
        last one must not have run to completion if the evidence arrived
        first."""
        passes = self.result["scan"]["passes"]
        for p in passes:
            self.assertIn("chunks", p)
            self.assertGreaterEqual(p["chunks"], 1)
            self.assertLessEqual(
                p["fetched"], p["requested"],
                "a pass cannot fetch more than its own ladder")
        self.assertIn("stopped early", self.result["scan"]["stopReason"])

    def test_a_rerun_reuses_the_cache_and_costs_nothing(self):
        self.assertTrue(self.result2["ok"],
                        f"re-run failed: {self.result2.get('reason')}")
        self.assertEqual(self.served_second_run, 0,
                         "a re-run must not touch the network")
        self.assertEqual(self.result2["acquisition"]["ffmpegCalls"], 0)
        self.assertEqual(self.result2["acquisition"]["framesFromCache"],
                         self.result2["acquisition"]["framesRequested"])

    def test_the_layout_lands_where_the_committed_reference_says(self):
        """The correctness guard, against real ground truth.

        The fixture is built from the very frames `layouts/owcs_jksix_qwc.
        json` was calibrated from, so there IS a right answer here and the
        sparse path can be held to it. Comparing the sparse path against
        the full-download path instead would only ever say the two differ —
        never which one is wrong.

        The tolerance is 30px at 1920x1080. A portrait is ~54px wide and
        the chip pitch is ~107px, so 30px cannot hide a slot landing on the
        wrong hero, while still allowing for the fixture being a re-encode
        of those frames at a different quantiser (which moves blob edges by
        a pixel or two) and for the reference having been calibrated from
        eight hand-picked instants rather than a scan.
        """
        ref_path = os.path.join(REPO, "layouts", "owcs_jksix_qwc.json")
        if not os.path.exists(ref_path):
            self.skipTest("the reference layout is not committed")
        with open(ref_path, encoding="utf-8") as f:
            ref = json.load(f)
        with open(self.result["layoutPath"], encoding="utf-8") as f:
            got = json.load(f)

        worst = 0
        for side in ("slots_a", "slots_b"):
            for i, (a, b) in enumerate(zip(ref[side], got[side]), start=1):
                d = max(abs(x - y) for x, y in zip(a, b))
                worst = max(worst, d)
                self.assertLessEqual(
                    d, 30,
                    f"{side[-1]}{i} landed {d}px from the reference "
                    f"(reference {a}, sparse {b}) — that is far enough to be "
                    f"on a different hero")
        print(f"\n    [ground truth] worst slot delta vs the committed "
              f"reference: {worst}px at 1920x1080")

    def test_a_rerun_produces_the_same_layout(self):
        """Determinism: same frames in, same geometry out."""
        with open(self.result["layoutPath"], encoding="utf-8") as f:
            first = json.load(f)
        with open(self.layout2, encoding="utf-8") as f:
            second = json.load(f)
        self.assertEqual(first["slots_a"], second["slots_a"])
        self.assertEqual(first["slots_b"], second["slots_b"])
        self.assertEqual(first["hud_probe"]["chips_a"],
                         second["hud_probe"]["chips_a"])


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg is not installed")
class TestRefusalIsPreserved(unittest.TestCase):
    """A broadcast with no HUD must be refused, not guessed at."""

    def test_a_broadcast_with_no_gameplay_is_refused(self):
        import make_broadcast
        import range_server
        tmp = tempfile.mkdtemp(prefix="crn_")
        try:
            vod = os.path.join(tmp, "desk.mp4")
            make_broadcast.build(vod, 300.0, [])      # desk only, no HUD
            httpd, port = range_server.serve(tmp)
            try:
                res = cr.run(f"http://127.0.0.1:{port}/desk.mp4",
                             "no-hud", os.path.join(tmp, "layout.json"),
                             duration=300.0,
                             cache_root=os.path.join(tmp, "cache"),
                             frames_dir=os.path.join(tmp, "frames"),
                             resolver=_resolver)
            finally:
                httpd.shutdown()
            self.assertFalse(res["ok"])
            self.assertFalse(os.path.exists(os.path.join(tmp, "layout.json")),
                             "a refused calibration must write no layout")
            self.assertIn("chip", (res.get("reason") or "").lower())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
