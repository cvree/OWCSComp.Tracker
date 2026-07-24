# Automation discovery fixtures

Offline FACEIT Data API payloads for the Phase B discovery tests and for
`cli.py sync-* --fixture-dir pipeline/fixtures/automation`.

`fixture_transport` (pipeline/automation/faceit_api.py) serves a championship
matches request for `/championships/<id>/matches` from `<id>.json`. Each file
is a real-shaped `{"items":[ ...raw FACEIT match dicts... ]}` payload.

These are **synthetic, fact-only** payloads: teams, rosters, scheduled times,
statuses and results — never hero compositions. No credentials, no real match
data, no captured VOD content.
