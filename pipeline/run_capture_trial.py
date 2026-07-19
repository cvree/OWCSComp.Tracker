#!/usr/bin/env python3
"""
run_capture_trial.py — first capture trial + a visible preview page.

Tries a SHORT real capture from a YouTube VOD source (never the whole VOD),
draws the current layout boxes, crops hero-slot candidates, and writes a
self-describing preview at reports/capture_trial/index.html.

If the environment can't reach YouTube (no network / no yt-dlp), it does NOT
stop: it runs the exact same flow on the bundled fixture frames and labels
the whole report "FIXTURE FALLBACK". Either way it writes no comps to the DB
and runs no full-VOD detection — this is a preview only.

--clip-mode controls how real capture pulls frames from YouTube (see
video_ingest.py): "local-window" (default) downloads ONE clip covering the
whole [start,end] window and seeks locally — reliable at any offset, fixing
yt-dlp/ffmpeg failures seeking deep into a long VOD ("could not seek to
position ..."). "per-timestamp" is the older one-remote-seek-per-offset mode,
kept as an explicit fallback.

Usage:
  python3 pipeline/run_capture_trial.py --source owcs-afcxdimpsle \
        --start 1:30:00 --end 1:40:00 --every 30
  python3 pipeline/run_capture_trial.py --source owcs-afcxdimpsle \
        --start 1:30:00 --end 1:32:00 --every 30 --clip-mode local-window
"""
from __future__ import annotations
import argparse
import datetime as dt
import html
import os
import shutil
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import capture  # noqa: E402
import video_ingest as vi  # noqa: E402
import extract_calibration_frames as ecf  # noqa: E402
import build_layout_debug as bld  # noqa: E402
import build_hero_templates as bht  # noqa: E402

TRIAL_DIR = os.path.join(db.REPO_ROOT, "reports", "capture_trial")
FIX_FRAMES = os.path.join(db.REPO_ROOT, "pipeline", "fixtures", "video",
                          "demo_match", "frames")
DEMO_LAYOUT = os.path.join(db.REPO_ROOT, "pipeline", "fixtures", "video",
                           "demo-layout.json")
STARTER_LAYOUT = os.path.join(db.REPO_ROOT, "layouts", "owcs_youtube_2026.json")


def log(m: str) -> None:
    print(f"[capture-trial] {m}", flush=True)


def _rel(path: str) -> str:
    return os.path.relpath(path, TRIAL_DIR)


# --------------------------------------------------------------- the trial
def run_trial(source: str, start, end, every: int, height: int,
              sources_path: str, clip_mode: str = "local-window",
              real_probe: dict | None = None,
              real_frame_fn=None, real_download_fn=None) -> dict:
    """Attempt real capture; fall back to fixtures. Returns a result dict.

    clip_mode is passed straight through to extract_calibration_frames.run
    (see video_ingest.py for what each mode does). real_frame_fn/real_download_fn
    let callers (tests) inject fakes instead of touching yt-dlp/ffmpeg/network:
    in "local-window" mode real_download_fn replaces the clip download and
    real_frame_fn replaces the local per-offset ffmpeg seek; in "per-timestamp"
    mode real_frame_fn replaces the per-offset remote yt-dlp fetch.
    """
    shutil.rmtree(TRIAL_DIR, ignore_errors=True)
    frames_dir = os.path.join(TRIAL_DIR, "frames")
    debug_dir = os.path.join(TRIAL_DIR, "layout_debug")
    crops_dir = os.path.join(TRIAL_DIR, "crops")

    src = vi.find_source(sources_path, source)
    url = (src or {}).get("url") or (src or {}).get("vodUrl") or "(unknown)"

    real_cmd = (f"python3 pipeline/extract_calibration_frames.py "
                f"--source {source} --start {start} --end {end} "
                f"--every {every} --clip-mode {clip_mode}")

    mode = "real"
    reason = None
    meta = None
    frames: list[str] = []
    # Which layout the preview boxes/crops use. Real 1080p frames -> the
    # starter layout being calibrated. Fixture frames -> the self-consistent
    # demo layout so the preview actually lines up.
    layout_path = STARTER_LAYOUT

    try:
        if src is None:
            raise RuntimeError(f"no source id '{source}' in {sources_path}")
        if not vi.is_youtube_source(src):
            raise RuntimeError(f"source '{source}' is not a youtube source")
        ecf_kwargs = dict(source=source, start=start, end=end, every=every,
                          out=frames_dir, fmt="png", height=height,
                          sources_path=sources_path, probe_override=real_probe,
                          clip_mode=clip_mode)
        if clip_mode == "local-window":
            if real_download_fn is not None:
                ecf_kwargs["download_fn"] = real_download_fn
            if real_frame_fn is not None:
                ecf_kwargs["local_frame_fn"] = real_frame_fn
        else:
            ecf_kwargs["frame_fn"] = real_frame_fn or vi._download_section_frame
        res = ecf.run(**ecf_kwargs)
        frames = res.get("frames", [])
        meta = res.get("plan")
        if not frames:
            raise RuntimeError("capture returned zero frames")
        log(f"REAL capture OK — {len(frames)} frames from {url}")
    except Exception as e:  # noqa: BLE001 — any failure -> fixture fallback
        mode = "fixture fallback"
        reason = f"{type(e).__name__}: {e}"
        layout_path = DEMO_LAYOUT
        log(f"real capture unavailable ({reason}); using fixture fallback.")
        os.makedirs(frames_dir, exist_ok=True)
        for fn in sorted(f for f in os.listdir(FIX_FRAMES) if f.endswith(".png")):
            shutil.copy(os.path.join(FIX_FRAMES, fn),
                        os.path.join(frames_dir, fn))
            frames.append(os.path.join(frames_dir, fn))

    layout = capture.load_layout(layout_path)

    # ---- layout debug ----------------------------------------------------
    debug_imgs = bld.process_dir(frames_dir, layout, debug_dir)

    # In fixture mode, also show the STARTER layout's placeholder boxes on a
    # real-shaped frame so the misalignment (what calibration fixes) is visible.
    starter_demo = None
    if mode == "fixture fallback":
        first = sorted(f for f in os.listdir(frames_dir) if f.endswith(".png"))[0]
        img = cv2.imread(os.path.join(frames_dir, first))
        ann = bld.draw_layout(img, capture.load_layout(STARTER_LAYOUT))
        starter_demo = os.path.join(debug_dir, "STARTER_layout_placeholders.png")
        cv2.imwrite(starter_demo, ann)

    # ---- hero crop candidates -------------------------------------------
    crop_recs = bht.crop_candidates(frames_dir, layout, crops_dir)

    result = {
        "mode": mode, "reason": reason, "source": source, "url": url,
        "start": str(start), "end": str(end), "every": every,
        "clip_mode": clip_mode,
        "real_cmd": real_cmd, "layout_path": _rel(layout_path)
        if layout_path.startswith(TRIAL_DIR) else os.path.relpath(
            layout_path, db.REPO_ROOT),
        "frames": sorted(frames), "debug_imgs": sorted(debug_imgs),
        "starter_demo": starter_demo, "crop_recs": crop_recs,
        "plan": meta, "frames_dir": frames_dir, "debug_dir": debug_dir,
        "crops_dir": crops_dir,
    }
    write_index(result)
    return result


# ------------------------------------------------------------------ report
def _thumbs(paths, cls="shot") -> str:
    out = []
    for p in paths:
        rel = html.escape(_rel(p))
        cap = html.escape(os.path.basename(p))
        out.append(f'<figure class="{cls}"><a href="{rel}" target="_blank">'
                   f'<img src="{rel}" loading="lazy"></a>'
                   f'<figcaption>{cap}</figcaption></figure>')
    return "".join(out)


def _crop_gallery(recs) -> str:
    by_frame: dict[str, list] = {}
    for r in recs:
        by_frame.setdefault(r["frame"], []).append(r)
    blocks = []
    for frame in sorted(by_frame):
        rs = by_frame[frame]
        t = html.escape(rs[0]["time"])
        cells = []
        for side in ("A", "B"):
            for r in sorted((x for x in rs if x["side"] == side),
                            key=lambda x: x["slot"]):
                rel = html.escape(_rel(r["path"]))
                cells.append(f'<figure class="crop"><img src="{rel}" loading="lazy">'
                             f'<figcaption>{r["side"]}{r["slot"]}</figcaption></figure>')
        blocks.append(f'<div class="cropframe"><h4>{t} '
                      f'<small>{html.escape(frame)}</small></h4>'
                      f'<div class="row">{"".join(cells)}</div></div>')
    return "".join(blocks)


def write_index(res: dict) -> None:
    os.makedirs(TRIAL_DIR, exist_ok=True)
    real = res["mode"] == "real"
    banner_cls = "ok" if real else "warn"
    banner = ("REAL YOUTUBE CAPTURE" if real
              else "FIXTURE FALLBACK — no real YouTube frames")
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    reason_html = ""
    if not real:
        reason_html = (
            f'<p class="reason"><b>Why fallback:</b> '
            f'{html.escape(res["reason"] or "unknown")}. '
            f'The sandbox has no network and no <code>yt-dlp</code>, so real '
            f'capture from YouTube cannot run here. The flow below is the '
            f'identical pipeline, run on the bundled fixture frames.</p>')

    if real:
        next_steps = [
            "Open the layout_debug images and confirm the boxes hug each hero "
            "portrait, the anchor, and the replay marker.",
            "Nudge rectangles in layouts/owcs_youtube_2026.json where boxes "
            "are off, then re-run this trial.",
            "From the crop candidates, rename one clean crop per hero to "
            "templates/&lt;hero_id&gt;.png.",
        ]
    else:
        next_steps = [
            "Run the real command (top of page) on a machine with network + "
            "<code>yt-dlp</code> + <code>ffmpeg</code> to pull the real "
            "1:30:00–1:40:00 frames.",
            "The fixture preview uses the self-consistent demo layout so the "
            "boxes line up. For the real VOD you calibrate "
            "<code>layouts/owcs_youtube_2026.json</code> instead — see the "
            "'STARTER layout placeholders' image to see how far off the "
            "current guesses are.",
            "Then draw debug boxes, adjust rectangles, and crop hero "
            "candidates exactly as shown here.",
        ]

    plan_html = ""
    if res.get("plan"):
        p = res["plan"]
        plan_html = (f'<li>Planned real window: '
                     f'{vi.fmt_hms(p["start"])}–{vi.fmt_hms(p["end"])} '
                     f'every {p["interval"]}s → {p["count"]} frames</li>')
    else:
        # compute the planned count for the requested real window for display
        try:
            s = vi.parse_time(res["start"]); e = vi.parse_time(res["end"])
            cnt = len(range(s, e, res["every"]))
            plan_html = (f'<li>Real window would sample '
                         f'{res["start"]}–{res["end"]} every {res["every"]}s '
                         f'→ {cnt} frames (only ~2s fetched per frame)</li>')
        except Exception:
            pass

    starter_html = ""
    if res.get("starter_demo"):
        starter_html = (
            '<h2>Starter layout placeholders (needs calibration)</h2>'
            '<p>These are the placeholder rectangles from '
            '<code>owcs_youtube_2026.json</code> drawn on a sample frame. '
            'They are intentionally not aligned yet — aligning them to real '
            'OWCS frames is the calibration step.</p>'
            f'<div class="grid">{_thumbs([res["starter_demo"]], "shot")}</div>')

    doc = f"""<!doctype html><meta charset="utf-8">
<title>OWCS capture trial — {html.escape(res['source'])}</title>
<style>
 body{{font:14px system-ui,sans-serif;margin:24px;max-width:1100px;
   background:#0f1115;color:#e8e8ea}}
 h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:17px;margin:26px 0 8px;
   border-bottom:1px solid #2a2e37;padding-bottom:4px}}
 h4{{margin:14px 0 6px;font-size:13px;font-weight:600}}
 small{{color:#8a90a0;font-weight:400}} code{{background:#1b1f27;padding:1px 5px;
   border-radius:4px}}
 .banner{{padding:12px 16px;border-radius:8px;font-weight:700;margin:12px 0}}
 .banner.ok{{background:#123a1e;color:#7ee29b;border:1px solid #1e6b38}}
 .banner.warn{{background:#3a2e12;color:#f2c879;border:1px solid #7a5c1e}}
 .reason{{color:#f2c879}}
 pre{{background:#1b1f27;padding:12px;border-radius:8px;overflow:auto;
   font-size:12.5px;line-height:1.5}}
 .grid,.row{{display:flex;flex-wrap:wrap;gap:10px}}
 figure{{margin:0;text-align:center}}
 .shot img{{width:210px;border:1px solid #2a2e37;border-radius:4px;background:#000}}
 .crop img{{width:64px;height:64px;object-fit:contain;background:#000;
   border:1px solid #2a2e37;image-rendering:pixelated}}
 figcaption{{color:#8a90a0;font-size:11px;margin-top:2px}}
 .cropframe{{margin:6px 0 14px}} ul{{line-height:1.7}}
 .meta{{color:#8a90a0}}
</style>
<h1>OWCS Comp Tracker — capture trial</h1>
<p class="meta">{html.escape(res['source'])} ·
 <a href="{html.escape(res['url'])}" style="color:#79b8ff">VOD</a> ·
 generated {ts}</p>
<div class="banner {banner_cls}">{banner}</div>
{reason_html}

<h2>What ran (no DB writes, no full-VOD detection)</h2>
<ul>
 <li><b>Real capture command:</b> <code>{html.escape(res['real_cmd'])}</code></li>
 <li><b>Clip mode:</b> <code>{html.escape(res.get('clip_mode', 'local-window'))}</code></li>
 {plan_html}
 <li>Layout used for these previews: <code>{html.escape(res['layout_path'])}</code></li>
 <li>Frames previewed: {len(res['frames'])} ·
     debug images: {len(res['debug_imgs'])} ·
     hero crops: {len(res['crop_recs'])}</li>
</ul>
<pre># exact flow (fixture fallback runs the same three tools)
python3 pipeline/extract_calibration_frames.py --source {html.escape(res['source'])} \\
      --start {html.escape(res['start'])} --end {html.escape(res['end'])} --every {res['every']} \\
      --clip-mode {html.escape(res.get('clip_mode', 'local-window'))}
python3 pipeline/build_layout_debug.py --layout layouts/owcs_youtube_2026.json \\
      --frames-dir reports/calibration_frames/{html.escape(res['source'])}
python3 pipeline/build_hero_templates.py --layout layouts/owcs_youtube_2026.json \\
      --frames-dir reports/calibration_frames/{html.escape(res['source'])}</pre>

<h2>Captured frames{"" if real else " (fixture)"}</h2>
<div class="grid">{_thumbs(res['frames'])}</div>

<h2>Layout debug images</h2>
<p>Green = team A slots, blue = team B slots, yellow = anchor,
   red = replay, magenta = score/map.</p>
<div class="grid">{_thumbs(res['debug_imgs'])}</div>

{starter_html}

<h2>Hero crop candidates</h2>
<p>Rename the cleanest crop of each hero to
   <code>templates/&lt;hero_id&gt;.png</code>. A/B = team, 1–5 = slot.</p>
{_crop_gallery(res['crop_recs'])}

<h2>Next adjustment needed</h2>
<ul>{''.join(f'<li>{s}</li>' for s in next_steps)}</ul>
"""
    with open(os.path.join(TRIAL_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(doc)
    log(f"wrote {os.path.join(TRIAL_DIR, 'index.html')}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="owcs-afcxdimpsle")
    ap.add_argument("--start", default="1:30:00")
    ap.add_argument("--end", default="1:40:00")
    ap.add_argument("--every", type=int, default=30)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--sources", default=vi.DEFAULT_SOURCES)
    ap.add_argument("--clip-mode", choices=["local-window", "per-timestamp"],
                    default="local-window",
                    help="local-window (default): one yt-dlp download for "
                         "the whole window + local ffmpeg seeks (reliable "
                         "at any offset). per-timestamp: one remote yt-dlp "
                         "seek per offset (fallback; unreliable deep into "
                         "long VODs).")
    args = ap.parse_args()
    res = run_trial(args.source, args.start, args.end, args.every,
                    args.height, args.sources, clip_mode=args.clip_mode)
    print(f"[capture-trial] mode: {res['mode'].upper()} — "
          f"open reports/capture_trial/index.html")


if __name__ == "__main__":
    main()
