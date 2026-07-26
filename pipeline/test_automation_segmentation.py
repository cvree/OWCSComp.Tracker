#!/usr/bin/env python3
"""
test_automation_segmentation.py — Phase F assisted map segmentation:
candidate grouping, pre/post-roll, review actions (approve/reject/split/
merge/adjust), and clip extraction (fake ffmpeg — no real video needed for
the unit-level tests; one end-to-end test drives real ffmpeg against a tiny
synthetic clip since ffmpeg is available in this environment).
Run: python3 pipeline/test_automation_segmentation.py
"""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from automation import job_store as js  # noqa: E402
from automation import segmentation as seg  # noqa: E402


def _sample(offset, gameplay, score=0.9, scene_change=False):
    return {"offset": offset, "gameplay": gameplay, "reason": "gameplay" if gameplay else "no-hud",
            "score": score, "sceneChange": scene_change}


class TestGrouping(unittest.TestCase):
    def test_single_contiguous_range(self):
        samples = [_sample(o, True) for o in range(0, 100, 10)]
        cands = seg._group_candidates(samples, interval=10, pre_roll=5,
                                      post_roll=5, gap_tolerance=1,
                                      min_candidate_seconds=10)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["start_time"], 0.0)  # clamped at 0 (pre-roll)
        self.assertEqual(cands[0]["end_time"], 105.0)  # last sample (90) + interval(10) + post-roll(5)

    def test_gap_splits_into_two_ranges(self):
        samples = ([_sample(o, True) for o in range(0, 50, 10)]
                  + [_sample(o, False) for o in range(50, 150, 10)]
                  + [_sample(o, True) for o in range(150, 250, 10)])
        cands = seg._group_candidates(samples, interval=10, pre_roll=0,
                                      post_roll=0, gap_tolerance=1,
                                      min_candidate_seconds=10)
        self.assertEqual(len(cands), 2)
        self.assertLess(cands[0]["end_time"], cands[1]["start_time"])

    def test_short_flicker_gap_tolerated(self):
        samples = ([_sample(o, True) for o in range(0, 50, 10)]
                  + [_sample(50, False)]                       # single missed sample
                  + [_sample(o, True) for o in range(60, 120, 10)])
        cands = seg._group_candidates(samples, interval=10, pre_roll=0,
                                      post_roll=0, gap_tolerance=1,
                                      min_candidate_seconds=10)
        self.assertEqual(len(cands), 1, "one flickered sample should not fragment the range")

    def test_too_short_candidate_dropped(self):
        samples = [_sample(0, True)]
        cands = seg._group_candidates(samples, interval=10, pre_roll=0,
                                      post_roll=0, gap_tolerance=0,
                                      min_candidate_seconds=30)
        self.assertEqual(cands, [])

    def test_no_gameplay_samples_yields_no_candidates(self):
        samples = [_sample(o, False) for o in range(0, 100, 10)]
        cands = seg._group_candidates(samples, interval=10, pre_roll=5,
                                      post_roll=5, gap_tolerance=1,
                                      min_candidate_seconds=10)
        self.assertEqual(cands, [])

    def test_confidence_is_mean_gameplay_score(self):
        samples = [_sample(0, True, score=0.6), _sample(10, True, score=1.0)]
        cands = seg._group_candidates(samples, interval=10, pre_roll=0,
                                      post_roll=0, gap_tolerance=0,
                                      min_candidate_seconds=10)
        self.assertAlmostEqual(cands[0]["confidence"], 0.8)

    def test_pre_roll_clamped_at_zero(self):
        samples = [_sample(5, True), _sample(15, True)]
        cands = seg._group_candidates(samples, interval=10, pre_roll=100,
                                      post_roll=0, gap_tolerance=0,
                                      min_candidate_seconds=10)
        self.assertEqual(cands[0]["start_time"], 0.0)


class SegmentDbTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = js.JobStore(os.path.join(self.tmp.name, "automation.sqlite"))
        self.con = self.store.con

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def seed(self, n=2):
        cands = [{"start_time": float(i * 100), "end_time": float(i * 100 + 60),
                  "confidence": 0.8, "signals": {"n": i}} for i in range(n)]
        return seg.store_candidates(self.con, "vid1", "m-test", cands,
                                    source_job_key="process:vid1:v1")


class TestStorageAndCrud(SegmentDbTestBase):
    def test_store_and_list(self):
        ids = self.seed(2)
        rows = seg.list_segments(self.con, video_id="vid1")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["review_status"], "pending")
        self.assertEqual([r["id"] for r in rows], ids)

    def test_get_missing_returns_none(self):
        self.assertIsNone(seg.get_segment(self.con, 999))

    def test_adjust_boundaries(self):
        [sid] = self.seed(1)
        updated = seg.adjust_boundaries(self.con, sid, start_time=10, end_time=90)
        self.assertEqual(updated["start_time"], 10)
        self.assertEqual(updated["end_time"], 90)
        with self.assertRaises(ValueError):
            seg.adjust_boundaries(self.con, sid, start_time=90, end_time=10)

    def test_approve_requires_full_metadata(self):
        [sid] = self.seed(1)
        approved = seg.approve_segment(
            self.con, sid, map_order=1, map_name="Nepal", map_mode="Control",
            team_a="qad", team_b="twis", side_assignment="team_a_left",
            layout_id="owcs_jksix_qwc", reviewer_note="clean window")
        self.assertEqual(approved["review_status"], "approved")
        self.assertEqual(approved["map_name"], "Nepal")
        self.assertEqual(approved["team_a"], "qad")

    def test_reject_and_mark_invalid(self):
        [a, b] = self.seed(2)
        r = seg.reject_segment(self.con, a, reason="not a match window")
        self.assertEqual(r["review_status"], "rejected")
        inv = seg.mark_invalid(self.con, b, reason="spans a replay")
        self.assertEqual(inv["review_status"], "invalid")

    def test_split_segment_preserves_original(self):
        [sid] = self.seed(1)
        seg_row = seg.get_segment(self.con, sid)
        mid = (seg_row["start_time"] + seg_row["end_time"]) / 2
        first, second = seg.split_segment(self.con, sid, split_time=mid)
        self.assertEqual(seg.get_segment(self.con, sid)["review_status"], "split")
        self.assertEqual(first["end_time"], mid)
        self.assertEqual(second["start_time"], mid)
        self.assertEqual(first["review_status"], "pending")

    def test_split_requires_time_inside_segment(self):
        [sid] = self.seed(1)
        row = seg.get_segment(self.con, sid)
        with self.assertRaises(ValueError):
            seg.split_segment(self.con, sid, split_time=row["end_time"] + 5)

    def test_merge_segments_spans_union(self):
        [a, b] = self.seed(2)
        merged = seg.merge_segments(self.con, a, b)
        self.assertEqual(seg.get_segment(self.con, a)["review_status"], "merged")
        self.assertEqual(seg.get_segment(self.con, b)["review_status"], "merged")
        row_a, row_b = seg.get_segment(self.con, a), seg.get_segment(self.con, b)
        self.assertEqual(merged["start_time"], min(row_a["start_time"], row_b["start_time"]))

    def test_merge_rejects_different_videos(self):
        [a] = self.seed(1)
        [b] = seg.store_candidates(self.con, "vid-other", "m-test",
                                   [{"start_time": 0.0, "end_time": 60.0,
                                     "confidence": 0.5, "signals": {}}])
        with self.assertRaises(ValueError):
            seg.merge_segments(self.con, a, b)

    def test_unknown_segment_raises(self):
        with self.assertRaises(seg.SegmentNotFound):
            seg.get_segment(self.con, 12345) or seg._require(self.con, 12345)


class TestExtraction(SegmentDbTestBase):
    def test_extraction_refuses_unapproved_segment(self):
        [sid] = self.seed(1)
        with self.assertRaises(ValueError):
            seg.extract_segment_clip(self.con, sid, "irrelevant.mp4", self.tmp.name)

    def test_extraction_with_fake_ffmpeg(self):
        [sid] = self.seed(1)
        seg.approve_segment(self.con, sid, map_order=1, map_name="Nepal",
                            map_mode="Control", team_a="qad", team_b="twis",
                            side_assignment="team_a_left", layout_id="l1")

        def fake_cut(src, start, end, out, runner=None):
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as f:
                f.write(b"\x00" * 8192)

        def fake_validate(path):
            return True, "ok (fake)"

        def fake_resolution(path):
            return {"width": 1280, "height": 720, "codec": "h264", "duration": 60.0}

        result = seg.extract_segment_clip(
            self.con, sid, "source.mp4", self.tmp.name,
            cut_fn=fake_cut, validate_fn=fake_validate,
            resolution_fn=fake_resolution)
        self.assertIsNotNone(result["extracted_hash"])
        self.assertEqual(result["extracted_width"], 1280)
        self.assertEqual(len(result["extracted_hash"]), 64)

    def test_extraction_failure_on_invalid_clip_raises(self):
        import video_ingest as vi
        [sid] = self.seed(1)
        seg.approve_segment(self.con, sid, map_order=1, map_name="Nepal",
                            map_mode="Control", team_a="qad", team_b="twis",
                            side_assignment="team_a_left", layout_id="l1")

        def fake_cut(src, start, end, out, runner=None):
            os.makedirs(os.path.dirname(out), exist_ok=True)
            open(out, "wb").close()  # empty/corrupt

        def fake_validate(path):
            return False, "too small"

        with self.assertRaises(vi.InvalidClip):
            seg.extract_segment_clip(self.con, sid, "source.mp4", self.tmp.name,
                                     cut_fn=fake_cut, validate_fn=fake_validate)


class TestRealFfmpegExtraction(unittest.TestCase):
    """One end-to-end check with the REAL ffmpeg binary against a tiny
    synthetic clip, proving extract_segment_clip's default cut_fn actually
    works (not just its injected fakes)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = js.JobStore(os.path.join(self.tmp.name, "automation.sqlite"))
        self.con = self.store.con

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_real_ffmpeg_cut(self):
        if not shutil_which("ffmpeg"):
            self.skipTest("ffmpeg not installed in this environment")
        src = os.path.join(self.tmp.name, "src.mp4")
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=5:duration=10",
            "-pix_fmt", "yuv420p", src,
        ], check=True)
        [sid] = seg.store_candidates(self.con, "vid1", "m-test",
                                     [{"start_time": 0.0, "end_time": 5.0,
                                       "confidence": 0.9, "signals": {}}])
        seg.approve_segment(self.con, sid, map_order=1, map_name="Test",
                            map_mode="Control", team_a="a", team_b="b",
                            side_assignment="a_left", layout_id="l1")
        result = seg.extract_segment_clip(self.con, sid, src, self.tmp.name)
        self.assertTrue(os.path.exists(result["extracted_path"] and
                                       os.path.join(os.path.dirname(HERE),
                                                    result["extracted_path"])))
        self.assertEqual(len(result["extracted_hash"]), 64)


def shutil_which(tool):
    import shutil
    return shutil.which(tool)


if __name__ == "__main__":
    unittest.main(verbosity=2)
