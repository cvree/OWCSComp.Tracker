"""Offline tests for the auto match finder (automation/match_finder.py).

Everything runs with injected fetchers/runners — no network, no yt-dlp
binary required, no API key. Covers: RSS parsing, streams-tab
normalization, two-source merge, likeness gating, ledger idempotency,
report/job joining, snapshot export, and the failure paths (a dead source
is a recorded error, never a crash; the report never raises).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from automation import link_intake as li  # noqa: E402
from automation import job_store as js  # noqa: E402
from automation import match_finder as mf  # noqa: E402
import site_paths  # noqa: E402  (cross-drive-safe relative paths)


RSS_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/">
  <title>Overwatch Esports</title>
  <entry>
    <yt:videoId>bcast001AAA</yt:videoId>
    <title>OWCS 2026 Stage 2 Playoffs Day 3 - Team A vs Team B</title>
    <published>2026-07-20T17:00:00+00:00</published>
    <author><name>Overwatch Esports</name></author>
    <media:group><media:description>Full broadcast of the
    playoffs bracket.</media:description></media:group>
  </entry>
  <entry>
    <yt:videoId>promo002BBB</yt:videoId>
    <title>Top 10 Tips to Rank Up FAST</title>
    <published>2026-07-21T12:00:00+00:00</published>
    <author><name>Overwatch Esports</name></author>
    <media:group><media:description>guide</media:description></media:group>
  </entry>
  <entry>
    <title>malformed entry with no video id</title>
  </entry>
</feed>
"""

FLAT_FIXTURE = {
    "entries": [
        {"id": "bcast001AAA",
         "title": "OWCS 2026 Stage 2 Playoffs Day 3 - Team A vs Team B",
         "duration": 14400, "live_status": "was_live",
         "timestamp": 1784480400, "channel": "Overwatch Esports"},
        {"id": "strm003CCCC",
         "title": "OWCS 2026 Stage 2 - Group Stage Day 1 - BoS vs FOX",
         "duration": 12000, "live_status": "was_live",
         "timestamp": 1784000000, "channel": "Overwatch Esports"},
        {"id": "promo002BBB",
         "title": "Top 10 Tips to Rank Up FAST",
         "duration": 45, "live_status": None, "timestamp": None},
        {"title": "no id — must be skipped"},
    ]
}

CHANNEL = {"id": "test_chan", "channelId": "UCtest", "title": "OW Esports",
           "sourceUrl": "https://www.youtube.com/@ow_esports"}


class FakeRunner:
    """subprocess stand-in for the streams-tab fetch."""

    def __init__(self, stdout: str = "", returncode: int = 0,
                 stderr: str = "", raise_missing: bool = False):
        self.stdout, self.returncode = stdout, returncode
        self.stderr, self.raise_missing = stderr, raise_missing
        self.cmds: list[list[str]] = []

    def run(self, cmd, **kw):
        self.cmds.append(cmd)
        if self.raise_missing:
            raise FileNotFoundError("yt-dlp")

        class R:
            pass

        r = R()
        r.stdout, r.returncode, r.stderr = (self.stdout, self.returncode,
                                            self.stderr)
        return r


def _no_network(url):  # a test that hits this has broken the injection
    raise AssertionError(f"unexpected network fetch: {url}")


class TestRssParsing(unittest.TestCase):
    def test_parse_rss_extracts_entries_and_skips_malformed(self):
        entries = mf.parse_rss(RSS_FIXTURE)
        self.assertEqual([e["videoId"] for e in entries],
                         ["bcast001AAA", "promo002BBB"])
        e = entries[0]
        self.assertIn("Playoffs Day 3", e["title"])
        self.assertEqual(e["publishedAt"], "2026-07-20T17:00:00+00:00")
        self.assertIn("Full broadcast", e["description"])
        self.assertEqual(e["channelTitle"], "Overwatch Esports")
        self.assertIsNone(e["durationSeconds"])  # RSS honestly has none

    def test_unparseable_feed_raises_value_error(self):
        with self.assertRaises(ValueError):
            mf.parse_rss(b"this is not xml at all <<<")

    def test_fetch_rss_channel_records_error_instead_of_raising(self):
        def boom(url):
            raise OSError("connection refused")
        entries, err = mf.fetch_rss_channel("UCx", fetch=boom)
        self.assertEqual(entries, [])
        self.assertIn("UCx", err)
        self.assertIn("OSError", err)


class TestStreamsTab(unittest.TestCase):
    def test_normalizes_flat_entries(self):
        runner = FakeRunner(stdout=json.dumps(FLAT_FIXTURE))
        entries, err = mf.fetch_streams_tab(
            "https://www.youtube.com/@ow_esports", limit=10, runner=runner)
        self.assertIsNone(err)
        self.assertEqual(len(entries), 3)  # the no-id entry is skipped
        by_id = {e["videoId"]: e for e in entries}
        b = by_id["bcast001AAA"]
        self.assertEqual(b["durationSeconds"], 14400)
        self.assertEqual(b["liveBroadcastStatus"], "completed")  # was_live
        self.assertTrue(b["publishedAt"].startswith("2026-"))
        self.assertIsNone(by_id["promo002BBB"]["liveBroadcastStatus"])
        # the /streams tab is what got scanned, with the caller's limit
        self.assertIn("https://www.youtube.com/@ow_esports/streams",
                      runner.cmds[0])
        self.assertIn("10", runner.cmds[0])

    def test_ytdlp_failure_is_an_error_not_a_crash(self):
        entries, err = mf.fetch_streams_tab(
            "https://x", runner=FakeRunner(returncode=1, stderr="HTTP 403"))
        self.assertEqual(entries, [])
        self.assertIn("403", err)
        entries, err = mf.fetch_streams_tab(
            "https://x", runner=FakeRunner(raise_missing=True))
        self.assertEqual(entries, [])
        self.assertIn("yt-dlp not found", err)


class TestMergeAndLikeness(unittest.TestCase):
    def _candidates(self):
        rss = mf.parse_rss(RSS_FIXTURE)
        flat, _ = mf.fetch_streams_tab(
            "https://x", runner=FakeRunner(stdout=json.dumps(FLAT_FIXTURE)))
        return mf.merge_channel_entries(CHANNEL, rss, flat)

    def test_two_sources_merge_into_one_candidate(self):
        cands = {c["videoId"]: c for c in self._candidates()}
        self.assertEqual(len(cands), 3)
        b = cands["bcast001AAA"]
        self.assertEqual(b["sources"], ["rss", "streams"])
        # streams tab wins duration/live status; RSS supplies published
        self.assertEqual(b["durationSeconds"], 14400)
        self.assertEqual(b["liveBroadcastStatus"], "completed")
        self.assertEqual(b["publishedAt"], "2026-07-20T17:00:00+00:00")
        self.assertEqual(b["url"],
                         "https://www.youtube.com/watch?v=bcast001AAA")

    def test_likeness_gate_separates_broadcast_from_promo(self):
        cands = {c["videoId"]: c for c in self._candidates()}
        self.assertEqual(cands["bcast001AAA"]["likeness"]["confidence"],
                         "likely")
        promo = cands["promo002BBB"]["likeness"]
        self.assertEqual(promo["confidence"], "unlikely")
        self.assertTrue(promo["reasons"])  # never dropped silently

    def test_description_never_stored_on_a_candidate(self):
        for c in self._candidates():
            self.assertNotIn("description", c)


class TestLedger(unittest.TestCase):
    """NOTE: every load_ledger() call here pins BOTH `path` and `snapshot`
    to files inside a fresh temp dir. `load_ledger` falls back to the
    COMMITTED assets/data/matchfinder.v1.json when `path` is missing/corrupt
    and no explicit `snapshot` is given (see TestLedgerSnapshotFallback for
    that feature) — in CI the real find-matches step runs before these
    tests and legitimately populates that file with real broadcasts, so a
    test that omitted `snapshot=` would silently inherit production data
    instead of the empty ledger it assumes. This bit exactly once, in the
    first live CI run of this workflow."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "match_finder.json")
        self.snap = os.path.join(self._tmp.name, "never-written-snapshot.json")

    def tearDown(self):
        self._tmp.cleanup()

    def _load(self):
        return mf.load_ledger(self.path, snapshot=self.snap)

    def _scan_once(self, now):
        rss = mf.parse_rss(RSS_FIXTURE)
        cands = mf.merge_channel_entries(CHANNEL, rss, [])
        ledger = mf.merge_ledger(self._load(), cands,
                                 channels=[CHANNEL], source_errors=[],
                                 now=now)
        mf.save_ledger(ledger, self.path)
        return ledger

    def test_rescan_is_idempotent_and_preserves_first_seen(self):
        first = self._scan_once("2026-07-25T00:00:00+00:00")
        again = self._scan_once("2026-07-26T00:00:00+00:00")
        self.assertEqual(len(first["candidates"]), len(again["candidates"]))
        c0 = again["candidates"][0]
        self.assertEqual(c0["firstSeenAt"], "2026-07-25T00:00:00+00:00")
        self.assertEqual(c0["lastSeenAt"], "2026-07-26T00:00:00+00:00")

    def test_candidates_that_leave_the_feed_are_kept(self):
        self._scan_once("2026-07-25T00:00:00+00:00")
        ledger = mf.merge_ledger(
            self._load(), [], channels=[CHANNEL],
            source_errors=["rss UCtest: down"],
            now="2026-07-27T00:00:00+00:00")
        self.assertEqual(len(ledger["candidates"]), 2)  # archive, not window
        self.assertEqual(ledger["sourceErrors"], ["rss UCtest: down"])

    def test_newest_published_first_unknown_last(self):
        rss = mf.parse_rss(RSS_FIXTURE)
        flat, _ = mf.fetch_streams_tab(
            "https://x", runner=FakeRunner(stdout=json.dumps(FLAT_FIXTURE)))
        ledger = mf.merge_ledger(
            self._load(),
            mf.merge_channel_entries(CHANNEL, rss, flat),
            channels=[CHANNEL], source_errors=[], now="2026-07-25T00:00:00+00:00")
        ids = [c["videoId"] for c in ledger["candidates"]]
        # promo published 07-21 > broadcast 07-20 > stream 2026-07-13;
        # promo002BBB has a published date from RSS so it sorts by it
        self.assertEqual(ids[0], "promo002BBB")
        self.assertIn("bcast001AAA", ids[:2])

    def test_corrupt_ledger_file_resets_cleanly(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{corrupt json")
        led = self._load()
        self.assertEqual(led["candidates"], [])


class TestScanOrchestration(unittest.TestCase):
    """scan() calls load_ledger() with no arguments, which resolves BOTH
    LEDGER_REL and SNAPSHOT_REL against the repo root — so a test that
    swaps only LEDGER_REL still falls back to the COMMITTED
    assets/data/matchfinder.v1.json the instant its own tmp ledger doesn't
    exist yet (every case here). Both must be swapped, or these tests only
    pass by luck of an empty snapshot ever having been committed."""

    def test_scan_uses_injected_fetchers_only(self):
        runner = FakeRunner(stdout=json.dumps(FLAT_FIXTURE))
        fetches = []

        def fake_fetch(url):
            fetches.append(url)
            return RSS_FIXTURE
        with tempfile.TemporaryDirectory() as tmp:
            old_ledger, old_snap = mf.LEDGER_REL, mf.SNAPSHOT_REL
            # keep the real ledger + snapshot untouched: point the module at
            # a tmp dir for both, so no committed production data can leak in
            mf.LEDGER_REL = site_paths.site_relpath(
                os.path.join(tmp, "match_finder.json"), mf._repo_root())
            mf.SNAPSHOT_REL = site_paths.site_relpath(
                os.path.join(tmp, "never-written-snapshot.json"),
                mf._repo_root())
            try:
                ledger = mf.scan([CHANNEL], limit=5, fetch=fake_fetch,
                                 runner=runner,
                                 now="2026-07-25T00:00:00+00:00")
            finally:
                mf.LEDGER_REL, mf.SNAPSHOT_REL = old_ledger, old_snap
        self.assertEqual(len(fetches), 1)
        self.assertIn("UCtest", fetches[0])
        self.assertEqual(len(ledger["candidates"]), 3)
        self.assertEqual(ledger["sourceErrors"], [])

    def test_one_dead_source_still_yields_the_other(self):
        runner = FakeRunner(stdout=json.dumps(FLAT_FIXTURE))
        with tempfile.TemporaryDirectory() as tmp:
            old_ledger, old_snap = mf.LEDGER_REL, mf.SNAPSHOT_REL
            mf.LEDGER_REL = site_paths.site_relpath(
                os.path.join(tmp, "match_finder.json"), mf._repo_root())
            mf.SNAPSHOT_REL = site_paths.site_relpath(
                os.path.join(tmp, "never-written-snapshot.json"),
                mf._repo_root())
            try:
                ledger = mf.scan([CHANNEL], fetch=_no_network_soft,
                                 runner=runner,
                                 now="2026-07-25T00:00:00+00:00")
            finally:
                mf.LEDGER_REL, mf.SNAPSHOT_REL = old_ledger, old_snap
        self.assertEqual(len(ledger["sourceErrors"]), 1)
        self.assertIn("rss", ledger["sourceErrors"][0])
        self.assertEqual(len(ledger["candidates"]), 3)  # streams still there


def _no_network_soft(url):
    raise OSError("egress blocked")


class TestReportAndSnapshot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "automation.sqlite")
        self.store = js.JobStore(self.db)

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def _ledger(self):
        rss = mf.parse_rss(RSS_FIXTURE)
        flat, _ = mf.fetch_streams_tab(
            "https://x", runner=FakeRunner(stdout=json.dumps(FLAT_FIXTURE)))
        return mf.merge_ledger(
            {"schema": mf.SCHEMA, "candidates": []},
            mf.merge_channel_entries(CHANNEL, rss, flat),
            channels=[CHANNEL], source_errors=[],
            now="2026-07-25T00:00:00+00:00")

    def test_report_joins_intake_job_state(self):
        li.ingest_link(self.store,
                       "https://www.youtube.com/watch?v=bcast001AAA",
                       client=None, requested_by="test")
        report = mf.build_report(ledger=self._ledger(), store=self.store)
        by_id = {c["videoId"]: c for c in report["candidates"]}
        job = by_id["bcast001AAA"]["job"]
        self.assertIsNotNone(job)
        self.assertEqual(job["jobKey"], li.job_key_for("bcast001AAA"))
        self.assertIn("state", job)
        self.assertIn("nextCommand", job)
        self.assertIsNone(by_id["promo002BBB"]["job"])
        self.assertEqual(report["summary"]["tracked"], 1)
        self.assertEqual(report["summary"]["total"], 3)
        self.assertEqual(report["summary"]["likely"], 2)

    def test_an_unopenable_db_still_reports_every_candidate(self):
        """The guard that stops a scheduled scan from committing an empty
        snapshot over every broadcast ever found: the intake-state join is
        optional enrichment, so a dead DB costs the `job` field and nothing
        else."""
        # A DIRECTORY as the DB path: sqlite cannot open it. (A path inside a
        # missing directory would NOT work here — JobStore creates parents.)
        report = mf.build_report(self._tmp.name, ledger=self._ledger())
        self.assertEqual(report["summary"]["total"], 3)
        self.assertEqual(report["summary"]["tracked"], 0)
        self.assertTrue(all(c["job"] is None for c in report["candidates"]))
        self.assertTrue(any("join unavailable" in e
                            for e in report["sourceErrors"]),
                        "a degraded join must be reported, not hidden")

    def test_report_never_raises(self):
        # even a hostile ledger shape produces a valid empty report
        report = mf.build_report(ledger={"candidates": None}, store=self.store)
        self.assertEqual(report["schema"], mf.SCHEMA)
        self.assertEqual(report["candidates"], [])

    def test_snapshot_export_round_trips(self):
        report = mf.build_report(ledger=self._ledger(), store=self.store)
        path = os.path.join(self._tmp.name, "matchfinder.v1.json")
        mf.export_snapshot(report, path)
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
        self.assertEqual(loaded["schema"], mf.SCHEMA)
        self.assertEqual(len(loaded["candidates"]), 3)
        # nothing secret can be in the snapshot: only public metadata
        blob = json.dumps(loaded)
        self.assertNotIn("cookie", blob.lower())
        self.assertNotIn("api_key", blob.lower())

    def test_format_report_is_printable_and_actionable(self):
        text = mf.format_report(
            mf.build_report(ledger=self._ledger(), store=self.store))
        self.assertIn("convert-link", text)
        self.assertIn("bcast001AAA", text)
        self.assertIn("likely", text)


class TestStreamsUrlResolution(unittest.TestCase):
    """The /streams URL must be built from the confirmed channelId. The
    real registry's sourceUrl is a legacy custom URL
    (youtube.com/OW_Esports) whose /streams sub-path is not the canonical
    form; /channel/<id>/streams always resolves."""

    def test_channel_id_wins_over_a_legacy_custom_url(self):
        url = mf.channel_streams_url(
            {"channelId": "UCabc", "sourceUrl": "https://www.youtube.com/OW_Esports"})
        self.assertEqual(url, "https://www.youtube.com/channel/UCabc/streams")

    def test_falls_back_to_source_url_without_a_channel_id(self):
        url = mf.channel_streams_url(
            {"channelId": None, "sourceUrl": "https://www.youtube.com/@x"})
        self.assertEqual(url, "https://www.youtube.com/@x")
        self.assertIsNone(mf.channel_streams_url({}))

    def test_scan_scans_the_canonical_streams_url(self):
        runner = FakeRunner(stdout=json.dumps(FLAT_FIXTURE))
        with tempfile.TemporaryDirectory() as tmp:
            old = mf.LEDGER_REL
            mf.LEDGER_REL = site_paths.site_relpath(
                os.path.join(tmp, "l.json"), mf._repo_root())
            try:
                mf.scan([CHANNEL], fetch=lambda u: RSS_FIXTURE, runner=runner,
                        now="2026-07-25T00:00:00+00:00")
            finally:
                mf.LEDGER_REL = old
        self.assertIn("https://www.youtube.com/channel/UCtest/streams",
                      runner.cmds[0])

    def test_the_real_registry_channel_resolves_canonically(self):
        for ch in mf.scan_channels():
            self.assertRegex(mf.channel_streams_url(ch),
                             r"^https://www\.youtube\.com/channel/UC[\w-]+/streams$")


class TestLedgerSnapshotFallback(unittest.TestCase):
    """The ledger is gitignored operator state, so a CI runner starts every
    scan without one. Falling back to the COMMITTED snapshot is what keeps
    firstSeenAt and the archive intact across scheduled runs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ledger = os.path.join(self._tmp.name, "absent.json")
        self.snap = os.path.join(self._tmp.name, "snapshot.json")

    def tearDown(self):
        self._tmp.cleanup()

    def _write_snapshot(self, candidates):
        with open(self.snap, "w", encoding="utf-8") as f:
            json.dump({"schema": mf.SCHEMA,
                       "generatedAt": "2026-07-25T00:00:00+00:00",
                       "candidates": candidates}, f)

    def test_missing_ledger_recovers_the_archive_from_the_snapshot(self):
        self._write_snapshot([{"videoId": "bcast001AAA",
                               "firstSeenAt": "2026-07-20T00:00:00+00:00"}])
        led = mf.load_ledger(self.ledger, snapshot=self.snap)
        self.assertEqual(led["candidates"][0]["firstSeenAt"],
                         "2026-07-20T00:00:00+00:00")

    def test_stale_job_state_never_survives_into_the_ledger(self):
        self._write_snapshot([{"videoId": "bcast001AAA",
                               "job": {"state": "PUBLISHED"}}])
        led = mf.load_ledger(self.ledger, snapshot=self.snap)
        self.assertNotIn("job", led["candidates"][0])

    def test_a_present_ledger_is_preferred_over_the_snapshot(self):
        self._write_snapshot([{"videoId": "from-snapshot"}])
        with open(self.ledger, "w", encoding="utf-8") as f:
            json.dump({"schema": mf.SCHEMA,
                       "candidates": [{"videoId": "from-ledger"}]}, f)
        led = mf.load_ledger(self.ledger, snapshot=self.snap)
        self.assertEqual(led["candidates"][0]["videoId"], "from-ledger")

    def test_neither_file_yields_a_valid_empty_ledger(self):
        led = mf.load_ledger(self.ledger,
                             snapshot=os.path.join(self._tmp.name, "nope.json"))
        self.assertEqual(led["candidates"], [])
        self.assertEqual(led["schema"], mf.SCHEMA)

    def test_a_rescan_after_recovery_keeps_the_original_first_seen(self):
        """The property the whole fallback exists for: the scheduled CI run
        must not reset firstSeenAt every 6 hours."""
        self._write_snapshot([{"videoId": "bcast001AAA",
                               "firstSeenAt": "2026-07-20T00:00:00+00:00",
                               "publishedAt": "2026-07-20T17:00:00+00:00"}])
        recovered = mf.load_ledger(self.ledger, snapshot=self.snap)
        merged = mf.merge_ledger(
            recovered,
            mf.merge_channel_entries(CHANNEL, mf.parse_rss(RSS_FIXTURE), []),
            channels=[CHANNEL], source_errors=[],
            now="2026-07-29T00:00:00+00:00")
        row = {c["videoId"]: c for c in merged["candidates"]}["bcast001AAA"]
        self.assertEqual(row["firstSeenAt"], "2026-07-20T00:00:00+00:00")
        self.assertEqual(row["lastSeenAt"], "2026-07-29T00:00:00+00:00")


class TestChannelAuthority(unittest.TestCase):
    def test_only_verified_enabled_channels_are_scanned(self):
        rows = [
            {"id": "ok", "channelId": "UC1", "enabled": True,
             "verifiedStatus": "verified", "sourceUrl": "https://y/1"},
            {"id": "no-id", "channelId": None, "enabled": True},
            {"id": "disabled", "channelId": "UC2", "enabled": False},
            {"id": "failed", "channelId": "UC3", "enabled": True,
             "verifiedStatus": "failed"},
        ]
        chans = mf.scan_channels(rows)
        self.assertEqual([c["id"] for c in chans], ["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
