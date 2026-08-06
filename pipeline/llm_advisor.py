#!/usr/bin/env python3
"""
llm_advisor.py — optional, advisory-only language-model assistance.

This module exists because two jobs in this pipeline are language problems
wearing computer-vision clothing:

  * **name reconciliation** — an OCR'd nameplate reads "TW1STED M1NDS" or a
    handle transliterates from Hangul, and `difflib` has no idea that those
    are the same entity a human recognizes instantly;
  * **failure triage** — `calibrate_source.py` refuses with reasons like
    "b3 portrait box has almost no detail (texture 31)", which is precise,
    correct, and completely opaque to someone who just wanted a layout.

Everything else in the pipeline stays exactly as it was. In particular this
module has NOTHING to do with measuring HUD geometry: pixel coordinates come
from `calibrate_source.py`'s RANSAC grid fit, which is deterministic,
reproducible from the VOD, and far better at that job than any model.

THE RULES, which the code enforces rather than merely documents:

  1. **Advisory only.** Every return value carries `advisory: True` and
     `binding: False`, plus a `provenance` block naming the provider and
     model. Nothing here writes to the DB, a layout, or an export. The
     output is a suggestion addressed to a human gate — that is the whole
     contract, and `assert_never_binding()` is the guard callers use.
  2. **Closed vocabulary.** `suggest_team`/`suggest_player` may only return
     an id from the caller-supplied list. A model that answers with anything
     else — a plausible-looking team it invented, a reworded id, a null —
     is refused and downgraded to an abstention. It is structurally
     impossible for this module to invent a team or a person.
  3. **Gap-filling only.** Both suggesters take the deterministic result and
     REFUSE TO RUN if it already resolved. The fuzzy matcher is never
     second-guessed; the advisor only ever speaks where it stayed silent.
  4. **Off by default.** No key in the environment means every entry point
     returns a clean "unavailable" and the caller proceeds exactly as it did
     before. There is no code path where a missing key is an error.
  5. **Never logs a key.** Provider selection reports NAMES only, matching
     `desktop/owcs_desktop/credentials.py`'s convention.

Keys come from the environment (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`GEMINI_API_KEY`), put there by the desktop credential vault exactly like
`FACEIT_API_KEY` and `YOUTUBE_API_KEY` already are. Nothing in the public
static site touches this module — the site has no server to hold a key and
no business asking a visitor for one.

Transport is stdlib `urllib` (same as `ingest_faceit.py` and
`automation/faceit_api.py`) so no provider SDK enters requirements.txt, and
it is injectable so the whole module is exercised offline in tests.

Usage:
  python3 pipeline/llm_advisor.py --check
  python3 pipeline/llm_advisor.py --explain-calibration reports/cal.json
  python3 pipeline/llm_advisor.py --suggest-team "TW1STED M1NDS" --db-teams
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ADVISOR_VERSION = "advisor-v1"

#: Wall-clock ceiling per call. The advisor is a convenience; it must never
#: be the reason an ingest run hangs. One attempt, no retries.
TIMEOUT_S = 20

#: Below this self-reported confidence a suggestion is downgraded to an
#: abstention. Deliberately high: a weak guess about who a player is costs
#: more to review than it saves.
MIN_SUGGESTION_CONFIDENCE = 0.70

#: Cap on how many candidates are put in front of a model. A roster or teams
#: table larger than this is a sign the caller should have narrowed it first,
#: and a huge list makes the closed-vocabulary check meaningless.
MAX_CANDIDATES = 200


class AdvisorUnavailable(RuntimeError):
    """No provider is configured. A normal state, never an error condition —
    callers catch this and carry on with the deterministic result."""


# --------------------------------------------------------------- providers
#: Each provider knows how to build a request and how to dig the text back
#: out of its own response shape. Adding one is a dict entry, not a branch
#: scattered through the module.
PROVIDERS: dict[str, dict] = {
    "anthropic": {
        "env": "ANTHROPIC_API_KEY",
        "label": "Claude (Anthropic)",
        "default_model": "claude-sonnet-4-5",
        "url": "https://api.anthropic.com/v1/messages",
    },
    "openai": {
        "env": "OPENAI_API_KEY",
        "label": "ChatGPT (OpenAI)",
        "default_model": "gpt-4o-mini",
        "url": "https://api.openai.com/v1/chat/completions",
    },
    "gemini": {
        "env": "GEMINI_API_KEY",
        "label": "Gemini (Google)",
        "default_model": "gemini-2.0-flash",
        "url": ("https://generativelanguage.googleapis.com/v1beta/models/"
                "{model}:generateContent"),
    },
}

#: Preference order when several keys are present. Nothing deep — it is the
#: order the capabilities were developed and tested against.
PROVIDER_ORDER = ("anthropic", "openai", "gemini")


def _env(env: dict | None = None) -> dict:
    return os.environ if env is None else env


def configured_providers(env: dict | None = None) -> list[str]:
    """Provider NAMES with a key present, in preference order. Safe to log —
    this function never touches a key's value beyond testing it for
    emptiness."""
    e = _env(env)
    return [p for p in PROVIDER_ORDER
            if (e.get(PROVIDERS[p]["env"]) or "").strip()]


def describe_providers(env: dict | None = None) -> list[dict]:
    """Presence-only summary, shaped like `CredentialVault.describe()` so a
    UI can render it with the same code. No values, ever."""
    ready = set(configured_providers(env))
    return [{"name": name,
             "label": meta["label"],
             "env": meta["env"],
             "default_model": meta["default_model"],
             "configured": name in ready}
            for name, meta in PROVIDERS.items()]


# --------------------------------------------------------------- transport
def _http_post_json(url: str, headers: dict, payload: dict,
                    timeout: int = TIMEOUT_S) -> dict:
    """One POST, one JSON response. Raises RuntimeError with a message that
    is safe to print — provider error bodies can echo request metadata, so
    only the status line and a short excerpt are surfaced."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        excerpt = ""
        try:
            excerpt = exc.read().decode("utf-8", "replace")[:200]
        except Exception:                       # pragma: no cover - defensive
            pass
        raise RuntimeError(
            f"provider returned HTTP {exc.code}"
            + (f": {excerpt}" if excerpt else "")) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"provider unreachable ({exc.reason})") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"provider sent unparseable JSON ({exc})") from exc


def _build_request(provider: str, model: str, key: str,
                   system: str, user: str, max_tokens: int) -> tuple:
    """(url, headers, payload) for one provider. Kept together with
    `_extract_text` so a provider's two halves never drift apart."""
    meta = PROVIDERS[provider]
    if provider == "anthropic":
        return (meta["url"],
                {"x-api-key": key, "anthropic-version": "2023-06-01"},
                {"model": model, "max_tokens": max_tokens,
                 "system": system,
                 "messages": [{"role": "user", "content": user}]})
    if provider == "openai":
        return (meta["url"],
                {"authorization": f"Bearer {key}"},
                {"model": model, "max_tokens": max_tokens,
                 "messages": [{"role": "system", "content": system},
                              {"role": "user", "content": user}]})
    # gemini — key rides in a header, not the query string, so it cannot
    # leak into a proxy access log
    return (meta["url"].format(model=model),
            {"x-goog-api-key": key},
            {"system_instruction": {"parts": [{"text": system}]},
             "contents": [{"role": "user", "parts": [{"text": user}]}],
             "generationConfig": {"maxOutputTokens": max_tokens}})


def _extract_text(provider: str, resp: dict) -> str:
    """The assistant's text, whatever envelope it arrived in."""
    try:
        if provider == "anthropic":
            return "".join(b.get("text", "") for b in resp.get("content", [])
                           if b.get("type") == "text")
        if provider == "openai":
            return resp["choices"][0]["message"]["content"] or ""
        parts = resp["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise RuntimeError(
            f"{provider} response missing expected fields ({exc})") from exc


def _parse_json_object(text: str) -> dict:
    """The first JSON object in a model's reply.

    Models wrap JSON in prose or fences no matter how firmly the prompt says
    not to, so this scans for a balanced top-level object rather than
    trusting `json.loads(text)`. A reply with no object at all raises —
    callers turn that into an abstention, never a guess."""
    s = (text or "").strip()
    start = s.find("{")
    if start < 0:
        raise RuntimeError("model reply contained no JSON object")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(s[start:i + 1])
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"model reply was not valid JSON ({exc})") from exc
                if not isinstance(obj, dict):
                    raise RuntimeError("model reply was not a JSON object")
                return obj
    raise RuntimeError("model reply had an unterminated JSON object")


# ---------------------------------------------------------------- envelope
def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def _envelope(kind: str, provider: str | None, model: str | None,
              payload: dict) -> dict:
    """Stamp a result as advisory. Every public function returns through
    here, so there is no way to emit an un-tagged suggestion."""
    out = dict(payload)
    out["advisory"] = True
    out["binding"] = False
    out["provenance"] = {
        "source": "llm" if provider else "offline-rules",
        "kind": kind,
        "provider": provider,
        "model": model,
        "at": _now_iso(),
        "version": ADVISOR_VERSION,
    }
    return out


def assert_never_binding(result: dict) -> dict:
    """Call-site guard for anything that persists data.

    Any code that writes to the DB, a layout file or an export should pass
    values through here first: an advisory result raises rather than being
    silently stored as a measurement. This is the tripwire that keeps
    rule 1 true as the codebase grows."""
    if isinstance(result, dict) and result.get("advisory"):
        raise RuntimeError(
            "advisory LLM output reached a persistence path — suggestions "
            "must be confirmed by a human gate before they are stored "
            f"(kind={result.get('provenance', {}).get('kind')})")
    return result


# ----------------------------------------------------------------- advisor
class Advisor:
    """A configured provider, or none.

    `transport` is the seam the tests use: it takes
    (url, headers, payload, timeout) and returns the parsed response dict,
    so every prompt, guard and parse path runs offline with no key."""

    def __init__(self, provider: str | None = None, model: str | None = None,
                 *, env: dict | None = None, transport=None,
                 timeout: int = TIMEOUT_S):
        self.env = _env(env)
        self.timeout = timeout
        self._transport = transport or _http_post_json
        available = configured_providers(self.env)
        if provider:
            if provider not in PROVIDERS:
                raise AdvisorUnavailable(
                    f"unknown provider {provider!r}; "
                    f"known: {', '.join(sorted(PROVIDERS))}")
            if provider not in available and transport is None:
                raise AdvisorUnavailable(
                    f"{PROVIDERS[provider]['label']} selected but "
                    f"{PROVIDERS[provider]['env']} is not set")
            self.provider = provider
        elif available:
            self.provider = available[0]
        elif transport is not None:
            self.provider = PROVIDER_ORDER[0]      # test transport supplied
        else:
            raise AdvisorUnavailable(
                "no language-model key configured (set one of: "
                + ", ".join(PROVIDERS[p]["env"] for p in PROVIDER_ORDER)
                + ") — this is optional; every command works without it")
        self.model = model or PROVIDERS[self.provider]["default_model"]

    # ------------------------------------------------------------- calling
    def ask_json(self, system: str, user: str, max_tokens: int = 512) -> dict:
        """One prompt in, one JSON object out. Raises RuntimeError on any
        transport, envelope or parse failure — callers turn that into an
        abstention rather than letting it reach a user."""
        key = (self.env.get(PROVIDERS[self.provider]["env"]) or "").strip()
        url, headers, payload = _build_request(
            self.provider, self.model, key, system, user, max_tokens)
        resp = self._transport(url, headers, payload, self.timeout)
        return _parse_json_object(_extract_text(self.provider, resp))

    # ------------------------------------------------ name reconciliation
    def suggest_team(self, text: str, known_teams: list[dict],
                     deterministic: dict | None = None) -> dict:
        """Propose a team id for OCR text the fuzzy matcher could not place.

        `deterministic` is `team_identify.match_team()`'s result. When it
        already resolved, this refuses to run — the advisor fills gaps, it
        never overrides a measurement.
        """
        return self._suggest(
            kind="team-suggestion", text=text, candidates=known_teams,
            deterministic=deterministic, det_key="team", id_key="team",
            label_of=lambda t: (f"{t['id']}: {t.get('name', '')}"
                                + (f" ({t['code']})" if t.get("code") else "")),
            what="esports team",
            hint=("Broadcast overlays abbreviate, stylise and translate team "
                  "names, and OCR corrupts them further (0/O, 1/I, 5/S). "
                  "Consider full names, short codes, regional spellings and "
                  "common fan abbreviations."))

    def suggest_player(self, text: str, roster: list[dict],
                       deterministic: dict | None = None) -> dict:
        """Propose a player id for a nameplate the fuzzy matcher could not
        place. Same gap-filling contract as `suggest_team`.

        This is the highest-risk capability in the module — inventing a
        person is the failure `player_identify.py` is built to prevent — so
        the closed-vocabulary check is the load-bearing guard, not the
        prompt."""
        return self._suggest(
            kind="player-suggestion", text=text, candidates=roster,
            deterministic=deterministic, det_key="player", id_key="player",
            label_of=lambda p: (f"{p['id']}: {p.get('handle', '')}"
                                + (f" [{p['team_id']}]" if p.get("team_id")
                                   else "")),
            what="professional player handle",
            hint=("Handles are short, stylised and often transliterated. OCR "
                  "confuses 0/O, 1/l/I, 5/S and rn/m, and may drop or add a "
                  "leading sponsor tag."))

    def _suggest(self, *, kind: str, text: str, candidates: list[dict],
                 deterministic: dict | None, det_key: str, id_key: str,
                 label_of, what: str, hint: str) -> dict:
        """The shared body of both suggesters: the guards, the prompt, and
        the closed-vocabulary check that makes the whole thing safe."""
        def abstain(reason: str, provider=None, model=None) -> dict:
            return _envelope(kind, provider, model,
                             {id_key: None, "confidence": 0.0,
                              "why": reason, "consulted": provider is not None})

        # Rule 3 — the advisor only speaks where the measurement was silent.
        if deterministic and deterministic.get(det_key):
            return abstain(
                f"deterministic match already resolved "
                f"{deterministic[det_key]!r} — advisor not consulted")
        text = (text or "").strip()
        if len(text) < 2:
            return abstain("text too short to reconcile")
        if not candidates:
            return abstain("no known candidates to match against")
        if len(candidates) > MAX_CANDIDATES:
            return abstain(
                f"{len(candidates)} candidates exceeds the {MAX_CANDIDATES} "
                "cap — narrow the list before consulting the advisor")

        allowed = {str(c["id"]) for c in candidates}
        listing = "\n".join(f"- {label_of(c)}" for c in candidates)
        system = (
            "You reconcile noisy OCR text from an esports broadcast overlay "
            f"against a fixed list of known {what}s.\n"
            "Rules you must follow exactly:\n"
            "1. Answer with ONE JSON object and nothing else.\n"
            '2. Schema: {"id": <id from the list, or null>, '
            '"confidence": <0.0-1.0>, "why": "<one short sentence>"}.\n'
            "3. The id MUST be copied verbatim from the supplied list. Never "
            "invent one, never reformat one, never answer with a name.\n"
            "4. If you are not confident, answer null. Abstaining is the "
            "correct answer whenever the evidence is weak — a wrong "
            "identification is far more costly than no identification.")
        user = (f"{hint}\n\nOCR text: {text!r}\n\n"
                f"Known {what}s:\n{listing}\n\n"
                "Which one is it? Remember: null if unsure.")

        try:
            obj = self.ask_json(system, user, max_tokens=300)
        except RuntimeError as exc:
            return abstain(f"advisor unavailable: {exc}",
                           self.provider, self.model)

        raw_id = obj.get("id")
        why = str(obj.get("why", ""))[:200]
        try:
            conf = float(obj.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))

        if raw_id is None:
            return abstain(f"advisor abstained: {why or 'no confident match'}",
                           self.provider, self.model)
        # Rule 2 — the load-bearing guard. Anything outside the supplied
        # vocabulary is refused outright, including near-misses.
        if str(raw_id) not in allowed:
            return abstain(
                f"advisor proposed {str(raw_id)[:60]!r}, which is not in the "
                "supplied list — refused", self.provider, self.model)
        if conf < MIN_SUGGESTION_CONFIDENCE:
            return abstain(
                f"advisor confidence {conf:.2f} below the "
                f"{MIN_SUGGESTION_CONFIDENCE:.2f} floor: {why}",
                self.provider, self.model)

        return _envelope(kind, self.provider, self.model, {
            id_key: str(raw_id), "confidence": round(conf, 3),
            "why": why or "no rationale given", "consulted": True,
            "needs_human_confirmation": True,
        })

    # --------------------------------------------------------- triage
    def explain_calibration(self, reasons: list[str], confidence: float,
                            ok: bool) -> dict:
        """Rewrite calibration reasons as next steps a person can act on.

        Zero data-path risk: this touches wording, never geometry. It also
        degrades to the offline rules below rather than failing."""
        base = explain_calibration_offline(reasons, confidence, ok)
        if not reasons:
            return base
        system = (
            "You explain automated HUD-calibration failures to someone "
            "capturing an esports broadcast, who may not be technical.\n"
            "Answer with ONE JSON object and nothing else:\n"
            '{"summary": "<=2 plain sentences", '
            '"steps": ["<concrete action>", ...]}.\n'
            "At most four steps, each something the person can actually do "
            "(pick different frames, check the video, re-run with more "
            "samples, adjust boxes by hand). Never invent pixel coordinates "
            "or claim to know what the frames contain — you have only the "
            "diagnostic text below.")
        user = (
            f"Calibration {'succeeded with warnings' if ok else 'was refused'}"
            f" at confidence {confidence:.2f}"
            f"{' (floor 0.55)' if not ok else ''}.\n\n"
            "Diagnostics:\n" + "\n".join(f"- {r}" for r in reasons)
            + "\n\nBackground: the calibrator finds the small saturated "
              "ult-charge chips along the top HUD, fits a 5-slot uniform grid "
              "per side, and places portrait boxes beside them. It needs "
              "several frames of LIVE GAMEPLAY with the full HUD visible — "
              "menus, replays, killcams and intermissions have no chip row.")
        try:
            obj = self.ask_json(system, user, max_tokens=500)
        except RuntimeError as exc:
            base["provenance"]["fallback_reason"] = str(exc)
            return base

        summary = str(obj.get("summary", "")).strip()
        steps = [str(s).strip() for s in (obj.get("steps") or [])
                 if str(s).strip()][:4]
        if not summary or not steps:
            base["provenance"]["fallback_reason"] = (
                "advisor reply missing summary or steps")
            return base
        return _envelope("calibration-triage", self.provider, self.model, {
            "summary": summary[:400], "steps": steps,
            "reasons": list(reasons), "confidence": round(confidence, 3),
            "ok": bool(ok),
        })


# ----------------------------------------------------------- offline rules
#: (substring, plain-English step). Ordered — the first match per reason
#: wins. These are the failures the calibrator actually emits; anything
#: unmatched falls through to a generic step rather than being dropped.
_TRIAGE_RULES: list[tuple[str, str]] = [
    ("no readable frames supplied",
     "No image was readable. Check the frames are PNGs the tool can open, "
     "and that the folder is not empty."),
    ("chip row not found",
     "The coloured ult-charge chips along the top of the HUD were not found "
     "on one side. Pick frames from LIVE GAMEPLAY — not a menu, replay, "
     "killcam, intermission or map-transition screen."),
    ("only one side produced grid candidates",
     "Only one team's HUD row was detected. Choose frames where both teams' "
     "full HUD is visible and unobstructed."),
    ("side pitches disagree",
     "The two sides disagree about HUD spacing, which usually means one row "
     "matched something that is not the HUD. Add more gameplay frames from "
     "different moments in the map."),
    ("visible in too few frames",
     "The HUD was only visible in a frame or two. Supply more frames "
     "(five to ten spread across the map is a good target)."),
    ("out of bounds",
     "A portrait box fell outside the video. This usually means the frames "
     "are letterboxed, cropped or a different resolution than the broadcast "
     "— re-extract them at the source resolution."),
    ("almost no detail",
     "A portrait box landed on flat background rather than hero art. Check "
     "the calibration sheet, then nudge the boxes by hand in the control "
     "room if the rest of the grid looks right."),
    ("not mirror-symmetric",
     "The two sides are not mirrored the way this HUD normally is. Review "
     "the calibration sheet before trusting the layout."),
]

_GENERIC_STEP = ("Review the calibration sheet image and adjust the boxes by "
                 "hand if the automatic fit is close but not exact.")


def explain_calibration_offline(reasons: list[str], confidence: float,
                                ok: bool) -> dict:
    """Plain-English triage with no key, no network and no model.

    This is what makes the feature honest: the useful half of calibration
    triage is a lookup table, it ships to everyone, and the advisor only
    ever improves the wording."""
    steps: list[str] = []
    for r in reasons or []:
        low = r.lower()
        step = next((s for frag, s in _TRIAGE_RULES if frag in low), None)
        if step is None:
            step = _GENERIC_STEP
        if step not in steps:
            steps.append(step)
    if not reasons:
        summary = (f"Calibration succeeded cleanly at {confidence:.0%} "
                   "confidence with no warnings.")
    elif ok:
        summary = (f"Calibration succeeded at {confidence:.0%} confidence, "
                   f"but flagged {len(reasons)} thing(s) worth a look before "
                   "you trust the layout.")
    else:
        summary = (f"Calibration was refused at {confidence:.0%} confidence "
                   "(the floor is 55%). No layout was written — that is the "
                   "tool declining to guess, not a crash.")
    return _envelope("calibration-triage", None, None, {
        "summary": summary, "steps": steps[:4], "reasons": list(reasons or []),
        "confidence": round(confidence, 3), "ok": bool(ok),
    })


# --------------------------------------------------------------- top level
def try_advisor(provider: str | None = None, model: str | None = None,
                *, env: dict | None = None, transport=None) -> "Advisor | None":
    """An `Advisor`, or None when nothing is configured.

    The convenience wrapper for every call site: `adv = try_advisor()` then
    `if adv:` keeps the "optional" promise from leaking try/except blocks
    through the pipeline."""
    try:
        return Advisor(provider, model, env=env, transport=transport)
    except AdvisorUnavailable:
        return None


def explain_calibration(reasons: list[str], confidence: float, ok: bool,
                        *, provider: str | None = None,
                        model: str | None = None,
                        env: dict | None = None, transport=None) -> dict:
    """Triage that always returns something useful, key or no key."""
    adv = try_advisor(provider, model, env=env, transport=transport)
    if adv is None:
        return explain_calibration_offline(reasons, confidence, ok)
    return adv.explain_calibration(reasons, confidence, ok)


def format_advice(advice: dict) -> str:
    """Render triage for a terminal, provenance included so nobody mistakes
    a model's wording for a measurement."""
    prov = advice.get("provenance", {})
    src = (f"{prov.get('provider')}/{prov.get('model')}"
           if prov.get("provider") else "offline rules")
    lines = [f"[advisor] {advice.get('summary', '')}", f"[advisor] source: {src} "
             "(advisory — not part of the evidence chain)"]
    if prov.get("fallback_reason"):
        lines.append(f"[advisor] fell back to offline rules: "
                     f"{prov['fallback_reason']}")
    for i, step in enumerate(advice.get("steps", []), 1):
        lines.append(f"  {i}. {step}")
    return "\n".join(lines)


# --------------------------------------------------------------------- CLI
def _cmd_check(args) -> int:
    rows = describe_providers()
    print("language-model advisor — optional, advisory only\n")
    for r in rows:
        mark = "configured" if r["configured"] else "not set"
        print(f"  {r['label']:<20} {r['env']:<20} {mark}")
    ready = [r for r in rows if r["configured"]]
    print()
    if not ready:
        print("No key set. Every command still works — name reconciliation "
              "falls back to\nfuzzy matching and calibration triage falls "
              "back to its offline rules.")
        print("\nAdd a key in the desktop control room (Credentials), or "
              "export one:")
        for r in rows:
            print(f"  export {r['env']}=...")
        return 0
    print(f"Active provider: {ready[0]['label']} "
          f"({ready[0]['default_model']})")
    if args.probe:
        adv = try_advisor()
        try:
            obj = adv.ask_json('Reply with JSON only.',
                               'Return {"ok": true} and nothing else.', 50)
            print(f"Live probe: OK ({obj})")
        except RuntimeError as exc:
            print(f"Live probe FAILED: {exc}")
            return 1
    return 0


def _cmd_explain(args) -> int:
    try:
        with open(args.explain_calibration, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"could not read calibration result: {exc}")
        return 2
    advice = explain_calibration(doc.get("reasons", []),
                                 float(doc.get("confidence", 0.0)),
                                 bool(doc.get("ok", False)),
                                 provider=args.provider, model=args.model)
    print(format_advice(advice))
    return 0


def _cmd_suggest_team(args) -> int:
    import db
    import team_identify
    con = db.connect()
    try:
        teams = team_identify.known_teams_from_db(con)
    finally:
        con.close()
    det = team_identify.match_team(args.suggest_team, teams)
    print(f"deterministic: {det}")
    if det.get("team"):
        print("resolved without the advisor — nothing to consult")
        return 0
    adv = try_advisor(args.provider, args.model)
    if adv is None:
        print("no advisor configured (see --check) — the fuzzy matcher's "
              "abstention stands")
        return 0
    res = adv.suggest_team(args.suggest_team, teams, det)
    print(json.dumps(res, indent=2))
    if res.get("team"):
        print(f"\nSUGGESTION ONLY — confirm '{res['team']}' by hand before "
              "it is recorded anywhere.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Optional, advisory-only LLM assistance. Never writes "
                    "to the database, a layout or an export.")
    ap.add_argument("--check", action="store_true",
                    help="show which providers are configured")
    ap.add_argument("--probe", action="store_true",
                    help="with --check, make one live call to verify the key")
    ap.add_argument("--explain-calibration", metavar="JSON",
                    help="plain-English triage of a calibration result file")
    ap.add_argument("--suggest-team", metavar="OCR_TEXT",
                    help="reconcile OCR text against the teams table")
    ap.add_argument("--provider", choices=sorted(PROVIDERS))
    ap.add_argument("--model")
    args = ap.parse_args(argv)

    if args.explain_calibration:
        return _cmd_explain(args)
    if args.suggest_team:
        return _cmd_suggest_team(args)
    return _cmd_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
