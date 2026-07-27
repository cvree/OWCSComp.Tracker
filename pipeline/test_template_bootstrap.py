#!/usr/bin/env python3
"""
test_template_bootstrap.py — Phase 5 hero-template coverage + bootstrap.

Offline: temporary template directories, a temporary content DB, and
generated crops. No network.

Covered behaviors:
  * coverage measured against the FULL roster, not against what exists
  * uncovered heroes named, so the detection ceiling is visible
  * a template filename matching no hero id is reported (a phantom hero)
  * variant discovery matches detect.load_templates' filename grammar
  * an existing template set is REUSED, never re-harvested
  * official hero art is used only to LABEL, never as a template
  * low-margin label suggestions are surfaced for review
  * provenance is recorded and merged across harvests
  * packaging validation fails a detection-capable layout with no templates_dir
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import check_packaging  # noqa: E402
import db as content_db  # noqa: E402
import template_bootstrap as tb  # noqa: E402

# Roles use the content schema's own CHECK vocabulary ('Tank'/'Damage'/
# 'Support') so these rows insert into a real heroes table unchanged.
ROSTER = [
    {"id": "tracer", "name": "Tracer", "role": "Damage"},
    {"id": "lucio", "name": "Lúcio", "role": "Support"},
    {"id": "rein", "name": "Reinhardt", "role": "Tank"},
    {"id": "ana", "name": "Ana", "role": "Support"},
]


def write_png(path: str, *, seed: int = 0, size: int = 48) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    rng = np.random.default_rng(seed)
    cv2.imwrite(path, rng.integers(0, 256, (size, size, 3), dtype=np.uint8))
    return path


class TestScanTemplateDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = os.path.join(self.tmp.name, "templates")
        os.makedirs(self.d)

    def tearDown(self):
        self.tmp.cleanup()

    def test_plain_and_variant_filenames_group_by_hero(self):
        for name in ("tracer.png", "tracer.a.png", "tracer.dead.png",
                     "lucio.png"):
            write_png(os.path.join(self.d, name))
        found = tb.scan_template_dir(self.d)
        self.assertEqual(sorted(found), ["lucio", "tracer"])
        self.assertEqual(len(found["tracer"]), 3)
        variants = sorted(v["variant"] for v in found["tracer"])
        self.assertEqual(variants, ["", "a", "dead"])

    def test_underscore_prefixed_and_non_png_files_are_ignored(self):
        write_png(os.path.join(self.d, "_candidates_montage.png"))
        write_png(os.path.join(self.d, "tracer.png"))
        with open(os.path.join(self.d, "notes.txt"), "w") as f:
            f.write("x")
        self.assertEqual(sorted(tb.scan_template_dir(self.d)), ["tracer"])

    def test_missing_directory_is_empty_not_an_error(self):
        self.assertEqual(tb.scan_template_dir(os.path.join(self.d, "nope")), {})

    def test_variant_grammar_matches_the_real_detector(self):
        """The coverage report must never claim a file detection won't load."""
        import detect
        for name in ("tracer.png", "tracer.a.png", "lucio.ult.png"):
            write_png(os.path.join(self.d, name))
        lib = detect.load_templates(self.d)
        scanned = tb.scan_template_dir(self.d)
        self.assertEqual(sorted(lib), sorted(scanned))
        for hero in lib:
            self.assertEqual(len(lib[hero]), len(scanned[hero]), hero)


class TestCoverageReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = os.path.join(self.tmp.name, "templates")
        os.makedirs(self.d)

    def tearDown(self):
        self.tmp.cleanup()

    def test_coverage_is_measured_against_the_full_roster(self):
        write_png(os.path.join(self.d, "tracer.png"))
        write_png(os.path.join(self.d, "lucio.png"))
        status = tb.template_set_status(self.d, ROSTER)
        self.assertEqual(status["rosterSize"], 4)
        self.assertEqual(status["coveredCount"], 2)
        self.assertEqual(status["coveragePct"], 50.0)
        self.assertEqual(status["uncovered"], ["ana", "rein"])
        self.assertIn("Ana", status["uncoveredNames"])
        self.assertIn("UNKNOWN", status["note"])

    def test_an_empty_package_is_zero_percent_not_fine(self):
        status = tb.template_set_status(self.d, ROSTER)
        self.assertEqual(status["coveredCount"], 0)
        self.assertEqual(status["coveragePct"], 0.0)
        self.assertEqual(len(status["uncovered"]), 4)

    def test_a_missing_directory_is_reported_as_missing(self):
        status = tb.template_set_status(os.path.join(self.d, "gone"), ROSTER)
        self.assertFalse(status["exists"])
        self.assertEqual(status["coveredCount"], 0)

    def test_full_coverage_says_so(self):
        for h in ROSTER:
            write_png(os.path.join(self.d, f"{h['id']}.png"))
        status = tb.template_set_status(self.d, ROSTER)
        self.assertEqual(status["coveragePct"], 100.0)
        self.assertEqual(status["uncovered"], [])
        self.assertIn("every roster hero", status["note"])

    def test_a_filename_matching_no_hero_is_reported(self):
        write_png(os.path.join(self.d, "tracer.png"))
        write_png(os.path.join(self.d, "traccer.png"))     # a typo
        status = tb.template_set_status(self.d, ROSTER)
        self.assertEqual(status["unknownTemplateFiles"], ["traccer"])
        self.assertIn("UNKNOWN FILES", tb.format_status(status))

    def test_single_variant_heroes_are_flagged(self):
        write_png(os.path.join(self.d, "tracer.png"))
        write_png(os.path.join(self.d, "lucio.png"))
        write_png(os.path.join(self.d, "lucio.dead.png"))
        status = tb.template_set_status(self.d, ROSTER)
        self.assertEqual(status["singleVariantHeroes"], ["tracer"])

    def test_variant_meanings_are_explained(self):
        write_png(os.path.join(self.d, "lucio.dead.png"))
        write_png(os.path.join(self.d, "lucio.weird.png"))
        status = tb.template_set_status(self.d, ROSTER)
        meanings = {v["variant"]: v["meaning"] for v in status["variantsByHero"]["lucio"]}
        self.assertIn("dead", meanings["dead"])
        self.assertIn("custom state", meanings["weird"])


class TestCoverageForLayout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = content_db.connect(os.path.join(self.tmp.name, "owcs.sqlite"))
        content_db.init_schema(self.con)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_a_layout_without_templates_dir_is_reported_not_crashed(self):
        path = os.path.join(self.tmp.name, "lay.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"frame_width": 1920, "frame_height": 1080}, f)
        rep = tb.coverage_for_layout(self.con, path)
        self.assertFalse(rep["exists"])
        self.assertIn("declares no templates_dir", rep["note"])

    def test_the_repos_real_proven_layout_reports_real_coverage(self):
        """A regression guard on the shipped package, not a fixture: the
        proven owcs_jksix_qwc set must keep reporting honest coverage."""
        con = content_db.connect()
        try:
            content_db.init_schema(con)
            rep = tb.coverage_for_layout(
                con, os.path.join(content_db.REPO_ROOT, "layouts",
                                  "owcs_jksix_qwc.json"))
        finally:
            con.close()
        self.assertTrue(rep["exists"])
        self.assertGreater(rep["coveredCount"], 0)
        self.assertLess(rep["coveragePct"], 100.0,
                        "if this package ever reaches 100% coverage, update "
                        "this test — it exists to keep the ceiling honest")
        self.assertTrue(rep["uncoveredNames"])


class TestProvenance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = os.path.join(self.tmp.name, "templates")
        os.makedirs(self.d)

    def tearDown(self):
        self.tmp.cleanup()

    def test_provenance_records_and_merges_across_harvests(self):
        tb.write_provenance(self.d, [
            {"file": "tracer.png", "sourceOffset": 412.0, "cluster": "a1_c0"}],
            source_video="vid1", labeled_by="alice", layout_id="owcs_test")
        tb.write_provenance(self.d, [
            {"file": "lucio.png", "sourceOffset": 900.0, "cluster": "b3_c1"}],
            source_video="vid1", labeled_by="bob", layout_id="owcs_test")
        prov = tb.load_provenance(self.d)
        files = [e["file"] for e in prov["entries"]]
        self.assertEqual(files, ["lucio.png", "tracer.png"])
        self.assertEqual(len(prov["harvests"]), 2)
        self.assertEqual(prov["harvests"][1]["labeledBy"], "bob")
        for e in prov["entries"]:
            self.assertIn("recordedAt", e)

    def test_re_recording_the_same_file_updates_it_rather_than_duplicating(self):
        tb.write_provenance(self.d, [{"file": "tracer.png", "cluster": "a1_c0"}])
        tb.write_provenance(self.d, [{"file": "tracer.png", "cluster": "a2_c3"}])
        prov = tb.load_provenance(self.d)
        self.assertEqual(len(prov["entries"]), 1)
        self.assertEqual(prov["entries"][0]["cluster"], "a2_c3")

    def test_missing_provenance_is_none_not_an_error(self):
        self.assertIsNone(tb.load_provenance(self.d))

    def test_corrupt_provenance_is_none_not_an_exception(self):
        with open(os.path.join(self.d, tb.PROVENANCE_FILENAME), "w") as f:
            f.write("{not json")
        self.assertIsNone(tb.load_provenance(self.d))

    def test_provenance_file_is_never_mistaken_for_a_template(self):
        tb.write_provenance(self.d, [{"file": "tracer.png"}])
        self.assertEqual(tb.scan_template_dir(self.d), {})


class TestLabelSuggestion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.icons = os.path.join(self.tmp.name, "official")

    def tearDown(self):
        self.tmp.cleanup()

    def _icon(self, hero_id: str, seed: int):
        path = os.path.join(self.icons, hero_id, "icon.png")
        write_png(path, seed=seed, size=64)
        return path

    def test_a_cluster_matching_one_icon_gets_a_confident_suggestion(self):
        self._icon("tracer", 1)
        self._icon("lucio", 2)
        index = tb.official_icon_index(ROSTER, assets_dir=self.icons)
        proto = cv2.imread(index["tracer"], cv2.IMREAD_GRAYSCALE)
        out = tb.suggest_labels({"a1_c0": proto}, index)
        s = out["suggestions"]["a1_c0"]
        self.assertEqual(s["hero"], "tracer")
        self.assertTrue(s["confident"])
        self.assertGreaterEqual(s["margin"], tb.LABEL_MARGIN)
        self.assertEqual(out["needsReview"], [])

    def test_a_low_margin_cluster_is_surfaced_for_review_not_labeled(self):
        self._icon("tracer", 1)
        self._icon("lucio", 1)              # identical art -> a genuine tie
        index = tb.official_icon_index(ROSTER, assets_dir=self.icons)
        proto = cv2.imread(index["tracer"], cv2.IMREAD_GRAYSCALE)
        out = tb.suggest_labels({"a1_c0": proto}, index)
        s = out["suggestions"]["a1_c0"]
        self.assertIsNone(s["hero"])
        self.assertFalse(s["confident"])
        self.assertIn("a human must label", s["reason"])
        self.assertEqual(out["needsReview"], ["a1_c0"])

    def test_a_cluster_matching_nothing_well_is_not_labeled(self):
        self._icon("tracer", 1)
        index = tb.official_icon_index(ROSTER, assets_dir=self.icons)
        noise = np.full((48, 48), 128, np.uint8)      # flat grey, no structure
        out = tb.suggest_labels({"a1_c0": noise}, index)
        self.assertIsNone(out["suggestions"]["a1_c0"]["hero"])

    def test_no_icons_available_means_no_suggestion_at_all(self):
        out = tb.suggest_labels({"a1_c0": np.zeros((10, 10), np.uint8)}, {})
        self.assertEqual(out["unmatched"], ["a1_c0"])
        self.assertEqual(out["suggestions"], {})

    def test_the_note_states_official_art_is_never_a_template(self):
        out = tb.suggest_labels({}, {})
        self.assertIn("No official asset is ever written into a template set",
                      out["note"])

    def test_official_index_only_returns_heroes_with_real_asset_files(self):
        self._icon("tracer", 1)
        index = tb.official_icon_index(ROSTER, assets_dir=self.icons)
        self.assertEqual(sorted(index), ["tracer"])

    def test_the_repos_real_official_assets_are_discoverable(self):
        con = content_db.connect()
        try:
            content_db.init_schema(con)
            roster = tb.full_roster(con)
        finally:
            con.close()
        index = tb.official_icon_index(roster)
        self.assertGreater(len(index), 20,
                           "official hero assets should be committed for most "
                           "of the roster (see build_hero_official_assets.py)")
        for path in index.values():
            self.assertTrue(os.path.exists(path))


class TestBootstrap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = content_db.connect(os.path.join(self.tmp.name, "owcs.sqlite"))
        content_db.init_schema(self.con)
        for h in ROSTER:
            self.con.execute(
                "INSERT OR REPLACE INTO heroes (id,name,role) VALUES (?,?,?)",
                (h["id"], h["name"], h["role"]))
        self.con.commit()
        self.d = os.path.join(self.tmp.name, "templates", "pkg")
        os.makedirs(self.d)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_an_existing_set_is_reused_never_reharvested(self):
        for h in ROSTER:
            write_png(os.path.join(self.d, f"{h['id']}.png"))
        result = tb.bootstrap(self.con, self.d)
        self.assertEqual(result["action"], "reuse-existing")
        self.assertTrue(result["reused"])
        self.assertIn("reusing the existing template set", result["message"])

    def test_a_partial_set_is_still_reused_but_names_the_ceiling(self):
        write_png(os.path.join(self.d, "tracer.png"))
        result = tb.bootstrap(self.con, self.d)
        self.assertEqual(result["action"], "reuse-existing")
        self.assertIn("will read UNKNOWN", result["message"])
        self.assertEqual(result["status"]["coveredCount"], 1)

    def test_an_empty_set_with_no_clip_reports_what_it_needs(self):
        result = tb.bootstrap(self.con, self.d)
        self.assertEqual(result["action"], "needs-harvest")
        self.assertIn("no clip/layout was supplied", result["message"])
        self.assertFalse(result["reused"])

    def test_bootstrap_never_writes_a_hero_template(self):
        before = set(os.listdir(self.d))
        tb.bootstrap(self.con, self.d)
        self.assertEqual(set(os.listdir(self.d)), before)


class TestPackagingValidation(unittest.TestCase):
    """The gate that stops a layout from shipping without its templates."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "layouts"))
        check_packaging.FAILS.clear()
        check_packaging.WARNS.clear()

    def tearDown(self):
        check_packaging.FAILS.clear()
        check_packaging.WARNS.clear()
        self.tmp.cleanup()

    def _layout(self, name: str, **extra):
        lay = {"frame_width": 1920, "frame_height": 1080}
        lay.update(extra)
        with open(os.path.join(self.root, "layouts", name), "w",
                  encoding="utf-8") as f:
            json.dump(lay, f)

    def _detection_capable(self, **extra):
        return dict(
            hud_probe={"chips_a": [[0, 0, 10, 10]] * 5,
                       "chips_b": [[100, 0, 10, 10]] * 5},
            slots_a=[[0, 20, 20, 20]] * 5, slots_b=[[100, 20, 20, 20]] * 5,
            **extra)

    def test_a_detection_capable_layout_without_templates_dir_fails(self):
        self._layout("pkg.json", **self._detection_capable())
        check_packaging.check_layouts(self.root)
        self.assertTrue(any("no 'templates_dir'" in f
                            for f in check_packaging.FAILS),
                        check_packaging.FAILS)

    def test_a_templates_dir_that_does_not_exist_fails(self):
        self._layout("pkg.json",
                     **self._detection_capable(templates_dir="templates/gone"))
        check_packaging.check_layouts(self.root)
        self.assertTrue(any("missing or empty" in f
                            for f in check_packaging.FAILS),
                        check_packaging.FAILS)

    def test_an_empty_templates_dir_fails(self):
        os.makedirs(os.path.join(self.root, "templates", "pkg"))
        self._layout("pkg.json",
                     **self._detection_capable(templates_dir="templates/pkg"))
        check_packaging.check_layouts(self.root)
        self.assertTrue(any("missing or empty" in f
                            for f in check_packaging.FAILS))

    def test_a_dir_holding_only_the_provenance_file_still_fails(self):
        d = os.path.join(self.root, "templates", "pkg")
        os.makedirs(d)
        tb.write_provenance(d, [{"file": "tracer.png"}])
        self._layout("pkg.json",
                     **self._detection_capable(templates_dir="templates/pkg"))
        check_packaging.check_layouts(self.root)
        self.assertTrue(any("missing or empty" in f
                            for f in check_packaging.FAILS),
                        "a package with provenance but no templates is empty")

    def test_a_populated_templates_dir_passes(self):
        d = os.path.join(self.root, "templates", "pkg")
        write_png(os.path.join(d, "tracer.png"))
        self._layout("pkg.json",
                     **self._detection_capable(templates_dir="templates/pkg"))
        check_packaging.check_layouts(self.root)
        self.assertEqual(check_packaging.FAILS, [])

    def test_a_diagnostic_only_layout_warns_instead_of_failing(self):
        self._layout("fixture.json", anchor={"rect": [0, 0, 10, 10]})
        check_packaging.check_layouts(self.root)
        self.assertEqual(check_packaging.FAILS, [])
        self.assertTrue(any("diagnostic/fixture layout only" in w
                            for w in check_packaging.WARNS))

    def test_a_phantom_hero_template_file_is_a_hard_failure(self):
        d = os.path.join(self.root, "templates", "pkg")
        write_png(os.path.join(d, "tracer.png"))
        write_png(os.path.join(d, "notahero.png"))
        self._layout("pkg.json",
                     **self._detection_capable(templates_dir="templates/pkg"))
        os.makedirs(os.path.join(self.root, "data"))
        con = content_db.connect(os.path.join(self.root, "data", "owcs.sqlite"))
        content_db.init_schema(con)
        for h in ROSTER:
            con.execute("INSERT OR REPLACE INTO heroes (id,name,role) "
                        "VALUES (?,?,?)", (h["id"], h["name"], h["role"]))
        con.commit()
        con.close()
        check_packaging.check_template_coverage(self.root)
        self.assertTrue(any("matching no hero id" in f
                            for f in check_packaging.FAILS),
                        check_packaging.FAILS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
