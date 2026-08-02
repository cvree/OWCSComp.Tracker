/* =====================================================================
   engine.js — HUD calibration, in the browser, with no server.

   This is a faithful port of the parts of pipeline/calibrate_source.py that
   a browser can do: find the team colour chips along the top of a broadcast
   frame, fit a five-position grid to each side, and derive the ten hero
   portrait boxes from those grids.

   It exists because the alternative — "install Python, install ffmpeg, run
   these four commands" — is the single biggest wall between someone who has
   a VOD and a working layout. Everything here runs on ImageData the page
   already has, so a person with a browser and a video file can produce a
   calibrated layout and never see a terminal.

   WHAT IS THE SAME as the Python calibrator, deliberately:
     * the HUD band, saturation/value thresholds and chip size limits
       (SAT_MIN, VAL_MIN, CHIP_W_FRAC, CHIP_ASPECT, BAND_*_FRAC);
     * the RANSAC over blob EDGES rather than blob centres — chips merge
       with the portrait art beside them into one wide blob, so exact
       five-blob rows cannot be assumed, but one of the two edge families
       always lands on a clean grid;
     * portrait cells scored on being textured, colourful and temporally
       stable, which is what separates real portrait art from chrome (flat),
       player names (colourless) and the game world (unstable);
     * refusing below a confidence floor instead of emitting a guess.

   WHAT IS DIFFERENT, and why it is stated rather than hidden: the Python
   calibrator writes a `hud_probe` recording the chip geometry it measured,
   and layouts carrying one are treated as production-calibrated. A layout
   built here is marked `calibration_source: "browser"` and carries its own
   `browser_probe` instead. It is a real measurement — but it is measured
   from frames the user chose by eye, so it enters the same review path a
   hand-adjusted layout does rather than claiming the full pipeline's
   provenance.
   ===================================================================== */
window.OWCSCalibrate = (function () {
  'use strict';

  /* Thresholds — kept numerically identical to calibrate_source.py so a
     layout built here and one built by the pipeline describe the same HUD. */
  const BAND_TOP_FRAC = 0.03, BAND_BOT_FRAC = 0.26;
  const SAT_MIN = 110, VAL_MIN = 90;
  const CHIP_W_FRAC_LO = 0.012;
  const CHIP_ASPECT_LO = 0.55, CHIP_ASPECT_HI = 1.9;
  const CONFIDENCE_FLOOR = 0.55;
  const EXPECT = 5;

  /* ------------------------------------------------------- colour space */

  /** Per-pixel hue/saturation/value (OpenCV ranges) plus grayscale. */
  function analyse(imageData) {
    const { width: w, height: h, data } = imageData;
    const n = w * h;
    const hue = new Uint8Array(n);      // 0-179, as OpenCV's HSV
    const sat = new Uint8Array(n);      // 0-255
    const val = new Uint8Array(n);      // 0-255
    const gray = new Uint8ClampedArray(n);
    for (let i = 0, p = 0; i < n; i++, p += 4) {
      const r = data[p], g = data[p + 1], b = data[p + 2];
      const max = r > g ? (r > b ? r : b) : (g > b ? g : b);
      const min = r < g ? (r < b ? r : b) : (g < b ? g : b);
      const d = max - min;
      val[i] = max;
      sat[i] = max === 0 ? 0 : ((d * 255 / max) | 0);
      let hDeg = 0;
      if (d !== 0) {
        if (max === r) hDeg = 60 * (((g - b) / d) % 6);
        else if (max === g) hDeg = 60 * ((b - r) / d + 2);
        else hDeg = 60 * ((r - g) / d + 4);
        if (hDeg < 0) hDeg += 360;
      }
      hue[i] = (hDeg / 2) | 0;          // 0-179
      gray[i] = (0.114 * b + 0.587 * g + 0.299 * r) | 0;
    }
    return { w, h, hue, sat, val, gray };
  }

  /* --------------------------------------------------------- chip finder */

  /**
   * Saturated solid blobs in the top HUD band, chip-sized, any hue.
   * Port of find_chip_blobs(). Connected components via iterative flood
   * fill — recursion would blow the stack on a 1080p mask.
   */
  function findChipBlobs(frame) {
    const { w, h, sat, val } = frame;
    const y0 = Math.floor(h * BAND_TOP_FRAC);
    const y1 = Math.floor(h * BAND_BOT_FRAC);
    const bandH = y1 - y0;
    if (bandH <= 0) return [];

    const mask = new Uint8Array(w * bandH);
    for (let y = 0; y < bandH; y++) {
      const src = (y + y0) * w;
      const dst = y * w;
      for (let x = 0; x < w; x++) {
        const i = src + x;
        if (sat[i] >= SAT_MIN && val[i] >= VAL_MIN) mask[dst + x] = 1;
      }
    }
    closeMask(mask, w, bandH);

    const wmin = CHIP_W_FRAC_LO * w, wmax = 0.45 * w;
    const hmin = 0.018 * h, hmax = 0.10 * h;
    const seen = new Uint8Array(w * bandH);
    const stack = new Int32Array(w * bandH);
    const blobs = [];

    for (let start = 0; start < mask.length; start++) {
      if (!mask[start] || seen[start]) continue;
      let sp = 0;
      stack[sp++] = start;
      seen[start] = 1;
      let minX = w, maxX = -1, minY = bandH, maxY = -1, area = 0;
      while (sp > 0) {
        const idx = stack[--sp];
        const x = idx % w, y = (idx / w) | 0;
        area++;
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
        for (let dy = -1; dy <= 1; dy++) {
          for (let dx = -1; dx <= 1; dx++) {
            if (!dx && !dy) continue;
            const nx = x + dx, ny = y + dy;
            if (nx < 0 || ny < 0 || nx >= w || ny >= bandH) continue;
            const nIdx = ny * w + nx;
            if (mask[nIdx] && !seen[nIdx]) { seen[nIdx] = 1; stack[sp++] = nIdx; }
          }
        }
      }
      const bw = maxX - minX + 1, bh = maxY - minY + 1;
      if (bw < wmin || bw > wmax || bh < hmin || bh > hmax) continue;
      const aspect = bw / bh;
      if (aspect >= CHIP_ASPECT_LO && aspect <= CHIP_ASPECT_HI) {
        if (area < 0.4 * bw * bh) continue;      // chip-shaped: must be solid
      } else if (aspect > CHIP_ASPECT_HI && area >= 0.3 * bw * bh) {
        // Wider than a chip but still row-shaped: a chip merged with its
        // neighbouring portrait. Its edges still land on the slot grid.
      } else {
        continue;
      }
      blobs.push({ x: minX, y: minY + y0, w: bw, h: bh });
    }
    return blobs;
  }

  /** 3x3 morphological close, so a chip split by an overlay reads as one. */
  function closeMask(mask, w, h) {
    const dil = new Uint8Array(mask.length);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        let on = 0;
        for (let dy = -1; dy <= 1 && !on; dy++) {
          for (let dx = -1; dx <= 1; dx++) {
            const nx = x + dx, ny = y + dy;
            if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
            if (mask[ny * w + nx]) { on = 1; break; }
          }
        }
        dil[y * w + x] = on;
      }
    }
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        let all = 1;
        for (let dy = -1; dy <= 1 && all; dy++) {
          for (let dx = -1; dx <= 1; dx++) {
            const nx = x + dx, ny = y + dy;
            if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
            if (!dil[ny * w + nx]) { all = 0; break; }
          }
        }
        mask[y * w + x] = all;
      }
    }
  }

  /* ---------------------------------------------------------- grid fitting */

  const median = (xs) => {
    if (!xs.length) return 0;
    const s = xs.slice().sort((a, b) => a - b);
    const m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  };

  /**
   * Fit five-position uniform-pitch grids to one side's chip blobs.
   * Port of _iter_grid_fits() + fit_uniform_rows().
   *
   * The RANSAC runs over blob EDGES, not centres: when the portrait sits to
   * the right of its chip the merged blob's LEFT edge is still the chip's
   * left edge (and mirrored the other way), so one of the two edge families
   * always lands on a clean five-slot grid even when no frame contains five
   * separate chip blobs.
   */
  function fitUniformRows(blobs, frameW, topK) {
    if (blobs.length < 3) return [];
    const pitchLo = 0.030 * frameW, pitchHi = 0.095 * frameW;
    const out = [], seen = new Set();

    for (const edge of ['left', 'right']) {
      const pts = Array.from(new Set(
        blobs.map((b) => (edge === 'left' ? b.x : b.x + b.w))
      )).sort((a, b) => a - b);

      for (let i = 0; i < pts.length; i++) {
        for (let j = i + 1; j < pts.length; j++) {
          const span = pts[j] - pts[i];
          for (let k = 1; k < EXPECT; k++) {
            const pitch = span / k;
            if (pitch < pitchLo || pitch > pitchHi) continue;
            const tol = Math.max(3.0, 0.08 * pitch);
            for (let a0 = 0; a0 < EXPECT - k; a0++) {
              const x0 = pts[i] - a0 * pitch;
              const hits = [];
              let resid = 0;
              for (let t = 0; t < EXPECT; t++) {
                const gx = x0 + t * pitch;
                let bestD = Infinity, bestP = null;
                for (const p of pts) {
                  const d = Math.abs(p - gx);
                  if (d < bestD) { bestD = d; bestP = p; }
                }
                if (bestD <= tol) { hits.push(bestP); resid += bestD; }
              }
              if (hits.length < 3) continue;

              const hitSet = new Set(hits);
              const hitBlobs = blobs.filter(
                (b) => hitSet.has(edge === 'left' ? b.x : b.x + b.w));
              if (!hitBlobs.length) continue;
              // Chip size from the MIN dimension: resists merged blobs,
              // whose width is the chip plus the portrait beside it.
              const size = Math.round(median(hitBlobs.map(
                (b) => Math.min(b.w, b.h))));
              const grid = [];
              for (let t = 0; t < EXPECT; t++) grid.push(x0 + t * pitch);
              const xs = grid.map((g) =>
                Math.round(edge === 'left' ? g : g - size));

              const key = `${Math.round(pitch)}|${xs[0] >> 2}|${edge}`;
              if (seen.has(key)) continue;
              seen.add(key);
              out.push({
                xs, y: Math.round(median(hitBlobs.map((b) => b.y))),
                w: size, h: size, pitch,
                residual: resid / hits.length / pitch,
                inliers: hits.length, edge,
              });
            }
          }
        }
      }
    }
    out.sort((a, b) => (b.inliers - a.inliers) || (a.residual - b.residual));
    return out.slice(0, topK || 8);
  }

  /* ------------------------------------------------------- cell scoring */

  /** Edge-detail score. Portrait art scores far above flat UI chrome. */
  function texture(frame, x, y, size) {
    const { w, h, gray } = frame;
    if (x < 0 || y < 0 || x + size > w || y + size > h || size < 3) return 0;
    let sum = 0, sumSq = 0, n = 0;
    for (let yy = y + 1; yy < y + size - 1; yy++) {
      for (let xx = x + 1; xx < x + size - 1; xx++) {
        const i = yy * w + xx;
        // 4-neighbour Laplacian
        const lap = 4 * gray[i] - gray[i - 1] - gray[i + 1]
          - gray[i - w] - gray[i + w];
        sum += lap; sumSq += lap * lap; n++;
      }
    }
    if (!n) return 0;
    const mean = sum / n;
    return sumSq / n - mean * mean;
  }

  /** Fraction of pixels in the cell that carry real colour (sat >= 60). */
  function satFraction(frame, x, y, size) {
    const { w, sat } = frame;
    let on = 0, n = 0;
    for (let yy = y; yy < y + size; yy++) {
      for (let xx = x; xx < x + size; xx++) {
        if (sat[yy * w + xx] >= 60) on++;
        n++;
      }
    }
    return n ? on / n : 0;
  }

  /**
   * Hue DIVERSITY over the coloured pixels of a cell, 0..1.
   *
   * This is the measurement that separates a hero portrait from the team
   * chip beside it, and leaving it out is not a small approximation: a chip
   * is one flat team colour, saturated and perfectly stable across frames,
   * so on texture+saturation+stability alone it outscores the art it sits
   * next to. Portrait art mixes skin, hair and costume hues and spreads
   * across the histogram. Without this term the wizard locked side A onto
   * the chips — a whole cell-width left of the portraits — on the verified
   * Nepal broadcast.
   */
  function hueDiversity(frame, x, y, size) {
    const { w, hue, sat } = frame;
    const bins = new Float64Array(18);
    let total = 0;
    for (let yy = y; yy < y + size; yy++) {
      for (let xx = x; xx < x + size; xx++) {
        const i = yy * w + xx;
        if (sat[i] < 60) continue;
        bins[Math.min(17, (hue[i] / 10) | 0)]++;
        total++;
      }
    }
    if (total < 20) return 0;
    let max = 0;
    for (let i = 0; i < 18; i++) if (bins[i] > max) max = bins[i];
    return 1 - max / total;
  }

  /** Mean absolute grayscale difference between consecutive frames, 0..1. */
  function frameDiff(frames, f, x, y, size) {
    const { w } = frames[0];
    const a = frames[f - 1].gray, b = frames[f].gray;
    let diff = 0, n = 0;
    for (let yy = y; yy < y + size; yy++) {
      for (let xx = x; xx < x + size; xx++) {
        const i = yy * w + xx;
        diff += Math.abs(a[i] - b[i]); n++;
      }
    }
    return n ? diff / n / 255 : 1;
  }

  /**
   * How portrait-like are the five cells at (xs+dx, y, size)?
   * Faithful port of _cell_score(), weights included.
   *
   * Portraits are temporally STABLE (same art all game), TEXTURED (real
   * drawings, not chrome), COLOURFUL, and hue-DIVERSE. Player-name text is
   * stable and textured but nearly colourless; the game world is colourful
   * but unstable; chrome and team chips are stable and flat and single-hued.
   * The blend separates all four.
   */
  function cellScoreDetail(frames, xs, y, size) {
    const { w, h } = frames[0];
    const texs = [], sats = [], divs = [], stabs = [];
    for (const x of xs) {
      if (x < 0 || y < 0 || x + size > w || y + size > h) return null;
      const tVals = [], sVals = [], hVals = [], dVals = [];
      for (let i = 0; i < frames.length; i++) {
        tVals.push(texture(frames[i], x, y, size));
        sVals.push(satFraction(frames[i], x, y, size));
        const div = hueDiversity(frames[i], x, y, size);
        if (div > 0) hVals.push(div);
        if (i) dVals.push(frameDiff(frames, i, x, y, size));
      }
      texs.push(median(tVals));
      sats.push(median(sVals));
      divs.push(hVals.length ? median(hVals) : 0);
      stabs.push(1 - Math.min(1, (dVals.length ? median(dVals) : 1) * 4));
    }
    const mean = (a) => a.reduce((p, c) => p + c, 0) / a.length;
    // Soft cap on texture: at HUD scale both badges and portraits saturate a
    // low cap, so the ramp is kept long enough to separate them, and hue
    // diversity counts at full strength.
    const texReward = Math.min(mean(texs) / 4000, 1);
    const div = mean(divs);
    return {
      score: 0.35 * mean(stabs) + 0.30 * texReward
        + 0.10 * mean(sats) + 0.25 * div,
      // "Is this DRAWN ART?" on its own. Texture and hue diversity are the
      // only two terms that answer that: a flat team chip maxes stability
      // and saturation, so those are necessary but not sufficient. Used to
      // break near-ties, where the blended score genuinely cannot tell a
      // portrait from the chip beside it.
      art: 0.55 * texReward + 0.45 * div,
      tex: mean(texs), div, sat: mean(sats), stab: mean(stabs),
    };
  }

  function cellScore(frames, xs, y, size) {
    const d = cellScoreDetail(frames, xs, y, size);
    return d ? d.score : -1;
  }

  /**
   * Find the five portrait boxes for one side, given its fitted chip grid.
   * Port of refine_portraits(). The grid row may BE the portraits (badge
   * over art) or sit beside them (separate ult chip), so the cell is
   * searched freely: one shared dx offset, with y and size optimised to
   * maximise portrait-likeness across the captured frames.
   */
  //: Two placements this close in blended score are not distinguishable by
  //: it, and the tie is broken on "which looks more like drawn art".
  const TIE_FRACTION = 0.03;

  function refinePortraits(frames, row, opts) {
    const o = opts || {};
    const pitch = row.pitch;
    const cands = [];
    const dxStep = Math.max(2, Math.round(pitch / 24));
    const fracs = o.size ? [null] : [0.5, 0.55, 0.6, 0.65, 0.7, 0.75];
    for (const frac of fracs) {
      const size = o.size || Math.round(pitch * frac);
      if (size < 8) continue;
      const dys = o.y !== undefined && o.y !== null
        ? [o.y - row.y]
        : range(-Math.max(6, (size / 3) | 0), 2, 2);
      for (let dx = Math.round(-0.6 * pitch); dx <= Math.round(0.85 * pitch); dx += dxStep) {
        for (const dy of dys) {
          const xs = row.xs.map((x) => Math.round(x + dx));
          const y = row.y + dy;
          const d = cellScoreDetail(frames, xs, y, size);
          if (d) cands.push({ ...d, dx, y, size, xs });
        }
      }
    }
    if (!cands.length) return null;
    cands.sort((a, b) => b.score - a.score);
    const top = cands[0];

    // NEAR-TIE RESOLUTION.
    //
    // The blended score is bimodal on a real HUD: the hero portrait and the
    // ult-percentage chip beside it sit on separate peaks. On the verified
    // Nepal broadcast those peaks are 0.6558 and 0.6551 — a gap of 0.1% —
    // and the chip wins, because it is perfectly stable and fully saturated
    // while the portraits genuinely change (heroes swap mid-map, which is
    // the whole thing this project measures).
    //
    // So when a rival placement is within TIE_FRACTION and is a genuinely
    // DIFFERENT position rather than a neighbouring pixel, the tie is broken
    // on texture and hue diversity alone. A chip is one flat colour with a
    // number on it; a portrait is drawn art. At those two peaks the true one
    // wins on texture 4014 vs 2768 and on hue diversity 0.568 vs 0.427.
    const apart = Math.max(4, 0.35 * pitch);
    const rivals = cands.filter((c) =>
      c.score >= top.score * (1 - TIE_FRACTION)
      && Math.abs(c.dx - top.dx) >= apart);
    let best = top;
    let ambiguity = 0;
    if (rivals.length) {
      const field = [top, ...rivals];
      field.sort((a, b) => b.art - a.art);
      best = field[0];
      // How close the runner-up was, as a 0..1 "do not trust this blindly"
      // signal that flows into the reported confidence.
      const rival = field[1];
      ambiguity = rival
        ? 1 - Math.min(1, (best.art - rival.art) / (0.12 || 1))
        : 0;
    }

    return {
      boxes: best.xs.map((x) => [x, best.y, best.size, best.size]),
      meta: {
        cell_score: Math.round(best.score * 1000) / 1000,
        art_score: Math.round(best.art * 1000) / 1000,
        dx: best.dx, size: best.size,
        direction: best.dx >= 0 ? 1 : -1,
        ambiguity: Math.round(ambiguity * 100) / 100,
        rivals: rivals.length,
      },
    };
  }

  function range(from, to, step) {
    const out = [];
    for (let v = from; v <= to; v += step) out.push(v);
    return out;
  }

  /* -------------------------------------------------------- side picking */

  /** Split blobs into a left half and a right half of the frame. */
  function splitSides(blobs, frameW) {
    const mid = frameW / 2;
    return {
      a: blobs.filter((b) => b.x + b.w / 2 < mid),
      b: blobs.filter((b) => b.x + b.w / 2 >= mid),
    };
  }

  /**
   * Pair one candidate grid per side, preferring pairs that agree on pitch
   * and sit at the same height — the two team rows of a real broadcast are
   * always mirror images of each other.
   */
  function pickJoint(candsA, candsB, frameW) {
    let best = null;
    for (const a of candsA.slice(0, 6)) {
      for (const b of candsB.slice(0, 6)) {
        const pitchAgree = 1 - Math.min(
          Math.abs(a.pitch - b.pitch) / Math.max(a.pitch, b.pitch), 1);
        const yAgree = 1 - Math.min(Math.abs(a.y - b.y) / 40, 1);
        const aLeft = a.xs[0];
        const bRight = frameW - (b.xs[b.xs.length - 1] + b.w);
        const symmetry = 1 - Math.min(
          Math.abs(aLeft - bRight) / (0.06 * frameW), 1);
        const inliers = (a.inliers + b.inliers) / (2 * EXPECT);
        const resid = 1 - Math.min((a.residual + b.residual) / 2 / 0.08, 1);
        const score = 0.28 * pitchAgree + 0.22 * yAgree + 0.2 * symmetry
          + 0.2 * inliers + 0.1 * resid;
        if (!best || score > best.score) best = { score, a, b };
      }
    }
    return best;
  }

  /* ------------------------------------------------------- validation */

  function validate(boxesA, boxesB, frames, frameW, frameH) {
    const reasons = [];
    for (const [side, boxes] of [['A', boxesA], ['B', boxesB]]) {
      boxes.forEach(([x, y, w, h], i) => {
        if (x < 0 || y < 0 || x + w > frameW || y + h > frameH) {
          reasons.push(`Team ${side} slot ${i + 1} falls outside the frame.`);
          return;
        }
        const t = median(frames.map((f) => texture(f, x, y, w)));
        if (t < 50) {
          reasons.push(
            `Team ${side} slot ${i + 1} has almost no detail — it is probably `
            + `not sitting on a hero portrait.`);
        }
      });
    }
    if (boxesA.length && boxesB.length) {
      const a1 = boxesA[0][0];
      const last = boxesB[boxesB.length - 1];
      const b5r = frameW - (last[0] + last[2]);
      if (Math.abs(a1 - b5r) > 0.02 * frameW) {
        reasons.push(
          `The two teams' rows are not mirror images (left margin ${a1}px vs `
          + `right margin ${b5r}px) — check both sides look right.`);
      }
    }
    return reasons;
  }

  function confidence(rowA, rowB, reasons, nFrames) {
    if (!rowA || !rowB) return 0;
    const inl = (rowA.inliers + rowB.inliers) / (2 * EXPECT);
    const resid = 1 - Math.min((rowA.residual + rowB.residual) / 2 / 0.08, 1);
    const cells = Math.min(
      ((rowA.meta ? rowA.meta.cell_score : 0)
        + (rowB.meta ? rowB.meta.cell_score : 0)) / 2 / 0.6, 1);
    const frames = Math.min(nFrames / 4, 1);
    let score = 0.3 * inl + 0.2 * resid + 0.35 * cells + 0.15 * frames;
    score -= 0.12 * reasons.length;
    // A placement that was chosen over a near-tied rival is exactly the case
    // where a human should glance at it, so the reported confidence says so
    // rather than presenting a coin-flip as certainty.
    const ambiguity = Math.max(
      (rowA.meta && rowA.meta.ambiguity) || 0,
      (rowB.meta && rowB.meta.ambiguity) || 0);
    score -= 0.25 * ambiguity;
    return Math.max(0, Math.min(1, score));
  }

  /* ------------------------------------------------------------- driver */

  /**
   * Calibrate from captured frames.
   *
   * `imageDatas` are full-resolution frames of the SAME broadcast at the
   * SAME size. Returns {ok, confidence, boxesA, boxesB, reasons, ...}. When
   * `ok` is false the reasons say what to do about it — the caller shows
   * those and offers the manual editor, exactly as the pipeline refuses
   * below its own confidence floor rather than emitting a guess.
   */
  function calibrate(imageDatas, options) {
    const opts = options || {};
    const onProgress = opts.onProgress || function () {};
    if (!imageDatas || !imageDatas.length) {
      return { ok: false, confidence: 0, reasons: ['No frames were captured.'] };
    }
    onProgress(0.05, 'Reading the frames');
    const frames = imageDatas.map(analyse);
    const frameW = frames[0].w, frameH = frames[0].h;
    for (const f of frames) {
      if (f.w !== frameW || f.h !== frameH) {
        return {
          ok: false, confidence: 0,
          reasons: ['The captured frames are different sizes. Capture them '
            + 'all from the same video.'],
        };
      }
    }

    onProgress(0.2, 'Looking for the team colour chips');
    // Blobs are gathered across every frame: a chip hidden by an overlay in
    // one frame is usually clean in another, and the grid fit only needs the
    // union to contain three good edges.
    const allBlobs = [];
    frames.forEach((f) => { for (const b of findChipBlobs(f)) allBlobs.push(b); });
    if (allBlobs.length < 4) {
      return {
        ok: false, confidence: 0, blobs: allBlobs, frameW, frameH,
        reasons: ['No team colour chips were found along the top of these '
          + 'frames. Capture moments during live play, when both teams\' hero '
          + 'portraits are on screen — not a replay, the desk, or a kill cam.'],
      };
    }

    onProgress(0.4, 'Fitting the hero slot grid');
    const sides = splitSides(allBlobs, frameW);
    const candsA = fitUniformRows(sides.a, frameW);
    const candsB = fitUniformRows(sides.b, frameW);
    if (!candsA.length || !candsB.length) {
      return {
        ok: false, confidence: 0, blobs: allBlobs, frameW, frameH,
        reasons: [!candsA.length && !candsB.length
          ? 'Neither team\'s row of hero slots could be found.'
          : `Only ${candsA.length ? 'the left' : 'the right'} team's row of `
            + 'hero slots could be found.',
        'Try capturing a few more frames from clear moments of live play.'],
      };
    }

    const joint = pickJoint(candsA, candsB, frameW);
    onProgress(0.6, 'Locating the hero portraits');
    let refinedA = refinePortraits(frames, joint.a);
    onProgress(0.8, 'Locating the hero portraits');
    let refinedB = refinePortraits(frames, joint.b);
    // One HUD package means ONE portrait row: the same cell size, the same
    // height, and the two sides mirroring each other. When the independent
    // fits disagree, the side that scored lower is re-fitted under the
    // stronger side's size and y, and its horizontal offset is nudged toward
    // the mirror of the stronger side.
    //
    // This is what makes the weaker side reliable rather than nearly-right.
    // On the verified Nepal broadcast, side A on its own settled on the blue
    // ult-percentage chip 22px left of the portraits and 13px high — that
    // chip is flat, saturated and perfectly stable, so it beats real art on
    // stability and colour, and drifting upward into the white team banner
    // lent it enough hue variety to win outright. Constrained to side B's
    // row, it lands on the portraits.
    if (refinedA && refinedB) {
      const aStronger = refinedA.meta.cell_score >= refinedB.meta.cell_score;
      const strong = aStronger ? refinedA : refinedB;
      const strongBoxes = strong.boxes;
      const strongY = strongBoxes[0][1];
      const strongSize = strongBoxes[0][2];
      // NOTE: the weaker side is NOT nudged toward a mirror of the stronger
      // one, tempting as that is. These rows are only approximately
      // symmetric — on the verified Nepal broadcast the margins are 90px and
      // 52px at 1920, which `validate()` passes by 0.4px — so a symmetry
      // prior strong enough to help would drag a correct row off its
      // portraits. Worse, it would MANUFACTURE the symmetry that
      // `validate()` then checks for, turning that check into a tautology.
      const weakRow = aStronger ? joint.b : joint.a;
      const refit = refinePortraits(frames, weakRow, {
        size: strongSize, y: strongY,
      });
      if (refit) {
        if (aStronger) refinedB = refit; else refinedA = refit;
      }
    }
    if (!refinedA || !refinedB) {
      return {
        ok: false, confidence: 0, blobs: allBlobs, frameW, frameH,
        reasons: ['The hero portraits could not be located next to the chips.'],
      };
    }

    onProgress(0.95, 'Checking the result');
    const reasons = validate(refinedA.boxes, refinedB.boxes,
      frames, frameW, frameH);
    const rowA = Object.assign({}, joint.a, { meta: refinedA.meta });
    const rowB = Object.assign({}, joint.b, { meta: refinedB.meta });
    const score = confidence(rowA, rowB, reasons, frames.length);

    onProgress(1, 'Done');
    return {
      // Same rule the pipeline calibrator uses: the confidence floor decides,
      // and the reasons have already pushed the score down. Requiring zero
      // reasons instead would refuse this broadcast over a symmetry warning
      // that its OWN verified layout also triggers at 720p — the reasons are
      // shown to the user either way, which is what they are for.
      ok: score >= CONFIDENCE_FLOOR,
      confidence: Math.round(score * 100) / 100,
      floor: CONFIDENCE_FLOOR,
      boxesA: refinedA.boxes,
      boxesB: refinedB.boxes,
      chipsA: joint.a.xs.map((x) => [x, joint.a.y, joint.a.w, joint.a.h]),
      chipsB: joint.b.xs.map((x) => [x, joint.b.y, joint.b.w, joint.b.h]),
      rowA, rowB, reasons, blobs: allBlobs, frameW, frameH,
      frameCount: frames.length,
    };
  }

  /* ---------------------------------------------------------- layout out */

  /**
   * The layout document, in exactly the shape pipeline/layout_registry.py
   * and detect.py read.
   *
   * `calibration_source: "browser"` and `browser_probe` are what keep this
   * honest: `hud_probe` is the pipeline calibrator's own measurement and is
   * what marks a layout production-calibrated, so a browser-built layout
   * does not claim it. It records what it actually measured, under its own
   * key, and enters review like any hand-adjusted layout.
   */
  function toLayout(result, meta) {
    const info = meta || {};
    const name = info.name || 'my-broadcast';
    return {
      _comments: [
        'Built in a browser with the OWCS Comp Tracker calibration wizard',
        `(calibrate.html) on ${new Date().toISOString().slice(0, 10)}.`,
        '',
        'The ten portrait boxes below were MEASURED from real frames of this',
        'broadcast: the team colour chips were found by saturation, a',
        'five-position grid was fitted to each side, and the portrait cells',
        'were located by searching for the most portrait-like offset across',
        `every captured frame (confidence ${result.confidence}).`,
        '',
        'This carries `calibration_source: "browser"` and a `browser_probe`',
        'rather than the `hud_probe` that pipeline/calibrate_source.py',
        'writes. That difference is deliberate and load-bearing: only a',
        'layout with a hud_probe counts as production-calibrated, so this',
        'goes through review rather than claiming provenance it does not',
        'have. Re-run the desktop app\'s calibration on this broadcast to',
        'promote it.',
      ],
      frame_width: result.frameW,
      frame_height: result.frameH,
      sample_interval_seconds: 30,
      slots_a: result.boxesA,
      slots_b: result.boxesB,
      match_threshold: 0.62,
      templates_dir: `templates/${name}`,
      calibration_source: 'browser',
      browser_probe: {
        version: 'browser-calib-v1',
        source_id: name,
        frames_used: result.frameCount,
        confidence: result.confidence,
        chip_row_a: {
          y: result.rowA.y, w: result.rowA.w, h: result.rowA.h,
          pitch: Math.round(result.rowA.pitch * 100) / 100,
          residual: Math.round(result.rowA.residual * 10000) / 10000,
          inliers: result.rowA.inliers, edge: result.rowA.edge,
        },
        chip_row_b: {
          y: result.rowB.y, w: result.rowB.w, h: result.rowB.h,
          pitch: Math.round(result.rowB.pitch * 100) / 100,
          residual: Math.round(result.rowB.residual * 10000) / 10000,
          inliers: result.rowB.inliers, edge: result.rowB.edge,
        },
        portrait_cell: { a: result.rowA.meta, b: result.rowB.meta },
        manually_adjusted: !!info.adjusted,
        built_at: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
      },
    };
  }

  return {
    analyse, findChipBlobs, fitUniformRows, refinePortraits, splitSides,
    pickJoint, validate, calibrate, toLayout,
    // Scoring internals are exported so the test suite can measure them
    // against real broadcast frames rather than only asserting the
    // end-to-end result.
    texture, satFraction, hueDiversity, cellScore,
    CONFIDENCE_FLOOR,
  };
})();
