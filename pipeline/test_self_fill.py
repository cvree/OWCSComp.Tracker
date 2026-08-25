#!/usr/bin/env python3
"""
test_self_fill.py — the layer that makes the published site fill itself.

Everything here is offline and deterministic: `self_fill` reads committed
files and writes one artifact, so every test drives it with in-memory
fixtures and never touches the network, the DB or the real repo files.

What is covered:

  * title reading — event/stage/day/week/region/phase are taken from the
    title when it says them and are NULL when it does not (the invariant
    that keeps this layer from inventing facts);
  * calendar linking — the evidence ladder (date -> +region -> +stage), a
    real multi-event link when the title itself names both, and a REFUSAL
    with its reason when several events fit and nothing distinguishes them;
  * lifecycle state — a broadcast already published is never offered as a
    find, an in-flight intake job is reported as in flight, and a refused
    upload is ignored rather than dropped;
  * purity — identical inputs produce identical bytes, and no wall clock
    reaches the artifact (which is what makes `--check` a usable CI gate);
  * safety — the payload can never carry a hero composition, a score, or
    anything credential-shaped;
  * the match-finder date backfill that feeds it: bounded, refusal-aware,
    metadata-only, and never able to invent a date it was not given.

Run: python3 pipeline/test_self_fill.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from automation import match_finder as mf  # noqa: E402
from automation import self_fill as sf  # noqa: E402


# --------------------------------------------------------------- fixtures
def candidate(vid, title, *, published="2026-07-20T18:00:00+00:00",
              confidence="likely", refused=False, duration=14400,
              job=None, reasons=None):
    return {
        "videoId": vid,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "title": title,
        "publishedAt": published,
        "durationSeconds": duration,
        "channelId": "UCtest",
        "channelTitle": "Overwatch Esports",
        "channelRegistryId": "ow_esports_global",
        "firstSeenAt": "2026-07-20T19:00:00+00:00",
        "lastSeenAt": "2026-07-21T19:00:00+00:00",
        "liveBroadcastStatus": "completed",
        "sources": ["streams"],
        "likeness": {"confidence": confidence, "score": 60,
                     "reasons": reasons or ["+15 tournament terminology"],
                     "refused": refused,
                     "refusalReason": "too short" if refused else None},
        "job": job,
    }


def snapshot(candidates, *, generated="2026-07-21T19:00:00+00:00",
             errors=None):
    return {
        "schema": "matchfinder.v1",
        "generatedAt": generated,
        "channels": [{"id": "ow_esports_global", "channelId": "UCtest",
                      "title": "Overwatch Esports",
                      "sourceUrl": "https://www.youtube.com/OW_Esports"}],
        "sourceErrors": list(errors or []),
        "candidates": candidates,
        "summary": {},
    }


CALENDAR = {"events": [
    {"id": "na_stage2", "name": "OWCS 2026 — North America Stage 2",
     "region": "na", "stage": "Stage 2",
     "startDate": "2026-07-01", "endDate": "2026-08-15"},
    {"id": "emea_stage2", "name": "OWCS 2026 — EMEA Stage 2",
     "region": "emea", "stage": "Stage 2",
     "startDate": "2026-07-01", "endDate": "2026-08-15"},
    {"id": "korea_stage2", "name": "OWCS 2026 — Korea Stage 2",
     "region": "korea", "stage": "Stage 2",
     "startDate": "2026-07-01", "endDate": "2026-08-15"},
    {"id": "na_stage3", "name": "OWCS 2026 — North America Stage 3",
     "region": "na", "stage": "Stage 3",
     "startDate": "2026-07-01", "endDate": "2026-08-15"},
]}

PUBLIC = {
    "matches": [{
        "id": "m-published",
        "streamUrl": "https://www.youtube.com/live/PUBLISHED01",
        "sources": [{"type": "vod",
                     "url": "https://www.youtube.com/live/PUBLISHED01"}],
        "captureRunId": "run-1",
    }],
    "captureRuns": [{"id": "run-1",
                     "url": "https://youtu.be/RUNONLY0001"}],
    "vodSources": [{"matchId": "m-published",
                    "url": "https://www.youtube.com/watch?v=VODONLY0001"}],
}


def build(candidates, **kw):
    return sf.build(snapshot=snapshot(candidates, **kw), public=PUBLIC,
                    calendar=CALENDAR, interval_hours=6)


def row_for(payload, vid):
    return {b["videoId"]: b for b in payload["broadcasts"]}[vid]


# ------------------------------------------------------------ title read
class TestTitleReading(unittest.TestCase):
    def test_a_full_title_is_read_into_its_parts(self):
        p = sf.parse_title("[DROPS] OWCS 2026 | NA/EMEA | Stage 2 Week 3 Day 2")
        self.assertEqual(p["stage"], 2)
        self.assertEqual(p["week"], 3)
        self.assertEqual(p["day"], 2)
        self.assertEqual(p["year"], 2026)
        self.assertEqual(p["regions"], ["na", "emea"])
        self.assertEqual(p["confidence"], "clear")

    def test_the_day_is_stripped_from_the_event_name_but_the_stage_is_not(self):
        """A day identifies one BROADCAST inside an event; a stage is part
        of the event's own identity. Getting this backwards would either
        split one event into five or merge two stages into one."""
        a = sf.parse_title("OWCS 2026 | Stage 2 Day 1")
        b = sf.parse_title("OWCS 2026 | Stage 2 Day 4")
        c = sf.parse_title("OWCS 2026 | Stage 3 Day 1")
        self.assertEqual(a["eventKey"], b["eventKey"])
        self.assertNotEqual(a["eventKey"], c["eventKey"])
        self.assertIn("Stage 2", a["eventName"])
        self.assertNotIn("Day", a["eventName"])

    def test_a_hyphenated_day_is_still_a_day(self):
        self.assertEqual(sf.parse_title("2026 OWCS Bootcamp Day-3")["day"], 3)

    def test_an_emptied_bracket_does_not_split_an_event_in_two(self):
        with_day = sf.parse_title("Overwatch Collegiate Homecoming 2025 [DAY 1]")
        finals = sf.parse_title("Overwatch Collegiate Homecoming 2025")
        self.assertEqual(with_day["eventKey"], finals["eventKey"])

    def test_nothing_is_invented_from_a_bare_title(self):
        p = sf.parse_title("Overwatch Esports Stream")
        for field in ("stage", "day", "week", "year", "phase", "fixture"):
            self.assertIsNone(p[field], f"{field} was invented")
        self.assertEqual(p["regions"], [])
        self.assertEqual(p["confidence"], "title-only")

    def test_a_region_word_inside_another_word_is_not_a_region(self):
        self.assertEqual(sf.parse_title("Final Analysis")["regions"], [])
        self.assertEqual(sf.parse_title("Banana Bread Cup")["regions"], [])

    def test_a_fixture_is_read_only_when_the_title_states_one(self):
        self.assertEqual(
            sf.parse_title("OWCS | Team Falcons vs Crazy Raccoon")["fixture"],
            ["Team Falcons", "Crazy Raccoon"])
        self.assertIsNone(sf.parse_title("OWCS 2026 Day 1")["fixture"])

    def test_promotional_uploads_are_marked_as_companions(self):
        self.assertTrue(sf.parse_title("Midseason Championship Recap")["companion"])
        self.assertFalse(sf.parse_title("OWCS 2026 | Stage 2 Day 1")["companion"])

    def test_emoji_and_hashtags_are_removed_not_kept_as_words(self):
        p = sf.parse_title("The Throne has a New Ruler \U0001F3C6 | 2026 OWCS "
                           "Midseason Championship #overwatch")
        self.assertNotIn("#overwatch", p["eventName"])
        self.assertNotIn("\U0001F3C6", p["eventName"])


# -------------------------------------------------------------- calendar
class TestCalendarLinking(unittest.TestCase):
    def test_a_region_and_stage_pin_one_event(self):
        p = sf.parse_title("OWCS 2026 | North America | Stage 2 Day 1")
        cal = sf.match_calendar_event(p, "2026-07-20T18:00:00+00:00",
                                      CALENDAR["events"])
        self.assertEqual(cal["eventId"], "na_stage2")
        self.assertEqual(cal["matchedBy"], "date+region+stage")

    def test_a_title_naming_two_regions_links_to_both_events(self):
        """An "NA/EMEA Stage 2" broadcast genuinely belongs to both regional
        events. Refusing that link would be less accurate, not more."""
        p = sf.parse_title("OWCS 2026 | NA/EMEA | Stage 2 Day 1")
        cal = sf.match_calendar_event(p, "2026-07-20T18:00:00+00:00",
                                      CALENDAR["events"])
        self.assertEqual(cal["eventIds"], ["emea_stage2", "na_stage2"])
        self.assertIsNone(cal["eventId"], "two events is not one event")
        self.assertEqual(cal["matchedBy"], "date+region+stage")

    def test_an_ambiguous_date_refuses_to_link_and_says_why(self):
        p = sf.parse_title("Overwatch Esports Stream")
        cal = sf.match_calendar_event(p, "2026-07-20T18:00:00+00:00",
                                      CALENDAR["events"])
        self.assertIsNone(cal["eventId"])
        self.assertEqual(cal["eventIds"], [])
        self.assertIn("does not say which", cal["why"])
        self.assertEqual(len(cal["candidates"]), 4,
                         "every candidate must be listed, not hidden")

    def test_a_date_outside_every_window_links_to_nothing(self):
        p = sf.parse_title("OWCS 2026 | North America | Stage 2 Day 1")
        cal = sf.match_calendar_event(p, "2025-01-01T00:00:00+00:00",
                                      CALENDAR["events"])
        self.assertIsNone(cal["eventId"])
        self.assertIn("no calendar event", cal["why"])

    def test_a_dateless_broadcast_is_never_placed_on_the_calendar(self):
        p = sf.parse_title("OWCS 2026 | North America | Stage 2 Day 1")
        cal = sf.match_calendar_event(p, None, CALENDAR["events"])
        self.assertIsNone(cal["eventId"])
        self.assertIn("no publish date", cal["why"])


# ----------------------------------------------------------------- state
class TestLifecycleState(unittest.TestCase):
    def test_a_published_broadcast_is_never_offered_as_a_find(self):
        for vid in ("PUBLISHED01", "RUNONLY0001", "VODONLY0001"):
            payload = build([candidate(vid, "OWCS 2026 | Stage 2 Day 1")])
            row = row_for(payload, vid)
            self.assertEqual(row["state"], "published", vid)
            self.assertEqual(row["matchId"], "m-published", vid)
            self.assertIsNone(row["nextAction"], vid)

    def test_an_intake_job_reports_where_that_job_actually_is(self):
        payload = build([
            candidate("INFLIGHT001", "OWCS 2026 | Stage 2 Day 1",
                      job={"state": "DOWNLOADING", "jobKey": "k1"}),
            candidate("REVIEWME001", "OWCS 2026 | Stage 2 Day 2",
                      job={"state": "NEEDS_REVIEW", "jobKey": "k2"}),
        ])
        self.assertEqual(row_for(payload, "INFLIGHT001")["state"], "working")
        self.assertEqual(row_for(payload, "REVIEWME001")["state"], "review")

    def test_a_refused_upload_is_ignored_but_never_dropped(self):
        payload = build([candidate("SHORT000001", "Top 10 Tips", duration=45,
                                   confidence="unlikely", refused=True)])
        row = row_for(payload, "SHORT000001")
        self.assertEqual(row["state"], "ignored")
        self.assertIsNone(row["nextAction"],
                          "an ignored upload must not be offered for processing")
        self.assertEqual(payload["summary"]["ignored"], 1)

    def test_a_found_broadcast_carries_both_a_link_and_a_command(self):
        payload = build([candidate("FINDME00001", "OWCS 2026 | Stage 2 Day 1")])
        action = row_for(payload, "FINDME00001")["nextAction"]
        self.assertIn("submit.html?url=", action["href"])
        self.assertIn("convert-link", action["command"])

    def test_a_companion_upload_is_listed_but_not_offered_for_processing(self):
        payload = build([candidate("RECAP000001",
                                   "OWCS 2026 Midseason Championship Recap")])
        row = row_for(payload, "RECAP000001")
        self.assertTrue(row["parsed"]["companion"])
        self.assertIsNone(row["nextAction"])


# --------------------------------------------------------------- rollups
class TestRollups(unittest.TestCase):
    def setUp(self):
        self.payload = build([
            candidate("DAY10000001", "OWCS 2026 | NA/EMEA | Stage 2 Day 1"),
            candidate("DAY20000001", "OWCS 2026 | NA/EMEA | Stage 2 Day 2"),
            candidate("OTHER000001", "Calling All Heroes 2026 | Day 1"),
            candidate("SHORT000001", "Top 10 Tips", duration=45,
                      confidence="unlikely", refused=True),
        ])

    def test_days_of_one_event_roll_up_into_one_row(self):
        events = {e["key"]: e for e in self.payload["events"]}
        stage2 = [e for k, e in events.items() if "stage-2" in k][0]
        self.assertEqual(stage2["broadcasts"], 2)
        self.assertEqual(stage2["days"], [1, 2])

    def test_an_ignored_upload_is_in_no_event(self):
        for ev in self.payload["events"]:
            self.assertNotIn("Top 10", ev["name"])

    def test_the_summary_adds_up(self):
        s = self.payload["summary"]
        self.assertEqual(s["broadcastsKnown"], 4)
        self.assertEqual(s["awaitingProcessing"], 3)
        self.assertEqual(s["ignored"], 1)
        self.assertEqual(s["channelsScanned"], 1)

    def test_broadcasts_are_newest_first_with_the_dateless_tail_last(self):
        payload = build([
            candidate("OLD00000001", "OWCS 2026 | Stage 2 Day 1",
                      published="2026-07-01T00:00:00+00:00"),
            candidate("NODATE00001", "OWCS 2026 | Stage 2 Day 9",
                      published=None),
            candidate("NEW00000001", "OWCS 2026 | Stage 2 Day 2",
                      published="2026-07-30T00:00:00+00:00"),
        ])
        self.assertEqual([b["videoId"] for b in payload["broadcasts"]],
                         ["NEW00000001", "OLD00000001", "NODATE00001"])


# ---------------------------------------------------------------- purity
class TestArtifactIsPure(unittest.TestCase):
    def test_the_same_inputs_produce_the_same_bytes(self):
        rows = [candidate("SAME0000001", "OWCS 2026 | Stage 2 Day 1")]
        self.assertEqual(sf.render_js(build(rows)), sf.render_js(build(rows)))

    def test_no_wall_clock_reaches_the_artifact(self):
        """`generatedAt` is copied from the SCAN. If it were `now`, the
        artifact would differ on every rebuild, every scheduled run would
        commit a no-op diff, and `--check` could never be a CI gate."""
        payload = build([candidate("SAME0000001", "OWCS 2026 | Stage 2 Day 1")],
                        generated="2026-07-21T19:00:00+00:00")
        self.assertEqual(payload["generatedAt"], "2026-07-21T19:00:00+00:00")
        self.assertEqual(payload["scan"]["nextExpectedAt"],
                         "2026-07-22T01:00:00+00:00")

    def test_the_written_file_assigns_the_global_the_pages_read(self):
        payload = build([candidate("SAME0000001", "OWCS 2026 | Stage 2 Day 1")])
        with tempfile.TemporaryDirectory() as tmp:
            path = sf.write(payload, os.path.join(tmp, "discovered.v1.js"))
            text = open(path, encoding="utf-8").read()
        self.assertIn("window.OWCS_DISCOVERED = {", text)
        self.assertTrue(text.rstrip().endswith(";"))
        body = json.loads(text[text.index("{"):text.rstrip().rstrip(";").rindex("}") + 1])
        self.assertEqual(body["schema"], "discovered.v1")

    def test_a_missing_input_degrades_the_build_instead_of_killing_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = sf.build(root=tmp)
        self.assertEqual(payload["broadcasts"], [])
        self.assertTrue(any(not i["loaded"] for i in payload["inputs"]),
                        "a missing input must be reported, not hidden")
        self.assertIn("MISSING INPUT", sf.format_report(payload))


# ---------------------------------------------------------------- safety
class TestSafety(unittest.TestCase):
    """These assert on the payload's KEYS, not on its text. A broadcast
    title legitimately contains words like "Heroes" (Calling All Heroes is
    a real OWCS event), so a substring scan over the whole document would
    be both false-positive on real data and no proof of anything."""

    @staticmethod
    def all_keys(node, out=None):
        out = out if out is not None else set()
        if isinstance(node, dict):
            for k, v in node.items():
                out.add(str(k).lower())
                TestSafety.all_keys(v, out)
        elif isinstance(node, list):
            for v in node:
                TestSafety.all_keys(v, out)
        return out

    def test_the_payload_can_never_carry_a_composition_or_a_result(self):
        keys = self.all_keys(build([
            candidate("SAFE0000001", "OWCS 2026 | Stage 2 Day 1"),
            candidate("SAFE0000002", "OWCS 2026 | Team A vs Team B"),
        ]))
        for forbidden in ("heroes", "compsnapshots", "scorea", "scoreb",
                          "winner", "reviewstatus", "maps", "mapresults"):
            self.assertNotIn(forbidden, keys,
                             f"the discovery layer leaked `{forbidden}` — it "
                             "must never carry published match data")

    def test_no_credential_shaped_key_can_appear(self):
        keys = self.all_keys(build([
            candidate("SAFE0000001", "OWCS 2026 | Stage 2 Day 1")]))
        for forbidden in ("api_key", "apikey", "token", "secret", "password",
                          "cookie", "authorization"):
            self.assertNotIn(forbidden, keys)

    def test_the_real_committed_artifact_carries_no_match_data_either(self):
        keys = self.all_keys(sf.build())
        for forbidden in ("heroes", "compsnapshots", "scorea", "winner",
                          "reviewstatus"):
            self.assertNotIn(forbidden, keys)

    def test_the_committed_artifact_is_in_step_with_the_committed_scan(self):
        """The CI gate, asserted here so the suite fails locally too: the
        checked-in discovery layer must be exactly what a fresh build of the
        checked-in scan produces."""
        path = sf.path_for(sf.OUTPUT_REL)
        self.assertTrue(os.path.exists(path),
                        "assets/data/discovered.v1.js is missing — run "
                        "`python3 pipeline/automation/cli.py self-fill`")
        with open(path, encoding="utf-8") as f:
            committed = f.read()
        self.assertEqual(
            committed, sf.render_js(sf.build()),
            "assets/data/discovered.v1.js is out of date — run "
            "`python3 pipeline/automation/cli.py self-fill`")


# ------------------------------------------------------- workflow cadence
class TestScanCadence(unittest.TestCase):
    def test_the_interval_is_read_from_the_real_workflow(self):
        hours = sf.scan_interval_hours(sf.path_for(sf.WORKFLOW_REL))
        self.assertIsNotNone(hours, "the match-finder cron stopped parsing")
        self.assertTrue(0 < hours <= 24)

    def test_an_unparseable_cron_makes_no_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "wf.yml")
            with open(path, "w", encoding="utf-8") as f:
                f.write("on:\n  schedule:\n    - cron: \"17 3,9,15 * * *\"\n")
            self.assertIsNone(sf.scan_interval_hours(path))


# --------------------------------------------------------- date backfill
class TestDateBackfill(unittest.TestCase):
    """The finder-side half: a broadcast with no air date can never be
    placed on the calendar, so the scan fills a bounded number per run."""

    def ledger(self):
        return {"candidates": [
            dict(candidate("NODATE00001", "OWCS 2026 | Stage 2 Day 1",
                           published=None), job=None),
            dict(candidate("NODATE00002", "OWCS 2026 | Stage 2 Day 2",
                           published=None), job=None),
            dict(candidate("SHORT000001", "Top 10 Tips", published=None,
                           refused=True, confidence="unlikely"), job=None),
            dict(candidate("HASDATE0001", "OWCS 2026 | Stage 2 Day 3"),
                 job=None),
        ]}

    def test_the_budget_is_respected(self):
        calls = []

        def meta(vid):
            calls.append(vid)
            return {"publishedAt": "2026-07-15T00:00:00+00:00",
                    "durationSeconds": None, "liveBroadcastStatus": None}, None

        _led, errors, filled = mf.fill_missing_dates(
            self.ledger(), limit=1, fetch_meta=meta)
        self.assertEqual(filled, 1)
        self.assertEqual(calls, ["NODATE00001"])
        self.assertEqual(errors, [])

    def test_a_refused_upload_never_costs_a_request(self):
        calls = []

        def meta(vid):
            calls.append(vid)
            return {"publishedAt": "2026-07-15T00:00:00+00:00"}, None

        mf.fill_missing_dates(self.ledger(), limit=10, fetch_meta=meta)
        self.assertNotIn("SHORT000001", calls)
        self.assertNotIn("HASDATE0001", calls,
                         "a row that already has a date must not be re-fetched")

    def test_zero_disables_it_entirely(self):
        def meta(vid):  # pragma: no cover - must never run
            raise AssertionError("fill_missing_dates called with limit=0")

        _led, errors, filled = mf.fill_missing_dates(
            self.ledger(), limit=0, fetch_meta=meta)
        self.assertEqual((errors, filled), ([], 0))

    def test_a_failed_lookup_is_recorded_and_the_scan_continues(self):
        def meta(vid):
            if vid == "NODATE00001":
                return None, "date-backfill NODATE00001: yt-dlp exit 1"
            return {"publishedAt": "2026-07-15T00:00:00+00:00"}, None

        _led, errors, filled = mf.fill_missing_dates(
            self.ledger(), limit=5, fetch_meta=meta)
        self.assertEqual(len(errors), 1)
        self.assertEqual(filled, 1, "the second row must still be filled")

    def test_a_lookup_that_returns_no_date_invents_nothing(self):
        def meta(vid):
            return {"publishedAt": None, "durationSeconds": 100}, None

        led, _errors, filled = mf.fill_missing_dates(
            self.ledger(), limit=5, fetch_meta=meta)
        self.assertEqual(filled, 0)
        self.assertIsNone(led["candidates"][0]["publishedAt"])

    def test_a_filled_row_records_where_the_date_came_from(self):
        def meta(vid):
            return {"publishedAt": "2026-07-15T00:00:00+00:00",
                    "durationSeconds": 9000,
                    "liveBroadcastStatus": "completed"}, None

        led, _errors, _filled = mf.fill_missing_dates(
            self.ledger(), limit=1, fetch_meta=meta)
        row = {c["videoId"]: c for c in led["candidates"]}["NODATE00001"]
        self.assertEqual(row["publishedAt"], "2026-07-15T00:00:00+00:00")
        self.assertIn("video-metadata", row["sources"])

    def test_a_release_timestamp_is_read_from_the_streams_tab(self):
        """A finished livestream carries release_timestamp far more often
        than timestamp, and for a broadcast that IS the air date."""
        class Runner:
            @staticmethod
            def run(cmd, **kw):
                class R:
                    returncode = 0
                    stdout = json.dumps({"entries": [
                        {"id": "REL00000001", "title": "OWCS 2026 Day 1",
                         "duration": 14400, "live_status": "was_live",
                         "release_timestamp": 1784480400}]})
                    stderr = ""
                return R()

        entries, err = mf.fetch_streams_tab("https://y/c", runner=Runner)
        self.assertIsNone(err)
        self.assertTrue(entries[0]["publishedAt"],
                        "release_timestamp must produce a publish date")


# ------------------------------------------- the writer matches the gate
class TestFindMatchesWritesWhatTheGateChecks(unittest.TestCase):
    """The regression that silently stopped the site updating itself.

    `find-matches` writes assets/data/discovered.v1.js and the validate step
    immediately after it recomputes that file and byte-compares. Those two
    must be the SAME build, or every scheduled scan finds real broadcasts and
    then fails the gate before it can commit them — which is exactly what
    happened for ten days: the scan worked perfectly, and the result was
    thrown away four lines later.
    """

    def test_the_provenance_records_every_input_it_loaded(self):
        payload = sf.build()
        names = [i["name"] for i in payload["inputs"]]
        self.assertEqual(names, ["scan", "published dataset",
                                 "official calendar"],
                         "a from-disk build must record all three inputs")

    def test_passing_the_snapshot_in_drops_the_scan_record(self):
        """Documents WHY the caller must not pass it: provenance is honest
        about what it actually loaded, so a handed-in snapshot is correctly
        not recorded as a load. That is a feature — it just means the writer
        has to build from disk, like the gate does."""
        snap = sf.load_json(sf.path_for(sf.SNAPSHOT_REL))
        handed_in = [i["name"] for i in sf.build(snapshot=snap)["inputs"]]
        self.assertNotIn("scan", handed_in)
        self.assertNotEqual(sf.render_js(sf.build(snapshot=snap)),
                            sf.render_js(sf.build()),
                            "if these ever agree this guard is moot")

    def test_find_matches_builds_the_artifact_from_disk(self):
        """The actual guard, asserted against the real cli.py: the self-fill
        call inside `find-matches` must take no snapshot argument."""
        import ast
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "automation", "cli.py")
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef)
                   and n.name == "cmd_find_matches"), None)
        self.assertIsNotNone(fn, "cmd_find_matches disappeared from cli.py")
        calls = [n for n in ast.walk(fn)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "build"]
        self.assertTrue(calls, "find-matches no longer builds the site's "
                               "discovery layer at all")
        for call in calls:
            passed = {kw.arg for kw in call.keywords}
            self.assertNotIn(
                "snapshot", passed,
                "find-matches must call self_fill.build() with NO snapshot "
                "argument, so the artifact it writes is byte-identical to "
                "the one the validate step recomputes from disk")


if __name__ == "__main__":
    unittest.main(verbosity=2)
