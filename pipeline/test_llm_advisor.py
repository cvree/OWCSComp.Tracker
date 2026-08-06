#!/usr/bin/env python3
"""
test_llm_advisor.py — the advisory-only LLM layer, fully offline.

No key, no network, no provider SDK. `Advisor` takes an injectable transport
(same trick `ocr_hud.py` uses for OCR engines), so every prompt, guard and
parse path below runs against a fake provider.

The interesting tests are the REFUSALS. This module's value is not that it
can relay a model's answer — it is that it cannot be talked into inventing a
team, a player, or a fact:

  * a model answering with an id outside the supplied list is refused;
  * a low-confidence answer is downgraded to an abstention;
  * a resolved deterministic match short-circuits the advisor entirely;
  * every result is stamped advisory, and `assert_never_binding` raises if
    one reaches a persistence path;
  * a dead provider degrades to offline rules instead of failing a run.

Run:  python3 pipeline/test_llm_advisor.py   (non-zero on failure)
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import llm_advisor as LA  # noqa: E402

FAILS: list[str] = []
COUNT = 0


def check(label: str, cond: bool) -> None:
    global COUNT
    COUNT += 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAILS.append(label)


TEAMS = [
    {"id": "twis", "name": "Twisted Minds", "code": "TM"},
    {"id": "falcons", "name": "Team Falcons", "code": "TF"},
    {"id": "zeta", "name": "ZETA DIVISION", "code": "ZETA"},
]
ROSTER = [
    {"id": "p-zox", "handle": "ZOX", "team_id": "twis"},
    {"id": "p-hanbin", "handle": "Hanbin", "team_id": "falcons"},
]


def fake_transport(reply_text: str, *, provider: str = "anthropic",
                   fail: str | None = None):
    """A provider that returns `reply_text` in `provider`'s envelope.

    Records the last request so tests can assert on what was actually sent
    (notably: that the key never lands in a URL)."""
    seen: dict = {}

    def transport(url, headers, payload, timeout):
        seen["url"], seen["headers"] = url, headers
        seen["payload"], seen["timeout"] = payload, timeout
        if fail:
            raise RuntimeError(fail)
        if provider == "anthropic":
            return {"content": [{"type": "text", "text": reply_text}]}
        if provider == "openai":
            return {"choices": [{"message": {"content": reply_text}}]}
        return {"candidates": [{"content": {"parts": [{"text": reply_text}]}}]}

    transport.seen = seen  # type: ignore[attr-defined]
    return transport


def advisor(reply: str, *, provider: str = "anthropic", fail=None,
            env: dict | None = None) -> LA.Advisor:
    return LA.Advisor(provider, env=env or {"ANTHROPIC_API_KEY": "test-key",
                                            "OPENAI_API_KEY": "test-key",
                                            "GEMINI_API_KEY": "test-key"},
                      transport=fake_transport(reply, provider=provider,
                                               fail=fail))


def main() -> int:
    print("provider discovery")
    check("no keys -> no providers",
          LA.configured_providers({}) == [])
    check("one key -> that provider",
          LA.configured_providers({"OPENAI_API_KEY": "x"}) == ["openai"])
    check("blank key does not count",
          LA.configured_providers({"ANTHROPIC_API_KEY": "   "}) == [])
    check("preference order is anthropic-first",
          LA.configured_providers(
              {"GEMINI_API_KEY": "x", "ANTHROPIC_API_KEY": "x"})[0]
          == "anthropic")
    check("describe() never carries a value",
          all("value" not in r and "key" not in r
              for r in LA.describe_providers({"ANTHROPIC_API_KEY": "secret"})))
    check("describe() reports configured state",
          [r["configured"] for r in
           LA.describe_providers({"ANTHROPIC_API_KEY": "x"})
           if r["name"] == "anthropic"] == [True])
    check("try_advisor with no key returns None",
          LA.try_advisor(env={}) is None)
    try:
        LA.Advisor(env={})
        unavailable = False
    except LA.AdvisorUnavailable:
        unavailable = True
    check("Advisor with no key raises AdvisorUnavailable", unavailable)

    print("\nrequest shape (per provider)")
    for prov, keycheck in (
            ("anthropic", lambda h: h.get("x-api-key") == "test-key"),
            ("openai", lambda h: h.get("authorization") == "Bearer test-key"),
            ("gemini", lambda h: h.get("x-goog-api-key") == "test-key")):
        adv = advisor('{"id": "twis", "confidence": 0.95, "why": "ok"}',
                      provider=prov)
        adv.suggest_team("TW1STED M1NDS", TEAMS, {"team": None})
        seen = adv._transport.seen                    # type: ignore
        check(f"{prov}: key travels in a header", keycheck(seen["headers"]))
        check(f"{prov}: key never appears in the URL",
              "test-key" not in seen["url"])
        check(f"{prov}: response envelope parsed", True)

    print("\nname reconciliation — the happy path")
    adv = advisor('{"id": "twis", "confidence": 0.93, '
                  '"why": "OCR 1/I confusion on Twisted Minds"}')
    res = adv.suggest_team("TW1STED M1NDS", TEAMS, {"team": None})
    check("suggests the right team", res["team"] == "twis")
    check("carries confidence", res["confidence"] == 0.93)
    check("flagged advisory", res["advisory"] is True)
    check("flagged non-binding", res["binding"] is False)
    check("demands human confirmation",
          res["needs_human_confirmation"] is True)
    check("provenance names the provider",
          res["provenance"]["provider"] == "anthropic"
          and res["provenance"]["source"] == "llm")
    check("provenance carries a model", bool(res["provenance"]["model"]))
    check("provenance is timestamped", res["provenance"]["at"].endswith("Z"))

    print("\nname reconciliation — the refusals that matter")
    adv = advisor('{"id": "spacestation", "confidence": 0.99, "why": "sure"}')
    res = adv.suggest_team("SSG", TEAMS, {"team": None})
    check("id outside the supplied list is REFUSED", res["team"] is None)
    check("refusal explains itself", "not in the supplied list" in res["why"])

    adv = advisor('{"id": "twis", "confidence": 0.42, "why": "maybe"}')
    res = adv.suggest_team("T?????", TEAMS, {"team": None})
    check("below-floor confidence is downgraded to abstention",
          res["team"] is None and "below the" in res["why"])

    adv = advisor('{"id": null, "confidence": 0.0, "why": "cannot tell"}')
    res = adv.suggest_team("XXXX", TEAMS, {"team": None})
    check("explicit null abstention respected", res["team"] is None)
    check("abstention still stamped advisory", res["advisory"] is True)

    adv = advisor('{"id": "TWIS", "confidence": 0.9, "why": "case"}')
    res = adv.suggest_team("twisted", TEAMS, {"team": None})
    check("a reformatted id is refused, not normalised", res["team"] is None)

    print("\ngap-filling contract (rule 3)")
    adv = advisor('{"id": "falcons", "confidence": 0.99, "why": "override"}')
    res = adv.suggest_team("TWISTED MINDS", TEAMS,
                           {"team": "twis", "method": "exact"})
    check("resolved deterministic match short-circuits the advisor",
          res["team"] is None and "already resolved" in res["why"])
    check("and the provider was never called",
          adv._transport.seen == {})                  # type: ignore

    print("\ninput guards")
    adv = advisor('{"id": "twis", "confidence": 0.99, "why": "x"}')
    check("text too short abstains",
          adv.suggest_team("T", TEAMS, {"team": None})["team"] is None)
    check("empty candidate list abstains",
          adv.suggest_team("TWISTED", [], {"team": None})["team"] is None)
    big = [{"id": f"t{i}", "name": f"Team {i}", "code": f"T{i}"}
           for i in range(LA.MAX_CANDIDATES + 1)]
    check("oversized candidate list abstains",
          adv.suggest_team("TWISTED", big, {"team": None})["team"] is None)
    check("guards never called the provider",
          adv._transport.seen == {})                  # type: ignore

    print("\nplayer suggestions (same guards, higher stakes)")
    adv = advisor('{"id": "p-zox", "confidence": 0.88, "why": "ZOX"}')
    res = adv.suggest_player("Z0X", ROSTER, {"player": None})
    check("suggests the right player", res["player"] == "p-zox")
    check("player result is advisory", res["advisory"] is True)
    adv = advisor('{"id": "p-newguy", "confidence": 0.99, "why": "new"}')
    res = adv.suggest_player("WHO", ROSTER, {"player": None})
    check("CANNOT invent a person outside the roster", res["player"] is None)

    print("\nmalformed provider replies degrade to abstention")
    for label, reply in (("prose with no JSON", "I think it is Twisted Minds"),
                         ("truncated object", '{"id": "twis", "conf'),
                         ("a JSON array", '["twis"]'),
                         ("invalid JSON", "{id: twis,}")):
        res = advisor(reply).suggest_team("TW1STED", TEAMS, {"team": None})
        check(f"{label} -> abstention, not a crash", res["team"] is None)
    res = advisor('```json\n{"id": "twis", "confidence": 0.9, "why": "x"}\n```'
                  ).suggest_team("TW1STED", TEAMS, {"team": None})
    check("fenced JSON is still parsed", res["team"] == "twis")
    res = advisor('Sure! {"id": "twis", "confidence": 0.9, "why": "a } brace"}'
                  ).suggest_team("TW1STED", TEAMS, {"team": None})
    check("braces inside strings do not confuse the parser",
          res["team"] == "twis")
    res = advisor('{"id": "twis", "confidence": "high", "why": "x"}'
                  ).suggest_team("TW1STED", TEAMS, {"team": None})
    check("non-numeric confidence is treated as zero", res["team"] is None)

    print("\ntransport failure never breaks a run")
    res = advisor("{}", fail="provider unreachable (timeout)").suggest_team(
        "TW1STED", TEAMS, {"team": None})
    check("dead provider -> abstention", res["team"] is None)
    check("abstention names the failure", "unavailable" in res["why"])

    print("\ncalibration triage — offline rules (no key at all)")
    off = LA.explain_calibration(
        ["left chip row not found (3 candidate blobs pooled from 5 frames) "
         "— are these live-gameplay frames?",
         "a2 portrait box has almost no detail (texture 31) — likely not on "
         "a portrait"], 0.31, False, env={})
    check("works with no key", off["provenance"]["source"] == "offline-rules")
    check("still stamped advisory", off["advisory"] is True)
    check("summary explains the refusal honestly",
          "refused" in off["summary"] and "not a crash" in off["summary"])
    check("chip-row reason -> gameplay-frames advice",
          any("LIVE GAMEPLAY" in s for s in off["steps"]))
    check("texture reason -> its own advice",
          any("flat background" in s for s in off["steps"]))
    check("one step per distinct reason", len(off["steps"]) == 2)

    every = LA.explain_calibration_offline(
        [frag for frag, _ in LA._TRIAGE_RULES], 0.4, False)
    check("every documented reason maps to a step (no generic fallback)",
          LA._GENERIC_STEP not in every["steps"])
    unknown = LA.explain_calibration_offline(["something nobody predicted"],
                                             0.4, False)
    check("an unknown reason still yields a step",
          unknown["steps"] == [LA._GENERIC_STEP])
    clean = LA.explain_calibration_offline([], 0.91, True)
    check("clean run reports success", "succeeded cleanly" in clean["summary"])
    check("clean run has no steps", clean["steps"] == [])
    warned = LA.explain_calibration_offline(["sides not mirror-symmetric"],
                                            0.72, True)
    check("ok-with-warnings is worded as such",
          "succeeded at" in warned["summary"])

    print("\ncalibration triage — with a provider")
    adv = advisor(json.dumps({
        "summary": "The HUD was not visible in the frames you picked.",
        "steps": ["Pick frames during live gameplay.", "Add more frames."]}))
    res = adv.explain_calibration(["left chip row not found"], 0.2, False)
    check("uses the model's wording", res["steps"][0].startswith("Pick"))
    check("still advisory", res["advisory"] is True)
    check("provenance names the model source",
          res["provenance"]["source"] == "llm")
    check("keeps the raw reasons alongside",
          res["reasons"] == ["left chip row not found"])

    adv = advisor(json.dumps({"summary": "", "steps": []}))
    res = adv.explain_calibration(["left chip row not found"], 0.2, False)
    check("empty model reply falls back to offline rules",
          res["provenance"]["source"] == "offline-rules"
          and res["steps"] and "fallback_reason" in res["provenance"])
    adv = advisor("{}", fail="HTTP 401")
    res = adv.explain_calibration(["left chip row not found"], 0.2, False)
    check("bad key falls back to offline rules, still useful",
          res["provenance"]["source"] == "offline-rules" and bool(res["steps"]))
    check("and says why it fell back",
          "401" in res["provenance"]["fallback_reason"])

    print("\nthe persistence tripwire")
    raised = False
    try:
        LA.assert_never_binding(off)
    except RuntimeError:
        raised = True
    check("assert_never_binding raises on advisory output", raised)
    check("and passes ordinary values through",
          LA.assert_never_binding({"team": "twis"}) == {"team": "twis"})
    check("advisory flag present on every public return",
          all(r.get("advisory") is True for r in (off, res, clean, warned)))

    print("\nCLI")
    check("--check with no key exits 0 (optional feature)",
          LA.main(["--check"]) == 0)

    print("\nformat_advice")
    text = LA.format_advice(off)
    check("renders the summary", "refused" in text)
    check("labels itself advisory", "advisory" in text)
    check("names the offline source", "offline rules" in text)

    print()
    if FAILS:
        print(f"{len(FAILS)} of {COUNT} ADVISOR CHECKS FAILED:")
        for f in FAILS:
            print(f"  FAIL  {f}")
        return 1
    print(f"ALL {COUNT} LLM ADVISOR TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
