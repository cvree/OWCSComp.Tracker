#!/usr/bin/env python3
"""
fetch_vendor.py — put ffmpeg, ffprobe and yt-dlp inside the application.

The user must never install anything, so the installer carries these three
binaries itself. They are fetched at *build* time (never at run time, and
never by the installed application) from their official release hosts, and
every download is checked against a size floor and a signature sniff so a
truncated or HTML error page cannot end up shipped as an executable.

    python packaging/fetch_vendor.py             # fetch into vendor/bin
    python packaging/fetch_vendor.py --check     # report what is present

Licensing, stated because shipping other people's binaries requires it:
ffmpeg is redistributed under the LGPL/GPL terms of the gyan.dev "essentials"
build, and yt-dlp under the Unlicense. `vendor/README.md` is written next to
the binaries recording the exact source URL and retrieval date of everything
in the folder.
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
VENDOR_BIN = os.path.join(REPO, "vendor", "bin")

FFMPEG_ZIP = ("https://www.gyan.dev/ffmpeg/builds/"
              "ffmpeg-release-essentials.zip")
YTDLP_EXE = ("https://github.com/yt-dlp/yt-dlp/releases/latest/download/"
             "yt-dlp.exe")

USER_AGENT = "OWCSCompTracker-build/1.0"
#: Anything smaller than this is not the binary we asked for.
MIN_SIZES = {"ffmpeg.exe": 20_000_000, "ffprobe.exe": 20_000_000,
             "yt-dlp.exe": 5_000_000}


def _fetch(url: str, *, timeout: int = 900) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _is_pe(data: bytes) -> bool:
    """A Windows executable starts with 'MZ'. An HTML error page does not."""
    return data[:2] == b"MZ"


def fetch_ffmpeg(dest: str) -> list[str]:
    print(f"[vendor] fetching ffmpeg from {FFMPEG_ZIP}")
    payload = _fetch(FFMPEG_ZIP)
    written = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for name in archive.namelist():
            base = os.path.basename(name)
            if base.lower() not in ("ffmpeg.exe", "ffprobe.exe"):
                continue
            data = archive.read(name)
            if not _is_pe(data):
                raise RuntimeError(f"{base} from the archive is not an executable")
            target = os.path.join(dest, base.lower())
            with open(target, "wb") as f:
                f.write(data)
            written.append(target)
            print(f"[vendor] wrote {target} ({len(data):,} bytes)")
    missing = {"ffmpeg.exe", "ffprobe.exe"} - {os.path.basename(p) for p in written}
    if missing:
        raise RuntimeError(f"the ffmpeg archive did not contain: {sorted(missing)}")
    return written


def fetch_ytdlp(dest: str) -> str:
    print(f"[vendor] fetching yt-dlp from {YTDLP_EXE}")
    data = _fetch(YTDLP_EXE)
    if not _is_pe(data):
        raise RuntimeError("the yt-dlp download is not an executable")
    target = os.path.join(dest, "yt-dlp.exe")
    with open(target, "wb") as f:
        f.write(data)
    print(f"[vendor] wrote {target} ({len(data):,} bytes)")
    return target


def verify(dest: str = VENDOR_BIN) -> dict[str, dict]:
    """What is present, how big, and whether it looks like a real binary."""
    report = {}
    for name, floor in MIN_SIZES.items():
        path = os.path.join(dest, name)
        if not os.path.exists(path):
            report[name] = {"present": False, "ok": False,
                            "detail": "not fetched"}
            continue
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(2)
        ok = size >= floor and head == b"MZ"
        report[name] = {
            "present": True, "ok": ok, "bytes": size,
            "detail": "ok" if ok else
                      (f"only {size:,} bytes (expected at least {floor:,})"
                       if size < floor else "not a Windows executable"),
        }
    return report


def write_readme(dest: str) -> str:
    path = os.path.join(os.path.dirname(dest), "README.md")
    import datetime
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "# Vendored binaries\n\n"
            "Fetched at build time by `packaging/fetch_vendor.py` and shipped\n"
            "inside the Windows installer so the user never installs anything.\n"
            "The installed application always prefers these over anything else\n"
            "on PATH (see `desktop/owcs_desktop/health.py::resolve_binary`).\n\n"
            f"Retrieved: {stamp}\n\n"
            "| binary | source | licence |\n"
            "|---|---|---|\n"
            f"| ffmpeg.exe, ffprobe.exe | {FFMPEG_ZIP} | LGPL/GPL "
            "(gyan.dev 'essentials' build) |\n"
            f"| yt-dlp.exe | {YTDLP_EXE} | Unlicense |\n\n"
            "This directory is generated and is not committed.\n")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="report what is present without fetching")
    parser.add_argument("--dest", default=VENDOR_BIN)
    args = parser.parse_args(argv)

    if args.check:
        report = verify(args.dest)
        for name, info in report.items():
            print(f"{'OK  ' if info['ok'] else 'MISS'} {name}: {info['detail']}")
        return 0 if all(i["ok"] for i in report.values()) else 1

    os.makedirs(args.dest, exist_ok=True)
    fetch_ffmpeg(args.dest)
    fetch_ytdlp(args.dest)
    write_readme(args.dest)

    report = verify(args.dest)
    bad = {n: i for n, i in report.items() if not i["ok"]}
    if bad:
        print(f"[vendor] FAILED: {bad}", file=sys.stderr)
        return 1
    total = sum(i.get("bytes", 0) for i in report.values())
    print(f"[vendor] all binaries present ({total:,} bytes total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
