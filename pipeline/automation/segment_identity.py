"""
segment_identity.py — propose as much segment identity as the evidence
supports, BEFORE a human approves anything (Phase 4).

Segment approval used to demand map, mode, teams, side and order from the
operator by hand. This module proposes all of it from the broadcast itself,
with evidence, so approval becomes "accept or correct" instead of "type it
all in". What it never does is decide: a proposal is stored in
`map_segments.proposals` and mirrored into `ingest_findings`; only
`segmentation.approve_segment` writes the confirmed values, and only a human
calls that.

Signals, each delegated to the module that owns it:
  map + mode      -> map_identify.identify_map (OCR consensus, catalog mode)
  teams           -> team_identify.identify_teams (OCR consensus per side)
  players         -> player_identify.identify_players (nameplate consensus)
  side continuity -> gameplay_state.side_hue across the segment, so a
                     mid-match side swap is DETECTED rather than assumed
  map order       -> chronological position among this video's segments

Every field carries {value, source, confidence, evidence, reason}. Nothing is
ever left implicitly UNKNOWN: a signal with no answer says so, with why, and a
signal whose evidence contradicts itself becomes an explicit review task.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_DIR = os.path.dirname(_HERE)
REPO_ROOT = os.path.dirname(_PIPELINE_DIR)
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

import capture  # noqa: E402
import cv2  # noqa: E402
import gameplay_state as gs  # noqa: E402
import map_identify as mid  # noqa: E402
import player_identify as pid  # noqa: E402
import team_identify as tid  # noqa: E402

# Every identity signal this module is responsible for. Used to guarantee the
# output NEVER silently omits one — a missing key would read as "not
# applicable" when the truth is "unknown".
SIGNALS = ("map", "mode", "teamA", "teamB", "sideAssignment", "mapOrder",
           "players")

# A side-hue shift this large across a segment, persisting on both sides, is
# treated as a possible side swap worth a human's attention.
SIDE_HUE_SWAP_DELTA = 25.0


def log(msg: str) -> None:
    print(f"[identity] {msg}", flush=True)


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _field(value, *, source: str, confidence: float, reason: str,
           evidence=None) -> dict:
    """One proposed field. `source`/`confidence`/`evidence` are mandatory by
    construction — an inferred value without provenance cannot be built."""
    return {"value": value, "source": source,
            "confidence": round(float(confidence), 3), "reason": reason,
            "evidence": evidence or []}


def _unknown(*, source: str, reason: str, evidence=None) -> dict:
    return _field(None, source=source, confidence=0.0, reason=reason,
                  evidence=evidence)


# ------------------------------------------------------------ side continuity
def side_continuity(frames_bgr_with_time: list[tuple[float, "object"]],
                    layout: dict) -> dict:
    """Track each side's chip-row hue across a segment.

    A stable hue per side means the same team held that screen side for the
    whole window. A persistent jump on BOTH sides is the signature of a side
    swap, and this reports it as a candidate — it never rewrites the team
    assignment itself, because a swap changes which team is on which side and
    that is exactly the kind of call a human must confirm.
    """
    series = {"a": [], "b": []}
    for t, frame in frames_bgr_with_time:
        for side in ("a", "b"):
            hue = gs.side_hue(frame, layout, side)
            if hue is not None:
                series[side].append({"t": t, "hue": round(hue, 1)})
    out = {"series": series, "swapCandidate": False, "reason": ""}
    if len(series["a"]) < 2 or len(series["b"]) < 2:
        out["reason"] = ("not enough chip-row hue samples to judge side "
                         "continuity")
        return out
    first_a, last_a = series["a"][0]["hue"], series["a"][-1]["hue"]
    first_b, last_b = series["b"][0]["hue"], series["b"][-1]["hue"]
    delta_a = abs(last_a - first_a)
    delta_b = abs(last_b - first_b)
    crossed = (abs(last_a - first_b) < SIDE_HUE_SWAP_DELTA / 2
               and abs(last_b - first_a) < SIDE_HUE_SWAP_DELTA / 2)
    if delta_a >= SIDE_HUE_SWAP_DELTA and delta_b >= SIDE_HUE_SWAP_DELTA and crossed:
        out["swapCandidate"] = True
        out["reason"] = (
            f"both chip rows changed hue and CROSSED (a: {first_a}->{last_a}, "
            f"b: {first_b}->{last_b}) — the teams may have swapped sides "
            f"inside this window; split the segment or confirm the assignment")
    elif delta_a >= SIDE_HUE_SWAP_DELTA or delta_b >= SIDE_HUE_SWAP_DELTA:
        out["reason"] = (
            f"one side's hue drifted (a: {first_a}->{last_a}, "
            f"b: {first_b}->{last_b}) without a crossover — usually lighting "
            f"or an ult-charge tint, not a side swap")
    else:
        out["reason"] = (f"both chip rows stable (a: {first_a}->{last_a}, "
                         f"b: {first_b}->{last_b}) — one side assignment holds "
                         f"for the whole window")
    return out


# ------------------------------------------------------------- map ordering
def propose_map_order(con: sqlite3.Connection, video_id: str,
                      segment_id: int, start_time: float) -> dict:
    """Chronological position of this segment among the same video's
    non-rejected segments. Time order in a broadcast IS map order, so this is
    a fact rather than an inference — but it is still recorded with its
    evidence, because a rejected/merged sibling changes it."""
    rows = list(con.execute(
        """SELECT id, start_time, review_status FROM map_segments
           WHERE video_id = ? AND review_status NOT IN
                 ('rejected', 'invalid', 'split', 'merged')
           ORDER BY start_time ASC""", (video_id,)))
    peers = [r for r in rows]
    order = None
    for i, r in enumerate(peers, start=1):
        if r["id"] == segment_id:
            order = i
            break
    if order is None:
        return _unknown(source="chronological-position",
                        reason=(f"segment {segment_id} is not among this "
                                f"video's live segments (rejected or merged?)"))
    return _field(order, source="chronological-position", confidence=1.0,
                  reason=(f"{order} of {len(peers)} live segment(s) for video "
                          f"{video_id}, ordered by broadcast start time"),
                  evidence=[{"segmentId": r["id"], "start": r["start_time"],
                             "status": r["review_status"]} for r in peers])


# ---------------------------------------------------------------- proposals
def propose_identity(con: sqlite3.Connection, content_con, segment: dict, *,
                     layout: dict, frames: list[tuple[float, str]],
                     read_fn, match_id: str | None = None,
                     known_teams: list[dict] | None = None,
                     known_maps: list[dict] | None = None,
                     roster: list[dict] | None = None) -> dict:
    """Build the full identity proposal for ONE segment.

    `frames` are (offset_seconds, png_path) sample frames from inside the
    segment window, taken from the SCAN PROXY. `read_fn` is the injected OCR
    reader (frame_bgr -> [{text, conf, box}]).

    Returns a dict with one entry per SIGNALS key, plus `reviewTasks` for
    every disagreement and `generatedAt`/`framesUsed` provenance. Writes
    nothing — `store_proposals` persists it.
    """
    frames_bgr: list[tuple[float, object]] = []
    for t, path in frames:
        img = cv2.imread(path)
        if img is not None:
            frames_bgr.append((t, img))
    if not frames_bgr:
        return {
            "generatedAt": _utcnow_iso(), "framesUsed": 0,
            **{k: _unknown(source="none",
                           reason="no readable frame inside this segment window")
               for k in SIGNALS},
            "reviewTasks": [{"kind": "segment_identity", "severity": "blocking",
                             "reason": "no readable frames — cannot propose "
                                       "any identity for this segment"}],
        }

    fh, fw = frames_bgr[0][1].shape[:2]
    scaled, scale_info = capture.scale_layout_to_frame(layout, fw, fh)
    ocr_per_frame = [(t, read_fn(img)) for t, img in frames_bgr]

    known_maps = (known_maps if known_maps is not None
                  else mid.known_maps_from_db(content_con))
    known_teams = (known_teams if known_teams is not None
                   else tid.known_teams_from_db(content_con))

    # --- map + mode -------------------------------------------------------
    map_res = mid.identify_map(ocr_per_frame, known_maps, layout=scaled,
                              fw=fw, fh=fh)
    if map_res.get("map"):
        map_field = _field(map_res["map"], source="ocr-consensus",
                           confidence=map_res["confidence"],
                           reason=map_res["reason"],
                           evidence=map_res.get("evidence"))
        mode_field = _field(map_res["mode"], source="map-catalog",
                            confidence=1.0,
                            reason=(f"mode of {map_res['map']} from the "
                                    f"game_maps catalog (never OCR'd "
                                    f"independently)"))
    else:
        map_field = _unknown(source="ocr-consensus", reason=map_res["reason"],
                             evidence=map_res.get("evidence"))
        mode_field = _unknown(source="map-catalog",
                              reason="mode follows the map; the map is UNKNOWN")

    # --- teams ------------------------------------------------------------
    teams = tid.identify_teams(ocr_per_frame, ocr_per_frame, scaled,
                              known_teams, fw, fh)
    team_fields = {}
    for side, key in (("a", "teamA"), ("b", "teamB")):
        r = teams[side]
        if r.get("team"):
            team_fields[key] = _field(
                r["team"], source="ocr-consensus", confidence=r["confidence"],
                reason=r["reason"],
                evidence=[{"raw": r.get("raw_text"), "frames": r["n_frames"],
                           "method": r.get("method")}])
        else:
            team_fields[key] = _unknown(
                source="ocr-consensus", reason=r["reason"],
                evidence=r.get("unresolved") or [])

    # --- side continuity --------------------------------------------------
    continuity = side_continuity(frames_bgr, scaled)
    if team_fields["teamA"]["value"] and team_fields["teamB"]["value"]:
        side_field = _field(
            "team_a_left",
            source="ocr-side-zones+hue-continuity",
            confidence=min(team_fields["teamA"]["confidence"],
                           team_fields["teamB"]["confidence"]),
            reason=(f"{team_fields['teamA']['value']} read on the LEFT side "
                    f"zone and {team_fields['teamB']['value']} on the RIGHT; "
                    f"{continuity['reason']}"),
            evidence=[continuity["series"]])
        if continuity["swapCandidate"]:
            side_field["value"] = None
            side_field["confidence"] = 0.0
            side_field["reason"] = continuity["reason"]
    else:
        side_field = _unknown(
            source="ocr-side-zones+hue-continuity",
            reason=("side assignment follows the team identities; at least one "
                    "team is UNKNOWN. " + continuity["reason"]),
            evidence=[continuity["series"]])

    # --- map order --------------------------------------------------------
    order_field = propose_map_order(con, segment["video_id"], segment["id"],
                                    segment["start_time"])

    # --- players ----------------------------------------------------------
    if roster is None:
        team_ids = [f["value"] for f in (team_fields["teamA"],
                                        team_fields["teamB"]) if f["value"]]
        roster = pid.known_players_from_db(
            content_con, team_ids=team_ids or None,
            match_id=match_id or segment.get("candidate_match_id"))
    players = pid.identify_players(ocr_per_frame, scaled, roster, fw=fw, fh=fh)
    players_field = _field(
        players["slots"], source="nameplate-ocr-consensus",
        confidence=round(players["resolved"] / max(len(players["slots"]), 1), 3),
        reason=(f"{players['resolved']} of {len(players['slots'])} nameplates "
                f"matched a known roster entry; "
                f"{len(players['candidatePlayers'])} unmatched candidate(s) "
                f"recorded without creating any player"),
        evidence=players["candidatePlayers"])

    proposal = {
        "generatedAt": _utcnow_iso(),
        "framesUsed": len(frames_bgr),
        "frameSize": [fw, fh],
        "layoutScaling": scale_info.get("note"),
        "map": map_field, "mode": mode_field,
        "teamA": team_fields["teamA"], "teamB": team_fields["teamB"],
        "sideAssignment": side_field, "mapOrder": order_field,
        "players": players_field,
        "sideContinuity": continuity,
        "candidatePlayers": players["candidatePlayers"],
    }

    # --- review tasks: every disagreement, named --------------------------
    tasks: list[dict] = []
    if map_res.get("disagreement"):
        tasks.append({"kind": "map_identity", "severity": "blocking",
                      "reason": map_res.get("modeConflict") or map_res["reason"]})
    if map_res.get("weakCandidate"):
        tasks.append({"kind": "map_identity", "severity": "advisory",
                      "reason": (f"weak map candidate "
                                 f"{map_res['weakCandidate']}: "
                                 f"{map_res['reason']}")})
    for key in ("teamA", "teamB"):
        if not proposal[key]["value"]:
            tasks.append({"kind": "team_identity", "severity": "blocking",
                          "reason": f"{key} UNKNOWN: {proposal[key]['reason']}"})
    if (proposal["teamA"]["value"]
            and proposal["teamA"]["value"] == proposal["teamB"]["value"]):
        tasks.append({"kind": "team_identity", "severity": "blocking",
                      "reason": (f"both side zones read the same team "
                                 f"({proposal['teamA']['value']}) — a match "
                                 f"never has one team on both sides")})
    if continuity["swapCandidate"]:
        tasks.append({"kind": "side_assignment", "severity": "blocking",
                      "reason": continuity["reason"]})
    if not proposal["map"]["value"]:
        tasks.append({"kind": "map_identity", "severity": "blocking",
                      "reason": f"map UNKNOWN: {proposal['map']['reason']}"})
    if players["duplicates"]:
        tasks.append({"kind": "player_identity", "severity": "advisory",
                      "reason": (f"nameplate OCR gave the same player two "
                                 f"slots: {players['duplicates']}")})
    if players["candidatePlayers"]:
        tasks.append({"kind": "player_identity", "severity": "advisory",
                      "reason": (f"{len(players['candidatePlayers'])} nameplate(s) "
                                 f"matched nobody on the roster — add the "
                                 f"player deliberately or correct the roster; "
                                 f"nothing was created")})
    proposal["reviewTasks"] = tasks
    proposal["identityStatus"] = (
        "blocked" if any(t["severity"] == "blocking" for t in tasks)
        else "proposed" if any(proposal[k]["value"] is not None
                               for k in ("map", "teamA", "teamB"))
        else "unknown")

    # Guarantee: no signal is ever silently absent.
    missing = [k for k in SIGNALS if k not in proposal]
    if missing:
        raise AssertionError(f"identity proposal omitted signal(s): {missing}")
    return proposal


# ------------------------------------------------------------------ storage
def store_proposals(con: sqlite3.Connection, segment_id: int,
                    proposal: dict) -> None:
    """Persist a proposal onto the segment row. Deliberately stored beside —
    never instead of — the human-confirmed columns, so `approve_segment`
    remains the only writer of truth."""
    con.execute(
        """UPDATE map_segments SET proposals = ?, identity_status = ?,
               updated_at = ? WHERE id = ?""",
        (json.dumps(proposal), proposal.get("identityStatus"),
         _utcnow_iso(), segment_id))
    con.commit()


def load_proposals(con: sqlite3.Connection, segment_id: int) -> dict | None:
    row = con.execute("SELECT proposals FROM map_segments WHERE id = ?",
                      (segment_id,)).fetchone()
    if not row or not row["proposals"]:
        return None
    try:
        return json.loads(row["proposals"])
    except ValueError:
        return None


FINDING_KIND = "segment_identity"


def record_findings(content_con, ingest_id: str, proposal: dict) -> int:
    """Mirror a proposal into `ingest_findings` — the content DB's existing
    audit table for inferred fields — one row per signal, each with its
    source, confidence and evidence.

    Idempotent for a given ingest_id: this run's rows are deleted and
    rewritten, exactly the pattern `ingest_map.write_db` uses, so re-running
    identity never accumulates duplicates.
    """
    content_con.execute(
        "DELETE FROM ingest_findings WHERE ingest_id=? AND kind=?",
        (ingest_id, FINDING_KIND))
    n = 0
    for signal in SIGNALS:
        field = proposal.get(signal) or {}
        value = field.get("value")
        # `players` is a list of ten slot verdicts; store it as JSON in
        # `value` and keep the human-readable count in raw_text.
        stored = (value if isinstance(value, (str, type(None)))
                  else json.dumps(value))
        raw = field.get("reason")
        if signal == "players" and isinstance(value, list):
            resolved = sum(1 for s in value if s.get("player"))
            raw = f"{resolved}/{len(value)} nameplates matched a known roster"
        content_con.execute(
            """INSERT INTO ingest_findings
               (ingest_id, kind, field, raw_text, value, confidence, method,
                status, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ingest_id, FINDING_KIND, signal, (raw or "")[:2000],
             (stored or "")[:8000] if stored is not None else None,
             field.get("confidence"), field.get("source"),
             "unknown" if value in (None, [], {}) else "proposed",
             json.dumps({"reason": field.get("reason"),
                         "evidence": field.get("evidence")})[:8000]))
        n += 1
    content_con.commit()
    return n


def accept_proposed(con: sqlite3.Connection, segment_id: int, *,
                    reviewer_note: str | None = None,
                    layout_id: str | None = None) -> dict:
    """"Accept proposed" — approve a segment using the values the machine
    proposed, in ONE action instead of retyping them.

    Refuses unless every field approval needs is actually proposed, and
    refuses outright when the proposal carries a blocking review task. This
    is still a human action (a person ran it); it just spares them the
    typing. It calls the SAME `segmentation.approve_segment` gate — there is
    no second, looser approval path.
    """
    from . import segmentation as seg
    proposal = load_proposals(con, segment_id)
    if not proposal:
        raise ValueError(f"segment {segment_id} has no identity proposal — "
                         f"run identity proposal first")
    blocking = [t for t in proposal.get("reviewTasks", [])
                if t["severity"] == "blocking"]
    if blocking:
        raise ValueError(
            f"refusing to accept a blocked proposal for segment {segment_id}: "
            + "; ".join(t["reason"] for t in blocking))
    missing = [k for k in ("map", "mode", "teamA", "teamB", "sideAssignment",
                           "mapOrder")
               if not (proposal.get(k) or {}).get("value")]
    if missing:
        raise ValueError(
            f"cannot accept proposal for segment {segment_id}: "
            f"{', '.join(missing)} still UNKNOWN — edit them by hand instead")
    seg_row = seg.get_segment(con, segment_id)
    resolved_layout = layout_id or (seg_row or {}).get("layout_id")
    if not resolved_layout:
        raise ValueError(
            f"segment {segment_id} has no layout_id and none was supplied — "
            f"approval always records which broadcast layout was used")
    note = reviewer_note or (
        "accepted the automatic identity proposal "
        f"(map {proposal['map']['confidence']:.0%}, "
        f"teams {min(proposal['teamA']['confidence'], proposal['teamB']['confidence']):.0%})")
    return seg.approve_segment(
        con, segment_id,
        map_order=int(proposal["mapOrder"]["value"]),
        map_name=proposal["map"]["value"],
        map_mode=proposal["mode"]["value"],
        team_a=proposal["teamA"]["value"],
        team_b=proposal["teamB"]["value"],
        side_assignment=proposal["sideAssignment"]["value"],
        layout_id=resolved_layout, reviewer_note=note)


def format_proposal(proposal: dict) -> str:
    lines = [f"  frames used   : {proposal.get('framesUsed')}",
             f"  status        : {proposal.get('identityStatus')}"]
    for key in SIGNALS:
        f = proposal.get(key) or {}
        if key == "players":
            slots = f.get("value") or []
            resolved = sum(1 for s in slots if s.get("player"))
            lines.append(f"  players       : {resolved}/{len(slots)} resolved "
                         f"(conf {f.get('confidence')}) — {f.get('reason')}")
            for s in slots:
                mark = s.get("player") or "UNKNOWN"
                lines.append(f"    {s['side']}{s['index']}: {mark} "
                             f"— {s.get('reason')}")
            continue
        value = f.get("value")
        lines.append(f"  {key:<14}: {value if value is not None else 'UNKNOWN'} "
                     f"[{f.get('source')}] conf={f.get('confidence')}")
        lines.append(f"                  {f.get('reason')}")
    for t in proposal.get("reviewTasks", []):
        lines.append(f"  {t['severity'].upper()}: [{t['kind']}] {t['reason']}")
    return "\n".join(lines)
