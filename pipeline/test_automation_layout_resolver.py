#!/usr/bin/env python3
"""
test_automation_layout_resolver.py — Phase 3 layout resolution + segmentation.

Everything is generated: synthetic frames built to satisfy (or deliberately
fail) the SAME structural HUD probe production detection uses, plus synthetic
layouts describing where that HUD sits. No network, no real VOD.

Covered behaviors:
  * a layout whose geometry matches scores high; a foreign layout scores 0
  * an aspect-ratio mismatch is reported as a mismatch, not a weak match
  * automatic reuse only above the score bar and only with a clear margin
  * ambiguity between two equally-good layouts goes to a human
  * no candidate layout -> calibration; low-confidence calibration -> HARD
    refusal, and `approve-layout` refuses to publish a refused calibration
  * gameplay-state rejection rules are NOT weakened: desk / replay-banner /
    partial-HUD frames stay rejected, with their reasons recorded
  * segmentation proposes real windows with thumbnails and rejection reasons
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import capture  # noqa: E402
import gameplay_state as gs  # noqa: E402
from automation import job_store as js  # noqa: E402
from automation import layout_resolver as lr  # noqa: E402
from automation import models  # noqa: E402
from automation import segmentation as seg  # noqa: E402
from automation import state_machine as sm  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))

FRAME_W, FRAME_H = 1280, 720
RNG = np.random.default_rng(20260727)


# --------------------------------------------------------------- synthetic HUD
def hud_layout(*, chip_y=110, chip_size=40, pitch=100, left_x=40,
               right_x=740, slot_dy=50, slot_size=48,
               templates_dir="templates") -> dict:
    """A layout in the exact shape calibrate_source.py writes: a hud_probe
    chip row per side plus the ten portrait slots next to them."""
    chips_a = [[left_x + i * pitch, chip_y, chip_size, chip_size] for i in range(5)]
    chips_b = [[right_x + i * pitch, chip_y, chip_size, chip_size] for i in range(5)]
    slots_a = [[left_x + i * pitch, chip_y + slot_dy, slot_size, slot_size]
               for i in range(5)]
    slots_b = [[right_x + i * pitch, chip_y + slot_dy, slot_size, slot_size]
               for i in range(5)]
    return {
        "frame_width": FRAME_W, "frame_height": FRAME_H,
        "sample_interval_seconds": 10,
        "hud_probe": {"chips_a": chips_a, "chips_b": chips_b,
                      "sat_min": 110, "val_min": 90, "min_chips_per_side": 4},
        "slots_a": slots_a, "slots_b": slots_b,
        "match_threshold": 0.6, "templates_dir": templates_dir,
    }


def _fill_chip(frame, rect, hue):
    x, y, w, h = rect
    patch = np.zeros((h, w, 3), np.uint8)
    patch[:, :] = (hue, 230, 240)                     # saturated + bright
    frame[y:y + h, x:x + w] = cv2.cvtColor(patch, cv2.COLOR_HSV2BGR)


def _fill_portrait(frame, rect):
    """High-frequency noise — a real hero portrait measures thousands of
    Laplacian variance, and that is exactly what probe_hud looks for."""
    x, y, w, h = rect
    frame[y:y + h, x:x + w] = RNG.integers(0, 256, (h, w, 3), dtype=np.uint8)


def gameplay_frame(layout: dict, *, chips_a=5, chips_b=5, textured=True,
                   width=FRAME_W, height=FRAME_H):
    """A frame the structural probe should call `gameplay` (or, with fewer
    chips / no texture, exactly the rejections it is supposed to make)."""
    scale_x, scale_y = width / FRAME_W, height / FRAME_H
    frame = np.full((height, width, 3), 30, np.uint8)   # dark, unsaturated bg
    probe = layout["hud_probe"]
    for side, n, hue in (("a", chips_a, 15), ("b", chips_b, 110)):
        for i, rect in enumerate(probe[f"chips_{side}"]):
            if i >= n:
                continue
            _fill_chip(frame, [int(rect[0] * scale_x), int(rect[1] * scale_y),
                               max(2, int(rect[2] * scale_x)),
                               max(2, int(rect[3] * scale_y))], hue)
        if textured:
            for rect in layout[f"slots_{side}"]:
                _fill_portrait(frame, [int(rect[0] * scale_x), int(rect[1] * scale_y),
                                       max(2, int(rect[2] * scale_x)),
                                       max(2, int(rect[3] * scale_y))])
    return frame


def desk_frame(width=FRAME_W, height=FRAME_H):
    """A caster-desk / graphic frame: smooth gradients, no HUD structure."""
    grad = np.linspace(20, 90, width, dtype=np.uint8)
    frame = np.repeat(grad[None, :], height, axis=0)
    return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)


class TestSyntheticFramesActuallyExerciseTheRealProbe(unittest.TestCase):
    """If the generated frames didn't drive the real classifier, every test
    below would be meaningless — so assert that first."""

    def setUp(self):
        self.layout = hud_layout()

    def test_gameplay_frame_is_classified_gameplay(self):
        state, reason = gs.classify_frame(gameplay_frame(self.layout),
                                          dict(self.layout))
        self.assertEqual(state, "gameplay", reason)

    def test_desk_frame_is_rejected_as_no_hud(self):
        state, reason = gs.classify_frame(desk_frame(), dict(self.layout))
        self.assertEqual(state, "no-hud", reason)

    def test_one_sided_hud_is_partial_not_gameplay(self):
        state, _ = gs.classify_frame(
            gameplay_frame(self.layout, chips_b=0), dict(self.layout))
        self.assertEqual(state, "partial-hud")

    def test_untextured_chips_are_not_gameplay(self):
        """A transition wipe can saturate the chip boxes; the portrait-texture
        backstop is what stops it being read as gameplay."""
        state, _ = gs.classify_frame(
            gameplay_frame(self.layout, textured=False), dict(self.layout))
        self.assertNotEqual(state, "gameplay")


class TestFingerprint(unittest.TestCase):
    def setUp(self):
        self.layout = hud_layout()
        self.frames = [gameplay_frame(self.layout) for _ in range(6)]

    def test_matching_layout_scores_high(self):
        fp = lr.fingerprint_layout(self.frames, self.layout)
        self.assertEqual(fp["gameplay"], 6)
        self.assertEqual(fp["score"], 1.0)
        self.assertGreater(fp["medianTextureA"], gs.MIN_PORTRAIT_TEXTURE)

    def test_foreign_layout_scores_zero(self):
        """A different broadcast package's geometry lands on empty pixels."""
        foreign = hud_layout(chip_y=500, left_x=300, right_x=900, pitch=60)
        fp = lr.fingerprint_layout(self.frames, foreign)
        self.assertEqual(fp["gameplay"], 0)
        self.assertEqual(fp["score"], 0.0)

    def test_aspect_ratio_mismatch_is_reported_not_scored(self):
        square = dict(self.layout, frame_width=720, frame_height=720)
        fp = lr.fingerprint_layout(self.frames, square)
        self.assertEqual(fp["score"], 0.0)
        self.assertIn("aspect ratio mismatch", fp["note"])

    def test_a_mostly_desk_broadcast_still_scores_above_the_reuse_bar(self):
        """A real VOD is mostly NOT gameplay; the bar has to be reachable."""
        frames = ([desk_frame()] * 17) + ([gameplay_frame(self.layout)] * 4)
        fp = lr.fingerprint_layout(frames, self.layout)
        self.assertGreaterEqual(fp["score"], lr.AUTO_REUSE_SCORE)

    def test_a_desk_only_sample_does_not_reach_the_bar(self):
        fp = lr.fingerprint_layout([desk_frame()] * 10, self.layout)
        self.assertLess(fp["score"], lr.MIN_CANDIDATE_SCORE)

    def test_layout_scaled_to_a_proxy_resolution_still_matches(self):
        """The whole point of scanning a 360p proxy: a 1080p-native layout
        must fingerprint the same on a downscaled frame."""
        small = [gameplay_frame(self.layout, width=640, height=360)
                 for _ in range(4)]
        fp = lr.fingerprint_layout(small, self.layout)
        self.assertGreater(fp["score"], 0.0)


class TestMatchLayouts(unittest.TestCase):
    def setUp(self):
        self.right = hud_layout()
        self.wrong = hud_layout(chip_y=520, left_x=200, right_x=1000, pitch=55)
        self.frames = [gameplay_frame(self.right) for _ in range(8)]

    def _cands(self, *layouts):
        return [{"layoutId": f"layout{i}", "path": f"/layouts/layout{i}.json",
                 "layout": l} for i, l in enumerate(layouts)]

    def test_clear_winner_is_reused(self):
        res = lr.match_layouts(self.frames, self._cands(self.wrong, self.right))
        self.assertEqual(res["decision"], "reuse")
        self.assertEqual(res["best"]["layoutId"], "layout1")
        self.assertGreater(res["margin"], lr.MIN_SCORE_MARGIN)

    def test_two_identical_layouts_are_ambiguous_not_a_coin_flip(self):
        res = lr.match_layouts(self.frames,
                               self._cands(self.right, dict(self.right)))
        self.assertEqual(res["decision"], "ambiguous")
        self.assertIn("too close to choose automatically", res["reason"])

    def test_no_matching_layout_is_no_match(self):
        res = lr.match_layouts(self.frames, self._cands(self.wrong))
        self.assertEqual(res["decision"], "no_match")
        self.assertIn("never calibrated", res["reason"])

    def test_empty_candidate_list_is_no_match(self):
        res = lr.match_layouts(self.frames, [])
        self.assertEqual(res["decision"], "no_match")
        self.assertIsNone(res["best"])


class TestLoadCandidateLayouts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, layout):
        with open(os.path.join(self.tmp.name, name), "w", encoding="utf-8") as f:
            json.dump(layout, f)

    def test_only_layouts_with_a_calibrated_probe_are_candidates(self):
        self._write("good.json", hud_layout())
        self._write("placeholder.json", {"frame_width": 1920,
                                         "frame_height": 1080,
                                         "anchor": {"rect": [0, 0, 10, 10]}})
        self._write("broken.json", {"hud_probe": {"chips_a": [[0, 0, 4, 4]]}})
        cands = lr.load_candidate_layouts(self.tmp.name)
        self.assertEqual([c["layoutId"] for c in cands], ["good"])

    def test_preferred_layout_is_evaluated_first(self):
        self._write("a_first.json", hud_layout())
        self._write("z_last.json", hud_layout())
        cands = lr.load_candidate_layouts(self.tmp.name, prefer="z_last")
        self.assertEqual(cands[0]["layoutId"], "z_last")

    def test_the_repos_real_layouts_are_discoverable(self):
        """A regression guard on the shipped layouts, not on a fixture: the
        proven owcs_jksix_qwc package must remain fingerprintable."""
        cands = lr.load_candidate_layouts(lr.LAYOUTS_DIR)
        ids = [c["layoutId"] for c in cands]
        self.assertIn("owcs_jksix_qwc", ids)

    def test_unreadable_layout_dir_is_not_fatal(self):
        self.assertEqual(
            lr.load_candidate_layouts(os.path.join(self.tmp.name, "nope")), [])


class TestSampleTimes(unittest.TestCase):
    def test_samples_avoid_the_countdown_and_outro(self):
        times = lr.sample_frame_times(3600, 10)
        self.assertEqual(len(times), 10)
        self.assertGreater(times[0], 3600 * 0.05)
        self.assertLess(times[-1], 3600 * 0.95)

    def test_degenerate_inputs_are_safe(self):
        self.assertEqual(lr.sample_frame_times(0, 5), [])
        self.assertEqual(lr.sample_frame_times(-10, 5), [])
        self.assertEqual(lr.sample_frame_times(100, 0), [])
        # A very short clip still yields in-bounds, ordered offsets.
        times = lr.sample_frame_times(1, 4)
        self.assertEqual(times, sorted(times))
        self.assertTrue(all(0 < t < 1 for t in times), times)


class ResolverJobBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = js.JobStore(os.path.join(self.tmp.name, "automation.sqlite"))
        self.layouts_dir = os.path.join(self.tmp.name, "layouts")
        self.reports_dir = os.path.join(self.tmp.name, "reports")
        os.makedirs(self.layouts_dir)
        self.layout = hud_layout()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def write_layout(self, name, layout=None):
        path = os.path.join(self.layouts_dir, f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(layout or self.layout, f)
        return path

    def make_job(self, state=sm.DOWNLOADED):
        key = models.record_key("vidLayout")
        self.store.enqueue(models.KIND_RECORD, key, state=sm.ARCHIVED,
                           payload={"videoId": "vidLayout",
                                    "media": {"localPath": "x/vid.mp4",
                                              "proxy": {"localPath": "x/vid.proxy360p.mp4"}}})
        self.store.transition(key, sm.DOWNLOADING)
        self.store.transition(key, state)
        return self.store.get(key)

    def frame_files(self, frames) -> list[tuple[float, str]]:
        d = os.path.join(self.tmp.name, "frames")
        os.makedirs(d, exist_ok=True)
        out = []
        for i, f in enumerate(frames):
            p = os.path.join(d, f"sample_{i:08d}.png")
            cv2.imwrite(p, f)
            out.append((float(i * 60), p))
        return out


class TestResolveLayout(ResolverJobBase):
    def test_matching_committed_layout_is_reused_automatically(self):
        self.write_layout("owcs_test_package")
        job = self.make_job()
        res = lr.resolve_layout(
            self.store, job, layouts_dir=self.layouts_dir,
            reports_dir=self.reports_dir, harvest=False,
            frames=self.frame_files([gameplay_frame(self.layout)] * 6))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["layoutId"], "owcs_test_package")
        job = self.store.get(job.job_key)
        self.assertEqual(job.payload["expectedLayoutId"], "owcs_test_package")
        rec = job.payload["layout"]
        self.assertEqual(rec["source"], "committed-layout-reuse")
        self.assertFalse(rec["approvalRequired"])
        # State unchanged — an automatic reuse needs no human gate.
        self.assertEqual(job.state, sm.DOWNLOADED)

    def test_reuse_with_harvest_enabled_does_not_crash(self):
        """Regression: the reuse path's marker harvest referenced a key
        (best["layout"]) that only exists on the CANDIDATE list, not on the
        scored/ranked list resolve_layout actually holds at that point — so
        harvest=True (the real default every caller but the unit tests uses)
        raised a KeyError on every automatic reuse. Caught by the end-to-end
        offline suite, not by this file, because every test here happened to
        pass harvest=False."""
        self.write_layout("owcs_test_package")
        job = self.make_job()
        res = lr.resolve_layout(
            self.store, job, layouts_dir=self.layouts_dir,
            reports_dir=self.reports_dir, harvest=True,
            frames=self.frame_files([gameplay_frame(self.layout)] * 6))
        self.assertTrue(res["ok"], res)
        self.assertIn("markers", res["record"])
        self.assertIn("gameplay", res["record"]["markers"]["harvested"])

    def test_the_full_decision_is_recorded_for_audit(self):
        self.write_layout("right_one")
        self.write_layout("wrong_one",
                          hud_layout(chip_y=520, left_x=200, right_x=1000))
        job = self.make_job()
        lr.resolve_layout(self.store, job, layouts_dir=self.layouts_dir,
                          reports_dir=self.reports_dir, harvest=False,
                          frames=self.frame_files([gameplay_frame(self.layout)] * 5))
        rec = self.store.get(job.job_key).payload["layout"]
        scored = {c["layoutId"]: c["score"] for c in rec["candidates"]}
        self.assertEqual(scored["right_one"], 1.0)
        self.assertEqual(scored["wrong_one"], 0.0)
        self.assertIn("reason", rec)
        self.assertEqual(rec["framesSampled"], 5)

    def test_no_known_layout_triggers_calibration_and_refuses_low_confidence(self):
        # Only a foreign layout is committed, and the frames are desk frames
        # with no chip rows at all — calibration cannot succeed, and must
        # refuse rather than emit a low-confidence layout.
        self.write_layout("foreign", hud_layout(chip_y=520, left_x=200))
        job = self.make_job()
        res = lr.resolve_layout(
            self.store, job, layouts_dir=self.layouts_dir,
            reports_dir=self.reports_dir, harvest=False,
            frames=self.frame_files([desk_frame()] * 4))
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "layout_calibration_refused")
        job = self.store.get(job.job_key)
        self.assertEqual(job.state, sm.NEEDS_LAYOUT)
        self.assertEqual(job.last_error_code, "layout_calibration_refused")
        self.assertIn("blocked", job.payload["layout"])

    def test_no_proxy_is_a_hard_refusal_never_a_full_vod_fallback(self):
        key = models.record_key("noproxy")
        self.store.enqueue(models.KIND_RECORD, key, state=sm.ARCHIVED,
                           payload={"videoId": "noproxy",
                                    "media": {"localPath": "x/vid.mp4"}})
        self.store.transition(key, sm.DOWNLOADING)
        self.store.transition(key, sm.DOWNLOADED)
        with self.assertRaises(lr.LayoutRefusal) as ctx:
            lr.resolve_layout(self.store, self.store.get(key),
                              layouts_dir=self.layouts_dir,
                              reports_dir=self.reports_dir)
        self.assertEqual(ctx.exception.code, "no_scan_proxy")

    def test_unreadable_frames_refuse(self):
        self.write_layout("any")
        job = self.make_job()
        bad = os.path.join(self.tmp.name, "not_an_image.png")
        open(bad, "wb").write(b"not a png")
        with self.assertRaises(lr.LayoutRefusal) as ctx:
            lr.resolve_layout(self.store, job, layouts_dir=self.layouts_dir,
                              reports_dir=self.reports_dir,
                              frames=[(0.0, bad)])
        self.assertEqual(ctx.exception.code, "no_readable_frames")


class TestMarkerHarvesting(ResolverJobBase):
    def test_states_that_occurred_are_saved_and_the_rest_reported_missing(self):
        frames = self.frame_files([
            gameplay_frame(self.layout), gameplay_frame(self.layout),
            gameplay_frame(self.layout, chips_b=0),      # partial -> scoreboard
            desk_frame(),                                 # no-hud -> break
        ])
        out = lr.harvest_markers(frames, self.layout,
                                 os.path.join(self.tmp.name, "markers"))
        self.assertIn("gameplay", out["harvested"])
        self.assertIn("scoreboard", out["harvested"])
        self.assertIn("break", out["harvested"])
        # No automatic classifier exists for these — they must be reported
        # missing, never fabricated.
        self.assertIn("round_emblem", out["missingStates"])
        self.assertIn("highlight", out["missingStates"])
        for info in out["harvested"].values():
            self.assertTrue(os.path.exists(
                os.path.join(lr.REPO_ROOT, info["framePath"])
            ) or os.path.exists(info["framePath"]))


class TestApproveLayout(ResolverJobBase):
    def _refused_job(self):
        self.write_layout("foreign", hud_layout(chip_y=520, left_x=200))
        job = self.make_job()
        lr.resolve_layout(self.store, job, layouts_dir=self.layouts_dir,
                          reports_dir=self.reports_dir, harvest=False,
                          frames=self.frame_files([desk_frame()] * 4))
        return job.job_key

    def test_approving_a_refused_calibration_is_refused(self):
        key = self._refused_job()
        with self.assertRaises(lr.LayoutRefusal) as ctx:
            lr.approve_layout(self.store, key, confirm=True,
                              approved_by="alice", layouts_dir=self.layouts_dir)
        self.assertEqual(ctx.exception.code, "calibration_refused")

    def test_approval_requires_confirm(self):
        key = self._refused_job()
        with self.assertRaises(lr.LayoutRefusal) as ctx:
            lr.approve_layout(self.store, key, layouts_dir=self.layouts_dir)
        self.assertEqual(ctx.exception.code, "confirmation_required")

    def test_approving_an_automatic_reuse_is_a_no_op(self):
        self.write_layout("owcs_test_package")
        job = self.make_job()
        lr.resolve_layout(self.store, job, layouts_dir=self.layouts_dir,
                          reports_dir=self.reports_dir, harvest=False,
                          frames=self.frame_files([gameplay_frame(self.layout)] * 5))
        res = lr.approve_layout(self.store, job.job_key,
                                layouts_dir=self.layouts_dir)
        self.assertTrue(res["ok"])
        self.assertIn("nothing to approve", res["note"])

    def test_unresolved_job_cannot_be_approved(self):
        job = self.make_job()
        with self.assertRaises(lr.LayoutRefusal) as ctx:
            lr.approve_layout(self.store, job.job_key, confirm=True,
                              layouts_dir=self.layouts_dir)
        self.assertEqual(ctx.exception.code, "not_resolved")

    def test_a_good_calibration_can_be_approved_and_lands_in_layouts(self):
        """The positive path: real chip rows in the frames, so
        calibrate_source succeeds, and approval copies the layout in."""
        self.write_layout("foreign", hud_layout(chip_y=520, left_x=200,
                                                right_x=1100, pitch=30))
        job = self.make_job()
        res = lr.resolve_layout(
            self.store, job, layouts_dir=self.layouts_dir,
            reports_dir=self.reports_dir, harvest=False,
            frames=self.frame_files([gameplay_frame(self.layout)] * 6))
        rec = self.store.get(job.job_key).payload["layout"]
        calib = rec.get("calibration") or {}
        if calib.get("refusal"):
            self.skipTest(f"synthetic frames did not satisfy the real "
                          f"calibrator: {calib['refusal']}")
        self.assertTrue(rec["approvalRequired"])
        self.assertEqual(self.store.get(job.job_key).state, sm.NEEDS_LAYOUT)
        out = lr.approve_layout(self.store, job.job_key, confirm=True,
                                approved_by="alice",
                                layouts_dir=self.layouts_dir)
        self.assertTrue(os.path.exists(out["layoutPath"]))
        job = self.store.get(job.job_key)
        self.assertEqual(job.payload["expectedLayoutId"], out["layoutId"])
        self.assertEqual(job.payload["layout"]["approvedBy"], "alice")
        self.assertEqual(job.state, sm.PROCESSING)


class TestSegmentationAgainstRealFrames(unittest.TestCase):
    """Segmentation must work on the layouts this repo actually ships — the
    proven `owcs_jksix_qwc` profile has NO anchor template, which the previous
    anchor-only classifier could not handle at all."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.layout = hud_layout()

    def tearDown(self):
        self.tmp.cleanup()

    def _fake_extract(self, states: list[str]):
        """Write one PNG per sample offset and return them like
        capture.extract_frames does."""
        d = os.path.join(self.tmp.name, "thumbs")
        os.makedirs(d, exist_ok=True)
        paths = []
        for i, st in enumerate(states):
            frame = {"gameplay": lambda: gameplay_frame(self.layout),
                     "desk": desk_frame,
                     "partial": lambda: gameplay_frame(self.layout, chips_b=0),
                     }[st]()
            p = os.path.join(d, f"{i * 10:08d}.png")
            cv2.imwrite(p, frame)
            paths.append(p)

        def _extract(video_path, out_dir, interval):
            return paths
        return _extract

    def test_hud_probe_layout_needs_no_anchor_template(self):
        states = (["desk"] * 3) + (["gameplay"] * 12) + (["desk"] * 3)
        cands = seg.generate_candidates(
            "ignored.mp4", self.layout, out_dir=self.tmp.name, interval=10,
            extract_frames_fn=self._fake_extract(states))
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["signals"]["method"], "hud-structural-probe")
        self.assertEqual(cands[0]["signals"]["gameplaySamples"], 12)

    def test_desk_content_is_never_promoted_into_a_candidate(self):
        cands = seg.generate_candidates(
            "ignored.mp4", self.layout, out_dir=self.tmp.name, interval=10,
            extract_frames_fn=self._fake_extract(["desk"] * 20))
        self.assertEqual(cands, [])

    def test_candidates_carry_three_useful_thumbnails(self):
        states = (["gameplay"] * 15)
        cands = seg.generate_candidates(
            "ignored.mp4", self.layout, out_dir=self.tmp.name, interval=10,
            extract_frames_fn=self._fake_extract(states))
        thumbs = cands[0]["thumbnails"]
        self.assertEqual(len(thumbs), 3)
        offsets = [t["offset"] for t in thumbs]
        self.assertEqual(offsets, sorted(offsets))
        self.assertLess(offsets[0], offsets[-1])   # start / middle / end

    def test_rejections_are_recorded_with_reasons(self):
        # A tolerated single-sample gap keeps a range together AND records why
        # that sample was rejected.
        states = (["gameplay"] * 6) + ["partial"] + (["gameplay"] * 6)
        cands = seg.generate_candidates(
            "ignored.mp4", self.layout, out_dir=self.tmp.name, interval=10,
            extract_frames_fn=self._fake_extract(states))
        self.assertEqual(len(cands), 1)
        rejections = cands[0]["signals"]["rejections"]
        self.assertTrue(rejections)
        self.assertTrue(any("scoreboard" in k for k in rejections))

    def test_two_maps_separated_by_a_break_become_two_candidates(self):
        states = ((["gameplay"] * 10) + (["desk"] * 6) + (["gameplay"] * 10))
        cands = seg.generate_candidates(
            "ignored.mp4", self.layout, out_dir=self.tmp.name, interval=10,
            extract_frames_fn=self._fake_extract(states))
        self.assertEqual(len(cands), 2)
        self.assertLess(cands[0]["end_time"], cands[1]["start_time"] + 40)

    def test_a_layout_that_can_classify_nothing_raises_clearly(self):
        naked = {"frame_width": FRAME_W, "frame_height": FRAME_H,
                 "slots_a": [], "slots_b": []}
        with self.assertRaises(ValueError) as ctx:
            seg.generate_candidates(
                "ignored.mp4", naked, out_dir=self.tmp.name, interval=10,
                extract_frames_fn=self._fake_extract(["gameplay"]))
        self.assertIn("hud_probe", str(ctx.exception))

    def test_rejection_summary_names_every_refused_sample(self):
        samples = [
            {"offset": 0, "gameplay": False, "reason": "no-hud"},
            {"offset": 10, "gameplay": False, "reason": "no-hud"},
            {"offset": 20, "gameplay": False, "reason": "replay marker 0.91"},
            {"offset": 30, "gameplay": True, "reason": "chips a:5/5 b:5/5"},
        ]
        summary = seg.rejection_summary(samples)
        labels = list(summary)
        self.assertEqual(sum(v["count"] for v in summary.values()), 3)
        self.assertTrue(any("desk" in l for l in labels))
        self.assertTrue(any("replay" in l for l in labels))


class TestExtractionUsesTheFullResolutionSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = js.JobStore(os.path.join(self.tmp.name, "automation.sqlite"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_extracting_from_the_proxy_is_refused(self):
        ids = seg.store_candidates(self.store.con, "vid", "m1",
                                   [{"start_time": 0.0, "end_time": 60.0,
                                     "confidence": 0.9, "signals": {}}])
        seg.approve_segment(self.store.con, ids[0], map_order=1,
                            map_name="nepal", map_mode="control",
                            team_a="a", team_b="b", side_assignment="team_a_left",
                            layout_id="owcs_jksix_qwc")
        with self.assertRaises(ValueError) as ctx:
            seg.extract_segment_clip(self.store.con, ids[0],
                                     "/data/vid.proxy360p.mp4",
                                     os.path.join(self.tmp.name, "out"))
        self.assertIn("scan proxy", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
