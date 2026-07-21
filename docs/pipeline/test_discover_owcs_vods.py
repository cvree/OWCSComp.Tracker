#!/usr/bin/env python3
"""test_discover_owcs_vods.py — offline tests for YouTube VOD discovery.

Uses a fake yt-dlp runner (canned --flat-playlist JSON); no network, no
yt-dlp binary needed.
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import discover_owcs_vods as dv  # noqa: E402
import video_ingest as vi        # noqa: E402

TMP = os.path.join(ROOT, "work", "test_discover")
SRC = os.path.join(TMP, "video_sources.json")
_fails = 0


def check(name, cond):
    global _fails
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        _fails += 1


FAKE_PAYLOAD = {
    "entries": [
        {"id": "AAAAAAAAAA1", "title": "OWCS 2026 Champions Clash — Stage 2 Finals",
         "duration": 21600, "upload_date": "20260620"},
        {"id": "BBBBBBBBBB2", "title": "Overwatch dev update",
         "duration": 480, "upload_date": "20260618"},
        {"id": "CCCCCCCCCC3", "title": "OWCS NA Stage 2 Week 3",
         "duration": 14400},                       # no upload_date (flat mode)
        {"title": "broken entry, no id"},
    ]
}


class FakeRunner:
    """Stands in for subprocess: records cmds, returns canned yt-dlp JSON."""

    def __init__(self, payload=FAKE_PAYLOAD, fail=None):
        self.cmds = []
        self.payload = payload
        self.fail = fail                         # exception class to raise

    def run(self, cmd, check=True, capture_output=True, text=True):
        self.cmds.append(cmd)
        if self.fail:
            raise self.fail(cmd[0])
        return types.SimpleNamespace(returncode=0,
                                     stdout=json.dumps(self.payload),
                                     stderr="")


def fresh_sources():
    os.makedirs(TMP, exist_ok=True)
    with open(SRC, "w", encoding="utf-8") as f:
        json.dump({"_readme": ["test"], "sources": []}, f)


def main():
    if os.path.isdir(TMP):
        shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP, exist_ok=True)

    print("discovery parsing:")
    runner = FakeRunner()
    vods = dv.discover("https://www.youtube.com/@ow_esports/streams", 20,
                       runner=runner)
    check("one yt-dlp call, metadata only",
          len(runner.cmds) == 1 and "--flat-playlist" in runner.cmds[0]
          and "-J" in runner.cmds[0])
    check("limit passed to yt-dlp",
          "--playlist-end" in runner.cmds[0]
          and "20" in runner.cmds[0])
    check("parses 3 usable entries (skips id-less)", len(vods) == 3)
    check("builds canonical watch URLs",
          vods[0]["url"].startswith("https://www.youtube.com/watch?v="))
    check("formats upload_date",
          any(v["date"] == "2026-06-20" for v in vods))
    check("missing date becomes TBD",
          any(v["date"] == "TBD" for v in vods))

    print("scoring + slugs:")
    by_id = {v["videoId"]: v for v in vods}
    check("OWCS finals scores highest",
          vods[0]["videoId"] == "AAAAAAAAAA1")
    check("short non-OWCS clip scores lowest",
          vods[-1]["videoId"] == "BBBBBBBBBB2")
    check("slug format owcs-<videoid lower>",
          by_id["AAAAAAAAAA1"]["slug"] == "owcs-aaaaaaaaaa1")

    print("dry run:")
    # CLI tests monkeypatch fetch_channel_entries so main() never shells out.
    orig = dv.fetch_channel_entries
    dv.fetch_channel_entries = lambda url, limit, runner=None: FAKE_PAYLOAD["entries"]
    try:
        fresh_sources()
        rc = dv.main(["--provider", "youtube", "--limit", "20",
                      "--select", "1", "--sources", SRC])
        data = dv.load_sources_file(SRC)
        check("select without --write exits 0", rc == 0)
        check("dry run writes nothing", data["sources"] == [])

        print("--write:")
        rc = dv.main(["--provider", "youtube", "--limit", "20",
                      "--select", "1", "--write", "--sources", SRC])
        data = dv.load_sources_file(SRC)
        check("--write exits 0", rc == 0)
        check("--write saves one source", len(data["sources"]) == 1)
        s = data["sources"][0] if data["sources"] else {}
        check("saved entry has expected fields",
              s.get("id") == "owcs-aaaaaaaaaa1"
              and s.get("platform") == "youtube"
              and s.get("url") == "https://www.youtube.com/watch?v=AAAAAAAAAA1"
              and s.get("enabled") is True
              and s.get("layout") == dv.DEFAULT_LAYOUT)
        check("_readme preserved", "_readme" in data)

        print("duplicates:")
        rc = dv.main(["--provider", "youtube", "--limit", "20",
                      "--select", "1", "--write", "--sources", SRC])
        data = dv.load_sources_file(SRC)
        check("duplicate not added again",
              rc == 0 and len(data["sources"]) == 1)

        print("source lookup integration:")
        found = vi.find_source(SRC, "owcs-aaaaaaaaaa1")
        check("saved source resolves via video_ingest.find_source",
              found is not None and vi.is_youtube_source(found))
    finally:
        dv.fetch_channel_entries = orig

    print("missing yt-dlp:")
    msg = ""
    try:
        dv.fetch_channel_entries("https://x", 5,
                                 runner=FakeRunner(fail=FileNotFoundError))
    except SystemExit as e:
        msg = str(e)
    check("helpful install message",
          "yt-dlp.exe" in msg and "github.com/yt-dlp" in msg
          and "C:\\ffmpeg\\bin" in msg)

    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{'ALL PASS' if _fails == 0 else str(_fails) + ' FAILURE(S)'}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
