#!/usr/bin/env python3
"""
test_desktop_api.py — the control room's API, the intake router, health,
repair and updates.

This is the surface a browser talks to. The properties worth locking down are
the ones a screenshot cannot show:

  * no route, on any path including error paths, can return a stored API key;
  * a route that mutates something records who did it, and records
    `anonymous` rather than inventing a name when nobody signed it;
  * a layout name is never used to reach outside the layouts directory;
  * an update is refused unless its published checksum matches;
  * the intake box classifies every documented link shape correctly, and
    rejects the rest with a reason a person can act on;
  * health reports failure as failure and never as an unexplained pass.

Run: python3 pipeline/test_desktop_api.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "desktop"))

from owcs_desktop import (credentials, health, intake, paths,  # noqa: E402
                          repair, updates, webapi)


class TempHome(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="owcs-test-api-")
        self._old = os.environ.get(paths.HOME_ENV)
        os.environ[paths.HOME_ENV] = self._tmp.name
        paths.ensure_layout()

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop(paths.HOME_ENV, None)
        else:
            os.environ[paths.HOME_ENV] = self._old
        self._tmp.cleanup()


# -------------------------------------------------------------- routing
class TestRouting(TempHome):
    READ_ROUTES = ("overview", "health", "settings", "credentials", "storage",
                   "backups", "repairs", "queue", "review", "calibration",
                   "publish", "task", "logs", "paths",
                   "heroes/coverage", "templates/review")

    def test_every_documented_get_route_answers(self):
        for route in self.READ_ROUTES:
            with self.subTest(route=route):
                result = webapi.handle_get("/api/desktop/" + route)
                self.assertIsNotNone(result, f"{route} is not routed")
                code, payload = result
                self.assertEqual(code, 200, f"{route} -> {code}: {payload}")
                self.assertIsInstance(payload, dict)

    def test_the_gap_plan_needs_a_layout_and_says_so(self):
        """A route that quietly answers for the wrong package is worse than
        one that refuses."""
        code, payload = webapi.handle_get("/api/desktop/heroes/gaps")
        self.assertEqual(code, 400)
        self.assertIn("layout", payload["error"])

        code, payload = webapi.handle_get(
            "/api/desktop/heroes/gaps", "layout=owcs_jksix_qwc")
        self.assertEqual(code, 200)
        self.assertTrue(payload["available"], payload.get("error"))
        self.assertEqual(payload["layoutId"], "owcs_jksix_qwc")
        # Every missing hero is either somewhere we have footage of, or
        # named as never-seen. Dropping one silently would hide a gap.
        self.assertEqual(
            len(payload["reachable"]) + len(payload["neverSeen"]),
            payload["rosterSize"] - payload["covered"])

    def test_coverage_never_reports_a_number_it_cannot_back(self):
        code, payload = webapi.handle_get("/api/desktop/heroes/coverage")
        self.assertEqual(code, 200)
        self.assertTrue(payload["available"], payload.get("error"))
        for layout in payload["layouts"]:
            self.assertLessEqual(layout["validated"], layout["covered"],
                                 f"{layout['layoutId']} reports more heroes "
                                 f"validated than it has templates for")
            self.assertLessEqual(layout["covered"], layout["rosterSize"])

    def test_a_non_desktop_path_is_not_claimed(self):
        """Returning None is what lets serve.py keep serving its own API."""
        self.assertIsNone(webapi.handle_get("/api/ping"))
        self.assertIsNone(webapi.handle_post("/api/run", {}))

    def test_an_unknown_desktop_route_is_404_not_a_crash(self):
        code, payload = webapi.handle_get("/api/desktop/does-not-exist")
        self.assertEqual(code, 404)
        self.assertFalse(payload["ok"])

    def test_an_unknown_post_route_is_404(self):
        code, _ = webapi.handle_post("/api/desktop/nope", {})
        self.assertEqual(code, 404)


# ---------------------------------------------------------- secret safety
class TestNoSecretEverLeaves(TempHome):
    SECRET = "ZZZZ-this-must-never-be-returned-YYYY"

    def setUp(self) -> None:
        super().setUp()
        credentials.CredentialVault().set("FACEIT_API_KEY", self.SECRET)

    def test_no_read_route_returns_the_value(self):
        for route in TestRouting.READ_ROUTES:
            with self.subTest(route=route):
                _code, payload = webapi.handle_get("/api/desktop/" + route)
                self.assertNotIn(self.SECRET, json.dumps(payload, default=str))

    def test_the_credentials_route_reports_presence_only(self):
        _code, payload = webapi.handle_get("/api/desktop/credentials")
        entry = next(c for c in payload["credentials"]
                     if c["name"] == "FACEIT_API_KEY")
        self.assertTrue(entry["present"])
        self.assertNotIn("value", entry)
        self.assertNotIn(self.SECRET, json.dumps(payload))

    def test_setting_a_credential_does_not_echo_it_back(self):
        code, payload = webapi.handle_post(
            "/api/desktop/credentials",
            {"name": "YOUTUBE_API_KEY", "value": "AAAA-echo-me-BBBB"})
        self.assertEqual(code, 200)
        self.assertNotIn("AAAA-echo-me-BBBB", json.dumps(payload))

    def test_key_shaped_text_is_masked_out_of_free_text(self):
        masked = webapi.mask("failed with token abcdefghijklmnopqrstuvwxyz012345")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz012345", masked)
        self.assertIn("[redacted]", masked)

    def test_an_unknown_credential_is_refused(self):
        code, payload = webapi.handle_post(
            "/api/desktop/credentials", {"name": "SSH_PRIVATE_KEY", "value": "x"})
        self.assertEqual(code, 400)
        self.assertFalse(payload["ok"])


# ------------------------------------------------------------- settings
class TestSettingsRoutes(TempHome):
    def test_valid_patch_applies(self):
        code, payload = webapi.handle_post(
            "/api/desktop/settings", {"settings": {"maxStorageGb": 25.0}})
        self.assertEqual(code, 200)
        self.assertEqual(payload["values"]["maxStorageGb"], 25.0)

    def test_invalid_patch_is_a_400_with_a_reason(self):
        code, payload = webapi.handle_post(
            "/api/desktop/settings", {"settings": {"maxStorageGb": "loads"}})
        self.assertEqual(code, 400)
        self.assertIn("maxStorageGb", payload["error"])

    def test_an_empty_patch_is_refused(self):
        code, _ = webapi.handle_post("/api/desktop/settings", {"settings": {}})
        self.assertEqual(code, 400)


# ---------------------------------------------------------- attribution
class TestAttributionIsRecordedNotDemanded(TempHome):
    """Every change still says who made it. None of them refuse to happen.

    While this is a prototype the owner has decided every visitor is trusted
    to edit correctly, so the name fields no longer gate anything. That is a
    change to the GATE, not to the RECORD: an unsigned action still writes
    its row, and writes `anonymous` in the column that was left blank.

    Refusing unsigned edits never protected anything — the field was free
    text that nothing verified, so anyone could type any name. Inventing a
    name to fill the column would have been the one genuinely harmful
    option, because it would put a false attribution into the audit trail.
    """

    def test_an_unsigned_action_records_anonymous_rather_than_a_blank(self):
        self.assertEqual(webapi.attribute(""), webapi.ANONYMOUS)
        self.assertEqual(webapi.attribute(None), webapi.ANONYMOUS)
        self.assertEqual(webapi.attribute("   "), webapi.ANONYMOUS)

    def test_a_name_that_is_given_is_kept_exactly(self):
        self.assertEqual(webapi.attribute("  Connor  "), "Connor")

    def test_no_name_is_ever_invented(self):
        """The only substitute for a missing name is one that says so."""
        self.assertIn("anon", webapi.ANONYMOUS.lower())

    def test_a_review_decision_without_a_name_is_not_refused_for_that(self):
        _code, payload = webapi.handle_post(
            "/api/desktop/review/decide",
            {"kind": "stint", "id": 1, "decision": "approve", "reviewer": ""})
        # It may still fail because there is no such stint in this empty
        # database — but never because nobody signed it.
        self.assertNotIn("name", str(payload.get("error", "")).lower())

    def test_the_decision_itself_is_still_validated(self):
        """Trusting the person is not the same as trusting the payload."""
        _code, payload = webapi.handle_post(
            "/api/desktop/review/decide",
            {"kind": "stint", "id": 1, "decision": "maybe", "reviewer": ""})
        self.assertFalse(payload["ok"])

    def test_an_unknown_decision_is_refused(self):
        _code, payload = webapi.handle_post(
            "/api/desktop/review/decide",
            {"kind": "stint", "id": 1, "decision": "maybe", "reviewer": "A"})
        self.assertFalse(payload["ok"])

    def test_an_unknown_review_kind_is_refused(self):
        _code, payload = webapi.handle_post(
            "/api/desktop/review/decide",
            {"kind": "wishes", "id": 1, "decision": "approve", "reviewer": "A"})
        self.assertFalse(payload["ok"])

    def test_a_layout_edit_without_a_name_is_not_refused_for_that(self):
        # Against a COPY of the layouts, never the committed ones. Now that
        # this edit is no longer refused it actually writes, and layouts are
        # written under app_root() — so without this the test rewrote
        # layouts/owcs-demo.json in the repository and broke two unrelated
        # computer-vision suites. It did exactly that once.
        import shutil
        sandbox = os.path.join(self._tmp.name, "app")
        os.makedirs(sandbox, exist_ok=True)
        shutil.copytree(os.path.join(REPO, "layouts"),
                        os.path.join(sandbox, "layouts"))
        old = os.environ.get("OWCS_APP_ROOT")
        os.environ["OWCS_APP_ROOT"] = sandbox
        try:
            _code, payload = webapi.handle_post(
                "/api/desktop/calibration/save",
                {"name": "owcs-demo",
                 "boxes": [{"id": "slots_a/0", "rect": [1, 2, 3, 4]}],
                 "editor": ""})
        finally:
            if old is None:
                os.environ.pop("OWCS_APP_ROOT", None)
            else:
                os.environ["OWCS_APP_ROOT"] = old
        self.assertNotIn("a name is required", str(payload.get("error", "")))

    def test_intake_without_a_name_is_accepted(self):
        """The gate that most obviously stood in a new user's way."""
        code, payload = webapi.handle_post(
            "/api/desktop/intake/submit",
            {"input": "https://www.youtube.com/watch?v=jkSiX___Qwc",
             "requestedBy": ""})
        self.assertNotEqual(code, 400, payload)
        self.assertNotIn("name is required", str(payload.get("error", "")))


# ---------------------------------------------------------- calibration
class TestCalibrationEditor(TempHome):
    def test_layouts_are_listed_with_honest_calibration_state(self):
        _code, payload = webapi.handle_get("/api/desktop/calibration")
        by_name = {l["name"]: l for l in payload["layouts"]}
        self.assertTrue(by_name["owcs_jksix_qwc"]["calibrated"])
        self.assertFalse(by_name["owcs_youtube_2026"]["calibrated"],
                         "a hand-guessed starter was presented as calibrated")

    def test_boxes_are_extracted_from_every_geometry_shape(self):
        """A layout spells geometry three ways: a bare rect, an object with a
        `rect`, and a list of those. Missing one silently hides HUD regions
        from the editor, which is how a user 'fixes' a layout and changes
        nothing."""
        _code, payload = webapi.handle_get(
            "/api/desktop/calibration", "name=owcs_jksix_qwc")
        kinds = {b["key"] for b in payload["boxes"]}
        self.assertIn("slots_a", kinds)       # list of bare rects
        self.assertIn("anchor", kinds)        # object with .rect
        self.assertIn("reject", kinds)        # list of objects with .rect
        self.assertEqual(
            len([b for b in payload["boxes"] if b["key"] == "slots_a"]), 5)
        for box in payload["boxes"]:
            self.assertEqual(len(box["rect"]), 4)
            self.assertGreater(box["rect"][2], 0)

    def test_a_layout_name_cannot_escape_the_layouts_directory(self):
        for evil in ("../../etc/passwd", "..\\..\\windows\\win.ini",
                     "/etc/passwd", "a/b"):
            with self.subTest(name=evil):
                code, payload = webapi.handle_get(
                    "/api/desktop/calibration", "name=" + evil)
                self.assertEqual(code, 400, f"{evil} was not refused")
                self.assertFalse(payload["ok"])

    def test_a_bad_rectangle_is_refused_before_anything_is_written(self):
        path = os.path.join(paths.app_root(), "layouts", "owcs-demo.json")
        with open(path, "rb") as f:
            before = f.read()
        result = webapi.calibration_save(
            "owcs-demo", [{"id": "slots_a/0", "rect": [10, 10, 0, 5]}],
            editor="Tester")
        self.assertFalse(result["ok"])
        with open(path, "rb") as f:
            self.assertEqual(f.read(), before, "a rejected save still wrote")

    def test_an_unknown_box_id_is_refused(self):
        result = webapi.calibration_save(
            "owcs-demo", [{"id": "made_up/9", "rect": [1, 1, 5, 5]}],
            editor="Tester")
        self.assertFalse(result["ok"])
        self.assertIn("not a box", result["error"])

    def test_saving_unchanged_boxes_is_a_no_op(self):
        _code, payload = webapi.handle_get("/api/desktop/calibration",
                                           "name=owcs-demo")
        boxes = [{"id": b["id"], "rect": b["rect"]} for b in payload["boxes"]]
        result = webapi.calibration_save("owcs-demo", boxes, editor="Tester")
        self.assertTrue(result["ok"])
        self.assertEqual(result["changed"], [])


# --------------------------------------------------------------- health
class TestHealth(TempHome):
    def test_checks_have_the_shape_the_ui_renders(self):
        report = health.run_checks()
        self.assertIn(report["ok"], (True, False))
        for check in report["checks"]:
            self.assertIn(check["status"], (health.OK, health.WARN, health.FAIL))
            self.assertTrue(check["label"])
            self.assertTrue(check["detail"])
            if "repair" in check and check["repair"].startswith("repair."):
                self.assertIn(check["repair"], repair.ACTIONS,
                              f"{check['id']} offers a repair action that does "
                              f"not exist — a button that cannot work")

    def test_a_failure_blocks_processing(self):
        report = health.run_checks()
        failing = [c["id"] for c in report["checks"] if c["status"] == health.FAIL]
        self.assertEqual(bool(failing), not report["canProcess"])
        self.assertEqual(sorted(failing), sorted(report["blocking"]))

    def test_binaries_resolve_or_are_reported_missing(self):
        for name in health.REQUIRED_BINARIES:
            found = health.resolve_binary(name)
            if found is not None:
                self.assertTrue(os.path.isfile(found))

    def test_readiness_reports_a_skip_as_a_skip_not_a_pass(self):
        """A machine where the suites cannot run is NOT a ready machine."""
        class FakeRunner:
            @staticmethod
            def run(cmd, **kwargs):
                class R:
                    returncode = 0
                    stdout = "OK (skipped=2)\nSKIP no ffmpeg"
                    stderr = ""
                return R()

        report = health.run_readiness_test(runner=FakeRunner)
        self.assertFalse(report["ok"],
                         "an all-skipped readiness run reported ready")
        self.assertEqual(report["passed"], 0)

    def test_readiness_reports_a_failure_as_a_failure(self):
        class FakeRunner:
            @staticmethod
            def run(cmd, **kwargs):
                class R:
                    returncode = 1
                    stdout = "3 FAILURES"
                    stderr = ""
                return R()

        report = health.run_readiness_test(runner=FakeRunner)
        self.assertFalse(report["ok"])
        self.assertGreaterEqual(report["failed"], 1)

    def test_readiness_passes_only_on_real_passes(self):
        class FakeRunner:
            @staticmethod
            def run(cmd, **kwargs):
                class R:
                    returncode = 0
                    stdout = "ALL PASS"
                    stderr = ""
                return R()

        report = health.run_readiness_test(runner=FakeRunner)
        self.assertTrue(report["ok"])
        self.assertEqual(report["failed"], 0)


# --------------------------------------------------------------- repair
class TestRepair(TempHome):
    def test_every_health_repair_id_has_an_action(self):
        offered = {c.get("repair") for c in health.run_checks()["checks"]}
        for action_id in offered:
            if action_id and action_id.startswith("repair."):
                self.assertIn(action_id, repair.ACTIONS)

    def test_every_action_is_described_for_the_ui(self):
        for entry in repair.list_actions():
            self.assertTrue(entry["label"])
            self.assertTrue(entry["help"])

    def test_an_unknown_action_is_refused_not_guessed(self):
        result = repair.run("repair.format-c-drive")
        self.assertFalse(result["ok"])
        self.assertIn("unknown repair action", result["detail"])

    def test_databases_repair_creates_both(self):
        result = repair.run("repair.databases")
        self.assertTrue(result["ok"], result)
        self.assertTrue(os.path.exists(paths.automation_db()))
        self.assertTrue(os.path.exists(paths.content_db()))

    def test_storage_repair_never_removes_the_audit_trail(self):
        marker = os.path.join(paths.sub("evidence"), "crop.png")
        with open(marker, "wb") as f:
            f.write(b"evidence")
        result = repair.run("repair.storage")
        self.assertTrue(result["ok"], result)
        self.assertTrue(os.path.exists(marker),
                        "the storage repair deleted evidence")

    def test_a_crashing_action_is_a_reported_failure(self):
        original = repair.ACTIONS["repair.storage"]["fn"]

        def explode(**kwargs):
            raise RuntimeError("disk on fire")

        repair.ACTIONS["repair.storage"]["fn"] = explode
        try:
            result = repair.run("repair.storage")
        finally:
            repair.ACTIONS["repair.storage"]["fn"] = original
        self.assertFalse(result["ok"])
        self.assertIn("disk on fire", result["detail"])


# -------------------------------------------------------------- updates
class TestUpdates(TempHome):
    def test_version_comparison(self):
        self.assertTrue(updates.is_newer("1.1.0", "1.0.0"))
        self.assertTrue(updates.is_newer("v2.0.0", "1.9.9"))
        self.assertFalse(updates.is_newer("1.0.0", "1.0.0"))
        self.assertFalse(updates.is_newer("0.9.0", "1.0.0"))
        self.assertTrue(updates.is_newer("1.0.1", "1.0"))

    def test_a_network_failure_is_reported_not_raised(self):
        def boom(url):
            raise OSError("no route to host")

        result = updates.check_for_update(fetch=boom)
        self.assertFalse(result["ok"])
        self.assertFalse(result["available"])
        self.assertIn("could not reach", result["error"])

    def test_prereleases_are_excluded_from_the_stable_channel(self):
        releases = json.dumps([
            {"tag_name": "v2.0.0", "prerelease": True, "assets": []},
            {"tag_name": "v1.0.1", "prerelease": False, "assets": [
                {"name": "OWCSCompTracker-1.0.1-Setup.exe",
                 "browser_download_url": "https://example/x.exe", "size": 1}]},
        ]).encode()
        stable = updates.check_for_update(channel="stable", current="1.0.0",
                                          fetch=lambda u: releases)
        self.assertEqual(stable["latest"], "v1.0.1")
        pre = updates.check_for_update(channel="prerelease", current="1.0.0",
                                       fetch=lambda u: releases)
        self.assertEqual(pre["latest"], "v2.0.0")

    def test_an_unverifiable_download_is_deleted_not_kept(self):
        """No checksum published -> refuse. This is the whole point of the
        feature: never hand the user an unverified executable."""
        info = {"installer": {"url": "https://example/setup.exe",
                              "name": "OWCSCompTracker-9.9.9-Setup.exe"},
                "checksums": None}
        result = updates.download_update(
            info, dest_dir=paths.sub("updates"), fetch=lambda u: b"MZ payload")
        self.assertFalse(result["ok"])
        self.assertIn("refusing", result["error"])
        self.assertEqual(os.listdir(paths.sub("updates")), [])

    def test_a_checksum_mismatch_is_deleted_not_run(self):
        info = {"installer": {"url": "https://example/setup.exe",
                              "name": "OWCSCompTracker-9.9.9-Setup.exe"},
                "checksums": "https://example/SHA256SUMS"}

        def fetch(url):
            if url.endswith("SHA256SUMS"):
                return (("0" * 64) +
                        "  OWCSCompTracker-9.9.9-Setup.exe\n").encode()
            return b"a tampered payload"

        result = updates.download_update(info, dest_dir=paths.sub("updates"),
                                         fetch=fetch)
        self.assertFalse(result["ok"])
        self.assertIn("checksum", result["error"])
        self.assertEqual(os.listdir(paths.sub("updates")), [])

    def test_a_matching_checksum_is_kept(self):
        import hashlib
        payload = b"MZ a genuine installer"
        digest = hashlib.sha256(payload).hexdigest()
        name = "OWCSCompTracker-9.9.9-Setup.exe"
        info = {"installer": {"url": "https://example/setup.exe", "name": name},
                "checksums": "https://example/SHA256SUMS"}

        def fetch(url):
            if url.endswith("SHA256SUMS"):
                return f"{digest}  {name}\n".encode()
            return payload

        result = updates.download_update(info, dest_dir=paths.sub("updates"),
                                         fetch=fetch)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["verified"])
        self.assertTrue(os.path.exists(result["path"]))

    def test_checksum_parsing(self):
        parsed = updates.parse_checksums(
            f"{'a' * 64}  file-one.exe\nnot a checksum line\n"
            f"{'b' * 64} *file-two.exe\n")
        self.assertEqual(parsed, {"file-one.exe": "a" * 64,
                                  "file-two.exe": "b" * 64})


# --------------------------------------------------------------- intake
class TestIntakeClassification(unittest.TestCase):
    """One box has to understand everything a user might paste."""

    def assert_kind(self, text, kind, accepted=True):
        verdict = intake.classify(text)
        self.assertEqual(verdict["kind"], kind, f"{text!r} -> {verdict}")
        self.assertEqual(verdict["accepted"], accepted, f"{text!r} -> {verdict}")
        return verdict

    def test_youtube_video_shapes(self):
        for url in ("https://www.youtube.com/watch?v=jkSiX___Qwc",
                    "https://youtu.be/jkSiX___Qwc",
                    "https://youtu.be/jkSiX___Qwc?t=1795",
                    "https://www.youtube.com/live/jkSiX___Qwc",
                    "https://www.youtube.com/embed/jkSiX___Qwc",
                    "www.youtube.com/watch?v=jkSiX___Qwc",
                    "https://m.youtube.com/watch?v=jkSiX___Qwc"):
            with self.subTest(url=url):
                verdict = self.assert_kind(url, intake.KIND_YOUTUBE_VIDEO)
                self.assertEqual(verdict["videoId"], "jkSiX___Qwc")

    def test_a_playlist_url_is_a_playlist_but_a_watch_url_in_one_is_not(self):
        self.assert_kind("https://www.youtube.com/playlist?list=PLabcdefghijkl",
                         intake.KIND_YOUTUBE_PLAYLIST)
        verdict = self.assert_kind(
            "https://www.youtube.com/watch?v=jkSiX___Qwc&list=PLabcdefghijkl",
            intake.KIND_YOUTUBE_VIDEO)
        self.assertIn("playlist", verdict["detail"])

    def test_faceit_room_and_championship(self):
        uuid = "2b5ba1ef-1c8f-4b8e-9a3e-1234567890ab"
        room = self.assert_kind(
            f"https://www.faceit.com/en/ow2/room/1-{uuid}",
            intake.KIND_FACEIT_MATCH)
        self.assertEqual(room["matchId"], uuid)
        champ = self.assert_kind(
            f"https://www.faceit.com/en/championship/{uuid}/OWCS",
            intake.KIND_FACEIT_CHAMPIONSHIP)
        self.assertEqual(champ["championshipId"], uuid)

    def test_a_faceit_link_with_a_bad_id_is_refused_with_a_reason(self):
        verdict = intake.classify("https://www.faceit.com/en/ow2/room/1-nope")
        self.assertFalse(verdict["accepted"])
        self.assertIn("not a FACEIT id", verdict["reason"])

    def test_a_local_video_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "broadcast.mp4")
            with open(path, "wb") as f:
                f.write(b"\0" * 2048)
            verdict = self.assert_kind(path, intake.KIND_LOCAL_FILE)
            self.assertEqual(verdict["path"], path)
            self.assert_kind("file://" + path, intake.KIND_LOCAL_FILE)

    def test_a_missing_or_non_video_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "nope.mp4")
            self.assert_kind(missing, intake.KIND_LOCAL_FILE, accepted=False)
            doc = os.path.join(tmp, "notes.txt")
            with open(doc, "w", encoding="utf-8") as f:
                f.write("hello")
            verdict = self.assert_kind(doc, intake.KIND_LOCAL_FILE,
                                       accepted=False)
            self.assertIn("not a video format", verdict["reason"])

    def test_a_windows_path_is_not_mangled_on_other_platforms(self):
        """abspath() on a drive-letter path off Windows glues the working
        directory in front and reports a nonsense location back to the user."""
        verdict = intake.classify(r"D:\broadcasts\owcs.mp4")
        self.assertEqual(verdict["kind"], intake.KIND_LOCAL_FILE)
        self.assertEqual(verdict["path"], r"D:\broadcasts\owcs.mp4")

    def test_unsupported_sites_are_refused_by_name(self):
        verdict = intake.classify("https://www.twitch.tv/owcs")
        self.assertFalse(verdict["accepted"])
        self.assertIn("twitch.tv", verdict["reason"])

    def test_empty_input_explains_what_is_accepted(self):
        verdict = intake.classify("   ")
        self.assertFalse(verdict["accepted"])
        for word in ("YouTube", "FACEIT", "video file"):
            self.assertIn(word, verdict["reason"])

    def test_every_verdict_has_what_the_ui_renders(self):
        for text in ("https://www.youtube.com/watch?v=jkSiX___Qwc", "",
                     "https://twitch.tv/x", "https://www.faceit.com/en/x"):
            verdict = intake.classify(text)
            self.assertIn("kind", verdict)
            self.assertIn("label", verdict)
            self.assertIn("accepted", verdict)
            if not verdict["accepted"]:
                self.assertTrue(verdict.get("reason"),
                                f"{text!r} was refused with no reason")

    def test_submit_refuses_an_unclassifiable_paste_without_side_effects(self):
        result = intake.submit("https://example.com/video",
                               requested_by="Tester")
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"])

    def test_playlist_expansion_parses_a_flat_listing(self):
        class FakeRunner:
            @staticmethod
            def run(cmd, **kwargs):
                class R:
                    returncode = 0
                    stdout = json.dumps({"title": "OWCS 2026", "entries": [
                        {"id": "jkSiX___Qwc", "title": "Day 1", "duration": 100},
                        {"id": "not-an-id", "title": "junk"},
                        {"id": "8c105lnzlam", "title": "Day 2"}]})
                    stderr = ""
                return R()

        original = health.resolve_binary
        health.resolve_binary = lambda name: "/usr/bin/yt-dlp"
        try:
            result = intake.expand_playlist("PLabc", runner=FakeRunner)
        finally:
            health.resolve_binary = original
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2, "a malformed id was accepted")
        self.assertEqual(result["videos"][0]["videoId"], "jkSiX___Qwc")


if __name__ == "__main__":
    unittest.main(verbosity=2)
