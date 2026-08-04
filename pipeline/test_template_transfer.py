#!/usr/bin/env python3
"""
test_template_transfer.py — the cross-package transfer measurement.

The measurement decides how expensive every new broadcast is, so the thing
that must not rot is its HONESTY, not its numbers. Three properties are
pinned here:

  * a set scored against its own package is flagged, because that is a
    measurement of nothing dressed as a perfect score;
  * recall and ranking are kept apart, because a set that ranks the right
    hero first while refusing to name it is in a completely different
    situation from one that has no idea, and collapsing them into "accuracy"
    hides which;
  * the safety half still runs on foreign footage — a shared library that
    names heroes it has never seen is the failure this project exists to
    prevent, and it must be measured on the other broadcast too.

Run: python3 pipeline/test_template_transfer.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import template_evidence as te  # noqa: E402
import template_transfer as tt  # noqa: E402


def portrait(seed: int, size: int = 40):
    """A distinct, busy, deterministic 'hero portrait'."""
    rng = np.random.default_rng(seed)
    img = rng.integers(40, 220, (size, size), dtype=np.uint8)
    cv2.circle(img, (size // 2, size // 2), size // 3, int(seed % 255), -1)
    return img


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="xfer_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _templates(self, heroes: dict[str, int]) -> str:
        d = os.path.join(self.tmp, "tpl")
        os.makedirs(d, exist_ok=True)
        for hero, seed in heroes.items():
            cv2.imwrite(os.path.join(d, f"{hero}.png"), portrait(seed))
        return d

    def _manifest(self, crops: list[tuple[str, int]], *,
                  layout_id: str = "other_package",
                  jitter: bool = False) -> dict:
        d = os.path.join(self.tmp, "crops")
        os.makedirs(d, exist_ok=True)
        records = []
        rng = np.random.default_rng(1234)
        for i, (hero, seed) in enumerate(crops):
            fn = f"t{i:09.1f}_a1.png"
            img = portrait(seed)
            if jitter:
                # Enough to drop correlation below 1.0, nowhere near enough
                # to change which hero ranks first.
                noise = rng.normal(0, 12, img.shape)
                img = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)
            cv2.imwrite(os.path.join(d, fn), img)
            records.append({"file": fn, "hero": hero, "slot": "a1",
                            "t": float(i * 10), "state": "gameplay",
                            "side": "a", "labelSource": te.LABEL_CONSENSUS})
        return {"evidenceSetVersion": te.MANIFEST_VERSION,
                "sourceReport": "reports/ingest/other", "cropsDir": d,
                "layoutId": layout_id, "crops": records, "excluded": [],
                "counts": {"labeled": len(records)}, "stints": [],
                "labelPolicy": {"method": "test"}}


class TestMeasuringNothing(Base):
    def test_a_set_scored_against_its_own_package_is_flagged(self):
        """Otherwise the easiest possible mistake produces the most
        reassuring possible number."""
        tpl = self._templates({"lucio": 3})
        man = self._manifest([("lucio", 3)] * 3, layout_id="tpl")
        report = tt.measure(tpl, man, repo_root=ROOT, limit_per_hero=None)
        self.assertTrue(report["samePackage"])

    def test_different_packages_are_not_flagged(self):
        tpl = self._templates({"lucio": 3})
        man = self._manifest([("lucio", 3)] * 3, layout_id="somewhere_else")
        self.assertFalse(tt.measure(tpl, man, repo_root=ROOT)["samePackage"])

    def test_no_overlap_is_reported_as_unmeasurable_not_as_failure(self):
        """A set with no hero in common with the other broadcast has not
        failed to transfer — nothing was asked of it."""
        tpl = self._templates({"lucio": 3})
        man = self._manifest([("tracer", 88)] * 4)
        report = tt.measure(tpl, man, repo_root=ROOT)
        self.assertEqual(report["overlapHeroes"], [])
        self.assertIsNone(report["verdict"]["transfers"])
        self.assertIn("cannot be measured", report["verdict"]["summary"])


class TestRecallAndRankingAreSeparate(Base):
    def test_identical_art_transfers(self):
        tpl = self._templates({"lucio": 3, "kiriko": 9})
        man = self._manifest([("lucio", 3)] * 6)
        report = tt.measure(tpl, man, repo_root=ROOT)
        self.assertEqual(report["totals"]["recallRate"], 1.0)
        self.assertTrue(report["verdict"]["transfers"])

    def test_a_set_that_ranks_right_but_scores_low_is_not_called_useless(self):
        """The real finding this module was written for: 82.5% of foreign
        Lúcio crops ranked Lúcio first while 0% cleared the floor. Reporting
        that as "does not transfer" would throw away the one result that
        says a foreign set can bootstrap a new package's harvest.
        """
        v = tt.verdict(trials=200, recalled=0, ranked_first=165,
                       false_match_rate=0.0)
        self.assertFalse(v["transfers"])
        self.assertTrue(v["partialSignal"])
        self.assertIn("above chance", v["summary"])

    def test_genuine_noise_is_still_called_no_signal(self):
        v = tt.verdict(trials=200, recalled=0, ranked_first=20,
                       false_match_rate=0.0)
        self.assertFalse(v["partialSignal"])
        self.assertIn("no usable signal", v["summary"])

    def test_ranking_ignores_the_floor(self):
        """Ranking must be measured without the floor, or it can never say
        anything the recall number did not already say.

        The probes are jittered rather than pixel-identical: identical art
        correlates at exactly 1.0, which clears every legal floor, so an
        identical-art fixture cannot express "ranked right, refused anyway"
        — the very situation being pinned.
        """
        tpl = self._templates({"lucio": 3, "kiriko": 9})
        man = self._manifest([("lucio", 3)] * 6, jitter=True)
        layout = {"unknown_floor": 0.99}
        report = tt.measure(tpl, man, layout=layout, repo_root=ROOT)
        self.assertEqual(report["totals"]["recallRate"], 0.0,
                         "the floor was not applied to the recall side")
        self.assertEqual(report["totals"]["rankingRate"], 1.0,
                         "ranking was silently gated by the floor")


class TestSafetyIsMeasuredOnForeignFootage(Base):
    def test_uncovered_heroes_become_false_match_trials(self):
        tpl = self._templates({"lucio": 3})
        man = self._manifest([("lucio", 3)] * 3 + [("tracer", 88)] * 5)
        report = tt.measure(tpl, man, repo_root=ROOT)
        self.assertEqual(report["falseMatch"]["trials"], 5,
                         "crops of heroes the set cannot know must be tested, "
                         "not skipped")

    def test_naming_a_hero_it_has_no_template_for_is_recorded(self):
        tpl = self._templates({"lucio": 3})
        # A floor of 0 accepts anything, which is exactly the dangerous
        # configuration this half of the test exists to expose.
        man = self._manifest([("tracer", 88)] * 4)
        report = tt.measure(tpl, man, layout={"unknown_floor": 0.0,
                                              "min_margin": 0.0},
                            repo_root=ROOT)
        self.assertEqual(report["falseMatch"]["matched"], 4)
        self.assertFalse(report["verdict"]["safe"])


class TestPerSidePortraitRegions(Base):
    """The confound that made the first run of this report 0% ranking.

    One `portrait_roi` applied to both sides is correct within a package and
    meaningless across two, because the packages do not frame their slots
    the same way. If this ever silently reverts to a single ROI, the
    measurement quietly returns "no signal" for every pair.
    """

    def test_the_probe_roi_can_differ_from_the_template_roi(self):
        tpl = self._templates({"lucio": 3})
        man = self._manifest([("lucio", 3)] * 3)
        report = tt.measure(tpl, man, repo_root=ROOT,
                            layout={"portrait_roi": [0.0, 0.0, 1.0, 0.7]},
                            probe_layout={"portrait_roi": [0.0, 0.25, 1.0, 1.0]})
        self.assertEqual(report["detector"]["templateRoi"], [0.0, 0.0, 1.0, 0.7])
        self.assertEqual(report["detector"]["probeRoi"], [0.0, 0.25, 1.0, 1.0])

    def test_omitting_the_probe_layout_uses_one_roi_for_both(self):
        """The honest default: it is what today's detector actually does."""
        tpl = self._templates({"lucio": 3})
        man = self._manifest([("lucio", 3)] * 3)
        report = tt.measure(tpl, man, repo_root=ROOT,
                            layout={"portrait_roi": [0.0, 0.0, 1.0, 0.7]})
        self.assertEqual(report["detector"]["probeRoi"],
                         report["detector"]["templateRoi"])


class TestTheCommittedResult(unittest.TestCase):
    """The real pair, if its report has been generated. Not a fixture — the
    point is that the repository's own answer stays reproducible."""

    PATH = os.path.join(ROOT, "reports", "validation",
                        "transfer_jksix_to_8c105lnzlam.json")

    def test_the_report_keeps_recall_and_ranking_apart(self):
        if not os.path.exists(self.PATH):
            self.skipTest("transfer report not generated in this checkout")
        with open(self.PATH, "r", encoding="utf-8") as f:
            report = json.load(f)
        totals = report["totals"]
        self.assertIn("recallRate", totals)
        self.assertIn("rankingRate", totals)
        self.assertNotEqual(
            report["detector"]["templateRoi"], report["detector"]["probeRoi"],
            "the committed run compared two packages that frame their slots "
            "differently — one ROI for both would make it meaningless")

    def test_the_safety_half_actually_ran(self):
        if not os.path.exists(self.PATH):
            self.skipTest("transfer report not generated in this checkout")
        with open(self.PATH, "r", encoding="utf-8") as f:
            report = json.load(f)
        self.assertGreater(report["falseMatch"]["trials"], 0)
        self.assertLessEqual(report["falseMatch"]["rate"],
                             report["falseMatch"]["ceiling"],
                             "a set that names heroes it has never seen must "
                             "never be presented as a transfer candidate")


if __name__ == "__main__":
    unittest.main(verbosity=2)
