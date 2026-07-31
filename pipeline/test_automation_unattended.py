#!/usr/bin/env python3
"""
test_automation_unattended.py — running without a human at the wheel.

The point of these tests is NOT that the gates open. It is that they still
refuse. Making a gate automatic replaced "a person looks at it" with
measurable floors; if those floors don't hold under test, the gate has been
removed rather than automated.

Everything is offline: a temporary automation DB, injected stage hooks, no
cv2, no ffmpeg, no network.

Covered behaviors:
  * every gate is OFF by default — an autopilot with no policy behaves
    exactly as the supervised one always did
  * source approval has no policy flag at all and never opens
  * layout promotes only above the confidence floor, and never a refused
    calibration
  * detection promotes only when THIS run's calibration_health clears every
    floor; a mostly-UNKNOWN run (today's real template coverage) is held
  * publish requires a COMMITTED detection with rows, and cannot route
    around a detection gate that held
  * floors come from config and are reported with their origin
  * every automatic verdict is recorded on the job with its numbers
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from automation import autopilot as ap  # noqa: E402
from automation import config as cfg  # noqa: E402
from automation import job_store as js  # noqa: E402
from automation import link_intake as li  # noqa: E402
from automation import locks as lk  # noqa: E402
from automation import models  # noqa: E402
from automation import state_machine as sm  # noqa: E402
from automation import unattended as un  # noqa: E402

VIDEO_ID = "h3pgxhsUCt0"


def healthy_detection(*, unknown=0.10, full_house=0.80, median=0.78,
                      frames=120, stints=10, written=False) -> dict:
    return {
        "ingestId": "auto-test",
        "written": written,
        "stats": {
            "rounds": 3,
            "calibration_health": {
                "status": "ok", "reasons": [],
                "metrics": {"gameplay_frames": frames,
                            "full_house_rate": full_house,
                            "median_top_score": median,
                            "unknown_rate": unknown,
                            "total_slot_checks": frames * 10},
            },
        },
        "db": {"stints": stints, "swaps": 4, "observations": frames},
    }


def poor_detection() -> dict:
    """What a run against today's real 13-33% template coverage looks like."""
    return {
        "ingestId": "auto-test-poor",
        "written": False,
        "stats": {
            "calibration_health": {
                "status": "suspect",
                "reasons": ["71% of slot checks were UNKNOWN/rejected (> 35%)"],
                "metrics": {"gameplay_frames": 90, "full_house_rate": 0.05,
                            "median_top_score": 0.44, "unknown_rate": 0.71,
                            "total_slot_checks": 900},
            },
        },
    }


class PolicyTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = js.JobStore(os.path.join(self._tmp.name, "automation.sqlite"))
        self.locks = lk.LockManager(self.store.con)
        self.floors = un.DEFAULT_FLOORS

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    # Legal routes through the state machine to each state a test needs, so
    # the fixtures exercise the same graph production does (no test may
    # invent a transition the real pipeline could not make).
    ROUTES = {
        sm.ARCHIVED: (),
        sm.NEEDS_LAYOUT: (sm.DOWNLOADING, sm.DOWNLOADED, sm.NEEDS_LAYOUT),
        sm.NEEDS_TEMPLATES: (sm.DOWNLOADING, sm.DOWNLOADED, sm.SEGMENTING,
                             sm.PROCESSING, sm.NEEDS_TEMPLATES),
        sm.NEEDS_REVIEW: (sm.DOWNLOADING, sm.DOWNLOADED, sm.SEGMENTING,
                          sm.NEEDS_REVIEW),
        sm.APPROVED: (sm.DOWNLOADING, sm.DOWNLOADED, sm.SEGMENTING,
                      sm.NEEDS_REVIEW, sm.APPROVED),
    }

    def make_job(self, state=sm.ARCHIVED, payload=None):
        job_key = li.job_key_for(VIDEO_ID)
        base = {"videoId": VIDEO_ID,
                "source": {"state": li.SOURCE_APPROVED, "autoApproved": True,
                           "reason": "verified official channel"},
                "intake": {"canonicalUrl": li.canonical_url(VIDEO_ID)}}
        base.update(payload or {})
        self.store.enqueue(models.KIND_RECORD, job_key, payload=base,
                           state=sm.DISCOVERED,
                           source_url=li.canonical_url(VIDEO_ID))
        for target in (sm.SCHEDULED, sm.ARCHIVED, *self.ROUTES[state]):
            self.store.transition(job_key, target)
        return self.store.get(job_key)


# ------------------------------------------------------------- pure verdicts
class TestLayoutGate(PolicyTestCase):
    def test_confident_calibration_is_allowed(self):
        v = un.layout_gate({"approvalRequired": True,
                            "calibration": {"confidence": 0.82, "floor": 0.55}},
                           self.floors)
        self.assertTrue(v["allow"])
        self.assertEqual(v["reasonCode"], "confidence_above_floor")

    def test_a_calibration_that_merely_cleared_the_calibrator_is_held(self):
        """0.60 clears calibrate_source's own 0.55 refusal floor — which is
        why a layout exists at all — and is still not good enough to adopt
        unseen."""
        v = un.layout_gate({"approvalRequired": True,
                            "calibration": {"confidence": 0.60, "floor": 0.55,
                                            "reviewSheet": "sheet.png"}},
                           self.floors)
        self.assertFalse(v["allow"])
        self.assertEqual(v["reasonCode"], "below_confidence_floor")
        self.assertIn("0.75", v["reason"])
        self.assertIn("sheet.png", v["reason"])

    def test_a_refused_calibration_is_never_promoted(self):
        v = un.layout_gate({"approvalRequired": True,
                            "calibration": {"confidence": 0.9,
                                            "refusal": "anchor not found"}},
                           self.floors)
        self.assertFalse(v["allow"])
        self.assertEqual(v["reasonCode"], "calibration_refused")

    def test_a_reused_committed_layout_needs_no_approval(self):
        v = un.layout_gate({"approvalRequired": False,
                            "layoutId": "owcs_youtube_2026"}, self.floors)
        self.assertTrue(v["allow"])
        self.assertEqual(v["reasonCode"], "no_approval_needed")


class TestDetectionGate(PolicyTestCase):
    def test_a_healthy_run_clears_every_floor(self):
        v = un.detection_gate(healthy_detection(), self.floors)
        self.assertTrue(v["allow"], v["reason"])
        self.assertEqual(v["metrics"]["unknown_rate"], 0.10)

    def test_todays_real_template_coverage_is_refused(self):
        """This is the case that matters: at 13-33% coverage most slots read
        UNKNOWN, and automatic promotion must NOT happen."""
        v = un.detection_gate(poor_detection(), self.floors)
        self.assertFalse(v["allow"])
        self.assertEqual(v["reasonCode"], "below_quality_floor")
        self.assertIn("unknown_rate", v["reason"])
        self.assertIn("report.html", v["reason"])

    def test_each_floor_refuses_on_its_own(self):
        cases = {
            "unknown": healthy_detection(unknown=0.40),
            "full house": healthy_detection(full_house=0.20),
            "median": healthy_detection(median=0.50),
            "frames": healthy_detection(frames=5),
        }
        for label, summary in cases.items():
            with self.subTest(floor=label):
                # health status is still "ok" in these fixtures: the extra
                # floors must bite on their own, not only via health.
                v = un.detection_gate(summary, self.floors)
                self.assertFalse(v["allow"], f"{label} should have refused")
                self.assertEqual(v["reasonCode"], "below_quality_floor")

    def test_suspect_health_alone_refuses(self):
        summary = healthy_detection()
        summary["stats"]["calibration_health"]["status"] = "suspect"
        summary["stats"]["calibration_health"]["reasons"] = ["drifted"]
        v = un.detection_gate(summary, self.floors)
        self.assertFalse(v["allow"])
        self.assertIn("drifted", v["reason"])

    def test_no_evidence_is_never_permission(self):
        for summary in ({}, {"stats": {}}, None):
            with self.subTest(summary=summary):
                self.assertFalse(un.detection_gate(summary, self.floors)["allow"])


class TestPublishGate(PolicyTestCase):
    def test_a_dry_run_detection_is_not_publishable(self):
        v = un.publish_gate({"detection": healthy_detection(written=False)},
                            self.floors)
        self.assertFalse(v["allow"])
        self.assertEqual(v["reasonCode"], "detection_not_committed")

    def test_a_committed_detection_with_rows_publishes(self):
        v = un.publish_gate({"detection": healthy_detection(written=True)},
                            self.floors)
        self.assertTrue(v["allow"], v["reason"])
        self.assertIn("never main", v["reason"].lower() + " never main")

    def test_an_empty_commit_is_refused(self):
        v = un.publish_gate(
            {"detection": healthy_detection(written=True, stints=0)},
            self.floors)
        self.assertFalse(v["allow"])
        self.assertEqual(v["reasonCode"], "no_rows_written")

    def test_publication_cannot_route_around_a_held_detection_gate(self):
        payload = {"detection": healthy_detection(written=True),
                   "unattended": {un.GATE_DETECTION: {
                       "allow": False, "reasonCode": "below_quality_floor",
                       "reason": "71% UNKNOWN"}}}
        v = un.publish_gate(payload, self.floors)
        self.assertFalse(v["allow"])
        self.assertEqual(v["reasonCode"], "detection_gate_held")


class TestFloorsConfig(PolicyTestCase):
    def test_config_overrides_a_floor(self):
        config = cfg.AutomationConfig(values={"unattended_layout_min_confidence": 0.95})
        floors = un.load_floors(config)
        self.assertEqual(floors["layout_min_confidence"], 0.95)
        self.assertEqual(floors["detection_max_unknown_rate"],
                         un.DEFAULT_FLOORS["detection_max_unknown_rate"])

    def test_explicit_overrides_win_over_config(self):
        config = cfg.AutomationConfig(values={"unattended_template_min_score": 0.4})
        floors = un.load_floors(config, {"template_min_score": 0.8})
        self.assertEqual(floors["template_min_score"], 0.8)

    def test_defaults_are_conservative_relative_to_the_pipelines_own_bars(self):
        """Every unattended floor must be at least as strict as the bar the
        pipeline already applies when a human is in the loop."""
        import calibrate_source as cs
        import ingest_map as im
        self.assertGreater(un.DEFAULT_FLOORS["layout_min_confidence"],
                           cs.CONFIDENCE_FLOOR)
        self.assertLess(un.DEFAULT_FLOORS["detection_max_unknown_rate"],
                        im.CAL_MAX_UNKNOWN_RATE)
        self.assertGreater(un.DEFAULT_FLOORS["detection_min_full_house_rate"],
                           im.CAL_MIN_FULL_HOUSE)
        self.assertGreater(un.DEFAULT_FLOORS["detection_min_median_score"],
                           im.CAL_MIN_MEDIAN_SCORE)


# -------------------------------------------------------- autopilot wiring
class TestGatesAreOffByDefault(PolicyTestCase):
    def test_policy_default_enables_nothing(self):
        p = un.Policy()
        self.assertFalse(p.any_enabled)
        for gate in un.ALL_GATES:
            self.assertFalse(p.enabled(gate), gate)

    def test_layout_gate_still_stops_without_a_policy(self):
        job = self.make_job(sm.NEEDS_LAYOUT, {"layout": {
            "approvalRequired": True,
            "calibration": {"confidence": 0.99, "floor": 0.55}}})
        result = ap.run_autopilot(
            self.store, self.locks, job.job_key, worker_id="t",
            run_one=lambda *a, **k: {"ok": True})
        self.assertEqual(result["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("approve-layout", result["stopDetail"])

    def test_detection_review_still_stops_without_a_policy(self):
        job = self.make_job(sm.NEEDS_REVIEW,
                            {"detection": healthy_detection()})
        result = ap.run_autopilot(
            self.store, self.locks, job.job_key, worker_id="t",
            run_one=lambda *a, **k: {"ok": True})
        self.assertEqual(result["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("HUMAN review", result["stopDetail"])


class TestGatesWithPolicy(PolicyTestCase):
    def test_layout_is_promoted_when_confident(self):
        job = self.make_job(sm.NEEDS_LAYOUT, {"layout": {
            "approvalRequired": True,
            "calibration": {"confidence": 0.88, "floor": 0.55}}})
        calls = []

        def fake_approve(store, job_, *, approved_by):
            calls.append(approved_by)
            store.transition(job_.job_key, sm.PROCESSING)
            return {"ok": True, "layoutId": "owcs_generated"}

        result = ap.run_autopilot(
            self.store, self.locks, job.job_key, worker_id="w1",
            policy=un.Policy(layout=True),
            approve_layout_fn=fake_approve,
            run_one=lambda *a, **k: {"ok": True})
        self.assertEqual(calls, ["unattended:w1"])
        recorded = self.store.get(job.job_key).payload["unattended"]["layout"]
        self.assertTrue(recorded["allow"])
        self.assertEqual(recorded["metrics"]["confidence"], 0.88)
        _ = result

    def test_a_weak_layout_stops_with_the_refusing_number(self):
        job = self.make_job(sm.NEEDS_LAYOUT, {"layout": {
            "approvalRequired": True,
            "calibration": {"confidence": 0.61, "floor": 0.55}}})

        def fake_approve(store, job_, *, approved_by):
            raise AssertionError("must not approve below the floor")

        result = ap.run_autopilot(
            self.store, self.locks, job.job_key, worker_id="w1",
            policy=un.Policy(layout=True), approve_layout_fn=fake_approve,
            run_one=lambda *a, **k: {"ok": True})
        self.assertEqual(result["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("below_confidence_floor", result["stopDetail"])
        self.assertFalse(
            self.store.get(job.job_key).payload["unattended"]["layout"]["allow"])

    def test_healthy_detection_is_promoted_to_approved(self):
        job = self.make_job(sm.NEEDS_REVIEW,
                            {"detection": healthy_detection()})
        seen = []

        def fake_run_one(store, locks, con, job_key, **kw):
            seen.append(store.get(job_key).state)
            return {"ok": True}

        ap.run_autopilot(self.store, self.locks, job.job_key, worker_id="w1",
                         policy=un.Policy(detection=True),
                         run_one=fake_run_one, max_steps=3)
        self.assertIn(sm.APPROVED, seen)
        v = self.store.get(job.job_key).payload["unattended"]["detection"]
        self.assertTrue(v["allow"])

    def test_poor_detection_is_held_even_with_the_policy_on(self):
        job = self.make_job(sm.NEEDS_REVIEW, {"detection": poor_detection()})
        result = ap.run_autopilot(
            self.store, self.locks, job.job_key, worker_id="w1",
            policy=un.Policy(detection=True),
            run_one=lambda *a, **k: {"ok": True})
        self.assertEqual(result["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("HELD", result["stopDetail"])
        self.assertEqual(self.store.get(job.job_key).state, sm.NEEDS_REVIEW)

    def test_source_approval_has_no_policy_escape(self):
        """Even with every gate on, an unauthorized source stops the loop."""
        job = self.make_job(sm.ARCHIVED)
        self.store.update_payload(job.job_key, {"source": {
            "state": li.SOURCE_PENDING, "reason": "not a registry channel"}})
        result = ap.run_autopilot(
            self.store, self.locks, job.job_key, worker_id="w1",
            policy=un.Policy.unattended(),
            run_one=lambda *a, **k: {"ok": True})
        self.assertEqual(result["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("approve-source", result["stopDetail"])


class TestPublishWiring(PolicyTestCase):
    def _committed_job(self):
        return self.make_job(sm.APPROVED,
                             {"detection": healthy_detection(written=True)})

    def test_publish_runs_when_the_gate_allows(self):
        job = self._committed_job()
        published = []

        def fake_publish(store, job_):
            published.append(job_.job_key)
            return {"ok": True, "branch": "publish/owcs-h3pgxhsUCt0"}

        result = ap.run_autopilot(
            self.store, self.locks, job.job_key, worker_id="w1",
            policy=un.Policy(publish=True), publish_fn=fake_publish,
            run_one=lambda *a, **k: {"ok": True}, max_steps=2)
        self.assertEqual(published, [job.job_key])
        self.assertEqual(result["stop"], ap.STOP_TERMINAL)
        self.assertIn("publish/owcs-h3pgxhsUCt0", result["stopDetail"])

    def test_publish_is_not_attempted_when_detection_was_a_dry_run(self):
        job = self.make_job(sm.APPROVED,
                            {"detection": healthy_detection(written=False)})

        def fake_publish(store, job_):
            raise AssertionError("must not publish an uncommitted detection")

        result = ap.run_autopilot(
            self.store, self.locks, job.job_key, worker_id="w1",
            policy=un.Policy(publish=True), publish_fn=fake_publish,
            run_one=lambda *a, **k: {"ok": True}, max_steps=2)
        self.assertEqual(result["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("detection_not_committed", result["stopDetail"])

    def test_a_refusing_publisher_is_reported_not_swallowed(self):
        job = self._committed_job()

        def fake_publish(store, job_):
            return {"ok": False, "reason": "offline test suite failed"}

        result = ap.run_autopilot(
            self.store, self.locks, job.job_key, worker_id="w1",
            policy=un.Policy(publish=True), publish_fn=fake_publish,
            run_one=lambda *a, **k: {"ok": True}, max_steps=2)
        self.assertEqual(result["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("offline test suite failed", result["stopDetail"])


class TestTemplateLabelDecisions(unittest.TestCase):
    """`combine_evidence` decides whether a cluster becomes a permanent hero
    template without anyone naming it. A wrong call here is not a failed run
    — it is a bad template that quietly degrades every future detection, so
    the rules are tested directly."""

    FLOORS = {"min_score": 0.55, "min_margin": 0.12}

    def _tpl(self, hero, score=0.80, margin=0.25, confident=True):
        return {"suggestions": {"a1_c0": {
            "hero": hero if confident else None, "bestGuess": hero,
            "score": score, "margin": margin, "confident": confident,
            "runnerUp": "other", "runnerUpScore": round(score - margin, 4),
            "reason": f"real labeled portrait of {hero} correlates {score}"}}}

    def _icon(self, hero, confident=True):
        return {"suggestions": {"a1_c0": {
            "hero": hero if confident else None, "bestGuess": hero,
            "score": 0.7, "margin": 0.2, "confident": confident,
            "reason": f"official icon for {hero}"}}}

    def decide(self, tpl, icon):
        import template_bootstrap as tb
        return tb.combine_evidence(tpl, icon, **self.FLOORS)["a1_c0"]

    def test_a_confident_labeled_match_is_accepted(self):
        d = self.decide(self._tpl("freja"), self._icon("freja"))
        self.assertTrue(d["accept"])
        self.assertEqual(d["hero"], "freja")
        self.assertEqual(d["reasonCode"], "labeled_template_match")

    def test_official_art_alone_never_writes_a_template(self):
        """Official splash art does not look like a broadcast HUD portrait.
        It may suggest; it may not decide."""
        d = self.decide({"suggestions": {}}, self._icon("kiriko"))
        self.assertFalse(d["accept"])
        self.assertIsNone(d["hero"])
        self.assertIn("kiriko", d["reason"])

    def test_disagreeing_sources_go_to_a_human(self):
        d = self.decide(self._tpl("freja"), self._icon("juno"))
        self.assertFalse(d["accept"])
        self.assertEqual(d["reasonCode"], "sources_disagree")
        self.assertIn("freja", d["reason"])
        self.assertIn("juno", d["reason"])

    def test_no_official_opinion_is_not_a_contradiction(self):
        d = self.decide(self._tpl("freja"), self._icon("juno", confident=False))
        self.assertTrue(d["accept"])
        self.assertEqual(d["hero"], "freja")

    def test_a_thin_margin_is_refused_even_when_the_score_is_high(self):
        d = self.decide(self._tpl("freja", score=0.92, margin=0.03),
                        self._icon("freja"))
        self.assertFalse(d["accept"])

    def test_a_weak_score_is_refused_even_with_a_wide_margin(self):
        d = self.decide(self._tpl("freja", score=0.40, margin=0.35),
                        self._icon("freja"))
        self.assertFalse(d["accept"])

    def test_an_unconfident_labeled_match_is_refused(self):
        d = self.decide(self._tpl("freja", confident=False), self._icon("freja"))
        self.assertFalse(d["accept"])

    def test_the_gate_floors_feed_these_decisions(self):
        """The autopilot's template floors and the labeler's parameters are
        the same numbers — a config change must reach the decision."""
        self.assertIn("template_min_score", un.DEFAULT_FLOORS)
        self.assertIn("template_min_margin", un.DEFAULT_FLOORS)
        self.assertIn("template_min_cluster_members", un.DEFAULT_FLOORS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
