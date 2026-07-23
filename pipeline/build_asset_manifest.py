#!/usr/bin/env python3
"""Build assets/data/asset_manifest.json — the single audited registry of
every public-facing team and hero image.

Honesty rules (enforced by pipeline/test_assets.py):
  * Only assets that exist on disk are listed with a file path.
  * Every entry records where it came from (source), when, its dimensions,
    a content hash, and a review status.
  * A hero without a verified real portrait gets an explicit
    "fallback-monogram" entry — the site renders a designed crest/monogram,
    never a broken image and never a guessed picture.
  * Team logos are NEVER guessed. Until a verified official mark is
    downloaded (see fetch notes below), teams carry "fallback-crest"
    entries with the candidate official sources documented for the
    network-enabled fetch step.

Fetching verified marks (run on a machine with open network):
  1. Confirm the official source listed under teams[].candidateSources.
  2. Download the official mark, place it at
     assets/img/teams/<team-id>/logo.png (transparent background).
  3. Re-run this script — it will pick the file up, hash it, measure it
     and flip the review status to "verified-official" with the source
     URL you record in assets/data/team_asset_sources.json.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEROES_DIR = os.path.join(ROOT, "assets", "img", "heroes")
TEAMS_DIR = os.path.join(ROOT, "assets", "img", "teams")
OUT_PATH = os.path.join(ROOT, "assets", "data", "asset_manifest.json")
SOURCES_PATH = os.path.join(ROOT, "assets", "data", "team_asset_sources.json")
PUBLIC_DATA = os.path.join(ROOT, "assets", "data", "public_data.v1.js")
FIXTURE_DATA = os.path.join(ROOT, "assets", "data", "public_fixture.v1.js")


def _load_public(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        src = f.read()
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return json.loads(src[src.index("{"): src.rindex("}") + 1])


def _png_size(path: str) -> tuple[int, int] | None:
    """Read PNG dimensions from the IHDR chunk (no image lib needed)."""
    try:
        with open(path, "rb") as f:
            head = f.read(26)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        w = int.from_bytes(head[16:20], "big")
        h = int.from_bytes(head[20:24], "big")
        return (w, h)
    except OSError:
        return None


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def build() -> dict:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    prod = _load_public(PUBLIC_DATA)

    # ---- heroes ---------------------------------------------------------
    portrait_manifest = {}
    pm_path = os.path.join(HEROES_DIR, "manifest.json")
    if os.path.exists(pm_path):
        with open(pm_path, encoding="utf-8") as f:
            portrait_manifest = json.load(f).get("heroes", {})

    heroes_out = {}
    for h in prod.get("heroes", []):
        hid = h["id"]
        png = os.path.join(HEROES_DIR, f"{hid}.png")
        if os.path.exists(png):
            size = _png_size(png)
            prov = portrait_manifest.get(hid, {})
            heroes_out[hid] = {
                "assetType": "portrait",
                "path": f"assets/img/heroes/{hid}.png",
                "source": prov.get("file", "harvested broadcast crop"),
                "sourceKind": "broadcast-crop",
                "attribution": ("Cropped from an official OWCS broadcast "
                                "frame by this repo's CV pipeline; hero "
                                "identity evidenced in the labeling log."),
                "license": ("Broadcast-derived thumbnail used for "
                            "identification in a non-commercial fan "
                            "project; Overwatch and its heroes are "
                            "trademarks of Blizzard Entertainment, Inc."),
                "retrievedAt": prov.get("generatedAt") or now,
                "width": size[0] if size else None,
                "height": size[1] if size else None,
                "hash": _sha256(png),
                "reviewStatus": "verified-broadcast-crop",
            }
        else:
            heroes_out[hid] = {
                "assetType": "portrait-fallback",
                "path": None,
                "source": None,
                "sourceKind": "designed-fallback",
                "attribution": ("Intentional designed monogram — no "
                                "verified real portrait has been harvested "
                                "for this hero yet."),
                "license": None,
                "retrievedAt": now,
                "width": None,
                "height": None,
                "hash": None,
                "reviewStatus": "fallback-monogram",
            }

    # ---- teams ----------------------------------------------------------
    team_sources = {}
    if os.path.exists(SOURCES_PATH):
        with open(SOURCES_PATH, encoding="utf-8") as f:
            team_sources = json.load(f).get("teams", {})

    fixture = _load_public(FIXTURE_DATA)
    all_teams = {t["id"]: t for t in prod.get("teams", [])}
    for t in fixture.get("teams", []):
        all_teams.setdefault(t["id"], dict(t, _demo=True))

    teams_out = {}
    for tid, t in sorted(all_teams.items()):
        src = team_sources.get(tid, {})
        logo = os.path.join(TEAMS_DIR, tid, "logo.png")
        if os.path.exists(logo) and src.get("sourceUrl"):
            size = _png_size(logo)
            teams_out[tid] = {
                "assetType": "logo",
                "path": f"assets/img/teams/{tid}/logo.png",
                "source": src["sourceUrl"],
                "sourceKind": src.get("sourceKind", "official"),
                "attribution": src.get("attribution",
                                       f"Official {t['name']} mark."),
                "license": src.get("license",
                                   "Team mark used nominatively to "
                                   "identify the organization in a "
                                   "non-commercial fan project."),
                "retrievedAt": src.get("retrievedAt", now),
                "width": size[0] if size else None,
                "height": size[1] if size else None,
                "hash": _sha256(logo),
                "reviewStatus": "verified-official",
            }
        else:
            teams_out[tid] = {
                "assetType": "crest-fallback",
                "path": None,
                "source": None,
                "sourceKind": "designed-fallback",
                "attribution": ("Intentional designed crest (team code "
                                "monogram) — no verified official mark has "
                                "been retrieved and reviewed yet. Never a "
                                "guessed logo."),
                "license": None,
                "candidateSources": src.get("candidateSources", []),
                "retrievedAt": now,
                "width": None,
                "height": None,
                "hash": None,
                "reviewStatus": "fallback-crest",
            }

    return {
        "meta": {
            "schema": "assets.v1",
            "generatedAt": now,
            "note": ("Audited registry of public team/hero imagery. "
                     "Entries with reviewStatus starting with 'fallback' "
                     "are INTENTIONAL designed fallbacks — the site never "
                     "shows a broken or guessed image."),
        },
        "heroes": heroes_out,
        "teams": teams_out,
    }


def main() -> None:
    manifest = build()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, ensure_ascii=False)
        f.write("\n")
    heroes = manifest["heroes"]
    real = sum(1 for h in heroes.values()
               if h["reviewStatus"] == "verified-broadcast-crop")
    teams = manifest["teams"]
    verified = sum(1 for t in teams.values()
                   if t["reviewStatus"] == "verified-official")
    print(f"Wrote {os.path.relpath(OUT_PATH, ROOT)}: "
          f"{real}/{len(heroes)} heroes with verified portraits, "
          f"{verified}/{len(teams)} teams with verified logos "
          f"(the rest are intentional designed fallbacks).")


if __name__ == "__main__":
    main()
