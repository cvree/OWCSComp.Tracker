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
import proc_text  # noqa: E402  (UTF-8 subprocess decoding)

MIN_PY = (3, 10)
DB_REMEDY = "python pipeline/init_db.py --with-sample"

# A remedy is meant to be COPIED, so `remedy` holds a whole command that
# works as written on a clean Windows install and NOTHING else — no
# parentheses, no "then do X". Advice that is not itself runnable goes in
# `note`, which is printed beside the command and never copied with it.
# Mixing the two produced remedies that could not be pasted anywhere.
#
# `python -m pip` rather than `pip`, because a machine with more than one
# Python has more than one `pip`, and the wrong one installs into an
# interpreter this process is not running.
PIP_REMEDY = "python -m pip install -r requirements.txt"
FFMPEG_REMEDY = ("winget install --id Gyan.FFmpeg -e "
                 "--accept-source-agreements --accept-package-agreements")
REOPEN_NOTE = ("Then CLOSE this terminal and open a new one — a window that "
               "was already open cannot see a program installed after it "
               "started.")


def _check(name: str, status: str, detail: str, remedy: str = "",
           note: str = "") -> dict:
    return {"name": name, "status": status, "detail": detail,
            "remedy": remedy, "note": note}


def _tool_version(exe: str, args: list[str] | None = None,
                  runner=subprocess) -> tuple[bool, str]:
    """(found, first-line-of-version-output)."""
    try:
        res = runner.run([exe, *(args or ["-version"])], check=True,
                         capture_output=True, text=True, timeout=20,
                         **proc_text.PIPE_TEXT)
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
                  "",
                  "" if ok else
                  f"Install Python {MIN_PY[0]}.{MIN_PY[1]} or newer from "
                  f"python.org, and tick \"Add python.exe to PATH\" on the "
                  f"installer's first screen.")


def check_ffmpeg(runner=subprocess) -> dict:
    ok, line = _tool_version("ffmpeg", runner=runner)
    return _check("ffmpeg", "ok" if ok else "fail", line,
                  "" if ok else FFMPEG_REMEDY,
                  "" if ok else REOPEN_NOTE)


def check_ffprobe(runner=subprocess) -> dict:
    ok, line = _tool_version("ffprobe", runner=runner)
    return _check("ffprobe", "ok" if ok else "warn", line,
                  "" if ok else FFMPEG_REMEDY,
                  "" if ok else "ffprobe ships alongside ffmpeg, so this "
                                "means the ffmpeg install is incomplete. "
                                + REOPEN_NOTE)


def check_ytdlp(runner=subprocess) -> dict:
    ok, line = _tool_version("yt-dlp", ["--version"], runner=runner)
    return _check("yt-dlp", "ok" if ok else "warn",
                  f"yt-dlp {line}" if ok else line,
                  "" if ok else PIP_REMEDY,
                  "" if ok else "yt-dlp is in requirements.txt. Only YouTube "
                                "capture needs it — local MP4 files work "
                                "without it.")


def check_js_runtime(which=shutil.which) -> dict:
    # `video_ingest` reaches cv2 through `capture`, and a machine that is
    # MISSING cv2 is precisely the machine someone runs this on. Importing it
    # unguarded meant the readiness check died of the fault it exists to
    # report — a traceback about cv2 while checking for a JS runtime, with
    # the ffmpeg and yt-dlp lines never printed at all. The probe itself is a
    # PATH lookup, so it is repeated here rather than depended upon.
    try:
        import video_ingest as vi
        name, path = vi.detect_js_runtime(which)
    except Exception:  # noqa: BLE001 — any import failure, not just cv2's
        name, path = next((("deno" if n == "deno" else "node", p)
                           for n in ("deno", "node") for p in [which(n)] if p),
                          (None, None))
    if name == "deno":
        return _check("js-runtime", "ok", f"deno at {path}")
    if name == "node":
        return _check("js-runtime", "ok",
                      f"node at {path} (passed to yt-dlp via "
                      "--js-runtimes node)")
    return _check("js-runtime", "warn",
                  "no Deno/Node found — yt-dlp may stall on some YouTube "
                  "formats",
                  "winget install --id OpenJS.NodeJS.LTS -e "
                  "--accept-source-agreements --accept-package-agreements",
                  "Optional. The download ladder and the direct-url fallback "
                  "still work without it, just less reliably. " + REOPEN_NOTE)


def check_opencv() -> dict:
    try:
        import cv2
        return _check("opencv", "ok", f"cv2 {cv2.__version__}")
    except Exception as e:
        return _check("opencv", "fail",
                      f"cv2 import failed: {type(e).__name__}: {e}",
                      PIP_REMEDY)


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
    def _safe(name, fn, *a, **kw) -> dict:
        """Run one check without letting it take the other nine with it.

        This is the tool someone runs BECAUSE their install is broken, so a
        check throwing is an expected input, not an impossible one. An
        unguarded raise used to replace the whole readiness report with a
        traceback about whichever check happened to be first — the operator
        then never learned that ffmpeg was missing too.
        """
        try:
            return fn(*a, **kw)
        except Exception as e:  # noqa: BLE001 — reporting beats propagating
            return _check(name, "fail",
                          f"the {name} check itself failed: "
                          f"{type(e).__name__}: {e}",
                          PIP_REMEDY,
                          "A check that cannot run is usually an incomplete "
                          "install. If reinstalling the requirements does not "
                          "clear it, this one is a bug worth reporting.")

    checks = [
        _safe("python", check_python),
        _safe("ffmpeg", check_ffmpeg),
        _safe("ffprobe", check_ffprobe),
        _safe("yt-dlp", check_ytdlp),
        _safe("js-runtime", check_js_runtime),
        _safe("opencv", check_opencv),
        _safe("database", check_database, db_path, fix=fix_db),
        _safe("source", check_source, source, sources_path),
        # resolve_layout reads the sources file, so it can throw too; fold
        # both halves into the one guarded call.
        _safe("layout", lambda: check_layout(
            resolve_layout(source, layout, sources_path))),
        _safe("writable", check_writable),
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
    proc_text.enable_utf8_stdio()
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
            if c.get("note"):
                print(f"           {c['note']}")
        print()
        print("READY for capture" if res["ok"] else
              f"NOT READY — fix: {', '.join(res['failed'])}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
