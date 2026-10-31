"""
sync.py — one spreadsheet sync, end to end, with the failure rules.

    download (or read a local file)
      -> SHA-256; unchanged from the last good import? stop, write NOTHING
      -> raw cell layer (every tab)
      -> normalise + validate (fails loudly on a structure change)
      -> reconcile against the published VOD maps
      -> write raw / normalized / manifest / reports / public export

Any failure before the last step leaves every committed file as it was and
writes a failure report instead (reports/external/gsheet/last_failure.*),
plus `lastAttempt` in the manifest so the Source health page says so.

`run_import` is the pure core (bytes in, documents out) that the tests drive;
everything with a side effect is in the thin wrappers below it.
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass

from . import PARSER_VERSION
from .aliases import AliasBook
from .export import (build_payload, changes, failure_markdown, hero_names_from,
                     inventory_markdown, reconciliation_markdown, render)
from .fetch import FetchFailed, download, save_snapshot, sha256
from .layout import Layout
from .normalize import ImportFailed, import_workbook
from .reconcile import reconcile
from .store import (Paths, dumps, empty_manifest, last_good, read_json, read_public,
                    write_json, write_text)
from .workbook import WorkbookError, read_xlsx, source_row_count


@dataclass
class Bundle:
    source: dict
    layout_cfg: dict
    aliases_cfg: dict
    decisions: dict
    public: dict

    @classmethod
    def load(cls, paths: Paths) -> "Bundle":
        return cls(source=read_json(paths.source_cfg, {}) or {},
                   layout_cfg=read_json(paths.layout_cfg, {}) or {},
                   aliases_cfg=read_json(paths.aliases_cfg, {}) or {},
                   decisions=read_json(paths.decisions, {}) or {},
                   public=read_public(paths.public))

    def aliases(self) -> AliasBook:
        return AliasBook.build(self.aliases_cfg, self.public,
                               self.decisions.get("aliasApprovals") or [])

    def layout(self) -> Layout:
        return Layout(self.layout_cfg)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


# ------------------------------------------------------------------ core
def normalize_raw(raw_doc: dict, bundle: Bundle, previous_tabs: dict | None = None,
                  accept_structure_change: bool = False) -> dict:
    return import_workbook(raw_doc["workbook"], bundle.aliases(), bundle.layout(),
                           previous_tabs=previous_tabs,
                           accept_structure_change=accept_structure_change)


def payload_for(normalized: dict | None, manifest: dict, bundle: Bundle, fixture: bool = False):
    recon = reconcile(normalized, bundle.public, bundle.decisions) if normalized else None
    payload = build_payload(normalized=normalized, recon=recon, manifest=manifest,
                            source=bundle.source, decisions=bundle.decisions,
                            hero_names=hero_names_from(bundle.public), fixture=fixture)
    return recon, payload


def run_import(data: bytes, *, bundle: Bundle, manifest: dict, previous_normalized: dict | None,
               source_url: str, at: str, accept_structure_change: bool = False,
               snapshot_file: str | None = None) -> dict:
    """Pure: workbook bytes -> every document a successful import writes.
    Raises WorkbookError / ImportFailed."""
    digest = sha256(data)
    raw = read_xlsx(data)
    prev = last_good(manifest)
    normalized = import_workbook(raw, bundle.aliases(), bundle.layout(),
                                 previous_tabs=(prev or {}).get("tabs"),
                                 accept_structure_change=accept_structure_change)
    inv = normalized["inventory"]
    entry = {
        "sha256": digest, "importedAt": at, "bytes": len(data), "sourceUrl": source_url,
        "sheetNames": [s["name"] for s in raw["sheets"]],
        "parserVersion": PARSER_VERSION, "layoutVersion": inv.get("layoutVersion"),
        "aliasesVersion": inv.get("aliasesVersion"), "sourceRowCount": source_row_count(raw),
        "counts": inv.get("counts"), "changes": changes(previous_normalized, normalized),
        "tabs": {t["name"]: t["purpose"] for t in inv["tabs"]},
        "status": "imported", "snapshotFile": snapshot_file,
    }
    manifest = dict(manifest)
    snaps = [s for s in manifest.get("snapshots") or [] if s.get("sha256") != digest]
    manifest["snapshots"] = (snaps + [entry])[-200:]
    manifest["lastGood"] = digest
    manifest["lastAttempt"] = {"at": at, "status": "ok", "sha256": digest}
    raw_doc = {"format": "gsheet-raw.v1", "sha256": digest, "importedAt": at,
               "sourceUrl": source_url, "spreadsheetId": bundle.source.get("spreadsheetId"),
               "parserVersion": PARSER_VERSION, "workbook": raw}
    recon, payload = payload_for(normalized, manifest, bundle)
    return {"raw": raw_doc, "normalized": normalized, "manifest": manifest,
            "recon": recon, "payload": payload, "sha256": digest}


# --------------------------------------------------------------- wrappers
def _write_outputs(paths: Paths, docs: dict) -> None:
    write_json(paths.raw, docs["raw"])
    write_json(paths.normalized, docs["normalized"])
    write_json(paths.manifest, docs["manifest"])
    _write_reports(paths, docs["normalized"], docs["recon"], docs["manifest"],
                   docs.get("source") or {})
    write_text(paths.export, render(docs["payload"]))
    lf = os.path.join(paths.reports, "last_failure.json")
    if os.path.exists(lf):
        os.remove(lf)
        md = os.path.join(paths.reports, "last_failure.md")
        if os.path.exists(md):
            os.remove(md)


def _write_reports(paths: Paths, normalized: dict, recon: dict | None, manifest: dict,
                   source: dict) -> None:
    inv = normalized.get("inventory") or {}
    snap = last_good(manifest)
    write_json(os.path.join(paths.reports, "inventory.json"), inv)
    write_text(os.path.join(paths.reports, "inventory.md"), inventory_markdown(inv, snap, source))
    if recon is not None:
        write_json(os.path.join(paths.reports, "reconciliation.json"), recon)
        write_text(os.path.join(paths.reports, "reconciliation.md"), reconciliation_markdown(recon))


def _fail(paths: Paths, bundle: Bundle, manifest: dict, stage: str, reason: str, at: str,
          digest: str | None = None, inventory: dict | None = None) -> dict:
    failure = {"at": at, "status": "failed", "stage": stage, "reason": reason,
               "sha256": digest, "lastGood": manifest.get("lastGood"),
               "inventory": inventory or {}}
    write_json(os.path.join(paths.reports, "last_failure.json"), failure)
    write_text(os.path.join(paths.reports, "last_failure.md"), failure_markdown(failure))
    manifest = dict(manifest)
    manifest["lastAttempt"] = {k: failure[k] for k in ("at", "status", "stage", "reason", "sha256")}
    write_json(paths.manifest, manifest)
    # Re-render the export from the LAST GOOD normalized data, so the site
    # says the latest sync failed — without replacing any imported record.
    normalized = read_json(paths.normalized)
    _, payload = payload_for(normalized, manifest, bundle)
    write_text(paths.export, render(payload))
    return {"status": "failed", "stage": stage, "reason": reason, "sha256": digest}


def sync(paths: Paths, *, data: bytes | None = None, source_url: str | None = None,
         force: bool = False, accept_structure_change: bool = False, opener=None,
         at: str | None = None) -> dict:
    """Download (unless `data` is given) and import. Returns a status dict:
    {'status': 'unchanged' | 'imported' | 'failed', ...}."""
    bundle = Bundle.load(paths)
    at = at or now_iso()
    manifest = read_json(paths.manifest) or empty_manifest(bundle.source)
    url = source_url or bundle.source.get("exportUrl")
    if data is None:
        try:
            data = download(url, timeout=float(bundle.source.get("timeoutSeconds") or 60),
                            max_bytes=int(bundle.source.get("maxDownloadBytes") or 50 * 2**20),
                            opener=opener)
        except FetchFailed as exc:
            return _fail(paths, bundle, manifest, "download", str(exc), at)
    digest = sha256(data)
    if not force and digest == manifest.get("lastGood"):
        return {"status": "unchanged", "sha256": digest}
    snap = None
    try:
        snap = os.path.basename(save_snapshot(data, paths.cache, at[:10]))
    except OSError:
        snap = None
    previous = read_json(paths.normalized)
    try:
        docs = run_import(data, bundle=bundle, manifest=manifest, previous_normalized=previous,
                          source_url=url, at=at, accept_structure_change=accept_structure_change,
                          snapshot_file=snap)
    except WorkbookError as exc:
        return _fail(paths, bundle, manifest, "read-workbook", str(exc), at, digest)
    except ImportFailed as exc:
        return _fail(paths, bundle, manifest, "validate", str(exc), at, digest, exc.inventory)
    docs["source"] = bundle.source
    _write_outputs(paths, docs)
    c = docs["normalized"]["inventory"]["counts"]
    return {"status": "imported", "sha256": digest, "counts": c,
            "changes": docs["manifest"]["snapshots"][-1]["changes"],
            "reconciliation": docs["recon"]["summary"]}


def rebuild(paths: Paths, write: bool = True) -> dict:
    """Re-read the COMMITTED raw layer with today's parser, aliases, layout
    and decisions — no download. Returns the documents (and writes them)."""
    bundle = Bundle.load(paths)
    manifest = read_json(paths.manifest) or empty_manifest(bundle.source)
    raw_doc = read_json(paths.raw)
    normalized = normalize_raw(raw_doc, bundle) if raw_doc else None
    recon, payload = payload_for(normalized, manifest, bundle)
    if write:
        if normalized is not None:
            write_json(paths.normalized, normalized)
            _write_reports(paths, normalized, recon, manifest, bundle.source)
        write_text(paths.export, render(payload))
    return {"normalized": normalized, "recon": recon, "payload": payload, "manifest": manifest}


def check(paths: Paths) -> list[str]:
    """CI gate: the committed normalized layer and public export must be
    exactly what a rebuild from the committed raw layer produces."""
    fresh = rebuild(paths, write=False)
    problems = []
    if fresh["normalized"] is not None:
        try:
            with open(paths.normalized, encoding="utf-8") as f:
                committed = f.read()
        except OSError:
            committed = None
        if committed != dumps(fresh["normalized"]):
            problems.append("data/external/gsheet/normalized.json is not what the committed raw "
                            "layer + configs produce — run: python3 pipeline/sheet_sync.py rebuild")
    try:
        with open(paths.export, encoding="utf-8") as f:
            committed_js = f.read()
    except OSError:
        committed_js = None
    if committed_js != render(fresh["payload"]):
        problems.append("assets/data/external_sheet.v1.js is out of date (raw layer, configs, "
                        "decisions or the published VOD dataset changed) — run: "
                        "python3 pipeline/sheet_sync.py rebuild")
    return problems
