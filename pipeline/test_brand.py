#!/usr/bin/env python3
"""
test_brand.py — one brand mark, everywhere (docs/BRAND.md).

  * desktop/build_icon.py is the only drawing of the mark: every file it
    owns (favicon, brand SVGs, home-screen icon, .ico, .png) matches it;
  * the header and footer's inline copy (BRAND_MARK in app/shell.js) is the
    generator's own slots, so the page mark cannot drift from the favicon;
  * the old marks are gone: no "CT" text tile, no gold hexagon;
  * every page links the favicon and the home-screen icon;
  * the 16 px icon is a mirror image (snapped slots stay symmetric).

Run: python3 pipeline/test_brand.py
"""
from __future__ import annotations

import glob
import importlib.util
import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import proc_text  # noqa: E402  (UTF-8 subprocess decoding)


def generator():
    spec = importlib.util.spec_from_file_location("build_icon", os.path.join(ROOT, "desktop", "build_icon.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Brand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = generator()

    def test_every_brand_file_matches_the_generator(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "desktop", "build_icon.py"), "--check"],
                           capture_output=True, cwd=ROOT, **proc_text.PIPE_TEXT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_the_header_mark_is_the_generators_mark(self):
        js = read("assets", "js", "app", "shell.js")
        m = re.search(r"const BRAND_MARK = .*?\n\s*'(<rect.*?)'", js, re.S)
        self.assertIsNotNone(m, "BRAND_MARK not found in shell.js")
        self.assertEqual(m.group(1), self.g._slots_svg("currentColor"))
        self.assertIn('class="brand__mark" aria-hidden="true">\' + BRAND_MARK', js)

    def test_the_old_marks_are_gone(self):
        js = read("assets", "js", "app", "shell.js")
        self.assertNotIn('aria-hidden="true">CT</span>', js)
        for css in ("owcs.css", "appliance.css", "desktop.css"):
            self.assertNotIn("brand--pub", read("assets", "css", css), css)
            self.assertNotIn(".brand-mark", read("assets", "css", css), css)

    def test_every_page_links_the_favicon_and_home_screen_icon(self):
        for path in sorted(glob.glob(os.path.join(ROOT, "*.html"))):
            with open(path, encoding="utf-8") as f:
                html = f.read()
            if 'http-equiv="refresh"' in html:
                continue
            name = os.path.basename(path)
            self.assertIn('href="assets/img/favicon.svg"', html, name)
            self.assertIn('href="assets/img/brand/apple-touch-icon.png"', html, name)

    def test_the_slots_sit_on_a_regular_pentagon(self):
        pts = self.g.slot_centres()
        self.assertEqual(len(pts), 5)
        d = lambda a, b: ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
        # neighbours round the pentagon: top, right, bottom right, bottom left, left
        ring = [pts[0], pts[2], pts[4], pts[3], pts[1]]
        sides = [d(ring[i], ring[(i + 1) % 5]) for i in range(5)]
        self.assertLess(max(sides) - min(sides), 0.01, sides)
        self.assertAlmostEqual(pts[0][0], 16.0, places=2)    # the tank slot on the axis

    def test_small_icons_are_mirror_images(self):
        for size in (16, 24, 32):
            px = self.g.render(size)
            ink = [[1 if (p[3] and p[0] < 90) else 0 for p in row] for row in px]
            cols = [x for row in ink for x, v in enumerate(row) if v]
            axis = (min(cols) + max(cols)) / 2
            for row in ink:
                xs = [x for x, v in enumerate(row) if v]
                if xs:
                    self.assertEqual(sorted(xs), sorted(int(2 * axis - x) for x in xs), size)


if __name__ == "__main__":
    unittest.main(verbosity=2)
