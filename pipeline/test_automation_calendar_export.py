#!/usr/bin/env python3
"""
test_automation_calendar_export.py — discovered scheduled matches reach the
public calendar dataset (public.v1) with honest status/capture, and matches
that were NOT discovered (no competition/lifecycle) are not fabricated.
Run: python3 pipeline/test_automation_calendar_export.py
"""
from __future__ import annotations
import datetime as dt
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db as content_db  # noqa: E402
import export_data  # noqa: E402
from automation import discovery as disc  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc)


def _seed_teams(con):
    for tid, name in (("alpha", "Alpha"), ("bravo", "Bravo")):
        con.execute("INSERT INTO teams (id, name, region, code) VALUES (?,?,?,?)",
                    (tid, name, "na", name[:3].upper()))


def _insert_match(con, mid, **kw):
    con.execute(
        """INSERT INTO matches
             (id, region, date, scheduled_at, status, lifecycle_status,
              capture_status, competition_id, event_name, stage,
              team_a, team_b, score_a, score_b, winner_team, faceit_room_url)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (mid, "na", kw["date"], kw.get("scheduled_at"), kw["status"],
         kw.get("lifecycle"), kw.get("capture"), kw.get("competition_id"),
         kw.get("event", "OWCS NA"), kw.get("stage", "Stage 2"),
         "alpha", "bravo", kw.get("sa"), kw.get("sb"),
         kw.get("winner"), kw.get("room")))
    con.commit()


class TestCalendarExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = content_db.connect(os.path.join(self.tmp.name, "owcs.sqlite"))
        content_db.init_schema(self.con)
        _seed_teams(self.con)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def _payload(self):
        return export_data.build_public_payload(self.con)

    def test_upcoming_match_appears_on_calendar(self):
        sched = (NOW + dt.timedelta(days=3)).isoformat()
        _insert_match(self.con, "faceit-1-a", date=sched[:10], scheduled_at=sched,
                      status="upcoming", lifecycle="scheduled", capture="pending",
                      competition_id="c_na")
        pl = self._payload()
        ids = {m["id"]: m for m in pl["matches"]}
        self.assertIn("faceit-1-a", ids)
        m = ids["faceit-1-a"]
        self.assertEqual(m["status"], "upcoming")
        self.assertEqual(m["scheduledAt"], sched)      # real scheduled time
        self.assertEqual(m["captureStatus"], "needs-source")
        # its tournament + teams are registered
        self.assertTrue(any(t["id"] == m["tournamentId"] for t in pl["tournaments"]))
        self.assertTrue({"alpha", "bravo"} <= {t["id"] for t in pl["teams"]})

    def test_status_and_capture_mapping(self):
        base = (NOW + dt.timedelta(days=-2)).isoformat()
        _insert_match(self.con, "faceit-live", date=base[:10], scheduled_at=base,
                      status="live", lifecycle="live", competition_id="c")
        _insert_match(self.con, "faceit-final", date=base[:10], scheduled_at=base,
                      status="final", lifecycle="finished", competition_id="c",
                      sa=3, sb=2, winner="alpha")
        _insert_match(self.con, "faceit-cxl", date=base[:10], scheduled_at=base,
                      status="unknown", lifecycle="cancelled", capture="cancelled",
                      competition_id="c")
        pl = self._payload()
        ids = {m["id"]: m for m in pl["matches"]}
        self.assertEqual(ids["faceit-live"]["status"], "live")
        self.assertEqual(ids["faceit-final"]["status"], "completed")
        self.assertEqual(ids["faceit-cxl"]["status"], "cancelled")
        # cancelled needs no capture chip
        self.assertIsNone(ids["faceit-cxl"]["captureStatus"])

    def test_undiscovered_match_not_fabricated(self):
        # A plain match with no competition_id/lifecycle and no ingest run must
        # NOT appear (the exporter only surfaces reviewed CV runs + discovered
        # matches; it never invents calendar rows).
        base = (NOW + dt.timedelta(days=1)).isoformat()
        _insert_match(self.con, "manual-1", date=base[:10], scheduled_at=base,
                      status="upcoming", lifecycle=None, capture=None,
                      competition_id=None)
        pl = self._payload()
        self.assertNotIn("manual-1", {m["id"] for m in pl["matches"]})

    def test_end_to_end_discovery_then_export(self):
        # Full path: FACEIT fixture -> discovery upsert -> public export shows it.
        import automation.faceit_api as fa
        import json as _json

        sched = int((NOW + dt.timedelta(days=4)).timestamp())
        raw = {"match_id": "1-e2e", "competition_id": "CH", "status": "SCHEDULED",
               "scheduled_at": sched, "region": "EU",
               "faceit_url": "https://www.faceit.com/{lang}/ow2/room/1-e2e",
               "teams": {"faction1": {"team_id": "TA", "name": "Alpha", "roster": []},
                         "faction2": {"team_id": "TB", "name": "Bravo", "roster": []}},
               "results": {}}

        def _t(url, headers):
            return (200, _json.dumps({"items": [raw]}), None) if "/matches" in url else (404, None, "x")

        client = fa.FaceitClient(transport=_t)
        comp = {"id": "c_na", "championshipId": "CH", "region": "na",
                "name": "OWCS NA", "enabled": True, "tier": 1, "stage": "Stage 2"}
        from automation.config import AutomationConfig, DEFAULTS
        disc.sync_faceit(con=self.con, store=None,
                         client=client, config=AutomationConfig(values=dict(DEFAULTS)),
                         competitions=[comp], now=NOW, dry_run=False)
        pl = self._payload()
        self.assertIn("faceit-1-e2e", {m["id"] for m in pl["matches"]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
