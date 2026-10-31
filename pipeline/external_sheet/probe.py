"""
probe.py — print what a workbook actually contains, to design its layout.

Read-only and text-only: for every tab, its size and its first rows (cell
reference + value), and where embedded images are anchored (cell + which
media file, by content hash, so the same icon used 300 times is visibly one
picture). Used when a real sheet does not match the configured layout:

    python3 pipeline/sheet_sync.py probe --xlsx FILE
    (or the sheet-sync workflow with `probe: true`, which prints it to the log)
"""
from __future__ import annotations

import hashlib
import io
import posixpath
import zipfile
from xml.etree import ElementTree as ET

from .workbook import _NS, _RID, _rels, a1, grid, read_xlsx

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


def cell_text(v) -> str:
    if isinstance(v, dict):
        return v.get("date") or v.get("time") or str(v)
    return str(v).replace("\n", "⏎")


def probe(data: bytes, rows_per_tab: int = 30, cols: int = 40, width: int = 28) -> str:
    raw = read_xlsx(data)
    anchors = image_anchors(data)
    lines = []
    pkg = raw.get("package") or {}
    lines.append(f"WORKBOOK {pkg.get('bytes')} bytes, {len(raw['sheets'])} tabs, "
                 f"{pkg.get('mediaFiles')} media files")
    all_hashes: dict[str, int] = {}
    for imgs in anchors.values():
        for i in imgs:
            all_hashes[i["hash"]] = all_hashes.get(i["hash"], 0) + 1
    lines.append(f"IMAGES anchored: {sum(all_hashes.values())}, distinct pictures: {len(all_hashes)}")
    for sheet in raw["sheets"]:
        g, _ = grid(sheet)
        imgs = anchors.get(sheet["name"]) or []
        lines.append("")
        lines.append(f"=== TAB {sheet['index'] + 1}: {sheet['name']!r} state={sheet['state']} "
                     f"rows={len(sheet['rows'])} maxRow={sheet['maxRow']} maxCol={sheet['maxColumn']} "
                     f"merged={len(sheet['merged'])} links={len(sheet['links'])} images={len(imgs)}")
        shown = 0
        for r, row in enumerate(g):
            cells = [f"{a1(r, c)}={cell_text(v)[:width]}" for c, v in enumerate(row[:cols])
                     if v not in (None, "")]
            if not cells:
                continue
            lines.append("  " + " | ".join(cells))
            shown += 1
            if shown >= rows_per_tab:
                break
        if imgs:
            sample = ", ".join(f"{i['cell']}:{i['hash']}" for i in imgs[:40])
            lines.append(f"  IMAGES (cell:picture) {sample}{' …' if len(imgs) > 40 else ''}")
        if sheet["merged"]:
            lines.append(f"  MERGED (first 15): {', '.join(sheet['merged'][:15])}")
    return "\n".join(lines) + "\n"


def dump(data: bytes, spec: str, hero_names: dict | None = None) -> str:
    """Every cell of one range, images shown in place: 'Tab!A1:CR27'.
    Each picture is printed as IMG:<hash>, or as <name> when the Hero Index
    tab maps it (picture above, name below)."""
    from openpyxl.utils import range_boundaries
    tab, rng = spec.rsplit("!", 1)
    raw = read_xlsx(data)
    anchors = image_anchors(data)
    names = hero_names if hero_names is not None else hero_index(raw, anchors)
    sheet = next(s for s in raw["sheets"] if s["name"] == tab)
    g, origin = grid(sheet)
    c1, r1, c2, r2 = range_boundaries(rng)
    imgs: dict[str, list[str]] = {}
    for i in anchors.get(tab) or []:
        imgs.setdefault(i["cell"], []).append(i["hash"])
    out = [f"DUMP {spec}"]
    for r in range(r1 - 1, min(r2, len(g))):
        cells = []
        for c in range(c1 - 1, c2):
            ref = a1(r, c)
            v = g[r][c] if c < len(g[r]) else None
            bits = []
            if v not in (None, "") and (r, c) not in origin:
                bits.append(cell_text(v)[:30])
            for h in imgs.get(ref, []):
                bits.append("IMG:" + (names.get(h) or h))
            if bits:
                cells.append(f"{ref}={'/'.join(bits)}")
        if cells:
            out.append("  " + " | ".join(cells))
    return "\n".join(out) + "\n"


def hero_index(raw: dict, anchors: dict) -> dict[str, str]:
    """{picture hash: name} from a 'Hero Index'-style tab: a picture in one
    row, its name in the cell directly below."""
    out: dict[str, str] = {}
    for sheet in raw["sheets"]:
        if "index" not in sheet["name"].lower():
            continue
        g, _ = grid(sheet)
        from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
        for i in anchors.get(sheet["name"]) or []:
            col_l, row = coordinate_from_string(i["cell"])
            c, r = column_index_from_string(col_l) - 1, row - 1
            below = g[r + 1][c] if r + 1 < len(g) and c < len(g[r + 1]) else None
            if isinstance(below, str) and below.strip() and i["hash"] not in out:
                out[i["hash"]] = below.strip()
    return out
