"""
store.py — where the external layer lives on disk, and the snapshot ledger.

    config/external/gsheet_source.json       which sheet, credit text
    config/external/gsheet_layout.json       how tabs are read
    config/external/aliases.json             canonical spellings
    corrections/external_reconciliation.json human decisions (links, aliases)

    data/external/gsheet/raw/index.json      RAW layer index (hash, date, tabs, package)
    data/external/gsheet/raw/sheets/*.json.gz  RAW layer: every cell of one tab per
                                             file, gzip with a fixed mtime (deterministic)
    data/external/gsheet/normalized.json.gz  External* records + provenance (deterministic gzip)
    data/external/gsheet/manifest.json       snapshot ledger (hash, date, versions)
    reports/external/gsheet/*.json|md        inventory, reconciliation, last failure
    assets/data/external_sheet.v1.js         what the public site renders
    work/gsheet/*.xlsx                       original downloads (git-ignored)

Every writer is deterministic (sorted keys, LF, UTF-8) so CI can rebuild and
compare (`sheet_sync.py check`).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
from dataclasses import dataclass

#: GitHub rejects files over 100 MB; stay clear of it with every file we
#: commit, and fail the import (keeping the last good data) instead.
MAX_COMMITTED_FILE_BYTES = 95 * 1024 * 1024

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
    def raw_dir(self) -> str: return self.p("data", "external", "gsheet", "raw")
    @property
    def raw(self) -> str: return os.path.join(self.raw_dir, "index.json")
    @property
    def raw_legacy(self) -> str: return os.path.join(self.raw_dir, "cells.json")
    @property
    def normalized(self) -> str: return self.p("data", "external", "gsheet", "normalized.json.gz")
    @property
    def normalized_legacy(self) -> str: return self.p("data", "external", "gsheet", "normalized.json")
    @property
    def manifest(self) -> str: return self.p("data", "external", "gsheet", "manifest.json")
    @property
    def reports(self) -> str: return self.p("reports", "external", "gsheet")
    @property
    def export(self) -> str: return self.p("assets", "data", "external_sheet.v1.js")
    @property
    def export_detail_dir(self) -> str: return self.p("assets", "data", "external_sheet")
    @property
    def export_summary(self) -> str: return self.p("assets", "data", "external_summary.v1.js")
    @property
    def export_series(self) -> str: return self.p("assets", "data", "external_series.v1.js")
    @property
    def export_tiers(self) -> str: return self.p("assets", "data", "sheet_tiers.v1.js")
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


# ------------------------------------------------------------ raw layer
class StoreTooLarge(RuntimeError):
    """A file the import would commit is larger than GitHub accepts."""


def _mb(n: int) -> str:
    return f"{n / 2**20:.1f} MB" if n >= 2**20 else f"{n / 1024:.0f} KB"


def _shard_name(index: int, name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "sheet"
    return f"sheets/{index:03d}-{slug}.json.gz"


def deterministic_gzip(body: bytes) -> bytes:
    """gzip with a fixed mtime AND a fixed OS byte. Python 3.13 changed the
    header's OS field (3 -> 255) for the same input, so the byte-for-byte
    drift gate failed between a 3.13 laptop and the 3.12 CI runner with
    identical content. The header has no checksum, so pinning byte 9 is safe."""
    blob = bytearray(gzip.compress(body, compresslevel=9, mtime=0))
    blob[9] = 255
    return bytes(blob)


def same_gzip_content(path: str, blob: bytes) -> bool:
    """True when the gzip file at `path` holds exactly the bytes `blob`
    decompresses to. zlib builds may compress the same input differently,
    so what must not drift is the CONTENT, not the compressed bytes."""
    try:
        with open(path, "rb") as f:
            return gzip.decompress(f.read()) == gzip.decompress(blob)
    except (OSError, EOFError, gzip.BadGzipFile):
        return False


def raw_shards(raw_doc: dict) -> tuple[dict, dict[str, bytes]]:
    """Pure: the raw document -> (index document, {relative path: gzip bytes}).

    One gzip file per worksheet keeps every committed file small even for
    a very large workbook, and means a sync that changes one tab adds one
    new blob to git rather than the whole workbook again. gzip is written
    with mtime=0 so the same cells always give the same bytes (the CI
    drift gate depends on that)."""
    wb = raw_doc.get("workbook") or {}
    shards: dict[str, bytes] = {}
    sheets_index = []
    for sheet in wb.get("sheets") or []:
        rel = _shard_name(sheet["index"], sheet["name"])
        body = json.dumps(sheet, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        blob = deterministic_gzip(body)
        if len(blob) > MAX_COMMITTED_FILE_BYTES:
            raise StoreTooLarge(f"tab '{sheet['name']}' compresses to {_mb(len(blob))} (limit {_mb(MAX_COMMITTED_FILE_BYTES)}), "
                                "over GitHub's file limit — it cannot be committed")
        shards[rel] = blob
        sheets_index.append({"index": sheet["index"], "name": sheet["name"],
                             "state": sheet.get("state"), "rows": len(sheet.get("rows") or []),
                             "file": rel, "sha256": hashlib.sha256(body).hexdigest(),
                             "bytes": len(blob)})
    index = {k: v for k, v in raw_doc.items() if k != "workbook"}
    index["format"] = "gsheet-raw.v2"
    index["workbookFormat"] = wb.get("format")
    index["package"] = wb.get("package")
    index["sheets"] = sheets_index
    return index, shards


def write_raw(paths: "Paths", raw_doc: dict, prepared: tuple | None = None) -> None:
    index, shards = prepared or raw_shards(raw_doc)
    for rel, blob in shards.items():
        target = os.path.join(paths.raw_dir, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(blob)
    shard_dir = os.path.join(paths.raw_dir, "sheets")
    keep = {os.path.basename(r) for r in shards}
    if os.path.isdir(shard_dir):
        for fn in os.listdir(shard_dir):
            if fn.endswith(".json.gz") and fn not in keep:
                os.remove(os.path.join(shard_dir, fn))      # a tab that no longer exists
    write_json(paths.raw, index)
    if os.path.exists(paths.raw_legacy):
        os.remove(paths.raw_legacy)


def read_raw(paths: "Paths") -> dict | None:
    """The committed raw document in its in-memory shape
    ({..., "workbook": {"sheets": [...]}}), or None when never synced."""
    index = read_json(paths.raw)
    if index is None:
        return read_json(paths.raw_legacy)      # pre-shard layout
    sheets = []
    for entry in index.get("sheets") or []:
        with open(os.path.join(paths.raw_dir, entry["file"]), "rb") as f:
            sheets.append(json.loads(gzip.decompress(f.read()).decode("utf-8")))
    doc = {k: v for k, v in index.items() if k not in ("sheets", "workbookFormat", "package")}
    doc["workbook"] = {"format": index.get("workbookFormat"), "package": index.get("package"),
                       "sheets": sheets}
    return doc


def check_size(label: str, text: str) -> None:
    size = len(text.encode("utf-8"))
    if size > MAX_COMMITTED_FILE_BYTES:
        raise StoreTooLarge(f"{label} would be {_mb(size)} (limit {_mb(MAX_COMMITTED_FILE_BYTES)}), over GitHub's file limit")


# ------------------------------------------------------- normalized layer
def normalized_bytes(doc: dict) -> bytes:
    """Deterministic gzip of the normalized layer. A full community sheet
    expands to many tens of MB of JSON (every listed hero is a record with
    its cell reference); compressed it stays far under GitHub's limit."""
    body = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    blob = deterministic_gzip(body)
    if len(blob) > MAX_COMMITTED_FILE_BYTES:
        raise StoreTooLarge(f"normalized.json.gz would be {_mb(len(blob))} (limit {_mb(MAX_COMMITTED_FILE_BYTES)}), over GitHub's file limit")
    return blob


def write_normalized(paths: "Paths", doc: dict, blob: bytes | None = None) -> None:
    os.makedirs(os.path.dirname(paths.normalized), exist_ok=True)
    blob = blob if blob is not None else normalized_bytes(doc)
    # Same content already committed: leave the file alone, so machines
    # with different zlib/Python builds never rewrite it back and forth.
    if not same_gzip_content(paths.normalized, blob):
        with open(paths.normalized, "wb") as f:
            f.write(blob)
    if os.path.exists(paths.normalized_legacy):
        os.remove(paths.normalized_legacy)


def read_normalized(paths: "Paths") -> dict | None:
    try:
        with open(paths.normalized, "rb") as f:
            return json.loads(gzip.decompress(f.read()).decode("utf-8"))
    except FileNotFoundError:
        return read_json(paths.normalized_legacy)
    except (OSError, ValueError):
        return None
