#!/usr/bin/env python3
"""
build_icon.py — the brand mark, drawn once, written everywhere.

THE MARK: five portrait slots on the corners of a regular pentagon, ink on
a lime tile. Five slots are a team's five heroes; the pentagon's order from
the top (one, two, two) is the 1-2-2 lineup the site reads in its own role
order, Tank, Damage, Support. The pentagon itself is never drawn: the eye
closes it (Gestalt closure), so the mark stays five simple shapes and still
reads as one unit, the comp.

Construction (on a 32-unit grid; docs/BRAND.md has the reasoning):

  tile    32 x 32, corner radius 7.2 (22.5%, the app-icon proportion);
          lime, --accent-hi at the top left to --accent at the bottom right
  slots   6.6 square, corner radius 1.8 (27%, the hero tiles' rounding);
          ink, --accent-ink
  layout  slot centres on a regular pentagon of circumradius 9.2 centred
          at (16, 16.58): the slot group is centred, then lifted 0.3 so it
          sits on the optical centre, not the geometric one. Neighbouring
          slots are all 10.8 apart, so the gaps share one rhythm.
  margin  >= 3.9 on every side (12%), clear space for the tile's own edge

One generator, every surface, so they can never drift apart:

  assets/img/favicon.svg                 the browser tab, every page
  assets/img/brand/mark.svg              the tile, for anywhere an <img> is wanted
  assets/img/brand/mark-lime.svg         slots only, lime, for dark grounds
  assets/img/brand/mark-ink.svg          slots only, ink, for light grounds
  assets/img/brand/apple-touch-icon.png  180 x 180, full bleed (iOS rounds it)
  desktop/assets/owcs.ico                installer, executable (16 … 256)
  desktop/assets/owcs.png                tray, control room, setup

Raster sizes at or below 32 px snap every slot edge to whole pixels, so the
16 px icon is five crisp squares rather than five smudges. Pure stdlib and
deterministic: running it twice writes byte-identical files, which is what
lets the tests assert the committed files match.

    python3 desktop/build_icon.py            # write every file above
    python3 desktop/build_icon.py --check    # verify the committed files
"""
from __future__ import annotations

import argparse
import math
import os
import struct
import sys

SIZES = (16, 24, 32, 48, 64, 128, 256)

# The site's own tokens (assets/css/owcs.css).
ACCENT_HI = (0xD6, 0xF2, 0x90)   # --accent-hi, tile top left
ACCENT = (0xC7, 0xE7, 0x70)      # --accent, tile bottom right
INK = (0x0E, 0x12, 0x06)         # --accent-ink, the slots
TRANSPARENT = (0, 0, 0, 0)

GRID = 32.0
TILE_RADIUS = 7.2
SLOT = 6.6
SLOT_RADIUS = 1.8
PENTAGON_R = 9.2
CENTRE = (16.0, 16.58)
#: Tank at the top, then Damage, then Support: vertices at -90°, -18°, 54°,
#: 126° and 198°, listed top first, then left to right down the pentagon.
ANGLES = (-90.0, 198.0, -18.0, 126.0, 54.0)


def slot_centres() -> list[tuple[float, float]]:
    cx, cy = CENTRE
    return [(round(cx + PENTAGON_R * math.cos(math.radians(a)), 3),
             round(cy + PENTAGON_R * math.sin(math.radians(a)), 3)) for a in ANGLES]


# ------------------------------------------------------------------- SVG
def _num(v: float) -> str:
    return ("%.2f" % v).rstrip("0").rstrip(".")


def _slots_svg(fill: str) -> str:
    h = SLOT / 2
    return "".join('<rect x="%s" y="%s" width="%s" height="%s" rx="%s" fill="%s"/>' % (
        _num(x - h), _num(y - h), _num(SLOT), _num(SLOT), _num(SLOT_RADIUS), fill) for x, y in slot_centres())


def _hex(c: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % c


def svg_tile(title: str = "OWCS Comp Tracker") -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" role="img" aria-label="%s">'
            '<defs><linearGradient id="t" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="%s"/><stop offset="1" stop-color="%s"/></linearGradient></defs>'
            '<rect width="32" height="32" rx="%s" fill="url(#t)"/>%s</svg>\n') % (
        title, _hex(ACCENT_HI), _hex(ACCENT), _num(TILE_RADIUS), _slots_svg(_hex(INK)))


def svg_slots(fill: tuple[int, int, int]) -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" role="img" '
            'aria-label="OWCS Comp Tracker">%s</svg>\n') % _slots_svg(_hex(fill))


# ---------------------------------------------------------------- raster
def _in_rrect(x: float, y: float, x0: float, y0: float, x1: float, y1: float, r: float) -> bool:
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    cx = x0 + r if x < x0 + r else (x1 - r if x > x1 - r else x)
    cy = y0 + r if y < y0 + r else (y1 - r if y > y1 - r else y)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= r * r


def render(size: int, full_bleed: bool = False) -> list[list[tuple[int, int, int, int]]]:
    """One RGBA image, top row first, 4 x 4 samples a pixel."""
    k = size / GRID
    snap = size <= 32
    boxes = []
    h = SLOT / 2
    w = max(2, round(SLOT * k - 0.3))       # 16 → 3, 24 → 5, 32 → 6: gaps stay open
    # Snapped, the five slots must stay a mirror image: an odd slot width
    # cannot centre on an even canvas, so the axis moves half a pixel left
    # rather than the top slot alone sitting off it.
    axis = size / 2 - (0.5 if (w % 2) != (size % 2) else 0.0)
    radius = SLOT_RADIUS * k if not snap or size >= 24 else 0.6
    for i, (cx, cy) in enumerate(slot_centres()):
        x0, y0 = (cx - h) * k, (cy - h) * k
        if snap:
            y0 = round(y0)
            if i == 0:
                x0 = axis - w / 2
            elif i % 2:                        # left of the pair
                x0 = round(axis + (cx * k - size / 2) - w / 2)
            else:                              # right: mirror its partner
                x0 = 2 * axis - (boxes[i - 1][0] + w)
            boxes.append((x0, y0, x0 + w, y0 + w, radius))
        else:
            boxes.append((x0, y0, x0 + SLOT * k, y0 + SLOT * k, radius))
    tr = 0.0 if full_bleed else TILE_RADIUS * k
    n = 4
    pixels = []
    for py in range(size):
        row = []
        for px in range(size):
            tile_hits = ink_hits = 0
            for sy in range(n):
                for sx in range(n):
                    x, y = px + (sx + 0.5) / n, py + (sy + 0.5) / n
                    if tr and not _in_rrect(x, y, 0, 0, size, size, tr):
                        continue
                    tile_hits += 1
                    if any(_in_rrect(x, y, *b) for b in boxes):
                        ink_hits += 1
            if not tile_hits:
                row.append(TRANSPARENT)
                continue
            t = (px + py + 1) / (2 * size)            # the diagonal gradient
            bg = tuple(round(a + (b - a) * t) for a, b in zip(ACCENT_HI, ACCENT))
            f = ink_hits / tile_hits
            rgb = tuple(round(c * (1 - f) + i * f) for c, i in zip(bg, INK))
            row.append(rgb + (round(255 * tile_hits / (n * n)),))
        pixels.append(row)
    return pixels


def _bmp_dib(pixels: list[list[tuple[int, int, int, int]]]) -> bytes:
    """A 32bpp bottom-up BGRA DIB with the AND mask an ICO still expects."""
    size = len(pixels)
    header = struct.pack(
        "<IiiHHIIiiII",
        40,            # biSize
        size,          # biWidth
        size * 2,      # biHeight — colour plane + mask, per the ICO format
        1, 32, 0,      # planes, bpp, compression
        size * size * 4, 0, 0, 0, 0)
    body = bytearray()
    for row in reversed(pixels):          # bottom-up
        for (r, g, b, a) in row:
            body += bytes((b, g, r, a))
    # AND mask: fully transparent pixels masked out, rows padded to 4 bytes.
    mask = bytearray()
    stride = ((size + 31) // 32) * 4
    for row in reversed(pixels):
        bits = bytearray(stride)
        for x, (_r, _g, _b, a) in enumerate(row):
            if a == 0:
                bits[x // 8] |= 0x80 >> (x % 8)
        mask += bits
    return header + bytes(body) + bytes(mask)


#: At and above this size the entry is stored as PNG rather than a raw DIB.
#: Windows Vista onwards reads PNG-compressed icon entries, and a 256x256
#: 32bpp DIB is a quarter of a megabyte on its own — this keeps the committed
#: icon a few KB instead of a few hundred.
PNG_FROM = 64


def build_ico(sizes: tuple[int, ...] = SIZES) -> bytes:
    images = [build_png(s) if s >= PNG_FROM else _bmp_dib(render(s))
              for s in sizes]
    out = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    offset = 6 + 16 * len(images)
    for size, data in zip(sizes, images):
        out += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size, 0 if size >= 256 else size,
            0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for data in images:
        out += data
    return bytes(out)


def build_png(size: int = 128, full_bleed: bool = False) -> bytes:
    """A PNG of the same mark, for the tray (pystray wants a bitmap) and for
    anywhere a .ico is not accepted. Written with zlib, no image library."""
    import zlib
    pixels = render(size, full_bleed)
    raw = bytearray()
    for row in pixels:
        raw.append(0)                      # filter type 0
        for (r, g, b, a) in row:
            raw += bytes((r, g, b, a))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ICO_PATH = os.path.join(HERE, "assets", "owcs.ico")
PNG_PATH = os.path.join(HERE, "assets", "owcs.png")
BRAND = os.path.join(REPO, "assets", "img", "brand")


def outputs() -> dict[str, bytes]:
    """Every file the generator owns: {absolute path: bytes}."""
    return {
        ICO_PATH: build_ico(),
        PNG_PATH: build_png(),
        os.path.join(REPO, "assets", "img", "favicon.svg"): svg_tile().encode(),
        os.path.join(BRAND, "mark.svg"): svg_tile().encode(),
        os.path.join(BRAND, "mark-lime.svg"): svg_slots(ACCENT).encode(),
        os.path.join(BRAND, "mark-ink.svg"): svg_slots(INK).encode(),
        os.path.join(BRAND, "apple-touch-icon.png"): build_png(180, full_bleed=True),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="verify the committed files match this generator")
    args = parser.parse_args(argv)

    files = outputs()
    if args.check:
        problems = []
        for path, expected in files.items():
            try:
                with open(path, "rb") as f:
                    actual = f.read()
            except OSError as exc:
                problems.append(f"{path}: {exc}")
                continue
            if actual != expected:
                problems.append(
                    f"{path} does not match build_icon.py "
                    f"({len(actual)} bytes on disk, {len(expected)} generated)")
        for problem in problems:
            print(f"FAIL {problem}")
        print("OK icon files match their generator" if not problems else "")
        return 1 if problems else 0

    for path, data in files.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        print(f"wrote {os.path.relpath(path, REPO)} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
