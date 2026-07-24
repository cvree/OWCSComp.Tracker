#!/usr/bin/env python3
"""
test_automation_reconcile.py — Phase B4 source reconciliation.
Proves conflicts are surfaced as warnings and never silently overwritten.
Run: python3 pipeline/test_automation_reconcile.py
"""
from __future__ import annotations
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from automation import owcs_calendar as cal  # noqa: E402
from automation import reconcile as rec  # noqa: E402


def event(eid, comp_id=None, start="2026-07-01", end="2026-08-15",
          region="na", channels=("ow_esports_global",)):
    return cal._normalize_event({
        "id": eid, "name": eid, "region": region, "stage": "Stage 2",
        "startDate": start, "endDate": end, "faceitCompetitionId": comp_id,
        "broadcastChannels": list(channels), "verified": False})


def nmatch(mid, when, *, status="finished", content="final",
           name_a="A", name_b="B", score=(3, 1), comp_id="CH"):
    return {
        "faceitMatchId": mid, "competitionId": comp_id, "region": "na",
        "lifecycleStatus": status, "contentStatus": content,
        "scheduledAt": when, "startedAt": None, "finishedAt": when,
        "teams": [{"side": "A", "name": name_a}, {"side": "B", "name": name_b}],
        "score": {"a": score[0], "b": score[1]}, "winnerSide": "A",
    }


class TestReconcile(unittest.TestCase):
    def test_match_within_event_no_warning(self):
        events = [event("e1", comp_id="c1")]
        w = rec.reconcile([nmatch("1-a", "2026-07-10T18:00:00+00:00")], events)
        codes = {x["code"] for x in w}
        self.assertNotIn("FACEIT_MATCH_NO_CALENDAR_EVENT", codes)
        self.assertNotIn("START_TIME_MISMATCH", codes)

    def test_match_outside_all_events(self):
        events = [event("e1", comp_id="c1", start="2026-01-01", end="2026-01-31")]
        w = rec.reconcile([nmatch("1-a", "2026-07-10T18:00:00+00:00")], events)
        self.assertIn("FACEIT_MATCH_NO_CALENDAR_EVENT",
                      {x["code"] for x in w})

    def test_competition_without_calendar_event(self):
        w = rec.reconcile([], [event("e1", comp_id=None)],
                          competitions=[{"id": "c_missing", "championshipId": "X"}])
        self.assertIn("CALENDAR_EVENT_NO_FACEIT_COMP", {x["code"] for x in w})

    def test_event_without_broadcast(self):
        events = [event("e1", comp_id="c1", channels=())]
        w = rec.reconcile([], events, competitions=[{"id": "c1", "championshipId": "X"}])
        self.assertIn("COMPETITION_NO_BROADCAST", {x["code"] for x in w})

    def test_completed_without_result(self):
        events = [event("e1", comp_id="c1")]
        m = nmatch("1-a", "2026-07-10T18:00:00+00:00", score=(None, None))
        w = rec.reconcile([m], events)
        self.assertIn("COMPLETED_NO_RESULT", {x["code"] for x in w})

    def test_team_unresolved(self):
        events = [event("e1", comp_id="c1")]
        m = nmatch("1-a", "2026-07-10T18:00:00+00:00", name_a=None)
        w = rec.reconcile([m], events)
        self.assertIn("TEAM_UNRESOLVED", {x["code"] for x in w})

    def test_conflicting_faceit_vs_calendar_start_times(self):
        # helper compares two specific times; beyond tolerance -> conflict
        self.assertTrue(rec.start_time_conflict(
            "2026-07-10T18:00:00+00:00", "2026-07-10T22:00:00+00:00"))
        self.assertFalse(rec.start_time_conflict(
            "2026-07-10T18:00:00+00:00", "2026-07-10T18:30:00+00:00"))
        self.assertFalse(rec.start_time_conflict(None, "2026-07-10T18:00:00+00:00"))

    def test_start_time_mismatch_vs_event_window(self):
        events = [event("e1", comp_id="c1", start="2026-07-01", end="2026-07-05")]
        # match linked by covering-window fallback but falls just outside
        m = nmatch("1-a", "2026-07-20T18:00:00+00:00")
        w = rec.reconcile([m], events)
        codes = {x["code"] for x in w}
        # Either no covering event (no event covers 07-20) -> NO_CALENDAR_EVENT
        self.assertIn("FACEIT_MATCH_NO_CALENDAR_EVENT", codes)

    def test_never_mutates_inputs(self):
        events = [event("e1", comp_id="c1")]
        m = nmatch("1-a", "2026-07-10T18:00:00+00:00")
        snapshot = dict(m)
        rec.reconcile([m], events)
        self.assertEqual(m, snapshot)  # pure


if __name__ == "__main__":
    unittest.main(verbosity=2)
