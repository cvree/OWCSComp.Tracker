#!/usr/bin/env python3
"""test_run_owcs_auto.py — offline tests for the auto pipeline + clip tool.

No network, no yt-dlp, no ffmpeg: every external step is faked. Progress
streaming is tested with a real (tiny, local) python subprocess.
"""
from __future__ import annotations
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import video_ingest as vi          # noqa: E402
import download_vod_clip as dvc    # noqa: E402
import run_owcs_auto as roa        # noqa: E402

TMP = os.path.join(ROOT, "work", "test_auto")
_fails = 0


def check(name, cond):
    global _fails
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        _fails += 1


def fake_png(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x89PNG fake")


def make_steps(order, detect_status="skipped"):
    """Fake step functions that record their call order."""
    def preflight(**kw):
        order.append("preflight")
        return {"ok": True, "failed": [], "warned": [], "checks": []}

    def probe(x):
        order.append("probe")
        return {"title": "fake vod", "duration": 7200}

    def clip(url, start, end, out, height=720, force=False, **kw):
        clip.kwargs = dict(kw, height=height)
        order.append("clip")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        open(out, "wb").write(b"clip")
        return {"path": out, "reused": False, "sizeBytes": 4}

    def frame(clip_path, off, clip_start, out_path):
        order.append(f"frame@{off}")
        fake_png(out_path)
        return True

    def filt(raw, kept, layout):
        order.append("filter")
        return {"filtered": False, "keptDir": raw, "kept": 3, "rejected": 0}

    def detect(frames, layout, report_dir):
        order.append("detect")
        return {"status": detect_status, "reason": "no templates"}

    def debug(frames, layout, out):
        order.append("debug")
        return {"images": 3, "outDir": out}

    def dashboard(run_name, layout):
        order.append("dashboard")
        return {"status": "ok", "out": "vision_dashboard.html",
                "next": "nothing — every check passed"}

    def export():
        order.append("export")
        return {"out": "assets/js/data.js", "matches": 0}

    def status(rec):
        order.append("status")
        status.record = rec

    return dict(probe_fn=probe, clip_fn=clip, frame_fn=frame,
                filter_fn=filt, detect_fn=detect, debug_fn=debug,
                dashboard_fn=dashboard, export_fn=export, status_fn=status,
                preflight_fn=preflight), status


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP, exist_ok=True)
    sources = os.path.join(TMP, "video_sources.json")
    with open(sources, "w", encoding="utf-8") as f:
        json.dump({"sources": [{
            "id": "fake-src", "platform": "youtube",
            "url": "https://www.youtube.com/watch?v=FAKE",
            "layout": "layouts/owcs_youtube_2026.json", "enabled": True,
        }]}, f)

    print("orchestration order (youtube mode):")
    order = []
    steps, status = make_steps(order)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rec = roa.run_auto(source="fake-src", start="1:30:00", end="1:31:30",
                           every=30, sources_path=sources, **steps)
    got = [s for s in order if not s.startswith("frame@")]
    check("steps run in order preflight→probe→clip→filter→detect→debug→"
          "dashboard→export→status",
          got == ["preflight", "probe", "clip", "filter", "detect", "debug",
                  "dashboard", "status", "export", "status"])
    check("status upserted before export (run visible in exported data)",
          order.index("status") < order.index("export"))
    check("frames extracted between clip and filter",
          order.index("clip") < order.index("frame@5400") <
          order.index("filter"))
    check("planned 3 frames at absolute VOD offsets",
          [s for s in order if s.startswith("frame@")]
          == ["frame@5400", "frame@5430", "frame@5460"])
    check("run marked ok", rec["ok"] is True)
    check("status record has counts",
          status.record["framesRaw"] == 3 and status.record["framesKept"] == 3)

    print("progress logging:")
    out = buf.getvalue()
    check("numbered step banners", "[auto] [1/9] preflight" in out
          and "[auto] [2/9] probe" in out
          and "[auto] [8/9] vision debug dashboard" in out
          and "[auto] [9/9] export" in out)
    check("shows window + frame count",
          "1:30:00-1:31:30" in out and "3 planned frame(s)" in out)
    check("per-frame progress lines", "[1/3] frame @ 1:30:00" in out)
    check("prints report + server next-step", "pipeline/serve.py" in out)
    check("report index.html written",
          os.path.exists(os.path.join(
              ROOT, "reports", "auto",
              "fake-src_013000_013130", "index.html")))

    print("local MP4 mode:")
    local = os.path.join(TMP, "day1.mp4")
    open(local, "wb").write(b"mp4")
    order2 = []
    steps2, status2 = make_steps(order2)
    with contextlib.redirect_stdout(io.StringIO()):
        rec2 = roa.run_auto(local=local, start="0", end="1:30", every=30,
                            sources_path=sources, **steps2)
    check("local mode ok, no clip download",
          rec2["ok"] and "clip" not in order2)
    check("local offsets relative to file",
          [s for s in order2 if s.startswith("frame@")]
          == ["frame@0", "frame@30", "frame@60"])
    check("mode recorded as local", rec2["mode"] == "local"
          and rec2["localFile"] == local)

    print("failure path:")
    order4 = []
    steps4, status4 = make_steps(order4)
    def det_boom(*a, **k):
        order4.append("detect")
        raise RuntimeError("cv2 crash on placeholder layout")
    steps4["detect_fn"] = det_boom
    with contextlib.redirect_stdout(io.StringIO()):
        rec4 = roa.run_auto(local=local, start=0, end=60, every=30,
                            sources_path=sources, **steps4)
    check("detection crash is non-fatal (run continues to export)",
          rec4["ok"] is True and rec4["detection"]["status"] == "error"
          and "export" in order4)

    order3 = []
    steps3, status3 = make_steps(order3)
    def boom(*a, **k):
        order3.append("filter")
        raise RuntimeError("synthetic filter crash")
    steps3["filter_fn"] = boom
    with contextlib.redirect_stdout(io.StringIO()):
        rec3 = roa.run_auto(local=local, start=0, end=60, every=30,
                            sources_path=sources, **steps3)
    check("failure recorded, later steps skipped, status still written",
          rec3["ok"] is False and "synthetic filter crash" in rec3["error"]
          and "export" not in order3 and "status" in order3)

    print("per-step status records (Phase 1):")
    names = [s["name"] for s in rec["steps"]]
    check("success run records all 9 steps",
          names == ["preflight", "probe", "clip", "frames", "filter",
                    "detect", "layout-debug", "vision-dashboard", "export"])
    check("step records carry name/status/detail/out keys",
          all(set(s) >= {"name", "status", "detail", "out"}
              for s in rec["steps"]))
    check("detect step surfaces skipped + reason",
          next(s for s in rec["steps"] if s["name"] == "detect")["status"]
          == "skipped")
    vstep = next(s for s in rec["steps"] if s["name"] == "vision-dashboard")
    check("vision-dashboard step ran ok and names the next action",
          vstep["status"] == "ok" and "next:" in vstep["detail"]
          and vstep["out"].endswith("vision_dashboard.html"))
    check("unfiltered filter step shows skipped, not silent ok",
          next(s for s in rec["steps"] if s["name"] == "filter")["status"]
          == "skipped")
    check("runStatus label is partial (detection skipped)",
          rec["runStatus"] == "partial")
    by3 = {s["name"]: s for s in rec3["steps"]}
    check("failed run names the failing step",
          by3["filter"]["status"] == "failed"
          and "synthetic filter crash" in by3["filter"]["detail"])
    check("steps after failure are explicit not-run",
          by3["detect"]["status"] == "not-run"
          and by3["export"]["status"] == "not-run"
          and "filter" in by3["export"]["detail"])
    check("failed runStatus label", rec3["runStatus"] == "failed")
    check("steps survive the auto_runs upsert round-trip",
          isinstance(status3.record.get("steps"), list)
          and len(status3.record["steps"]) == 9)

    print("report generation (success + failure):")
    rep_ok = os.path.join(ROOT, "reports", "auto",
                          "fake-src_013000_013130", "index.html")
    html_ok = open(rep_ok, encoding="utf-8").read()
    check("success report has step table + status pill",
          "PARTIAL" in html_ok and ">probe<" in html_ok
          and ">layout-debug<" in html_ok)
    check("success report links runs.html + layout_debug",
          "runs.html" in html_ok and "layout_debug/" in html_ok)
    rep_fail = os.path.join(ROOT, "reports", "auto",
                            rec3["run"], "index.html")
    check("failure ALSO produces a report page", os.path.exists(rep_fail))
    html_fail = open(rep_fail, encoding="utf-8").read()
    check("failure report names failing step + remedy line",
          "FAILED" in html_fail and "synthetic filter crash" in html_fail
          and "Next:" in html_fail and "not-run" in html_fail)
    check("remedy mapping: yt-dlp / ffmpeg / default",
          "pip install yt-dlp" in roa.remedy_for("[Errno 2] 'yt-dlp'")
          and "ffmpeg.org" in roa.remedy_for("ffmpeg: not found")
          and "step table" in roa.remedy_for("weird unknown thing"))
    check("report generation is non-fatal on bad input",
          roa.write_report_index("/nonexistent\x00dir", rec) is None)
    check("html escaping in report",
          "&lt;b&gt;" in roa.build_report_html(
              {"run": "<b>", "steps": [], "ok": True}, []))

    print("detection preflight (explained skips, not cv2 crashes):")
    import numpy as np
    import cv2
    pf_dir = os.path.join(TMP, "pf_frames")
    os.makedirs(pf_dir, exist_ok=True)
    cv2.imwrite(os.path.join(pf_dir, "000000.png"),
                np.zeros((720, 1280, 3), dtype=np.uint8))
    # 1280x720 frame + 1920x1080 layout: SAME aspect -> auto-scales, no skip.
    lay1080 = {"frame_width": 1920, "frame_height": 1080,
               "slots_a": [[360, 22, 58, 58]], "slots_b": [[1502, 22, 58, 58]]}
    check("same-aspect resolution mismatch auto-scales (no skip)",
          roa.detect_preflight(pf_dir, lay1080) is None)
    # aspect mismatch cannot scale -> clear skip with remedy.
    lay_aspect = {"frame_width": 1440, "frame_height": 1080,
                  "slots_a": [[360, 22, 58, 58]], "slots_b": [[900, 22, 58, 58]]}
    r = roa.detect_preflight(pf_dir, lay_aspect)
    check("aspect-mismatch is a clear skip with remedy",
          r is not None and "1280x720" in r and "aspect" in r)
    # box outside AFTER scaling -> clear skip.
    lay_bad = {"frame_width": 1280, "frame_height": 720,
               "slots_a": [[1250, 22, 58, 58]], "slots_b": []}
    r2 = roa.detect_preflight(pf_dir, lay_bad)
    check("out-of-bounds layout box is a clear skip",
          r2 is not None and "outside" in r2 and "calibration" in r2)
    lay_ok = {"frame_width": 1280, "frame_height": 720,
              "slots_a": [[360, 22, 58, 58]], "slots_b": [[900, 22, 58, 58]]}
    check("matching layout passes preflight",
          roa.detect_preflight(pf_dir, lay_ok) is None)
    check("empty frames dir is a clear skip",
          "no frames" in (roa.detect_preflight(
              os.path.join(TMP, "nope"), lay_ok) or ""))

    print("clip height defaults to layout frame_height:")
    lay_path = os.path.join(TMP, "lay1080.json")
    with open(lay_path, "w", encoding="utf-8") as f:
        json.dump({"frame_height": 1080}, f)
    check("_layout_frame_height reads the layout",
          roa._layout_frame_height(lay_path) == 1080)
    check("missing/broken layout falls back to None",
          roa._layout_frame_height(os.path.join(TMP, "missing.json")) is None)
    heights = []
    stepsH, statusH = make_steps([])
    def clip_h(url, start, end, out, height=720, force=False, **kw):
        heights.append(height)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        open(out, "wb").write(b"clip")
        return {"path": out, "reused": False}
    stepsH["clip_fn"] = clip_h
    with open(sources, "w", encoding="utf-8") as f:
        json.dump({"sources": [{
            "id": "fake-src", "platform": "youtube",
            "url": "https://www.youtube.com/watch?v=FAKE",
            "layout": lay_path, "enabled": True}]}, f)
    with contextlib.redirect_stdout(io.StringIO()):
        recH = roa.run_auto(source="fake-src", start=0, end=60, every=30,
                            sources_path=sources, **stepsH)
    check("youtube clip uses layout height when --height omitted",
          heights == [1080] and recH["height"] == 1080)
    heightsX = []
    stepsX, _ = make_steps([])
    stepsX["clip_fn"] = lambda url, s2, e2, out, height=720, force=False, **kw: (
        heightsX.append(height),
        os.makedirs(os.path.dirname(out), exist_ok=True),
        open(out, "wb").write(b"c"),
        {"path": out, "reused": False})[-1]
    with contextlib.redirect_stdout(io.StringIO()):
        roa.run_auto(source="fake-src", start=0, end=60, every=30,
                     height=480, sources_path=sources, **stepsX)
    check("explicit --height still wins", heightsX == [480])

    print("exported data.js is never one status behind (self-reference fix):")
    snaps = []
    stepsS, _ = make_steps([])
    real_status = stepsS["status_fn"]
    def snap_status(rec):
        snaps.append(json.loads(json.dumps(rec)))
        real_status(rec)
    stepsS["status_fn"] = snap_status
    with contextlib.redirect_stdout(io.StringIO()):
        roa.run_auto(local=local, start=0, end=60, every=30,
                     sources_path=sources, **stepsS)
    pre_export = snaps[0]   # the upsert that export_fn will read
    check("pre-export upsert already has all 9 steps incl. export:ok",
          len(pre_export["steps"]) == 9
          and pre_export["steps"][-1]["name"] == "export"
          and pre_export["steps"][-1]["status"] == "ok")
    check("pre-export upsert has final runStatus + finishedAt",
          pre_export.get("runStatus") in ("ok", "partial")
          and pre_export.get("finishedAt"))

    print("export failure downgrades honestly:")
    stepsE, statusE = make_steps([])
    def export_boom():
        raise RuntimeError("disk full during export")
    stepsE["export_fn"] = export_boom
    with contextlib.redirect_stdout(io.StringIO()):
        recE = roa.run_auto(local=local, start=0, end=60, every=30,
                            sources_path=sources, **stepsE)
    byE = {s["name"]: s for s in recE["steps"]}
    check("export step flips ok -> failed (no duplicate row)",
          recE["ok"] is False and recE["runStatus"] == "failed"
          and byE["export"]["status"] == "failed"
          and len(recE["steps"]) == 9
          and "disk full" in byE["export"]["detail"])

    print("video-only download by default (--with-audio opts in):")
    check("format: video-only has no audio stream, no merge",
          "+ba" not in vi.clip_format(720)
          and "bestvideo[height<=720]" in vi.clip_format(720))
    check("format: with_audio adds +ba merge",
          "+ba" in vi.clip_format(720, with_audio=True))

    class CmdRunner:  # fake without Popen: captures cmd, creates -o file
        def __init__(self): self.cmds = []
        def run(self, cmd, check=True, capture_output=True, text=True):
            self.cmds.append(cmd)
            out = cmd[cmd.index("-o") + 1]
            os.makedirs(os.path.dirname(out), exist_ok=True)
            open(out, "wb").write(b"clip")
            import types
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    cr = CmdRunner()
    vo_out = os.path.join(TMP, "clips", "vo.mp4")
    with contextlib.redirect_stdout(io.StringIO()):
        vi._download_youtube_clip("https://x", 0, 30, vo_out, 720, runner=cr)
    fmt = cr.cmds[0][cr.cmds[0].index("-f") + 1]
    check("yt-dlp invoked WITHOUT audio by default", "+ba" not in fmt)
    cr2 = CmdRunner()
    with contextlib.redirect_stdout(io.StringIO()):
        vi._download_youtube_clip("https://x", 0, 30, vo_out, 720, runner=cr2,
                                  with_audio=True)
    fmt2 = cr2.cmds[0][cr2.cmds[0].index("-f") + 1]
    check("with_audio=True passes +ba to yt-dlp", "+ba" in fmt2)
    stepsA, statusA = make_steps([])
    with contextlib.redirect_stdout(io.StringIO()):
        roa.run_auto(source="fake-src", start=0, end=60, every=30,
                     sources_path=sources, **stepsA)
    check("run_auto passes with_audio=False by default",
          stepsA["clip_fn"].kwargs.get("with_audio") is False)
    stepsB, _ = make_steps([])
    with contextlib.redirect_stdout(io.StringIO()):
        roa.run_auto(source="fake-src", start=0, end=60, every=30,
                     with_audio=True, sources_path=sources, **stepsB)
    check("run_auto --with-audio opts into audio",
          stepsB["clip_fn"].kwargs.get("with_audio") is True)

    print("yt-dlp ext-append rename fix (clip.mp4.webm -> clip.mp4):")
    class ExtRunner(CmdRunner):
        def run(self, cmd, check=True, capture_output=True, text=True):
            self.cmds.append(cmd)
            out = cmd[cmd.index("-o") + 1]
            os.makedirs(os.path.dirname(out), exist_ok=True)
            open(out + ".webm", "wb").write(b"clip")   # ext appended
            import types
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    er = ExtRunner()
    ext_out = os.path.join(TMP, "clips", "ext.mp4")
    with contextlib.redirect_stdout(io.StringIO()) as bext:
        vi._download_youtube_clip("https://x", 0, 30, ext_out, 720, runner=er)
    check("appended-ext file renamed to the expected path",
          os.path.exists(ext_out) and not os.path.exists(ext_out + ".webm")
          and "renamed" in bext.getvalue())

    print("--fast smoke mode:")
    orderF = []
    stepsF, statusF = make_steps(orderF)
    with contextlib.redirect_stdout(io.StringIO()) as bf:
        recF = roa.run_auto(source="fake-src", start="1:30:00", end="1:32:00",
                            every=5, fast=True, sources_path=sources,
                            **stepsF)
    framesF = [s for s in orderF if s.startswith("frame@")]
    check("--fast caps window to 30s",
          recF["window"] == "1:30:00-1:30:30"
          and "window capped to 30s" in bf.getvalue())
    check("--fast lowers height to 480 when not explicit",
          stepsF["clip_fn"].kwargs.get("height") == 480
          and recF["height"] == 480)
    check("--fast raises too-dense sampling to every 10s",
          framesF == ["frame@5400", "frame@5410", "frame@5420"])
    check("--fast recorded on the run", recF["fast"] is True)
    stepsG, _ = make_steps([])
    with contextlib.redirect_stdout(io.StringIO()):
        recG = roa.run_auto(source="fake-src", start=0, end=20, every=10,
                            fast=True, height=720, sources_path=sources,
                            **stepsG)
    check("--fast keeps an explicit --height",
          stepsG["clip_fn"].kwargs.get("height") == 720)
    check("--fast never grows a small window", recG["window"] == "0:00:00-0:00:20")

    print("heartbeat when a download emits nothing:")
    bh = io.StringIO()
    with contextlib.redirect_stdout(bh):
        vi._run_live([sys.executable, "-c",
                      "import time; print('start', flush=True); "
                      "time.sleep(0.6); print('done', flush=True)"],
                     "[yt-dlp]", heartbeat_every=0.15,
                     idle_msg="still downloading")
    outh = bh.getvalue()
    check("heartbeat printed during silence",
          "still downloading... elapsed" in outh
          and "no output for" in outh)
    check("real lines still streamed around the heartbeat",
          "[yt-dlp] start" in outh and "[yt-dlp] done" in outh)
    bh2 = io.StringIO()
    with contextlib.redirect_stdout(bh2):
        vi._run_live([sys.executable, "-c", "print('quick', flush=True)"],
                     "[x]", heartbeat_every=30)
    check("no heartbeat spam on fast commands",
          "elapsed" not in bh2.getvalue())

    print("clip cache states are announced:")
    cache_out = os.path.join(TMP, "clips", "cache.mp4")
    os.makedirs(os.path.dirname(cache_out), exist_ok=True)
    # size-tolerant validator so these cache-state assertions don't depend on
    # a real ffprobe or on the 4KB byte-floor (cache-safety has its own test).
    ok_validate = lambda p, **k: (os.path.exists(p) and os.path.getsize(p) > 0,
                                  "ok")
    def dl_touch(url, s2, e2, out, height, **kw):
        open(out, "wb").write(b"newclip-bytes")
    open(cache_out, "wb").write(b"old-but-nonempty")
    with contextlib.redirect_stdout(io.StringIO()) as bc1:
        r_reu = dvc.download_clip("https://x", 0, 30, cache_out,
                                  download_fn=dl_touch, validate_fn=ok_validate)
    check("complete cached clip announces REUSING",
          r_reu["reused"] and "REUSING" in bc1.getvalue())
    os.remove(cache_out)
    open(cache_out + ".part", "wb").write(b"half")
    with contextlib.redirect_stdout(io.StringIO()) as bc2:
        dvc.download_clip("https://x", 0, 30, cache_out, download_fn=dl_touch,
                          validate_fn=ok_validate)
    check("partial file announces RESUME", "RESUME" in bc2.getvalue())
    open(cache_out + ".part", "wb").write(b"half")
    with contextlib.redirect_stdout(io.StringIO()) as bc3:
        r_f = dvc.download_clip("https://x", 0, 30, cache_out, force=True,
                                download_fn=dl_touch, validate_fn=ok_validate)
    check("--force announces DELETING and re-downloads",
          "DELETING" in bc3.getvalue() and r_f["reused"] is False
          and not os.path.exists(cache_out + ".part"))

    # invalid (tiny/corrupt) cache is auto-deleted + re-downloaded, not reused
    open(cache_out, "wb").write(b"\x00" * 8)   # 8-byte stub
    reject_small = lambda p, **k: (os.path.getsize(p) >= 64, "too small")
    with contextlib.redirect_stdout(io.StringIO()) as bc4:
        def dl_big(url, s2, e2, out, height, **kw):
            open(out, "wb").write(b"\x00" * 128)
        r_bad = dvc.download_clip("https://x", 0, 30, cache_out,
                                  download_fn=dl_big, validate_fn=reject_small)
    check("invalid cache announces invalid/corrupt + re-downloads",
          "invalid/corrupt" in bc4.getvalue() and r_bad["reused"] is False)

    print("reusable clip command:")
    clip_out = os.path.join(TMP, "clips", "reuse.mp4")
    calls = []
    def fake_dl(url, start, end, out, height, **kw):
        calls.append(out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        open(out, "wb").write(b"clipdata")
    okv = lambda p, **k: (os.path.exists(p) and os.path.getsize(p) > 0, "ok")
    with contextlib.redirect_stdout(io.StringIO()):
        r1 = dvc.download_clip("https://x", 0, 60, clip_out,
                               download_fn=fake_dl, validate_fn=okv)
        n1 = len(calls)
        r2 = dvc.download_clip("https://x", 0, 60, clip_out,
                               download_fn=fake_dl, validate_fn=okv)
        n2 = len(calls)
        r3 = dvc.download_clip("https://x", 0, 60, clip_out, force=True,
                               download_fn=fake_dl, validate_fn=okv)
        n3 = len(calls)
    check("first call downloads", n1 == 1 and r1["reused"] is False)
    check("second call reuses existing clip (no download)",
          n2 == 1 and r2["reused"] is True)
    check("--force re-downloads", n3 == 2 and r3["reused"] is False)

    print("clip CLI resolves saved source:")
    with contextlib.redirect_stdout(io.StringIO()) as b2:
        code = None
        try:
            dvc.main(["--source", "nope", "--start", "0", "--end", "60",
                      "--sources", sources])
        except SystemExit as e:
            code = str(e)
    check("unknown source id gives clear error", "no source id" in (code or ""))

    print("live progress streaming (_run_live):")
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        vi._run_live([sys.executable, "-c",
                      "print('line one'); print('50.0% of ~10MB')"],
                     "[yt-dlp]")
    out2 = buf2.getvalue()
    check("subprocess output streamed with prefix",
          "[yt-dlp] line one" in out2 and "[yt-dlp] 50.0%" in out2)
    err = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            vi._run_live([sys.executable, "-c",
                          "print('bad thing'); raise SystemExit(3)"], "[x]")
    except subprocess.CalledProcessError as e:
        err = e
    check("failure raises with output tail attached",
          err is not None and "bad thing" in (err.output or ""))

    class OnlyRun:  # fake runner without Popen -> old captured path
        def __init__(self): self.cmds = []
        def run(self, cmd, check=True, capture_output=True, text=True):
            self.cmds.append(cmd)
            import types
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    fr = OnlyRun()
    vi._run_live(["whatever"], "[x]", runner=fr)
    check("fake runners without Popen still use .run()", fr.cmds == [["whatever"]])

    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{'ALL PASS' if _fails == 0 else str(_fails) + ' FAILURE(S)'}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
