#!/usr/bin/env python3
"""
label_batch.py — turn a broadcast into a page of hero cards a person can
name in a few minutes.

Hero template coverage is the ceiling on everything this project publishes:
a composition needs five NAMED heroes, and a hero with no template in the
broadcast package reads UNKNOWN forever. Raising that ceiling needs a human
to say "that portrait is Kiriko" — official art cannot do it, and neither
can another package's templates. Both were measured against the 3,070 real
slot crops of the Nepal map before this module was written:

  * official hero icons as label suggestions: **0 of 18** clusters right;
  * templates from a different broadcast package: **0 of 9** right.

A broadcast HUD portrait is its own rendering, per production. So the job
here is not to guess names. It is to make naming cheap:

  1. **Cluster.** Every slot crop of every gameplay frame, normalised and
     grouped by correlation across ALL ten slots. On Nepal, 3,070 crops
     become 18 clusters at 99.3 % purity — the same hero in the same
     portrait state lands in one card.
  2. **Link what the timeline proves.** A hero's grey "dead" portrait and
     its ult-flash portrait are different pictures of the same hero, and
     they appear in the SAME slot INSIDE that hero's own stint. A cluster
     whose crops sit ≥80 % inside a bigger cluster's span in its own slot
     is marked `nestedIn` it — 6 of 6 such links were the same hero on
     Nepal. The labelling page offers that as a one-click "same as card N",
     which the person still confirms.
  3. **Write a batch** under `labelling/<batch>/`: a prototype image and a
     handful of time-spread samples per cluster, the slot/time evidence,
     and the member list the forge will build from.

A person's labels then go through `label_forge.py`, which hands them to the
existing forge: build slice → quality gate → held-out validation → only
VALIDATED heroes are promoted. Nothing in this module writes a template.

Member crops — the raw material the forge cuts templates from — are written
to `work/label-crops/<batch>/` (gitignored). On a runner they travel as a
workflow artifact; the batch records which one.

CLI:
  # from an existing ingest report (offline; how the tests exercise it)
  python3 pipeline/label_batch.py --report reports/ingest/qad-twis-nepal \\
      --layout layouts/owcs_jksix_qwc.json --batch nepal-test

  # from a remote broadcast (Twitch works on a GitHub-hosted runner)
  python3 pipeline/label_batch.py --url https://www.twitch.tv/videos/<id> \\
      --layout layouts/owcs_8c105lnzlam.json

  # rebuild the page's index (assets/data/labelling.v1.js), or gate it in CI
  python3 pipeline/label_batch.py --index [--check]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # noqa: E402

SCHEMA = "label-batch.v1"
INDEX_SCHEMA = "label-index.v1"
LABEL_ROOT = os.path.join(db.REPO_ROOT, "labelling")
CROPS_ROOT = os.path.join(db.REPO_ROOT, "work", "label-crops")

# Correlation a crop needs with a cluster's leader to join it. 0.55–0.70 all
# gave 99.2–99.3 % purity on Nepal; 0.62 is the value harvest_templates.py
# already uses per slot, so the two cluster the same way.
CLUSTER_SIM = 0.62
# A cluster smaller than this is a transition, a killfeed overlap or a fade.
# It is counted, not shown: nobody should be asked to name a dissolve.
MIN_CLUSTER = 5
# Member crops kept per cluster for the forge. Spread evenly over time so
# the forge's build / dead-zone / holdout split still has a timeline to cut.
MEMBER_CAP = 80
# Sample crops shown per card, spread over the cluster's time span.
SAMPLES = 6
# Fraction of a cluster's crops (in its own main slot) that must fall inside
# another cluster's span in that slot for the two to be linked.
NEST_INSIDE = 0.80
# Side of the normalised square every crop is compared at.
FEATURE_SIDE = 24

_REPORT_CROP = re.compile(r"^t(\d+(?:\.\d+)?)_([ab][1-5])\.png$")


def log(msg: str) -> None:
    print(f"[label-batch] {msg}", flush=True)


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def crop_name(t: float, slot: str) -> str:
    """The same name an ingest report gives a crop: t0001805.0_a1.png."""
    return f"t{float(t):09.1f}_{slot}.png"


# ------------------------------------------------------------- acquisition
def crops_from_report(report_dir: str) -> list[dict]:
    """Every slot crop an ingest run kept as evidence."""
    import cv2
    ev = os.path.join(report_dir, "evidence")
    out = []
    for fn in sorted(os.listdir(ev)):
        m = _REPORT_CROP.match(fn)
        if not m:
            continue
        img = cv2.imread(os.path.join(ev, fn), cv2.IMREAD_COLOR)
        if img is not None:
            out.append({"t": float(m.group(1)), "slot": m.group(2), "img": img})
    return out


def crops_from_frames(frames: dict[float, str], layout: dict) -> tuple[list[dict], dict]:
    """Cut the ten slots out of every frame the gameplay filter accepts.

    A replay, a scoreboard or the desk renders no HUD — or worse, a HUD from
    another moment — so only `gameplay` frames contribute."""
    import cv2
    import capture
    import gameplay_state as gs

    crops: list[dict] = []
    states: dict[str, int] = defaultdict(int)
    for t in sorted(frames):
        frame = cv2.imread(frames[t], cv2.IMREAD_COLOR)
        if frame is None:
            states["unreadable"] += 1
            continue
        lay, info = capture.scale_layout_to_frame(layout, frame.shape[1],
                                                  frame.shape[0])
        if not info.get("ok"):
            states["wrong-aspect"] += 1
            continue
        state, _reason = gs.classify_frame(frame, lay)
        states[state] += 1
        if state != "gameplay":
            continue
        for side in ("a", "b"):
            for i, (x, y, w, h) in enumerate(lay[f"slots_{side}"], 1):
                crop = frame[y:y + h, x:x + w]
                if crop.size:
                    crops.append({"t": float(t), "slot": f"{side}{i}",
                                  "img": crop.copy()})
    return crops, dict(states)


def dense_offsets(windows: list[dict], *, step: float, pad: float,
                  duration: float, budget: int) -> list[float]:
    """Evenly spaced offsets across the gameplay windows, within budget.

    When the windows hold more samples than the budget, the step widens
    rather than the tail being cut, so every window is still represented."""
    spans = [(max(0.0, w["start"] - pad),
              min(duration or w["end"] + pad, w["end"] + pad)) for w in windows]
    total = sum(max(0.0, b - a) for a, b in spans)
    if total <= 0 or budget <= 0:
        return []
    step = max(step, total / budget)
    out: list[float] = []
    for a, b in spans:
        t = a
        while t <= b and len(out) < budget:
            out.append(round(t, 1))
            t += step
    return sorted(set(out))


def harvest_remote(url: str, layout: dict, *, step: float = 10.0,
                   max_frames: int = 360, pad: float = 60.0,
                   source=None, duration: float | None = None,
                   probe_fn=None) -> tuple[dict[float, str], dict]:
    """Frames from inside the broadcast's gameplay, fetched remotely.

    The gameplay windows come from `calibrate_remote.acquire` — the same
    ladder that already finds live play in a nine-hour broadcast without
    downloading it — and the dense pass samples only inside them."""
    import calibrate_remote as cr
    import remote_frames as rf

    src = source or rf.RemoteFrameSource(url, height=720)
    duration = duration if duration is not None else cr._probe_duration(url, probe_fn)
    scan = cr.acquire(src, duration, trial_fn=None)
    windows = cr.gameplay_windows(list(scan["clean"]))
    log(f"gameplay windows: {len(windows)} "
        + ", ".join(f"{w['start']:.0f}–{w['end']:.0f}s" for w in windows[:8]))
    offsets = dense_offsets(windows, step=step, pad=pad,
                            duration=duration, budget=max_frames)
    frames: dict[float, str] = dict(scan["clean"])
    for i in range(0, len(offsets), 24):
        got = src.grab(offsets[i:i + 24])
        frames.update({t: p for t, p in got.items() if p})
    stats = {"windows": windows, "scanStop": scan["stopReason"],
             "offsetsRequested": len(offsets), "framesAcquired": len(frames),
             "durationSeconds": duration,
             "transfer": {k: v for k, v in src.stats.items()
                          if k in ("framesFetched", "framesMissing",
                                   "framesFromCache", "seconds",
                                   "bytesDownloaded")}}
    return frames, stats


# --------------------------------------------------------------- clustering
def roi_for(layout: dict, crops: list[dict]) -> dict:
    """The part of a slot that is the portrait.

    The layout's own `portrait_roi` when it has one. Otherwise propose one
    from these crops: a slot box that includes the player-name strip would
    otherwise cluster by PLAYER, and a player who swaps keeps their name.
    The proposal is used for grouping only — it is never written into a
    layout, because the detector must be validated on what production uses.
    """
    import cv2
    import detect
    import portrait_roi as pr

    roi = detect.portrait_roi(layout)
    if roi is not None:
        return {"roi": list(roi), "from": "layout",
                "reason": "the layout's own portrait_roi"}
    shape = defaultdict(list)
    for c in crops:
        shape[c["img"].shape[:2]].append(c)
    if not shape:
        return {"roi": None, "from": "none", "reason": "no crops"}
    common = max(shape.values(), key=len)
    grays = [cv2.cvtColor(c["img"], cv2.COLOR_BGR2GRAY) for c in common[:400]]
    found = pr.discover(grays)
    if found.get("roi"):
        return {"roi": list(found["roi"]), "from": "discovered",
                "reason": found.get("reason", "")}
    return {"roi": None, "from": "none",
            "reason": found.get("reason", "no portrait ROI proposed")}


def features(crops: list[dict], roi) -> "object":
    """Zero-mean, unit-norm grayscale vectors: a dot product between two of
    them is their normalised correlation."""
    import cv2
    import detect
    import numpy as np

    rows = []
    for c in crops:
        g = cv2.cvtColor(c["img"], cv2.COLOR_BGR2GRAY)
        g = detect.apply_roi(g, tuple(roi) if roi else None)
        g = cv2.resize(g, (FEATURE_SIDE, FEATURE_SIDE),
                       interpolation=cv2.INTER_AREA).astype(np.float32)
        g -= g.mean()
        n = float(np.linalg.norm(g))
        rows.append((g / n if n > 1e-6 else g).ravel())
    return np.stack(rows) if rows else np.zeros((0, FEATURE_SIDE ** 2), np.float32)


def cluster(feats, *, sim: float = CLUSTER_SIM) -> list[list[int]]:
    """Leader clustering in input (time) order; largest cluster first.

    Deterministic for a given input order, which is what lets a batch be
    rebuilt byte-for-byte from the same crops."""
    import numpy as np

    leaders: list = []
    members: list[list[int]] = []
    for i in range(len(feats)):
        x = feats[i]
        if leaders:
            sims = np.asarray(leaders) @ x
            j = int(sims.argmax())
            if sims[j] >= sim:
                members[j].append(i)
                continue
        leaders.append(x)
        members.append([i])
    order = sorted(range(len(members)), key=lambda k: (-len(members[k]), k))
    return [members[k] for k in order]


def _spread(seq: list, n: int) -> list:
    """n items evenly spaced through seq (all of it when it is short)."""
    if len(seq) <= n:
        return list(seq)
    step = (len(seq) - 1) / (n - 1) if n > 1 else 0
    return [seq[round(i * step)] for i in range(n)]


def describe(groups: list[list[int]], crops: list[dict]) -> list[dict]:
    """The evidence a person needs to name a card: which slots, when."""
    out = []
    for k, idx in enumerate(groups):
        slots: dict[str, list[float]] = defaultdict(list)
        for i in idx:
            slots[crops[i]["slot"]].append(crops[i]["t"])
        times = sorted(crops[i]["t"] for i in idx)
        sides = sorted({s[0] for s in slots})
        out.append({
            "id": f"c{k:02d}", "size": len(idx),
            "side": sides[0] if len(sides) == 1 else "both",
            "tFirst": times[0], "tLast": times[-1],
            "mainSlot": max(slots, key=lambda s: (len(slots[s]), s)),
            "slots": {s: {"n": len(ts), "tFirst": min(ts), "tLast": max(ts)}
                      for s, ts in sorted(slots.items())},
            "nestedIn": None,
        })
    return out


def link_nested(cards: list[dict], groups: list[list[int]],
                crops: list[dict]) -> int:
    """Mark a card `nestedIn` the larger card whose stint it sits inside.

    The rule is about the timeline, not the picture: a dead or ult-flash
    portrait looks nothing like the live one, but it can only appear in the
    slot where that hero is playing, while they are playing. Returns how
    many links were made."""
    times: list[dict[str, list[float]]] = []
    for idx in groups:
        per: dict[str, list[float]] = defaultdict(list)
        for i in idx:
            per[crops[i]["slot"]].append(crops[i]["t"])
        times.append(per)
    links = 0
    for a, card in enumerate(cards):
        slot = card["mainSlot"]
        mine = times[a][slot]
        best = None
        for b, other in enumerate(cards):
            theirs = times[b].get(slot)
            if b == a or not theirs or len(theirs) <= len(mine):
                continue
            lo, hi = min(theirs), max(theirs)
            inside = sum(lo <= t <= hi for t in mine) / len(mine)
            if inside >= NEST_INSIDE and (best is None or len(theirs) > best[1]):
                best = (b, len(theirs), inside)
        if best is not None:
            card["nestedIn"] = cards[best[0]]["id"]
            card["nestedEvidence"] = (
                f"{best[2] * 100:.0f}% of this card's {slot} crops fall inside "
                f"{cards[best[0]]['id']}'s stint in {slot}")
            links += 1
    # Chains collapse to their root so the page never has to walk them.
    by_id = {c["id"]: c for c in cards}
    for c in cards:
        seen = set()
        while c["nestedIn"] and by_id[c["nestedIn"]]["nestedIn"] \
                and c["nestedIn"] not in seen:
            seen.add(c["nestedIn"])
            c["nestedIn"] = by_id[c["nestedIn"]]["nestedIn"]
    return links


# ------------------------------------------------------------------ writing
def build_batch(crops: list[dict], *, batch_id: str, layout: dict,
                layout_id: str, source: dict, out_dir: str, crops_dir: str,
                created_at: str | None = None, acquisition: dict | None = None,
                repo_root: str = db.REPO_ROOT, write_crops: bool = True) -> dict:
    """Cluster, link, write images + member crops, and return the batch.

    `write_crops=False` when `crops_dir` already holds every crop under its
    report name — an ingest report's committed evidence — so a batch built
    from a report references those files instead of duplicating them."""
    import cv2
    import numpy as np

    crops = sorted(crops, key=lambda c: (c["t"], c["slot"]))
    roi = roi_for(layout, crops)
    groups = cluster(features(crops, roi["roi"]))
    shown = [g for g in groups if len(g) >= MIN_CLUSTER]
    small = sum(len(g) for g in groups if len(g) < MIN_CLUSTER)
    cards = describe(shown, crops)
    links = link_nested(cards, shown, crops)

    os.makedirs(out_dir, exist_ok=True)
    if write_crops:
        os.makedirs(crops_dir, exist_ok=True)
    for stale in os.listdir(out_dir):
        if re.match(r"^c\d+(_s\d+)?\.png$", stale):
            os.remove(os.path.join(out_dir, stale))

    kept = 0
    for card, idx in zip(cards, shown):
        ordered = sorted(idx, key=lambda i: (crops[i]["t"], crops[i]["slot"]))
        # Prototype: the per-pixel median of a spread of members — steadier
        # than any one frame, and still a real broadcast portrait.
        same = [crops[i]["img"] for i in _spread(ordered, 40)
                if crops[i]["img"].shape == crops[ordered[0]]["img"].shape]
        proto = np.median(np.stack(same), axis=0).astype(np.uint8)
        card["image"] = f"{card['id']}.png"
        cv2.imwrite(os.path.join(out_dir, card["image"]), proto)
        card["samples"] = []
        for s, i in enumerate(_spread(ordered, SAMPLES)):
            name = f"{card['id']}_s{s}.png"
            cv2.imwrite(os.path.join(out_dir, name), crops[i]["img"])
            card["samples"].append({"image": name, "t": crops[i]["t"],
                                    "slot": crops[i]["slot"]})
        card["members"] = []
        for i in _spread(ordered, MEMBER_CAP):
            name = crop_name(crops[i]["t"], crops[i]["slot"])
            if write_crops:
                cv2.imwrite(os.path.join(crops_dir, name), crops[i]["img"])
            card["members"].append([name, crops[i]["t"], crops[i]["slot"]])
            kept += 1

    def rel(p):
        return os.path.relpath(os.path.abspath(p), repo_root).replace(os.sep, "/")

    batch = {
        "schema": SCHEMA,
        "batchId": batch_id,
        "createdAt": created_at or _utcnow_iso(),
        "source": source,
        "layoutId": layout_id,
        "templatesDir": layout.get("templates_dir"),
        "roi": roi,
        "cropsDir": rel(crops_dir),
        "cropsArtifact": None,
        "acquisition": acquisition or {},
        "policy": {"clusterSimilarity": CLUSTER_SIM,
                   "minClusterSize": MIN_CLUSTER,
                   "memberCap": MEMBER_CAP, "nestInside": NEST_INSIDE,
                   "featureSide": FEATURE_SIDE},
        "counts": {"crops": len(crops), "clusters": len(groups),
                   "cards": len(cards), "linkedCards": links,
                   "cropsInSmallClusters": small, "membersKept": kept},
        "honesty": (
            "Cards are grouped by picture similarity and linked by the "
            "timeline. Neither names a hero: a person does, on label.html, "
            "and only templates that then pass held-out validation are "
            "promoted. Official hero art and other packages' templates were "
            "measured as label suggestions and were wrong every time, so "
            "none are offered."),
        "cards": cards,
    }
    with open(os.path.join(out_dir, "batch.json"), "w", encoding="utf-8") as f:
        json.dump(batch, f, indent=1)
        f.write("\n")
    return batch


# -------------------------------------------------------------------- index
def _read(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def build_index(root: str = LABEL_ROOT) -> dict:
    """What the labelling page renders: every batch, how far it has got,
    and its cards (without the member lists, which only the forge needs).

    A pure function of the committed files — no wall clock — so an
    unchanged tree rebuilds byte-for-byte and `--check` can gate it."""
    card_keys = ("id", "size", "side", "tFirst", "tLast", "mainSlot",
                 "slots", "nestedIn", "nestedEvidence", "image", "samples")
    batches = []
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            b = _read(os.path.join(root, name, "batch.json"))
            if not b or b.get("schema") != SCHEMA:
                continue
            labels = _read(os.path.join(root, name, "labels.json")) or {}
            forged = _read(os.path.join(root, name, "forge-report.json")) or {}
            named = {k: v for k, v in (labels.get("labels") or {}).items()
                     if v and v != "skip"}
            state = ("forged" if forged else
                     "labelled" if named else "needs-labels")
            batches.append({
                "batchId": b["batchId"],
                "createdAt": b.get("createdAt"),
                "title": (b.get("source") or {}).get("title"),
                "url": (b.get("source") or {}).get("url"),
                "layoutId": b.get("layoutId"),
                "cards": len(b.get("cards") or []),
                "labelled": len(named),
                "heroes": sorted(set(named.values())),
                "state": state,
                "promoted": sorted((forged.get("promotion") or {})
                                   .get("promotedHeroes") or []),
                "refused": sorted(((forged.get("promotion") or {})
                                   .get("refused") or {}).keys()),
                "labels": labels.get("labels") or {},
                "labelledBy": labels.get("labelledBy"),
                "verdicts": {h: v.get("verdict") for h, v in
                             (forged.get("heroes") or {}).items()},
                "leaveOneOut": forged.get("leaveOneOut"),
                "counts": b.get("counts") or {},
                "cards": [{k: c.get(k) for k in card_keys}
                          for c in b.get("cards") or []],
            })
    batches.sort(key=lambda r: (r["createdAt"] or "", r["batchId"]), reverse=True)
    return {"schema": INDEX_SCHEMA,
            "generatedAt": batches[0]["createdAt"] if batches else None,
            "batches": batches}


# The page loads this as a script, like every other dataset on the site:
# it works from any static host and from a file:// copy, with no fetch.
INDEX_JS = os.path.join(db.REPO_ROOT, "assets", "data", "labelling.v1.js")


def index_text(index: dict) -> str:
    return ("/* Generated by pipeline/label_batch.py --index. Do not edit. */\n"
            "window.OWCS_LABELLING = " + json.dumps(index, indent=1) + ";\n")


def write_index(root: str = LABEL_ROOT, path: str | None = None) -> str:
    path = path or (INDEX_JS if root == LABEL_ROOT
                    else os.path.join(root, "labelling.v1.js"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(index_text(build_index(root)))
    return path


# ----------------------------------------------------------- what to harvest
def batched_video_ids(root: str = LABEL_ROOT) -> set[str]:
    """Video ids that already have a batch, so a harvest never repeats one."""
    out = set()
    if os.path.isdir(root):
        for name in os.listdir(root):
            b = _read(os.path.join(root, name, "batch.json")) or {}
            vid = (b.get("source") or {}).get("videoId")
            if vid:
                out.add(str(vid))
    return out


def pick_broadcast(queue: list[dict], *, allowed_families: list[str],
                   exclude: set[str] | None = None) -> dict | None:
    """The broadcast a harvest should cut into cards next, or None.

    `queue` is `match_finder.fetchable_queue(...)` — likely, still listed,
    on a platform the runner can fetch. On top of that, the same three
    preferences the calibration workflow learned the hard way:

      * a title that POSITIVELY names an allowed event family, because the
        layout being harvested for describes that production's overlay;
      * an original airing over a rebroadcast of it;
      * the longest, because more runtime means more maps, and more maps
        means more heroes on one page of cards.
    """
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from automation import link_intake as li

    skip = exclude or set()
    pool = [r for r in queue if str(r.get("videoId")) not in skip
            and li.event_family(r.get("title")) in (allowed_families or [])]
    if not pool:
        return None
    originals = [r for r in pool
                 if "rebroadcast" not in str(r.get("title") or "").lower()]
    pool = originals or pool
    return max(pool, key=lambda r: (r.get("durationSeconds") or 0,
                                    str(r.get("videoId"))))


# ---------------------------------------------------------------------- CLI
def _source_from_url(url: str, title: str | None) -> dict:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from automation import link_intake as li
    parsed = li.parse_link(url)
    return {"url": parsed["canonicalUrl"], "platform": parsed.get("platform"),
            "videoId": parsed["videoId"], "title": title}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", help="ingest report dir to read crops from")
    ap.add_argument("--url", help="remote broadcast to harvest from")
    ap.add_argument("--title", help="broadcast title, shown on the page")
    ap.add_argument("--layout", help="layout json describing this HUD")
    ap.add_argument("--batch", help="batch id (default: <video>-<layout>)")
    ap.add_argument("--step", type=float, default=10.0)
    ap.add_argument("--max-frames", type=int, default=360)
    ap.add_argument("--index", action="store_true",
                    help="rebuild assets/data/labelling.v1.js")
    ap.add_argument("--check", action="store_true",
                    help="with --index: exit 1 if the committed index is stale")
    args = ap.parse_args(argv)

    if args.index:
        if args.check:
            want = index_text(build_index())
            have = (open(INDEX_JS, encoding="utf-8").read()
                    if os.path.exists(INDEX_JS) else None)
            if have != want:
                print("assets/data/labelling.v1.js is stale — run "
                      "`python3 pipeline/label_batch.py --index`")
                return 1
            print("assets/data/labelling.v1.js is current")
            return 0
        print(write_index())
        return 0

    if not args.layout or not (args.report or args.url):
        ap.error("supply --layout and one of --report / --url (or --index)")
    import capture
    layout = capture.load_layout(args.layout)
    layout_id = os.path.splitext(os.path.basename(args.layout))[0]

    acquisition: dict = {}
    if args.report:
        crops = crops_from_report(args.report)
        # --url alongside --report only RECORDS where the footage came from;
        # nothing is fetched.
        source = (_source_from_url(args.url, args.title) if args.url else
                  {"url": None, "platform": None,
                   "videoId": os.path.basename(os.path.normpath(args.report)),
                   "title": args.title})
        source["report"] = os.path.relpath(
            os.path.abspath(args.report), db.REPO_ROOT).replace(os.sep, "/")
        acquisition = {"method": "ingest-report"}
    else:
        source = _source_from_url(args.url, args.title)
        frames, stats = harvest_remote(args.url, layout, step=args.step,
                                       max_frames=args.max_frames)
        crops, states = crops_from_frames(frames, layout)
        acquisition = dict(stats, method="remote-frames", frameStates=states)
        log(f"{len(frames)} frame(s) → {len(crops)} slot crop(s); "
            f"frame states {states}")
    if not crops:
        log("no gameplay crops — nothing to label")
        return 1

    batch_id = args.batch or f"{source['videoId']}-{layout_id}"
    batch_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", batch_id)
    batch = build_batch(
        crops, batch_id=batch_id, layout=layout, layout_id=layout_id,
        source=source, out_dir=os.path.join(LABEL_ROOT, batch_id),
        crops_dir=(os.path.join(args.report, "evidence") if args.report
                   else os.path.join(CROPS_ROOT, batch_id)),
        write_crops=not args.report, acquisition=acquisition)
    write_index()
    c = batch["counts"]
    log(f"{batch_id}: {c['crops']} crops → {c['cards']} card(s) to name "
        f"({c['linkedCards']} linked by the timeline, "
        f"{c['cropsInSmallClusters']} crops in transient clusters skipped)")
    log(f"label them at label.html?batch={batch_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
