"""
workbook.py — the raw cell layer.

An xlsx export is turned into one JSON document that keeps EVERYTHING the
parser might ever want, for every worksheet — including tabs and columns
today's parser does not understand: every non-empty cell value (dates kept
as dates), merged ranges, hyperlinks, hidden state. It is committed as
data/external/gsheet/raw/cells.json, so a better parser can re-read an old
snapshot (`sheet_sync.py rebuild`) without downloading it again.

The document is a pure function of the workbook's cell contents: keys are
sorted and nothing time-dependent is added here, so the same workbook always
yields byte-identical JSON.
"""
from __future__ import annotations

import datetime as dt
import io
from typing import Any

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


def read_xlsx(data: bytes) -> dict:
    """xlsx bytes -> raw cell document {sheets: [...]}."""
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
    return {"format": "xlsx-cells.v1", "sheets": sheets}


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
