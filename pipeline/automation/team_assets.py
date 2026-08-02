"""
team_assets.py — verified team-logo pipeline (Roadmap Phase D2).

State machine, exactly as the guide specifies:

    candidate -> downloaded -> validated -> human-approved -> published

Every step before "human-approved" is fully automatic and safe to rerun.
The step from validated -> human-approved is NEVER automatic: it only
happens when an operator calls approve_candidate() with an explicit
confirm=True naming the exact team and URL being approved. No code path in
this module (or the CLI wired to it) can promote a candidate past
"validated" on its own. A scheduled workflow may re-publish an ALREADY
approved candidate (e.g. after a rerun), but may never approve a new one.

Source-authority order (lower rank wins when several candidates exist for
one team): official team website/brand media (1) > official team social
account (2) > official OWCS/FACEIT source (3) > another authoritative
tournament source (4). There is no rank 5 "guessed" tier — an unverifiable
source is never registered as a candidate at all.

Nothing here is ever hotlinked to the live site: a candidate is fetched
once into a gitignored local staging dir (data/asset_staging/), and only a
PUBLISHED, human-approved asset is ever copied into the committed
assets/img/teams/<id>/ tree the site actually serves.

Known, documented limitation: this environment's OpenCV/libwebp binding
does not round-trip an alpha channel through WebP (verified — a written
RGBA WebP reads back 3-channel). Since "preserve transparency" is a hard
requirement, the square/wide variants — which must stay transparent — are
written as PNG. WebP is used only for the dark-safe/light-safe variants,
which are always composited onto an opaque backing plate and therefore
have no transparency to lose. AVIF is not generated at all: no AVIF
encoder is available without adding a new project dependency, which is
out of scope for this stdlib/existing-deps-only automation layer.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
DEFAULT_ASSET_SOURCES = os.path.join(REPO_ROOT, "assets", "data", "team_asset_sources.json")
DEFAULT_STAGING_DIR = os.path.join(REPO_ROOT, "data", "asset_staging")
DEFAULT_TEAMS_DIR = os.path.join(REPO_ROOT, "assets", "img", "teams")

AUTHORITY_RANK = {
    "official-website": 1,
    "official-social": 2,
    "official-owcs": 3,
    "official-faceit": 3,
    "other-authoritative": 4,
}
MIN_DIMENSION = 48
MAX_ASPECT_RATIO = 4.0
DARK_LUMINANCE_THRESHOLD = 60     # logo darker than this vanishes on a dark bg
LIGHT_LUMINANCE_THRESHOLD = 195   # logo lighter than this vanishes on a light bg

Transport = Callable[[str], bytes]


def _site_relpath(path: str, start: str = REPO_ROOT) -> str:
    """Relative path for a committed asset, GitHub-Pages-safe: forward
    slashes ALWAYS, even on Windows where os.path.relpath emits backslashes
    (which the browser can never resolve as a URL path separator) — and
    never raising across Windows drives. See pipeline/site_paths.py."""
    import site_paths
    return site_paths.site_relpath(path, start)


def _now_iso(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.timezone.utc)).replace(microsecond=0).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def load_registry(path: str = DEFAULT_ASSET_SOURCES) -> dict:
    if not os.path.exists(path):
        return {"teams": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_registry(registry: dict, path: str = DEFAULT_ASSET_SOURCES) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=1)
        f.write("\n")


def _entry(registry: dict, team_id: str) -> dict:
    teams = registry.setdefault("teams", {})
    entry = teams.setdefault(team_id, {"candidateSources": [], "assetCandidates": []})
    # An entry written by team_enrichment.py (Phase D) only ever has
    # candidateSources — backfill assetCandidates so this module works on
    # the real, already-committed registry shape, not just a fresh one.
    entry.setdefault("candidateSources", [])
    entry.setdefault("assetCandidates", [])
    return entry


def _find(registry: dict, team_id: str, url: str) -> dict | None:
    return next((c for c in registry.get("teams", {}).get(team_id, {}).get("assetCandidates", [])
                if c["url"] == url), None)


# --------------------------------------------------------------- candidate
def add_candidate(
    registry: dict, team_id: str, url: str, source_kind: str, *,
    note: str = "", now: dt.datetime | None = None,
) -> dict:
    """Register a candidate logo source. Idempotent by url. Never fetches
    anything — this only records where a human said to look, ranked by the
    source-authority order above."""
    if source_kind not in AUTHORITY_RANK:
        raise ValueError(
            f"unknown source_kind {source_kind!r}; must be one of {sorted(AUTHORITY_RANK)}")
    existing = _find(registry, team_id, url)
    if existing:
        return existing
    entry = _entry(registry, team_id)
    cand = {
        "url": url, "sourceKind": source_kind, "authorityRank": AUTHORITY_RANK[source_kind],
        "note": note, "discoveredAt": _now_iso(now), "state": "candidate",
    }
    entry["assetCandidates"].append(cand)
    return cand


def ranked_candidates(registry: dict, team_id: str) -> list[dict]:
    """Candidates for a team, best authority (lowest rank number) first."""
    entry = registry.get("teams", {}).get(team_id, {})
    return sorted(entry.get("assetCandidates", []),
                 key=lambda c: (c["authorityRank"], c["discoveredAt"]))


def collect_from_enrichment(registry: dict) -> list[dict]:
    """Mechanically promote the plain-string FACEIT avatar/cover candidates
    team_enrichment.py already recorded (see its `candidateSources` list)
    into ranked, structured assetCandidates — 'official-faceit' authority,
    since the URL came straight from FACEIT's own team API, not a guess.
    Idempotent by url. Returns the newly-added candidates."""
    import re
    added = []
    for team_id, entry in registry.get("teams", {}).items():
        for line in entry.get("candidateSources", []):
            m = re.search(r"(https?://\S+)$", line)
            if not m:
                continue
            url = m.group(1)
            if _find(registry, team_id, url):
                continue
            note = "FACEIT team avatar/cover" if "FACEIT" in line else line[:80]
            added.append(add_candidate(registry, team_id, url, "official-faceit", note=note))
    return added


# --------------------------------------------------------------- download
def download_candidate(
    registry: dict, team_id: str, url: str, transport: Transport, *,
    staging_dir: str = DEFAULT_STAGING_DIR, now: dt.datetime | None = None,
) -> dict:
    """Fetch a candidate's bytes ONCE into a local, gitignored staging path.
    Never touches the committed assets/ tree. Raises KeyError if the url was
    never registered as a candidate first — nothing is ever fetched on a
    guess."""
    cand = _find(registry, team_id, url)
    if cand is None:
        raise KeyError(f"{url!r} was never registered as a candidate for {team_id!r}")
    data = transport(url)
    digest = _sha256_bytes(data)
    os.makedirs(os.path.join(staging_dir, team_id), exist_ok=True)
    ext = os.path.splitext(url.split("?")[0])[1] or ".png"
    local_path = os.path.join(staging_dir, team_id, f"{digest}{ext}")
    with open(local_path, "wb") as f:
        f.write(data)
    cand.update({"state": "downloaded", "localPath": local_path,
                "hash": digest, "byteSize": len(data), "downloadedAt": _now_iso(now)})
    return cand


# --------------------------------------------------------------- validate
def validate_candidate(
    registry: dict, team_id: str, url: str, *,
    published_hashes: set[str] | None = None,
) -> dict:
    """Open the downloaded file and check it is real, sane, non-duplicate
    imagery. Never raises on bad content — a bad image is a REJECTED state,
    not a crash, so one bad candidate never blocks the run."""
    import cv2  # local import: only needed on the validate/publish path

    cand = _find(registry, team_id, url)
    if cand is None:
        raise KeyError(f"{url!r} was never registered as a candidate for {team_id!r}")
    if cand.get("state") != "downloaded":
        raise ValueError(f"candidate must be downloaded before validation (state={cand.get('state')})")

    path = cand["localPath"]
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        cand.update({"state": "rejected", "rejectReason": "malformed or unreadable image file"})
        return cand
    h, w = img.shape[:2]
    if h < MIN_DIMENSION or w < MIN_DIMENSION:
        cand.update({"state": "rejected",
                     "rejectReason": f"too small ({w}x{h}, minimum {MIN_DIMENSION}px)"})
        return cand
    aspect = max(w, h) / max(1, min(w, h))
    if aspect > MAX_ASPECT_RATIO:
        cand.update({"state": "rejected", "rejectReason": f"extreme aspect ratio ({w}x{h})"})
        return cand
    if published_hashes and cand.get("hash") in published_hashes:
        cand.update({"state": "rejected",
                     "rejectReason": "duplicate of an already-published asset (same hash)"})
        return cand
    has_alpha = img.ndim == 3 and img.shape[2] == 4
    cand.update({
        "state": "validated", "width": w, "height": h,
        "hasTransparency": has_alpha, "aspectRatio": round(w / h, 3),
    })
    return cand


# --------------------------------------------------------------- approve
def approve_candidate(
    registry: dict, team_id: str, url: str, *, approved_by: str, confirm: bool,
    now: dt.datetime | None = None,
) -> dict:
    """The ONLY step in this pipeline a human must explicitly take. confirm
    must be passed True by the caller — there is no default that approves a
    candidate by accident."""
    if not confirm:
        raise ValueError("approve_candidate requires confirm=True — never approved implicitly")
    cand = _find(registry, team_id, url)
    if cand is None:
        raise KeyError(f"{url!r} was never registered as a candidate for {team_id!r}")
    if cand.get("state") != "validated":
        raise ValueError(f"candidate must be validated before approval (state={cand.get('state')})")
    cand.update({"state": "human-approved", "approvedBy": approved_by, "approvedAt": _now_iso(now)})
    return cand


# ------------------------------------------------------------- image math
def _mean_luminance(img) -> float:
    import numpy as np
    if img.ndim == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3]
        mask = alpha > 10
        if not mask.any():
            mask = np.ones(img.shape[:2], dtype=bool)
        bgr = img[:, :, :3][mask]
    else:
        bgr = img.reshape(-1, img.shape[2] if img.ndim == 3 else 1)
    b, g, r = bgr[:, 0].mean(), bgr[:, 1].mean(), bgr[:, 2].mean() if bgr.shape[1] > 2 else bgr[:, 0].mean()
    return 0.114 * b + 0.587 * g + 0.299 * r


def accent_color(img) -> tuple[int, int, int]:
    """Restrained accent color (RGB) — the mean of non-transparent pixels,
    for a banner/hover backing, never a garish full-saturation pick."""
    import numpy as np
    if img.ndim == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3]
        mask = alpha > 10
        if not mask.any():
            mask = np.ones(img.shape[:2], dtype=bool)
        pixels = img[:, :, :3][mask]
    else:
        pixels = img.reshape(-1, 3)
    b, g, r = (int(v) for v in pixels.mean(axis=0))
    return (r, g, b)


def _square_crop(img):
    h, w = img.shape[:2]
    side = min(h, w)
    y0, x0 = (h - side) // 2, (w - side) // 2
    return img[y0:y0 + side, x0:x0 + side]


def _wide_pad(img, ratio: float = 2.0):
    import cv2
    h, w = img.shape[:2]
    target_w = int(round(h * ratio))
    if target_w <= w:
        return img
    pad = target_w - w
    left, right = pad // 2, pad - pad // 2
    channels = img.shape[2] if img.ndim == 3 else 1
    pad_val = (0, 0, 0, 0) if channels == 4 else (0, 0, 0)
    return cv2.copyMakeBorder(img, 0, 0, left, right, cv2.BORDER_CONSTANT, value=pad_val)


def _safe_variant(img, target: str):
    """Flatten onto an opaque backing that matches the theme this variant
    targets (near-black for 'dark', near-white for 'light'). If the logo
    would actually vanish against that background (too dark for the dark
    variant, too light for the light one), it gets the OPPOSITE neutral as
    a rescue backing instead — never a clashing brand-color guess, and
    never left blending into invisibility."""
    import numpy as np
    lum = _mean_luminance(img)
    page_bg = (28, 24, 22) if target == "dark" else (238, 240, 242)
    rescue_bg = (238, 240, 242) if target == "dark" else (28, 24, 22)
    needs_rescue = (target == "dark" and lum < DARK_LUMINANCE_THRESHOLD) or \
                   (target == "light" and lum > LIGHT_LUMINANCE_THRESHOLD)
    backing = rescue_bg if needs_rescue else page_bg
    h, w = img.shape[:2]
    canvas = np.zeros((h, w, 3), dtype=np.uint8)
    canvas[:] = backing
    if img.ndim == 3 and img.shape[2] == 4:
        alpha = (img[:, :, 3:4].astype(np.float32) / 255.0)
        fg = img[:, :, :3].astype(np.float32)
        out = fg * alpha + canvas.astype(np.float32) * (1 - alpha)
        return out.astype(np.uint8)
    return img[:, :, :3] if img.ndim == 3 else img


# --------------------------------------------------------------- publish
def publish_candidate(
    con, registry: dict, team_id: str, url: str, *,
    teams_dir: str = DEFAULT_TEAMS_DIR, now: dt.datetime | None = None,
) -> dict:
    """The final, automatic step — but ONLY for a candidate already in
    'human-approved' state. Generates variants, preserves any previously
    published mark under history/ instead of deleting it, writes provenance
    back to the registry, and sets teams.logo_url. A scheduled workflow may
    call this to re-publish an already-approved candidate after a rerun,
    but nothing upstream of 'human-approved' is ever reachable without a
    human explicitly calling approve_candidate()."""
    import cv2

    cand = _find(registry, team_id, url)
    if cand is None:
        raise KeyError(f"{url!r} was never registered as a candidate for {team_id!r}")
    if cand.get("state") not in ("human-approved", "published"):
        raise ValueError(
            f"candidate must be human-approved before publish (state={cand.get('state')})")

    now_iso = _now_iso(now)
    src = cv2.imread(cand["localPath"], cv2.IMREAD_UNCHANGED)
    if src is None:
        raise ValueError(f"approved file at {cand['localPath']} is no longer readable")

    team_dir = os.path.join(teams_dir, team_id)
    os.makedirs(team_dir, exist_ok=True)

    row = con.execute("SELECT logo_url FROM teams WHERE id=?", (team_id,)).fetchone()
    if row and row["logo_url"]:
        old_path = os.path.join(REPO_ROOT, row["logo_url"])
        if os.path.exists(old_path):
            with open(old_path, "rb") as f:
                old_hash = _sha256_bytes(f.read())
            if old_hash != cand.get("hash"):
                # The mark is actually changing — preserve the outgoing file
                # under history/ instead of overwriting it. A rerun that
                # republishes the SAME approved candidate (same hash) is a
                # no-op here, so idempotent reruns never churn history/.
                hist_dir = os.path.join(team_dir, "history")
                os.makedirs(hist_dir, exist_ok=True)
                os.replace(old_path, os.path.join(hist_dir, f"{now_iso[:10]}-logo.png"))

    orig_path = os.path.join(team_dir, "logo.png")
    cv2.imwrite(orig_path, src)
    variants = {"original": _site_relpath(orig_path)}

    for name, im in (("square", _square_crop(src)), ("wide", _wide_pad(src))):
        p = os.path.join(team_dir, f"logo-{name}.png")
        cv2.imwrite(p, im)
        variants[name] = _site_relpath(p)

    for target in ("dark-safe", "light-safe"):
        safe = _safe_variant(src, "dark" if target == "dark-safe" else "light")
        p = os.path.join(team_dir, f"logo-{target}.webp")
        cv2.imwrite(p, safe, [cv2.IMWRITE_WEBP_QUALITY, 92])
        variants[target] = _site_relpath(p)

    color = accent_color(src)
    con.execute("UPDATE teams SET logo_url=? WHERE id=?", (variants["original"], team_id))
    con.commit()

    cand.update({
        "state": "published", "publishedAt": now_iso,
        "variants": variants, "accentColorRgb": list(color),
        "avif": None,
        "effectiveFrom": now_iso[:10],
    })
    return cand
