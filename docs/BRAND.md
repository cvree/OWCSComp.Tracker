# The brand mark

One mark, everywhere: the browser tab, the header, the footer, the home-screen
icon, the Windows installer, the tray and the control room. Before this there
were four (a "CT" text tile in the header, a lime target in the tab, violet and
cyan bars on the desktop app, and a gold hexagon left in a stylesheet), and none
of them said what the site does.

![The mark](../assets/img/brand/mark.svg)

## What it says

**Five portrait slots on the corners of a pentagon.** Five slots are a team's
five heroes. Read from the top, the pentagon is one, two, two: the 1-2-2 lineup,
in the order the site always lists roles (Tank, Damage, Support). The slots are
the same rounded squares as the hero tiles on every page, so the mark looks like
the product's own furniture, not a badge stuck on top of it.

The pentagon itself is never drawn. Five evenly spaced shapes are enough for the
eye to close the outline by itself (Gestalt closure), so the mark stays five
simple shapes and still reads as one unit: the comp.

## Why it is built the way it is

The choices below are the usual tests a mark has to pass. Each one was checked
on rendered candidates, not argued in the abstract. Six concepts were drawn at
16, 24, 32, 64 and 128 px on a dark tab and a light one. The rejected ones are
listed at the end.

| Test | What the mark does |
| --- | --- |
| **Recognisable at 16 px** | Five solid squares, no lines, no text. A drawn outline (a ring joining the slots) blurred into a smudge at 16 px and was dropped. |
| **Silhouette** | A filled lime tile, not a dark one. The dark-tile versions disappeared on dark browser tabs; the lime tile holds on light and dark. |
| **One colour** | Works as lime on dark, ink on light, or any single colour (`mark-lime.svg`, `mark-ink.svg`). The gradient is a finish, never load-bearing. |
| **Built from the system** | Lime is `--accent-hi` to `--accent`, the slots are `--accent-ink`. The brand is the one place the accent is spent besides published facts (docs/DESIGN.md, rule 1). |
| **Optical, not only geometric, centre** | The slot group is centred in the tile and then lifted 0.3 units: a shape centred exactly looks low. |
| **Even rhythm** | On a regular pentagon every neighbouring pair of slots is the same distance apart (10.8 units), so all the gaps match. |
| **Symmetry survives pixels** | At 16, 24 and 32 px every slot edge snaps to a whole pixel, and the five are snapped as a mirrored set. A 3 px slot cannot centre on a 16 px canvas, so the whole mark moves half a pixel rather than the top slot alone sitting off-centre. A symmetry error reads far worse than a half-pixel shift. |
| **Not borrowed** | No Overwatch or OWCS logo shapes, no circle-and-break, no hero likenesses. The site is an independent fan project and its mark says so by looking like itself. |

## Construction

On a 32-unit grid (`desktop/build_icon.py` holds the numbers):

- **Tile:** 32 × 32, corner radius 7.2 (22.5%, the app-icon proportion).
  Diagonal gradient `#d6f290` → `#c7e770`.
- **Slots:** 6.6 square, corner radius 1.8 (27%, the hero tiles' rounding).
  Ink `#0e1206`.
- **Layout:** slot centres on a regular pentagon of circumradius 9.2, centred at
  (16, 16.58). Vertices at −90° (the tank, top), −18° / 198° (damage) and
  54° / 126° (support).
- **Clear space:** at least 3.9 units (12%) inside the tile on every side.
  Outside the tile, keep a quarter of the tile's width clear.

## The lockup

Mark, then the name in Saira Condensed caps, tracked +0.045em: **OWCS** a shade
back (`--tx-2`), **COMP TRACKER** in full white. The product's own name carries the
weight. The mark is 28 px in the header and 22 px in the footer, and the control
room uses the same lockup. The mono tagline ("every map · every comp") sits under
the name on wide screens only.

## Files

`python3 desktop/build_icon.py` writes every one of these from the numbers above.
`--check` (run by the tests) fails if any of them drifts from the generator.

| File | Used for |
| --- | --- |
| `assets/img/favicon.svg` | the browser tab, every page |
| `assets/img/brand/mark.svg` | the tile, anywhere an `<img>` is wanted |
| `assets/img/brand/mark-lime.svg` | slots only, lime, for dark grounds |
| `assets/img/brand/mark-ink.svg` | slots only, ink, for light grounds |
| `assets/img/brand/apple-touch-icon.png` | iOS / Android home screen (180 px, full bleed) |
| `desktop/assets/owcs.ico` | installer and executable, 16 to 256 px |
| `desktop/assets/owcs.png` | tray, control room, setup |

The header and footer draw the slots inline (`BRAND_MARK` in `app/shell.js`) with
the tile in CSS. `pipeline/test_brand.py` holds that copy to the generator.

## Rejected candidates

- **Slots on a dark tile.** The cleanest drawing, but invisible at 16 px on a
  dark tab.
- **Slots joined by a ring.** Reads as a molecule or a network diagram, and the
  ring becomes a grey haze at small sizes.
- **Dots instead of squares.** Lost the link to the hero tiles and read as a
  flower at 16 px.
- **A chevron of five slots** ("rising"). Too wide for a square tile; at 16 px it
  was a dotted line.
- **A pentagon shield.** Bold, but generic: a hundred esports badges look like it.
