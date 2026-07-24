#!/usr/bin/env python3
"""
test_automation_youtube_api.py — YouTube Data API v3 client (Phase C2).
Proves: pagination, fixture transport, quota accounting, error
classification (retryable/quota_exceeded/permanent), quota-exhaustion
detection, deterministic cache keys, and that the API key NEVER appears in
any call record, cache filename, or exception message. No network, no key.
Run: python3 pipeline/test_automation_youtube_api.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from automation import youtube_api as yt  # noqa: E402


def channel_item(cid="UC123", handle="@OW_Esports", title="Overwatch Esports",
                 uploads="UUuploads123"):
    return {
        "id": cid,
        "snippet": {"title": title, "customUrl": handle},
        "contentDetails": {"relatedPlaylists": {"uploads": uploads}},
    }


def video_item(vid, *, title="OWCS Match", live=None, duration="PT1H30M0S"):
    item = {
        "id": vid,
        "snippet": {"title": title, "description": "", "publishedAt": "2026-07-20T18:00:00Z",
                    "liveBroadcastContent": "none",
                    "thumbnails": {"high": {"url": f"https://img.example/{vid}.jpg"}}},
        "contentDetails": {"duration": duration},
        "status": {"privacyStatus": "public"},
    }
    if live:
        item["liveStreamingDetails"] = live
    return item


class TestErrorClassification(unittest.TestCase):
    def test_quota_exceeded(self):
        self.assertEqual(yt.classify_error(403, "quotaExceeded"), "quota_exceeded")
        self.assertEqual(yt.classify_error(403, "dailyLimitExceeded"), "quota_exceeded")

    def test_retryable(self):
        self.assertEqual(yt.classify_error(500, None), "retryable")
        self.assertEqual(yt.classify_error(503, None), "retryable")
        self.assertEqual(yt.classify_error(429, None), "retryable")
        self.assertEqual(yt.classify_error(None, "backendError"), "retryable")

    def test_permanent(self):
        self.assertEqual(yt.classify_error(404, "notFound"), "permanent")
        self.assertEqual(yt.classify_error(400, "invalidParameter"), "permanent")


class TestSanitizeUrl(unittest.TestCase):
    def test_key_stripped(self):
        url = "https://www.googleapis.com/youtube/v3/channels?id=UC123&key=SECRET123"
        clean = yt._sanitize_url(url)
        self.assertNotIn("SECRET123", clean)
        self.assertIn("id=UC123", clean)


class TestFixtureClient(unittest.TestCase):
    def test_channel_by_handle_and_uploads_playlist(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "channels_handle_ow_esports.json"), "w") as f:
                json.dump({"items": [channel_item()]}, f)
            client = yt.YouTubeClient(transport=yt.fixture_transport(d))
            item = client.get_channel_by_handle("OW_Esports")
            self.assertEqual(item["id"], "UC123")
            self.assertEqual(yt.uploads_playlist_id(item), "UUuploads123")
            self.assertEqual(client.quota_used, 1)
            self.assertEqual(client.quota_by_endpoint["channels.list"], 1)

    def test_channel_by_id_not_found_raises(self):
        with tempfile.TemporaryDirectory() as d:
            client = yt.YouTubeClient(transport=yt.fixture_transport(d))
            with self.assertRaises(yt.YouTubeApiError):
                client.get_channel_by_id("UCdoesnotexist")

    def test_playlist_items_pagination(self):
        with tempfile.TemporaryDirectory() as d:
            page1 = {"items": [{"contentDetails": {"videoId": "v1"}},
                               {"contentDetails": {"videoId": "v2"}}],
                     "nextPageToken": "PAGE2"}
            page2 = {"items": [{"contentDetails": {"videoId": "v3"}}]}
            with open(os.path.join(d, "playlistItems_uuuploads123_page1.json"), "w") as f:
                json.dump(page1, f)
            with open(os.path.join(d, "playlistItems_uuuploads123_page2.json"), "w") as f:
                json.dump(page2, f)
            client = yt.YouTubeClient(transport=yt.fixture_transport(d))
            items = client.list_playlist_items("UUuploads123")
            ids = [i["contentDetails"]["videoId"] for i in items]
            self.assertEqual(ids, ["v1", "v2", "v3"])
            self.assertEqual(client.quota_by_endpoint["playlistItems.list"], 2)

    def test_pagination_stops_at_lookback_boundary(self):
        # Page 1 is all recent (within the boundary); page 2's oldest item
        # predates it, so pagination must stop WITHOUT fetching page 3 at all
        # (a 1000-video channel should not be walked for a 14-day lookback).
        with tempfile.TemporaryDirectory() as d:
            page1 = {"items": [
                {"contentDetails": {"videoId": "new1", "videoPublishedAt": "2026-07-20T00:00:00Z"}},
                {"contentDetails": {"videoId": "new2", "videoPublishedAt": "2026-07-15T00:00:00Z"}},
            ], "nextPageToken": "PAGE2"}
            page2 = {"items": [
                {"contentDetails": {"videoId": "old1", "videoPublishedAt": "2026-01-01T00:00:00Z"}},
            ], "nextPageToken": "PAGE3"}
            page3 = {"items": [
                {"contentDetails": {"videoId": "ancient", "videoPublishedAt": "2020-01-01T00:00:00Z"}},
            ]}
            for name, payload in (("page1", page1), ("page2", page2), ("page3", page3)):
                with open(os.path.join(d, f"playlistItems_uuuploads123_{name}.json"), "w") as f:
                    json.dump(payload, f)
            client = yt.YouTubeClient(transport=yt.fixture_transport(d))
            stats: dict = {}
            items = client.list_playlist_items(
                "UUuploads123", stop_before="2026-07-01T00:00:00Z", stats=stats)
            ids = [i["contentDetails"]["videoId"] for i in items]
            # page2's oldest item (old1, 2026-01-01) is already before the
            # cutoff, so page2 is fetched (page1 didn't cross it yet) but
            # page3 (PAGE3 token) is never requested.
            self.assertIn("new1", ids)
            self.assertIn("old1", ids)
            self.assertNotIn("ancient", ids)
            self.assertEqual(stats["pages"], 2)
            self.assertEqual(client.quota_by_endpoint["playlistItems.list"], 2)

    def test_full_history_ignores_stop_before(self):
        with tempfile.TemporaryDirectory() as d:
            page1 = {"items": [
                {"contentDetails": {"videoId": "new1", "videoPublishedAt": "2026-07-20T00:00:00Z"}},
            ], "nextPageToken": "PAGE2"}
            page2 = {"items": [
                {"contentDetails": {"videoId": "ancient", "videoPublishedAt": "2020-01-01T00:00:00Z"}},
            ]}
            for name, payload in (("page1", page1), ("page2", page2)):
                with open(os.path.join(d, f"playlistItems_uuuploads123_{name}.json"), "w") as f:
                    json.dump(payload, f)
            client = yt.YouTubeClient(transport=yt.fixture_transport(d))
            # stop_before=None (the full_history contract) walks every page.
            items = client.list_playlist_items("UUuploads123", stop_before=None)
            ids = [i["contentDetails"]["videoId"] for i in items]
            self.assertIn("ancient", ids)

    def test_videos_list_batches_and_dedupes(self):
        with tempfile.TemporaryDirectory() as d:
            ids = [f"v{i}" for i in range(3)]
            with open(os.path.join(d, f"videos_{'_'.join(sorted(ids))}.json"), "w") as f:
                json.dump({"items": [video_item(v) for v in ids]}, f)
            client = yt.YouTubeClient(transport=yt.fixture_transport(d))
            # A duplicate id (e.g. a full-day broadcast appearing twice in an
            # uploads scan) must not be requested twice or returned twice.
            got = client.list_videos(["v0", "v1", "v2", "v0"])
            self.assertEqual(len(got), 3)
            self.assertEqual(client.quota_by_endpoint["videos.list"], 1)

    def test_search_fallback_costs_100_units(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "search_uc123_page1.json"), "w") as f:
                json.dump({"items": [{"id": {"videoId": "v9"}}]}, f)
            client = yt.YouTubeClient(transport=yt.fixture_transport(d))
            found = client.search_channel_videos("UC123")
            self.assertEqual(len(found), 1)
            self.assertEqual(client.quota_used, 100)

    def test_no_key_no_transport_raises_auth(self):
        client = yt.YouTubeClient(api_key=None, transport=None)
        with self.assertRaises(yt.YouTubeAuthError):
            client.get_channel_by_id("UC123")

    def test_missing_fixture_is_404_permanent(self):
        with tempfile.TemporaryDirectory() as d:
            client = yt.YouTubeClient(transport=yt.fixture_transport(d))
            try:
                client.get_channel_by_id("UCnope")
                self.fail("expected YouTubeApiError")
            except yt.YouTubeApiError as exc:
                self.assertEqual(yt.classify_error(exc.status, exc.reason), "permanent")


class TestQuotaExhaustion(unittest.TestCase):
    def test_quota_exceeded_raised_distinctly(self):
        def _t(url, headers):
            body = json.dumps({"error": {"code": 403, "message": "quota",
                                         "errors": [{"reason": "quotaExceeded"}]}})
            return 403, body, "HTTP 403"
        client = yt.YouTubeClient(transport=_t)
        with self.assertRaises(yt.YouTubeQuotaExceeded):
            client.get_channel_by_id("UC123")

    def test_generic_403_is_not_quota_exceeded(self):
        def _t(url, headers):
            body = json.dumps({"error": {"code": 403, "message": "forbidden",
                                         "errors": [{"reason": "forbidden"}]}})
            return 403, body, "HTTP 403"
        client = yt.YouTubeClient(transport=_t)
        with self.assertRaises(yt.YouTubeApiError) as ctx:
            client.get_channel_by_id("UC123")
        self.assertNotIsInstance(ctx.exception, yt.YouTubeQuotaExceeded)


class TestQuotaSink(unittest.TestCase):
    def test_sink_called_with_endpoint_and_units(self):
        seen = []
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "channels_id_uc123.json"), "w") as f:
                json.dump({"items": [channel_item(cid="UC123")]}, f)
            client = yt.YouTubeClient(transport=yt.fixture_transport(d),
                                      quota_sink=lambda ep, units: seen.append((ep, units)))
            client.get_channel_by_id("UC123")
        self.assertEqual(seen, [("channels.list", 1)])


class TestCaching(unittest.TestCase):
    def test_deterministic_cache_key_no_key_leakage(self):
        with tempfile.TemporaryDirectory() as fixdir, tempfile.TemporaryDirectory() as cachedir:
            with open(os.path.join(fixdir, "channels_id_uc123.json"), "w") as f:
                json.dump({"items": [channel_item(cid="UC123")]}, f)
            client = yt.YouTubeClient(api_key="TOP-SECRET-KEY",
                                      transport=yt.fixture_transport(fixdir), cache_dir=cachedir)
            client.get_channel_by_id("UC123")
            cached_files = os.listdir(cachedir)
            self.assertEqual(len(cached_files), 1)
            with open(os.path.join(cachedir, cached_files[0])) as f:
                content = f.read()
            self.assertNotIn("TOP-SECRET-KEY", content)
            # A second identical call reuses the same deterministic filename.
            client.get_channel_by_id("UC123")
            self.assertEqual(os.listdir(cachedir), cached_files)

    def test_cache_hit_skips_network_and_quota(self):
        with tempfile.TemporaryDirectory() as fixdir, tempfile.TemporaryDirectory() as cachedir:
            with open(os.path.join(fixdir, "channels_id_uc123.json"), "w") as f:
                json.dump({"items": [channel_item(cid="UC123")]}, f)
            calls_made = []
            real_transport = yt.fixture_transport(fixdir)

            def counting_transport(url, headers):
                calls_made.append(url)
                return real_transport(url, headers)

            client = yt.YouTubeClient(transport=counting_transport, cache_dir=cachedir,
                                      cache_ttl_seconds=3600)
            client.get_channel_by_id("UC123")
            self.assertEqual(len(calls_made), 1)
            self.assertEqual(client.quota_used, 1)
            self.assertEqual(client.cache_hits, 0)
            # Repeated dry-runs within the TTL must not hit the network again
            # or spend more quota — this is the "incremental/cache-aware"
            # efficiency requirement.
            client.get_channel_by_id("UC123")
            self.assertEqual(len(calls_made), 1, "second call should be served from cache")
            self.assertEqual(client.quota_used, 1, "cache hit must not spend quota")
            self.assertEqual(client.cache_hits, 1)

    def test_cache_ttl_zero_disables_caching(self):
        with tempfile.TemporaryDirectory() as fixdir, tempfile.TemporaryDirectory() as cachedir:
            with open(os.path.join(fixdir, "channels_id_uc123.json"), "w") as f:
                json.dump({"items": [channel_item(cid="UC123")]}, f)
            client = yt.YouTubeClient(transport=yt.fixture_transport(fixdir), cache_dir=cachedir,
                                      cache_ttl_seconds=0)
            client.get_channel_by_id("UC123")
            client.get_channel_by_id("UC123")
            self.assertEqual(client.quota_used, 2)
            self.assertEqual(client.cache_hits, 0)

    def test_no_key_in_call_audit_trail(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "channels_id_uc123.json"), "w") as f:
                json.dump({"items": [channel_item(cid="UC123")]}, f)
            client = yt.YouTubeClient(api_key="TOP-SECRET-KEY", transport=yt.fixture_transport(d))
            client.get_channel_by_id("UC123")
            for call in client.calls:
                self.assertNotIn("TOP-SECRET-KEY", call["url"])

    def test_error_message_never_contains_key(self):
        with tempfile.TemporaryDirectory() as d:
            client = yt.YouTubeClient(api_key="TOP-SECRET-KEY", transport=yt.fixture_transport(d))
            try:
                client.get_channel_by_id("UCnope")
            except yt.YouTubeApiError as exc:
                self.assertNotIn("TOP-SECRET-KEY", str(exc))


if __name__ == "__main__":
    unittest.main(verbosity=2)
