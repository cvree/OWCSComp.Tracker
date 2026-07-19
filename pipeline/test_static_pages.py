"""Static-page checks for the control-room redesign.

Offline, no browser: verifies the pages exist, share the design system
(style.css + ui.js), keep their data hooks (runs-list / src-list / api
fallback), and that generated report CSS stays on-theme. Guards against
the redesign breaking the pipeline's browser surfaces.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILS = 0


def check(name: str, ok: bool) -> None:
    global FAILS
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILS += 1


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def main() -> None:
    print("design system files:")
    css = read("assets/css/style.css")
    check("style.css has v2 tokens", "--st-ok" in css and "--mono" in css)
    check("style.css has pill/console/chip/timeline components",
          all(c in css for c in
              [".pill-v2", ".console", ".cmd-chip", ".timeline", ".hud",
               ".empty-v2", ".seg", ".flow"]))
    check("reduced motion respected",
          "prefers-reduced-motion" in css)
    check("ui.js exists with copy + status helpers",
          os.path.exists(os.path.join(ROOT, "assets/js/ui.js"))
          and "cmd-copy" in read("assets/js/ui.js")
          and "nav-status" in read("assets/js/ui.js"))

    print("motion layer (uplink):")
    motion = read("assets/js/motion.js")
    check("motion engine exists with reduced-motion + Save-Data guard",
          "prefers-reduced-motion" in motion and "saveData" in motion)
    check("engine layers: flow/entrance/reveals/decrypt/physics/ambience",
          all(k in motion for k in
              ["initFlow", "initEntrance", "watchReveal", "decrypt",
               "initMagnetic", "initTiltSpot", "atmosphere"]))
    check("Vanta is guarded by a WebGL probe + width + fallback net",
          "webglOK" in motion and "canvasNet" in motion
          and "VANTA.NET" in motion)
    check("inner scroll regions are excluded from smooth scroll",
          "data-lenis-prevent" in motion)
    check("every boot step is crash-isolated", "safely(" in motion)
    for v in ["three.min.js", "vanta.net.min.js", "ScrollTrigger.min.js"]:
        check(f"vendored {v} exists and is non-trivial",
              os.path.getsize(os.path.join(ROOT, "assets/vendor", v)) > 10000)
    check("style.css has the motion layer (progress bar + ambience holder)",
          ".scroll-progress" in css and "#cr-atmosphere" in css)

    print("pages load the shared system:")
    pages = ["index.html", "run.html", "runs.html", "sources.html",
             "admin.html", "teams.html",
             "prep.html", "fact-admin.html"]
    for p in pages:
        h = read(p)
        check(f"{p}: links style.css + ui.js",
              "assets/css/style.css" in h and "assets/js/ui.js" in h)
        check(f"{p}: loads the vendored motion stack",
              "assets/vendor/lenis.min.js" in h
              and "assets/vendor/gsap.min.js" in h
              and "assets/vendor/ScrollTrigger.min.js" in h
              and "assets/js/motion.js" in h)
    check("index.html opts into the Vanta tactical grid",
          'data-vanta="net"' in read("index.html")
          and "assets/vendor/three.min.js" in read("index.html")
          and "assets/vendor/vanta.net.min.js" in read("index.html"))

    # stats.html and matches.html were rebuilt as public fan pages — they
    # now load the public shell instead (fully covered by
    # test_public_site.py; asserted here too so a regression to neither
    # shell can slip through).
    for p in ["stats.html", "matches.html"]:
        h = read(p)
        check(f"{p}: rebuilt on the public shell",
              "assets/css/public.css" in h
              and "assets/js/public/core.js" in h)

    print("nav links resolve to real files:")
    import re
    for p in ["index.html", "run.html", "runs.html", "sources.html"]:
        h = read(p)
        hrefs = re.findall(r'href="([a-z\-]+\.html)"', h)
        missing = [x for x in set(hrefs)
                   if not os.path.exists(os.path.join(ROOT, x))]
        check(f"{p}: all page links exist ({len(set(hrefs))})", not missing)

    print("run.html keeps the control-room contract:")
    h = read("run.html")
    check("api endpoints wired",
          all(e in h for e in ["/api/ping", "/api/run", "/api/status",
                               "/api/cancel", "/api/test", "/api/sources"]))
    check("static fallback panel present",
          "pipeline/serve.py" in h and 'id="fallback"' in h)
    check("terminal fallback command visible",
          "run_owcs_auto.py" in h and 'id="fallbackCmd"' in h)
    check("live console + cancel + timeline present",
          'id="log"' in h and 'id="cancelBtn"' in h
          and 'id="stepTimeline"' in h)
    check("status pill states covered",
          all(s in read("assets/css/style.css") for s in
              [".pill-v2.ok", ".pill-v2.failed", ".pill-v2.timeout",
               ".pill-v2.partial", ".pill-v2.running"]))

    print("runs.html keeps autoRuns rendering:")
    h = read("runs.html")
    check("reads OWCS_DATA.autoRuns", "OWCS_DATA" in h and "autoRuns" in h)
    check("status filter + empty state",
          'data-f="failed"' in h and "empty-v2" in h)
    check("evidence links preserved",
          "layout.html" in h and "crops.html" in h
          and "regenEvidence" in h)

    print("sources.html keeps videoSources rendering:")
    h = read("sources.html")
    check("reads OWCS_DATA.videoSources", "videoSources" in h)
    check("copy slug + run command chips",
          "copy slug" in h and "run_owcs_auto.py --source" in h)

    print("generated report theme (python constants):")
    import run_owcs_auto as roa
    html = roa.build_report_html({"run": "theme-check", "steps": [],
                                  "ok": True}, [])
    check("run report is dark-themed + linked",
          "#060b15" in html and "Chakra Petch" in html
          and "runs.html" in html and "crops.html" in html)
    import build_layout_debug as bld
    check("layout viewer css is dark-themed", "#060b15" in bld._LAYOUT_HTML_CSS)
    import build_crop_report as bcr
    check("crop report css is dark-themed", "#060b15" in bcr._CSS)
    check("crop label colors on-palette",
          bcr._LABEL_COLORS["OK"] == "#2ebd6b"
          and bcr._LABEL_COLORS["NO-MATCH"] == "#ff5c64")

    print("primary real-VOD source is registered:")
    import json as _json
    srcs = _json.load(open(os.path.join(ROOT, "data", "sources",
                                        "video_sources.json")))["sources"]
    by_id = {s.get("id"): s for s in srcs if s.get("id")}
    check("owcs-8c105lnzlam source exists",
          "owcs-8c105lnzlam" in by_id)
    if "owcs-8c105lnzlam" in by_id:
        s = by_id["owcs-8c105lnzlam"]
        check("VOD source points at the right video + layout",
              "8C105lNzLAM" in (s.get("url") or "")
              and s.get("layout") == "layouts/owcs_8c105lnzlam.json")
        check("VOD layout file exists + is valid json",
              os.path.exists(os.path.join(ROOT, s["layout"]))
              and isinstance(_json.load(open(os.path.join(ROOT, s["layout"]))),
                             dict))

    print("run report shows the gated comp-promotion path:")
    html_det = roa.build_report_html(
        {"run": "r", "steps": [], "ok": True,
         "detection": {"status": "ok"}, "runStatus": "partial"}, [])
    check("promote section appears when detection ran",
          "Comp promotion" in html_det
          and "promote_detections.py" in html_det
          and "No comp is written" in html_det)
    html_skip = roa.build_report_html(
        {"run": "r", "steps": [], "ok": True,
         "detection": {"status": "skipped"}, "runStatus": "partial"}, [])
    check("no promote section when detection skipped",
          "Comp promotion" not in html_skip)

    print()
    if FAILS:
        print(f"FAIL — {FAILS} check(s) failed")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
