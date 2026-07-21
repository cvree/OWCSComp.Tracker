#!/usr/bin/env python3
"""
test_capture_reliability.py — offline tests for this session's capture
robustness work. No network, no real yt-dlp; ffmpeg/ffprobe are NOT invoked
(fake runners throughout).

Covers:
  * preflight checks: DB missing-tables detection + auto-init, unknown
    source, missing/invalid layout, yt-dlp escalation for youtube sources
  * JS-runtime detection (node must be opted in via --js-runtimes)
  * capture attempts recorded per strategy (ok / stalled / error)
  * CalledProcessError also walks the format ladder (not just stalls)
  * direct-URL + ffmpeg fallback runs after the ladder is exhausted, and
    its failure never masks the original error
  * corrupt fresh download is deleted (never poisons the next run's cache)
  * probe_clip_resolution parses ffprobe JSON (and degrades to None)
  * run record carries captureAttempts / clipResolution / crop counts and
    the report HTML renders attempts, resolution, skipped slots, preflight
  * run_status_of: evidence failure -> partial, never failed
  * run.html has the readiness panel + capture buttons; runs.html links
    report/layout/crops

Run:  python pipeline/test_capture_reliability.py   (non-zero on failure)
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix="owcs_reliab_")
os.environ["OWCS_DB"] = os.path.join(TMP, "test.sqlite")

import video_ingest as vi        # noqa: E402
import download_vod_clip as dvc  # noqa: E402
import run_owcs_auto as roa      # noqa: E402
import preflight as pf           # noqa: E402
import db                        # noqa: E402

_fails = 0


def check(name, cond):
    global _fails
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        _fails += 1


def main():
    # ---- 1. JS runtime detection ----------------------------------------
    print("JS runtime detection")
    which_none = lambda n: None
    which_node = lambda n: r"C:\nodejs\node.exe" if n == "node" else None
    which_deno = lambda n: "/usr/bin/deno" if n == "deno" else None
    check("no runtime -> no extra args",
          vi.js_runtime_args(which_none) == [])
    check("node -> opted in via --js-runtimes node",
          vi.js_runtime_args(which_node) == ["--js-runtimes", "node"])
    check("deno -> default-enabled, no args",
          vi.js_runtime_args(which_deno) == [])
    check("detect names the runtime",
          vi.detect_js_runtime(which_node)[0] == "node"
          and vi.detect_js_runtime(which_none)[0] is None)

    # ---- 1b. --fast prefers muxed/progressive formats --------------------
    print("format ladder: muxed-first for smoke runs")
    normal = vi.clip_format_ladder(480)
    muxed = vi.clip_format_ladder(480, prefer_muxed=True)
    check("normal ladder starts with bestvideo (quality first)",
          normal[0].startswith("bestvideo"))
    check("prefer_muxed puts the progressive selector first",
          muxed[0] == "best[height<=480]"
          and muxed[1].startswith("bestvideo"))
    check("worst stays the last resort in both",
          normal[-1] == "worst" and muxed[-1] == "worst")

    # ---- 2. preflight: DB missing tables + auto-init ---------------------
    print("preflight: database")
    db_path = os.path.join(TMP, "fresh.sqlite")
    missing = pf.db_tables_missing(db_path)
    check("missing DB reports all required tables missing",
          set(missing) == set(pf.REQUIRED_TABLES))
    c = pf.check_database(db_path, fix=False)
    check("no-fix check is a WARN with the exact init command",
          c["status"] == "warn" and "init_db.py" in c["remedy"])
    c2 = pf.check_database(db_path, fix=True)
    check("fix=True auto-initializes schema + reference data",
          c2["status"] == "ok" and pf.db_tables_missing(db_path) == [])
    con = db.connect(db_path)
    n_heroes = con.execute("SELECT COUNT(*) c FROM heroes").fetchone()["c"]
    con.close()
    check("auto-init seeded the heroes table (no late "
          "'no such table: heroes')", n_heroes > 0)
    check("'no such table' has a mapped remedy",
          "init_db.py" in roa.remedy_for("no such table: heroes"))

    # ---- 3. preflight: source + layout + escalation ----------------------
    print("preflight: source / layout")
    src_file = os.path.join(TMP, "sources.json")
    lay_file = os.path.join(TMP, "lay.json")
    with open(lay_file, "w", encoding="utf-8") as f:
        json.dump({"frame_width": 1920, "frame_height": 1080,
                   "slots_a": [[0, 0, 5, 5]] * 5,
                   "slots_b": [[10, 0, 5, 5]] * 5}, f)
    with open(src_file, "w", encoding="utf-8") as f:
        json.dump({"sources": [{"id": "s1", "platform": "youtube",
                                "url": "https://youtube.com/watch?v=X",
                                "layout": lay_file, "enabled": True}]}, f)
    check("known source passes",
          pf.check_source("s1", src_file)["status"] == "ok")
    check("unknown source FAILS with remedy",
          pf.check_source("nope", src_file)["status"] == "fail")
    check("valid layout passes",
          pf.check_layout(lay_file)["status"] == "ok")
    check("missing layout FAILS",
          pf.check_layout(os.path.join(TMP, "no.json"))["status"] == "fail")
    bad_lay = os.path.join(TMP, "bad.json")
    with open(bad_lay, "w", encoding="utf-8") as f:
        f.write("{not json")
    check("broken layout JSON FAILS with syntax detail",
          pf.check_layout(bad_lay)["status"] == "fail")
    res = pf.run_checks(source="s1", sources_path=src_file, fix_db=True,
                        db_path=os.path.join(TMP, "fresh2.sqlite"))
    names = [c["name"] for c in res["checks"]]
    check("run_checks covers env + db + source + layout + writable",
          {"python", "ffmpeg", "ffprobe", "yt-dlp", "js-runtime",
           "database", "source", "layout", "writable"} <= set(names))

    # ---- 4. capture attempts + ladder on errors --------------------------
    print("capture attempts: ladder walks on errors too")
    out = os.path.join(TMP, "clips", "att.mp4")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    class ErrRunner:
        """First rung exits non-zero, second succeeds + writes the clip."""
        def __init__(self):
            self.n = 0

        def run(self, cmd, check=True, capture_output=True, text=True,
                timeout=None):
            self.n += 1
            if self.n == 1:
                raise subprocess.CalledProcessError(
                    1, cmd, output="ERROR: Requested format is not available")
            with open(out, "wb") as f:
                f.write(b"\x00" * 5000)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    er = ErrRunner()
    dres = vi._download_youtube_clip("u", 0, 20, out, height=480, runner=er,
                                     formats=["fmtA", "fmtB"])
    atts = dres["attempts"]
    check("non-stall error walks the ladder to the next format", er.n == 2)
    check("both attempts recorded with outcomes",
          len(atts) == 2 and atts[0]["outcome"] == "error"
          and atts[1]["outcome"] == "ok")
    check("attempt records carry strategy + format",
          atts[0]["strategy"] == "yt-dlp section"
          and atts[0]["format"] == "fmtA")

    # ---- 5. direct-URL fallback after ladder exhaustion ------------------
    print("direct-url + ffmpeg last resort")
    out2 = os.path.join(TMP, "clips", "direct.mp4")

    class DirectRunner:
        """yt-dlp section always errors; -g returns a URL; ffmpeg writes."""
        def __init__(self):
            self.cmds = []

        def run(self, cmd, check=True, capture_output=True, text=True,
                timeout=None):
            self.cmds.append(list(cmd))
            if "-g" in cmd:
                return types.SimpleNamespace(
                    returncode=0, stdout="https://rr1.example/video\n",
                    stderr="")
            if cmd[0] == "ffmpeg":
                with open(out2, "wb") as f:
                    f.write(b"\x00" * 5000)
                return types.SimpleNamespace(returncode=0, stdout="",
                                             stderr="")
            raise subprocess.CalledProcessError(1, cmd,
                                                output="format unavailable")

    dr = DirectRunner()
    dres2 = vi._download_youtube_clip("u", 5, 25, out2, height=480,
                                      runner=dr, formats=["fmtA"])
    atts2 = dres2["attempts"]
    check("direct-url fallback ran and succeeded",
          os.path.exists(out2)
          and atts2[-1]["strategy"] == "direct-url + ffmpeg"
          and atts2[-1]["outcome"] == "ok")
    check("every strategy is in the attempt list",
          [a["strategy"] for a in atts2]
          == ["yt-dlp section", "direct-url + ffmpeg"])
    ff = next(c for c in dr.cmds if c[0] == "ffmpeg")
    check("ffmpeg cut uses -ss/-t against the direct URL",
          "-ss" in ff and "https://rr1.example/video" in ff)

    # failure of the direct path never masks the original error
    class AllFailRunner:
        def run(self, cmd, check=True, capture_output=True, text=True,
                timeout=None):
            raise subprocess.CalledProcessError(1, cmd, output="nope")

    got = None
    try:
        vi._download_youtube_clip("u", 0, 20,
                                  os.path.join(TMP, "clips", "nope.mp4"),
                                  height=480, runner=AllFailRunner(),
                                  formats=["fmtA"])
    except subprocess.CalledProcessError as e:
        got = e
    check("all-fail raises the ORIGINAL yt-dlp error (not a direct-url "
          "TypeError)", got is not None)
    check("original error carries the attempt history",
          len(getattr(got, "attempts", [])) == 2
          and getattr(got, "attempts")[-1]["outcome"] == "error")

    # ---- 6. corrupt fresh download is deleted ----------------------------
    print("corrupt fresh download deleted")
    bad_out = os.path.join(TMP, "clips", "bad.mp4")

    def corrupt_dl(url, s, e, o, h, **kw):
        with open(o, "wb") as f:
            f.write(b"\x00" * 8)
        return {"attempts": [{"strategy": "yt-dlp section", "format": "f",
                              "outcome": "ok"}]}

    raised = None
    try:
        dvc.download_clip("u", 0, 30, bad_out, download_fn=corrupt_dl,
                          validate_fn=lambda p, **k: (False, "too small"))
    except vi.InvalidClip as e:
        raised = str(e)
    check("InvalidClip raised with clear message",
          raised is not None and "invalid/corrupt" in raised)
    check("the corrupt file was deleted (cache not poisoned)",
          not os.path.exists(bad_out))

    # ---- 7. probe_clip_resolution ----------------------------------------
    print("resolution probe")

    class ProbeRunner:
        def run(self, cmd, check=True, capture_output=True, text=True):
            return types.SimpleNamespace(returncode=0, stdout=json.dumps({
                "streams": [{"width": 640, "height": 360,
                             "codec_name": "h264"}],
                "format": {"duration": "30.03"}}), stderr="")

    r = vi.probe_clip_resolution("clip.mp4", runner=ProbeRunner())
    check("parses ffprobe JSON to WxH + codec + duration",
          r == {"width": 640, "height": 360, "codec": "h264",
                "duration": 30.0})

    class NoProbe:
        def run(self, cmd, **kw):
            raise FileNotFoundError("ffprobe")

    check("missing ffprobe degrades to None (never crashes a run)",
          vi.probe_clip_resolution("clip.mp4", runner=NoProbe()) is None)

    # ---- 8. record + report carry the capture story ----------------------
    print("run record + report")
    rec = {
        "run": "r1", "mode": "youtube", "source": "s1", "ok": True,
        "height": 480, "window": "0:06:00-0:06:30", "every": 10,
        "clip": "work/clips/x.mp4", "clipReused": False,
        "clipResolution": {"width": 640, "height": 360, "codec": "h264",
                           "duration": 30.0},
        "framesPlanned": 3, "framesRaw": 3, "framesKept": 3,
        "layoutScale": "layout scaled from 1920x1080 to 640x360 "
                       "(factor 0.3333)",
        "crops": 28, "cropsExpected": 30,
        "cropSkipped": ["000360.png b5: box [1592,8,76,72] outside the "
                        "640x360 frame"],
        "captureAttempts": [
            {"strategy": "yt-dlp section", "format": "fmtA",
             "outcome": "stalled", "seconds": 75.0, "note": "killed"},
            {"strategy": "direct-url + ffmpeg", "format": "best (direct)",
             "outcome": "ok", "seconds": 9.1, "note": "stream copy"}],
        "preflight": {"ok": True, "warned": ["js-runtime"], "checks": [
            {"name": "ffmpeg", "status": "ok", "detail": "ffmpeg 8",
             "remedy": ""},
            {"name": "js-runtime", "status": "warn",
             "detail": "no Deno/Node", "remedy": "install Node.js"}]},
        "detection": {"status": "skipped", "reason": "no templates"},
        "steps": [],
    }
    html = roa.build_report_html(rec, [])
    check("report shows requested vs actual resolution",
          "requested &lt;=480p" in html.replace("<=480p", "&lt;=480p")
          or "requested <=480p" in html)
    check("report shows actual 640x360", "640x360" in html)
    check("report lists every capture attempt",
          "Capture attempts" in html and "stalled" in html
          and "direct-url + ffmpeg" in html)
    check("report shows crop expected vs actual", "28 of 30 expected" in html)
    check("report lists skipped slots with reasons",
          "Skipped crop slots" in html and "outside the" in html)
    check("report shows the preflight table",
          "Preflight" in html and "js-runtime" in html)
    check("report notes cache freshness", "downloaded fresh" in html)
    check("report shows layout scaling note", "factor 0.3333" in html)
    reused = dict(rec, clipReused=True, captureAttempts=[])
    check("reused-cache run says so in the attempts section",
          "cached clip was validated and reused"
          in roa.build_report_html(reused, []))

    # ---- 9. status independence from detection/evidence ------------------
    print("run status independence")
    check("detection skipped -> partial (not failed)",
          roa.run_status_of({"ok": True,
                             "detection": {"status": "skipped"}})
          == "partial")
    check("evidence failure -> partial (not failed)",
          roa.run_status_of({"ok": True, "detection": {"status": "ok"},
                             "evidenceError": "ValueError: boom"})
          == "partial")
    check("clean run -> ok",
          roa.run_status_of({"ok": True, "detection": {"status": "ok"},
                             "filtered": True}) == "ok")

    # ---- 10. pages carry the new capture workflow ------------------------
    print("run.html / runs.html")
    with open(os.path.join(ROOT, "run.html"), encoding="utf-8") as f:
        rh = f.read()
    check("run.html has the readiness panel wired to /api/preflight",
          "/api/preflight" in rh and 'id="readyList"' in rh)
    check("run.html has fast + normal + force capture buttons",
          'id="startFastBtn"' in rh and 'id="startBtn"' in rh
          and 'id="forceBtn"' in rh)
    check("run.html has latest-report/crops + rebuild evidence",
          'id="latestReport"' in rh and 'id="latestCrops"' in rh
          and 'id="rebuildBtn"' in rh and "/api/latest-run" in rh)
    check("run.html timeline includes the preflight step",
          "Preflight" in rh)
    with open(os.path.join(ROOT, "runs.html"), encoding="utf-8") as f:
        rns = f.read()
    check("runs.html links report + layout + crops per run",
          "index.html" in rns and "layout.html" in rns
          and "crops.html" in rns)

    shutil.rmtree(TMP, ignore_errors=True)
    print()
    if _fails:
        print(f"FAIL — {_fails} check(s) failed")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
