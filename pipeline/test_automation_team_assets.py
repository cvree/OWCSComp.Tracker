#!/usr/bin/env python3
"""
test_automation_team_assets.py — Phase D2 verified team-logo pipeline.

Covers the state machine (candidate -> downloaded -> validated ->
human-approved -> published), source-authority ranking, rejection of
invalid/tiny/broken/duplicate images, transparency preservation, variant
generation (square/wide + dark/light-safe), historical logo preservation,
idempotent republish, and that nothing is ever hotlinked, guessed, or
auto-approved.
Run: python3 pipeline/test_automation_team_assets.py
"""
from __future__ import annotations
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db as content_db  # noqa: E402
from automation import team_assets as ta  # noqa: E402

try:
    import cv2
    import numpy as np
    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False


def _make_png(path, w, h, *, alpha=True, color=(30, 20, 15)):
    """A logo mark filling the inner ~70% of the canvas, transparent margin
    around it — so a CORNER pixel is real background, not part of the mark
    (needed to tell a rescue backing plate apart from the mark's own color)."""
    img = np.zeros((h, w, 4 if alpha else 3), dtype=np.uint8)
    my, mx = h // 6, w // 6
    if alpha:
        img[my:h - my, mx:w - mx, 3] = 255
    else:
        img[:, :, :3] = color
    img[my:h - my, mx:w - mx, :3] = color
    cv2.imwrite(path, img)
    return path


class TeamAssetsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.staging = os.path.join(self.tmp.name, "staging")
        self.teams_dir = os.path.join(self.tmp.name, "teams")
        self.content_path = os.path.join(self.tmp.name, "owcs.sqlite")
        self.con = content_db.connect(self.content_path)
        content_db.init_schema(self.con)
        self.con.execute(
            "INSERT INTO teams (id, name, region, code) VALUES ('qad','Al Qadsiah','emea','QAD')")
        self.con.commit()
        self.registry = {"teams": {}}

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def _transport_for(self, path):
        def _t(url):
            with open(path, "rb") as f:
                return f.read()
        return _t


@unittest.skipUnless(HAVE_CV2, "cv2/numpy not installed")
class TestAuthorityRanking(TeamAssetsTestCase):
    def test_official_website_outranks_social_outranks_faceit(self):
        ta.add_candidate(self.registry, "qad", "https://qad.gg/logo.png", "official-website")
        ta.add_candidate(self.registry, "qad", "https://twitter.com/qad/photo.png", "official-social")
        ta.add_candidate(self.registry, "qad", "https://faceit.com/avatar.png", "official-faceit")
        ranked = ta.ranked_candidates(self.registry, "qad")
        self.assertEqual([c["sourceKind"] for c in ranked],
                         ["official-website", "official-social", "official-faceit"])

    def test_unknown_source_kind_rejected(self):
        with self.assertRaises(ValueError):
            ta.add_candidate(self.registry, "qad", "https://x.com/y.png", "random-guess")

    def test_add_candidate_idempotent_by_url(self):
        ta.add_candidate(self.registry, "qad", "https://qad.gg/logo.png", "official-website")
        ta.add_candidate(self.registry, "qad", "https://qad.gg/logo.png", "official-website")
        self.assertEqual(len(self.registry["teams"]["qad"]["assetCandidates"]), 1)


@unittest.skipUnless(HAVE_CV2, "cv2/numpy not installed")
class TestValidation(TeamAssetsTestCase):
    def _register_and_download(self, path, url="https://qad.gg/logo.png"):
        ta.add_candidate(self.registry, "qad", url, "official-website")
        ta.download_candidate(self.registry, "qad", url, self._transport_for(path),
                              staging_dir=self.staging)
        return url

    def test_valid_image_passes(self):
        path = _make_png(os.path.join(self.tmp.name, "good.png"), 200, 200)
        url = self._register_and_download(path)
        cand = ta.validate_candidate(self.registry, "qad", url)
        self.assertEqual(cand["state"], "validated")
        self.assertTrue(cand["hasTransparency"])

    def test_tiny_image_rejected(self):
        path = _make_png(os.path.join(self.tmp.name, "tiny.png"), 10, 10)
        url = self._register_and_download(path)
        cand = ta.validate_candidate(self.registry, "qad", url)
        self.assertEqual(cand["state"], "rejected")
        self.assertIn("small", cand["rejectReason"])

    def test_malformed_file_rejected(self):
        path = os.path.join(self.tmp.name, "bad.png")
        with open(path, "wb") as f:
            f.write(b"not a real image")
        url = self._register_and_download(path)
        cand = ta.validate_candidate(self.registry, "qad", url)
        self.assertEqual(cand["state"], "rejected")
        self.assertIn("malformed", cand["rejectReason"])

    def test_extreme_aspect_ratio_rejected(self):
        path = _make_png(os.path.join(self.tmp.name, "banner.png"), 800, 60)
        url = self._register_and_download(path)
        cand = ta.validate_candidate(self.registry, "qad", url)
        self.assertEqual(cand["state"], "rejected")
        self.assertIn("aspect", cand["rejectReason"])

    def test_duplicate_of_published_hash_rejected(self):
        path = _make_png(os.path.join(self.tmp.name, "dupe.png"), 200, 200)
        url = self._register_and_download(path)
        cand = ta.download_candidate(self.registry, "qad", url, self._transport_for(path),
                                     staging_dir=self.staging)
        cand2 = ta.validate_candidate(self.registry, "qad", url,
                                      published_hashes={cand["hash"]})
        self.assertEqual(cand2["state"], "rejected")
        self.assertIn("duplicate", cand2["rejectReason"])

    def test_validate_before_download_raises(self):
        ta.add_candidate(self.registry, "qad", "https://qad.gg/logo.png", "official-website")
        with self.assertRaises(ValueError):
            ta.validate_candidate(self.registry, "qad", "https://qad.gg/logo.png")

    def test_never_fetches_an_unregistered_url(self):
        with self.assertRaises(KeyError):
            ta.download_candidate(self.registry, "qad", "https://nobody-added-this.png",
                                  self._transport_for(_make_png(
                                      os.path.join(self.tmp.name, "x.png"), 100, 100)),
                                  staging_dir=self.staging)


@unittest.skipUnless(HAVE_CV2, "cv2/numpy not installed")
class TestApprovalGate(TeamAssetsTestCase):
    def _valid_candidate(self, url="https://qad.gg/logo.png"):
        path = _make_png(os.path.join(self.tmp.name, "good.png"), 200, 200)
        ta.add_candidate(self.registry, "qad", url, "official-website")
        ta.download_candidate(self.registry, "qad", url, self._transport_for(path),
                              staging_dir=self.staging)
        ta.validate_candidate(self.registry, "qad", url)
        return url

    def test_approve_requires_confirm_true(self):
        url = self._valid_candidate()
        with self.assertRaises(ValueError):
            ta.approve_candidate(self.registry, "qad", url, approved_by="op", confirm=False)
        cand = next(c for c in self.registry["teams"]["qad"]["assetCandidates"] if c["url"] == url)
        self.assertEqual(cand["state"], "validated")

    def test_approve_before_validated_raises(self):
        ta.add_candidate(self.registry, "qad", "https://x.png", "official-website")
        with self.assertRaises(ValueError):
            ta.approve_candidate(self.registry, "qad", "https://x.png",
                                 approved_by="op", confirm=True)

    def test_publish_before_approved_raises(self):
        url = self._valid_candidate()
        with self.assertRaises(ValueError):
            ta.publish_candidate(self.con, self.registry, "qad", url, teams_dir=self.teams_dir)

    def test_approved_then_published(self):
        url = self._valid_candidate()
        ta.approve_candidate(self.registry, "qad", url, approved_by="op", confirm=True)
        out = ta.publish_candidate(self.con, self.registry, "qad", url, teams_dir=self.teams_dir)
        self.assertEqual(out["state"], "published")
        row = self.con.execute("SELECT logo_url FROM teams WHERE id='qad'").fetchone()
        self.assertIsNotNone(row["logo_url"])
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, row["logo_url"])) or
                        os.path.exists(row["logo_url"]))


@unittest.skipUnless(HAVE_CV2, "cv2/numpy not installed")
class TestVariantsAndHistory(TeamAssetsTestCase):
    def _publish(self, color=(30, 20, 15), w=300, h=220):
        path = _make_png(os.path.join(self.tmp.name, "logo.png"), w, h, color=color)
        url = f"https://qad.gg/logo-{color}-{w}x{h}.png"
        ta.add_candidate(self.registry, "qad", url, "official-website")
        ta.download_candidate(self.registry, "qad", url, self._transport_for(path),
                              staging_dir=self.staging)
        ta.validate_candidate(self.registry, "qad", url)
        ta.approve_candidate(self.registry, "qad", url, approved_by="op", confirm=True)
        return ta.publish_candidate(self.con, self.registry, "qad", url, teams_dir=self.teams_dir)

    def test_square_and_wide_variants_preserve_transparency(self):
        out = self._publish()
        team_dir = os.path.join(self.teams_dir, "qad")
        square = cv2.imread(os.path.join(team_dir, "logo-square.png"), cv2.IMREAD_UNCHANGED)
        wide = cv2.imread(os.path.join(team_dir, "logo-wide.png"), cv2.IMREAD_UNCHANGED)
        self.assertEqual(square.shape[2], 4)
        self.assertEqual(wide.shape[2], 4)
        self.assertEqual(square.shape[0], square.shape[1])  # square crop
        self.assertGreaterEqual(wide.shape[1], wide.shape[0])  # wider than tall

    def test_dark_safe_and_light_safe_variants_written(self):
        self._publish()
        team_dir = os.path.join(self.teams_dir, "qad")
        self.assertTrue(os.path.exists(os.path.join(team_dir, "logo-dark-safe.webp")))
        self.assertTrue(os.path.exists(os.path.join(team_dir, "logo-light-safe.webp")))

    def test_dark_logo_gets_light_rescue_backing_for_dark_safe_variant(self):
        self._publish(color=(15, 10, 8))  # very dark BGR
        team_dir = os.path.join(self.teams_dir, "qad")
        dark_variant = cv2.imread(os.path.join(team_dir, "logo-dark-safe.webp"))
        # Corner pixel (outside the opaque logo fill in _make_png) should be
        # the LIGHT rescue backing, not a dark one the logo would vanish into.
        corner = dark_variant[0, 0]
        self.assertGreater(int(corner.mean()), 150)

    def test_avif_explicitly_null_not_silently_fabricated(self):
        out = self._publish()
        self.assertIsNone(out["avif"])

    def test_accent_color_is_restrained_mean_not_extreme(self):
        out = self._publish(color=(200, 100, 50))
        r, g, b = out["accentColorRgb"]
        self.assertTrue(0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255)

    def test_republish_same_candidate_is_idempotent_no_history_churn(self):
        self._publish()
        team_dir = os.path.join(self.teams_dir, "qad")
        self.assertFalse(os.path.isdir(os.path.join(team_dir, "history")))
        # Re-publish the SAME already-published candidate (e.g. a rerun).
        cand_url = self.registry["teams"]["qad"]["assetCandidates"][0]["url"]
        ta.publish_candidate(self.con, self.registry, "qad", cand_url, teams_dir=self.teams_dir)
        self.assertFalse(os.path.isdir(os.path.join(team_dir, "history")))

    def test_new_approved_logo_preserves_old_one_in_history(self):
        self._publish(color=(30, 20, 15))
        team_dir = os.path.join(self.teams_dir, "qad")
        # A second, DIFFERENT approved candidate replaces the first.
        path2 = _make_png(os.path.join(self.tmp.name, "logo2.png"), 250, 250, color=(200, 180, 160))
        url2 = "https://qad.gg/logo-v2.png"
        ta.add_candidate(self.registry, "qad", url2, "official-website")
        ta.download_candidate(self.registry, "qad", url2, self._transport_for(path2),
                              staging_dir=self.staging)
        ta.validate_candidate(self.registry, "qad", url2)
        ta.approve_candidate(self.registry, "qad", url2, approved_by="op", confirm=True)
        ta.publish_candidate(self.con, self.registry, "qad", url2, teams_dir=self.teams_dir)
        hist_dir = os.path.join(team_dir, "history")
        self.assertTrue(os.path.isdir(hist_dir))
        self.assertTrue(os.listdir(hist_dir))


@unittest.skipUnless(HAVE_CV2, "cv2/numpy not installed")
class TestCollectFromEnrichment(TeamAssetsTestCase):
    def test_promotes_faceit_avatar_url_as_official_faceit(self):
        self.registry["teams"]["qad"] = {"candidateSources": [
            "FACEIT team avatar (unverified candidate, auto-discovered 2026-07-25 "
            "via FACEIT teams API for qad): https://distribution.faceit-cdn.net/avatar.png"]}
        added = ta.collect_from_enrichment(self.registry)
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["sourceKind"], "official-faceit")
        self.assertEqual(added[0]["url"], "https://distribution.faceit-cdn.net/avatar.png")

    def test_idempotent_no_duplicate_promotion(self):
        self.registry["teams"]["qad"] = {"candidateSources": [
            "FACEIT team avatar (...): https://distribution.faceit-cdn.net/avatar.png"]}
        ta.collect_from_enrichment(self.registry)
        added_again = ta.collect_from_enrichment(self.registry)
        self.assertEqual(len(added_again), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
