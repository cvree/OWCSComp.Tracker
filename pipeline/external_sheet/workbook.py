"""
workbook.py — the raw cell layer.

An xlsx export is turned into one JSON document that keeps EVERYTHING the
parser might ever want, for every worksheet — including tabs and columns
today's parser does not understand: every non-empty cell value (dates kept
as dates), merged ranges, hyperlinks, hidden state. It is committed as
data/external/gsheet/raw/ (index.json + one gzip file per tab), so a better parser can re-read an old
snapshot (`sheet_sync.py rebuild`) without downloading it again.

The document is a pure function of the workbook's cell contents: keys are
sorted and nothing time-dependent is added here, so the same workbook always
yields byte-identical JSON.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import posixpath
import zipfile
from typing import Any
from xml.etree import ElementTree as ET

try:                                   # optional until an import actually runs
    import openpyxl
    from openpyxl.utils import get_column_letter, range_boundaries
except ImportError:                    # pragma: no cover - reported by caller
    openpyxl = None


class WorkbookError(RuntimeError):
    """The bytes are not a readable xlsx workbook."""


def _cell_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        if v.time() == dt.time(0, 0):
            return {"date": v.date().isoformat()}
        return {"date": v.date().isoformat(), "datetime": v.isoformat()}
    if isinstance(v, dt.date):
        return {"date": v.isoformat()}
    if isinstance(v, dt.time):
        return {"time": v.isoformat()}
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        v = v.replace("\r\n", "\n").strip()
        return v or None
    if isinstance(v, (int, float, bool)):
        return v
    return str(v)


def package_profile(data: bytes) -> dict | None:
    """What the xlsx zip is made of — so a 60 MB workbook can be explained
    (embedded images? one enormous tab?) without opening it in Excel."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
    except zipfile.BadZipFile:
        return None
    sheets = sorted(((i.filename, i.file_size) for i in infos
                     if i.filename.startswith("xl/worksheets/") and i.filename.endswith(".xml")),
                    key=lambda x: -x[1])
    media = [i for i in infos if i.filename.startswith("xl/media/")]
    largest = sorted(infos, key=lambda i: -i.file_size)[:8]
    return {
        "bytes": len(data),
        "members": len(infos),
        "uncompressedBytes": sum(i.file_size for i in infos),
        "worksheetXml": [{"path": p, "bytes": b} for p, b in sheets[:40]],
        "mediaFiles": len(media),
        "mediaBytes": sum(i.file_size for i in media),
        "largestMembers": [{"path": i.filename, "bytes": i.file_size} for i in largest],
    }


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "pr": "http://schemas.openxmlformats.org/package/2006/relationships"}
_RID = "{%s}id" % _NS["r"]


def _rels(z: zipfile.ZipFile, path: str) -> dict[str, dict]:
    try:
        root = ET.fromstring(z.read(path))
    except (KeyError, ET.ParseError):
        return {}
    return {r.get("Id"): {"target": r.get("Target"), "mode": r.get("TargetMode")}
            for r in root.findall("pr:Relationship", _NS)}


def _sheet_extras(data: bytes) -> dict[str, dict]:
    """{sheet name: {state, merged[], links{}}} read straight from the xlsx
    XML. openpyxl's streaming (read-only) mode does not expose merged
    ranges, hyperlinks or hidden state, so they are read here with an
    incremental parser that never holds a whole sheet in memory."""
    out: dict[str, dict] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        wb_rels = _rels(z, "xl/_rels/workbook.xml.rels")
        for sh in wb.findall("m:sheets/m:sheet", _NS):
            rel = wb_rels.get(sh.get(_RID)) or {}
            target = rel.get("target") or ""
            path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(
                posixpath.join("xl", target))
            info = {"state": sh.get("state") or "visible", "merged": [], "links": {}}
            out[sh.get("name")] = info
            if not path.startswith("xl/worksheets/") or path not in z.namelist():
                continue
            sheet_rels = _rels(z, posixpath.join(posixpath.dirname(path), "_rels",
                                                 posixpath.basename(path) + ".rels"))
            with z.open(path) as f:
                for _, el in ET.iterparse(f, events=("end",)):
                    tag = el.tag.rsplit("}", 1)[-1]
                    if tag == "mergeCell" and el.get("ref"):
                        info["merged"].append(el.get("ref"))
                    elif tag == "hyperlink" and el.get("ref"):
                        rel_t = (sheet_rels.get(el.get(_RID)) or {}).get("target")
                        target_url = rel_t or (("#" + el.get("location")) if el.get("location") else None)
                        if target_url:
                            ref = el.get("ref")
                            if ":" in ref and openpyxl is not None:
                                c1, r1, c2, r2 = range_boundaries(ref)
                                for r in range(r1, min(r2, r1 + 2000) + 1):
                                    for c in range(c1, min(c2, c1 + 200) + 1):
                                        info["links"][f"{get_column_letter(c)}{r}"] = target_url
                            else:
                                info["links"][ref] = target_url
                    if tag == "row":
                        el.clear()          # keep memory flat on huge sheets
            info["merged"].sort()
    return out


XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
EMBED = "{%s}embed" % _NS["r"]
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _resolve(base_dir: str, target: str) -> str:
    """A relationship target, relative ('../media/x.png') or absolute ('/xl/media/x.png')."""
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(base_dir, target))


def image_anchors(data: bytes) -> dict[str, list[dict]]:
    """{sheet name: [{cell, media, hash}]} for every picture anchored in a
    worksheet drawing."""
    out: dict[str, list[dict]] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        wb_rels = _rels(z, "xl/_rels/workbook.xml.rels")
        hashes: dict[str, str] = {}
        for sh in wb.findall("m:sheets/m:sheet", _NS):
            target = (wb_rels.get(sh.get(_RID)) or {}).get("target") or ""
            path = _resolve("xl", target)
            rels = _rels(z, posixpath.join(posixpath.dirname(path), "_rels", posixpath.basename(path) + ".rels"))
            found = []
            for rel in rels.values():
                t = rel.get("target") or ""
                if "drawings/" not in t:
                    continue
                dpath = _resolve(posixpath.dirname(path), t)
                if dpath not in names:
                    continue
                drels = _rels(z, posixpath.join(posixpath.dirname(dpath), "_rels", posixpath.basename(dpath) + ".rels"))
                root = ET.fromstring(z.read(dpath))
                for anchor in list(root):
                    frm = anchor.find(f"{{{XDR}}}from")
                    blip = anchor.find(f".//{{{A}}}blip")
                    if frm is None or blip is None:
                        continue
                    col = int(frm.find(f"{{{XDR}}}col").text)
                    row = int(frm.find(f"{{{XDR}}}row").text)
                    emb = blip.get(EMBED)
                    mt = (drels.get(emb) or {}).get("target") or ""
                    mpath = _resolve(posixpath.dirname(dpath), mt)
                    if mpath in names and mpath not in hashes:
                        hashes[mpath] = hashlib.sha1(z.read(mpath)).hexdigest()[:10]
                    found.append({"cell": a1(row, col), "media": posixpath.basename(mpath),
                                  "hash": hashes.get(mpath)})
            out[sh.get("name")] = found
    return out


def read_xlsx(data: bytes) -> dict:
    """xlsx bytes -> raw cell document {sheets: [...]}.

    Streams cell values with openpyxl's read-only mode (memory stays flat
    however large the workbook is) and reads merged ranges, hyperlinks and
    hidden state from the sheet XML directly."""
    if openpyxl is None:
        raise WorkbookError("openpyxl is not installed — pip install -r requirements.txt")
    if not data[:2] == b"PK":
        head = data[:200].decode("utf-8", "replace").lower()
        hint = (" (the response is an HTML page — the sheet is probably not "
                "shared publicly, or Google asked for a sign-in)"
                if "<html" in head or "<!doctype" in head else "")
        raise WorkbookError("the download is not an xlsx file" + hint)
    try:
        extras = _sheet_extras(data)
        anchors = image_anchors(data)
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception as exc:  # openpyxl / zipfile / xml raise many types
        raise WorkbookError(f"could not open the workbook: {exc}") from exc
    sheets = []
    try:
        for index, ws in enumerate(wb.worksheets):
            if hasattr(ws, "reset_dimensions"):
                ws.reset_dimensions()       # never trust the file's own <dimension>
            rows: list[list] = []
            max_col = 0
            for row in ws.iter_rows():
                values = [_cell_value(getattr(c, "value", None)) for c in row]
                while values and values[-1] is None:
                    values.pop()
                if not values:
                    continue
                r = next((c.row for c in row if getattr(c, "row", None)), None)
                if r is None:
                    continue
                rows.append([r, values])
                max_col = max(max_col, len(values))
            ex = extras.get(ws.title) or {"state": "visible", "merged": [], "links": {}}
            imgs = sorted([i["cell"], i["hash"]] for i in anchors.get(ws.title) or [] if i.get("hash"))
            sheets.append({
                "name": ws.title,
                "index": index,
                "state": ex["state"],
                "maxRow": rows[-1][0] if rows else 0,
                "maxColumn": max_col,
                "rows": rows,
                "merged": ex["merged"],
                "links": dict(sorted(ex["links"].items())),
                # Pictures anchored in the tab: [cell, content hash]. A sheet
                # that shows heroes as icons stores them here, not in cells.
                "images": imgs,
            })
    finally:
        wb.close()
    return {"format": "xlsx-cells.v1", "sheets": sheets, "package": package_profile(data)}


def read_xlsx_full(data: bytes) -> dict:
    """The previous, whole-workbook-in-memory reader. Kept as the reference
    the streaming reader is tested against (test_external_sheet.py)."""
    if openpyxl is None:
        raise WorkbookError("openpyxl is not installed — pip install -r requirements.txt")
    if not data[:2] == b"PK":
        head = data[:200].decode("utf-8", "replace").lower()
        hint = (" (the response is an HTML page — the sheet is probably not "
                "shared publicly, or Google asked for a sign-in)"
                if "<html" in head or "<!doctype" in head else "")
        raise WorkbookError("the download is not an xlsx file" + hint)
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    except Exception as exc:  # openpyxl raises many types for a bad zip
        raise WorkbookError(f"could not open the workbook: {exc}") from exc
    sheets = []
    for index, ws in enumerate(wb.worksheets):
        rows: list[list] = []
        links: dict[str, str] = {}
        for row in ws.iter_rows():
            values = []
            for c in row:
                values.append(_cell_value(c.value))
                link = getattr(c, "hyperlink", None)
                target = getattr(link, "target", None) if link else None
                if target:
                    links[c.coordinate] = str(target)
            while values and values[-1] is None:
                values.pop()
            if values:
                rows.append([row[0].row, values])
        merged = sorted(str(r) for r in ws.merged_cells.ranges)
        sheets.append({
            "name": ws.title,
            "index": index,
            "state": ws.sheet_state or "visible",
            "maxRow": ws.max_row,
            "maxColumn": ws.max_column,
            "rows": rows,
            "merged": merged,
            "links": dict(sorted(links.items())),
        })
    return {"format": "xlsx-cells.v1", "sheets": sheets, "package": package_profile(data)}


def grid(sheet: dict) -> tuple[list[list[Any]], dict[tuple[int, int], tuple[int, int]]]:
    """(dense 2-D grid, merged-origin map) for one raw sheet.

    grid[r][c] is 0-based. Every cell inside a merged range carries the
    range's top-left value — that is what a person reading the sheet sees —
    and `origin[(r, c)]` records where that value really lives, so
    provenance can say 'merged from A3' instead of pretending each cell
    held it."""
    rows = sheet.get("rows") or []
    if not rows:
        return [], {}
    if openpyxl is None and sheet.get("merged"):
        # Without openpyxl merged ranges cannot be expanded, and silently
        # skipping them would change what the sheet says.
        raise WorkbookError("openpyxl is required to read merged cells — pip install -r requirements.txt")
    height = max(r for r, _ in rows)
    width = max([len(v) for _, v in rows] + [0])
    for rng in sheet.get("merged") or []:
        if openpyxl is not None:
            min_col, min_row, max_col, max_row = range_boundaries(rng)
            height, width = max(height, max_row), max(width, max_col)
    g: list[list[Any]] = [[None] * width for _ in range(height)]
    for r, values in rows:
        for c, v in enumerate(values):
            g[r - 1][c] = v
    origin: dict[tuple[int, int], tuple[int, int]] = {}
    if openpyxl is not None:
        for rng in sheet.get("merged") or []:
            min_col, min_row, max_col, max_row = range_boundaries(rng)
            top = g[min_row - 1][min_col - 1]
            for r in range(min_row - 1, max_row):
                for c in range(min_col - 1, max_col):
                    if (r, c) != (min_row - 1, min_col - 1):
                        g[r][c] = top
                        origin[(r, c)] = (min_row - 1, min_col - 1)
    return g, origin


def a1(row0: int, col0: int) -> str:
    """0-based (row, col) -> 'B12'."""
    if openpyxl is None:
        letters, n = "", col0 + 1
        while n:
            n, rem = divmod(n - 1, 26)
            letters = chr(65 + rem) + letters
        return f"{letters}{row0 + 1}"
    return f"{get_column_letter(col0 + 1)}{row0 + 1}"


def source_row_count(raw: dict) -> int:
    return sum(len(s.get("rows") or []) for s in raw.get("sheets") or [])
