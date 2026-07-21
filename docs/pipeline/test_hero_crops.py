#!/usr/bin/env python3
"""
test_hero_crops.py — offline, deterministic tests for the hero-crop capture +
review workflow (capture_hero_crops.py) and its serve.py API endpoints.

No network, no yt-dlp/ffmpeg, no DB writes, no comp promotion. Uses the demo
layout (1280x720) against the checked-in 1280x720 fixture frames so all 10
slot crops are valid. serve.py runs on an ephemeral localhost port with REPO
pointed at a temp tree.
"""
from __future__ import annotations
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture  # noqa: E402
import capture_hero_crops as chc  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURE_FRAMES = os.path.join(HERE, "fixtures", "video", "demo_match", "frames")
DEMO_LAYOUT = os.path.join(ROOT, "layouts", "owcs-demo.json")
FIXTURE_FILES = ["000600.png", "001200.png"]          # two clean 1280x720 frames

_fails = 0


def check(name: str, ok: bool) -> None:
    global _fails
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        _fails += 1


def _make_run(tmp: str, run: str = "demo_run"):
    """Frames dir + report dir for a fake run; returns (frames_dir, report_dir)."""
    frames_dir = os.path.join(tmp, "work", "auto", run, "frames_raw")
    os.makedirs(frames_dir, exist_ok=True)
    for f in FIXTURE_FILES:
        shutil.copy(os.path.join(FIXTURE_FRAMES, f),
                    os.path.join(frames_dir, f))
    report_dir = os.path.join(tmp, "reports", "auto", run)
    return frames_dir, report_dir


def _layout():
    lay = capture.load_layout(DEMO_LAYOUT)
    lay["_path"] = "layouts/owcs-demo.json"
    return lay


# --------------------------------------------------------------- unit tests
def test_capture_ten_crops(tmp: str) -> None:
    print("capture writes 10 crops per frame:")
    frames_dir, report_dir = _make_run(tmp)
    res = chc.capture_run("demo_run", _layout(), frames_dir, report_dir,
                          max_frames=8)
    check("2 frames processed", res["frames"] == 2)
    check("20 crops total (10 x 2)", res["crops"] == 20)
    crops_dir = os.path.join(report_dir, "hero_crops", "crops")
    pngs = [f for f in os.listdir(crops_dir) if f.endswith(".png")]
    check("20 crop PNGs on disk", len(pngs) == 20)
    per_frame = {"000600": 0, "001200": 0}
    for f in pngs:
        per_frame[f.split("_")[0]] = per_frame.get(f.split("_")[0], 0) + 1
    check("10 crops for frame 000600", per_frame["000600"] == 10)
    check("10 crops for frame 001200", per_frame["001200"] == 10)
    check("crops.json written", os.path.exists(chc.crops_json_path(report_dir)))
    check("labels.json written", os.path.exists(chc.labels_json_path(report_dir)))
    check("contact sheet written",
          os.path.exists(chc.contact_sheet_path(report_dir)))


def test_metadata_fields(tmp: str) -> None:
    print("metadata includes run/frame/side/slot/path:")
    frames_dir, report_dir = _make_run(tmp, "meta_run")
    chc.capture_run("meta_run", _layout(), frames_dir, report_dir)
    meta = json.load(open(chc.crops_json_path(report_dir)))
    crops = meta["crops"]
    check("20 crop metadata entries", len(crops) == 20)
    need = {"id", "run", "frame", "offset", "side", "slot", "crop",
            "guess", "score", "label_status", "label"}
    check("every entry has all required fields",
          all(need <= set(c.keys()) for c in crops))
    c0 = next(c for c in crops if c["id"] == "000600_a1")
    check("run recorded", c0["run"] == "meta_run")
    check("frame recorded", c0["frame"] == "000600.png")
    check("offset parsed from frame name", c0["offset"] == 600)
    check("side recorded", c0["side"] == "a")
    check("slot id recorded", c0["slot"] == "a1")
    check("crop path points into hero_crops/crops",
          c0["crop"] == "hero_crops/crops/000600_a1.png")
    check("sides split 10/10",
          sum(c["side"] == "a" for c in crops) == 10
          and sum(c["side"] == "b" for c in crops) == 10)
    check("all slots default to unlabeled",
          all(c["label_status"] == "unlabeled" for c in crops))


def test_label_updates_sidecar(tmp: str) -> None:
    print("labeling one crop updates the JSON sidecar:")
    frames_dir, report_dir = _make_run(tmp, "label_run")
    chc.capture_run("label_run", _layout(), frames_dir, report_dir)
    entry, err = chc.set_label(report_dir, "000600_a1", "kiriko")
    check("no error", err is None)
    check("returned entry is labeled", entry["label_status"] == "labeled")
    check("returned entry hero set", entry["label"] == "kiriko")
    labels = json.load(open(chc.labels_json_path(report_dir)))
    check("labels.json has the crop", labels.get("000600_a1", {}).get("hero")
          == "kiriko")
    check("labels.json status labeled",
          labels["000600_a1"]["status"] == "labeled")
    # crops.json reflects it too
    meta = json.load(open(chc.crops_json_path(report_dir)))
    c = next(c for c in meta["crops"] if c["id"] == "000600_a1")
    check("crops.json reflects the label", c["label"] == "kiriko")
    # idempotent
    e2, _ = chc.set_label(report_dir, "000600_a1", "ana")
    labels2 = json.load(open(chc.labels_json_path(report_dir)))
    check("relabel overwrites (idempotent)",
          labels2["000600_a1"]["hero"] == "ana")
    check("unknown crop id errors",
          chc.set_label(report_dir, "999999_z9", "ana")[1] is not None)


def test_reject_updates_sidecar(tmp: str) -> None:
    print("rejecting one crop updates the JSON sidecar:")
    frames_dir, report_dir = _make_run(tmp, "reject_run")
    chc.capture_run("reject_run", _layout(), frames_dir, report_dir)
    entry, err = chc.reject_crop(report_dir, "000600_b3")
    check("no error", err is None)
    check("entry status rejected", entry["label_status"] == "rejected")
    labels = json.load(open(chc.labels_json_path(report_dir)))
    check("labels.json marks rejected",
          labels.get("000600_b3", {}).get("status") == "rejected")
    check("unknown crop id errors",
          chc.reject_crop(report_dir, "nope_a1")[1] is not None)


def test_candidate_export_dry_run(tmp: str) -> None:
    print("candidate export is dry-run by default:")
    frames_dir, report_dir = _make_run(tmp, "cand_run")
    chc.capture_run("cand_run", _layout(), frames_dir, report_dir)
    chc.set_label(report_dir, "000600_a1", "kiriko")
    troot = os.path.join(tmp, "templates_dry")
    res = chc.export_candidates("cand_run", report_dir, templates_root=troot)
    check("dry-run reports wrote=False", res["wrote"] is False)
    check("one labeled crop planned", res["count"] == 1)
    check("planned target is under candidates/",
          "candidates" in res["planned"][0]["dest"])
    check("nothing written to disk", not os.path.exists(troot))


def test_candidate_export_write_isolated(tmp: str) -> None:
    print("candidate write saves ONLY into templates/candidates/:")
    frames_dir, report_dir = _make_run(tmp, "cand_w_run")
    chc.capture_run("cand_w_run", _layout(), frames_dir, report_dir)
    chc.set_label(report_dir, "000600_a1", "kiriko")
    chc.set_label(report_dir, "001200_b2", "ana")
    chc.reject_crop(report_dir, "000600_a3")          # rejected -> excluded
    troot = os.path.join(tmp, "templates_real")
    os.makedirs(troot, exist_ok=True)
    # a pre-existing REAL template that must never be touched
    real = os.path.join(troot, "genji.png")
    with open(real, "wb") as f:
        f.write(b"REAL-TEMPLATE-BYTES")
    res = chc.export_candidates("cand_w_run", report_dir,
                                templates_root=troot, write=True)
    check("write reports wrote=True", res["wrote"] is True)
    check("only 2 labeled (rejected excluded)", res["count"] == 2)
    cand = os.path.join(troot, "candidates")
    check("candidates/ created", os.path.isdir(cand))
    check("kiriko candidate written",
          os.path.isfile(os.path.join(cand, "kiriko",
                                      "cand_w_run_a1_000600.png")))
    check("ana candidate written",
          os.path.isfile(os.path.join(cand, "ana",
                                      "cand_w_run_b2_001200.png")))
    # real templates dir: only genji.png + candidates/ at top level, untouched
    top = sorted(os.listdir(troot))
    check("top-level templates dir untouched (genji.png + candidates only)",
          top == ["candidates", "genji.png"])
    check("real template bytes unchanged",
          open(real, "rb").read() == b"REAL-TEMPLATE-BYTES")


def test_contact_sheet_honesty(tmp: str) -> None:
    print("contact sheet states capture-only honesty note:")
    frames_dir, report_dir = _make_run(tmp, "html_run")
    chc.capture_run("html_run", _layout(), frames_dir, report_dir)
    html = open(chc.contact_sheet_path(report_dir), encoding="utf-8").read()
    check("says review/capture only — does not write comps",
          "does not write comps" in html)
    check("links to run report", "index.html" in html)
    check("embeds the run id for the API", "html_run" in html)


def test_heroes_message_offline(tmp: str) -> None:
    print("hero list gives an init hint when the DB is absent:")
    missing = os.path.join(tmp, "no_such.sqlite")
    heroes, msg = chc.load_heroes(missing)
    check("no heroes returned", heroes == [])
    check("message points to init_db --with-sample",
          msg is not None and "init_db.py --with-sample" in msg)


# ------------------------------------------------------------- API endpoints
def _api(port: int, path: str, body: dict | None = None):
    url = f"http://127.0.0.1:{port}{path}"
    if body is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_api_endpoints(tmp: str) -> None:
    print("serve.py hero-crop API (list / label / reject):")
    import serve
    repo = os.path.join(tmp, "apirepo")
    frames_dir, report_dir = _make_run(repo, "api_run")
    chc.capture_run("api_run", _layout(), frames_dir, report_dir)
    serve.REPO = repo                       # point the endpoints at temp tree

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.1)
    try:
        code, payload = _api(port, "/api/runs/api_run/hero-crops")
        check("GET hero-crops 200", code == 200)
        check("GET returns 20 crops", len(payload.get("crops", [])) == 20)

        code, _ = _api(port, "/api/runs/ghost_run/hero-crops")
        check("GET unknown run 404", code == 404)

        code, entry = _api(port, "/api/runs/api_run/hero-crops/000600_a1/label",
                           {"hero": "kiriko"})
        check("POST label 200", code == 200)
        check("POST label returns labeled", entry.get("label") == "kiriko")

        code, entry = _api(port, "/api/runs/api_run/hero-crops/000600_a2/reject",
                           {})
        check("POST reject 200", code == 200)
        check("POST reject returns rejected",
              entry.get("label_status") == "rejected")

        code, err = _api(port, "/api/runs/api_run/hero-crops/zzz_z9/label",
                         {"hero": "ana"})
        check("POST label unknown crop 404", code == 404)

        # persisted to the sidecar the CLI/page also read
        labels = json.load(open(chc.labels_json_path(report_dir)))
        check("API label persisted to labels.json",
              labels["000600_a1"]["hero"] == "kiriko")
        check("API reject persisted to labels.json",
              labels["000600_a2"]["status"] == "rejected")
    finally:
        httpd.shutdown()


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="owcs_herocrops_")
    try:
        test_capture_ten_crops(tmp)
        test_metadata_fields(tmp)
        test_label_updates_sidecar(tmp)
        test_reject_updates_sidecar(tmp)
        test_candidate_export_dry_run(tmp)
        test_candidate_export_write_isolated(tmp)
        test_contact_sheet_honesty(tmp)
        test_heroes_message_offline(tmp)
        test_api_endpoints(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if _fails:
        print(f"\n{_fails} CHECK(S) FAILED")
        return 1
    print("\nALL HERO CROP TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
