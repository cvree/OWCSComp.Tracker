#!/usr/bin/env python3
"""
live_identity.py — who played which map, read off the broadcast's own HUD.

`live_ingest.py` finds WHERE the maps are in a broadcast; this names them.
The EWC26 / Midseason broadcast prints everything identity needs as text in
its top strip, on every gameplay frame: the map name, "GAME N", and each
team's name. The timeline samples `live_ingest` already holds are enough —
identity costs no extra download.

How a field is trusted:
  * every frame is read several ways (three thresholds × line/block mode),
    because one setting never reads every map: "SAMOA" needs a high
    threshold, "SHAMBALI / MONASTERY" needs block mode;
  * each reading must resolve against something known — a map in the
    catalog (`map_identify.match_map`, plus the layout's `map_aliases` for
    how this broadcast spells a map), a team in the DB
    (`team_identify.match_team`), or the GAME pattern — or it is ignored;
  * a value is named only when it wins a clear majority of the frames.

A team the DB does not know (most of an international event) is not
guessed into a known one: it is reported with the name the broadcast
printed and `known: false`, for a person to confirm once.

What it never does: read the series score. The 0/8 digits in the score box
are not reliable enough to state a winner (measured: the probe frames read
"8" for "0" about half the time).
"""
from __future__ import annotations

import collections
import re

import map_identify as mi
import ocr_hud
import team_identify as ti

# A value is named when it wins at least this share of the frames that gave
# any resolvable reading, and at least MIN_FRAMES of them.
MIN_SHARE = 0.5
MIN_FRAMES = 2
THRESHOLDS = ("otsu", 150, 190)
MODES = ("7", "6")          # tesseract page-seg: single line, then block
_GAME_RE = re.compile(r"\b[GC]AME\s*([0-9|Il])(?![A-Za-z0-9])")
# A team bar reads as junk + NAME + junk ("y VARREL ye", "97 GLOBANT \\"):
# keep the upper-case words of two or more characters that are not bare numbers.
_TEAM_WORD = re.compile(r"^(?=.*[A-Z])[A-Z0-9.'&]{2,}$")
# A known team is named only on (near-)exact evidence. team_identify's fuzzy
# cutoff (0.72) read "GEEKAY ESPORTS" as Gen.G Esports; at an international
# event most teams are not in the DB, and a wrong known team is far worse
# than an honest unknown one.
KNOWN_QUALITY = 0.9
# Readings this alike are one name misread ("GEEXAY"/"GEEKAY", "VIRTUSPRO"/
# "VIRTUS PRO") and vote together.
SAME_NAME = 0.8


def tesseract_line():
    """The default OCR: (binary image, page-seg mode) -> text."""
    import pytesseract

    def read(img, psm: str) -> str:
        return " ".join(pytesseract.image_to_string(img, config=f"--psm {psm}").split())
    return read


def zone_readings(frame, frac, ocr) -> list[str]:
    """Every non-empty reading of one zone of one frame."""
    import cv2
    fh, fw = frame.shape[:2]
    x, y = int(frac[0] * fw), int(frac[1] * fh)
    w, h = max(1, int(frac[2] * fw)), max(1, int(frac[3] * fh))
    crop = frame[y:y + h, x:x + w]
    if crop.size == 0:
        return []
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    # The strip is drawn for 1080p; reading it at 720p needs it larger.
    g = cv2.resize(g, None, fx=4 * 720 / fh, fy=4 * 720 / fh,
                   interpolation=cv2.INTER_CUBIC)
    out = []
    for thr in THRESHOLDS:
        if thr == "otsu":
            _, t = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        else:
            _, t = cv2.threshold(g, thr, 255, cv2.THRESH_BINARY)
        # white HUD text -> black on white, with a margin tesseract likes
        t = cv2.copyMakeBorder(255 - t, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
        for psm in MODES:
            s = ocr(t, psm)
            if s:
                out.append(s)
    return out


def clean_team(text: str) -> str:
    # The bar is all capitals, so a lower-case l is a misread 1 ("Tl" = T1).
    words = [w.replace("l", "1") for w in text.split()]
    words = [w for w in words if _TEAM_WORD.match(w) and not w.isdigit()]
    return " ".join(words)


def _key(name: str) -> str:
    return ocr_hud._norm_text(name).replace(" ", "")


def cluster_names(per_frame: list[set]) -> list[set]:
    """Map each frame's readings onto one spelling per misread name: the
    most-read spelling of each group of alike readings (ties: the longer)."""
    import difflib
    counts = collections.Counter(n for names in per_frame for n in names)
    reps: list[str] = []
    for name, _ in sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0])):
        if not any(difflib.SequenceMatcher(None, _key(name), _key(r)).ratio() >= SAME_NAME
                   for r in reps):
            reps.append(name)

    def rep(name: str) -> str:
        return max(reps, key=lambda r: difflib.SequenceMatcher(None, _key(name), _key(r)).ratio())
    return [{rep(n) for n in names} for names in per_frame]


def resolve_map(text: str, known_maps: list[dict], aliases: dict) -> str | None:
    norm = ocr_hud._norm_text(text)
    for spelled, map_id in aliases.items():
        if not spelled.startswith("_") and ocr_hud._norm_text(spelled) == norm:
            return map_id
    return mi.match_map(text, known_maps)["map"]


def resolve_game(text: str) -> int | None:
    m = _GAME_RE.search(text.upper())
    if not m:
        return None
    d = m.group(1)
    return 1 if d in "|Il" else int(d)


def _winner(per_frame: list[set], label: str) -> dict:
    """Majority over frames; each frame votes once per value it resolved."""
    votes = collections.Counter(v for vals in per_frame for v in vals)
    voting = sum(1 for vals in per_frame if vals)
    if not votes:
        return {"value": None, "votes": 0, "of": len(per_frame),
                "reason": f"no frame gave a {label} that resolves"}
    (best, n), *rest = votes.most_common()
    runner = rest[0][1] if rest else 0
    if n < MIN_FRAMES or n < MIN_SHARE * voting or n == runner:
        return {"value": None, "votes": n, "of": len(per_frame),
                "reason": f"no clear {label}: {dict(votes.most_common(3))}"}
    return {"value": best, "votes": n, "of": len(per_frame), "reason": None}


def identify(frames: list, layout: dict, *, known_maps: list[dict],
             known_teams: list[dict], ocr=None) -> dict | None:
    """Identity of one map window from its gameplay frames, or None when the
    layout declares no identity zones."""
    zones = layout.get("identity_zones")
    if not zones:
        return None
    ocr = ocr or tesseract_line()
    aliases = layout.get("map_aliases") or {}
    per = {"map": [], "game": [], "team_a": [], "team_b": []}
    for frame in frames:
        reads = {k: zone_readings(frame, zones[k], ocr) for k in per if k in zones}
        per["map"].append({m for m in (resolve_map(t, known_maps, aliases)
                                       for t in reads.get("map", [])) if m})
        per["game"].append({g for g in map(resolve_game, reads.get("game", [])) if g})
        for side in ("team_a", "team_b"):
            per[side].append({c for c in map(clean_team, reads.get(side, [])) if len(c) >= 2})
    out = {"map": _winner(per["map"], "map"), "game": _winner(per["game"], "game")}
    for side in ("team_a", "team_b"):
        w = _winner(cluster_names(per[side]), "team name")
        if w["value"]:
            m = ti.match_team(w["value"], known_teams)
            known = bool(m["team"]) and m["quality"] >= KNOWN_QUALITY
            w.update(printed=w["value"], value=m["team"] if known else w["value"],
                     known=known)
        out[side] = w
    out["frames"] = len(frames)
    out["complete"] = all(out[k]["value"] for k in ("map", "team_a", "team_b"))
    return out


def same_map(a: dict, b: dict) -> bool:
    """Two adjacent windows that are one map split by a long pause."""
    ia, ib = a.get("identity") or {}, b.get("identity") or {}
    if not (ia.get("complete") and ib.get("complete")):
        return False
    return all(ia[k]["value"] == ib[k]["value"] for k in ("map", "game", "team_a", "team_b"))


def merge_split_maps(maps: list[dict]) -> list[dict]:
    """Join adjacent windows whose identity says they are the same map."""
    out: list[dict] = []
    for m in maps:
        if out and same_map(out[-1], m):
            prev = out[-1]
            prev["end"] = m["end"]
            prev["runs"] = prev["runs"] + m["runs"]
            prev["gameplaySamples"] += m["gameplaySamples"]
            prev["seconds"] = round(prev["end"] - prev["start"], 1)
            prev["mergedPause"] = True
        else:
            out.append(dict(m))
    for i, m in enumerate(out, 1):
        m["index"] = i
    return out


def harmonise_names(maps: list[dict]) -> list[dict]:
    """One spelling per unknown team across the whole broadcast.

    A team plays two to five maps a day; each window votes its own spelling
    ("WEHIBO GAMING" on one, "WEIBO GAMING" on the next). Grouping every
    window's unknown name the same way frames are grouped, weighted by the
    votes each won, gives the broadcast's best reading of each team."""
    import difflib
    weight: collections.Counter = collections.Counter()
    for m in maps:
        for side in ("team_a", "team_b"):
            t = (m.get("identity") or {}).get(side) or {}
            if t.get("value") and not t.get("known"):
                weight[t["value"]] += t.get("votes", 1)
    reps: list[str] = []
    for name, _ in sorted(weight.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0])):
        if not any(difflib.SequenceMatcher(None, _key(name), _key(r)).ratio() >= SAME_NAME
                   for r in reps):
            reps.append(name)
    for m in maps:
        for side in ("team_a", "team_b"):
            t = (m.get("identity") or {}).get(side) or {}
            if t.get("value") and not t.get("known") and reps:
                best = max(reps, key=lambda r: difflib.SequenceMatcher(
                    None, _key(t["value"]), _key(r)).ratio())
                if difflib.SequenceMatcher(None, _key(t["value"]), _key(best)).ratio() >= SAME_NAME:
                    t["value"] = best
    return maps


def attach(maps: list[dict], samples: list[dict], layout: dict, *,
           known_maps: list[dict], known_teams: list[dict], ocr=None,
           per_map: int = 24) -> list[dict]:
    """Identify every map window from the timeline frames inside it, then
    join windows that are one map and give each unknown team one spelling."""
    import cv2
    if not layout.get("identity_zones"):
        return maps
    for m in maps:
        inside = [s["frame"] for s in samples
                  if s.get("state") == "gameplay" and s.get("frame")
                  and m["start"] <= s["offset"] <= m["end"]]
        if len(inside) > per_map:
            step = len(inside) / per_map
            inside = [inside[int(i * step)] for i in range(per_map)]
        frames = [f for f in (cv2.imread(p) for p in inside) if f is not None]
        m["identity"] = identify(frames, layout, known_maps=known_maps,
                                 known_teams=known_teams, ocr=ocr)
    return harmonise_names(merge_split_maps(maps))
