"""
self_fill.py — the layer that makes the PUBLISHED site fill itself.

The match finder already runs unattended every few hours on a free runner
and writes `assets/data/matchfinder.v1.json`: every OWCS broadcast it has
ever seen, scored with the same likeness gate intake trusts. Until this
module existed, that file was committed and then rendered by nothing — the
only page that could read it was the operator's own localhost portal
(`/api/matchfinder`). So the live site kept showing whatever a HUMAN last
pushed, while sixty-three real broadcasts sat in a committed file two
directories away. "The tracker updates itself" was true of the data and
false of the site.

This module closes that gap. It is a pure, offline, stdlib-only function of
four files already in the repo:

    assets/data/matchfinder.v1.json   what the scan found (+ intake state)
    config/owcs_calendar.json         the official event calendar
    assets/data/public_data.v1.js     what is already published
    .github/workflows/match-finder.yml  how often the scan runs

and it writes ONE artifact the static pages load like any other dataset:

    assets/data/discovered.v1.js      window.OWCS_DISCOVERED

What it adds on top of the raw scan, all of it derived and none of it
guessed:

  * a parsed reading of each title — event, stage, day, region(s), phase,
    fixture — where the title literally says so, and `null` where it does
    not. `parsed.confidence` says how much of the title was understood.
  * a link to the official calendar event whose window and region actually
    contain the broadcast, with `matchedBy` naming the evidence ("date",
    "date+region") and an explicit ambiguity list when several events
    could fit. Never a link on a coin flip.
  * a lifecycle state per broadcast — published / working / queued /
    found / ignored — computed by joining the video id against the
    PUBLISHED dataset and the intake job the scan already carries.
  * an event rollup, so the site can say "Midseason Championship: 5
    broadcasts found, 0 published" instead of listing five orphan videos.
  * the next action for each row, as both a site link and the exact
    command, so a discovered broadcast is one click from being processed.

Three invariants, because this feeds a product whose entire claim is that
it never states something a person did not verify:

  1. NOTHING here can publish a hero composition, approve a source, or
     touch the DB. It reads four files and writes one. Every row it emits
     is labelled machine-discovered, and the site renders that label.
  2. It never invents a fact. A title that does not name a region yields
     no region; a date that fits three calendar events yields no calendar
     link and says why.
  3. It is a PURE function of its inputs — `generatedAt` is copied from
     the scan that produced the data, never read from the wall clock — so
     the same inputs always produce the same bytes, a re-run commits
     nothing, and the whole thing is testable offline.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from typing import Any

from . import config as cfg

SCHEMA = "discovered.v1"

# Repo-relative paths, resolved against the config repo root.
SNAPSHOT_REL = os.path.join("assets", "data", "matchfinder.v1.json")
PUBLIC_DATA_REL = os.path.join("assets", "data", "public_data.v1.js")
CALENDAR_REL = os.path.join("config", "owcs_calendar.json")
WORKFLOW_REL = os.path.join(".github", "workflows", "match-finder.yml")
OUTPUT_REL = os.path.join("assets", "data", "discovered.v1.js")

# The lifecycle vocabulary the site already speaks (core.js STATE_WORDS),
# plus `found` for "the scan found this and nobody has done anything with
# it yet" — the whole point of this file.
STATE_PUBLISHED = "published"
STATE_REVIEW = "review"
STATE_WORKING = "working"
STATE_QUEUED = "queued"
STATE_FOUND = "found"
STATE_IGNORED = "ignored"

# How stale a scan has to be before the site calls it out. The scheduled
# scan runs several times a day; a full day without one means the workflow
# is broken, not that OWCS went quiet.
STALE_AFTER_HOURS = 24


def _repo_root() -> str:
    return cfg.REPO_ROOT


def path_for(rel: str, root: str | None = None) -> str:
    return os.path.join(root or _repo_root(), rel)


# --------------------------------------------------------------- loading
def load_json(path: str) -> dict | None:
    """Read a JSON file, or None when it is missing/unreadable. A missing
    input degrades the build (and says so in `inputs`); it never raises,
    because this runs unattended on a runner."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def load_public_data(path: str) -> dict | None:
    """Parse `window.OWCS_PUBLIC = {...};` back into a dict.

    The published dataset ships as a JS assignment (so it loads from
    file:// with no fetch and no build step), but its body is plain JSON —
    slicing between the first `{` and the last `}` is exact, not a guess,
    because the exporter writes `json.dump` output and nothing else."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def scan_interval_hours(path: str) -> float | None:
    """How often the scheduled scan runs, read from the workflow's own cron
    so the site can say "next scan within N hours" without a second copy of
    the number drifting away from the schedule.

    Only the two cron shapes this workflow actually uses are understood —
    `M */N * * *` and `M * * * *`. Anything else returns None and the site
    simply does not make the claim."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- cron:"):
            continue
        expr = stripped.split(":", 1)[1].strip().strip("\"'")
        fields = expr.split()
        if len(fields) != 5:
            continue
        hour = fields[1]
        if hour == "*":
            return 1.0
        m = re.fullmatch(r"\*/(\d{1,2})", hour)
        if m and int(m.group(1)) > 0:
            return float(int(m.group(1)))
    return None


# ------------------------------------------------------------ title read
_TAG_RE = re.compile(r"^\s*(?:\[[^\]]*\]|\([^)]*\))\s*")
_HASHTAG_RE = re.compile(r"#\w+")
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF"      # emoji + pictographs
    "←-⇿⌀-⏿"  # arrows, technical symbols
    "①-➿⬀-⯿"  # enclosed alphanumerics, dingbats
    "️‍]")              # variation selector, ZWJ
_DAY_RE = re.compile(r"\bday[\s-]*(\d{1,2})\b", re.I)
_WEEK_RE = re.compile(r"\bweek[\s-]*(\d{1,2})\b", re.I)
_STAGE_RE = re.compile(r"\bstage[\s-]*(\d{1,2})\b", re.I)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_VS_RE = re.compile(r"^(.{2,60}?)\s+(?:vs\.?|versus)\s+(.{2,60})$", re.I)

# Region words, in the exact forms broadcast titles use. Two-letter codes
# are matched only as standalone words: "NA" is a region, "na" inside a
# word is not.
_REGION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("na", r"\bnorth\s+america\b|\bnorth\s+american\b|\bna\b"),
    ("emea", r"\bemea\b|\beurope\b|\beuropean\b"),
    ("korea", r"\bkorea\b|\bkorean\b|\bkr\b"),
    ("japan", r"\bjapan\b|\bjapanese\b|\bjp\b"),
    ("pacific", r"\bpacific\b|\bapac\b|\basia[- ]pacific\b"),
    ("china", r"\bchina\b|\bchinese\b|\bcn\b"),
)

# Phase words, most specific first — "grand finals" must win over "finals".
_PHASE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("grand-finals", r"\bgrand\s+finals?\b"),
    ("promotion-relegation", r"\bpromotion\s*/?\s*relegation\b|\bpro[- ]?rel\b"),
    ("midseason-championship", r"\bmidseason\s+championship\b"),
    ("championship", r"\bchampionship\b"),
    ("playoffs", r"\bplay-?offs?\b"),
    ("qualifier", r"\bqualifiers?\b|\bopen\s+quals?\b"),
    ("group-stage", r"\bgroup\s+stage\b|\bgroups\b"),
    ("finals", r"\bfinals?\b"),
    ("showmatch", r"\bshow\s?match\b"),
)

# Uploads that are ABOUT a broadcast rather than being one. They are kept
# in the payload (hiding them would make the archive lie) but they are
# never offered as something to process.
_COMPANION_RE = re.compile(
    r"\brecap\b|\bhighlights?\b|\btop\s+plays?\b|\btrailer\b|\bpreview\b|"
    r"\bpress\s+conference\b|\bwatch\s+party\b|\binterview\b|\bteaser\b|"
    r"\bpatch\s+notes\b|\bexplained\b", re.I)


def _slug(text: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return out or "unknown"


def clean_title(title: str | None) -> str:
    """Broadcast title with leading `[DROPS]`-style tags, hashtags and
    emoji removed. Everything else is left exactly as the channel wrote
    it — this is a reading of the title, not a rewrite of it."""
    text = str(title or "")
    text = _EMOJI_RE.sub(" ", text)
    text = _HASHTAG_RE.sub(" ", text)
    while True:
        stripped = _TAG_RE.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    return re.sub(r"\s+", " ", text).strip(" -–—:|")


def parse_title(title: str | None) -> dict[str, Any]:
    """Read what a broadcast title literally says.

    Every field is None/empty unless the title states it. `confidence` is
    a plain-language summary of how much was understood, so the site can
    show "read from the title" honestly rather than implying a lookup
    happened."""
    cleaned = clean_title(title)
    low = cleaned.lower()

    day = _DAY_RE.search(cleaned)
    week = _WEEK_RE.search(cleaned)
    stage = _STAGE_RE.search(cleaned)
    year = _YEAR_RE.search(cleaned)

    regions = [rid for rid, pattern in _REGION_PATTERNS
               if re.search(pattern, low)]
    phase = next((pid for pid, pattern in _PHASE_PATTERNS
                  if re.search(pattern, low)), None)

    # Event name: the title minus the parts that identify one BROADCAST
    # inside the event (Day 3, Week 2). Stage stays — a stage is part of
    # an event's identity; a day is not.
    parts = [p.strip() for p in re.split(r"\s*\|\s*", cleaned) if p.strip()]
    kept: list[str] = []
    for part in parts:
        without = _WEEK_RE.sub("", _DAY_RE.sub("", part))
        # "Homecoming 2025 [DAY 1]" loses its day and is left holding an
        # empty bracket, which would otherwise split one event into two.
        without = re.sub(r"[\[(]\s*[\])]", " ", without)
        without = re.sub(r"\s+", " ", without).strip(" -–—:")
        if without:
            kept.append(without)
    event_name = " — ".join(kept) if kept else cleaned

    fixture = None
    for part in parts:
        m = _VS_RE.match(part.strip())
        if m:
            fixture = [m.group(1).strip(), m.group(2).strip()]
            break

    known = sum(1 for v in (day or week, stage, phase, regions, year) if v)
    confidence = ("clear" if event_name and known >= 2
                  else "partial" if event_name and known >= 1
                  else "title-only")

    return {
        "cleanTitle": cleaned,
        "eventName": event_name,
        "eventKey": _slug(event_name),
        "year": int(year.group(1)) if year else None,
        "stage": int(stage.group(1)) if stage else None,
        "day": int(day.group(1)) if day else None,
        "week": int(week.group(1)) if week else None,
        "regions": regions,
        "phase": phase,
        "fixture": fixture,
        "companion": bool(_COMPANION_RE.search(low)),
        "confidence": confidence,
    }


# ------------------------------------------------------------- calendar
def _date_of(iso: str | None) -> str | None:
    return str(iso)[:10] if iso else None


def _stage_number(stage: Any) -> int | None:
    """`"Stage 2"` -> 2. The calendar writes the stage as prose; titles
    write it as prose too, and only the number is comparable."""
    m = re.search(r"(\d{1,2})", str(stage or ""))
    return int(m.group(1)) if m else None


def match_calendar_event(parsed: dict, published_at: str | None,
                         events: list[dict]) -> dict:
    """Link one broadcast to the official calendar event that actually
    contains it, or say why it could not be linked.

    Evidence is layered: the date window is the floor, then the region the
    title names, then the stage number it names. `matchedBy` records
    exactly which of those did the narrowing ("date+region+stage"), so a
    link is always accompanied by the reason it was made. When several
    events survive and nothing in the title distinguishes them, the link
    is refused and every candidate is listed — an ambiguous link presented
    as a fact is exactly the kind of invented certainty this product
    exists to avoid."""
    def _refusal(why: str, candidates: list[dict] | None = None) -> dict:
        return {"eventId": None, "eventName": None, "eventIds": [],
                "matchedBy": None,
                "candidates": sorted(str(e.get("id"))
                                     for e in (candidates or [])),
                "why": why}

    day = _date_of(published_at)
    if not day:
        return _refusal("the broadcast has no publish date")

    in_window = [e for e in events
                 if e.get("startDate") and e.get("endDate")
                 and str(e["startDate"]) <= day <= str(e["endDate"])]
    if not in_window:
        return _refusal("no calendar event covers this date")

    pool, evidence = in_window, ["date"]

    regions = parsed.get("regions") or []
    if regions:
        narrowed = [e for e in pool if e.get("region") in regions]
        if narrowed:
            pool, evidence = narrowed, evidence + ["region"]

    stage = parsed.get("stage")
    if stage is not None:
        narrowed = [e for e in pool
                    if _stage_number(e.get("stage")) == stage]
        if narrowed:
            pool, evidence = narrowed, evidence + ["stage"]
    matched_by = "+".join(evidence)
    ids = sorted(str(e.get("id")) for e in pool)

    # A single event is a clean link. Several events are still a real link
    # when the TITLE itself named what narrowed them — an "NA/EMEA Stage 2"
    # broadcast genuinely belongs to both regional events, and saying so is
    # more accurate than refusing. Several events narrowed by nothing but
    # the date is a coin flip, and is refused.
    if len(pool) == 1:
        ev = pool[0]
        return {"eventId": ev.get("id"), "eventName": ev.get("name"),
                "eventIds": ids, "matchedBy": matched_by,
                "candidates": ids, "why": None}
    if len(evidence) > 1:
        return {"eventId": None,
                "eventName": " · ".join(str(e.get("name")) for e in pool),
                "eventIds": ids, "matchedBy": matched_by,
                "candidates": ids,
                "why": f"the title covers {len(pool)} calendar events"}
    return _refusal(f"{len(pool)} calendar events cover this date — "
                    "the title does not say which", pool)


# -------------------------------------------------------------- publish
_VIDEO_ID_RES = (
    re.compile(r"[?&]v=([A-Za-z0-9_-]{6,})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{6,})"),
    re.compile(r"youtube\.com/(?:live|embed|shorts)/([A-Za-z0-9_-]{6,})"),
    # A Twitch VOD. Without this a broadcast discovered on Twitch and later
    # published would never join against its own published record, and the
    # site would offer to process a match it had already published.
    re.compile(r"twitch\.tv/videos/([0-9]{6,15})"),
)


def video_id_from_url(url: str | None) -> str | None:
    """The video id inside a URL, in any of the forms the published dataset
    uses (watch?v=, youtu.be/, /live/, /embed/, twitch.tv/videos/)."""
    text = str(url or "")
    for pattern in _VIDEO_ID_RES:
        m = pattern.search(text)
        if m:
            return m.group(1)
    return None


def published_video_index(public: dict | None) -> dict[str, dict]:
    """video id -> the published match it belongs to.

    Every place the published dataset can name a video is consulted (a
    match's streamUrl, its `sources`, its VOD sources, its capture run),
    because a broadcast the site has ALREADY published must never be
    offered as an undiscovered find."""
    index: dict[str, dict] = {}
    if not public:
        return index
    runs = {r.get("id"): r for r in (public.get("captureRuns") or [])
            if isinstance(r, dict)}
    vods: dict[str, list[str]] = {}
    for v in public.get("vodSources") or []:
        if isinstance(v, dict) and v.get("matchId"):
            vods.setdefault(str(v["matchId"]), []).append(str(v.get("url") or ""))

    for match in public.get("matches") or []:
        if not isinstance(match, dict):
            continue
        urls = [match.get("streamUrl")]
        urls += [s.get("url") for s in (match.get("sources") or [])
                 if isinstance(s, dict)]
        urls += vods.get(str(match.get("id")), [])
        run = runs.get(match.get("captureRunId"))
        if isinstance(run, dict):
            urls.append(run.get("url"))
        for url in urls:
            vid = video_id_from_url(url)
            if vid:
                index[vid] = match
    return index


# The intake job states the scan carries, mapped onto the site's words.
_JOB_STATE_WORDS = {
    "NEW": STATE_QUEUED,
    "QUEUED": STATE_QUEUED,
    "ARCHIVED": STATE_QUEUED,
    "DOWNLOADING": STATE_WORKING,
    "DOWNLOADED": STATE_WORKING,
    "SEGMENTING": STATE_WORKING,
    "SEGMENTED": STATE_WORKING,
    "DETECTING": STATE_WORKING,
    "NEEDS_REVIEW": STATE_REVIEW,
    "READY_FOR_DETECTION": STATE_WORKING,
    "READY_FOR_PUBLISH": STATE_REVIEW,
    "PUBLISHED": STATE_PUBLISHED,
}


def broadcast_state(row: dict, published: dict[str, dict]) -> dict:
    """Where one discovered broadcast stands, and why.

    The `why` is written for a visitor, not an operator: it is the sentence
    the site prints under the row."""
    vid = str(row.get("videoId") or "")
    match = published.get(vid)
    if match:
        return {"state": STATE_PUBLISHED, "matchId": match.get("id"),
                "why": "This broadcast has been read, reviewed and published."}

    job = row.get("job") or None
    if isinstance(job, dict) and job.get("state"):
        state = _JOB_STATE_WORDS.get(str(job["state"]).upper(), STATE_WORKING)
        return {"state": state, "matchId": None,
                "why": f"A tracker is working on this ({job['state']})."}

    likeness = row.get("likeness") or {}
    if likeness.get("refused"):
        return {"state": STATE_IGNORED, "matchId": None,
                "why": likeness.get("refusalReason")
                or "Too short to be a broadcast."}
    if likeness.get("confidence") != "likely":
        return {"state": STATE_IGNORED, "matchId": None,
                "why": "Scored as unlikely to be a match broadcast."}
    return {"state": STATE_FOUND, "matchId": None,
            "why": "Found automatically. Nobody has processed it yet."}


def next_action(state: str, row: dict, parsed: dict) -> dict | None:
    """The one thing that moves this row forward — as a site link AND the
    exact command, because the published copy can show the button while
    only a connected tracker can run the command."""
    url = str(row.get("url") or "")
    if state == STATE_FOUND and not parsed.get("companion"):
        return {
            "label": "Process this broadcast",
            "href": "submit.html?url=" + url,
            "command": 'python3 pipeline/automation/cli.py convert-link '
                       f'--url "{url}"',
        }
    if state == STATE_REVIEW:
        return {"label": "Review the detections", "href": "review.html",
                "command": None}
    if state == STATE_PUBLISHED:
        return None
    return None


# ----------------------------------------------------------------- build
def _event_rollup(broadcasts: list[dict]) -> list[dict]:
    """One row per event the scan has evidence for, so the site groups five
    days of one championship as one thing.

    Everything here is a fold over what the broadcasts themselves already
    say — the years, stages, phases, regions, day and week numbers their
    own titles state, and the runtimes the source reported. Nothing is
    looked up and nothing is inferred: an event whose broadcasts never name
    a year has no year, and says so."""
    events: dict[str, dict] = {}
    for b in broadcasts:
        if b["state"] == STATE_IGNORED:
            continue
        parsed = b["parsed"]
        key = parsed["eventKey"]
        ev = events.setdefault(key, {
            "key": key,
            "name": parsed["eventName"],
            "calendarEventIds": list(b["calendar"].get("eventIds") or []),
            "regions": [],
            "broadcasts": 0,
            "published": 0,
            "found": 0,
            "days": [],
            "weeks": [],
            "years": [],
            "stages": [],
            "phases": [],
            "channels": [],
            # Runtime the SOURCE reported, and how many rows carried one.
            # Two separate numbers on purpose: "412 hours" over 38 of 41
            # broadcasts is a floor, not a total, and the site says so.
            "runtimeSeconds": 0,
            "runtimeKnown": 0,
            # How many of this event's broadcasts have a real air date.
            # Usually 0 — see date_coverage() for why that is not a bug in
            # this function.
            "dated": 0,
            "firstAt": b.get("publishedAt"),
            "lastAt": b.get("publishedAt"),
        })
        ev["broadcasts"] += 1
        if b["state"] == STATE_PUBLISHED:
            ev["published"] += 1
        if b["state"] == STATE_FOUND:
            ev["found"] += 1
        if b.get("publishedAt"):
            ev["dated"] += 1
        runtime = b.get("durationSeconds")
        if isinstance(runtime, (int, float)) and runtime > 0:
            ev["runtimeSeconds"] += int(runtime)
            ev["runtimeKnown"] += 1
        for field, value in (("days", parsed["day"]), ("weeks", parsed["week"]),
                             ("years", parsed["year"]), ("stages", parsed["stage"]),
                             ("phases", parsed["phase"]),
                             ("channels", b.get("channelTitle"))):
            if value not in (None, "") and value not in ev[field]:
                ev[field].append(value)
        for r in parsed["regions"]:
            if r not in ev["regions"]:
                ev["regions"].append(r)
        for cal_id in b["calendar"].get("eventIds") or []:
            if cal_id not in ev["calendarEventIds"]:
                ev["calendarEventIds"].append(cal_id)
        for field, better in (("firstAt", min), ("lastAt", max)):
            when = b.get("publishedAt")
            if when and ev[field]:
                ev[field] = better(ev[field], when)
            elif when:
                ev[field] = when
    for ev in events.values():
        for field in ("days", "weeks", "years", "stages"):
            ev[field].sort()
        for field in ("regions", "phases", "channels"):
            ev[field].sort()
        # The one year the site labels this event with. Only when the
        # event's broadcasts agree; a title set that names two years gets
        # none rather than an arbitrary pick.
        ev["season"] = ev["years"][0] if len(ev["years"]) == 1 else None
    return sorted(events.values(),
                  key=lambda e: (e["lastAt"] or "", e["name"]), reverse=True)


def _season_rollup(events: list[dict]) -> list[dict]:
    """The archive by competitive year, because that is how anyone looking
    for a broadcast thinks about it.

    An event whose titles never state a year lands in the `season: null`
    bucket rather than being guessed into one. Sorted newest first with
    that bucket last, so the page never opens on the unknowns."""
    seasons: dict[Any, dict] = {}
    for ev in events:
        key = ev["season"]
        row = seasons.setdefault(key, {
            "season": key, "events": 0, "broadcasts": 0, "found": 0,
            "published": 0, "runtimeSeconds": 0, "runtimeKnown": 0,
            "dated": 0, "eventKeys": [],
        })
        row["events"] += 1
        row["eventKeys"].append(ev["key"])
        for field in ("broadcasts", "found", "published", "runtimeSeconds",
                      "runtimeKnown", "dated"):
            row[field] += ev[field]
    for row in seasons.values():
        row["eventKeys"].sort()
    return sorted(seasons.values(),
                  key=lambda r: (r["season"] is not None, r["season"] or 0),
                  reverse=True)


#: Every source error the scan writes while trying to give an archived
#: broadcast its real air date. Matched as a prefix because the rest of the
#: string is the video id and the source's own message.
_DATE_ERROR_PREFIX = "date-backfill"


def date_coverage(broadcasts: list[dict],
                  source_errors: list[str]) -> dict[str, Any]:
    """How much of the archive has a real air date, and — when most of it
    does not — the reason taken from the scan's own errors.

    This exists because "date unknown" on 246 rows is not an answer. The
    scan knows exactly why: the channel listing it reads in one cheap
    request carries no timestamp, and the per-video lookup that would
    supply one is being refused by the source. Saying that is the
    difference between a site that looks broken and a site that is honest
    about a blocker it does not control."""
    considered = [b for b in broadcasts if b["state"] != STATE_IGNORED]
    known = sum(1 for b in considered if b.get("publishedAt"))
    # Per-video refusals only. The finder also writes ONE summary line when
    # it trips its circuit breaker; counting that as a failed lookup would
    # overstate how many requests were actually spent.
    refusals = [e for e in source_errors
                if str(e).startswith(_DATE_ERROR_PREFIX + " ")
                and not str(e).startswith(_DATE_ERROR_PREFIX + ": ")]
    stopped = [e for e in source_errors
               if str(e).startswith(_DATE_ERROR_PREFIX + ": stopped")]
    unknown = len(considered) - known
    if unknown and (refusals or stopped):
        reason = ("The scan reads a whole channel in one cheap request, and "
                  "that listing carries no air date. The per-video lookup "
                  "that would supply one is currently being refused by the "
                  "source, so these broadcasts keep the date they came with "
                  "— none.")
        blocked = True
    elif unknown:
        reason = ("The scan reads a whole channel in one cheap request, and "
                  "that listing carries no air date. A bounded number of "
                  "per-video lookups per run fills them in over time.")
        blocked = False
    else:
        reason = None
        blocked = False
    return {
        "known": known,
        "unknown": unknown,
        "considered": len(considered),
        "blocked": blocked,
        "failedLookups": len(refusals),
        "reason": reason,
    }


def build(*, snapshot: dict | None = None, public: dict | None = None,
          calendar: dict | None = None, interval_hours: float | None = None,
          root: str | None = None) -> dict:
    """Assemble the whole payload. Every argument defaults to the committed
    file, so the normal call is `build()` and the tests pass fixtures."""
    inputs: list[dict] = []

    def _record(name: str, rel: str, ok: bool) -> None:
        inputs.append({"name": name, "path": rel.replace(os.sep, "/"),
                       "loaded": ok})

    if snapshot is None:
        snapshot = load_json(path_for(SNAPSHOT_REL, root))
        _record("scan", SNAPSHOT_REL, snapshot is not None)
    if public is None:
        public = load_public_data(path_for(PUBLIC_DATA_REL, root))
        _record("published dataset", PUBLIC_DATA_REL, public is not None)
    if calendar is None:
        calendar = load_json(path_for(CALENDAR_REL, root))
        _record("official calendar", CALENDAR_REL, calendar is not None)
    if interval_hours is None:
        interval_hours = scan_interval_hours(path_for(WORKFLOW_REL, root))

    snapshot = snapshot or {}
    events = [e for e in ((calendar or {}).get("events") or [])
              if isinstance(e, dict)]
    published = published_video_index(public)

    broadcasts: list[dict] = []
    for row in snapshot.get("candidates") or []:
        if not isinstance(row, dict) or not row.get("videoId"):
            continue
        parsed = parse_title(row.get("title"))
        cal = match_calendar_event(parsed, row.get("publishedAt"), events)
        state = broadcast_state(row, published)
        likeness = row.get("likeness") or {}
        # Absent means YouTube — the platform every row meant before
        # broadcasts existed on more than one. Emitting it only when it
        # differs keeps a YouTube-only scan rebuilding byte-identically
        # instead of restating one constant across 288 rows.
        platform = row.get("platform") or "youtube"
        broadcasts.append({
            "videoId": row["videoId"],
            **({"platform": platform} if platform != "youtube" else {}),
            "url": row.get("url"),
            "title": row.get("title"),
            "channelId": row.get("channelId"),
            "channelTitle": row.get("channelTitle"),
            "publishedAt": row.get("publishedAt"),
            "durationSeconds": row.get("durationSeconds"),
            "firstSeenAt": row.get("firstSeenAt"),
            "lastSeenAt": row.get("lastSeenAt"),
            "liveStatus": row.get("liveBroadcastStatus"),
            "sources": row.get("sources") or [],
            "likeness": {
                "confidence": likeness.get("confidence"),
                "score": likeness.get("score"),
                "reasons": likeness.get("reasons") or [],
            },
            "parsed": parsed,
            "calendar": cal,
            "state": state["state"],
            "why": state["why"],
            "matchId": state["matchId"],
            "nextAction": next_action(state["state"], row, parsed),
        })

    # Newest first, with the undated tail kept at the end rather than
    # sorted to the top on an empty string.
    dated = sorted((b for b in broadcasts if b["publishedAt"]),
                   key=lambda b: str(b["publishedAt"]), reverse=True)
    broadcasts = dated + [b for b in broadcasts if not b["publishedAt"]]

    counts = {"total": len(broadcasts)}
    for state in (STATE_PUBLISHED, STATE_REVIEW, STATE_WORKING,
                  STATE_QUEUED, STATE_FOUND, STATE_IGNORED):
        counts[state] = sum(1 for b in broadcasts if b["state"] == state)
    actionable = counts[STATE_FOUND]

    scan_at = snapshot.get("generatedAt")
    channels = snapshot.get("channels") or []
    source_errors = [e for e in (snapshot.get("sourceErrors") or [])]
    rollup = _event_rollup(broadcasts)
    seasons = _season_rollup(rollup)
    dates = date_coverage(broadcasts, source_errors)
    calendar_linked = sum(1 for b in broadcasts
                          if b["calendar"].get("eventIds"))
    runtime_seconds = sum(e["runtimeSeconds"] for e in rollup)
    runtime_known = sum(e["runtimeKnown"] for e in rollup)

    return {
        "schema": SCHEMA,
        # Copied from the scan on purpose: this payload is a pure function
        # of its inputs, so identical inputs produce identical bytes and a
        # rebuild that found nothing new commits nothing.
        "generatedAt": scan_at,
        "scan": {
            "generatedAt": scan_at,
            "intervalHours": interval_hours,
            "nextExpectedAt": _plus_hours(scan_at, interval_hours),
            "staleAfterHours": STALE_AFTER_HOURS,
            "channels": [{"id": c.get("id"), "title": c.get("title"),
                          "channelId": c.get("channelId"),
                          "sourceUrl": c.get("sourceUrl")}
                         for c in channels if isinstance(c, dict)],
            "sourceErrors": list(source_errors),
        },
        "inputs": inputs,
        "counts": counts,
        "summary": {
            "broadcastsKnown": counts["total"],
            "awaitingProcessing": actionable,
            "published": counts[STATE_PUBLISHED],
            "inFlight": counts[STATE_WORKING] + counts[STATE_QUEUED]
                        + counts[STATE_REVIEW],
            "ignored": counts[STATE_IGNORED],
            "events": len(rollup),
            "calendarLinked": calendar_linked,
            "channelsScanned": len(channels),
            # The archive's real scale, as two honest numbers: how much
            # runtime the source reported, over how many broadcasts it
            # reported one for. Never one number implying the other.
            "runtimeSeconds": runtime_seconds,
            "runtimeKnown": runtime_known,
            "seasons": len([s for s in seasons if s["season"] is not None]),
            "datedBroadcasts": dates["known"],
        },
        "dateCoverage": dates,
        "seasons": seasons,
        "events": rollup,
        "broadcasts": broadcasts,
    }


def _plus_hours(iso: str | None, hours: float | None) -> str | None:
    if not iso or not hours:
        return None
    try:
        base = dt.datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    return (base + dt.timedelta(hours=hours)).replace(
        microsecond=0).isoformat()


# ----------------------------------------------------------------- write
_HEADER = """\
/* =====================================================================
   OWCS Comp Tracker — discovered.v1.js  (AUTOMATIC DISCOVERY LAYER)

   GENERATED by pipeline/automation/self_fill.py from the unattended
   match-finder scan. Do not hand-edit.

   This is NOT published match data. Every row here is a broadcast the
   scan found on a verified official channel, with whatever its own title
   says about it — never a hero composition, never a result, never a fact
   a person has confirmed. The published record stays in
   assets/data/public_data.v1.js and the two are never merged.
   ===================================================================== */
"""


def render_js(payload: dict) -> str:
    body = json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False)
    return _HEADER + "\nwindow.OWCS_DISCOVERED = " + body + ";\n"


def write(payload: dict, path: str | None = None) -> str:
    path = path or path_for(OUTPUT_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(render_js(payload))
    return path


# ---------------------------------------------------------------- report
def format_report(payload: dict, *, now: dt.datetime | None = None) -> str:
    """Terminal rendering — what the workflow log and the operator see."""
    s = payload.get("summary") or {}
    scan = payload.get("scan") or {}
    lines = [
        f"[self-fill] {s.get('broadcastsKnown', 0)} broadcast(s) known · "
        f"{s.get('awaitingProcessing', 0)} awaiting processing · "
        f"{s.get('inFlight', 0)} in flight · "
        f"{s.get('published', 0)} published · "
        f"{s.get('ignored', 0)} ignored",
        f"[self-fill] {s.get('events', 0)} event(s) derived · "
        f"{s.get('calendarLinked', 0)} broadcast(s) linked to the official "
        f"calendar · {s.get('channelsScanned', 0)} channel(s) scanned",
    ]
    age = scan_age_hours(payload, now=now)
    if age is not None:
        stale = age > (scan.get("staleAfterHours") or STALE_AFTER_HOURS)
        lines.append(f"[self-fill] last scan {age:.1f}h ago"
                     + (" — STALE, check the match-finder workflow" if stale
                        else ""))
    for err in scan.get("sourceErrors") or []:
        lines.append(f"  SOURCE ERROR: {err}")
    for missing in [i for i in payload.get("inputs") or []
                    if not i.get("loaded")]:
        lines.append(f"  MISSING INPUT: {missing.get('path')} — the layer "
                     "still built, with less to say")
    for ev in (payload.get("events") or [])[:10]:
        lines.append(f"  {ev['name'][:60]:60} {ev['broadcasts']:>3} found "
                     f"{ev['published']:>3} published")
    return "\n".join(lines)


def scan_age_hours(payload: dict, *, now: dt.datetime | None = None
                   ) -> float | None:
    """Hours since the scan that produced this payload. Kept OUT of the
    payload itself so the artifact stays a pure function of its inputs."""
    when = (payload.get("scan") or {}).get("generatedAt")
    if not when:
        return None
    try:
        base = dt.datetime.fromisoformat(str(when))
    except ValueError:
        return None
    if base.tzinfo is None:
        base = base.replace(tzinfo=dt.timezone.utc)
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    return (now - base).total_seconds() / 3600.0
