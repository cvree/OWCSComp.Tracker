#!/usr/bin/env python3
"""
comp_solver.py — role-aware composition resolver.

Overwatch 2 competitive is role-locked 5v5: every live team comp is exactly
ONE tank, TWO damage, TWO support. That is a hard structural constraint the
per-slot template matcher does not use — it reads each portrait in isolation,
so a washed-out or half-occluded slot comes back UNKNOWN / LOW / ambiguous
(see the struggled Sojourn and Lúcio crops) even when the rest of the row
makes the answer obvious.

This module adds that reasoning. Given the honest per-slot reads from
detect.read_slot (which carry a full `scores` map of EVERY hero's template
score, not just the winner), it finds the single hero-per-slot assignment
that:

  1. satisfies the role histogram exactly (default 1 Tank / 2 Damage /
     2 Support),
  2. uses no hero twice, and
  3. maximizes the total template score.

A slot whose raw read was already confident and role-consistent stays
"direct". A slot the matcher refused to call is filled by the constraint and
marked "role-inferred" — reported at its real (lower) score, never laundered
into false confidence. If the confident reads themselves cannot form a legal
comp (e.g. two strong tank reads), the comp is flagged as an anomaly instead
of being silently forced — that is the honest signal that a read is wrong or
the footage is not a live role-locked comp.

Pure Python: no cv2 / numpy, so the logic is unit-testable anywhere.
"""
from __future__ import annotations
import argparse
import itertools
import json
import os
import sys

ROLES = ("Tank", "Damage", "Support")
DEFAULT_TARGET = {"Tank": 1, "Damage": 2, "Support": 2}

# a role-inferred pick is only trustworthy if the template still had SOMETHING
# to say for that hero — below this its own score, we keep it UNKNOWN rather
# than assert a hero the pixels don't support at all.
INFER_FLOOR = 0.30
# how far a direct, above-floor read must lead for the solver to refuse to
# override it; overriding a confident read is the "anomaly" signal.
STRONG_READ = 0.55


def load_hero_roles(con=None) -> dict:
    """{hero_id: role} from the heroes table (or an injected connection)."""
    if con is None:
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, here)
        import db  # noqa: E402
        con = db.connect()
    return {r["id"]: r["role"]
            for r in con.execute("SELECT id, role FROM heroes")}


def _slot_candidates(slot: dict, hero_roles: dict, role: str,
                     limit: int = 4) -> list[tuple[str, float]]:
    """Top `limit` (hero, score) for one slot restricted to `role`, best
    first. Uses the full `scores` map when present, else the single read."""
    scores = slot.get("scores")
    if not scores:
        h = slot.get("hero")
        scores = {h: slot.get("score", 0.0)} if h and h != "UNKNOWN" else {}
    ranked = sorted(
        ((h, s) for h, s in scores.items() if hero_roles.get(h) == role),
        key=lambda hs: -hs[1])
    return ranked[:limit]


def _raw_top(slot: dict) -> tuple[str | None, float]:
    """The matcher's own confident pick for a slot, or (None, score) when it
    declined (UNKNOWN)."""
    h = slot.get("hero")
    if not h or h == "UNKNOWN":
        # best raw candidate score, for reporting, even though rejected
        scores = slot.get("scores") or {}
        best = max(scores.values()) if scores else slot.get("score", 0.0)
        return None, float(best)
    return h, float(slot.get("score", 0.0))


def solve(slots: list[dict], hero_roles: dict,
          target: dict | None = None) -> dict:
    """Resolve five slot reads into the best legal role-locked comp.

    `slots` is a list of 5 detect.read_slot dicts (needs `scores`, `hero`,
    `score`). Returns a dict describing the resolved comp, each slot's
    provenance, and any composition anomaly.
    """
    target = target or DEFAULT_TARGET
    n = len(slots)
    total_needed = sum(target.values())
    if n != total_needed:
        return {"valid": False, "slots": [], "heroes": [],
                "confidence": None,
                "anomaly": f"expected {total_needed} slots, got {n}",
                "target": target}

    # Enumerate every way to hand the target roles out to the slots, e.g.
    # for 1/2/2 that is 5!/(1!2!2!) = 30 role patterns. For each we pick the
    # best non-duplicate heroes and keep the highest-scoring legal comp.
    role_multiset = []
    for role, k in target.items():
        role_multiset += [role] * k

    best = None
    for pattern in set(itertools.permutations(role_multiset)):
        assignment = _best_for_pattern(slots, hero_roles, pattern)
        if assignment is None:
            continue
        score = sum(a["score"] for a in assignment)
        if best is None or score > best[0]:
            best = (score, pattern, assignment)

    if best is None:
        # No legal assignment at all — happens only when some slot has zero
        # candidates of a role the comp still needs (real read_slot scores
        # every hero, so this is rare). Still report each slot's confident
        # raw read and a confidence from them; never drop what we did see.
        raw = [_raw_top(s) for s in slots]
        conf = [rs for (rh, rs) in raw if rh is not None]
        return {"valid": False, "slots": [
            {"slot": i + 1, "hero": rh or "UNKNOWN",
             "role": hero_roles.get(rh) if rh else None,
             "score": round(rs, 3),
             "source": "direct" if rh else "unresolved",
             "raw": rh or "UNKNOWN", "raw_score": round(rs, 3)}
            for i, (rh, rs) in enumerate(raw)],
            "heroes": [rh or "UNKNOWN" for (rh, _s) in raw],
            "confidence": round(sum(conf) / len(conf), 3) if conf else None,
            "inferred": 0,
            "anomaly": "no legal 1/2/2 assignment from these reads",
            "target": target}

    _score, _pattern, assignment = best
    out_slots, overrides = [], []
    for i, (slot, a) in enumerate(zip(slots, assignment)):
        raw_hero, raw_score = _raw_top(slot)
        if a["hero"] == raw_hero:
            source = "direct"
        elif raw_hero is None:
            # matcher declined; the constraint filled it
            source = ("role-inferred" if a["score"] >= INFER_FLOOR
                      else "unresolved")
        else:
            # matcher had a DIFFERENT confident pick that the constraint
            # overruled — track it; a strong overruled read is an anomaly
            source = "role-corrected"
            if raw_score >= STRONG_READ:
                overrides.append(
                    f"slot {i + 1}: read {raw_hero}@{raw_score:.2f} "
                    f"overruled by {a['hero']}@{a['score']:.2f} to keep 1/2/2")
        hero = a["hero"] if source != "unresolved" else "UNKNOWN"
        out_slots.append({
            "slot": i + 1, "hero": hero, "role": a["role"],
            "score": round(a["score"], 3), "source": source,
            "raw": raw_hero or "UNKNOWN", "raw_score": round(raw_score, 3),
        })

    resolved = [s["hero"] for s in out_slots]
    unresolved = [s for s in out_slots if s["hero"] == "UNKNOWN"]
    dupes = len(set(h for h in resolved if h != "UNKNOWN")) != \
        len([h for h in resolved if h != "UNKNOWN"])
    confident = [s["score"] for s in out_slots if s["source"] != "unresolved"]
    confidence = round(sum(confident) / len(confident), 3) if confident else None

    anomaly = None
    if overrides:
        anomaly = "; ".join(overrides)
    elif unresolved:
        anomaly = (f"{len(unresolved)} slot(s) below the inference floor "
                   f"{INFER_FLOOR} — comp incomplete")
    elif dupes:
        anomaly = "duplicate hero in the resolved comp"

    return {
        "valid": not unresolved and not dupes and not overrides,
        "slots": out_slots,
        "heroes": resolved,
        "roles": {r: sum(1 for s in out_slots if s["role"] == r) for r in ROLES},
        "confidence": confidence,
        "inferred": sum(1 for s in out_slots if s["source"] == "role-inferred"),
        "anomaly": anomaly,
        "target": target,
    }


def _best_for_pattern(slots, hero_roles, pattern) -> list[dict] | None:
    """Best non-duplicate hero picks for a fixed slot->role pattern, via a
    tiny DFS over each slot's top candidates of the required role."""
    cand = [_slot_candidates(s, hero_roles, pattern[i])
            for i, s in enumerate(slots)]
    if any(len(c) == 0 for c in cand):
        return None
    best = [None]  # (total, [picks])

    def dfs(i, used, acc, total):
        if i == len(slots):
            if best[0] is None or total > best[0][0]:
                best[0] = (total, list(acc))
            return
        # optimistic bound: even taking each remaining slot's top candidate
        # can't beat the incumbent -> prune
        if best[0] is not None:
            remaining = sum(cand[j][0][1] for j in range(i, len(slots)))
            if total + remaining <= best[0][0]:
                return
        for hero, score in cand[i]:
            if hero in used:
                continue
            acc.append({"hero": hero, "role": pattern[i], "score": score})
            dfs(i + 1, used | {hero}, acc, total + score)
            acc.pop()

    dfs(0, frozenset(), [], 0.0)
    return best[0][1] if best[0] else None


def _demo() -> dict:
    """A worked example matching the struggled crops: a5 came back UNKNOWN
    (Sojourn just under the ambiguity margin) while the row clearly needs a
    support; the solver completes the 1/2/2."""
    roles = {"mauga": "Tank", "shion": "Damage", "sojourn": "Damage",
             "sym": "Damage", "lucio": "Support", "kiriko": "Support",
             "juno": "Support"}
    slots = [
        {"hero": "mauga", "score": 0.67, "scores": {"mauga": 0.67, "shion": 0.46}},
        {"hero": "shion", "score": 0.61, "scores": {"shion": 0.61, "sojourn": 0.40}},
        {"hero": "sym", "score": 0.64, "scores": {"sym": 0.64, "sojourn": 0.42}},
        {"hero": "lucio", "score": 0.66, "scores": {"lucio": 0.66, "kiriko": 0.40}},
        # a5: UNKNOWN — Sojourn@0.53 vs sym@0.50, margin 0.033 < 0.04
        {"hero": "UNKNOWN", "score": 0.53,
         "scores": {"sojourn": 0.53, "sym": 0.50, "kiriko": 0.47, "juno": 0.44}},
    ]
    return solve(slots, roles)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", action="store_true",
                    help="run the worked struggled-crop example")
    args = ap.parse_args()
    if args.demo:
        print(json.dumps(_demo(), indent=1))
        return
    print("comp_solver: import and call solve(slots, hero_roles). "
          "Use --demo for a worked example.")


if __name__ == "__main__":
    main()
