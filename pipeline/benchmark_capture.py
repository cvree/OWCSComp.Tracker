#!/usr/bin/env python3
"""
benchmark_capture.py — old path vs new, measured rather than asserted.

Two ways to get calibration frames out of a broadcast:

  OLD  download the whole VOD with yt-dlp, then extract frames locally.
  NEW  resolve one direct media URL, then pull only the sampled frames by
       HTTP range, adaptively, stopping when the evidence is sufficient.

This runs both and reports the numbers that decide whether the change was
worth making: bytes over the wire, frames acquired, wall time, calibration
confidence, and — the one that matters most — whether the two paths produce
an EQUIVALENT layout. A faster calibration that lands the boxes somewhere
else is not an improvement, so the layouts are compared slot by slot.

TWO MODES
---------
`--url` runs against a real broadcast. Requires yt-dlp, ffmpeg and network,
and is the invocation to use on an OWCS VOD:

    python pipeline/benchmark_capture.py \\
        --url "https://www.youtube.com/watch?v=jkSiX___Qwc" \\
        --source-id owcs-jksix-qwc

`--fixture` runs entirely offline against a broadcast-shaped MP4 built from
this repository's own committed OWCS frames and served over a byte-range
HTTP server that counts what it sends. That count is measured at the wire
by the other end, so it cannot be fooled by caching or estimation — which
makes the offline mode the more TRUSTWORTHY measurement of the two, at a
smaller scale. Both modes report the same table.

Bytes on the real-VOD path are read from the host's interface counters
(Linux) and are therefore host-wide: run it on an otherwise quiet machine,
or trust the fixture mode's server-side count instead.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "fixtures"))

import proc_text  # noqa: E402
import calibrate_remote as cr  # noqa: E402
import calibrate_source as cs  # noqa: E402
import remote_frames as rf  # noqa: E402


def log(msg: str) -> None:
    print(f"[bench] {msg}", flush=True)


def human(n) -> str:
    if n is None:
        return "not measured"
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return str(n)


# ------------------------------------------------------------ equivalence
def compare_layouts(a: dict, b: dict, tol_px: int = 8) -> dict:
    """Are these the same calibration?

    Compared in NORMALISED coordinates so a layout calibrated at one
    capture resolution can be compared with one calibrated at another —
    which is the whole point of `norm_slots_*` existing. `tol_px` is stated
    at 1920x1080; a hero portrait is ~55px wide there, so 8px is
    comfortably inside "the box is on the same hero".
    """
    out = {"comparable": False, "equivalent": False, "maxDeltaPx": None,
            "perSlot": [], "note": ""}
    if not a or not b:
        out["note"] = "one side produced no layout"
        return out
    deltas = []
    for side in ("slots_a", "slots_b"):
        ra, rb = a.get(side), b.get(side)
        if not ra or not rb or len(ra) != len(rb):
            out["note"] = f"{side}: different slot counts"
            return out
        for i, (box_a, box_b) in enumerate(zip(ra, rb), start=1):
            d = max(abs(x - y) for x, y in zip(box_a, box_b))
            deltas.append(d)
            out["perSlot"].append({"slot": f"{side[-1]}{i}", "deltaPx": d,
                                   "old": box_a, "new": box_b})
    out["comparable"] = True
    out["maxDeltaPx"] = max(deltas) if deltas else None
    out["equivalent"] = bool(deltas) and max(deltas) <= tol_px
    out["note"] = (f"max slot delta {max(deltas)}px at 1920x1080 "
                   f"(tolerance {tol_px}px)") if deltas else "no slots"
    return out


def layout_of(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# -------------------------------------------------------------- the paths
def run_old_path(url: str, source_id: str, work: str, *,
                 interval: float, height: int, download_fn=None,
                 bytes_probe=None) -> dict:
    """Whole VOD, then extract every `interval` seconds locally."""
    os.makedirs(work, exist_ok=True)
    vod = os.path.join(work, "vod.mp4")
    frames_dir = os.path.join(work, "frames")
    shutil.rmtree(frames_dir, ignore_errors=True)
    os.makedirs(frames_dir)

    before = bytes_probe() if bytes_probe else None
    t0 = time.monotonic()
    if download_fn:
        download_fn(url, vod)
    else:
        cmd = ["yt-dlp", "-f",
               f"bv*[height<={height}]+ba/b[height<={height}]/b",
               "--no-playlist", "-o", vod, url]
        subprocess.run(cmd, check=True, capture_output=True,
                       **proc_text.PIPE_TEXT)
    dl_seconds = time.monotonic() - t0
    size = os.path.getsize(vod) if os.path.exists(vod) else 0

    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", vod,
         "-vf", f"fps=1/{interval}", "-q:v", "2", "-start_number", "0",
         os.path.join(frames_dir, "f%06d.png")],
        check=True, capture_output=True, **proc_text.PIPE_TEXT)
    frames = sorted(os.path.join(frames_dir, f)
                    for f in os.listdir(frames_dir) if f.endswith(".png"))
    after = bytes_probe() if bytes_probe else None

    # EVERYTHING after acquisition is held identical to the new path — the
    # same screen, the same gameplay filter, the same calibrate_source CLI.
    # A benchmark that let the old path skip the filter would not be
    # measuring acquisition, it would be measuring a strawman: feeding a
    # calibrator eighteen frames of analyst desk makes it refuse, and that
    # refusal says nothing about how the frames were fetched.
    by_offset = {float(i) * interval: p for i, p in enumerate(frames)}
    out_layout = os.path.join(work, "layout_old.json")
    cal_t0 = time.monotonic()
    sel = cr.select_and_calibrate(
        by_offset, source_id, out_layout,
        duration=max(by_offset) if by_offset else 1.0,
        stage_dir=os.path.join(work, "staged"))
    cal_seconds = time.monotonic() - cal_t0

    return {
        "path": "old (download the whole VOD)",
        "bytes": (after - before) if (before is not None
                                      and after is not None) else size,
        "vodBytes": size,
        "framesAcquired": len(frames),
        "framesUsed": len(sel.get("used") or {}),
        "seconds": round(time.monotonic() - t0, 1),
        "downloadSeconds": round(dl_seconds, 1),
        "calibrationSeconds": round(cal_seconds, 1),
        "confidence": sel.get("confidence"),
        "ok": bool(sel.get("ok")),
        "layout": layout_of(out_layout),
        "layoutPath": out_layout if os.path.exists(out_layout) else None,
        "reasons": sel.get("reasons") or ([sel["reason"]]
                                          if sel.get("reason") else []),
    }


def run_new_path(url: str, source_id: str, work: str, *,
                 duration: float, height: int, resolver=None,
                 bytes_probe=None) -> dict:
    os.makedirs(work, exist_ok=True)
    out_layout = os.path.join(work, "layout_new.json")
    before = bytes_probe() if bytes_probe else None
    t0 = time.monotonic()
    res = cr.run(url, source_id, out_layout, duration=duration,
                 height=height, cache_root=os.path.join(work, "cache"),
                 frames_dir=os.path.join(work, "frames_new"),
                 sheet=os.path.join(work, "sheet_new.png"),
                 resolver=resolver)
    after = bytes_probe() if bytes_probe else None
    acq = res.get("acquisition") or {}
    return {
        "path": "new (sparse remote frames)",
        "bytes": ((after - before) if (before is not None
                                       and after is not None)
                  else acq.get("bytesDownloaded")),
        "vodBytes": 0,
        "framesAcquired": res.get("framesAcquired", 0),
        "framesUsed": res.get("framesUsed", 0),
        "seconds": res.get("wallSeconds", round(time.monotonic() - t0, 1)),
        "downloadSeconds": None,
        "calibrationSeconds": None,
        "confidence": res.get("confidence"),
        "ok": bool(res.get("ok")),
        "layout": layout_of(out_layout),
        "layoutPath": out_layout if os.path.exists(out_layout) else None,
        "reasons": [],
        "stopReason": res.get("scan", {}).get("stopReason"),
        "ytdlpCalls": acq.get("ytdlpCalls"),
        "ffmpegReads": acq.get("ffmpegCalls"),
        "gameplayWindows": res.get("gameplayWindows"),
    }


# ------------------------------------------------------------- reporting
def report(old: dict, new: dict, *, duration: float, wire=None,
           reference: dict | None = None) -> str:
    cmp_ = compare_layouts(old.get("layout"), new.get("layout"))
    lines = []
    a = lines.append
    a("")
    a("=" * 74)
    a("  OWCS capture benchmark — full download vs sparse remote frames")
    a("=" * 74)
    a(f"  broadcast length: {duration / 60:.1f} min "
      f"({duration / 3600:.2f} h)")
    a("")
    a(f"  {'':26} {'OLD (full download)':>22} {'NEW (sparse)':>22}")
    a(f"  {'-' * 70}")

    def row(label, ov, nv):
        a(f"  {label:26} {str(ov):>22} {str(nv):>22}")

    row("bytes over the wire", human(old["bytes"]), human(new["bytes"]))
    if old["bytes"] and new["bytes"]:
        row("reduction", "—", f"{old['bytes'] / max(1, new['bytes']):.1f}x less")
    row("frames acquired", old["framesAcquired"], new["framesAcquired"])
    row("frames calibrated from", old["framesUsed"], new["framesUsed"])
    row("wall time (s)", old["seconds"], new["seconds"])
    row("calibration confidence", old["confidence"], new["confidence"])
    row("layout written", "yes" if old["ok"] else "REFUSED",
        "yes" if new["ok"] else "REFUSED")
    if new.get("ytdlpCalls") is not None:
        row("yt-dlp invocations", 1, new["ytdlpCalls"])
        row("ffmpeg reads", 1, new["ffmpegReads"])
    a("")
    a(f"  old vs new: "
      + ("EQUIVALENT" if cmp_["equivalent"]
         else ("DIFFERENT" if cmp_["comparable"] else "not comparable")))
    a(f"    {cmp_['note']}")
    if cmp_["comparable"] and not cmp_["equivalent"]:
        worst = sorted(cmp_["perSlot"], key=lambda s: -s["deltaPx"])[:3]
        for s in worst:
            a(f"    {s['slot']}: old {s['old']} vs new {s['new']} "
              f"({s['deltaPx']}px)")

    # Comparing the two paths to EACH OTHER cannot say which one is right.
    # When a trusted layout for this broadcast exists, both are measured
    # against it instead — that is the only comparison that can tell a
    # regression from an improvement.
    if reference:
        ro = compare_layouts(reference, old.get("layout"))
        rn = compare_layouts(reference, new.get("layout"))
        a("")
        a("  against the reference layout (ground truth):")
        a(f"    old: {ro['maxDeltaPx']}px" if ro["comparable"]
          else "    old: not comparable")
        a(f"    new: {rn['maxDeltaPx']}px" if rn["comparable"]
          else "    new: not comparable")
        if ro["comparable"] and rn["comparable"]:
            if rn["maxDeltaPx"] <= ro["maxDeltaPx"]:
                a("    -> the sparse path is at least as close to the "
                  "reference as the full download")
            else:
                a("    -> WARNING: the sparse path is further from the "
                  "reference than the full download")
    if new.get("stopReason"):
        a("")
        a(f"  new path stopped because: {new['stopReason']}")
    if new.get("gameplayWindows"):
        a(f"  new path also located {len(new['gameplayWindows'])} gameplay "
          f"window(s) for the deep pass")
    if wire:
        a("")
        a(f"  wire measurement: {wire}")
    a("=" * 74)
    a("")
    return "\n".join(lines)


# ------------------------------------------------------------------ modes
def fixture_mode(args) -> int:
    """Offline, over real HTTP, with the bytes counted by the server."""
    import make_broadcast
    import range_server

    tmp = args.work or tempfile.mkdtemp(prefix="bench_")
    os.makedirs(tmp, exist_ok=True)
    vod = os.path.join(tmp, "vod.mp4")
    windows = [(args.duration * 0.2, args.duration * 0.4),
               (args.duration * 0.65, args.duration * 0.85)]
    if not os.path.exists(vod):
        log(f"building a {args.duration / 60:.0f}-minute broadcast fixture "
            f"from the committed OWCS frames (this takes a minute)…")
        make_broadcast.build(vod, args.duration, windows)
    log(f"fixture: {os.path.getsize(vod) / 1e6:.1f} MB, live gameplay in "
        + ", ".join(f"{int(a)}-{int(b)}s" for a, b in windows))

    httpd, port = range_server.serve(tmp)
    url = f"http://127.0.0.1:{port}/vod.mp4"
    resolver = lambda u, h: (u, "benchmark-direct")   # noqa: E731

    def fake_download(_url, out):
        """The old path's download, over the SAME HTTP server, so both
        paths are measured on the same wire."""
        import urllib.request
        with urllib.request.urlopen(url) as r, open(out, "wb") as f:
            shutil.copyfileobj(r, f)

    try:
        log("running the OLD path: download the whole VOD, then sample it")
        range_server.reset()
        old = run_old_path(url, args.source_id, os.path.join(tmp, "old"),
                           interval=args.interval, height=args.height,
                           download_fn=fake_download)
        old["bytes"] = range_server.total_served()
        old_requests = range_server.total_requests()

        log("running the NEW path: sparse remote frames, adaptive")
        range_server.reset()
        new = run_new_path(url, args.source_id, os.path.join(tmp, "new"),
                           duration=args.duration, height=args.height,
                           resolver=resolver)
        new["bytes"] = range_server.total_served()
        new_requests = range_server.total_requests()
    finally:
        httpd.shutdown()

    wire = (f"counted by the HTTP server itself — "
            f"{old_requests} request(s) old, {new_requests} new")
    print(report(old, new, duration=args.duration, wire=wire,
                 reference=layout_of(args.reference) if args.reference else None))
    if args.json:
        print(json.dumps({"old": {k: v for k, v in old.items()
                                  if k != "layout"},
                          "new": {k: v for k, v in new.items()
                                  if k != "layout"},
                          "equivalence": compare_layouts(old.get("layout"),
                                                         new.get("layout"))},
                         indent=1, default=str))
    if not args.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if (new["ok"] and new["bytes"] < old["bytes"]) else 1


def real_mode(args) -> int:
    """Against an actual broadcast. Needs yt-dlp, ffmpeg and network."""
    import video_ingest as vi
    meta = vi.probe_vod(args.url)
    duration = float(meta.get("duration") or 0)
    if not duration:
        log("could not probe the broadcast duration")
        return 1
    log(f"broadcast: {meta.get('title')} — {duration / 3600:.2f}h")
    if duration > 3600 and not args.yes:
        log(f"the OLD path will download the ENTIRE {duration / 3600:.1f}-hour "
            f"broadcast (expect gigabytes). Re-run with --yes to confirm, or "
            f"use --skip-old to measure only the new path.")
        return 1

    tmp = args.work or tempfile.mkdtemp(prefix="benchreal_")
    os.makedirs(tmp, exist_ok=True)
    probe = rf._iface_bytes

    old = None
    if not args.skip_old:
        log("running the OLD path — downloading the whole VOD…")
        old = run_old_path(args.url, args.source_id,
                           os.path.join(tmp, "old"),
                           interval=args.interval, height=args.height,
                           bytes_probe=probe)
    log("running the NEW path — sparse remote frames…")
    new = run_new_path(args.url, args.source_id, os.path.join(tmp, "new"),
                       duration=duration, height=args.height,
                       bytes_probe=probe)

    if old is None:
        old = {"path": "old (skipped)", "bytes": None, "vodBytes": 0,
               "framesAcquired": "—", "framesUsed": "—", "seconds": "—",
               "confidence": "—", "ok": False, "layout": None,
               "reasons": ["--skip-old"]}
    print(report(old, new, duration=duration,
                 reference=layout_of(args.reference) if args.reference else None,
                 wire=("host interface counters (Linux, host-wide — run on "
                       "a quiet machine)" if rf._iface_bytes() is not None
                       else "not measurable on this platform")))
    if args.json:
        print(json.dumps({"old": {k: v for k, v in old.items()
                                  if k != "layout"},
                          "new": {k: v for k, v in new.items()
                                  if k != "layout"}},
                         indent=1, default=str))
    return 0 if new["ok"] else 1


def main(argv=None) -> int:
    proc_text.enable_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="benchmark against a real broadcast")
    g.add_argument("--fixture", action="store_true",
                   help="benchmark offline against a synthesised broadcast "
                        "served over real HTTP (bytes counted at the wire)")
    ap.add_argument("--source-id", default="benchmark")
    ap.add_argument("--duration", type=float, default=1800.0,
                    help="fixture length in seconds (fixture mode)")
    ap.add_argument("--interval", type=float, default=60.0,
                    help="the OLD path's sampling interval")
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--work", help="keep the working files here")
    ap.add_argument("--keep", action="store_true",
                    help="do not delete the working directory")
    ap.add_argument("--skip-old", action="store_true",
                    help="real mode: do not download the whole VOD")
    ap.add_argument("--yes", action="store_true",
                    help="real mode: confirm downloading a long broadcast")
    ap.add_argument("--reference", help="a trusted layout for this "
                    "broadcast; both paths are measured against it, which "
                    "is the only comparison that can tell an improvement "
                    "from a regression")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    return fixture_mode(args) if args.fixture else real_mode(args)


if __name__ == "__main__":
    raise SystemExit(main())
