#!/usr/bin/env python3
"""
build_cards_fixture.py — a SYNTHETIC workbook in the real sheet's MATCH-CARD
layout (docs/SHEET-LAYOUT-NOTES.md), with heroes as embedded pictures.

Not the real community sheet. Nothing in it is a real result except the one
card deliberately mirroring the VOD milestone (Al Qadsiah vs Twisted Minds,
Nepal as map 1, Stage 2 playoffs), which exists so reconciliation has a real
VOD map to link to WITHOUT a date. Every other team and player is invented
and named to look invented ('Fixture …').

  Landing Page        the season year ('… Series 2026') and editor credit
  Hero Index          role icons with their label to the right (row 2); hero
                      pictures with the name in the cell below; one hero
                      our catalog does not know ('Fixturehero')
  EMEA - Stage 2      card 1: QAD vs Twisted Minds, three maps, heroes up
                      to three per player, bans as text + pictures
                      card 2: Fixture Alpha vs Fixture Beta: a Push map
                      (distances in the map list), a draw, a forfeited ban,
                      and a tiebreak map that has no map panel
                      card 3: scheduled — label only, no teams
  Japan - Stage 1     one card with an unknown hero and a picture the Hero
                      Index does not name
  Stage 2 Hero Bans   the sheet's own totals (summary — never imported)
  Map Index           reference
  Self-Check          the sheet's own counts: EMEA Stage 2 agrees, Japan
                      Stage 1 deliberately does not, NA Stage 2 has no tab

Needs Pillow (to make the pictures):
    python3 pipeline/fixtures/gsheet/build_cards_fixture.py
Writes pipeline/fixtures/gsheet/owcs_cards_fixture.xlsx (committed).
"""
from __future__ import annotations

import hashlib
import io
import os

import openpyxl
from openpyxl.drawing.image import Image as XImage
from openpyxl.utils import get_column_letter

try:
    from PIL import Image as PImage, ImageDraw
except ImportError:  # pragma: no cover
    raise SystemExit("build_cards_fixture.py needs Pillow: pip install pillow")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "owcs_cards_fixture.xlsx")

HEROES = ["D.Va", "Mauga", "Sigma", "Winston", "Shion", "Symmetra", "Sojourn", "Tracer",
          "Cassidy", "Ana", "Kiriko", "Juno", "Lúcio", "Mercy", "Pharah", "Fixturehero"]
ROLES = {"TANK": "Tank", "DAMAGE": "Damage", "SUPPORT": "Support"}
_PNG: dict[str, bytes] = {}


def png(key: str) -> bytes:
    """A small, unique, deterministic picture per key."""
    if key not in _PNG:
        d = hashlib.sha1(key.encode("utf-8")).digest()
        im = PImage.new("RGB", (16, 16), (d[0], d[1], d[2]))
        dr = ImageDraw.Draw(im)
        for i in range(8):
            dr.point((d[3 + i] % 16, d[11 + i] % 16), fill=(255 - d[0], 255 - d[1], 255 - d[2]))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        _PNG[key] = buf.getvalue()
    return _PNG[key]


def put_pic(ws, ref: str, key: str) -> None:
    img = XImage(io.BytesIO(png(key)))
    img.anchor = ref
    ws.add_image(img)


def cell(col0: int, row1: int) -> str:
    return f"{get_column_letter(col0 + 1)}{row1}"


N, STEP = 13, 17                         # first map panel at column N, every 17 columns
STARTS = [0, 3, 7, 10, 13]               # player column offsets inside a panel
ICONS_A = {"Damage": "role:DAMAGE", "Tank": "role:TANK", "Support": "role:SUPPORT"}
ICONS_B = {"Damage": "role2:DAMAGE", "Tank": "role2:TANK", "Support": "role2:SUPPORT"}
ORDER = ["Damage", "Damage", "Tank", "Support", "Support"]


def card(ws, top: int, *, event: str, fmt: str, label: str, teams=None, series=None,
         maplist=(), panels=(), potm=None) -> None:
    """One match card whose event label is on row `top` (1-based).

    teams   = ((codeA, fullA), (codeB, fullB))
    maplist = [(wonA, scoreA, map, scoreB, wonB), …]  (merged two-row cells)
    panels  = [dict(map=, a=[(name, [heroes…])×5], b=…, banA=, banB=,
                    banPicA=, banPicB=, winner=, score=)]
    """
    b = top
    ws[f"C{b}"] = event
    ws[f"C{b + 1}"] = fmt
    ws[f"C{b + 2}"] = label
    if not teams:
        return
    (ca, fa), (cb, fb) = teams
    ws[f"C{b + 4}"], ws[f"J{b + 4}"] = ca, cb
    put_pic(ws, f"E{b + 4}", "logo:" + ca)
    put_pic(ws, f"I{b + 4}", "logo:" + cb)
    if series:
        ws[f"G{b + 4}"], ws[f"H{b + 4}"] = series
    for i, (wa, sa, mp, sb, wb) in enumerate(maplist):
        r = b + 9 + 2 * i
        for col_letter, v in (("C", wa), ("D", sa), ("F", mp), ("J", sb), ("K", wb)):
            if v is not None:
                ws[f"{col_letter}{r}"] = v
        ws.merge_cells(f"F{r}:I{r + 1}")
        put_pic(ws, f"E{r}", "mode:" + mp)
    if potm:
        ws[f"C{b + 21}"] = f"Player of the Match:\n{potm}"
    for k, pan in enumerate(panels):
        p = N + k * STEP
        for side, row_names, hero_rows, icons in (("a", b, (b + 1, b + 2), ICONS_A),
                                                   ("b", b + 22, (b + 18, b + 20), ICONS_B)):
            for i, (pname, heroes) in enumerate(pan[side]):
                s = p + STARTS[i]
                put_pic(ws, cell(s, row_names), icons[ORDER[i]])
                ws[cell(s + 1, row_names)] = pname
                # Up to three pictures per player: two on the first hero
                # row (span columns s, s+2), the third on the second row.
                spots = [(hero_rows[0], s), (hero_rows[0], s + 2), (hero_rows[1], s)]
                for (r, c), h in zip(spots, heroes):
                    put_pic(ws, cell(c, r), h if h.startswith("raw:") else "hero:" + h)
        ws[cell(p, b + 3)] = f"{fa} [0-0]"
        ws[cell(p + 10, b + 3)] = pan["map"]
        put_pic(ws, cell(p + 10, b + 5), "map:" + pan["map"])
        put_pic(ws, cell(p, b + 6), "logo:" + ca)
        ws[cell(p + 3, b + 6)], ws[cell(p + 6, b + 6)] = f"{ca} Ban", "◄"
        ws[cell(p + 7, b + 6)], ws[cell(p + 8, b + 6)] = "►", f"{cb} Ban"
        for off, key in ((3, "banPicA"), (7, "banPicB")):
            if pan.get(key):
                put_pic(ws, cell(p + off, b + 7), pan[key])
        put_pic(ws, cell(p, b + 11), "logo:" + cb)
        ws[cell(p + 10, b + 13)] = pan["winner"]
        if pan.get("banA"):
            ws[cell(p + 3, b + 14)] = pan["banA"]
        if pan.get("banB"):
            ws[cell(p + 7, b + 14)] = pan["banB"]
        ws[cell(p, b + 15)] = f"{fb} [0-0]"
        ws[cell(p + 10, b + 16)] = f"Map: {pan['score']}"
        ws[cell(p + 13, b + 16)] = f"Series: {pan.get('series', '')}"


def landing(wb) -> None:
    ws = wb.create_sheet("Landing Page")
    ws["B1"] = "Overwatch Champions Series 2026 — fixture workbook (synthetic)"
    ws["G3"] = "Editor Contacts:\n@fixture-editor"


def hero_index(wb) -> None:
    ws = wb.create_sheet("Hero Index")
    for i, label in enumerate(ROLES):
        c = 1 + 6 * i
        put_pic(ws, cell(c, 2), "role:" + label)
        ws[cell(c + 1, 2)] = label
    for i, h in enumerate(HEROES):
        r = 3 + 2 * (i // 8)
        c = 1 + (i % 8)
        put_pic(ws, cell(c, r), "hero:" + h)
        ws[cell(c, r + 1)] = h


QAD = [("FixQ-Dps1", ["Shion"]), ("FixQ-Dps2", ["Symmetra"]), ("FixQ-Tank", ["Mauga"]),
       ("FixQ-Sup1", ["Kiriko"]), ("FixQ-Sup2", ["Juno", "Lúcio"])]
TM = [("FixT-Dps1", ["Sojourn"]), ("FixT-Dps2", ["Symmetra"]), ("FixT-Tank", ["D.Va"]),
      ("FixT-Sup1", ["Lúcio"]), ("FixT-Sup2", ["Kiriko"])]


def emea(wb) -> None:
    ws = wb.create_sheet("EMEA - Stage 2")
    card(ws, 3, event="OWCS EMEA", fmt="Playoffs Upper Final [Bo3]", label="Week 3 | Day 2 | Game 1",
         teams=(("QAD", "Al Qadsiah"), ("TM", "Twisted Minds")), series=(1, 2),
         maplist=[(None, 1, "Nepal", 2, "◄"), ("►", 3, "Rialto", 2, None),
                  (None, 0, "Colosseo", 2, "◄")],
         potm="FixT-Sup2",
         panels=[
             dict(map="Nepal", a=QAD, b=TM, banA="Tracer", banB="Ana", banPicA="hero:Tracer",
                  banPicB="hero:Ana", winner="Twisted Minds Wins!", score="1-2", series="0-1"),
             dict(map="Rialto", a=[(n, ["Cassidy" if i == 0 else h[0]]) for i, (n, h) in enumerate(QAD)],
                  b=TM, banA="Pharah", banB="Mercy", winner="Al Qadsiah Wins!", score="3-2", series="1-1"),
             dict(map="Colosseo", a=QAD, b=[(n, ["Tracer", "Sojourn"] if i == 0 else h) for i, (n, h) in enumerate(TM)],
                  banA="Kiriko", banB="Juno", winner="Twisted Minds Wins!", score="0-2", series="1-2"),
         ])
    alpha = [("Alpha-1", ["Tracer"]), ("Alpha-2", ["Sojourn"]), ("Alpha-3", ["Winston"]),
             ("Alpha-4", ["Ana"]), ("Alpha-5", ["Kiriko"])]
    beta = [("Beta-1", ["Cassidy", "Tracer", "Pharah"]), ("Beta-2", ["Symmetra"]), ("Beta-3", ["Sigma"]),
            ("Beta-4", ["Juno"]), ("Beta-5", ["Lúcio"])]
    card(ws, 29, event="OWCS EMEA", fmt="Round Robin [Bo5]", label="Week 1 | Day 1 | Game 2",
         teams=(("FXA", "Fixture Alpha"), ("FXB", "Fixture Beta")), series=(1, 2),
         maplist=[("►", 2, "Busan", 1, None), (None, 70.36, "Runasapi", 109, "◄"),
                  (None, 3, "Midtown", 3, None), (None, 1, "Watchpoint: Gibraltar", 2, "◄")],
         panels=[
             dict(map="Busan", a=alpha, b=beta, banA="Forfeit", banB="Forfeit",
                  winner="Fixture Alpha Wins!", score="2-1"),
             dict(map="Runasapi", a=alpha, b=beta, banA="Mercy", banB="D.Va",
                  winner="Fixture Beta Wins!", score="0-1"),
             dict(map="Midtown", a=alpha, b=beta, banA="Winston", banB="Shion",
                  winner="Draw!", score="3-3"),
         ])
    card(ws, 55, event="OWCS EMEA", fmt="Round Robin [Bo5]", label="Week 4 | Day 1 | Game 1")


def japan(wb) -> None:
    ws = wb.create_sheet("Japan - Stage 1")
    g = [("Gamma-1", ["Fixturehero"]), ("Gamma-2", ["raw:unnamed-picture"]), ("Gamma-3", ["D.Va"]),
         ("Gamma-4", ["Ana"]), ("Gamma-5", ["Kiriko"])]
    d = [("Delta-1", ["Tracer"]), ("Delta-2", ["Sojourn"]), ("Delta-3", ["Winston"]),
         ("Delta-4", ["Juno"]), ("Delta-5", ["Lúcio"])]
    card(ws, 3, event="OWCS Japan", fmt="Round Robin [Bo3]", label="Week 1 | Day 1 | Game 1",
         teams=(("FXG", "Fixture Gamma"), ("FXD", "Fixture Delta")), series=(2, 0),
         maplist=[("►", 2, "Busan", 0, None), ("►", 3, "Rialto", 1, None)],
         panels=[dict(map="Busan", a=g, b=d, banA="Mercy", banB="Juno",
                      winner="Fixture Gamma Wins!", score="2-0"),
                 dict(map="Rialto", a=g, b=d, banA="Ana", banB="Sigma",
                      winner="Fixture Gamma Wins!", score="3-1")])


def totals(wb) -> None:
    ws = wb.create_sheet("Stage 2 Hero Bans")
    ws.append(["Hero", "Bans", "Share"])
    ws.append(["Tracer", 1, 0.1])
    ws.append(["Mercy", 2, 0.2])
    ws = wb.create_sheet("Map Index")
    ws["B2"] = "Control"
    ws["B4"] = "Nepal"
    put_pic(ws, "B6", "map:Nepal")


def self_check(wb) -> None:
    ws = wb.create_sheet("Self-Check")
    ws["B2"] = "Region"
    for c, h in (("E", "Total\nHero Bans"), ("F", "Total\nMap Picks"), ("H", "Maps\n(Spreadsheet)"),
                 ("I", "Matches\n(Spreadsheet)")):
        ws[f"{c}2"] = h
    rows = [("Stage 1", "JP", 4, 3, 1),          # wrong on purpose: the card has 2 maps
            ("Stage 2", "EMEA", 14, 7, 2),       # agrees: 3 + 4 maps, 2 played cards
            (None, "NA", 0, 0, 0),               # no 'North America - Stage 2' tab here
            ("SUM", None, 18, 10, 3)]
    for i, (stage, region, bans, maps, matches) in enumerate(rows):
        r = 3 + i
        if stage:
            ws[f"B{r}"] = stage
        if region:
            ws[f"D{r}"] = region
        ws[f"E{r}"], ws[f"H{r}"], ws[f"I{r}"] = bans, maps, matches


def build() -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    landing(wb)
    emea(wb)
    japan(wb)
    totals(wb)
    hero_index(wb)
    self_check(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


if __name__ == "__main__":
    data = build()
    with open(OUT, "wb") as f:
        f.write(data)
    print(f"wrote {OUT} ({len(data)} bytes)")
