#!/usr/bin/env python3
"""
discord_notify.py — post to a Discord channel through a webhook ($0, stdlib).

Two messages, both sent from the data workflows:

  results   after a spreadsheet import is published: the series that are new
            since the previous import, and the meta since the newest balance
            patch (most-picked heroes per role, biggest risers and fallers
            against the patch before, most-banned heroes).

              python pipeline/discord_notify.py results --base <git rev>

            `--base` is the commit the import started from; a series is new
            when its id is not in that commit's external_series.v1.js.
            Nothing new -> nothing is posted.

  alert     a plain operator message (a failed sync, a test that no longer
            holds on the new data).

              python pipeline/discord_notify.py alert --title "..." --body "..."

The webhook URL is read from the environment, never from a file or an
argument, so it cannot end up in the repository or a log:

  DISCORD_WEBHOOK_URL         results (and alerts, when the next is unset)
  DISCORD_ALERT_WEBHOOK_URL   alerts, when they belong in a different channel

With no URL set — a fork, a local run — or with --dry-run, the message is
printed and nothing is sent; the exit code is 0 either way, and a Discord
outage never fails the workflow that called this.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SERIES_JS = "assets/data/external_series.v1.js"
TIERS_JS = "assets/data/sheet_tiers.v1.js"
DATA_JS = "assets/js/data.js"
PATCHES_JS = "assets/data/patches.v1.js"
SITE = "https://cvree.github.io/OWCSComp.Tracker/"

ROLES = ("Tank", "Damage", "Support")
MAX_RESULTS = 12          # series listed in one message
MIN_MAPS = 8              # fewer maps than this since the patch: no meta block
MIN_PREV_MAPS = 8         # …and no risers/fallers without a baseline
LIME, RED = 0xB6F23C, 0xE5484D


# ------------------------------------------------------------------ reading
def js_object(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        return json.loads(text[text.index("{"):text.rindex("}") + 1])
    except ValueError:
        return None


def read(path: str) -> str | None:
    try:
        with open(os.path.join(ROOT, path), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def read_at(rev: str, path: str) -> str | None:
    try:
        return subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT, check=True,
                              capture_output=True, text=True).stdout
    except (subprocess.CalledProcessError, OSError):
        return None


def patches() -> list[dict]:
    """Newest first. patches.v1.js is hand-written JS, not JSON, so the
    fields are picked out one entry at a time."""
    out = []
    for body in re.findall(r"\{([^{}]*\bdate\s*:[^{}]*)\}", read(PATCHES_JS) or ""):
        f = dict(re.findall(r"(\w+)\s*:\s*\"([^\"]*)\"", body))
        if f.get("date"):
            out.append({"date": f["date"], "from": f.get("owcsFrom") or f["date"], "name": f.get("name") or f["date"]})
    return sorted(out, key=lambda p: p["date"], reverse=True)


# ------------------------------------------------------------------ results
def new_series(now: dict, before: dict | None) -> list[dict]:
    old = {s["id"] for s in (before or {}).get("series") or []}
    return [s for s in now.get("series") or [] if s["id"] not in old and s.get("winner")]


def stage_name(s: dict) -> str:
    return s.get("stageRaw") or s.get("event") or ""


def result_lines(series: list[dict], names: dict) -> list[str]:
    def team(t):
        return names.get(t) or t or "TBD"
    lines = []
    for s in series:
        a, b = team(s.get("teamA")), team(s.get("teamB"))
        a, b = (f"**{a}**", b) if s.get("winner") == s.get("teamA") else (a, f"**{b}**")
        lines.append(f"{a} {s.get('scoreA')}–{s.get('scoreB')} {b}")
    return lines


# --------------------------------------------------------------------- meta
def usage(tiers: dict, start: str, end: str | None = None) -> dict:
    """{maps, picks: {hero: share of team sides}, bans: {hero: share of maps}}
    over maps whose series was played in [start, end)."""
    matches = tiers.get("matches") or []
    maps, picks, bans = 0, {}, {}
    for mp in tiers.get("maps") or []:
        d = (matches[mp["m"]] if 0 <= mp.get("m", -1) < len(matches) else {}).get("d")
        if not d or d < start or (end and d >= end):
            continue
        maps += 1
        for side in ("a", "b"):
            for hero, w in (mp.get(side) or {}).get("h") or []:
                picks[hero] = picks.get(hero, 0.0) + w
            for hero in (mp.get(side) or {}).get("bn") or []:
                bans[hero] = bans.get(hero, 0) + 1
    return {"maps": maps,
            "picks": {h: v / (2 * maps) for h, v in picks.items()} if maps else {},
            "bans": {h: v / maps for h, v in bans.items()} if maps else {}}


def pct(x: float) -> str:
    return f"{round(x * 100)}%"


def meta_fields(tiers: dict, heroes: dict) -> tuple[str, list[dict]]:
    """(one-line description, embed fields) for the meta since the newest
    patch, or ("", []) when there is not enough to say."""
    ps = patches()
    if not ps:
        return "", []
    cur = usage(tiers, ps[0]["from"])
    if cur["maps"] < MIN_MAPS:
        return "", []

    def name(h):
        return (heroes.get(h) or {}).get("name") or h

    fields = []
    for role in ROLES:
        top = sorted((h for h in cur["picks"] if (heroes.get(h) or {}).get("role") == role),
                     key=lambda h: -cur["picks"][h])[:3]
        if top:
            fields.append({"name": role, "inline": True,
                           "value": "\n".join(f"{name(h)} · {pct(cur['picks'][h])}" for h in top)})
    if len(ps) > 1:
        prev = usage(tiers, ps[1]["from"], ps[0]["from"])
        if prev["maps"] >= MIN_PREV_MAPS:
            delta = {h: cur["picks"].get(h, 0.0) - prev["picks"].get(h, 0.0)
                     for h in set(cur["picks"]) | set(prev["picks"])}
            up = [h for h in sorted(delta, key=lambda h: -delta[h]) if delta[h] >= 0.03][:3]
            down = [h for h in sorted(delta, key=lambda h: delta[h]) if delta[h] <= -0.03][:3]
            if up:
                fields.append({"name": "Rising", "inline": True, "value": "\n".join(
                    f"▲ {name(h)} · +{round(delta[h] * 100)} pts" for h in up)})
            if down:
                fields.append({"name": "Falling", "inline": True, "value": "\n".join(
                    f"▼ {name(h)} · {round(delta[h] * 100)} pts" for h in down)})
    banned = sorted(cur["bans"], key=lambda h: -cur["bans"][h])[:3]
    if banned:
        fields.append({"name": "Most banned", "inline": True,
                       "value": "\n".join(f"{name(h)} · {pct(cur['bans'][h])}" for h in banned)})
    line = (f"**Meta since the {ps[0]['name']} patch** ({ps[0]['date']}) · {cur['maps']} maps · "
            f"pick rate is the share of team comps a hero was in")
    return line, fields


def results_message(base: str) -> dict | None:
    now = js_object(read(SERIES_JS))
    if not now:
        return None
    fresh = new_series(now, js_object(read_at(base, SERIES_JS)))
    if not fresh:
        return None
    tiers = js_object(read(TIERS_JS)) or {}
    names = {k: v[0] for k, v in (tiers.get("teams") or {}).items() if v and v[0]}
    heroes = {h["id"]: h for h in (js_object(read(DATA_JS)) or {}).get("heroes") or []}

    groups: dict[str, list[dict]] = {}
    for s in fresh:
        groups.setdefault(stage_name(s), []).append(s)
    parts, shown = [], 0
    for stage, rows in groups.items():
        room = MAX_RESULTS - shown
        if room <= 0:
            break
        parts.append(f"__{stage}__\n" + "\n".join(result_lines(rows[:room], names)))
        shown += min(len(rows), room)
    if len(fresh) > shown:
        parts.append(f"…and {len(fresh) - shown} more on the [Matches page]({SITE}series.html?view=results)")

    line, fields = meta_fields(tiers, heroes)
    desc = "\n\n".join(parts + ([line] if line else []))
    n = len(fresh)
    return {"username": "OWCS Comp Tracker", "allowed_mentions": {"parse": []}, "embeds": [{
        "title": f"{n} new series result{'s' if n != 1 else ''}",
        "url": f"{SITE}series.html?view=results",
        "description": desc[:4000],
        "color": LIME,
        "fields": fields,
        "footer": {"text": "Results and heroes from the community OWCS spreadsheet · full meta: "
                           f"{SITE}meta.html?t=patch"},
    }]}


def alert_message(title: str, body: str, url: str | None) -> dict:
    embed = {"title": title[:250], "description": body[:4000], "color": RED}
    if url:
        embed["url"] = url
    return {"username": "OWCS Comp Tracker", "allowed_mentions": {"parse": []}, "embeds": [embed]}


# ------------------------------------------------------------------ sending
def send(payload: dict | None, hook: str | None, dry_run: bool) -> None:
    if payload is None:
        print("[discord] nothing new to post")
        return
    if dry_run or not hook:
        print("[discord] " + ("dry run" if dry_run else "no webhook configured") + " — not sent:")
        print(json.dumps(payload, indent=1, ensure_ascii=False))
        return
    req = urllib.request.Request(hook, data=json.dumps(payload).encode("utf-8"), method="POST", headers={
        "Content-Type": "application/json",
        # Discord answers the default urllib agent with 403.
        "User-Agent": "owcs-comp-tracker (github-actions, 1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print(f"[discord] posted (HTTP {r.status})")
    except urllib.error.HTTPError as e:
        # The response body can name the channel; the URL is the secret and is never printed.
        print(f"::warning::Discord refused the message (HTTP {e.code}) — check the webhook secret")
    except (urllib.error.URLError, OSError) as e:
        print(f"::warning::Discord could not be reached ({type(e).__name__})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("results")
    r.add_argument("--base", default="HEAD~1", help="commit the import started from")
    r.add_argument("--dry-run", action="store_true")
    a = sub.add_parser("alert")
    a.add_argument("--title", required=True)
    a.add_argument("--body", default="")
    a.add_argument("--url")
    a.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    main_hook = os.environ.get("DISCORD_WEBHOOK_URL") or None
    if args.cmd == "results":
        send(results_message(args.base), main_hook, args.dry_run)
    else:
        send(alert_message(args.title, args.body, args.url),
             os.environ.get("DISCORD_ALERT_WEBHOOK_URL") or main_hook, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
