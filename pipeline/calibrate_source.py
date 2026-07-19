#!/usr/bin/env python3
"""
calibrate_source.py — computational HUD calibration for an OWCS broadcast.

Derives the ten hero-portrait boxes from the broadcast's own structure
instead of hand-placed rectangles. The OWCS HUD renders each player as an
[ult-charge chip][portrait] cell in a uniform-pitch row below the team bar:
the chips are small, saturated, solid-color squares — the most stable
computer-detectable anchor on the HUD — so calibration works like this:

  1. sample several representative frames (gameplay, fights, round starts),
  2. in each frame, scan the top HUD band for high-saturation chip blobs,
  3. per screen side, fit the best 5-blob uniform-pitch row (pitch fitted,
     outliers rejected — stray UI pixels can't poison the row),
  4. aggregate chip geometry across frames with medians (a frame where the
     HUD is covered simply contributes nothing),
  5. place each portrait next to its chip and let local TEXTURE decide the
     cell direction per side (portrait art has far more edge detail than
     the bar background), refining the offset within a small search range,
  6. validate: pitch uniformity, side symmetry, in-bounds boxes, per-slot
     texture — each failure becomes an explicit reason,
  7. write a reusable layout profile (native 1920x1080 pixel rects +
     resolution-independent normalized rects + calibration metadata) and an
     annotated calibration sheet for human review.

The profile is refused (no file written, exit 2) when confidence is below
the floor — better no calibration than boxes on wallpaper.

Usage:
  python pipeline/calibrate_source.py --frames-dir work/nepal/calib_frames \
      --source-id owcs-jksix-qwc --out layouts/owcs_jksix_qwc.json
  python pipeline/calibrate_source.py --clip work/clips/nepal.mp4 \
      --times 60,120,300,600,900 --source-id owcs-jksix-qwc \
      --out layouts/owcs_jksix_qwc.json
"""
from __future__ import annotations
import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

CALIB_VERSION = "calib-v1"
NATIVE_W, NATIVE_H = 1920, 1080

# The HUD chip row lives in the upper band of the frame on every OWCS
# broadcast package we have seen; searching a generous band keeps this
# robust to package tweaks without letting mid-screen effects in.
BAND_TOP_FRAC, BAND_BOT_FRAC = 0.03, 0.26

SAT_MIN = 110          # chips are saturated solid colors (team-tinted)
VAL_MIN = 90
CHIP_W_FRAC = (0.012, 0.09)   # chip width / frame width; the high end
                              # admits chip+portrait MERGED blobs — the
                              # edge-grid fit sorts those out
CHIP_ASPECT = (0.55, 1.9)     # w/h of a chip blob
MIN_GOOD_FRAMES = 2
CONFIDENCE_FLOOR = 0.55


# ------------------------------------------------------------ frame supply
def frames_from_clip(clip: str, times: list[float], out_dir: str) -> list[str]:
    """Extract one PNG per timestamp (seconds into the clip) with ffmpeg."""
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for t in times:
        out = os.path.join(out_dir, f"calib_{int(t):06d}.png")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
               "-ss", str(t), "-i", clip, "-frames:v", "1", "-y", out]
        subprocess.run(cmd, check=True)
        if os.path.exists(out):
            paths.append(out)
    return paths


# ------------------------------------------------------------- chip finder
def find_chip_blobs(frame_bgr) -> list[tuple[int, int, int, int]]:
    """Saturated solid blobs in the top HUD band, chip-sized, any hue."""
    h, w = frame_bgr.shape[:2]
    y0, y1 = int(h * BAND_TOP_FRAC), int(h * BAND_BOT_FRAC)
    band = frame_bgr[y0:y1]
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 1] >= SAT_MIN) & (hsv[:, :, 2] >= VAL_MIN)).astype(
        np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    blobs = []
    wmin = CHIP_W_FRAC[0] * w
    wmax = 0.45 * w         # chip chains (chip+portrait+chip...) stay in;
    hmin, hmax = 0.018 * h, 0.10 * h   # but the row height is chip-like
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if bw < wmin or bw > wmax or not (hmin <= bh <= hmax):
            continue
        aspect = bw / bh
        if CHIP_ASPECT[0] <= aspect <= CHIP_ASPECT[1]:
            # chip-shaped: must be solid
            if area < 0.4 * bw * bh:
                continue
        elif aspect > CHIP_ASPECT[1] and area >= 0.3 * bw * bh:
            # wider than a chip but still row-shaped: a chip merged with
            # its neighbouring portrait (or a whole chain of cells) —
            # its edges still land on the slot grid
            pass
        else:
            continue
        blobs.append((int(x), int(y + y0), int(bw), int(bh)))
    return blobs


def fit_uniform_rows(blobs: list, frame_w: int,
                     expect: int = 5, top_k: int = 8) -> list[dict]:
    """Top-K candidate grids for one side (see fit_uniform_row)."""
    cands: list[dict] = []
    seen = set()
    for cand in _iter_grid_fits(blobs, frame_w, expect):
        key = (round(cand["pitch"]), cand["xs"][0] // 4, cand["edge"])
        if key in seen:
            continue
        seen.add(key)
        cands.append(cand)
    cands.sort(key=lambda c: (-c["inliers"], c["residual"]))
    return cands[:top_k]


def _iter_grid_fits(blobs: list, frame_w: int, expect: int = 5):
    """Fit a 5-position uniform-pitch grid to the chip blobs of one side.

    Chips regularly MERGE with the colorful portrait art next to them into
    one wider blob, so exact 5-blob rows can't be assumed. Instead this does
    a small RANSAC over arithmetic progressions on blob EDGES: when the
    portrait sits right of its chip the blob's LEFT edge is still the chip's
    left edge (and mirrored for the other direction), so one of the two edge
    families always lands on a clean 5-slot grid. Missing chips are filled
    from the fitted pitch. Returns
    {'xs','y','w','h','pitch','residual','inliers','edge'} or None.
    """
    if len(blobs) < 3:
        return
    pitch_lo, pitch_hi = 0.030 * frame_w, 0.095 * frame_w
    for edge in ("left", "right"):
        pts = sorted(set(
            b[0] if edge == "left" else b[0] + b[2] for b in blobs))
        n = len(pts)
        for i in range(n):
            for j in range(i + 1, n):
                span = pts[j] - pts[i]
                for k in range(1, expect):
                    pitch = span / k
                    if not (pitch_lo <= pitch <= pitch_hi):
                        continue
                    tol = max(3.0, 0.08 * pitch)
                    # anchor the grid on pts[i] at each possible slot index
                    for a0 in range(0, expect - k):
                        x0 = pts[i] - a0 * pitch
                        grid = [x0 + t * pitch for t in range(expect)]
                        hits, resid = [], 0.0
                        for gx in grid:
                            d = min((abs(p - gx), p) for p in pts)
                            if d[0] <= tol:
                                hits.append(d[1])
                                resid += d[0]
                        if len(hits) < 3:
                            continue
                        hit_blobs = [
                            b for b in blobs
                            if (b[0] if edge == "left" else b[0] + b[2])
                            in hits]
                        # chip size: min dimension resists merged blobs
                        bw = int(statistics.median(
                            min(b[2], b[3]) for b in hit_blobs))
                        bh = int(statistics.median(
                            min(b[2], b[3]) for b in hit_blobs))
                        xs = ([int(round(g)) for g in grid]
                              if edge == "left" else
                              [int(round(g - bw)) for g in grid])
                        yield {
                            "xs": xs,
                            "y": int(statistics.median(
                                b[1] for b in hit_blobs)),
                            "w": bw, "h": bh,
                            "pitch": float(pitch),
                            "residual": float(resid / len(hits) / pitch),
                            "inliers": len(hits),
                            "edge": edge,
                        }


def verify_rows(frames_bgr: list, cands: list) -> list:
    """Score candidate grids against pixel evidence and re-rank.

    A REAL chip box is a solid patch of saturated team color with almost no
    texture; a grid that landed on portrait art or background shows either
    low saturation or high texture. vscore = saturated-pixel fraction minus
    a texture penalty, averaged over frames and slots."""
    if not cands:
        return cands
    hsvs = [cv2.cvtColor(f, cv2.COLOR_BGR2HSV) for f in frames_bgr]
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames_bgr]
    fh, fw = frames_bgr[0].shape[:2]
    for c in cands:
        sats, texs, stabs = [], [], []
        y, w, h = c["y"], c["w"], c["h"]
        oob = False
        for x in c["xs"]:
            if x < 0 or x + w > fw or y + h > fh:
                oob = True
                continue
            s_vals, t_vals = [], []
            for hsv, g in zip(hsvs, grays):
                box = hsv[y:y + h, x:x + w]
                s_vals.append(float(np.mean(
                    (box[:, :, 1] >= SAT_MIN) & (box[:, :, 2] >= VAL_MIN))))
                t_vals.append(texture(g[y:y + h, x:x + w]))
            # temporal stability: HUD chrome barely changes between frames
            # taken seconds apart; the game world behind a false grid does.
            diffs = []
            for i in range(len(grays) - 1):
                b1 = grays[i][y:y + h, x:x + w].astype(np.float32)
                b2 = grays[i + 1][y:y + h, x:x + w].astype(np.float32)
                diffs.append(float(np.mean(np.abs(b1 - b2))) / 255.0)
            stabs.append(1.0 - min(1.0, (statistics.median(diffs)
                                         if diffs else 1.0) * 4.0))
            sats.append(statistics.median(s_vals))
            texs.append(statistics.median(t_vals))
        if oob or not sats:
            c["vscore"] = -1.0
            continue
        sat = float(np.mean(sats))
        stab = float(np.mean(stabs))
        # texture is REWARDED (a row of portraits is textured; a chip row
        # scores via sat+stability instead) — what's punished is the
        # unstable game world and flat empty chrome.
        tex_reward = min(float(np.mean(texs)) / 1500.0, 1.0)
        # inlier bonus: an alias grid (half/mixed pitch) rarely explains
        # all five positions with real blob edges — the true grid does.
        c["vscore"] = round(0.55 * stab + 0.25 * sat + 0.20 * tex_reward
                            + 0.02 * c["inliers"], 4)
    cands.sort(key=lambda c: (-c["vscore"], -c["inliers"], c["residual"]))
    return cands


def pick_joint(cands_a: list, cands_b: list,
               frame_h: int = 1080) -> tuple:
    """Choose one grid per side, preferring pairs whose pitch AND row-y
    AGREE — the two sides render the same HUD package at the same height,
    which kills lookalike rows that exist on only one side (map pills,
    killfeed). Returns (row_a, row_b, warning|None)."""
    if not cands_a or not cands_b:
        return (cands_a[0] if cands_a else None,
                cands_b[0] if cands_b else None,
                "only one side produced grid candidates")
    y_tol = max(8, int(0.02 * frame_h))
    best, best_score = None, None
    for ca in cands_a:
        for cb in cands_b:
            dp = abs(ca["pitch"] - cb["pitch"]) / max(ca["pitch"],
                                                      cb["pitch"])
            if dp > 0.05 or abs(ca["y"] - cb["y"]) > y_tol:
                continue
            score = (ca.get("vscore", 0) + cb.get("vscore", 0) - 2 * dp,
                     ca["inliers"] + cb["inliers"],
                     -(ca["residual"] + cb["residual"]))
            if best_score is None or score > best_score:
                best, best_score = (ca, cb), score
    if best:
        return best[0], best[1], None
    return (cands_a[0], cands_b[0],
            "side pitches disagree (no agreeing candidate pair) — "
            "calibration is suspect")


def side_presence(frames_bgr: list, row: dict) -> int:
    """Frames in which >= 3 of the row's 5 cells show saturated pixels.

    Pixel-based on purpose: blob merging makes per-frame EDGE matching
    undercount, but a visible HUD row always lights its cells up."""
    count = 0
    y, w, h = row["y"], row["w"], row["h"]
    for f in frames_bgr:
        hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
        fh, fw = hsv.shape[:2]
        hits = 0
        for x in row["xs"]:
            if x < 0 or y < 0 or x + w > fw or y + h > fh:
                continue
            box = hsv[y:y + h, x:x + w]
            if float(np.mean((box[:, :, 1] >= 60)
                             & (box[:, :, 2] >= VAL_MIN))) >= 0.2:
                hits += 1
        if hits >= 3:
            count += 1
    return count


# --------------------------------------------------------- texture helpers
def texture(gray_crop) -> float:
    """Edge-detail score — portrait art scores far above flat UI chrome."""
    if gray_crop.size == 0:
        return 0.0
    return float(cv2.Laplacian(gray_crop, cv2.CV_64F).var())


def portrait_offset_direction(frame_gray, row: dict) -> tuple[int, float, float]:
    """+1 if portraits sit right of the chips, -1 if left.

    Interior cells can't disambiguate (left of chip N is portrait N-1 when
    the direction is +1 — equally textured), so only the row ENDS vote:
    if portraits are right of chips there is art right of the LAST chip and
    nothing left of the FIRST; mirrored otherwise."""
    h, w = frame_gray.shape[:2]
    size = max(row["h"], int(round(row["pitch"] * 0.5)))
    py = row["y"]

    def tex_at(px):
        if px < 0 or px + size > w or py + size > h:
            return 0.0
        return texture(frame_gray[py:py + size, px:px + size])

    r = tex_at(row["xs"][-1] + row["w"] + 1)   # right of last chip
    l = tex_at(row["xs"][0] - size - 1)        # left of first chip
    return (1, r, l) if r >= l else (-1, r, l)


# ---------------------------------------------------------- main pipeline
def _cell_score(frames_gray, hsvs, xs, y, size, fw, fh) -> float:
    """How portrait-like are the 5 cells at (xs+dx, y, size)?

    Portraits are temporally STABLE (same art all game), TEXTURED (real
    drawings, not chrome), and COLORFUL. Player-name text below the row is
    stable+textured but nearly colorless; the game world is colorful but
    unstable; chrome is stable but flat. The blend separates all three."""
    stabs, texs, sats, divs = [], [], [], []
    for x in xs:
        if x < 0 or x + size > fw or y < 0 or y + size > fh:
            return -1.0
        t_vals, d_vals, s_vals, h_vals = [], [], [], []
        for i, g in enumerate(frames_gray):
            box = g[y:y + size, x:x + size]
            t_vals.append(texture(box))
            hbox = hsvs[i][y:y + size, x:x + size]
            s_vals.append(float(np.mean(hbox[:, :, 1] >= 60)))
            # hue DIVERSITY: portrait art mixes skin/hair/costume hues;
            # ult badges, pills and team chips are one flat team color.
            m = hbox[:, :, 1] >= 60
            if m.sum() >= 20:
                hist, _ = np.histogram(hbox[:, :, 0][m], bins=18,
                                       range=(0, 180))
                h_vals.append(1.0 - float(hist.max()) / float(hist.sum()))
            if i:
                prev = frames_gray[i - 1][y:y + size, x:x + size]
                d_vals.append(float(np.mean(np.abs(
                    box.astype(np.float32) - prev.astype(np.float32))))
                    / 255.0)
        texs.append(statistics.median(t_vals))
        sats.append(statistics.median(s_vals))
        divs.append(statistics.median(h_vals) if h_vals else 0.0)
        stabs.append(1.0 - min(1.0, (statistics.median(d_vals)
                                     if d_vals else 1.0) * 4.0))
    # soft caps: at HUD scale both badges and portraits saturate a low
    # texture cap, so keep the ramp long enough to separate them, and let
    # hue diversity count at full strength — it is THE badge/art divider.
    tex_reward = min(float(np.mean(texs)) / 4000.0, 1.0)
    return (0.35 * float(np.mean(stabs)) + 0.3 * tex_reward
            + 0.10 * float(np.mean(sats))
            + 0.25 * float(np.mean(divs)))


def refine_portraits(frames_bgr: list, frames_gray: list, row: dict,
                     frame_w: int, frame_h: int,
                     fixed_size: int | None = None) -> tuple[list, dict]:
    """Find the 5 portrait boxes for one side given its fitted grid row.

    Depending on the broadcast package the grid row may BE the portraits
    (badge overlaps art) or sit beside them (separate ult chip), so the
    portrait cell is searched freely: shared dx offset, y and size are
    optimized to maximize portrait-likeness (_cell_score) across frames."""
    hsvs = [cv2.cvtColor(f, cv2.COLOR_BGR2HSV) for f in frames_bgr]
    pitch = row["pitch"]
    best = None
    sizes = ([fixed_size] if fixed_size else
             [int(round(pitch * f)) for f in (0.5, 0.55, 0.6, 0.65,
                                              0.7, 0.75)])
    for size in sizes:
        if size < 8:
            continue
        for dx in range(int(-0.6 * pitch), int(0.85 * pitch) + 1, 2):
            for dy in range(-max(6, size // 3), 3, 2):
                xs = [int(round(x + dx)) for x in row["xs"]]
                y = row["y"] + dy
                s = _cell_score(frames_gray, hsvs, xs, y, size,
                                frame_w, frame_h)
                if best is None or s > best[0]:
                    best = (s, dx, y, size, xs)
    s, dx, y, size, xs = best
    boxes = [[x, y, size, size] for x in xs]
    meta = {"cell_score": round(s, 3), "dx": dx, "size": size,
            "direction": (1 if dx >= 0 else -1)}
    return boxes, meta


def validate(boxes_a, boxes_b, frames_gray, frame_w, frame_h) -> tuple[list, dict]:
    reasons = []
    tex = {"a": [], "b": []}
    for side, boxes in (("a", boxes_a), ("b", boxes_b)):
        for i, (x, y, w, h) in enumerate(boxes, 1):
            if x < 0 or y < 0 or x + w > frame_w or y + h > frame_h:
                reasons.append(f"{side}{i} box out of bounds: {[x, y, w, h]}")
                continue
            t = statistics.median(
                texture(g[y:y + h, x:x + w]) for g in frames_gray)
            tex[side].append(t)
            if t < 50:
                reasons.append(
                    f"{side}{i} portrait box has almost no detail "
                    f"(texture {t:.0f}) — likely not on a portrait")
    # symmetry: A row measured from left edge vs B row from right edge
    if boxes_a and boxes_b:
        a1 = boxes_a[0][0]
        b5r = frame_w - (boxes_b[-1][0] + boxes_b[-1][2])
        if abs(a1 - b5r) > 0.02 * frame_w:
            reasons.append(
                f"sides not mirror-symmetric (a1 left margin {a1}px vs "
                f"b5 right margin {b5r}px) — verify visually")
    return reasons, tex


def confidence_score(row_a, row_b, reasons, n_frames) -> float:
    if not row_a or not row_b:
        return 0.0
    frac_a = row_a["n_frames"] / n_frames
    frac_b = row_b["n_frames"] / n_frames
    inlier_frac = min(row_a["inliers"], row_b["inliers"]) / 5.0
    pitch_pen = min(max(row_a["residual"], row_b["residual"]), 0.12)
    hard = sum(1 for r in reasons if "out of bounds" in r
               or "no detail" in r or "disagree" in r)
    score = (0.4 * min(frac_a, frac_b) + 0.3 * inlier_frac
             + 0.3 * (1 - pitch_pen / 0.12))
    score -= 0.15 * hard
    return max(0.0, min(1.0, score))


def draw_sheet(frames_bgr, layout_px: dict, out_path: str) -> None:
    """Annotated calibration sheet: every box over up to 4 frames."""
    tiles = []
    for f in frames_bgr[:4]:
        img = f.copy()
        for side, color in (("slots_a", (0, 200, 255)), ("slots_b", (255, 160, 0))):
            for i, (x, y, w, h) in enumerate(layout_px[side], 1):
                cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)
                cv2.putText(img, f"{side[-1]}{i}", (x, max(12, y - 4)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
        tiles.append(img)
    if not tiles:
        return
    h, w = tiles[0].shape[:2]
    tiles = [cv2.resize(t, (w, h)) for t in tiles]
    rows = [np.hstack(tiles[i:i + 2]) if len(tiles[i:i + 2]) == 2
            else np.hstack([tiles[i], np.zeros_like(tiles[i])])
            for i in range(0, len(tiles), 2)]
    sheet = np.vstack(rows)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cv2.imwrite(out_path, sheet)


def calibrate(frame_paths: list[str], source_id: str,
              templates_dir: str | None = None) -> dict:
    """Full calibration over representative frames.

    Returns {'ok', 'confidence', 'reasons', 'layout', 'sheet_frames', ...};
    'layout' is present only when calibration succeeded.
    """
    frames = [cv2.imread(p) for p in frame_paths]
    frames = [f for f in frames if f is not None]
    if not frames:
        return {"ok": False, "confidence": 0.0,
                "reasons": ["no readable frames supplied"]}
    fh, fw = frames[0].shape[:2]
    frames = [f for f in frames if f.shape[:2] == (fh, fw)]
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]

    # pool chip blobs across frames — the HUD is static, so partial
    # detections from many frames assemble into complete rows
    per_frame_blobs = [find_chip_blobs(f) for f in frames]
    left = [b for blobs in per_frame_blobs for b in blobs
            if b[0] + b[2] / 2 < fw / 2]
    right = [b for blobs in per_frame_blobs for b in blobs
             if b[0] + b[2] / 2 >= fw / 2]
    cands_a = verify_rows(frames, fit_uniform_rows(left, fw, top_k=200))
    cands_b = verify_rows(frames, fit_uniform_rows(right, fw, top_k=200))
    row_a, row_b, joint_warn = pick_joint(cands_a, cands_b, fh)
    reasons = [joint_warn] if joint_warn else []
    if not row_a:
        reasons.append(
            f"left chip row not found ({len(left)} candidate blobs pooled "
            f"from {len(frames)} frames) — are these live-gameplay frames?")
    if not row_b:
        reasons.append(
            f"right chip row not found ({len(right)} candidate blobs "
            f"pooled from {len(frames)} frames)")
    if not row_a or not row_b:
        return {"ok": False, "confidence": 0.0, "reasons": reasons}
    row_a["n_frames"] = side_presence(frames, row_a)
    row_b["n_frames"] = side_presence(frames, row_b)
    if min(row_a["n_frames"], row_b["n_frames"]) < MIN_GOOD_FRAMES:
        reasons.append(
            f"chip rows visible in too few frames (a: {row_a['n_frames']}, "
            f"b: {row_b['n_frames']} of {len(frames)})")

    boxes_a, meta_a = refine_portraits(frames, grays, row_a, fw, fh)
    boxes_b, meta_b = refine_portraits(frames, grays, row_b, fw, fh)
    if meta_a["size"] != meta_b["size"]:
        # one HUD package -> one portrait size; re-fit the weaker side
        # with the stronger side's cell size
        if meta_a["cell_score"] >= meta_b["cell_score"]:
            boxes_b, meta_b = refine_portraits(
                frames, grays, row_b, fw, fh, fixed_size=meta_a["size"])
        else:
            boxes_a, meta_a = refine_portraits(
                frames, grays, row_a, fw, fh, fixed_size=meta_b["size"])
    dir_a, dir_b = meta_a["direction"], meta_b["direction"]
    v_reasons, tex = validate(boxes_a, boxes_b, grays, fw, fh)
    reasons.extend(v_reasons)
    conf = confidence_score(row_a, row_b, reasons, len(frames))

    sx, sy = NATIVE_W / fw, NATIVE_H / fh

    def native(r):
        return [int(round(r[0] * sx)), int(round(r[1] * sy)),
                int(round(r[2] * sx)), int(round(r[3] * sy))]

    def norm(r):
        return [round(r[0] / fw, 5), round(r[1] / fh, 5),
                round(r[2] / fw, 5), round(r[3] / fh, 5)]

    chip_boxes_a = [[x, row_a["y"], row_a["w"], row_a["h"]]
                    for x in row_a["xs"]]
    chip_boxes_b = [[x, row_b["y"], row_b["w"], row_b["h"]]
                    for x in row_b["xs"]]

    layout = {
        "_comments": [
            f"AUTO-CALIBRATED profile for source {source_id} "
            f"({CALIB_VERSION}). Chip rows located by HSV blob detection +",
            "uniform-pitch fitting over multiple frames; portrait cells",
            "placed next to their ult chips with texture-driven refinement.",
            "Pixel rects are at 1920x1080 native; norm_* are fractions of",
            "frame size (resolution-independent record).",
            "Regenerate deterministically with pipeline/calibrate_source.py.",
        ],
        "frame_width": NATIVE_W,
        "frame_height": NATIVE_H,
        "sample_interval_seconds": 10,
        "hud_probe": {
            "chips_a": [native(r) for r in chip_boxes_a],
            "chips_b": [native(r) for r in chip_boxes_b],
            "sat_min": SAT_MIN,
            "val_min": VAL_MIN,
            "min_chips_per_side": 4,
        },
        "slots_a": [native(r) for r in boxes_a],
        "slots_b": [native(r) for r in boxes_b],
        "norm_slots_a": [norm(r) for r in boxes_a],
        "norm_slots_b": [norm(r) for r in boxes_b],
        "match_threshold": 0.6,
        "templates_dir": templates_dir or f"templates/{source_id.replace('-', '_')}",
        "calibration": {
            "version": CALIB_VERSION,
            "source_id": source_id,
            "calibrated_at_resolution": [fw, fh],
            "frames_used": len(frames),
            "chip_row_a": {k: row_a[k] for k in
                           ("y", "w", "h", "pitch", "residual", "n_frames")},
            "chip_row_b": {k: row_b[k] for k in
                           ("y", "w", "h", "pitch", "residual", "n_frames")},
            "portrait_direction": {"a": dir_a, "b": dir_b},
            "portrait_cell": {"a": meta_a, "b": meta_b},
            "confidence": round(conf, 3),
            "warnings": reasons,
        },
    }
    return {"ok": conf >= CONFIDENCE_FLOOR, "confidence": conf,
            "reasons": reasons, "layout": layout,
            "frames_bgr": frames, "boxes_a": boxes_a, "boxes_b": boxes_b,
            "frame_size": (fw, fh)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames-dir", help="dir of representative PNG frames")
    ap.add_argument("--clip", help="local clip to pull frames from")
    ap.add_argument("--times", help="comma list of clip seconds (with --clip)")
    ap.add_argument("--source-id", required=True)
    ap.add_argument("--out", required=True, help="layouts/<profile>.json")
    ap.add_argument("--sheet", help="calibration sheet PNG "
                    "(default reports/calibration/<source>/sheet.png)")
    ap.add_argument("--templates-dir", help="override templates_dir")
    ap.add_argument("--force", action="store_true",
                    help="write even below the confidence floor")
    args = ap.parse_args(argv)

    if args.frames_dir:
        paths = [os.path.join(args.frames_dir, f)
                 for f in sorted(os.listdir(args.frames_dir))
                 if f.endswith(".png")]
    elif args.clip and args.times:
        tmp = tempfile.mkdtemp(prefix="calib_")
        paths = frames_from_clip(
            args.clip, [float(t) for t in args.times.split(",")], tmp)
    else:
        raise SystemExit("supply --frames-dir OR --clip + --times")

    res = calibrate(paths, args.source_id, args.templates_dir)
    print(f"[calibrate] confidence {res['confidence']:.2f} "
          f"({'OK' if res['ok'] else 'REFUSED'})")
    for r in res["reasons"]:
        print(f"  reason: {r}")
    if not res.get("layout"):
        print("[calibrate] no chip rows -> nothing written")
        return 2
    if not res["ok"] and not args.force:
        print("[calibrate] below confidence floor "
              f"{CONFIDENCE_FLOOR} -> refusing to write (use --force to "
              "override after visual review)")
        return 2

    # preserve human-added marker keys across recalibration
    if os.path.exists(args.out):
        try:
            with open(args.out, "r", encoding="utf-8") as f:
                old = json.load(f)
            for key in ("reject", "replay", "anchor", "score_map",
                        "round_emblem"):
                if key in old and key not in res["layout"]:
                    res["layout"][key] = old[key]
                    print(f"[calibrate] preserved existing '{key}' config")
        except (ValueError, OSError):
            pass
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res["layout"], f, indent=1)
    print(f"[calibrate] wrote {args.out}")

    # sheet is drawn at the CAPTURE resolution (boxes as refined)
    sheet = args.sheet or os.path.join(
        db.REPO_ROOT, "reports", "calibration", args.source_id, "sheet.png")
    lay_px = {"slots_a": res["boxes_a"], "slots_b": res["boxes_b"]}
    draw_sheet(res["frames_bgr"], lay_px, sheet)
    print(f"[calibrate] sheet -> {sheet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
