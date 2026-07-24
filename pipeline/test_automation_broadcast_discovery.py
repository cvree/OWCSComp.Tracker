#!/usr/bin/env python3
"""
test_automation_broadcast_discovery.py — YouTube channel verification (C1)
+ broadcast discovery/normalization (C3). Covers: handle resolution + skip/
not-found/quota-exceeded, live-status normalization (upcoming/live/completed/
ordinary VOD), duration parsing, the rolling 14-day + horizon window
(including boundaries and "unknown timing kept"), channel-with-no-channelId
skip, channel-not-found, playlist pagination, opt-in search fallback,
duplicate video ids deduped, idempotent reruns (no duplicate video rows or
jobs), renamed/delayed broadcasts updating in place, multiple official
language feeds, and API-failure -> retry job. No network, no API key.
Run: python3 pipeline/test_automation_broadcast_discovery.py
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
from automation import broadcast_discovery as bd  # noqa: E402
from automation import models  # noqa: E402
from automation import state_machine as sm  # noqa: E402
from automation import youtube_api as yt  # noqa: E402
from automation.config import AutomationConfig, DEFAULTS  # noqa: E402
from automation.job_store import JobStore  # noqa: E402

NOW = dt.datetime(2026, 7, 24, 12, 0, 0, tzinfo=dt.timezone.utc)


def epoch_iso(days_from_now: float) -> str:
    return (NOW + dt.timedelta(days=days_from_now)).replace(microsecond=0).isoformat()


def channel_row(**kw) -> dict:
    return {
        "id": kw.get("id", "ow_esports_global"), "name": kw.get("name", "Overwatch Esports"),
        "platform": "youtube", "channelId": kw.get("channelId", "UC123"),
        "region": kw.get("region", "global"), "language": kw.get("language", "en"),
        "official": kw.get("official", True), "priority": 100,
        "sourceUrl": kw.get("sourceUrl", "https://www.youtube.com/@OW_Esports"),
        "disabledReason": kw.get("disabledReason"),
        "enabled": kw.get("enabled", True),
    }


def raw_channel(cid="UC123", uploads="UUuploads"):
    return {"id": cid, "snippet": {"title": "Overwatch Esports", "customUrl": "@OW_Esports"},
            "contentDetails": {"relatedPlaylists": {"uploads": uploads}}}


def raw_video(vid, *, title="OWCS 2026 NA Grand Final", live=None, duration="PT2H0M0S",
             published="2026-07-20T18:00:00Z"):
    item = {"id": vid,
            "snippet": {"title": title, "description": "", "publishedAt": published,
                       "liveBroadcastContent": "none", "thumbnails": {}},
            "contentDetails": {"duration": duration}}
    if live:
        item["liveStreamingDetails"] = live
    return item


def _cfg(**over):
    v = dict(DEFAULTS)
    v.update(over)
    return AutomationConfig(values=v)


class TestVerifyChannels(unittest.TestCase):
    def test_resolves_by_handle_when_no_channel_id(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "channels_handle_ow_esports.json"), "w") as f:
                json.dump({"items": [raw_channel()]}, f)
            client = yt.YouTubeClient(transport=yt.fixture_transport(d))
            ch = channel_row(channelId=None, sourceUrl="https://www.youtube.com/OW_Esports")
            report = bd.verify_channels(client, [ch], now=NOW)
            self.assertEqual(report["channels"][0]["status"], "verified")
            self.assertEqual(report["channels"][0]["channelId"], "UC123")
            self.assertEqual(report["channels"][0]["uploadsPlaylistId"], "UUuploads")
            self.assertEqual(report["verifiedCount"], 1)

    def test_skipped_when_no_id_and_no_source_url(self):
        client = yt.YouTubeClient(transport=lambda url, h: (404, None, "n/a"))
        ch = channel_row(channelId=None, sourceUrl=None, disabledReason="no evidence")
        report = bd.verify_channels(client, [ch])
        self.assertEqual(report["channels"][0]["status"], "skipped")
        self.assertEqual(report["channels"][0]["reason"], "no evidence")
        self.assertEqual(report["skippedCount"], 1)

    def test_not_found(self):
        # The real API answers an unknown id with HTTP 200 + empty items,
        # never a 404 — simulate that exactly (a fixture-lookup 404 would
        # instead be an API-error test, not a not-found test).
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "channels_id_ucnope.json"), "w") as f:
                json.dump({"items": []}, f)
            client = yt.YouTubeClient(transport=yt.fixture_transport(d))
            report = bd.verify_channels(client, [channel_row(channelId="UCnope", sourceUrl=None)])
            self.assertEqual(report["channels"][0]["status"], "not_found")

    def test_quota_exceeded_surfaced_distinctly(self):
        def _t(url, headers):
            return 403, json.dumps({"error": {"errors": [{"reason": "quotaExceeded"}]}}), "HTTP 403"
        client = yt.YouTubeClient(transport=_t)
        report = bd.verify_channels(client, [channel_row()])
        self.assertEqual(report["channels"][0]["status"], "quota_exceeded")


class TestNormalizeVideo(unittest.TestCase):
    def setUp(self):
        self.channel = channel_row()

    def test_upcoming(self):
        v = bd.normalize_video(raw_video("v1", live={"scheduledStartTime": epoch_iso(1)}),
                               channel=self.channel, discovered_at=NOW)
        self.assertEqual(v["liveBroadcastStatus"], "upcoming")

    def test_live(self):
        v = bd.normalize_video(
            raw_video("v1", live={"scheduledStartTime": epoch_iso(-0.1), "actualStartTime": epoch_iso(-0.05)}),
            channel=self.channel, discovered_at=NOW)
        self.assertEqual(v["liveBroadcastStatus"], "live")

    def test_completed_archive(self):
        v = bd.normalize_video(
            raw_video("v1", live={"scheduledStartTime": epoch_iso(-1), "actualStartTime": epoch_iso(-1),
                                  "actualEndTime": epoch_iso(-0.9)}),
            channel=self.channel, discovered_at=NOW)
        self.assertEqual(v["liveBroadcastStatus"], "completed")

    def test_ordinary_vod_never_a_livestream(self):
        v = bd.normalize_video(raw_video("v1"), channel=self.channel, discovered_at=NOW)
        self.assertEqual(v["liveBroadcastStatus"], "none")

    def test_duration_parsed(self):
        v = bd.normalize_video(raw_video("v1", duration="PT1H42M30S"),
                               channel=self.channel, discovered_at=NOW)
        self.assertEqual(v["durationSeconds"], 1 * 3600 + 42 * 60 + 30)

    def test_region_language_official_from_channel(self):
        v = bd.normalize_video(raw_video("v1"), channel=self.channel, discovered_at=NOW)
        self.assertEqual(v["region"], "global")
        self.assertEqual(v["language"], "en")
        self.assertTrue(v["officialChannel"])

    def test_source_url(self):
        v = bd.normalize_video(raw_video("v1"), channel=self.channel, discovered_at=NOW)
        self.assertEqual(v["sourceUrl"], "https://www.youtube.com/watch?v=v1")


class TestWindow(unittest.TestCase):
    def _v(self, **over):
        base = {"liveBroadcastStatus": "none", "actualEndAt": None,
                "publishedAt": None, "scheduledStartAt": None}
        base.update(over)
        return base

    def test_live_always_kept(self):
        self.assertTrue(bd.in_window(self._v(liveBroadcastStatus="live"), NOW, 14, 30))

    def test_within_lookback(self):
        self.assertTrue(bd.in_window(self._v(actualEndAt=epoch_iso(-13.5)), NOW, 14, 30))

    def test_outside_lookback(self):
        self.assertFalse(bd.in_window(self._v(actualEndAt=epoch_iso(-15)), NOW, 14, 30))

    def test_within_horizon(self):
        self.assertTrue(bd.in_window(self._v(scheduledStartAt=epoch_iso(20)), NOW, 14, 30))

    def test_outside_horizon(self):
        self.assertFalse(bd.in_window(self._v(scheduledStartAt=epoch_iso(40)), NOW, 14, 30))

    def test_unknown_timing_kept(self):
        self.assertTrue(bd.in_window(self._v(), NOW, 14, 30))


class DiscoveryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "automation.sqlite")

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except PermissionError:
            pass  # Windows: a not-yet-closed sqlite handle can briefly hold the file open

    def store(self) -> JobStore:
        return JobStore(self.db, config=_cfg())


class TestDiscoverChannelVideos(DiscoveryCase):
    def test_no_channel_id_skips(self):
        client = yt.YouTubeClient(transport=lambda u, h: (404, None, "n/a"))
        result = bd.discover_channel_videos(client, channel_row(channelId=None),
                                            lookback_days=14, horizon_days=30, now=NOW)
        self.assertIn("no confirmed channelId", result["error"])

    def test_channel_not_found(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "channels_id_ucnope.json"), "w") as f:
                json.dump({"items": []}, f)
            client = yt.YouTubeClient(transport=yt.fixture_transport(d))
            result = bd.discover_channel_videos(client, channel_row(channelId="UCnope"),
                                                lookback_days=14, horizon_days=30, now=NOW)
            self.assertIn("not found", result["error"])

    def test_playlist_pagination_and_hydration(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "channels_id_uc123.json"), "w") as f:
                json.dump({"items": [raw_channel()]}, f)
            with open(os.path.join(d, "playlistItems_uuuploads_page1.json"), "w") as f:
                json.dump({"items": [{"contentDetails": {"videoId": "v1"}}],
                          "nextPageToken": "PAGE2"}, f)
            with open(os.path.join(d, "playlistItems_uuuploads_page2.json"), "w") as f:
                json.dump({"items": [{"contentDetails": {"videoId": "v2"}}]}, f)
            with open(os.path.join(d, "videos_v1_v2.json"), "w") as f:
                json.dump({"items": [raw_video("v1", published=epoch_iso(-1)),
                                     raw_video("v2", published=epoch_iso(-2))]}, f)
            client = yt.YouTubeClient(transport=yt.fixture_transport(d))
            result = bd.discover_channel_videos(client, channel_row(),
                                                lookback_days=14, horizon_days=30, now=NOW)
            self.assertIsNone(result["error"])
            self.assertEqual(result["videosSeen"], 2)
            self.assertEqual(result["inWindow"], 2)
            self.assertFalse(result["usedSearchFallback"])

    def test_search_fallback_only_when_no_uploads(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "channels_id_uc123.json"), "w") as f:
                json.dump({"items": [raw_channel(uploads="")]}, f)
            with open(os.path.join(d, "search_uc123_page1.json"), "w") as f:
                json.dump({"items": [{"id": {"videoId": "v9"}}]}, f)
            with open(os.path.join(d, "videos_v9.json"), "w") as f:
                json.dump({"items": [raw_video("v9", published=epoch_iso(-1))]}, f)
            client = yt.YouTubeClient(transport=yt.fixture_transport(d))
            result = bd.discover_channel_videos(client, channel_row(),
                                                lookback_days=14, horizon_days=30, now=NOW,
                                                allow_search_fallback=True)
            self.assertTrue(result["usedSearchFallback"])
            self.assertEqual(result["videosSeen"], 1)

    def test_search_fallback_not_used_unless_opted_in(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "channels_id_uc123.json"), "w") as f:
                json.dump({"items": [raw_channel(uploads="")]}, f)
            client = yt.YouTubeClient(transport=yt.fixture_transport(d))
            result = bd.discover_channel_videos(client, channel_row(),
                                                lookback_days=14, horizon_days=30, now=NOW,
                                                allow_search_fallback=False)
            self.assertFalse(result["usedSearchFallback"])
            self.assertEqual(result["videosSeen"], 0)


class TestUpsertIdempotency(DiscoveryCase):
    def test_insert_then_update_in_place(self):
        s = self.store()
        v = bd.normalize_video(raw_video("v1", title="Old Title", published=epoch_iso(-1)),
                               channel=channel_row(), discovered_at=NOW)
        self.assertEqual(bd.upsert_broadcast_video(s, v), "inserted")
        n1 = s.con.execute("SELECT COUNT(*) FROM broadcast_videos").fetchone()[0]
        self.assertEqual(n1, 1)

        # Renamed broadcast (title changed on rerun) updates in place, no dup row.
        v2 = bd.normalize_video(raw_video("v1", title="New Title (VOD)", published=epoch_iso(-1)),
                                channel=channel_row(), discovered_at=NOW)
        self.assertEqual(bd.upsert_broadcast_video(s, v2), "updated")
        n2 = s.con.execute("SELECT COUNT(*) FROM broadcast_videos").fetchone()[0]
        self.assertEqual(n2, 1)
        row = s.con.execute("SELECT title FROM broadcast_videos WHERE video_id='v1'").fetchone()
        self.assertEqual(row["title"], "New Title (VOD)")
        s.close()

    def test_delayed_broadcast_scheduled_time_updates(self):
        s = self.store()
        v1 = bd.normalize_video(raw_video("v1", live={"scheduledStartTime": epoch_iso(1)}),
                                channel=channel_row(), discovered_at=NOW)
        bd.upsert_broadcast_video(s, v1)
        v2 = bd.normalize_video(raw_video("v1", live={"scheduledStartTime": epoch_iso(2)}),
                                channel=channel_row(), discovered_at=NOW)
        bd.upsert_broadcast_video(s, v2)
        row = s.con.execute("SELECT scheduled_start_at FROM broadcast_videos WHERE video_id='v1'").fetchone()
        self.assertEqual(row["scheduled_start_at"], epoch_iso(2))
        s.close()

    def test_state_never_regresses(self):
        s = self.store()
        # First seen as a completed archive.
        v1 = bd.normalize_video(
            raw_video("v1", live={"scheduledStartTime": epoch_iso(-1), "actualStartTime": epoch_iso(-1),
                                  "actualEndTime": epoch_iso(-0.9)}),
            channel=channel_row(), discovered_at=NOW)
        bd.upsert_broadcast_video(s, v1)
        row = s.con.execute("SELECT coverage_state FROM broadcast_videos WHERE video_id='v1'").fetchone()
        self.assertEqual(row["coverage_state"], sm.ARCHIVED)
        # A human/reviewer manually advances it (simulating a later phase).
        s.con.execute("UPDATE broadcast_videos SET coverage_state=? WHERE video_id='v1'",
                      (sm.NEEDS_REVIEW,))
        s.con.commit()
        # Rerunning discovery on the same (now-archived) video must not
        # silently regress the reviewer's advanced state back to ARCHIVED.
        bd.upsert_broadcast_video(s, v1)
        row2 = s.con.execute("SELECT coverage_state FROM broadcast_videos WHERE video_id='v1'").fetchone()
        self.assertEqual(row2["coverage_state"], sm.NEEDS_REVIEW)
        s.close()

    def test_upsert_enqueues_broadcast_job_idempotently(self):
        s = self.store()
        v = bd.normalize_video(raw_video("v1"), channel=channel_row(), discovered_at=NOW)
        bd.upsert_broadcast_video(s, v)
        bd.upsert_broadcast_video(s, v)
        jobs = s.list_jobs(kind=models.KIND_BROADCAST)
        keys = [j.job_key for j in jobs]
        self.assertEqual(keys.count(models.broadcast_key("v1")), 1)
        s.close()


class TestSyncBroadcasts(DiscoveryCase):
    def _client_with_video(self, video_id="v1", **video_kw):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "channels_id_uc123.json"), "w") as f:
            json.dump({"items": [raw_channel()]}, f)
        with open(os.path.join(d, "playlistItems_uuuploads_page1.json"), "w") as f:
            json.dump({"items": [{"contentDetails": {"videoId": video_id}}]}, f)
        with open(os.path.join(d, f"videos_{video_id}.json"), "w") as f:
            json.dump({"items": [raw_video(video_id, published=epoch_iso(-1), **video_kw)]}, f)
        return yt.YouTubeClient(transport=yt.fixture_transport(d))

    def test_dry_run_writes_nothing(self):
        s = self.store()
        client = self._client_with_video()
        summary = bd.sync_broadcasts(client=client, store=s, channels=[channel_row()],
                                     lookback_days=14, horizon_days=30, dry_run=True, now=NOW)
        self.assertEqual(summary["upserted"], 0)
        self.assertEqual(s.con.execute("SELECT COUNT(*) FROM broadcast_videos").fetchone()[0], 0)
        self.assertEqual(len(s.list_jobs()), 0)
        s.close()

    def test_live_sync_upserts_and_dedupes_on_rerun(self):
        s = self.store()
        summary1 = bd.sync_broadcasts(client=self._client_with_video(), store=s,
                                      channels=[channel_row()], lookback_days=14,
                                      horizon_days=30, dry_run=False, now=NOW)
        self.assertEqual(summary1["upserted"], 1)
        n1 = s.con.execute("SELECT COUNT(*) FROM broadcast_videos").fetchone()[0]
        # Rerun with the same channel/window: no duplicate video row, no
        # duplicate broadcast-discovery scan job.
        summary2 = bd.sync_broadcasts(client=self._client_with_video(), store=s,
                                      channels=[channel_row()], lookback_days=14,
                                      horizon_days=30, dry_run=False, now=NOW)
        self.assertEqual(summary2["upserted"], 0)
        self.assertEqual(summary2["scanJobsCreated"], 0)
        n2 = s.con.execute("SELECT COUNT(*) FROM broadcast_videos").fetchone()[0]
        self.assertEqual(n1, n2)
        s.close()

    def test_no_enabled_channels_notes_and_skips(self):
        s = self.store()
        summary = bd.sync_broadcasts(client=self._client_with_video(), store=s, channels=[],
                                     lookback_days=14, horizon_days=30, now=NOW)
        self.assertIn("note", summary)
        s.close()

    def test_api_failure_creates_retry_job(self):
        s = self.store()
        client = yt.YouTubeClient(transport=lambda u, h: (500, None, "boom"))
        summary = bd.sync_broadcasts(client=client, store=s, channels=[channel_row()],
                                     lookback_days=14, horizon_days=30, dry_run=False, now=NOW)
        self.assertEqual(len(summary["errors"]), 1)
        key = models.broadcast_discovery_key(
            "ow_esports_global", (NOW - dt.timedelta(days=14)).date().isoformat(),
            (NOW + dt.timedelta(days=30)).date().isoformat())
        job = s.get(key)
        self.assertIsNotNone(job)
        self.assertEqual(job.state, sm.RETRY_SCHEDULED)
        s.close()

    def test_multiple_language_feeds_produce_separate_video_rows(self):
        s = self.store()
        en_channel = channel_row(id="ow_esports_global", language="en")
        kr_channel = channel_row(id="ow_esports_korea", language="ko", channelId="UC456",
                                 sourceUrl=None)
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "channels_id_uc123.json"), "w") as f:
            json.dump({"items": [raw_channel(cid="UC123")]}, f)
        with open(os.path.join(d, "channels_id_uc456.json"), "w") as f:
            json.dump({"items": [raw_channel(cid="UC456", uploads="UUuploadsKR")]}, f)
        with open(os.path.join(d, "playlistItems_uuuploads_page1.json"), "w") as f:
            json.dump({"items": [{"contentDetails": {"videoId": "ven"}}]}, f)
        with open(os.path.join(d, "playlistItems_uuuploadskr_page1.json"), "w") as f:
            json.dump({"items": [{"contentDetails": {"videoId": "vkr"}}]}, f)
        with open(os.path.join(d, "videos_ven.json"), "w") as f:
            json.dump({"items": [raw_video("ven", published=epoch_iso(-1))]}, f)
        with open(os.path.join(d, "videos_vkr.json"), "w") as f:
            json.dump({"items": [raw_video("vkr", published=epoch_iso(-1))]}, f)
        client = yt.YouTubeClient(transport=yt.fixture_transport(d))
        summary = bd.sync_broadcasts(client=client, store=s, channels=[en_channel, kr_channel],
                                     lookback_days=14, horizon_days=30, dry_run=False, now=NOW)
        self.assertEqual(summary["upserted"], 2)
        langs = {r["language"] for r in s.con.execute("SELECT language FROM broadcast_videos")}
        self.assertEqual(langs, {"en", "ko"})
        s.close()

    def test_no_composition_or_content_db_writes(self):
        # Broadcast discovery only ever touches the AUTOMATION db, which has
        # no hero-composition tables at all (content db's comp_snapshots/
        # snapshot_heroes/hero_stints/hero_swaps never exist here).
        s = self.store()
        bd.sync_broadcasts(client=self._client_with_video(), store=s, channels=[channel_row()],
                           lookback_days=14, horizon_days=30, dry_run=False, now=NOW)
        tables = {r[0] for r in s.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        forbidden = {"comp_snapshots", "snapshot_heroes", "hero_stints", "hero_swaps"}
        self.assertEqual(tables & forbidden, set())
        s.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
