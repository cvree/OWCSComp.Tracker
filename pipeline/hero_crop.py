#!/usr/bin/env python3
"""
hero_crop.py — frame a hero's official splash art on the hero.

Blizzard's own hero pages serve one wide splash per hero. It is a *scene*,
not a portrait: the hero stands somewhere off-centre in a depth-of-field
blurred map location, and the bottom third is a flat colour extension the
marketing site puts its own text over. A naive centre-square crop of that
(what this repo did until now) reliably produced a thumbnail that is 60%
empty backdrop with the hero's head clipped off the top edge — at 40px in
a stats table, literally unrecognisable.

The fix is to find the hero before cropping. Two signals do it, and both
come from how the art is made rather than from any per-hero tuning:

  * The flat extension has no detail at all. Row detail (mean |Laplacian|)
    collapses to ~0 across it, so the live region is found by walking in
    from the edges while rows stay flat.

  * The hero is the only thing in focus. The background is bokeh-blurred by
    the renderer, so a block-wise sharpness map lights up on the character
    and stays dark everywhere else. The largest connected blob of
    above-threshold blocks is the hero.

From that blob we take a square that keeps the head with a little headroom
rather than centring on the blob's middle — a portrait framed like a
portrait. Everything here is derived per image; there is no hand-tuned
per-hero table, so a hero added to the roster tomorrow frames itself.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Box:
    """A crop rectangle in pixels."""
    x: int
    y: int
    w: int
    h: int

    @property
    def x1(self) -> int:
        return self.x + self.w

    @property
    def y1(self) -> int:
        return self.y + self.h


def _detail_rows(gray) -> "list[float]":
    """Per-row mean absolute Laplacian — how much is going on in each row."""
    import cv2
    import numpy as np

    lap = cv2.Laplacian(cv2.GaussianBlur(gray, (3, 3), 0), cv2.CV_32F, ksize=3)
    return np.abs(lap).mean(axis=1)


def live_region(img) -> Box:
    """The part of the splash that is actual artwork.

    Blizzard pads the composition with a flat colour band (usually at the
    bottom, occasionally letterboxed top and bottom) for its own page
    layout. Those rows carry no detail, so we walk in from each edge while
    rows stay below a floor derived from the image's own detail
    distribution — never a fixed pixel count, which would be a guess about
    a layout that can change.
    """
    import numpy as np

    h, w = img.shape[:2]
    gray = _to_gray(img)
    rows = _detail_rows(gray)
    if rows.size == 0:
        return Box(0, 0, w, h)

    # A floor relative to this image's own busiest rows: anything under a
    # small fraction of the 90th-percentile row is "flat", which survives
    # both a near-black night scene and a bright beach one.
    strong = float(np.percentile(rows, 90))
    floor = max(strong * 0.10, 0.35)

    top = 0
    while top < h - 1 and rows[top] < floor:
        top += 1
    bottom = h
    while bottom > top + 1 and rows[bottom - 1] < floor:
        bottom -= 1

    # Never trust the trim to eat the picture: keep at least half the frame.
    if bottom - top < h * 0.5:
        return Box(0, 0, w, h)
    return Box(0, top, w, bottom - top)


def _to_gray(img):
    import cv2

    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def sharpness_map(img, block: int = 16):
    """Block-wise local sharpness, normalised to 0..1.

    The renderer blurs everything that is not the hero, so this is a
    subject mask in all but name.
    """
    import cv2
    import numpy as np

    gray = _to_gray(img).astype(np.float32)
    h, w = gray.shape[:2]
    bh, bw = max(1, h // block), max(1, w // block)
    small_h, small_w = bh * block, bw * block
    gray = cv2.resize(gray, (small_w, small_h), interpolation=cv2.INTER_AREA)

    lap = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
    tiles = lap.reshape(bh, block, bw, block)
    energy = tiles.std(axis=(1, 3))

    energy = cv2.GaussianBlur(energy, (0, 0), 1.0)
    lo, hi = float(energy.min()), float(energy.max())
    if hi - lo < 1e-6:
        return np.zeros_like(energy)
    return (energy - lo) / (hi - lo)


@dataclass(frozen=True)
class Subject:
    """Where the hero is.

    `box` is everything in focus — the figure plus whatever it is holding
    or standing next to. `core` is just the figure: the run of columns that
    are as tall as the tallest column in the mask. Framing uses `core`,
    because the thing that makes a portrait unrecognisable is a prop
    dragging the crop sideways off the face.
    """
    box: Box
    core: Box
    head_x: int


def subject(img, block: int = 16) -> "Subject | None":
    """The in-focus subject, in image pixels.

    Takes the sharpest blocks, keeps the largest connected component of
    them (so a sharp logo on a wall in the background cannot drag the crop
    away from the hero), and returns its extent. Returns None when the
    image has no dominant in-focus region — a caller should then fall back
    to a plain geometric crop rather than invent a subject.

    The component's own bounding box is not enough to frame on. Torbjörn
    stands beside a turret, Freja beside a ship, Doomfist beside a raised
    gauntlet — all in focus, all part of the same blob, all of them pulling
    the box's centre away from the hero's face. So the figure is isolated
    from its props by column height: the hero is a full-height silhouette
    and the props are not, so the run of columns whose vertical extent
    rivals the tallest column *is* the figure. That run becomes `core`, and
    framing works off it.
    """
    import cv2
    import numpy as np

    energy = sharpness_map(img, block)
    if energy.size == 0:
        return None

    # Otsu on the energy map: it splits "in focus" from "blurred backdrop"
    # from the image's own histogram instead of a magic constant.
    as_u8 = (energy * 255).astype(np.uint8)
    _, mask = cv2.threshold(as_u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8), iterations=2)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 1:
        return None
    # Label 0 is background; pick the biggest real component.
    areas = stats[1:, cv2.CC_STAT_AREA]
    best = int(np.argmax(areas)) + 1
    if areas.max() < mask.size * 0.01:
        return None

    x, y, w, h = (int(stats[best, cv2.CC_STAT_LEFT]), int(stats[best, cv2.CC_STAT_TOP]),
                  int(stats[best, cv2.CC_STAT_WIDTH]), int(stats[best, cv2.CC_STAT_HEIGHT]))

    figure = (labels == best)
    core_bx, core_bw, core_by, core_bh = _core_columns(figure, x, y, w, h)
    head_bx = _head_x(figure, core_bx, core_bw, core_by, core_bh)

    sy = img.shape[0] / labels.shape[0]
    sx = img.shape[1] / labels.shape[1]
    scale = lambda v, s: int(round(v * s))  # noqa: E731
    return Subject(
        box=Box(scale(x, sx), scale(y, sy), scale(w, sx), scale(h, sy)),
        core=Box(scale(core_bx, sx), scale(core_by, sy),
                 max(1, scale(core_bw, sx)), max(1, scale(core_bh, sy))),
        head_x=scale(head_bx, sx),
    )


def _head_x(figure, cx: int, cw: int, cy: int, ch: int) -> float:
    """Where the head sits horizontally, in block coordinates.

    The mass centre of the top band of the figure. A pose with one arm
    thrown out sideways has its body centre well away from its face; this
    puts the face back in the middle of the frame.
    """
    import numpy as np

    band = max(1, int(round(ch * 0.24)))
    top = figure[cy:cy + band, cx:cx + cw]
    weights = top.sum(axis=0).astype("float64")
    if weights.sum() <= 0:
        return cx + cw / 2.0
    cols = np.arange(weights.size, dtype="float64")
    return cx + float((cols * weights).sum() / weights.sum())


def _core_columns(figure, x: int, y: int, w: int, h: int):
    """The figure without its props, in block coordinates.

    Mask density per column, smoothed, split into runs of columns that
    carry real weight. The hero is the heaviest run: a body is a wide solid
    silhouette, while a gun barrel, a rocket trail or a turret leg is
    narrow, so total mask area separates them where a bounding box cannot.
    Ties break upward — of two comparable runs, the one whose top edge sits
    higher is the one with the head on it.
    """
    import numpy as np

    if not figure.any():
        return x, w, y, h

    density = figure.sum(axis=0).astype("float64")
    if density.size >= 3:
        density = np.convolve(density, np.ones(3) / 3.0, mode="same")
    peak = float(density.max())
    if peak <= 0:
        return x, w, y, h

    live_cols = density >= peak * 0.35
    runs: list[tuple[int, int]] = []
    start = None
    for c, on in enumerate(live_cols):
        if on and start is None:
            start = c
        elif not on and start is not None:
            runs.append((start, c - 1))
            start = None
    if start is not None:
        runs.append((start, live_cols.size - 1))
    if not runs:
        return x, w, y, h

    def score(run):
        left, right = run
        band = figure[:, left:right + 1]
        area = float(band.sum())
        rows = np.where(band.any(axis=1))[0]
        top = int(rows[0]) if rows.size else figure.shape[0]
        # Reaching higher in the frame is worth up to a third more area:
        # enough to prefer a head-bearing silhouette over an equally heavy
        # prop beside it, not enough to pick a thin antenna over a body.
        lift = 1.0 + 0.33 * (1.0 - top / max(1, figure.shape[0]))
        return area * lift

    left, right = max(runs, key=score)
    band = figure[:, left:right + 1]
    rows = np.where(band.any(axis=1))[0]
    if rows.size == 0:
        return x, w, y, h
    return left, right - left + 1, int(rows[0]), int(rows[-1] - rows[0] + 1)


def portrait_box(img, aspect: float = 1.0, *, headroom: float = 0.12,
                 fill: float = 0.86, block: int = 16) -> Box:
    """Where to crop for a portrait of the hero at the given aspect ratio.

    `fill` is how much of the crop's height the subject should occupy, and
    `headroom` is the share of the crop left above the subject's top edge —
    together they frame a head-and-shoulders shot rather than a centred
    rectangle that happens to contain a person.

    Falls back to a top-biased crop of the live region when no subject is
    found, which is still far better than a centre crop: these splashes
    always put the character above the flat band, never below it.
    """
    live = live_region(img)
    view = img[live.y:live.y1, live.x:live.x1]
    subj = subject(view, block)

    if subj is None:
        side_h = min(live.h, int(round(live.w / aspect)))
        side_w = int(round(side_h * aspect))
        x = live.x + (live.w - side_w) // 2
        return _clamp(Box(x, live.y, side_w, side_h), img)

    figure = subj.core
    # Height that makes the subject fill `fill` of the frame, bounded so a
    # tiny far-away subject is not upscaled into mush and a huge one is not
    # cropped tighter than the live region allows.
    want_h = int(round(figure.h / max(fill, 0.2)))
    want_h = max(want_h, int(live.h * 0.34))
    want_h = min(want_h, live.h)
    want_w = int(round(want_h * aspect))
    if want_w > live.w:
        want_w = live.w
        want_h = min(live.h, int(round(want_w / aspect)))

    # Centre between the head and the figure's own middle: all head puts a
    # leaning pose's body out of frame, all body puts the face in a corner.
    cx = live.x + int(round(subj.head_x * 0.55 + (figure.x + figure.w / 2) * 0.45))
    x = cx - want_w // 2
    y = live.y + figure.y - int(round(want_h * headroom))

    return _clamp(Box(x, y, want_w, want_h), img, live)


def _clamp(box: Box, img, live: Box | None = None) -> Box:
    """Keep a box inside the image (and inside the live region if given)."""
    h, w = img.shape[:2]
    bx, by = (live.x, live.y) if live else (0, 0)
    bw, bh = (live.w, live.h) if live else (w, h)

    cw = min(box.w, bw)
    ch = min(box.h, bh)
    x = max(bx, min(box.x, bx + bw - cw))
    y = max(by, min(box.y, by + bh - ch))
    return Box(int(x), int(y), int(cw), int(ch))


def crop(img, box: Box):
    return img[box.y:box.y1, box.x:box.x1]


def render(img, size: int, aspect: float = 1.0, **kw):
    """Crop to the subject and resize to `size` px on the long edge."""
    import cv2

    box = portrait_box(img, aspect, **kw)
    out = crop(img, box)
    if out.size == 0:
        return None
    w = size if aspect >= 1 else int(round(size * aspect))
    h = size if aspect <= 1 else int(round(size / aspect))
    interp = cv2.INTER_AREA if out.shape[0] > h else cv2.INTER_CUBIC
    return cv2.resize(out, (max(1, w), max(1, h)), interpolation=interp)
