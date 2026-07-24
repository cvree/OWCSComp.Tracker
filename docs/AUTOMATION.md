# Automation foundation (Roadmap Phase A + discovery scaffolding)

This document covers the automation layer added under `pipeline/automation/`.
It implements the **foundation** of the *OWCS Comp Tracker — Complete Automation
Roadmap*: the persistent job/state spine plus the discovery-side registries and
the rolling coverage report. It does **not** record video or download VODs —
that belongs to the self-hosted worker described in the roadmap's later passes.
This layer is what makes such work trackable, resumable, idempotent and safe to
run twice.

Everything here is **stdlib-only** (sqlite3 + a tiny dependency-free YAML
parser), so it runs in exactly the offline environment CI and the site build
already use — no new dependencies, no secrets.

## What's implemented

| Roadmap item | Where | Status |
|---|---|---|
| A1 automation database (persistent, not workflow artifacts) | `pipeline/automation/schema.sql` | ✅ |
| A1 job store / state machine / locks / models | `job_store.py`, `state_machine.py`, `locks.py`, `models.py` | ✅ |
| A2 global idempotency (deterministic job keys) | `models.py` (`match_key`, `record_key`, …) + `jobs.job_key` PK | ✅ |
| A3 distributed locking (leases + heartbeats + crash steal) | `locks.py` | ✅ |
| A4 operator config file | `config/automation.yml` + `config.py` | ✅ |
| B1 curated FACEIT competition registry | `config/faceit_competitions.json` | ✅ (placeholder IDs; fill + enable) |
| C1 verified broadcast-channel registry | `config/broadcast_channels.json` | ✅ (placeholder IDs; fill + enable) |
| D4 rolling 14-day completeness report | `coverage.py` + `cli.py coverage` | ✅ |
| State-retention on failure (dead-letter, J1/J2) | `job_store.record_attempt` → `RETRY_SCHEDULED` / `FAILED_PERMANENT` | ✅ |

The state machine (the roadmap's `DISCOVERED … PUBLISHED / FAILED / IGNORED`
graph) is enforced on every transition, so a bug can never skip review and jump
straight to `PUBLISHED`, and **no record is ever deleted on failure** — a failed
job keeps its error code, message, attempt count, timestamps, worker id, source
URL and diagnostic path, then moves to `FAILED_PERMANENT` once its per-kind
retry ceiling is hit (still visible, still actionable).

## Not yet implemented (later roadmap passes)

FACEIT schedule syncing (B2/B3), YouTube upload discovery (C2/C3), the
self-hosted recording daemon (Phase E), broadcast segmentation (Phase F), the
detector/layout/template automation (Phase G), and automated publication PRs
(Phase I). Each of these plugs into this foundation: they enqueue jobs with the
deterministic keys above, take a lease before touching a shared resource, and
transition through the state machine.

## Operator CLI

All commands are offline and read-mostly (`init-db` and `coverage --save` are
the only writers, and they only touch the automation DB):

```bash
python pipeline/automation/cli.py init-db          # create/upgrade the job DB
python pipeline/automation/cli.py config           # resolved operator config
python pipeline/automation/cli.py registries       # competition/channel registries
python pipeline/automation/cli.py coverage         # rolling 14-day report (Phase D4)
python pipeline/automation/cli.py coverage --save  # + persist a coverage snapshot
python pipeline/automation/cli.py status           # job counts by state + locks
```

`coverage` reads the content DB (`data/owcs.sqlite`) as the universe of tracked
matches and prints the roadmap's D4 block, listing **every** match missing an
official broadcast individually.

## Going live (filling the registries)

Both registries ship with placeholder IDs and every entry **disabled**, so the
discovery layer never ingests on a guess:

1. `config/faceit_competitions.json` — set each competition's real
   `championshipId` from the FACEIT Data API and flip `enabled: true`. Only
   enabled entries with a real ID are returned by `config.load_competitions()`.
2. `config/broadcast_channels.json` — set each channel's real `channelId` and
   flip `enabled: true`. Prefer channel upload playlists over broad search
   (quota: `videos.list` = 1 unit, `search.list` ≈ 100).
3. Tunables live in `config/automation.yml` (lookback window, retry ceilings,
   backoff schedule, lease TTL, publish mode, regions).

## Data locations

- Automation job queue: `data/automation.sqlite` (gitignored — runtime state,
  regenerable via `init-db`; override with `OWCS_AUTOMATION_DB`).
- Content DB: `data/owcs.sqlite` (committed, unchanged by this layer).

## Tests

Six offline suites, run the same way as the rest of the pipeline
(`python pipeline/test_*.py`):

`test_automation_state_machine.py`, `test_automation_config.py`,
`test_automation_schema.py`, `test_automation_job_store.py`,
`test_automation_locks.py`, `test_automation_coverage.py`.
