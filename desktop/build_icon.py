#!/usr/bin/env python3
"""
build_icon.py — generate the application icon with no image library.

The installer, the executable and the tray all need a real multi-resolution
.ico. Pulling in Pillow just to draw six squares would put an image library on
the critical path of the build, so this writes the ICO container and its
32-bit BGRA bitmaps directly. Pure stdlib, deterministic output — running it
twice produces byte-identical files, which is what lets the packaging test
assert the committed icon matches its generator.

The mark: a dark rounded tile with five ascending bars — the five HUD portrait
slots the tracker reads — under a single accent sweep.

    python3 desktop/build_icon.py            # rewrite desktop/assets/owcs.ico
    python3 desktop/build_icon.py --check     # verify the committed file
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

SIZES = (16, 24, 32, 48, 64, 128, 256)

# Palette — the site's own dark surface plus its violet/cyan accents.
BG = (0x0B, 0x0D, 0x14)          # near-black tile
BG_EDGE = (0x1A, 0x1F, 0x2E)     # subtle rim
BAR = (0x8B, 0x5C, 0xF6)         # violet
BAR_HI = (0x22, 0xD3, 0xEE)      # cyan, for the tallest bar
TRANSPARENT = (0, 0, 0, 0)


def _rounded(x: float, y: float, size: float, radius: float) -> bool:
    """Inside a rounded square occupying the whole tile?"""
    lo, hi = radius, size - radius
    cx = lo if x < lo else (hi if x > hi else x)
    cy = lo if y < lo else (hi if y > hi else y)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= radius * radius


def render(size: int) -> list[list[tuple[int, int, int, int]]]:
    """One RGBA image, top row first."""
    pixels = [[TRANSPARENT] * size for _ in range(size)]
    radius = max(2.0, size * 0.22)
    inset = max(0.5, size * 0.02)

    # Five bars, ascending, with the tallest picked out in cyan.
    n = 5
    margin = size * 0.18
    span = size - 2 * margin
    gap = span * 0.16 / (n - 1)
    bar_w = (span - gap * (n - 1)) / n
    base = size - margin * 0.95
    heights = [0.28, 0.44, 0.60, 0.86, 0.52]

    for py in range(size):
        for px in range(size):
            x, y = px + 0.5, py + 0.5
            if not _rounded(x - inset, y - inset, size - 2 * inset, radius):
                continue
            # Rim: the outermost ring of the tile is a touch lighter.
            edge = not _rounded(x - inset - 1, y - inset - 1,
                                size - 2 * inset - 2, radius)
            r, g, b = BG_EDGE if edge else BG

            for i in range(n):
                left = margin + i * (bar_w + gap)
                if left <= x <= left + bar_w:
                    top = base - span * heights[i]
                    if y >= top:
                        r, g, b = BAR_HI if heights[i] == max(heights) else BAR
                    break
            pixels[py][px] = (r, g, b, 255)
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


def build_png(size: int = 128) -> bytes:
    """A PNG of the same mark, for the tray (pystray wants a bitmap) and for
    anywhere a .ico is not accepted. Written with zlib, no image library."""
    import zlib
    pixels = render(size)
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
ICO_PATH = os.path.join(HERE, "assets", "owcs.ico")
PNG_PATH = os.path.join(HERE, "assets", "owcs.png")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="verify the committed files match this generator")
    args = parser.parse_args(argv)

    ico, png = build_ico(), build_png()
    if args.check:
        problems = []
        for path, expected in ((ICO_PATH, ico), (PNG_PATH, png)):
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

    os.makedirs(os.path.dirname(ICO_PATH), exist_ok=True)
    with open(ICO_PATH, "wb") as f:
        f.write(ico)
    with open(PNG_PATH, "wb") as f:
        f.write(png)
    print(f"wrote {ICO_PATH} ({len(ico)} bytes, sizes {', '.join(map(str, SIZES))})")
    print(f"wrote {PNG_PATH} ({len(png)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
