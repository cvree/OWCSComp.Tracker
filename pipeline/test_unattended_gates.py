#!/usr/bin/env python3
"""
test_unattended_gates.py — the five unattended approval gates.

The point of this suite is NOT to prove the gates can approve things. It is
to prove they REFUSE, because a gate that can be talked into a "yes" by
missing data is worse than no gate at all — it launders absent evidence into
an approval that looks identical to a real one.

So the bulk of these tests feed each gate the ways real metrics go wrong:
absent, null, non-numeric, present-but-short, contradicted by a second
source, or belonging to a run that a human already refused. Each one must
come back `passed=False` with a stable `reason_code`. The handful of
positive tests exist only to prove the gates are not simply stuck closed.

Everything here is offline and dependency-free: `gates` imports nothing
heavier than the standard library, which is exactly what lets this file run
in a container with no OpenCV, no ffmpeg and no network.

Run: python3 pipeline/test_unattended_gates.py
"""
from __future__ import annotations
import argparse
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from automation import config as cfgmod  # noqa: E402
from automation import gates as gt  # noqa: E402
from automation import job_store as js  # noqa: E402
from automation import link_intake as li  # noqa: E402
from automation import ops  # noqa: E402


def healthy_detection(unknown=0.10, full=0.90, median=0.80,
                      frames=120) -> dict:
    """A detection payload whose health clears every floor comfortably.

    Each keyword lets one metric be pushed below its floor in isolation, so
    a failing test names exactly which threshold did the refusing.
    """
    return {
        "ingestId": "beta-test-seg1",
        "stats": {"calibration_health": {
            "status": "ok",
            "metrics": {"unknown_rate": unknown, "full_house_rate": full,
                        "median_top_score": median, "gameplay_frames": frames},
        }},
        "written": False,
    }


# ============================================================ floor resolution
class TestFloors(unittest.TestCase):
    def test_defaults_are_reported_as_defaults(self):
        rows = gt.resolve_floors(None)
        self.assertEqual(rows["auto_detect_max_unknown_rate"]["value"], 0.25)
        self.assertEqual(rows["auto_detect_max_unknown_rate"]["source"],
                         "default")

    def test_a_config_override_is_reported_as_config(self):
        cfg = cfgmod.AutomationConfig(
            values={"auto_layout_min_confidence": 0.9},
            explicit={"auto_layout_min_confidence"})
        rows = gt.resolve_floors(cfg)
        self.assertEqual(rows["auto_layout_min_confidence"]["value"], 0.9)
        self.assertEqual(rows["auto_layout_min_confidence"]["source"], "config")
        # A key the file did NOT set stays a default even though the merged
        # `values` view would contain it.
        self.assertEqual(rows["auto_detect_min_full_house"]["source"], "default")

    def test_a_merged_config_does_not_make_every_floor_look_tuned(self):
        """load_config merges DEFAULTS into `values`; membership there must
        not be mistaken for the operator having chosen the number."""
        cfg = cfgmod.load_config()
        rows = gt.resolve_floors(cfg)
        untouched = [k for k, v in rows.items() if v["source"] == "config"]
        self.assertEqual(untouched, [],
                         "no gate floor is set in the shipped automation.yml, "
                         "so none should report as config-sourced")

    def test_a_non_numeric_floor_is_a_loud_error(self):
        cfg = cfgmod.AutomationConfig(values={"auto_layout_min_confidence": "high"},
                                      explicit={"auto_layout_min_confidence"})
        with self.assertRaises(ValueError):
            gt.resolve_floors(cfg)

    def test_every_floor_belongs_to_a_real_gate(self):
        for key, info in gt.resolve_floors(None).items():
            self.assertIn(info["gate"], gt.ALL_GATES, key)

    def test_format_floors_names_every_gate_and_its_flag(self):
        text = gt.format_floors(None, gt.GateSettings())
        for gate in gt.ALL_GATES:
            self.assertIn(gate.upper(), text)
            self.assertIn(gt.GATE_FLAGS[gate], text)


# ============================================================== gate settings
class TestGateSettings(unittest.TestCase):
    def test_nothing_is_automatic_by_default(self):
        s = gt.GateSettings()
        self.assertFalse(s.any_enabled())
        for gate in gt.ALL_GATES:
            self.assertFalse(s.enabled(gate), gate)

    def test_unattended_turns_on_all_five(self):
        s = gt.GateSettings.from_args(argparse.Namespace(unattended=True))
        self.assertEqual(s.enabled_gates(), list(gt.ALL_GATES))

    def test_one_flag_enables_only_that_gate(self):
        s = gt.GateSettings.from_args(argparse.Namespace(auto_detect=True))
        self.assertEqual(s.enabled_gates(), [gt.GATE_DETECTION])

    def test_an_absent_namespace_attribute_is_off_not_an_error(self):
        s = gt.GateSettings.from_args(argparse.Namespace())
        self.assertFalse(s.any_enabled())


# ================================================================ source gate
class TestSourceGate(unittest.TestCase):
    OK_META = {"status": "ok", "liveBroadcastStatus": "completed",
               "durationSeconds": 7200, "channelId": "UCx",
               "channelTitle": "Some Caster"}
    OK_LIKENESS = {"score": 60, "confidence": "likely", "reasons": []}

    def test_an_already_approved_source_passes_trivially(self):
        v = gt.evaluate_source_gate({"state": "approved",
                                     "reasonCode": "registry_channel"})
        self.assertTrue(v.passed)
        self.assertEqual(v.reason_code, "already_approved")

    def test_a_human_rejection_is_never_re_opened(self):
        v = gt.evaluate_source_gate(
            {"state": "rejected", "decidedBy": "Connor"},
            metadata=self.OK_META, likeness=self.OK_LIKENESS)
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "human_rejection_stands")

    def test_missing_metadata_refuses(self):
        v = gt.evaluate_source_gate({"state": "pending-approval"},
                                    metadata={"status": "unavailable",
                                              "errorCode": "no_client"},
                                    likeness=self.OK_LIKENESS)
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "metadata_unavailable")

    def test_no_metadata_at_all_refuses(self):
        v = gt.evaluate_source_gate({"state": "pending-approval"})
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "metadata_unavailable")

    def test_a_live_stream_is_not_a_completed_vod(self):
        meta = dict(self.OK_META, liveBroadcastStatus="live")
        v = gt.evaluate_source_gate({"state": "pending-approval"},
                                    metadata=meta, likeness=self.OK_LIKENESS)
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "not_a_completed_vod")

    def test_a_short_video_refuses(self):
        meta = dict(self.OK_META, durationSeconds=300)
        v = gt.evaluate_source_gate({"state": "pending-approval"},
                                    metadata=meta, likeness=self.OK_LIKENESS)
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "duration_too_short")

    def test_an_unknown_duration_refuses_rather_than_assuming_zero(self):
        meta = dict(self.OK_META, durationSeconds=None)
        v = gt.evaluate_source_gate({"state": "pending-approval"},
                                    metadata=meta, likeness=self.OK_LIKENESS)
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "duration_too_short")

    def test_an_unlikely_broadcast_refuses_even_with_a_passing_number(self):
        lk = {"score": 99, "confidence": "unlikely", "reasons": ["guide"]}
        v = gt.evaluate_source_gate({"state": "pending-approval"},
                                    metadata=self.OK_META, likeness=lk)
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "likeness_below_floor")

    def test_likeness_below_the_floor_refuses(self):
        lk = {"score": 20, "confidence": "likely", "reasons": []}
        v = gt.evaluate_source_gate({"state": "pending-approval"},
                                    metadata=self.OK_META, likeness=lk)
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "likeness_below_floor")

    def test_a_missing_likeness_refuses(self):
        v = gt.evaluate_source_gate({"state": "pending-approval"},
                                    metadata=self.OK_META, likeness=None)
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "likeness_unavailable")

    def test_strong_provenance_and_likeness_passes(self):
        v = gt.evaluate_source_gate({"state": "pending-approval"},
                                    metadata=self.OK_META,
                                    likeness=self.OK_LIKENESS)
        self.assertTrue(v.passed)
        self.assertEqual(v.reason_code, "likeness_and_provenance")
        self.assertEqual(v.metrics["likenessScore"], 60)


# ================================================================ layout gate
class TestLayoutGate(unittest.TestCase):
    def test_no_confidence_refuses(self):
        v = gt.evaluate_layout_gate({"layoutId": "x"})
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "no_confidence_recorded")

    def test_below_the_hard_calibration_floor_is_an_outright_refusal(self):
        v = gt.evaluate_layout_gate({"confidence": 0.40})
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "below_calibration_floor")

    def test_between_the_floors_holds_for_a_human(self):
        """0.55 <= conf < 0.75 is exactly the band where calibration stands
        behind the layout but nobody has looked at the sheet."""
        v = gt.evaluate_layout_gate({"confidence": 0.60})
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "below_unattended_floor")

    def test_the_boundary_value_passes(self):
        v = gt.evaluate_layout_gate({"confidence": 0.75})
        self.assertTrue(v.passed)

    def test_a_high_confidence_layout_passes(self):
        v = gt.evaluate_layout_gate({"confidence": 0.91, "layoutId": "owcs_x"})
        self.assertTrue(v.passed)
        self.assertEqual(v.metrics["layoutId"], "owcs_x")

    def test_a_raised_floor_from_config_is_honoured(self):
        floors = dict(gt.floor_values())
        floors["auto_layout_min_confidence"] = 0.95
        v = gt.evaluate_layout_gate({"confidence": 0.80}, floors=floors)
        self.assertFalse(v.passed)


# ============================================================= templates gate
class TestTemplatesGate(unittest.TestCase):
    GOOD = {"clusterId": "a1_c0", "hero": "tracer", "score": 0.82,
            "runnerUpScore": 0.40, "frames": 12,
            "referenceKind": "labeled-portrait"}

    def test_a_clean_portrait_match_passes(self):
        v = gt.evaluate_templates_gate(self.GOOD)
        self.assertTrue(v.passed)
        self.assertEqual(v.reason_code, "portrait_match_clear")

    def test_no_hero_candidate_refuses(self):
        v = gt.evaluate_templates_gate(dict(self.GOOD, hero=None))
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "no_candidate_hero")

    def test_scoring_against_official_art_is_refused_outright(self):
        """The whole design rests on scoring against real labelled portraits;
        a candidate scored against a splash render must never clear."""
        v = gt.evaluate_templates_gate(
            dict(self.GOOD, referenceKind="official-art"))
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "wrong_reference_kind")

    def test_too_few_frames_refuses(self):
        v = gt.evaluate_templates_gate(dict(self.GOOD, frames=3))
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "too_few_frames")

    def test_an_unknown_frame_count_refuses(self):
        v = gt.evaluate_templates_gate(dict(self.GOOD, frames=None))
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "too_few_frames")

    def test_a_weak_best_score_refuses(self):
        v = gt.evaluate_templates_gate(dict(self.GOOD, score=0.42))
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "score_below_floor")

    def test_a_thin_margin_refuses(self):
        v = gt.evaluate_templates_gate(
            dict(self.GOOD, score=0.60, runnerUpScore=0.55))
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "margin_below_floor")

    def test_official_art_naming_a_different_hero_is_a_hold(self):
        v = gt.evaluate_templates_gate(
            dict(self.GOOD, officialOpinion={"hero": "sombra"}))
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "official_art_contradicts")

    def test_official_art_agreeing_is_recorded_but_not_required(self):
        agree = gt.evaluate_templates_gate(
            dict(self.GOOD, officialOpinion={"hero": "Tracer"}))
        self.assertTrue(agree.passed, "case must not matter for agreement")
        silent = gt.evaluate_templates_gate(
            dict(self.GOOD, officialOpinion={}))
        self.assertTrue(silent.passed, "no official opinion is not a veto")

    def test_no_runner_up_means_a_full_margin(self):
        v = gt.evaluate_templates_gate(
            dict(self.GOOD, runnerUpScore=None))
        self.assertTrue(v.passed)
        self.assertEqual(v.metrics["margin"], self.GOOD["score"])


# ============================================================= detection gate
class TestDetectionGate(unittest.TestCase):
    def test_healthy_metrics_pass(self):
        v = gt.evaluate_detection_gate(healthy_detection())
        self.assertTrue(v.passed)
        self.assertEqual(v.reason_code, "health_above_floor")

    def test_no_detection_refuses(self):
        v = gt.evaluate_detection_gate(None)
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "no_detection_recorded")

    def test_a_run_with_no_health_block_refuses(self):
        v = gt.evaluate_detection_gate({"ingestId": "x", "stats": {}})
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "no_calibration_health")

    def test_a_high_unknown_rate_refuses(self):
        v = gt.evaluate_detection_gate(healthy_detection(unknown=0.30))
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "health_below_floor")
        self.assertIn("unknown rate", v.reason)

    def test_a_low_full_house_rate_refuses(self):
        v = gt.evaluate_detection_gate(healthy_detection(full=0.45))
        self.assertFalse(v.passed)
        self.assertIn("full-house", v.reason)

    def test_a_low_median_score_refuses(self):
        v = gt.evaluate_detection_gate(healthy_detection(median=0.61))
        self.assertFalse(v.passed)
        self.assertIn("median score", v.reason)

    def test_too_few_gameplay_frames_refuses_however_good_the_ratios(self):
        """Six perfect frames is not evidence — this is the floor that stops
        a tiny sample from riding three flattering ratios into production."""
        v = gt.evaluate_detection_gate(
            healthy_detection(unknown=0.0, full=1.0, median=0.99, frames=6))
        self.assertFalse(v.passed)
        self.assertIn("gameplay", v.reason)

    def test_a_null_metric_is_not_read_as_a_perfect_score(self):
        det = healthy_detection()
        det["stats"]["calibration_health"]["metrics"]["unknown_rate"] = None
        v = gt.evaluate_detection_gate(det)
        self.assertFalse(v.passed)

    def test_a_non_numeric_metric_refuses(self):
        det = healthy_detection()
        det["stats"]["calibration_health"]["metrics"]["median_top_score"] = "high"
        v = gt.evaluate_detection_gate(det)
        self.assertFalse(v.passed)

    def test_the_gate_is_stricter_than_ingest_maps_suspect_thresholds(self):
        """A run ingest_map would call healthy (0.34 unknown / 0.45 full /
        0.61 median all pass its checks) must still be refused here."""
        v = gt.evaluate_detection_gate(
            healthy_detection(unknown=0.34, full=0.45, median=0.61))
        self.assertFalse(v.passed)

    def test_every_failing_metric_is_named_not_just_the_first(self):
        v = gt.evaluate_detection_gate(
            healthy_detection(unknown=0.9, full=0.1, median=0.1, frames=1))
        for token in ("gameplay", "unknown rate", "full-house", "median score"):
            self.assertIn(token, v.reason)


# =============================================================== publish gate
class TestPublishGate(unittest.TestCase):
    def _payload(self, **over):
        base = {
            "detection": {"ingestId": "i1", "written": True,
                          "db": {"stints": 12}},
            "autoApprovals": {"detection": {"passed": True,
                                            "reasonCode": "health_above_floor"}},
        }
        base.update(over)
        return base

    def test_a_committed_detection_with_stints_passes(self):
        v = gt.evaluate_publish_gate(self._payload())
        self.assertTrue(v.passed)
        self.assertEqual(v.reason_code, "committed_with_stints")

    def test_no_detection_refuses(self):
        v = gt.evaluate_publish_gate({})
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "no_detection_recorded")

    def test_an_uncommitted_candidate_pass_refuses(self):
        v = gt.evaluate_publish_gate(self._payload(
            detection={"ingestId": "i1", "written": False,
                       "db": {"stints": 12}}))
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "detection_not_committed")

    def test_zero_stints_refuses(self):
        v = gt.evaluate_publish_gate(self._payload(
            detection={"ingestId": "i1", "written": True, "db": {"stints": 0}}))
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "no_stints")

    def test_a_detection_gate_that_never_ran_refuses(self):
        v = gt.evaluate_publish_gate(self._payload(autoApprovals={}))
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "detection_gate_not_recorded")

    def test_publishing_may_not_inherit_approval_from_a_failed_detection_gate(self):
        """The case that matters: a human rescued the detection review, so
        the detection gate itself refused. An automatic publish must not
        quietly treat the human's approval as its own."""
        v = gt.evaluate_publish_gate(self._payload(
            autoApprovals={"detection": {"passed": False,
                                         "reasonCode": "health_below_floor"}}))
        self.assertFalse(v.passed)
        self.assertEqual(v.reason_code, "detection_gate_failed")


# ==================================================== audit trail / recording
class TestVerdictPayload(unittest.TestCase):
    def test_a_verdict_carries_its_metrics_and_floors(self):
        v = gt.evaluate_detection_gate(healthy_detection(unknown=0.9))
        p = v.to_payload()
        self.assertEqual(p["gate"], "detection")
        self.assertFalse(p["passed"])
        self.assertEqual(p["metrics"]["unknownRate"], 0.9)
        self.assertEqual(p["floors"]["auto_detect_max_unknown_rate"], 0.25)
        self.assertTrue(p["decidedAt"], "a verdict is always timestamped")
        self.assertTrue(p["decidedBy"].startswith("automatic-gate:"))

    def test_an_automatic_decision_never_claims_to_be_a_person(self):
        v = gt.evaluate_layout_gate({"confidence": 0.9})
        self.assertEqual(v.to_payload()["decidedBy"], "automatic-gate:layout")


# ============================================ approve_source's automatic path
class TestAutoApproveSourceRecording(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = js.JobStore(os.path.join(self.tmp.name, "a.sqlite"))
        job = ops.create_job_from_broadcast(
            self.store, match_id="m1", video_id="vid1",
            source_url="https://www.youtube.com/watch?v=vid1",
            channel_id="UCx", team_a="a", team_b="b",
            expected_layout_id="owcs_jksix_qwc")
        self.key = job.job_key
        self.store.update_payload(self.key, {
            "source": {"state": li.SOURCE_PENDING,
                       "reasonCode": "channel_not_in_registry"}})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _passing(self):
        return gt.evaluate_source_gate(
            {"state": "pending-approval"},
            metadata={"status": "ok", "liveBroadcastStatus": "completed",
                      "durationSeconds": 7200, "channelId": "UCx"},
            likeness={"score": 60, "confidence": "likely"}).to_payload()

    def test_an_automatic_approval_is_marked_as_automatic(self):
        li.approve_source(self.store, self.key, approved_by="",
                          confirm=True, auto_gate=self._passing())
        src = self.store.get(self.key).payload["source"]
        self.assertEqual(src["state"], li.SOURCE_APPROVED)
        self.assertTrue(src["autoApproved"])
        self.assertEqual(src["reasonCode"], "unattended_gate")
        self.assertEqual(src["decidedBy"], "automatic-gate:source")
        self.assertTrue(src["gate"]["passed"])
        self.assertIn("likenessScore", src["gate"]["metrics"])

    def test_a_manual_approval_is_still_recorded_as_a_person(self):
        li.approve_source(self.store, self.key, approved_by="Connor",
                          confirm=True)
        src = self.store.get(self.key).payload["source"]
        self.assertFalse(src["autoApproved"])
        self.assertEqual(src["reasonCode"], "manual_approval")
        self.assertEqual(src["decidedBy"], "Connor")

    def test_a_failing_verdict_cannot_record_an_approval(self):
        bad = gt.evaluate_source_gate({"state": "pending-approval"}).to_payload()
        with self.assertRaises(li.LinkIntakeError) as ctx:
            li.approve_source(self.store, self.key, approved_by="",
                              confirm=True, auto_gate=bad)
        self.assertEqual(ctx.exception.code, "gate_did_not_pass")
        self.assertEqual(self.store.get(self.key).payload["source"]["state"],
                         li.SOURCE_PENDING)

    def test_a_gate_may_never_reject_a_source(self):
        with self.assertRaises(li.LinkIntakeError) as ctx:
            li.approve_source(self.store, self.key, approved_by="",
                              confirm=True, reject=True,
                              auto_gate=self._passing())
        self.assertEqual(ctx.exception.code, "auto_rejection_refused")

    def test_confirm_is_still_required_on_the_automatic_path(self):
        with self.assertRaises(li.LinkIntakeError) as ctx:
            li.approve_source(self.store, self.key, approved_by="",
                              auto_gate=self._passing())
        self.assertEqual(ctx.exception.code, "confirmation_required")

    def test_a_manual_approval_with_no_approver_is_still_refused(self):
        """Removing the human name requirement for the gate path must not
        have removed it for the human path."""
        with self.assertRaises(li.LinkIntakeError) as ctx:
            li.approve_source(self.store, self.key, approved_by="  ",
                              confirm=True)
        self.assertEqual(ctx.exception.code, "approver_required")


# ================================================ the additive-only labeller
class TestPortraitReferences(unittest.TestCase):
    """The reference set the templates gate scores against.

    These tests need no cv2: building the index is pure filesystem work, and
    that is deliberate — the part that decides WHAT a cluster is compared
    against should be inspectable without a CV stack.
    """

    def setUp(self):
        from automation import auto_label
        self.auto_label = auto_label
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        # A flat legacy set plus two per-package sets.
        self._png(self.root, "ana.png")
        self._png(os.path.join(self.root, "pkg_a"), "tracer.png")
        self._png(os.path.join(self.root, "pkg_a"), "tracer.dead.png")
        self._png(os.path.join(self.root, "pkg_b"), "mei.png")

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _png(directory, name):
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, name), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")

    def test_both_flat_and_per_package_sets_are_indexed(self):
        idx = self.auto_label.portrait_reference_index(templates_root=self.root)
        self.assertIn("ana", idx)
        self.assertIn("tracer", idx)
        self.assertIn("mei", idx)

    def test_all_variants_of_a_hero_are_kept_as_references(self):
        idx = self.auto_label.portrait_reference_index(templates_root=self.root)
        self.assertEqual(len(idx["tracer"]), 2,
                         "alive and dead portraits are both valid references")

    def test_the_package_being_labelled_is_excluded(self):
        """Scoring a cluster against the very set being extended would be
        circular, and would cheerfully re-confirm an existing mistake."""
        idx = self.auto_label.portrait_reference_index(
            exclude_dir=os.path.join(self.root, "pkg_a"),
            templates_root=self.root)
        self.assertNotIn("tracer", idx)
        self.assertIn("mei", idx)
        self.assertIn("ana", idx)

    def test_a_missing_templates_root_is_empty_not_an_error(self):
        idx = self.auto_label.portrait_reference_index(
            templates_root=os.path.join(self.root, "nope"))
        self.assertEqual(idx, {})


class TestLabellingIsAdditive(unittest.TestCase):
    def test_the_auto_labeller_never_calls_stage_labels(self):
        """`harvest_templates.stage_labels` deletes every *.png in the output
        directory before writing. That is right for a human doing a full
        reviewed pass and catastrophic for an unattended one that labelled
        fewer heroes, so the unattended path must never route through it.

        Asserted against the source because the hazard is the CALL itself —
        by the time a behavioural test could observe it, a real package's
        templates would already be gone.
        """
        from automation import auto_label
        with open(auto_label.__file__, encoding="utf-8") as f:
            source = f.read()
        code = "\n".join(line for line in source.splitlines()
                         if not line.strip().startswith("#"))
        # The docstring explains WHY it is not used; strip it before checking.
        body = code.split('"""', 2)[-1]
        self.assertNotIn("stage_labels", body)

    def test_harvest_templates_stage_labels_really_does_wipe(self):
        """Pins the hazard the design works around. If upstream ever makes
        stage_labels additive, this test fails and the workaround can go."""
        import inspect
        import harvest_templates as ht
        src = inspect.getsource(ht.stage_labels)
        self.assertIn("os.remove", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
