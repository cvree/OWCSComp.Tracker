#!/usr/bin/env python3
"""
test_desktop_core.py — paths, settings, credentials and autostart.

These four modules decide where a user's data lives, whether their API keys
are protected, and whether the application starts with Windows. Each test
below exists because getting one of them wrong is silent: nothing crashes,
and the damage (a key written in the clear, an autostart entry pointing at a
deleted folder, a settings file truncated by a power cut) only shows up
later.

Every test runs against a temporary OWCS_HOME, so no test can touch a real
profile, and the Windows-only paths are exercised through injected backends
rather than skipped — a DPAPI code path that is only ever tested on Windows
is a code path that is never tested in CI.

Run: python3 pipeline/test_desktop_core.py
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

from owcs_desktop import autostart, credentials, paths  # noqa: E402
from owcs_desktop.settings import (DEFAULTS, SCHEMA, Settings,  # noqa: E402
                                   SettingsError, atomic_write_json)


class TempHome(unittest.TestCase):
    """Every subclass gets a private data root."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="owcs-test-home-")
        self._old = os.environ.get(paths.HOME_ENV)
        os.environ[paths.HOME_ENV] = self._tmp.name
        paths.ensure_layout()

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop(paths.HOME_ENV, None)
        else:
            os.environ[paths.HOME_ENV] = self._old
        self._tmp.cleanup()


# ------------------------------------------------------------------ paths
class TestPaths(TempHome):
    def test_data_root_honours_the_override(self):
        self.assertEqual(os.path.realpath(paths.data_root()),
                         os.path.realpath(self._tmp.name))

    def test_every_subdirectory_is_created(self):
        for name in paths.SUBDIRS:
            self.assertTrue(os.path.isdir(paths.sub(name)),
                            f"{name} was not created")

    def test_unknown_subdirectory_is_refused_by_name(self):
        with self.assertRaises(KeyError) as ctx:
            paths.sub("nope")
        self.assertIn("nope", str(ctx.exception))

    def test_app_root_holds_the_pipeline(self):
        """app_root() must find the payload, not the data root. Confusing the
        two would have the app writing databases into Program Files."""
        self.assertTrue(os.path.isdir(os.path.join(paths.app_root(), "pipeline")))
        self.assertNotEqual(os.path.realpath(paths.app_root()),
                            os.path.realpath(paths.data_root()))

    def test_no_developer_machine_path_is_baked_in(self):
        """Nothing in the desktop package may hardcode an absolute path from
        whatever machine it was written on."""
        import re
        suspicious = re.compile(
            r"""["'](?:/home/|/Users/|[A-Za-z]:\\\\Users\\\\|/root/)""")
        offenders = []
        pkg = os.path.join(REPO, "desktop", "owcs_desktop")
        for name in sorted(os.listdir(pkg)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(pkg, name), "r", encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    if suspicious.search(line):
                        offenders.append(f"{name}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [], "hardcoded machine paths:\n"
                         + "\n".join(offenders))

    def test_apply_environment_points_the_pipeline_at_user_storage(self):
        env: dict = {}
        applied = paths.apply_environment(env=env)
        self.assertEqual(env["OWCS_DB"], paths.content_db())
        self.assertEqual(env["OWCS_AUTOMATION_DB"], paths.automation_db())
        self.assertEqual(env["OWCS_MEDIA_ROOT"], paths.sub("media"))
        self.assertIn("OWCS_DB", applied)
        # Every database path must be under the writable root, never the
        # installed payload.
        for key in ("OWCS_DB", "OWCS_AUTOMATION_DB", "OWCS_MEDIA_ROOT"):
            self.assertTrue(env[key].startswith(paths.data_root()),
                            f"{key} escaped the data root: {env[key]}")

    def test_apply_environment_never_overrides_an_operator_setting(self):
        env = {"OWCS_DB": "/somewhere/chosen.sqlite"}
        paths.apply_environment(env=env)
        self.assertEqual(env["OWCS_DB"], "/somewhere/chosen.sqlite")

    def test_worker_media_root_follows_the_override(self):
        """The pipeline's worker resolves DEFAULT_MEDIA_ROOT from
        OWCS_MEDIA_ROOT at import time — that is the hook the desktop layer
        uses to keep multi-gigabyte downloads off Program Files."""
        import importlib
        sys.path.insert(0, REPO)
        os.environ["OWCS_MEDIA_ROOT"] = paths.sub("media")
        try:
            from pipeline.automation import worker
            importlib.reload(worker)
            self.assertEqual(worker.DEFAULT_MEDIA_ROOT, paths.sub("media"))
        finally:
            os.environ.pop("OWCS_MEDIA_ROOT", None)
            from pipeline.automation import worker as w2
            importlib.reload(w2)

    def test_seeding_never_overwrites_existing_user_data(self):
        created = paths.seed_from_payload()
        self.assertTrue(created, "the shipped database was not seeded")
        with open(paths.content_db(), "wb") as f:
            f.write(b"the user's own work")
        again = paths.seed_from_payload()
        self.assertEqual(again, [], "an upgrade re-seeded over user data")
        with open(paths.content_db(), "rb") as f:
            self.assertEqual(f.read(), b"the user's own work")


# --------------------------------------------------------------- settings
class TestSettings(TempHome):
    def test_defaults_when_the_file_is_absent(self):
        self.assertEqual(Settings().load(), DEFAULTS)

    def test_a_corrupt_file_still_boots(self):
        """A settings file damaged by a bad shutdown must not stop the
        application from starting — it falls back to defaults."""
        with open(paths.settings_file(), "w", encoding="utf-8") as f:
            f.write("{not json at all")
        self.assertEqual(Settings().load(), DEFAULTS)

    def test_a_bad_value_in_the_file_falls_back_per_key(self):
        atomic_write_json(paths.settings_file(),
                          {"maxStorageGb": "enormous", "autoStart": False})
        values = Settings().load()
        self.assertEqual(values["maxStorageGb"], DEFAULTS["maxStorageGb"])
        self.assertIs(values["autoStart"], False)

    def test_unknown_keys_are_refused_on_write(self):
        with self.assertRaises(SettingsError):
            Settings().set("colourOfTheBikeshed", "blue")

    def test_out_of_range_is_refused(self):
        with self.assertRaises(SettingsError):
            Settings().set("maxStorageGb", 0.5)
        with self.assertRaises(SettingsError):
            Settings().set("autoPublishMinConfidence", 1.5)
        with self.assertRaises(SettingsError):
            Settings().set("updateChannel", "nightly")

    def test_a_rejected_patch_writes_nothing_at_all(self):
        """All-or-nothing: a patch with one bad key must not half-apply."""
        settings = Settings()
        settings.set("maxStorageGb", 42.0)
        with self.assertRaises(SettingsError):
            settings.update({"minFreeDiskGb": 25.0, "nonsense": 1})
        self.assertEqual(Settings().get("minFreeDiskGb"),
                         DEFAULTS["minFreeDiskGb"])
        self.assertEqual(Settings().get("maxStorageGb"), 42.0)

    def test_booleans_are_not_confused_with_numbers(self):
        with self.assertRaises(SettingsError):
            Settings().set("maxConcurrentJobs", True)

    def test_writes_are_atomic(self):
        """The target is only ever replaced whole. A failure part-way through
        must leave the previous file intact and no scratch files behind."""
        settings = Settings()
        settings.set("maxStorageGb", 33.0)
        directory = os.path.dirname(paths.settings_file())
        before = {n for n in os.listdir(directory) if n.startswith(".tmp-")}
        self.assertEqual(before, set(), "a temp file was left behind")

        class Boom(Exception):
            pass

        real_replace = os.replace

        def explode(src, dst):
            raise Boom("simulated power cut")

        os.replace = explode
        try:
            with self.assertRaises(Boom):
                settings.set("maxStorageGb", 99.0)
        finally:
            os.replace = real_replace
        self.assertEqual(Settings().get("maxStorageGb"), 33.0,
                         "a failed save damaged the previous settings")
        leftovers = [n for n in os.listdir(directory) if n.startswith(".tmp-")]
        self.assertEqual(leftovers, [], "a failed save left scratch files")

    def test_every_schema_entry_is_renderable(self):
        """The UI renders straight from the schema, so an entry missing a
        label or help text would ship as a blank form field."""
        for entry in Settings().schema_for_ui():
            self.assertTrue(entry["label"], f"{entry['key']} has no label")
            self.assertTrue(entry["help"], f"{entry['key']} has no help text")
            self.assertIn(entry["type"], ("str", "int", "float", "bool"))
        self.assertEqual({e["key"] for e in Settings().schema_for_ui()},
                         set(SCHEMA))


# ------------------------------------------------------------ credentials
class RecordingBackend(credentials.Backend):
    """Stands in for DPAPI: reversible, and provably applied."""

    name = credentials.PROTECTION_DPAPI

    def __init__(self) -> None:
        self.protected = 0

    def protect(self, data: bytes) -> bytes:
        self.protected += 1
        return b"WRAPPED:" + data[::-1]

    def unprotect(self, data: bytes) -> bytes:
        assert data.startswith(b"WRAPPED:"), "unprotect saw unwrapped bytes"
        return data[len(b"WRAPPED:"):][::-1]


class TestCredentials(TempHome):
    def vault(self, backend=None):
        return credentials.CredentialVault(
            backend=backend or RecordingBackend())

    def test_round_trip(self):
        vault = self.vault()
        vault.set("FACEIT_API_KEY", "s3cr3t-value-9999")
        self.assertEqual(vault.get("FACEIT_API_KEY"), "s3cr3t-value-9999")
        self.assertTrue(vault.has("FACEIT_API_KEY"))

    def test_the_value_never_appears_in_the_file(self):
        """The single most important property here."""
        secret = "AAAA-do-not-store-me-in-the-clear-BBBB"
        vault = self.vault()
        vault.set("YOUTUBE_API_KEY", secret)
        with open(vault.path, "rb") as f:
            raw = f.read()
        self.assertNotIn(secret.encode(), raw)
        self.assertNotIn(secret.encode()[::-1], raw.replace(b"WRAPPED:", b""))

    def test_describe_never_leaks_a_value(self):
        secret = "CCCC-still-secret-DDDD"
        vault = self.vault()
        vault.set("FACEIT_API_KEY", secret)
        rendered = json.dumps(vault.describe())
        self.assertNotIn(secret, rendered)
        self.assertIn('"present": true', rendered)

    def test_the_backend_is_actually_used(self):
        backend = RecordingBackend()
        vault = self.vault(backend)
        vault.set("FACEIT_API_KEY", "x" * 20)
        self.assertEqual(backend.protected, 1,
                         "the credential was stored without going through the "
                         "protection backend")

    def test_empty_value_deletes(self):
        vault = self.vault()
        vault.set("FACEIT_API_KEY", "something")
        vault.set("FACEIT_API_KEY", "")
        self.assertFalse(vault.has("FACEIT_API_KEY"))
        self.assertIsNone(vault.get("FACEIT_API_KEY"))

    def test_unknown_credentials_are_refused(self):
        with self.assertRaises(credentials.CredentialError):
            self.vault().set("AWS_SECRET_ACCESS_KEY", "nope")

    def test_a_damaged_vault_reports_instead_of_crashing(self):
        vault = self.vault()
        with open(vault.path, "w", encoding="utf-8") as f:
            f.write("}}} not json")
        self.assertFalse(vault.has("FACEIT_API_KEY"))
        self.assertEqual(vault.names(), [])
        with self.assertRaises(credentials.CredentialError):
            vault.get("FACEIT_API_KEY")

    def test_export_environment_returns_names_not_values(self):
        vault = self.vault()
        vault.set("FACEIT_API_KEY", "EEEE-value-FFFF")
        env: dict = {}
        exported = vault.export_environment(env)
        self.assertEqual(exported, ["FACEIT_API_KEY"])
        self.assertEqual(env["FACEIT_API_KEY"], "EEEE-value-FFFF")
        self.assertNotIn("EEEE-value-FFFF", json.dumps(exported))

    def test_the_unencrypted_backend_says_so(self):
        """When DPAPI is unavailable the UI must be told the truth, not shown
        a padlock the storage does not have."""
        vault = credentials.CredentialVault(
            backend=credentials.FilePermissionBackend())
        vault.set("FACEIT_API_KEY", "plain")
        for entry in vault.describe():
            self.assertFalse(entry["encrypted"])
            self.assertEqual(entry["protection"], credentials.PROTECTION_FILE)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions only")
    def test_the_file_is_not_world_readable(self):
        vault = credentials.CredentialVault(
            backend=credentials.FilePermissionBackend())
        vault.set("FACEIT_API_KEY", "plain")
        mode = os.stat(vault.path).st_mode & 0o777
        self.assertEqual(mode, 0o600, f"credential file is mode {oct(mode)}")

    def test_dpapi_is_only_claimed_on_windows(self):
        if sys.platform != "win32":
            self.assertFalse(credentials.dpapi_available())
            self.assertIsInstance(credentials.default_backend(),
                                  credentials.FilePermissionBackend)


# -------------------------------------------------------------- autostart
class TestAutoStart(TempHome):
    def test_enable_disable_round_trip(self):
        backend = autostart.MemoryBackend()
        auto = autostart.AutoStart(backend)
        self.assertFalse(auto.is_enabled())
        command = auto.enable()
        self.assertTrue(auto.is_enabled())
        self.assertIn("--tray", command)
        auto.disable()
        self.assertFalse(auto.is_enabled())

    def test_the_command_is_absolute(self):
        """The Run key has no working directory: a relative command silently
        never starts."""
        command = autostart.launch_command()
        first = command.split('"')[1] if command.startswith('"') else command.split()[0]
        self.assertTrue(os.path.isabs(first), f"not absolute: {command}")

    def test_a_stale_entry_is_detected_and_repaired(self):
        """After an upgrade that moved the install folder, the stored command
        points at an executable that no longer exists. Silently, the app just
        stops starting with Windows."""
        backend = autostart.MemoryBackend()
        backend.write('"C:\\Old Location\\OWCSCompTracker.exe" --tray')
        auto = autostart.AutoStart(backend)
        status = auto.status()
        self.assertTrue(status["enabled"])
        self.assertTrue(status["stale"], "a moved install was not detected")
        auto.enable()
        self.assertFalse(auto.status()["stale"])

    def test_unsupported_platform_reports_instead_of_pretending(self):
        auto = autostart.AutoStart(autostart.NullBackend())
        result = auto.sync(True)
        self.assertFalse(result["supported"])
        self.assertFalse(result["enabled"])
        self.assertIn("Windows", result["detail"])

    def test_sync_makes_reality_match_the_setting(self):
        backend = autostart.MemoryBackend()
        auto = autostart.AutoStart(backend)
        auto.sync(True)
        self.assertIsNotNone(backend.read())
        auto.sync(False)
        self.assertIsNone(backend.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
