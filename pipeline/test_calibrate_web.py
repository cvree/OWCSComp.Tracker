#!/usr/bin/env python3
"""
test_calibrate_web.py — the browser calibration wizard.

calibrate.html is the only page on the public site that DOES something rather
than displaying something, and it is the one a newcomer is most likely to meet
first. It must therefore be held to the same standard as the pipeline:

  * it works with no server — GitHub Pages serves it as static files, so a
    reference to an API route or an external host would break it silently for
    every visitor while working perfectly in local development;
  * it never claims production provenance it does not have. `hud_probe` is
    what marks a layout production-calibrated; a browser-built layout must
    carry `browser_probe` instead, and the import path must strip a
    `hud_probe` even if the file it is handed contains one;
  * every control is wired, and every route it calls exists;
  * the layout it emits is in the exact shape detect.py and layout_registry
    read.

The detection ENGINE itself is verified against real broadcast frames by
`test_calibrate_engine.py`, which needs a browser; this suite is the static
half and runs everywhere.

Run: python3 pipeline/test_calibrate_web.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "desktop"))

import layout_registry as lr  # noqa: E402
from owcs_desktop import paths, webapi  # noqa: E402

PAGE = os.path.join(REPO, "calibrate.html")
ENGINE = os.path.join(REPO, "assets", "js", "calibrate", "engine.js")
WIZARD = os.path.join(REPO, "assets", "js", "calibrate", "wizard.js")
STYLE = os.path.join(REPO, "assets", "css", "calibrate.css")


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


class TestFilesExist(unittest.TestCase):
    def test_the_page_and_its_assets_are_present(self):
        for path in (PAGE, ENGINE, WIZARD, STYLE):
            with self.subTest(file=os.path.basename(path)):
                self.assertTrue(os.path.exists(path), f"{path} is missing")

    def test_every_referenced_asset_resolves(self):
        html = read(PAGE)
        for rel in re.findall(r'(?:href|src)="([^"#:]+)"', html):
            target = rel.split("?")[0]
            if target.startswith(("http", "//", "mailto")):
                continue
            with self.subTest(asset=target):
                self.assertTrue(os.path.exists(os.path.join(REPO, target)),
                                f"calibrate.html references {target}, which "
                                f"does not exist")


class TestItWorksWithoutAServer(unittest.TestCase):
    """The whole point: this runs on GitHub Pages, where there is no backend."""

    def test_the_page_calls_no_api(self):
        for path in (PAGE, WIZARD, ENGINE):
            with self.subTest(file=os.path.basename(path)):
                text = read(path)
                self.assertNotIn("/api/", text,
                                 f"{os.path.basename(path)} calls an API — it "
                                 f"would break on the static site")
                self.assertNotIn("fetch(", text,
                                 f"{os.path.basename(path)} fetches something "
                                 f"at runtime")

    def test_nothing_is_loaded_from_another_host(self):
        html = read(PAGE)
        external = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
        self.assertEqual(external, [],
                         f"calibrate.html loads {external} from the network")

    def test_the_video_never_leaves_the_browser(self):
        """No upload path may exist at all — the page promises this in so
        many words, so the promise is tested rather than trusted."""
        wizard = read(WIZARD)
        for forbidden in ("XMLHttpRequest", "FormData", "navigator.sendBeacon",
                          "WebSocket"):
            self.assertNotIn(forbidden, wizard,
                             f"wizard.js uses {forbidden} — the page claims "
                             f"nothing is uploaded")

    def test_the_page_says_so_where_the_user_can_see_it(self):
        html = read(PAGE)
        self.assertRegex(html, r"never uploaded|inside this browser",
                         "the page does not tell the user their video stays "
                         "local, which is the main thing they will wonder")


class TestNoDeadControls(unittest.TestCase):
    def test_every_button_is_wired(self):
        html, wizard = read(PAGE), read(WIZARD)
        for tag, element_id in re.findall(
                r'<(button|input)\b[^>]*\bid="([\w-]+)"', html):
            with self.subTest(control=element_id):
                self.assertIn(element_id, wizard,
                              f'<{tag} id="{element_id}"> is never referenced '
                              f'in wizard.js — a control that does nothing')

    def test_the_script_only_reaches_for_ids_that_exist(self):
        html, wizard = read(PAGE), read(WIZARD)
        present = set(re.findall(r'\bid="([\w-]+)"', html))
        wanted = set(re.findall(r"""\$\(['"]([\w-]+)['"]\)""", wizard))
        self.assertEqual(wanted - present, set(),
                         f"wizard.js reaches for missing ids: "
                         f"{sorted(wanted - present)}")

    def test_the_steps_match_between_markup_and_script(self):
        html, wizard = read(PAGE), read(WIZARD)
        crumbs = re.findall(r'<li data-step="([\w-]+)"', html)
        panels = re.findall(r'class="cal-step[^"]*" data-step="([\w-]+)"', html)
        declared = re.search(r"const STEPS = \[([^\]]+)\]", wizard)
        self.assertIsNotNone(declared)
        in_code = re.findall(r"'([\w-]+)'", declared.group(1))
        self.assertEqual(crumbs, panels, "progress crumbs and panels disagree")
        self.assertEqual(in_code, panels, "wizard.js walks different steps "
                                          "than the page renders")

    def test_no_inline_event_handlers(self):
        self.assertNotRegex(read(PAGE), r'\son(click|change|submit)="')

    def test_the_page_is_in_english(self):
        """A stray non-English string shipped in a button once; this is the
        guard. The wizard has no localisation, so any Cyrillic or CJK
        character in the markup is a mistake, not a translation."""
        stray = re.findall(r"[Ѐ-ӿ一-鿿぀-ヿ]+",
                           read(PAGE))
        self.assertEqual(stray, [], f"non-English text in calibrate.html: {stray}")


class TestEngineContract(unittest.TestCase):
    """Static checks on the engine. Its ACCURACY is measured against real
    broadcast frames in test_calibrate_engine.py."""

    def test_it_exports_what_the_wizard_uses(self):
        engine, wizard = read(ENGINE), read(WIZARD)
        for fn in re.findall(r"\bE\.(\w+)\(", wizard):
            with self.subTest(export=fn):
                self.assertRegex(engine, rf"\b{fn}[,\s]*[,}}]|{fn}(?=,)",
                                 f"wizard.js calls E.{fn}() but engine.js does "
                                 f"not export it")

    def test_the_thresholds_match_the_pipeline_calibrator(self):
        """The browser and the pipeline must describe the same HUD. If these
        drift, a layout built in one and used by the other is subtly wrong in
        a way nothing would report."""
        engine = read(ENGINE)
        py = read(os.path.join(HERE, "calibrate_source.py"))

        def py_value(name):
            m = re.search(rf"^{name} = ([\d.]+)", py, re.M)
            return m.group(1) if m else None

        for js_name, py_name in (("SAT_MIN", "SAT_MIN"),
                                 ("VAL_MIN", "VAL_MIN"),
                                 ("CONFIDENCE_FLOOR", "CONFIDENCE_FLOOR")):
            with self.subTest(constant=js_name):
                js = re.search(rf"{js_name} = ([\d.]+)", engine)
                self.assertIsNotNone(js, f"{js_name} not found in engine.js")
                self.assertEqual(float(js.group(1)), float(py_value(py_name)),
                                 f"{js_name} differs between the browser "
                                 f"engine and calibrate_source.py")

    def test_the_band_matches_the_pipeline(self):
        engine = read(ENGINE)
        py = read(os.path.join(HERE, "calibrate_source.py"))
        m = re.search(r"BAND_TOP_FRAC, BAND_BOT_FRAC = ([\d.]+), ([\d.]+)", py)
        self.assertIsNotNone(m)
        for name, expected in (("BAND_TOP_FRAC", m.group(1)),
                               ("BAND_BOT_FRAC", m.group(2))):
            js = re.search(rf"{name} = ([\d.]+)", engine)
            self.assertEqual(float(js.group(1)), float(expected),
                             f"{name} differs from the pipeline")


class TestProvenanceIsNotOverclaimed(unittest.TestCase):
    """`hud_probe` is what layout_registry treats as production-calibrated.
    A browser-built layout must never carry one."""

    def test_the_engine_emits_browser_probe_not_hud_probe(self):
        engine = read(ENGINE)
        self.assertIn("browser_probe", engine)
        self.assertIn("calibration_source: 'browser'", engine)
        emitted = re.search(r"function toLayout[\s\S]{0,4000}?\n  }", engine)
        self.assertIsNotNone(emitted)
        self.assertNotIn("hud_probe:", emitted.group(0),
                         "the browser wizard emits a hud_probe, which would "
                         "make its layout count as production-calibrated")

    def test_a_browser_layout_is_not_treated_as_calibrated(self):
        """The decisive check, against the real registry."""
        layout = {
            "frame_width": 1280, "frame_height": 720,
            "slots_a": [[60, 78, 35, 35]] * 5,
            "slots_b": [[929, 78, 35, 35]] * 5,
            "calibration_source": "browser",
            "browser_probe": {"version": "browser-calib-v1", "confidence": 0.9},
        }
        self.assertFalse(lr.is_calibrated(layout),
                         "a browser-built layout is being treated as "
                         "production-calibrated")

    def test_the_wizard_tells_the_user_this(self):
        html = read(PAGE)
        self.assertRegex(html, r"browser-calibrated|browser calibrated",
                         "the wizard does not tell the user its layout is "
                         "held to a lower standard")
        self.assertIn("review", html)


class TestImportPath(unittest.TestCase):
    """The desktop app's 'Import a layout' button, which the wizard's final
    screen tells the user to press."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="owcs-cal-import-")
        self._old = os.environ.get(paths.HOME_ENV)
        os.environ[paths.HOME_ENV] = self._tmp.name
        paths.ensure_layout()

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop(paths.HOME_ENV, None)
        else:
            os.environ[paths.HOME_ENV] = self._old
        self._tmp.cleanup()
        for name in ("wizard-test-layout", "wizard-strip-test"):
            stale = os.path.join(REPO, "layouts", f"{name}.json")
            if os.path.exists(stale):
                os.unlink(stale)

    def good_layout(self):
        return {
            "frame_width": 1280, "frame_height": 720,
            "slots_a": [[60 + i * 70, 78, 35, 35] for i in range(5)],
            "slots_b": [[929 + i * 70, 78, 35, 35] for i in range(5)],
            "match_threshold": 0.62,
            "calibration_source": "browser",
            "browser_probe": {"confidence": 0.71, "frames_used": 6},
        }

    def test_the_button_the_wizard_names_actually_exists(self):
        """The wizard's last screen says "press Import a layout". If that
        control is not there, the instruction is a dead end."""
        room = read(os.path.join(REPO, "control-room.html"))
        self.assertIn('id="importLayout"', room)
        self.assertIn("Import a layout", room)
        js = read(os.path.join(REPO, "assets", "js", "desktop", "control-room.js"))
        self.assertIn("calibration/import", js)

    def test_an_anonymous_import_is_refused(self):
        result = webapi.calibration_import(self.good_layout(),
                                           name="wizard-test-layout",
                                           importer="")
        self.assertFalse(result["ok"])
        self.assertIn("name is required", result["error"])

    def test_a_non_layout_is_refused(self):
        for bad in (None, [], "hello", {"nope": 1}):
            with self.subTest(value=bad):
                result = webapi.calibration_import(
                    bad, name="wizard-test-layout", importer="Tester")
                self.assertFalse(result["ok"])

    def test_wrong_slot_counts_are_refused(self):
        layout = self.good_layout()
        layout["slots_a"] = layout["slots_a"][:3]
        result = webapi.calibration_import(layout, name="wizard-test-layout",
                                           importer="Tester")
        self.assertFalse(result["ok"])
        self.assertIn("exactly 5", result["error"])

    def test_a_slot_outside_the_frame_is_refused(self):
        layout = self.good_layout()
        layout["slots_b"][4] = [1270, 78, 35, 35]
        result = webapi.calibration_import(layout, name="wizard-test-layout",
                                           importer="Tester")
        self.assertFalse(result["ok"])
        self.assertIn("outside the frame", result["error"])

    def test_a_name_cannot_escape_the_layouts_directory(self):
        for evil in ("../../etc/passwd", "a/b", "..\\win"):
            with self.subTest(name=evil):
                result = webapi.calibration_import(
                    self.good_layout(), name=evil, importer="Tester")
                self.assertFalse(result["ok"])

    def test_a_good_layout_imports_and_is_readable_by_the_registry(self):
        result = webapi.calibration_import(
            self.good_layout(), name="wizard-test-layout", importer="Tester")
        self.assertTrue(result["ok"], result)
        path = os.path.join(REPO, "layouts", "wizard-test-layout.json")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as f:
            written = json.load(f)
        self.assertEqual(written["calibration_source"], "browser-import")
        self.assertEqual(written["imported"]["by"], "Tester")
        # Readable by the registry, and NOT production-calibrated.
        self.assertFalse(lr.is_calibrated(written))
        described = lr.describe(path)
        self.assertFalse(described["calibrated"])
        self.assertTrue(described["starter"])

    def test_an_import_may_not_smuggle_in_a_hud_probe(self):
        """A file could claim production provenance simply by including the
        key. The importer strips it rather than trusting the file."""
        layout = self.good_layout()
        layout["hud_probe"] = {"chips_a": [[1, 2, 3, 4]],
                               "chips_b": [[5, 6, 7, 8]]}
        self.assertTrue(lr.is_calibrated(layout), "test setup is wrong")
        result = webapi.calibration_import(layout, name="wizard-strip-test",
                                           importer="Tester")
        self.assertTrue(result["ok"], result)
        path = os.path.join(REPO, "layouts", "wizard-strip-test.json")
        with open(path, encoding="utf-8") as f:
            written = json.load(f)
        self.assertNotIn("hud_probe", written,
                         "an imported layout kept its hud_probe and now counts "
                         "as production-calibrated")
        self.assertFalse(lr.is_calibrated(written))

    def test_importing_over_an_existing_layout_is_refused(self):
        webapi.calibration_import(self.good_layout(),
                                  name="wizard-test-layout", importer="Tester")
        again = webapi.calibration_import(self.good_layout(),
                                          name="wizard-test-layout",
                                          importer="Tester")
        self.assertFalse(again["ok"])
        self.assertIn("already exists", again["error"])


class TestDiscoverability(unittest.TestCase):
    def test_the_public_site_links_to_the_wizard(self):
        """A calibration tool nobody can find is not a feature."""
        self.assertIn("calibrate.html", read(os.path.join(REPO, "index.html")),
                      "the front page does not link to the calibration wizard")

    def test_the_wizard_is_indexable(self):
        """Unlike the control room, this page is FOR the public, so it must
        not carry the noindex the operator surfaces do."""
        self.assertNotIn('content="noindex"', read(PAGE))



class TestAnyoneCanContribute(unittest.TestCase):
    """Contribution is deliberately open. A calibration for a broadcast
    nobody has covered is the most useful thing anyone can send, and
    gatekeeping it would mean fewer broadcasts get read.

    Open does NOT mean trusted: what arrives still carries `browser_probe`
    rather than `hud_probe`, so it cannot enter production without review.
    That separation is what makes it safe to let anyone submit.
    """

    def test_the_wizard_offers_a_share_path(self):
        html = read(PAGE)
        self.assertIn('id="share"', html)
        self.assertIn("anyone may send one", html)

    def test_sharing_needs_no_account_backend_or_approval(self):
        """The public site is static. A share button that implied an upload
        endpoint would be a fake button."""
        wizard = read(WIZARD)
        self.assertIn("issues/new", wizard,
                      "sharing does not go anywhere a static site can reach")
        self.assertNotIn("fetch(", wizard)

    def test_the_submission_carries_the_layout_and_its_provenance(self):
        wizard = read(WIZARD)
        self.assertIn("browser_probe", wizard)
        self.assertIn("JSON.stringify(layout", wizard)
        self.assertIn("review", wizard,
                      "the submission does not say the layout needs review")

    def test_an_oversized_submission_is_refused_not_truncated(self):
        """A silently cut-off URL would submit half a layout."""
        wizard = read(WIZARD)
        self.assertIn("url.length >", wizard)
        self.assertIn("too large", wizard)

    def test_a_name_is_optional_for_sharing_but_offered(self):
        html = read(PAGE)
        self.assertIn('id="shareBy"', html)
        self.assertIn("optional", html)


class TestIdentityIsRememberedNotWeakened(unittest.TestCase):
    """Attribution is asked for once instead of every time. It is still
    required everywhere it was required before."""

    IDENTITY = os.path.join(REPO, "assets", "js", "identity.js")

    def test_the_helper_exists_and_is_loaded_where_names_are_collected(self):
        self.assertTrue(os.path.exists(self.IDENTITY))
        for page in ("calibrate.html", "control-room.html", "setup.html"):
            with self.subTest(page=page):
                self.assertIn("assets/js/identity.js",
                              read(os.path.join(REPO, page)),
                              f"{page} collects a name but does not remember it")

    def test_every_name_field_is_marked_for_binding(self):
        room = read(os.path.join(REPO, "control-room.html"))
        for field in ("reviewer", "calEditor", "intakeBy", "importBy"):
            with self.subTest(field=field):
                self.assertRegex(
                    room, rf'id="{field}" data-identity',
                    f"{field} is not bound to the remembered identity")

    def test_it_stores_a_name_and_nothing_else(self):
        """A label, not a credential. Nothing is authenticated by it, so it
        must never grow into somewhere secrets get kept — this checks what
        the module DOES, not what its prose says about itself."""
        source = read(self.IDENTITY)
        stored = re.findall(r"setItem\(([^,]+),", source)
        self.assertEqual(
            [s.strip() for s in stored], ["KEY"],
            f"identity.js writes something other than the name: {stored}")
        self.assertEqual(re.findall(r"const KEY = '([^']+)'", source),
                         ["owcs.identity.name"])
        for forbidden in ("password", "secret", "apikey", "api_key", "bearer"):
            with self.subTest(term=forbidden):
                self.assertNotIn(forbidden, source.lower(),
                                 f"identity.js mentions {forbidden}; it holds "
                                 f"a display name and must hold nothing else")

    def test_storage_being_unavailable_does_not_break_the_page(self):
        """Private browsing throws on localStorage access."""
        source = read(self.IDENTITY)
        self.assertGreaterEqual(source.count("catch"), 2,
                                "localStorage access is not guarded")

    def test_the_server_still_requires_attribution(self):
        """The decisive check: remembering a name client-side must not have
        removed the server-side requirement."""
        result = webapi.review_decide(kind="stint", item_id=1,
                                      decision="approve", reviewer="")
        self.assertFalse(result["ok"])
        self.assertIn("reviewer name", result["error"])
        self.assertFalse(webapi.calibration_save(
            "owcs-demo", [{"id": "slots_a/0", "rect": [1, 1, 5, 5]}],
            editor="")["ok"])
        self.assertFalse(webapi.calibration_import(
            {"frame_width": 1, "frame_height": 1, "slots_a": [], "slots_b": []},
            name="x", importer="")["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
