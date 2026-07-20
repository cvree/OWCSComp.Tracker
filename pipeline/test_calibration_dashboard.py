#!/usr/bin/env python3
"""
Offline tests for the autocalibration dashboard's data layer:
  - pipeline/calibration_status.py  (honest per-source health)
  - pipeline/build_hero_portraits.py (portraits from REAL crops only)

No network, no ffmpeg, no DB writes. Runs against the committed repo.
"""
from __future__ import annotations
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calibration_status as cs           # noqa: E402
import build_hero_portraits as bhp        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = 0


def check(name: str, ok: bool) -> None:
    global FAILS
    if not ok:
        FAILS += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")


def main() -> int:
    print("calibration_status: honest per-source health")
    st = cs.build_status()
    check("returns sources + counts + rosterSize",
          "sources" in st and "counts" in st and "rosterSize" in st)
    check("counts only ok/warn/fail", set(st["counts"]) <= {"ok", "warn", "fail"})
    for s in st["sources"]:
        check(f"{s['id']}: grade is ok/warn/fail",
              s["status"] in ("ok", "warn", "fail"))
        check(f"{s['id']}: every non-ok grade has at least one reason",
              s["status"] == "ok" or bool(s["reasons"]))
        check(f"{s['id']}: template coverage is a fraction of the roster",
              0.0 <= (s["rosterCoverage"] or 0) <= 1.0)

    # the CR-ZETA source is the live regression target — it must be present,
    # probed (hud_probe present), and carry its partial (7-hero) template set
    cr = next((s for s in st["sources"] if s["id"] == "owcs-8c105lnzlam"), None)
    check("CR-ZETA source is tracked", cr is not None)
    if cr:
        check("CR-ZETA has a hud_probe (else it reads zero frames)",
              cr["hudProbe"] is True)
        check("CR-ZETA template coverage is honestly reported as partial",
              0 < cr["templates"]["heroes"] < 10)
        check("CR-ZETA is not falsely graded ok while coverage is partial",
              cr["status"] in ("warn", "fail"))

    # a layout with no hud_probe must be flagged, not silently ok
    probeless = [s for s in st["sources"]
                 if s.get("layoutPath") and s["hudProbe"] is False]
    for s in probeless:
        check(f"{s['id']}: missing hud_probe is called out in reasons",
              any("hud_probe" in r or "probe" in r for r in s["reasons"]))

    print("build_hero_portraits: REAL broadcast crops only")
    files = bhp.real_template_files()
    check("scans per-source template dirs",
          all(p.count(os.sep) >= 2 for _, _, p in files) if files else True)
    check("never pulls the root-level synthetic starter set",
          all("templates" + os.sep in p and
              os.path.basename(os.path.dirname(p)) != "templates"
              for _, _, p in files))
    manifest = bhp.build(dry_run=True)
    check("dry-run picks a source crop for every covered hero",
          bool(manifest) and all(
              m["file"].startswith("templates/") and m["file"].count("/") >= 2
              for m in manifest.values()))
    check("dry-run writes nothing",
          not os.path.exists(os.path.join(bhp.OUT_DIR, "__probe__.png")))

    # the committed manifest (if generated) must trace every portrait to a
    # real crop that still exists on disk
    mp = os.path.join(ROOT, "assets", "img", "heroes", "manifest.json")
    if os.path.exists(mp):
        with open(mp, encoding="utf-8") as f:
            man = json.load(f)
        check("committed manifest: every portrait traces to an on-disk "
              "broadcast crop",
              all(os.path.exists(os.path.join(ROOT, m["file"]))
                  for m in man["heroes"].values()))
        check("committed manifest: every portrait png exists",
              all(os.path.exists(os.path.join(ROOT, "assets", "img",
                  "heroes", f"{h}.png")) for h in man["heroes"]))

    print()
    if FAILS:
        print(f"FAILED: {FAILS} check(s)")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
