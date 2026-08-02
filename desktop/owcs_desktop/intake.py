"""
intake.py — one box, every kind of thing a user might paste.

The pipeline's own `link_intake.parse_link` is deliberately strict: it accepts
exactly one shape, a single official YouTube broadcast, because that is the
only shape it is allowed to auto-authorize. That strictness is a feature and
this module does not weaken it.

What this module adds is the *front door*: it classifies whatever was pasted,
and routes each kind to the right existing machinery.

    youtube video      -> straight to link_intake (unchanged path)
    youtube playlist    -> expanded to its video ids with yt-dlp metadata only
                           (`--flat-playlist -J`, no media), each then routed
                           through the same single-video path
    faceit room/match   -> the FACEIT ingest, which produces match FACTS only
                           (teams, maps, score, bans) and never a composition
    faceit championship -> the championship's match list, each of which can
                           then be ingested as facts
    local video file    -> a local-media job, so a file already on disk skips
                           download entirely

`classify()` is pure and offline — no network, no subprocess — so the UI can
tell the user what it thinks they pasted the moment they paste it, and so the
whole routing table is testable without touching YouTube or FACEIT.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.parse
from typing import Any

from . import paths

KIND_YOUTUBE_VIDEO = "youtube-video"
KIND_YOUTUBE_PLAYLIST = "youtube-playlist"
KIND_FACEIT_MATCH = "faceit-match"
KIND_FACEIT_CHAMPIONSHIP = "faceit-championship"
KIND_LOCAL_FILE = "local-file"
KIND_UNKNOWN = "unknown"

YOUTUBE_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
    "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
})
FACEIT_HOSTS = frozenset({"faceit.com", "www.faceit.com"})

VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".ts", ".m4v", ".flv",
})

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{12,42}$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def classify(text: str) -> dict[str, Any]:
    """What is this? Offline, no side effects.

    Always returns a dict with `kind`, a human `label`, and `accepted`.
    A rejected paste carries `reason` explaining what would be accepted —
    the UI shows that instead of a generic failure.
    """
    raw = (text or "").strip().strip('"').strip("'")
    if not raw:
        return {"kind": KIND_UNKNOWN, "accepted": False, "input": raw,
                "label": "nothing pasted",
                "reason": "Paste a YouTube link, a FACEIT link, or the path to "
                          "a video file on this PC."}

    # A local file first: a Windows path (C:\…) is not a URL, and a file:// URL
    # is one that we can turn straight back into a path.
    local = _as_local_path(raw)
    if local is not None:
        exists = os.path.isfile(local)
        ext = os.path.splitext(local)[1].lower()
        if not exists:
            return {"kind": KIND_LOCAL_FILE, "accepted": False, "input": raw,
                    "path": local, "label": "local video file",
                    "reason": f"There is no file at {local}."}
        if ext not in VIDEO_EXTENSIONS:
            return {"kind": KIND_LOCAL_FILE, "accepted": False, "input": raw,
                    "path": local, "label": "local file",
                    "reason": f"{ext or 'that file'} is not a video format this "
                              f"app reads ({', '.join(sorted(VIDEO_EXTENSIONS))})."}
        return {"kind": KIND_LOCAL_FILE, "accepted": True, "input": raw,
                "path": local, "label": "local video file",
                "detail": f"{os.path.basename(local)} "
                          f"({os.path.getsize(local) / (1024 ** 3):.2f} GB)"}

    if "//" not in raw.split("?", 1)[0] and re.match(
            r"^(?:www\.|m\.)?(?:youtube\.com|youtu\.be|faceit\.com)/", raw, re.I):
        raw = "https://" + raw

    parsed = urllib.parse.urlsplit(raw)
    host = (parsed.hostname or "").lower()
    query = urllib.parse.parse_qs(parsed.query)
    parts = [p for p in parsed.path.split("/") if p]

    if host in YOUTUBE_HOSTS:
        list_id = (query.get("list") or [None])[0]
        has_video = bool((query.get("v") or [None])[0]) or host.endswith("youtu.be") \
            or (len(parts) >= 2 and parts[0].lower() in
                ("live", "embed", "shorts", "v"))
        if list_id and not has_video:
            if not _PLAYLIST_ID_RE.match(list_id):
                return {"kind": KIND_YOUTUBE_PLAYLIST, "accepted": False,
                        "input": raw, "label": "YouTube playlist",
                        "reason": f"{list_id!r} is not a valid playlist id."}
            return {"kind": KIND_YOUTUBE_PLAYLIST, "accepted": True,
                    "input": raw, "playlistId": list_id,
                    "label": "YouTube playlist",
                    "detail": "Every broadcast in the playlist will be queued "
                              "through the same intake gate as a single link."}
        # A watch URL that also carries ?list= is a video, not a playlist.
        return _classify_youtube_video(raw, parsed, query, parts, host, list_id)

    if host in FACEIT_HOSTS:
        return _classify_faceit(raw, parts)

    if parsed.scheme in ("http", "https"):
        return {"kind": KIND_UNKNOWN, "accepted": False, "input": raw,
                "label": "unsupported site", "host": host,
                "reason": f"{host or 'that site'} is not a source this app "
                          "reads. Paste a YouTube or FACEIT link, or a video "
                          "file on this PC."}

    return {"kind": KIND_UNKNOWN, "accepted": False, "input": raw,
            "label": "unrecognised",
            "reason": "That does not look like a YouTube link, a FACEIT link, "
                      "or a path to a video file."}


def _classify_youtube_video(raw, parsed, query, parts, host, list_id):
    video_id = None
    if host.endswith("youtu.be"):
        video_id = parts[0] if parts else None
    else:
        video_id = (query.get("v") or [None])[0]
        if not video_id and len(parts) >= 2 and parts[0].lower() in (
                "live", "embed", "shorts", "v"):
            video_id = parts[1]
    if not video_id:
        return {"kind": KIND_YOUTUBE_VIDEO, "accepted": False, "input": raw,
                "label": "YouTube link",
                "reason": "No video id in that link. Paste a watch?v=, "
                          "youtu.be/ or /live/ broadcast link."}
    if not _VIDEO_ID_RE.match(video_id):
        return {"kind": KIND_YOUTUBE_VIDEO, "accepted": False, "input": raw,
                "label": "YouTube link",
                "reason": f"{video_id!r} is not a valid 11-character video id."}
    out = {"kind": KIND_YOUTUBE_VIDEO, "accepted": True, "input": raw,
           "videoId": video_id, "label": "YouTube broadcast",
           "canonicalUrl": f"https://www.youtube.com/watch?v={video_id}"}
    if list_id:
        out["detail"] = ("This link is inside a playlist; only this one video "
                         "will be queued. Paste the playlist URL on its own to "
                         "queue all of them.")
    return out


def _classify_faceit(raw: str, parts: list[str]) -> dict[str, Any]:
    lowered = [p.lower() for p in parts]
    for marker, kind, label in (("room", KIND_FACEIT_MATCH, "FACEIT matchroom"),
                                ("championship", KIND_FACEIT_CHAMPIONSHIP,
                                 "FACEIT tournament")):
        if marker in lowered:
            ident = parts[lowered.index(marker) + 1] if \
                lowered.index(marker) + 1 < len(parts) else ""
            # Room ids are commonly pasted with FACEIT's "1-" prefix.
            bare = ident[2:] if ident.lower().startswith("1-") else ident
            if not bare:
                return {"kind": kind, "accepted": False, "input": raw,
                        "label": label,
                        "reason": f"That {label.lower()} link has no id in it."}
            if not _UUID_RE.match(bare):
                return {"kind": kind, "accepted": False, "input": raw,
                        "label": label, "id": bare,
                        "reason": f"{bare!r} is not a FACEIT id."}
            key = "matchId" if kind == KIND_FACEIT_MATCH else "championshipId"
            return {"kind": kind, "accepted": True, "input": raw, key: bare,
                    "label": label,
                    "detail": "FACEIT supplies match facts only — teams, maps, "
                              "score and bans. Hero compositions never come "
                              "from FACEIT."}
    return {"kind": KIND_UNKNOWN, "accepted": False, "input": raw,
            "label": "FACEIT link",
            "reason": "Paste a FACEIT matchroom (/room/…) or tournament "
                      "(/championship/…) link."}


def _as_local_path(raw: str) -> str | None:
    """A filesystem path, or None. Handles file:// and Windows drive paths."""
    if raw.lower().startswith("file://"):
        # Normalise separators BEFORE parsing. `file://C:\videos\x.mp4` has no
        # forward slash after the authority, so urlsplit takes the ENTIRE
        # remainder as the host and leaves the path empty — the drive-letter
        # test below would never fire and the file would be reported missing.
        parsed = urllib.parse.urlsplit(raw.replace("\\", "/"))
        path = urllib.parse.unquote(parsed.path)
        netloc = urllib.parse.unquote(parsed.netloc or "")
        # `file://C:\videos\x.mp4` — what Windows itself hands you when you
        # drag a file into an address bar, and what a `file://` + path
        # concatenation produces. urlsplit reads `C:` as the HOST, leaving
        # path as `\videos\x.mp4`, which resolves nowhere. Put the drive
        # back. (A UNC path `file://server/share` keeps its host, so only a
        # drive-letter netloc is treated this way.)
        if re.fullmatch(r"[A-Za-z]:", netloc):
            return netloc + path
        if netloc:                              # UNC: file://server/share/x
            return "\\\\" + netloc + path.replace("/", "\\")
        if re.match(r"^/[A-Za-z]:", path):      # file:///C:/x -> C:/x
            return path[1:]
        return os.path.abspath(path)
    if re.match(r"^[A-Za-z]:[\\/]", raw) or raw.startswith("\\\\"):
        # A drive-letter or UNC path is already absolute. Do NOT run it
        # through abspath(): off Windows that would glue the current working
        # directory in front of it and report a nonsense location back to the
        # user. Return it exactly as pasted.
        return raw
    if raw.startswith(("/", "./", "../", "~")) and "://" not in raw:
        return os.path.abspath(os.path.expanduser(raw))
    return None


# ------------------------------------------------------------- expansion
def expand_playlist(playlist_id: str, *, limit: int = 100,
                    runner=subprocess) -> dict[str, Any]:
    """List a playlist's videos with yt-dlp, metadata only.

    `--flat-playlist -J` dumps the index without touching a single media
    stream — the same call the existing match finder uses for a channel's
    streams tab.
    """
    from . import health
    ytdlp = health.resolve_binary("yt-dlp")
    if not ytdlp:
        return {"ok": False, "error": "yt-dlp is not available; run the "
                                      "dependency repair from the Health page"}
    url = f"https://www.youtube.com/playlist?list={playlist_id}"
    try:
        proc = runner.run(
            [ytdlp, "--flat-playlist", "-J", "--no-warnings",
             "--playlist-end", str(int(limit)), url],
            capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"could not read the playlist: {exc}"}
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        return {"ok": False, "error": "\n".join(tail) or "yt-dlp failed"}
    try:
        doc = json.loads(proc.stdout or "{}")
    except ValueError:
        return {"ok": False, "error": "the playlist listing was not readable"}

    videos = []
    for entry in doc.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        vid = entry.get("id")
        if not vid or not _VIDEO_ID_RE.match(str(vid)):
            continue
        videos.append({
            "videoId": vid,
            "title": entry.get("title"),
            "durationSeconds": entry.get("duration"),
            "url": f"https://www.youtube.com/watch?v={vid}",
        })
    return {"ok": True, "playlistId": playlist_id,
            "title": doc.get("title"), "count": len(videos), "videos": videos}


# --------------------------------------------------------------- routing
def submit(text: str, *, requested_by: str, auto_accept: bool = False,
           limit: int = 25, runner=subprocess) -> dict[str, Any]:
    """Classify and queue whatever was pasted.

    Returns {"ok", "kind", "queued": [...], "skipped": [...], "detail"}.
    Every queued item goes through the pipeline's existing gates — this
    function starts work, it never approves anything.
    """
    verdict = classify(text)
    if not verdict.get("accepted"):
        return {"ok": False, "kind": verdict["kind"],
                "error": verdict.get("reason", "that link was not understood"),
                "classification": verdict}

    kind = verdict["kind"]
    if kind == KIND_YOUTUBE_VIDEO:
        return _submit_video(verdict["canonicalUrl"], requested_by=requested_by,
                             auto_accept=auto_accept, classification=verdict)

    if kind == KIND_YOUTUBE_PLAYLIST:
        listing = expand_playlist(verdict["playlistId"], limit=limit,
                                  runner=runner)
        if not listing.get("ok"):
            return {"ok": False, "kind": kind, "error": listing["error"],
                    "classification": verdict}
        queued, skipped = [], []
        for video in listing["videos"]:
            result = _submit_video(video["url"], requested_by=requested_by,
                                   auto_accept=auto_accept,
                                   classification=verdict)
            (queued if result.get("ok") else skipped).append(
                {**video, "detail": result.get("detail") or result.get("error")})
        return {"ok": bool(queued), "kind": kind, "queued": queued,
                "skipped": skipped, "playlist": listing.get("title"),
                "detail": f"{len(queued)} broadcast(s) queued, "
                          f"{len(skipped)} skipped, from "
                          f"{listing['count']} in the playlist",
                "classification": verdict}

    if kind == KIND_FACEIT_MATCH:
        return _submit_faceit_match(verdict["matchId"], verdict,
                                    requested_by=requested_by, runner=runner)

    if kind == KIND_FACEIT_CHAMPIONSHIP:
        return _submit_faceit_championship(verdict["championshipId"], verdict,
                                           limit=limit)

    if kind == KIND_LOCAL_FILE:
        return _submit_local(verdict["path"], verdict, requested_by=requested_by)

    return {"ok": False, "kind": kind, "error": "nothing to do with that",
            "classification": verdict}


def _store():
    sys.path.insert(0, paths.app_root())
    from pipeline.automation import job_store as js
    store = js.JobStore(paths.automation_db())
    store.init_db()
    return store


def _submit_video(url: str, *, requested_by: str, auto_accept: bool,
                  classification: dict) -> dict[str, Any]:
    from pipeline.automation import link_intake as li
    store = _store()
    try:
        result = li.ingest_link(store, url, requested_by=requested_by)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "classification": classification}
    finally:
        store.close()
    job_key = (result or {}).get("jobKey") or (result or {}).get("job_key")
    return {"ok": True, "kind": KIND_YOUTUBE_VIDEO, "jobKey": job_key,
            "queued": [{"jobKey": job_key, "url": url}],
            "detail": (result or {}).get("detail")
                      or f"queued as {job_key}",
            "result": result, "classification": classification}


def _submit_faceit_match(match_id: str, classification: dict, *,
                         requested_by: str, runner=subprocess) -> dict[str, Any]:
    """Ingest one FACEIT matchroom's FACTS through the existing ingester."""
    app = paths.app_root()
    env = dict(os.environ)
    paths.apply_environment(env=env)
    script = os.path.join(app, "pipeline", "ingest_faceit.py")
    if not os.path.exists(script):
        return {"ok": False, "error": "the FACEIT ingester is not installed",
                "classification": classification}
    url = f"https://www.faceit.com/en/ow2/room/1-{match_id}"
    try:
        proc = runner.run(
            paths.python_command() + [script, "--room-url", url],
            cwd=app, env=env, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc), "classification": classification}
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        return {"ok": False, "error": "\n".join(output.splitlines()[-6:]),
                "classification": classification}
    return {"ok": True, "kind": KIND_FACEIT_MATCH, "matchId": match_id,
            "queued": [{"matchId": match_id, "url": url}],
            "detail": "\n".join(output.splitlines()[-6:])
                      or "FACEIT match facts ingested",
            "classification": classification}


def _submit_faceit_championship(championship_id: str, classification: dict, *,
                                limit: int = 25) -> dict[str, Any]:
    """List a tournament's matches so each can be ingested as facts.

    Deliberately a listing, not a bulk ingest: a championship can carry
    hundreds of matches, and queueing them all on one paste is not a decision
    this should make for the user.
    """
    sys.path.insert(0, paths.app_root())
    from . import credentials as cred
    from pipeline.automation import faceit_api as fa

    key = None
    try:
        key = cred.CredentialVault().get("FACEIT_API_KEY")
    except cred.CredentialError:
        key = None
    key = key or os.environ.get("FACEIT_API_KEY")
    if not key:
        return {"ok": False, "kind": KIND_FACEIT_CHAMPIONSHIP,
                "error": "Reading a FACEIT tournament needs a FACEIT API key. "
                         "Add one on the Credentials page — single matchrooms "
                         "and YouTube links work without it.",
                "classification": classification}
    try:
        client = fa.FaceitClient(api_key=key)
        championship = client.get_championship(championship_id)
        matches = client.list_championship_matches(championship_id, limit=limit)
    except Exception as exc:
        return {"ok": False, "kind": KIND_FACEIT_CHAMPIONSHIP,
                "error": f"{type(exc).__name__}: {exc}",
                "classification": classification}
    rows = []
    for raw in matches or []:
        try:
            row = fa.normalize_match(raw)
        except Exception:
            continue
        rows.append({
            "matchId": row.get("faceitMatchId") or raw.get("match_id"),
            "teamA": row.get("teamA", {}).get("name"),
            "teamB": row.get("teamB", {}).get("name"),
            "status": row.get("status"),
            "scheduledAt": row.get("scheduledAt"),
        })
    return {"ok": True, "kind": KIND_FACEIT_CHAMPIONSHIP,
            "championshipId": championship_id,
            "championship": (championship or {}).get("name"),
            "matches": rows, "queued": [],
            "detail": f"{len(rows)} match(es) found. Choose the ones to "
                      f"ingest — a tournament is not queued wholesale.",
            "classification": classification}


def _submit_local(path: str, classification: dict, *,
                  requested_by: str) -> dict[str, Any]:
    """Register a video already on this PC as a job with the download done.

    The job enters at DOWNLOADED with its media path recorded, so every stage
    after the download — layout resolution, segmentation, detection, review,
    publication — is the identical code path a YouTube broadcast takes.
    """
    import hashlib
    sys.path.insert(0, paths.app_root())
    from pipeline.automation import models, state_machine as sm

    digest = hashlib.sha256(os.path.abspath(path).encode("utf-8")).hexdigest()[:16]
    job_key = f"local-{digest}"
    store = _store()
    try:
        existing = store.get(job_key)
        if existing is not None:
            return {"ok": True, "kind": KIND_LOCAL_FILE, "jobKey": job_key,
                    "queued": [{"jobKey": job_key, "path": path}],
                    "detail": f"already queued as {job_key} "
                              f"(state {existing.state})",
                    "classification": classification}
        size = os.path.getsize(path)
        store.enqueue(
            job_key=job_key, kind=models.KIND_RECORD, state=sm.DOWNLOADED,
            payload={
                "source": "local-file",
                "localPath": os.path.abspath(path),
                "mediaPath": os.path.abspath(path),
                "title": os.path.basename(path),
                "bytes": size,
                "requestedBy": requested_by,
                # A local file has no official-channel provenance, so it is
                # marked as operator-supplied. Nothing downstream may treat it
                # as an authorized official broadcast.
                "sourceAuthorized": False,
                "sourceKind": "operator-supplied local file",
            })
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "classification": classification}
    finally:
        store.close()
    return {"ok": True, "kind": KIND_LOCAL_FILE, "jobKey": job_key,
            "queued": [{"jobKey": job_key, "path": path}],
            "detail": f"queued {os.path.basename(path)} as {job_key}; "
                      "processing starts from the layout stage since the file "
                      "is already on disk",
            "classification": classification}
