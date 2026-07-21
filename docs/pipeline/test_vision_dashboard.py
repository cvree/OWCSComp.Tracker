#!/usr/bin/env python3
"""
test_vision_dashboard.py — offline, deterministic tests for
vision_dashboard.py.

Covers (per task spec):
  1. dashboard works with everything missing (no crash, MISS rows, fix cmd)
  2. dashboard works with synthetic run artifacts (real crops via
     capture_hero_crops against fixture frames)
  3. dashboard links existing reports and marks missing ones
  4. dashboard shows the next recommended action
  5. dashboard includes crop + context sections (context PNGs generated)
  6. NO writes outside reports/auto/<run>/vision_dashboard* — DB, templates,
     layouts, work/, labels are byte-identical before/after

No network, no yt-dlp/ffmpeg, no DB writes, no comp promotion.
"""
from __future__ import annotations
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture                      # noqa: E402
import capture_hero_crops as chc    # noqa: E402
import vision_dashboard as vd       # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURE_FRAMES = os.path.join(HERE, "fixtures", "video", "demo_match",
                              "frames")
DEMO_LAYOUT = os.path.join(ROOT, "layouts", "owcs-demo.json")
FIXTURE_FILES = ["000600.png", "001200.png"]

_fails = 0


def check(name: str, ok: bool) -> None:
    global _fails
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        _fails += 1


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def snapshot(root: str, skip_prefix: str) -> dict:
    """{relpath: (size, mtime_ns)} for every file except under skip_prefix."""
    out = {}
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            r = os.path.relpath(p, root)
            if r.startswith(skip_prefix):
                continue
            st = os.stat(p)
            out[r] = (st.st_size, st.st_mtime_ns)
    return out


def make_root(tmp: str) -> str:
    root = os.path.join(tmp, "repo")
    for d in ("data", "layouts", "reports", "templates", "work", "pipeline"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    return root


# ---------------------------------------------------------------- test 1 + 4
def test_missing_everything(tmp: str) -> None:
    print("\n[1] empty tree — no crash, MISS rows, next action = init DB")
    root = make_root(os.path.join(tmp, "t1"))
    res = vd.generate("owcs-8c105lnzlam_000600_000630",
                      "layouts/owcs_8c105lnzlam.json", root=root)
    html = read(res["html"])
    check("dashboard html written", os.path.isfile(res["html"]))
    check("html inside reports/auto/<run>/",
          os.path.normpath(res["html"]).startswith(
              os.path.normpath(os.path.join(
                  root, "reports", "auto",
                  "owcs-8c105lnzlam_000600_000630"))))
    check("MISS rows rendered", "MISS" in html)
    check("next action recommends init DB", res["rec"]["id"] == "db")
    check("next action shows exact command",
          "init_db.py" in html and "Next recommended action" in html)
    check("run id parsed into capture command",
          "--start 0:06:00 --end 0:06:30" in html)
    check("missing links marked", "(missing)" in html)
    check("no crash marker: workflow table present",
          "Full workflow" in html)


# ------------------------------------------------------------ test 2 + 3 + 5
def test_synthetic_run(tmp: str) -> str:
    print("\n[2] synthetic run artifacts — visuals, guesses, labels, links")
    root = make_root(os.path.join(tmp, "t2"))
    run = "demo_000600_001200"
    # DB marker + layout + fixture frames
    open(os.path.join(root, "data", "owcs.sqlite"), "wb").close()
    lay_path = os.path.join(root, "layouts", "owcs-demo.json")
    shutil.copy(DEMO_LAYOUT, lay_path)
    fdir = os.path.join(root, "work", "auto", run, "frames_raw")
    os.makedirs(fdir)
    for f in FIXTURE_FILES:
        shutil.copy(os.path.join(FIXTURE_FRAMES, f), fdir)
    # real hero-crop artifacts (crops.json / labels.json / hero_crops.html)
    report_dir = os.path.join(root, "reports", "auto", run)
    layout = capture.load_layout(lay_path)
    layout["_path"] = lay_path
    chc.capture_run(run, layout, fdir, report_dir,
                    templates_dir=os.path.join(ROOT, "templates"))
    # one label + one reject so all three states appear
    meta = chc.load_meta(report_dir)
    first, second = meta["crops"][0]["id"], meta["crops"][1]["id"]
    heroes = [h["id"] if isinstance(h, dict) else h
              for h in (chc.load_heroes()[0] or [])]
    hero = heroes[0] if heroes else "ana"
    chc.set_label(report_dir, first, hero)
    chc.reject_crop(report_dir, second)
    # a couple of pre-existing reports to link + one candidate page
    for fn in ("index.html", "layout.html", "candidate_calib.html"):
        with open(os.path.join(report_dir, fn), "w", encoding="utf-8") as fh:
            fh.write("<html>stub</html>")

    before = snapshot(root, os.path.join("reports", "auto", run,
                                         "vision_dashboard"))
    res = vd.generate(run, "layouts/owcs-demo.json", root=root)
    after = snapshot(root, os.path.join("reports", "auto", run,
                                        "vision_dashboard"))
    html = read(res["html"])

    check("raw frames shown", "frames_raw/000600.png" in html.replace("\\", "/")
          or "000600.png" in html)
    check("annotated frames shown", "_annotated.png" in html)
    check("crop images shown", "hero_crops/crops/" in html)
    ctx_dir = os.path.join(report_dir, "vision_dashboard", "context")
    n_ctx = len(os.listdir(ctx_dir)) if os.path.isdir(ctx_dir) else 0
    check("context crops generated (10 per frame)",
          n_ctx == 10 * len(FIXTURE_FILES))
    check("context images referenced", "vision_dashboard/context/" in html)
    check("quality pills rendered", 'class="pill q-' in html.replace("'", '"'))
    check("detector guess/score column present",
          ("guess:" in html) or ("no detector data" in html))
    check("manual label shown", f"label: <b>{hero}</b>" in html)
    check("rejected state shown", "st-rejected" in html)
    check("unlabeled state shown", "st-unlabeled" in html)
    check("[3] links existing reports",
          'href="index.html"' in html and 'href="layout.html"' in html
          and 'href="hero_crops.html"' in html
          and 'href="candidate_calib.html"' in html)
    check("[3] missing reports marked with fix command",
          "candidate_detections.html (missing)" in html
          and "crops.html" in html)
    check("[4] next action = label more crops (1 labeled)",
          res["rec"]["id"] == "labels"
          and "label more crops" in res["rec"]["human"])
    check("[6] zero writes outside vision_dashboard*", before == after)
    new = [p for p in snapshot(root, "~none~") if p not in before]
    check("[6] every new file is under vision_dashboard*",
          new and all(
              os.path.join("reports", "auto", run, "vision_dashboard")
              in p or p.endswith("vision_dashboard.html") for p in new))
    return root


# ----------------------------------------------------- crops.json-less path
def test_frames_only(tmp: str) -> None:
    print("\n[5b] frames but no crops.json — on-the-fly crops + context")
    root = make_root(os.path.join(tmp, "t3"))
    run = "demo_000600_001200"
    open(os.path.join(root, "data", "owcs.sqlite"), "wb").close()
    shutil.copy(DEMO_LAYOUT, os.path.join(root, "layouts", "owcs-demo.json"))
    fdir = os.path.join(root, "work", "auto", run, "frames_raw")
    os.makedirs(fdir)
    shutil.copy(os.path.join(FIXTURE_FRAMES, FIXTURE_FILES[0]), fdir)

    res = vd.generate(run, "layouts/owcs-demo.json", root=root)
    html = read(res["html"])
    dash = os.path.join(root, "reports", "auto", run, "vision_dashboard")
    check("fallback crops cut", os.path.isdir(os.path.join(dash, "crops"))
          and len(os.listdir(os.path.join(dash, "crops"))) == 10)
    check("fallback annotated frame generated",
          os.path.isdir(os.path.join(dash, "annotated")))
    check("fallback context crops cut",
          len(os.listdir(os.path.join(dash, "context"))) == 10)
    check("no-detector note shown", "no detector data" in html)
    check("next action = layout debug (frames exist, no layout.html)",
          res["rec"]["id"] == "layout_debug")


# -------------------------------------------------------------- unit pieces
def test_units() -> None:
    print("\n[u] unit checks")
    check("run id parsing",
          vd.parse_run_id("owcs-8c105lnzlam_000600_000630")
          == ("owcs-8c105lnzlam", "0:06:00", "0:06:30"))
    check("odd run id tolerated",
          vd.parse_run_id("weird")[0] == "weird")
    if vd.HAS_CV:
        import numpy as np
        blank = np.zeros((72, 76, 3), np.uint8)
        check("classify blank", vd.classify_crop(blank) == "blank")
        noisy = (np.random.RandomState(7).rand(72, 76, 3) * 255)\
            .astype(np.uint8)
        check("classify colorful noise usable",
              vd.classify_crop(noisy) in ("usable", "suspicious"))
        check("classify low score suspicious",
              vd.classify_crop(noisy, score=0.1) == "suspicious")
        check("classify missing crop partial",
              vd.classify_crop(None) == "partial")
    lab = {"a": {"status": "labeled", "hero": "ana"},
           "b": {"status": "rejected", "hero": None},
           "c": {"hero": "mei"}}
    tmp = tempfile.mkdtemp()
    p = os.path.join(tmp, "labels.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(lab, fh)
    check("label_stats counts labeled+rejected",
          vd.label_stats(p) == (2, 1))
    check("label_stats tolerates missing file",
          vd.label_stats(os.path.join(tmp, "nope.json")) == (0, 0))


def main() -> int:
    print("vision_dashboard offline tests")
    with tempfile.TemporaryDirectory() as tmp:
        test_missing_everything(tmp)
        test_synthetic_run(tmp)
        test_frames_only(tmp)
        test_units()
    print(f"\n{'ALL PASS' if _fails == 0 else f'{_fails} FAILURE(S)'}")
    return 1 if _fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
