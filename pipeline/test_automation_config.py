#!/usr/bin/env python3
"""
test_automation_config.py — dependency-free YAML subset parser, config
defaults, and registry loaders. No network, no PyYAML.
Run: python3 pipeline/test_automation_config.py
"""
from __future__ import annotations
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from automation import config as cfg  # noqa: E402
from automation import models  # noqa: E402


class TestYamlSubset(unittest.TestCase):
    def test_scalars_and_types(self):
        parsed = cfg.parse_simple_yaml(
            "a: 1\nb: 2.5\nc: true\nd: false\ne: null\nf: hello\ng: 'quoted'\n"
        )
        self.assertEqual(parsed["a"], 1)
        self.assertEqual(parsed["b"], 2.5)
        self.assertIs(parsed["c"], True)
        self.assertIs(parsed["d"], False)
        self.assertIsNone(parsed["e"])
        self.assertEqual(parsed["f"], "hello")
        self.assertEqual(parsed["g"], "quoted")

    def test_list_block(self):
        parsed = cfg.parse_simple_yaml("regions:\n  - na\n  - emea\n  - global\n")
        self.assertEqual(parsed["regions"], ["na", "emea", "global"])

    def test_comments_and_blanks_ignored(self):
        parsed = cfg.parse_simple_yaml("# header\n\nx: 1  # inline\n")
        self.assertEqual(parsed, {"x": 1})

    def test_list_item_without_parent_raises(self):
        with self.assertRaises(ValueError):
            cfg.parse_simple_yaml("- orphan\n")


class TestConfigLoading(unittest.TestCase):
    def test_real_automation_yml_loads(self):
        c = cfg.load_config()
        self.assertEqual(c.lookback_days, 14)
        self.assertEqual(c.schedule_horizon_days, 30)
        self.assertIn("na", c.regions)
        self.assertIn("global", c.regions)
        self.assertEqual(c.publish_mode, "pull_request")
        self.assertTrue(len(c.retry_backoff_minutes) >= 1)

    def test_missing_file_uses_defaults(self):
        c = cfg.load_config("/no/such/automation.yml")
        self.assertEqual(c.lookback_days, cfg.DEFAULTS["lookback_days"])

    def test_max_attempts_per_kind(self):
        c = cfg.load_config()
        self.assertEqual(c.max_attempts_for(models.KIND_RECORD),
                         c.get("max_recording_retries"))
        self.assertEqual(c.max_attempts_for(models.KIND_PROCESS),
                         c.get("max_processing_retries"))
        self.assertEqual(c.max_attempts_for(models.KIND_DISCOVERY),
                         c.get("max_discovery_retries"))


class TestRegistries(unittest.TestCase):
    def test_competitions_file_parses(self):
        allc = cfg.load_all_competitions()
        self.assertTrue(len(allc) >= 1)
        # Every row declares an explicit tier and region.
        for c in allc:
            self.assertIn(c.get("tier"), (1, 2, 3))
            self.assertTrue(c.get("region"))
        # The enabled entries are the two API-verified OWCS 2026 Open
        # Qualifiers (NA, EMEA); every enabled entry must carry a real
        # championshipId and be marked verified.
        live = cfg.load_competitions()
        self.assertEqual(len(live), 2)
        regions = {c["region"] for c in live}
        self.assertEqual(regions, {"na", "emea"})
        for c in live:
            self.assertTrue(c.get("championshipId"))
            self.assertTrue(c.get("verified"))
            self.assertEqual(c.get("season"), "2026")
        # Disabled entries must NOT carry a championshipId (never guessed).
        for c in allc:
            if not c.get("enabled"):
                self.assertIsNone(c.get("championshipId"))

    def test_channels_file_parses(self):
        allch = cfg.load_all_channels()
        self.assertTrue(len(allch) >= 1)
        self.assertEqual(cfg.load_channels(), [])
        for ch in allch:
            self.assertIn("official", ch)
            self.assertIn("priority", ch)
            # Phase C1: every entry declares the new evidence/verification
            # fields, and every disabled entry MUST carry an explicit reason
            # (a gap in coverage is never allowed to be silent).
            for key in ("sourceUrl", "ownershipEvidence", "verifiedDate",
                       "verifiedStatus", "disabledReason", "preferredLayout"):
                self.assertIn(key, ch)
            if not ch.get("enabled"):
                self.assertTrue(ch.get("disabledReason"),
                                f"{ch['id']} is disabled but has no disabledReason")
            self.assertIsNone(ch.get("channelId"))  # none verified yet — never guessed
            self.assertEqual(ch.get("verifiedStatus"), "unverified")


class TestJobIdentity(unittest.TestCase):
    def test_deterministic_keys(self):
        self.assertEqual(models.match_key("1-abc"), "match:1-abc")
        self.assertEqual(models.record_key("VID", "1080p"), "record:vid:1080p")
        self.assertEqual(models.map_key("m01", 3), "map:m01:3")
        # Same inputs -> same key (idempotency foundation).
        self.assertEqual(models.broadcast_key("XyZ"), models.broadcast_key("XyZ"))

    def test_slug_never_empty(self):
        self.assertEqual(models.slug(""), "unknown")
        self.assertEqual(models.slug(None), "unknown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
