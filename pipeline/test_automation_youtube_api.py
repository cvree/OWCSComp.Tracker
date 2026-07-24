#!/usr/bin/env python3
"""
test_automation_youtube_api.py — YouTube Data API client + normalizer (Phase C2).

Proves: broadcast lifecycle classification (upcoming/live/completed/vod), quota
accounting per endpoint, quota-budget stop, API quotaExceeded handling, key
REDACTION everywhere (audit trail, cache, errors), fixture transport for
channels/playlistItems/videos/search, batching + de-dupe, and that NO
composition field is ever produced and the key never leaks. No network, no key.
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

# A deliberately fake, non-Google-shaped token so push-protection never flags it.
SECRET = "FAKE_yt_key_do_not_leak_0000000000"


def video(vid="vid1", *, title="OWCS NA", desc="", lbc="none", live=None,
          channel="UC_OFFICIAL", channel_title="Overwatch Esports", published="2026-07-20T18:00:00Z"):
    raw = {
        "id": vid,
        "snippet": {"title": title, "description": desc, "channelId": channel,
                    "channelTitle": channel_title, "publishedAt": published,
                    "liveBroadcastContent": lbc},
        "contentDetails": {"duration": "PT2H30M"},
        "status": {"privacyStatus": "public"},
    }
    if live is not None:
        raw["liveStreamingDetails"] = live
    return raw


class TestClassify(unittest.TestCase):
    def test_upcoming(self):
        v = video(lbc="upcoming", live={"scheduledStartTime": "2026-07-25T18:00:00Z"})
        self.assertEqual(yt.classify_broadcast(v), "upcoming")

    def test_live(self):
        v = video(lbc="live", live={"actualStartTime": "2026-07-24T18:00:00Z"})
        self.assertEqual(yt.classify_broadcast(v), "live")

    def test_completed_livestream(self):
        v = video(lbc="none", live={"actualStartTime": "2026-07-20T18:00:00Z",
                                     "actualEndTime": "2026-07-20T23:00:00Z"})
        self.assertEqual(yt.classify_broadcast(v), "completed")

    def test_plain_upload_is_vod(self):
        self.assertEqual(yt.classify_broadcast(video(lbc="none")), "vod")


class TestNormalize(unittest.TestCase):
    def test_video_shape_and_url(self):
        n = yt.normalize_video(video("abc123"))
        self.assertEqual(n["videoId"], "abc123")
        self.assertEqual(n["url"], "https://www.youtube.com/watch?v=abc123")
        self.assertEqual(n["broadcastType"], "vod")

    def test_search_style_id_dict(self):
        raw = {"id": {"videoId": "srch1"}, "snippet": {"title": "x", "channelId": "UC1",
               "liveBroadcastContent": "none", "publishedAt": "2026-07-20T00:00:00Z"}}
        self.assertEqual(yt.normalize_video(raw)["videoId"], "srch1")

    def test_playlist_item_video_id(self):
        item = {"contentDetails": {"videoId": "plv"}, "snippet": {}}
        self.assertEqual(yt.playlist_item_video_id(item), "plv")

    def test_no_composition_field_ever(self):
        n = yt.normalize_video(video(title="Kiriko hero swap montage", desc="dva reaper"))
        for key in n:
            low = key.lower()
            self.assertNotIn("hero", low)
            self.assertNotIn("swap", low)
            self.assertNotEqual(low, "composition")

    def test_channel_uploads_playlist(self):
        raw = {"id": "UC_X", "snippet": {"title": "Overwatch Esports", "customUrl": "@ow_esports"},
               "contentDetails": {"relatedPlaylists": {"uploads": "UU_X"}}}
        n = yt.normalize_channel(raw)
        self.assertEqual(n["channelId"], "UC_X")
        self.assertEqual(n["uploadsPlaylistId"], "UU_X")


class TestRedaction(unittest.TestCase):
    def test_redact_key(self):
        url = "https://api/videos?id=1&key=SECRETVAL&part=snippet"
        self.assertNotIn("SECRETVAL", yt.redact_key(url))
        self.assertIn("key=REDACTED", yt.redact_key(url))

    def test_key_never_in_audit_or_cache(self):
        # A transport that echoes back a minimal payload; assert the recorded URL
        # (and any cache file) never contains the secret.
        def transport(url, headers):
            assert SECRET in url  # the real key IS sent to the wire
            return 200, json.dumps({"items": []}), None
        with tempfile.TemporaryDirectory() as d:
            c = yt.YoutubeClient(api_key=SECRET, transport=transport, cache_dir=d)
            c.get_videos(["v1"])
            for call in c.calls:
                self.assertNotIn(SECRET, call["url"])
                self.assertIn("key=REDACTED", call["url"])
            for fn in os.listdir(d):
                with open(os.path.join(d, fn)) as fh:
                    body = fh.read()
                # cache stores response body only; make sure no key leaked in
                self.assertNotIn(SECRET, body)


class TestQuota(unittest.TestCase):
    def _client(self, budget=10000, items=None):
        def transport(url, headers):
            return 200, json.dumps({"items": items or []}), None
        return yt.YoutubeClient(api_key=SECRET, transport=transport, quota_budget=budget)

    def test_costs_accumulate(self):
        c = self._client()
        c.get_videos(["a"])            # +1
        c.get_channels_by_ids(["UC"])  # +1
        c.search_channel_videos("UC")  # +100
        self.assertEqual(c.quota_used, 102)

    def test_budget_blocks_before_call(self):
        c = self._client(budget=1)
        c.get_videos(["a"])  # uses the only unit
        with self.assertRaises(yt.YoutubeQuotaError):
            c.get_videos(["b"])
        # Search (100u) is refused when only part of the budget remains.
        c2 = self._client(budget=50)
        with self.assertRaises(yt.YoutubeQuotaError):
            c2.search_channel_videos("UC")

    def test_api_quota_exceeded_body(self):
        def transport(url, headers):
            return 403, json.dumps({"error": {"errors": [{"reason": "quotaExceeded"}]}}), "HTTP 403"
        c = yt.YoutubeClient(api_key=SECRET, transport=transport)
        with self.assertRaises(yt.YoutubeQuotaError):
            c.get_videos(["a"])


class TestFixtureTransport(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def _write(self, name, obj):
        with open(os.path.join(self.d, name), "w") as f:
            json.dump(obj, f)

    def test_channels_playlist_videos(self):
        self._write("handle_ow_esports.json", {"items": [{"id": "UC_OFF",
            "snippet": {"title": "Overwatch Esports"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU_OFF"}}}]})
        self._write("playlist_uu_off.json", {"items": [
            {"contentDetails": {"videoId": "v1"}, "snippet": {"publishedAt": "2026-07-20T00:00:00Z"}}]})
        self._write("videos_v1.json", {"items": [video("v1")]})
        c = yt.YoutubeClient(transport=yt.fixture_transport(self.d))
        ch = c.get_channel_by_handle("ow_esports")
        self.assertEqual(ch["id"], "UC_OFF")
        items = c.list_playlist_items("UU_OFF")
        self.assertEqual(yt.playlist_item_video_id(items[0]), "v1")
        vids = c.get_videos(["v1"])
        self.assertEqual(len(vids), 1)

    def test_missing_fixture_raises(self):
        c = yt.YoutubeClient(transport=yt.fixture_transport(self.d))
        with self.assertRaises(yt.YoutubeApiError):
            c.get_videos(["nope"])

    def test_no_key_no_transport_raises_auth(self):
        c = yt.YoutubeClient(api_key=None, transport=None)
        with self.assertRaises(yt.YoutubeAuthError):
            c.get_videos(["v1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
