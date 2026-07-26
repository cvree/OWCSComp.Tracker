#!/usr/bin/env python3
"""
build_hero_official_assets.py — official Blizzard hero presentation assets
(Phase D3).

Distinct from build_hero_portraits.py (real BROADCAST crops, evidence that a
hero was actually played — untouched by this script). This script fetches
each hero's own official page at overwatch.blizzard.com/en-us/heroes/<slug>/
— Blizzard's own marketing site, the single unambiguous authoritative source
for "what does this hero look like" — and derives three presentation
variants from the ONE hero-specific splash image found there:

  * artwork  — the fetched image as-is (full official composition)
  * portrait — a center-square crop of it (a derivation, not a separate
               Blizzard-provided asset — documented as such)
  * icon     — a small square resize of the portrait

Every hero page also carries several GENERIC images shared across the whole
site (an "Outro" banner, "Origin_Story", "Perks", "Related_Heroes" panels —
identical content-ids on every hero's page). The one hero-specific image is
found by matching the URL's own embedded name against the hero's real name
(diacritics stripped, alnum-only, case-insensitive) — never a guess, never a
positional/first-match assumption. A hero whose page doesn't yield a
confident match is left unresolved (no image written) rather than picking
the wrong asset.

Fetched bytes are cached once in a gitignored local staging dir
(data/asset_staging/heroes/) — reruns without --force reuse the cache and
never re-hit the network. Nothing here is ever hotlinked to the live site:
only the derived local files under assets/img/heroes/official/<id>/ are
ever referenced by the public pages.

Usage:
  python3 pipeline/build_hero_official_assets.py             # fetch + build
  python3 pipeline/build_hero_official_assets.py --dry-run   # report only
  python3 pipeline/build_hero_official_assets.py --hero-id zarya
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

OUT_DIR = os.path.join(db.REPO_ROOT, "assets", "img", "heroes", "official")
STAGING_DIR = os.path.join(db.REPO_ROOT, "data", "asset_staging", "heroes")
MANIFEST_PATH = os.path.join(OUT_DIR, "manifest.json")
PORTRAIT_SIZE = 256
ICON_SIZE = 64
USER_AGENT = "Mozilla/5.0 (compatible; owcs-comp-tracker/1.0; non-commercial fan site)"

# DB hero id -> Blizzard's own URL slug at overwatch.blizzard.com/en-us/heroes/.
# Verified live (200 OK, page lists this exact set) 2026-07-25 — every one of
# the 52 heroes in this repo's roster is a real, currently-live Overwatch hero.
HERO_SLUGS: dict[str, str] = {
    "anran": "anran", "ashe": "ashe", "bastion": "bastion", "cass": "cassidy",
    "echo": "echo", "emre": "emre", "freja": "freja", "genji": "genji",
    "hanzo": "hanzo", "junkrat": "junkrat", "mei": "mei", "pharah": "pharah",
    "reaper": "reaper", "shion": "shion", "sierra": "sierra",
    "sojourn": "sojourn", "soldier": "soldier-76", "sombra": "sombra",
    "sym": "symmetra", "torb": "torbjorn", "tracer": "tracer",
    "vendetta": "vendetta", "venture": "venture", "widow": "widowmaker",
    "ana": "ana", "bap": "baptiste", "brig": "brigitte", "illari": "illari",
    "jetcat": "jetpack-cat", "juno": "juno", "kiriko": "kiriko",
    "lw": "lifeweaver", "lucio": "lucio", "mercy": "mercy",
    "mizuki": "mizuki", "moira": "moira", "wuyang": "wuyang", "zen": "zenyatta",
    "dva": "dva", "domina": "domina", "doomfist": "doomfist",
    "hazard": "hazard", "jq": "junker-queen", "mauga": "mauga",
    "orisa": "orisa", "ram": "ramattra", "rein": "reinhardt",
    "hog": "roadhog", "sigma": "sigma", "winston": "winston",
    "ball": "wrecking-ball", "zarya": "zarya",
}

# Pre-release development codenames Blizzard's own CMS asset filenames
# still use post-launch, evidenced by independent reporting at reveal time
# (e.g. "Overwatch 2 New Characters: Freja, Aqua & Hero 45 Guide" covering
# Wuyang's then-unrevealed codename) — never a guess from the page itself,
# only added when corroborated by an outside source at the time of reveal.
KNOWN_CODENAMES: dict[str, str] = {
    "wuyang": "Aqua",
}

_SPLASH_RE = re.compile(
    r'https://blz-contentstack-images\.akamaized\.net/v3/assets/[a-z0-9]+/'
    r'[a-z0-9]+/[a-z0-9]+/960_([A-Za-z0-9_]+)\.(jpg|png)')


def _normalize(s: str) -> str:
    """Diacritic-stripped, alnum-only, lowercase — for matching a hero's
    real name against an asset filename regardless of accents/spacing."""
    nfkd = unicodedata.normalize("NFKD", s)
    ascii_only = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", ascii_only.lower())


def _now_iso(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.timezone.utc)).replace(microsecond=0).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def hero_page_url(slug: str) -> str:
    return f"https://overwatch.blizzard.com/en-us/heroes/{slug}/"


def fetch_url(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


_REVISION_SUFFIX_RE = re.compile(r"_v?\d+$", re.IGNORECASE)


def _strip_revision_suffix(name: str) -> str:
    """Blizzard's CMS appends a revision tag to a re-uploaded asset (seen
    live: 'Juno_v2', 'Mei_02') without changing the hero it depicts — this
    strips a trailing _v2/_02/_3 style suffix before matching. General CMS
    versioning, not a hero-specific rule."""
    return _REVISION_SUFFIX_RE.sub("", name)


def find_splash_url(html: str, hero_name: str) -> str | None:
    """The one hero-specific 960px splash image URL on this page, matched by
    its own embedded name against hero_name — never the first/generic hit.
    Tries a direct match first (so a real digit in the hero's own name, e.g.
    'Soldier: 76', is never mistaken for a CMS revision suffix); only falls
    back to stripping a trailing _v2/_02 style suffix if the direct match
    fails."""
    target = _normalize(hero_name)
    candidates = list(_SPLASH_RE.finditer(html))
    for m in candidates:
        if _normalize(m.group(1)) == target:
            return m.group(0)
    for m in candidates:
        if _normalize(_strip_revision_suffix(m.group(1))) == target:
            return m.group(0)
    return None


def _staged_path(hero_id: str, url: str) -> str:
    ext = os.path.splitext(url.split("?")[0])[1] or ".jpg"
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    return os.path.join(STAGING_DIR, f"{hero_id}-{digest}{ext}")


def fetch_hero_splash(hero_id: str, slug: str, hero_name: str, *,
                      force: bool = False) -> dict:
    """Resolve + cache-fetch one hero's splash art. Returns a dict with
    either {'ok': True, 'path', 'url', 'sourceUrl'} or {'ok': False, 'reason'}."""
    page_url = hero_page_url(slug)
    try:
        html = fetch_url(page_url).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"ok": False, "reason": f"could not fetch {page_url}: {e}"}

    matched_as = None
    img_url = find_splash_url(html, hero_name)
    if not img_url and hero_id in KNOWN_CODENAMES:
        matched_as = KNOWN_CODENAMES[hero_id]
        img_url = find_splash_url(html, matched_as)
    if not img_url:
        return {"ok": False, "reason": (
            f"no hero-specific splash image matched on {page_url} "
            f"(never guessed from a generic/shared page image)")}

    staged = _staged_path(hero_id, img_url)
    if force or not os.path.exists(staged):
        try:
            data = fetch_url(img_url)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return {"ok": False, "reason": f"could not download {img_url}: {e}"}
        os.makedirs(STAGING_DIR, exist_ok=True)
        with open(staged, "wb") as f:
            f.write(data)
    return {"ok": True, "path": staged, "sourceUrl": page_url, "imageUrl": img_url,
            "matchedAsCodename": matched_as}


def _square_crop(img):
    h, w = img.shape[:2]
    side = min(h, w)
    y0, x0 = (h - side) // 2, (w - side) // 2
    return img[y0:y0 + side, x0:x0 + side]


def build(dry_run: bool = False, only_hero_id: str | None = None,
         force: bool = False) -> dict:
    import cv2

    con = db.connect()
    heroes = {r["id"]: {"name": r["name"], "role": r["role"]}
              for r in con.execute("SELECT id, name, role FROM heroes")}
    con.close()

    now_iso = _now_iso()
    # Load any existing manifest so a single --hero-id run (or a partial
    # re-run after a network hiccup) merges in, never clobbering every
    # other already-resolved hero's entry.
    manifest: dict[str, dict] = {}
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH, encoding="utf-8") as f:
            manifest = json.load(f).get("heroes", {})
    unresolved: list[dict] = []
    this_run: list[str] = []

    ids = [only_hero_id] if only_hero_id else sorted(HERO_SLUGS)
    for hero_id in ids:
        if hero_id not in HERO_SLUGS:
            unresolved.append({"id": hero_id, "reason": "no known Blizzard slug for this hero id"})
            continue
        hero_name = heroes.get(hero_id, {}).get("name", hero_id)
        slug = HERO_SLUGS[hero_id]
        result = fetch_hero_splash(hero_id, slug, hero_name, force=force)
        if not result["ok"]:
            unresolved.append({"id": hero_id, "reason": result["reason"]})
            continue

        img = cv2.imread(result["path"])
        if img is None:
            unresolved.append({"id": hero_id, "reason": f"downloaded file at {result['path']} did not decode as an image"})
            continue
        h, w = img.shape[:2]
        if min(h, w) < 96:
            unresolved.append({"id": hero_id, "reason": f"splash image too small ({w}x{h})"})
            continue

        team_dir = os.path.join(OUT_DIR, hero_id)
        entry = {
            "sourceUrl": result["sourceUrl"],
            "imageUrl": result["imageUrl"],
            "matchedAsCodename": result.get("matchedAsCodename"),
            "retrievedAt": now_iso,
            "role": heroes.get(hero_id, {}).get("role"),
        }
        if not dry_run:
            os.makedirs(team_dir, exist_ok=True)
            artwork_path = os.path.join(team_dir, "artwork.jpg")
            cv2.imwrite(artwork_path, img, [cv2.IMWRITE_JPEG_QUALITY, 90])
            cv2.imwrite(os.path.join(team_dir, "artwork.webp"), img, [cv2.IMWRITE_WEBP_QUALITY, 88])

            portrait = _square_crop(img)
            portrait_up = cv2.resize(portrait, (PORTRAIT_SIZE, PORTRAIT_SIZE),
                                     interpolation=cv2.INTER_AREA)
            cv2.imwrite(os.path.join(team_dir, "portrait.png"), portrait_up)
            cv2.imwrite(os.path.join(team_dir, "portrait.webp"), portrait_up,
                       [cv2.IMWRITE_WEBP_QUALITY, 90])

            icon = cv2.resize(portrait_up, (ICON_SIZE, ICON_SIZE), interpolation=cv2.INTER_AREA)
            cv2.imwrite(os.path.join(team_dir, "icon.png"), icon)
            cv2.imwrite(os.path.join(team_dir, "icon.webp"), icon, [cv2.IMWRITE_WEBP_QUALITY, 90])

            def _rel(p):
                return os.path.relpath(p, db.REPO_ROOT).replace(os.sep, "/")

            with open(artwork_path, "rb") as f:
                artwork_hash = _sha256(f.read())
            entry.update({
                "artwork": {"path": _rel(artwork_path), "webp": _rel(os.path.join(team_dir, "artwork.webp")),
                           "width": w, "height": h, "hash": artwork_hash, "avif": None},
                "portrait": {"path": _rel(os.path.join(team_dir, "portrait.png")),
                            "webp": _rel(os.path.join(team_dir, "portrait.webp")),
                            "width": PORTRAIT_SIZE, "height": PORTRAIT_SIZE, "avif": None},
                "icon": {"path": _rel(os.path.join(team_dir, "icon.png")),
                        "webp": _rel(os.path.join(team_dir, "icon.webp")),
                        "width": ICON_SIZE, "height": ICON_SIZE, "avif": None},
            })
        manifest[hero_id] = entry
        this_run.append(hero_id)

    if not dry_run:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "note": ("Official Blizzard hero presentation art (portrait/"
                         "artwork/icon), distinct from the broadcast-crop "
                         "evidence portraits in assets/img/heroes/manifest.json. "
                         "Source: overwatch.blizzard.com, Blizzard's own "
                         "official hero pages. Non-commercial fan use per "
                         "Blizzard's Fan Content Policy; Overwatch and its "
                         "heroes are trademarks of Blizzard Entertainment, Inc."),
                "generatedAt": now_iso,
                "heroes": manifest,
                "unresolved": unresolved,
            }, f, indent=1, ensure_ascii=False)
            f.write("\n")

    return {"resolved": manifest, "unresolved": unresolved, "thisRun": this_run}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--hero-id", default=None, help="limit to one hero id")
    ap.add_argument("--force", action="store_true", help="re-download even if cached")
    args = ap.parse_args()

    result = build(dry_run=args.dry_run, only_hero_id=args.hero_id, force=args.force)
    verb = "would resolve" if args.dry_run else "resolved"
    for hero_id in sorted(result["thisRun"]):
        print(f"  {hero_id:<10s} {verb}  <- {result['resolved'][hero_id]['sourceUrl']}")
    for u in result["unresolved"]:
        print(f"  {u['id']:<10s} UNRESOLVED: {u['reason']}")
    print(f"this run: {len(result['thisRun'])} resolved, {len(result['unresolved'])} unresolved "
          f"— manifest now covers {len(result['resolved'])} hero(es) total")


if __name__ == "__main__":
    main()
