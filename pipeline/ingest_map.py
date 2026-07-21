#!/usr/bin/env python3
"""
ingest_map.py — full-map ingestion: one map of one VOD → auditable timeline.

Pipeline (staged, idempotent, evidence-first):
  1. BASELINE pass: sample the map window every N seconds, classify each
     frame's gameplay state (gameplay_state.classify_frame), and read all
     ten slots with detect.read_slot (top + runner-up + margin + UNKNOWN).
     Non-gameplay frames become skipped observations with reasons — they
     can never touch the composition timeline.
  2. DENSE pass: wherever consecutive accepted reads disagree on a slot's
     hero (a suspected swap) the window is resampled at 1 s to pin the
     earliest defensible transition time.
  3. TEMPORAL CONSENSUS: per-slot state machine with hysteresis — a hero
     is established/replaced only after CONFIRM_N consecutive agreeing
     accepted observations; isolated contradictions are recorded as
     REJECTED suspected swaps (with evidence), never as swaps.
  4. ROUNDS + SIDES: gameplay-coverage gaps split control rounds; per-side
     chip hue + comp-crossover checks keep team identity attached to the
     TEAM, not the screen side, across rounds.
  5. PERSIST: JSONL observations + JSON stints/swaps/rounds + evidence
     crops under reports/ingest/<ingest-id>/; with --write the same data
     lands in ingest_runs / slot_observations / map_rounds / hero_stints /
     hero_swaps keyed by (match, map, detector_version) so reruns REPLACE
     their own rows and never duplicate. Manual/reviewed rows are never
     touched.

Usage (see HANDOFF.md "Current status" for the full workflow):
  python pipeline/ingest_map.py --clip work/clips/nepal_720p.mp4 \
    --clip-offset 1800 --start 1843 --end 2774 \
    --layout layouts/owcs_jksix_qwc.json --source-id owcs-jksix-qwc \
    --ingest-id qad-tm-nepal --match m-qad-tm-s2po --map-order 1 \
    --team-a qadsiah --team-b twisted-minds --every 5 --write
"""
from __future__ import annotations
import argparse
import json
import os
import statistics
import subprocess
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import capture  # noqa: E402
import detect  # noqa: E402
import gameplay_state  # noqa: E402
import comp_solver  # noqa: E402

DETECTOR_VERSION = "det-v2"
CONFIRM_N = 3             # consecutive accepted obs to (re)establish a hero
CONFIRM_STRONG = 2        # ...or this many when every score >= STRONG_SCORE
STRONG_SCORE = 0.80
DENSE_STEP = 1.0          # seconds between dense-pass samples
ROUND_GAP = 25.0          # non-gameplay gap that splits control rounds
MIN_ROUND_LEN = 45.0
AUTO_HIGH_MEAN = 0.70     # stint auto-promotion floors
AUTO_HIGH_MIN = 0.60
HUE_SWAP_TOL = 12.0       # chip-hue distance that still reads as "same team"

# POST-UNLOCK GRACE WINDOW — players can still be speed-boosting or
# teleporting out of spawn for several seconds after a control point
# unlocks, sometimes still finishing a hero swap queued from the setup
# phase. A hero establishing itself (opener OR a swap) in the first
# POST_UNLOCK_GRACE seconds of a round is temporally real (it passed the
# normal consensus checks) but not yet trustworthy AS THE SETTLED PICK —
# it is capped at 'needs-review' unless the SAME hero is still being read
# POST_UNLOCK_RECHECK seconds later, i.e. it survived the shaky window.
# Dense sampling is forced through both windows (post_unlock_windows)
# specifically so this recheck always has data to work with.
POST_UNLOCK_GRACE = 30.0
POST_UNLOCK_RECHECK = 30.0


def log(msg: str) -> None:
    print(f"[ingest] {msg}", flush=True)


# ------------------------------------------------------------ frame supply
class FrameServer:
    """Extract + cache frames from the local clip by STREAM offset."""

    def __init__(self, clip: str, clip_offset: float, frames_dir: str):
        self.clip = clip
        self.clip_offset = clip_offset
        self.dir = frames_dir
        os.makedirs(frames_dir, exist_ok=True)
        self._cache: dict[float, str | None] = {}

    def path_for(self, t: float) -> str:
        return os.path.join(self.dir, f"t{t:09.1f}.jpg")

    def get(self, t: float) -> str | None:
        """Frame at stream offset t (seconds) or None past clip end."""
        t = round(t, 1)
        if t in self._cache:
            return self._cache[t]
        out = self.path_for(t)
        if not os.path.exists(out):
            ct = t - self.clip_offset
            if ct < 0:
                self._cache[t] = None
                return None
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
                   "-ss", f"{ct:.3f}", "-i", self.clip, "-frames:v", "1",
                   "-q:v", "2", "-y", out]
            subprocess.run(cmd, check=False)
        self._cache[t] = out if os.path.exists(out) else None
        return self._cache[t]

    def extract_baseline(self, start: float, end: float, every: float) -> list[float]:
        """Bulk-extract the baseline ladder in ONE ffmpeg call.

        Reruns reuse already-extracted frames (idempotent, and immune to
        a transient ffmpeg crash on the bulk pass)."""
        expected = []
        t = start
        while t <= end:
            expected.append(round(t, 1))
            t += every
        if all(os.path.exists(self.path_for(t)) for t in expected):
            for t in expected:
                self._cache[t] = self.path_for(t)
            return expected
        ct = start - self.clip_offset
        dur = end - start
        pattern = os.path.join(self.dir, "base%06d.jpg")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
               "-ss", f"{ct:.3f}", "-t", f"{dur:.3f}", "-i", self.clip,
               "-vf", f"fps=1/{every}", "-q:v", "2", "-start_number", "0",
               "-y", pattern]
        subprocess.run(cmd, check=True)
        ts = []
        i = 0
        while True:
            src = os.path.join(self.dir, f"base{i:06d}.jpg")
            if not os.path.exists(src):
                break
            t = round(start + i * every, 1)
            dst = self.path_for(t)
            if os.path.exists(dst):
                os.remove(src)
            else:
                os.replace(src, dst)
            self._cache[t] = dst
            ts.append(t)
            i += 1
        return ts


# ------------------------------------------------------------- observation
def resolve_sides(slots: dict, hero_roles: dict) -> dict:
    """Role-aware {a,b} comp resolution for one frame's ten slot reads.

    Purely additive: runs comp_solver on each side's five read_slot dicts so
    a slot the matcher left UNKNOWN/ambiguous can be completed from the 1/2/2
    role constraint, and a physically-impossible read is flagged. Returns
    {a: solve(...), b: solve(...)}; callers keep the raw reads untouched."""
    out = {}
    for side in ("a", "b"):
        reads = [slots.get(f"{side}{i}", {"hero": "UNKNOWN", "score": 0.0,
                                          "scores": {}}) for i in range(1, 6)]
        out[side] = comp_solver.solve(reads, hero_roles)
    return out


def observe(t: float, frame_path: str, layout_scaled: dict, lib: dict,
            crops_dir: str, save_crop: bool = True,
            ocr_read_fn=None, ocr_aliases: dict | None = None,
            hero_roles: dict | None = None) -> dict:
    """One frame -> state + (if gameplay) ten slot reads with evidence.

    ocr_read_fn/ocr_aliases (both optional, from --ocr-guard) run OCR
    EXACTLY ONCE per frame here — the resulting items are reused both to
    feed gameplay_state's generalized highlight guard (via a trivial
    wrapper closure, so it never re-invokes the engine) and stashed on
    the observation (key '_ocr', stripped before the JSONL dump like
    every other underscore-prefixed field) for team_identify/detect_bans,
    which need OCR from non-gameplay frames too (pick/ban screens are
    non-gameplay by definition)."""
    frame = cv2.imread(frame_path)
    if frame is None:
        return {"t": t, "state": "unreadable", "reason": "unreadable frame",
                "slots": {}}
    ocr_items = None
    guard_fn = None
    if ocr_read_fn is not None and ocr_aliases is not None:
        try:
            ocr_items = ocr_read_fn(frame)
        except Exception:
            ocr_items = []
        guard_fn = lambda _f, _items=ocr_items: _items      # noqa: E731
    state, reason = gameplay_state.classify_frame(
        frame, layout_scaled, ocr_read_fn=guard_fn,
        ocr_aliases=ocr_aliases if guard_fn else None)
    obs = {"t": t, "state": state, "reason": reason,
           "frame": os.path.basename(frame_path), "slots": {}}
    if ocr_items is not None:
        obs["_ocr"] = ocr_items
    if state != "gameplay":
        return obs
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    for side in ("a", "b"):
        for i, (x, y, w, h) in enumerate(layout_scaled[f"slots_{side}"], 1):
            crop = gray[y:y + h, x:x + w]
            read = detect.read_slot(crop, lib)
            key = f"{side}{i}"
            if save_crop:
                cp = os.path.join(crops_dir, f"t{t:09.1f}_{key}.png")
                if not os.path.exists(cp):
                    cv2.imwrite(cp, frame[y:y + h, x:x + w])
                read["crop"] = os.path.basename(cp)
            obs["slots"][key] = read
    # role-aware resolution (additive; raw reads above are untouched). Lets
    # a UNKNOWN/ambiguous slot be completed from the 1/2/2 constraint and
    # flags impossible comps — recorded for the report + consensus, never
    # overwriting an honest read.
    if hero_roles:
        obs["resolved"] = resolve_sides(obs["slots"], hero_roles)
    for side in ("a", "b"):
        hue = gameplay_state.side_hue(frame, layout_scaled, side)
        if hue is not None:
            obs[f"hue_{side}"] = round(hue, 1)
    emb = layout_scaled.get("round_emblem")
    if emb and emb.get("rect"):
        x, y, w, h = emb["rect"]
        crop = gray[y:y + h, x:x + w]
        if crop.size:
            obs["_emblem"] = cv2.resize(crop, (20, 20)).astype("float32")
    return obs


def slot_track(observations: list[dict], key: str) -> list[dict]:
    """Time-ordered ACCEPTED reads for one slot key ('a1'...'b5')."""
    out = []
    for o in observations:
        if o["state"] != "gameplay":
            continue
        r = o["slots"].get(key)
        if r and r["hero"] not in ("", "UNKNOWN") and r["reject"] is None:
            out.append({"t": o["t"], "hero": r["hero"], "score": r["score"],
                        "margin": r["margin"], "crop": r.get("crop"),
                        "template": r.get("template"),
                        "scores": r.get("scores") or {}})
    return sorted(out, key=lambda r: r["t"])


def change_windows(track: list[dict]) -> list[tuple[float, float]]:
    """(t_before, t_after) spans where the slot's accepted hero changes."""
    wins = []
    for i in range(1, len(track)):
        if track[i]["hero"] != track[i - 1]["hero"]:
            wins.append((track[i - 1]["t"], track[i]["t"]))
    return wins


# ------------------------------------------------------- temporal consensus
SWAP_MIN_SPAN = 3.0       # seconds the new hero must persist
SWAP_MIN_MARGIN = 0.05    # mean top-vs-runner-up margin in the run
SWAP_MIN_DISPLACE = 0.04  # new hero must beat the OLD hero's own score
                          # by this much on average — a dead/variant
                          # rendering of the old hero can't fake this
MAX_RUN_GAP = 20.0        # a candidate run may not bridge a gap this long
                          # — a stale isolated read must not pre-date a
                          # swap that really happened after a break


def round_for(t: float, rounds: list[dict]) -> dict | None:
    """The round whose start is the most recent one at/before t, else None."""
    matches = [r for r in rounds if r["start"] <= t]
    return max(matches, key=lambda r: r["start"]) if matches else None


def grace_ok_for_stint(st: dict) -> bool:
    """False only for a stint that both (a) began inside the post-unlock
    grace window and (b) never persisted to the recheck deadline while the
    round was still long enough for that recheck to have been possible.
    True for every ordinary stint (early_grace not set) and for any grace
    stint the round simply couldn't outlive."""
    if not st.get("early_grace"):
        return True
    rs = st.get("round_start")
    if rs is None:
        return True
    deadline = rs + POST_UNLOCK_GRACE + POST_UNLOCK_RECHECK
    re_ = st.get("round_end")
    if re_ is not None and re_ < deadline:
        return True     # round ended before a recheck was even possible
    return st["end"] >= deadline


def build_stints(track: list[dict], setup_spans: list[tuple[float, float]],
                 rounds: list[dict] | None = None
                 ) -> tuple[list[dict], list[dict]]:
    """Hysteresis walk over one slot's accepted reads.

    Returns (stints, events). Events include CONFIRMED swaps, REJECTED
    suspected swaps (with reasons + evidence) and SETUP-CHANGES (hero
    toggles during a setup phase — comp updates, not in-game swaps).

    `rounds` (optional, [{'start','end','index'}, ...]) tags every stint
    that begins inside POST_UNLOCK_GRACE seconds of its round's start with
    early_grace=True + the round's start/end, so write_db can cap its
    promotion status unless a later recheck corroborates it (see
    POST_UNLOCK_GRACE's docstring above)."""
    rounds = rounds or []
    stints: list[dict] = []
    events: list[dict] = []
    cur = None            # {'hero', 'start', 'obs': [reads]}
    pending: list[dict] = []

    def in_setup(t: float) -> bool:
        return any(s0 - 2 <= t <= s1 + 2 for (s0, s1) in setup_spans)

    def grace_tag(t: float) -> dict:
        r = round_for(t, rounds)
        early = bool(r) and (t - r["start"]) <= POST_UNLOCK_GRACE
        return {"early_grace": early,
                "round_start": r["start"] if r else None,
                "round_end": r["end"] if r else None}

    def confirmed(run: list[dict], against: str | None) -> tuple[bool, str]:
        """(ok, reason-if-not) for a candidate run replacing `against`."""
        n, span = len(run), (run[-1]["t"] - run[0]["t"]) if run else 0.0
        strong = (n >= CONFIRM_STRONG and span >= 2.0
                  and all(r["score"] >= STRONG_SCORE for r in run))
        if n < CONFIRM_N and not strong:
            return False, f"only {n} obs"
        if span < SWAP_MIN_SPAN and not strong:
            return False, f"span {span:.1f}s < {SWAP_MIN_SPAN}s"
        margin = statistics.mean(r["margin"] for r in run)
        if margin < SWAP_MIN_MARGIN:
            return False, f"mean margin {margin:.3f} too small"
        if against:
            disp = [r["score"] - r["scores"].get(against, -1.0)
                    for r in run if r.get("scores")]
            if disp and statistics.mean(disp) < SWAP_MIN_DISPLACE:
                return False, (f"{run[0]['hero']} does not displace "
                               f"{against} (mean displacement "
                               f"{statistics.mean(disp):.3f}) — likely a "
                               f"state variant of {against}")
        return True, ""

    def close_current(end_read):
        if cur and cur["obs"]:
            scores = [r["score"] for r in cur["obs"]]
            stints.append({
                "hero": cur["hero"], "start": cur["obs"][0]["t"],
                "end": end_read["t"] if end_read else cur["obs"][-1]["t"],
                "n_obs": len(cur["obs"]),
                "mean_conf": round(statistics.mean(scores), 3),
                "min_conf": round(min(scores), 3),
                "evidence_start": cur["obs"][0].get("crop"),
                "evidence_end": cur["obs"][-1].get("crop"),
                "early_grace": cur.get("early_grace", False),
                "round_start": cur.get("round_start"),
                "round_end": cur.get("round_end"),
            })

    for read in track:
        if cur is None:
            pending.append(read)
            if len(pending) > 1 and pending[-2]["hero"] != read["hero"]:
                pending = [read]
            if confirmed(pending, None)[0]:
                cur = {"hero": read["hero"], "obs": pending[:],
                      **grace_tag(pending[0]["t"])}
                pending = []
            continue
        if read["hero"] == cur["hero"]:
            if pending:
                # contradiction fizzled — record the rejected suspicion
                events.append({
                    "kind": "rejected-swap", "slot_from": cur["hero"],
                    "candidate": pending[0]["hero"], "t": pending[0]["t"],
                    "n_obs": len(pending),
                    "reason": (f"candidate {pending[0]['hero']} seen "
                               f"{len(pending)}x then {cur['hero']} "
                               "returned — noise, not a swap"),
                    "evidence": [p.get("crop") for p in pending[:3]],
                })
                pending = []
            cur["obs"].append(read)
            continue
        # different hero than current
        if pending and pending[-1]["hero"] != read["hero"]:
            events.append({
                "kind": "rejected-swap", "slot_from": cur["hero"],
                "candidate": pending[0]["hero"], "t": pending[0]["t"],
                "n_obs": len(pending),
                "reason": (f"candidate {pending[0]['hero']} interrupted by "
                           f"{read['hero']} before confirmation"),
                "evidence": [p.get("crop") for p in pending[:3]],
            })
            pending = []
        if pending and read["t"] - pending[-1]["t"] > MAX_RUN_GAP:
            pending = []
        pending.append(read)
        ok, why = confirmed(pending, cur["hero"])
        if ok:
            before = cur["obs"][-1]
            close_current(before)
            kind = ("setup-change" if in_setup(pending[0]["t"])
                    else "swap")
            events.append({
                "kind": kind, "from": cur["hero"],
                "to": read["hero"], "t": pending[0]["t"],
                "confidence": round(statistics.mean(
                    r["score"] for r in pending), 3),
                "margin": round(statistics.mean(
                    r["margin"] for r in pending), 3),
                "n_obs": len(pending),
                "evidence_before": before.get("crop"),
                "evidence_after": pending[0].get("crop"),
                "reason": (f"{read['hero']} persisted "
                           f"{len(pending)} obs "
                           f"({pending[0]['t']:.0f}s-"
                           f"{pending[-1]['t']:.0f}s) while "
                           f"{cur['hero']} no longer detected"
                           + (" [during setup phase — comp change,"
                              " not an in-game swap]"
                              if kind == "setup-change" else "")),
            })
            cur = {"hero": read["hero"], "obs": pending[:],
                  **grace_tag(pending[0]["t"])}
            pending = []
        elif len(pending) >= CONFIRM_N + 2 \
                and (pending[-1]["t"] - pending[0]["t"]) > SWAP_MIN_SPAN:
            # long-lived candidate that still can't confirm — surface it
            events.append({
                "kind": "rejected-swap", "slot_from": cur["hero"],
                "candidate": pending[0]["hero"], "t": pending[0]["t"],
                "n_obs": len(pending),
                "reason": f"persistent candidate rejected: {why}",
                "evidence": [p.get("crop") for p in pending[:3]],
            })
            pending = []
    if pending and cur is not None:
        events.append({
            "kind": "rejected-swap", "slot_from": cur["hero"],
            "candidate": pending[0]["hero"], "t": pending[0]["t"],
            "n_obs": len(pending),
            "reason": (f"candidate {pending[0]['hero']} only seen "
                       f"{len(pending)}x at window end — unresolved, "
                       "needs review"),
            "evidence": [p.get("crop") for p in pending[:3]],
            "unresolved": True,
        })
    elif pending and cur is None and pending[0:1]:
        # never established anything — too little data
        events.append({
            "kind": "unestablished", "candidate": pending[0]["hero"],
            "t": pending[0]["t"], "n_obs": len(pending),
            "reason": "not enough consecutive agreement to establish a hero",
        })
    close_current(None)

    # a stint that spans a round boundary is fine (same hero re-picked);
    # stints shorter than one confirmation at round edges stay as-is —
    # the review page surfaces everything.
    return stints, events


# ------------------------------------------------------------------ rounds
EMBLEM_SIM = 0.60         # correlation to consider the emblem "the same"
MIN_SEG_OBS = 4           # emblem segments shorter than this get absorbed


def _emblem_segments(observations: list[dict]) -> list[dict]:
    """Cluster the center point-emblem over time.

    The emblem is a LOCK during every setup phase and a distinct point
    letter during each combat round, so its cluster timeline IS the round
    structure — no OCR needed. Returns [{'cluster', 'start', 'end',
    'n'}] over gameplay observations that carry an emblem crop."""
    seq = [(o["t"], o["_emblem"]) for o in observations
           if o["state"] == "gameplay" and "_emblem" in o]
    if not seq:
        return []
    protos: list = []
    ids = []
    for (_t, vec) in seq:
        best, best_sim = None, -1.0
        for i, p in enumerate(protos):
            res = cv2.matchTemplate(vec, p, cv2.TM_CCOEFF_NORMED)
            sim = float(res.max())
            if sim > best_sim:
                best_sim, best = sim, i
        if best is not None and best_sim >= EMBLEM_SIM:
            ids.append(best)
        else:
            protos.append(vec)
            ids.append(len(protos) - 1)
    # median-of-3 smoothing kills single-frame flicker (overtime flames)
    sm = ids[:]
    for i in range(1, len(ids) - 1):
        trio = sorted([ids[i - 1], ids[i], ids[i + 1]])
        sm[i] = trio[1]
    segs = []
    for i, cid in enumerate(sm):
        t = seq[i][0]
        if segs and segs[-1]["cluster"] == cid:
            segs[-1]["end"] = t
            segs[-1]["n"] += 1
        else:
            segs.append({"cluster": cid, "start": t, "end": t, "n": 1})
    # absorb tiny segments into the previous one
    out = []
    for s in segs:
        if s["n"] < MIN_SEG_OBS and out:
            out[-1]["end"] = s["end"]
            out[-1]["n"] += s["n"]
        else:
            out.append(s)
    return out


def detect_rounds(observations: list[dict], start: float, end: float
                  ) -> tuple[list[dict], list[dict]]:
    """(rounds, setup_spans) from emblem segments, with a coverage-gap
    fallback when the layout has no round_emblem."""
    segs = _emblem_segments(observations)
    if segs:
        # the map always OPENS in a setup phase, so the first segment's
        # emblem (the lock) identifies every later setup segment; combat
        # rounds render distinct point letters
        setup_ids = {segs[0]["cluster"]}
        rounds, setups = [], []
        for s in segs:
            if s["cluster"] in setup_ids:
                setups.append({"start": s["start"], "end": s["end"]})
            else:
                # adjacent non-setup segments belong to the SAME round —
                # overtime flames etc. can re-cluster the emblem mid-round
                if rounds and not any(
                        st["start"] > rounds[-1]["end"]
                        and st["end"] < s["start"] for st in setups):
                    rounds[-1]["end"] = s["end"]
                else:
                    rounds.append({"start": s["start"], "end": s["end"]})
        rounds = [r for r in rounds
                  if r["end"] - r["start"] >= MIN_ROUND_LEN]
        for i, r in enumerate(rounds, 1):
            r["index"] = i
            r["confidence"] = 0.85
        if rounds:
            return rounds, setups

    # fallback: split on gameplay-coverage gaps
    times = sorted(o["t"] for o in observations if o["state"] == "gameplay")
    if not times:
        return [], []
    rounds = []
    seg_start = prev = times[0]
    for t in times[1:]:
        if t - prev >= ROUND_GAP:
            if prev - seg_start >= MIN_ROUND_LEN:
                rounds.append({"start": seg_start, "end": prev})
            seg_start = t
        prev = t
    if prev - seg_start >= MIN_ROUND_LEN:
        rounds.append({"start": seg_start, "end": prev})
    for i, r in enumerate(rounds, 1):
        r["index"] = i
        r["confidence"] = 0.8 if len(rounds) > 1 else 0.5
    _ = (start, end)
    return rounds, []


def post_unlock_windows(rounds: list[dict], start: float, end: float
                        ) -> set[tuple[float, float]]:
    """Dense-sample windows implementing the post-unlock capture policy:
    guarantee fine-grained coverage right after every round unlock
    (players speed-boosting/teleporting out of spawn can still swap heroes
    there) AND again around the recheck deadline ~60s in (settle
    confirmation) — regardless of whether the coarser baseline sampling
    happened to notice a change there on its own."""
    windows = set()
    for r in rounds:
        rs = r["start"]
        w1 = (max(start, rs), min(end, rs + POST_UNLOCK_GRACE + 2))
        if w1[1] > w1[0]:
            windows.add(w1)
        w2 = (max(start, rs + POST_UNLOCK_GRACE - 2),
              min(end, rs + POST_UNLOCK_GRACE + POST_UNLOCK_RECHECK + 2))
        if w2[1] > w2[0]:
            windows.add(w2)
    return windows


def detect_side_swaps(observations: list[dict], rounds: list[dict]
                      ) -> list[dict]:
    """Per round: does each screen side still hold the same team?

    Evidence = chip hue continuity. Returns per-round mapping decisions:
    [{'index', 'swapped', 'hue_a', 'hue_b', 'note'}] where 'swapped' means
    sides flipped RELATIVE TO ROUND 1."""
    out = []
    base = None
    for r in rounds:
        window = [o for o in observations
                  if o["state"] == "gameplay"
                  and r["start"] <= o["t"] <= r["end"]]
        hues_a = [o["hue_a"] for o in window if "hue_a" in o]
        hues_b = [o["hue_b"] for o in window if "hue_b" in o]
        ha = statistics.median(hues_a) if hues_a else None
        hb = statistics.median(hues_b) if hues_b else None
        dec = {"index": r["index"], "hue_a": ha, "hue_b": hb,
               "swapped": False, "note": "hue continuity"}
        if base is None:
            base = (ha, hb)
            dec["note"] = "reference round"
        elif None not in (ha, hb) and None not in base:
            same = (abs(ha - base[0]) <= HUE_SWAP_TOL
                    and abs(hb - base[1]) <= HUE_SWAP_TOL)
            crossed = (abs(ha - base[1]) <= HUE_SWAP_TOL
                       and abs(hb - base[0]) <= HUE_SWAP_TOL
                       and abs(base[0] - base[1]) > HUE_SWAP_TOL)
            if crossed and not same:
                dec["swapped"] = True
                dec["note"] = (f"chip hues crossed (a {base[0]:.0f}->"
                               f"{ha:.0f}, b {base[1]:.0f}->{hb:.0f})")
        out.append(dec)
    return out


# ------------------------------------------------------ calibration health
CAL_MIN_FULL_HOUSE = 0.40     # fraction of gameplay frames w/ all 10 slots
                              # accepted, below which the calibration looks
                              # unreliable on THIS capture
CAL_MIN_MEDIAN_SCORE = 0.60
CAL_MAX_UNKNOWN_RATE = 0.35


def calibration_health(observations: list[dict]) -> dict:
    """Runtime measurement of calibration health from THIS run's own
    accepted/rejected slot reads — distinct from calibrate_source.py's
    one-time offline confidence. A calibration can score well in
    isolation (its own sample frames) and still drift on a different
    capture of the same broadcast (resolution, compression, HUD scale
    tweak); this measures the calibration against the map it is actually
    being used on, every single run, and refuses to call it 'ok' when the
    evidence says otherwise. Returns {'status': ok|suspect, 'reasons':
    [...], 'metrics': {...}}."""
    gameplay = [o for o in observations if o["state"] == "gameplay"]
    if not gameplay:
        return {"status": "suspect",
                "reasons": ["no gameplay frames observed — cannot measure "
                           "calibration at all"],
                "metrics": {"gameplay_frames": 0}}
    all_scores = []
    full_house = 0
    total_checks = unknown_checks = 0
    for o in gameplay:
        slots = o.get("slots") or {}
        if not slots:
            continue
        accepted_this_frame = 0
        for r in slots.values():
            total_checks += 1
            if r.get("reject") is None and r.get("hero") not in ("", "UNKNOWN"):
                accepted_this_frame += 1
                all_scores.append(r["score"])
            else:
                unknown_checks += 1
        if accepted_this_frame == len(slots):
            full_house += 1
    full_house_rate = full_house / len(gameplay)
    median_score = statistics.median(all_scores) if all_scores else 0.0
    unknown_rate = (unknown_checks / total_checks) if total_checks else 1.0

    reasons = []
    if full_house_rate < CAL_MIN_FULL_HOUSE:
        reasons.append(
            f"only {full_house_rate:.0%} of gameplay frames had all 10 "
            f"slots accepted (< {CAL_MIN_FULL_HOUSE:.0%})")
    if median_score < CAL_MIN_MEDIAN_SCORE:
        reasons.append(
            f"median accepted match score {median_score:.2f} is low "
            f"(< {CAL_MIN_MEDIAN_SCORE:.2f})")
    if unknown_rate > CAL_MAX_UNKNOWN_RATE:
        reasons.append(
            f"{unknown_rate:.0%} of slot checks were UNKNOWN/rejected "
            f"(> {CAL_MAX_UNKNOWN_RATE:.0%})")
    status = "suspect" if reasons else "ok"
    metrics = {
        "gameplay_frames": len(gameplay),
        "full_house_rate": round(full_house_rate, 3),
        "median_top_score": round(median_score, 3),
        "unknown_rate": round(unknown_rate, 3),
        "total_slot_checks": total_checks,
    }
    if status == "suspect":
        reasons.append(
            "recommend: re-run pipeline/calibrate_source.py and/or "
            "pipeline/harvest_templates.py for this source")
    return {"status": status, "reasons": reasons, "metrics": metrics}


# ----------------------------------------------------------------- persist
def write_db(con, args, layout, observations, per_slot, rounds, side_map,
             stats, calib_health: dict | None = None,
             team_result: dict | None = None,
             ban_result: dict | None = None) -> dict:
    """Stage all ingestion data into the DB, idempotently.

    Rows belonging to this (match, map_order, detector_version) that came
    from CV and are not human-touched are REPLACED; manual_override rows
    and 'reviewed' stints are preserved untouched."""
    calib = layout.get("calibration") or {}
    calib_health = calib_health or {"status": "ok", "reasons": [],
                                    "metrics": {}}
    calib_suspect = calib_health.get("status") == "suspect"
    con.execute(
        """INSERT INTO ingest_runs (id, source_id, vod_url, match_id,
             map_order, start_offset, end_offset, detector_version,
             calibration_profile, calibration_version, status, stats_json,
             calibration_health, calibration_status, report_path,
             updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
           ON CONFLICT(id) DO UPDATE SET
             status=excluded.status, stats_json=excluded.stats_json,
             calibration_health=excluded.calibration_health,
             calibration_status=excluded.calibration_status,
             report_path=excluded.report_path,
             updated_at=CURRENT_TIMESTAMP""",
        (args.ingest_id, args.source_id, args.vod_url, args.match,
         args.map_order, int(args.start), int(args.end), DETECTOR_VERSION,
         args.layout, calib.get("version"), "complete",
         json.dumps(stats), json.dumps(calib_health),
         calib_health.get("status", "ok"),
         f"reports/ingest/{args.ingest_id}/report.html"))

    # map_result row for this map (create if missing; never override manual)
    mr = con.execute(
        "SELECT * FROM map_results WHERE match_id=? AND map_order=?",
        (args.match, args.map_order)).fetchone()
    if mr is None:
        cur = con.execute(
            """INSERT INTO map_results (match_id, map_order, map_id,
                 winner_team, source, vod_url, vod_start_seconds)
               VALUES (?,?,?,?,?,?,?)""",
            (args.match, args.map_order, args.map_id, args.map_winner,
             "cv", args.vod_url, int(args.start)))
        map_result_id = cur.lastrowid
    else:
        map_result_id = mr["id"]
        if args.map_winner and not mr["winner_team"]:
            con.execute("UPDATE map_results SET winner_team=? WHERE id=?",
                        (args.map_winner, map_result_id))

    # observations: full idempotent replace for this ingest id
    con.execute("DELETE FROM slot_observations WHERE ingest_id=?",
                (args.ingest_id,))
    ev_root = f"reports/ingest/{args.ingest_id}"
    obs_rows = []
    for o in observations:
        if o["state"] != "gameplay":
            obs_rows.append((args.ingest_id, o["t"], "a", 0, None,
                             o["state"], None, None, None, None, None, 0,
                             o["reason"], o.get("frame"), None, None))
            continue
        for key, r in o["slots"].items():
            side, slot = key[0], int(key[1])
            team = side_map.get(o["t"], {}).get(side)
            accepted = int(r["reject"] is None
                           and r["hero"] not in ("", "UNKNOWN"))
            obs_rows.append((
                args.ingest_id, o["t"], side, slot, team, o["state"],
                r["hero"], r["score"], r["second"], r["second_score"],
                r["margin"], accepted, r["reject"], o.get("frame"),
                (f"{ev_root}/evidence/{r['crop']}" if r.get("crop")
                 else None),
                r.get("template")))
    con.executemany(
        """INSERT OR REPLACE INTO slot_observations
           (ingest_id, offset_seconds, side, slot, team_id, state,
            hero_top, score_top, hero_second, score_second, margin,
            accepted, reject_reason, frame_path, crop_path, template_used)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", obs_rows)

    # rounds (respect manual rows)
    for r in rounds:
        row = con.execute(
            """SELECT id, source FROM map_rounds
               WHERE map_result_id=? AND round_index=?""",
            (map_result_id, r["index"])).fetchone()
        if row and row["source"] == "manual":
            continue
        con.execute(
            """INSERT INTO map_rounds (map_result_id, round_index,
                 start_offset, end_offset, confidence, source)
               VALUES (?,?,?,?,?,'cv')
               ON CONFLICT(map_result_id, round_index) DO UPDATE SET
                 start_offset=excluded.start_offset,
                 end_offset=excluded.end_offset,
                 confidence=excluded.confidence""",
            (map_result_id, r["index"], int(r["start"]), int(r["end"]),
             r["confidence"]))

    # stints + swaps: replace THIS detector version's CV rows, keep
    # manual/reviewed rows
    con.execute(
        """DELETE FROM hero_stints WHERE match_id=? AND map_result_id=?
           AND detector_version=? AND manual_override=0
           AND status != 'reviewed'""",
        (args.match, map_result_id, DETECTOR_VERSION))
    con.execute(
        """DELETE FROM hero_swaps WHERE match_id=? AND map_result_id=?
           AND detector_version=? AND manual_override=0""",
        (args.match, map_result_id, DETECTOR_VERSION))

    n_stints = n_swaps = 0
    for key, data in per_slot.items():
        side, slot = key[0], int(key[1])
        grace_lookup: dict[tuple[str, int], bool] = {}
        for st in data["stints"]:
            team = side_map.get(st["start"], {}).get(side) or \
                (args.team_a if side == "a" else args.team_b)
            g_ok = grace_ok_for_stint(st)
            grace_lookup[(st["hero"], int(st["start"]))] = g_ok
            status = ("auto-high"
                      if g_ok and not calib_suspect and (
                          (st["mean_conf"] >= AUTO_HIGH_MEAN
                           and st["min_conf"] >= AUTO_HIGH_MIN
                           and st["n_obs"] >= CONFIRM_N)
                          # a long stint may contain one weak-but-accepted
                          # frame; 20+ agreeing observations at a high
                          # mean is overwhelming temporal evidence
                          or (st["n_obs"] >= 20 and st["mean_conf"] >= 0.80))
                      else "needs-review")
            note_parts = []
            if not g_ok:
                note_parts.append(
                    f"established {int(st['start'] - st['round_start'])}s "
                    "after round start (post-unlock grace window) — not "
                    f"yet corroborated by a reobservation "
                    f"~{int(POST_UNLOCK_GRACE + POST_UNLOCK_RECHECK)}s "
                    "in; capped at needs-review in case this is a "
                    "spawn-room straggler who was still mid-swap")
            if calib_suspect:
                note_parts.append(
                    "calibration health suspect for this run: "
                    + "; ".join(calib_health.get("reasons", [])))
            notes = " | ".join(note_parts) or None
            con.execute(
                """INSERT INTO hero_stints (ingest_id, match_id,
                     map_result_id, team_id, side, slot, hero_id,
                     start_offset, end_offset, n_obs, mean_conf, min_conf,
                     status, source, detector_version, evidence_start,
                     evidence_end, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(match_id, map_result_id, team_id, slot,
                               start_offset, detector_version)
                   DO UPDATE SET end_offset=excluded.end_offset,
                     n_obs=excluded.n_obs, mean_conf=excluded.mean_conf,
                     min_conf=excluded.min_conf, status=excluded.status,
                     notes=excluded.notes, updated_at=CURRENT_TIMESTAMP
                   WHERE hero_stints.manual_override=0
                     AND hero_stints.status != 'reviewed'""",
                (args.ingest_id, args.match, map_result_id, team, side,
                 slot, st["hero"], int(st["start"]), int(st["end"]),
                 st["n_obs"], st["mean_conf"], st["min_conf"], status,
                 "cv", DETECTOR_VERSION,
                 (f"{ev_root}/evidence/{st['evidence_start']}"
                  if st.get("evidence_start") else None),
                 (f"{ev_root}/evidence/{st['evidence_end']}"
                  if st.get("evidence_end") else None),
                 notes))
            n_stints += 1
        for ev in data["events"]:
            if ev["kind"] not in ("swap", "rejected-swap"):
                continue
            team = side_map.get(ev["t"], {}).get(side) or \
                (args.team_a if side == "a" else args.team_b)
            swap_grace_ok = grace_lookup.get(
                (ev.get("to"), int(ev.get("t", -1))), True)
            swap_ok = swap_grace_ok and not calib_suspect
            status = ("confirmed" if ev["kind"] == "swap" and swap_ok
                      else "uncertain" if ev["kind"] == "swap"
                      else ("uncertain" if ev.get("unresolved")
                            else "rejected"))
            reason = ev["reason"]
            if ev["kind"] == "swap" and not swap_grace_ok:
                reason += (" [post-unlock grace: not yet corroborated by "
                          f"a ~{int(POST_UNLOCK_RECHECK)}s-later recheck — "
                          "could be a spawn-room straggler]")
            if ev["kind"] == "swap" and calib_suspect:
                reason += " [calibration health suspect for this run]"
            con.execute(
                """INSERT INTO hero_swaps (ingest_id, match_id,
                     map_result_id, team_id, side, slot, from_hero,
                     to_hero, offset_seconds, confidence, status, reason,
                     evidence_before, evidence_after, detector_version)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(match_id, map_result_id, team_id, slot,
                               offset_seconds, to_hero, detector_version)
                   DO UPDATE SET confidence=excluded.confidence,
                     status=excluded.status, reason=excluded.reason
                   WHERE hero_swaps.manual_override=0""",
                (args.ingest_id, args.match, map_result_id, team, side,
                 slot, ev.get("from") or ev.get("slot_from"),
                 ev.get("to") or ev.get("candidate"), int(ev["t"]),
                 ev.get("confidence"), status, reason,
                 (f"{ev_root}/evidence/{ev['evidence_before']}"
                  if ev.get("evidence_before") else None),
                 (f"{ev_root}/evidence/{ev['evidence_after']}"
                  if ev.get("evidence_after") else None),
                 DETECTOR_VERSION))
            n_swaps += 1

    # ---- OCR findings: team identity + ban candidates (staged, reviewable)
    # Only touched when this run actually carries fresh OCR evidence — a
    # rerun WITHOUT --ocr-guard leaves any previously-written OCR findings
    # alone rather than deleting them for lack of new data.
    n_bans = n_findings = 0
    import team_identify   # local import: only needed on this path
    if team_result:
        con.execute(
            "DELETE FROM ingest_findings WHERE ingest_id=? AND kind=?",
            (args.ingest_id, "team_identity"))
        for side in ("a", "b"):
            det = team_result.get(side)
            operator_team = args.team_a if side == "a" else args.team_b
            cross = team_identify.cross_check(det, operator_team)
            con.execute(
                """INSERT INTO ingest_findings (ingest_id, kind, field,
                     raw_text, value, confidence, method, status, notes)
                   VALUES (?,'team_identity',?,?,?,?,?,?,?)""",
                (args.ingest_id, side, (det or {}).get("raw_text"),
                 (det or {}).get("team"), (det or {}).get("confidence"),
                 (det or {}).get("method"),
                 "confirmed" if cross["agrees"] else "candidate",
                 cross["note"]))
            n_findings += 1
            if cross["agrees"] is False:
                log(f"WARNING team identity mismatch on side {side}: "
                    f"{cross['note']}")
    if ban_result:
        con.execute("DELETE FROM hero_bans WHERE ingest_id=?",
                    (args.ingest_id,))
        con.execute(
            "DELETE FROM ingest_findings WHERE ingest_id=? AND kind=?",
            (args.ingest_id, "ban_candidate"))
        for side in ("a", "b"):
            team = args.team_a if side == "a" else args.team_b
            for i, item in enumerate(ban_result.get(side, []), 1):
                con.execute(
                    """INSERT INTO hero_bans (match_id, map_result_id,
                         map_order, team_id, hero_id, ban_order, source,
                         confidence, ingest_id, evidence_path)
                       VALUES (?,?,?,?,?,?,'cv',?,?,?)""",
                    (args.match, map_result_id, args.map_order, team,
                     item["hero"], i, item["confidence"], args.ingest_id,
                     (f"{ev_root}/evidence/{item['evidence']}"
                      if item.get("evidence") else None)))
                n_bans += 1
        for item in ban_result.get("unresolved", []):
            con.execute(
                """INSERT INTO ingest_findings (ingest_id, kind, field,
                     value, confidence, status, notes)
                   VALUES (?,'ban_candidate',?,?,?,'candidate',?)""",
                (args.ingest_id, item.get("side"), item["hero"],
                 item["confidence"], item["reason"]))
            n_findings += 1

    con.commit()
    return {"map_result_id": map_result_id, "stints": n_stints,
            "swaps": n_swaps, "observations": len(obs_rows),
            "bans": n_bans, "findings": n_findings}


# --------------------------------------------------------------- side map
def build_side_map(observations, rounds, side_decisions, team_a, team_b):
    """{t: {'a': team_id, 'b': team_id}} for every observation time."""
    out = {}
    dec_by_round = {d["index"]: d for d in side_decisions}
    for o in observations:
        mapping = {"a": team_a, "b": team_b}
        for r in rounds:
            if r["start"] - ROUND_GAP <= o["t"] <= r["end"] + ROUND_GAP:
                if dec_by_round.get(r["index"], {}).get("swapped"):
                    mapping = {"a": team_b, "b": team_a}
                break
        out[o["t"]] = mapping
    return out


# -------------------------------------------------------------------- main
def run(args) -> dict:
    layout = capture.load_layout(args.layout)
    lib = detect.load_templates(os.path.join(
        db.REPO_ROOT, layout["templates_dir"]))
    log(f"templates: {len(lib)} heroes from {layout['templates_dir']}")
    try:
        hero_roles = comp_solver.load_hero_roles(db.connect())
        log(f"roles: loaded {len(hero_roles)} hero roles for 1/2/2 "
            "composition resolution")
    except Exception as e:      # role resolution is additive — never fatal
        hero_roles = None
        log(f"roles: unavailable ({e}); comp resolution disabled this run")

    out_root = os.path.join(db.REPO_ROOT, "reports", "ingest",
                            args.ingest_id)
    crops_dir = os.path.join(out_root, "evidence")
    frames_dir = os.path.join(db.REPO_ROOT, "work", "ingest",
                              args.ingest_id, "frames")
    os.makedirs(crops_dir, exist_ok=True)

    fs = FrameServer(args.clip, args.clip_offset, frames_dir)

    # scale layout once against a probe frame
    probe_t = args.start
    fp = fs.get(probe_t)
    probe = cv2.imread(fp) if fp else None
    if probe is None:
        raise SystemExit(f"cannot read a frame at offset {probe_t}")
    fh, fw = probe.shape[:2]
    layout_scaled, info = capture.scale_layout_to_frame(layout, fw, fh)
    if not info["ok"]:
        raise SystemExit(f"layout cannot scale to {fw}x{fh}: "
                         f"{info['reason']}")
    log(f"frames {fw}x{fh} — {info['note']}")

    # ---- optional OCR layer: generalized highlight guard, team identity,
    # ban detection. Degrades gracefully — no engine installed means every
    # OCR-dependent feature below silently sits out, exactly like ocr_hud.py.
    ocr_read_fn = None
    ocr_aliases = None
    if getattr(args, "ocr_guard", False):
        import ocr_hud
        ocr_aliases = ocr_hud.load_aliases()
        try:
            ocr_read_fn = ocr_hud.make_reader(args.ocr_engine)
            log(f"OCR guard enabled: engine={args.ocr_engine}")
        except RuntimeError as e:
            log(f"OCR guard requested but unavailable ({e}) — "
                "continuing without it")

    # ---- pass 1: baseline
    ts = fs.extract_baseline(args.start, args.end, args.every)
    log(f"baseline: {len(ts)} frames every {args.every}s")
    observations = []
    for t in ts:
        p = fs.get(t)
        if p:
            observations.append(observe(
                t, p, layout_scaled, lib, crops_dir,
                ocr_read_fn=ocr_read_fn, ocr_aliases=ocr_aliases,
                hero_roles=hero_roles))
    n_game = sum(1 for o in observations if o["state"] == "gameplay")
    log(f"baseline states: gameplay {n_game}, "
        f"other {len(observations) - n_game}")

    # ---- pass 2: dense around every suspected change
    slot_keys = [f"{s}{i}" for s in ("a", "b") for i in range(1, 6)]
    dense_windows: set[tuple[float, float]] = set()
    for key in slot_keys:
        for (t0, t1) in change_windows(slot_track(observations, key)):
            if t1 - t0 > DENSE_STEP:
                dense_windows.add((t0, t1))
    # also densify entries into gameplay after gaps (round starts)
    game_ts = sorted(o["t"] for o in observations
                     if o["state"] == "gameplay")
    for i in range(1, len(game_ts)):
        if game_ts[i] - game_ts[i - 1] >= ROUND_GAP:
            dense_windows.add((max(args.start,
                                   game_ts[i] - args.every), game_ts[i]))
    # preliminary round detection (baseline data is enough — the emblem
    # clusters at the baseline sample rate) purely to locate unlock times,
    # so the post-unlock grace/recheck windows get guaranteed coverage
    # before the FINAL round detection runs on the complete observation set
    prelim_rounds, _prelim_setups = detect_rounds(
        observations, args.start, args.end)
    unlock_windows = post_unlock_windows(prelim_rounds, args.start, args.end)
    dense_windows |= unlock_windows
    log(f"dense pass: {len(dense_windows)} windows "
        f"({len(unlock_windows)} post-unlock grace/recheck)")
    seen_ts = {o["t"] for o in observations}
    for (t0, t1) in sorted(dense_windows):
        t = t0 + DENSE_STEP
        while t < t1:
            rt = round(t, 1)
            if rt not in seen_ts:
                p = fs.get(rt)
                if p:
                    observations.append(observe(
                        rt, p, layout_scaled, lib, crops_dir,
                        ocr_read_fn=ocr_read_fn, ocr_aliases=ocr_aliases,
                        hero_roles=hero_roles))
                    seen_ts.add(rt)
            t += DENSE_STEP
    observations.sort(key=lambda o: o["t"])
    n_game = sum(1 for o in observations if o["state"] == "gameplay")
    log(f"total observations: {len(observations)} ({n_game} gameplay)")

    # ---- calibration health: measured from THIS run's own evidence, not
    # trusted from calibrate_source.py's one-time offline confidence
    calib_health = calibration_health(observations)
    log(f"calibration health: {calib_health['status']}"
        + (f" — {'; '.join(calib_health['reasons'])}"
           if calib_health["reasons"] else ""))

    # ---- rounds + sides
    rounds, setups = detect_rounds(observations, args.start, args.end)
    _round_spans = [(r['index'], int(r['start']), int(r['end']))
                    for r in rounds]
    log(f"rounds: {_round_spans}")
    _setup_spans = [(int(s['start']), int(s['end'])) for s in setups]
    log(f"setup phases: {_setup_spans}")
    side_decisions = detect_side_swaps(observations, rounds)
    for d in side_decisions:
        log(f"round {d['index']}: sides "
            f"{'SWAPPED' if d['swapped'] else 'unchanged'} ({d['note']})")
    side_map = build_side_map(observations, rounds, side_decisions,
                              args.team_a, args.team_b)

    # ---- consensus per slot
    setup_spans = [(s["start"], s["end"]) for s in setups]
    per_slot = {}
    for key in slot_keys:
        track = slot_track(observations, key)
        stints, events = build_stints(track, setup_spans, rounds)
        per_slot[key] = {"stints": stints, "events": events,
                         "n_reads": len(track)}
        swaps = [e for e in events if e["kind"] == "swap"]
        if swaps:
            log(f"slot {key}: " + "; ".join(
                f"{s['from']}->{s['to']}@{s['t']:.0f}s" for s in swaps))

    # ---- team identity + ban detection (only when OCR is available) ------
    team_result = None
    ban_result = None
    if ocr_read_fn is not None:
        import team_identify
        import detect_bans
        ocr_frames = [(o["t"], o["_ocr"]) for o in observations
                      if "_ocr" in o]
        con_ro = db.connect()
        known_teams = team_identify.known_teams_from_db(con_ro)
        team_result = team_identify.identify_teams(
            ocr_frames, ocr_frames, layout, known_teams, fw, fh)
        for side in ("a", "b"):
            det = team_result[side]
            operator = args.team_a if side == "a" else args.team_b
            cross = team_identify.cross_check(det, operator)
            log(f"team identity {side}: {det.get('team') or '?'} "
                f"({det.get('confidence', 0):.0%} over "
                f"{det.get('n_frames', 0)} frame(s)) vs operator "
                f"'{operator}' — {cross['note']}")
        ban_result = detect_bans.detect_bans_in_frames(
            ocr_frames, ocr_aliases, fw, layout=layout, fh=fh)
        log(f"ban detection: {len(ban_result['a'])} confirmed side a, "
            f"{len(ban_result['b'])} confirmed side b, "
            f"{len(ban_result['unresolved'])} unresolved "
            f"({ban_result['pickban_frames']}/{ban_result['frames_scanned']} "
            "frames looked like a pick/ban screen)")

    # ---- artifacts
    stats = {
        "window": [args.start, args.end],
        "baseline_every": args.every,
        "frames_sampled": len(observations),
        "gameplay_frames": n_game,
        "skipped_frames": len(observations) - n_game,
        "dense_windows": len(dense_windows),
        "rounds": len(rounds),
        "confirmed_swaps": sum(
            1 for d in per_slot.values()
            for e in d["events"] if e["kind"] == "swap"),
        "rejected_swaps": sum(
            1 for d in per_slot.values()
            for e in d["events"] if e["kind"] == "rejected-swap"),
        "setup_changes": sum(
            1 for d in per_slot.values()
            for e in d["events"] if e["kind"] == "setup-change"),
        "setup_spans": [[s["start"], s["end"]] for s in setups],
        "detector_version": DETECTOR_VERSION,
        "calibration_health": calib_health,
        "ocr_guard": ocr_read_fn is not None,
    }
    if team_result:
        stats["team_identity"] = team_result
    if ban_result:
        stats["ban_detection"] = {
            k: v for k, v in ban_result.items()
            if k in ("frames_scanned", "pickban_frames")}
        stats["ban_detection"]["confirmed"] = {
            "a": len(ban_result["a"]), "b": len(ban_result["b"])}
        stats["ban_detection"]["unresolved"] = len(ban_result["unresolved"])
    os.makedirs(out_root, exist_ok=True)
    with open(os.path.join(out_root, "observations.jsonl"), "w",
              encoding="utf-8") as f:
        for o in observations:
            f.write(json.dumps(
                {k: v for k, v in o.items() if not k.startswith("_")})
                + "\n")
    for name, payload in (("stints.json", per_slot),
                          ("rounds.json", {"rounds": rounds,
                                           "setups": setups,
                                           "sides": side_decisions}),
                          ("stats.json", stats)):
        with open(os.path.join(out_root, name), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1)
    log(f"artifacts -> {out_root}")

    result = {"stats": stats, "per_slot": per_slot, "rounds": rounds,
              "side_decisions": side_decisions,
              "observations": observations, "out_root": out_root}

    if args.write:
        con = db.connect()
        db.init_schema(con)
        wrote = write_db(con, args, layout, observations, per_slot, rounds,
                         side_map, stats, calib_health=calib_health,
                         team_result=team_result, ban_result=ban_result)
        log(f"DB: map_result {wrote['map_result_id']}, "
            f"{wrote['stints']} stints, {wrote['swaps']} swap rows, "
            f"{wrote['observations']} observations, "
            f"{wrote['bans']} CV bans, {wrote['findings']} findings")
        result["db"] = wrote
    else:
        log("dry run: nothing written to the DB (use --write)")

    import build_ingest_report
    pairing = {"title": f"{args.match} · map {args.map_order} "
                        f"({args.map_id or '?'})",
               "team_a": args.team_a, "team_b": args.team_b}
    build_ingest_report.build(args.ingest_id, args.layout,
                              result.get("db"), pairing)
    log(f"report -> {out_root}/report.html · review -> review.html")

    # every report gets a hero crop report — the per-frame, per-slot evidence
    # grid (top hero, runner-up, margin, honest UNKNOWN) that's otherwise
    # only visible via observations.jsonl. Best-effort: never fails the run.
    try:
        import build_crop_report
        # FrameServer names this run's frames "t<offset>.jpg"; a frames_dir
        # reused across re-runs can also hold un-renamed "base######.jpg"
        # leftovers from an earlier extraction — never show those as
        # evidence for this ingest.
        cres = build_crop_report.process(
            frames_dir, layout, out_root,
            name_filter=lambda fn: fn.startswith("t"),
            run_report_href="report.html", layout_href=None)
        log(f"crop report -> {out_root}/crops.html "
            f"({cres['crops']} crop(s) from {cres['frames']} frame(s))")
    except Exception as e:
        log(f"crop report failed (non-fatal): {type(e).__name__}: {e}")

    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", required=True, help="local clip file")
    ap.add_argument("--clip-offset", type=float, default=0.0,
                    help="stream offset (s) of clip t=0")
    ap.add_argument("--start", type=float, required=True,
                    help="map start, stream offset seconds")
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--layout", required=True)
    ap.add_argument("--source-id", required=True)
    ap.add_argument("--ingest-id", required=True)
    ap.add_argument("--match", required=True)
    ap.add_argument("--map-order", type=int, required=True)
    ap.add_argument("--map-id", default=None,
                    help="game_maps id (required with --write on new map)")
    ap.add_argument("--map-winner", default=None)
    ap.add_argument("--team-a", required=True,
                    help="team on screen-left in round 1")
    ap.add_argument("--team-b", required=True)
    ap.add_argument("--vod-url", default=None)
    ap.add_argument("--every", type=float, default=5.0)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--ocr-guard", action="store_true",
                    help="enable the generalized OCR layer: highlight/"
                         "replay guard, team-identity cross-check, and "
                         "ban detection. Requires an OCR engine (pip "
                         "install easyocr, or --ocr-engine tesseract/"
                         "paddle with their own install); silently sits "
                         "out if none is available.")
    ap.add_argument("--ocr-engine", default="easyocr",
                    choices=["easyocr", "tesseract", "paddle", "none"])
    args = ap.parse_args(argv)
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
