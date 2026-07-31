"""
gates.py — the unattended-approval gate engine.

Every gate in this pipeline used to end the same way: the autopilot stopped
and named a command for a human to type. That is still the default, and it
is still what happens when the evidence is thin. This module is the other
half — the part that lets a gate be *cleared by evidence* instead of by a
person, and records exactly which evidence cleared it.

Five gates, five flags:

    source      --auto-source     provenance + broadcast likeness
    layout      --auto-layout     calibration confidence
    templates   --auto-templates  labelled-portrait match score + margin
    detection   --auto-detect     THIS run's own calibration health
    publish     --auto-publish    a committed detection with real stints

`--unattended` turns on all five.

Three properties every gate here holds to:

  * **Pure.** Nothing in this module reads a database, writes a file, or
    imports cv2. A gate takes metrics in and returns a `Verdict` out. That
    is what makes all 40+ gate tests run offline in a container with no
    ffmpeg, no OpenCV and no network — the same reason `detection_runner`
    keeps its heavy imports function-local.

  * **Refusal is the default.** Every evaluator starts from "no" and needs
    a positive reason to reach "yes". Missing metrics are never treated as
    passing metrics: `None` unknown-rate is a refusal, not a 0.0. This is
    the single most important invariant in the file, and the reason the
    tests assert refusals rather than approvals.

  * **Auditable.** A `Verdict` carries the metrics it judged, the floors it
    judged them against, and where each floor came from (config or default).
    An automatic approval recorded on a job is therefore reconstructable
    months later: you can see the numbers, and you can see the bar. That is
    the whole argument for allowing automatic approval at all — an
    unexplained "approved" would be strictly worse than a human's signature,
    but an approval that shows its work is strictly better.

The floors are deliberately STRICTER than the pipeline's existing "is this
broken?" thresholds. `ingest_map` calls a run *suspect* below 0.40 full-house
/ 0.60 median / above 0.35 unknown; those answer "should a human look at
this?". The floors here answer "may this reach production with nobody
looking?" — a different and much higher question, so 0.60 / 0.65 / 0.25.
Passing `ingest_map`'s health check is not enough to clear this gate, and
that gap is intentional, not an oversight.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------- gate names
GATE_SOURCE = "source"
GATE_LAYOUT = "layout"
GATE_TEMPLATES = "templates"
GATE_DETECTION = "detection"
GATE_PUBLISH = "publish"

ALL_GATES = (GATE_SOURCE, GATE_LAYOUT, GATE_TEMPLATES, GATE_DETECTION,
             GATE_PUBLISH)

# The config key that enables each gate, and the CLI flag that maps to it.
GATE_FLAGS: dict[str, str] = {
    GATE_SOURCE: "--auto-source",
    GATE_LAYOUT: "--auto-layout",
    GATE_TEMPLATES: "--auto-templates",
    GATE_DETECTION: "--auto-detect",
    GATE_PUBLISH: "--auto-publish",
}

# --------------------------------------------------------------------- floors
# Every tunable in one table so `unattended-floors` can print the live value
# of each one next to where it came from. Key -> (default, gate, description).
FLOOR_SPECS: dict[str, tuple[Any, str, str]] = {
    # source ---------------------------------------------------------------
    "auto_source_min_likeness": (
        55, GATE_SOURCE,
        "minimum broadcast-likeness score to auto-approve a NON-registry "
        "source (registry channels are already automatic and ignore this)"),
    "auto_source_min_duration_seconds": (
        1200, GATE_SOURCE,
        "minimum VOD duration to auto-approve a non-registry source"),
    # layout ---------------------------------------------------------------
    "auto_layout_min_confidence": (
        0.75, GATE_LAYOUT,
        "calibration confidence required to approve a layout unattended"),
    "auto_layout_refuse_below": (
        0.55, GATE_LAYOUT,
        "calibration confidence below which calibration is refused outright "
        "(calibrate_source's own floor — never lowered here)"),
    # templates ------------------------------------------------------------
    "auto_templates_min_score": (
        0.55, GATE_TEMPLATES,
        "minimum match score against a real labelled portrait to auto-label "
        "a hero template"),
    "auto_templates_min_margin": (
        0.12, GATE_TEMPLATES,
        "minimum score margin between the best and runner-up hero before an "
        "auto-label is trusted"),
    "auto_templates_min_frames": (
        4, GATE_TEMPLATES,
        "minimum distinct frames backing a cluster before it may be labelled"),
    # detection ------------------------------------------------------------
    "auto_detect_max_unknown_rate": (
        0.25, GATE_DETECTION,
        "maximum UNKNOWN/rejected slot-read rate for an unattended detection "
        "approval (ingest_map calls a run merely 'suspect' above 0.35)"),
    "auto_detect_min_full_house": (
        0.60, GATE_DETECTION,
        "minimum fraction of gameplay frames with all 10 slots accepted "
        "(ingest_map's suspect threshold is 0.40)"),
    "auto_detect_min_median_score": (
        0.65, GATE_DETECTION,
        "minimum median accepted template match score "
        "(ingest_map's suspect threshold is 0.60)"),
    "auto_detect_min_gameplay_frames": (
        30, GATE_DETECTION,
        "minimum confirmed gameplay frames — a handful of frames can hit "
        "every ratio above and still be far too little evidence"),
    # publish --------------------------------------------------------------
    "auto_publish_min_stints": (
        1, GATE_PUBLISH,
        "minimum committed hero stints required before an automatic publish"),
}


def resolve_floors(cfg: Any = None) -> dict[str, dict[str, Any]]:
    """{key: {"value", "default", "source", "gate", "description"}}.

    `source` is "config" when automation.yml overrides the default and
    "default" otherwise — this is what `unattended-floors` prints, so an
    operator can tell a tuned floor from an assumed one at a glance.

    Accepts anything with a `.values` mapping (an `AutomationConfig`) or a
    plain dict, so the gate tests never need to touch the config loader.
    """
    if cfg is None:
        overrides: dict[str, Any] = {}
        explicit: set = set()
    elif isinstance(cfg, dict):
        overrides = cfg
        explicit = set(cfg)
    else:
        overrides = dict(getattr(cfg, "values", {}) or {})
        # An AutomationConfig's `values` is DEFAULTS merged with the file, so
        # membership there proves nothing. `explicit` is the set of keys the
        # file really set, and it is what distinguishes a tuned floor from an
        # assumed one in the `unattended-floors` listing.
        explicit = set(getattr(cfg, "explicit", None) or ())

    out: dict[str, dict[str, Any]] = {}
    for key, (default, gate, desc) in FLOOR_SPECS.items():
        present = (key in explicit and overrides.get(key) is not None)
        raw = overrides[key] if present else default
        # A float default means a float floor; keep ints (frame counts) int.
        try:
            value = type(default)(raw)
        except (TypeError, ValueError):
            raise ValueError(
                f"config key {key!r} must be a {type(default).__name__}, "
                f"got {raw!r}") from None
        out[key] = {
            "value": value,
            "default": default,
            "source": "config" if present else "default",
            "gate": gate,
            "description": desc,
        }
    return out


def floor_values(cfg: Any = None) -> dict[str, Any]:
    """Just {key: value} — the form the evaluators want."""
    return {k: v["value"] for k, v in resolve_floors(cfg).items()}


# -------------------------------------------------------------------- verdict
@dataclass
class Verdict:
    """One gate's decision, with everything needed to re-derive it later."""
    gate: str
    passed: bool
    reason_code: str
    reason: str
    metrics: dict[str, Any] = field(default_factory=dict)
    floors: dict[str, Any] = field(default_factory=dict)
    decided_at: str = ""
    decided_by: str = ""

    def to_payload(self) -> dict[str, Any]:
        """The shape recorded on the job payload under `autoApprovals`.

        camelCase to match every other payload block in this package."""
        return {
            "gate": self.gate,
            "passed": self.passed,
            "reasonCode": self.reason_code,
            "reason": self.reason,
            "metrics": dict(self.metrics),
            "floors": dict(self.floors),
            "decidedAt": self.decided_at,
            "decidedBy": self.decided_by,
        }


def _utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _verdict(gate: str, passed: bool, code: str, reason: str, *,
             metrics: dict | None = None, floors: dict | None = None,
             decided_by: str | None = None, now: str | None = None) -> Verdict:
    return Verdict(
        gate=gate, passed=passed, reason_code=code, reason=reason,
        metrics=metrics or {}, floors=floors or {},
        decided_at=now or _utcnow_iso(),
        decided_by=decided_by or f"automatic-gate:{gate}",
    )


def _num(value: Any) -> float | None:
    """Coerce to float, or None. A metric that is absent, null or
    non-numeric must never be silently read as 0.0 — for `unknown_rate`
    that would turn missing evidence into a perfect score."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- the gates
def _discovery_provenance(discovery: dict | None) -> dict | None:
    """Channel-binding evidence from a keyless discovery pass, or None.

    A video that appears in `youtube.com/feeds/videos.xml?channel_id=<id>`,
    or in that channel's own /streams tab, is bound to that channel by
    YouTube itself — the same authority the API would be quoting. So when
    the discovery pass found this video on a channel that is in the VERIFIED
    registry, the provenance question is answered without an API key.

    Requires the registry binding to be explicit: a discovery record with no
    `channelRegistryId` was not matched to a verified channel and proves
    nothing at all.
    """
    d = discovery or {}
    registry_id = d.get("channelRegistryId")
    if not registry_id:
        return None
    sources = [str(s) for s in (d.get("sources") or [])]
    return {"registryChannel": registry_id,
            "channelId": d.get("channelId"),
            "channelTitle": d.get("channelTitle"),
            "sources": sources,
            "durationSeconds": d.get("durationSeconds"),
            "liveBroadcastStatus": d.get("liveBroadcastStatus"),
            "likeness": d.get("likeness")}


def evaluate_source_gate(source: dict | None, *, metadata: dict | None = None,
                         likeness: dict | None = None,
                         discovery: dict | None = None,
                         floors: dict | None = None,
                         now: str | None = None) -> Verdict:
    """Gate 0 — may this source be downloaded with nobody signing for it?

    This is the gate the earlier design deliberately left manual, and the
    one `make fully automatic` adds. It is also the gate with the least
    room for cleverness, so the rule is narrow on purpose:

      * a source already approved (including the registry auto-approval
        that has always existed) passes trivially;
      * an explicitly REJECTED source is never re-opened by automation —
        a human said no, and a flag does not overrule a person;
      * anything else needs retrieved metadata, a completed (not live or
        upcoming) VOD, a plausible duration, and a broadcast-likeness score
        at or above the floor.

    Missing metadata is always a refusal: an un-fetchable channel is exactly
    the case where "probably fine" is worth least.
    """
    fl = floors or floor_values()
    min_score = fl["auto_source_min_likeness"]
    min_dur = fl["auto_source_min_duration_seconds"]
    used = {"auto_source_min_likeness": min_score,
            "auto_source_min_duration_seconds": min_dur}
    src = source or {}
    state = src.get("state")

    if state == "approved":
        return _verdict(
            GATE_SOURCE, True, "already_approved",
            f"source already authorized ({src.get('reasonCode') or 'no code'})",
            metrics={"priorState": state,
                     "autoApproved": bool(src.get("autoApproved"))},
            floors=used, now=now)

    if state == "rejected":
        return _verdict(
            GATE_SOURCE, False, "human_rejection_stands",
            "a human rejected this source — automation never re-opens a "
            "refusal, whatever flags are set",
            metrics={"priorState": state,
                     "decidedBy": src.get("decidedBy")},
            floors=used, now=now)

    meta = metadata or {}
    provenance = _discovery_provenance(discovery)
    if meta.get("status") != "ok":
        # No API metadata. A keyless discovery pass may still have bound
        # this video to a verified registry channel via that channel's own
        # feed — evidence of the same kind, from the same authority.
        if provenance is None:
            return _verdict(
                GATE_SOURCE, False, "metadata_unavailable",
                "source metadata could not be retrieved and no verified "
                "channel-feed provenance was recorded, so there is nothing "
                "to judge — this is the last case that should ever be "
                "waved through",
                metrics={"metadataStatus": meta.get("status"),
                         "errorCode": meta.get("errorCode")},
                floors=used, now=now)
        return _evaluate_discovery_source(provenance, floors=used, now=now)

    live_status = meta.get("liveBroadcastStatus")
    if live_status in ("live", "upcoming"):
        return _verdict(
            GATE_SOURCE, False, "not_a_completed_vod",
            f"video is {live_status} — only completed VODs are processed; "
            f"intake will re-decide once the stream has ended",
            metrics={"liveBroadcastStatus": live_status},
            floors=used, now=now)

    duration = _num(meta.get("durationSeconds"))
    if duration is None or duration < min_dur:
        return _verdict(
            GATE_SOURCE, False, "duration_too_short",
            f"duration {duration if duration is not None else 'unknown'}s is "
            f"below the {min_dur}s floor for an unattended approval",
            metrics={"durationSeconds": duration},
            floors=used, now=now)

    lk = likeness or {}
    score = _num(lk.get("score"))
    if score is None:
        return _verdict(
            GATE_SOURCE, False, "likeness_unavailable",
            "no broadcast-likeness score was computed for this source",
            metrics={"likeness": None}, floors=used, now=now)
    if lk.get("confidence") == "unlikely" or score < min_score:
        return _verdict(
            GATE_SOURCE, False, "likeness_below_floor",
            f"broadcast-likeness {score:g} is below the {min_score} floor "
            f"for a non-registry source — "
            + "; ".join(list(lk.get("reasons") or [])[:3]),
            metrics={"likenessScore": score,
                     "likenessConfidence": lk.get("confidence")},
            floors=used, now=now)

    return _verdict(
        GATE_SOURCE, True, "likeness_and_provenance",
        f"non-registry source cleared unattended: likeness {score:g} "
        f">= {min_score}, completed VOD of {duration:.0f}s "
        f"(channel {meta.get('channelTitle') or meta.get('channelId') or '?'})",
        metrics={"likenessScore": score, "durationSeconds": duration,
                 "channelId": meta.get("channelId"),
                 "channelTitle": meta.get("channelTitle"),
                 "liveBroadcastStatus": live_status},
        floors=used, now=now)


def _evaluate_discovery_source(prov: dict, *, floors: dict,
                               now: str | None = None) -> Verdict:
    """The keyless provenance path: a verified registry channel's own feed.

    Held to the SAME duration, live-status and likeness floors as the API
    path. The only thing that differs is where the channel binding came
    from, and a registry channel's own feed is not a weaker source for that
    question than the API — it is the same fact from the same publisher.
    """
    min_score = floors["auto_source_min_likeness"]
    min_dur = floors["auto_source_min_duration_seconds"]
    metrics = {"registryChannel": prov["registryChannel"],
               "channelId": prov.get("channelId"),
               "discoverySources": prov.get("sources"),
               "provenance": "verified-channel-feed"}

    live_status = prov.get("liveBroadcastStatus")
    if live_status in ("live", "upcoming"):
        return _verdict(
            GATE_SOURCE, False, "not_a_completed_vod",
            f"video is {live_status} on the channel feed — only completed "
            f"VODs are processed",
            metrics=dict(metrics, liveBroadcastStatus=live_status),
            floors=floors, now=now)

    duration = _num(prov.get("durationSeconds"))
    if duration is None or duration < min_dur:
        # The RSS feed alone carries no duration; the /streams tab does. A
        # candidate seen only via RSS therefore waits rather than being
        # approved on an unknown length.
        return _verdict(
            GATE_SOURCE, False, "duration_unknown_or_short",
            f"channel-feed duration "
            f"{duration if duration is not None else 'unknown (RSS carries none)'}"
            f" does not clear the {min_dur}s floor",
            metrics=dict(metrics, durationSeconds=duration),
            floors=floors, now=now)

    lk = prov.get("likeness") or {}
    score = _num(lk.get("score"))
    if score is None:
        return _verdict(
            GATE_SOURCE, False, "likeness_unavailable",
            "the discovery pass recorded no broadcast-likeness score",
            metrics=metrics, floors=floors, now=now)
    if lk.get("confidence") == "unlikely" or score < min_score:
        return _verdict(
            GATE_SOURCE, False, "likeness_below_floor",
            f"broadcast-likeness {score:g} is below the {min_score} floor — "
            + "; ".join(list(lk.get("reasons") or [])[:3]),
            metrics=dict(metrics, likenessScore=score,
                         likenessConfidence=lk.get("confidence")),
            floors=floors, now=now)

    return _verdict(
        GATE_SOURCE, True, "registry_channel_feed",
        f"found on verified registry channel {prov['registryChannel']!r}'s "
        f"own feed ({', '.join(prov.get('sources') or ['feed'])}); "
        f"likeness {score:g} >= {min_score}, completed VOD of "
        f"{duration:.0f}s — no API key was needed to establish this",
        metrics=dict(metrics, likenessScore=score, durationSeconds=duration),
        floors=floors, now=now)


def evaluate_layout_gate(layout: dict | None, *, floors: dict | None = None,
                         now: str | None = None) -> Verdict:
    """Gate 1 — may a freshly-calibrated layout be approved unattended?

    Two thresholds, and the lower one is not ours: `calibrate_source` already
    refuses to emit a layout below its own confidence floor, and a job in
    that state has no layout to approve at all. The upper floor is the
    unattended bar, well above the "worth a human's time" line.
    """
    fl = floors or floor_values()
    min_conf = fl["auto_layout_min_confidence"]
    refuse_below = fl["auto_layout_refuse_below"]
    used = {"auto_layout_min_confidence": min_conf,
            "auto_layout_refuse_below": refuse_below}
    lay = layout or {}
    conf = _num(lay.get("confidence"))

    if conf is None:
        return _verdict(
            GATE_LAYOUT, False, "no_confidence_recorded",
            "no calibration confidence was recorded for this layout — "
            "without a number there is nothing to clear the gate with",
            metrics={"confidence": None}, floors=used, now=now)

    reasons = [str(r) for r in (lay.get("reasons") or [])]
    metrics = {"confidence": conf, "layoutId": lay.get("layoutId"),
               "reasons": reasons[:5]}

    if conf < refuse_below:
        return _verdict(
            GATE_LAYOUT, False, "below_calibration_floor",
            f"calibration confidence {conf:g} is below the hard "
            f"{refuse_below} floor — this layout is refused outright, not "
            f"merely held for review",
            metrics=metrics, floors=used, now=now)

    if conf < min_conf:
        return _verdict(
            GATE_LAYOUT, False, "below_unattended_floor",
            f"calibration confidence {conf:g} clears the {refuse_below} "
            f"calibration floor but is under the {min_conf} unattended "
            f"floor — a human should look at the review sheet",
            metrics=metrics, floors=used, now=now)

    return _verdict(
        GATE_LAYOUT, True, "confidence_above_floor",
        f"calibration confidence {conf:g} >= {min_conf}",
        metrics=metrics, floors=used, now=now)


def evaluate_templates_gate(candidate: dict | None, *,
                            floors: dict | None = None,
                            now: str | None = None) -> Verdict:
    """Gate 2 — may this hero-template cluster be auto-labelled?

    The scoring reference is a real labelled BROADCAST PORTRAIT from an
    already-reviewed package, never official splash art. That is a deliberate
    design call, and the existing suggester's own docstring makes the case:
    a splash render does not look like a HUD portrait, so scoring against one
    measures the wrong thing.

    Official art still gets a say — as a VETO only. `officialOpinion` naming
    a different hero than the portrait match is a hold, not a tiebreak. Two
    independent sources disagreeing about identity is precisely when a coin
    flip is least defensible.
    """
    fl = floors or floor_values()
    min_score = fl["auto_templates_min_score"]
    min_margin = fl["auto_templates_min_margin"]
    min_frames = fl["auto_templates_min_frames"]
    used = {"auto_templates_min_score": min_score,
            "auto_templates_min_margin": min_margin,
            "auto_templates_min_frames": min_frames}
    cand = candidate or {}
    hero = cand.get("hero")
    score = _num(cand.get("score"))
    runner_up = _num(cand.get("runnerUpScore"))
    frames = _num(cand.get("frames"))
    reference = cand.get("referenceKind")

    metrics = {"hero": hero, "score": score, "runnerUpScore": runner_up,
               "frames": int(frames) if frames is not None else None,
               "clusterId": cand.get("clusterId"),
               "referenceKind": reference,
               "officialOpinion": (cand.get("officialOpinion") or {}).get("hero")}

    if not hero:
        return _verdict(
            GATE_TEMPLATES, False, "no_candidate_hero",
            "cluster produced no hero candidate to label",
            metrics=metrics, floors=used, now=now)

    # The reference-kind check is what keeps the "score against real
    # portraits" design decision enforceable rather than aspirational.
    if reference not in (None, "labeled-portrait"):
        return _verdict(
            GATE_TEMPLATES, False, "wrong_reference_kind",
            f"candidate was scored against {reference!r}, not a labelled "
            f"broadcast portrait — official art suggests, it never decides",
            metrics=metrics, floors=used, now=now)

    if frames is None or frames < min_frames:
        return _verdict(
            GATE_TEMPLATES, False, "too_few_frames",
            f"cluster is backed by "
            f"{int(frames) if frames is not None else 'an unknown number of'} "
            f"frame(s), under the {min_frames}-frame floor",
            metrics=metrics, floors=used, now=now)

    if score is None or score < min_score:
        return _verdict(
            GATE_TEMPLATES, False, "score_below_floor",
            f"best portrait match {score if score is not None else 'n/a'} "
            f"is below the {min_score} floor for {hero}",
            metrics=metrics, floors=used, now=now)

    # No runner-up at all means nothing else was close — full margin.
    margin = score if runner_up is None else round(score - runner_up, 4)
    metrics["margin"] = margin
    if margin < min_margin:
        return _verdict(
            GATE_TEMPLATES, False, "margin_below_floor",
            f"{hero} beat the runner-up by only {margin:g}, under the "
            f"{min_margin} margin floor — too close to label unattended",
            metrics=metrics, floors=used, now=now)

    official = cand.get("officialOpinion") or {}
    official_hero = official.get("hero")
    if official_hero and str(official_hero).lower() != str(hero).lower():
        return _verdict(
            GATE_TEMPLATES, False, "official_art_contradicts",
            f"portrait match says {hero} but official-art scoring says "
            f"{official_hero} — two sources naming different heroes is a "
            f"hold, not a coin flip",
            metrics=metrics, floors=used, now=now)

    return _verdict(
        GATE_TEMPLATES, True, "portrait_match_clear",
        f"{hero} matched a labelled portrait at {score:g} "
        f"(margin {margin:g}) across {int(frames)} frames"
        + (f", official art concurs" if official_hero else ""),
        metrics=metrics, floors=used, now=now)


def evaluate_detection_gate(detection: dict | None, *,
                            floors: dict | None = None,
                            now: str | None = None) -> Verdict:
    """Gate 3 — may this detection's compositions reach production?

    Judged on THIS run's own calibration health, not on the layout's
    historical reputation. A calibration can score well in isolation and
    still drift on a different capture of the same broadcast; `ingest_map`
    already measures that per-run, and this gate reads those exact metrics
    against a stricter bar than the "suspect" warning uses.

    All four conditions are required. The three ratios can all look healthy
    on a tiny sample, which is what the gameplay-frame floor is for.
    """
    fl = floors or floor_values()
    max_unknown = fl["auto_detect_max_unknown_rate"]
    min_full = fl["auto_detect_min_full_house"]
    min_median = fl["auto_detect_min_median_score"]
    min_frames = fl["auto_detect_min_gameplay_frames"]
    used = {"auto_detect_max_unknown_rate": max_unknown,
            "auto_detect_min_full_house": min_full,
            "auto_detect_min_median_score": min_median,
            "auto_detect_min_gameplay_frames": min_frames}

    det = detection or {}
    stats = det.get("stats") or {}
    health = stats.get("calibration_health") or {}
    m = health.get("metrics") or {}

    unknown = _num(m.get("unknown_rate"))
    full_house = _num(m.get("full_house_rate"))
    median = _num(m.get("median_top_score"))
    frames = _num(m.get("gameplay_frames"))
    if frames is None:
        frames = _num(stats.get("gameplay_frames"))

    metrics = {"unknownRate": unknown, "fullHouseRate": full_house,
               "medianTopScore": median,
               "gameplayFrames": int(frames) if frames is not None else None,
               "healthStatus": health.get("status"),
               "ingestId": det.get("ingestId")}

    if not det:
        return _verdict(
            GATE_DETECTION, False, "no_detection_recorded",
            "no detection summary on the job — nothing to judge",
            metrics=metrics, floors=used, now=now)

    if not m:
        return _verdict(
            GATE_DETECTION, False, "no_calibration_health",
            "this detection run recorded no calibration-health metrics, so "
            "its own reliability is unmeasured — refusing",
            metrics=metrics, floors=used, now=now)

    failures: list[str] = []
    if frames is None or frames < min_frames:
        failures.append(
            f"{int(frames) if frames is not None else 'unknown'} gameplay "
            f"frames (< {min_frames})")
    if unknown is None or unknown > max_unknown:
        failures.append(
            f"unknown rate {unknown if unknown is not None else 'n/a'} "
            f"(> {max_unknown})")
    if full_house is None or full_house < min_full:
        failures.append(
            f"full-house rate {full_house if full_house is not None else 'n/a'} "
            f"(< {min_full})")
    if median is None or median < min_median:
        failures.append(
            f"median score {median if median is not None else 'n/a'} "
            f"(< {min_median})")

    if failures:
        return _verdict(
            GATE_DETECTION, False, "health_below_floor",
            "this run's detection health is below the unattended floor: "
            + "; ".join(failures),
            metrics=metrics, floors=used, now=now)

    return _verdict(
        GATE_DETECTION, True, "health_above_floor",
        f"detection health clears every floor: unknown {unknown:g} "
        f"<= {max_unknown}, full-house {full_house:g} >= {min_full}, "
        f"median {median:g} >= {min_median}, {int(frames)} gameplay frames",
        metrics=metrics, floors=used, now=now)


def evaluate_publish_gate(job_payload: dict | None, *,
                          floors: dict | None = None,
                          now: str | None = None) -> Verdict:
    """Gate 4 — may this job's committed detection be published unattended?

    Publication here means what it has always meant: a scoped commit on a
    fresh branch, pushed. Never main, never a merge. The gate therefore
    checks that there is something real to publish and that the detection
    behind it cleared its OWN gate — an automatic publish riding on a
    detection a human had to rescue would be laundering the weaker decision
    through the stronger one.
    """
    fl = floors or floor_values()
    min_stints = fl["auto_publish_min_stints"]
    used = {"auto_publish_min_stints": min_stints}
    payload = job_payload or {}
    det = payload.get("detection") or {}
    db = det.get("db") or {}
    stints = _num(db.get("stints"))

    metrics = {"written": bool(det.get("written")),
               "stints": int(stints) if stints is not None else None,
               "ingestId": det.get("ingestId")}

    if not det:
        return _verdict(
            GATE_PUBLISH, False, "no_detection_recorded",
            "no detection on the job — nothing to publish",
            metrics=metrics, floors=used, now=now)

    if not det.get("written"):
        return _verdict(
            GATE_PUBLISH, False, "detection_not_committed",
            "detection has not been committed (write=False) — a candidate "
            "pass is not publishable",
            metrics=metrics, floors=used, now=now)

    if stints is None or stints < min_stints:
        return _verdict(
            GATE_PUBLISH, False, "no_stints",
            f"committed detection produced "
            f"{int(stints) if stints is not None else 'no recorded'} hero "
            f"stint(s), under the {min_stints} floor — publishing an empty "
            f"result is worse than publishing nothing",
            metrics=metrics, floors=used, now=now)

    # The detection gate's own verdict, as recorded by the autopilot.
    prior = ((payload.get("autoApprovals") or {}).get(GATE_DETECTION)) or {}
    metrics["detectionGate"] = prior.get("reasonCode")
    if not prior:
        return _verdict(
            GATE_PUBLISH, False, "detection_gate_not_recorded",
            "no detection-gate verdict is recorded on this job, so the "
            "quality of what would be published is unestablished",
            metrics=metrics, floors=used, now=now)
    if not prior.get("passed"):
        return _verdict(
            GATE_PUBLISH, False, "detection_gate_failed",
            f"the detection gate did not pass "
            f"({prior.get('reasonCode')}) — an automatic publish may not "
            f"inherit approval from a detection that needed a human",
            metrics=metrics, floors=used, now=now)

    return _verdict(
        GATE_PUBLISH, True, "committed_with_stints",
        f"committed detection with {int(stints)} hero stint(s) and a passing "
        f"detection gate — publishing to a fresh branch",
        metrics=metrics, floors=used, now=now)


# ------------------------------------------------------------- flag plumbing
@dataclass
class GateSettings:
    """Which gates may be cleared automatically on this run.

    Defaults are all-off: constructing this with no arguments reproduces the
    fully supervised behaviour exactly.
    """
    source: bool = False
    layout: bool = False
    templates: bool = False
    detection: bool = False
    publish: bool = False

    @classmethod
    def from_args(cls, args: Any) -> "GateSettings":
        """Read the CLI flags off an argparse namespace. `--unattended`
        turns on every gate; an individual flag can also be passed alone."""
        unattended = bool(getattr(args, "unattended", False))
        return cls(
            source=unattended or bool(getattr(args, "auto_source", False)),
            layout=unattended or bool(getattr(args, "auto_layout", False)),
            templates=unattended or bool(getattr(args, "auto_templates", False)),
            detection=unattended or bool(getattr(args, "auto_detect", False)),
            publish=unattended or bool(getattr(args, "auto_publish", False)),
        )

    def enabled(self, gate: str) -> bool:
        return bool(getattr(self, gate, False))

    def any_enabled(self) -> bool:
        return any(self.enabled(g) for g in ALL_GATES)

    def enabled_gates(self) -> list[str]:
        return [g for g in ALL_GATES if self.enabled(g)]

    def to_payload(self) -> dict[str, bool]:
        return {g: self.enabled(g) for g in ALL_GATES}


def describe_floors(cfg: Any = None, settings: GateSettings | None = None
                    ) -> list[dict[str, Any]]:
    """Rows for `unattended-floors`, grouped by gate in pipeline order."""
    resolved = resolve_floors(cfg)
    rows: list[dict[str, Any]] = []
    for gate in ALL_GATES:
        for key, info in resolved.items():
            if info["gate"] != gate:
                continue
            rows.append({
                "gate": gate,
                "flag": GATE_FLAGS[gate],
                "enabled": settings.enabled(gate) if settings else False,
                "key": key,
                **info,
            })
    return rows


def format_floors(cfg: Any = None, settings: GateSettings | None = None) -> str:
    """Human-readable `unattended-floors` output: the live value of every
    floor and whether it came from config or from the built-in default."""
    rows = describe_floors(cfg, settings)
    lines: list[str] = []
    current = None
    for row in rows:
        if row["gate"] != current:
            current = row["gate"]
            state = ("ENABLED" if row["enabled"] else "off")
            lines.append("")
            lines.append(f"{current.upper()}  ({row['flag']}, {state})")
        lines.append(
            f"  {row['key']:<38} {str(row['value']):>8}  [{row['source']}]")
        lines.append(f"      {row['description']}")
    lines.append("")
    lines.append("Every floor above is a REFUSAL threshold: a gate opens only "
                 "when its metrics clear the bar.")
    lines.append("Anything not listed here is not automatable — it stops for "
                 "a human by construction.")
    return "\n".join(lines).lstrip("\n")
