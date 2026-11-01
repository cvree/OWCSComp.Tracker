"""
external_sheet — the imported community spreadsheet, as a separate source layer.

The tracker's own data is VOD-derived: hero stints read off the broadcast
HUD, with seconds, swaps and evidence frames. A community Google Sheet
records a broader, longer history — map picks, map results, hero bans and
the heroes each player was listed on — but it is a manually maintained
external record with no timing and no evidence frames.

This package imports that sheet WITHOUT blurring the two:

    fetch.py       download the public xlsx export once per sync (pipeline /
                   GitHub Actions only — no page ever contacts Google),
                   SHA-256 it, keep the last good snapshot on failure
    workbook.py    xlsx -> raw cell layer: every tab, every non-empty cell,
                   merged ranges and hyperlinks, preserved verbatim
    layout.py      find each tab's header row and decide what the tab IS
                   from its columns (config/external/gsheet_layout.json)
    aliases.py     the one alias layer: teams, players, heroes, maps, modes,
                   regions, stages, dates (config/external/aliases.json)
    normalize.py   raw cells -> External* records, each carrying the original
                   sheet text and the cell it came from
    reconcile.py   conservative scored matching against the published VOD
                   maps; both claims kept, compared field by field
    export.py      assets/data/external_sheet.v1.js (window.OWCS_EXTERNAL) +
                   the source-inventory and reconciliation reports
    manifest.py    the snapshot ledger (hashes, dates, parser versions)

The CLI is pipeline/sheet_sync.py. Contracts: docs/EXTERNAL-SHEET.md.
"""

PARSER_VERSION = "gsheet-parser-2"
SCHEMA = "external.v1"
