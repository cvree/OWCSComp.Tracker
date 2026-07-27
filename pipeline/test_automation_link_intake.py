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
]


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
            "https://twitch.tv/videos/12345": "unsupported_host",
            "https://www.youtube.com/@OW_Esports": "no_video_id",
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
            li.ingest_link(self.store, "https://twitch.tv/videos/1",
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
        self.assertTrue(any("broadcast-likeness WARNING" in w
                            for w in res["warnings"]))
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
