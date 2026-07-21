#!/usr/bin/env python3
"""
run_owcs_auto.py — ONE command: VOD window (or local MP4) -> frames ->
filter -> detection (if ready) -> layout debug images -> site export.

YouTube source (from video_sources.json; clip is downloaded ONCE into
work/clips/ and reused on re-runs):
  python pipeline/run_owcs_auto.py --source owcs-afcxdimpsle \
      --start 1:30:00 --end 1:35:00 --every 30

Local MP4 (no network, no yt-dlp):
  python pipeline/run_owcs_auto.py --local work/clips/day1_0130_0135.mp4 \
      --start 0 --end 5:00 --every 30

Every step prints a numbered banner with what it is doing, where output
goes, and OK/FAILED. Afterwards:
  python -m http.server 8000     ->  http://localhost:8000/sources.html

Detection runs only if the layout's hero templates exist ("if ready");
results go to reports/auto/<run>/detections.json for review — this command
NEVER writes comps to the database. Run status is appended to
data/auto_runs.json and exported to the site as OWCS_DATA.autoRuns.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402
import video_ingest as vi  # noqa: E402
import download_vod_clip as dvc  # noqa: E402
import preflight as pf  # noqa: E402

DEFAULT_LAYOUT = "layouts/owcs_youtube_2026.json"
AUTO_RUNS_PATH = os.path.join(db.REPO_ROOT, "data", "auto_runs.json")
MAX_RUNS_KEPT = 20

# Clip-download stall guards (seconds with no real byte progress before the
# yt-dlp download is killed and a fallback format is tried). Fast mode is a
# smoke test — it should finish or fail quickly, never heartbeat forever.
FAST_STALL_TIMEOUT = 75
DEFAULT_STALL_TIMEOUT = 180


def log(msg: str) -> None:
    print(f"[auto] {msg}", flush=True)


def banner(i: int, n: int, title: str) -> None:
    log(f"[{i}/{n}] {title}")


# Map common failures to a "what to do next" line (terminal AND report).
# More specific patterns MUST come first — remedy_for returns the first match,
# and a stall error string also contains "yt-dlp", so the stall/JS/timeout
# entries are listed ahead of the generic "yt-dlp not found" one.
_REMEDIES = [
    ("preflight failed", "fix the FAIL item(s) named in the error (each "
                         "carries its own fix), then re-run. "
                         "`python pipeline/preflight.py --source <id>` shows "
                         "the same checks any time."),
    ("no such table", "the database is missing its tables — run "
                      "`python pipeline/init_db.py --with-sample` once, then "
                      "re-run (the preflight step normally auto-fixes this)."),
    ("no download progress", "YouTube section download stalled. Try a local "
                             "MP4 (--local), another VOD, browser cookies "
                             "(yt-dlp --cookies-from-browser), or install a JS "
                             "runtime (Deno/Node) for yt-dlp."),
    ("stalltimeout", "YouTube section download stalled. Try a local MP4 "
                     "(--local), another VOD, browser cookies, or install a JS "
                     "runtime for yt-dlp."),
    ("javascript runtime", "yt-dlp needs a JS runtime for some YouTube "
                           "formats — install Deno or Node.js, then re-run. "
                           "See https://github.com/yt-dlp/yt-dlp/wiki."),
    ("ffmpeg", "ffmpeg not found — install from ffmpeg.org (Windows: "
               "`winget install ffmpeg`), ensure it is on PATH, then re-run."),
    ("yt-dlp not found", "yt-dlp not found — install with `pip install yt-dlp` "
                         "(Windows: `winget install yt-dlp`), then re-run."),
    ("no such file or directory: 'yt-dlp'",
     "yt-dlp not found — install with `pip install yt-dlp` "
     "(Windows: `winget install yt-dlp`), then re-run."),
    ("local file not found", "check the --local path (relative paths resolve "
                             "from the repo root)."),
    ("no frames extracted", "window may be past the VOD end — check --start/"
                            "--end against the VOD duration in the probe line."),
    ("invalid/corrupt", "the clip file was invalid/corrupt and was removed — "
                        "re-run to download a fresh clip (add --force to force "
                        "a clean re-download)."),
    ("invalidclip", "the clip file was invalid/corrupt — re-run to download a "
                    "fresh clip (add --force to force a clean re-download)."),
    ("could not read vod metadata", "yt-dlp couldn't read the VOD — check the "
     "URL is public/not age-gated, that you have network access, and (if "
     "behind a proxy) that TLS works; browser cookies "
     "(--cookies-from-browser) often help. Or use a local MP4 (--local)."),
    ("certificate_verify_failed", "TLS/SSL error reaching YouTube — a proxy or "
     "clock issue is blocking the connection. Fix the network/proxy or use a "
     "local MP4 (--local)."),
    ("sign in to confirm", "YouTube is age/bot-gating this VOD — pass browser "
     "cookies: yt-dlp --cookies-from-browser chrome, or use a local MP4."),
    ("could not seek", "remote seek failed — the clip download should avoid "
                       "this; retry with --force-clip."),
    ("yt-dlp", "yt-dlp not found — install with `pip install yt-dlp` "
               "(Windows: `winget install yt-dlp`), then re-run."),
]


def remedy_for(error: str) -> str:
    low = (error or "").lower()
    for needle, fix in _REMEDIES:
        if needle in low:
            return fix
    return ("re-run the same command; if it fails again, open the report's "
            "step table to see which step broke and its detail.")


def _mark(steps: list, name: str, status: str, detail: str = "",
          out: str = "") -> dict:
    """Append a per-step result record (blueprint: name/status/detail/out)."""
    st = {"name": name, "status": status, "detail": detail, "out": out}
    steps.append(st)
    return st


def run_status_of(record: dict) -> str:
    """Blueprint labels + timeout. ok / partial / failed / timeout.

    Capture success is INDEPENDENT of detection: a run whose clip, frames,
    layout debug, crops, and export all succeeded is at worst PARTIAL when
    detection was skipped/errored, the filter wasn't ready, or the evidence
    pages had a problem — never 'failed' for those reasons alone."""
    if not record.get("ok"):
        return "timeout" if record.get("timedOut") else "failed"
    det = (record.get("detection") or {}).get("status")
    if det in ("skipped", "error") or record.get("filtered") is False \
            or record.get("evidenceError"):
        return "partial"
    return "ok"


def _tag(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}{m:02d}{s:02d}"


def _abspath(p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(db.REPO_ROOT, p)


# ------------------------------------------------------------ step impls
def _probe_local(path: str) -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(f"local file not found: {path}")
    return {"title": os.path.basename(path), "duration": 0,
            "sizeBytes": os.path.getsize(path)}


def _extract_frames(clip_path: str, clip_start: int, offsets: list[int],
                    out_dir: str, frame_fn=vi._extract_frame_local) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    made = []
    n = len(offsets)
    for i, off in enumerate(offsets, start=1):
        out = os.path.join(out_dir, f"{off:06d}.png")
        log(f"  [{i}/{n}] frame @ {vi.fmt_hms(off)} -> {os.path.basename(out)}")
        frame_fn(clip_path, off, clip_start, out)
        made.append(out)
    return made


def _step_filter(raw_dir: str, kept_dir: str, layout_path: str) -> dict:
    """Filter gameplay frames; falls back to raw if layout lacks templates."""
    import capture
    import frame_filter
    layout = capture.load_layout(layout_path)
    try:
        res = frame_filter.filter_frames(raw_dir, kept_dir, layout)
        return {"filtered": True, "keptDir": kept_dir,
                "kept": len(res["kept"]), "rejected": len(res["rejected"])}
    except (ValueError, FileNotFoundError) as e:
        log(f"  filter not ready ({e}) — using raw frames unfiltered.")
        raw_count = len([f for f in os.listdir(raw_dir) if f.endswith(".png")])
        return {"filtered": False, "keptDir": raw_dir,
                "kept": raw_count, "rejected": 0}


def detect_preflight(frames_dir: str, layout: dict) -> str | None:
    """Cheap sanity checks BEFORE detection so failures are explained, not
    raw cv2 assertions. Returns a skip reason, or None when safe to run."""
    import cv2
    lw = layout.get("frame_width")
    lh = layout.get("frame_height")
    pngs = sorted(f for f in os.listdir(frames_dir)
                  if f.endswith(".png")) if os.path.isdir(frames_dir) else []
    if not pngs:
        return f"no frames in {frames_dir}"
    img = cv2.imread(os.path.join(frames_dir, pngs[0]))
    if img is None:
        return f"first frame unreadable: {pngs[0]}"
    fh, fw = img.shape[:2]
    import capture
    scaled, sinfo = capture.scale_layout_to_frame(layout, fw, fh)
    if not sinfo["ok"]:  # same-aspect mismatches scale; others cannot
        return (f"frame is {fw}x{fh} but layout expects {lw}x{lh} and the "
                f"aspect ratios differ — cannot auto-scale; re-run with "
                f"--height {lh}, or calibrate a layout for {fw}x{fh} frames")
    for key in ("slots_a", "slots_b"):
        for (x, y, w, h) in scaled.get(key, []):
            if w <= 0 or h <= 0 or x < 0 or y < 0 \
                    or x + w > fw or y + h > fh:
                return (f"layout box {key} [{x},{y},{w},{h}] "
                        f"({sinfo['note']}) falls outside the {fw}x{fh} "
                        f"frame — layout needs calibration "
                        "(see docs/layout-calibration.md)")
    return None


def _step_detect(frames_dir: str, layout_path: str, report_dir: str) -> dict:
    """Run hero detection IF templates are ready; write JSON only, no DB."""
    import capture
    import hero_overlay_detect as hod
    layout = capture.load_layout(layout_path)
    tdir = hod.resolve_templates_dir(layout, None)
    if not tdir or not os.path.isdir(tdir) or not os.listdir(tdir):
        return {"status": "skipped",
                "reason": f"no hero templates for layout ({tdir or 'none'}) "
                          "— build them with build_hero_templates.py"}
    skip = detect_preflight(frames_dir, layout)
    if skip:
        return {"status": "skipped", "reason": skip}
    import cv2
    pngs = sorted(f for f in os.listdir(frames_dir) if f.endswith(".png"))
    img0 = cv2.imread(os.path.join(frames_dir, pngs[0]))
    fh, fw = img0.shape[:2]
    layout, sinfo = capture.scale_layout_to_frame(layout, fw, fh)
    if sinfo["scaled"]:
        log(f"  {sinfo['note']}")
    lib = hod.load_lib(layout)
    results = hod.detect_dir(frames_dir, layout, lib)
    out = os.path.join(report_dir, "detections.json")
    os.makedirs(report_dir, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    n = len(results) if hasattr(results, "__len__") else 0
    return {"status": "ok", "framesRead": n, "out": out,
            "layoutScale": sinfo["note"]}


def _step_debug(frames_dir: str, layout_path: str, out_dir: str) -> dict:
    """Annotated frames + layout.html + crops.html (Phase 2 evidence)."""
    import capture
    import build_layout_debug as bld
    import build_crop_report as bcr
    layout = capture.load_layout(layout_path)
    made = bld.process_dir(frames_dir, layout, out_dir)
    report_dir = os.path.dirname(out_dir.rstrip("/\\"))
    warns = bld.validate_layout(layout)
    fsz = bld.first_frame_size(frames_dir)
    res: dict = {"images": len(made), "outDir": out_dir,
                 "layoutWarnings": len(warns)}
    if fsz:
        _, sinfo = capture.scale_layout_to_frame(layout, *fsz)
        res["layoutScale"] = sinfo["note"]
        if sinfo["scaled"]:
            log(f"  {sinfo['note']}")
    try:  # evidence pages are best-effort: never kill the run over HTML
        bld.write_layout_html(os.path.join(report_dir, "layout.html"),
                              layout, layout_path, made,
                              frames_dir=frames_dir, frame_size=fsz)
        cres = bcr.process(frames_dir, layout, report_dir)
        res["crops"] = cres["crops"]
        res["cropsExpected"] = cres.get("frames", 0) * 10
        res["cropTemplates"] = cres["templates"]
        if cres.get("skipped"):
            res["cropSkipped"] = [f"{s['frame']} {s['slot']}: {s['note']}"
                                  for s in cres["skipped"]]
            for line in res["cropSkipped"]:
                log(f"  crop SKIPPED {line}")
        # hero crop capture/review page (capture-only — writes ZERO comps)
        import capture_hero_crops as chc
        hres = chc.capture_run(os.path.basename(report_dir.rstrip("/\\")),
                               layout, frames_dir, report_dir)
        res["heroCrops"] = hres["crops"]
        log(f"  hero crop review: {hres['crops']} crop(s) -> "
            f"hero_crops.html (review/label only, no comps)")
    except Exception as e:
        res["evidenceError"] = f"{type(e).__name__}: {e}"
        log(f"  evidence pages failed (non-fatal): {res['evidenceError']}")
    return res


def _step_vision_dashboard(run_name: str, layout_path: str) -> dict:
    """The ONE debug page: anchors (context crops), hero guesses, scores,
    quality, status ladder — over whatever this run already produced.
    Best-effort: it never writes comps/DB/templates, so a failure here is
    never a run failure, only a note on the report."""
    import vision_dashboard as vd
    res = vd.generate(run_name, layout_path, root=db.REPO_ROOT)
    return {"status": "ok", "out": res["html"], "next": res["rec"]["human"]}


def _step_export() -> dict:
    import export_data
    con = db.connect()
    payload = export_data.build_payload(con)
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    body = (export_data.HEADER.format(ts=ts)
            + json.dumps(payload, indent=1, ensure_ascii=False) + ";\n")
    os.makedirs(os.path.dirname(export_data.OUT_PATH), exist_ok=True)
    with open(export_data.OUT_PATH, "w", encoding="utf-8") as f:
        f.write(body)
    return {"out": export_data.OUT_PATH,
            "matches": len(payload["matches"])}


# --------------------------------------------------------------- status
def append_run(record: dict, path: str = AUTO_RUNS_PATH) -> None:
    """Insert or update (upsert) a run record — keyed by run + startedAt."""
    runs = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                runs = json.load(f).get("runs", [])
        except (ValueError, OSError):
            runs = []
    key = (record.get("run"), record.get("startedAt"))
    runs = [r for r in runs
            if (r.get("run"), r.get("startedAt")) != key]
    runs.insert(0, record)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"runs": runs[:MAX_RUNS_KEPT]}, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


_STEP_COLORS = {"ok": "#2ebd6b", "skipped": "#e8a13c", "error": "#e8a13c",
                "failed": "#ff5c64", "not-run": "#64748f"}
_RUN_COLORS = {"ok": "#2ebd6b", "partial": "#e8a13c", "failed": "#ff5c64",
               "timeout": "#ff5c64"}

# Control-room dark theme — matches assets/css/style.css tokens so reports
# feel like part of the site even though they are standalone files.
_REPORT_CSS = """
:root{--bg:#060b15;--raise:#0c1524;--surface:#111c31;--line:#1f2e4d;
--text:#e9eef7;--muted:#8ea0bd;--amber:#ffa92b}
body{font-family:Inter,"Segoe UI",system-ui,sans-serif;max-width:980px;
margin:0 auto;padding:28px 18px 48px;color:var(--text);background:
radial-gradient(1000px 420px at 85% -10%,rgba(79,169,255,.07),transparent 60%),
var(--bg);line-height:1.55}
h1{font-family:"Chakra Petch","Segoe UI",sans-serif;font-size:1.5rem;
letter-spacing:.01em}
h2{font-family:"Chakra Petch","Segoe UI",sans-serif;font-size:1.02rem;
margin-top:30px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted)}
table{border-collapse:collapse;width:100%;margin:10px 0;background:var(--surface);
border:1px solid var(--line);border-radius:10px;overflow:hidden}
td,th{border-bottom:1px solid var(--line);padding:8px 12px;text-align:left;
font-size:.88rem;vertical-align:top}
th{font-family:"Chakra Petch",sans-serif;font-size:.68rem;letter-spacing:.12em;
text-transform:uppercase;color:var(--muted);background:var(--raise)}
tr:last-child td{border-bottom:0}
code{background:rgba(255,255,255,.07);padding:1px 6px;border-radius:4px;
font-family:ui-monospace,Consolas,monospace;font-size:.85em}
.pill{display:inline-block;color:#fff;border-radius:999px;padding:2px 12px;
font-family:"Chakra Petch",sans-serif;font-weight:700;font-size:.72rem;
letter-spacing:.08em;text-transform:uppercase;vertical-align:middle}
.err{border:1px solid rgba(255,92,100,.5);background:rgba(255,92,100,.12);
padding:12px 16px;border-radius:10px;margin:14px 0;border-left:4px solid #ff5c64}
.thumbs img{max-width:220px;border:1px solid var(--line);margin:4px;
border-radius:8px;transition:border-color .12s ease}
.thumbs img:hover{border-color:var(--amber)}
.muted{color:var(--muted);font-size:.85rem}
a{color:var(--amber);text-decoration:none}a:hover{text-decoration:underline}
"""


def _esc(v) -> str:
    return (str(v).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;")) if v is not None else "—"


def build_report_html(record: dict, debug_images: list[str]) -> str:
    """Pure string-template report (one shared helper, no framework)."""
    status = record.get("runStatus") or run_status_of(record)
    pill = (f"<span class='pill' style='background:"
            f"{_RUN_COLORS.get(status, '#777')}'>{status.upper()}</span>")
    det = record.get("detection") or {}
    res = record.get("clipResolution") or {}
    actual_res = (f"{res.get('width')}x{res.get('height')}"
                  if res.get("width") else "unknown")
    req_h = record.get("height")
    res_line = (f"requested <={req_h}p · actual {actual_res}"
                if req_h else f"actual {actual_res}")
    if (req_h and res.get("height")
            and int(res["height"]) < int(req_h) * 0.9):
        res_line += " ⚠ lower than requested (fallback format)"
    crops_line = "—"
    if record.get("cropsExpected") is not None:
        crops_line = (f"{record.get('crops', 0)} of "
                      f"{record['cropsExpected']} expected")
        if record.get("cropSkipped"):
            crops_line += f" · {len(record['cropSkipped'])} slot(s) skipped"
    summary = "".join(
        f"<tr><td>{k}</td><td>{_esc(v)}</td></tr>" for k, v in [
            ("mode", record.get("mode")), ("source", record.get("source")),
            ("window", f"{record.get('window')} every "
                       f"{record.get('every')}s"),
            ("layout", record.get("layout")),
            ("resolution", res_line),
            ("frames", f"{record.get('framesRaw', '—')} extracted / "
                       f"{record.get('framesPlanned', '—')} planned · "
                       f"{record.get('framesKept', '—')} kept"
                       + (" (unfiltered)" if record.get("filtered") is False
                          else "")),
            ("layout scaling", record.get("layoutScale") or "—"),
            ("crops", crops_line),
            ("detection", det.get("status") or "—"),
            ("clip", f"{record.get('clip')}"
                     + (" (reused cached)" if record.get("clipReused")
                        else " (downloaded fresh)"
                        if record.get("mode") == "youtube" else "")),
            ("started", record.get("startedAt")),
            ("finished", record.get("finishedAt")),
        ])
    # ---- capture attempts: every strategy tried, in order ----------------
    attempts = record.get("captureAttempts") or []
    att_sec = ""
    if attempts:
        att_rows = "".join(
            f"<tr><td>{i}</td><td>{_esc(a.get('strategy'))}</td>"
            f"<td><code>{_esc(a.get('format'))}</code></td>"
            f"<td><span class='pill' style='background:"
            f"{'#2ebd6b' if a.get('outcome') == 'ok' else '#ff5c64'}'>"
            f"{_esc(a.get('outcome'))}</span></td>"
            f"<td>{_esc(a.get('seconds', '—'))}s</td>"
            f"<td>{_esc(a.get('note') or '—')}</td></tr>"
            for i, a in enumerate(attempts, start=1))
        att_sec = ("<h2>Capture attempts</h2>"
                   "<table><tr><th>#</th><th>strategy</th><th>format</th>"
                   "<th>outcome</th><th>took</th><th>note</th></tr>"
                   f"{att_rows}</table>")
    elif record.get("clipReused"):
        att_sec = ("<h2>Capture attempts</h2><p class='muted'>None — a "
                   "cached clip was validated and reused (pass force "
                   "re-download to fetch fresh).</p>")
    # ---- skipped crop slots, with exact reasons --------------------------
    skip_sec = ""
    if record.get("cropSkipped"):
        skip_rows = "".join(f"<li><code>{_esc(s)}</code></li>"
                            for s in record["cropSkipped"])
        skip_sec = (f"<h2>Skipped crop slots "
                    f"({len(record['cropSkipped'])})</h2>"
                    f"<ul>{skip_rows}</ul>")
    # ---- preflight results ----------------------------------------------
    pre = record.get("preflight") or {}
    pre_sec = ""
    if pre.get("checks"):
        pcolor = {"ok": "#2ebd6b", "warn": "#e8a13c", "fail": "#ff5c64"}
        pre_rows = "".join(
            f"<tr><td>{_esc(c['name'])}</td>"
            f"<td><span class='pill' style='background:"
            f"{pcolor.get(c['status'], '#777')}'>{_esc(c['status'])}</span>"
            f"</td><td>{_esc(c['detail'])}"
            + (f"<br><em>fix: {_esc(c['remedy'])}</em>" if c.get("remedy")
               else "") + "</td></tr>"
            for c in pre["checks"])
        pre_sec = ("<h2>Preflight (capture readiness)</h2>"
                   "<table><tr><th>check</th><th>status</th>"
                   f"<th>detail</th></tr>{pre_rows}</table>")
    step_rows = "".join(
        f"<tr><td>{i}</td><td>{_esc(s['name'])}</td>"
        f"<td><span class='pill' style='background:"
        f"{_STEP_COLORS.get(s['status'], '#777')}'>{_esc(s['status'])}</span>"
        f"</td><td>{_esc(s.get('detail'))}</td>"
        f"<td><code>{_esc(s.get('out') or '—')}</code></td></tr>"
        for i, s in enumerate(record.get("steps") or [], start=1))
    err_box = ""
    if not record.get("ok") and record.get("error"):
        err_box = (f"<div class='err'><strong>FAILED:</strong> "
                   f"{_esc(record['error'])}<br>"
                   f"<strong>Next:</strong> {_esc(remedy_for(record['error']))}"
                   "</div>")
    thumbs = "".join(
        f"<a href='layout_debug/{_esc(f)}'>"
        f"<img src='layout_debug/{_esc(f)}' alt='{_esc(f)}'></a>"
        for f in debug_images[:8])
    thumb_sec = (f"<h2>Layout debug frames</h2><div class='thumbs'>{thumbs}"
                 "</div><p class='muted'>Boxes drawn from the layout JSON — "
                 "if they don't sit on the hero portraits, the layout needs "
                 "calibration before detections can be trusted.</p>"
                 if thumbs else
                 "<p class='muted'>No layout debug images were produced for "
                 "this run.</p>")
    det_link = ("<a href='detections.json'>detections.json</a> · "
                if det.get("status") == "ok" else "")
    # Comp-promotion section: only shown when detection actually produced
    # readings. It NEVER writes anything — it shows the exact gated command and
    # the honesty rule, because a static report can't (and shouldn't) run it.
    promote_sec = ""
    if det.get("status") == "ok":
        run_id = _esc(record.get("run"))
        promote_sec = (
            "<h2>Comp promotion (gated — nothing written yet)</h2>"
            "<p class='muted'>Detections above are CV evidence only. No comp is "
            "written to the database unless it passes the promotion gate: all 5 "
            "slots per team above the match threshold AND consistent across "
            "consecutive frames, OR a manual review. Run the gate yourself "
            "(dry-run first):</p>"
            f"<pre>python pipeline/promote_detections.py --run {run_id}</pre>"
            "<p class='muted'>That classifies snapshots into high / needs-review "
            "and writes <code>review_queue.json</code> here, writing ZERO comps. "
            "To actually write the high-confidence comps, pair the run to a "
            "match and add <code>--write</code>:</p>"
            f"<pre>python pipeline/promote_detections.py --run {run_id} --write \\\n"
            "  --match &lt;match_id&gt; --map-order 1 "
            "--team-a &lt;team&gt; --team-b &lt;team&gt;</pre>"
            "<p class='muted'>Manual corrections in admin.html always override "
            "CV. FACEIT supplies match/map structure only — never the comp.</p>")
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<title>{_esc(record.get('run'))} — auto run report</title>"
            f"<style>{_REPORT_CSS}</style></head><body>"
            f"<h1>Auto run: {_esc(record.get('run'))} {pill}</h1>"
            f"{err_box}"
            f"<h2>Summary</h2><table>{summary}</table>"
            f"{att_sec}"
            f"<h2>Steps</h2><table><tr><th>#</th><th>step</th><th>status</th>"
            f"<th>detail</th><th>output</th></tr>{step_rows}</table>"
            f"{skip_sec}"
            f"{pre_sec}"
            f"{thumb_sec}"
            f"{promote_sec}"
            "<p class='muted'>Hero crop review/capture only — does not write "
            "comps.</p>"
            f"<p>{det_link}"
            "<a href='vision_dashboard.html' title='hero/anchor/score "
            "debugging for every frame in this run'>vision dashboard</a> · "
            "<a href='layout.html'>layout debug viewer</a> · "
            "<a href='crops.html'>hero crop report</a> · "
            "<a href='hero_crops.html'>hero crop review + label</a> · "
            "<a href='layout_debug/'>raw debug images</a> · "
            "<a href='../../../runs.html'>all runs</a> · "
            "<a href='../../../sources.html'>sources</a></p>"
            "</body></html>")


def write_report_index(report_dir: str, record: dict) -> None:
    """Best-effort report generation — never kills the run (blueprint P1)."""
    try:
        os.makedirs(report_dir, exist_ok=True)
        dbg_dir = os.path.join(report_dir, "layout_debug")
        debug_images = sorted(
            f for f in os.listdir(dbg_dir)
            if f.lower().endswith(".png")) if os.path.isdir(dbg_dir) else []
        html = build_report_html(record, debug_images)
        with open(os.path.join(report_dir, "index.html"), "w",
                  encoding="utf-8") as f:
            f.write(html)
    except Exception as e:  # report is best-effort
        log(f"report generation failed (non-fatal): {type(e).__name__}: {e}")


# ----------------------------------------------------------- orchestrate
def _layout_frame_height(layout_path: str) -> int | None:
    """Read frame_height from the layout JSON (best-effort, no cv2)."""
    try:
        with open(_abspath(layout_path), "r", encoding="utf-8") as f:
            v = json.load(f).get("frame_height")
        return int(v) if v else None
    except (OSError, ValueError, TypeError):
        return None


def run_auto(source=None, local=None, start=0, end=None, every=30,
             layout=None, height=None, force_clip=False, sources_path=None,
             fast=False, with_audio=False, stall_timeout=None,
             probe_fn=None, clip_fn=None, frame_fn=None, filter_fn=None,
             detect_fn=None, debug_fn=None, dashboard_fn=None, export_fn=None,
             status_fn=None, preflight_fn=None) -> dict:
    """Run the full pipeline. All step functions are injectable for tests."""
    sources_path = sources_path or vi.DEFAULT_SOURCES
    clip_fn = clip_fn or dvc.download_clip
    frame_fn = frame_fn or vi._extract_frame_local
    filter_fn = filter_fn or _step_filter
    detect_fn = detect_fn or _step_detect
    debug_fn = debug_fn or _step_debug
    dashboard_fn = dashboard_fn or _step_vision_dashboard
    export_fn = export_fn or _step_export
    status_fn = status_fn or append_run
    preflight_fn = preflight_fn or pf.run_checks

    start, end = vi.parse_time(start), vi.parse_time(end)
    if end <= start:
        raise SystemExit("--end must be after --start")
    if every <= 0:
        raise SystemExit("--every must be > 0")

    if fast:  # ultra-fast smoke mode: prove the pipeline, don't calibrate
        FAST_WINDOW, FAST_HEIGHT, FAST_EVERY = 30, 480, 10
        if end - start > FAST_WINDOW:
            end = start + FAST_WINDOW
            log(f"--fast: window capped to {FAST_WINDOW}s "
                f"({vi.fmt_hms(start)}-{vi.fmt_hms(end)})")
        if height is None:
            height = FAST_HEIGHT
            log(f"--fast: clip height {FAST_HEIGHT}p (detection will be "
                "skipped if the layout expects more — that's expected; "
                "this mode only proves capture works)")
        if every < FAST_EVERY:
            every = FAST_EVERY
            log(f"--fast: sampling every {FAST_EVERY}s")

    # Clip-download stall guard (seconds of NO real byte progress before the
    # download is killed). --fast is meant to finish or fail fast, so it uses
    # a short 75s guard; a normal calibration run gets a more patient 180s.
    if stall_timeout is None:
        stall_timeout = FAST_STALL_TIMEOUT if fast else DEFAULT_STALL_TIMEOUT
    log(f"clip stall guard: {int(stall_timeout)}s of no download progress "
        f"→ kill + fallback ({'fast' if fast else 'normal'} mode)")

    # ---- resolve input --------------------------------------------------
    if local:
        mode, name, url = "local", os.path.splitext(os.path.basename(local))[0], None
        layout_path = layout or DEFAULT_LAYOUT
        probe_fn = probe_fn or _probe_local
    elif source:
        src = vi.find_source(sources_path, source)
        if not src:
            raise SystemExit(f"no source id '{source}' in {sources_path}")
        if not vi.is_youtube_source(src):
            raise SystemExit(f"source '{source}' is not a youtube source")
        mode, name = "youtube", source
        url = src.get("url") or src.get("vodUrl")
        layout_path = layout or src.get("layout") or DEFAULT_LAYOUT
        probe_fn = probe_fn or vi.probe_vod
    else:
        raise SystemExit("provide --source or --local")

    if height is None:  # keep clip resolution consistent with the layout
        height = _layout_frame_height(layout_path) or 720
        if mode == "youtube":
            log(f"clip height defaulting to layout frame_height: {height}p")
    record_height = height

    run_name = f"{name}_{_tag(start)}_{_tag(end)}"
    raw_dir = os.path.join(db.REPO_ROOT, "work", "auto", run_name, "frames_raw")
    kept_dir = os.path.join(db.REPO_ROOT, "work", "auto", run_name, "frames")
    report_dir = os.path.join(db.REPO_ROOT, "reports", "auto", run_name)
    report_rel = f"reports/auto/{run_name}/"
    offsets = list(range(start, end, every))
    steps: list[dict] = []
    record: dict = {
        "run": run_name, "mode": mode, "source": name,
        "url": url, "localFile": local, "layout": layout_path,
        "window": f"{vi.fmt_hms(start)}-{vi.fmt_hms(end)}", "every": every,
        "startedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "reportDir": report_rel, "ok": False, "steps": steps,
        "height": record_height, "fast": bool(fast),
        "withAudio": bool(with_audio),
    }
    _STEP_NAMES = ["preflight", "probe", "clip", "frames", "filter",
                   "detect", "layout-debug", "vision-dashboard", "export"]
    n_steps = 9
    log(f"run: {run_name} · mode: {mode} · layout: {layout_path}")
    log(f"window: {record['window']} every {every}s -> "
        f"{len(offsets)} planned frame(s)")

    cur_step = _STEP_NAMES[0]
    try:
        # 1. preflight — catch setup problems BEFORE any download ----------
        cur_step = "preflight"
        banner(1, n_steps, "preflight — capture readiness")
        pres = preflight_fn(source=name if mode == "youtube" else None,
                            layout=layout_path, sources_path=sources_path,
                            fix_db=True,
                            need_youtube=(mode == "youtube"))
        record["preflight"] = pres
        for c in pres.get("checks", []):
            if c["status"] != "ok":
                log(f"  {c['status'].upper()}: {c['name']} — {c['detail']}")
        if not pres.get("ok", True):
            fails = "; ".join(
                f"{c['name']}: {c['detail']}"
                + (f" — fix: {c['remedy']}" if c.get("remedy") else "")
                for c in pres.get("checks", [])
                if c["status"] == "fail") or "unknown check failure"
            raise RuntimeError(f"preflight failed — {fails}")
        warned = pres.get("warned") or []
        log("  readiness OK"
            + (f" (warnings: {', '.join(warned)})" if warned else ""))
        _mark(steps, "preflight",
              "ok",
              detail="all checks passed" if not warned else
                     f"passed with warning(s): {', '.join(warned)}")

        # 2. probe --------------------------------------------------------
        cur_step = "probe"
        banner(2, n_steps, f"probe {mode} input")
        info = probe_fn(local if mode == "local" else url)
        log(f"  \"{info.get('title')}\""
            + (f" duration {vi.fmt_hms(info['duration'])}"
               if info.get("duration") else ""))
        _mark(steps, "probe", "ok",
              detail=f"\"{info.get('title')}\""
                     + (f" · {vi.fmt_hms(info['duration'])}"
                        if info.get("duration") else ""))

        # 3. clip (reused if already on disk) ------------------------------
        cur_step = "clip"
        if mode == "youtube":
            clip_path = dvc.default_out(name, start, end)
            banner(3, n_steps, f"clip -> {clip_path}")
            if fast:
                log("  --fast: trying muxed/progressive format first "
                    "(most reliable for section downloads; actual "
                    "resolution is reported)")
            cres = clip_fn(url, start, end, clip_path,
                           height=height, force=force_clip,
                           with_audio=with_audio,
                           stall_timeout=stall_timeout,
                           prefer_muxed=bool(fast))
            clip_path, clip_start = cres["path"], start
            record["clipReused"] = cres.get("reused", False)
            record["captureAttempts"] = cres.get("attempts") or []
            record["clipResolution"] = cres.get("resolution")
            record["clipSizeBytes"] = cres.get("sizeBytes")
            res_note = ""
            if record["clipResolution"]:
                r = record["clipResolution"]
                res_note = f" · actual {r['width']}x{r['height']}"
            clip_detail = ("reused cached clip" if record["clipReused"]
                           else "downloaded fresh") + res_note
            n_att = len(record["captureAttempts"])
            if n_att > 1:
                clip_detail += f" · {n_att} capture attempt(s)"
        else:
            banner(3, n_steps, f"clip: using local file {local} (no download)")
            clip_path, clip_start = _abspath(local), 0
            record["clipResolution"] = vi.probe_clip_resolution(clip_path)
            clip_detail = "local file (no download)"
            if record["clipResolution"]:
                r = record["clipResolution"]
                clip_detail += f" · {r['width']}x{r['height']}"
        record["clip"] = os.path.relpath(clip_path, db.REPO_ROOT) \
            if os.path.isabs(clip_path) else clip_path
        _mark(steps, "clip", "ok", detail=clip_detail, out=record["clip"])

        # 4. frames --------------------------------------------------------
        cur_step = "frames"
        banner(4, n_steps, f"extract {len(offsets)} frame(s) -> {raw_dir}")
        made = _extract_frames(clip_path, clip_start, offsets, raw_dir,
                               frame_fn=frame_fn)
        record["framesRaw"] = len(made)
        record["framesPlanned"] = len(offsets)
        if not made:
            raise RuntimeError("no frames extracted")
        _mark(steps, "frames", "ok",
              detail=f"{len(made)}/{len(offsets)} planned frame(s)",
              out=os.path.relpath(raw_dir, db.REPO_ROOT))

        # 5. filter ---------------------------------------------------------
        cur_step = "filter"
        banner(5, n_steps, "filter gameplay/replay/break frames")
        fres = filter_fn(raw_dir, kept_dir, layout_path)
        record["filtered"] = fres["filtered"]
        record["framesKept"] = fres["kept"]
        frames_for_next = fres["keptDir"]
        log(f"  kept {fres['kept']} frame(s)"
            + (f", rejected {fres['rejected']}" if fres["filtered"] else
               " (unfiltered)"))
        _mark(steps, "filter",
              "ok" if fres["filtered"] else "skipped",
              detail=(f"kept {fres['kept']}, rejected {fres['rejected']}"
                      if fres["filtered"] else
                      "filter not ready — raw frames used unfiltered"),
              out=os.path.relpath(frames_for_next, db.REPO_ROOT)
              if os.path.isabs(frames_for_next) else frames_for_next)

        # 6. detection (only if ready; JSON report, never the DB) ------------
        cur_step = "detect"
        banner(6, n_steps, "hero detection (if templates ready)")
        try:
            dres = detect_fn(frames_for_next, layout_path, report_dir)
        except Exception as e:  # detection is optional — never kills the run
            dres = {"status": "error",
                    "reason": f"{type(e).__name__}: {e} "
                              "(layout/templates likely not calibrated yet)"}
        record["detection"] = dres
        log(f"  detection: {dres.get('status')}"
            + (f" — {dres.get('reason')}" if dres.get("reason") else
               f" — {dres.get('framesRead', 0)} frame(s) read"))
        _mark(steps, "detect", dres.get("status", "error"),
              detail=dres.get("reason")
              or f"{dres.get('framesRead', 0)} frame(s) read",
              out=dres.get("out", ""))

        # 7. layout debug images ---------------------------------------------
        cur_step = "layout-debug"
        banner(7, n_steps, "layout debug images")
        dbg = debug_fn(frames_for_next, layout_path,
                       os.path.join(report_dir, "layout_debug"))
        record["debugImages"] = dbg.get("images", 0)
        record["layoutWarnings"] = dbg.get("layoutWarnings", 0)
        record["layoutScale"] = dbg.get("layoutScale")
        record["crops"] = dbg.get("crops", 0)
        record["cropsExpected"] = dbg.get("cropsExpected")
        record["cropSkipped"] = dbg.get("cropSkipped", [])
        record["evidenceError"] = dbg.get("evidenceError")
        detail = (f"{record['debugImages']} annotated frame(s), "
                  f"{dbg.get('crops', 0)}"
                  + (f"/{record['cropsExpected']}"
                     if record.get("cropsExpected") else "")
                  + " crop(s)")
        if dbg.get("layoutWarnings"):
            detail += f", {dbg['layoutWarnings']} layout warning(s)"
        if record["cropSkipped"]:
            detail += f", {len(record['cropSkipped'])} slot(s) skipped"
        if dbg.get("evidenceError"):
            detail += f" — evidence pages failed: {dbg['evidenceError']}"
        log(f"  wrote {record['debugImages']} annotated frame(s), "
            f"{dbg.get('crops', 0)} crop(s)"
            + (f", {dbg['layoutWarnings']} layout warning(s)"
               if dbg.get("layoutWarnings") else ""))
        _mark(steps, "layout-debug", "ok", detail=detail,
              out=f"{report_rel}layout.html")

        # 8. vision dashboard — the one page with hero/anchor/score debugging
        # over everything the run just produced. Best-effort: never fails
        # the run, since it only summarizes evidence already on disk.
        cur_step = "vision-dashboard"
        banner(8, n_steps, "vision debug dashboard")
        try:
            vres = dashboard_fn(run_name, layout_path)
            log(f"  wrote {vres.get('out')} — next: {vres.get('next')}")
            _mark(steps, "vision-dashboard", vres.get("status", "ok"),
                  detail=f"next: {vres.get('next')}" if vres.get("next")
                  else "generated", out=f"{report_rel}vision_dashboard.html")
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"
            log(f"  vision dashboard failed (non-fatal): {reason}")
            _mark(steps, "vision-dashboard", "error", detail=reason)

        # 9. export — complete the record and upsert FIRST, so the exported
        # data.js contains this run's final step table (the export step is
        # self-referential: it must be in the data it exports).
        cur_step = "export"
        record["ok"] = True                 # everything before export passed
        _mark(steps, "export", "ok", detail="site data regenerated",
              out="assets/js/data.js")
        record["runStatus"] = run_status_of(record)
        record["finishedAt"] = dt.datetime.now(dt.timezone.utc).isoformat()
        status_fn(record)
        banner(9, n_steps, "export site data")
        eres = export_fn()
        log(f"  wrote {eres.get('out')}")
    except Exception as e:
        record["ok"] = False
        record["error"] = f"{type(e).__name__}: {e}"
        if isinstance(e, vi.StallTimeout) or "no download progress" in str(e):
            record["timedOut"] = True   # -> run status "timeout", not "failed"
        cur = next((s for s in steps if s["name"] == cur_step), None)
        if cur:                             # e.g. export marked ok, then threw
            cur.update(status="failed", detail=record["error"])
        else:
            _mark(steps, cur_step, "failed", detail=record["error"])
        done = {s["name"] for s in steps}
        for name in _STEP_NAMES:            # remaining steps: explicit, not silent
            if name not in done:
                _mark(steps, name, "not-run",
                      detail=f"skipped — run failed at '{cur_step}'")
        log(f"FAILED at step '{cur_step}' — {record['error']}")
        log(f"next: {remedy_for(record['error'])}")
    finally:
        record["runStatus"] = run_status_of(record)
        record.setdefault(
            "finishedAt", dt.datetime.now(dt.timezone.utc).isoformat())
        write_report_index(_abspath(report_rel), record)
        status_fn(record)

    if record["ok"]:
        log(f"{record['runStatus'].upper()} — report: {report_rel}index.html")
        log("next: python pipeline/serve.py  ->  "
            "http://localhost:8000/runs.html")
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="One-command OWCS auto pipeline")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--source", help="youtube source id (video_sources.json)")
    g.add_argument("--local", help="a local .mp4 clip on disk")
    ap.add_argument("--start", default="0", help="seconds or H:MM:SS")
    ap.add_argument("--end", required=True, help="seconds or H:MM:SS")
    ap.add_argument("--every", type=int, default=30)
    ap.add_argument("--layout", help=f"layout json (default source layout "
                    f"or {DEFAULT_LAYOUT})")
    ap.add_argument("--height", type=int, default=None,
                    help="clip height; default: the layout's frame_height")
    ap.add_argument("--force-clip", "--force", action="store_true",
                    dest="force_clip",
                    help="delete any cached/partial clip and re-download")
    ap.add_argument("--fast", action="store_true",
                    help="ultra-fast smoke mode: <=30s window, 480p, "
                    "sparse frames — proves the pipeline quickly")
    ap.add_argument("--with-audio", action="store_true",
                    help="also download audio (slower; frame "
                    "extraction never needs it)")
    ap.add_argument("--stall-timeout", type=float, default=None,
                    dest="stall_timeout",
                    help="seconds with no clip-download progress before "
                    "yt-dlp is killed and a simpler format is tried "
                    f"(default {FAST_STALL_TIMEOUT}s in --fast, else "
                    f"{DEFAULT_STALL_TIMEOUT}s)")
    ap.add_argument("--sources", default=vi.DEFAULT_SOURCES)
    args = ap.parse_args(argv)

    record = run_auto(source=args.source, local=args.local, start=args.start,
                      end=args.end, every=args.every, layout=args.layout,
                      height=args.height, force_clip=args.force_clip,
                      fast=args.fast, with_audio=args.with_audio,
                      stall_timeout=args.stall_timeout,
                      sources_path=args.sources)
    return 0 if record.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
