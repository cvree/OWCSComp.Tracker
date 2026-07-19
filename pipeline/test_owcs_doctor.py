#!/usr/bin/env python3
"""
Offline tests for pipeline/owcs_doctor.py

No real VOD, no network, no DB writes, no labelling: every test builds a
synthetic temp repo tree with tempfile.TemporaryDirectory and asserts the
doctor's checks + next-step recommendation.

Run:
    python -m pytest pipeline/test_owcs_doctor.py -q
    python -m unittest pipeline.test_owcs_doctor
"""

import json
import os
import tempfile
import unittest

import owcs_doctor as doc  # imported as a sibling module (run from pipeline/ or with pipeline on path)

RUN = "owcs-8c105lnzlam_000600_000630"
LAYOUT_REL = "layouts/owcs_8c105lnzlam.json"

# Cumulative scaffold stages, in pipeline order. Building up to (and including)
# index i means every rung <= i is satisfied and rung i+1 is the next step.
STAGES = ["folders", "db", "layout", "run", "frames", "calibration",
          "crops", "labels", "candidates", "cand_report", "cand_dryrun"]

# What the recommender should say is next, when the tree is built up to STAGES[i].
EXPECTED_NEXT = {
    "folders":     "db",
    "db":          "layout",
    "layout":      "run",
    "run":         "frames_raw",
    "frames":      "calibration",
    "calibration": "hero_crops",
    "crops":       "labels",
    "labels":      "candidates",
    "candidates":  "cand_report",
    "cand_report": "cand_dryrun",
    "cand_dryrun": "ready",
}


def _touch(path, content=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def scaffold(root, upto_stage, label_count=doc.MIN_LABELS):
    """Create the tree cumulatively up to and including `upto_stage`."""
    idx = STAGES.index(upto_stage)
    stages = STAGES[: idx + 1]

    if "folders" in stages:
        for d in ("pipeline", "layouts", "reports", "templates", "data/eval"):
            os.makedirs(os.path.join(root, d), exist_ok=True)
    if "db" in stages:
        _touch(os.path.join(root, "data/owcs.sqlite"), "")
    if "layout" in stages:
        _touch(os.path.join(root, LAYOUT_REL), json.dumps({"slots": list(range(10))}))
    if "run" in stages:
        os.makedirs(os.path.join(root, f"reports/auto/{RUN}"), exist_ok=True)
        _touch(os.path.join(root, f"reports/auto/{RUN}/index.html"), "<html></html>")
    if "frames" in stages:
        _touch(os.path.join(root, f"reports/auto/{RUN}/frames_raw/000600.png"), "x")
    if "calibration" in stages:
        _touch(os.path.join(root, f"reports/auto/{RUN}/layout.html"), "<html></html>")
    if "crops" in stages:
        _touch(os.path.join(root, f"reports/auto/{RUN}/hero_crops.html"), "<html></html>")
    if "labels" in stages:
        _touch(os.path.join(root, "data/eval/labels.json"),
               json.dumps([{"frame": i} for i in range(label_count)]))
    if "candidates" in stages:
        _touch(os.path.join(root, "templates/candidates/tracer.png"), "x")
    if "cand_report" in stages:
        _touch(os.path.join(root, f"reports/candidates/{RUN}/index.html"), "<html></html>")
    if "cand_dryrun" in stages:
        _touch(os.path.join(root, f"reports/candidates/{RUN}/dry_run.json"), "{}")


def next_step_id(root):
    paths = doc.resolve_paths(root, RUN, LAYOUT_REL)
    results = doc.run_checks(paths)
    return doc.recommend_next(results, paths).step_id


class TestLadder(unittest.TestCase):
    def test_full_ladder_recommendations(self):
        """Each cumulative stage recommends the correct next rung."""
        for stage, expected in EXPECTED_NEXT.items():
            with tempfile.TemporaryDirectory() as root:
                scaffold(root, stage)
                self.assertEqual(next_step_id(root), expected,
                                 f"after building '{stage}' expected next '{expected}'")


class TestRequiredCases(unittest.TestCase):
    def test_missing_db(self):
        with tempfile.TemporaryDirectory() as root:
            scaffold(root, "folders")  # folders but no DB
            paths = doc.resolve_paths(root, RUN, LAYOUT_REL)
            checks = {c.id: c for c in doc.run_checks(paths)}
            self.assertFalse(checks["db"].ok)
            rec = doc.recommend_next(list(checks.values()), paths)
            self.assertEqual(rec.step_id, "db")
            self.assertEqual(rec.human, "initialize DB")

    def test_missing_frames(self):
        with tempfile.TemporaryDirectory() as root:
            scaffold(root, "run")  # run folder exists, no frames
            paths = doc.resolve_paths(root, RUN, LAYOUT_REL)
            checks = {c.id: c for c in doc.run_checks(paths)}
            self.assertFalse(checks["frames_raw"].ok)
            self.assertEqual(next_step_id(root), "frames_raw")

    def test_missing_labels(self):
        with tempfile.TemporaryDirectory() as root:
            scaffold(root, "crops")  # crops exist, no labels.json
            paths = doc.resolve_paths(root, RUN, LAYOUT_REL)
            checks = {c.id: c for c in doc.run_checks(paths)}
            self.assertFalse(checks["labels"].ok)
            rec = doc.recommend_next(list(checks.values()), paths)
            self.assertEqual(rec.step_id, "labels")
            self.assertEqual(rec.human, "open hero_crops.html and label crops")

    def test_labels_exist_enough(self):
        with tempfile.TemporaryDirectory() as root:
            scaffold(root, "labels")  # >= MIN_LABELS
            paths = doc.resolve_paths(root, RUN, LAYOUT_REL)
            checks = {c.id: c for c in doc.run_checks(paths)}
            self.assertTrue(checks["labels"].ok)
            # next unmet rung is candidate templates
            self.assertEqual(next_step_id(root), "candidates")

    def test_labels_too_few_says_label_more(self):
        with tempfile.TemporaryDirectory() as root:
            scaffold(root, "labels", label_count=3)  # below MIN_LABELS
            paths = doc.resolve_paths(root, RUN, LAYOUT_REL)
            checks = {c.id: c for c in doc.run_checks(paths)}
            self.assertFalse(checks["labels"].ok)
            rec = doc.recommend_next(list(checks.values()), paths)
            self.assertEqual(rec.step_id, "labels")
            self.assertEqual(rec.human, "label more crops")

    def test_candidate_reports_exist(self):
        with tempfile.TemporaryDirectory() as root:
            scaffold(root, "cand_report")
            paths = doc.resolve_paths(root, RUN, LAYOUT_REL)
            checks = {c.id: c for c in doc.run_checks(paths)}
            self.assertTrue(checks["cand_report"].ok)
            # only the dry-run remains
            self.assertEqual(next_step_id(root), "cand_dryrun")

    def test_all_present_is_ready(self):
        with tempfile.TemporaryDirectory() as root:
            scaffold(root, "cand_dryrun")
            paths = doc.resolve_paths(root, RUN, LAYOUT_REL)
            report = doc.build_report(paths)
            self.assertTrue(report["ready"])
            self.assertEqual(report["next_step"]["step_id"], "ready")
            self.assertTrue(all(c["ok"] for c in report["checks"]))


class TestRunIdAndCommands(unittest.TestCase):
    def test_parse_run_id(self):
        source, start, end = doc.parse_run_id(RUN)
        self.assertEqual(source, "owcs-8c105lnzlam")
        self.assertEqual(start, "0:06:00")
        self.assertEqual(end, "0:06:30")

    def test_parse_run_id_unparseable(self):
        source, start, end = doc.parse_run_id("weird-run-name")
        self.assertEqual(source, "weird-run-name")
        self.assertIsNone(start)
        self.assertIsNone(end)

    def test_capture_command_uses_parsed_run_id(self):
        with tempfile.TemporaryDirectory() as root:
            scaffold(root, "layout")  # next step = run auto capture
            paths = doc.resolve_paths(root, RUN, LAYOUT_REL)
            rec = doc.recommend_next(doc.run_checks(paths), paths)
            self.assertEqual(rec.step_id, "run")
            self.assertIn("owcs-8c105lnzlam", rec.command)
            self.assertIn("0:06:00", rec.command)
            self.assertIn("0:06:30", rec.command)


class TestReadOnlyAndBanner(unittest.TestCase):
    def test_checks_do_not_write(self):
        with tempfile.TemporaryDirectory() as root:
            scaffold(root, "crops")
            before = _snapshot(root)
            paths = doc.resolve_paths(root, RUN, LAYOUT_REL)
            doc.build_report(paths)  # run all checks
            self.assertEqual(before, _snapshot(root), "doctor checks must not write anything")

    def test_emit_banner_creates_new_file_only(self):
        with tempfile.TemporaryDirectory() as root:
            scaffold(root, "crops")
            paths = doc.resolve_paths(root, RUN, LAYOUT_REL)
            rec = doc.recommend_next(doc.run_checks(paths), paths)
            out = doc.emit_banner(paths, rec)
            self.assertTrue(out and os.path.isfile(out))
            self.assertTrue(out.endswith("next_step.html"))
            with open(out, encoding="utf-8") as fh:
                self.assertIn("Doctor says next", fh.read())

    def test_main_exit_codes(self):
        import contextlib, io
        with tempfile.TemporaryDirectory() as root:
            scaffold(root, "db")  # not ready
            with contextlib.redirect_stdout(io.StringIO()):
                rc = doc.main(["--run", RUN, "--layout", LAYOUT_REL, "--root", root, "--json"])
            self.assertEqual(rc, 1)
        with tempfile.TemporaryDirectory() as root:
            scaffold(root, "cand_dryrun")  # ready
            with contextlib.redirect_stdout(io.StringIO()):
                rc = doc.main(["--run", RUN, "--layout", LAYOUT_REL, "--root", root, "--json"])
            self.assertEqual(rc, 0)


def _snapshot(root):
    out = []
    for dp, _, fns in os.walk(root):
        for fn in fns:
            out.append(os.path.relpath(os.path.join(dp, fn), root))
    return sorted(out)


if __name__ == "__main__":
    unittest.main()
