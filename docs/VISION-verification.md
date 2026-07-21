# Vision — calibration & verification upgrade

Every number on the public site traces to a frame. This document is the
design for making those frames *trustworthy by construction* — so the
tracker reads a comp only when a comp is really on screen, and resolves the
hard slots using the rules of the game instead of guessing.

It is driven by the real failure modes seen in the crop reports: a slot that
comes back **UNKNOWN** (Sojourn just under the ambiguity margin) or **LOW**
(Lúcio at 0.51) not because the pipeline is broken, but because a single
portrait, read in isolation, is genuinely ambiguous. The fix is *context* —
the phase of the match, and the role structure of the team.

## Principles (unchanged)
- **Never a silently-confident guess.** A weak read stays UNKNOWN; an
  inferred read is labelled as inferred and reported at its real score.
- **Additive, never destructive.** Every new signal is recorded alongside
  the raw reads. Nothing overwrites an honest read or blocks a run.
- **FACEIT is authoritative for facts** (teams, bans, scores). The vision
  layer *confirms* those facts; it doesn't invent them.

## The four upgrades

### 1. Phase-aware capture — only read a comp when a comp is real
`pipeline/recapture_planner.py`

A broadcast minute isn't uniformly worth reading. The planner classifies
every sampled moment into a phase and only *counts* settled live combat:

| phase | why it's skipped |
|---|---|
| **setup / prepare** | players hold random or movement heroes ("speeding" to spawn) before locking the real pick |
| **post-start** (first ~8 s of a round) | a swap queued during setup is still landing |
| **post-round** (last **10 s** before the round ends) | the "finish + 10 s to allow for the swap" window — losers re-pick for the next round |
| **highlight / replay** | renders a full HUD that is *not* the live comp at that timestamp |
| **no-hud** | desk, player-cams, transitions |

Everything skipped is **bookmarked with its reason**, and the settled-combat
spans become **dense recapture windows** for a second pass — so frames are
spent only where the comp is real. (`settle` and `grace` are tunable; the
round/setup spans come from the existing emblem detector.)

### 2. Role-aware resolution — the 1 / 2 / 2 constraint
`pipeline/comp_solver.py`

OW2 comp is role-locked: exactly **1 Tank, 2 Damage, 2 Support**. The matcher
reads each slot in isolation and never uses this. The solver does: given the
honest per-slot reads (each carries a full `scores` map of *every* hero's
template score), it finds the one hero-per-slot assignment that

1. satisfies the role histogram exactly,
2. uses no hero twice, and
3. maximizes total template score.

This is exactly what rescues the struggled crops. In the real a5 case the
matcher was torn between **Sojourn@0.53** and **sym@0.50** — both *Damage* —
and refused (margin 0.033 < 0.04). But slots a2/a3 already fill the two
Damage seats, so a5 *cannot* be Damage; it must be Support, and the best
Support candidate (Kiriko@0.47) completes a legal 1/2/2. It's recorded as
`role-inferred` at 0.47 — never laundered into false confidence.

Provenance per slot: `direct` (matcher agreed) · `role-inferred` (constraint
filled a slot the matcher declined) · `role-corrected` / **anomaly** (the
constraint had to overrule a *strong* read — the honest signal that a read
is wrong or the footage isn't a live role-locked comp) · `unresolved` (even
the best legal pick is below the inference floor — stays UNKNOWN).

Wired into `ingest_map.observe` additively as `obs["resolved"]`.

### 3. Match confirmation — is this the right VOD/game?
`pipeline/match_confirm.py` (fuses `team_identify` + `detect_bans`)

FACEIT says which two teams and (when available) which bans a window
belongs to. The HUD shows the same independently — the team-name plates, and
the ban subsection (a smaller strip off to the side, not in line with the
comp portraits). Confirmation cross-checks them:

- **team names** matching the expected pair (in either screen orientation —
  it also detects **sides-swapped**) is the primary signal;
- **bans** overlapping the FACEIT list reinforce it.

Verdict is `confirmed` True / False / **None** — None never blocks (the
operator's pairing stands), False warns "wrong VOD/game?" before anything is
trusted. Bans here are a *confirmation* signal; the authoritative ban list
still comes from FACEIT.

### 4. Team-name calibration
Team-name plates and the ban subsection are layout regions
(`team_left` / `team_right` zones for `team_identify`, an exclude/`bans`
region for `detect_bans`). Calibrating them per broadcast lets #3 run. This
is the one piece that still needs real frames to place the rects — the logic
above is done and tested; the rects are cut during the normal per-broadcast
calibration pass (same workflow as the HUD probe).

## What's proven vs. what proves out on the next ingest
- **Proven now (offline, deterministic):** the solver's 1/2/2 resolution and
  anomaly flagging, the phase gating + grace + recapture windowing, and the
  confirmation fusion — all unit-tested (`test_comp_solver.py`,
  `test_recapture_planner.py`, `test_match_confirm.py`), and the solver is
  wired into `ingest_map` without disturbing the existing tested path.
- **Proves out on the next real ingest** (run on a machine with the VOD):
  the calibrated ban/team-name rects, and the end-to-end lift on real
  broadcast frames. These are engine improvements — the public site updates
  automatically when a match is re-ingested and re-exported.
