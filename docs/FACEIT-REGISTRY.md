# FACEIT competition registry — verification procedure & findings

This document records how `config/faceit_competitions.json` is verified against
the **live FACEIT Data API**, and the result of the 2026-07-24 verification pass.

## Why verification runs in GitHub Actions, not locally

The `FACEIT_API_KEY` is a **GitHub Actions repository secret**. It is never
present in a developer sandbox, and the FACEIT API host is not reachable from
the sandbox's network policy. Therefore all live verification runs through the
`discovery` workflow's read-only `workflow_dispatch` modes, where the secret and
network exist. IDs are **never guessed**; a competition is enabled only after
its championship id resolves against the API.

## Read-only tooling

CLI (offline-testable with `--fixture-dir pipeline/fixtures/automation`):

```bash
# find the official OWCS organizer id
python pipeline/automation/cli.py list-organizers --query "Overwatch"
# list an organizer's championships (authoritative ids) or search by name
python pipeline/automation/cli.py list-championships --organizer <ORG_ID> --json
python pipeline/automation/cli.py list-championships --query "OWCS 2026" --type all --json
# confirm one championship's official details
python pipeline/automation/cli.py verify-competition <CHAMPIONSHIP_ID>
# verify EVERY enabled competition in the registry
python pipeline/automation/cli.py verify-registry
```

Workflow (`discovery.yml`) read-only modes, dispatched with the secret:

```bash
# candidate discovery (search + organizer listing)
gh workflow run discovery.yml -r <branch> -f mode=candidates -f query="OWCS 2026"
gh workflow run discovery.yml -r <branch> -f mode=candidates -f org="<ORG_ID>,<ORG_ID>"
# verify enabled competitions resolve via the API
gh workflow run discovery.yml -r <branch> -f mode=verify
# real rolling 14-day dry-run (no DB writes)
gh workflow run discovery.yml -r <branch> -f mode=dryrun -f lookback_days=14
```

## Verified organizers (live API, 2026-07-24)

| Organizer id | Name | Role |
|---|---|---|
| `abd401de-e6ec-4ef1-8d4b-3d820f8f62ce` | **OWCS \| Overwatch Champions Series** | Official OWCS organizer (ran OWCS 2024 stages) |
| `f0e8a591-08fd-4619-9d59-d97f0571842e` | **FACEIT League - Overwatch** | Official feeder; runs OWCS 2026 Open Qualifiers |
| `3147a43b-7da5-40db-8691-20d87a0bc946` | **Overwatch Esports & PlayOverwatch** | Blizzard official esports org |

## Enabled competitions (verified 2/2 via the API)

| Registry id | Name | championshipId | region | tier |
|---|---|---|---|---|
| `owcs_2026_oq_na` | OWCS 2026 OQ - NA | `6165cc94-3d1f-4851-ad95-f0c99198e0d2` | na | 2 |
| `owcs_2026_oq_emea` | OWCS 2026 OQ - EMEA | `05eef1be-c479-45d7-b5c7-d07c5875bb6e` | emea | 2 |

Both are OWCS 2026 Open Qualifiers (official, Tier 2), status `finished`. Dates
are not exposed by the API (null).

## Coverage finding (the key blocker)

The FACEIT Data API exposes, for OWCS:
- **OWCS 2024** full stages (org `abd401de`) — old season, intentionally NOT enabled.
- **OWCS 2025 EMEA Open Qualifier** and **OWCS 2026 OQ NA/EMEA** (org `f0e8a591`).

It exposes **no** OWCS 2026 *main-stage* regional leagues, and **no**
Korea/Japan/Pacific/China/Global OWCS championships. The `/organizers/{id}/
championships` endpoint returns empty for all three official organizers, so
those competitions are simply not published on FACEIT — the OWCS 2026 main
broadcasts are operated off-FACEIT.

**Consequence:** the NA/EMEA *main* stages and the Korea/Japan/Pacific/China/
Global regions stay **disabled** with a `null` championshipId until a real
official id is confirmed. They are not guessed.

## Real 14-day dry-run result (2026-07-24, `mode=dryrun`)

```
[automation] sync-all (dry-run):
  competitions   : 2 (owcs_2026_oq_na, owcs_2026_oq_emea)
  matches seen   : 262  in-window: 0
  upserted       : 0  (dry-run — no writes)
  calendar events: 4
  reconciliation : 2 warning(s)
    [CALENDAR_EVENT_NO_FACEIT_COMP] competition owcs_2026_oq_na has no official calendar event
    [CALENDAR_EVENT_NO_FACEIT_COMP] competition owcs_2026_oq_emea has no official calendar event
```

262 real matches were fetched across the two enabled qualifiers; **0** fall in
the previous 14 days (both qualifiers finished earlier in 2026), so nothing
would be written even in a live run. No credentials were printed, no database
was written, and no runtime DB/cache was committed.

## Re-running

Any of the modes above can be re-run at will; they are read-only and safe. The
exact command for another real dry-run is in `docs/AUTOMATION.md` and above
(`mode=dryrun`).
