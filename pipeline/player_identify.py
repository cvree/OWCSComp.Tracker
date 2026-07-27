#!/usr/bin/env python3
"""
player_identify.py — read player NAMEPLATES against known rosters (Phase 4).

Each of the ten HUD slots has a nameplate under it. Reading them tells you
which player is in which slot, which is what turns a hero timeline into a
per-player one. It is also the single easiest place in this pipeline to
invent a person, so the rules here are strict:

  * A nameplate resolves to a player ONLY by matching a KNOWN roster entry
    (`players` / `match_rosters` in the content DB). Nothing else.
  * A nameplate that does not match becomes a CANDIDATE — recorded with its
    raw OCR text, confidence and frames — and never a `players` row. Creating
    a player is a separate, explicit, human action; this module has no code
    path that inserts one.
  * Consensus over frames, same as maps and teams: one frame never decides.
  * Two roster members tying on fuzzy similarity returns UNKNOWN for that
    slot, with both names in the reason.
  * A player already assigned to another slot in the same read cannot win a
    second slot: ten slots hold ten distinct people, so a duplicate is
    evidence of an OCR error, and both slots are marked ambiguous rather
    than one silently keeping the name.

The OCR reader is injected (`read_fn`: frame_bgr -> [{text, conf, box}]), so
everything here is offline-testable with a fake reader.
"""
from __future__ import annotations

import difflib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ocr_hud  # noqa: E402

MIN_AGREE_FRAMES = 2
FUZZY_CUTOFF = 0.80      # handles are short; a loose cutoff invents people
FUZZY_MARGIN = 0.10
MIN_OCR_CONF = 0.30
# How far below a slot box the nameplate text may sit, as a fraction of the
# slot height. Reuses ocr_hud's own convention (SLOT_PAD_FRAC) rather than
# introducing a second, competing geometry.
NAMEPLATE_BELOW_FRAC = 1.6
NAMEPLATE_WIDTH_PAD = 0.45


def _norm_handle(s: str) -> str:
    return ocr_hud._norm_text(s)


def known_players_from_db(con, *, team_ids: list[str] | None = None,
                          match_id: str | None = None) -> list[dict]:
    """Read-only roster snapshot: [{'id','handle','teamId'}, ...].

    Scoped as tightly as the caller can manage — a match's own rosters when
    `match_id` is given, else the two teams' current players. A narrower
    roster is a SAFER roster: fewer strangers to fuzzy-match against.
    """
    rows: list[dict] = []
    if match_id:
        for r in con.execute(
                """SELECT p.id AS id, p.nickname AS handle, r.team_id AS team_id
                   FROM match_rosters r JOIN players p ON p.id = r.player_id
                   WHERE r.match_id = ?""", (match_id,)):
            rows.append({"id": r["id"], "handle": r["handle"],
                         "teamId": r["team_id"]})
    if not rows and team_ids:
        q = ("SELECT id, nickname AS handle, team_id FROM players "
             f"WHERE team_id IN ({','.join('?' for _ in team_ids)})")
        for r in con.execute(q, list(team_ids)):
            rows.append({"id": r["id"], "handle": r["handle"],
                         "teamId": r["team_id"]})
    return [r for r in rows if r.get("handle")]


def match_player(text: str, roster: list[dict]) -> dict:
    """Normalize one OCR nameplate to a player id, honest about ambiguity.

    Returns {"player": id|None, "quality": float, "method":
             exact|fuzzy|none, "reason": str}.
    """
    t = _norm_handle(text)
    if not t or len(t) < 2:
        return {"player": None, "quality": 0.0, "method": "none",
                "reason": "too short"}
    if t.isdigit():
        return {"player": None, "quality": 0.0, "method": "none",
                "reason": "numeric text is never a handle"}
    if not roster:
        return {"player": None, "quality": 0.0, "method": "none",
                "reason": "no known roster to match against"}

    by_handle: dict[str, list[dict]] = {}
    for p in roster:
        by_handle.setdefault(_norm_handle(p["handle"]), []).append(p)

    if t in by_handle:
        hits = by_handle[t]
        if len(hits) > 1:
            return {"player": None, "quality": 0.0, "method": "none",
                    "reason": (f"handle '{t}' is shared by "
                               f"{', '.join(p['id'] for p in hits)}")}
        return {"player": hits[0]["id"], "quality": 1.0, "method": "exact",
                "reason": t}

    ranked = sorted(((difflib.SequenceMatcher(None, t, h).ratio(), ps[0])
                     for h, ps in by_handle.items()), key=lambda r: -r[0])
    if not ranked or ranked[0][0] < FUZZY_CUTOFF:
        return {"player": None, "quality": 0.0, "method": "none",
                "reason": (f"no roster handle matches '{t}'"
                           + (f" (best {ranked[0][1]['handle']} "
                              f"{ranked[0][0]:.2f} < {FUZZY_CUTOFF})"
                              if ranked else ""))}
    best_r, best_p = ranked[0]
    other = next(((r, p) for r, p in ranked[1:] if p["id"] != best_p["id"]), None)
    if other and best_r - other[0] < FUZZY_MARGIN:
        return {"player": None, "quality": 0.0, "method": "none",
                "reason": (f"ambiguous fuzzy: {best_p['id']}/{other[1]['id']} "
                           f"({best_r:.2f} vs {other[0]:.2f})")}
    return {"player": best_p["id"], "quality": round(best_r, 3),
            "method": "fuzzy",
            "reason": f"'{t}'~'{best_p['handle']}' {best_r:.2f}"}


def nameplate_zone(slot_rect: list[int], fw: int, fh: int) -> list[int]:
    """The pixel box a slot's nameplate text occupies: directly under the
    portrait, a little wider than it (handles overflow the portrait width),
    clamped to the frame."""
    x, y, w, h = slot_rect
    pad = int(round(w * NAMEPLATE_WIDTH_PAD))
    zx = max(0, x - pad)
    zw = min(fw - zx, w + pad * 2)
    zy = min(fh - 1, y + h)
    zh = max(1, min(fh - zy, int(round(h * NAMEPLATE_BELOW_FRAC))))
    return [zx, zy, zw, zh]


def slot_nameplate_zones(layout: dict, fw: int, fh: int) -> list[dict]:
    """[{side, index, zone}] for all ten slots of a SCALED layout."""
    out = []
    for side in ("a", "b"):
        for i, rect in enumerate(layout.get(f"slots_{side}") or [], start=1):
            out.append({"side": side, "index": i,
                        "zone": nameplate_zone(list(rect), fw, fh)})
    return out


def identify_players(ocr_per_frame: list[tuple[float, list[dict]]],
                     layout: dict, roster: list[dict], *,
                     fw: int, fh: int,
                     min_agree: int = MIN_AGREE_FRAMES) -> dict:
    """Resolve all ten slots' players across frames.

    Returns:
      {"slots": [{side, index, player, confidence, method, reason, nFrames,
                  candidates: [...], rawTexts: [...]}],
       "resolved": int, "unknown": int,
       "candidatePlayers": [{raw, frames, slots, note}],
       "duplicates": [...], "disagreement": bool}

    `candidatePlayers` is the honest output for a nameplate nobody in the
    roster matches. It exists so an operator can add a real player
    deliberately — this function never creates one.
    """
    zones = slot_nameplate_zones(layout, fw, fh)
    per_slot: dict[tuple[str, int], dict] = {
        (z["side"], z["index"]): {"votes": {}, "raw": [], "unmatched": {}}
        for z in zones}

    for t, items in ocr_per_frame:
        for z in zones:
            key = (z["side"], z["index"])
            cands = [it for it in items
                     if it.get("conf", 0) >= MIN_OCR_CONF
                     and ocr_hud._center_in(it["box"], z["zone"])]
            cands.sort(key=lambda i: -i["conf"])
            if not cands:
                continue
            it = cands[0]
            bucket = per_slot[key]
            bucket["raw"].append({"t": t, "raw": it["text"],
                                  "conf": round(float(it["conf"]), 3)})
            m = match_player(it["text"], roster)
            if m["player"]:
                bucket["votes"].setdefault(m["player"], []).append({
                    "t": t, "raw": it["text"], "conf": float(it["conf"]),
                    "method": m["method"], "quality": m["quality"],
                    "score": m["quality"] * float(it["conf"]),
                })
            else:
                norm = _norm_handle(it["text"])
                if norm and len(norm) >= 2 and not norm.isdigit():
                    entry = bucket["unmatched"].setdefault(
                        norm, {"raw": it["text"], "frames": [],
                               "reason": m["reason"]})
                    entry["frames"].append(t)

    slots: list[dict] = []
    for z in zones:
        key = (z["side"], z["index"])
        bucket = per_slot[key]
        base = {"side": z["side"], "index": z["index"],
                "rawTexts": bucket["raw"][:6]}
        if not bucket["votes"]:
            unmatched = sorted(bucket["unmatched"].values(),
                               key=lambda e: -len(e["frames"]))
            slots.append(dict(base, player=None, confidence=0.0,
                              method="none", nFrames=0,
                              candidates=unmatched[:3],
                              reason=("no roster match for this nameplate"
                                      if unmatched else
                                      "no nameplate text read for this slot")))
            continue
        ranked = sorted(bucket["votes"].items(),
                        key=lambda kv: (len({e["t"] for e in kv[1]}),
                                        sum(e["score"] for e in kv[1])),
                        reverse=True)
        pid, evs = ranked[0]
        n = len({e["t"] for e in evs})
        contenders = [(p, len({e["t"] for e in ev})) for p, ev in ranked
                      if len({e["t"] for e in ev}) >= min_agree]
        if len(contenders) > 1:
            slots.append(dict(base, player=None, confidence=0.0,
                              method="none", nFrames=n, candidates=[],
                              reason=("conflicting nameplate evidence: "
                                      + ", ".join(f"{p} in {c} frame(s)"
                                                  for p, c in contenders))))
            continue
        if n < min_agree:
            slots.append(dict(base, player=None, confidence=0.0,
                              method="none", nFrames=n, candidates=[],
                              weakCandidate=pid,
                              reason=(f"'{pid}' matched in only {n} frame(s) "
                                      f"(< {min_agree} required)")))
            continue
        slots.append(dict(base, player=pid,
                          confidence=round(min(sum(e["score"] for e in evs)
                                               / len(evs), 1.0), 3),
                          method=evs[0]["method"], nFrames=n, candidates=[],
                          reason=f"{n} frame(s) agree on '{pid}'"))

    # Ten slots hold ten distinct people. A player winning two slots is an
    # OCR error, so BOTH slots become UNKNOWN rather than one keeping it.
    seen: dict[str, list[dict]] = {}
    for s in slots:
        if s["player"]:
            seen.setdefault(s["player"], []).append(s)
    duplicates = []
    for pid, rows in seen.items():
        if len(rows) < 2:
            continue
        where = ", ".join(f"{r['side']}{r['index']}" for r in rows)
        duplicates.append({"player": pid,
                           "slots": [f"{r['side']}{r['index']}" for r in rows]})
        for r in rows:
            r["player"] = None
            r["confidence"] = 0.0
            r["method"] = "none"
            r["reason"] = (f"'{pid}' also matched slot(s) {where} — a player "
                           f"cannot hold two slots, so both are UNKNOWN")

    candidates: dict[str, dict] = {}
    for z in zones:
        bucket = per_slot[(z["side"], z["index"])]
        for norm, entry in bucket["unmatched"].items():
            c = candidates.setdefault(norm, {
                "raw": entry["raw"], "normalized": norm, "frames": [],
                "slots": [], "reason": entry["reason"],
                "note": ("candidate only — a player row is NEVER created "
                         "automatically; add them deliberately if this is a "
                         "real roster member")})
            c["frames"] = sorted(set(c["frames"]) | set(entry["frames"]))[:8]
            slot_label = f"{z['side']}{z['index']}"
            if slot_label not in c["slots"]:
                c["slots"].append(slot_label)

    resolved = sum(1 for s in slots if s["player"])
    return {
        "slots": slots,
        "resolved": resolved,
        "unknown": len(slots) - resolved,
        "candidatePlayers": sorted(candidates.values(),
                                   key=lambda c: -len(c["frames"]))[:12],
        "duplicates": duplicates,
        "disagreement": bool(duplicates) or any(
            "conflicting" in (s.get("reason") or "") for s in slots),
    }


def format_result(result: dict) -> str:
    lines = [f"  players: {result['resolved']} resolved, "
             f"{result['unknown']} UNKNOWN"]
    for s in result["slots"]:
        label = f"{s['side']}{s['index']}"
        if s["player"]:
            lines.append(f"    {label}: {s['player']} "
                         f"conf={s['confidence']:.2f} ({s['method']}, "
                         f"{s['nFrames']} frame(s))")
        else:
            lines.append(f"    {label}: UNKNOWN — {s['reason']}")
    for c in result["candidatePlayers"]:
        lines.append(f"    candidate {c['raw']!r} in slot(s) "
                     f"{','.join(c['slots'])} over {len(c['frames'])} frame(s) "
                     f"— {c['reason']}")
    for d in result["duplicates"]:
        lines.append(f"    DUPLICATE {d['player']} in {', '.join(d['slots'])}")
    return "\n".join(lines)
