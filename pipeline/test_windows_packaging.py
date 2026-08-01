#!/usr/bin/env python3
"""
test_windows_packaging.py — the installer is buildable and honest.

The Windows installer cannot be built or run on a Linux CI runner, so this
suite checks everything about it that CAN be checked anywhere, and the
`windows-app` workflow does the rest on a real windows-latest runner (build,
silent install, health checks, the end-to-end readiness test, uninstall).

What is verified here:

  * the payload the spec ships is complete, and is the SAME list the build
    and this test read — a file the build includes but the test does not know
    about is the packaging bug that only appears on a user's machine;
  * the committed icon is byte-identical to what its generator produces, and
    is a valid multi-resolution ICO;
  * the installer script does not need administrator rights, does not delete
    user data silently, and registers autostart under HKCU;
  * the version is consistent across the package, the exe's version resource
    and the installer;
  * the clean-machine workflow actually installs from the artifact rather
    than from a checkout — a "clean install test" that checks the repo out
    first proves nothing;
  * every entrypoint mode the installer and autostart reference exists.

Run: python3 pipeline/test_windows_packaging.py
"""
from __future__ import annotations

import os
import re
import struct
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PACKAGING = os.path.join(REPO, "packaging")
sys.path.insert(0, PACKAGING)
sys.path.insert(0, os.path.join(REPO, "desktop"))

import payload  # noqa: E402
import owcs_desktop  # noqa: E402


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


class TestPayload(unittest.TestCase):
    def test_every_required_entry_exists(self):
        missing = payload.missing_required(REPO)
        self.assertEqual(missing, [],
                         "the installer would ship without: " + ", ".join(missing))

    def test_the_things_detection_cannot_run_without_are_required(self):
        """layouts and templates are not optional. An installer that ships
        without them installs perfectly and can never identify a hero."""
        required = {rel for rel, req, _why in payload.PAYLOAD if req}
        for essential in ("layouts", "templates", "pipeline",
                          "desktop/owcs_desktop", "config"):
            self.assertIn(essential, required,
                          f"{essential} is not a required payload entry")

    def test_every_entry_explains_itself(self):
        for rel, _required, reason in payload.PAYLOAD:
            self.assertTrue(reason and len(reason) > 20,
                            f"{rel} has no explanation of why it ships")

    def test_collect_datas_includes_the_control_room_pages(self):
        datas = payload.collect_datas(REPO)
        shipped = {os.path.basename(src) for src, _dest in datas}
        for page in ("control-room.html", "setup.html", "index.html"):
            self.assertIn(page, shipped, f"{page} is not shipped")

    def test_collect_datas_refuses_a_build_missing_required_payload(self):
        import tempfile
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(FileNotFoundError):
                payload.collect_datas(empty)

    def test_the_runtime_modules_are_hidden_imports(self):
        """PyInstaller's static analysis cannot see modules the pipeline
        imports by name at runtime; missing one produces an exe that starts
        and then fails on the first job."""
        for module in ("cv2", "numpy", "yt_dlp", "sqlite3",
                       "owcs_desktop.supervisor", "owcs_desktop.webapi"):
            self.assertIn(module, payload.HIDDEN_IMPORTS)

    def test_heavy_unused_packages_are_excluded(self):
        for module in ("torch", "matplotlib", "scipy", "paddleocr"):
            self.assertIn(module, payload.EXCLUDES)


class TestIcon(unittest.TestCase):
    ICO = os.path.join(REPO, "desktop", "assets", "owcs.ico")
    PNG = os.path.join(REPO, "desktop", "assets", "owcs.png")

    def test_the_committed_icon_matches_its_generator(self):
        """The icon is generated, not drawn by hand. If the committed file
        drifts from the generator, the build and the repository disagree
        about what the application looks like."""
        result = subprocess.run(
            [sys.executable, os.path.join(REPO, "desktop", "build_icon.py"),
             "--check"], capture_output=True, text=True, cwd=REPO)
        self.assertEqual(result.returncode, 0,
                         result.stdout + result.stderr)

    def test_the_ico_is_a_valid_multi_resolution_icon(self):
        with open(self.ICO, "rb") as f:
            head = f.read(6)
        reserved, kind, count = struct.unpack("<HHH", head)
        self.assertEqual(reserved, 0)
        self.assertEqual(kind, 1, "not an ICO")
        self.assertGreaterEqual(count, 5,
                                "too few sizes; Windows scales badly without "
                                "16, 32 and 256 px entries")

    def test_a_small_size_is_present_for_the_tray(self):
        """The notification area draws at 16px. Without a real 16px entry
        Windows downscales 256px and the mark turns to mush."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "build_icon", os.path.join(REPO, "desktop", "build_icon.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertIn(16, module.SIZES)
        self.assertIn(256, module.SIZES)

    def test_the_png_exists_for_the_tray_library(self):
        self.assertTrue(os.path.exists(self.PNG))
        with open(self.PNG, "rb") as f:
            self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")


class TestInstallerScript(unittest.TestCase):
    ISS = os.path.join(PACKAGING, "installer.iss")

    def setUp(self) -> None:
        self.text = read(self.ISS)

    def test_no_administrator_rights_are_required(self):
        """A per-user install is the difference between one click and asking
        IT. Requiring admin also breaks the DPAPI credential vault, which is
        per-user by design."""
        self.assertIn("PrivilegesRequired=lowest", self.text)

    def test_autostart_is_registered_per_user_and_removed_on_uninstall(self):
        self.assertIn("Root: HKCU", self.text)
        self.assertIn(r"Software\Microsoft\Windows\CurrentVersion\Run", self.text)
        self.assertIn("uninsdeletevalue", self.text,
                      "the startup entry would survive uninstall")

    def test_user_data_is_never_deleted_without_asking(self):
        self.assertIn("MB_YESNO", self.text)
        self.assertIn("MB_DEFBUTTON2", self.text,
                      "the destructive option is the default button")
        deltree = self.text.count("DelTree")
        self.assertEqual(deltree, 1,
                         "more than one place deletes user data")

    def test_the_running_service_is_stopped_before_files_are_replaced(self):
        """A live supervisor holds ffmpeg.exe and the bundled Python open; an
        upgrade that did not stop it first would fail halfway through."""
        self.assertIn("--stop-service", self.text)
        self.assertIn("[UninstallRun]", self.text)

    def test_the_app_id_is_a_fixed_guid(self):
        match = re.search(r"AppId=\{\{([0-9A-Fa-f-]+)\}", self.text)
        self.assertIsNotNone(match, "no AppId — upgrades would install beside "
                                    "the previous version instead of replacing it")
        self.assertRegex(
            match.group(1),
            r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
            r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$",
            "the AppId is not a valid GUID; Inno Setup would reject it")

    def test_it_ships_the_whole_bundle(self):
        self.assertIn(r"..\dist\OWCSCompTracker\*", self.text)
        self.assertIn("recursesubdirs", self.text)

    def test_the_licence_file_it_references_exists(self):
        match = re.search(r"LicenseFile=(.+)", self.text)
        self.assertIsNotNone(match)
        rel = match.group(1).strip().replace("\\", os.sep)
        self.assertTrue(os.path.exists(os.path.normpath(
            os.path.join(PACKAGING, rel))), f"LicenseFile {rel} is missing")

    def test_the_icon_it_references_exists(self):
        for key in ("SetupIconFile",):
            match = re.search(rf"{key}=(.+)", self.text)
            rel = match.group(1).strip().replace("\\", os.sep)
            self.assertTrue(os.path.exists(os.path.normpath(
                os.path.join(PACKAGING, rel))), f"{key} {rel} is missing")

    def test_the_post_install_step_opens_the_wizard(self):
        """"Install and it is set up" is one flow, not two."""
        self.assertIn("--setup", self.text)
        self.assertIn("postinstall", self.text)


class TestVersionConsistency(unittest.TestCase):
    def test_the_version_resource_matches_the_package(self):
        """Windows shows this resource in Properties, SmartScreen and Task
        Manager. A stale one makes an upgraded install look like the old
        version to everything except the app itself."""
        version = owcs_desktop.__version__
        text = read(os.path.join(PACKAGING, "version_info.txt"))
        expected = tuple(int(p) for p in version.split(".")[:3]) + (0,)

        for field in ("filevers", "prodvers"):
            match = re.search(rf"{field}=\(([\d,\s]+)\)", text)
            self.assertIsNotNone(match, f"version_info.txt has no {field}")
            actual = tuple(int(n) for n in match.group(1).split(","))
            self.assertEqual(actual, expected,
                             f"{field} is {actual}, package version is {version}")

        dotted = ".".join(str(n) for n in expected)
        for field in ("FileVersion", "ProductVersion"):
            match = re.search(rf"StringStruct\('{field}',\s*'([^']*)'\)", text)
            self.assertIsNotNone(match, f"version_info.txt has no {field}")
            self.assertEqual(match.group(1), dotted,
                             f"{field} is {match.group(1)}, expected {dotted}")

    def test_the_version_is_a_release_number(self):
        self.assertRegex(owcs_desktop.__version__, r"^\d+\.\d+\.\d+$")

    def test_the_build_script_syncs_the_resource(self):
        text = read(os.path.join(PACKAGING, "build_windows.py"))
        self.assertIn("sync_version_resource", text)


class TestSpec(unittest.TestCase):
    def setUp(self) -> None:
        self.text = read(os.path.join(PACKAGING, "owcs.spec"))

    def test_the_spec_uses_the_shared_payload_definition(self):
        """The spec and this test must never keep separate lists."""
        self.assertIn("from payload import", self.text)
        self.assertIn("collect_datas", self.text)

    def test_it_builds_a_windowed_onedir_bundle(self):
        self.assertIn("console=False", self.text,
                      "a console window would appear at every sign-in")
        self.assertIn("COLLECT(", self.text,
                      "onefile unpacks OpenCV on every launch")

    def test_upx_is_off(self):
        self.assertNotIn("upx=True", self.text)

    def test_the_entrypoint_is_the_single_app(self):
        self.assertIn("owcs_app.py", self.text)


class TestEntrypointModes(unittest.TestCase):
    """Every mode the installer, the autostart entry and the workflow invoke
    must actually exist, or the installed app fails at exactly the moment a
    user first meets it."""

    def setUp(self) -> None:
        sys.path.insert(0, os.path.join(REPO, "desktop"))
        import owcs_app
        self.parser = owcs_app.build_parser()

    def known(self, *args) -> bool:
        try:
            self.parser.parse_args(list(args))
            return True
        except SystemExit:
            return False

    def test_every_referenced_mode_parses(self):
        for flag in ("--tray", "--service", "--control-room", "--setup",
                     "--check", "--readiness", "--stop-service"):
            with self.subTest(flag=flag):
                self.assertTrue(self.known(flag), f"{flag} is not a mode")
        self.assertTrue(self.known("--repair", "repair.databases"))

    def test_the_default_is_the_tray(self):
        args = self.parser.parse_args([])
        self.assertFalse(args.service)
        self.assertFalse(args.control_room)

    def test_autostart_uses_a_mode_that_exists(self):
        from owcs_desktop import autostart
        command = autostart.launch_command()
        self.assertIn("--tray", command)
        self.assertTrue(self.known("--tray"))

    def test_the_installer_only_invokes_real_modes(self):
        iss = read(os.path.join(PACKAGING, "installer.iss"))
        for flag in re.findall(r'Parameters: "(--[a-z-]+)', iss):
            with self.subTest(flag=flag):
                self.assertTrue(self.known(flag),
                                f"installer.iss runs {flag}, which is not a "
                                f"mode the application accepts")


class TestCleanMachineWorkflow(unittest.TestCase):
    WORKFLOW = os.path.join(REPO, ".github", "workflows", "windows-app.yml")

    def setUp(self) -> None:
        self.text = read(self.WORKFLOW)
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed")
        self.doc = yaml.safe_load(self.text)

    def test_the_clean_install_job_does_not_check_out_the_source(self):
        """The whole value of this job is that it has nothing but the
        installer. A checkout would let a missing bundled file be satisfied
        by the repository and the test would prove nothing."""
        job = self.doc["jobs"]["clean-install"]
        steps = job["steps"]
        for step in steps:
            self.assertNotIn("actions/checkout", str(step.get("uses", "")),
                             "the clean-machine job checks out the source, so "
                             "it no longer tests a clean machine")
            self.assertNotIn("actions/setup-python", str(step.get("uses", "")),
                             "the clean-machine job installs Python, so it no "
                             "longer tests a machine without it")

    def test_it_installs_silently_from_the_artifact(self):
        job = str(self.doc["jobs"]["clean-install"])
        self.assertIn("download-artifact", job)
        self.assertIn("/VERYSILENT", job)

    def test_it_verifies_the_checksum_before_running_the_installer(self):
        job = self.doc["jobs"]["clean-install"]
        names = [s.get("name", "") for s in job["steps"]]
        checksum = next(i for i, n in enumerate(names) if "checksum" in n.lower())
        install = next(i for i, n in enumerate(names) if "Install silently" in n)
        self.assertLess(checksum, install,
                        "the installer is executed before its checksum is "
                        "verified")

    def test_it_runs_the_real_end_to_end_readiness_test(self):
        job = str(self.doc["jobs"]["clean-install"])
        self.assertIn("--readiness", job,
                      "the clean-machine job never proves the installed app "
                      "can actually process a broadcast")
        self.assertIn("--check", job)

    def test_it_proves_processing_survives_the_window_closing(self):
        names = [s.get("name", "")
                 for s in self.doc["jobs"]["clean-install"]["steps"]]
        self.assertTrue(
            any("survives" in n.lower() for n in names),
            "nothing verifies the claim that closing the control room leaves "
            "processing running")

    def test_it_uninstalls_and_checks_nothing_is_left(self):
        job = str(self.doc["jobs"]["clean-install"])
        self.assertIn("unins", job)
        self.assertIn("survived uninstall", job)

    def test_the_build_verifies_the_frozen_exe_before_wrapping_it(self):
        build = read(os.path.join(PACKAGING, "build_windows.py"))
        self.assertIn("stage_verify", build)
        # verify must run before the installer is produced
        self.assertLess(build.index("def stage_verify"),
                        build.index("def stage_installer"))
        self.assertIn("--check", build)

    def test_the_release_publishes_checksums_the_updater_can_use(self):
        job = str(self.doc["jobs"]["release"])
        self.assertIn("SHA256SUMS", job)
        from owcs_desktop import updates
        self.assertEqual(updates.CHECKSUM_ASSET, "SHA256SUMS")

    def test_the_installer_name_matches_what_the_updater_looks_for(self):
        from owcs_desktop import updates
        name = f"OWCSCompTracker-{owcs_desktop.__version__}-Setup.exe"
        self.assertIsNotNone(
            updates.INSTALLER_PATTERN.match(name),
            f"the updater would not recognise its own installer, {name}")


class TestVendoredBinaries(unittest.TestCase):
    def test_the_fetcher_verifies_what_it_downloads(self):
        text = read(os.path.join(PACKAGING, "fetch_vendor.py"))
        self.assertIn("MZ", text, "downloads are not checked for being real "
                                  "executables")
        self.assertIn("MIN_SIZES", text)

    def test_it_records_the_licences_of_what_it_ships(self):
        text = read(os.path.join(PACKAGING, "fetch_vendor.py"))
        for word in ("LGPL", "Unlicense", "licence"):
            self.assertIn(word, text)

    def test_the_app_prefers_the_bundled_binaries(self):
        """Whatever unrelated ffmpeg is on a user's PATH must not be picked
        up in preference to the version the app was tested with."""
        from owcs_desktop import health, paths as p
        source = read(os.path.join(REPO, "desktop", "owcs_desktop", "health.py"))
        vendored_first = source.index("vendored = os.path.join")
        which_after = source.index("shutil.which(name)")
        self.assertLess(vendored_first, which_after)
        self.assertTrue(p.vendor_dir().endswith(os.path.join("vendor", "bin")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
