"""
unattended.py — the policy layer for running without a human at the wheel.

Every gate the autopilot used to stop at existed for a reason: approving a
layout, promoting hero compositions into production and publishing are all
decisions with consequences. Making them automatic does NOT mean removing
the judgement — it means writing the judgement down as measurable floors,
so the machine can apply it consistently and REFUSE just as loudly as a
person would.

That is what this module is. It is pure: it reads a job's recorded evidence
and returns an allow/hold verdict with the numbers behind it. It writes
nothing, runs nothing, and knows nothing about the loop that calls it.

Four gates, each independently opt-in (nothing here is on by default):

  layout      A freshly-calibrated layout may be promoted into `layouts/`
              only when the calibrator's own confidence clears
              `layout_min_confidence` — a bar deliberately set ABOVE
              `calibrate_source.CONFIDENCE_FLOOR` (the floor below which a
              calibration is refused outright). "Not refused" is not the
              same as "good enough to adopt unseen".

  templates   Hero templates may be auto-labeled from cluster evidence only
              above a similarity AND margin floor, and only for heroes the
              package does not already cover. See `template_bootstrap.
              auto_label` — a wrong template is permanent damage, so the
              margin discipline mirrors detect.py's own slot matching.

  detection   Compositions may be promoted into production only when THIS
              run's own `calibration_health` says the detection was sound:
              the health status itself, plus explicit ceilings on the
              UNKNOWN rate and floors on full-house rate, median match score
              and gameplay-frame count. This is the gate that matters most:
              template coverage across this repo's packages currently runs
              13-33%, and at that coverage most compositions SHOULD be
              refused. The floors are what make "automatic" mean "when the
              evidence is good", not "always".

  publish     A validated publication commit may be pushed only after a
              detection result was actually committed (write=True) and the
              detection gate itself passed. Publication still goes to a
              BRANCH — `publish.publish_job` never touches main — so the
              final merge remains a human act unless the repo is configured
              otherwise.

Floors come from `config/automation.yml` (keys prefixed `unattended_`) with
the conservative defaults below. Every verdict carries `metrics` and a
human-readable `reason`, and the caller records it on the job, so an
automatic decision leaves exactly the same audit trail a human approval
does — who/what decided, when, on what evidence.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

# ---------------------------------------------------------------- the floors
# Deliberately conservative. Raising a floor makes the machine refuse more
# often; lowering one is a decision an operator makes explicitly in
# config/automation.yml, where it is visible and diffable.
DEFAULT_FLOORS: dict[str, Any] = {
    # --- layout ---------------------------------------------------------
    # calibrate_source.CONFIDENCE_FLOOR is 0.55 (below it, calibration is
    # refused outright). Adopting a layout unseen demands a real margin
    # above that, not merely clearing it.
    "layout_min_confidence": 0.75,

    # --- templates ------------------------------------------------------
    # A cluster must look like ONE hero and not much like the runner-up.
    "template_min_score": 0.55,
    "template_min_margin": 0.12,
    # A cluster seen in only a frame or two is as likely to be an artifact
    # (killcam, transition, overlay) as a portrait.
    "template_min_cluster_members": 4,

    # --- detection ------------------------------------------------------
    # ingest_map.calibration_health's own bars are unknown<=0.35,
    # full_house>=0.40, median>=0.60. Promoting to production unreviewed
    # asks for better than "not suspect".
    "detection_require_health_ok": True,
    "detection_max_unknown_rate": 0.25,
    "detection_min_full_house_rate": 0.60,
    "detection_min_median_score": 0.65,
    "detection_min_gameplay_frames": 30,
    "detection_min_stints": 1,

    # --- publish --------------------------------------------------------
    "publish_require_detection_committed": True,
    "publish_require_detection_gate": True,
}

# Gate names. Stable strings — recorded on jobs and printed by the CLI.
GATE_LAYOUT = "layout"
GATE_TEMPLATES = "templates"
GATE_DETECTION = "detection"
GATE_PUBLISH = "publish"
ALL_GATES = (GATE_LAYOUT, GATE_TEMPLATES, GATE_DETECTION, GATE_PUBLISH)


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_floors(config: Any = None, overrides: dict | None = None
                ) -> dict[str, Any]:
    """DEFAULT_FLOORS, overlaid with any `unattended_<key>` entries from the
    operator config, overlaid with explicit caller overrides.

    An unknown `unattended_*` key is a typo the operator should see, so it is
    kept in the returned dict (nothing silently drops) — callers reading only
    known keys are unaffected, and `describe_floors` prints everything.
    """
    floors = dict(DEFAULT_FLOORS)
    values = getattr(config, "values", None) if config is not None else None
    if isinstance(values, dict):
        for key, value in values.items():
            if str(key).startswith("unattended_"):
                floors[str(key)[len("unattended_"):]] = value
    for key, value in (overrides or {}).items():
        if value is not None:
            floors[key] = value
    return floors


class Policy:
    """Which gates may act automatically, and on what floors.

    Nothing is enabled by default: `Policy()` behaves exactly like today's
    fully-supervised autopilot. `Policy.unattended()` turns all four on,
    which is what the scheduled runner uses.
    """

    def __init__(self, *, layout: bool = False, templates: bool = False,
                 detection: bool = False, publish: bool = False,
                 floors: dict | None = None, decided_by: str | None = None):
        self.layout = bool(layout)
        self.templates = bool(templates)
        self.detection = bool(detection)
        self.publish = bool(publish)
        self.floors = dict(floors or DEFAULT_FLOORS)
        self.decided_by = decided_by or "unattended-policy"

    @classmethod
    def unattended(cls, *, floors: dict | None = None,
                   decided_by: str | None = None) -> "Policy":
        return cls(layout=True, templates=True, detection=True, publish=True,
                   floors=floors, decided_by=decided_by)

    @property
    def any_enabled(self) -> bool:
        return any((self.layout, self.templates, self.detection, self.publish))

    def enabled(self, gate: str) -> bool:
        return bool(getattr(self, gate, False))

    def describe(self) -> dict:
        return {"gates": {g: self.enabled(g) for g in ALL_GATES},
                "floors": dict(self.floors), "decidedBy": self.decided_by}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        on = [g for g in ALL_GATES if self.enabled(g)]
        return f"<Policy {'+'.join(on) or 'supervised'}>"


def verdict(gate: str, allow: bool, code: str, reason: str,
            metrics: dict | None = None, *, decided_by: str | None = None
            ) -> dict[str, Any]:
    """One gate decision, in the shape every caller records on the job."""
    return {"gate": gate, "allow": allow, "reasonCode": code,
            "reason": reason, "metrics": metrics or {},
            "decidedBy": decided_by, "decidedAt": _utcnow_iso()}


# ------------------------------------------------------------------- layout
def layout_gate(record: dict | None, floors: dict, *,
                decided_by: str | None = None) -> dict[str, Any]:
    """May this job's calibrated layout be promoted without a human?

    `record` is `job.payload["layout"]` as written by
    `layout_resolver.resolve_layout`.
    """
    rec = record or {}
    if not rec:
        return verdict(GATE_LAYOUT, False, "not_resolved",
                       "no layout resolution on this job yet",
                       decided_by=decided_by)
    if not rec.get("approvalRequired"):
        return verdict(GATE_LAYOUT, True, "no_approval_needed",
                       f"layout {rec.get('layoutId')} is a reuse of an "
                       f"already-committed package — nothing to approve",
                       decided_by=decided_by)
    calib = rec.get("calibration") or {}
    if calib.get("refusal"):
        return verdict(GATE_LAYOUT, False, "calibration_refused",
                       f"the calibrator refused this layout: {calib['refusal']}",
                       decided_by=decided_by)
    confidence = calib.get("confidence")
    floor = float(floors.get("layout_min_confidence",
                             DEFAULT_FLOORS["layout_min_confidence"]))
    metrics = {"confidence": confidence, "floor": floor,
               "calibrationFloor": calib.get("floor")}
    if confidence is None:
        return verdict(GATE_LAYOUT, False, "no_confidence_recorded",
                       "the calibration recorded no confidence score — a "
                       "human must look at the sheet", metrics,
                       decided_by=decided_by)
    if float(confidence) < floor:
        return verdict(GATE_LAYOUT, False, "below_confidence_floor",
                       f"calibration confidence {confidence} is below the "
                       f"unattended floor {floor} (it cleared the "
                       f"calibrator's own {calib.get('floor')} floor, which "
                       f"is why a layout exists at all) — review "
                       f"{calib.get('reviewSheet') or 'the calibration sheet'}",
                       metrics, decided_by=decided_by)
    return verdict(GATE_LAYOUT, True, "confidence_above_floor",
                   f"calibration confidence {confidence} >= {floor} — "
                   f"promoting layout unattended", metrics,
                   decided_by=decided_by)


# ---------------------------------------------------------------- detection
def detection_gate(summary: dict | None, floors: dict, *,
                   decided_by: str | None = None) -> dict[str, Any]:
    """May these compositions reach production without a human review?

    `summary` is `job.payload["detection"]` as written by
    `detection_runner.run_detection` — its `stats.calibration_health` is
    THIS run's own measurement of how well the detection actually went,
    which is exactly the evidence a reviewer would look at first.
    """
    s = summary or {}
    if not s:
        return verdict(GATE_DETECTION, False, "no_detection",
                       "no detection has been run on this job",
                       decided_by=decided_by)
    stats = s.get("stats") or {}
    health = stats.get("calibration_health") or {}
    metrics = dict(health.get("metrics") or {})
    metrics["healthStatus"] = health.get("status")
    metrics["stints"] = (s.get("db") or {}).get("stints")
    metrics["rounds"] = stats.get("rounds")

    if not health:
        return verdict(GATE_DETECTION, False, "no_calibration_health",
                       "the detection recorded no calibration_health block — "
                       "there is no evidence to judge it by", metrics,
                       decided_by=decided_by)

    fails: list[str] = []
    if (floors.get("detection_require_health_ok", True)
            and health.get("status") != "ok"):
        fails.append(
            f"calibration health is {health.get('status')!r}: "
            + "; ".join(health.get("reasons") or ["no reason recorded"]))

    def _ceiling(key: str, label: str) -> None:
        cap = floors.get(key)
        val = metrics.get(label)
        if cap is not None and val is not None and float(val) > float(cap):
            fails.append(f"{label} {val} exceeds the {cap} ceiling")

    def _floor(key: str, label: str) -> None:
        bar = floors.get(key)
        val = metrics.get(label)
        if bar is None:
            return
        if val is None:
            fails.append(f"{label} was not measured, so the {bar} floor "
                         f"cannot be checked")
        elif float(val) < float(bar):
            fails.append(f"{label} {val} is below the {bar} floor")

    _ceiling("detection_max_unknown_rate", "unknown_rate")
    _floor("detection_min_full_house_rate", "full_house_rate")
    _floor("detection_min_median_score", "median_top_score")
    _floor("detection_min_gameplay_frames", "gameplay_frames")

    if fails:
        return verdict(GATE_DETECTION, False, "below_quality_floor",
                       "detection quality is below the unattended floors — "
                       + "; ".join(fails)
                       + ". Review reports/ingest/"
                       + str(s.get("ingestId") or "<id>")
                       + "/report.html and approve by hand if it is right.",
                       metrics, decided_by=decided_by)
    return verdict(GATE_DETECTION, True, "quality_above_floors",
                   f"detection quality cleared every floor "
                   f"(health ok, unknown_rate {metrics.get('unknown_rate')}, "
                   f"full_house_rate {metrics.get('full_house_rate')}, "
                   f"median score {metrics.get('median_top_score')}, "
                   f"{metrics.get('gameplay_frames')} gameplay frames)",
                   metrics, decided_by=decided_by)


def committed_detection_gate(summary: dict | None, floors: dict, *,
                             decided_by: str | None = None) -> dict[str, Any]:
    """Did a detection actually get COMMITTED (write=True), with rows?

    Separate from `detection_gate` on purpose: that one judges quality before
    promotion, this one confirms the write really happened before anything is
    published. A dry run that never wrote is not publishable, however good
    its numbers were.
    """
    s = summary or {}
    db = s.get("db") or {}
    metrics = {"written": s.get("written"), "stints": db.get("stints"),
               "swaps": db.get("swaps"), "observations": db.get("observations")}
    if not s.get("written"):
        return verdict(GATE_PUBLISH, False, "detection_not_committed",
                       "the recorded detection was a dry run (write=False) — "
                       "nothing has been persisted to publish", metrics,
                       decided_by=decided_by)
    min_stints = floors.get("detection_min_stints")
    if min_stints is not None and (db.get("stints") or 0) < int(min_stints):
        return verdict(GATE_PUBLISH, False, "no_rows_written",
                       f"the committed detection wrote "
                       f"{db.get('stints') or 0} hero stint(s), below the "
                       f"{min_stints} minimum — publishing an empty result "
                       f"would say more than the evidence does", metrics,
                       decided_by=decided_by)
    return verdict(GATE_PUBLISH, True, "detection_committed",
                   f"detection committed {db.get('stints')} stint(s) and "
                   f"{db.get('swaps')} swap row(s)", metrics,
                   decided_by=decided_by)


# ------------------------------------------------------------------ publish
def publish_gate(job_payload: dict | None, floors: dict, *,
                 decided_by: str | None = None) -> dict[str, Any]:
    """May a publication commit be created and pushed without a human?

    Requires a committed detection and (unless explicitly disabled) that the
    detection quality gate itself passed — publishing is downstream of the
    decision to promote, so it must not be a way around it.
    """
    payload = job_payload or {}
    summary = payload.get("detection") or {}
    if floors.get("publish_require_detection_committed", True):
        committed = committed_detection_gate(summary, floors,
                                             decided_by=decided_by)
        if not committed["allow"]:
            return committed
    if floors.get("publish_require_detection_gate", True):
        recorded = ((payload.get("unattended") or {}).get(GATE_DETECTION)
                    or {})
        if recorded and not recorded.get("allow"):
            return verdict(GATE_PUBLISH, False, "detection_gate_held",
                           f"the detection gate held this job "
                           f"([{recorded.get('reasonCode')}] "
                           f"{recorded.get('reason')}) — publication cannot "
                           f"route around it",
                           recorded.get("metrics") or {},
                           decided_by=decided_by)
    return verdict(GATE_PUBLISH, True, "ready_to_publish",
                   "a committed detection with rows is present and no gate "
                   "is holding this job — creating the publication branch "
                   "(never main; the merge stays a human act)",
                   decided_by=decided_by)


# --------------------------------------------------------------- rendering
def format_verdict(v: dict) -> str:
    """One operator-readable line per decision."""
    mark = "AUTO" if v.get("allow") else "HELD"
    return f"  {mark} [{v.get('gate')}/{v.get('reasonCode')}] {v.get('reason')}"


def describe_floors(floors: dict) -> str:
    return "\n".join(f"  {k:38} {v}" for k, v in sorted(floors.items()))


def record_verdict(store, job_key: str, v: dict) -> None:
    """Persist one gate verdict on the job, keeping every gate's latest.

    The audit trail an automatic decision leaves must be at least as good as
    a human's: what was decided, by what policy, on which numbers, when.
    """
    job = store.get(job_key)
    prior = dict((job.payload.get("unattended") or {})) if job else {}
    prior[v["gate"]] = v
    store.update_payload(job_key, {"unattended": prior})
