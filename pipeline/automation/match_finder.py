"""
match_finder.py — automatic OWCS broadcast discovery on permanently free
sources (the "match finder").

Two no-key, no-quota, $0 sources, merged per verified channel:

  * The channel's public RSS feed
    (https://www.youtube.com/feeds/videos.xml?channel_id=...) — stdlib
    urllib + xml.etree. Free forever, no API key, no quota, ~15 newest
    videos with title/published/description.
  * `yt-dlp --flat-playlist` on the channel's /streams tab — no key, no
    login, reaches the full livestream archive (bounded by --limit) and
    carries the duration + live status the RSS feed lacks.

Only channels from the VERIFIED registry (config/broadcast_channels.json,
enabled + confirmed channelId) are scanned — the same authority rule the
intake path enforces. Every candidate is scored with the SAME tuned
broadcast-likeness gate production intake trusts
(broadcast_matching.broadcast_likeness): a promo/guide/Shorts upload is
labeled "unlikely" WITH its reasons, never silently dropped and never
silently included.

Hard guarantees (match the rest of the automation layer):
  * Never downloads video, never writes comps, never approves anything.
    Queueing a candidate goes through the SAME `link_intake.ingest_link`
    gate as a hand-pasted URL — one deterministic job per video, source
    approval rules unchanged.
  * Idempotent ledger (data/match_finder.json): re-scanning never
    duplicates a candidate and never loses `firstSeen`.
  * A source failure is recorded as a named error in the report; the other
    source still contributes. A status/report read never raises.
  * No credential anywhere: both sources are public and unauthenticated.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable

from . import broadcast_matching as bmatch
from . import config as cfg
from . import link_intake as li
from . import job_store as js
import proc_text  # noqa: E402  (UTF-8 subprocess decoding)

SCHEMA = "matchfinder.v1"
RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
DEFAULT_LIMIT = 60
FETCH_TIMEOUT = 20
# Repo-relative artifact paths (resolved against config repo root).
LEDGER_REL = os.path.join("data", "match_finder.json")
SNAPSHOT_REL = os.path.join("assets", "data", "matchfinder.v1.json")

_ATOM = "{http://www.w3.org/2005/Atom}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"
_MEDIA = "{http://search.yahoo.com/mrss/}"

# yt-dlp flat-playlist live_status -> the liveBroadcastStatus vocabulary
# broadcast_likeness was tuned on (youtube_api normalization).
_LIVE_STATUS = {"was_live": "completed", "is_live": "live",
                "post_live": "completed", "is_upcoming": "upcoming"}


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _repo_root() -> str:
    return cfg.REPO_ROOT


def ledger_path() -> str:
    return os.path.join(_repo_root(), LEDGER_REL)


def snapshot_path() -> str:
    return os.path.join(_repo_root(), SNAPSHOT_REL)


# ------------------------------------------------------------- channels
def scan_channels(channels: list[dict] | None = None) -> list[dict]:
    """Verified + enabled registry channels with a confirmed channelId —
    the only channels the finder will scan (never a guessed handle)."""
    idx = li.registry_channel_index(channels)
    out = []
    for cid, ch in idx.items():
        out.append({
            "id": ch.get("id"),
            "channelId": cid,
            "title": ch.get("title") or ch.get("id"),
            "sourceUrl": ch.get("sourceUrl"),
        })
    return sorted(out, key=lambda c: str(c.get("id")))


def channel_streams_url(channel: dict) -> str | None:
    """The /streams URL to scan for one channel.

    Built from the confirmed `channelId` whenever there is one:
    `/channel/<id>/streams` is the canonical form that always resolves,
    whereas a registry `sourceUrl` may be a legacy custom URL
    (youtube.com/OW_Esports) whose /streams sub-path does not. Falls back
    to the recorded sourceUrl only when no channelId exists — which, for a
    registry channel, `scan_channels` has already ruled out."""
    cid = channel.get("channelId")
    if cid:
        return f"https://www.youtube.com/channel/{cid}/streams"
    return channel.get("sourceUrl") or None


# ------------------------------------------------------------------ RSS
def _http_fetch(url: str, timeout: float = FETCH_TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": "owcs-comp-tracker/1.0 (match finder; free RSS poll)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_rss(xml_bytes: bytes) -> list[dict]:
    """YouTube channel Atom feed -> normalized entries. Malformed entries
    are skipped individually; a malformed document raises ValueError for
    the caller to record as a source error."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"unparseable RSS feed: {exc}") from exc
    out = []
    for entry in root.findall(f"{_ATOM}entry"):
        vid_el = entry.find(f"{_YT}videoId")
        vid = vid_el.text.strip() if vid_el is not None and vid_el.text else None
        if not vid:
            continue
        title_el = entry.find(f"{_ATOM}title")
        pub_el = entry.find(f"{_ATOM}published")
        desc = None
        group = entry.find(f"{_MEDIA}group")
        if group is not None:
            d = group.find(f"{_MEDIA}description")
            desc = d.text if d is not None else None
        author = entry.find(f"{_ATOM}author/{_ATOM}name")
        out.append({
            "videoId": vid,
            "title": title_el.text if title_el is not None else None,
            "publishedAt": pub_el.text if pub_el is not None else None,
            "description": desc,
            "channelTitle": author.text if author is not None else None,
            "durationSeconds": None,       # the RSS feed does not carry it
            "liveBroadcastStatus": None,   # nor this
        })
    return out


def fetch_rss_channel(channel_id: str,
                      fetch: Callable[[str], bytes] = _http_fetch
                      ) -> tuple[list[dict], str | None]:
    """(entries, error). Never raises — a feed failure is a recorded error,
    not a crash, so the streams-tab source can still contribute."""
    url = RSS_URL.format(cid=channel_id)
    try:
        return parse_rss(fetch(url)), None
    except Exception as exc:  # noqa: BLE001 — every failure becomes a report row
        return [], f"rss {channel_id}: {type(exc).__name__}: {exc}"


def _iso_from_epoch(value) -> str | None:
    """Unix seconds -> UTC ISO-8601, or None for anything that is not a
    usable epoch."""
    if isinstance(value, (int, float)) and value > 0:
        return dt.datetime.fromtimestamp(
            value, dt.timezone.utc).replace(microsecond=0).isoformat()
    return None


# ---------------------------------------------------------- streams tab
def fetch_streams_tab(channel_url: str, limit: int = DEFAULT_LIMIT,
                      runner=subprocess) -> tuple[list[dict], str | None]:
    """One `yt-dlp --flat-playlist -J` metadata dump of the channel's
    /streams tab. (entries, error); never raises."""
    url = channel_url.rstrip("/")
    if not url.endswith("/streams"):
        url += "/streams"
    cmd = ["yt-dlp", "--flat-playlist", "--playlist-end", str(limit),
           "-J", "--no-warnings", url]
    try:
        res = runner.run(cmd, capture_output=True, text=True, timeout=120,
                         **proc_text.PIPE_TEXT)
    except FileNotFoundError:
        return [], "streams-tab: yt-dlp not found on PATH"
    except Exception as exc:  # noqa: BLE001
        return [], f"streams-tab: {type(exc).__name__}: {exc}"
    if res.returncode != 0:
        tail = (res.stderr or "").strip()[-300:]
        return [], f"streams-tab: yt-dlp exit {res.returncode}: {tail}"
    try:
        payload = json.loads(res.stdout or "{}")
    except ValueError:
        return [], "streams-tab: unparseable yt-dlp JSON"
    out = []
    for e in payload.get("entries") or []:
        vid = e.get("id")
        if not vid:
            continue
        dur = e.get("duration")
        # A finished livestream carries `release_timestamp` (when it went
        # live) far more often than `timestamp` in a flat-playlist dump, and
        # for a broadcast that IS the air date. Reading only `timestamp` was
        # why sixty of ninety-two archived broadcasts had no date at all —
        # and a broadcast with no date can never be placed on the calendar.
        published = _iso_from_epoch(e.get("timestamp")) or \
            _iso_from_epoch(e.get("release_timestamp"))
        out.append({
            "videoId": vid,
            "title": e.get("title"),
            "publishedAt": published,
            "description": e.get("description"),
            "channelTitle": e.get("channel") or e.get("uploader"),
            "durationSeconds": int(dur) if isinstance(dur, (int, float)) else None,
            "liveBroadcastStatus": _LIVE_STATUS.get(e.get("live_status")),
        })
    return out, None


# -------------------------------------------------------- date backfill
def fetch_video_metadata(video_id: str, runner=subprocess
                         ) -> tuple[dict | None, str | None]:
    """One metadata-only `yt-dlp -J` for a single video. (fields, error);
    never raises.

    `--skip-download` is belt and braces next to `-J` (which already only
    dumps JSON): this function must be incapable of pulling media, because
    the whole finder's safety claim is that it never downloads video."""
    url = li.canonical_url(video_id)
    cmd = ["yt-dlp", "-J", "--skip-download", "--no-warnings",
           "--no-playlist", url]
    try:
        res = runner.run(cmd, capture_output=True, text=True, timeout=60,
                         **proc_text.PIPE_TEXT)
    except FileNotFoundError:
        return None, "date-backfill: yt-dlp not found on PATH"
    except Exception as exc:  # noqa: BLE001
        return None, f"date-backfill {video_id}: {type(exc).__name__}: {exc}"
    if res.returncode != 0:
        tail = (res.stderr or "").strip()[-200:]
        return None, f"date-backfill {video_id}: yt-dlp exit {res.returncode}: {tail}"
    try:
        info = json.loads(res.stdout or "{}")
    except ValueError:
        return None, f"date-backfill {video_id}: unparseable yt-dlp JSON"
    published = (_iso_from_epoch(info.get("release_timestamp"))
                 or _iso_from_epoch(info.get("timestamp")))
    if not published:
        # upload_date is a bare YYYYMMDD with no time. Midnight UTC is a
        # deliberate, visible approximation of a DATE we do have — it is
        # never invented, and it is only ever used when the precise
        # timestamp genuinely is not published.
        raw = str(info.get("upload_date") or "")
        if len(raw) == 8 and raw.isdigit():
            published = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}T00:00:00+00:00"
    dur = info.get("duration")
    return {
        "publishedAt": published,
        "durationSeconds": int(dur) if isinstance(dur, (int, float)) else None,
        "liveBroadcastStatus": _LIVE_STATUS.get(info.get("live_status")),
    }, None


def fill_missing_dates(ledger: dict, *, limit: int = 0, runner=subprocess,
                       fetch_meta: Callable[[str], tuple[dict | None, str | None]]
                       | None = None) -> tuple[dict, list[str], int]:
    """Give dateless archived broadcasts their real air date, a bounded
    number per scan. Returns (ledger, errors, filled).

    Why this exists: the /streams tab is dumped with `--flat-playlist`,
    which is one cheap request for the whole channel but frequently omits
    the timestamp. A broadcast with no date cannot be placed on the
    official calendar, cannot be ordered against anything, and cannot be
    told apart from last season's — so most of the archive was unusable to
    the site even though it had been found.

    The budget is the point. Each fill is one extra metadata request, so
    the scan spends `limit` of them per run, oldest-known-first, and the
    archive completes itself over a handful of scheduled runs instead of
    hammering the source in one burst. `limit=0` disables it entirely,
    which is what every offline test and dry run uses.

    The budget counts REQUESTS, not successes. It used to count only the
    rows it filled, so a failing lookup cost a request and bought nothing
    against the limit — and when the source refused every lookup (YouTube
    now bot-checks the runner and asks for cookies), the loop walked the
    entire dateless archive. A budget of 40 spent 245 requests and wrote
    245 near-identical errors into the public snapshot, hammering a source
    hardest at the exact moment it was already refusing us."""
    if limit <= 0:
        return ledger, [], 0
    meta = fetch_meta or (lambda vid: fetch_video_metadata(vid, runner=runner))
    errors: list[str] = []
    filled = 0
    attempts = 0
    for row in ledger.get("candidates") or []:
        if attempts >= limit:
            break
        if not isinstance(row, dict) or row.get("publishedAt"):
            continue
        # Nothing to place on a calendar: a refused Short is not worth a
        # request, and never will be.
        if (row.get("likeness") or {}).get("refused"):
            continue
        attempts += 1
        fields, err = meta(row["videoId"])
        if err:
            errors.append(err)
            continue
        if not fields or not fields.get("publishedAt"):
            continue
        row["publishedAt"] = fields["publishedAt"]
        for key in ("durationSeconds", "liveBroadcastStatus"):
            if row.get(key) is None and fields.get(key) is not None:
                row[key] = fields[key]
        if "video-metadata" not in (row.get("sources") or []):
            row["sources"] = sorted((row.get("sources") or [])
                                    + ["video-metadata"])
        filled += 1
    if filled:
        # Dates just changed, so the archive's newest-first ordering has to
        # be recomputed or the new dates would sit in the undated tail.
        rows = sorted(ledger["candidates"],
                      key=lambda c: c.get("publishedAt") or "", reverse=True)
        rows.sort(key=lambda c: c.get("publishedAt") is None)
        ledger["candidates"] = rows
    return ledger, errors, filled


# ---------------------------------------------------------------- merge
def merge_channel_entries(channel: dict, rss: list[dict],
                          streams: list[dict]) -> list[dict]:
    """Merge both sources for one channel into scored candidates. The
    streams tab wins for duration/live status (RSS has neither); RSS wins
    for published time when both exist. The full description is used for
    likeness scoring and then DROPPED — the ledger stores the verdict and
    its reasons, not kilobytes of marketing copy."""
    by_id: dict[str, dict] = {}
    for src_name, entries in (("rss", rss), ("streams", streams)):
        for e in entries:
            vid = e["videoId"]
            cur = by_id.setdefault(vid, {
                "videoId": vid, "title": None, "publishedAt": None,
                "description": None, "durationSeconds": None,
                "liveBroadcastStatus": None, "channelTitle": None,
                "sources": []})
            if src_name not in cur["sources"]:
                cur["sources"].append(src_name)
            for k in ("title", "publishedAt", "description",
                      "durationSeconds", "liveBroadcastStatus",
                      "channelTitle"):
                if cur.get(k) is None and e.get(k) is not None:
                    cur[k] = e[k]
            # streams tab carries the authoritative duration/live status
            if src_name == "streams":
                if e.get("durationSeconds") is not None:
                    cur["durationSeconds"] = e["durationSeconds"]
                if e.get("liveBroadcastStatus") is not None:
                    cur["liveBroadcastStatus"] = e["liveBroadcastStatus"]
    out = []
    for vid, c in by_id.items():
        likeness = bmatch.broadcast_likeness({
            "title": c.get("title"),
            "description": c.get("description"),
            "durationSeconds": c.get("durationSeconds"),
            "liveBroadcastStatus": c.get("liveBroadcastStatus"),
        })
        out.append({
            "videoId": vid,
            "url": li.canonical_url(vid),
            "title": c.get("title"),
            "publishedAt": c.get("publishedAt"),
            "durationSeconds": c.get("durationSeconds"),
            "liveBroadcastStatus": c.get("liveBroadcastStatus"),
            "channelRegistryId": channel.get("id"),
            "channelId": channel.get("channelId"),
            "channelTitle": c.get("channelTitle") or channel.get("title"),
            "sources": sorted(c["sources"]),
            "likeness": likeness,
        })
    return out


# --------------------------------------------------------------- ledger
def _read_candidates_file(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and isinstance(data.get("candidates"), list):
        return data
    return None


def load_ledger(path: str | None = None,
                snapshot: str | None = None) -> dict:
    """The accumulated candidate archive.

    Falls back to the COMMITTED snapshot when the local ledger file is
    absent, because the ledger is gitignored operator state: a CI runner
    starts every scan with no ledger at all, and without this fallback each
    scheduled run would reset `firstSeenAt` and forget every broadcast that
    has scrolled out of the feed window. The snapshot's per-candidate `job`
    field is dropped — that is live intake state joined at report time, not
    ledger data, and a stale copy must never be re-published."""
    data = _read_candidates_file(path or ledger_path())
    if data is None:
        data = _read_candidates_file(snapshot or snapshot_path())
        if data is not None:
            data = dict(data)
            data["candidates"] = [
                {k: v for k, v in c.items() if k != "job"}
                for c in data["candidates"] if isinstance(c, dict)]
    if data is not None:
        return data
    return {"schema": SCHEMA, "generatedAt": None, "channels": [],
            "candidates": [], "sourceErrors": []}


def merge_ledger(ledger: dict, scanned: list[dict], *,
                 channels: list[dict], source_errors: list[str],
                 now: str | None = None) -> dict:
    """Idempotent merge: a re-scan updates fields and lastSeenAt but never
    duplicates a video and never loses firstSeenAt. Candidates that fell
    out of the feed window are kept — the ledger is the accumulating
    archive of every broadcast the finder has ever seen."""
    now = now or _utcnow_iso()
    by_id = {c["videoId"]: dict(c) for c in ledger.get("candidates") or []}
    for cand in scanned:
        prev = by_id.get(cand["videoId"])
        row = dict(cand)
        row["firstSeenAt"] = prev.get("firstSeenAt", now) if prev else now
        row["lastSeenAt"] = now
        by_id[cand["videoId"]] = row
    # newest first; unknown published dates sink to the end
    rows = sorted(by_id.values(),
                  key=lambda c: c.get("publishedAt") or "", reverse=True)
    rows.sort(key=lambda c: c.get("publishedAt") is None)
    return {"schema": SCHEMA, "generatedAt": now,
            "channels": channels, "candidates": rows,
            "sourceErrors": source_errors}


def save_ledger(ledger: dict, path: str | None = None) -> str:
    path = path or ledger_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2, sort_keys=True)
        f.write("\n")
    return path


# ----------------------------------------------------------------- scan
def scan(channels: list[dict] | None = None, *, limit: int = DEFAULT_LIMIT,
         fetch: Callable[[str], bytes] = _http_fetch, runner=subprocess,
         extra_channel_urls: list[str] | None = None,
         now: str | None = None) -> dict:
    """Scan every verified channel on both free sources and return the
    merged, scored ledger (not yet saved — the caller decides)."""
    chans = scan_channels() if channels is None else channels
    errors: list[str] = []
    candidates: list[dict] = []
    for ch in chans:
        rss_entries: list[dict] = []
        if ch.get("channelId"):
            rss_entries, err = fetch_rss_channel(ch["channelId"], fetch=fetch)
            if err:
                errors.append(err)
        streams_entries: list[dict] = []
        streams_url = channel_streams_url(ch)
        if streams_url:
            streams_entries, err2 = fetch_streams_tab(
                streams_url, limit=limit, runner=runner)
            if err2:
                errors.append(err2)
        candidates.extend(merge_channel_entries(ch, rss_entries,
                                                streams_entries))
    for url in extra_channel_urls or []:
        pseudo = {"id": url, "channelId": None, "title": url,
                  "sourceUrl": url}
        entries, err3 = fetch_streams_tab(url, limit=limit, runner=runner)
        if err3:
            errors.append(err3)
        candidates.extend(merge_channel_entries(pseudo, [], entries))
    return merge_ledger(load_ledger(), candidates, channels=chans,
                        source_errors=errors, now=now)


# --------------------------------------------------------------- report
def _current_verdict(row: dict) -> dict:
    """Re-judge one archived candidate against TODAY's rules.

    The ledger keeps every broadcast the finder has ever seen, including
    ones that scrolled out of the feed window years of scans ago. Those rows
    are never re-scanned, so their likeness verdict is frozen at whatever
    the rules said on the day they were found — which meant tightening the
    gate only ever affected videos discovered afterwards, and the ninety
    already in the list kept their old verdicts forever.

    The verdict is therefore recomputed here, from fields the row already
    carries. Two rules keep this honest:

      * it can only ever TIGHTEN. The stored verdict was scored with the
        full description, which the ledger deliberately does not keep, so a
        re-score sees strictly less evidence; taking the worse of the two
        means missing evidence can never promote something.
      * the ledger file is not rewritten. This is the report's judgement,
        and a row whose verdict moved says so.
    """
    stored = row.get("likeness") or {}
    fresh = bmatch.broadcast_likeness({
        "title": row.get("title"),
        "description": None,                 # dropped from the ledger by design
        "durationSeconds": row.get("durationSeconds"),
        "liveBroadcastStatus": row.get("liveBroadcastStatus"),
    })
    verdict = dict(fresh)
    if stored.get("confidence") == "unlikely":
        verdict["confidence"] = "unlikely"
    if stored.get("refused"):
        verdict["refused"] = True
        verdict["refusalReason"] = stored.get("refusalReason") or verdict.get("refusalReason")
    if stored.get("confidence") and stored["confidence"] != verdict["confidence"]:
        verdict["rescoredFrom"] = stored["confidence"]
    out = dict(row)
    out["likeness"] = verdict
    return out


def build_report(db_path: str | None = None, *,
                 ledger: dict | None = None,
                 store: "js.JobStore | None" = None) -> dict:
    """The ledger joined with the live intake job for each candidate, so
    one glance answers "which broadcasts exist, and where is each one in
    the pipeline?". Must never raise — the portal renders whatever this
    returns."""
    try:
        led = ledger if ledger is not None else load_ledger()
        # The job join is OPTIONAL enrichment; the candidate list is the
        # payload. Opening the automation DB must therefore never be able to
        # fail the whole report — on a CI runner the DB is gitignored and
        # absent, and if a store failure emptied the report the caller would
        # commit that empty snapshot over every broadcast ever found.
        own_store = False
        join_error: str | None = None
        if store is None:
            try:
                store = js.JobStore(db_path or js.DEFAULT_DB)
                own_store = True
            except Exception as exc:  # noqa: BLE001
                store = None
                join_error = (f"intake-state join unavailable "
                              f"({type(exc).__name__}: {exc}) — candidates "
                              f"are still complete")
        rows = []
        tracked = 0
        try:
            for cand in led.get("candidates") or []:
                row = dict(cand)
                job = None
                if store is not None:
                    try:
                        j = store.get(li.job_key_for(cand["videoId"]))
                    except Exception:  # noqa: BLE001
                        j = None
                    if j is not None:
                        source = (j.payload.get("source") or {})
                        job = {"jobKey": j.job_key, "state": j.state,
                               "sourceState": source.get("state"),
                               "nextCommand": li.next_command(j)}
                        tracked += 1
                row["job"] = job
                rows.append(row)
        finally:
            if own_store and store is not None:
                store.close()
        rows = [_current_verdict(r) for r in rows]
        # Shorts, clips and promo cutdowns are REFUSED by the length gate,
        # not merely ranked low. They stay in the ledger — the verdict and
        # its reason are part of the audit trail, and re-scanning must not
        # rediscover them as new — but they are not offered as something to
        # convert, because there is nothing in them to convert. A job that
        # already exists keeps its row: hiding a broadcast someone is
        # actually working on would be worse than showing a short one.
        def _refused(r: dict) -> bool:
            return bool((r.get("likeness") or {}).get("refused")) and not r.get("job")

        ignored = [r for r in rows if _refused(r)]
        rows = [r for r in rows if not _refused(r)]
        likely = sum(1 for r in rows
                     if (r.get("likeness") or {}).get("confidence") == "likely")
        errors = list(led.get("sourceErrors") or [])
        if join_error:
            errors.append(join_error)
        return {"schema": SCHEMA, "generatedAt": led.get("generatedAt"),
                "channels": led.get("channels") or [],
                "sourceErrors": errors,
                "candidates": rows,
                "summary": {"total": len(rows), "likely": likely,
                            "tracked": tracked, "ignoredTooShort": len(ignored)}}
    except Exception as exc:  # noqa: BLE001 — a status read must never 500
        return {"schema": SCHEMA, "generatedAt": None, "channels": [],
                "sourceErrors": [f"report: {type(exc).__name__}: {exc}"],
                "candidates": [],
                "summary": {"total": 0, "likely": 0, "tracked": 0,
                            "ignoredTooShort": 0}}


def export_snapshot(report: dict, path: str | None = None) -> str:
    """Write the static snapshot GitHub Pages serves (read-only mirror of
    the live /api/matchfinder report)."""
    path = path or snapshot_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")
    return path


def format_report(report: dict) -> str:
    """Terminal rendering of the report (the CLI's output)."""
    lines = []
    s = report.get("summary") or {}
    ignored = s.get("ignoredTooShort") or 0
    lines.append(f"[match-finder] {s.get('total', 0)} candidate(s) — "
                 f"{s.get('likely', 0)} likely broadcast(s), "
                 f"{s.get('tracked', 0)} already in the pipeline"
                 + (f", {ignored} Shorts/short upload(s) ignored" if ignored else ""))
    for err in report.get("sourceErrors") or []:
        lines.append(f"  SOURCE ERROR: {err}")
    for c in report.get("candidates") or []:
        lk = c.get("likeness") or {}
        job = c.get("job")
        state = job["state"] if job else "not queued"
        dur = c.get("durationSeconds")
        dur_s = f"{dur // 3600}h{(dur % 3600) // 60:02d}m" if dur else "?"
        lines.append(f"  [{lk.get('confidence', '?'):8}] {c['videoId']} "
                     f"{(c.get('publishedAt') or '')[:10]:10} {dur_s:>7}  "
                     f"{(c.get('title') or '')[:70]}")
        lines.append(f"             state: {state} · "
                     f"queue: python pipeline/automation/cli.py "
                     f"convert-link --url \"{c['url']}\"")
    return "\n".join(lines)
