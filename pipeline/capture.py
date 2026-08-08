#!/usr/bin/env python3
"""
capture.py — Stage 3A: VOD → gameplay frames.

For each match with status='final', a vod_url, and no comp snapshots yet:
  1. fetch ONLY the sampled frames from the broadcast, by HTTP range
     (pipeline/remote_frames.py) — the whole VOD is never downloaded,
  2. keep only frames classified as LIVE GAMEPLAY (HUD anchor present,
     replay marker absent), delete the rest.

WHY THIS NO LONGER DOWNLOADS THE VOD
Sampling every five minutes of a nine-hour broadcast wants about a hundred
frames. The old path fetched several gigabytes of video to produce them and
then deleted it — paying for 100% of the bytes to keep 0.001% of the
pixels, every single run. `remote_frames` resolves one direct media URL and
seeks to each sample with an HTTP range request instead, which costs a few
hundred kilobytes per frame and caches what it fetches.

`--full-download` restores the old behaviour for the cases that genuinely
need the file (a broadcast whose CDN refuses range requests, or an offline
archive), and `--dry-run` on a local file is unchanged.

Gameplay classification is deterministic template matching against small
reference crops defined in a per-broadcast layout config (layouts/*.json):

  {
    "frame_width": 1280, "frame_height": 720,
    "sample_interval_seconds": 300,
    "anchor":  { "rect": [x,y,w,h], "template": "layouts/anchor.png",
                 "min_score": 0.75 },
    "replay":  { "rect": [x,y,w,h], "template": "layouts/replay.png",
                 "min_score": 0.75 },          // optional
    "slots_a": [[x,y,w,h] x5],                  // used by detect.py
    "slots_b": [[x,y,w,h] x5],
    "match_threshold": 0.6                      // used by detect.py
  }

Build the anchor/replay templates once by cropping a real broadcast frame
(see detect.py --build-templates for the slot crops helper).

Usage:
  python3 pipeline/capture.py --layout layouts/owcs-demo.json
  python3 pipeline/capture.py --layout ... --dry-run path/to/local.mp4
      # process a local file, print a keep/reject report, write nothing
  python3 pipeline/capture.py --layout ... --match m01
      # only this match id
  python3 pipeline/capture.py --layout ... --full-download
      # the old behaviour: fetch the whole VOD first

Requires: yt-dlp and ffmpeg on PATH (pip install yt-dlp).
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

WORK_DIR = os.path.join(db.REPO_ROOT, "work")


# ---------------------------------------------------------------- layout
def load_layout(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        layout = json.load(f)
    layout["_dir"] = os.path.dirname(os.path.abspath(path))
    return layout


ASPECT_TOL = 0.02  # max relative aspect-ratio difference for auto-scaling


def scale_layout_to_frame(layout: dict, frame_w: int, frame_h: int) -> tuple:
    """Scale a layout's rectangles from its native frame size to an actual
    frame size, e.g. a 1920x1080 layout applied to 640x360 fallback frames.

    Only same-aspect-ratio scaling is allowed (within ASPECT_TOL). Returns
    (scaled_layout, info) where info is:
      {"scaled": bool, "ok": bool, "factor": float,
       "from": [lw, lh], "to": [fw, fh], "note": str, "reason": str|None}
    - frame size == layout size, or layout has no frame size: identity.
    - aspect mismatch: ok=False + reason, layout returned UNCHANGED —
      the caller decides whether to skip (detection does; drawing may not).
    Never mutates the input layout.
    """
    lw, lh = layout.get("frame_width"), layout.get("frame_height")
    fw, fh = int(frame_w), int(frame_h)
    base = {"scaled": False, "ok": True, "factor": 1.0,
            "from": [lw, lh], "to": [fw, fh], "reason": None}
    if not (isinstance(lw, int) and lw > 0 and isinstance(lh, int) and lh > 0):
        return layout, dict(base, note="layout has no native frame size — "
                                       "rects used as-is")
    if (fw, fh) == (lw, lh):
        return layout, dict(base, note=f"frame matches layout ({lw}x{lh})")
    la, fa = lw / lh, fw / fh
    if abs(la - fa) / la > ASPECT_TOL:
        reason = (f"aspect ratio mismatch: layout {lw}x{lh} "
                  f"({la:.3f}) vs frame {fw}x{fh} ({fa:.3f}) — cannot scale; "
                  f"re-capture at {lw}x{lh} or calibrate a layout for "
                  f"{fw}x{fh}")
        return layout, dict(base, ok=False, reason=reason, note=reason)
    sx, sy = fw / lw, fh / lh

    def srect(r):
        x, y, w, h = r
        return [int(round(x * sx)), int(round(y * sy)),
                max(1, int(round(w * sx))), max(1, int(round(h * sy)))]

    out = dict(layout)
    for key in ("slots_a", "slots_b"):
        if isinstance(out.get(key), list):
            out[key] = [srect(r) for r in out[key]]
    for key in ("anchor", "replay", "score_map", "round_emblem"):
        cfg = out.get(key)
        if isinstance(cfg, dict) and cfg.get("rect"):
            out[key] = dict(cfg, rect=srect(cfg["rect"]))
        elif isinstance(cfg, (list, tuple)) and len(cfg) == 4:
            out[key] = srect(cfg)
    # 'hud_probe' (from calibrate_source) carries chip-box lists per side.
    if isinstance(out.get("hud_probe"), dict):
        hp = dict(out["hud_probe"])
        for key in ("chips_a", "chips_b"):
            if isinstance(hp.get(key), list):
                hp[key] = [srect(r) for r in hp[key]]
        out["hud_probe"] = hp
    # 'reject' is a LIST of marker dicts, each with its own rect — scale each.
    if isinstance(out.get("reject"), list):
        out["reject"] = [
            (dict(m, rect=srect(m["rect"]))
             if isinstance(m, dict) and m.get("rect") else m)
            for m in out["reject"]
        ]
    out["frame_width"], out["frame_height"] = fw, fh
    factor = round(sx, 4)
    note = f"layout scaled from {lw}x{lh} to {fw}x{fh} (factor {factor})"
    return out, dict(base, scaled=True, factor=factor, note=note)


def _load_template(layout: dict, key: str):
    cfg = layout.get(key)
    if not cfg:
        return None
    tpath = cfg["template"]
    if not os.path.isabs(tpath):
        tpath = os.path.join(db.REPO_ROOT, tpath)
    img = cv2.imread(tpath, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"layout {key} template not found: {tpath}")
    return {"img": img, "rect": cfg["rect"],
            "min_score": cfg.get("min_score", 0.75)}


def _load_reject_markers(layout: dict) -> list:
    """Load the optional 'reject' markers — banners whose presence means the
    frame is NOT countable gameplay (HIGHLIGHT / HIGHLIGHTS / POTG, etc.).

    Absent 'reject' key -> [] -> the feature is OFF and behavior is unchanged.

    Each layout entry looks like:
      {"label": "highlight", "rect": [x, y, w, h],
       "template": "layouts/<name>-highlight.png", "min_score": 0.8,
       "kind": "template"}          # 'kind' defaults to "template"

    'kind' is the single extension point: a future "text" kind (OCR the rect
    for the words HIGHLIGHT/HIGHLIGHTS appearing anywhere in it) plugs in here
    and in reject_reason() WITHOUT changing is_gameplay or the frame-filter
    path. Template kinds carry a loaded grayscale 'img'.
    """
    cfgs = layout.get("reject")
    if not cfgs:
        return []
    if not isinstance(cfgs, list):
        raise ValueError("layout 'reject' must be a list of marker configs")
    markers = []
    for i, cfg in enumerate(cfgs):
        if not isinstance(cfg, dict) or not cfg.get("rect"):
            raise ValueError(f"reject[{i}] needs at least a 'rect'")
        kind = cfg.get("kind", "template")
        label = cfg.get("label", "highlight")
        marker = {"label": label, "kind": kind, "rect": cfg["rect"],
                  "min_score": cfg.get("min_score", 0.8)}
        if kind == "template":
            tpath = cfg.get("template")
            if not tpath:
                raise ValueError(
                    f"reject[{i}] kind 'template' needs a 'template' path")
            if not os.path.isabs(tpath):
                tpath = os.path.join(db.REPO_ROOT, tpath)
            img = cv2.imread(tpath, cv2.IMREAD_GRAYSCALE)
            if img is None:
                raise FileNotFoundError(
                    f"reject marker '{label}' template not found: {tpath}")
            marker["img"] = img
        else:
            raise ValueError(
                f"reject[{i}] kind '{kind}' not supported yet (only "
                "'template'; a 'text'/OCR kind is the planned extension)")
        markers.append(marker)
    return markers


def reject_reason(frame_gray, markers: list) -> str | None:
    """First reject marker that fires -> its reason string, else None.

    This is the ONE place reject logic lives, so adding OCR later is a new
    branch here, not a filter rewrite. Template markers reuse the same
    region_score() as anchor/replay: matchTemplate slides the marker across
    its rect, so a banner is caught ANYWHERE WITHIN that rect (use a broad
    rect to approximate 'anywhere on screen').
    """
    for m in markers:
        if m.get("kind", "template") == "template":
            score = region_score(frame_gray, m)
            if score >= m["min_score"]:
                return f"{m['label']} (marker {score:.2f})"
        # future: elif m["kind"] == "text":  # OCR the rect for HIGHLIGHT(S)
    return None


# ------------------------------------------------------------ classifier
def region_score(frame_gray, tpl) -> float:
    """Best match score of tpl['img'] inside tpl['rect'] of the frame.

    The rect is already scaled to the frame's actual size by the caller
    (scale_layout_to_frame), but the template IMAGE stays whatever pixel
    size it was cut at. Two different things can make template and crop
    sizes disagree, and they need different handling:

    * template cut SMALLER than its rect on purpose (a few px inset, so
      matchTemplate can slide and tolerate a bit of misalignment) — the
      normal case, crop >= template, handled by matchTemplate's own
      sliding search exactly as before.
    * template LARGER than the crop — e.g. cut against a higher-resolution
      capture of this source than a later run's fallback format landed on
      (yt-dlp's format ladder can return 640x360 on one run and 854x480 on
      the next for the same source) — matchTemplate requires template <=
      crop in both dimensions or it raises/returns nothing useful, so this
      used to be a hard 0.0 no matter how well the region actually matches.
      Resize the template DOWN to the crop's exact size instead (same idea
      detect.match_slot_ranked already applies to hero templates: 'templates
      cut at one capture resolution match at any other')."""
    x, y, w, h = tpl["rect"]
    crop = frame_gray[y:y + h, x:x + w]
    if crop.shape[0] < 1 or crop.shape[1] < 1:
        return 0.0
    timg = tpl["img"]
    if timg.shape[0] > crop.shape[0] or timg.shape[1] > crop.shape[1]:
        if crop.shape[0] < 2 or crop.shape[1] < 2:
            return 0.0
        timg = cv2.resize(timg, (crop.shape[1], crop.shape[0]))
    res = cv2.matchTemplate(crop, timg, cv2.TM_CCOEFF_NORMED)
    return float(res.max())


def is_gameplay(frame_bgr, anchor, replay,
                rejects: list | None = None) -> tuple[bool, str, float]:
    """True only if HUD anchor is present, no reject marker fires, and the
    replay marker is absent.

    `rejects` is the optional list from _load_reject_markers (default None =
    feature OFF = unchanged behavior). Reject markers are checked FIRST so a
    HIGHLIGHT/POTG banner is rejected even when it overlays live gameplay or
    the HUD anchor is not visible on that frame.
    """
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    if rejects:
        reason = reject_reason(gray, rejects)
        if reason is not None:
            return False, reason, 0.0
    a_score = region_score(gray, anchor)
    if a_score < anchor["min_score"]:
        return False, f"no-hud (anchor {a_score:.2f})", a_score
    if replay is not None:
        r_score = region_score(gray, replay)
        if r_score >= replay["min_score"]:
            return False, f"replay (marker {r_score:.2f})", a_score
    return True, "gameplay", a_score


# --------------------------------------------------------------- ffmpeg
def extract_frames(video_path: str, out_dir: str, interval: int) -> list[str]:
    """One PNG every `interval` seconds, named by offset: 000600.png = 10 min."""
    os.makedirs(out_dir, exist_ok=True)
    # -vf fps=1/interval keeps timestamps derivable from the frame index.
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", video_path,
           "-vf", f"fps=1/{interval}", "-start_number", "0",
           os.path.join(out_dir, "idx%06d.png")]
    subprocess.run(cmd, check=True)
    frames = []
    for fn in sorted(os.listdir(out_dir)):
        if not fn.startswith("idx"):
            continue
        idx = int(fn[3:9])
        offset = idx * interval
        new = os.path.join(out_dir, f"{offset:06d}.png")
        os.rename(os.path.join(out_dir, fn), new)
        frames.append(new)
    return frames


def download_vod(url: str, out_path: str) -> None:
    """The WHOLE broadcast, ~720p. Only reached via --full-download now.

    Kept because two situations still need a local file: a CDN that refuses
    range requests, and archiving a broadcast that is about to disappear.
    Neither is the common case, which is why it is no longer the default."""
    cmd = ["yt-dlp", "-f", "bv*[height<=720]+ba/b[height<=720]/b",
           "--no-playlist", "-o", out_path, url]
    subprocess.run(cmd, check=True)


def sample_frames_remotely(url: str, frames_dir: str, interval: int,
                           duration: float | None = None,
                           height: int = 720, max_frames: int = 400,
                           source_id: str | None = None,
                           cache_root: str | None = None,
                           resolver=None) -> dict:
    """Fetch the sampled frames straight out of the remote broadcast.

    Same output contract as `extract_frames`: one image per sample, named by
    its offset, so everything downstream is unchanged. What differs is that
    the bytes for the other 99.9% of the broadcast are never transferred.

    Returns {"frames": [...paths], "offsets": [...], "stats": {...}}.
    """
    import remote_frames as rf

    if duration is None:
        try:
            import video_ingest as vi
            duration = float(vi.probe_vod(url).get("duration") or 0.0)
        except Exception:            # noqa: BLE001 — a probe failure is data
            duration = 0.0
    if not duration:
        raise ValueError(
            "could not determine the broadcast's duration, so there is "
            "nothing to sample across — pass a duration or use "
            "--full-download")

    offsets = [float(t) for t in range(0, int(duration), int(interval))]
    if len(offsets) > max_frames:
        # Thin the ladder evenly rather than truncating it: a capped run
        # must still cover the whole broadcast, not just its first hour.
        step = len(offsets) / float(max_frames)
        offsets = [offsets[int(i * step)] for i in range(max_frames)]

    src = rf.RemoteFrameSource(url, height=height, cache_root=cache_root,
                               source_id=source_id,
                               **({"resolver": resolver} if resolver else {}))
    got = src.grab(offsets)
    os.makedirs(frames_dir, exist_ok=True)
    frames = []
    for t in sorted(got):
        path = got[t]
        if not path:
            continue
        dest = os.path.join(frames_dir, f"{int(round(t)):06d}.png")
        img = cv2.imread(path)
        if img is None:
            continue
        cv2.imwrite(dest, img)
        frames.append(dest)
    return {"frames": frames, "offsets": sorted(got), "stats": src.report()}


# ------------------------------------------------------------------ run
def process_video(video_path: str, frames_dir: str, layout: dict,
                  report_only: bool = False, frames: list | None = None) -> dict:
    """Classify sampled frames as gameplay or not.

    `frames` lets a caller supply frames it already has (the remote sparse
    path does), so the classification half of this function is shared by
    both acquisition modes rather than duplicated."""
    anchor = _load_template(layout, "anchor")
    replay = _load_template(layout, "replay")
    rejects = _load_reject_markers(layout)
    if anchor is None:
        raise ValueError("layout must define an 'anchor' region+template")

    interval = layout.get("sample_interval_seconds", 300)
    if frames is None:
        frames = extract_frames(video_path, frames_dir, interval)

    kept, rejected = [], []
    for fp in frames:
        frame = cv2.imread(fp)
        if frame is None:
            rejected.append((fp, "unreadable"))
            os.remove(fp)
            continue
        ok, reason, _ = is_gameplay(frame, anchor, replay, rejects)
        if ok:
            kept.append(fp)
        else:
            rejected.append((fp, reason))
            if not report_only:
                os.remove(fp)

    return {"kept": kept, "rejected": rejected, "interval": interval}


def pending_matches(con, only: str | None):
    q = """SELECT m.* FROM matches m
           WHERE m.status='final' AND m.vod_url IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM comp_snapshots s
                             WHERE s.match_id = m.id)"""
    rows = con.execute(q).fetchall()
    return [r for r in rows if only is None or r["id"] == only]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", required=True, help="layouts/*.json")
    ap.add_argument("--match", help="only process this match id")
    ap.add_argument("--dry-run", metavar="VIDEO",
                    help="classify a local video file and report; no DB, no deletes")
    ap.add_argument("--max", type=int, default=2,
                    help="max VODs per run (free-CI budget)")
    ap.add_argument("--full-download", action="store_true",
                    help="fetch the entire VOD before sampling (the old "
                         "behaviour; only needed when the CDN refuses range "
                         "requests, or to archive the file)")
    ap.add_argument("--height", type=int, default=720,
                    help="max frame height to request (sparse mode)")
    args = ap.parse_args()

    layout = load_layout(args.layout)

    if args.dry_run:
        out = os.path.join(WORK_DIR, "dryrun")
        shutil.rmtree(out, ignore_errors=True)
        res = process_video(args.dry_run, out, layout, report_only=True)
        print(f"Sampled every {res['interval']}s — "
              f"kept {len(res['kept'])}, rejected {len(res['rejected'])}")
        for fp, reason in res["rejected"]:
            print(f"  reject {os.path.basename(fp)}: {reason}")
        print(f"Frames left in {out} for inspection.")
        return

    con = db.connect()
    todo = pending_matches(con, args.match)[: args.max]
    if not todo:
        print("Nothing to capture: no final matches with a vod_url and no snapshots.")
        return

    interval = layout.get("sample_interval_seconds", 300)
    for m in todo:
        mdir = os.path.join(WORK_DIR, m["id"])
        frames_dir = os.path.join(mdir, "frames")
        os.makedirs(mdir, exist_ok=True)

        if args.full_download:
            video = os.path.join(mdir, "vod.mp4")
            print(f"[{m['id']}] --full-download: fetching the whole VOD "
                  f"{m['vod_url']}")
            download_vod(m["vod_url"], video)
            print(f"[{m['id']}] extracting + classifying frames")
            res = process_video(video, frames_dir, layout)
            os.remove(video)  # disk hygiene for free CI
        else:
            print(f"[{m['id']}] sampling every {interval}s straight from "
                  f"{m['vod_url']} (no VOD download)")
            try:
                acq = sample_frames_remotely(
                    m["vod_url"], frames_dir, interval,
                    height=args.height, source_id=m["id"])
            except Exception as exc:      # noqa: BLE001
                print(f"[{m['id']}] sparse acquisition FAILED — {exc}")
                print(f"[{m['id']}] retry with --full-download if this "
                      f"broadcast will not serve byte ranges")
                continue
            st = acq["stats"]
            print(f"[{m['id']}] acquired {len(acq['frames'])} frame(s) "
                  f"({st['framesFromCache']} from cache) in "
                  f"{st['ffmpegCalls']} read(s), {st['ytdlpCalls']} yt-dlp call(s)"
                  + (f", {st['bytesDownloaded'] / 1e6:.1f} MB"
                     if st.get("bytesDownloaded") is not None else ""))
            res = process_video(None, frames_dir, layout,
                                frames=acq["frames"])

        print(f"[{m['id']}] kept {len(res['kept'])} gameplay frames "
              f"({len(res['rejected'])} rejected) → {frames_dir}")
        print(f"[{m['id']}] next: python3 pipeline/detect.py "
              f"--layout {args.layout} --match {m['id']}")


if __name__ == "__main__":
    main()
