#!/usr/bin/env python3
"""
test_hero_official_assets.py — Phase D3 official hero presentation assets.

Two kinds of coverage:
  * Pure-function unit tests for the matching/normalization logic in
    build_hero_official_assets.py, entirely offline (synthetic HTML
    fixtures, no network).
  * Validation of the real, committed asset_manifest.json's "heroOfficial"
    section + the actual files on disk — proves every one of the 52 public
    hero ids resolves to a real official asset or the intentional
    unknown-hero fallback, with GitHub-Pages-safe paths, attribution,
    dimensions/hashes, and a WebP variant (AVIF explicitly null).
Run: python3 pipeline/test_hero_official_assets.py
"""
from __future__ import annotations
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db  # noqa: E402
import build_hero_official_assets as hoa  # noqa: E402

MANIFEST_PATH = os.path.join(db.REPO_ROOT, "assets", "data", "asset_manifest.json")


def _fake_page(entries: list[str]) -> str:
    """A synthetic hero page carrying the same inline-style CSS pattern the
    real overwatch.blizzard.com pages use for their splash images."""
    body = "\n".join(
        f'<div style="--x:url({url})"></div>' for url in entries)
    return f"<html><body>{body}</body></html>"


GENERIC_URL = ("https://blz-contentstack-images.akamaized.net/v3/assets/"
              "blt2477dcaf4ebd440c/blt29fc116531f1acaa/660c5c8d022c72028b87ff42/960_Outro.jpg")


def _hero_url(name: str) -> str:
    return (f"https://blz-contentstack-images.akamaized.net/v3/assets/"
            f"blt2477dcaf4ebd440c/blt000000000000000/000000000000000000000000/960_{name}.jpg")


class TestNormalize(unittest.TestCase):
    def test_strips_diacritics_and_case(self):
        self.assertEqual(hoa._normalize("Torbjörn"), "torbjorn")
        self.assertEqual(hoa._normalize("Lúcio"), "lucio")

    def test_strips_punctuation_and_spaces(self):
        self.assertEqual(hoa._normalize("D.Va"), "dva")
        self.assertEqual(hoa._normalize("Junker Queen"), "junkerqueen")
        self.assertEqual(hoa._normalize("Soldier: 76"), "soldier76")


class TestRevisionSuffix(unittest.TestCase):
    def test_strips_v_suffix(self):
        self.assertEqual(hoa._strip_revision_suffix("Juno_v2"), "Juno")

    def test_strips_numeric_suffix(self):
        self.assertEqual(hoa._strip_revision_suffix("Mei_02"), "Mei")

    def test_leaves_plain_name_untouched(self):
        self.assertEqual(hoa._strip_revision_suffix("Tracer"), "Tracer")


class TestFindSplashUrl(unittest.TestCase):
    def test_matches_hero_specific_image_over_generic_ones(self):
        html = _fake_page([GENERIC_URL, _hero_url("Tracer"), GENERIC_URL])
        url = hoa.find_splash_url(html, "Tracer")
        self.assertIsNotNone(url)
        self.assertIn("Tracer", url)

    def test_never_matches_a_generic_shared_image(self):
        html = _fake_page([GENERIC_URL])
        self.assertIsNone(hoa.find_splash_url(html, "Tracer"))

    def test_direct_match_preferred_over_revision_stripped_match(self):
        # A hero whose real name itself ends in digits (Soldier: 76) must
        # match directly, never have its own digits stripped as if they
        # were a CMS revision suffix.
        html = _fake_page([_hero_url("Soldier_76")])
        url = hoa.find_splash_url(html, "Soldier: 76")
        self.assertIsNotNone(url)

    def test_matches_after_stripping_revision_suffix(self):
        html = _fake_page([_hero_url("Juno_v2")])
        url = hoa.find_splash_url(html, "Juno")
        self.assertIsNotNone(url)

    def test_diacritic_hero_name_matches_ascii_filename(self):
        html = _fake_page([_hero_url("Torbjorn")])
        url = hoa.find_splash_url(html, "Torbjörn")
        self.assertIsNotNone(url)

    def test_unmatched_hero_returns_none_never_a_guess(self):
        html = _fake_page([_hero_url("SomeOtherHero"), GENERIC_URL])
        self.assertIsNone(hoa.find_splash_url(html, "Zarya"))


class TestHeroSlugCoverage(unittest.TestCase):
    def test_every_db_hero_has_a_known_slug_mapping(self):
        con = db.connect()
        ids = {r["id"] for r in con.execute("SELECT id FROM heroes")}
        con.close()
        missing = ids - set(hoa.HERO_SLUGS)
        self.assertEqual(missing, set(), f"heroes missing a Blizzard slug mapping: {missing}")

    def test_slug_values_are_url_safe(self):
        import re
        for hero_id, slug in hoa.HERO_SLUGS.items():
            self.assertRegex(slug, r"^[a-z0-9-]+$", f"{hero_id}: slug {slug!r} not URL-safe")


@unittest.skipUnless(os.path.exists(MANIFEST_PATH), "asset_manifest.json not built yet")
class TestCommittedManifest(unittest.TestCase):
    def setUp(self):
        with open(MANIFEST_PATH, encoding="utf-8") as f:
            self.manifest = json.load(f)
        self.hero_official = self.manifest.get("heroOfficial", {})
        con = db.connect()
        self.all_hero_ids = {r["id"] for r in con.execute("SELECT id FROM heroes")}
        con.close()

    def test_all_52_public_hero_ids_present(self):
        self.assertEqual(set(self.hero_official), self.all_hero_ids)

    def test_every_hero_resolves_or_has_intentional_fallback(self):
        for hero_id, entry in self.hero_official.items():
            self.assertIn(entry["reviewStatus"],
                          ("official-blizzard-source", "fallback-unknown-hero"),
                          f"{hero_id}: unexpected reviewStatus {entry['reviewStatus']!r}")

    def test_resolved_heroes_have_real_files_on_disk(self):
        for hero_id, entry in self.hero_official.items():
            if entry["reviewStatus"] != "official-blizzard-source":
                continue
            for kind in ("portrait", "artwork", "icon"):
                asset = entry[kind]
                self.assertIsNotNone(asset, f"{hero_id}.{kind} missing")
                full = os.path.join(db.REPO_ROOT, asset["path"])
                self.assertTrue(os.path.exists(full), f"{hero_id}.{kind}: {full} does not exist")
                self.assertGreater(os.path.getsize(full), 0, f"{hero_id}.{kind}: empty file")

    def test_paths_are_github_pages_safe(self):
        for hero_id, entry in self.hero_official.items():
            if entry["reviewStatus"] != "official-blizzard-source":
                continue
            for kind in ("portrait", "artwork", "icon"):
                for key in ("path", "webp"):
                    p = entry[kind][key]
                    self.assertNotIn("\\", p, f"{hero_id}.{kind}.{key}: backslash in {p!r}")
                    self.assertFalse(p.startswith("/"), f"{hero_id}.{kind}.{key}: absolute path {p!r}")

    def test_webp_present_avif_explicitly_null(self):
        for hero_id, entry in self.hero_official.items():
            if entry["reviewStatus"] != "official-blizzard-source":
                continue
            for kind in ("portrait", "artwork", "icon"):
                asset = entry[kind]
                self.assertTrue(os.path.exists(os.path.join(db.REPO_ROOT, asset["webp"])),
                               f"{hero_id}.{kind}: webp variant missing")
                self.assertIsNone(asset["avif"], f"{hero_id}.{kind}: avif should be explicit None, not fabricated")

    def test_dimensions_and_hash_recorded(self):
        for hero_id, entry in self.hero_official.items():
            if entry["reviewStatus"] != "official-blizzard-source":
                continue
            self.assertIsNotNone(entry["artwork"]["hash"])
            for kind in ("portrait", "artwork", "icon"):
                self.assertGreater(entry[kind]["width"], 0)
                self.assertGreater(entry[kind]["height"], 0)

    def test_source_and_attribution_present(self):
        for hero_id, entry in self.hero_official.items():
            if entry["reviewStatus"] != "official-blizzard-source":
                continue
            self.assertTrue(entry["sourceUrl"].startswith("https://overwatch.blizzard.com/"))
            self.assertTrue(entry["attribution"])
            self.assertTrue(entry["usageNote"])

    def test_role_metadata_present(self):
        for hero_id, entry in self.hero_official.items():
            self.assertIn(entry["role"], ("Tank", "Damage", "Support"), hero_id)

    def test_aliases_never_fabricated_only_curated_list(self):
        import build_asset_manifest as bam
        for hero_id, entry in self.hero_official.items():
            self.assertEqual(entry["aliases"], bam.HERO_ALIASES.get(hero_id, []))

    def test_fallback_entries_have_no_broken_paths(self):
        for hero_id, entry in self.hero_official.items():
            if entry["reviewStatus"] == "fallback-unknown-hero":
                self.assertIsNone(entry["portrait"])
                self.assertIsNone(entry["artwork"])
                self.assertIsNone(entry["icon"])
                self.assertIsNone(entry["sourceUrl"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
