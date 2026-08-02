#!/usr/bin/env python3
"""
test_desktop_pages.py — the control room and wizard pages are real.

A UI is where "looks finished" and "is finished" diverge most easily, so this
suite checks the two things a screenshot cannot:

  * **No dead controls.** Every button and nav item in the markup is wired to
    something in the JavaScript, and every API route the JavaScript calls
    exists in the Python. A button that silently does nothing is exactly the
    "fake button" this project must not ship.
  * **No leakage into the public site.** These pages are served only from
    127.0.0.1 and must never be linked from, or loaded by, a public page — and
    they must carry noindex, because GitHub Pages serves the whole repository.

It also checks the pages are self-contained (no external script or style
host), since the control room has to work on a machine with no internet.

Run: python3 pipeline/test_desktop_pages.py
"""
from __future__ import annotations

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "desktop"))

from owcs_desktop import webapi  # noqa: E402

PAGES = ("control-room.html", "setup.html")
SCRIPTS = ("assets/js/desktop/api.js",
           "assets/js/desktop/control-room.js",
           "assets/js/desktop/setup.js")


def read(rel: str) -> str:
    with open(os.path.join(REPO, rel), "r", encoding="utf-8") as f:
        return f.read()


class TestPagesExist(unittest.TestCase):
    def test_every_page_and_script_is_present(self):
        for rel in PAGES + SCRIPTS + ("assets/css/desktop.css",
                                      "desktop/assets/owcs.png"):
            with self.subTest(file=rel):
                self.assertTrue(os.path.exists(os.path.join(REPO, rel)),
                                f"{rel} is missing")

    def test_every_referenced_asset_resolves(self):
        """A 404 on a stylesheet turns the control room into unstyled text."""
        for page in PAGES:
            text = read(page)
            for rel in re.findall(r'(?:href|src)="([^"#:]+)"', text):
                if rel.startswith(("http", "//", "mailto")):
                    continue
                target = rel.split("?")[0]
                with self.subTest(page=page, asset=target):
                    self.assertTrue(
                        os.path.exists(os.path.join(REPO, target)),
                        f"{page} references {target}, which does not exist")


class TestNoDeadControls(unittest.TestCase):
    """Every id the markup exposes must be used by the script, and every id
    the script reaches for must exist in the markup."""

    def page_ids(self, page: str) -> set[str]:
        return set(re.findall(r'\bid="([A-Za-z][\w-]*)"', read(page)))

    def script_ids(self, script: str) -> set[str]:
        text = read(script)
        return set(re.findall(r"""\$\(['"]([\w-]+)['"]\)""", text)) | \
            set(re.findall(r"""getElementById\(['"]([\w-]+)['"]\)""", text))

    def test_control_room_script_only_reaches_for_ids_that_exist(self):
        missing = self.script_ids("assets/js/desktop/control-room.js") - \
            self.page_ids("control-room.html")
        self.assertEqual(missing, set(),
                         f"control-room.js reaches for ids that are not in the "
                         f"page: {sorted(missing)}")

    def test_setup_script_only_reaches_for_ids_that_exist(self):
        missing = self.script_ids("assets/js/desktop/setup.js") - \
            self.page_ids("setup.html")
        self.assertEqual(missing, set(),
                         f"setup.js reaches for ids that are not in the page: "
                         f"{sorted(missing)}")

    def test_every_interactive_element_with_an_id_is_wired(self):
        """A button with an id that nothing listens to is a dead control."""
        for page, script in (("control-room.html",
                              "assets/js/desktop/control-room.js"),
                             ("setup.html", "assets/js/desktop/setup.js")):
            markup, code = read(page), read(script)
            for match in re.finditer(
                    r'<(button|input|select)\b[^>]*\bid="([\w-]+)"', markup):
                tag, element_id = match.group(1), match.group(2)
                if tag in ("input", "select"):
                    continue      # read on submit, not necessarily by id here
                with self.subTest(page=page, button=element_id):
                    self.assertIn(
                        element_id, code,
                        f"{page}: <button id=\"{element_id}\"> is never "
                        f"referenced in {os.path.basename(script)} — a button "
                        f"that does nothing")

    def test_every_nav_target_has_a_view(self):
        markup = read("control-room.html")
        nav = set(re.findall(r'<button data-view="([\w-]+)"', markup))
        views = set(re.findall(r'class="view[^"]*" data-view="([\w-]+)"', markup))
        self.assertEqual(nav - views, set(),
                         f"nav entries with no view: {sorted(nav - views)}")
        self.assertEqual(views - nav, set(),
                         f"views with no way to reach them: "
                         f"{sorted(views - nav)}")

    def test_the_wizard_steps_match_its_crumbs(self):
        markup = read("setup.html")
        crumbs = re.findall(r'<li data-step="([\w-]+)"', markup)
        steps = re.findall(r'class="wizstep[^"]*" data-step="([\w-]+)"', markup)
        self.assertEqual(crumbs, steps,
                         "the wizard's progress crumbs and its steps disagree")
        code = read("assets/js/desktop/setup.js")
        declared = re.search(r"const STEPS = \[([^\]]+)\]", code)
        self.assertIsNotNone(declared)
        in_code = re.findall(r"'([\w-]+)'", declared.group(1))
        self.assertEqual(in_code, steps,
                         "setup.js walks a different set of steps than the "
                         "page renders")


class TestEveryApiCallExists(unittest.TestCase):
    """The other half of "no dead buttons": the routes must be real."""

    def called_routes(self) -> set[str]:
        """Route names the pages ask for.

        Matching only `D.get('x')` misses routes chosen by an expression —
        the worker toggle passes `running ? 'worker/stop' : 'worker/start'`.
        So every quoted route-shaped literal in the desktop scripts counts,
        which is the conservative direction: a false positive here would name
        a route that must exist, never hide one that does not.
        """
        routes = set()
        for script in SCRIPTS:
            text = read(script)
            # The first argument of each get()/post() call, whatever shape it
            # is: a literal, a ternary, a concatenation.
            for arg in re.findall(r"\b(?:D\.)?(?:get|post)\(([^,)]+)", text):
                for literal in re.findall(r"""['"]([\w][\w/\-]*)""", arg):
                    routes.add(literal.strip("/"))
        return {r for r in routes if r}

    def handled_routes(self) -> set[str]:
        """Route names the dispatcher answers, read from its source.

        Deliberately static. Calling each route to see whether it 404s would
        mean this test starts a readiness run, contacts the update service and
        queues an intake every time it runs.
        """
        source = read("desktop/owcs_desktop/webapi.py")
        return set(re.findall(r'route == "([\w/\-]+)"', source))

    def test_every_route_the_ui_calls_is_routed_in_python(self):
        unknown = sorted(self.called_routes() - self.handled_routes())
        self.assertEqual(unknown, [],
                         f"the UI calls routes the application does not "
                         f"handle: {unknown}")

    def test_the_ui_actually_calls_the_routes_that_exist(self):
        """The other direction: a route nothing calls is either dead code or
        a feature that was built and never wired to a control."""
        # `intake/classify` is called with a query string, and the setup
        # wizard reaches some routes the control room does not.
        unused = sorted(self.handled_routes() - self.called_routes())
        self.assertEqual(unused, [],
                         f"the application handles routes no page calls: "
                         f"{unused}")

    def test_the_api_helper_targets_the_local_application_only(self):
        text = read("assets/js/desktop/api.js")
        self.assertIn("const BASE = '/api/desktop/'", text)
        self.assertNotIn("http://", text.replace("http://127.0.0.1", ""))
        self.assertNotIn("https://", text)

    def test_an_unreachable_application_is_reported_to_the_user(self):
        """When the background app is closed, every view must say so rather
        than render stale data as if it were live."""
        text = read("assets/js/desktop/api.js")
        self.assertIn("offline", text)
        self.assertIn("not responding", text)


class TestSelfContained(unittest.TestCase):
    def test_no_external_script_or_style_is_required(self):
        """The control room has to work on a machine with no internet."""
        for page in PAGES:
            text = read(page)
            for match in re.finditer(r'<(script|link)[^>]*(?:src|href)="(https?://[^"]+)"',
                                     text):
                self.fail(f"{page} loads {match.group(2)} from the network; "
                          f"the control room must work offline")

    def test_no_inline_event_handlers(self):
        """onclick="..." in markup is unreviewable and breaks under a strict
        CSP; everything is wired in the script instead."""
        for page in PAGES:
            text = read(page)
            self.assertNotRegex(text, r'\son(click|change|submit)="',
                                f"{page} uses an inline event handler")


class TestNotPartOfThePublicSite(unittest.TestCase):
    """GitHub Pages serves the whole repository. These pages must not be
    indexed and must not be reachable from a public page."""

    def test_both_pages_are_noindex(self):
        for page in PAGES:
            self.assertIn('name="robots" content="noindex"', read(page),
                          f"{page} is not marked noindex")

    def test_no_public_page_links_to_the_control_room(self):
        public = [f for f in sorted(os.listdir(REPO))
                  if f.endswith(".html") and f not in PAGES]
        offenders = []
        for page in public:
            text = read(page)
            for target in PAGES:
                if re.search(rf'href="{re.escape(target)}"', text):
                    offenders.append(f"{page} -> {target}")
        self.assertEqual(offenders, [],
                         f"public pages link to the local application: "
                         f"{offenders}")

    def test_no_public_page_loads_the_desktop_scripts(self):
        public = [f for f in sorted(os.listdir(REPO))
                  if f.endswith(".html") and f not in PAGES]
        offenders = []
        for page in public:
            if "assets/js/desktop/" in read(page):
                offenders.append(page)
        self.assertEqual(offenders, [])


class TestAttributionIsAskedForInTheUi(unittest.TestCase):
    """The API refuses anonymous decisions; the UI must collect the name
    rather than let the user hit a wall."""

    def test_the_review_page_asks_for_a_name(self):
        self.assertIn('id="reviewer"', read("control-room.html"))
        self.assertIn("reviewer", read("assets/js/desktop/control-room.js"))

    def test_the_calibration_editor_asks_for_a_name(self):
        self.assertIn('id="calEditor"', read("control-room.html"))

    def test_the_intake_asks_for_a_name(self):
        self.assertIn('id="intakeBy"', read("control-room.html"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
