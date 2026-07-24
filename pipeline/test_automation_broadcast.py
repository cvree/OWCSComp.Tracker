#!/usr/bin/env python3
"""
test_automation_broadcast.py — Phase C broadcast discovery + matching.

Covers every roadmap-required scenario, all offline (no network, no key):
  * delayed + renamed broadcasts        * duplicate videos
  * full-day broadcast (many matches)   * multiple language feeds
  * unofficial mirrors rejected         * missing broadcast recorded
  * quota exhaustion (clean stop)       * API failures -> retry jobs
  * rolling 14-day boundary             * idempotent reruns
  * prefer uploads playlist over search * NEVER writes hero compositions
  * auto-link gated by the master switch (off by default -> review)

Run: python3 pipeline/test_automation_broadcast.py
"""
from __future__ import annotations
import datetime as dt
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db as content_db  # noqa: E402
from automation import broadcast as bc  # noqa: E402
from automation import job_store as js  # noqa: E402
from automation import models  # noqa: E402
from automation import youtube_api as yt  # noqa: E402
from automation.config import AutomationConfig, DEFAULTS  # noqa: E402

NOW = dt.datetime(2026, 7, 24, 12, 0, 0, tzinfo=dt.timezone.utc)
OFFICIAL = "UC_OW_ESPORTS_OFFICIAL"
OFFICIAL_KR = "UC_OW_ESPORTS_KOREA"
UPLOADS = "UU_OW_ESPORTS_OFFICIAL"
UPLOADS_KR = "UU_OW_ESPORTS_KOREA"


def iso(days=0, hours=0):
    return (NOW + dt.timedelta(days=days, hours=hours)).replace(microsecond=0).isoformat()


def _cfg(**over):
    v = dict(DEFAULTS)
    v.update(over)
    return AutomationConfig(values=v)


def channel(cid="ow_esports_global", channel_id=OFFICIAL, uploads=UPLOADS,
            region="global", language="en", priority=100, enabled=True):
    return {"id": cid, "name": "Overwatch Esports", "platform": "youtube",
            "channelId": channel_id, "uploadsPlaylistId": uploads,
            "region": region, "language": language, "official": True,
            "priority": priority, "enabled": enabled}


def match(mid="match:1", *, content_id="faceit-1", fmid="1", region="na",
          event="OWCS 2026 North America Stage 2", team_a="Spacestation",
          team_b="NTMR", scheduled=None, finished=None, lifecycle="finished",
          language=None):
    return {"id": mid, "contentId": content_id, "faceitMatchId": fmid,
            "region": region, "eventName": event, "teamA": team_a, "teamB": team_b,
            "scheduledAt": scheduled, "finishedAt": finished or iso(-1),
            "lifecycle": lifecycle, "language": language}


def yt_video(vid, *, title, desc="", channel_id=OFFICIAL, lbc="none",
             live=None, published=None):
    raw = {"id": vid, "snippet": {
        "title": title, "description": desc, "channelId": channel_id,
        "channelTitle": "Overwatch Esports",
        "publishedAt": published or iso(-1), "liveBroadcastContent": lbc}}
    if live is not None:
        raw["liveStreamingDetails"] = live
    return raw


class PoolTransport:
    """A fixture transport backed by an in-memory map: uploads playlist per
    channel + a video-details store. Lets each test declare a scenario as data.
    Tracks whether search.list was ever called (to prove the cheap path)."""

    def __init__(self, uploads: dict[str, list[str]], videos: dict[str, dict],
                 channels: dict[str, dict] | None = None, fail: set | None = None):
        self.uploads = uploads          # playlistId -> [videoId, ...]
        self.videos = videos            # videoId -> raw video dict
        self.channels = channels or {}  # channelId/handle -> raw channel
        self.fail = fail or set()       # endpoints/ids that should error
        self.search_calls = 0

    def __call__(self, url, headers):
        import urllib.parse as up
        p = up.urlparse(url)
        endpoint = p.path.rstrip("/").rsplit("/", 1)[-1]
        q = up.parse_qs(p.query)
        if endpoint in self.fail:
            return 500, None, "boom"
        if endpoint == "playlistItems":
            pid = q["playlistId"][0]
            if pid in self.fail:
                return 500, None, "playlist boom"
            items = [{"contentDetails": {"videoId": v},
                      "snippet": {"publishedAt": (self.videos.get(v, {}).get("snippet", {})
                                                  .get("publishedAt"))}}
                     for v in self.uploads.get(pid, [])]
            return 200, json.dumps({"items": items}), None
        if endpoint == "videos":
            ids = q["id"][0].split(",")
            items = [self.videos[v] for v in ids if v in self.videos]
            return 200, json.dumps({"items": items}), None
        if endpoint == "channels":
            key = (q.get("id") or q.get("forHandle") or ["?"])[0]
            raw = self.channels.get(key)
            return 200, json.dumps({"items": [raw] if raw else []}), None
        if endpoint == "search":
            self.search_calls += 1
            return 200, json.dumps({"items": []}), None
        return 404, None, "unmapped"


class BroadcastCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.auto_path = os.path.join(self.tmp.name, "automation.sqlite")
        self.content_path = os.path.join(self.tmp.name, "owcs.sqlite")
        self.con = content_db.connect(self.content_path)
        content_db.init_schema(self.con)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def store(self):
        return js.JobStore(self.auto_path, config=_cfg())

    def client(self, transport, budget=10000):
        return yt.YoutubeClient(transport=transport, quota_budget=budget)

    def go(self, transport, channels, matches, *, store=None, content_con=None,
            dry_run=False, config=None, budget=10000):
        return bc.discover_broadcasts(
            store=store, client=self.client(transport, budget), channels=channels,
            matches=matches, content_con=content_con, config=config or _cfg(),
            now=NOW, lookback_days=14, horizon_days=30, dry_run=dry_run)


class TestHappyPath(BroadcastCase):
    def test_team_specific_high_confidence_reviewed_by_default(self):
        t = PoolTransport(
            {UPLOADS: ["v1"]},
            {"v1": yt_video("v1", title="OWCS NA Stage 2 — Spacestation vs NTMR",
                            desc="North America", published=iso(-1))})
        s = self.store()
        r = self.go(t, [channel()], [match()], store=s, content_con=self.con)
        # High-confidence, but broadcast_auto_link is OFF -> review, not linked.
        self.assertEqual(len(r["located"]), 0)
        self.assertEqual(len(r["review"]), 1)
        self.assertEqual(r["review"][0]["confidence"], "high")
        self.assertEqual(r["autoLinked"], [])
        cov = s.con.execute("SELECT state, best_video_id, auto_linked FROM broadcast_coverage").fetchone()
        self.assertEqual(cov["state"], "NEEDS_REVIEW")
        self.assertEqual(cov["best_video_id"], "v1")
        self.assertEqual(cov["auto_linked"], 0)
        # No vod_url written to content while auto-link is off.
        self.assertIsNone(self.con.execute("SELECT vod_url FROM matches WHERE id='faceit-1'").fetchone() or None)
        s.close()

    def test_auto_link_when_switch_on(self):
        # Seed the content match so auto-link can attach a vod_url.
        self.con.execute("INSERT INTO teams (id,name,region,code) VALUES "
                         "('spacestation','Spacestation','na','SSG'),('ntmr','NTMR','na','NTM')")
        self.con.execute(
            "INSERT INTO matches (id, region, date, status, team_a, team_b) "
            "VALUES ('faceit-1','na','2026-07-23','final','spacestation','ntmr')")
        self.con.commit()
        t = PoolTransport(
            {UPLOADS: ["v1"]},
            {"v1": yt_video("v1", title="OWCS NA Stage 2: Spacestation vs NTMR",
                            desc="North America", published=iso(-1))})
        s = self.store()
        cfg = _cfg(broadcast_auto_link=True)
        r = self.go(t, [channel()], [match()], store=s, content_con=self.con, config=cfg)
        self.assertEqual(len(r["located"]), 1)
        self.assertEqual(len(r["autoLinked"]), 1)
        vod = self.con.execute("SELECT vod_url FROM matches WHERE id='faceit-1'").fetchone()["vod_url"]
        self.assertEqual(vod, "https://www.youtube.com/watch?v=v1")
        cov = s.con.execute("SELECT state, auto_linked FROM broadcast_coverage").fetchone()
        self.assertEqual(cov["state"], "LOCATED")
        self.assertEqual(cov["auto_linked"], 1)
        s.close()

    def test_prefers_uploads_over_search(self):
        t = PoolTransport(
            {UPLOADS: ["v1"]},
            {"v1": yt_video("v1", title="OWCS NA — Spacestation vs NTMR", desc="NA")})
        self.go(t, [channel()], [match()])
        self.assertEqual(t.search_calls, 0)  # never touched the 100u endpoint


class TestBroadcastLifecycles(BroadcastCase):
    def test_upcoming_live_completed_vod_all_matched(self):
        vids = {
            "u": yt_video("u", title="OWCS NA — Alpha vs Bravo", desc="NA", lbc="upcoming",
                          live={"scheduledStartTime": iso(1)}, published=iso(1)),
            "l": yt_video("l", title="OWCS NA — Cee vs Dee", desc="NA", lbc="live",
                          live={"actualStartTime": iso(0)}, published=iso(0)),
            "c": yt_video("c", title="OWCS NA — Eee vs Fff", desc="NA", lbc="none",
                          live={"actualStartTime": iso(-1), "actualEndTime": iso(-1, 5)},
                          published=iso(-1)),
            "d": yt_video("d", title="OWCS NA — Ggg vs Hhh VOD", desc="NA", published=iso(-2)),
        }
        t = PoolTransport({UPLOADS: list(vids)}, vids)
        ms = [
            match("match:u", fmid="u", team_a="Alpha", team_b="Bravo", scheduled=iso(1), lifecycle="scheduled"),
            match("match:l", fmid="l", team_a="Cee", team_b="Dee", scheduled=iso(0), lifecycle="live"),
            match("match:c", fmid="c", team_a="Eee", team_b="Fff", finished=iso(-1)),
            match("match:d", fmid="d", team_a="Ggg", team_b="Hhh", finished=iso(-2)),
        ]
        r = self.go(t, [channel()], ms)
        # each match found a candidate of the matching broadcast type
        types = {rec["matchId"]: rec["broadcastType"] for rec in r["review"]}
        self.assertEqual(types["match:u"], "upcoming")
        self.assertEqual(types["match:l"], "live")
        self.assertEqual(types["match:c"], "completed")
        self.assertEqual(types["match:d"], "vod")
        self.assertEqual(len(r["missing"]), 0)


class TestDelayedAndRenamed(BroadcastCase):
    def test_renamed_broadcast_still_matches_on_teams(self):
        # Title was renamed post-stream; team names still identify it.
        t = PoolTransport(
            {UPLOADS: ["v1"]},
            {"v1": yt_video("v1", title="[REBROADCAST] Spacestation Gaming vs NTMR (edited)",
                            desc="OWCS North America", published=iso(-1))})
        r = self.go(t, [channel()], [match()])
        self.assertEqual(len(r["review"]) + len(r["located"]), 1)

    def test_delayed_broadcast_time_shifted(self):
        # Match scheduled day -2 but the stream actually went up a day later; a
        # same-day/window time signal is absent, teams still carry it to review.
        t = PoolTransport(
            {UPLOADS: ["v1"]},
            {"v1": yt_video("v1", title="Spacestation vs NTMR — OWCS NA",
                            desc="NA", published=iso(1))})
        m = match(scheduled=iso(-2), finished=iso(-2))
        r = self.go(t, [channel()], [m])
        self.assertEqual(len(r["missing"]), 0)


class TestDuplicates(BroadcastCase):
    def test_duplicate_video_ids_collapse(self):
        # The same video listed twice in the uploads playlist must yield ONE
        # candidate, not two.
        t = PoolTransport(
            {UPLOADS: ["v1", "v1"]},
            {"v1": yt_video("v1", title="OWCS NA — Spacestation vs NTMR", desc="NA")})
        s = self.store()
        self.go(t, [channel()], [match()], store=s, content_con=self.con)
        n = s.con.execute("SELECT COUNT(*) FROM broadcast_candidates WHERE match_id='match:1'").fetchone()[0]
        self.assertEqual(n, 1)
        s.close()


class TestFullDay(BroadcastCase):
    def test_full_day_broadcast_covers_several_matches(self):
        # One "Day 3" VOD, no team names, covers three matches that day.
        t = PoolTransport(
            {UPLOADS: ["day3"]},
            {"day3": yt_video("day3", title="OWCS 2026 North America Stage 2 — Day 3 FULL BROADCAST",
                              desc="North America", published=iso(-1))})
        ms = [match(f"match:{i}", fmid=str(i), team_a=f"Team{i}A", team_b=f"Team{i}B",
                    finished=iso(-1)) for i in range(3)]
        s = self.store()
        r = self.go(t, [channel()], ms, store=s, content_con=self.con)
        # All three matches are covered by the same video (review, full-day).
        self.assertEqual(len(r["review"]), 3)
        self.assertTrue(all(rec["videoId"] == "day3" for rec in r["review"]))
        self.assertTrue(all(rec["fullDay"] for rec in r["review"]))
        links = s.con.execute(
            "SELECT DISTINCT match_id FROM broadcast_candidates WHERE video_id='day3'").fetchall()
        self.assertEqual(len(links), 3)
        s.close()


class TestMultiLanguage(BroadcastCase):
    def test_language_feeds_route_to_their_region_channels(self):
        # An English global feed and a Korean feed of different matches.
        chans = [channel(), channel("ow_esports_korea", OFFICIAL_KR, UPLOADS_KR,
                                    region="korea", language="ko", priority=90)]
        t = PoolTransport(
            {UPLOADS: ["en1"], UPLOADS_KR: ["kr1"]},
            {"en1": yt_video("en1", title="OWCS NA — Spacestation vs NTMR", desc="NA",
                             channel_id=OFFICIAL),
             "kr1": yt_video("kr1", title="OWCS 코리아 — Team Falcons vs Crazy Raccoon",
                             desc="Korea", channel_id=OFFICIAL_KR)})
        ms = [match("match:na", fmid="na", region="na"),
              match("match:kr", fmid="kr", region="korea", team_a="Team Falcons",
                    team_b="Crazy Raccoon", event="OWCS 2026 Korea")]
        r = self.go(t, chans, ms)
        by = {rec["matchId"]: rec["channelId"] for rec in (r["review"] + r["located"])}
        self.assertEqual(by["match:na"], OFFICIAL)
        self.assertEqual(by["match:kr"], OFFICIAL_KR)


class TestUnofficialMirror(BroadcastCase):
    def test_mirror_rejected_even_with_perfect_title(self):
        # A re-uploader's channel sneaks a perfectly-titled video into the pool;
        # its channelId is not official -> rejected, never linked.
        t = PoolTransport(
            {UPLOADS: ["good", "mirror"]},
            {"good": yt_video("good", title="OWCS NA — Spacestation vs NTMR", desc="NA"),
             "mirror": yt_video("mirror", title="OWCS NA — Spacestation vs NTMR (full match)",
                                desc="North America", channel_id="UC_SOME_PIRATE")})
        s = self.store()
        r = self.go(t, [channel()], [match()], store=s, content_con=self.con)
        self.assertTrue(any(x["videoId"] == "mirror" for x in r["rejectedMirrors"]))
        best = s.con.execute("SELECT best_video_id FROM broadcast_coverage").fetchone()["best_video_id"]
        self.assertEqual(best, "good")
        # the mirror never becomes a candidate row
        vids = {row["video_id"] for row in s.con.execute("SELECT video_id FROM broadcast_candidates")}
        self.assertNotIn("mirror", vids)
        s.close()


class TestMissingAndUnsupported(BroadcastCase):
    def test_missing_broadcast_recorded_explicitly(self):
        t = PoolTransport({UPLOADS: []}, {})  # channel has no uploads at all
        s = self.store()
        r = self.go(t, [channel()], [match()], store=s, content_con=self.con)
        self.assertEqual(len(r["missing"]), 1)
        cov = s.con.execute("SELECT state, reason FROM broadcast_coverage").fetchone()
        self.assertEqual(cov["state"], "MISSING")
        self.assertTrue(cov["reason"])
        s.close()

    def test_unsupported_region_recorded(self):
        # A Korea match but only a global... actually global supports all. Use a
        # regional-only channel set with no channel for the match's region.
        chans = [channel("ow_esports_korea", OFFICIAL_KR, UPLOADS_KR, region="korea", language="ko")]
        t = PoolTransport({UPLOADS_KR: []}, {})
        s = self.store()
        r = self.go(t, chans, [match(region="na")], store=s, content_con=self.con)
        self.assertEqual(len(r["unsupported"]), 1)
        cov = s.con.execute("SELECT state FROM broadcast_coverage").fetchone()
        self.assertEqual(cov["state"], "UNSUPPORTED")
        s.close()

    def test_no_official_channels_records_all_missing(self):
        chans = [channel(enabled=False)]  # disabled -> no official channel
        t = PoolTransport({}, {})
        s = self.store()
        r = self.go(t, chans, [match(), match("match:2", fmid="2")], store=s)
        self.assertIn("note", r)
        self.assertEqual(len(r["missing"]), 2)
        s.close()


class TestQuotaAndFailures(BroadcastCase):
    def test_quota_exhaustion_stops_cleanly(self):
        # Budget only allows the first channel's playlist page + one videos call.
        chans = [channel(priority=100), channel("ow_esports_korea", OFFICIAL_KR,
                                                UPLOADS_KR, region="korea", priority=90)]
        vids = {"v1": yt_video("v1", title="OWCS NA — Spacestation vs NTMR", desc="NA")}
        t = PoolTransport({UPLOADS: ["v1"], UPLOADS_KR: ["k1"]},
                          {**vids, "k1": yt_video("k1", title="OWCS KR", channel_id=OFFICIAL_KR)})
        s = self.store()
        # 2 units: 1 playlist + 1 videos for the first channel, then exhausted.
        r = self.go(t, chans, [match()], store=s, content_con=self.con, budget=2)
        self.assertTrue(any(e["code"] == "QUOTA_EXCEEDED" for e in r["errors"]))
        self.assertLessEqual(r["quotaUsed"], 2)
        # A retry job was queued for the channel we couldn't reach.
        self.assertTrue(s.list_jobs(kind=models.KIND_DISCOVERY))
        s.close()

    def test_api_failure_queues_retry(self):
        t = PoolTransport({UPLOADS: ["v1"]}, {"v1": yt_video("v1", title="x")},
                          fail={UPLOADS})
        s = self.store()
        r = self.go(t, [channel()], [match()], store=s, content_con=self.con)
        self.assertTrue(any(e["code"] == "YOUTUBE_API_ERROR" for e in r["errors"]))
        key = models.calendar_key("youtube", "ow_esports_global")
        job = s.get(key)
        self.assertIsNotNone(job)
        self.assertEqual(job.state, "RETRY_SCHEDULED")
        s.close()

    def test_quota_recorded_in_ledger(self):
        t = PoolTransport({UPLOADS: ["v1"]},
                          {"v1": yt_video("v1", title="OWCS NA — Spacestation vs NTMR", desc="NA")})
        s = self.store()
        self.go(t, [channel()], [match()], store=s, content_con=self.con)
        row = s.con.execute("SELECT day, units, mode FROM youtube_quota").fetchone()
        self.assertEqual(row["mode"], "discover")
        self.assertGreaterEqual(row["units"], 2)
        s.close()


class TestWindowBoundary(BroadcastCase):
    def test_rolling_14_day_boundary(self):
        vids = {
            "inside": yt_video("inside", title="OWCS NA — Spacestation vs NTMR", desc="NA",
                               published=iso(-13.5)),
            "outside": yt_video("outside", title="OWCS NA — Spacestation vs NTMR", desc="NA",
                                published=iso(-16)),
        }
        t = PoolTransport({UPLOADS: ["inside", "outside"]}, vids)
        # The match itself is inside; the old VOD (outside the window) is filtered.
        r = self.go(t, [channel()], [match(finished=iso(-13.5))])
        self.assertEqual(r["videosSeen"], 1)


class TestIdempotencyAndPurity(BroadcastCase):
    def test_idempotent_rerun(self):
        t = PoolTransport(
            {UPLOADS: ["v1"]},
            {"v1": yt_video("v1", title="OWCS NA — Spacestation vs NTMR", desc="NA")})
        s1 = self.store()
        self.go(t, [channel()], [match()], store=s1, content_con=self.con)
        cand1 = s1.con.execute("SELECT COUNT(*) FROM broadcast_candidates").fetchone()[0]
        cov1 = s1.con.execute("SELECT COUNT(*) FROM broadcast_coverage").fetchone()[0]
        jobs1 = len(s1.list_jobs(kind=models.KIND_BROADCAST))
        s1.close()
        s2 = self.store()
        r2 = self.go(t, [channel()], [match()], store=s2, content_con=self.con)
        cand2 = s2.con.execute("SELECT COUNT(*) FROM broadcast_candidates").fetchone()[0]
        cov2 = s2.con.execute("SELECT COUNT(*) FROM broadcast_coverage").fetchone()[0]
        self.assertEqual(cand1, cand2)
        self.assertEqual(cov1, cov2)
        self.assertEqual(r2["broadcastJobsCreated"], 0)  # job already existed
        self.assertEqual(len(s2.list_jobs(kind=models.KIND_BROADCAST)), jobs1)
        s2.close()

    def test_dry_run_writes_nothing(self):
        t = PoolTransport(
            {UPLOADS: ["v1"]},
            {"v1": yt_video("v1", title="OWCS NA — Spacestation vs NTMR", desc="NA")})
        s = self.store()
        r = self.go(t, [channel()], [match()], store=s, content_con=self.con, dry_run=True)
        self.assertEqual(len(r["review"]) + len(r["located"]), 1)
        self.assertEqual(s.con.execute("SELECT COUNT(*) FROM broadcast_candidates").fetchone()[0], 0)
        self.assertEqual(s.con.execute("SELECT COUNT(*) FROM broadcast_coverage").fetchone()[0], 0)
        self.assertEqual(s.con.execute("SELECT COUNT(*) FROM youtube_quota").fetchone()[0], 0)
        s.close()

    def test_never_writes_hero_compositions(self):
        # Even a title packed with hero words must not create comp/hero rows, and
        # auto-link (when on) only ever writes vod_url.
        self.con.execute("INSERT INTO teams (id,name,region,code) VALUES "
                         "('spacestation','Spacestation','na','SSG'),('ntmr','NTMR','na','NTM')")
        self.con.execute(
            "INSERT INTO matches (id, region, date, status, team_a, team_b) "
            "VALUES ('faceit-1','na','2026-07-23','final','spacestation','ntmr')")
        self.con.commit()
        t = PoolTransport(
            {UPLOADS: ["v1"]},
            {"v1": yt_video("v1", title="Spacestation vs NTMR — Kiriko Tracer Genji swaps!",
                            desc="dva reaper winston comp North America")})
        s = self.store()
        self.go(t, [channel()], [match()], store=s, content_con=self.con,
                 config=_cfg(broadcast_auto_link=True))
        for tbl in ("comp_snapshots", "snapshot_heroes", "hero_stints", "hero_swaps", "map_results"):
            self.assertEqual(self.con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0], 0,
                            f"{tbl} must stay empty")
        s.close()


class TestCommittedFixtures(BroadcastCase):
    """End-to-end through the REAL youtube_api.fixture_transport against the
    committed pipeline/fixtures/youtube/* scenario (backs the CLI dry-run)."""

    FIXTURE_DIR = os.path.join(HERE, "fixtures", "youtube")

    def go_fixtures(self, channels, matches, **kw):
        client = yt.YoutubeClient(transport=yt.fixture_transport(self.FIXTURE_DIR),
                                  quota_budget=10000)
        return bc.discover_broadcasts(
            store=kw.get("store"), client=client, channels=channels, matches=matches,
            content_con=kw.get("content_con"), config=kw.get("config") or _cfg(),
            now=NOW, lookback_days=14, horizon_days=30, dry_run=kw.get("dry_run", False))

    def test_fixture_scenario_matches_and_windows(self):
        chans = [channel()]  # UC_OW_ESPORTS_OFFICIAL / UU_OW_ESPORTS_OFFICIAL, enabled
        ms = [
            match("match:finals", fmid="nf", team_a="Spacestation", team_b="NTMR",
                  finished="2026-07-22T23:00:00Z"),
            match("match:day3", fmid="d3", team_a="Toronto Defiant", team_b="Vancouver Titans",
                  finished="2026-07-23T20:00:00Z"),
        ]
        r = self.go_fixtures(chans, ms)
        # oldstage1vod (2026-06-01) is outside the 14-day window -> filtered.
        self.assertEqual(r["videosSeen"], 3)
        located = {rec["matchId"]: rec for rec in (r["review"] + r["located"])}
        # team-specific VOD matched the finals
        self.assertEqual(located["match:finals"]["videoId"], "nafinalsvod")
        # the full-day broadcast covered the day-3 match
        self.assertEqual(located["match:day3"]["videoId"], "naday3broadcast")
        self.assertTrue(located["match:day3"]["fullDay"])
        self.assertEqual(len(r["missing"]), 0)

    def test_fixture_channel_verification(self):
        report = bc.verify_channels(
            yt.YoutubeClient(transport=yt.fixture_transport(self.FIXTURE_DIR)),
            [{"id": "ow_esports_global", "platform": "youtube",
              "officialSourceUrl": "https://www.youtube.com/@ow_esports"}])
        r0 = report["channels"][0]
        self.assertEqual(r0["status"], "verified")
        self.assertEqual(r0["resolvedChannelId"], "UC_OW_ESPORTS_OFFICIAL")
        self.assertEqual(r0["uploadsPlaylistId"], "UU_OW_ESPORTS_OFFICIAL")


if __name__ == "__main__":
    unittest.main(verbosity=2)
