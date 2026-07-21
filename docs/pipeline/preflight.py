#!/usr/bin/env python3
"""
preflight.py — capture-readiness checks, run BEFORE a capture starts.

One place that answers "will a capture run work on this machine?" instead of
letting a missing tool / missing DB table fail the run twenty minutes in
(e.g. the classic late "no such table: heroes" at the export step).

Checks (each returns ok / warn / fail + a concrete remedy):
  python        version is new enough for the pipeline
  ffmpeg        on PATH and runs
  ffprobe       on PATH and runs (clip validation + resolution reporting)
  yt-dlp        on PATH, version printed (YouTube capture only)
  js-runtime    Deno/Node available for yt-dlp format unscrambling (warn)
  opencv        cv2 importable (evidence pages + detection)
  database      DB file exists and has the schema tables (auto-fixable)
  source        the requested source id exists + is enabled (when given)
  layout        the layout JSON exists and has 5+5 slots (when resolvable)
  writable      work/ + reports/ + data/ accept writes

'fail' means the capture WILL break; 'warn' means it will run degraded
(e.g. no JS runtime -> yt-dlp may stall onto fallback formats).

Usage:
  python pipeline/preflight.py                       # environment only
  python pipeline/preflight.py --source owcs-8c105lnzlam
  python pipeline/preflight.py --fix-db              # auto-init missing DB
  python pipeline/preflight.py --json                # machine-readable
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

MIN_PY = (3, 10)
DB_REMEDY = "python pipeline/init_db.py --with-sample"


def _check(name: str, status: str, detail: str, remedy: str = "") -> dict:
    return {"name": name, "status": status, "detail": detail,
            "remedy": remedy}


def _tool_version(exe: str, args: list[str] | None = None,
                  runner=subprocess) -> tuple[bool, str]:
    """(found, first-line-of-version-output)."""
    try:
        res = runner.run([exe, *(args or ["-version"])], check=True,
                         capture_output=True, text=True, timeout=20)
        first = ((res.stdout or res.stderr or "").strip().splitlines()
                 or ["(no output)"])[0]
        return True, first[:120]
    except FileNotFoundError:
        return False, "not found on PATH"
    except subprocess.CalledProcessError as e:
        return False, f"found but exited {e.returncode}"
    except Exception as e:  # timeout etc.
        return False, f"{type(e).__name__}: {e}"


def check_python() -> dict:
    v = sys.version_info
    ok = (v.major, v.minor) >= MIN_PY
    return _check("python", "ok" if ok else "fail",
                  f"Python {v.major}.{v.minor}.{v.micro}",
                  "" if ok else f"install Python >= "
                               f"{MIN_PY[0]}.{MIN_PY[1]}")


def check_ffmpeg(runner=subprocess) -> dict:
    ok, line = _tool_version("ffmpeg", runner=runner)
    return _check("ffmpeg", "ok" if ok else "fail", line,
                  "" if ok else "install ffmpeg (Windows: winget install "
                                "ffmpeg) and ensure it is on PATH")


def check_ffprobe(runner=subprocess) -> dict:
    ok, line = _tool_version("ffprobe", runner=runner)
    return _check("ffprobe", "ok" if ok else "warn", line,
                  "" if ok else "ffprobe ships with ffmpeg — reinstall "
                                "ffmpeg; clip validation falls back to a "
                                "byte-size check without it")


def check_ytdlp(runner=subprocess) -> dict:
    ok, line = _tool_version("yt-dlp", ["--version"], runner=runner)
    return _check("yt-dlp", "ok" if ok else "warn",
                  f"yt-dlp {line}" if ok else line,
                  "" if ok else "install with `pip install yt-dlp` — only "
                                "needed for YouTube capture; local MP4 mode "
                                "works without it")


def check_js_runtime(which=shutil.which) -> dict:
    import video_ingest as vi
    name, path = vi.detect_js_runtime(which)
    if name == "deno":
        return _check("js-runtime", "ok", f"deno at {path}")
    if name == "node":
        return _check("js-runtime", "ok",
                      f"node at {path} (passed to yt-dlp via "
                      "--js-runtimes node)")
    return _check("js-runtime", "warn",
                  "no Deno/Node found — yt-dlp may stall on some YouTube "
                  "formats",
                  "install Node.js (winget install OpenJS.NodeJS.LTS) or "
                  "Deno; the capture ladder + direct-url fallback still "
                  "apply without one")


def check_opencv() -> dict:
    try:
        import cv2
        return _check("opencv", "ok", f"cv2 {cv2.__version__}")
    except Exception as e:
        return _check("opencv", "fail",
                      f"cv2 import failed: {type(e).__name__}: {e}",
                      "pip install opencv-python-headless")


REQUIRED_TABLES = ("heroes", "teams", "matches", "comp_snapshots")


def db_tables_missing(db_path: str | None = None) -> list[str]:
    """Which required tables are absent (all of them if no DB file)."""
    path = db_path or db.DB_PATH
    if not os.path.exists(path):
        return list(REQUIRED_TABLES)
    try:
        con = db.connect(path)
        have = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
    except Exception:
        return list(REQUIRED_TABLES)
    return [t for t in REQUIRED_TABLES if t not in have]


def init_db_reference(db_path: str | None = None) -> str:
    """Create schema + seed reference data (heroes/maps/teams) in place.

    Idempotent (INSERT OR REPLACE); never touches comps or sample matches.
    Returns a summary line. This is the auto-fix for a missing/blank DB so a
    capture never dies at the export step with 'no such table: heroes'."""
    import init_db as idb
    con = db.connect(db_path or db.DB_PATH)
    db.init_schema(con)
    idb.seed_reference(con, idb.load_sample())
    idb.migrate(con)
    con.close()
    return f"initialized schema + reference data at {db_path or db.DB_PATH}"


def check_database(db_path: str | None = None, fix: bool = False) -> dict:
    missing = db_tables_missing(db_path)
    if not missing:
        return _check("database", "ok",
                      f"initialized ({os.path.basename(db_path or db.DB_PATH)}"
                      ", all required tables present)")
    if fix:
        try:
            note = init_db_reference(db_path)
            return _check("database", "ok", f"auto-initialized — {note}")
        except Exception as e:
            return _check("database", "fail",
                          f"auto-init failed: {type(e).__name__}: {e}",
                          DB_REMEDY)
    return _check("database", "warn",
                  f"missing table(s): {', '.join(missing)} — will be "
                  "auto-initialized when a run starts",
                  DB_REMEDY)


def check_source(source_id: str | None,
                 sources_path: str | None = None) -> dict:
    if not source_id:
        return _check("source", "ok", "no source selected (local MP4 mode "
                                      "or environment-only check)")
    import video_ingest as vi
    src = vi.find_source(sources_path or vi.DEFAULT_SOURCES, source_id)
    if src is None:
        return _check("source", "fail",
                      f"no source id '{source_id}' in video_sources.json",
                      "pick a source on the Run page, or add it with "
                      "pipeline/manage_sources.py")
    if not src.get("enabled", True):
        return _check("source", "fail", f"source '{source_id}' is disabled",
                      "enable it in data/sources/video_sources.json")
    url = src.get("url") or src.get("vodUrl") or ""
    return _check("source", "ok", f"{source_id} -> {url[:80]}")


def resolve_layout(source_id: str | None, layout: str | None,
                   sources_path: str | None = None) -> str | None:
    """The layout path a run with these args would use (best-effort)."""
    if layout:
        return layout
    if source_id:
        import video_ingest as vi
        src = vi.find_source(sources_path or vi.DEFAULT_SOURCES, source_id)
        if src and src.get("layout"):
            return src["layout"]
    return None


def check_layout(layout_path: str | None) -> dict:
    if not layout_path:
        return _check("layout", "ok", "default layout will be used")
    p = layout_path if os.path.isabs(layout_path) \
        else os.path.join(db.REPO_ROOT, layout_path)
    if not os.path.exists(p):
        return _check("layout", "fail", f"layout not found: {layout_path}",
                      "fix the layout path in video_sources.json or pass "
                      "--layout")
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except ValueError as e:
        return _check("layout", "fail",
                      f"layout is not valid JSON: {e}",
                      f"fix the syntax in {layout_path}")
    a, b = data.get("slots_a"), data.get("slots_b")
    if not (isinstance(a, list) and len(a) == 5
            and isinstance(b, list) and len(b) == 5):
        return _check("layout", "warn",
                      f"{layout_path}: expected 5+5 slots, got "
                      f"{len(a) if isinstance(a, list) else 0}+"
                      f"{len(b) if isinstance(b, list) else 0} — crops will "
                      "be incomplete",
                      "calibrate the layout (see docs/layout-calibration.md)")
    lw, lh = data.get("frame_width"), data.get("frame_height")
    return _check("layout", "ok",
                  f"{layout_path} ({lw}x{lh} native, 5+5 slots)")


def check_writable() -> dict:
    bad = []
    for sub in ("work", "reports", "data"):
        d = os.path.join(db.REPO_ROOT, sub)
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write_probe")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
        except OSError as e:
            bad.append(f"{sub}/ ({e})")
    if bad:
        return _check("writable", "fail",
                      "cannot write to: " + ", ".join(bad),
                      "fix folder permissions / run from a writable copy of "
                      "the repo")
    return _check("writable", "ok", "work/, reports/, data/ writable")


def run_checks(source: str | None = None, layout: str | None = None,
               sources_path: str | None = None, fix_db: bool = False,
               db_path: str | None = None, need_youtube: bool = True) -> dict:
    """All checks -> {"ok", "failed", "warned", "checks": [...]}.

    ok is True when nothing FAILED (warnings are allowed — the run degrades
    honestly). need_youtube=False relaxes yt-dlp to informational (local MP4
    runs don't need it)."""
    checks = [
        check_python(),
        check_ffmpeg(),
        check_ffprobe(),
        check_ytdlp(),
        check_js_runtime(),
        check_opencv(),
        check_database(db_path, fix=fix_db),
        check_source(source, sources_path),
        check_layout(resolve_layout(source, layout, sources_path)),
        check_writable(),
    ]
    if need_youtube and source:
        # a youtube capture NEEDS yt-dlp — escalate its warn to fail
        for c in checks:
            if c["name"] == "yt-dlp" and c["status"] == "warn":
                c["status"] = "fail"
                c["detail"] += " (required for a YouTube source)"
    failed = [c for c in checks if c["status"] == "fail"]
    warned = [c for c in checks if c["status"] == "warn"]
    return {"ok": not failed, "failed": [c["name"] for c in failed],
            "warned": [c["name"] for c in warned], "checks": checks}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Capture readiness checks")
    ap.add_argument("--source", help="also check this source id + its layout")
    ap.add_argument("--layout", help="check this layout path explicitly")
    ap.add_argument("--fix-db", action="store_true",
                    help="auto-initialize the DB (schema + reference data) "
                         "if tables are missing")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    res = run_checks(source=args.source, layout=args.layout,
                     fix_db=args.fix_db)
    if args.as_json:
        print(json.dumps(res, indent=1))
    else:
        icon = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}
        for c in res["checks"]:
            print(f"  {icon[c['status']]}  {c['name']:<10} {c['detail']}")
            if c["remedy"]:
                print(f"        -> {c['remedy']}")
        print()
        print("READY for capture" if res["ok"] else
              f"NOT READY — fix: {', '.join(res['failed'])}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
