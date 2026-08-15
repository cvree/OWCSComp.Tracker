#!/usr/bin/env python3
"""
test_hero_crop.py — the hero portraits show the hero.

The bug this guards against is not subtle and it shipped for months: the
official splash art was cropped down the middle, so a 40px portrait in a
stats table was mostly blurred backdrop with the hero's head clipped off
the top. It was not caught because nothing asserted anything about what
was *in* the crop — only that a file of the right size existed.

So these tests assert framing, on the real committed art, offline:

  * every hero the site advertises official art for has all four variants
    on disk at the size the manifest claims;
  * the crop is a real crop — the portrait's content differs from a plain
    centre square, for the great majority of heroes (some splashes really
    are centred, so this is a population check, not a per-hero one);
  * the crop lands on the detected subject rather than beside it;
  * the flat colour band Blizzard pads the splash with is excluded;
  * the framing is deterministic — the same input crops identically twice,
    because a recrop that reshuffled every file would be unreviewable.

Run: python3 pipeline/test_hero_crop.py
"""
from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hero_crop  # noqa: E402

OFFICIAL = os.path.join(ROOT, "assets", "img", "heroes", "official")
MANIFEST = os.path.join(OFFICIAL, "manifest.json")


def hero_ids() -> list:
    return sorted(d for d in os.listdir(OFFICIAL)
                  if os.path.isdir(os.path.join(OFFICIAL, d)))


def artwork(hero_id: str):
    import cv2
    return cv2.imread(os.path.join(OFFICIAL, hero_id, "artwork.jpg"))


def image_size(hero_id: str, fname: str) -> tuple:
    """(width, height) of a committed variant.

    Read with cv2 rather than Pillow: cv2 and numpy are already the
    pipeline's two image dependencies, and a test is a bad reason to add a
    third to requirements.txt.
    """
    import cv2

    path = os.path.join(OFFICIAL, hero_id, fname)
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    assert img is not None, f"{path} did not decode"
    return (img.shape[1], img.shape[0])


class TestCommittedVariants(unittest.TestCase):
    def test_every_hero_has_every_variant(self):
        missing = []
        for hero_id in hero_ids():
            for name in ("artwork.jpg", "artwork.webp", "card.webp",
                         "portrait.png", "portrait.webp", "icon.png", "icon.webp"):
                if not os.path.exists(os.path.join(OFFICIAL, hero_id, name)):
                    missing.append(f"{hero_id}/{name}")
        self.assertEqual(missing, [], f"missing variants: {missing}")

    def test_manifest_sizes_match_the_files(self):
        with open(MANIFEST, encoding="utf-8") as f:
            heroes = json.load(f)["heroes"]
        wrong = []
        for hero_id, entry in heroes.items():
            for key, fname in (("portrait", "portrait.webp"), ("icon", "icon.webp"),
                               ("card", "card.webp")):
                spec = entry.get(key)
                if not spec:
                    wrong.append(f"{hero_id}: manifest has no {key}")
                    continue
                size = image_size(hero_id, fname)
                if size != (spec["width"], spec["height"]):
                    wrong.append(f"{hero_id}/{fname} is {size}, "
                                 f"manifest says {(spec['width'], spec['height'])}")
        self.assertEqual(wrong, [], f"size mismatches: {wrong}")

    def test_portraits_are_square_and_icons_are_smaller(self):
        bad = []
        for hero_id in hero_ids():
            pw, ph = image_size(hero_id, "portrait.webp")
            iw, _ = image_size(hero_id, "icon.webp")
            if pw != ph:
                bad.append(f"{hero_id} portrait {(pw, ph)} is not square")
            if iw >= pw:
                bad.append(f"{hero_id} icon width {iw} is not smaller than {pw}")
        self.assertEqual(bad, [], f"{bad}")


class TestFraming(unittest.TestCase):
    """The crop is chosen from the picture, not from its geometry."""

    def test_crop_is_not_a_centre_square_for_most_heroes(self):
        centred = []
        for hero_id in hero_ids():
            img = artwork(hero_id)
            self.assertIsNotNone(img, f"{hero_id}: artwork.jpg did not decode")
            h, w = img.shape[:2]
            side = min(h, w)
            box = hero_crop.portrait_box(img)
            same_x = abs(box.x - (w - side) // 2) < side * 0.05
            same_y = abs(box.y - (h - side) // 2) < side * 0.05
            same_size = abs(box.w - side) < side * 0.05
            if same_x and same_y and same_size:
                centred.append(hero_id)
        # A handful of splashes genuinely are centred; a majority would
        # mean the subject search has stopped working.
        self.assertLess(len(centred), len(hero_ids()) * 0.2,
                        f"too many crops are plain centre squares: {centred}")

    def test_crop_contains_the_subject(self):
        outside = []
        for hero_id in hero_ids():
            img = artwork(hero_id)
            live = hero_crop.live_region(img)
            subj = hero_crop.subject(img[live.y:live.y1, live.x:live.x1])
            if subj is None:
                continue
            core_cx = live.x + subj.core.x + subj.core.w // 2
            box = hero_crop.portrait_box(img)
            if not (box.x <= core_cx <= box.x1):
                outside.append(f"{hero_id}: subject centre {core_cx} outside crop "
                               f"[{box.x}, {box.x1}]")
        self.assertEqual(outside, [], f"{outside}")

    def test_crop_excludes_the_flat_padding_band(self):
        """Blizzard pads the splash with a flat band for its own page text.

        Wherever the live region is genuinely shorter than the file, the
        crop must stay inside it — that band is the single biggest source
        of "the thumbnail is 60% empty".
        """
        leaked = []
        for hero_id in hero_ids():
            img = artwork(hero_id)
            live = hero_crop.live_region(img)
            if live.h >= img.shape[0] - 2:
                continue  # nothing was trimmed on this one
            box = hero_crop.portrait_box(img)
            if box.y < live.y or box.y1 > live.y1:
                leaked.append(f"{hero_id}: crop [{box.y}, {box.y1}] leaves live "
                              f"[{live.y}, {live.y1}]")
        self.assertEqual(leaked, [], f"{leaked}")

    def test_framing_is_deterministic(self):
        for hero_id in hero_ids()[:8]:
            img = artwork(hero_id)
            first = hero_crop.portrait_box(img)
            second = hero_crop.portrait_box(img)
            self.assertEqual(first, second, f"{hero_id} framed differently on a rerun")

    def test_render_returns_the_requested_shape(self):
        img = artwork(hero_ids()[0])
        square = hero_crop.render(img, 256)
        self.assertEqual(square.shape[:2], (256, 256))
        wide = hero_crop.render(img, 600, 3 / 2)
        self.assertEqual(wide.shape[1], 600)
        self.assertEqual(wide.shape[0], 400)


class TestDegenerateInput(unittest.TestCase):
    """A synthetic image with no subject must not crash or invent one."""

    def test_flat_image_falls_back_to_a_geometric_crop(self):
        import numpy as np

        flat = np.full((400, 700, 3), 40, dtype=np.uint8)
        box = hero_crop.portrait_box(flat)
        self.assertEqual(box.w, box.h)
        self.assertGreaterEqual(box.x, 0)
        self.assertLessEqual(box.x1, 700)
        self.assertLessEqual(box.y1, 400)

    def test_live_region_never_eats_the_picture(self):
        import numpy as np

        rng = np.random.default_rng(7)
        noise = rng.integers(0, 255, (300, 500, 3), dtype=np.uint8)
        live = hero_crop.live_region(noise)
        self.assertGreaterEqual(live.h, 150)


if __name__ == "__main__":
    unittest.main(verbosity=2)
