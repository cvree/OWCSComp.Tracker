#!/usr/bin/env python3
"""
test_automation_link_intake.py — Phase 1 URL-only intake (link_intake.py).

Everything here is offline: a fake YouTube transport, a temporary automation
DB, and an in-test channel registry. No network, no API key, no cv2.

Covered behaviors (each one a required guarantee):
  * URL normalization across every accepted spelling
  * refusal codes for every rejected URL shape
  * one deterministic job per video id (duplicate intake attaches)
  * automatic authorization for a verified registry channel
  * manual authorization (audited) for anything else, and rejection
  * metadata failure never auto-approves and never drops the link
  * broadcast-likeness warnings for promos/guides/shorts
  * dry runs write nothing
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from automation import job_store as js  # noqa: E402
from automation import link_intake as li  # noqa: E402
from automation import models  # noqa: E402
from automation import state_machine as sm  # noqa: E402
from automation import youtube_api as yt  # noqa: E402

OFFICIAL_CHANNEL = "UCiAInBL9kUzz1XRxk66v-gw"
OTHER_CHANNEL = "UCsomeRandomUploader00"

REGISTRY = [
    {"id": "ow_esports_global", "platform": "youtube",
     "channelId": OFFICIAL_CHANNEL, "region": "global", "language": "en",
     "official": True, "enabled": True, "verifiedStatus": "verified"},
    # An enabled row with NO confirmed channel id can never authorize.
    {"id": "ow_esports_korea", "platform": "youtube", "channelId": None,
     "region": "korea", "official": True, "enabled": True,
     "verifiedStatus": "unverified"},
    # A disabled row is not authority either, even with a real id.
    {"id": "disabled_official", "platform": "youtube",
     "channelId": "UCdisabledButReal0", "region": "na", "official": True,
     "enabled": False, "verifiedStatus": "verified"},
    # The official Twitch destination. Keyed by the channel LOGIN, which is
    # what a Twitch id namespace looks like.
    {"id": "ow_esports_twitch", "platform": "twitch",
     "channelId": "ow_esports", "region": "global", "language": "en",
     "official": True, "enabled": True, "verifiedStatus": "verified"},
]

TWITCH_VOD = "2854348714"


def fake_twitch_runner(*, video_id=TWITCH_VOD, login="OW_Esports",
                       title="OWCS 2026 | Stage 2 Playoffs | Day 2",
                       duration=21600, is_live=False,
                       returncode=0, stdout=None, stderr="",
                       raise_exc=None):
    """A stand-in for the one `yt-dlp -J` call the Twitch metadata path
    makes. No network, no yt-dlp binary, no key — which is the point."""
    def run(cmd, timeout):
        if raise_exc is not None:
            raise raise_exc
        payload = {"id": video_id, "title": title, "uploader_id": login,
                   "channel": "Overwatch Esports", "duration": duration,
                   "timestamp": 1756000000, "is_live": is_live,
                   "description": "OWCS official broadcast"}

        class Proc:
            pass
        proc = Proc()
        proc.returncode = returncode
        proc.stdout = json.dumps(payload) if stdout is None else stdout
        proc.stderr = stderr
        return proc
    return run


def fake_client(*, video_id="dQw4w9WgXcQ", channel_id=OFFICIAL_CHANNEL,
                title="OWCS 2026 Stage 2 | Team A vs Team B | Day 1",
                duration="PT4H12M30S", live="completed",
                items=None, raise_exc=None) -> yt.YouTubeClient:
    """A YouTubeClient wired to an injected transport that answers
    videos.list from in-test data (never the network)."""
    body = {"items": items if items is not None else [{
        "id": video_id,
        "snippet": {"channelId": channel_id, "channelTitle": "Overwatch Esports",
                    "title": title, "description": "Full broadcast VOD.",
                    "publishedAt": "2026-07-20T10:00:00Z",
                    "liveBroadcastContent": "none"},
        "status": {"privacyStatus": "public"},
        "contentDetails": {"duration": duration},
        "liveStreamingDetails": ({"actualStartTime": "2026-07-20T10:05:00Z",
                                  "actualEndTime": "2026-07-20T14:17:30Z"}
                                 if live == "completed" else
                                 {"scheduledStartTime": "2026-07-30T10:00:00Z"}
                                 if live == "upcoming" else {}),
    }]}

    def transport(url, headers):
        if raise_exc is not None:
            raise raise_exc
        return 200, json.dumps(body), None

    return yt.YouTubeClient(transport=transport, cache_dir=None)


class TestUrlNormalization(unittest.TestCase):
    """Every accepted spelling of the same broadcast collapses to one id,
    one canonical URL, and therefore one job key."""

    VARIANTS = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "http://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?t=90",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/v/dQw4w9WgXcQ",
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1h2m3s",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ#t=45",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123&index=4",
        "https://www.youtube.com/watch?si=abc123&v=dQw4w9WgXcQ&feature=share",
        "  https://www.youtube.com/watch?v=dQw4w9WgXcQ  ",
        "youtu.be/dQw4w9WgXcQ",
        "www.youtube.com/watch?v=dQw4w9WgXcQ",
    ]

    def test_every_variant_yields_the_same_identity(self):
        for url in self.VARIANTS:
            with self.subTest(url=url):
                p = li.parse_link(url)
                self.assertEqual(p["videoId"], "dQw4w9WgXcQ")
                self.assertEqual(p["canonicalUrl"],
                                 "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
                self.assertEqual(li.job_key_for(p["videoId"]),
                                 "record:dqw4w9wgxcq:source")

    def test_timestamps_are_parsed_but_never_change_identity(self):
        self.assertEqual(li.parse_link(
            "https://youtu.be/dQw4w9WgXcQ?t=90")["startSeconds"], 90)
        self.assertEqual(li.parse_link(
            "https://youtu.be/dQw4w9WgXcQ?t=90s")["startSeconds"], 90)
        self.assertEqual(li.parse_link(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1h2m3s"
        )["startSeconds"], 3723)
        self.assertEqual(li.parse_link(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ#t=45"
        )["startSeconds"], 45)
        # An unparseable timestamp is dropped, never fatal.
        self.assertIsNone(li.parse_link(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=banana"
        )["startSeconds"])

    def test_playlist_and_tracking_params_recorded_not_identifying(self):
        p = li.parse_link(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxyz&si=abc")
        self.assertEqual(p["playlistId"], "PLxyz")
        self.assertIn("list", p["droppedParams"])
        self.assertIn("si", p["droppedParams"])

    def test_rejections_carry_stable_codes(self):
        cases = {
            "": "empty_url",
            "   ": "empty_url",
            "ftp://youtube.com/watch?v=dQw4w9WgXcQ": "unsupported_scheme",
            "https://vimeo.com/watch?v=dQw4w9WgXcQ": "unsupported_host",
            "https://www.youtube.com/@OW_Esports": "no_video_id",
            # Twitch is a supported HOST now, but only /videos/<id> names a
            # broadcast: a channel page is whatever is live at the moment,
            # and a clip is not the VOD.
            "https://www.twitch.tv/ow_esports": "no_video_id",
            "https://www.twitch.tv/ow_esports/clip/SomeClipName": "no_video_id",
            "https://www.twitch.tv/videos/notanid": "malformed_video_id",
            "https://www.twitch.tv/videos/12345": "malformed_video_id",
            "https://www.youtube.com/watch?list=PLxyz": "no_video_id",
            "https://www.youtube.com/watch?v=tooshort": "malformed_video_id",
            "https://youtu.be/way-too-long-to-be-an-id": "malformed_video_id",
        }
        for url, code in cases.items():
            with self.subTest(url=url):
                with self.assertRaises(li.LinkIntakeError) as ctx:
                    li.parse_link(url)
                self.assertEqual(ctx.exception.code, code)


class IntakeTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = js.JobStore(os.path.join(self._tmp.name, "automation.sqlite"))

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()


class TestIngestLink(IntakeTestCase):
    def test_official_channel_is_auto_approved_and_ready_to_download(self):
        res = li.ingest_link(self.store,
                             "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                             client=fake_client(), channels=REGISTRY)
        self.assertTrue(res["created"])
        self.assertFalse(res["duplicate"])
        self.assertEqual(res["source"]["state"], li.SOURCE_APPROVED)
        self.assertTrue(res["source"]["autoApproved"])
        self.assertEqual(res["source"]["reasonCode"], "registry_channel")
        # An authorized source lands in ARCHIVED — the worker's claimable
        # "official broadcast linked, download not started" state.
        self.assertEqual(res["state"], sm.ARCHIVED)
        self.assertIn("worker-run", res["nextCommand"])

    def test_operator_supplies_only_the_url(self):
        """The whole point of Phase 1: no match id, channel id, video id,
        teams or layout id from the operator — intake derives or defers all
        of it."""
        res = li.ingest_link(self.store,
                             "https://youtu.be/dQw4w9WgXcQ?t=3600",
                             client=fake_client(), channels=REGISTRY)
        job = self.store.get(res["jobKey"])
        self.assertEqual(job.payload["videoId"], "dQw4w9WgXcQ")
        self.assertEqual(job.payload["channelId"], OFFICIAL_CHANNEL)
        self.assertEqual(job.payload["sourceUrl"],
                         "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        # Identity that intake cannot know is honestly UNKNOWN, not invented.
        for field in ("matchId", "teamA", "teamB", "expectedLayoutId"):
            self.assertIsNone(job.payload[field], field)
        self.assertEqual(job.payload["intake"]["startSecondsHint"], 3600)

    def test_duplicate_link_attaches_instead_of_creating_a_second_job(self):
        first = li.ingest_link(self.store,
                              "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                              client=fake_client(), channels=REGISTRY)
        for url in ("https://youtu.be/dQw4w9WgXcQ",
                    "https://www.youtube.com/live/dQw4w9WgXcQ?t=120",
                    "https://m.youtube.com/watch?v=dQw4w9WgXcQ&si=x"):
            again = li.ingest_link(self.store, url, client=fake_client(),
                                   channels=REGISTRY)
            self.assertFalse(again["created"])
            self.assertTrue(again["duplicate"])
            self.assertEqual(again["jobKey"], first["jobKey"])
        self.assertEqual(len(self.store.list_jobs(kind=models.KIND_RECORD)), 1)
        job = self.store.get(first["jobKey"])
        # Every paste is kept in the audit history.
        self.assertEqual(len(job.payload["intake"]["history"]), 3)

    def test_reprocessing_never_rewinds_a_job_already_in_flight(self):
        res = li.ingest_link(self.store,
                             "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                             client=fake_client(), channels=REGISTRY)
        self.store.transition(res["jobKey"], sm.DOWNLOADING)
        self.store.transition(res["jobKey"], sm.DOWNLOADED)
        again = li.ingest_link(self.store, "https://youtu.be/dQw4w9WgXcQ",
                               client=fake_client(), channels=REGISTRY)
        self.assertEqual(again["state"], sm.DOWNLOADED)

    def test_non_registry_channel_blocks_on_manual_approval(self):
        res = li.ingest_link(self.store,
                             "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                             client=fake_client(channel_id=OTHER_CHANNEL),
                             channels=REGISTRY)
        self.assertEqual(res["source"]["state"], li.SOURCE_PENDING)
        self.assertEqual(res["source"]["reasonCode"], "channel_not_in_registry")
        self.assertEqual(res["state"], sm.DISCOVERED)
        self.assertIn("approve-source", res["nextCommand"])

    def test_registry_row_without_a_confirmed_channel_id_authorizes_nothing(self):
        res = li.ingest_link(self.store,
                             "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                             client=fake_client(channel_id=None),
                             channels=REGISTRY)
        self.assertEqual(res["source"]["state"], li.SOURCE_PENDING)

    def test_disabled_registry_row_is_not_authority(self):
        res = li.ingest_link(self.store,
                             "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                             client=fake_client(channel_id="UCdisabledButReal0"),
                             channels=REGISTRY)
        self.assertEqual(res["source"]["state"], li.SOURCE_PENDING)

    def test_dry_run_writes_nothing(self):
        res = li.ingest_link(self.store,
                             "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                             client=fake_client(), channels=REGISTRY,
                             dry_run=True)
        self.assertTrue(res["dryRun"])
        self.assertEqual(self.store.list_jobs(), [])
        self.assertIsNone(self.store.get(res["jobKey"]))

    def test_dry_run_after_a_real_intake_still_writes_nothing(self):
        li.ingest_link(self.store, "https://youtu.be/dQw4w9WgXcQ",
                       client=fake_client(), channels=REGISTRY)
        before = self.store.get(li.job_key_for("dQw4w9WgXcQ")).updated_at
        li.ingest_link(self.store, "https://youtu.be/dQw4w9WgXcQ",
                       client=fake_client(), channels=REGISTRY, dry_run=True)
        self.assertEqual(
            self.store.get(li.job_key_for("dQw4w9WgXcQ")).updated_at, before)

    def test_refused_url_creates_no_job(self):
        with self.assertRaises(li.LinkIntakeError):
            li.ingest_link(self.store, "https://vimeo.com/watch?v=abc",
                           client=fake_client(), channels=REGISTRY)
        self.assertEqual(self.store.list_jobs(), [])


class TestMetadataFailures(IntakeTestCase):
    def test_missing_api_key_records_the_link_and_blocks_approval(self):
        client = yt.YouTubeClient(api_key=None, transport=None, cache_dir=None)
        res = li.ingest_link(self.store,
                             "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                             client=client, channels=REGISTRY)
        self.assertTrue(res["created"])          # link is never dropped
        self.assertEqual(res["metadata"]["status"], "unavailable")
        self.assertEqual(res["metadata"]["errorCode"], "no_api_key")
        self.assertEqual(res["source"]["state"], li.SOURCE_PENDING)
        self.assertTrue(any("metadata unavailable" in w for w in res["warnings"]))

    def test_quota_exceeded_is_classified_not_swallowed(self):
        def transport(url, headers):
            return 403, json.dumps({"error": {"errors": [
                {"reason": "quotaExceeded"}], "message": "quota"}}), "HTTP 403"
        client = yt.YouTubeClient(transport=transport, cache_dir=None)
        res = li.ingest_link(self.store, "https://youtu.be/dQw4w9WgXcQ",
                             client=client, channels=REGISTRY)
        self.assertEqual(res["metadata"]["errorCode"], "quota_exceeded")
        self.assertEqual(res["source"]["state"], li.SOURCE_PENDING)

    def test_deleted_or_private_video_reports_not_found(self):
        res = li.ingest_link(self.store, "https://youtu.be/dQw4w9WgXcQ",
                             client=fake_client(items=[]), channels=REGISTRY)
        self.assertEqual(res["metadata"]["status"], "not_found")
        self.assertEqual(res["metadata"]["errorCode"], "video_not_found")
        self.assertEqual(res["source"]["state"], li.SOURCE_PENDING)

    def test_network_error_is_classified(self):
        client = yt.YouTubeClient(
            transport=lambda u, h: (None, None, "connection refused"),
            cache_dir=None)
        res = li.ingest_link(self.store, "https://youtu.be/dQw4w9WgXcQ",
                             client=client, channels=REGISTRY)
        self.assertEqual(res["metadata"]["status"], "unavailable")
        self.assertTrue(res["metadata"]["errorCode"].startswith("api_"))

    def test_no_client_at_all_still_records_the_link(self):
        res = li.ingest_link(self.store, "https://youtu.be/dQw4w9WgXcQ",
                             client=None, channels=REGISTRY)
        self.assertTrue(res["created"])
        self.assertEqual(res["metadata"]["errorCode"], "no_client")
        self.assertEqual(res["source"]["state"], li.SOURCE_PENDING)


class TestBroadcastLikeness(IntakeTestCase):
    def test_promo_short_on_the_official_channel_warns_and_blocks(self):
        res = li.ingest_link(
            self.store, "https://youtu.be/dQw4w9WgXcQ",
            client=fake_client(title="Overwatch 2 lootbox giveaway! #shorts",
                               duration="PT28S", live="none"),
            channels=REGISTRY)
        self.assertEqual(res["likeness"]["confidence"], "unlikely")
        # 28 seconds is under the hard floor, so this is a REFUSAL rather
        # than a score to weigh — the warning says so in those words.
        self.assertTrue(res["likeness"]["refused"])
        self.assertTrue(any(w.startswith("REFUSED") for w in res["warnings"]),
                        res["warnings"])
        # Official channel is NOT enough when the video isn't a broadcast.
        self.assertEqual(res["source"]["state"], li.SOURCE_PENDING)
        self.assertEqual(res["source"]["reasonCode"], "broadcast_likeness_failed")

    def test_hero_guide_video_is_flagged(self):
        res = li.ingest_link(
            self.store, "https://youtu.be/dQw4w9WgXcQ",
            client=fake_client(title="Freja tips and tricks — hero guide",
                               duration="PT8M12S", live="none"),
            channels=REGISTRY)
        self.assertEqual(res["likeness"]["confidence"], "unlikely")
        self.assertEqual(res["source"]["reasonCode"], "broadcast_likeness_failed")

    def test_real_broadcast_passes_the_gate(self):
        res = li.ingest_link(self.store, "https://youtu.be/dQw4w9WgXcQ",
                             client=fake_client(), channels=REGISTRY)
        self.assertEqual(res["likeness"]["confidence"], "likely")
        self.assertEqual(res["source"]["state"], li.SOURCE_APPROVED)

    def test_live_and_upcoming_streams_are_warned_about(self):
        res = li.ingest_link(self.store, "https://youtu.be/dQw4w9WgXcQ",
                             client=fake_client(live="upcoming"),
                             channels=REGISTRY)
        self.assertTrue(any("upcoming" in w for w in res["warnings"]))
        job = self.store.get(res["jobKey"])
        self.assertTrue(any("only completed VODs" in b
                            for b in li.blocking_reasons(job)))


class TestManualApproval(IntakeTestCase):
    def _pending_job(self) -> str:
        res = li.ingest_link(self.store, "https://youtu.be/dQw4w9WgXcQ",
                             client=fake_client(channel_id=OTHER_CHANNEL),
                             channels=REGISTRY)
        return res["jobKey"]

    def test_approval_requires_confirm(self):
        key = self._pending_job()
        with self.assertRaises(li.LinkIntakeError) as ctx:
            li.approve_source(self.store, key, approved_by="alice")
        self.assertEqual(ctx.exception.code, "confirmation_required")
        self.assertEqual(self.store.get(key).state, sm.DISCOVERED)

    def test_approval_requires_an_accountable_human(self):
        key = self._pending_job()
        with self.assertRaises(li.LinkIntakeError) as ctx:
            li.approve_source(self.store, key, approved_by="  ", confirm=True)
        self.assertEqual(ctx.exception.code, "approver_required")

    def test_confirmed_approval_is_audited_and_unblocks_the_job(self):
        key = self._pending_job()
        res = li.approve_source(self.store, key, approved_by="alice",
                                reason="verified this is the official EMEA restream",
                                confirm=True)
        self.assertEqual(res["state"], sm.ARCHIVED)
        src = self.store.get(key).payload["source"]
        self.assertEqual(src["state"], li.SOURCE_APPROVED)
        self.assertFalse(src["autoApproved"])
        self.assertEqual(src["decidedBy"], "alice")
        self.assertEqual(src["reasonCode"], "manual_approval")
        self.assertEqual(src["priorReasonCode"], "channel_not_in_registry")
        self.assertTrue(src["decidedAt"])

    def test_rejection_is_recorded_and_the_job_is_never_downloadable(self):
        key = self._pending_job()
        res = li.approve_source(self.store, key, approved_by="bob",
                                reason="unofficial reupload", reject=True,
                                confirm=True)
        self.assertEqual(res["state"], sm.IGNORED)
        src = self.store.get(key).payload["source"]
        self.assertEqual(src["state"], li.SOURCE_REJECTED)
        self.assertEqual(src["decidedBy"], "bob")
        self.assertIn("rejected", li.next_command(self.store.get(key)))

    def test_repaste_does_not_overwrite_a_recorded_human_approval(self):
        key = self._pending_job()
        li.approve_source(self.store, key, approved_by="alice", confirm=True)
        again = li.ingest_link(self.store, "https://youtu.be/dQw4w9WgXcQ",
                               client=fake_client(channel_id=OTHER_CHANNEL),
                               channels=REGISTRY)
        self.assertEqual(again["source"]["decidedBy"], "alice")
        self.assertEqual(self.store.get(key).payload["source"]["state"],
                         li.SOURCE_APPROVED)

    def test_approving_an_unknown_job_fails_cleanly(self):
        with self.assertRaises(li.LinkIntakeError) as ctx:
            li.approve_source(self.store, "record:nope:source",
                              approved_by="alice", confirm=True)
        self.assertEqual(ctx.exception.code, "no_such_job")


class TestLinkStatus(IntakeTestCase):
    def test_status_by_job_video_id_and_url(self):
        res = li.ingest_link(self.store, "https://youtu.be/dQw4w9WgXcQ",
                             client=fake_client(), channels=REGISTRY)
        for kwargs in ({"job_key": res["jobKey"]},
                       {"video_id": "dQw4w9WgXcQ"},
                       {"url": "https://www.youtube.com/live/dQw4w9WgXcQ"}):
            rows = li.link_status(self.store, **kwargs)
            self.assertEqual(len(rows), 1, kwargs)
            self.assertEqual(rows[0]["videoId"], "dQw4w9WgXcQ")
            self.assertEqual(rows[0]["sourceState"], li.SOURCE_APPROVED)

    def test_status_lists_blocking_reasons_and_next_command(self):
        li.ingest_link(self.store, "https://youtu.be/dQw4w9WgXcQ",
                       client=fake_client(channel_id=OTHER_CHANNEL),
                       channels=REGISTRY)
        rows = li.link_status(self.store)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["blocking"])
        self.assertIn("approve-source", rows[0]["nextCommand"])
        self.assertIn("BLOCKED", li.format_status(rows))

    def test_empty_status_is_explicit(self):
        self.assertEqual(li.link_status(self.store), [])
        self.assertIn("no intake jobs", li.format_status([]))


class TestFiveMinuteFloor(IntakeTestCase):
    """The hard length gate. Everything else in the likeness score is a
    signal another signal can outvote; this one cannot be, because a
    professional series cannot happen in under five minutes."""

    def _ingest(self, *, url="https://youtu.be/dQw4w9WgXcQ", **kw):
        return li.ingest_link(self.store, url,
                              client=fake_client(**kw), channels=REGISTRY)

    def test_four_minute_upload_is_refused_however_matchy_the_title(self):
        res = self._ingest(title="OWCS 2026 Grand Finals | Team A vs Team B | Day 3",
                           duration="PT4M", live="completed")
        self.assertTrue(res["likeness"]["refused"])
        self.assertEqual(res["likeness"]["confidence"], "unlikely")
        self.assertEqual(res["source"]["state"], li.SOURCE_PENDING)

    def test_six_minute_upload_clears_the_floor(self):
        res = self._ingest(duration="PT6M")
        self.assertFalse(res["likeness"]["refused"])

    def test_a_shorts_url_is_refused_on_its_spelling_alone(self):
        res = self._ingest(url="https://www.youtube.com/shorts/dQw4w9WgXcQ",
                           title="OWCS 2026 Playoffs Day 1 Team A vs Team B",
                           duration="PT4H", live="completed")
        self.assertTrue(res["likeness"]["refused"])
        self.assertIn("/shorts/", res["likeness"]["refusalReason"])

    def test_a_shorts_url_is_still_the_same_job_as_the_watch_url(self):
        """Refusing on the spelling must not fork identity — the same video
        pasted both ways is still one job."""
        a = li.parse_link("https://www.youtube.com/shorts/dQw4w9WgXcQ")
        b = li.parse_link("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(a["videoId"], b["videoId"])
        self.assertEqual(a["canonicalUrl"], b["canonicalUrl"])
        self.assertTrue(a["isShortsUrl"])
        self.assertFalse(b["isShortsUrl"])

    def test_next_command_offers_no_approval_route_for_a_refused_video(self):
        self._ingest(duration="PT30S")
        job = self.store.get(li.job_key_for("dQw4w9WgXcQ"))
        self.assertIn("none —", li.next_command(job))
        self.assertTrue(any("too short" in r
                            for r in li.blocking_reasons(job)))

    def test_approving_a_refused_video_by_hand_is_refused_too(self):
        self._ingest(duration="PT30S")
        key = li.job_key_for("dQw4w9WgXcQ")
        with self.assertRaises(li.LinkIntakeError) as caught:
            li.approve_source(self.store, key, approved_by="Connor", confirm=True)
        self.assertEqual(caught.exception.code, "too_short_to_process")

    def test_force_is_the_documented_escape_hatch(self):
        self._ingest(duration="PT30S")
        key = li.job_key_for("dQw4w9WgXcQ")
        res = li.approve_source(self.store, key, approved_by="Connor",
                                confirm=True, force=True)
        self.assertEqual(res["source"]["state"], li.SOURCE_APPROVED)

    def test_rejecting_a_refused_video_never_needs_force(self):
        self._ingest(duration="PT30S")
        key = li.job_key_for("dQw4w9WgXcQ")
        res = li.approve_source(self.store, key, approved_by="Connor",
                                confirm=True, reject=True)
        self.assertEqual(res["source"]["state"], li.SOURCE_REJECTED)


class TestNewBroadcastHasAWayForward(IntakeTestCase):
    """NEEDS_LAYOUT is the expected state for a production nobody has
    calibrated yet, and the two situations behind it need opposite advice.
    Answering both with `approve-layout` is what dead-ended new broadcasts:
    when the resolver refused, there is nothing to approve and that command
    can only keep saying no."""

    def _job_in_needs_layout(self, layout):
        li.ingest_link(self.store, "https://youtu.be/dQw4w9WgXcQ",
                       client=fake_client(), channels=REGISTRY)
        key = li.job_key_for("dQw4w9WgXcQ")
        self.store.update_payload(key, {"layout": layout})
        self.store.transition(key, sm.DOWNLOADING)
        self.store.transition(key, sm.DOWNLOADED)
        self.store.transition(key, sm.NEEDS_LAYOUT)
        return self.store.get(key)

    def test_a_calibrated_candidate_asks_for_approval(self):
        job = self._job_in_needs_layout(
            {"approvalRequired": True, "calibration": {"confidence": 0.91}})
        self.assertIn("approve-layout", li.next_command(job))

    def test_a_refused_calibration_sends_you_to_the_wizard(self):
        job = self._job_in_needs_layout(
            {"approvalRequired": True,
             "blocked": "confidence 0.31 below floor 0.62",
             "calibration": {"refusal": "confidence 0.31 below floor 0.62"}})
        cmd = li.next_command(job)
        self.assertNotIn("approve-layout", cmd)
        self.assertIn("calibrate.html", cmd)
        self.assertIn("resolve-layout", cmd)
        self.assertTrue(any("new to the tracker" in r
                            for r in li.blocking_reasons(job)),
                        li.blocking_reasons(job))

    def test_the_wizard_link_carries_the_broadcast(self):
        job = self._job_in_needs_layout({"blocked": "no HUD found"})
        self.assertIn("watch?v=dQw4w9WgXcQ", li.next_command(job))


class TestStatesReadAsEnglish(unittest.TestCase):
    """`-> ARCHIVED` means "queued, ready to download". Read as English it
    means the opposite, which is exactly how a successful retry got read as
    a job being shelved."""

    def test_every_state_has_a_plain_label(self):
        for state in sm.ALL_STATES:
            self.assertIn(state, sm.LABELS, f"{state} has no plain-English label")

    def test_describe_pairs_the_code_with_the_meaning(self):
        text = sm.describe(sm.ARCHIVED)
        self.assertIn("ARCHIVED", text)
        self.assertIn("ready to download", text)

    def test_an_unknown_state_still_renders(self):
        self.assertEqual(sm.describe("WAT"), "WAT")



class TestTwitchIntake(IntakeTestCase):
    """Twitch is the one source unattended hardware can actually fetch.

    GitHub-hosted runners are bot-checked by YouTube on every player client
    (measured — see docs/UNATTENDED.md), while the same runner resolves a
    Twitch VOD and pulls frames from it with no cookies and no key. Intake
    used to reject twitch.tv outright, which meant the only reachable source
    was the only refused one.
    """

    def test_a_twitch_vod_is_parsed_canonicalised_and_identified(self):
        for spelling in (
            f"https://www.twitch.tv/videos/{TWITCH_VOD}",
            f"twitch.tv/videos/{TWITCH_VOD}",
            f"https://m.twitch.tv/videos/{TWITCH_VOD}?t=1h2m3s",
        ):
            with self.subTest(url=spelling):
                p = li.parse_link(spelling)
                self.assertEqual(p["platform"], li.TWITCH)
                self.assertEqual(p["videoId"], TWITCH_VOD)
                self.assertEqual(
                    p["canonicalUrl"],
                    f"https://www.twitch.tv/videos/{TWITCH_VOD}")

    def test_the_timestamp_is_a_hint_and_never_changes_identity(self):
        plain = li.parse_link(f"https://www.twitch.tv/videos/{TWITCH_VOD}")
        stamped = li.parse_link(
            f"https://www.twitch.tv/videos/{TWITCH_VOD}?t=1h2m3s")
        self.assertEqual(stamped["startSeconds"], 3723)
        self.assertIsNone(plain["startSeconds"])
        self.assertEqual(li.job_key_for(plain["videoId"], plain["platform"]),
                         li.job_key_for(stamped["videoId"], stamped["platform"]))

    def test_youtube_job_keys_are_unchanged_by_the_platform_argument(self):
        """Every key already in a job store must keep resolving."""
        self.assertEqual(li.job_key_for("dQw4w9WgXcQ"),
                         li.job_key_for("dQw4w9WgXcQ", li.YOUTUBE))
        self.assertEqual(li.canonical_url("dQw4w9WgXcQ"),
                         "https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_two_platforms_never_share_a_job_key(self):
        self.assertNotEqual(li.job_key_for("123456789", li.TWITCH),
                            li.job_key_for("123456789", li.YOUTUBE))

    def test_an_official_twitch_vod_auto_approves_with_no_key_at_all(self):
        res = li.ingest_link(
            self.store, f"https://www.twitch.tv/videos/{TWITCH_VOD}",
            client=None,                       # no YouTube client, no API key
            channels=REGISTRY,
            twitch_runner=fake_twitch_runner())
        self.assertTrue(res["created"])
        self.assertEqual(res["platform"], li.TWITCH)
        self.assertEqual(res["metadata"]["status"], "ok")
        self.assertEqual(res["metadata"]["channelId"], "ow_esports")
        self.assertEqual(res["source"]["state"], li.SOURCE_APPROVED)
        self.assertTrue(res["source"]["autoApproved"])
        self.assertEqual(res["source"]["registryChannel"], "ow_esports_twitch")

    def test_an_unregistered_twitch_channel_is_never_auto_approved(self):
        res = li.ingest_link(
            self.store, f"https://www.twitch.tv/videos/{TWITCH_VOD}",
            client=None, channels=REGISTRY,
            twitch_runner=fake_twitch_runner(login="some_random_streamer"))
        self.assertEqual(res["source"]["state"], li.SOURCE_PENDING)
        self.assertEqual(res["source"]["reasonCode"], "channel_not_in_registry")

    def test_a_youtube_channel_id_cannot_authorize_a_twitch_broadcast(self):
        """Channel ids are only unique WITHIN a platform. Authorization is
        the last gate before an unattended download, so it must not rest on
        two id namespaces happening not to overlap."""
        decision = li.authorize_source(
            {"status": "ok", "channelId": OFFICIAL_CHANNEL,
             "channelTitle": "Overwatch Esports"},
            registry=li.registry_channel_index(REGISTRY),
            platform=li.TWITCH)
        self.assertEqual(decision["state"], li.SOURCE_PENDING)
        self.assertEqual(decision["reasonCode"], "channel_platform_mismatch")
        self.assertFalse(decision["auto"])

    def test_a_still_live_twitch_stream_is_flagged_not_processed(self):
        res = li.ingest_link(
            self.store, f"https://www.twitch.tv/videos/{TWITCH_VOD}",
            client=None, channels=REGISTRY,
            twitch_runner=fake_twitch_runner(is_live=True))
        self.assertEqual(res["metadata"]["liveBroadcastStatus"], "live")
        self.assertTrue(any("only COMPLETED VODs" in w
                            for w in res["warnings"]))

    def test_metadata_failure_records_the_link_and_blocks_approval(self):
        for runner, code in (
            (fake_twitch_runner(returncode=1,
                                stderr="ERROR: video does not exist"),
             "video_not_found"),
            (fake_twitch_runner(raise_exc=FileNotFoundError("yt-dlp")),
             "no_ytdlp"),
            (fake_twitch_runner(stdout="not json at all"),
             "ytdlp_unparseable"),
        ):
            with self.subTest(code=code):
                store = js.JobStore(os.path.join(self._tmp.name, f"{code}.db"))
                res = li.ingest_link(
                    store, f"https://www.twitch.tv/videos/{TWITCH_VOD}",
                    client=None, channels=REGISTRY, twitch_runner=runner)
                self.assertTrue(res["created"])   # the link is never dropped
                self.assertEqual(res["metadata"]["errorCode"], code)
                self.assertEqual(res["source"]["state"], li.SOURCE_PENDING)

    def test_intake_is_idempotent_across_twitch_url_spellings(self):
        first = li.ingest_link(
            self.store, f"https://www.twitch.tv/videos/{TWITCH_VOD}",
            client=None, channels=REGISTRY,
            twitch_runner=fake_twitch_runner())
        second = li.ingest_link(
            self.store, f"twitch.tv/videos/{TWITCH_VOD}?t=42",
            client=None, channels=REGISTRY,
            twitch_runner=fake_twitch_runner())
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(len(self.store.list_jobs()), 1)


class TestJobKeyLookupsCarryThePlatform(IntakeTestCase):
    """A job is stored under a PLATFORM-QUALIFIED key. Every lookup path
    has to compute the same key, or a Twitch job is written once and then
    invisible to everything that goes looking for it — intake would create
    it again on the next scan, the CLI would report it missing, and the
    finder would render it as never-queued.

    This is a class of bug, not five bugs, so it is tested as one: the key
    a lookup computes must equal the key intake wrote."""

    def test_intake_writes_and_finds_the_same_key(self):
        res = li.ingest_link(
            self.store, f"https://www.twitch.tv/videos/{TWITCH_VOD}",
            client=None, channels=REGISTRY,
            twitch_runner=fake_twitch_runner())
        self.assertTrue(res["created"])
        self.assertIsNotNone(self.store.get(res["jobKey"]))
        # The key a caller derives from the URL alone must match it.
        parsed = li.parse_link(f"https://www.twitch.tv/videos/{TWITCH_VOD}")
        self.assertEqual(
            li.job_key_for(parsed["videoId"], parsed["platform"]),
            res["jobKey"])

    def test_link_status_finds_a_twitch_job_by_its_url(self):
        """The path that regressed: link_status parsed the URL for its id
        and then dropped the platform when building the key."""
        res = li.ingest_link(
            self.store, f"https://www.twitch.tv/videos/{TWITCH_VOD}",
            client=None, channels=REGISTRY,
            twitch_runner=fake_twitch_runner())
        rows = li.link_status(
            self.store, url=f"https://www.twitch.tv/videos/{TWITCH_VOD}")
        self.assertEqual([r["jobKey"] for r in rows], [res["jobKey"]])

    def test_a_youtube_lookup_is_unaffected(self):
        res = li.ingest_link(self.store, "https://youtu.be/dQw4w9WgXcQ",
                             client=fake_client(), channels=REGISTRY)
        rows = li.link_status(self.store,
                              url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual([r["jobKey"] for r in rows], [res["jobKey"]])
        self.assertEqual(res["jobKey"], li.job_key_for("dQw4w9WgXcQ"))

    def test_re_ingesting_a_twitch_link_never_makes_a_second_job(self):
        """If a lookup computed a different key, intake would look for an
        existing job, not find its own, and create another."""
        for _ in range(3):
            li.ingest_link(
                self.store, f"https://www.twitch.tv/videos/{TWITCH_VOD}",
                client=None, channels=REGISTRY,
                twitch_runner=fake_twitch_runner())
        self.assertEqual(len(self.store.list_jobs()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

