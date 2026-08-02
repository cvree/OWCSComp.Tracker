#!/usr/bin/env python3
"""
test_site_paths.py — paths must survive Windows, including two drives.

The first real Windows CI run failed ten suites on one line:

    ValueError: path is on mount 'C:', start on mount 'D:'

`os.path.relpath` has no answer when two paths are on different drives, so it
raises. On a GitHub Windows runner the checkout is on D: and `tempfile` hands
back C:. In production the same thing happens the moment someone puts their
media root on a second drive — which the desktop app positively encourages,
since broadcasts are enormous — and the job dies with `unknown_error` naming
neither the drive nor the path.

This suite pins both halves of the fix: the helper is total, and no module
that records a stored path goes back to calling `relpath` directly.

Run: python3 pipeline/test_site_paths.py
"""
from __future__ import annotations

import ast
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import site_paths  # noqa: E402


class CrossDrive:
    """Make os.path.relpath behave the way ntpath does across mounts."""

    def __enter__(self):
        self._real = os.path.relpath

        def raiser(path, start=None):
            raise ValueError("path is on mount 'C:', start on mount 'D:'")

        os.path.relpath = raiser
        return self

    def __exit__(self, *exc):
        os.path.relpath = self._real


class TestSiteRelpath(unittest.TestCase):
    def test_ordinary_relative_path(self):
        self.assertEqual(
            site_paths.site_relpath(os.path.join("/repo", "a", "b.png"), "/repo"),
            "a/b.png")

    def test_always_forward_slashes(self):
        """A stored backslash never resolves as a URL separator, so these
        strings are useless to the browser that has to fetch them."""
        out = site_paths.site_relpath(os.path.join("/repo", "a", "b", "c.png"),
                                      "/repo")
        self.assertNotIn("\\", out)
        self.assertIn("/", out)

    def test_cross_drive_returns_a_usable_path_instead_of_raising(self):
        """The whole point. An absolute path is the honest answer when no
        relative one exists — it is at least resolvable on the machine that
        recorded it."""
        with CrossDrive():
            out = site_paths.site_relpath("/tmp/media/clip.mp4", "/repo")
        self.assertTrue(out, "returned nothing for a cross-drive path")
        self.assertIn("clip.mp4", out)
        self.assertNotIn("\\", out)

    def test_cross_drive_never_raises_for_any_input(self):
        with CrossDrive():
            for value in ("/a/b", "relative/path", "x"):
                with self.subTest(path=value):
                    site_paths.site_relpath(value, "/repo")

    def test_empty_input_is_empty_output(self):
        self.assertEqual(site_paths.site_relpath("", "/repo"), "")

    def test_is_relative(self):
        self.assertTrue(site_paths.is_relative("/repo/a/b", "/repo"))
        self.assertFalse(site_paths.is_relative("/elsewhere/a", "/repo"))
        self.assertFalse(site_paths.is_relative("", "/repo"))

    def test_is_relative_is_false_across_drives_not_an_exception(self):
        with CrossDrive():
            self.assertFalse(site_paths.is_relative("/tmp/x", "/repo"))


class TestNoModuleCallsRelpathDirectly(unittest.TestCase):
    """The regression guard.

    Every module that turns an absolute path into a STORED one must go
    through the helper. A new direct `os.path.relpath` call is how this bug
    comes back, and it will only show up on someone else's machine.
    """

    #: Modules that record paths into payloads, exports, reports or HTML.
    GUARDED = (
        "automation/worker.py",
        "automation/team_assets.py",
        "automation/layout_resolver.py",
        "automation/publish.py",
        "build_layout_debug.py",
        "export_data.py",
    )

    def test_guarded_modules_use_the_helper(self):
        offenders = []
        for rel in self.GUARDED:
            path = os.path.join(HERE, rel)
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                source = f.read()
            # Parse rather than grep: the modules explain this hazard in
            # their own docstrings, and prose describing `os.path.relpath`
            # is not a call to it. `ast` sees only real calls.
            tree = ast.parse(source, filename=rel)
            lines = source.splitlines()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if (isinstance(func, ast.Attribute) and func.attr == "relpath"
                        and isinstance(func.value, ast.Attribute)
                        and func.value.attr == "path"):
                    text = lines[node.lineno - 1].strip()
                    offenders.append(f"{rel}:{node.lineno}: {text}")
        self.assertEqual(
            offenders, [],
            "these call os.path.relpath directly, which RAISES across Windows "
            "drives — use site_paths.site_relpath:\n  " + "\n  ".join(offenders))

    def test_the_guarded_modules_actually_import_it(self):
        for rel in self.GUARDED:
            path = os.path.join(HERE, rel)
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            if "site_relpath" not in text:
                continue
            with self.subTest(module=rel):
                self.assertRegex(
                    text, r"import site_paths",
                    f"{rel} uses site_relpath but never imports site_paths")


class TestWindowsFileUrls(unittest.TestCase):
    """`file://` URLs are what Windows itself produces when you drag a file
    into an address bar, and the intake box has to understand them."""

    def setUp(self) -> None:
        sys.path.insert(0, os.path.join(REPO, "desktop"))
        from owcs_desktop import intake
        self.parse = intake._as_local_path

    def test_windows_drive_as_authority(self):
        """`file://C:\\videos\\x.mp4` — urlsplit reads `C:` as the HOST and
        leaves the path empty, so without normalising separators first the
        drive is lost and the file is reported missing."""
        self.assertEqual(self.parse(r"file://C:\videos\x.mp4"),
                         "C:/videos/x.mp4")

    def test_windows_three_slash_form(self):
        self.assertEqual(self.parse("file:///C:/videos/x.mp4"),
                         "C:/videos/x.mp4")

    def test_posix_form(self):
        self.assertEqual(self.parse("file:///home/user/x.mp4"),
                         "/home/user/x.mp4")

    def test_unc_path_keeps_its_host(self):
        self.assertEqual(self.parse("file://server/share/x.mp4"),
                         r"\\server\share\x.mp4")

    def test_a_bare_windows_path_is_untouched(self):
        """Already absolute; running it through abspath off Windows would
        glue the working directory in front of it."""
        self.assertEqual(self.parse(r"D:\broadcasts\x.mp4"),
                         r"D:\broadcasts\x.mp4")

    def test_a_url_is_not_a_path(self):
        self.assertIsNone(self.parse("https://www.youtube.com/watch?v=abc"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
