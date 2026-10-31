"""
store.py — where the external layer lives on disk, and the snapshot ledger.

    config/external/gsheet_source.json       which sheet, credit text
    config/external/gsheet_layout.json       how tabs are read
    config/external/aliases.json             canonical spellings
    corrections/external_reconciliation.json human decisions (links, aliases)

    data/external/gsheet/raw/cells.json      RAW layer: every tab, every cell
    data/external/gsheet/normalized.json     External* records + provenance
    data/external/gsheet/manifest.json       snapshot ledger (hash, date, versions)
    reports/external/gsheet/*.json|md        inventory, reconciliation, last failure
    assets/data/external_sheet.v1.js         what the public site renders
    work/gsheet/*.xlsx                       original downloads (git-ignored)

Every writer is deterministic (sorted keys, LF, UTF-8) so CI can rebuild and
compare (`sheet_sync.py check`).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))


@dataclass(frozen=True)
class Paths:
    root: str = REPO_ROOT

    def p(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    @property
    def source_cfg(self) -> str: return self.p("config", "external", "gsheet_source.json")
    @property
    def layout_cfg(self) -> str: return self.p("config", "external", "gsheet_layout.json")
    @property
    def aliases_cfg(self) -> str: return self.p("config", "external", "aliases.json")
    @property
    def decisions(self) -> str: return self.p("corrections", "external_reconciliation.json")
    @property
    def raw(self) -> str: return self.p("data", "external", "gsheet", "raw", "cells.json")
    @property
    def normalized(self) -> str: return self.p("data", "external", "gsheet", "normalized.json")
    @property
    def manifest(self) -> str: return self.p("data", "external", "gsheet", "manifest.json")
    @property
    def reports(self) -> str: return self.p("reports", "external", "gsheet")
    @property
    def export(self) -> str: return self.p("assets", "data", "external_sheet.v1.js")
    @property
    def public(self) -> str: return self.p("assets", "data", "public_data.v1.js")
    @property
    def cache(self) -> str: return self.p("work", "gsheet")


def read_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def dumps(obj) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=True) + "\n"


def write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def write_json(path: str, obj) -> None:
    write_text(path, dumps(obj))


def read_public(path: str) -> dict:
    """window.OWCS_PUBLIC = {...}; -> dict (same rule as self_fill)."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return {}
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return {}


# ------------------------------------------------------------ manifest
def empty_manifest(source: dict) -> dict:
    return {"source": source.get("id"), "spreadsheetId": source.get("spreadsheetId"),
            "exportUrl": source.get("exportUrl"), "snapshots": [], "lastGood": None,
            "lastAttempt": None}


def last_good(manifest: dict) -> dict | None:
    sha = manifest.get("lastGood")
    return next((s for s in manifest.get("snapshots") or [] if s.get("sha256") == sha), None)
