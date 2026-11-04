#!/usr/bin/env python3
"""
test_schedule.py — the season schedule behind the Matches page.

  * round names: every format the sheet writes lands in the right phase,
    bracket side and round;
  * the real export: every played series is in it once; the scheduled
    cards are there as slots (Korea's Stage 3 playoffs on Nov 6-8, China's
    on Nov 7-8); every phase id and match id resolves; brackets are drawn
    so a winner sits beside the series they advanced to; standings add up;
    nothing upcoming carries a time no source gave;
  * Liquipedia: the Match2 parser reads opponents, scores (also from map
    winners), time zones, round headers and streams from a saved page, and
    the merge fills a TBD slot with its teams and start time without
    touching a played result;
  * the committed export is exactly what the committed inputs build.

Run: python3 pipeline/test_schedule.py
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import schedule_build as SB          # noqa: E402
import liquipedia_schedule as LQ     # noqa: E402

FAILS = 0


def check(name: str, ok: bool) -> None:
    global FAILS
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILS += 1


def test_classify() -> None:
    print("round names:")
    cases = {
        "Round Robin [Bo5]": ("regular", "league", None, "Round robin", 5),
        "Swiss Stage (Round 3) [Bo5]": ("swiss", "league", None, "Swiss round 3", 5),
        "Group Stage [Bo3]": ("groups", "league", None, "Group stage", 3),
        "Seeding Decider [Bo5]": ("seeding", "list", None, "Seeding decider", 5),
        "Last Chance Qualifier [Bo5]": ("lcq", "list", None, "Last Chance Qualifier", 5),
        "Quarterfinal [Bo7]": ("playoffs", "bracket", "main", "Quarterfinals", 7),
        "Playoff Semifinals [Bo5]": ("playoffs", "bracket", "main", "Semifinals", 5),
        "Upper Finals [Bo5]": ("playoffs", "bracket", "upper", "Upper Final", 5),
        "Lower Round 1 [Bo3]": ("playoffs", "bracket", "lower", "Lower Round 1", 3),
        "Group B Upper Quarterfinals [Bo3]": ("group-b", "bracket", "upper", "Upper Quarterfinals", 3),
        "Third Place Match [Bo7]": ("playoffs", "bracket", "third", "Third place", 7),
        "Grand Finals [Bo7]": ("playoffs", "bracket", "final", "Grand Final", 7),
        "Upper Bracket Semifinals": ("playoffs", "bracket", "upper", "Upper Semifinals", None),
    }
    for fmt, want in cases.items():
        c = SB.classify(fmt)
        got = (c["phase"], c["kind"], c["side"], c["round"], c["bestOf"])
        check(f"{fmt!r} -> {want[:4]}", got == want)


def load_export() -> dict:
    return SB.read_js_object(SB.OUT)


def test_export() -> None:
    print("the committed export:")
    doc = load_export()
    check("the export exists and parses", bool(doc and doc.get("events") and doc.get("matches")))
    if not doc:
        return
    series = SB.read_js_object(SB.SERIES_JS)["series"]
    ids = [m["id"] for m in doc["matches"]]
    check("match ids are unique", len(ids) == len(set(ids)))
    played = {s["id"] for s in series}
    check(f"every played series is in the schedule once ({len(played)})",
          played <= set(ids) and sum(1 for i in ids if i in played) == len(played))
    byid = {m["id"]: m for m in doc["matches"]}
    ev = {e["id"]: e for e in doc["events"]}
    check("every match belongs to a listed event", all(m["event"] in ev for m in doc["matches"]))
    refs = [mid for e in doc["events"] for p in e["phases"] for mid in p["matches"]]
    check("every phase lists only real matches, each once", all(r in byid for r in refs) and len(refs) == len(set(refs)))

    kr = [m for m in doc["matches"] if m["event"] == "korea/stage-3" and m["phase"] == "playoffs"]
    check("Korea Stage 3 playoffs: six slots (2 QF, 2 SF, third place, final)", len(kr) == 6)
    check("…on Nov 6, 7 and 8, from the published playoff days",
          sorted({m["date"] for m in kr}) == ["2026-11-06", "2026-11-07", "2026-11-08"]
          and all(m["dateFrom"] in ("published-day", "liquipedia", "liquipedia-day", "official") for m in kr))
    check("…every one Bo7", all(m["bestOf"] == 7 for m in kr))
    cn = [m for m in doc["matches"] if m["event"] == "china/stage-3" and m["phase"] == "playoffs"]
    check("China Stage 3 playoffs: a double-elimination bracket on Nov 7-8",
          len(cn) == 6 and {m["date"] for m in cn} == {"2026-11-07", "2026-11-08"}
          and next(p for p in ev["china/stage-3"]["phases"] if p["id"] == "playoffs")["bracket"]["type"] == "double")
    check("a stage with unpublished days gives its slots no date (never a guess)",
          all(m["date"] is None for m in doc["matches"] if m["event"] == "asia/stage-3" and m["state"] != "done"))
    check("the World Finals are on the schedule with their window",
          ev.get("international/world-finals", {}).get("window") == ["2026-12-02", "2026-12-06"])
    check("nothing still to play carries a start time no source gave",
          all(not m.get("startsAt") or m.get("dateFrom") in ("liquipedia", "official")
              for m in doc["matches"]))
    check("an estimated date says it is one",
          all(m["dateFrom"] in (None, "estimate", "published-day", "liquipedia", "liquipedia-day", "official")
              for m in doc["matches"]))

    cc = ev["international/champions-clash"]
    b = cc["phases"][-1]["bracket"]
    check("Champions Clash: double elimination with a final", b["type"] == "double" and len(b["final"]) == 1)
    check("…its champion is the final's winner (Crazy Raccoon)", cc["champion"] == "cr")
    # The drawing order: each semifinal's winner is in the final, each
    # quarterfinal pair feeds the semifinal beside it.
    up = b["upper"]
    qf, sf = up[0]["matches"], up[1]["matches"]
    feeds = [byid[q]["winner"] for q in qf]
    sf_teams = [{byid[s]["teamA"], byid[s]["teamB"]} for s in sf]
    check("…the bracket is drawn so quarterfinal winners sit beside their semifinal",
          feeds[0] in sf_teams[0] and feeds[1] in sf_teams[0] and feeds[2] in sf_teams[1] and feeds[3] in sf_teams[1])

    na = next(p for p in ev["na/stage-3"]["phases"] if p["id"] == "regular")
    st = na["standings"]
    games = [byid[i] for i in na["matches"]]
    check("standings: wins add up to series played", sum(r["w"] for r in st) == sum(1 for g in games if g["winner"]))
    check("standings: map difference sums to zero", sum(r["diff"] for r in st) == 0)
    check("standings: ordered by wins", all(st[i]["w"] >= st[i + 1]["w"] for i in range(len(st) - 1)))


SAMPLE = os.path.join(HERE, "fixtures", "liquipedia", "korea_stage3_playoffs.wiki")


def test_liquipedia_parser() -> None:
    print("Liquipedia's Match2 markup:")
    cfg = LQ.load_config()
    title = "Overwatch Champions Series/2026/Korea/Stage 3/Playoffs"
    check("a regional stage title maps to its event", LQ.event_for_title(title, cfg) == "korea/stage-3")
    check("Asia nests the same way", LQ.event_for_title("Overwatch Champions Series/2026/Asia/Stage 3", cfg)
          == "asia/stage-3")
    check("a qualifier is skipped", LQ.event_for_title(
        "Overwatch Champions Series/2026/NA/Stage 3/Open Qualifier", cfg) is None)
    check("the World Finals map by rule",
          LQ.event_for_title("Overwatch Champions Series/2026/World Finals", cfg) == "international/world-finals")
    with open(SAMPLE, encoding="utf-8") as f:
        found = LQ.parse_page(f.read(), title, "korea/stage-3", cfg["wiki"])
    check("every {{Match}} on the page is read (6)", len(found) == 6)
    a = found[0]
    check("opponents by position", (a["teamA"], a["teamB"]) == ("t1", "team falcons"))
    check("a KST time becomes +09:00", a["startsAt"] == "2026-11-06T18:00:00+09:00")
    check("the round header comes from the bracket", a["round"] == "Quarterfinals")
    check("streams become links", [s["url"] for s in a["streams"]] ==
          ["https://www.twitch.tv/ow_esports_kr", "https://www.youtube.com/@OverwatchEsportsKR"])
    b = found[1]
    check("template= names a team", b["teamA"] == "cheeseburger")
    check("a missing score is counted from the map winners", (b["scoreA"], b["scoreB"]) == (4, 1))
    check("finished=true is read", b["finished"] is True)
    check("an ISO date with a time parses", b["startsAt"] == "2026-11-06T21:30:00+09:00")
    check("the header carries to the rest of its round", found[1]["round"] == "Quarterfinals"
          and found[3]["round"] == "Semifinals")
    check("TBD opponents read as unknown", found[2]["teamB"] is None and found[4]["teamA"] is None
          and found[4]["teamB"] is None)
    check("a date with no time keeps the day and no time", found[3]["startsAt"] is None and found[3]["date"] == "2026-11-07")
    check("the third-place slot is named", found[5]["round"] == "Third Place Match")
    check("CST on a China page is China time", LQ.parse_date("2026-11-07 - 19:00 {{Abbr/CST}}", "china/stage-3")[0]
          == "2026-11-07T19:00:00+08:00")
    check("…and US Central elsewhere", LQ.parse_date("2026-11-07 - 19:00 {{Abbr/CST}}", "na/stage-3")[0]
          == "2026-11-07T19:00:00-06:00")
    check("an unknown zone keeps the day only", LQ.parse_date("2026-11-07 - 19:00 {{Abbr/XYZ}}", None)
          == (None, "2026-11-07"))


def test_merge() -> None:
    print("joining Liquipedia onto the sheet:")
    cfg = LQ.load_config()
    with open(SAMPLE, encoding="utf-8") as f:
        found = LQ.parse_page(f.read(), "Overwatch Champions Series/2026/Korea/Stage 3/Playoffs",
                              "korea/stage-3", cfg["wiki"])
    real = SB.LIQUIPEDIA
    tmp = os.path.join(os.environ.get("TMPDIR", "/tmp"), "owcs-test-liquipedia.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"fetchedAt": "2026-11-04T00:00:00+00:00", "pages": [{"title": "x"}],
                   "matches": found + [{"event": "international/world-finals", "round": "Upper Quarterfinals",
                                        "teamA": "Crazy Raccoon", "teamB": "Twisted Minds",
                                        "startsAt": "2026-12-02T11:00:00+08:00", "date": "2026-12-02",
                                        "finished": False, "order": 1}]}, f)
    SB.LIQUIPEDIA = tmp
    try:
        doc = SB.build()
    finally:
        SB.LIQUIPEDIA = real
        os.remove(tmp)
    kr = sorted([m for m in doc["matches"] if m["event"] == "korea/stage-3" and m["phase"] == "playoffs"],
                key=SB.order_key)
    qf = [m for m in kr if m["round"] == "Quarterfinals"]
    check("a TBD quarterfinal slot takes Liquipedia's teams",
          {qf[0]["teamA"], qf[0]["teamB"]} == {"t1", "falcons"})
    check("…and its start time, marked as Liquipedia's",
          qf[0]["startsAt"] == "2026-11-06T18:00:00+09:00" and qf[0]["dateFrom"] == "liquipedia")
    check("…and its streams", bool(qf[0].get("streams")))
    check("a finished Liquipedia series fills its slot's result",
          qf[1]["state"] == "done" and (qf[1]["scoreA"], qf[1]["scoreB"]) == (4, 1)
          and qf[1]["winner"] == qf[1]["teamA"])
    sf = [m for m in kr if m["round"] == "Semifinals"]
    check("a half-known semifinal gets its one known team", sf[0]["teamA"] == "zeta" and sf[0]["teamB"] is None)
    check("a day-only date is kept as a day", sf[1]["date"] == "2026-11-07" and not sf[1].get("startsAt"))
    wf = [m for m in doc["matches"] if m["event"] == "international/world-finals"]
    check("an event with no sheet cards gets Liquipedia's matches", len(wf) == 1 and wf[0]["teamA"] == "cr")
    played = {m["id"]: m for m in doc["matches"] if m["series"]}
    base = {m["id"]: m for m in load_export()["matches"] if m["series"]}
    check("no played sheet result changes", all((played[i]["scoreA"], played[i]["scoreB"], played[i]["winner"]) ==
                                               (base[i]["scoreA"], base[i]["scoreB"], base[i]["winner"]) for i in base))
    check("the sources credit Liquipedia when it is used", any(s["id"] == "liquipedia" for s in doc["sources"]))


def test_merge_real_pages() -> None:
    """The shapes the first live fetch (2026-11-04) brought, which the join
    used to turn into ~500 phantom series: a stage page that also lists the
    regular season under date headers with no finished flag, and playoff
    matches whose header is {{Stage|Regional Playoffs}}."""
    print("joining a whole live stage page:")
    def lpm(ev, rnd, a, b, starts, order):
        return {"event": ev, "round": rnd, "teamA": a, "teamB": b, "startsAt": starts,
                "date": starts[:10], "finished": False, "order": order}
    rows = [
        lpm("korea/stage-3", "October 3rd", "Crazy Raccoon", "O2 Blast", "2026-10-03T18:35:00+09:00", 1),
        lpm("korea/stage-3", "October 2nd", "Team Falcons", "ZETA DIVISION", "2026-10-02T18:45:00+09:00", 2),
        lpm("korea/stage-3", "October 17th", "T1", None, "2026-11-06T15:00:00+09:00", 3),
        lpm("korea/stage-3", "Regional Playoffs", "Cheeseburger", "Team Falcons", "2026-11-06T17:00:00+09:00", 4),
        lpm("korea/stage-3", "Regional Playoffs", "ZETA DIVISION", None, "2026-11-07T15:00:00+09:00", 5),
        lpm("korea/stage-3", "Regional Playoffs", "Crazy Raccoon", None, "2026-11-07T17:00:00+09:00", 6),
        lpm("korea/stage-3", "Third Place Match", None, None, "2026-11-08T15:00:00+09:00", 7),
        lpm("korea/stage-3", "Regional Playoffs", None, None, "2026-11-08T17:00:00+09:00", 8),
        lpm("asia/stage-3", "Day 1", None, "VARREL", "2026-11-11T17:00:00+09:00", 9),
    ]
    real = SB.LIQUIPEDIA
    tmp = os.path.join(os.environ.get("TMPDIR", "/tmp"), "owcs-test-liquipedia-live.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"fetchedAt": "2026-11-04T18:00:00+00:00", "pages": [{"title": "x"}], "matches": rows}, f)
    SB.LIQUIPEDIA = tmp
    try:
        doc = SB.build()
    finally:
        SB.LIQUIPEDIA = real
        os.remove(tmp)
    kr = sorted([m for m in doc["matches"] if m["event"] == "korea/stage-3" and m["phase"] == "playoffs"],
                key=SB.order_key)
    check("a date header is not a round, so October's series add nothing",
          len(kr) == 6 and all(m["id"].startswith("xs-") for m in kr))
    third = [m for m in kr if m["side"] == "third"]
    check("…and 'October 3rd' does not take the third-place slot",
          len(third) == 1 and third[0]["date"] == "2026-11-08")
    qf = [m for m in kr if m["round"] == "Quarterfinals"]
    check("a playoff match with no usable round fills the empty slot on its day",
          [m["startsAt"] for m in qf] == ["2026-11-06T15:00:00+09:00", "2026-11-06T17:00:00+09:00"])
    check("…in start order, with the teams Liquipedia knows",
          qf[0]["teamA"] == "t1" and qf[1]["teamA"] is not None)
    gf = [m for m in kr if m["side"] == "final"]
    check("the last match of the final day is the final", gf and gf[0]["startsAt"] == "2026-11-08T17:00:00+09:00")
    asia = [m for m in doc["matches"] if m["event"] == "asia/stage-3"]
    check("a stage the sheet covers gains no Liquipedia-only series",
          not any(m["id"].startswith("xl-") for m in asia + kr))
    lpm_meta = doc["meta"]["generatedFrom"]["liquipedia"]
    check("…and the run says how many it could not place", lpm_meta["unjoined"] >= 2 and lpm_meta["added"] == 0)


def test_reproducible() -> None:
    print("reproducibility:")
    with open(SB.OUT, encoding="utf-8") as f:
        committed = f.read()
    check("the committed schedule.v1.js is what the committed inputs build", committed == SB.render(SB.build()))


def main() -> int:
    test_classify()
    test_export()
    test_liquipedia_parser()
    test_merge()
    test_merge_real_pages()
    test_reproducible()
    print(f"\n{'OK' if not FAILS else str(FAILS) + ' FAILED'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
