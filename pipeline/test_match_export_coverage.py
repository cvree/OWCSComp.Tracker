#!/usr/bin/env python3
"""
test_match_export_coverage.py — Phase D2.1 match export coverage report.

Covers pipeline/automation/match_export_coverage.py: the report's counters
are computed directly against export_data.build_public_payload (never a
second opinion), and every excluded match carries exactly one explicit,
human-readable reason instead of a silent gap.
Run: python3 pipeline/test_match_export_coverage.py
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
from automation import match_export_coverage as mec  # noqa: E402
from automation import match_repair as mr  # noqa: E402


def _insert_team(con, tid):
    con.execute("INSERT INTO teams (id, name, region, code) VALUES (?,?,?,?)",
               (tid, tid.title(), "na", tid[:3].upper()))


def _insert_match(con, mid, status, **kw):
    con.execute(
        """INSERT INTO matches
             (id, source_ref, region, date, scheduled_at, status,
              lifecycle_status, competition_id, team_a, team_b)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (mid, kw.get("source_ref"), kw.get("region", "na"),
         kw.get("date", "2026-06-01"), kw.get("scheduled_at"), status,
         kw.get("lifecycle"), kw.get("competition_id"),
         kw.get("team_a", "a"), kw.get("team_b", "b")))
    con.commit()


class CoverageTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(os.path.join(self.tmp.name, "owcs.sqlite"))
        db.init_schema(self.con)
        _insert_team(self.con, "a")
        _insert_team(self.con, "b")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()


class TestCoverageCounts(CoverageTestCase):
    def test_synthetic_excluded_with_reason(self):
        _insert_match(self.con, "m01", "final", source_ref="sample:m01")
        report = mec.build_coverage_report(self.con)
        self.assertEqual(report["counts"]["total"], 1)
        self.assertEqual(report["counts"]["excluded"], 1)
        self.assertEqual(report["excluded"][0]["reason"], "synthetic-fixture")

    def test_undiscovered_upcoming_stub_excluded_as_no_discovery_evidence(self):
        _insert_match(self.con, "m-stub", "upcoming")
        report = mec.build_coverage_report(self.con)
        self.assertEqual(report["excluded"][0]["reason"], "no-discovery-evidence")

    def test_unknown_status_excluded_as_needs_review(self):
        _insert_match(self.con, "m-unk", "unknown")
        report = mec.build_coverage_report(self.con)
        self.assertEqual(report["excluded"][0]["reason"], "needs-review")
        self.assertEqual(report["counts"]["needs_review"], 1)

    def test_repaired_match_no_longer_excluded(self):
        # 'final' matches already bypass the gate regardless of repair (the
        # export-gate fix itself) — the meaningful before/after case is an
        # 'upcoming' match, which only reaches the export once
        # lifecycle_status is backfilled AND it's inside the discovery window.
        sched = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3)).isoformat()
        _insert_match(self.con, "m-real", "upcoming", date=sched[:10], scheduled_at=sched)
        before = mec.build_coverage_report(self.con)
        self.assertEqual(before["counts"]["exported"], 0)
        mr.repair_matches(self.con, write=True)
        after = mec.build_coverage_report(self.con, repaired_this_run=1)
        self.assertEqual(after["counts"]["exported"], 1)
        self.assertEqual(after["counts"]["excluded"], 0)
        self.assertEqual(after["counts"]["repaired_this_run"], 1)

    def test_missing_competition_and_lifecycle_counted(self):
        _insert_match(self.con, "m-x", "final")
        report = mec.build_coverage_report(self.con)
        self.assertEqual(report["counts"]["missing_competition"], 1)
        self.assertEqual(report["counts"]["missing_lifecycle"], 1)

    def test_every_excluded_match_has_a_reason(self):
        _insert_match(self.con, "m1", "final", source_ref="sample:m1")
        _insert_match(self.con, "m2", "upcoming")
        _insert_match(self.con, "m3", "unknown")
        report = mec.build_coverage_report(self.con)
        for e in report["excluded"]:
            self.assertTrue(e["reason"])

    def test_repaired_match_appears_in_search_calendar_dataset(self):
        # search/calendar both read export_data.build_public_payload's
        # `matches` list directly — a repaired match reaching that list IS
        # the contract those pages depend on.
        import export_data as ed
        _insert_match(self.con, "m-cal", "final", date="2026-07-01")
        mr.repair_matches(self.con, write=True)
        payload = ed.build_public_payload(self.con)
        self.assertIn("m-cal", {m["id"] for m in payload["matches"]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
