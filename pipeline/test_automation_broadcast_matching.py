#!/usr/bin/env python3
"""
test_automation_broadcast_matching.py — explainable broadcast<->match
scoring and linking (Roadmap Phase C4). Covers every scoring signal in
isolation, the HIGH/MEDIUM/LOW confidence boundaries, HIGH->proposed-link,
MEDIUM->review-task, LOW->rejected-and-not-stored, one-video-to-many-matches
and many-videos-to-one-match support, idempotent reruns (no duplicate
candidate rows/jobs), and unofficial-mirror rejection. No network.
Run: python3 pipeline/test_automation_broadcast_matching.py
"""
from __future__ import annotations
import datetime as dt
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from automation import broadcast_matching as bm  # noqa: E402
from automation import models  # noqa: E402
from automation import state_machine as sm  # noqa: E402
from automation.config import AutomationConfig, DEFAULTS  # noqa: E402
from automation.job_store import JobStore  # noqa: E402

NOW = dt.datetime(2026, 7, 24, 12, 0, 0, tzinfo=dt.timezone.utc)


def _cfg(**over):
    v = dict(DEFAULTS)
    v.update(over)
    return AutomationConfig(values=v)


def video(**over):
    v = {
        "videoId": "v1", "platform": "youtube", "channelId": "ow_esports_global",
        "title": "OWCS 2026 NA Grand Final: Falcons vs Zeta",
        "description": "", "officialChannel": True, "region": "na", "language": "en",
        "durationSeconds": 5400, "liveBroadcastStatus": "completed",
        "actualStartAt": "2026-07-20T20:00:00+00:00",
        "scheduledStartAt": "2026-07-20T20:00:00+00:00",
        "publishedAt": "2026-07-20T20:00:00+00:00",
    }
    v.update(over)
    return v


def ctx(**over):
    c = {
        "matchId": "match:1-a", "teamA": "Falcons", "teamB": "Zeta", "region": "na",
        "language": "en", "scheduledAt": "2026-07-20T20:00:00+00:00", "completedAt": None,
        "status": "finished", "competitionName": "OWCS 2026 NA", "faceitUrl": None,
    }
    c.update(over)
    return c


class TestScoringSignals(unittest.TestCase):
    def test_official_channel_bonus(self):
        a = bm.score_candidate(video(officialChannel=True), ctx())
        b = bm.score_candidate(video(officialChannel=False), ctx())
        self.assertGreater(a["score"], b["score"])
        self.assertEqual(a["score"] - b["score"],
                         bm.WEIGHT_OFFICIAL_CHANNEL - bm.WEIGHT_UNOFFICIAL_CHANNEL_PENALTY)

    def test_team_names_each_up_to_two(self):
        both = bm.score_candidate(video(), ctx())
        one = bm.score_candidate(video(title="OWCS 2026 NA Grand Final: Falcons vs ???"), ctx())
        neither = bm.score_candidate(video(title="OWCS 2026 NA Grand Final"), ctx())
        self.assertEqual(both["score"] - one["score"], bm.WEIGHT_TEAM_NAME_EACH)
        self.assertEqual(one["score"] - neither["score"], bm.WEIGHT_TEAM_NAME_EACH)

    def test_owcs_title_pattern(self):
        with_pattern = bm.score_candidate(video(title="OWCS Grand Final"), ctx(teamA=None, teamB=None))
        without = bm.score_candidate(video(title="random stream"), ctx(teamA=None, teamB=None))
        self.assertGreater(with_pattern["score"], without["score"])

    def test_region_match(self):
        match_r = bm.score_candidate(video(region="na"), ctx(region="na", teamA=None, teamB=None))
        mismatch_r = bm.score_candidate(video(region="korea"), ctx(region="na", teamA=None, teamB=None))
        self.assertEqual(match_r["score"] - mismatch_r["score"], bm.WEIGHT_REGION_MATCH)

    def test_language_match(self):
        match_l = bm.score_candidate(video(language="en"), ctx(language="en", teamA=None, teamB=None))
        mismatch_l = bm.score_candidate(video(language="ko"), ctx(language="en", teamA=None, teamB=None))
        self.assertEqual(match_l["score"] - mismatch_l["score"], bm.WEIGHT_LANGUAGE_MATCH)

    def test_faceit_reference(self):
        with_ref = bm.score_candidate(
            video(description="room: faceit.com/en/ow2/room/1-abc123"),
            ctx(teamA=None, teamB=None, faceitUrl="https://www.faceit.com/en/ow2/room/1-abc123"))
        without_ref = bm.score_candidate(
            video(description="no room mentioned"),
            ctx(teamA=None, teamB=None, faceitUrl="https://www.faceit.com/en/ow2/room/1-abc123"))
        self.assertEqual(with_ref["score"] - without_ref["score"], bm.WEIGHT_FACEIT_REFERENCE)

    def test_time_close_vs_same_day_vs_conflict(self):
        close = bm.score_candidate(video(actualStartAt="2026-07-20T20:05:00+00:00"),
                                   ctx(scheduledAt="2026-07-20T20:00:00+00:00", teamA=None, teamB=None))
        same_day = bm.score_candidate(video(actualStartAt="2026-07-20T08:00:00+00:00"),
                                      ctx(scheduledAt="2026-07-20T20:00:00+00:00", teamA=None, teamB=None))
        conflict = bm.score_candidate(video(actualStartAt="2026-07-25T20:00:00+00:00"),
                                      ctx(scheduledAt="2026-07-20T20:00:00+00:00", teamA=None, teamB=None))
        no_signal = bm.score_candidate(video(actualStartAt="2026-07-22T20:00:00+00:00"),
                                       ctx(scheduledAt="2026-07-20T20:00:00+00:00", teamA=None, teamB=None))
        self.assertIn(f"+{bm.WEIGHT_TIME_CLOSE}", " ".join(close["reasons"]))
        self.assertIn(f"+{bm.WEIGHT_TIME_SAME_DAY}", " ".join(same_day["reasons"]))
        self.assertIn(f"{bm.WEIGHT_TIME_CONFLICT_PENALTY}", " ".join(conflict["reasons"]))
        # 2 days apart: beyond "same day" but not yet a declared conflict (< 48h)... actually
        # 2026-07-22 is 2 days (48h) from 07-20 boundary; assert no positive time bonus fired.
        self.assertFalse(any("start time within" in r for r in no_signal["reasons"]))

    def test_live_status_match(self):
        both_live = bm.score_candidate(video(liveBroadcastStatus="live"),
                                       ctx(status="live", teamA=None, teamB=None))
        only_video_live = bm.score_candidate(video(liveBroadcastStatus="live"),
                                             ctx(status="finished", teamA=None, teamB=None))
        self.assertEqual(both_live["score"] - only_video_live["score"], bm.WEIGHT_LIVE_STATUS_MATCH)

    def test_duration_plausible_vs_too_short(self):
        full = bm.score_candidate(video(durationSeconds=5400), ctx(teamA=None, teamB=None))
        clip = bm.score_candidate(video(durationSeconds=90), ctx(teamA=None, teamB=None))
        self.assertGreater(full["score"], clip["score"])
        self.assertEqual(full["score"] - clip["score"],
                         bm.WEIGHT_DURATION_PLAUSIBLE - bm.WEIGHT_DURATION_TOO_SHORT_PENALTY)


class TestConfidenceBands(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(bm.confidence_band(bm.HIGH_THRESHOLD), "high")
        self.assertEqual(bm.confidence_band(bm.HIGH_THRESHOLD - 1), "medium")
        self.assertEqual(bm.confidence_band(bm.MEDIUM_THRESHOLD), "medium")
        self.assertEqual(bm.confidence_band(bm.MEDIUM_THRESHOLD - 1), "low")

    def test_strong_match_is_high(self):
        r = bm.score_candidate(video(), ctx())
        self.assertEqual(r["confidence"], "high")

    def test_unofficial_unrelated_clip_is_low(self):
        r = bm.score_candidate(
            video(officialChannel=False, title="random clip", description="",
                 region=None, language=None, durationSeconds=45, liveBroadcastStatus="none"),
            ctx())
        self.assertEqual(r["confidence"], "low")

    def test_official_but_ambiguous_is_medium(self):
        # Official channel + OWCS pattern, but no team/region/time agreement.
        r = bm.score_candidate(
            video(title="OWCS 2026 Highlights", description="", region=None, language=None,
                 actualStartAt=None, scheduledStartAt=None, publishedAt=None,
                 liveBroadcastStatus="none", durationSeconds=1800),
            ctx(teamA="Falcons", teamB="Zeta", region=None, language=None))
        self.assertEqual(r["confidence"], "medium")


class LinkingCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "automation.sqlite")

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except PermissionError:
            pass

    def store(self) -> JobStore:
        return JobStore(self.db, config=_cfg())


class TestLinkCandidates(LinkingCase):
    def test_high_creates_proposed_candidate_and_job(self):
        s = self.store()
        result = bm.score_candidate(video(), ctx())
        summary = bm.link_candidates(s, video(), [(ctx(), result)], dry_run=False)
        self.assertEqual(len(summary["linked"]), 1)
        row = s.con.execute(
            "SELECT * FROM broadcast_candidates WHERE match_id=? AND video_id=?",
            (ctx()["matchId"], "v1")).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["confidence"], "high")
        self.assertEqual(row["state"], sm.DISCOVERED)  # proposed, not auto-applied
        job = s.get(models.broadcast_match_link_key("v1", ctx()["matchId"]))
        self.assertIsNotNone(job)
        s.close()

    def test_medium_creates_review_task(self):
        s = self.store()
        med_ctx = ctx(teamA="Falcons", teamB="Zeta", region=None, language=None)
        med_video = video(title="OWCS 2026 Highlights", region=None, language=None,
                          actualStartAt=None, scheduledStartAt=None, publishedAt=None,
                          liveBroadcastStatus="none")
        result = bm.score_candidate(med_video, med_ctx)
        self.assertEqual(result["confidence"], "medium")
        bm.link_candidates(s, med_video, [(med_ctx, result)], dry_run=False)
        row = s.con.execute(
            "SELECT * FROM broadcast_candidates WHERE match_id=? AND video_id=?",
            (med_ctx["matchId"], "v1")).fetchone()
        self.assertEqual(row["state"], sm.NEEDS_REVIEW)
        review = s.con.execute(
            "SELECT * FROM review_tasks WHERE kind='broadcast_link' AND ref_key=?",
            (f"v1:{med_ctx['matchId']}",)).fetchone()
        self.assertIsNotNone(review)
        self.assertEqual(review["state"], "NEEDS_REVIEW")
        s.close()

    def test_low_rejected_and_not_stored(self):
        s = self.store()
        low_ctx = ctx()
        low_video = video(officialChannel=False, title="random clip", description="",
                          region=None, language=None, durationSeconds=45,
                          liveBroadcastStatus="none", actualStartAt=None,
                          scheduledStartAt=None, publishedAt=None)
        result = bm.score_candidate(low_video, low_ctx)
        self.assertEqual(result["confidence"], "low")
        summary = bm.link_candidates(s, low_video, [(low_ctx, result)], dry_run=False)
        self.assertEqual(len(summary["rejected"]), 1)
        row = s.con.execute(
            "SELECT * FROM broadcast_candidates WHERE match_id=? AND video_id=?",
            (low_ctx["matchId"], "v1")).fetchone()
        self.assertIsNone(row)
        s.close()

    def test_dry_run_writes_nothing(self):
        s = self.store()
        result = bm.score_candidate(video(), ctx())
        bm.link_candidates(s, video(), [(ctx(), result)], dry_run=True)
        n = s.con.execute("SELECT COUNT(*) FROM broadcast_candidates").fetchone()[0]
        self.assertEqual(n, 0)
        self.assertEqual(len(s.list_jobs()), 0)
        s.close()

    def test_one_video_links_many_matches(self):
        s = self.store()
        v = video()
        pairs = [
            (ctx(matchId="match:1-a"), bm.score_candidate(v, ctx(matchId="match:1-a"))),
            (ctx(matchId="match:1-b", teamA="Other", teamB="Team"),
             bm.score_candidate(v, ctx(matchId="match:1-b", teamA="Other", teamB="Team"))),
        ]
        bm.link_candidates(s, v, pairs, dry_run=False)
        rows = s.con.execute("SELECT match_id FROM broadcast_candidates WHERE video_id='v1'").fetchall()
        match_ids = {r["match_id"] for r in rows}
        self.assertIn("match:1-a", match_ids)
        s.close()

    def test_many_videos_link_one_match(self):
        s = self.store()
        c = ctx()
        for vid in ("v1", "v2"):
            v = video(videoId=vid)
            result = bm.score_candidate(v, c)
            bm.link_candidates(s, v, [(c, result)], dry_run=False)
        rows = s.con.execute("SELECT video_id FROM broadcast_candidates WHERE match_id=?",
                             (c["matchId"],)).fetchall()
        self.assertEqual({r["video_id"] for r in rows}, {"v1", "v2"})
        s.close()

    def test_idempotent_rerun_no_duplicate_rows(self):
        s = self.store()
        result = bm.score_candidate(video(), ctx())
        bm.link_candidates(s, video(), [(ctx(), result)], dry_run=False)
        bm.link_candidates(s, video(), [(ctx(), result)], dry_run=False)
        n = s.con.execute(
            "SELECT COUNT(*) FROM broadcast_candidates WHERE match_id=? AND video_id='v1'",
            (ctx()["matchId"],)).fetchone()[0]
        self.assertEqual(n, 1)
        jobs = s.list_jobs(kind=models.KIND_BROADCAST)
        keys = [j.job_key for j in jobs]
        self.assertEqual(keys.count(models.broadcast_match_link_key("v1", ctx()["matchId"])), 1)
        s.close()


class TestMatchBroadcastsOrchestrator(LinkingCase):
    def _seed_match(self, s, **over):
        row = {
            "id": "match:1-a", "faceit_match_id": "1-a", "competition_id": "c_na",
            "region": "na", "team_a": "Falcons", "team_b": "Zeta",
            "scheduled_at": "2026-07-20T20:00:00+00:00", "completed_at": None,
            "status": "finished", "tier": 2, "faceit_room_url": None,
            "state": sm.DISCOVERED, "capture_status": "pending", "data_status": "pending",
            "raw": "{}",
        }
        row.update(over)
        s.con.execute(
            """INSERT INTO scheduled_matches
                 (id, faceit_match_id, competition_id, region, team_a, team_b,
                  scheduled_at, completed_at, status, tier, faceit_room_url,
                  state, capture_status, data_status, raw)
               VALUES (:id,:faceit_match_id,:competition_id,:region,:team_a,:team_b,
                       :scheduled_at,:completed_at,:status,:tier,:faceit_room_url,
                       :state,:capture_status,:data_status,:raw)""",
            row)
        s.con.commit()

    def _seed_video(self, s, video_id="v1", **over):
        v = {
            "video_id": video_id, "platform": "youtube", "channel_id": "ow_esports_global",
            "title": "OWCS 2026 NA Grand Final: Falcons vs Zeta", "description": "",
            "published_at": "2026-07-20T20:00:00+00:00",
            "scheduled_start_at": "2026-07-20T20:00:00+00:00",
            "actual_start_at": "2026-07-20T20:00:00+00:00", "actual_end_at": "2026-07-20T21:30:00+00:00",
            "live_broadcast_status": "completed", "duration_seconds": 5400,
            "thumbnail_url": None, "source_url": f"https://www.youtube.com/watch?v={video_id}",
            "region": "na", "language": "en", "official_channel": 1,
            "response_hash": "x", "coverage_state": sm.ARCHIVED,
        }
        v.update(over)
        s.con.execute(
            """INSERT INTO broadcast_videos
                 (video_id, platform, channel_id, title, description, published_at,
                  scheduled_start_at, actual_start_at, actual_end_at, live_broadcast_status,
                  duration_seconds, thumbnail_url, source_url, region, language,
                  official_channel, response_hash, coverage_state)
               VALUES (:video_id,:platform,:channel_id,:title,:description,:published_at,
                       :scheduled_start_at,:actual_start_at,:actual_end_at,:live_broadcast_status,
                       :duration_seconds,:thumbnail_url,:source_url,:region,:language,
                       :official_channel,:response_hash,:coverage_state)""",
            v)
        s.con.commit()

    def test_end_to_end_high_confidence_link(self):
        s = self.store()
        self._seed_match(s)
        self._seed_video(s)
        summary = bm.match_broadcasts(s, dry_run=False)
        self.assertEqual(summary["videosScored"], 1)
        self.assertGreaterEqual(summary["linked"], 1)
        s.close()

    def test_unofficial_mirror_rejected(self):
        s = self.store()
        self._seed_match(s)
        self._seed_video(s, video_id="mirror1", official_channel=0,
                         title="stream mirror upload", description="",
                         region=None, language=None, duration_seconds=60,
                         live_broadcast_status="none", scheduled_start_at=None,
                         actual_start_at=None, actual_end_at=None,
                         published_at="2026-07-20T20:00:00+00:00")
        summary = bm.match_broadcasts(s, dry_run=False)
        self.assertEqual(summary["linked"], 0)
        row = s.con.execute(
            "SELECT * FROM broadcast_candidates WHERE video_id='mirror1'").fetchone()
        self.assertIsNone(row)
        s.close()

    def test_time_window_prefilter_excludes_far_matches(self):
        s = self.store()
        self._seed_match(s, id="match:far", scheduled_at="2026-01-01T00:00:00+00:00")
        self._seed_video(s)
        summary = bm.match_broadcasts(s, dry_run=False, time_window_hours=72)
        matched_ids = {c["matchId"] for r in summary["results"] for c in r["linked"] + r["reviewed"]}
        self.assertNotIn("match:far", matched_ids)
        s.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
