#!/usr/bin/env python3
"""
test_ocr_hud.py — offline, deterministic tests for ocr_hud.py.

No OCR engine, no model download, no network: a fake read_fn is injected
(same injectable pattern as the yt-dlp fakes elsewhere in the suite).
Synthetic 1280x720 frames are drawn with cv2 so slot geometry matches the
demo layout. Verifies: scene classification/ignore flags, team-name zones,
hero alias normalization (exact + fuzzy), slot-contamination flagging,
stable-comp tally, JSON/HTML outputs, missing-everything behavior, and the
no-writes guarantee outside ocr_hud*.
"""
from __future__ import annotations
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np                  # noqa: E402
import cv2                          # noqa: E402
import capture                      # noqa: E402
import ocr_hud                      # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEMO_LAYOUT = os.path.join(ROOT, "layouts", "owcs-demo.json")
ALIASES = os.path.join(ROOT, "data", "heroes_aliases.json")

_fails = 0


def check(name: str, ok: bool) -> None:
    global _fails
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        _fails += 1


def make_root(tmp: str) -> str:
    root = os.path.join(tmp, "repo")
    for d in ("data", "layouts", "reports", "work"):
        os.makedirs(os.path.join(root, d))
    shutil.copy(ALIASES, os.path.join(root, "data", "heroes_aliases.json"))
    shutil.copy(DEMO_LAYOUT, os.path.join(root, "layouts", "owcs-demo.json"))
    return root


def blank_frame(w=1280, h=720):
    return np.full((h, w, 3), 30, np.uint8)


def item(text, x, y, w=80, h=20, conf=0.9):
    return {"text": text, "conf": conf, "box": [x, y, w, h]}


def snapshot(root: str) -> dict:
    out = {}
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            r = os.path.relpath(p, root)
            if "ocr_hud" in r:
                continue
            st = os.stat(p)
            out[r] = (st.st_size, st.st_mtime_ns)
    return out


# ------------------------------------------------------------- unit: aliases
def test_alias_normalization() -> None:
    print("\n[u1] hero alias normalization")
    al = ocr_hud.load_aliases(ALIASES)
    cases = [("BAPTISTE", "bap"), ("bap", "bap"), ("D.Va", "dva"),
             ("DVA", "dva"), ("Reinhardt", "rein"), ("REINHART", "rein"),
             ("WIDOWMAKER", "widow"), ("Lúcio", "lucio"),
             ("SOLDIER: 76", "soldier"), ("Wrecking Ball", "ball"),
             ("JUNKER QUEEN", "jq"), ("Sojurn", "sojourn")]
    for raw, want in cases:
        m = ocr_hud.match_hero(raw, al)
        check(f"'{raw}' -> {want}", m["hero"] == want and m["quality"] > 0)
    check("garbage -> None",
          ocr_hud.match_hero("XQZ99PLAYER", al)["hero"] is None)
    check("short junk -> None", ocr_hud.match_hero("A", al)["hero"] is None)

    # truncations / OCR typos (method-aware)
    for raw, want, meth in [("WID", "widow", ("exact", "prefix")),
                            ("WIN", "winston", ("exact", "prefix")),
                            ("WINST", "winston", ("prefix",)),
                            ("KIRIK", "kiriko", ("prefix",)),
                            ("0VA", "dva", ("exact",)),
                            ("LUC1O", "lucio", ("exact",)),
                            ("WINSTQN", "winston", ("fuzzy",))]:
        m = ocr_hud.match_hero(raw, al)
        check(f"typo '{raw}' -> {want} via {'|'.join(meth)}",
              m["hero"] == want and m["method"] in meth)
    # ambiguity: margin + multi-prefix must refuse, with a naming reason
    amb = {"alias_map": {"WINSTONA": "winston", "WINSTONB": "widow"},
           "names": {}, "kw_cats": {}, "ignore": []}
    m = ocr_hud.match_hero("WINSTONC", amb)
    check("ambiguous fuzzy refused with reason",
          m["hero"] is None and "ambiguous" in m["reason"])
    m2 = ocr_hud.match_hero("WINSTON", amb)
    check("ambiguous prefix refused",
          m2["hero"] is None and "ambiguous prefix" in m2["reason"])


# --------------------------------------------------------- unit: scene class
def test_scene_classification() -> None:
    print("\n[u2] scene classification")
    al = ocr_hud.load_aliases(ALIASES)
    c = lambda items: ocr_hud.classify_frame(items, al)[0]     # noqa: E731
    r = lambda items: ocr_hud.classify_frame(items, al)[2]     # noqa: E731
    check("plain gameplay", c([item("PharahMain42", 100, 30)]) == "gameplay")
    check("REPLAY -> replay", c([item("REPLAY", 600, 300)]) == "replay")
    check("HIGHLIGHTS -> highlight",
          c([item("HIGHLIGHTS", 600, 300)]) == "highlight")
    check("POTG -> highlight",
          c([item("PLAY OF THE GAME", 500, 300)]) == "highlight")
    check("VICTORY -> intermission",
          c([item("VICTORY", 600, 300)]) == "intermission")
    check("STARTING SOON -> intermission",
          c([item("Starting Soon", 500, 300)]) == "intermission")
    check("PAUSED -> unknown", c([item("PAUSED", 600, 300)]) == "unknown")
    check("reason names the keyword", "PAUSED" in r([item("PAUSED", 0, 0)]))
    # word-boundary: no raw-substring false positives
    check("ROUNDTWO tag stays gameplay",
          c([item("ROUNDTWO", 100, 30)]) == "gameplay")
    check("GROUNDED stays gameplay",
          c([item("GROUNDED", 100, 30)]) == "gameplay")
    check("REPLAYER stays gameplay",
          c([item("xX_REPLAYER_Xx", 100, 30)]) == "gameplay")
    check("'ROUND 2' (word) -> unknown",
          c([item("ROUND 2", 100, 30)]) == "unknown")
    check("replay beats caution when both present",
          c([item("REPLAY", 0, 0), item("PAUSED", 0, 40)]) == "replay")


# ------------------------------------------------------- full synthetic run
def test_synthetic_run(tmp: str) -> None:
    print("\n[3] synthetic run — zones, slots, stability, outputs, no-writes")
    root = make_root(os.path.join(tmp, "t3"))
    run = "synth_000000_000030"
    fdir = os.path.join(root, "work", "auto", run, "frames_raw")
    os.makedirs(fdir)
    for fn in ("000000.png", "000010.png", "000020.png"):
        cv2.imwrite(os.path.join(fdir, fn), blank_frame())

    layout = capture.load_layout(os.path.join(root, "layouts",
                                              "owcs-demo.json"))
    sb = ocr_hud.slot_boxes(layout, 1280, 720)
    a1, b1 = sb[0], sb[5]
    check("10 slot boxes from demo layout", len(sb) == 10)

    def fake_read_factory():
        """frame 1+2: gameplay w/ teams + hero text; frame 3: REPLAY."""
        calls = {"n": 0}

        def read(frame):
            calls["n"] += 1
            if calls["n"] == 3:
                return [item("REPLAY", 560, 330, 160, 60)]
            out = [
                item("CRAZY RACCOON", 20, 20, 200, 24),        # team_left zone
                item("ZETA DIVISION", 1000, 20, 200, 24),      # team_right
                # hero text just under slot a1 / b1 (inside text_zone)
                item("WINSTON", a1["box"][0],
                     a1["box"][1] + a1["box"][3] + 4, a1["box"][2], 16),
                item("KIRIKO", b1["box"][0],
                     b1["box"][1] + b1["box"][3] + 4, b1["box"][2], 16),
                # readable text INSIDE slot a2's box = contaminated layout box
                item("ROUNDTWO", sb[1]["box"][0] + 2, sb[1]["box"][1] + 2,
                     sb[1]["box"][2] - 4, 16),
            ]
            if calls["n"] == 2:                      # a1 stable across 2 frames
                return out
            return out
        return read

    before = snapshot(root)
    res = ocr_hud.run_diagnostics(run, "layouts/owcs-demo.json", root=root,
                                  engine="fake", read_fn=fake_read_factory())
    after = snapshot(root)

    data = json.load(open(res["json"], encoding="utf-8"))
    html = open(res["html"], encoding="utf-8").read()
    f1 = data["frames"][0]["analysis"]
    f3 = data["frames"][2]["analysis"]

    check("json + html written", os.path.isfile(res["json"])
          and os.path.isfile(res["html"]))
    check("candidate/promoted honesty markers",
          data["candidate"] is True and data["promoted"] is False)
    check("team left detected", (f1["team_left"] or {}).get("raw")
          == "CRAZY RACCOON")
    check("team right detected", (f1["team_right"] or {}).get("raw")
          == "ZETA DIVISION")
    check("hero candidate a1 = winston", any(
        s["slot"] == "a1" and s["hero_candidate"] == "winston"
        for s in f1["slots"]))
    check("hero candidate b1 = kiriko", any(
        s["slot"] == "b1" and s["hero_candidate"] == "kiriko"
        for s in f1["slots"]))
    check("contaminated slot a2 flagged", "a2" in f1["contaminated_slots"])
    check("frame 3 classed replay + ignored",
          f3["scene"] == "replay" and f3["ignore"] is True)
    check("ignore reason recorded",
          "REPLAY" in (f3["ignore_reason"] or ""))
    check("OCR purposes grouped", {i["purpose"] for i in f1["ocr_purposes"]}
          >= {"team", "hero", "other"})
    check("verdict present with metrics",
          isinstance(data["verdict"], dict)
          and data["verdict"]["metrics"].get("frames") == 3)
    check("html shows what-to-do verdict", "What to do next" in html)
    check("zones default source", f1["zones_source"] == "default")
    check("stable tally excludes ignored frame",
          data["stable"]["frames_used"] == 2
          and data["stable"]["tally"]["a"].get("winston") == 2)
    check("annotated pngs written",
          len(os.listdir(os.path.join(root, "reports", "auto", run,
                                      "ocr_hud"))) == 3)
    check("html: ignore banner rendered", "IGNORED for comps" in html)
    check("html: hero match method shown", ">word<" in html
          or ">exact<" in html)
    check("html: TEXT IN BOX flag rendered", "TEXT IN BOX" in html)
    check("html: candidates-only disclaimer", "CANDIDATES ONLY" in html)
    check("html links vision dashboard", "vision_dashboard.html" in html)
    check("no writes outside ocr_hud*", before == after)


# ---------------------------------------- layout ocr_zones + verdict logic
def test_zones_and_verdicts(tmp: str) -> None:
    print("\n[5] layout ocr_zones override + verdict branches")
    root = make_root(os.path.join(tmp, "t5"))
    run = "zones_000000_000010"
    fdir = os.path.join(root, "work", "auto", run, "frames_raw")
    os.makedirs(fdir)
    cv2.imwrite(os.path.join(fdir, "000000.png"), blank_frame())

    # move team_left zone to bottom-left via layout ocr_zones (+ one invalid
    # entry that must be rejected, keeping its default)
    lay_p = os.path.join(root, "layouts", "owcs-demo.json")
    lay = json.load(open(lay_p, encoding="utf-8"))
    lay["ocr_zones"] = {"team_left": [0.0, 0.85, 0.3, 0.14],
                        "team_right": [2.0, -1, 0, 0]}       # invalid
    zoned = os.path.join(root, "layouts", "zoned.json")
    json.dump(lay, open(zoned, "w", encoding="utf-8"))
    lay_before = open(zoned, encoding="utf-8").read()

    def read_bottom(frame):
        return [item("CRAZY RACCOON", 20, 650, 200, 24),   # bottom-left now
                item("ZETA DIVISION", 1000, 20, 200, 24),  # default right
                item("filler one", 400, 400), item("filler two", 500, 400)]

    res = ocr_hud.run_diagnostics(run, zoned, root=root, engine="fake",
                                  read_fn=read_bottom)
    data = json.load(open(res["json"], encoding="utf-8"))
    a = data["frames"][0]["analysis"]
    check("custom team_left zone catches bottom text",
          (a["team_left"] or {}).get("raw") == "CRAZY RACCOON")
    check("invalid zone rejected, default kept",
          (a["team_right"] or {}).get("raw") == "ZETA DIVISION")
    check("zones_source = layout", a["zones_source"] == "layout ocr_zones")
    check("layout file untouched (no auto edits)",
          open(zoned, encoding="utf-8").read() == lay_before)
    check("verdict = gating (teams yes, heroes no)",
          data["verdict"]["verdict"] == "ocr-gating")

    # no-signal branch: almost no OCR items
    res2 = ocr_hud.run_diagnostics(run, zoned, root=root, engine="fake",
                                   read_fn=lambda f: [item("x", 5, 5)])
    d2 = json.load(open(res2["json"], encoding="utf-8"))
    check("verdict = no-signal on sparse OCR",
          d2["verdict"]["verdict"] == "no-signal")

    # fix-layout branch: text inside many slot boxes
    layout = capture.load_layout(lay_p)
    sb = ocr_hud.slot_boxes(layout, 1280, 720)

    def read_contam(frame):
        out = [item(f"UITEXT{i}", b["box"][0] + 2, b["box"][1] + 2,
                    max(20, b["box"][2] - 4), 14)
               for i, b in enumerate(sb[:6])]
        return out

    res3 = ocr_hud.run_diagnostics(run, zoned, root=root, engine="fake",
                                   read_fn=read_contam)
    d3 = json.load(open(res3["json"], encoding="utf-8"))
    check("verdict = fix-layout on contaminated boxes",
          d3["verdict"]["verdict"] == "fix-layout")

    # ocr-heroes branch: hero text under most slots
    def read_heroes(frame):
        heroes = ["WINSTON", "DVA", "KIRIKO", "ANA", "MEI",
                  "SIGMA", "TRACER", "GENJI", "LUCIO", "JUNO"]
        return [item(h, b["box"][0], b["box"][1] + b["box"][3] + 4,
                     b["box"][2], 16)
                for h, b in zip(heroes, sb)] + [item("f", 5, 700)]

    res4 = ocr_hud.run_diagnostics(run, zoned, root=root, engine="fake",
                                   read_fn=read_heroes)
    d4 = json.load(open(res4["json"], encoding="utf-8"))
    check("verdict = ocr-heroes on rich hero text",
          d4["verdict"]["verdict"] == "ocr-heroes")
    check("dashboard surfaces OCR verdict", _dashboard_shows_verdict(
        root, run))


def _dashboard_shows_verdict(root: str, run: str) -> bool:
    import vision_dashboard as vd
    res = vd.generate(run, "layouts/owcs-demo.json", root=root)
    html = open(res["html"], encoding="utf-8").read()
    return "OCR verdict" in html and "OCR useful for hero detection" in html


# ------------------------------------------------- missing-everything cases
def test_missing(tmp: str) -> None:
    print("\n[4] missing files / engine — graceful, no crash")
    root = make_root(os.path.join(tmp, "t4"))
    run = "ghost_000000_000030"
    res = ocr_hud.run_diagnostics(run, None, root=root, engine="fake",
                                  read_fn=lambda f: [])
    html = open(res["html"], encoding="utf-8").read()
    check("renders with zero frames", res["frames"] == 0)
    check("says capture needed", "no frames found" in html)
    check("says layout needed", "pass --layout" in html)

    # unavailable engine (easyocr almost certainly absent in CI sandbox):
    res2 = ocr_hud.run_diagnostics(run, None, root=root, engine="easyocr")
    note_ok = True  # if easyocr IS installed, note is empty — both fine
    if res2["engineNote"]:
        note_ok = "pip install easyocr" in res2["engineNote"]
    check("missing engine -> install hint, still renders", note_ok
          and os.path.isfile(res2["html"]))
    res3 = ocr_hud.run_diagnostics(run, None, root=root, engine="none")
    d3 = json.load(open(res3["json"], encoding="utf-8"))
    check("engine=none renders + no-signal verdict",
          os.path.isfile(res3["html"])
          and d3["verdict"]["verdict"] == "no-signal")


def main() -> int:
    print("ocr_hud offline tests")
    with tempfile.TemporaryDirectory() as tmp:
        test_alias_normalization()
        test_scene_classification()
        test_synthetic_run(tmp)
        test_zones_and_verdicts(tmp)
        test_missing(tmp)
    print(f"\n{'ALL PASS' if _fails == 0 else f'{_fails} FAILURE(S)'}")
    return 1 if _fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
