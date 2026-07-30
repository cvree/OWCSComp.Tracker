#!/usr/bin/env python3
"""
test_automation_keyless_metadata.py — public video metadata with NO API key.

The gap this closes: a machine without `YOUTUBE_API_KEY` used to stop at the
`approve-source --confirm` human gate on EVERY link, including links from the
verified official channel, because the only way to read the source's channel
id was the YouTube Data API. That was not a human decision; it was a missing
lookup. `keyless_metadata` performs that lookup on free, no-key sources
(yt-dlp's own metadata probe, then the public per-video Atom feed), and
`link_intake` uses it as a fallback — with the authorization rules unchanged.

Everything here is offline: a fake subprocess runner, a fake feed fetcher, a
temporary automation DB, an in-test channel registry. No network, no API key,
no yt-dlp binary, no cv2.

Covered behaviors (each one a required guarantee):
  * yt-dlp's JSON is normalized to the SAME shape the Data API path returns
  * the ladder falls through yt-dlp -> video feed, recording every attempt
  * a keyless-confirmed OFFICIAL channel auto-approves and is download-ready
  * a keyless-confirmed NON-registry channel still stops at the human gate
  * partial metadata (feed: no duration, no live status) cannot conclude a
    recent stream has ended — that stays a human decision
  * a live/upcoming broadcast is still refused, whichever provider said so
  * every keyless source failing keeps the API's own error as the headline
  * the fallback is never consulted when the API answered
  * intake never conjures a provider it was not given
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from automation import job_store as js  # noqa: E402
from automation import keyless_metadata as km  # noqa: E402
from automation import link_intake as li  # noqa: E402
from automation import state_machine as sm  # noqa: E402
from automation import youtube_api as yt  # noqa: E402

VIDEO_ID = "h3pgxhsUCt0"
OFFICIAL_CHANNEL = "UCiAInBL9kUzz1XRxk66v-gw"
OTHER_CHANNEL = "UCsomeRandomUploader00"

REGISTRY = [
    {"id": "ow_esports_global", "platform": "youtube",
     "channelId": OFFICIAL_CHANNEL, "region": "global", "language": "en",
     "official": True, "enabled": True, "verifiedStatus": "verified"},
]

BROADCAST_TITLE = "OWCS 2026 Stage 2 | Team Falcons vs Crazy Raccoon | Day 1"


def _iso(hours_ago: float) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)
            ).replace(microsecond=0).isoformat()


# ------------------------------------------------------------ fake transports
class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    """Stands in for `subprocess` — records every command, never executes."""

    def __init__(self, result=None, raises=None):
        self.result = result
        self.raises = raises
        self.commands: list[list[str]] = []

    def run(self, cmd, **kwargs):
        self.commands.append(list(cmd))
        if self.raises is not None:
            raise self.raises
        return self.result


def ytdlp_json(*, video_id=VIDEO_ID, channel_id=OFFICIAL_CHANNEL,
               title=BROADCAST_TITLE, duration=15130, live_status="was_live",
               availability="public", hours_ago=8.0) -> str:
    ts = int((dt.datetime.now(dt.timezone.utc)
              - dt.timedelta(hours=hours_ago)).timestamp())
    return json.dumps({
        "id": video_id,
        "title": title,
        "description": "Full broadcast VOD.",
        "channel_id": channel_id,
        "channel": "Overwatch Esports",
        "uploader": "Overwatch Esports",
        "duration": duration,
        "live_status": live_status,
        "availability": availability,
        "timestamp": ts,
        "upload_date": "20260720",
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
    })


def ytdlp_runner(**kw) -> FakeRunner:
    return FakeRunner(FakeCompleted(0, ytdlp_json(**kw), ""))


def feed_xml(*, video_id=VIDEO_ID, channel_id=OFFICIAL_CHANNEL,
             title=BROADCAST_TITLE, hours_ago=8.0) -> bytes:
    return (f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>yt:video:{video_id}</id>
    <yt:videoId>{video_id}</yt:videoId>
    <yt:channelId>{channel_id}</yt:channelId>
    <title>{title}</title>
    <author><name>Overwatch Esports</name></author>
    <published>{_iso(hours_ago)}</published>
    <media:group>
      <media:description>Full broadcast VOD.</media:description>
    </media:group>
  </entry>
</feed>""").encode("utf-8")


def feed_fetch(**kw):
    def _fetch(url, timeout=None):
        return feed_xml(**kw)
    return _fetch


def failing_fetch(exc):
    def _fetch(url, timeout=None):
        raise exc
    return _fetch


def no_yt_dlp() -> FakeRunner:
    return FakeRunner(raises=FileNotFoundError("yt-dlp"))


# ------------------------------------------------------------------- the ladder
class TestYtdlpRung(unittest.TestCase):
    def test_ytdlp_json_is_normalized_to_the_api_shape(self):
        meta = km.fetch_metadata(VIDEO_ID, runner=ytdlp_runner(),
                                 fetch=failing_fetch(AssertionError(
                                     "the feed must not be reached")))
        self.assertEqual(meta["status"], "ok")
        self.assertEqual(meta["source"], km.RUNG_YTDLP)
        self.assertEqual(meta["completeness"], "full")
        self.assertEqual(meta["videoId"], VIDEO_ID)
        self.assertEqual(meta["channelId"], OFFICIAL_CHANNEL)
        self.assertEqual(meta["channelTitle"], "Overwatch Esports")
        self.assertEqual(meta["title"], BROADCAST_TITLE)
        self.assertEqual(meta["durationSeconds"], 15130)
        self.assertEqual(meta["liveBroadcastStatus"], "completed")
        self.assertEqual(meta["privacyStatus"], "public")
        self.assertEqual([a["rung"] for a in meta["attempts"]], [km.RUNG_YTDLP])

    def test_probe_never_downloads_media(self):
        runner = ytdlp_runner()
        km.fetch_metadata(VIDEO_ID, runner=runner)
        cmd = runner.commands[0]
        self.assertIn("--skip-download", cmd)
        self.assertIn("--dump-single-json", cmd)
        self.assertIn("--no-playlist", cmd)
        # No credential is ever opted into by a metadata read.
        self.assertNotIn("--cookies-from-browser", cmd)

    def test_live_statuses_map_to_the_shared_vocabulary(self):
        for raw, expected in (("is_live", "live"), ("is_upcoming", "upcoming"),
                              ("was_live", "completed"),
                              ("post_live", "completed"),
                              ("not_live", None)):
            with self.subTest(raw=raw):
                meta = km.fetch_metadata(
                    VIDEO_ID, runner=ytdlp_runner(live_status=raw),
                    rungs=(km.RUNG_YTDLP,))
                self.assertEqual(meta["liveBroadcastStatus"], expected)
                self.assertEqual(meta["rawLiveStatus"], raw)

    def test_unavailable_video_is_not_found_not_a_probe_failure(self):
        runner = FakeRunner(FakeCompleted(
            1, "", "ERROR: [youtube] h3pgxhsUCt0: Video unavailable"))
        meta = km.fetch_metadata(VIDEO_ID, runner=runner,
                                 fetch=failing_fetch(AssertionError(
                                     "a deleted video ends the ladder")))
        self.assertEqual(meta["status"], "not_found")
        self.assertEqual(meta["errorCode"], "video_not_found")

    def test_broken_json_is_reported_not_raised(self):
        meta = km.fetch_metadata(
            VIDEO_ID, runner=FakeRunner(FakeCompleted(0, "not json", "")),
            rungs=(km.RUNG_YTDLP,))
        self.assertEqual(meta["status"], "unavailable")
        self.assertEqual(meta["attempts"][0]["errorCode"], "ytdlp_bad_json")


class TestFeedRung(unittest.TestCase):
    def test_feed_supplies_channel_evidence_but_is_partial(self):
        meta = km.fetch_metadata(VIDEO_ID, runner=no_yt_dlp(),
                                 fetch=feed_fetch())
        self.assertEqual(meta["status"], "ok")
        self.assertEqual(meta["source"], km.RUNG_FEED)
        self.assertEqual(meta["completeness"], "partial")
        self.assertEqual(meta["channelId"], OFFICIAL_CHANNEL)
        self.assertEqual(meta["title"], BROADCAST_TITLE)
        # Not knowing is recorded as None — never guessed as "completed".
        self.assertIsNone(meta["durationSeconds"])
        self.assertIsNone(meta["liveBroadcastStatus"])

    def test_ladder_records_every_attempt_in_order(self):
        meta = km.fetch_metadata(VIDEO_ID, runner=no_yt_dlp(),
                                 fetch=feed_fetch())
        self.assertEqual([a["rung"] for a in meta["attempts"]],
                         [km.RUNG_YTDLP, km.RUNG_FEED])
        self.assertFalse(meta["attempts"][0]["ok"])
        self.assertEqual(meta["attempts"][0]["errorCode"], "ytdlp_missing")
        self.assertTrue(meta["attempts"][1]["ok"])

    def test_feed_404_is_a_missing_video(self):
        exc = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        meta = km.fetch_metadata(VIDEO_ID, runner=no_yt_dlp(),
                                 fetch=failing_fetch(exc))
        self.assertEqual(meta["status"], "not_found")
        self.assertEqual(meta["errorCode"], "video_not_found")

    def test_every_source_failing_is_reported_not_raised(self):
        meta = km.fetch_metadata(
            VIDEO_ID, runner=no_yt_dlp(),
            fetch=failing_fetch(OSError("network is unreachable")))
        self.assertEqual(meta["status"], "unavailable")
        self.assertEqual(meta["errorCode"], "keyless_unavailable")
        self.assertIn("yt-dlp", meta["error"])
        self.assertEqual(len(meta["attempts"]), 2)


# ------------------------------------------------------------ intake wiring
class KeylessIntakeTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = js.JobStore(os.path.join(self._tmp.name,
                                              "automation.sqlite"))

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def keyless(self, *, runner=None, fetch=None):
        return km.resolver(runner=runner or ytdlp_runner(),
                           fetch=fetch or feed_fetch())

    def no_key_client(self) -> yt.YouTubeClient:
        """Exactly the operator's situation: no YOUTUBE_API_KEY, no
        transport — every API call raises YouTubeAuthError."""
        return yt.YouTubeClient(api_key=None, transport=None, cache_dir=None)

    def ingest(self, **kw):
        return li.ingest_link(
            self.store, f"https://www.youtube.com/watch?v={VIDEO_ID}",
            channels=REGISTRY, **kw)


class TestKeylessIntake(KeylessIntakeTestCase):
    def test_official_channel_auto_approves_without_an_api_key(self):
        """The headline behavior: no API key, and the pipeline still walks
        past the source gate for a verified official broadcast."""
        res = self.ingest(client=self.no_key_client(), keyless=self.keyless())
        self.assertEqual(res["metadata"]["status"], "ok")
        self.assertEqual(res["metadata"]["source"], km.RUNG_YTDLP)
        self.assertEqual(res["source"]["state"], li.SOURCE_APPROVED)
        self.assertTrue(res["source"]["autoApproved"])
        self.assertEqual(res["source"]["reasonCode"], "registry_channel")
        self.assertIn("without an API key", res["source"]["reason"])
        # Download-ready, and the next command is the worker — not a human.
        self.assertEqual(res["state"], sm.ARCHIVED)
        self.assertIn("worker-run", res["nextCommand"])
        job = self.store.get(res["jobKey"])
        self.assertEqual(job.payload["channelId"], OFFICIAL_CHANNEL)
        self.assertEqual(li.blocking_reasons(job), [])

    def test_the_api_failure_is_still_recorded_as_evidence(self):
        res = self.ingest(client=self.no_key_client(), keyless=self.keyless())
        self.assertEqual(res["metadata"]["primaryError"]["errorCode"],
                         "no_api_key")
        self.assertTrue(any("keyless source" in w for w in res["warnings"]))

    def test_non_registry_channel_still_stops_for_a_human(self):
        """Keyless evidence widens WHERE the channel id comes from, never
        WHICH channels may authorize a download."""
        res = self.ingest(
            client=self.no_key_client(),
            keyless=self.keyless(runner=ytdlp_runner(channel_id=OTHER_CHANNEL)))
        self.assertEqual(res["source"]["state"], li.SOURCE_PENDING)
        self.assertEqual(res["source"]["reasonCode"], "channel_not_in_registry")
        self.assertIn("approve-source", res["nextCommand"])

    def test_live_broadcast_is_refused_whichever_provider_said_so(self):
        res = self.ingest(
            client=self.no_key_client(),
            keyless=self.keyless(runner=ytdlp_runner(live_status="is_live",
                                                     duration=0)))
        self.assertTrue(any("is live" in w for w in res["warnings"]))
        job = self.store.get(res["jobKey"])
        self.assertTrue(any("only completed VODs" in b
                            for b in li.blocking_reasons(job)))

    def test_promo_upload_on_the_official_channel_still_fails_likeness(self):
        res = self.ingest(
            client=self.no_key_client(),
            keyless=self.keyless(runner=ytdlp_runner(
                title="Overwatch 2 lootbox giveaway! #shorts",
                duration=28, live_status="not_live")))
        self.assertEqual(res["likeness"]["confidence"], "unlikely")
        self.assertEqual(res["source"]["reasonCode"],
                         "broadcast_likeness_failed")
        self.assertEqual(res["source"]["state"], li.SOURCE_PENDING)


class TestPartialMetadataStaysHuman(KeylessIntakeTestCase):
    def test_recent_video_with_no_live_status_is_not_auto_approved(self):
        """The feed cannot prove a stream has ended. Auto-approving a
        possibly-live broadcast would send the worker at an in-progress
        stream, so this one IS a human decision."""
        res = self.ingest(
            client=self.no_key_client(),
            keyless=self.keyless(runner=no_yt_dlp(),
                                 fetch=feed_fetch(hours_ago=0.5)))
        self.assertEqual(res["metadata"]["source"], km.RUNG_FEED)
        self.assertEqual(res["source"]["state"], li.SOURCE_PENDING)
        self.assertEqual(res["source"]["reasonCode"], "live_status_unknown")
        self.assertIn("approve-source", res["nextCommand"])

    def test_older_video_with_partial_metadata_is_approved_with_a_warning(self):
        res = self.ingest(
            client=self.no_key_client(),
            keyless=self.keyless(runner=no_yt_dlp(),
                                 fetch=feed_fetch(hours_ago=48)))
        self.assertEqual(res["source"]["state"], li.SOURCE_APPROVED)
        self.assertEqual(res["state"], sm.ARCHIVED)
        self.assertTrue(any("no duration and no live status" in w
                            for w in res["warnings"]))

    def test_unknown_publish_time_is_never_permission(self):
        meta = {"status": "ok", "source": km.RUNG_FEED,
                "completeness": "partial", "channelId": OFFICIAL_CHANNEL,
                "publishedAt": None}
        decision = li.authorize_source(
            meta, registry=li.registry_channel_index(REGISTRY))
        self.assertEqual(decision["state"], li.SOURCE_PENDING)
        self.assertEqual(decision["reasonCode"], "live_status_unknown")


class TestFallbackBoundaries(KeylessIntakeTestCase):
    def test_a_working_api_is_never_second_guessed(self):
        def transport(url, headers):
            return 200, json.dumps({"items": [{
                "id": VIDEO_ID,
                "snippet": {"channelId": OFFICIAL_CHANNEL,
                            "channelTitle": "Overwatch Esports",
                            "title": BROADCAST_TITLE,
                            "description": "Full broadcast VOD.",
                            "publishedAt": "2026-07-20T10:00:00Z",
                            "liveBroadcastContent": "none"},
                "status": {"privacyStatus": "public"},
                "contentDetails": {"duration": "PT4H12M30S"},
                "liveStreamingDetails": {
                    "actualStartTime": "2026-07-20T10:05:00Z",
                    "actualEndTime": "2026-07-20T14:17:30Z"},
            }]}), None
        runner = FakeRunner(raises=AssertionError(
            "the keyless ladder must not run when the API answered"))
        res = self.ingest(
            client=yt.YouTubeClient(transport=transport, cache_dir=None),
            keyless=km.resolver(runner=runner, fetch=failing_fetch(
                AssertionError("nor the feed"))))
        self.assertEqual(res["metadata"]["source"], km.SOURCE_DATA_API)
        self.assertEqual(res["metadata"]["completeness"], "full")
        self.assertEqual(res["source"]["state"], li.SOURCE_APPROVED)
        self.assertEqual(runner.commands, [])

    def test_every_provider_failing_keeps_the_api_error_as_the_headline(self):
        res = self.ingest(
            client=self.no_key_client(),
            keyless=self.keyless(runner=no_yt_dlp(),
                                 fetch=failing_fetch(OSError("no network"))))
        self.assertEqual(res["metadata"]["status"], "unavailable")
        self.assertEqual(res["metadata"]["errorCode"], "no_api_key")
        self.assertEqual(res["source"]["state"], li.SOURCE_PENDING)
        # ...but WHY the free path also failed is on the record, with the fix.
        job = self.store.get(res["jobKey"])
        blocking = " ".join(li.blocking_reasons(job))
        self.assertIn("keyless fallback", blocking)
        self.assertIn("install yt-dlp", blocking)

    def test_a_crashing_resolver_never_breaks_intake(self):
        def boom(video_id):
            raise RuntimeError("resolver exploded")
        res = self.ingest(client=self.no_key_client(), keyless=boom)
        self.assertTrue(res["created"])
        self.assertEqual(res["source"]["state"], li.SOURCE_PENDING)
        self.assertEqual(res["metadata"]["keylessAttempts"][0]["errorCode"],
                         "keyless_crashed")

    def test_intake_conjures_no_provider_it_was_not_given(self):
        """Default construction stays inert: no client, no keyless, no
        network — the caller decides what an intake may touch."""
        res = self.ingest(client=None)
        self.assertEqual(res["metadata"]["errorCode"], "no_client")
        self.assertEqual(res["source"]["state"], li.SOURCE_PENDING)
        self.assertTrue(any("no keyless fallback was offered" in w
                            for w in res["warnings"]))

    def test_deleted_video_verdict_beats_the_api_could_not_ask_verdict(self):
        exc = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        res = self.ingest(
            client=self.no_key_client(),
            keyless=self.keyless(
                runner=FakeRunner(FakeCompleted(1, "", "ERROR: Private video")),
                fetch=failing_fetch(exc)))
        self.assertEqual(res["metadata"]["status"], "not_found")
        self.assertEqual(res["metadata"]["errorCode"], "video_not_found")
        self.assertEqual(res["source"]["state"], li.SOURCE_PENDING)


class TestLinkStatusProvenance(KeylessIntakeTestCase):
    def test_status_shows_which_provider_answered(self):
        self.ingest(client=self.no_key_client(), keyless=self.keyless())
        row = li.link_status(self.store, video_id=VIDEO_ID)[0]
        self.assertEqual(row["metadataSource"], km.RUNG_YTDLP)
        self.assertEqual(row["metadataCompleteness"], "full")
        self.assertIn(f"[via {km.RUNG_YTDLP}]", li.format_status([row]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
