#!/usr/bin/env python3
"""
liquipedia_schedule.py — start times, teams and streams from Liquipedia.

The community spreadsheet knows every series' round, format and order, and
the result once it is played; it does not know WHEN a series starts, and it
reads TBD until its editors fill a card in. Liquipedia knows both, usually
days earlier. This reads its OWCS pages and writes
data/external/liquipedia/matches.json, which pipeline/schedule_build.py
joins onto the sheet's schedule. It never touches comps, heroes or results
the sheet already has.

How (config/liquipedia.json has every knob and the API terms it keeps):

  1. list every page under the configured prefixes (list=allpages);
  2. map each title to a schedule event key, skipping qualifiers and
     pages that map to nothing (printed, so a rename is visible);
  3. read the pages' wikitext, 50 per request, one request per 2.5 s
     (action=query&prop=revisions — never action=parse);
  4. parse every {{Match ...}} — both opponents, the date and its time
     zone, best-of, score, finished, streams — and the round header the
     enclosing {{Bracket}} or {{Matchlist}} gives it;
  5. write the file only when its content changed.

No network here? `--offline FILE` parses a saved wikitext page instead,
which is also how the tests run.

Usage:
  python3 pipeline/liquipedia_schedule.py                # fetch + write
  python3 pipeline/liquipedia_schedule.py --dry-run      # fetch, print, write nothing
  python3 pipeline/liquipedia_schedule.py --offline page.wiki --title "Overwatch Champions Series/2026/Korea/Stage 3"
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIG = os.path.join(ROOT, "config", "liquipedia.json")
OUT = os.path.join(ROOT, "data", "external", "liquipedia", "matches.json")

#: Time-zone abbreviations Liquipedia writes after a match time
#: ({{Abbr/KST}}), as hours from UTC.
TZ = {"UTC": 0, "GMT": 0, "WET": 0, "BST": 1, "CET": 1, "CEST": 2, "EET": 2, "EEST": 3, "MSK": 3,
      "AST": 3, "GST": 4, "PKT": 5, "IST": 5.5, "ICT": 7, "WIB": 7, "SGT": 8, "PHT": 8, "HKT": 8,
      "AWST": 8, "CST": 8, "KST": 9, "JST": 9, "AEST": 10, "AEDT": 11, "NZST": 12, "NZDT": 13,
      "EST": -5, "EDT": -4, "CDT": -5, "MST": -7, "MDT": -6, "PST": -8, "PDT": -7, "BRT": -3,
      "ART": -3, "CLT": -4, "CLST": -3}
#: Liquipedia's own CST is US Central in NA pages and China Standard in
#: Chinese ones; a CST on a China page is +8, anywhere else -6.
TZ_US_CST = -6

STREAM_URL = {"twitch": "https://www.twitch.tv/{}", "youtube": "https://www.youtube.com/{}",
              "afreeca": "https://play.afreecatv.com/{}", "soop": "https://ch.sooplive.co.kr/{}",
              "chzzk": "https://chzzk.naver.com/live/{}", "huya": "https://www.huya.com/{}",
              "bilibili": "https://live.bilibili.com/{}", "douyu": "https://www.douyu.com/{}",
              "kick": "https://kick.com/{}"}

REGION_WORDS = (("north america", "na"), ("/na/", "na"), ("/na ", "na"), ("emea", "emea"), ("korea", "korea"),
                ("japan", "japan"), ("china", "china"), ("pacific", "pacific"))


# ------------------------------------------------------------- config
def load_config() -> dict:
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def event_for_title(title: str, cfg: dict) -> str | None:
    t = title.replace("_", " ")
    if cfg.get("skip") and re.search(cfg["skip"], t):
        return None
    for rule in cfg.get("events") or []:
        if re.search(rule["match"], t):
            return rule["event"]
    low = "/" + t.lower() + "/"
    st = re.search(r"stage\s*(\d)", low)
    if not st:
        return None
    for word, region in REGION_WORDS:
        if word in low:
            return f"{region}/stage-{st.group(1)}"
    if "asia" in low:
        return f"asia/stage-{st.group(1)}"
    return None


# ----------------------------------------------------- wikitext reading
def templates(text: str, name_re: str) -> list[tuple[int, int, str]]:
    """[(start, end, body)] of every top-level {{name ...}} whose name
    matches `name_re`, brace-balanced. `body` excludes the outer braces."""
    out = []
    pat = re.compile(r"\{\{\s*(" + name_re + r")\s*(?=[|}\n])", re.I)
    pos = 0
    while True:
        m = pat.search(text, pos)
        if not m:
            break
        depth, i = 0, m.start()
        while i < len(text):
            if text.startswith("{{", i):
                depth += 1
                i += 2
                continue
            if text.startswith("}}", i):
                depth -= 1
                i += 2
                if depth == 0:
                    break
                continue
            i += 1
        out.append((m.start(), i, text[m.start() + 2:i - 2]))
        pos = i
    return out


def split_params(body: str) -> tuple[str, list[str], dict[str, str]]:
    """'Name|a|k=v|...' at depth 0 -> (name, positional, named)."""
    parts, depth, cur, i = [], 0, [], 0
    while i < len(body):
        two = body[i:i + 2]
        if two in ("{{", "[["):
            depth += 1
            cur.append(two)
            i += 2
            continue
        if two in ("}}", "]]"):
            depth -= 1
            cur.append(two)
            i += 2
            continue
        ch = body[i]
        if ch == "|" and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    parts.append("".join(cur))
    name = parts[0].strip()
    pos, named = [], {}
    for p in parts[1:]:
        k, eq, v = p.partition("=")
        if eq and re.match(r"^\s*[\w ]+\s*$", k) and "{{" not in k:
            named[k.strip().lower()] = v.strip()
        else:
            pos.append(p.strip())
    return name, pos, named


def strip_markup(v: str | None) -> str:
    v = re.sub(r"<!--.*?-->", "", v or "", flags=re.S)
    v = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", v, flags=re.S)
    # A label wrapped in a template, {{Stage|Regional Playoffs}}: its text.
    v = re.sub(r"\{\{\s*(?:Stage|Abbr|Small|Nowrap|Bold)\s*\|([^{}|]*)\}\}", r"\1", v, flags=re.I)
    return v.strip()


def opponent(v: str | None) -> dict:
    """{{TeamOpponent|crazy raccoon|score=3}} -> {'name': 'crazy raccoon', 'score': 3}."""
    v = strip_markup(v)
    found = templates(v, r"[\w ]*Opponent")
    if not found:
        name = v.strip() or None
        return {"name": None if not name or name.lower() in ("tbd", "tba") else name, "score": None}
    tname, pos, named = split_params(found[0][2])
    name = named.get("template") or named.get("name") or (pos[0] if pos else None)
    if tname.lower().startswith(("tbd", "literal")) and (not name or name.lower() in ("tbd", "tba", "")):
        name = None
    if name and name.strip().lower() in ("tbd", "tba", "bye", ""):
        name = None
    score = named.get("score")
    try:
        score = int(score) if score not in (None, "") else None
    except ValueError:
        score = None
    return {"name": name.strip() if name else None, "score": score}


MONTHS = "january february march april may june july august september october november december".split()


def parse_date(v: str | None, page_event: str | None) -> tuple[str | None, str | None]:
    """'November 6, 2026 - 18:00 {{Abbr/KST}}' -> ('2026-11-06T18:00:00+09:00', '2026-11-06').
    A date with no time gives (None, date). Unparseable gives (None, None)."""
    v = strip_markup(v)
    if not v:
        return None, None
    tz_m = re.search(r"\{\{\s*abbr/([A-Za-z]+)\s*\}\}", v, re.I) or re.search(r"\b([A-Z]{2,5})\s*$", v)
    tz = tz_m.group(1).upper() if tz_m else None
    v = re.sub(r"\{\{.*?\}\}", "", v).replace(",", " ").strip()
    date = None
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", v)
    if m:
        date = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    else:
        m = re.match(r"^([A-Za-z]+)\s+(\d{1,2})\s+(\d{4})", v)
        if m and m.group(1).lower()[:3] in [x[:3] for x in MONTHS]:
            mon = [x[:3] for x in MONTHS].index(m.group(1).lower()[:3]) + 1
            date = dt.date(int(m.group(3)), mon, int(m.group(2)))
    if date is None:
        return None, None
    tm = re.search(r"(?:^|[\s-])(\d{1,2}):(\d{2})\b", v[m.end():])
    if not tm:
        return None, date.isoformat()
    if tz is None:
        tz = "UTC"
    if tz == "CST":
        off = TZ["CST"] if (page_event or "").startswith("china/") else TZ_US_CST
    elif tz in TZ:
        off = TZ[tz]
    else:
        return None, date.isoformat()          # a zone we do not know: keep the day only
    h, mi = int(tm.group(1)), int(tm.group(2))
    sign = "+" if off >= 0 else "-"
    a = abs(off)
    stamp = f"{date.isoformat()}T{h:02d}:{mi:02d}:00{sign}{int(a):02d}:{int(round((a % 1) * 60)):02d}"
    return stamp, date.isoformat()


def section_of(text: str, at: int) -> str | None:
    """The nearest ==Heading== above a position."""
    heads = list(re.finditer(r"^(={2,4})\s*([^=\n]+?)\s*\1\s*$", text[:at], re.M))
    return heads[-1].group(2).strip() if heads else None


def parse_page(text: str, title: str, event: str | None, wiki: str) -> list[dict]:
    """Every {{Match}} on a page, with its round header."""
    out: list[dict] = []
    url = wiki.rstrip("/") + "/" + urllib.parse.quote(title.replace(" ", "_"), safe="/:()")
    containers = templates(text, r"Bracket|Matchlist|SingleMatch")
    spans = []
    for start, end, body in containers:
        cname, _, named = split_params(body)
        spans.append((start, end, cname, named))
    for start, end, body in templates(text, r"Match"):
        # The key this match sits under (R1M2= / M3=) and its container.
        cont = next((s for s in spans if s[0] <= start < s[1]), None)
        before = text[max(0, start - 80):start]
        key_m = re.search(r"\|\s*((?:R\d+)?M\d+|RxMTP|RxMBR)\s*=\s*$", before)
        key = key_m.group(1) if key_m else None
        header = None
        if cont and key:
            named = cont[3]
            header = strip_markup(named.get(key.lower() + "header"))
            if not header:
                # Liquipedia writes the header once, on the round's first match.
                r = re.match(r"R(\d+)M(\d+)", key)
                mm = re.match(r"M(\d+)", key)
                if r:
                    for n in range(int(r.group(2)), 0, -1):
                        h = strip_markup(named.get(f"r{r.group(1)}m{n}header"))
                        if h:
                            header = h
                            break
                elif mm:
                    for n in range(int(mm.group(1)), 0, -1):
                        h = strip_markup(named.get(f"m{n}header"))
                        if h:
                            header = h
                            break
            if not header and key == "RxMTP":
                header = "Third Place Match"
        if not header and cont:
            title_param = strip_markup(cont[3].get("title"))
            header = title_param or None
        _, _, p = split_params(body)
        a, b = opponent(p.get("opponent1")), opponent(p.get("opponent2"))
        starts, day = parse_date(p.get("date"), event)
        # A score missing on the opponent is the count of maps each won.
        if a["score"] is None and b["score"] is None:
            wa = wb = 0
            for k, v in p.items():
                if re.match(r"^map\d+$", k):
                    _, _, mp = split_params((templates(v, r"Map") or [(0, 0, "Map")])[0][2])
                    if mp.get("winner") == "1":
                        wa += 1
                    elif mp.get("winner") == "2":
                        wb += 1
            if wa or wb:
                a["score"], b["score"] = wa, wb
        streams = []
        for k, v in p.items():
            v = strip_markup(v)
            if k in STREAM_URL and v:
                streams.append({"kind": k, "url": v if v.startswith("http") else STREAM_URL[k].format(v)})
        if "stream" in p and strip_markup(p["stream"]).startswith("http"):
            streams.append({"kind": "stream", "url": strip_markup(p["stream"])})
        try:
            bo = int(p.get("bestof")) if p.get("bestof") else None
        except ValueError:
            bo = None
        finished = strip_markup(p.get("finished")).lower() in ("true", "1", "yes", "y")
        out.append({
            "event": event, "page": title, "url": url, "section": section_of(text, start),
            "round": header or None, "key": key, "order": len(out) + 1,
            "teamA": a["name"], "teamB": b["name"], "scoreA": a["score"], "scoreB": b["score"],
            "bestOf": bo, "startsAt": starts, "date": day, "finished": finished,
            "streams": streams or None,
        })
    return out


# --------------------------------------------------------------- fetch
class Api:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.last = 0.0

    def get(self, **params) -> dict:
        wait = float(self.cfg.get("minIntervalSeconds") or 2.5) - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        params.setdefault("format", "json")
        params.setdefault("formatversion", "2")
        url = self.cfg["api"] + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": self.cfg["userAgent"],
                                                   "Accept-Encoding": "gzip"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=40) as r:
                    raw = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                self.last = time.monotonic()
                return json.loads(raw.decode("utf-8"))
            except Exception as exc:  # 429 / transient: back off, then give up loudly
                self.last = time.monotonic()
                if attempt == 3:
                    raise
                print(f"[liquipedia] {type(exc).__name__}: {exc}; retrying", file=sys.stderr)
                time.sleep(10 * (attempt + 1))
        return {}

    def titles(self, prefix: str) -> list[str]:
        out, cont = [], {}
        while True:
            d = self.get(action="query", list="allpages", apprefix=prefix, apnamespace=0,
                         apfilterredir="nonredirects", aplimit=500, **cont)
            out += [p["title"] for p in (d.get("query") or {}).get("allpages") or []]
            if "continue" not in d:
                return out
            cont = {k: v for k, v in d["continue"].items() if k != "continue"}

    def contents(self, titles: list[str]) -> dict[str, tuple[str, int | None]]:
        out = {}
        for i in range(0, len(titles), 50):
            d = self.get(action="query", prop="revisions", rvprop="content|ids", rvslots="main",
                         titles="|".join(titles[i:i + 50]))
            for p in (d.get("query") or {}).get("pages") or []:
                revs = p.get("revisions") or []
                if revs:
                    slot = (revs[0].get("slots") or {}).get("main") or {}
                    out[p["title"]] = (slot.get("content") or revs[0].get("content") or "", revs[0].get("revid"))
        return out


def fetch(cfg: dict) -> dict:
    api = Api(cfg)
    titles = []
    for prefix in cfg.get("prefixes") or []:
        titles += api.titles(prefix)
    mapped, unmapped = {}, []
    for t in sorted(set(titles)):
        ev = event_for_title(t, cfg)
        if ev:
            mapped[t] = ev
        else:
            unmapped.append(t)
    print(f"[liquipedia] {len(titles)} pages under {cfg.get('prefixes')}; {len(mapped)} map to an event")
    for t in unmapped:
        print(f"[liquipedia]   skipped: {t}")
    pages = api.contents(sorted(mapped))
    matches, page_rows = [], []
    for title in sorted(pages):
        text, revid = pages[title]
        found = parse_page(text, title, mapped[title], cfg["wiki"])
        page_rows.append({"title": title, "event": mapped[title], "revid": revid, "matches": len(found)})
        matches += found
    return {"pages": page_rows, "matches": matches, "unmapped": unmapped}


def write(doc: dict) -> bool:
    """Write when the content changed; fetchedAt alone is not a change."""
    old = None
    try:
        with open(OUT, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        pass
    body = {k: doc[k] for k in ("pages", "matches", "unmapped")}
    if old and {k: old.get(k) for k in body} == body:
        print("[liquipedia] unchanged")
        return False
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    out = {"_readme": "Written by pipeline/liquipedia_schedule.py from Liquipedia (CC BY-SA 3.0). "
                      "Do not hand-edit; put corrections in config/schedule.json `matches`.",
           "fetchedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(), **body}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    print(f"[liquipedia] wrote {len(body['matches'])} matches from {len(body['pages'])} pages")
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Liquipedia start times for the schedule")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--offline", help="parse a saved wikitext file instead of fetching")
    ap.add_argument("--title", default="Overwatch Champions Series/2026/Korea/Stage 3")
    args = ap.parse_args(argv)
    cfg = load_config()
    if args.offline:
        with open(args.offline, encoding="utf-8") as f:
            text = f.read()
        found = parse_page(text, args.title, event_for_title(args.title, cfg), cfg["wiki"])
        print(json.dumps(found, indent=1, ensure_ascii=False))
        return 0
    try:
        doc = fetch(cfg)
    except Exception as exc:
        print(f"[liquipedia] fetch FAILED: {type(exc).__name__}: {exc} — the last good file stays",
              file=sys.stderr)
        return 2
    if args.dry_run:
        for m in doc["matches"]:
            print(f"{m['event']:<30} {m.get('startsAt') or m.get('date') or '—':<26} {m.get('round') or '':<24} "
                  f"{m.get('teamA') or 'TBD'} vs {m.get('teamB') or 'TBD'}")
        return 0
    write(doc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
