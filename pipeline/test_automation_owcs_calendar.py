#!/usr/bin/env python3
"""
test_automation_owcs_calendar.py — official OWCS calendar adapter (Phase
B3/C7). Covers: the new event-level fields (season, scheduledTime,
tournamentFormat, sourceUrl, retrievedAt, sourceHash, verificationStatus),
the resilient `__NEXT_DATA__` extraction path (found/missing/malformed),
and that a live-fetch failure degrades to an empty list rather than
raising or fabricating match pairings/times. No network.
Run: python3 pipeline/test_automation_owcs_calendar.py
"""
from __future__ import annotations
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from automation import owcs_calendar as cal  # noqa: E402


def next_data_html(events: list[dict]) -> str:
    import json
    blob = json.dumps({"props": {"pageProps": {"schedule": {"events": events}}}})
    return f'<html><head><script id="__NEXT_DATA__" type="application/json">{blob}</script></head><body></body></html>'


class TestNormalizeEventFields(unittest.TestCase):
    def test_new_fields_default_safely_when_absent(self):
        e = cal._normalize_event({"id": "e1", "name": "OWCS Test", "verified": False})
        self.assertIsNone(e.season)
        self.assertIsNone(e.scheduled_time)
        self.assertIsNone(e.tournament_format)
        self.assertIsNone(e.source_url)
        self.assertIsNone(e.retrieved_at)
        self.assertIsNone(e.source_hash)
        self.assertEqual(e.verification_status, "unverified")

    def test_new_fields_carried_through_when_present(self):
        e = cal._normalize_event({
            "id": "e1", "name": "OWCS Test", "season": "2026",
            "scheduledTime": "18:00:00Z", "tournamentFormat": "single elimination",
            "sourceUrl": "https://esports.overwatch.com/en-us/schedule",
            "retrievedAt": "2026-07-24T00:00:00+00:00", "sourceHash": "abc123",
            "verified": True,
        })
        self.assertEqual(e.season, "2026")
        self.assertEqual(e.scheduled_time, "18:00:00Z")
        self.assertEqual(e.tournament_format, "single elimination")
        self.assertEqual(e.source_url, "https://esports.overwatch.com/en-us/schedule")
        self.assertEqual(e.source_hash, "abc123")
        self.assertEqual(e.verification_status, "verified")

    def test_explicit_verification_status_overrides_verified_bool(self):
        e = cal._normalize_event({"id": "e1", "verified": True, "verificationStatus": "stale"})
        self.assertEqual(e.verification_status, "stale")

    def test_committed_seed_file_still_loads(self):
        events = cal.load_events()
        self.assertGreaterEqual(len(events), 1)
        for e in events:
            self.assertIn(e.verification_status, ("unverified", "verified", "stale", "failed"))


class TestNextDataExtraction(unittest.TestCase):
    def test_parses_embedded_json(self):
        html = next_data_html([{"id": "e1", "name": "OWCS Korea Stage 2", "region": "korea",
                               "startDate": "2026-08-01", "endDate": "2026-08-10"}])
        data = cal.parse_next_data(html)
        self.assertIsNotNone(data)
        events = cal.extract_events_from_next_data(data)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["name"], "OWCS Korea Stage 2")
        self.assertEqual(events[0]["region"], "korea")

    def test_missing_script_tag_returns_none(self):
        self.assertIsNone(cal.parse_next_data("<html><body>no data here</body></html>"))

    def test_malformed_json_returns_none(self):
        html = '<script id="__NEXT_DATA__">{not valid json</script>'
        self.assertIsNone(cal.parse_next_data(html))

    def test_case_insensitive_field_matching(self):
        # A reshaped page with different casing must still be found.
        data = {"schedule": {"items": [{"Name": "OWCS Reshaped", "StartDate": "2026-09-01"}]}}
        events = cal.extract_events_from_next_data(data)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["name"], "OWCS Reshaped")

    def test_no_event_shaped_objects_returns_empty(self):
        data = {"props": {"pageProps": {"unrelated": {"foo": "bar", "count": 3}}}}
        self.assertEqual(cal.extract_events_from_next_data(data), [])

    def test_never_fabricates_match_pairing_fields(self):
        html = next_data_html([{"id": "e1", "name": "OWCS Global Finals", "startDate": "2026-11-01"}])
        events = cal.extract_events_from_next_data(cal.parse_next_data(html))
        for e in events:
            self.assertNotIn("teamA", e)
            self.assertNotIn("teamB", e)
            self.assertNotIn("matchId", e)


class TestHttpFetcher(unittest.TestCase):
    def test_successful_fetch_stamps_provenance(self):
        html = next_data_html([{"id": "e1", "name": "OWCS Pacific Stage 2",
                               "startDate": "2026-08-01", "endDate": "2026-08-10"}])

        def fake_transport(url):
            return 200, html, None

        events = cal.http_fetcher("https://esports.overwatch.com/en-us/schedule", transport=fake_transport)
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual(e["sourceUrl"], "https://esports.overwatch.com/en-us/schedule")
        self.assertIsNotNone(e["retrievedAt"])
        self.assertIsNotNone(e["sourceHash"])
        self.assertEqual(e["verificationStatus"], "unverified")

    def test_network_error_returns_empty_list_not_raise(self):
        def fake_transport(url):
            return None, None, "connection refused"
        self.assertEqual(cal.http_fetcher("https://x.test", transport=fake_transport), [])

    def test_http_error_status_returns_empty_list(self):
        def fake_transport(url):
            return 404, None, "HTTP 404"
        self.assertEqual(cal.http_fetcher("https://x.test", transport=fake_transport), [])

    def test_reshaped_page_with_no_next_data_returns_empty_list(self):
        def fake_transport(url):
            return 200, "<html><body>totally different page</body></html>", None
        self.assertEqual(cal.http_fetcher("https://x.test", transport=fake_transport), [])

    def test_fetcher_result_loads_through_normalize_event(self):
        html = next_data_html([{"id": "e1", "name": "OWCS Japan Stage 2",
                               "startDate": "2026-08-01", "endDate": "2026-08-10"}])

        def fake_transport(url):
            return 200, html, None
        raw_events = cal.http_fetcher("https://x.test", transport=fake_transport)
        events = [cal._normalize_event(e) for e in raw_events]
        self.assertEqual(events[0].verification_status, "unverified")
        self.assertIsNotNone(events[0].source_hash)


if __name__ == "__main__":
    unittest.main(verbosity=2)
