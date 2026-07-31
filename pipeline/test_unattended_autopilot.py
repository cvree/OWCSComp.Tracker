#!/usr/bin/env python3
"""
test_unattended_autopilot.py — the gates as wired into the autopilot loop.

`test_unattended_gates.py` proves the gate FUNCTIONS refuse. This file proves
the loop actually consults them, and — the part that matters most for anyone
who was relying on the old behaviour — that nothing changes when the flags
are off.

Three properties, in priority order:

  1. **Flags off means identical behaviour.** Every human gate that stopped
     the loop before still stops it, with the same stop kind. This is the
     regression surface: the gates are new code on the path every supervised
     run already takes.
  2. **An enabled gate that refuses still stops the loop.** Turning a flag on
     buys an evidence check, not a bypass. The stop must additionally name
     the metric that held it.
  3. **Every verdict lands on the job.** Refusals included — a refusal is the
     record that answers "why did nothing publish last night?".

Run: python3 pipeline/test_unattended_autopilot.py
"""
from __future__ import annotations
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from automation import autopilot as ap  # noqa: E402
from automation import gates as gt  # noqa: E402
from automation import job_store as js  # noqa: E402
from automation import link_intake as li  # noqa: E402
from automation import locks as lk  # noqa: E402
from automation import ops  # noqa: E402
from automation import state_machine as sm  # noqa: E402

APPROVED_SOURCE = {"state": "approved", "autoApproved": True,
                   "reasonCode": "registry_channel",
                   "reason": "test fixture: verified official channel"}

STRONG_METADATA = {"status": "ok", "liveBroadcastStatus": "completed",
                   "durationSeconds": 7200, "channelId": "UCsomecaster",
                   "channelTitle": "Some Caster"}
STRONG_LIKENESS = {"score": 60, "confidence": "likely", "reasons": []}


def detection_payload(unknown=0.10, full=0.90, median=0.80, frames=120,
                      written=False, stints=8) -> dict:
    d = {
        "ingestId": "beta-test-seg1",
        "stats": {"calibration_health": {
            "status": "ok",
            "metrics": {"unknown_rate": unknown, "full_house_rate": full,
                        "median_top_score": median,
                        "gameplay_frames": frames}}},
        "written": written,
    }
    if written:
        d["db"] = {"stints": stints}
    return d


class GateAutopilotBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = js.JobStore(os.path.join(self.tmp.name, "a.sqlite"))
        self.locks = lk.LockManager(self.store.con)
        self.con = self.store.con

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def create(self, video_id="vid1", *, source=APPROVED_SOURCE, payload=None):
        job = ops.create_job_from_broadcast(
            self.store, match_id="m-test", video_id=video_id,
            source_url=f"https://www.youtube.com/watch?v={video_id}",
            channel_id="UCofficial", team_a="qad", team_b="twis",
            expected_layout_id="owcs_jksix_qwc")
        patch = dict(payload or {})
        if source is not None:
            patch["source"] = source
        if patch:
            self.store.update_payload(job.job_key, patch)
        return self.store.get(job.job_key)

    def run_ap(self, job_key, **kw):
        kw.setdefault("worker_id", "gate-test")
        kw.setdefault("run_one", self._fail_run_one)
        return ap.run_autopilot(self.store, self.locks, job_key, **kw)

    @staticmethod
    def _fail_run_one(*a, **kw):
        raise AssertionError("run_one_job should not have been called")

    def approvals(self, job_key) -> dict:
        return self.store.get(job_key).payload.get("autoApprovals") or {}

    def to_state(self, job_key, *states):
        for s in states:
            self.store.transition(job_key, s)


# ============================================ 1. flags off == old behaviour
class TestFlagsOffChangesNothing(GateAutopilotBase):
    def test_unauthorized_source_still_stops(self):
        job = self.create(source=None)
        res = self.run_ap(job.job_key)
        self.assertTrue(res["ok"])
        self.assertEqual(res["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("approve-source", res["stopDetail"])
        self.assertEqual(self.approvals(job.job_key), {},
                         "a disabled gate evaluates nothing at all")

    def test_needs_layout_still_stops(self):
        job = self.create()
        self.to_state(job.job_key, sm.DOWNLOADING, sm.DOWNLOADED,
                      sm.NEEDS_LAYOUT)
        res = self.run_ap(job.job_key)
        self.assertEqual(res["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("approve-layout", res["stopDetail"])
        self.assertEqual(self.approvals(job.job_key), {})

    def test_needs_templates_still_stops(self):
        job = self.create()
        self.to_state(job.job_key, sm.DOWNLOADING, sm.DOWNLOADED,
                      sm.SEGMENTING, sm.PROCESSING, sm.NEEDS_TEMPLATES)
        res = self.run_ap(job.job_key)
        self.assertEqual(res["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("templates", res["stopDetail"])

    def test_detection_review_still_stops(self):
        job = self.create(payload={"detection": detection_payload()})
        self.to_state(job.job_key, sm.DOWNLOADING, sm.DOWNLOADED,
                      sm.SEGMENTING, sm.NEEDS_REVIEW)
        res = self.run_ap(job.job_key)
        self.assertEqual(res["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("never automatic", res["stopDetail"])
        self.assertEqual(self.store.get(job.job_key).state, sm.NEEDS_REVIEW)

    def test_the_stop_message_now_names_the_flag_that_would_automate_it(self):
        """An operator reading a refusal should learn the flag exists."""
        job = self.create(source=None)
        self.assertIn("--auto-source", self.run_ap(job.job_key)["stopDetail"])


# ================================== 2. an enabled gate that refuses stops
class TestEnabledGatesStillRefuse(GateAutopilotBase):
    def test_auto_source_refuses_a_source_with_no_metadata(self):
        job = self.create(source={"state": li.SOURCE_PENDING,
                                  "reasonCode": "channel_not_in_registry"})
        res = self.run_ap(job.job_key,
                          gate_settings=gt.GateSettings(source=True))
        self.assertTrue(res["ok"], "a held gate is still a legitimate rest")
        self.assertEqual(res["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("metadata_unavailable", res["stopDetail"])
        verdict = self.approvals(job.job_key)["source"]
        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["reasonCode"], "metadata_unavailable")

    def test_auto_source_never_re_opens_a_human_rejection(self):
        job = self.create(source={"state": li.SOURCE_REJECTED,
                                  "decidedBy": "Connor"},
                          payload={"metadata": STRONG_METADATA,
                                   "likeness": STRONG_LIKENESS})
        res = self.run_ap(job.job_key,
                          gate_settings=gt.GateSettings(source=True))
        self.assertEqual(res["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("human_rejection_stands", res["stopDetail"])
        self.assertEqual(self.store.get(job.job_key).payload["source"]["state"],
                         li.SOURCE_REJECTED)

    def test_auto_layout_refuses_a_low_confidence_calibration(self):
        job = self.create(payload={"layout": {
            "layoutId": "owcs_new", "approvalRequired": True,
            "calibration": {"confidence": 0.61, "sourceId": "owcs_new"}}})
        self.to_state(job.job_key, sm.DOWNLOADING, sm.DOWNLOADED,
                      sm.NEEDS_LAYOUT)
        res = self.run_ap(job.job_key,
                          gate_settings=gt.GateSettings(layout=True))
        self.assertEqual(res["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("below_unattended_floor", res["stopDetail"])
        self.assertEqual(self.store.get(job.job_key).state, sm.NEEDS_LAYOUT)
        self.assertEqual(self.approvals(job.job_key)["layout"]["metrics"]
                         ["confidence"], 0.61)

    def test_auto_detect_refuses_an_unhealthy_run_and_records_why(self):
        job = self.create(payload={
            "detection": detection_payload(unknown=0.40, full=0.30)})
        self.to_state(job.job_key, sm.DOWNLOADING, sm.DOWNLOADED,
                      sm.SEGMENTING, sm.NEEDS_REVIEW)
        res = self.run_ap(job.job_key,
                          gate_settings=gt.GateSettings(detection=True))
        self.assertEqual(res["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("health_below_floor", res["stopDetail"])
        self.assertEqual(self.store.get(job.job_key).state, sm.NEEDS_REVIEW,
                         "a refused detection must not reach APPROVED")
        metrics = self.approvals(job.job_key)["detection"]["metrics"]
        self.assertEqual(metrics["unknownRate"], 0.40)
        self.assertEqual(metrics["fullHouseRate"], 0.30)

    def test_a_refusal_records_the_floors_it_was_judged_against(self):
        job = self.create(payload={"detection": detection_payload(unknown=0.9)})
        self.to_state(job.job_key, sm.DOWNLOADING, sm.DOWNLOADED,
                      sm.SEGMENTING, sm.NEEDS_REVIEW)
        self.run_ap(job.job_key, gate_settings=gt.GateSettings(detection=True))
        floors = self.approvals(job.job_key)["detection"]["floors"]
        self.assertEqual(floors["auto_detect_max_unknown_rate"], 0.25)
        self.assertEqual(floors["auto_detect_min_gameplay_frames"], 30)


# ======================================= 3. an enabled gate that opens acts
class TestEnabledGatesOpen(GateAutopilotBase):
    def test_auto_source_authorizes_a_strong_non_registry_source(self):
        job = self.create(source={"state": li.SOURCE_PENDING,
                                  "reasonCode": "channel_not_in_registry"},
                          payload={"metadata": STRONG_METADATA,
                                   "likeness": STRONG_LIKENESS})
        # The loop advances past the source gate and then stops on the next
        # honest thing (nothing to download in this fixture) — what matters
        # here is that the source itself is now authorized, and recorded as
        # machine-decided rather than as somebody's signature.
        self.run_ap(job.job_key, gate_settings=gt.GateSettings(source=True),
                    run_one=lambda *a, **k: {"ok": False,
                                             "reason": "no media in fixture"})
        src = self.store.get(job.job_key).payload["source"]
        self.assertEqual(src["state"], li.SOURCE_APPROVED)
        self.assertTrue(src["autoApproved"])
        self.assertEqual(src["decidedBy"], "automatic-gate:source")
        self.assertTrue(self.approvals(job.job_key)["source"]["passed"])

    def test_auto_detect_approves_a_healthy_run(self):
        job = self.create(payload={"detection": detection_payload()})
        self.to_state(job.job_key, sm.DOWNLOADING, sm.DOWNLOADED,
                      sm.SEGMENTING, sm.NEEDS_REVIEW)
        self.run_ap(job.job_key, gate_settings=gt.GateSettings(detection=True),
                    run_one=lambda *a, **k: {"ok": False,
                                             "reason": "stop after approval"})
        self.assertEqual(self.store.get(job.job_key).state, sm.APPROVED)
        self.assertTrue(self.approvals(job.job_key)["detection"]["passed"])

    def test_publish_stays_supervised_without_its_own_flag(self):
        """--auto-detect alone must not imply --auto-publish: clearing the
        detection gate says the data is good, not that it may be pushed."""
        job = self.create(payload={
            "detection": detection_payload(written=True, stints=8),
            "autoApprovals": {"detection": {"passed": True,
                                            "reasonCode": "health_above_floor"}}})
        self.to_state(job.job_key, sm.DOWNLOADING, sm.DOWNLOADED,
                      sm.SEGMENTING, sm.NEEDS_REVIEW, sm.APPROVED)
        calls = []

        def run_one(*a, **k):
            calls.append(1)
            return {"ok": True, "summary": "committed"}

        res = self.run_ap(job.job_key,
                          gate_settings=gt.GateSettings(detection=True),
                          run_one=run_one)
        self.assertEqual(res["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("process-approved-job --publish", res["stopDetail"])

    def test_auto_publish_refuses_a_detection_that_needed_a_human(self):
        job = self.create(payload={
            "detection": detection_payload(written=True, stints=8),
            "autoApprovals": {"detection": {"passed": False,
                                            "reasonCode": "health_below_floor"}}})
        self.to_state(job.job_key, sm.DOWNLOADING, sm.DOWNLOADED,
                      sm.SEGMENTING, sm.NEEDS_REVIEW, sm.APPROVED)
        pushed = []
        res = self.run_ap(
            job.job_key, gate_settings=gt.GateSettings(publish=True),
            run_one=lambda *a, **k: {"ok": True, "summary": "committed"},
            publish_fn=lambda s, j: pushed.append(j) or {"branch": "nope"})
        self.assertEqual(res["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("detection_gate_failed", res["stopDetail"])
        self.assertEqual(pushed, [], "nothing may be pushed")

    def test_auto_publish_pushes_a_branch_when_the_gate_opens(self):
        job = self.create(payload={
            "detection": detection_payload(written=True, stints=8),
            "autoApprovals": {"detection": {"passed": True,
                                            "reasonCode": "health_above_floor"}}})
        self.to_state(job.job_key, sm.DOWNLOADING, sm.DOWNLOADED,
                      sm.SEGMENTING, sm.NEEDS_REVIEW, sm.APPROVED)
        pushed = []
        res = self.run_ap(
            job.job_key, gate_settings=gt.GateSettings(publish=True),
            run_one=lambda *a, **k: {"ok": True, "summary": "committed"},
            publish_fn=lambda s, j: (pushed.append(j.job_key)
                                     or {"branch": "data/publish-x"}))
        self.assertEqual(pushed, [job.job_key])
        self.assertIn("data/publish-x", res["stopDetail"])
        self.assertIn("main is untouched", res["stopDetail"])

    def test_a_publish_failure_is_a_blocker_not_a_silent_success(self):
        job = self.create(payload={
            "detection": detection_payload(written=True, stints=8),
            "autoApprovals": {"detection": {"passed": True,
                                            "reasonCode": "health_above_floor"}}})
        self.to_state(job.job_key, sm.DOWNLOADING, sm.DOWNLOADED,
                      sm.SEGMENTING, sm.NEEDS_REVIEW, sm.APPROVED)

        def boom(store, job_):
            raise RuntimeError("git push rejected")

        res = self.run_ap(
            job.job_key, gate_settings=gt.GateSettings(publish=True),
            run_one=lambda *a, **k: {"ok": True, "summary": "committed"},
            publish_fn=boom)
        self.assertEqual(res["stop"], ap.STOP_BLOCKED)
        self.assertFalse(res["ok"])
        self.assertIn("nothing was pushed", res["stopDetail"])


# ================================================= templates gate integration
class TestTemplatesGateWiring(GateAutopilotBase):
    def _job_in_needs_templates(self):
        job = self.create()
        self.to_state(job.job_key, sm.DOWNLOADING, sm.DOWNLOADED,
                      sm.SEGMENTING, sm.PROCESSING, sm.NEEDS_TEMPLATES)
        return job

    def test_a_pass_that_labels_nothing_still_stops_and_says_so(self):
        job = self._job_in_needs_templates()
        res = self.run_ap(
            job.job_key, gate_settings=gt.GateSettings(templates=True),
            label_fn=lambda s, j, floors=None: {
                "labelled": [], "held": [{"cluster": "a1_c0", "hero": "mei",
                                          "reasonCode": "margin_below_floor",
                                          "reason": "too close"}],
                "coverageBefore": "7/52 (13.5%)",
                "coverageAfter": "7/52 (13.5%)",
                "manifest": "templates/pkg/_auto_label_review.json"})
        self.assertEqual(res["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("held on every cluster", res["stopDetail"])
        self.assertFalse(self.approvals(job.job_key)["templates"]["passed"])

    def test_labelling_something_advances_the_loop(self):
        job = self._job_in_needs_templates()
        self.run_ap(
            job.job_key, gate_settings=gt.GateSettings(templates=True),
            run_one=lambda *a, **k: {"ok": False, "reason": "stop here"},
            label_fn=lambda s, j, floors=None: {
                "labelled": ["mei", "tracer"], "held": [],
                "coverageBefore": "7/52 (13.5%)",
                "coverageAfter": "9/52 (17.3%)",
                "manifest": "templates/pkg/_auto_label_review.json"})
        verdict = self.approvals(job.job_key)["templates"]
        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["metrics"]["labelled"], ["mei", "tracer"])

    def test_labelling_does_not_re_enter_needs_templates_and_spin(self):
        """Labelling changes coverage but does not itself move the job. Left
        alone the loop re-enters NEEDS_TEMPLATES and re-harvests until the
        step cap — burning a full harvest per iteration for nothing."""
        job = self._job_in_needs_templates()
        calls = []
        self.run_ap(
            job.job_key, gate_settings=gt.GateSettings(templates=True),
            run_one=lambda *a, **k: {"ok": False, "reason": "stop here"},
            label_fn=lambda s, j, floors=None: (
                calls.append(1) or {
                    "labelled": ["mei"], "held": [],
                    "coverageBefore": "7/52 (13.5%)",
                    "coverageAfter": "8/52 (15.4%)",
                    "manifest": "m.json"}))
        self.assertEqual(len(calls), 1,
                         "the labeller must run once, not once per step")

    def test_a_labeller_that_cannot_run_is_a_gate_hold_not_a_crash(self):
        job = self._job_in_needs_templates()

        def boom(store, job_, floors=None):
            raise ValueError("no approved, extracted segment clip")

        res = self.run_ap(job.job_key,
                          gate_settings=gt.GateSettings(templates=True),
                          label_fn=boom)
        self.assertEqual(res["stop"], ap.STOP_HUMAN_GATE)
        self.assertIn("could not run", res["stopDetail"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
