"""Asset-registry checks — every public team mark and hero face must
resolve to a verified real image OR an explicitly intentional designed
fallback. Never a broken image, never a guessed logo.

Offline, no browser: validates assets/data/asset_manifest.json against
both datasets (production export + demo fixture), the on-disk files, and
the client registry (assets/js/public/assets.js) wiring.
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAILS = 0


def check(name: str, ok: bool) -> None:
    global FAILS
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILS += 1


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def load_data(rel: str) -> dict:
    src = re.sub(r"/\*.*?\*/", "", read(rel), flags=re.S)
    return json.loads(src[src.index("{"): src.rindex("}") + 1])


def main() -> None:
    print("manifest exists + parses:")
    man_path = "assets/data/asset_manifest.json"
    check("asset_manifest.json exists",
          os.path.exists(os.path.join(ROOT, man_path)))
    man = json.loads(read(man_path))
    check("schema is assets.v1", man.get("meta", {}).get("schema") == "assets.v1")
    check("manifest has heroes + teams",
          isinstance(man.get("heroes"), dict) and isinstance(man.get("teams"), dict))

    prod = load_data("assets/data/public_data.v1.js")
    fix = load_data("assets/data/public_fixture.v1.js")

    print("every hero resolves to a real image or an intentional fallback:")
    hero_ids = {h["id"] for h in prod["heroes"]} | {h["id"] for h in fix["heroes"]}
    unresolved, broken = [], []
    for hid in sorted(hero_ids):
        e = man["heroes"].get(hid)
        if e is None:
            unresolved.append(hid)
            continue
        if e["reviewStatus"] == "verified-broadcast-crop":
            p = e.get("path")
            if not (p and os.path.exists(os.path.join(ROOT, p))):
                broken.append(hid)
            elif not (e.get("width") and e.get("height") and e.get("hash")):
                broken.append(hid + " (missing dims/hash)")
        elif e["reviewStatus"] != "fallback-monogram":
            unresolved.append(f"{hid} (unknown status {e['reviewStatus']})")
    check(f"all {len(hero_ids)} hero ids in the manifest ({len(unresolved)} unresolved)",
          not unresolved)
    check(f"verified portraits exist on disk with dims+hash ({len(broken)} broken)",
          not broken)

    print("portraitUrl paths in both datasets resolve to files:")
    missing = []
    for d in (prod, fix):
        for h in d["heroes"]:
            p = h.get("portraitUrl")
            if p and not os.path.exists(os.path.join(ROOT, p)):
                missing.append(p)
    check(f"all portraitUrl files exist ({len(missing)} missing)", not missing)

    print("every team resolves to a verified logo or an intentional crest:")
    team_ids = {t["id"] for t in prod["teams"]} | {t["id"] for t in fix["teams"]}
    unresolved, guessed = [], []
    for tid in sorted(team_ids):
        e = man["teams"].get(tid)
        if e is None:
            unresolved.append(tid)
            continue
        if e["reviewStatus"] == "verified-official":
            p = e.get("path")
            if not (p and os.path.exists(os.path.join(ROOT, p))
                    and e.get("source") and e.get("hash")):
                guessed.append(f"{tid} (verified without file/source/hash)")
        elif e["reviewStatus"] != "fallback-crest":
            unresolved.append(f"{tid} (unknown status {e['reviewStatus']})")
    check(f"all {len(team_ids)} team ids in the manifest ({len(unresolved)} unresolved)",
          not unresolved)
    check("no team logo without provenance (never guess)", not guessed)

    print("logoUrl in datasets only when a file backs it:")
    missing = []
    for d in (prod, fix):
        for t in d["teams"]:
            p = t.get("logoUrl")
            if p and not os.path.exists(os.path.join(ROOT, p)):
                missing.append(p)
    check(f"all logoUrl files exist ({len(missing)} missing)", not missing)

    print("client registry (assets.js):")
    reg = read("assets/js/public/assets.js")
    check("registry exists with crest + hero face + role icons",
          "teamCrest" in reg and "heroFace" in reg and "roleIcon" in reg)
    check("crest is inline SVG (cannot 404)",
          "<svg" in reg and "viewBox" in reg)
    check("registry documents the never-guess rule",
          "never a guess" in reg.lower() or "never a guessed" in reg.lower())
    check("verified images keep intrinsic dimensions (no layout shift)",
          'width=' in reg and 'height=' in reg)
    core = read("assets/js/public/core.js")
    check("core.js team plates delegate to the registry",
          "P.assets" in core and "teamMark" in core)
    check("core.js hero tiles delegate to the registry",
          "heroFace" in core)
    check("broken-image fallback hook still present",
          "imgFallback" in core or "img-fallback" in core)

    print("pages load the registry:")
    pages = [p for p in os.listdir(ROOT)
             if p.endswith(".html")
             and "assets/js/public/core.js" in read(p)]
    unwired = [p for p in pages if "assets/js/public/assets.js" not in read(p)]
    check(f"every core.js page also loads assets.js ({len(unwired)} unwired: {unwired})",
          not unwired)

    print("manifest builder is committed + rerunnable:")
    check("pipeline/build_asset_manifest.py exists",
          os.path.exists(os.path.join(ROOT, "pipeline/build_asset_manifest.py")))
    check("team candidate sources are documented (fetch step for a "
          "network-enabled machine)",
          os.path.exists(os.path.join(ROOT, "assets/data/team_asset_sources.json")))

    print()
    if FAILS:
        print(f"FAILED: {FAILS} check(s)")
        sys.exit(1)
    print("all asset checks passed")


if __name__ == "__main__":
    main()
