#!/usr/bin/env python3
"""
test_automation_match_repair.py — Phase D2.1 match-export repair.

Covers pipeline/automation/match_repair.py: evidence-based fixture_kind
classification (synthetic sample fixtures vs real production matches),
evidence-based lifecycle_status backfill (only from the match's own
pre-existing `status` field, never fabricated), idempotency, dry-run purity,
and that a repair never touches composition/evidence data.
Run: python3 pipeline/test_automation_match_repair.py
"""
from __future__ import annotations
import datetime as dt
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db  # noqa: E402
from automation import match_repair as mr  # noqa: E402


def _insert_team(con, tid, name=None):
    con.execute("INSERT INTO teams (id, name, region, code) VALUES (?,?,?,?)",
               (tid, name or tid.title(), "na", tid[:3].upper()))


def _insert_match(con, mid, status, **kw):
    con.execute(
        """INSERT INTO matches
             (id, source_ref, faceit_match_id, region, date, scheduled_at,
              status, lifecycle_status, competition_id, team_a, team_b)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (mid, kw.get("source_ref"), kw.get("faceit_match_id"), "na",
         kw.get("date", "2026-06-01"), kw.get("scheduled_at"), status,
         kw.get("lifecycle"), kw.get("competition_id"),
         kw.get("team_a", "a"), kw.get("team_b", "b")))
    con.commit()


class MatchRepairTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(os.path.join(self.tmp.name, "owcs.sqlite"))
        db.init_schema(self.con)
        _insert_team(self.con, "a")
        _insert_team(self.con, "b")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()


class TestFixtureClassification(MatchRepairTestCase):
    def test_sample_source_ref_is_synthetic(self):
        _insert_match(self.con, "m01", "final", source_ref="sample:m01",
                     faceit_match_id="sample-faceit-m01")
        row = self.con.execute("SELECT * FROM matches WHERE id='m01'").fetchone()
        self.assertEqual(mr.classify_fixture_kind(row), "synthetic")

    def test_real_match_defaults_to_production(self):
        _insert_match(self.con, "m-real", "final", source_ref="owcs-real-vod")
        row = self.con.execute("SELECT * FROM matches WHERE id='m-real'").fetchone()
        self.assertEqual(mr.classify_fixture_kind(row), "production")

    def test_explicit_existing_value_is_never_reclassified(self):
        _insert_match(self.con, "m-x", "final", source_ref="sample:weird")
        self.con.execute("UPDATE matches SET fixture_kind='production' WHERE id='m-x'")
        row = self.con.execute("SELECT * FROM matches WHERE id='m-x'").fetchone()
        self.assertEqual(mr.classify_fixture_kind(row), "production")


class TestLifecycleInference(MatchRepairTestCase):
    def test_completed_match_infers_finished(self):
        _insert_match(self.con, "m-final", "final")
        row = self.con.execute("SELECT * FROM matches WHERE id='m-final'").fetchone()
        value, source = mr.infer_lifecycle_status(row)
        self.assertEqual(value, "finished")
        self.assertEqual(source, "status-field-backfill")

    def test_scheduled_match_infers_scheduled(self):
        _insert_match(self.con, "m-up", "upcoming")
        row = self.con.execute("SELECT * FROM matches WHERE id='m-up'").fetchone()
        value, _ = mr.infer_lifecycle_status(row)
        self.assertEqual(value, "scheduled")

    def test_live_match_infers_live(self):
        _insert_match(self.con, "m-live", "live")
        row = self.con.execute("SELECT * FROM matches WHERE id='m-live'").fetchone()
        value, _ = mr.infer_lifecycle_status(row)
        self.assertEqual(value, "live")

    def test_unknown_status_has_no_safe_inference(self):
        _insert_match(self.con, "m-unk", "unknown")
        row = self.con.execute("SELECT * FROM matches WHERE id='m-unk'").fetchone()
        value, source = mr.infer_lifecycle_status(row)
        self.assertIsNone(value)
        self.assertIsNone(source)


class TestRepairMatches(MatchRepairTestCase):
    def test_missing_competition_id_alone_is_not_a_blocker(self):
        # competition_id has no safe source to infer from — it must simply
        # stay null, never fabricated, and never block the lifecycle repair.
        _insert_match(self.con, "m1", "final")
        report = mr.repair_matches(self.con, write=True)
        self.assertIn("m1", report["repaired"])
        row = self.con.execute("SELECT * FROM matches WHERE id='m1'").fetchone()
        self.assertIsNone(row["competition_id"])
        self.assertEqual(row["lifecycle_status"], "finished")

    def test_missing_lifecycle_status_backfilled(self):
        _insert_match(self.con, "m2", "final")
        report = mr.repair_matches(self.con, write=True)
        row = self.con.execute("SELECT * FROM matches WHERE id='m2'").fetchone()
        self.assertEqual(row["lifecycle_status"], "finished")
        self.assertEqual(row["lifecycle_source"], "status-field-backfill")
        self.assertIsNotNone(row["lifecycle_repaired_at"])

    def test_both_fields_missing_repaired_safely(self):
        _insert_match(self.con, "m3", "final")
        row = self.con.execute("SELECT * FROM matches WHERE id='m3'").fetchone()
        self.assertIsNone(row["competition_id"])
        self.assertIsNone(row["lifecycle_status"])
        mr.repair_matches(self.con, write=True)
        row = self.con.execute("SELECT * FROM matches WHERE id='m3'").fetchone()
        self.assertIsNone(row["competition_id"])          # never fabricated
        self.assertEqual(row["lifecycle_status"], "finished")

    def test_competition_id_never_guessed_from_shared_teams(self):
        _insert_match(self.con, "m4", "final")
        _insert_match(self.con, "m5", "final", competition_id=None)
        mr.repair_matches(self.con, write=True)
        for mid in ("m4", "m5"):
            row = self.con.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
            self.assertIsNone(row["competition_id"])

    def test_placeholder_unknown_status_enters_review(self):
        _insert_match(self.con, "m-ph", "unknown")
        report = mr.repair_matches(self.con, write=True)
        ids = {u["id"] for u in report["unresolved"]}
        self.assertIn("m-ph", ids)
        row = self.con.execute("SELECT * FROM matches WHERE id='m-ph'").fetchone()
        self.assertIsNone(row["lifecycle_status"])   # left alone, not guessed

    def test_synthetic_fixture_stays_hidden_and_untouched(self):
        _insert_match(self.con, "m01", "final", source_ref="sample:m01",
                     faceit_match_id="sample-faceit-m01")
        report = mr.repair_matches(self.con, write=True)
        self.assertIn("m01", report["synthetic"])
        self.assertNotIn("m01", report["repaired"])
        row = self.con.execute("SELECT * FROM matches WHERE id='m01'").fetchone()
        self.assertEqual(row["fixture_kind"], "synthetic")
        self.assertIsNone(row["lifecycle_status"])   # synthetic rows never get lifecycle

    def test_cancelled_match_lifecycle_left_untouched(self):
        # 'cancelled' isn't in matches.status's CHECK set — it only ever
        # arrives via an already-set lifecycle_status (e.g. FACEIT sync).
        # Repair must never overwrite it.
        _insert_match(self.con, "m-cxl", "unknown", lifecycle="cancelled")
        report = mr.repair_matches(self.con, write=True)
        self.assertNotIn("m-cxl", report["repaired"])
        row = self.con.execute("SELECT * FROM matches WHERE id='m-cxl'").fetchone()
        self.assertEqual(row["lifecycle_status"], "cancelled")

    def test_postponed_match_lifecycle_left_untouched(self):
        _insert_match(self.con, "m-pp", "upcoming", lifecycle="postponed")
        report = mr.repair_matches(self.con, write=True)
        self.assertNotIn("m-pp", report["repaired"])
        row = self.con.execute("SELECT * FROM matches WHERE id='m-pp'").fetchone()
        self.assertEqual(row["lifecycle_status"], "postponed")

    def test_completed_match_repaired(self):
        _insert_match(self.con, "m-c", "final")
        report = mr.repair_matches(self.con, write=True)
        self.assertIn("m-c", report["repaired"])

    def test_scheduled_match_repaired(self):
        _insert_match(self.con, "m-s", "upcoming")
        report = mr.repair_matches(self.con, write=True)
        self.assertIn("m-s", report["repaired"])
        row = self.con.execute("SELECT * FROM matches WHERE id='m-s'").fetchone()
        self.assertEqual(row["lifecycle_status"], "scheduled")

    def test_idempotent_repair(self):
        _insert_match(self.con, "m6", "final")
        first = mr.repair_matches(self.con, write=True)
        self.assertEqual(first["repaired"], ["m6"])
        second = mr.repair_matches(self.con, write=True)
        self.assertEqual(second["repaired"], [])
        self.assertIn("m6", second["alreadyOk"])

    def test_dry_run_makes_zero_writes(self):
        _insert_match(self.con, "m7", "final")
        mr.repair_matches(self.con, write=False)
        row = self.con.execute("SELECT * FROM matches WHERE id='m7'").fetchone()
        self.assertIsNone(row["lifecycle_status"])
        self.assertIsNone(row["fixture_kind"])

    def test_explicit_write_mode_persists(self):
        _insert_match(self.con, "m8", "final")
        mr.repair_matches(self.con, write=True)
        # reopen semantics: query again from the same (committed) connection
        row = self.con.execute("SELECT * FROM matches WHERE id='m8'").fetchone()
        self.assertEqual(row["lifecycle_status"], "finished")
        self.assertEqual(row["fixture_kind"], "production")

    def test_never_overwrites_an_existing_lifecycle_value(self):
        _insert_match(self.con, "m9", "final", lifecycle="forfeit")
        mr.repair_matches(self.con, write=True)
        row = self.con.execute("SELECT * FROM matches WHERE id='m9'").fetchone()
        self.assertEqual(row["lifecycle_status"], "forfeit")

    def test_no_composition_writes(self):
        self.con.execute("INSERT INTO heroes (id, name, role) VALUES ('kiriko','Kiriko','Support')")
        _insert_match(self.con, "m10", "final")
        self.con.execute("INSERT INTO game_maps (id, name, mode) VALUES ('kingsrow','K','Hybrid')")
        self.con.execute(
            "INSERT INTO map_results (match_id, map_order, map_id) VALUES ('m10',1,'kingsrow')")
        self.con.execute(
            """INSERT INTO hero_stints (match_id, map_result_id, team_id, slot, hero_id, start_offset)
               VALUES ('m10', 1, 'a', 1, 'kiriko', 0)""")
        self.con.commit()
        before = self.con.execute("SELECT COUNT(*) FROM hero_stints").fetchone()[0]
        mr.repair_matches(self.con, write=True)
        after = self.con.execute("SELECT COUNT(*) FROM hero_stints").fetchone()[0]
        self.assertEqual(before, after)

    def test_no_evidence_deletion(self):
        self.con.execute("INSERT INTO game_maps (id, name, mode) VALUES ('kingsrow','K','Hybrid')")
        _insert_match(self.con, "m11", "final")
        self.con.execute(
            "INSERT INTO map_results (match_id, map_order, map_id) VALUES ('m11',1,'kingsrow')")
        self.con.commit()
        before = self.con.execute("SELECT COUNT(*) FROM map_results").fetchone()[0]
        mr.repair_matches(self.con, write=True)
        after = self.con.execute("SELECT COUNT(*) FROM map_results").fetchone()[0]
        self.assertEqual(before, after)


class TestExportsAfterRepair(MatchRepairTestCase):
    def _payload(self):
        import export_data as ed
        return ed.build_public_payload(self.con)

    def test_valid_match_exports_after_repair(self):
        # A concluded (status='final') match exports immediately regardless
        # of repair (Phase D2.1's export-gate fix) — so the meaningful
        # before/after case is an 'upcoming' match, which only reaches the
        # public export once lifecycle_status is backfilled AND it falls
        # inside the rolling discovery window.
        sched = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3)).isoformat()
        _insert_match(self.con, "m-repaired", "upcoming",
                     date=sched[:10], scheduled_at=sched)
        self.assertNotIn("m-repaired", {m["id"] for m in self._payload()["matches"]})
        mr.repair_matches(self.con, write=True)
        self.assertIn("m-repaired", {m["id"] for m in self._payload()["matches"]})

    def test_synthetic_never_exports_even_after_repair_run(self):
        _insert_match(self.con, "m01", "final", source_ref="sample:m01")
        mr.repair_matches(self.con, write=True)
        self.assertNotIn("m01", {m["id"] for m in self._payload()["matches"]})

    def test_ambiguous_match_stays_out_with_reason_available(self):
        _insert_match(self.con, "m-amb", "unknown")
        mr.repair_matches(self.con, write=True)
        self.assertNotIn("m-amb", {m["id"] for m in self._payload()["matches"]})
        row = self.con.execute("SELECT * FROM matches WHERE id='m-amb'").fetchone()
        self.assertIsNotNone(mr._blocking_reason(row, "production", False, None))

    def test_repaired_match_appears_in_team_history(self):
        _insert_match(self.con, "m-hist", "final", team_a="a", team_b="b")
        mr.repair_matches(self.con, write=True)
        payload = self._payload()
        match_ids = {m["id"] for m in payload["matches"] if "a" in (m["teamA"], m["teamB"])}
        self.assertIn("m-hist", match_ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
