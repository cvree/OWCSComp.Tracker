#!/usr/bin/env python3
"""
test_automation_discovery.py — Phase B discovery orchestrator.

Covers the roadmap's required scenarios: idempotent repeat sync, multiple
tournaments/regions, changed start times, cancellation/forfeit, duplicate
teams/aliases, the 14-day boundary, partial API responses, API failures ->
retry jobs, stable public ids, dry-run purity, no composition leakage, and no
fixture contamination. No network, no API key.
Run: python3 pipeline/test_automation_discovery.py
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
from automation import discovery as disc  # noqa: E402
from automation import faceit_api as fa  # noqa: E402
from automation import job_store as js  # noqa: E402
from automation import models  # noqa: E402
from automation.config import AutomationConfig, DEFAULTS  # noqa: E402

NOW = dt.datetime(2026, 7, 24, 12, 0, 0, tzinfo=dt.timezone.utc)


def epoch(days_from_now: float) -> int:
    return int((NOW + dt.timedelta(days=days_from_now)).timestamp())


def raw_match(mid, status="FINISHED", **kw):
    return {
        "match_id": mid, "competition_id": kw.get("comp_id", "CH_NA"),
        "competition_name": kw.get("comp_name", "OWCS NA"),
        "region": kw.get("region", "EU"), "status": status,
        "scheduled_at": kw.get("scheduled"), "started_at": kw.get("started"),
        "finished_at": kw.get("finished"),
        "faceit_url": "https://www.faceit.com/{lang}/ow2/room/" + mid,
        "teams": {
            "faction1": {"team_id": kw.get("tid_a", "TA"), "name": kw.get("name_a", "Alpha"),
                         "roster": kw.get("roster_a", [{"player_id": "p1", "nickname": "aaa"}])},
            "faction2": {"team_id": kw.get("tid_b", "TB"), "name": kw.get("name_b", "Bravo"),
                         "roster": kw.get("roster_b", [{"player_id": "p2", "nickname": "bbb"}])},
        },
        "results": kw.get("results", {"winner": "faction1", "score": {"faction1": 3, "faction2": 1}}),
    }


def transport_for(mapping: dict) -> fa.Transport:
    """mapping: championshipId -> list[raw match] (or Exception to fail)."""
    import json as _json
    import re as _re

    def _t(url, headers):
        m = _re.search(r"/championships/([^/?]+)/matches", url)
        if not m:
            return 404, None, "unmapped"
        cid = m.group(1)
        val = mapping.get(cid)
        if val is None:
            return 404, None, "no such championship"
        if isinstance(val, Exception):
            return 500, None, str(val)
        return 200, _json.dumps({"items": val}), None
    return _t


def comp(cid="c_na", champ="CH_NA", region="na", name="OWCS NA", **kw):
    return {"id": cid, "championshipId": champ, "region": region, "name": name,
            "enabled": True, "tier": kw.get("tier", 1), "stage": kw.get("stage"),
            "season": kw.get("season", "2026")}


def _cfg(**over):
    v = dict(DEFAULTS); v.update(over)
    return AutomationConfig(values=v)


class DiscoveryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.content_path = os.path.join(self.tmp.name, "owcs.sqlite")
        self.auto_path = os.path.join(self.tmp.name, "automation.sqlite")
        self.con = content_db.connect(self.content_path)
        content_db.init_schema(self.con)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def store(self):
        return js.JobStore(self.auto_path, config=_cfg())

    def client(self, mapping):
        return fa.FaceitClient(transport=transport_for(mapping))

    def run_sync(self, mapping, competitions, **kw):
        store = kw.pop("store", None)
        return disc.sync_faceit(
            con=self.con, store=store, client=self.client(mapping),
            config=_cfg(), competitions=competitions, now=NOW,
            lookback_days=14, horizon_days=30, **kw)


class TestUpsertAndIdempotency(DiscoveryCase):
    def test_idempotent_repeat_sync(self):
        mapping = {"CH_NA": [raw_match("1-a", finished=epoch(-1))]}
        s1 = self.store()
        r1 = self.run_sync(mapping, [comp()], store=s1)
        self.assertEqual(r1["upserted"], 1)
        n1 = self.con.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        jobs1 = len(s1.list_jobs())
        s1.close()
        # Second identical run: no new match rows, no duplicate jobs.
        s2 = self.store()
        r2 = self.run_sync(mapping, [comp()], store=s2)
        n2 = self.con.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        self.assertEqual(n1, n2)
        self.assertEqual(r2["broadcastJobsCreated"], 0)  # already existed
        self.assertEqual(len(s2.list_jobs()), jobs1)
        s2.close()

    def test_stable_public_ids(self):
        r = self.run_sync({"CH_NA": [raw_match("1-XYZ", finished=epoch(-1))]}, [comp()])
        self.assertEqual(r["matches"][0]["id"], "faceit-1-XYZ")
        row = self.con.execute("SELECT source_ref FROM matches WHERE id='faceit-1-XYZ'").fetchone()
        self.assertEqual(row["source_ref"], "faceit:1-XYZ")

    def test_multiple_tournaments_and_regions(self):
        mapping = {
            "CH_NA": [raw_match("1-na", region="EU", finished=epoch(-2))],
            "CH_KR": [raw_match("1-kr", region="Korea", finished=epoch(-3))],
        }
        comps = [comp(cid="c_na", champ="CH_NA", region="na", name="OWCS NA"),
                 comp(cid="c_kr", champ="CH_KR", region="korea", name="OWCS KR")]
        r = self.run_sync(mapping, comps)
        self.assertEqual(r["upserted"], 2)
        regions = {row["region"] for row in self.con.execute("SELECT region FROM matches")}
        self.assertEqual(regions, {"na", "korea"})

    def test_changed_start_time_flagged(self):
        first = {"CH_NA": [raw_match("1-a", status="SCHEDULED", scheduled=epoch(2))]}
        self.run_sync(first, [comp()])
        # Same match rescheduled 3 days later.
        second = {"CH_NA": [raw_match("1-a", status="SCHEDULED", scheduled=epoch(5))]}
        r = self.run_sync(second, [comp()])
        self.assertEqual(r["rescheduled"], ["faceit-1-a"])
        sched = self.con.execute("SELECT scheduled_at FROM matches WHERE id='faceit-1-a'").fetchone()[0]
        self.assertEqual(sched[:10], (NOW + dt.timedelta(days=5)).date().isoformat())

    def test_cancellation_and_forfeit(self):
        mapping = {"CH_NA": [
            raw_match("1-cancel", status="CANCELLED", scheduled=epoch(-1)),
            raw_match("1-ff", status="FINISHED", finished=epoch(-1),
                      results={"winner": "faction2", "score": {"faction1": 0, "faction2": 0}}),
        ]}
        s = self.store()
        r = self.run_sync(mapping, [comp()], store=s)
        rows = {m["id"]: m for m in self.con.execute("SELECT * FROM matches")}
        self.assertEqual(rows["faceit-1-cancel"]["lifecycle_status"], "cancelled")
        self.assertEqual(rows["faceit-1-cancel"]["status"], "unknown")
        self.assertEqual(rows["faceit-1-cancel"]["capture_status"], "cancelled")
        self.assertEqual(rows["faceit-1-ff"]["lifecycle_status"], "forfeit")
        # Cancelled matches get no broadcast-discovery job.
        keys = {j.job_key for j in s.list_jobs(kind=models.KIND_BROADCAST)}
        self.assertIn("broadcast:match:1-ff", keys)
        self.assertNotIn("broadcast:match:1-cancel", keys)
        s.close()

    def test_duplicate_teams_and_aliases(self):
        # Same faceit team id reused across matches, with a later rename, must
        # resolve to ONE team row (alias-safe), not duplicate.
        mapping = {"CH_NA": [
            raw_match("1-a", tid_a="TEAM-42", name_a="Falcons", finished=epoch(-2)),
            raw_match("1-b", tid_a="TEAM-42", name_a="Team Falcons", finished=epoch(-1)),
        ]}
        self.run_sync(mapping, [comp()])
        rows = self.con.execute(
            "SELECT id, name FROM teams WHERE faceit_team_id='TEAM-42'").fetchall()
        self.assertEqual(len(rows), 1)
        # The rename updated the existing row's name in place.
        self.assertEqual(rows[0]["name"], "Team Falcons")

    def test_14_day_boundary(self):
        mapping = {"CH_NA": [
            raw_match("1-inside", finished=epoch(-13.5)),   # within 14 days
            raw_match("1-outside", finished=epoch(-15)),    # older than 14 days
            raw_match("1-future-ok", status="SCHEDULED", scheduled=epoch(20)),
            raw_match("1-future-far", status="SCHEDULED", scheduled=epoch(40)),  # past horizon
        ]}
        r = self.run_sync(mapping, [comp()])
        ids = {m["id"] for m in r["matches"]}
        self.assertIn("faceit-1-inside", ids)
        self.assertIn("faceit-1-future-ok", ids)
        self.assertNotIn("faceit-1-outside", ids)
        self.assertNotIn("faceit-1-future-far", ids)

    def test_partial_api_response(self):
        # A match missing teams/results must still upsert its id, no crash.
        mapping = {"CH_NA": [{"match_id": "1-partial", "status": "SCHEDULED",
                              "scheduled_at": epoch(1)}]}
        r = self.run_sync(mapping, [comp()])
        self.assertEqual(r["upserted"], 1)
        row = self.con.execute("SELECT * FROM matches WHERE id='faceit-1-partial'").fetchone()
        self.assertEqual(row["status"], "upcoming")

    def test_api_failure_creates_retry_job(self):
        mapping = {"CH_NA": RuntimeError("boom 500")}
        s = self.store()
        r = self.run_sync(mapping, [comp()], store=s)
        self.assertEqual(len(r["errors"]), 1)
        key = models.calendar_key("faceit", "c_na")
        job = s.get(key)
        self.assertIsNotNone(job)
        self.assertEqual(job.state, "RETRY_SCHEDULED")
        self.assertEqual(job.attempts, 1)
        self.assertEqual(job.last_error_code, "FACEIT_API_ERROR")
        s.close()


class TestDryRunAndPurity(DiscoveryCase):
    def test_dry_run_writes_nothing(self):
        mapping = {"CH_NA": [raw_match("1-a", finished=epoch(-1))]}
        r = disc.sync_faceit(con=self.con, store=None, client=self.client(mapping),
                             config=_cfg(), competitions=[comp()], now=NOW, dry_run=True)
        self.assertEqual(r["inWindow"], 1)
        self.assertEqual(r["matches"][0]["action"], "would-upsert")
        # No content rows written.
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM matches").fetchone()[0], 0)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM teams").fetchone()[0], 0)

    def test_no_composition_leakage(self):
        # Discovery must never write comp/snapshot rows, even with full rosters.
        mapping = {"CH_NA": [raw_match(
            "1-a", finished=epoch(-1),
            roster_a=[{"player_id": f"p{i}", "nickname": f"na{i}"} for i in range(5)],
            roster_b=[{"player_id": f"q{i}", "nickname": f"nb{i}"} for i in range(5)])]}
        self.run_sync(mapping, [comp()])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM comp_snapshots").fetchone()[0], 0)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM snapshot_heroes").fetchone()[0], 0)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM hero_stints").fetchone()[0], 0)
        # rosters/players ARE written (those are facts).
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM players").fetchone()[0], 10)

    def test_no_fixture_contamination(self):
        # Only the configured competition's matches land; nothing else appears,
        # and map_results stays empty (no invented maps from facts-only sync).
        mapping = {"CH_NA": [raw_match("1-a", finished=epoch(-1))]}
        self.run_sync(mapping, [comp()])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM matches").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM map_results").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
